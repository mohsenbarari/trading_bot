"""Strict, transport-free decoding for private melted-gold event envelopes.

The private crawler uses distinct Telegram channels for offer events and for
their delayed verifier updates.  Channel routing is supplied by the collector
as a trusted stream value; the inner envelope must independently agree with
that role before temporary raw staging accepts it.  One post may carry an
object, list, or delimiter-separated batch of JSON objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
import sqlite3
from typing import Any, Iterable, Mapping

from .market_contracts import MarketStoreContractError, normalize_utc
from .private_gold_staging import (
    PrivateGoldStagingError,
    PrivateGoldStagingOffer,
    PrivateGoldStagingTradeUpdate,
    stage_private_gold_offer,
    stage_private_gold_trade_update,
)


PRIVATE_GOLD_PAYLOAD_DECODER_VERSION = "private-gold-json-v1"
_BATCH_DIVIDER = re.compile(r"(?:\r?\n\s*)+[━─—-]{3,}\s*(?:\r?\n\s*)+")
_OFFER_STREAM = "OFFER"
_TRADE_STREAM = "TRADE"
_SOURCE_KEY = "account1_channel"
_TRADE_STATUSES = frozenset({"NONE", "FULL", "PARTIAL", "CHANGED_UNCLASSIFIED", "PENDING"})
_TRADE_STATUS_ALIASES = {"NO_TRADE": "NONE", "COMPLETED": "FULL", "TRADED": "FULL"}


@dataclass(frozen=True, slots=True)
class PrivateGoldPayloadEnvelope:
    """A raw outer Telegram post, routed by the trusted collector channel."""

    payload_text: str
    available_at_utc: datetime | str
    stream: str


@dataclass(frozen=True, slots=True)
class DecodedPrivateGoldPayload:
    """Transient events ready for bounded private staging, never Market Store."""

    offers: tuple[PrivateGoldStagingOffer, ...]
    trade_updates: tuple[PrivateGoldStagingTradeUpdate, ...]
    invalid_items: int
    duplicate_items: int
    conflicting_items: int


@dataclass(frozen=True, slots=True)
class PrivateGoldPayloadStageReport:
    """Privacy-safe counters only; neither text nor identifiers are exposed."""

    decoded_offers: int
    decoded_trade_updates: int
    inserted_or_updated_offers: int
    inserted_or_updated_trade_updates: int
    idempotent_replays: int
    staging_rejected_items: int
    invalid_items: int
    duplicate_items: int
    conflicting_items: int


def _stream(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in {_OFFER_STREAM, _TRADE_STREAM}:
        raise ValueError("private_gold_payload_stream_invalid")
    return normalized


def _message_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 64 or not normalized.isascii() or not normalized.isdecimal():
        return None
    return normalized


def _decode_json_objects(payload_text: str) -> tuple[list[Mapping[str, Any]], int]:
    """Decode only the documented object/list/batch forms, never prose."""

    text = str(payload_text or "").strip()
    if not text:
        return [], 1
    candidates: list[object] = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        for segment in _BATCH_DIVIDER.split(text):
            segment = segment.strip()
            if not segment:
                continue
            try:
                candidates.append(json.loads(segment))
            except json.JSONDecodeError:
                candidates.append(None)
    objects: list[Mapping[str, Any]] = []
    invalid = 0
    for candidate in candidates:
        items: Iterable[object] = candidate if isinstance(candidate, list) else (candidate,)
        for item in items:
            if isinstance(item, Mapping):
                objects.append(item)
            else:
                invalid += 1
    return objects, invalid


def _utc(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    try:
        return normalize_utc(value, field_name=field_name)  # type: ignore[arg-type]
    except MarketStoreContractError:
        return None


def _inner_is_valid(item: Mapping[str, Any], *, stream: str) -> Mapping[str, Any] | None:
    if str(item.get("schema_version") or "").strip() != "1.0":
        return None
    source = item.get("source")
    gold = item.get("gold")
    if not isinstance(source, Mapping) or not isinstance(gold, Mapping):
        return None
    if (
        str(source.get("market") or "").strip().lower() != "gold"
        or str(source.get("source_key") or "").strip() != _SOURCE_KEY
    ):
        return None
    expected_event_type = "message_created" if stream == _OFFER_STREAM else "offer_verified"
    if str(item.get("event_type") or "").strip() != expected_event_type:
        return None
    return gold


def _offer_from_item(
    item: Mapping[str, Any],
    *,
    available_at_utc: str,
) -> PrivateGoldStagingOffer | None:
    gold = _inner_is_valid(item, stream=_OFFER_STREAM)
    if gold is None or not str(gold.get("message_type") or "").strip():
        return None
    message_id = _message_id(gold.get("message_id"))
    text = gold.get("text")
    event_time = _utc(gold.get("telegram_datetime"), field_name="private_gold_payload_telegram_datetime")
    edited = _utc(
        gold.get("telegram_edit_datetime"),
        field_name="private_gold_payload_telegram_edit_datetime",
    )
    if message_id is None or not isinstance(text, str) or not text.strip() or event_time is None:
        return None
    if available_at_utc < event_time or (edited is not None and edited < event_time):
        return None
    return PrivateGoldStagingOffer(
        source_message_id=message_id,
        event_time_utc=event_time,
        available_at_utc=available_at_utc,
        text=text,
        edited_at_utc=edited,
    )


def _status(value: object) -> str | None:
    normalized = str(value or "").strip().upper()
    normalized = _TRADE_STATUS_ALIASES.get(normalized, normalized)
    return normalized if normalized in _TRADE_STATUSES else None


def _positive_quantity(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None
    return quantity if quantity > 0 else None


def _trade_from_item(
    item: Mapping[str, Any],
    *,
    available_at_utc: str,
) -> PrivateGoldStagingTradeUpdate | None:
    gold = _inner_is_valid(item, stream=_TRADE_STREAM)
    if gold is None or not isinstance(gold.get("verification"), Mapping):
        return None
    message_id = _message_id(gold.get("message_id"))
    trade = gold.get("trade")
    if message_id is None:
        return None
    verification = gold["verification"]
    verification_result = str(verification.get("result") or "").strip().lower()
    if trade is None:
        # The verifier emits no ``trade`` object for the overwhelmingly common
        # explicit no-trade result.  It is still economically important: it
        # overrides the source convention that an edited offer means a trade.
        if verification_result != "no_trade":
            return None
        return PrivateGoldStagingTradeUpdate(
            source_message_id=message_id,
            available_at_utc=available_at_utc,
            trade_status="NONE",
        )
    if not isinstance(trade, Mapping):
        return None
    if verification_result not in {"", "traded"}:
        return None
    status = _status(trade.get("status"))
    if status is None:
        return None
    detected = _utc(
        trade.get("trade_detected_at"),
        field_name="private_gold_payload_trade_detected_at",
    )
    edited = _utc(
        trade.get("telegram_edit_datetime"),
        field_name="private_gold_payload_trade_edited_at",
    )
    if detected is not None and available_at_utc < detected:
        return None
    if edited is not None and available_at_utc < edited:
        return None
    if status in {"FULL", "PARTIAL"} and detected is None and edited is None:
        return None
    quantity_value = trade.get("traded_quantity")
    quantity = _positive_quantity(quantity_value)
    if quantity_value is not None and quantity is None:
        return None
    return PrivateGoldStagingTradeUpdate(
        source_message_id=message_id,
        available_at_utc=available_at_utc,
        trade_status=status,
        traded_quantity=quantity,
        trade_detected_at_utc=detected,
        telegram_edit_datetime=edited,
    )


def _newer_offer(
    current: PrivateGoldStagingOffer,
    candidate: PrivateGoldStagingOffer,
) -> PrivateGoldStagingOffer | None:
    if current == candidate:
        return current
    if current.edited_at_utc is None and candidate.edited_at_utc is not None:
        return candidate
    if candidate.edited_at_utc is None and current.edited_at_utc is not None:
        return current
    if current.edited_at_utc is not None and candidate.edited_at_utc is not None:
        if str(candidate.edited_at_utc) > str(current.edited_at_utc):
            return candidate
        if str(current.edited_at_utc) > str(candidate.edited_at_utc):
            return current
    return None


def _trade_order_key(value: PrivateGoldStagingTradeUpdate) -> str | None:
    return max(
        (str(item) for item in (value.telegram_edit_datetime, value.trade_detected_at_utc) if item is not None),
        default=None,
    )


def _newer_trade(
    current: PrivateGoldStagingTradeUpdate,
    candidate: PrivateGoldStagingTradeUpdate,
) -> PrivateGoldStagingTradeUpdate | None:
    if current == candidate:
        return current
    current_time = _trade_order_key(current)
    candidate_time = _trade_order_key(candidate)
    if current_time is None or candidate_time is None:
        return None
    if candidate_time > current_time:
        return candidate
    if current_time > candidate_time:
        return current
    return None


def decode_private_gold_payload(envelope: PrivateGoldPayloadEnvelope) -> DecodedPrivateGoldPayload:
    """Decode one outer post and reject any wrong-channel or ambiguous item."""

    stream = _stream(envelope.stream)
    try:
        available_at = normalize_utc(
            envelope.available_at_utc,
            field_name="private_gold_payload_available_at_utc",
        )
    except MarketStoreContractError as exc:
        raise ValueError("private_gold_payload_available_at_invalid") from exc
    items, invalid_items = _decode_json_objects(envelope.payload_text)
    offers: dict[str, PrivateGoldStagingOffer] = {}
    trades: dict[str, PrivateGoldStagingTradeUpdate] = {}
    conflicts: set[str] = set()
    duplicates = 0
    for item in items:
        candidate = (
            _offer_from_item(item, available_at_utc=available_at)
            if stream == _OFFER_STREAM
            else _trade_from_item(item, available_at_utc=available_at)
        )
        if candidate is None:
            invalid_items += 1
            continue
        message_id = candidate.source_message_id
        if message_id in conflicts:
            invalid_items += 1
            continue
        target: dict[str, Any] = offers if stream == _OFFER_STREAM else trades
        current = target.get(message_id)
        if current is None:
            target[message_id] = candidate
            continue
        selected = (
            _newer_offer(current, candidate)
            if stream == _OFFER_STREAM
            else _newer_trade(current, candidate)
        )
        if selected is None:
            target.pop(message_id, None)
            conflicts.add(message_id)
            continue
        target[message_id] = selected
        duplicates += 1
    return DecodedPrivateGoldPayload(
        offers=tuple(sorted(offers.values(), key=lambda item: (str(item.event_time_utc), item.source_message_id))),
        trade_updates=tuple(sorted(trades.values(), key=lambda item: (str(item.available_at_utc), item.source_message_id))),
        invalid_items=invalid_items,
        duplicate_items=duplicates,
        conflicting_items=len(conflicts),
    )


def stage_private_gold_payload(
    connection: sqlite3.Connection,
    envelope: PrivateGoldPayloadEnvelope,
    *,
    staged_at_utc: datetime | str | None = None,
) -> PrivateGoldPayloadStageReport:
    """Stage only channel-consistent private events; caller owns transactions."""

    decoded = decode_private_gold_payload(envelope)
    changed_offers = 0
    changed_trades = 0
    rejected_by_staging = 0
    for offer in decoded.offers:
        try:
            changed_offers += int(stage_private_gold_offer(connection, offer, staged_at_utc=staged_at_utc))
        except PrivateGoldStagingError:
            # Validation remains fail-closed even when a future decoder change
            # accidentally becomes less strict than the staging contract.
            rejected_by_staging += 1
    for update in decoded.trade_updates:
        try:
            changed_trades += int(stage_private_gold_trade_update(connection, update, staged_at_utc=staged_at_utc))
        except PrivateGoldStagingError:
            rejected_by_staging += 1
    decoded_count = len(decoded.offers) + len(decoded.trade_updates)
    changed_count = changed_offers + changed_trades
    return PrivateGoldPayloadStageReport(
        decoded_offers=len(decoded.offers),
        decoded_trade_updates=len(decoded.trade_updates),
        inserted_or_updated_offers=changed_offers,
        inserted_or_updated_trade_updates=changed_trades,
        idempotent_replays=decoded_count - changed_count - rejected_by_staging,
        staging_rejected_items=rejected_by_staging,
        invalid_items=decoded.invalid_items,
        duplicate_items=decoded.duplicate_items,
        conflicting_items=decoded.conflicting_items,
    )


__all__ = [
    "DecodedPrivateGoldPayload",
    "PRIVATE_GOLD_PAYLOAD_DECODER_VERSION",
    "PrivateGoldPayloadEnvelope",
    "PrivateGoldPayloadStageReport",
    "decode_private_gold_payload",
    "stage_private_gold_payload",
]
