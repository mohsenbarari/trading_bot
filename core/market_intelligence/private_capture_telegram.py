"""Telegram source adapter for the Docker-native market capture engine.

This module performs transport normalization only.  It does not parse prices,
instruments, offers, or trades.  Live startup is intentionally gated by a
release-bound authority marker on the session mount so merely deploying an
image cannot create a second Telegram session owner.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import signal
import stat
import threading
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .private_capture import (
    ACCOUNT_SOURCES,
    CaptureEngine,
    CaptureRuntimeError,
    QuarantineEventIdentity,
    StageResult,
    parse_utc,
    utc_now,
    utc_text,
)


CAPTURE_CONFIG_CONTRACT = "market_telegram_capture_config/1.0"
AUTHORITY_MARKER_CONTRACT = "market_capture_authority/1.0"
SESSION_FILE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,48}\.session$")
LIVE_REPLY_CACHE_MAX_ENTRIES = 50_000
EXACT_CATCHUP_SOURCES = frozenset(
    {"MELTED_PRIMARY_FLOW", "GROUP_1", "GROUP_2"}
)

_QUARANTINABLE_BACKFILL_ERRORS = frozenset(
    {
        "CAPTURE_MESSAGE_ID_INVALID",
        "CAPTURE_MESSAGE_TEXT_INVALID",
        "CAPTURE_MESSAGE_TEXT_REQUIRED",
        "CAPTURE_MESSAGE_TEXT_TOO_LARGE",
        "COIN_CAPTURE_SENDER_NAME_INVALID",
        "COIN_CAPTURE_SENDER_TELEGRAM_ID_INVALID",
        "COIN_CAPTURE_SENDER_TELEGRAM_ID_MISSING",
        "MARKET_CAPTURE_ENTITIES_INVALID",
        "MARKET_CAPTURE_ENTITY_INVALID",
        "MARKET_CAPTURE_ENTITY_RANGE_INVALID",
    }
)


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    source_code: str
    source_family: str
    parser_profile: str
    account: str


SOURCE_POLICIES = {
    "MELTED_PRIMARY_FLOW": SourcePolicy(
        "MELTED_PRIMARY_FLOW", "TELEGRAM_PRIVATE", "MELTED_PRIMARY", "account1"
    ),
    "MELTED_AGGREGATE": SourcePolicy(
        "MELTED_AGGREGATE", "TELEGRAM_PUBLIC", "MELTED_AGGREGATE", "account1"
    ),
    "MELTED_FLOW": SourcePolicy(
        "MELTED_FLOW", "TELEGRAM_PUBLIC", "MELTED_FLOW", "account1"
    ),
    "USD_HERAT": SourcePolicy(
        "USD_HERAT", "TELEGRAM_PUBLIC", "USD_HERAT", "account1"
    ),
    "XAUUSD": SourcePolicy("XAUUSD", "TELEGRAM_PUBLIC", "XAUUSD", "account1"),
    "GROUP_1": SourcePolicy("GROUP_1", "TELEGRAM_GROUP", "COIN_GROUP", "account2"),
    "GROUP_2": SourcePolicy("GROUP_2", "TELEGRAM_GROUP", "COIN_GROUP", "account2"),
}


class CaptureBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_code: str = Field(min_length=2, max_length=64)
    peer_id: int


class TelegramCaptureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["market_telegram_capture_config/1.0"]
    account: Literal["account1", "account2"]
    api_id: int = Field(gt=0, le=2_147_483_647)
    api_hash: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    session_filename: str = Field(min_length=11, max_length=56)
    sources: tuple[CaptureBinding, ...]
    reconciliation_interval_seconds: int = Field(default=300, ge=60, le=900)
    connection_retries: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_account_contract(self) -> "TelegramCaptureConfig":
        if not SESSION_FILE_PATTERN.fullmatch(self.session_filename):
            raise ValueError("capture_session_filename_invalid")
        expected = ACCOUNT_SOURCES[self.account]
        actual = {binding.source_code for binding in self.sources}
        peers = [binding.peer_id for binding in self.sources]
        if actual != expected or len(actual) != len(self.sources):
            raise ValueError("capture_source_allowlist_mismatch")
        if len(peers) != len(set(peers)) or any(peer == 0 for peer in peers):
            raise ValueError("capture_peer_binding_invalid")
        if any(SOURCE_POLICIES[source].account != self.account for source in actual):
            raise ValueError("capture_source_account_mismatch")
        return self


@dataclass(frozen=True, slots=True)
class TelegramMessageSnapshot:
    message_id: int
    published_at: datetime
    edited_at: datetime | None
    text: str
    has_media: bool
    media_type: str | None
    action_type: str | None
    entities: tuple[dict[str, object], ...]
    reply_to_message_id: int | None
    reply_to_top_id: int | None
    grouped_id: int | None
    sender_id: int | None
    sender_kind: str
    sender_display_name: str | None
    is_forwarded: bool
    via_bot: bool
    post: bool
    silent: bool
    pinned: bool
    noforwards: bool
    is_forum: bool


def load_capture_config(path: Path, *, expected_account: str) -> TelegramCaptureConfig:
    try:
        if path.is_symlink() or not path.is_file():
            raise CaptureRuntimeError("capture_config_file_unavailable")
        if path.stat().st_size > 64 * 1024:
            raise CaptureRuntimeError("capture_config_file_too_large")
        config = TelegramCaptureConfig.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise CaptureRuntimeError("capture_config_invalid") from exc
    if config.account != expected_account:
        raise CaptureRuntimeError("capture_config_account_mismatch")
    return config


def load_hmac_key(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            raise CaptureRuntimeError("capture_hmac_key_unavailable")
        key = path.read_bytes()
    except OSError as exc:
        raise CaptureRuntimeError("capture_hmac_key_unavailable") from exc
    if len(key) < 32:
        raise CaptureRuntimeError("capture_hmac_key_too_short")
    return key


def validate_session_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise CaptureRuntimeError("capture_session_file_unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise CaptureRuntimeError("capture_session_file_invalid")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise CaptureRuntimeError("capture_session_file_mode_invalid")
    if info.st_uid != os.geteuid():
        raise CaptureRuntimeError("capture_session_file_owner_invalid")


def validate_authority_marker(
    session_root: Path, *, role: str, release_sha: str
) -> None:
    marker = session_root / "authority-container.json"
    try:
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
            raise CaptureRuntimeError("capture_authority_marker_unavailable")
        info = marker.stat()
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise CaptureRuntimeError("capture_authority_marker_permissions_invalid")
        document = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureRuntimeError("capture_authority_marker_invalid") from exc
    expected = {
        "contract": AUTHORITY_MARKER_CONTRACT,
        "authority": "container",
        "role": role,
        "release_sha": release_sha,
    }
    if not isinstance(document, dict) or any(
        document.get(key) != value for key, value in expected.items()
    ):
        raise CaptureRuntimeError("capture_authority_marker_mismatch")
    parse_utc(document.get("authorized_at_utc"), field="capture_authorized_at_utc")


def _aware(value: datetime | None, *, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise CaptureRuntimeError(f"{field}_timezone_required")
    return value.astimezone(timezone.utc)


def _quarantinable_backfill_error(exc: CaptureRuntimeError) -> bool:
    return str(exc).strip().upper() in _QUARANTINABLE_BACKFILL_ERRORS


def _quarantine_identity(
    policy: SourcePolicy,
    message: object,
    *,
    is_forum: bool,
    event_type: str,
    origin: str,
) -> tuple[QuarantineEventIdentity, TelegramMessageSnapshot] | None:
    """Return a value-free identity, or ``None`` when identity is ambiguous.

    Ambiguous events deliberately remain in the legacy unresolved quarantine;
    inventing a message id or revision would make later replay evidence false.
    """

    try:
        snapshot = snapshot_from_telethon(
            message,
            is_forum=is_forum,
        )
        identity = QuarantineEventIdentity(
            account=policy.account,
            source_code=policy.source_code,
            message_id=snapshot.message_id,
            revision_sha256=_revision(snapshot),
            event_type=event_type,
            origin=origin,
        )
        identity.validate()
    except (CaptureRuntimeError, TypeError, ValueError, OverflowError):
        return None
    return identity, snapshot


def _available(
    received_at: datetime, published_at: datetime, edited_at: datetime | None
) -> tuple[datetime, bool]:
    values = [received_at, published_at]
    if edited_at is not None:
        values.append(edited_at)
    result = max(values)
    return result, result != received_at


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _revision(snapshot: TelegramMessageSnapshot) -> str:
    material = {
        "text": snapshot.text,
        "media_type": snapshot.media_type,
        "action_type": snapshot.action_type,
        "entities": snapshot.entities,
        "reply_to_message_id": snapshot.reply_to_message_id,
        "reply_to_top_id": snapshot.reply_to_top_id,
        "grouped_id": snapshot.grouped_id,
        "forwarded": snapshot.is_forwarded,
        "via_bot": snapshot.via_bot,
        "post": snapshot.post,
        "silent": snapshot.silent,
        "pinned": snapshot.pinned,
        "noforwards": snapshot.noforwards,
    }
    return sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _stable_hash_id(prefix: str, identity: str) -> str:
    return prefix + sha256(identity.encode("utf-8")).hexdigest()


def _stable_uuid7(moment: datetime, identity: str) -> str:
    milliseconds = int(moment.timestamp() * 1000)
    digest = sha256(identity.encode("utf-8")).digest()
    payload = bytearray(16)
    for index in range(6):
        payload[index] = (milliseconds >> (8 * (5 - index))) & 0xFF
    payload[6] = 0x70 | (digest[0] & 0x0F)
    payload[7] = digest[1]
    payload[8] = 0x80 | (digest[2] & 0x3F)
    payload[9:16] = digest[3:10]
    encoded = bytes(payload).hex()
    return (
        f"{encoded[0:8]}-{encoded[8:12]}-{encoded[12:16]}-"
        f"{encoded[16:20]}-{encoded[20:32]}"
    )


def _sender_identity(key: bytes, sender_id: int | None) -> str | None:
    if sender_id is None:
        return None
    return hmac.new(
        key, f"peer:{sender_id}".encode("ascii"), sha256
    ).hexdigest()[:16]


def _bare_peer_id(value: int | None) -> int | None:
    if value is None:
        return None
    if value <= -1_000_000_000_000:
        return -value - 1_000_000_000_000
    if value < 0:
        return -value
    return value


def _content_type(snapshot: TelegramMessageSnapshot) -> str:
    if snapshot.action_type:
        return "service"
    if snapshot.has_media and snapshot.text:
        return "caption"
    if snapshot.has_media:
        return "media_only"
    return "text"


def build_market_event(
    policy: SourcePolicy,
    snapshot: TelegramMessageSnapshot,
    *,
    event_type: Literal["message_created", "message_snapshot", "message_edited"],
    received_at: datetime,
    backfill: bool,
    explicit_backfill: bool = False,
) -> dict[str, Any]:
    if policy.account != "account1":
        raise CaptureRuntimeError("market_capture_policy_invalid")
    received = _aware(received_at, field="market_capture_received_at")
    published = _aware(snapshot.published_at, field="market_capture_published_at")
    edited = _aware(snapshot.edited_at, field="market_capture_edited_at")
    assert received is not None and published is not None
    if event_type == "message_edited" and edited is None:
        raise CaptureRuntimeError("market_capture_edit_time_required")
    effective = edited if event_type == "message_edited" else published
    assert effective is not None
    available, adjusted = _available(received, published, edited)
    revision = _revision(snapshot)
    logical_type = "message_upsert" if event_type != "message_edited" else event_type
    explicit_identity = "|explicit_backfill" if explicit_backfill else ""
    identity = (
        f"market_channel_event|{policy.source_code}|{logical_type}|"
        f"{snapshot.message_id}|{int(effective.timestamp() * 1_000_000)}|{revision}"
        f"{explicit_identity}"
    )
    text_hash = sha256(snapshot.text.encode("utf-8")).hexdigest()
    return {
        "schema": "market_channel_event",
        "schema_version": "1.0",
        "event_id": _stable_hash_id("mce1_", identity),
        "event_type": event_type,
        "occurred_at_utc": utc_text(effective),
        "source": {
            "market": "coin_intelligence",
            "source_id": policy.source_code,
            "source_family": policy.source_family,
            "parser_profile": policy.parser_profile,
        },
        "message": {
            "message_id": str(snapshot.message_id),
            "published_at_utc": utc_text(published),
            "edited_at_utc": utc_text(edited) if edited else None,
            "text": snapshot.text,
            "text_sha256": text_hash,
            "revision_sha256": revision,
            "content_type": _content_type(snapshot),
            "entities": list(snapshot.entities),
            "media_type": snapshot.media_type,
            "action_type": snapshot.action_type,
            "reply": {
                "message_id": (
                    str(snapshot.reply_to_message_id)
                    if snapshot.reply_to_message_id is not None
                    else None
                ),
                "top_message_id": (
                    str(snapshot.reply_to_top_id)
                    if snapshot.reply_to_top_id is not None
                    else None
                ),
            },
            "grouped_id": (
                str(snapshot.grouped_id) if snapshot.grouped_id is not None else None
            ),
            "is_forwarded": snapshot.is_forwarded,
            "via_bot": snapshot.via_bot,
            "post": snapshot.post,
            "silent": snapshot.silent,
            "pinned": snapshot.pinned,
            "noforwards": snapshot.noforwards,
        },
        "producer": {
            "name": "market_channel_capture",
            "version": "2.0.0-docker",
            "origin": (
                "explicit_backfill"
                if explicit_backfill
                else "reconcile"
                if backfill
                else "live"
            ),
            "is_backfill": backfill,
            "received_at_utc": utc_text(received),
            "available_at_utc": utc_text(available),
            "source_clock_adjusted": adjusted,
        },
    }


def build_group_event(
    policy: SourcePolicy,
    snapshot: TelegramMessageSnapshot,
    *,
    event_type: Literal["message_created", "message_edited"],
    received_at: datetime,
    backfill: bool,
    reply_status: str,
    hmac_key: bytes,
    explicit_backfill: bool = False,
) -> dict[str, Any]:
    if policy.account != "account2":
        raise CaptureRuntimeError("group_capture_policy_invalid")
    if reply_status not in {
        "not_reply",
        "resolved_from_live_stream",
        "resolved_from_api",
        "unavailable",
        "deleted",
    }:
        raise CaptureRuntimeError("group_capture_reply_status_invalid")
    received = _aware(received_at, field="group_capture_received_at")
    published = _aware(snapshot.published_at, field="group_capture_published_at")
    edited = _aware(snapshot.edited_at, field="group_capture_edited_at")
    assert received is not None and published is not None
    if event_type == "message_edited" and edited is None:
        raise CaptureRuntimeError("group_capture_edit_time_required")
    occurred = edited if event_type == "message_edited" else published
    assert occurred is not None
    available, _adjusted = _available(received, published, edited)
    revision = _revision(snapshot) if event_type == "message_edited" else ""
    explicit_identity = "|explicit_backfill" if explicit_backfill else ""
    identity = (
        f"coin|{policy.source_code}|{event_type}|{snapshot.message_id}|"
        f"{int(occurred.timestamp() * 1_000_000)}|{revision}{explicit_identity}"
    )
    parent = snapshot.reply_to_message_id
    topic_id = snapshot.reply_to_top_id
    if snapshot.is_forum and topic_id is None and parent is not None:
        topic_id = parent
    return {
        "schema": "coin_group_event",
        "schema_version": "2.1",
        "event_id": _stable_uuid7(occurred, identity),
        "event_type": event_type,
        "occurred_at_utc": utc_text(occurred),
        "source": {"market": "coin", "source_id": policy.source_code},
        "message": {
            "message_id": str(snapshot.message_id),
            "published_at_utc": utc_text(published),
            "edited_at_utc": utc_text(edited) if edited else None,
            "text": snapshot.text,
            "content_type": _content_type(snapshot),
            "sender": {
                "peer_id": _sender_identity(hmac_key, snapshot.sender_id),
                "kind": snapshot.sender_kind,
                "telegram_id": (
                    str(_bare_peer_id(snapshot.sender_id))
                    if snapshot.sender_id is not None
                    else None
                ),
                "display_name": snapshot.sender_display_name,
            },
            "reply": {
                "status": reply_status,
                "message_id": str(parent) if parent is not None else None,
            },
            "is_forwarded": snapshot.is_forwarded,
            "topic": {
                "status": (
                    "resolved_from_api"
                    if snapshot.is_forum and topic_id is not None
                    else "unresolved"
                    if snapshot.is_forum
                    else "not_applicable"
                ),
                "topic_id": str(topic_id) if topic_id is not None else None,
            },
            "is_backfill": backfill,
        },
        "producer": {
            "name": "coin_group_capture",
            "version": "3.1.0-docker",
            "origin": (
                "explicit_backfill"
                if explicit_backfill
                else "reconcile"
                if backfill
                else "live"
            ),
            "available_at_utc": utc_text(available),
        },
    }


def build_deleted_event(
    policy: SourcePolicy,
    *,
    message_id: int,
    received_at: datetime,
) -> dict[str, Any]:
    received = _aware(received_at, field="capture_delete_received_at")
    assert received is not None
    if policy.account == "account1":
        return {
            "schema": "market_channel_event",
            "schema_version": "1.0",
            "event_id": _stable_hash_id(
                "mce1_", f"market_channel_event|delete|{policy.source_code}|{message_id}"
            ),
            "event_type": "message_deleted",
            "occurred_at_utc": utc_text(received),
            "source": {
                "market": "coin_intelligence",
                "source_id": policy.source_code,
                "source_family": policy.source_family,
                "parser_profile": policy.parser_profile,
            },
            "message": {
                "message_id": str(message_id),
                "published_at_utc": None,
                "edited_at_utc": None,
                "previous_revision_sha256": None,
            },
            "producer": {
                "name": "market_channel_capture",
                "version": "2.0.0-docker",
                "origin": "live",
                "is_backfill": False,
                "received_at_utc": utc_text(received),
                "available_at_utc": utc_text(received),
                "source_clock_adjusted": False,
            },
        }
    return {
        "schema": "coin_group_event",
        "schema_version": "2.1",
        "event_id": _stable_hash_id(
            "cge2_", f"coin_group_event|delete|{policy.source_code}|{message_id}"
        ),
        "event_type": "message_deleted",
        "occurred_at_utc": utc_text(received),
        "source": {"market": "coin", "source_id": policy.source_code},
        "message": {"message_id": str(message_id)},
        "producer": {
            "name": "coin_group_capture",
            "version": "3.1.0-docker",
            "origin": "live",
            "available_at_utc": utc_text(received),
        },
    }


def _safe_type(value: object) -> str | None:
    if value is None:
        return None
    name = type(value).__name__
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", name) else "Unknown"


def _display_name(sender: object | None) -> str | None:
    if sender is None:
        return None
    parts = [
        str(value).strip()
        for value in (
            getattr(sender, "first_name", None),
            getattr(sender, "last_name", None),
        )
        if str(value or "").strip()
    ]
    candidate = " ".join(parts) or str(getattr(sender, "title", None) or "").strip()
    if not candidate:
        return None
    normalized = " ".join(candidate.split())
    while len(normalized.encode("utf-8")) > 512:
        normalized = normalized[:-1]
    return normalized or None


def snapshot_from_telethon(
    message: object,
    *,
    is_forum: bool,
    sender_entity: object | None = None,
) -> TelegramMessageSnapshot:
    published = _aware(getattr(message, "date", None), field="telegram_message_date")
    if published is None:
        raise CaptureRuntimeError("telegram_message_date_missing")
    edited = _aware(getattr(message, "edit_date", None), field="telegram_edit_date")
    raw_entities = getattr(message, "entities", None) or []
    entities: list[dict[str, object]] = []
    for entity in raw_entities[:512]:
        offset = int(getattr(entity, "offset", -1))
        length = int(getattr(entity, "length", 0))
        if offset < 0 or length <= 0:
            continue
        entities.append(
            {
                "type": _safe_type(entity) or "Unknown",
                "offset_utf16": offset,
                "length_utf16": length,
                "url_present": bool(getattr(entity, "url", None)),
            }
        )
    reply = getattr(message, "reply_to", None)
    sender = sender_entity if sender_entity is not None else getattr(message, "sender", None)
    sender_id = getattr(message, "sender_id", None)
    sender_kind = "unknown"
    if sender_id is not None:
        sender_kind = "user"
        if bool(getattr(sender, "bot", False)):
            sender_kind = "bot"
        elif _safe_type(sender) == "Channel":
            sender_kind = "channel"
    action = getattr(message, "action", None)
    media = getattr(message, "media", None)
    text = getattr(message, "message", None)
    return TelegramMessageSnapshot(
        message_id=int(getattr(message, "id")),
        published_at=published,
        edited_at=edited,
        text=str(text) if text is not None else "",
        has_media=media is not None,
        media_type=_safe_type(media),
        action_type=_safe_type(action),
        entities=tuple(entities),
        reply_to_message_id=(
            int(getattr(reply, "reply_to_msg_id"))
            if reply is not None and getattr(reply, "reply_to_msg_id", None) is not None
            else None
        ),
        reply_to_top_id=(
            int(getattr(reply, "reply_to_top_id"))
            if reply is not None and getattr(reply, "reply_to_top_id", None) is not None
            else None
        ),
        grouped_id=(
            int(getattr(message, "grouped_id"))
            if getattr(message, "grouped_id", None) is not None
            else None
        ),
        sender_id=int(sender_id) if sender_id is not None else None,
        sender_kind=sender_kind,
        sender_display_name=_display_name(sender),
        is_forwarded=getattr(message, "fwd_from", None) is not None,
        via_bot=getattr(message, "via_bot_id", None) is not None,
        post=bool(getattr(message, "post", False)),
        silent=bool(getattr(message, "silent", False)),
        pinned=bool(getattr(message, "pinned", False)),
        noforwards=bool(getattr(message, "noforwards", False)),
        is_forum=bool(is_forum),
    )


class TelegramCaptureProvider:
    """Bounded-reconciliation Telethon owner for exactly one account."""

    def __init__(
        self,
        config: TelegramCaptureConfig,
        engine: CaptureEngine,
        *,
        session_path: Path,
        hmac_key: bytes | None,
        stop: threading.Event,
        heartbeat: Callable[[], None] | None = None,
        backfill_not_before: datetime | None = None,
        backfill_upper_bound: datetime | None = None,
        backfill_max_messages: int = 100_000,
        backfill_source_codes: frozenset[str] | None = None,
        release_sha: str | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.session_path = session_path
        self.hmac_key = hmac_key
        self.stop = stop
        self.heartbeat = heartbeat
        self.backfill_not_before = (
            _aware(backfill_not_before, field="capture_backfill_not_before")
            if backfill_not_before is not None
            else None
        )
        self.backfill_upper_bound = (
            _aware(backfill_upper_bound, field="capture_backfill_upper_bound")
            if backfill_upper_bound is not None
            else utc_now()
            if backfill_not_before is not None
            else None
        )
        if not 2_000 <= int(backfill_max_messages) <= 1_000_000:
            raise CaptureRuntimeError("capture_backfill_max_messages_invalid")
        self.backfill_max_messages = int(backfill_max_messages)
        requested_sources = backfill_source_codes or frozenset()
        if not requested_sources.issubset(SOURCE_POLICIES):
            raise CaptureRuntimeError("capture_backfill_source_codes_invalid")
        if self.backfill_not_before is not None and not requested_sources:
            raise CaptureRuntimeError("capture_backfill_source_codes_required")
        self.backfill_source_codes = frozenset(requested_sources)
        if self.backfill_not_before is not None:
            # Legacy markers collapsed source attribution and therefore
            # expand to the complete account.  New event-bound failures add
            # only their exact sources.  The configured promotion sources are
            # always retained.
            self.backfill_source_codes = frozenset(
                self.backfill_source_codes
                | self.engine.state.quarantine_replay_sources(
                    self.backfill_not_before
                )
            )
        if self.backfill_not_before is not None and not (
            self.backfill_source_codes & ACCOUNT_SOURCES[self.config.account]
        ):
            raise CaptureRuntimeError("capture_backfill_account_sources_missing")
        if (
            self.backfill_not_before is not None
            and self.backfill_upper_bound is not None
            and self.backfill_upper_bound < self.backfill_not_before
        ):
            raise CaptureRuntimeError("capture_backfill_bounds_invalid")
        if self.backfill_not_before is not None:
            if (
                release_sha is None
                or not re.fullmatch(r"[0-9a-f]{40}", release_sha)
            ):
                raise CaptureRuntimeError("capture_backfill_release_required")
        self.release_sha = release_sha
        self._replay_run_id: str | None = None
        self._policy_by_peer = {
            binding.peer_id: SOURCE_POLICIES[binding.source_code]
            for binding in config.sources
        }
        self._entity_by_source: dict[str, object] = {}
        self._peer_by_runtime_id: dict[int, SourcePolicy] = dict(self._policy_by_peer)
        self._live_seen: set[tuple[str, int]] = set()
        self._live_seen_order: deque[tuple[str, int]] = deque()
        self.reconciliation_truncated = False
        self.backfill_in_progress = False
        self._fatal: BaseException | None = None
        self._ready_for_live_updates = False

    def _ensure_replay_run(self) -> str | None:
        if self.backfill_not_before is None:
            return None
        if self.release_sha is None or self.backfill_upper_bound is None:
            raise CaptureRuntimeError("capture_backfill_release_required")
        sources = self.backfill_source_codes & ACCOUNT_SOURCES[self.config.account]
        if not sources:
            raise CaptureRuntimeError("capture_backfill_account_sources_missing")
        if self._replay_run_id is None:
            self._replay_run_id = self.engine.state.begin_replay_run(
                cutoff=self.backfill_not_before,
                upper_bound=self.backfill_upper_bound,
                source_codes=sources,
                release_sha=self.release_sha,
            )
            # On restart, ``begin_replay_run`` resumes the prior fixed window;
            # never widen it to the new process start time.
            self.backfill_upper_bound = self.engine.state.replay_run_upper_bound(
                self._replay_run_id
            )
        return self._replay_run_id

    def _fail_runtime(self, exc: BaseException) -> None:
        self._fatal = exc
        self.stop.set()

    def _note_event_quarantine(
        self,
        policy: SourcePolicy,
        message: object,
        exc: CaptureRuntimeError,
        *,
        marker: bytes,
        event_type: str,
        origin: str,
    ) -> None:
        entity = self._entity_by_source.get(policy.source_code)
        bound = _quarantine_identity(
            policy,
            message,
            is_forum=bool(getattr(entity, "forum", False)),
            event_type=event_type,
            origin=origin,
        )
        if bound is None:
            # No synthetic identity is permitted.  Preserve the old generic
            # unresolved marker so promotion continues to fail closed.
            self.engine.state.note_quarantine(
                marker, exc, source_code=policy.source_code
            )
            return
        identity, _snapshot = bound
        self.engine.state.note_event_quarantine(identity, exc)

    def _policy(self, peer_id: object) -> SourcePolicy | None:
        try:
            normalized = int(peer_id)
        except (TypeError, ValueError):
            return None
        return self._peer_by_runtime_id.get(normalized)

    def _remember_live_reply_parent(self, policy: SourcePolicy, message_id: int) -> None:
        # Account1 channels never consult this reply cache. Keeping their
        # high-frequency quote IDs here caused unbounded resident growth.
        if policy.account != "account2":
            return
        key = (policy.source_code, int(message_id))
        if key in self._live_seen:
            return
        if len(self._live_seen_order) >= LIVE_REPLY_CACHE_MAX_ENTRIES:
            self._live_seen.discard(self._live_seen_order.popleft())
        self._live_seen.add(key)
        self._live_seen_order.append(key)

    async def _capture_message(
        self,
        client: object,
        policy: SourcePolicy,
        message: object,
        *,
        backfill: bool,
        edited: bool,
        parent_depth: int = 0,
        explicit_backfill: bool = False,
        explicit_results: list[StageResult] | None = None,
    ) -> StageResult:
        sender_entity = getattr(message, "sender", None)
        if (
            policy.account == "account2"
            and sender_entity is None
            and getattr(message, "sender_id", None) is not None
        ):
            try:
                sender_entity = await message.get_sender()  # type: ignore[attr-defined]
            except Exception:  # Telegram entity lookup is non-critical enrichment.
                # Identity is still retained when Telegram supplies sender_id;
                # a missing display-name lookup must not interrupt capture.
                sender_entity = None
        snapshot = snapshot_from_telethon(
            message,
            is_forum=bool(getattr(self._entity_by_source[policy.source_code], "forum", False)),
            sender_entity=sender_entity,
        )
        entity_id = getattr(self._entity_by_source[policy.source_code], "id", None)
        if (
            policy.account == "account2"
            and snapshot.sender_kind == "channel"
            and _bare_peer_id(snapshot.sender_id)
            == _bare_peer_id(int(entity_id) if entity_id is not None else None)
        ):
            snapshot = replace(snapshot, sender_kind="anonymous_admin")
        received = utc_now()
        if policy.account == "account1":
            document = build_market_event(
                policy,
                snapshot,
                event_type=(
                    "message_edited"
                    if edited
                    else "message_snapshot"
                    if backfill
                    else "message_created"
                ),
                received_at=received,
                backfill=backfill,
                explicit_backfill=explicit_backfill,
            )
        else:
            if self.hmac_key is None:
                raise CaptureRuntimeError("group_capture_hmac_key_missing")
            reply_status = "not_reply"
            parent = snapshot.reply_to_message_id
            if parent is not None:
                key = (policy.source_code, parent)
                if key in self._live_seen:
                    reply_status = "resolved_from_live_stream"
                elif self.engine.state.message_deleted(policy.source_code, parent):
                    reply_status = "deleted"
                elif self.engine.state.has_message(policy.source_code, parent):
                    reply_status = "resolved_from_api"
                elif parent_depth < 20:
                    entity = self._entity_by_source[policy.source_code]
                    parent_message = await client.get_messages(entity, ids=parent)  # type: ignore[attr-defined]
                    if parent_message is not None:
                        parent_snapshot = snapshot_from_telethon(
                            parent_message,
                            is_forum=bool(getattr(entity, "forum", False)),
                        )
                        if parent_snapshot.published_at >= snapshot.published_at - timedelta(hours=2):
                            try:
                                await self._capture_message(
                                    client,
                                    policy,
                                    parent_message,
                                    backfill=True,
                                    edited=parent_snapshot.edited_at is not None,
                                    parent_depth=parent_depth + 1,
                                    # A recursively fetched reply ancestor is
                                    # causal context, not an item enumerated by
                                    # the explicit replay window.  Counting it
                                    # as explicit made attempted/accepted drift
                                    # from the immutable replay manifest.  If
                                    # the ancestor is itself in the requested
                                    # window, the top-level iterator will visit
                                    # it and create its own manifest entry.
                                    explicit_backfill=False,
                                    explicit_results=None,
                                )
                            except CaptureRuntimeError as exc:
                                if not (
                                    explicit_backfill
                                    and _quarantinable_backfill_error(exc)
                                ):
                                    raise
                                # A reply ancestor is causal context, not the
                                # in-window child currently being captured.  A
                                # media/service/invalid ancestor must not make
                                # the valid child disappear.  The same parent,
                                # when it is itself enumerated in the requested
                                # window, is still quarantined by the top-level
                                # loop and therefore blocks coverage.
                                self.engine.state.note_context_filter(
                                    exc, source_code=policy.source_code
                                )
                                reply_status = "unavailable"
                            else:
                                reply_status = "resolved_from_api"
                        else:
                            reply_status = "unavailable"
                    else:
                        reply_status = "unavailable"
                else:
                    reply_status = "unavailable"
            document = build_group_event(
                policy,
                snapshot,
                event_type="message_edited" if edited else "message_created",
                received_at=received,
                backfill=backfill,
                reply_status=reply_status,
                hmac_key=self.hmac_key,
                explicit_backfill=explicit_backfill,
            )
        result = self.engine.accept(document)
        if explicit_backfill and explicit_results is not None:
            explicit_results.append(result)
        if not backfill:
            self._remember_live_reply_parent(policy, snapshot.message_id)
        return result

    async def _reconcile_source(self, client: object, policy: SourcePolicy) -> None:
        entity = self._entity_by_source[policy.source_code]
        horizon = timedelta(minutes=30) if policy.account == "account1" else timedelta(hours=6)
        cutoff = utc_now() - horizon
        maximum = 2_000 if policy.account == "account1" else 10_000
        overlap_after_watermark = 200
        watermark = self.engine.state.highest_message_id(policy.source_code)
        watermark_reached = False
        overlap_seen = 0
        newest: list[object] = []
        async for message in client.iter_messages(entity, limit=maximum + 1):  # type: ignore[attr-defined]
            published = _aware(getattr(message, "date", None), field="telegram_reconcile_date")
            if published is None or published < cutoff:
                break
            newest.append(message)
            try:
                message_id = int(getattr(message, "id"))
            except (TypeError, ValueError):
                message_id = 0
            if watermark is not None and message_id <= watermark:
                watermark_reached = True
                overlap_seen += 1
                if overlap_seen >= overlap_after_watermark:
                    break
        if len(newest) > maximum:
            # A bounded first bootstrap intentionally does not claim old
            # history. On restart, however, failing to reach the durable
            # watermark means a real recovery gap and keeps health degraded.
            if watermark is not None and not watermark_reached:
                self.reconciliation_truncated = True
            newest = newest[:maximum]
        for message in reversed(newest):
            try:
                await self._capture_message(
                    client,
                    policy,
                    message,
                    backfill=True,
                    edited=getattr(message, "edit_date", None) is not None,
                )
            except CaptureRuntimeError as exc:
                # Telegram channels legitimately contain service/media-only
                # messages that have no model-consumable text. One such
                # historical item must not poison reconciliation or prevent
                # later valid market messages from becoming durable.
                self._note_event_quarantine(
                    policy,
                    message,
                    exc,
                    marker=b"telegram-reconcile-event",
                    event_type=(
                        "message_edited"
                        if getattr(message, "edit_date", None) is not None
                        else "message_snapshot"
                        if policy.account == "account1"
                        else "message_created"
                    ),
                    origin="reconcile",
                )

    async def _backfill_source_to_cutoff(
        self, client: object, policy: SourcePolicy
    ) -> None:
        cutoff = self.backfill_not_before
        if cutoff is None or policy.source_code not in self.backfill_source_codes:
            return
        replay_run_id = self._ensure_replay_run()
        if replay_run_id is None:
            raise CaptureRuntimeError("capture_replay_run_missing")
        if (
            self.engine.state.backfill_covers(policy.source_code, cutoff)
            and self.engine.state.replay_source_manifest_count(
                replay_run_id, policy.source_code
            )
            > 0
        ):
            return
        if policy.account != self.config.account:
            raise CaptureRuntimeError("capture_backfill_policy_account_mismatch")
        entity = self._entity_by_source[policy.source_code]
        # Ask Telegram for the fixed replay window in chronological order and
        # consume it as a stream.  The previous newest-first implementation
        # retained every Telethon Message in RAM before it could apply the
        # oldest item.  Large, high-frequency sources could therefore restart
        # at the safety limit without recording a single outcome.  ``reverse``
        # preserves the required oldest-to-newest capture sequence while the
        # lower offset keeps the exact owner cutoff inclusive.
        exhaustion = "cutoff_crossed"
        resume_message_id, attempted_total = (
            self.engine.state.resume_replay_source_backfill(
                replay_run_id, policy.source_code, cutoff
            )
        )
        messages_seen = attempted_total

        def record_results(results: list[StageResult]) -> None:
            nonlocal attempted_total
            for result in results:
                self.engine.state.note_backfill_outcome(
                    policy.source_code,
                    cutoff,
                    "duplicate" if result.status == "duplicate" else "accepted",
                )
                attempted_total += 1

        remaining_limit = self.backfill_max_messages - attempted_total + 1
        if remaining_limit < 1:
            raise CaptureRuntimeError("telegram_backfill_limit_exceeded")
        history = client.iter_messages(  # type: ignore[attr-defined]
            entity,
            limit=remaining_limit,
            reverse=True,
            offset_date=cutoff - timedelta(microseconds=1),
            **(
                {"min_id": resume_message_id}
                if resume_message_id is not None
                else {}
            ),
        )
        async for message in history:
            if self.stop.is_set():
                raise CaptureRuntimeError("telegram_backfill_interrupted")
            published = _aware(
                getattr(message, "date", None), field="telegram_backfill_date"
            )
            if published is None:
                raise CaptureRuntimeError("telegram_backfill_date_required")
            if (
                self.backfill_upper_bound is not None
                and published > self.backfill_upper_bound
            ):
                # Chronological iteration makes every later item outside the
                # immutable replay window as well.
                break
            if messages_seen >= self.backfill_max_messages:
                self.reconciliation_truncated = True
                raise CaptureRuntimeError("telegram_backfill_limit_exceeded")
            messages_seen += 1
            edited = _aware(
                getattr(message, "edit_date", None),
                field="telegram_backfill_edit_date",
            )
            if (
                edited is not None
                and self.backfill_upper_bound is not None
                and published <= self.backfill_upper_bound < edited
            ):
                # Telegram exposes only the latest revision here.  Pretending
                # it was the pre-upper-bound revision would fabricate a
                # point-in-time replay, so retain a source/revision-bound
                # blocker instead.
                self._note_event_quarantine(
                    policy,
                    message,
                    CaptureRuntimeError(
                        "CAPTURE_REPLAY_POINT_IN_TIME_REVISION_UNAVAILABLE"
                    ),
                    marker=b"telegram-explicit-backfill-event",
                    event_type="message_edited",
                    origin="explicit_backfill",
                )
                self.engine.state.note_backfill_outcome(
                    policy.source_code, cutoff, "quarantined"
                )
                attempted_total += 1
                continue
            if published < cutoff:
                # This should be impossible with the exclusive offset above.
                # Fail closed rather than silently accepting provider drift.
                raise CaptureRuntimeError("telegram_backfill_lower_bound_drift")

            if self.stop.is_set():
                raise CaptureRuntimeError("telegram_backfill_interrupted")
            results: list[StageResult] = []
            try:
                result = await self._capture_message(
                    client,
                    policy,
                    message,
                    backfill=True,
                    edited=getattr(message, "edit_date", None) is not None,
                    explicit_backfill=True,
                    explicit_results=results,
                )
            except CaptureRuntimeError as exc:
                record_results(results)
                if not _quarantinable_backfill_error(exc):
                    raise
                self._note_event_quarantine(
                    policy,
                    message,
                    exc,
                    marker=b"telegram-explicit-backfill-event",
                    event_type=(
                        "message_edited"
                        if getattr(message, "edit_date", None) is not None
                        else "message_snapshot"
                        if policy.account == "account1"
                        else "message_created"
                    ),
                    origin="explicit_backfill",
                )
                self.engine.state.note_backfill_outcome(
                    policy.source_code, cutoff, "quarantined"
                )
                attempted_total += 1
            except BaseException:
                # A durable accept can precede a transport/process failure.
                # Account every result already returned by the capture engine
                # before propagating so recovery evidence is never silently
                # reset to zero.
                record_results(results)
                raise
            else:
                record_results(results)
                event_type = (
                    "message_edited"
                    if getattr(message, "edit_date", None) is not None
                    else "message_snapshot"
                    if policy.account == "account1"
                    else "message_created"
                )
                bound = _quarantine_identity(
                    policy,
                    message,
                    is_forum=bool(getattr(entity, "forum", False)),
                    event_type=event_type,
                    origin="explicit_backfill",
                )
                if bound is None:
                    raise CaptureRuntimeError("capture_replay_identity_unavailable")
                identity, snapshot = bound
                available = self.engine.state.event_available_at(result.event_id)
                if available is None:
                    raise CaptureRuntimeError("capture_replay_durable_event_missing")
                event_time = (
                    snapshot.edited_at
                    if event_type == "message_edited"
                    else snapshot.published_at
                )
                self.engine.state.record_replay_manifest_entry(
                    run_id=replay_run_id,
                    identity=identity,
                    event_id=result.event_id,
                    content_type=_content_type(snapshot),
                    event_time_utc=utc_text(event_time),
                    available_at_utc=available,
                    capture_status=(
                        "duplicate" if result.status == "duplicate" else "accepted"
                    ),
                )
        self.engine.state.mark_backfill_complete(
            policy.source_code,
            cutoff,
            expected_attempted=attempted_total,
            exhaustion=exhaustion,
        )

    async def run(self) -> None:
        try:
            from telethon import TelegramClient, events, utils
        except ImportError as exc:
            raise CaptureRuntimeError("telethon_dependency_unavailable") from exc

        client = TelegramClient(
            str(self.session_path),
            self.config.api_id,
            self.config.api_hash,
            sequential_updates=True,
            flood_sleep_threshold=60,
            timeout=10,
            connection_retries=self.config.connection_retries,
            request_retries=3,
            retry_delay=1,
            auto_reconnect=True,
        )

        @client.on(events.NewMessage())
        async def on_created(event: object) -> None:
            if not self._ready_for_live_updates:
                return
            policy = self._policy(getattr(event, "chat_id", None))
            if policy is None:
                return
            try:
                await self._capture_message(
                    client, policy, getattr(event, "message"), backfill=False, edited=False
                )
            except CaptureRuntimeError as exc:
                self._note_event_quarantine(
                    policy,
                    getattr(event, "message"),
                    exc,
                    marker=b"telegram-created-event",
                    event_type=(
                        "message_snapshot"
                        if policy.account == "account1"
                        else "message_created"
                    ),
                    origin="live",
                )
            except BaseException as exc:
                self._fail_runtime(exc)

        @client.on(events.MessageEdited())
        async def on_edited(event: object) -> None:
            if not self._ready_for_live_updates:
                return
            policy = self._policy(getattr(event, "chat_id", None))
            if policy is None:
                return
            try:
                await self._capture_message(
                    client, policy, getattr(event, "message"), backfill=False, edited=True
                )
            except CaptureRuntimeError as exc:
                self._note_event_quarantine(
                    policy,
                    getattr(event, "message"),
                    exc,
                    marker=b"telegram-edited-event",
                    event_type="message_edited",
                    origin="live",
                )
            except BaseException as exc:
                self._fail_runtime(exc)

        @client.on(events.MessageDeleted())
        async def on_deleted(event: object) -> None:
            if not self._ready_for_live_updates:
                return
            policy = self._policy(getattr(event, "chat_id", None))
            if policy is None:
                return
            try:
                for message_id in tuple(getattr(event, "deleted_ids", ()) or ()):
                    if self.engine.state.message_deleted(policy.source_code, int(message_id)):
                        continue
                    self.engine.accept(
                        build_deleted_event(
                            policy, message_id=int(message_id), received_at=utc_now()
                        )
                    )
            except BaseException as exc:
                self._fail_runtime(exc)

        async def heartbeat_loop() -> None:
            while not self.stop.is_set():
                if self.heartbeat is not None:
                    self.heartbeat()
                await asyncio.sleep(5)

        await client.connect()
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            if not await client.is_user_authorized():
                raise CaptureRuntimeError("telegram_session_authorization_required")
            for binding in self.config.sources:
                entity = await client.get_entity(binding.peer_id)
                self._entity_by_source[binding.source_code] = entity
                self._peer_by_runtime_id[int(utils.get_peer_id(entity))] = SOURCE_POLICIES[
                    binding.source_code
                ]
            self._ready_for_live_updates = True
            heartbeat_task = asyncio.create_task(heartbeat_loop())
            self.backfill_in_progress = self.backfill_not_before is not None
            try:
                replay_run_id = self._ensure_replay_run()
                for binding in self.config.sources:
                    await self._backfill_source_to_cutoff(
                        client, SOURCE_POLICIES[binding.source_code]
                    )
                if replay_run_id is not None:
                    self.engine.state.complete_replay_run(replay_run_id)
                    # Quarantine is not evidence that replay failed, but a
                    # completed local manifest is also not proof that the
                    # processor, archive, ACK and bot-side Store received the
                    # same data.  Resolution therefore remains fail-closed
                    # until the independent catch-up audit emits and applies a
                    # digest-bound evidence bundle.
            finally:
                self.backfill_in_progress = False
            for binding in self.config.sources:
                await self._reconcile_source(
                    client, SOURCE_POLICIES[binding.source_code]
                )
            while not self.stop.is_set():
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self.stop.wait, self.config.reconciliation_interval_seconds),
                        timeout=self.config.reconciliation_interval_seconds + 1,
                    )
                except asyncio.TimeoutError:
                    pass
                if self.stop.is_set():
                    break
                for binding in self.config.sources:
                    await self._reconcile_source(
                        client, SOURCE_POLICIES[binding.source_code]
                    )
            if self._fatal is not None:
                raise CaptureRuntimeError("telegram_capture_durable_failure") from self._fatal
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            await client.disconnect()


def install_stop_handlers(stop: threading.Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


__all__ = [
    "AUTHORITY_MARKER_CONTRACT",
    "CAPTURE_CONFIG_CONTRACT",
    "CaptureBinding",
    "SOURCE_POLICIES",
    "SourcePolicy",
    "TelegramCaptureConfig",
    "TelegramCaptureProvider",
    "TelegramMessageSnapshot",
    "build_deleted_event",
    "build_group_event",
    "build_market_event",
    "install_stop_handlers",
    "load_capture_config",
    "load_hmac_key",
    "snapshot_from_telethon",
    "validate_authority_marker",
    "validate_session_file",
]
