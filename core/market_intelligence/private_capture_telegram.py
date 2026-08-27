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
    parse_utc,
    utc_now,
    utc_text,
)


CAPTURE_CONFIG_CONTRACT = "market_telegram_capture_config/1.0"
AUTHORITY_MARKER_CONTRACT = "market_capture_authority/1.0"
SESSION_FILE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,48}\.session$")
LIVE_REPLY_CACHE_MAX_ENTRIES = 50_000


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
    identity = (
        f"market_channel_event|{policy.source_code}|{logical_type}|"
        f"{snapshot.message_id}|{int(effective.timestamp() * 1_000_000)}|{revision}"
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
            "origin": "reconcile" if backfill else "live",
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
    identity = (
        f"coin|{policy.source_code}|{event_type}|{snapshot.message_id}|"
        f"{int(occurred.timestamp() * 1_000_000)}|{revision}"
    )
    parent = snapshot.reply_to_message_id
    topic_id = snapshot.reply_to_top_id
    if snapshot.is_forum and topic_id is None and parent is not None:
        topic_id = parent
    return {
        "schema": "coin_group_event",
        "schema_version": "2.0",
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
                "display_name": None,
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
            "version": "3.0.0-docker",
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
        "schema_version": "2.0",
        "event_id": _stable_hash_id(
            "cge2_", f"coin_group_event|delete|{policy.source_code}|{message_id}"
        ),
        "event_type": "message_deleted",
        "occurred_at_utc": utc_text(received),
        "source": {"market": "coin", "source_id": policy.source_code},
        "message": {"message_id": str(message_id)},
        "producer": {
            "name": "coin_group_capture",
            "version": "3.0.0-docker",
            "available_at_utc": utc_text(received),
        },
    }


def _safe_type(value: object) -> str | None:
    if value is None:
        return None
    name = type(value).__name__
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", name) else "Unknown"


def snapshot_from_telethon(message: object, *, is_forum: bool) -> TelegramMessageSnapshot:
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
    sender = getattr(message, "sender", None)
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
    ) -> None:
        self.config = config
        self.engine = engine
        self.session_path = session_path
        self.hmac_key = hmac_key
        self.stop = stop
        self.heartbeat = heartbeat
        self._policy_by_peer = {
            binding.peer_id: SOURCE_POLICIES[binding.source_code]
            for binding in config.sources
        }
        self._entity_by_source: dict[str, object] = {}
        self._peer_by_runtime_id: dict[int, SourcePolicy] = dict(self._policy_by_peer)
        self._live_seen: set[tuple[str, int]] = set()
        self._live_seen_order: deque[tuple[str, int]] = deque()
        self.reconciliation_truncated = False
        self._fatal: BaseException | None = None
        self._ready_for_live_updates = False

    def _fail_runtime(self, exc: BaseException) -> None:
        self._fatal = exc
        self.stop.set()

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
    ) -> None:
        snapshot = snapshot_from_telethon(
            message,
            is_forum=bool(getattr(self._entity_by_source[policy.source_code], "forum", False)),
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
                            await self._capture_message(
                                client,
                                policy,
                                parent_message,
                                backfill=True,
                                edited=parent_snapshot.edited_at is not None,
                                parent_depth=parent_depth + 1,
                            )
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
            )
        self.engine.accept(document)
        if not backfill:
            self._remember_live_reply_parent(policy, snapshot.message_id)

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
                self.engine.state.note_quarantine(
                    b"telegram-reconcile-event",
                    exc,
                    source_code=policy.source_code,
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
                self.engine.state.note_quarantine(
                    b"telegram-created-event", exc, source_code=policy.source_code
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
                self.engine.state.note_quarantine(
                    b"telegram-edited-event", exc, source_code=policy.source_code
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
            for binding in self.config.sources:
                await self._reconcile_source(
                    client, SOURCE_POLICIES[binding.source_code]
                )
            heartbeat_task = asyncio.create_task(heartbeat_loop())
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
