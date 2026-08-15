"""Explicit local orchestration for staged coin groups, without a worker.

The function in this module is the only bridge from short-lived private
staging to the normalized Market Store.  It is synchronous and caller-driven:
no collector, scheduler, network, or application request hook is registered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import sqlite3
from typing import Iterable

from .coin_group_resolution import CoinPriceAnchor, resolve_coin_group_offers, resolved_coin_group_observations
from .coin_group_staging import StagedCoinGroupMessage, list_current_staged_coin_group_messages
from .coin_group_trades import CoinGroupOfferRecord, coin_group_trade_observations, link_coin_group_trades
from .coin_groups import CoinGroupMessageInput
from .market_contracts import MarketObservation, normalize_utc
from .market_store import upsert_observation


COIN_GROUP_PIPELINE_VERSION = "coin-group-pipeline-v1"


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


def _source(message: StagedCoinGroupMessage) -> CoinGroupMessageInput:
    return CoinGroupMessageInput(
        group_number=message.group_number,
        source_event_id=message.message_id,
        published_at_utc=message.event_time_utc,
        available_at_utc=message.available_at_utc,
        text=message.text,
    )


def _store_anchors(connection: sqlite3.Connection) -> list[CoinPriceAnchor]:
    """Read only unit-compatible eligible facts; never infer a conversion here."""

    rows = connection.execute(
        """
        SELECT instrument, price_num, event_time_utc, available_at_utc,
               settlement_term, trade_form, quality_state, is_conditional
        FROM market_observations
        WHERE quality_state = 'ELIGIBLE'
          AND is_conditional = 0
          AND event_type IN ('OFFER', 'TRADE')
          AND price_unit = 'PROJECT_THOUSAND_TOMAN'
          AND instrument LIKE 'COIN_%'
        """
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
            )
        )
    return anchors


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
    base_anchors = tuple(_store_anchors(market_connection)) + tuple(additional_anchors)
    all_resolved: dict[tuple[int, int], list] = {}
    offer_facts = 0
    eligible_offers = 0
    pending_or_rejected_offers = 0
    for message in messages:
        source = _source(message)
        resolved = resolve_coin_group_offers(source, anchors=base_anchors)
        all_resolved[(message.group_number, message.message_id)] = resolved
        observations = resolved_coin_group_observations(
            source,
            anchors=base_anchors,
            resolution_available_at_utc=as_of,
        )
        for observation in observations:
            _upsert_if_semantically_changed(market_connection, observation)
            offer_facts += 1
        eligible_offers += sum(item.quality_state == "ELIGIBLE" for item in resolved)
        pending_or_rejected_offers += sum(item.quality_state != "ELIGIBLE" for item in resolved)

    records: list[CoinGroupOfferRecord] = []
    not_linkable = 0
    for message in messages:
        resolved = all_resolved[(message.group_number, message.message_id)]
        eligible = [item for item in resolved if item.quality_state == "ELIGIBLE"]
        if len(eligible) != 1:
            if eligible:
                not_linkable += 1
            continue
        records.append(
            CoinGroupOfferRecord(
                group_number=message.group_number,
                message_id=message.message_id,
                offerer_digest=message.sender_digest,
                offer_event_time_utc=message.event_time_utc,
                offer_available_at_utc=message.available_at_utc,
                offer=eligible[0],
            )
        )
    trades = link_coin_group_trades(messages, records)
    observations = coin_group_trade_observations(trades)
    for observation in observations:
        _upsert_if_semantically_changed(market_connection, observation)
    return CoinGroupPipelineReport(
        staged_messages_seen=len(messages),
        offer_facts_upserted=offer_facts,
        eligible_offers=eligible_offers,
        pending_or_rejected_offers=pending_or_rejected_offers,
        trade_facts_upserted=len(observations),
        eligible_trades=sum(item.quality_state == "ELIGIBLE" for item in trades),
        pending_or_rejected_trades=sum(item.quality_state != "ELIGIBLE" for item in trades),
        root_messages_not_trade_linkable=not_linkable,
    )
