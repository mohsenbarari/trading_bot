"""Explicit local orchestration for staged coin groups, without a worker.

The function in this module is the only bridge from short-lived private
staging to the normalized Market Store.  It is synchronous and caller-driven:
no collector, scheduler, network, or application request hook is registered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
import sqlite3
from statistics import median
from typing import Iterable

from .coin_group_resolution import (
    MAXIMUM_ANCHOR_AGE_SECONDS,
    CoinPriceAnchor,
    resolve_coin_group_offers,
    resolved_coin_group_observations,
)
from .coin_group_staging import StagedCoinGroupMessage, list_current_staged_coin_group_messages
from .coin_group_trades import CoinGroupOfferRecord, coin_group_trade_observations, link_coin_group_trades
from .coin_groups import CoinGroupMessageInput, parse_coin_group_offers
from .market_contracts import MarketObservation, normalize_utc
from .market_store import upsert_observation


COIN_GROUP_PIPELINE_VERSION = "coin-group-pipeline-v3-forward-anchors"
PROVISIONAL_BOOTSTRAP_WINDOW_SECONDS = 30 * 60
PROVISIONAL_MINIMUM_MESSAGES = 3
PROVISIONAL_MINIMUM_SENDERS = 2
PROVISIONAL_MINIMUM_NONCONDITIONAL_MESSAGES = 1
PROVISIONAL_MAXIMUM_RELATIVE_SPREAD = 0.015
_RETRACTION_REASON = "NO_LONGER_PRESENT_IN_CURRENT_STAGED_MESSAGE_GRAPH"


_SEMANTIC_OBSERVATION_COLUMNS = (
    "source_code",
    "source_family",
    "event_time_utc",
    "instrument",
    "market_label",
    "settlement_term",
    "trade_form",
    "event_type",
    "side",
    "price_value",
    "price_unit",
    "currency",
    "quantity_value",
    "quantity_unit",
    "parse_confidence",
    "parser_version",
    "quality_state",
    "quality_policy_version",
    "is_conditional",
    "attributes_json",
)


def _upsert_if_semantically_changed(
    connection: sqlite3.Connection,
    observation: MarketObservation,
) -> bool:
    """Preserve first availability when an idempotent replay changes nothing."""

    normalized = observation.normalized()
    existing = connection.execute(
        f"SELECT {','.join(_SEMANTIC_OBSERVATION_COLUMNS)} "
        "FROM market_observations WHERE event_key=?",
        (normalized.event_key,),
    ).fetchone()
    quantity_value = (
        str(normalized.quantity) if normalized.quantity is not None else None
    )
    expected = (
        normalized.source_code,
        normalized.source_family,
        normalized.event_time_utc,
        normalized.instrument,
        normalized.market_label,
        normalized.settlement_term,
        normalized.trade_form,
        normalized.event_type,
        normalized.side,
        str(normalized.price),
        normalized.price_unit,
        normalized.currency,
        quantity_value,
        normalized.quantity_unit,
        normalized.parse_confidence,
        normalized.parser_version,
        normalized.quality_state,
        normalized.quality_policy_version,
        int(normalized.is_conditional),
        normalized.attributes_json,
    )
    if existing is not None and tuple(
        existing[column] for column in _SEMANTIC_OBSERVATION_COLUMNS
    ) == expected:
        return False
    upsert_observation(connection, observation)
    return True


@dataclass(frozen=True, slots=True)
class CoinGroupPipelineReport:
    """Privacy-safe counters; no raw text/message IDs/senders are returned."""

    staged_messages_seen: int
    offer_facts_upserted: int
    eligible_offers: int
    pending_or_rejected_offers: int
    trade_facts_upserted: int
    eligible_trades: int
    pending_or_rejected_trades: int
    root_messages_not_trade_linkable: int
    retracted_facts: int


@dataclass(frozen=True, slots=True)
class _ExplicitClaim:
    group_number: int
    message_id: int
    sender_digest: bytes
    commodity_code: str
    price: int
    event_time_utc: str
    available_at_utc: str
    settlement_term: str
    trade_form: str
    is_conditional: bool


def _source(message: StagedCoinGroupMessage) -> CoinGroupMessageInput:
    return CoinGroupMessageInput(
        group_number=message.group_number,
        source_event_id=message.message_id,
        published_at_utc=message.event_time_utc,
        available_at_utc=message.available_at_utc,
        text=message.text,
    )


def _store_anchors(
    connection: sqlite3.Connection,
    *,
    minimum_event_time_utc: str | None = None,
) -> list[CoinPriceAnchor]:
    """Read only unit-compatible eligible facts; never infer a conversion here."""

    horizon_clause = " AND event_time_utc >= ?" if minimum_event_time_utc else ""
    parameters: tuple[object, ...] = (
        (minimum_event_time_utc,) if minimum_event_time_utc else ()
    )
    rows = connection.execute(
        """
        SELECT source_family,instrument,price_num,event_time_utc,available_at_utc,
               settlement_term, trade_form, quality_state, is_conditional
        FROM market_observations
        WHERE quality_state = 'ELIGIBLE'
          AND is_conditional = 0
          AND event_type IN ('OFFER', 'TRADE')
          AND price_unit = 'PROJECT_THOUSAND_TOMAN'
          AND instrument LIKE 'COIN_%'
        """ + horizon_clause,
        parameters,
    ).fetchall()
    anchors: list[CoinPriceAnchor] = []
    for row in rows:
        code = str(row["instrument"])[len("COIN_") :]
        try:
            numeric_price = float(row["price_num"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric_price) or not numeric_price.is_integer():
            continue
        price = int(numeric_price)
        anchors.append(
            CoinPriceAnchor(
                commodity_code=code,
                price_project_thousand_toman=price,
                event_time_utc=str(row["event_time_utc"]),
                available_at_utc=str(row["available_at_utc"]),
                settlement_term=str(row["settlement_term"]),
                trade_form=str(row["trade_form"]),
                quality_state=str(row["quality_state"]),
                # Group consensus may validate or resolve another group fact,
                # but it is not authoritative enough to contradict an
                # explicitly named commodity.
                evidence_kind=(
                    "GROUP_DERIVED"
                    if str(row["source_family"]).upper() == "GROUP"
                    else "CANONICAL"
                ),
            )
        )
    return anchors


def _stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _coherent_provisional_anchors(
    claims: Iterable[_ExplicitClaim],
    *,
    source: CoinGroupMessageInput,
) -> tuple[CoinPriceAnchor, ...]:
    """Promote only a tight, multi-message/multi-sender prior explicit cluster."""

    source_event = normalize_utc(
        source.published_at_utc,
        field_name="coin_group_bootstrap_source_event_time_utc",
    )
    source_available = normalize_utc(
        source.available_at_utc,
        field_name="coin_group_bootstrap_source_available_at_utc",
    )
    lower = _stamp(source_event) - timedelta(
        seconds=PROVISIONAL_BOOTSTRAP_WINDOW_SECONDS
    )
    grouped: dict[tuple[str, str, str], dict[tuple[int, int], _ExplicitClaim]] = {}
    for claim in claims:
        if not (
            claim.event_time_utc < source_event
            and claim.available_at_utc <= source_available
        ):
            continue
        if _stamp(claim.event_time_utc) < lower:
            continue
        key = (claim.commodity_code, claim.settlement_term, claim.trade_form)
        grouped.setdefault(key, {})[(claim.group_number, claim.message_id)] = claim

    anchors: list[CoinPriceAnchor] = []
    for (code, settlement, form), by_message in grouped.items():
        values = list(by_message.values())
        if len(values) < PROVISIONAL_MINIMUM_MESSAGES:
            continue
        if len({item.sender_digest for item in values}) < PROVISIONAL_MINIMUM_SENDERS:
            continue
        if (
            sum(not item.is_conditional for item in values)
            < PROVISIONAL_MINIMUM_NONCONDITIONAL_MESSAGES
        ):
            continue
        center = float(median(item.price for item in values))
        spread = (
            max(item.price for item in values) - min(item.price for item in values)
        ) / center
        if spread > PROVISIONAL_MAXIMUM_RELATIVE_SPREAD:
            continue
        anchors.extend(
            CoinPriceAnchor(
                commodity_code=code,
                price_project_thousand_toman=item.price,
                event_time_utc=item.event_time_utc,
                available_at_utc=item.available_at_utc,
                settlement_term=settlement,
                trade_form=form,
                evidence_kind="PROVISIONAL_EXPLICIT_CLUSTER",
            )
            for item in values
        )
    return tuple(anchors)


def _reconcile_missing_current_facts(
    connection: sqlite3.Connection,
    *,
    active_event_keys: set[bytes],
    staging_horizon_utc: str | None,
    available_at_utc: str,
) -> int:
    """Reject derived facts invalidated by edits/current reply-graph changes."""

    if staging_horizon_utc is None:
        return 0
    rows = connection.execute(
        """
        SELECT event_key,quality_state,attributes_json
        FROM market_observations
        WHERE source_code IN ('GROUP_1','GROUP_2')
          AND source_family='GROUP'
          AND event_type IN ('OFFER','TRADE')
          AND event_time_utc >= ?
          AND (
              parser_version LIKE 'coin-group-rules-%+coin-group-context-%'
              OR parser_version LIKE 'coin-group-trade-link-%'
          )
        """,
        (staging_horizon_utc,),
    ).fetchall()
    retracted = 0
    for row in rows:
        event_key = bytes(row["event_key"])
        if event_key in active_event_keys:
            continue
        try:
            attributes = json.loads(str(row["attributes_json"] or "{}"))
        except (TypeError, ValueError):
            attributes = {}
        if (
            str(row["quality_state"]).upper() == "REJECTED"
            and attributes.get("resolution_reason") == _RETRACTION_REASON
        ):
            continue
        attributes["resolution_reason"] = _RETRACTION_REASON
        attributes["reconciled_by"] = COIN_GROUP_PIPELINE_VERSION
        connection.execute(
            """
            UPDATE market_observations
            SET available_at_utc=?, parse_confidence=0,
                quality_state='REJECTED',
                quality_policy_version='coin-group-current-graph-v1',
                attributes_json=?
            WHERE event_key=?
            """,
            (
                available_at_utc,
                json.dumps(attributes, sort_keys=True, separators=(",", ":")),
                event_key,
            ),
        )
        retracted += 1
    return retracted


def process_coin_group_staging(
    staging_connection: sqlite3.Connection,
    market_connection: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
    additional_anchors: Iterable[CoinPriceAnchor] = (),
) -> CoinGroupPipelineReport:
    """Process current staging idempotently in one caller-owned Store transaction.

    ``additional_anchors`` exists for a future unit-safe Snapshot provider; it
    is explicit so this layer can never manufacture a project-unit conversion
    from another market.  The caller must commit/rollback ``market_connection``
    around this function.
    """

    as_of = normalize_utc(as_of_utc, field_name="coin_group_pipeline_as_of_utc")
    messages = list_current_staged_coin_group_messages(staging_connection, as_of_utc=as_of)
    staging_horizon = min((item.event_time_utc for item in messages), default=None)
    minimum_anchor_time = (
        (_stamp(staging_horizon) - timedelta(seconds=MAXIMUM_ANCHOR_AGE_SECONDS))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
        if staging_horizon is not None
        else None
    )
    base_anchors = tuple(
        _store_anchors(
            market_connection,
            minimum_event_time_utc=minimum_anchor_time,
        )
    ) + tuple(additional_anchors)
    all_resolved: dict[tuple[int, int], list] = {}
    explicit_claims: list[_ExplicitClaim] = []
    dynamic_anchors: list[CoinPriceAnchor] = []
    active_event_keys: set[bytes] = set()
    offer_facts = 0
    eligible_offers = 0
    pending_or_rejected_offers = 0
    for message in messages:
        source = _source(message)
        parsed = parse_coin_group_offers(source)
        provisional_anchors = _coherent_provisional_anchors(
            explicit_claims,
            source=source,
        )
        resolution_anchors = (
            *base_anchors,
            *provisional_anchors,
            *dynamic_anchors,
        )
        resolved = resolve_coin_group_offers(
            source,
            anchors=resolution_anchors,
            parsed_offers=parsed,
        )
        all_resolved[(message.group_number, message.message_id)] = resolved
        observations = resolved_coin_group_observations(
            source,
            anchors=resolution_anchors,
            resolution_available_at_utc=as_of,
            resolved_offers=resolved,
        )
        for observation in observations:
            active_event_keys.add(observation.event_key)
            offer_facts += int(
                _upsert_if_semantically_changed(market_connection, observation)
            )
        eligible_offers += sum(item.quality_state == "ELIGIBLE" for item in resolved)
        pending_or_rejected_offers += sum(item.quality_state != "ELIGIBLE" for item in resolved)
        dynamic_anchors.extend(
            CoinPriceAnchor(
                commodity_code=item.commodity_code,
                price_project_thousand_toman=item.price_project_thousand_toman,
                event_time_utc=message.event_time_utc,
                available_at_utc=message.available_at_utc,
                settlement_term=item.settlement_term,
                trade_form=item.trade_form,
                evidence_kind="GROUP_DERIVED",
            )
            for item in resolved
            if item.quality_state == "ELIGIBLE"
            and item.commodity_code is not None
            and not item.is_conditional
        )
        if message.sender_digest is not None:
            for candidate in parsed:
                if candidate.commodity_code is None:
                    continue
                explicit_claims.append(
                    _ExplicitClaim(
                        group_number=message.group_number,
                        message_id=message.message_id,
                        sender_digest=message.sender_digest,
                        commodity_code=candidate.commodity_code,
                        price=candidate.price_project_thousand_toman,
                        event_time_utc=message.event_time_utc,
                        available_at_utc=message.available_at_utc,
                        settlement_term=candidate.settlement_term,
                        trade_form=candidate.trade_form,
                        is_conditional=candidate.is_conditional,
                    )
                )

    records: list[CoinGroupOfferRecord] = []
    not_linkable = 0
    for message in messages:
        resolved = all_resolved[(message.group_number, message.message_id)]
        if len(resolved) != 1:
            if resolved:
                not_linkable += 1
            continue
        records.append(
            CoinGroupOfferRecord(
                group_number=message.group_number,
                message_id=message.message_id,
                offerer_digest=message.sender_digest,
                offer_event_time_utc=message.event_time_utc,
                offer_available_at_utc=message.available_at_utc,
                offer=resolved[0],
            )
        )
    trades = link_coin_group_trades(messages, records)
    observations = coin_group_trade_observations(
        trades,
        resolution_available_at_utc=as_of,
    )
    trade_facts = 0
    for observation in observations:
        active_event_keys.add(observation.event_key)
        trade_facts += int(
            _upsert_if_semantically_changed(market_connection, observation)
        )
    retracted_facts = _reconcile_missing_current_facts(
        market_connection,
        active_event_keys=active_event_keys,
        staging_horizon_utc=staging_horizon,
        available_at_utc=as_of,
    )
    return CoinGroupPipelineReport(
        staged_messages_seen=len(messages),
        offer_facts_upserted=offer_facts,
        eligible_offers=eligible_offers,
        pending_or_rejected_offers=pending_or_rejected_offers,
        trade_facts_upserted=trade_facts,
        eligible_trades=sum(item.quality_state == "ELIGIBLE" for item in trades),
        pending_or_rejected_trades=sum(item.quality_state != "ELIGIBLE" for item in trades),
        root_messages_not_trade_linkable=not_linkable,
        retracted_facts=retracted_facts,
    )
