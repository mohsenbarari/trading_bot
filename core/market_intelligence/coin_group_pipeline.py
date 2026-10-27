"""Explicit local orchestration for staged coin groups, without a worker.

The function in this module is the only bridge from short-lived private
staging to the normalized Market Store.  It is synchronous and caller-driven:
no collector, scheduler, network, or application request hook is registered.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import blake2b
import json
import math
import re
import sqlite3
from statistics import median
from typing import Iterable, Mapping

from .coin_group_feedback import CoinGroupParserFeedback

from .coin_group_resolution import (
    MAXIMUM_ANCHOR_AGE_SECONDS,
    CoinPriceAnchor,
    CoinPriceAnchorIndex,
    ResolvedCoinGroupOffer,
    resolve_coin_group_offers,
    resolved_coin_group_observations,
)
from .coin_group_staging import StagedCoinGroupMessage, list_current_staged_coin_group_messages
from .coin_group_trades import (
    CoinGroupOfferRecord,
    LinkedCoinGroupTrade,
    coin_group_trade_observations,
    link_coin_group_trades,
)
from .coin_groups import (
    _dimensions as coin_group_dimensions,
    _text as normalize_coin_group_text,
    CoinGroupMessageInput,
    parse_coin_group_offers,
)
from .market_contracts import MarketObservation, derive_event_key, normalize_utc
from .market_store import upsert_observation


COIN_GROUP_PIPELINE_VERSION = "coin-group-pipeline-v7-field-evidence"
PROVISIONAL_BOOTSTRAP_WINDOW_SECONDS = 30 * 60
PROVISIONAL_MINIMUM_MESSAGES = 3
PROVISIONAL_MINIMUM_SENDERS = 2
PROVISIONAL_MINIMUM_NONCONDITIONAL_MESSAGES = 1
PROVISIONAL_MAXIMUM_RELATIVE_SPREAD = 0.015
_RETRACTION_REASON = "NO_LONGER_PRESENT_IN_CURRENT_STAGED_MESSAGE_GRAPH"
_SYNTAX_NUMBERS = re.compile(r"\d+(?:[٬،,./_-]\d+)*")
_SAFE_PRICE_MULTIPLIERS = (0.001, 0.01, 0.1, 10.0, 100.0, 1_000.0)
_TRADE_ROOT_DERIVED_FIELDS = frozenset(
    {"commodity", "side", "settlement", "trade_form", "conditional"}
)
_SAFE_PATTERN_FIELDS = frozenset(
    {"side", "trade_form", "conditional"}
)
_FEEDBACK_TO_EVIDENCE_FIELD = {
    "event_validity": "event_type",
    "commodity": "instrument",
    "side": "side",
    "price": "price",
    "quantity": "quantity",
    "settlement": "settlement",
    "trade_form": "trade_form",
    "conditional": "conditional",
}


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
_AVAILABILITY_NEUTRAL_OBSERVATION_COLUMNS = frozenset(
    {
        "parse_confidence",
        "parser_version",
        "quality_policy_version",
        "attributes_json",
    }
)
_CAUSAL_ATTRIBUTE_KEYS = (
    "root_offer_event_key",
    "is_aggregate",
    "quantity_was_negotiated",
    "confirmation_kind",
)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _causal_attribute_signature(value: object) -> tuple[object, ...]:
    try:
        attributes = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        attributes = {}
    if not isinstance(attributes, dict):
        attributes = {}
    return tuple(attributes.get(key) for key in _CAUSAL_ATTRIBUTE_KEYS)


def _upsert_if_semantically_changed(
    connection: sqlite3.Connection,
    observation: MarketObservation,
) -> bool:
    """Preserve first availability when an idempotent replay changes nothing."""

    normalized = observation.normalized()
    existing = connection.execute(
        f"SELECT available_at_utc,{','.join(_SEMANTIC_OBSERVATION_COLUMNS)} "
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
    if existing is not None:
        actual = tuple(
            existing[column] for column in _SEMANTIC_OBSERVATION_COLUMNS
        )
        if actual == expected:
            return False
        changed_columns = {
            column
            for column, actual_value, expected_value in zip(
                _SEMANTIC_OBSERVATION_COLUMNS,
                actual,
                expected,
                strict=True,
            )
            if actual_value != expected_value
        }
        neutral_change = changed_columns <= _AVAILABILITY_NEUTRAL_OBSERVATION_COLUMNS
        if neutral_change and "attributes_json" in changed_columns:
            neutral_change = _causal_attribute_signature(
                existing["attributes_json"]
            ) == _causal_attribute_signature(normalized.attributes_json)
        if neutral_change:
            # A parser release identifier is provenance, not new economic
            # knowledge.  Persist it without making every unchanged historical
            # fact appear to have arrived at deployment time.
            observation = replace(
                observation,
                available_at_utc=str(existing["available_at_utc"]),
            )
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
    feedback_reviews_seen: int
    feedback_reviews_applied: int
    feedback_pattern_calibrations_applied: int
    applied_feedback_event_keys: tuple[bytes, ...]


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


@dataclass(frozen=True, slots=True)
class _ParserPatternCalibration:
    syntax_fingerprint: str
    event_type: str
    group_number: int
    ambiguous_fields: frozenset[str]
    event_confirmed: bool
    commodity_code: str
    side: str
    settlement_term: str
    trade_form: str
    is_conditional: bool
    price_multiplier: float | None
    review_revision: int
    reviewed_at_utc: str


def _source(message: StagedCoinGroupMessage) -> CoinGroupMessageInput:
    return CoinGroupMessageInput(
        group_number=message.group_number,
        source_event_id=message.message_id,
        published_at_utc=message.event_time_utc,
        available_at_utc=message.available_at_utc,
        text=message.text,
    )


def _remember_research_context(
    staging: sqlite3.Connection,
    *,
    event_key: bytes,
    root: StagedCoinGroupMessage,
    requester_message_id: int | None = None,
    requester_expires_at_utc: str | None = None,
) -> None:
    expires = max(root.expires_at_utc, requester_expires_at_utc or root.expires_at_utc)
    staging.execute(
        """
        INSERT INTO coin_group_fact_research_context(
          event_key,group_number,root_message_id,requester_message_id,
          expires_at_utc
        ) VALUES(?,?,?,?,?)
        ON CONFLICT(event_key) DO UPDATE SET
          root_message_id=excluded.root_message_id,
          requester_message_id=excluded.requester_message_id,
          expires_at_utc=excluded.expires_at_utc
        """,
        (
            event_key,
            root.group_number,
            root.message_id,
            requester_message_id,
            expires,
        ),
    )


def _actor_identity(message: StagedCoinGroupMessage) -> str | bytes | None:
    return message.sender_telegram_id or message.sender_digest


def _trade_requester(
    trade: LinkedCoinGroupTrade,
    *,
    message_by_key: Mapping[tuple[int, int], StagedCoinGroupMessage],
) -> StagedCoinGroupMessage | None:
    root = message_by_key.get((trade.group_number, trade.root_offer_message_id))
    if root is None:
        return None
    offerer = _actor_identity(root)
    current_id: int | None = trade.confirmation_message_id
    seen: set[int] = set()
    candidates: list[StagedCoinGroupMessage] = []
    while current_id is not None and current_id not in seen and len(seen) < 64:
        seen.add(current_id)
        current = message_by_key.get((trade.group_number, current_id))
        if current is None or current.message_id == root.message_id:
            break
        identity = _actor_identity(current)
        if identity is not None and (offerer is None or identity != offerer):
            candidates.append(current)
        current_id = current.reply_to_message_id
    return candidates[0] if candidates else None


def _syntax_fingerprint(
    text: str,
    *,
    event_type: str,
    offer_index: int = 0,
) -> str:
    """Hash a number-redacted grammar shape; never retain the private text."""

    normalized = normalize_coin_group_text(text)
    skeleton = _SYNTAX_NUMBERS.sub("#", normalized)
    material = f"{event_type.upper()}:{int(offer_index)}:{skeleton}".encode("utf-8")
    return blake2b(
        material,
        digest_size=32,
        person=b"coin-grp-syntax1",
    ).hexdigest()


def _trade_syntax_fingerprint(
    trade: LinkedCoinGroupTrade,
    observation: MarketObservation,
    *,
    message_by_key: Mapping[tuple[int, int], StagedCoinGroupMessage],
) -> str:
    """Bind learned trade grammar to its complete causal reply branch.

    A terminal acknowledgement such as ``برکت`` carries no commodity identity
    by itself.  The digest therefore includes the root-derived market identity
    and every message on the root-to-confirmation chain, with numbers redacted
    by ``_syntax_fingerprint``.  No private text is retained.
    """

    branch_text: list[str] = []
    seen: set[int] = set()
    message_id: int | None = trade.confirmation_message_id
    reached_root = False
    while message_id is not None and message_id not in seen and len(seen) < 64:
        seen.add(message_id)
        message = message_by_key.get((trade.group_number, message_id))
        if message is None:
            break
        branch_text.append(message.text)
        if message_id == trade.root_offer_message_id:
            reached_root = True
            break
        message_id = message.reply_to_message_id
    if reached_root:
        branch_text.reverse()
    context = "\n".join(
        (
            str(observation.instrument),
            str(observation.settlement_term),
            str(observation.trade_form),
            str(observation.side),
            "ROOT_REACHED" if reached_root else "ROOT_NOT_REACHED",
            *branch_text,
        )
    )
    return _syntax_fingerprint(context, event_type="TRADE_BRANCH_V2")


def _safe_price_multiplier(original: int, reviewed: int) -> float | None:
    if original <= 0 or reviewed <= 0:
        return None
    ratio = reviewed / original
    return next(
        (
            candidate
            for candidate in _SAFE_PRICE_MULTIPLIERS
            if math.isclose(ratio, candidate, rel_tol=1e-9, abs_tol=1e-9)
        ),
        None,
    )


def _calibration_from_feedback(
    review: CoinGroupParserFeedback,
    *,
    syntax_fingerprint: str,
    original_price: int,
) -> _ParserPatternCalibration:
    return _ParserPatternCalibration(
        syntax_fingerprint=syntax_fingerprint,
        event_type=review.event_type,
        group_number=review.group_number,
        ambiguous_fields=review.ambiguous_fields,
        event_confirmed=review.event_confirmed,
        commodity_code=review.commodity_code,
        side=review.side,
        settlement_term=review.settlement_term,
        trade_form=review.trade_form,
        is_conditional=review.is_conditional,
        price_multiplier=(
            _safe_price_multiplier(
                original_price,
                review.price_project_thousand_toman,
            )
            if "price" in review.ambiguous_fields
            else None
        ),
        review_revision=review.review_revision,
        reviewed_at_utc=review.reviewed_at_utc,
    )


def _store_parser_calibrations(
    connection: sqlite3.Connection,
) -> list[_ParserPatternCalibration]:
    rows = connection.execute(
        """
        SELECT event_type,source_code,instrument,side,settlement_term,trade_form,
               is_conditional,attributes_json
        FROM market_observations
        WHERE json_extract(attributes_json,'$.human_feedback_syntax_fingerprint')
              IS NOT NULL
          AND json_extract(attributes_json,'$.human_feedback_reviewed_at_utc')
              IS NOT NULL
        ORDER BY available_at_utc,event_key
        """
    ).fetchall()
    calibrations: list[_ParserPatternCalibration] = []
    for row in rows:
        try:
            attributes = json.loads(str(row["attributes_json"] or "{}"))
            fields = frozenset(
                str(item) for item in attributes["human_feedback_fields"]
            )
            fingerprint = str(
                attributes["human_feedback_syntax_fingerprint"]
            )
            reviewed_at = normalize_utc(
                attributes["human_feedback_reviewed_at_utc"],
                field_name="human_feedback_pattern_reviewed_at_utc",
            )
            group_number = int(str(row["source_code"])[-1])
            revision = int(attributes["human_feedback_revision"])
            event_confirmed = bool(attributes["human_feedback_event_confirmed"])
            raw_multiplier = attributes.get("human_feedback_price_multiplier")
            multiplier = (
                float(raw_multiplier) if raw_multiplier is not None else None
            )
            commodity = str(row["instrument"])[len("COIN_") :]
        except (KeyError, TypeError, ValueError):
            continue
        if (
            len(fingerprint) != 64
            or group_number not in {1, 2}
            or not fields
            or multiplier is not None
            and multiplier not in _SAFE_PRICE_MULTIPLIERS
        ):
            continue
        calibrations.append(
            _ParserPatternCalibration(
                syntax_fingerprint=fingerprint,
                event_type=str(row["event_type"]),
                group_number=group_number,
                ambiguous_fields=fields,
                event_confirmed=event_confirmed,
                commodity_code=commodity,
                side=str(row["side"]),
                settlement_term=str(row["settlement_term"]),
                trade_form=str(row["trade_form"]),
                is_conditional=bool(row["is_conditional"]),
                price_multiplier=multiplier,
                review_revision=revision,
                reviewed_at_utc=reviewed_at,
            )
        )
    return calibrations


def _matching_pattern_calibration(
    calibrations: Iterable[_ParserPatternCalibration],
    *,
    syntax_fingerprint: str,
    event_type: str,
    group_number: int,
    available_at_utc: str,
) -> _ParserPatternCalibration | None:
    candidates = [
        item
        for item in calibrations
        if item.syntax_fingerprint == syntax_fingerprint
        and item.event_type == event_type
        and item.group_number == group_number
        and item.reviewed_at_utc <= available_at_utc
    ]
    return max(
        candidates,
        key=lambda item: (item.reviewed_at_utc, item.review_revision),
        default=None,
    )


def _feedback_anchors(
    feedback: Iterable[CoinGroupParserFeedback],
) -> tuple[CoinPriceAnchor, ...]:
    """Convert confirmed reviews to causal anchors; never backdate review knowledge."""

    return tuple(
        CoinPriceAnchor(
            commodity_code=item.commodity_code,
            price_project_thousand_toman=item.price_project_thousand_toman,
            event_time_utc=item.source_event_time_utc,
            available_at_utc=item.reviewed_at_utc,
            settlement_term=item.settlement_term,
            trade_form=item.trade_form,
            evidence_kind="HUMAN_REVIEWED",
        )
        for item in feedback
        if item.event_confirmed and not item.is_conditional
    )


def _reviewed_offer(
    source: CoinGroupMessageInput,
    resolved: ResolvedCoinGroupOffer,
    feedback: Mapping[bytes, CoinGroupParserFeedback],
    *,
    as_of_utc: str,
) -> tuple[ResolvedCoinGroupOffer, CoinGroupParserFeedback | None]:
    offer_index = int(resolved.offer_index)
    key = derive_event_key(
        "coin-group-offer-v1",
        source.group_number,
        source.source_event_id,
        offer_index,
    )
    review = feedback.get(key)
    if (
        review is None
        or review.event_type != "OFFER"
        or review.group_number != int(source.group_number)
        or review.source_event_time_utc
        != normalize_utc(source.published_at_utc, field_name="feedback_offer_event_time")
        or review.reviewed_at_utc > as_of_utc
    ):
        return resolved, None
    return (
        replace(
            resolved,
            commodity_code=review.commodity_code,
            price_project_thousand_toman=review.price_project_thousand_toman,
            quantity=review.quantity,
            side=review.side,
            settlement_term=review.settlement_term,
            trade_form=review.trade_form,
            is_conditional=review.is_conditional,
            quality_state="ELIGIBLE" if review.event_confirmed else "REJECTED",
            resolution_reason=(
                "HUMAN_REVIEWED_FIELD_CORRECTION"
                if review.event_confirmed
                else "HUMAN_REVIEWED_NOT_AN_EVENT"
            ),
            authoritative_anchor_count=max(
                2, int(getattr(resolved, "authoritative_anchor_count", 0))
            ),
        ),
        review,
    )


def _reviewed_observation(
    observation: MarketObservation,
    review: CoinGroupParserFeedback,
    *,
    syntax_fingerprint: str,
) -> MarketObservation:
    price_multiplier = (
        _safe_price_multiplier(
            int(observation.price),
            review.price_project_thousand_toman,
        )
        if "price" in review.ambiguous_fields
        else None
    )
    attributes = dict(observation.attributes)
    field_evidence = dict(attributes.get("field_evidence") or {})
    for field in review.ambiguous_fields:
        evidence_field = _FEEDBACK_TO_EVIDENCE_FIELD[field]
        field_evidence[evidence_field] = ("HUMAN_REVIEWED_CORRECTION",)
    attributes.update(
        {
            "human_feedback_version": COIN_GROUP_PIPELINE_VERSION,
            "human_feedback_revision": review.review_revision,
            "human_feedback_fields": sorted(review.ambiguous_fields),
            "human_feedback_reviewed_at_utc": review.reviewed_at_utc,
            "human_feedback_event_confirmed": review.event_confirmed,
            "human_feedback_syntax_fingerprint": syntax_fingerprint,
            "human_feedback_price_multiplier": price_multiplier,
            "resolution_reason": (
                "HUMAN_REVIEWED_FIELD_CORRECTION"
                if review.event_confirmed
                else "HUMAN_REVIEWED_NOT_AN_EVENT"
            ),
            "field_evidence": field_evidence,
        }
    )
    return replace(
        observation,
        instrument="COIN_" + review.commodity_code,
        market_label="GROUP_COIN_" + review.commodity_code,
        settlement_term=review.settlement_term,
        trade_form=review.trade_form,
        side=review.side,
        price=review.price_project_thousand_toman,
        quantity=review.quantity,
        parse_confidence=1.0 if review.event_confirmed else 0.0,
        parser_version=(
            observation.parser_version
            + f"+human-feedback-r{review.review_revision}"
        ),
        quality_state="ELIGIBLE" if review.event_confirmed else "REJECTED",
        quality_policy_version="coin-group-human-feedback-v1",
        is_conditional=review.is_conditional,
        attributes=attributes,
    )


def _pattern_calibrated_offer(
    offer: ResolvedCoinGroupOffer,
    calibration: _ParserPatternCalibration,
) -> ResolvedCoinGroupOffer:
    # A number-redacted skeleton describes language only.  It cannot safely
    # transfer commodity, price, quantity, or event validity: the same shape
    # (for example ``# تا ف #``) is used for every coin family and both genuine
    # offers and chatter.  Those economic decisions remain exact-event or
    # strictly-prior price-context decisions.
    fields = calibration.ambiguous_fields & _SAFE_PATTERN_FIELDS
    if not fields:
        return offer
    return replace(
        offer,
        side=calibration.side if "side" in fields else offer.side,
        trade_form=(
            calibration.trade_form
            if "trade_form" in fields
            else offer.trade_form
        ),
        is_conditional=(
            calibration.is_conditional
            if "conditional" in fields
            else offer.is_conditional
        ),
        resolution_reason="HUMAN_REVIEWED_LINGUISTIC_SYNTAX_CALIBRATION",
    )


def _pattern_calibrated_observation(
    observation: MarketObservation,
    calibration: _ParserPatternCalibration,
    *,
    apply_economic_fields: bool = True,
    protected_fields: frozenset[str] = frozenset(),
) -> MarketObservation:
    review_fields = calibration.ambiguous_fields
    permitted_fields = review_fields & _SAFE_PATTERN_FIELDS - protected_fields
    fields = (
        permitted_fields
        if apply_economic_fields
        else frozenset()
    )
    skipped_fields = review_fields - permitted_fields
    quality = observation.quality_state
    attributes = dict(observation.attributes)
    field_evidence = dict(attributes.get("field_evidence") or {})
    for field in permitted_fields:
        field_evidence[_FEEDBACK_TO_EVIDENCE_FIELD[field]] = (
            "HUMAN_PATTERN_CALIBRATION",
        )
    attributes.update(
        {
            "human_pattern_calibration_revision": calibration.review_revision,
            "human_pattern_calibration_fields": sorted(review_fields),
            "human_pattern_calibration_reviewed_at_utc": calibration.reviewed_at_utc,
            "resolution_reason": "HUMAN_REVIEWED_LINGUISTIC_SYNTAX_CALIBRATION",
            "field_evidence": field_evidence,
        }
    )
    if skipped_fields:
        attributes["human_pattern_calibration_guarded_fields"] = sorted(
            skipped_fields
        )
    return replace(
        observation,
        trade_form=(
            calibration.trade_form
            if "trade_form" in fields
            else observation.trade_form
        ),
        side=calibration.side if "side" in fields else observation.side,
        parser_version=(
            observation.parser_version
            + f"+human-pattern-r{calibration.review_revision}"
        ),
        parse_confidence=1.0 if quality == "ELIGIBLE" else 0.0,
        quality_state=quality,
        quality_policy_version="coin-group-human-pattern-v1",
        is_conditional=(
            calibration.is_conditional
            if "conditional" in fields
            else observation.is_conditional
        ),
        attributes=attributes,
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
                attributes_json=?, inserted_at_utc=?
            WHERE event_key=?
            """,
            (
                available_at_utc,
                json.dumps(attributes, sort_keys=True, separators=(",", ":")),
                _utc_now(),
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
    parser_feedback: Mapping[bytes, CoinGroupParserFeedback] | None = None,
    reconciliation_horizon_utc: datetime | str | None = None,
    included_message_keys: frozenset[tuple[int, int]] | None = None,
    reconcile_missing_current_facts: bool = True,
) -> CoinGroupPipelineReport:
    """Process current staging idempotently in one caller-owned Store transaction.

    ``additional_anchors`` accepts only caller-normalized causal snapshots; it
    is explicit so this layer can never manufacture a project-unit conversion
    from another market.  The caller must commit/rollback ``market_connection``
    around this function.
    """

    as_of = normalize_utc(as_of_utc, field_name="coin_group_pipeline_as_of_utc")
    feedback = dict(parser_feedback or {})
    pattern_calibrations = _store_parser_calibrations(market_connection)
    pattern_calibrations_applied = 0
    messages = list_current_staged_coin_group_messages(staging_connection, as_of_utc=as_of)
    if included_message_keys is not None:
        messages = [
            message
            for message in messages
            if (message.group_number, message.message_id) in included_message_keys
        ]
    staging_horizon = min((item.event_time_utc for item in messages), default=None)
    reconciliation_horizon = (
        normalize_utc(
            reconciliation_horizon_utc,
            field_name="coin_group_reconciliation_horizon_utc",
        )
        if reconciliation_horizon_utc is not None
        else staging_horizon
    )
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
    ) + tuple(additional_anchors) + _feedback_anchors(feedback.values())
    anchor_index = CoinPriceAnchorIndex(base_anchors)
    all_resolved: dict[tuple[int, int], list] = {}
    explicit_claims: list[_ExplicitClaim] = []
    active_event_keys: set[bytes] = set()
    offer_facts = 0
    eligible_offers = 0
    pending_or_rejected_offers = 0
    applied_feedback_keys: set[bytes] = set()
    for message in messages:
        source = _source(message)
        trade_form, settlement = coin_group_dimensions(
            normalize_coin_group_text(source.text)
        )
        parsed = parse_coin_group_offers(
            source,
            price_context=anchor_index.reference_prices(
                settlement_term=settlement,
                trade_form=trade_form,
                source_event_time_utc=normalize_utc(
                    source.published_at_utc,
                    field_name="coin_group_parser_context_event_time_utc",
                ),
                source_available_at_utc=normalize_utc(
                    source.available_at_utc,
                    field_name="coin_group_parser_context_available_at_utc",
                ),
            ),
        )
        provisional_anchors = _coherent_provisional_anchors(
            explicit_claims,
            source=source,
        )
        resolver_output = resolve_coin_group_offers(
            source,
            anchors=anchor_index,
            parsed_offers=parsed,
            supplemental_anchors=provisional_anchors,
        )
        resolved = []
        offer_reviews: dict[int, CoinGroupParserFeedback] = {}
        offer_pattern_calibrations: dict[int, _ParserPatternCalibration] = {}
        offer_syntax_fingerprints: dict[int, str] = {}
        for item in resolver_output:
            offer_index = int(item.offer_index)
            syntax_fingerprint = _syntax_fingerprint(
                source.text,
                event_type="OFFER",
                offer_index=offer_index,
            )
            offer_syntax_fingerprints[offer_index] = syntax_fingerprint
            reviewed, review = _reviewed_offer(
                source,
                item,
                feedback,
                as_of_utc=as_of,
            )
            if review is not None:
                offer_reviews[offer_index] = review
                applied_feedback_keys.add(review.event_key)
                pattern_calibrations.append(
                    _calibration_from_feedback(
                        review,
                        syntax_fingerprint=syntax_fingerprint,
                        original_price=item.price_project_thousand_toman,
                    )
                )
            else:
                calibration = _matching_pattern_calibration(
                    pattern_calibrations,
                    syntax_fingerprint=syntax_fingerprint,
                    event_type="OFFER",
                    group_number=source.group_number,
                    available_at_utc=normalize_utc(
                        source.available_at_utc,
                        field_name="coin_group_pattern_offer_available_at_utc",
                    ),
                )
                if (
                    calibration is not None
                    and calibration.ambiguous_fields & _SAFE_PATTERN_FIELDS
                ):
                    reviewed = _pattern_calibrated_offer(reviewed, calibration)
                    offer_pattern_calibrations[offer_index] = calibration
                    pattern_calibrations_applied += 1
            resolved.append(reviewed)
        all_resolved[(message.group_number, message.message_id)] = resolved
        observations = resolved_coin_group_observations(
            source,
            anchors=(),
            resolution_available_at_utc=as_of,
            resolved_offers=resolved,
        )
        for offer_index, observation in enumerate(observations):
            review = feedback.get(observation.event_key)
            if review is not None and review.event_key in applied_feedback_keys:
                observation = _reviewed_observation(
                    observation,
                    review,
                    syntax_fingerprint=offer_syntax_fingerprints[offer_index],
                )
            else:
                calibration = offer_pattern_calibrations.get(offer_index)
                if calibration is not None:
                    observation = _pattern_calibrated_observation(
                        observation,
                        calibration,
                        apply_economic_fields=False,
                    )
            active_event_keys.add(observation.event_key)
            _remember_research_context(
                staging_connection,
                event_key=observation.event_key,
                root=message,
            )
            offer_facts += int(
                _upsert_if_semantically_changed(market_connection, observation)
            )
        eligible_offers += sum(item.quality_state == "ELIGIBLE" for item in resolved)
        pending_or_rejected_offers += sum(item.quality_state != "ELIGIBLE" for item in resolved)
        for item in resolved:
            if (
                item.quality_state != "ELIGIBLE"
                or item.commodity_code is None
                or item.is_conditional
            ):
                continue
            review = offer_reviews.get(int(item.offer_index))
            anchor_index.add(
                CoinPriceAnchor(
                    commodity_code=item.commodity_code,
                    price_project_thousand_toman=item.price_project_thousand_toman,
                    event_time_utc=message.event_time_utc,
                    available_at_utc=(
                        review.reviewed_at_utc
                        if review is not None
                        else message.available_at_utc
                    ),
                    settlement_term=item.settlement_term,
                    trade_form=item.trade_form,
                    evidence_kind=(
                        "HUMAN_REVIEWED"
                        if review is not None
                        else "GROUP_DERIVED"
                    ),
                )
            )
        if message.sender_digest is not None:
            for candidate_index, candidate in enumerate(parsed):
                candidate_key = derive_event_key(
                    "coin-group-offer-v1",
                    message.group_number,
                    message.message_id,
                    candidate_index,
                )
                if candidate_key in applied_feedback_keys:
                    continue
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
    reviewed_trade_observations: list[MarketObservation] = []
    message_by_key = {
        (message.group_number, message.message_id): message for message in messages
    }
    for trade, observation in zip(trades, observations, strict=True):
        syntax_fingerprint = _trade_syntax_fingerprint(
            trade,
            observation,
            message_by_key=message_by_key,
        )
        review = feedback.get(observation.event_key)
        if (
            review is not None
            and review.event_type == "TRADE"
            and review.group_number == int(observation.source_code[-1])
            and review.source_event_time_utc
            == normalize_utc(observation.event_time_utc, field_name="feedback_trade_event_time")
            and review.reviewed_at_utc <= as_of
        ):
            original_price = int(observation.price)
            observation = _reviewed_observation(
                observation,
                review,
                syntax_fingerprint=syntax_fingerprint,
            )
            applied_feedback_keys.add(review.event_key)
            pattern_calibrations.append(
                _calibration_from_feedback(
                    review,
                    syntax_fingerprint=syntax_fingerprint,
                    original_price=original_price,
                )
            )
        else:
            calibration = _matching_pattern_calibration(
                pattern_calibrations,
                syntax_fingerprint=syntax_fingerprint,
                event_type="TRADE",
                group_number=trade.group_number,
                available_at_utc=normalize_utc(
                    trade.available_at_utc,
                    field_name="coin_group_pattern_trade_available_at_utc",
                ),
            )
            if (
                calibration is not None
                and (
                    calibration.ambiguous_fields & _SAFE_PATTERN_FIELDS
                )
                - _TRADE_ROOT_DERIVED_FIELDS
            ):
                observation = _pattern_calibrated_observation(
                    observation,
                    calibration,
                    protected_fields=_TRADE_ROOT_DERIVED_FIELDS,
                )
                pattern_calibrations_applied += 1
        reviewed_trade_observations.append(observation)
        active_event_keys.add(observation.event_key)
        root_message = message_by_key.get(
            (trade.group_number, trade.root_offer_message_id)
        )
        requester = _trade_requester(trade, message_by_key=message_by_key)
        if root_message is not None:
            _remember_research_context(
                staging_connection,
                event_key=observation.event_key,
                root=root_message,
                requester_message_id=(requester.message_id if requester else None),
                requester_expires_at_utc=(
                    requester.expires_at_utc if requester else None
                ),
            )
        trade_facts += int(
            _upsert_if_semantically_changed(market_connection, observation)
        )
    retracted_facts = (
        _reconcile_missing_current_facts(
            market_connection,
            active_event_keys=active_event_keys,
            staging_horizon_utc=reconciliation_horizon,
            available_at_utc=as_of,
        )
        if reconcile_missing_current_facts
        else 0
    )
    return CoinGroupPipelineReport(
        staged_messages_seen=len(messages),
        offer_facts_upserted=offer_facts,
        eligible_offers=eligible_offers,
        pending_or_rejected_offers=pending_or_rejected_offers,
        trade_facts_upserted=trade_facts,
        eligible_trades=sum(
            item.quality_state == "ELIGIBLE" for item in reviewed_trade_observations
        ),
        pending_or_rejected_trades=sum(
            item.quality_state != "ELIGIBLE" for item in reviewed_trade_observations
        ),
        root_messages_not_trade_linkable=not_linkable,
        retracted_facts=retracted_facts,
        feedback_reviews_seen=len(feedback),
        feedback_reviews_applied=len(applied_feedback_keys),
        feedback_pattern_calibrations_applied=pattern_calibrations_applied,
        applied_feedback_event_keys=tuple(sorted(applied_feedback_keys)),
    )
