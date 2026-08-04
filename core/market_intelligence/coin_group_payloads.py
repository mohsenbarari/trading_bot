"""Strict decoder for private coin-group JSON envelopes.

One Telegram post may carry one event, an array of events, or several JSON
objects separated by the documented horizontal-rule divider.  This module is
transport-free: a collector supplies the outer post's *trusted availability*
timestamp and receives only transient staging messages plus safe counters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
import sqlite3
from typing import Any, Iterable, Mapping

from .coin_group_staging import CoinGroupStagingMessage, stage_coin_group_message
from .market_contracts import MarketStoreContractError, normalize_utc


COIN_GROUP_PAYLOAD_DECODER_VERSION = "coin-group-json-v1"
_BATCH_DIVIDER = re.compile(r"(?:\r?\n\s*)+[━─—-]{3,}\s*(?:\r?\n\s*)+")
_SOURCE_TO_GROUP = {"account2_group1": 1, "account2_group2": 2}
_EVENT_TYPES = frozenset({"message_created", "message_updated"})
_REPLY_RESOLVED = frozenset(
    {"resolved_from_dom", "resolved_from_navigation", "resolved_from_unique_preview_match"}
)


@dataclass(frozen=True, slots=True)
class CoinGroupPayloadEnvelope:
    """One private Telegram post, with availability set by the local collector."""

    payload_text: str
    available_at_utc: datetime | str


@dataclass(frozen=True, slots=True)
class DecodedCoinGroupPayload:
    """Transient decoder result; its messages may be passed to private staging."""

    messages: tuple[CoinGroupStagingMessage, ...]
    invalid_items: int
    duplicate_items: int
    conflicting_items: int


@dataclass(frozen=True, slots=True)
class CoinGroupPayloadStageReport:
    """Counters only; no raw text or private source identity crosses this boundary."""

    decoded_messages: int
    inserted_or_updated_messages: int
    idempotent_replays: int
    invalid_items: int
    duplicate_items: int
    conflicting_items: int


def _positive_decimal_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        candidate = int(value)
    else:
        return None
    return candidate if 0 < candidate <= 9_223_372_036_854_775_807 else None


def _decode_json_objects(payload_text: str) -> tuple[list[Mapping[str, Any]], int]:
    """Decode only documented object/list forms; arbitrary prose is rejected."""

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
                # A malformed sibling cannot poison a valid sibling, but it
                # must never be guessed into an event.
                candidates.append(None)
    objects: list[Mapping[str, Any]] = []
    invalid = 0
    for candidate in candidates:
        items: Iterable[object]
        if isinstance(candidate, list):
            items = candidate
        else:
            items = (candidate,)
        for item in items:
            if isinstance(item, Mapping):
                objects.append(item)
            else:
                invalid += 1
    return objects, invalid


def _decode_item(item: Mapping[str, Any], *, available_at_utc: str) -> CoinGroupStagingMessage | None:
    source = item.get("source")
    payload = item.get("coin")
    if not isinstance(source, Mapping) or not isinstance(payload, Mapping):
        return None
    if str(source.get("market") or "").strip().lower() != "coin":
        return None
    source_key = str(source.get("source_key") or "").strip()
    group_number = _SOURCE_TO_GROUP.get(source_key)
    if group_number is None or str(item.get("event_type") or "").strip() not in _EVENT_TYPES:
        return None
    message_id = _positive_decimal_id(payload.get("message_id"))
    if message_id is None:
        return None
    text = payload.get("text")
    event_time = payload.get("telegram_datetime")
    if not isinstance(text, str) or not text.strip() or event_time is None:
        return None
    try:
        event_time_utc = normalize_utc(event_time, field_name="coin_group_payload_telegram_datetime")
        edited_at_utc = (
            normalize_utc(
                payload.get("telegram_edit_datetime"),
                field_name="coin_group_payload_telegram_edit_datetime",
            )
            if payload.get("telegram_edit_datetime") is not None
            else None
        )
    except MarketStoreContractError:
        return None
    if available_at_utc < event_time_utc or (
        edited_at_utc is not None and edited_at_utc < event_time_utc
    ):
        return None
    reply_id: int | None = None
    if bool(payload.get("reply_detected")) and str(payload.get("reply_reference_status") or "") in _REPLY_RESOLVED:
        reply_id = _positive_decimal_id(payload.get("reply_message_id"))
        if reply_id is None:
            return None
    # Peer identity, when the scraper exposed it, is more stable than a
    # display name for checking that an offerer accepted a reply.  Either
    # value remains transient and is immediately one-way hashed by staging.
    peer_identity = payload.get("sender_peer_id")
    sender_name = payload.get("sender_name")
    if isinstance(peer_identity, (str, int)) and str(peer_identity).strip():
        sender = "peer:" + str(peer_identity).strip()
    elif isinstance(sender_name, str):
        sender = "name:" + sender_name
    else:
        sender = None
    return CoinGroupStagingMessage(
        group_number=group_number,
        message_id=message_id,
        event_time_utc=event_time_utc,
        available_at_utc=available_at_utc,
        text=text,
        reply_to_message_id=reply_id,
        sender_identity=sender,
        edited_at_utc=edited_at_utc,
    )


def _newer_edit(
    current: CoinGroupStagingMessage,
    candidate: CoinGroupStagingMessage,
) -> CoinGroupStagingMessage | None:
    """Select a version only when edit evidence is strictly ordered."""

    if current == candidate:
        return current
    current_edit = current.edited_at_utc
    candidate_edit = candidate.edited_at_utc
    if current_edit is None and candidate_edit is not None:
        return candidate
    if candidate_edit is None and current_edit is not None:
        return current
    if current_edit is not None and candidate_edit is not None:
        if str(candidate_edit) > str(current_edit):
            return candidate
        if str(current_edit) > str(candidate_edit):
            return current
    return None


def decode_coin_group_payload(envelope: CoinGroupPayloadEnvelope) -> DecodedCoinGroupPayload:
    """Decode a single/multiple event post without accepting routing ambiguity."""

    try:
        available_at = normalize_utc(
            envelope.available_at_utc, field_name="coin_group_payload_available_at_utc"
        )
    except MarketStoreContractError as exc:
        raise ValueError("coin_group_payload_available_at_invalid") from exc
    items, invalid_items = _decode_json_objects(envelope.payload_text)
    chosen: dict[tuple[int, int], CoinGroupStagingMessage] = {}
    conflicts: set[tuple[int, int]] = set()
    duplicates = 0
    for item in items:
        message = _decode_item(item, available_at_utc=available_at)
        if message is None:
            invalid_items += 1
            continue
        key = (message.group_number, message.message_id)
        if key in conflicts:
            invalid_items += 1
            continue
        existing = chosen.get(key)
        if existing is None:
            chosen[key] = message
            continue
        if existing == message:
            duplicates += 1
            continue
        selected = _newer_edit(existing, message)
        if selected is None:
            chosen.pop(key, None)
            conflicts.add(key)
            continue
        chosen[key] = selected
        duplicates += 1
    return DecodedCoinGroupPayload(
        messages=tuple(
            sorted(chosen.values(), key=lambda item: (item.event_time_utc, item.group_number, item.message_id))
        ),
        invalid_items=invalid_items,
        duplicate_items=duplicates,
        conflicting_items=len(conflicts),
    )


def stage_coin_group_payload(
    connection: sqlite3.Connection,
    envelope: CoinGroupPayloadEnvelope,
    *,
    staged_at_utc: datetime | str | None = None,
) -> CoinGroupPayloadStageReport:
    """Stage valid inner group messages; caller owns the transaction boundary."""

    decoded = decode_coin_group_payload(envelope)
    changed = 0
    for message in decoded.messages:
        changed += int(stage_coin_group_message(connection, message, staged_at_utc=staged_at_utc))
    return CoinGroupPayloadStageReport(
        decoded_messages=len(decoded.messages),
        inserted_or_updated_messages=changed,
        idempotent_replays=len(decoded.messages) - changed,
        invalid_items=decoded.invalid_items,
        duplicate_items=decoded.duplicate_items,
        conflicting_items=decoded.conflicting_items,
    )
