"""Causal projection of exact-event coin-group reviews into Market Store.

The transient private-message staging area is deliberately retained for only
three days.  A supervised review may finish after that horizon, when replaying
the raw message is no longer possible.  This module applies only a complete
review tied to the existing opaque event key.  It does not infer a syntax
pattern, recover raw text, or backdate the reviewed knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Iterable

from .coin_group_feedback import CoinGroupParserFeedback
from .market_contracts import MarketObservation, normalize_utc
from .market_store import upsert_observation


REVIEW_PROJECTION_VERSION = "coin-group-supervised-review-v1"
TRADE_RECONCILIATION_VERSION = "coin-group-supervised-trade-reconciliation-v1"
_HARD_TRADE_BLOCKERS = frozenset(
    {
        "COUNTERPARTY_DECLARATION_REQUIRES_OFFERER_CONFIRMATION",
        "NON_AGGREGATE_FILL_EXCEEDS_REMAINING_ROOT_QUANTITY",
        "PARTICIPANT_REJECTION_AFTER_CONFIRMATION",
    }
)
_FIELD_EVIDENCE = {
    "event_validity": "event_validity",
    "commodity": "commodity",
    "side": "side",
    "price": "price",
    "quantity": "quantity",
    "settlement": "settlement_term",
    "trade_form": "trade_form",
    "conditional": "is_conditional",
}


def _review_parser_version(revision: int) -> str:
    """Use the established bridge-safe namespace for human decisions."""

    return f"human-feedback-r{revision}"


def _trade_parser_version(revision: int) -> str:
    return f"human-feedback-r{revision}-trade-root"


class CoinGroupReviewProjectionError(ValueError):
    """A redacted exact-event projection contract failure."""


@dataclass(frozen=True, slots=True)
class CoinGroupReviewProjectionReport:
    submitted: int
    projected: int
    eligible: int
    rejected: int
    unchanged: int
    event_keys: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class CoinGroupTradeReconciliationReport:
    considered: int
    projected: int
    eligible: int
    rejected: int
    unchanged: int


def _row_for_review(
    connection: sqlite3.Connection,
    review: CoinGroupParserFeedback,
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM market_observations WHERE event_key=?",
        (review.event_key,),
    ).fetchone()
    if row is None:
        raise CoinGroupReviewProjectionError("review_projection_event_missing")
    if (
        str(row["source_code"]) != f"GROUP_{review.group_number}"
        or str(row["source_family"]) != "GROUP"
        or str(row["event_type"]) != review.event_type
        or normalize_utc(
            str(row["event_time_utc"]), field_name="review_projection_event_time"
        )
        != review.source_event_time_utc
        or str(row["price_unit"]) != "PROJECT_THOUSAND_TOMAN"
    ):
        raise CoinGroupReviewProjectionError("review_projection_event_mismatch")
    return row


def _attributes(row: sqlite3.Row) -> dict[str, object]:
    try:
        value = json.loads(str(row["attributes_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CoinGroupReviewProjectionError(
            "review_projection_attributes_invalid"
        ) from exc
    if not isinstance(value, dict):
        raise CoinGroupReviewProjectionError("review_projection_attributes_invalid")
    return value


def _reviewed_observation(
    row: sqlite3.Row,
    review: CoinGroupParserFeedback,
) -> MarketObservation:
    attributes = _attributes(row)
    evidence = attributes.get("field_evidence") or {}
    if not isinstance(evidence, dict):
        raise CoinGroupReviewProjectionError("review_projection_evidence_invalid")
    evidence = dict(evidence)
    for field in review.ambiguous_fields:
        evidence[_FIELD_EVIDENCE[field]] = ["SUPERVISED_EXACT_EVENT_REVIEW"]
    attributes.update(
        {
            "supervised_review_version": REVIEW_PROJECTION_VERSION,
            "supervised_review_revision": review.review_revision,
            "supervised_review_fields": sorted(review.ambiguous_fields),
            "supervised_reviewed_at_utc": review.reviewed_at_utc,
            "supervised_review_event_confirmed": review.event_confirmed,
            "resolution_reason": (
                "SUPERVISED_EXACT_EVENT_CORRECTION"
                if review.event_confirmed
                else "SUPERVISED_NOT_A_RELIABLE_EVENT"
            ),
            "field_evidence": evidence,
        }
    )
    attributes.setdefault("pre_review_parser_version", str(row["parser_version"]))
    # Knowledge from a later review must never be visible before the review.
    available_at = max(str(row["available_at_utc"]), review.reviewed_at_utc)
    return MarketObservation(
        event_key=review.event_key,
        source_code=str(row["source_code"]),
        source_family=str(row["source_family"]),
        event_time_utc=str(row["event_time_utc"]),
        available_at_utc=available_at,
        instrument="COIN_" + review.commodity_code,
        market_label="GROUP_COIN_" + review.commodity_code,
        settlement_term=review.settlement_term,
        trade_form=review.trade_form,
        event_type=review.event_type,
        side=review.side,
        price=review.price_project_thousand_toman,
        price_unit=str(row["price_unit"]),
        currency=str(row["currency"]),
        quantity=review.quantity,
        quantity_unit=str(row["quantity_unit"] or "COIN"),
        parse_confidence=1.0 if review.event_confirmed else 0.0,
        parser_version=_review_parser_version(review.review_revision),
        quality_state="ELIGIBLE" if review.event_confirmed else "REJECTED",
        quality_policy_version=REVIEW_PROJECTION_VERSION,
        is_conditional=review.is_conditional,
        attributes=attributes,
    )


def project_coin_group_reviews(
    connection: sqlite3.Connection,
    reviews: Iterable[CoinGroupParserFeedback],
) -> CoinGroupReviewProjectionReport:
    """Project a fully validated exact-event batch in the caller transaction."""

    ordered = tuple(sorted(reviews, key=lambda item: item.event_key))
    if len({item.event_key for item in ordered}) != len(ordered):
        raise CoinGroupReviewProjectionError("review_projection_duplicate_event")
    rows = tuple((review, _row_for_review(connection, review)) for review in ordered)
    projected = eligible = rejected = unchanged = 0
    for review, row in rows:
        attributes = _attributes(row)
        if (
            int(attributes.get("supervised_review_revision") or 0)
            == review.review_revision
            and str(row["quality_policy_version"]) == REVIEW_PROJECTION_VERSION
            and str(row["parser_version"])
            == _review_parser_version(review.review_revision)
        ):
            unchanged += 1
            continue
        upsert_observation(connection, _reviewed_observation(row, review))
        projected += 1
        eligible += int(review.event_confirmed)
        rejected += int(not review.event_confirmed)
    return CoinGroupReviewProjectionReport(
        submitted=len(ordered),
        projected=projected,
        eligible=eligible,
        rejected=rejected,
        unchanged=unchanged,
        event_keys=tuple(item.event_key for item in ordered),
    )


def _root_key(attributes: dict[str, object]) -> bytes:
    value = str(attributes.get("root_offer_event_key") or "").strip().lower()
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise CoinGroupReviewProjectionError(
            "trade_reconciliation_root_key_invalid"
        ) from exc
    if not 16 <= len(key) <= 64:
        raise CoinGroupReviewProjectionError("trade_reconciliation_root_key_invalid")
    return key


def _root_only_reason(value: object) -> bool:
    reasons = tuple(item.strip() for item in str(value or "").split(";") if item.strip())
    return bool(reasons) and all(
        item.startswith("ROOT_OFFER_NOT_MODEL_ELIGIBLE:") for item in reasons
    )


def _has_hard_trade_blocker(value: object) -> bool:
    return bool(
        _HARD_TRADE_BLOCKERS.intersection(
            item.strip() for item in str(value or "").split(";") if item.strip()
        )
    )


def reconcile_pending_trades_from_reviewed_roots(
    connection: sqlite3.Connection,
    *,
    cutoff_utc: str,
) -> CoinGroupTradeReconciliationReport:
    """Resolve pending trades only when their exact root offer was reviewed.

    A trade is eligible only when root ineligibility was its sole blocker and
    the reviewed root is eligible.  Rejections, overfills, unconfirmed
    counterparty declarations, post-confirmation rejections, and unsafe
    negotiated dimensions remain outside the model and are explicitly marked
    rejected rather than silently accepted.
    """

    cutoff = normalize_utc(cutoff_utc, field_name="trade_reconciliation_cutoff")
    rows = connection.execute(
        "SELECT * FROM market_observations "
        "WHERE source_code IN ('GROUP_1','GROUP_2') AND event_type='TRADE' "
        "AND event_time_utc>=? AND ("
        "quality_state IN ('PENDING_REVIEW','AMBIGUOUS') OR "
        "json_extract(attributes_json,'$.supervised_root_review_version')=?"
        ") "
        "ORDER BY event_time_utc,event_key",
        (cutoff, TRADE_RECONCILIATION_VERSION),
    ).fetchall()
    prepared: list[
        tuple[sqlite3.Row, sqlite3.Row, dict[str, object], int, str]
    ] = []
    for row in rows:
        attributes = _attributes(row)
        root = connection.execute(
            "SELECT * FROM market_observations WHERE event_key=?",
            (_root_key(attributes),),
        ).fetchone()
        if root is None:
            raise CoinGroupReviewProjectionError(
                "trade_reconciliation_root_missing"
            )
        root_attributes = _attributes(root)
        revision = int(root_attributes.get("supervised_review_revision") or 0)
        original_reason = str(
            attributes.get("pre_review_resolution_reason")
            or attributes.get("resolution_reason")
            or ""
        )
        if revision <= 0 and not _has_hard_trade_blocker(original_reason):
            continue
        reviewed_at = str(
            root_attributes.get("supervised_reviewed_at_utc")
            or root["available_at_utc"]
        )
        if revision > 0 and not reviewed_at:
            raise CoinGroupReviewProjectionError(
                "trade_reconciliation_root_review_invalid"
            )
        prepared.append((row, root, attributes, revision, reviewed_at))
    projected = eligible = rejected = unchanged = 0
    for row, root, attributes, revision, reviewed_at in prepared:
        if (
            attributes.get("supervised_root_review_version")
            == TRADE_RECONCILIATION_VERSION
            and int(attributes.get("supervised_root_review_revision") or 0)
            == revision
            and str(row["parser_version"]) == _trade_parser_version(revision)
        ):
            unchanged += 1
            continue
        original_reason = str(
            attributes.get("pre_review_resolution_reason")
            or attributes.get("resolution_reason")
            or ""
        )
        is_eligible = (
            revision > 0
            and str(root["quality_state"]) == "ELIGIBLE"
            and _root_only_reason(original_reason)
        )
        attributes.setdefault("pre_review_resolution_reason", original_reason)
        attributes.update(
            {
                "supervised_root_review_version": TRADE_RECONCILIATION_VERSION,
                "supervised_root_review_revision": revision,
                "supervised_root_reviewed_at_utc": reviewed_at,
                "resolution_reason": (
                    "SUPERVISED_REVIEWED_ROOT_TRADE"
                    if is_eligible
                    else "SUPERVISED_TRADE_NOT_MODEL_ELIGIBLE"
                ),
            }
        )
        attributes.setdefault("pre_review_parser_version", str(row["parser_version"]))
        available_at = max(str(row["available_at_utc"]), str(root["available_at_utc"]))
        upsert_observation(
            connection,
            MarketObservation(
                event_key=bytes(row["event_key"]),
                source_code=str(row["source_code"]),
                source_family=str(row["source_family"]),
                event_time_utc=str(row["event_time_utc"]),
                available_at_utc=available_at,
                instrument=str(root["instrument"]),
                market_label=str(root["market_label"]),
                settlement_term=str(row["settlement_term"]),
                trade_form=str(row["trade_form"]),
                event_type="TRADE",
                side=str(row["side"]),
                price=str(row["price_value"]),
                price_unit=str(row["price_unit"]),
                currency=str(row["currency"]),
                quantity=(
                    str(row["quantity_value"])
                    if row["quantity_value"] is not None
                    else None
                ),
                quantity_unit=(
                    str(row["quantity_unit"])
                    if row["quantity_unit"] is not None
                    else None
                ),
                parse_confidence=1.0 if is_eligible else 0.0,
                parser_version=_trade_parser_version(revision),
                quality_state="ELIGIBLE" if is_eligible else "REJECTED",
                quality_policy_version=TRADE_RECONCILIATION_VERSION,
                is_conditional=bool(row["is_conditional"]),
                attributes=attributes,
            ),
        )
        projected += 1
        eligible += int(is_eligible)
        rejected += int(not is_eligible)
    return CoinGroupTradeReconciliationReport(
        considered=len(prepared),
        projected=projected,
        eligible=eligible,
        rejected=rejected,
        unchanged=unchanged,
    )


__all__ = [
    "CoinGroupReviewProjectionError",
    "CoinGroupReviewProjectionReport",
    "CoinGroupTradeReconciliationReport",
    "REVIEW_PROJECTION_VERSION",
    "TRADE_RECONCILIATION_VERSION",
    "project_coin_group_reviews",
    "reconcile_pending_trades_from_reviewed_roots",
]
