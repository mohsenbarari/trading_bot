"""Strict, replay-safe adapter for the new Telegram capture spools.

The capture services deliberately persist transport-complete events rather
than model-specific rows.  This module is the narrow boundary that validates
those envelopes, keeps raw text in a three-day staging database, applies
edits/deletes idempotently, and projects privacy-minimized facts into Market
Store.  It performs no network I/O and never returns raw text or identifiers
in its reports.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import blake2b, sha256
import json
import re
import sqlite3
from typing import Iterable, Mapping

from .coin_group_feedback import CoinGroupParserFeedback
from .coin_group_pipeline import CoinGroupPipelineReport, process_coin_group_staging
from .coin_group_resolution import CoinPriceAnchor
from .coin_group_trades import MAX_REPLY_AGE_SECONDS
from .coin_groups import CoinGroupMessageInput, parse_coin_group_offers
from .coin_group_staging import (
    CoinGroupStagingMessage,
    delete_coin_group_staged_message,
    initialize_coin_group_staging,
    purge_expired_coin_group_staging,
    stage_coin_group_message,
)
from .market_contracts import (
    MarketStoreContractError,
    derive_event_key,
    normalize_utc,
)
from .market_store import initialize_market_store, upsert_observation
from .private_gold import (
    PRIVATE_GOLD_MINUTE_SOURCE_CODE,
    PrivateGoldOfferInput,
    private_gold_observations,
    refresh_private_gold_paper_minutes,
)
from .private_gold_trade_revisions import (
    PRIVATE_GOLD_OFFER_LIFETIME_SECONDS,
    PRIVATE_GOLD_TRADE_REVISION_VERSION,
    PrivateGoldRevision,
    PrivateGoldTradeDecision,
    extract_private_gold_trade,
)
from .public_telegram.ingest import (
    PublicTelegramMessage,
    ingest_public_message,
    link_melted_flow_trade_sides,
)
from .public_telegram.parser import parse_public_message, should_ignore_public_message
from .public_telegram.sources import source_for_code


CAPTURE_ADAPTER_SCHEMA_VERSION = 9
CAPTURE_ADAPTER_VERSION = "capture-event-adapter-v10-terminal-lineage"
CAPTURE_RAW_RETENTION = timedelta(days=3)
COIN_GROUP_ACTIVE_REPLAY_WINDOW = timedelta(hours=6)
MARKET_BACKFILL_REPLAY_WINDOW = timedelta(minutes=30)
_MAX_TEXT_BYTES = 32 * 1024
_SAFE_EVENT_ID = re.compile(r"^[A-Za-z0-9._:-]{16,160}$")
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MARKET_EVENT_TYPES = frozenset(
    {"message_created", "message_edited", "message_deleted", "message_snapshot"}
)
_GROUP_EVENT_TYPES = frozenset(
    {"message_created", "message_edited", "message_deleted"}
)
_GROUP_REPLY_STATUSES = frozenset(
    {
        "not_reply",
        "resolved_from_live_stream",
        "resolved_from_api",
        "unavailable",
        "deleted",
    }
)
_PUBLIC_SOURCE_CODES = frozenset(
    {"MELTED_AGGREGATE", "MELTED_FLOW", "USD_HERAT", "XAUUSD"}
)
_PRIMARY_SOURCE_CODE = "MELTED_PRIMARY_FLOW"
_MARKET_SOURCE_CODES = _PUBLIC_SOURCE_CODES | {_PRIMARY_SOURCE_CODE}
_GROUP_NUMBERS = {"GROUP_1": 1, "GROUP_2": 2}
_PROMOTION_BACKFILL_SOURCE_CODES = frozenset(
    {_PRIMARY_SOURCE_CODE, "GROUP_1", "GROUP_2"}
)
_EXPLICIT_BACKFILL_SOURCE_CODES = _MARKET_SOURCE_CODES | frozenset(
    _GROUP_NUMBERS
)
_EXPLICIT_BACKFILL_STATUSES = frozenset({"PENDING", "PARSED", "FILTERED"})
_NON_MODEL_CONTENT_TYPES = frozenset({"media_only", "service"})
_CONTENT_TYPES = frozenset({"text", "caption"}) | _NON_MODEL_CONTENT_TYPES
_RETRACTION_REASON = "CAPTURE_SOURCE_REVISION_NOT_CURRENT"
_V8_RECONCILIATION_CODE = "V8_PRIVATE_ROOT_AND_COIN_PARSER_REPAIR"
_PRIMARY_PRICE_POLICY_REJECTION = "PRICE_OUT_OF_CANONICAL_RANGE"


class CaptureEventContractError(RuntimeError):
    """An event cannot safely cross the capture/model boundary."""


@dataclass(frozen=True, slots=True)
class CaptureEvent:
    stream: str
    event_id: str
    event_type: str
    source_id: str
    message_id: int
    available_at_utc: str
    event_time_utc: str | None
    edited_at_utc: str | None
    text: str | None
    is_forwarded: bool
    is_backfill: bool
    is_explicit_backfill: bool = False
    parser_profile: str | None = None
    entities_json: str = "[]"
    sender_identity: str | None = None
    sender_telegram_id: str | None = None
    sender_display_name: str | None = None
    reply_to_message_id: int | None = None
    content_type: str = "text"


@dataclass(frozen=True, slots=True)
class CaptureStageReport:
    accepted: bool
    duplicate: bool
    staged_change: bool
    tombstone_applied: bool


@dataclass(frozen=True, slots=True)
class CaptureProjectionReport:
    market_messages_reprojected: int
    market_facts_upserted: int
    market_facts_retracted: int
    private_paper_minutes_refreshed: int
    private_trade_facts_upserted: int
    private_trade_messages_finalized: int
    private_trade_messages_ambiguous: int
    group_pipeline: CoinGroupPipelineReport | None
    raw_rows_purged: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_adapter_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema_version INTEGER NOT NULL,
    initialized_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capture_seen_events (
    event_id TEXT PRIMARY KEY,
    stream TEXT NOT NULL,
    available_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capture_seen_expiry
    ON capture_seen_events(expires_at_utc);

CREATE TABLE IF NOT EXISTS capture_tombstones (
    stream TEXT NOT NULL,
    source_id TEXT NOT NULL,
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    available_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    PRIMARY KEY(stream, source_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_capture_tombstone_expiry
    ON capture_tombstones(expires_at_utc);

CREATE TABLE IF NOT EXISTS capture_market_messages (
    source_id TEXT NOT NULL,
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    event_time_utc TEXT NOT NULL,
    available_at_utc TEXT NOT NULL,
    edited_at_utc TEXT,
    parser_profile TEXT NOT NULL,
    is_forwarded INTEGER NOT NULL CHECK(is_forwarded IN (0,1)),
    message_text TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    content_digest BLOB NOT NULL CHECK(length(content_digest)=32),
    revision INTEGER NOT NULL CHECK(revision > 0),
    expires_at_utc TEXT NOT NULL,
    PRIMARY KEY(source_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_capture_market_source_time
    ON capture_market_messages(source_id,event_time_utc,message_id);
CREATE INDEX IF NOT EXISTS idx_capture_market_expiry
    ON capture_market_messages(expires_at_utc);

CREATE TABLE IF NOT EXISTS capture_market_message_revisions (
    source_id TEXT NOT NULL,
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_time_utc TEXT NOT NULL,
    available_at_utc TEXT NOT NULL,
    edited_at_utc TEXT,
    parser_profile TEXT NOT NULL,
    is_forwarded INTEGER NOT NULL CHECK(is_forwarded IN (0,1)),
    message_text TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    content_digest BLOB NOT NULL CHECK(length(content_digest)=32),
    expires_at_utc TEXT NOT NULL,
    PRIMARY KEY(source_id,message_id,event_id)
);
CREATE INDEX IF NOT EXISTS idx_capture_market_revision_message
    ON capture_market_message_revisions(source_id,message_id,edited_at_utc,available_at_utc);
CREATE INDEX IF NOT EXISTS idx_capture_market_revision_expiry
    ON capture_market_message_revisions(expires_at_utc);

CREATE TABLE IF NOT EXISTS capture_primary_trade_deadlines (
    source_id TEXT NOT NULL,
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    finalize_after_utc TEXT NOT NULL,
    finalized_at_utc TEXT,
    expires_at_utc TEXT NOT NULL,
    PRIMARY KEY(source_id,message_id)
);
CREATE INDEX IF NOT EXISTS idx_capture_primary_trade_due
    ON capture_primary_trade_deadlines(finalized_at_utc,finalize_after_utc);

CREATE TABLE IF NOT EXISTS capture_primary_trade_outcomes (
    source_id TEXT NOT NULL,
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    status TEXT NOT NULL CHECK(status IN ('PENDING','FULL','PARTIAL','NONE','AMBIGUOUS')),
    reason TEXT NOT NULL,
    finalize_after_utc TEXT NOT NULL,
    finalized_at_utc TEXT,
    offered_quantity INTEGER CHECK(offered_quantity IS NULL OR offered_quantity > 0),
    executed_quantity INTEGER CHECK(executed_quantity IS NULL OR executed_quantity > 0),
    remaining_quantity INTEGER CHECK(remaining_quantity IS NULL OR remaining_quantity >= 0),
    evidence_event_id TEXT,
    updated_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    PRIMARY KEY(source_id,message_id)
);
CREATE INDEX IF NOT EXISTS idx_capture_primary_outcome_expiry
    ON capture_primary_trade_outcomes(expires_at_utc);

CREATE TABLE IF NOT EXISTS capture_dirty_market_messages (
    source_id TEXT NOT NULL,
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    event_time_utc TEXT,
    available_at_utc TEXT NOT NULL,
    PRIMARY KEY(source_id,message_id)
);
CREATE INDEX IF NOT EXISTS idx_capture_dirty_market_ready
    ON capture_dirty_market_messages(available_at_utc,source_id,message_id);

CREATE TABLE IF NOT EXISTS capture_dirty_groups (
    group_number INTEGER PRIMARY KEY CHECK(group_number IN (1,2)),
    available_at_utc TEXT NOT NULL,
    minimum_event_time_utc TEXT
);

CREATE TABLE IF NOT EXISTS capture_projection_reconciliations (
    reconciliation_code TEXT PRIMARY KEY,
    requested_at_utc TEXT NOT NULL,
    completed_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS capture_projection_keys (
    source_id TEXT NOT NULL,
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    event_key BLOB NOT NULL CHECK(length(event_key) BETWEEN 16 AND 64),
    bucket_utc TEXT,
    PRIMARY KEY(source_id,message_id,event_key)
);
CREATE INDEX IF NOT EXISTS idx_capture_projection_bucket
    ON capture_projection_keys(source_id,bucket_utc);

CREATE TABLE IF NOT EXISTS capture_file_cursors (
    stream TEXT NOT NULL,
    file_path TEXT NOT NULL,
    device INTEGER NOT NULL,
    inode INTEGER NOT NULL,
    byte_offset INTEGER NOT NULL CHECK(byte_offset >= 0),
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(stream,file_path)
);

CREATE TABLE IF NOT EXISTS capture_rejected_records (
    record_sha256 TEXT PRIMARY KEY CHECK(length(record_sha256)=64),
    stream TEXT NOT NULL,
    reason TEXT NOT NULL,
    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    occurrences INTEGER NOT NULL CHECK(occurrences > 0),
    expires_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capture_rejection_expiry
    ON capture_rejected_records(expires_at_utc);

CREATE TABLE IF NOT EXISTS capture_explicit_backfill_lineage (
    event_id TEXT PRIMARY KEY,
    stream TEXT NOT NULL CHECK(stream IN ('market','coin')),
    source_id TEXT NOT NULL CHECK(
      source_id IN ('MELTED_PRIMARY_FLOW','GROUP_1','GROUP_2')
    ),
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    event_type TEXT NOT NULL,
    event_time_utc TEXT,
    available_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','PARSED','FILTERED')),
    disposition_code TEXT NOT NULL,
    terminal_at_utc TEXT,
    expires_at_utc TEXT NOT NULL,
    CHECK(
      (status='PENDING' AND terminal_at_utc IS NULL)
      OR (status IN ('PARSED','FILTERED') AND terminal_at_utc IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_capture_explicit_backfill_status
    ON capture_explicit_backfill_lineage(status,stream,source_id,message_id);
CREATE INDEX IF NOT EXISTS idx_capture_explicit_backfill_expiry
    ON capture_explicit_backfill_lineage(expires_at_utc);

CREATE TABLE IF NOT EXISTS capture_event_lineage (
    event_id TEXT PRIMARY KEY,
    stream TEXT NOT NULL CHECK(stream IN ('market','coin')),
    source_id TEXT NOT NULL,
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    event_type TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('live','reconcile','explicit_backfill')),
    event_time_utc TEXT,
    available_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','PARSED','FILTERED')),
    disposition_code TEXT NOT NULL,
    terminal_at_utc TEXT,
    expires_at_utc TEXT NOT NULL,
    CHECK(
      (status='PENDING' AND terminal_at_utc IS NULL)
      OR (status IN ('PARSED','FILTERED') AND terminal_at_utc IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_capture_event_lineage_status
    ON capture_event_lineage(status,stream,source_id,message_id);
CREATE INDEX IF NOT EXISTS idx_capture_event_lineage_expiry
    ON capture_event_lineage(expires_at_utc);
CREATE TABLE IF NOT EXISTS capture_event_lineage_control (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    enabled_at_utc TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _primary_trade_finalize_after(event_time_utc: str) -> str:
    moment = datetime.fromisoformat(event_time_utc.replace("Z", "+00:00"))
    return (
        moment + timedelta(seconds=PRIVATE_GOLD_OFFER_LIFETIME_SECONDS)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp(value: object, *, field: str, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise CaptureEventContractError(f"{field}_required")
        return None
    try:
        return normalize_utc(value, field_name=field)
    except Exception as exc:
        raise CaptureEventContractError(f"{field}_invalid") from exc


def _positive_message_id(value: object) -> int:
    if isinstance(value, bool):
        raise CaptureEventContractError("capture_message_id_invalid")
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise CaptureEventContractError("capture_message_id_invalid") from exc
    if result <= 0:
        raise CaptureEventContractError("capture_message_id_invalid")
    return result


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CaptureEventContractError(f"{field}_invalid")
    return value


def _text(value: object, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise CaptureEventContractError("capture_message_text_required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise CaptureEventContractError("capture_message_text_invalid")
    if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise CaptureEventContractError("capture_message_text_too_large")
    return value


def _content_type_and_text(
    message: Mapping[str, object], *, deleted: bool
) -> tuple[str, str | None]:
    if deleted:
        return "deleted", _text(message.get("text"), required=False)
    raw_type = str(message.get("content_type") or "text").strip().lower()
    if raw_type not in _CONTENT_TYPES:
        raise CaptureEventContractError("capture_message_content_type_invalid")
    raw_text = message.get("text")
    if raw_type in _NON_MODEL_CONTENT_TYPES:
        if raw_text is not None and (
            not isinstance(raw_text, str) or bool(raw_text.strip())
        ):
            raise CaptureEventContractError("capture_non_model_text_invalid")
        return raw_type, None
    return raw_type, _text(raw_text, required=True)


def _entities_json(value: object, *, text: str | None) -> str:
    if value is None:
        return "[]"
    if not isinstance(value, list) or len(value) > 512:
        raise CaptureEventContractError("market_capture_entities_invalid")
    text_units = len(str(text or "").encode("utf-16-le")) // 2
    normalized: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise CaptureEventContractError("market_capture_entity_invalid")
        entity_type = str(item.get("type") or "").strip()
        try:
            offset = int(item.get("offset_utf16"))
            length = int(item.get("length_utf16"))
        except (TypeError, ValueError) as exc:
            raise CaptureEventContractError("market_capture_entity_range_invalid") from exc
        if (
            not entity_type
            or len(entity_type) > 96
            or offset < 0
            or length <= 0
            or offset + length > text_units
        ):
            raise CaptureEventContractError("market_capture_entity_range_invalid")
        row: dict[str, object] = {
            "type": entity_type,
            "offset_utf16": offset,
            "length_utf16": length,
        }
        if "url_present" in item:
            row["url_present"] = bool(item.get("url_present"))
        normalized.append(row)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _event_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_EVENT_ID.fullmatch(normalized):
        raise CaptureEventContractError("capture_event_id_invalid")
    return normalized


def _validate_time_order(event: CaptureEvent) -> None:
    if event.event_time_utc is not None and event.available_at_utc < event.event_time_utc:
        raise CaptureEventContractError("capture_available_before_event")
    if event.edited_at_utc is not None:
        if event.event_time_utc is None or event.edited_at_utc < event.event_time_utc:
            raise CaptureEventContractError("capture_edit_before_event")
        if event.available_at_utc < event.edited_at_utc:
            raise CaptureEventContractError("capture_available_before_edit")


def _validate_explicit_backfill_contract(event: CaptureEvent) -> None:
    if not event.is_explicit_backfill:
        return
    if not event.is_backfill:
        raise CaptureEventContractError("capture_explicit_backfill_flag_required")
    if event.source_id not in _EXPLICIT_BACKFILL_SOURCE_CODES:
        raise CaptureEventContractError("capture_explicit_backfill_source_unsupported")
    if (
        event.stream == "market" and event.source_id not in _MARKET_SOURCE_CODES
    ) or (
        event.stream == "coin" and event.source_id not in _GROUP_NUMBERS
    ):
        raise CaptureEventContractError("capture_explicit_backfill_stream_mismatch")


def decode_market_channel_event(record: object) -> CaptureEvent:
    envelope = _mapping(record, field="market_capture_envelope")
    if envelope.get("schema") != "market_channel_event" or str(envelope.get("schema_version")) != "1.0":
        raise CaptureEventContractError("market_capture_schema_unsupported")
    event_type = str(envelope.get("event_type") or "")
    if event_type not in _MARKET_EVENT_TYPES:
        raise CaptureEventContractError("market_capture_event_type_unsupported")
    source = _mapping(envelope.get("source"), field="market_capture_source")
    source_id = str(source.get("source_id") or "").strip().upper()
    if source_id not in _MARKET_SOURCE_CODES or source.get("market") != "coin_intelligence":
        raise CaptureEventContractError("market_capture_source_unsupported")
    profile = str(source.get("parser_profile") or "").strip().upper()
    if not profile:
        raise CaptureEventContractError("market_capture_parser_profile_missing")
    message = _mapping(envelope.get("message"), field="market_capture_message")
    deleted = event_type == "message_deleted"
    content_type, text = _content_type_and_text(message, deleted=deleted)
    if text is not None or content_type in _NON_MODEL_CONTENT_TYPES:
        digest_text = text or ""
        digest = str(message.get("text_sha256") or "").strip().lower()
        if not _HEX_SHA256.fullmatch(digest) or sha256(digest_text.encode("utf-8")).hexdigest() != digest:
            raise CaptureEventContractError("market_capture_text_digest_mismatch")
    producer = _mapping(envelope.get("producer"), field="market_capture_producer")
    is_backfill = bool(producer.get("is_backfill", False))
    event = CaptureEvent(
        stream="market",
        event_id=_event_id(envelope.get("event_id")),
        event_type=event_type,
        source_id=source_id,
        message_id=_positive_message_id(message.get("message_id")),
        available_at_utc=str(_stamp(producer.get("available_at_utc"), field="market_capture_available_at_utc")),
        event_time_utc=_stamp(message.get("published_at_utc"), field="market_capture_event_time_utc", required=not deleted),
        edited_at_utc=_stamp(message.get("edited_at_utc"), field="market_capture_edited_at_utc", required=False),
        text=text,
        is_forwarded=bool(message.get("is_forwarded", False)),
        is_backfill=is_backfill,
        is_explicit_backfill=(
            str(producer.get("origin") or "").strip() == "explicit_backfill"
        ),
        parser_profile=profile,
        entities_json=_entities_json(message.get("entities"), text=text),
        content_type=content_type,
    )
    _validate_time_order(event)
    _validate_explicit_backfill_contract(event)
    return event


def decode_coin_group_event(record: object) -> CaptureEvent:
    envelope = _mapping(record, field="coin_capture_envelope")
    schema_version = str(envelope.get("schema_version"))
    if (
        envelope.get("schema") != "coin_group_event"
        or schema_version not in {"2.0", "2.1"}
    ):
        raise CaptureEventContractError("coin_capture_schema_unsupported")
    event_type = str(envelope.get("event_type") or "")
    if event_type not in _GROUP_EVENT_TYPES:
        raise CaptureEventContractError("coin_capture_event_type_unsupported")
    source = _mapping(envelope.get("source"), field="coin_capture_source")
    source_id = str(source.get("source_id") or "").strip().upper()
    if source_id not in _GROUP_NUMBERS or source.get("market") != "coin":
        raise CaptureEventContractError("coin_capture_source_unsupported")
    message = _mapping(envelope.get("message"), field="coin_capture_message")
    deleted = event_type == "message_deleted"
    content_type, text = _content_type_and_text(message, deleted=deleted)
    producer = _mapping(envelope.get("producer"), field="coin_capture_producer")
    # Legacy reconciled rows explicitly lack a trustworthy receipt time.  They
    # are not repaired from event time because doing so would leak future data
    # into historical evaluation.  Live v2.2+ rows always populate this field.
    available = _stamp(producer.get("available_at_utc"), field="coin_capture_available_at_utc")
    reply_to = None
    sender = None
    sender_telegram_id = None
    sender_display_name = None
    if not deleted:
        reply = _mapping(message.get("reply"), field="coin_capture_reply")
        status = str(reply.get("status") or "")
        if status not in _GROUP_REPLY_STATUSES:
            raise CaptureEventContractError("coin_capture_reply_status_unsupported")
        if status in {"resolved_from_live_stream", "resolved_from_api"}:
            reply_to = _positive_message_id(reply.get("message_id"))
        sender_payload = _mapping(message.get("sender"), field="coin_capture_sender")
        raw_sender = str(sender_payload.get("peer_id") or "").strip()
        sender = raw_sender or None
        raw_telegram_id = str(sender_payload.get("telegram_id") or "").strip()
        if raw_telegram_id:
            if not re.fullmatch(r"[1-9][0-9]{0,19}", raw_telegram_id):
                raise CaptureEventContractError("coin_capture_sender_telegram_id_invalid")
            sender_telegram_id = raw_telegram_id
        raw_display_name = sender_payload.get("display_name")
        if raw_display_name is not None:
            if not isinstance(raw_display_name, str):
                raise CaptureEventContractError("coin_capture_sender_name_invalid")
            sender_display_name = " ".join(raw_display_name.split()) or None
            if (
                sender_display_name is not None
                and len(sender_display_name.encode("utf-8")) > 512
            ):
                raise CaptureEventContractError("coin_capture_sender_name_invalid")
        if schema_version == "2.1" and sender and sender_telegram_id is None:
            raise CaptureEventContractError("coin_capture_sender_telegram_id_missing")
    is_backfill = bool(message.get("is_backfill", False))
    event = CaptureEvent(
        stream="coin",
        event_id=_event_id(envelope.get("event_id")),
        event_type=event_type,
        source_id=source_id,
        message_id=_positive_message_id(message.get("message_id")),
        available_at_utc=str(available),
        event_time_utc=_stamp(message.get("published_at_utc"), field="coin_capture_event_time_utc", required=not deleted),
        edited_at_utc=_stamp(message.get("edited_at_utc"), field="coin_capture_edited_at_utc", required=False),
        text=text,
        is_forwarded=bool(message.get("is_forwarded", False)),
        is_backfill=is_backfill,
        is_explicit_backfill=(
            str(producer.get("origin") or "").strip() == "explicit_backfill"
        ),
        sender_identity=sender,
        sender_telegram_id=sender_telegram_id,
        sender_display_name=sender_display_name,
        reply_to_message_id=reply_to,
        content_type=content_type,
    )
    _validate_time_order(event)
    _validate_explicit_backfill_contract(event)
    return event


def decode_capture_event(record: object, *, stream: str) -> CaptureEvent:
    normalized = str(stream).strip().lower()
    if normalized == "market":
        return decode_market_channel_event(record)
    if normalized == "coin":
        return decode_coin_group_event(record)
    raise CaptureEventContractError("capture_stream_unsupported")


def initialize_capture_adapter(connection: sqlite3.Connection) -> None:
    initialize_coin_group_staging(connection)
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='capture_adapter_metadata'"
    ).fetchone()
    if row is None:
        connection.executescript(_SCHEMA)
        initialized = _utc_now()
        connection.execute(
            "INSERT INTO capture_adapter_metadata(singleton,schema_version,initialized_at_utc) VALUES(1,?,?)",
            (CAPTURE_ADAPTER_SCHEMA_VERSION, initialized),
        )
        connection.execute(
            "INSERT INTO capture_event_lineage_control(singleton,enabled_at_utc) "
            "VALUES(1,?)",
            (initialized,),
        )
        connection.commit()
        return
    metadata = connection.execute(
        "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
    ).fetchone()
    if metadata is not None and int(metadata[0]) == 1:
        connection.executescript(
            """
            ALTER TABLE capture_dirty_groups
              ADD COLUMN minimum_event_time_utc TEXT;
            UPDATE capture_adapter_metadata SET schema_version=2 WHERE singleton=1;
            """
        )
        connection.commit()
        metadata = connection.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
    if metadata is not None and int(metadata[0]) == 2:
        rows = connection.execute(
            """
            SELECT source_id,message_id,event_time_utc,available_at_utc,
                   edited_at_utc,parser_profile,is_forwarded,message_text
            FROM capture_market_messages
            """
        ).fetchall()
        for current in rows:
            event = CaptureEvent(
                stream="market",
                event_id="capture-digest-migration-v3",
                event_type="message_snapshot",
                source_id=str(current["source_id"]),
                message_id=int(current["message_id"]),
                available_at_utc=str(current["available_at_utc"]),
                event_time_utc=str(current["event_time_utc"]),
                edited_at_utc=(
                    str(current["edited_at_utc"])
                    if current["edited_at_utc"] is not None
                    else None
                ),
                text=str(current["message_text"]),
                is_forwarded=bool(current["is_forwarded"]),
                is_backfill=False,
                parser_profile=str(current["parser_profile"]),
            )
            connection.execute(
                """
                UPDATE capture_market_messages SET content_digest=?
                WHERE source_id=? AND message_id=?
                """,
                (
                    _market_digest(event),
                    event.source_id,
                    event.message_id,
                ),
            )
        connection.execute(
            "UPDATE capture_adapter_metadata SET schema_version=3 WHERE singleton=1"
        )
        connection.commit()
        metadata = connection.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
    if metadata is not None and int(metadata[0]) == 3:
        connection.executescript(
            """
            ALTER TABLE capture_market_messages
              ADD COLUMN entities_json TEXT NOT NULL DEFAULT '[]';
            CREATE TABLE capture_market_message_revisions (
                source_id TEXT NOT NULL,
                message_id INTEGER NOT NULL CHECK(message_id > 0),
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_time_utc TEXT NOT NULL,
                available_at_utc TEXT NOT NULL,
                edited_at_utc TEXT,
                parser_profile TEXT NOT NULL,
                is_forwarded INTEGER NOT NULL CHECK(is_forwarded IN (0,1)),
                message_text TEXT NOT NULL,
                entities_json TEXT NOT NULL,
                content_digest BLOB NOT NULL CHECK(length(content_digest)=32),
                expires_at_utc TEXT NOT NULL,
                PRIMARY KEY(source_id,message_id,event_id)
            );
            CREATE INDEX idx_capture_market_revision_message
                ON capture_market_message_revisions(
                  source_id,message_id,edited_at_utc,available_at_utc
                );
            CREATE INDEX idx_capture_market_revision_expiry
                ON capture_market_message_revisions(expires_at_utc);
            CREATE TABLE capture_primary_trade_deadlines (
                source_id TEXT NOT NULL,
                message_id INTEGER NOT NULL CHECK(message_id > 0),
                finalize_after_utc TEXT NOT NULL,
                finalized_at_utc TEXT,
                expires_at_utc TEXT NOT NULL,
                PRIMARY KEY(source_id,message_id)
            );
            CREATE INDEX idx_capture_primary_trade_due
                ON capture_primary_trade_deadlines(
                  finalized_at_utc,finalize_after_utc
                );
            """
        )
        rows = connection.execute(
            """
            SELECT source_id,message_id,event_time_utc,available_at_utc,
                   edited_at_utc,parser_profile,is_forwarded,message_text,
                   entities_json,content_digest,expires_at_utc
            FROM capture_market_messages
            """
        ).fetchall()
        for current in rows:
            seed_id = "capture-v4-seed-" + sha256(
                b"|".join(
                    (
                        str(current["source_id"]).encode(),
                        str(current["message_id"]).encode(),
                        bytes(current["content_digest"]),
                    )
                )
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO capture_market_message_revisions(
                  source_id,message_id,event_id,event_type,event_time_utc,
                  available_at_utc,edited_at_utc,parser_profile,is_forwarded,
                  message_text,entities_json,content_digest,expires_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(current["source_id"]),
                    int(current["message_id"]),
                    seed_id,
                    "message_snapshot",
                    str(current["event_time_utc"]),
                    str(current["available_at_utc"]),
                    (
                        str(current["edited_at_utc"])
                        if current["edited_at_utc"] is not None
                        else None
                    ),
                    str(current["parser_profile"]),
                    int(current["is_forwarded"]),
                    str(current["message_text"]),
                    str(current["entities_json"]),
                    bytes(current["content_digest"]),
                    str(current["expires_at_utc"]),
                ),
            )
            if str(current["source_id"]) == _PRIMARY_SOURCE_CODE:
                connection.execute(
                    """
                    INSERT INTO capture_primary_trade_deadlines(
                      source_id,message_id,finalize_after_utc,finalized_at_utc,
                      expires_at_utc
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        _PRIMARY_SOURCE_CODE,
                        int(current["message_id"]),
                        _primary_trade_finalize_after(str(current["event_time_utc"])),
                        str(current["available_at_utc"]),
                        str(current["expires_at_utc"]),
                    ),
                )
        connection.execute(
            "UPDATE capture_adapter_metadata SET schema_version=4 WHERE singleton=1"
        )
        connection.commit()
        metadata = connection.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
    if metadata is not None and int(metadata[0]) == 4:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS capture_primary_trade_outcomes (
                source_id TEXT NOT NULL,
                message_id INTEGER NOT NULL CHECK(message_id > 0),
                status TEXT NOT NULL CHECK(
                  status IN ('PENDING','FULL','PARTIAL','NONE','AMBIGUOUS')
                ),
                reason TEXT NOT NULL,
                finalize_after_utc TEXT NOT NULL,
                finalized_at_utc TEXT,
                offered_quantity INTEGER CHECK(
                  offered_quantity IS NULL OR offered_quantity > 0
                ),
                executed_quantity INTEGER CHECK(
                  executed_quantity IS NULL OR executed_quantity > 0
                ),
                remaining_quantity INTEGER CHECK(
                  remaining_quantity IS NULL OR remaining_quantity >= 0
                ),
                evidence_event_id TEXT,
                updated_at_utc TEXT NOT NULL,
                expires_at_utc TEXT NOT NULL,
                PRIMARY KEY(source_id,message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_capture_primary_outcome_expiry
                ON capture_primary_trade_outcomes(expires_at_utc);
            UPDATE capture_adapter_metadata
               SET schema_version=5
             WHERE singleton=1;
            """
        )
        connection.commit()
        metadata = connection.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
    if metadata is not None and int(metadata[0]) == 5:
        requested_at = _utc_now()
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS capture_projection_reconciliations (
                reconciliation_code TEXT PRIMARY KEY,
                requested_at_utc TEXT NOT NULL,
                completed_at_utc TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO capture_projection_reconciliations(
              reconciliation_code,requested_at_utc,completed_at_utc
            ) VALUES(?,?,NULL)
            """,
            (_V8_RECONCILIATION_CODE, requested_at),
        )
        # Only rows with a confirmed lifecycle outcome need the private-root
        # repair.  Reprojecting every high-volume offer would add avoidable
        # cutover load without changing its immutable economics.
        connection.execute(
            """
            INSERT INTO capture_dirty_market_messages(
              source_id,message_id,event_time_utc,available_at_utc
            )
            SELECT current.source_id,current.message_id,current.event_time_utc,?
            FROM capture_market_messages AS current
            JOIN capture_primary_trade_outcomes AS outcome
              ON outcome.source_id=current.source_id
             AND outcome.message_id=current.message_id
            WHERE current.source_id=? AND outcome.status IN ('FULL','PARTIAL')
            ON CONFLICT(source_id,message_id) DO UPDATE SET
              event_time_utc=excluded.event_time_utc,
              available_at_utc=MAX(
                excluded.available_at_utc,
                capture_dirty_market_messages.available_at_utc
              )
            """,
            (requested_at, _PRIMARY_SOURCE_CODE),
        )
        connection.execute(
            "UPDATE capture_adapter_metadata SET schema_version=6 WHERE singleton=1"
        )
        connection.commit()
        metadata = connection.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
    if metadata is not None and int(metadata[0]) == 6:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS capture_explicit_backfill_lineage (
                event_id TEXT PRIMARY KEY,
                stream TEXT NOT NULL CHECK(stream IN ('market','coin')),
                source_id TEXT NOT NULL CHECK(
                  source_id IN ('MELTED_PRIMARY_FLOW','GROUP_1','GROUP_2')
                ),
                message_id INTEGER NOT NULL CHECK(message_id > 0),
                event_type TEXT NOT NULL,
                event_time_utc TEXT,
                available_at_utc TEXT NOT NULL,
                status TEXT NOT NULL CHECK(
                  status IN ('PENDING','PARSED','FILTERED')
                ),
                disposition_code TEXT NOT NULL,
                terminal_at_utc TEXT,
                expires_at_utc TEXT NOT NULL,
                CHECK(
                  (status='PENDING' AND terminal_at_utc IS NULL)
                  OR (
                    status IN ('PARSED','FILTERED')
                    AND terminal_at_utc IS NOT NULL
                  )
                )
            );
            CREATE INDEX IF NOT EXISTS idx_capture_explicit_backfill_status
                ON capture_explicit_backfill_lineage(
                  status,stream,source_id,message_id
                );
            CREATE INDEX IF NOT EXISTS idx_capture_explicit_backfill_expiry
                ON capture_explicit_backfill_lineage(expires_at_utc);
            UPDATE capture_adapter_metadata
               SET schema_version=7
             WHERE singleton=1;
            """
        )
        connection.commit()
        metadata = connection.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
    if metadata is not None and int(metadata[0]) == 7:
        enabled = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS capture_event_lineage (
                event_id TEXT PRIMARY KEY,
                stream TEXT NOT NULL CHECK(stream IN ('market','coin')),
                source_id TEXT NOT NULL,
                message_id INTEGER NOT NULL CHECK(message_id > 0),
                event_type TEXT NOT NULL,
                origin TEXT NOT NULL CHECK(
                  origin IN ('live','reconcile','explicit_backfill')
                ),
                event_time_utc TEXT,
                available_at_utc TEXT NOT NULL,
                status TEXT NOT NULL CHECK(
                  status IN ('PENDING','PARSED','FILTERED')
                ),
                disposition_code TEXT NOT NULL,
                terminal_at_utc TEXT,
                expires_at_utc TEXT NOT NULL,
                CHECK(
                  (status='PENDING' AND terminal_at_utc IS NULL)
                  OR (
                    status IN ('PARSED','FILTERED')
                    AND terminal_at_utc IS NOT NULL
                  )
                )
            );
            CREATE INDEX IF NOT EXISTS idx_capture_event_lineage_status
                ON capture_event_lineage(
                  status,stream,source_id,message_id
                );
            CREATE INDEX IF NOT EXISTS idx_capture_event_lineage_expiry
                ON capture_event_lineage(expires_at_utc);
            CREATE TABLE IF NOT EXISTS capture_event_lineage_control (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                enabled_at_utc TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO capture_event_lineage_control(singleton,enabled_at_utc) "
            "VALUES(1,?)",
            (enabled,),
        )
        connection.execute(
            "UPDATE capture_adapter_metadata SET schema_version=8 WHERE singleton=1"
        )
        connection.commit()
        metadata = connection.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
    if metadata is not None and int(metadata[0]) == 8:
        # Projection drains this table in causal availability order.  Without
        # the matching index every bounded batch scans and sorts the complete
        # backlog, which makes recovery progressively slower as replay grows.
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_capture_dirty_market_ready
                ON capture_dirty_market_messages(
                  available_at_utc,source_id,message_id
                );
            UPDATE capture_adapter_metadata
               SET schema_version=9
             WHERE singleton=1;
            """
        )
        connection.commit()
        metadata = connection.execute(
            "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
        ).fetchone()
    if metadata is None or int(metadata[0]) != CAPTURE_ADAPTER_SCHEMA_VERSION:
        raise CaptureEventContractError("capture_adapter_schema_upgrade_required")


def _expiry(value: str) -> str:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (moment + CAPTURE_RAW_RETENTION).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _market_digest(event: CaptureEvent) -> bytes:
    digest = blake2b(digest_size=32, person=b"capture-market1")
    for value in (
        event.source_id,
        event.message_id,
        event.event_time_utc,
        event.edited_at_utc,
        int(event.is_forwarded),
        event.text,
        event.entities_json,
    ):
        encoded = str(value or "").encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def _mark_dirty_market(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    message_id: int,
    event_time_utc: str | None,
    available_at_utc: str,
) -> None:
    connection.execute(
        """
        INSERT INTO capture_dirty_market_messages(source_id,message_id,event_time_utc,available_at_utc)
        VALUES(?,?,?,?)
        ON CONFLICT(source_id,message_id) DO UPDATE SET
          event_time_utc=COALESCE(excluded.event_time_utc,capture_dirty_market_messages.event_time_utc),
          available_at_utc=MAX(excluded.available_at_utc,capture_dirty_market_messages.available_at_utc)
        """,
        (source_id, message_id, event_time_utc, available_at_utc),
    )


def _mark_dirty_group(
    connection: sqlite3.Connection,
    group: int,
    available: str,
    event_time: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO capture_dirty_groups(
          group_number,available_at_utc,minimum_event_time_utc
        ) VALUES(?,?,?)
        ON CONFLICT(group_number) DO UPDATE SET
          available_at_utc=MAX(excluded.available_at_utc,capture_dirty_groups.available_at_utc),
          minimum_event_time_utc=CASE
            WHEN capture_dirty_groups.minimum_event_time_utc IS NULL
              THEN excluded.minimum_event_time_utc
            WHEN excluded.minimum_event_time_utc IS NULL
              THEN capture_dirty_groups.minimum_event_time_utc
            ELSE MIN(
              capture_dirty_groups.minimum_event_time_utc,
              excluded.minimum_event_time_utc
            )
          END
        """,
        (group, available, event_time),
    )


def _apply_tombstone(connection: sqlite3.Connection, event: CaptureEvent) -> bool:
    existing = connection.execute(
        "SELECT available_at_utc FROM capture_tombstones WHERE stream=? AND source_id=? AND message_id=?",
        (event.stream, event.source_id, event.message_id),
    ).fetchone()
    if existing is not None and str(existing[0]) >= event.available_at_utc:
        return False
    if event.stream == "market":
        current = connection.execute(
            "SELECT event_time_utc,available_at_utc FROM capture_market_messages WHERE source_id=? AND message_id=?",
            (event.source_id, event.message_id),
        ).fetchone()
    else:
        current = connection.execute(
            "SELECT event_time_utc,available_at_utc FROM coin_group_staged_messages WHERE group_number=? AND message_id=?",
            (_GROUP_NUMBERS[event.source_id], event.message_id),
        ).fetchone()
    # An out-of-order tombstone cannot erase a revision already received
    # later.  Do this check before persisting the tombstone, otherwise future
    # idempotent replays of the valid revision would be suppressed too.
    if current is not None and str(current["available_at_utc"]) > event.available_at_utc:
        return False
    connection.execute(
        """
        INSERT INTO capture_tombstones(stream,source_id,message_id,available_at_utc,expires_at_utc)
        VALUES(?,?,?,?,?)
        ON CONFLICT(stream,source_id,message_id) DO UPDATE SET
          available_at_utc=excluded.available_at_utc,
          expires_at_utc=excluded.expires_at_utc
        """,
        (event.stream, event.source_id, event.message_id, event.available_at_utc, _expiry(event.available_at_utc)),
    )
    if event.stream == "market":
        event_time = str(current["event_time_utc"]) if current is not None else event.event_time_utc
        # The private source routinely removes an offer when its two-minute
        # display window closes.  That deletion is neither a correction nor
        # trade evidence: retain the bounded current row/revision graph so the
        # immutable offer and evidenced outcome survive.  Public-source
        # deletes remain true retractions.
        if event.source_id != _PRIMARY_SOURCE_CODE:
            connection.execute(
                "DELETE FROM capture_market_messages WHERE source_id=? AND message_id=?",
                (event.source_id, event.message_id),
            )
        _mark_dirty_market(
            connection,
            source_id=event.source_id,
            message_id=event.message_id,
            event_time_utc=event_time,
            available_at_utc=event.available_at_utc,
        )
        if event.source_id == _PRIMARY_SOURCE_CODE:
            connection.execute(
                """
                UPDATE capture_primary_trade_deadlines
                SET expires_at_utc=?
                WHERE source_id=? AND message_id=?
                """,
                (
                    _expiry(event.available_at_utc),
                    event.source_id,
                    event.message_id,
                ),
            )
    else:
        group = _GROUP_NUMBERS[event.source_id]
        delete_coin_group_staged_message(
            connection, group_number=group, message_id=event.message_id
        )
        _mark_dirty_group(
            connection,
            group,
            event.available_at_utc,
            str(current["event_time_utc"]) if current is not None else None,
        )
    return True


def _apply_market_message(connection: sqlite3.Connection, event: CaptureEvent) -> bool:
    tombstone = connection.execute(
        "SELECT 1 FROM capture_tombstones WHERE stream='market' AND source_id=? AND message_id=?",
        (event.source_id, event.message_id),
    ).fetchone()
    if tombstone is not None:
        return False
    assert event.event_time_utc is not None and event.text is not None
    revision_time = event.edited_at_utc or event.event_time_utc
    if event.is_backfill and not event.is_explicit_backfill:
        available = datetime.fromisoformat(
            event.available_at_utc.replace("Z", "+00:00")
        )
        oldest = (available - MARKET_BACKFILL_REPLAY_WINDOW).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")
        if revision_time < oldest:
            return False
    digest = _market_digest(event)
    existing = connection.execute(
        "SELECT * FROM capture_market_messages WHERE source_id=? AND message_id=?",
        (event.source_id, event.message_id),
    ).fetchone()
    if existing is not None:
        if bytes(existing["content_digest"]) == digest:
            return False
        existing_revision_time = str(existing["edited_at_utc"] or existing["event_time_utc"])
        incoming_revision_time = str(revision_time)
        if incoming_revision_time < existing_revision_time:
            return False
        if incoming_revision_time == existing_revision_time and event.available_at_utc <= str(existing["available_at_utc"]):
            return False
    connection.execute(
        """
        INSERT OR IGNORE INTO capture_market_message_revisions(
          source_id,message_id,event_id,event_type,event_time_utc,
          available_at_utc,edited_at_utc,parser_profile,is_forwarded,
          message_text,entities_json,content_digest,expires_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event.source_id,
            event.message_id,
            event.event_id,
            event.event_type,
            event.event_time_utc,
            event.available_at_utc,
            event.edited_at_utc,
            event.parser_profile,
            int(event.is_forwarded),
            event.text,
            event.entities_json,
            digest,
            _expiry(event.available_at_utc),
        ),
    )
    revision = int(existing["revision"]) + 1 if existing is not None else 1
    connection.execute(
        """
        INSERT INTO capture_market_messages(
          source_id,message_id,event_time_utc,available_at_utc,edited_at_utc,
          parser_profile,is_forwarded,message_text,entities_json,content_digest,
          revision,expires_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id,message_id) DO UPDATE SET
          event_time_utc=excluded.event_time_utc,
          available_at_utc=excluded.available_at_utc,
          edited_at_utc=excluded.edited_at_utc,
          parser_profile=excluded.parser_profile,
          is_forwarded=excluded.is_forwarded,
          message_text=excluded.message_text,
          entities_json=excluded.entities_json,
          content_digest=excluded.content_digest,
          revision=excluded.revision,
          expires_at_utc=excluded.expires_at_utc
        """,
        (
            event.source_id,
            event.message_id,
            event.event_time_utc,
            event.available_at_utc,
            event.edited_at_utc,
            event.parser_profile,
            int(event.is_forwarded),
            event.text,
            event.entities_json,
            digest,
            revision,
            _expiry(event.available_at_utc),
        ),
    )
    if event.source_id == _PRIMARY_SOURCE_CODE:
        finalize_after = _primary_trade_finalize_after(event.event_time_utc)
        reset_finalization = (
            event.event_type in {"message_created", "message_snapshot"}
            or (event.edited_at_utc is not None and event.edited_at_utc <= finalize_after)
        )
        connection.execute(
            """
            INSERT INTO capture_primary_trade_deadlines(
              source_id,message_id,finalize_after_utc,finalized_at_utc,
              expires_at_utc
            ) VALUES(?,?,?,?,?)
            ON CONFLICT(source_id,message_id) DO UPDATE SET
              finalize_after_utc=excluded.finalize_after_utc,
              finalized_at_utc=CASE
                WHEN ? THEN NULL
                ELSE capture_primary_trade_deadlines.finalized_at_utc
              END,
              expires_at_utc=excluded.expires_at_utc
            """,
            (
                event.source_id,
                event.message_id,
                finalize_after,
                None,
                _expiry(event.available_at_utc),
                int(reset_finalization),
            ),
        )
    _mark_dirty_market(
        connection,
        source_id=event.source_id,
        message_id=event.message_id,
        event_time_utc=event.event_time_utc,
        available_at_utc=event.available_at_utc,
    )
    return True


def _apply_group_message(connection: sqlite3.Connection, event: CaptureEvent) -> bool:
    tombstone = connection.execute(
        "SELECT 1 FROM capture_tombstones WHERE stream='coin' AND source_id=? AND message_id=?",
        (event.source_id, event.message_id),
    ).fetchone()
    if tombstone is not None or event.is_forwarded:
        return False
    assert event.event_time_utc is not None and event.text is not None
    group = _GROUP_NUMBERS[event.source_id]
    existing = connection.execute(
        "SELECT event_time_utc,available_at_utc,edited_at_utc FROM coin_group_staged_messages WHERE group_number=? AND message_id=?",
        (group, event.message_id),
    ).fetchone()
    if existing is not None:
        existing_revision_time = str(existing["edited_at_utc"] or existing["event_time_utc"])
        incoming_revision_time = str(event.edited_at_utc or event.event_time_utc)
        if incoming_revision_time < existing_revision_time:
            return False
        if incoming_revision_time == existing_revision_time and event.available_at_utc < str(existing["available_at_utc"]):
            return False
    changed = stage_coin_group_message(
        connection,
        CoinGroupStagingMessage(
            group_number=group,
            message_id=event.message_id,
            event_time_utc=event.event_time_utc,
            available_at_utc=event.available_at_utc,
            text=event.text,
            reply_to_message_id=event.reply_to_message_id,
            sender_identity=event.sender_identity,
            sender_telegram_id=event.sender_telegram_id,
            sender_display_name=event.sender_display_name,
            edited_at_utc=event.edited_at_utc,
        ),
        staged_at_utc=event.available_at_utc,
    )
    if changed:
        _mark_dirty_group(
            connection,
            group,
            event.available_at_utc,
            event.event_time_utc,
        )
    return changed


def _apply_non_model_message(
    connection: sqlite3.Connection, event: CaptureEvent
) -> bool:
    """Retire a previously modelled current row without parsing empty media.

    A standalone media/service event is terminal immediately.  If it is a
    newer revision of a public-market or group message, the prior model fact
    must also be retracted; otherwise an empty edit would leave stale pricing
    evidence active.  Private-gold offer economics remain immutable by their
    separate lifecycle contract.
    """

    if event.source_id == _PRIMARY_SOURCE_CODE:
        return False
    revision_time = event.edited_at_utc or event.event_time_utc
    if event.stream == "market":
        current = connection.execute(
            "SELECT event_time_utc,available_at_utc,edited_at_utc "
            "FROM capture_market_messages WHERE source_id=? AND message_id=?",
            (event.source_id, event.message_id),
        ).fetchone()
        if current is None:
            return False
        current_revision = str(current["edited_at_utc"] or current["event_time_utc"])
        if revision_time is None or revision_time < current_revision:
            return False
        connection.execute(
            "DELETE FROM capture_market_messages WHERE source_id=? AND message_id=?",
            (event.source_id, event.message_id),
        )
        _mark_dirty_market(
            connection,
            source_id=event.source_id,
            message_id=event.message_id,
            event_time_utc=str(current["event_time_utc"]),
            available_at_utc=event.available_at_utc,
        )
        return True
    group = _GROUP_NUMBERS[event.source_id]
    current = connection.execute(
        "SELECT event_time_utc,available_at_utc,edited_at_utc "
        "FROM coin_group_staged_messages WHERE group_number=? AND message_id=?",
        (group, event.message_id),
    ).fetchone()
    if current is None:
        return False
    current_revision = str(current["edited_at_utc"] or current["event_time_utc"])
    if revision_time is None or revision_time < current_revision:
        return False
    delete_coin_group_staged_message(
        connection, group_number=group, message_id=event.message_id
    )
    _mark_dirty_group(
        connection,
        group,
        event.available_at_utc,
        str(current["event_time_utc"]),
    )
    return True


def stage_capture_event(connection: sqlite3.Connection, event: CaptureEvent) -> CaptureStageReport:
    """Stage one validated event idempotently; caller commits the transaction."""

    _validate_explicit_backfill_contract(event)
    seen_event = connection.execute(
        "SELECT 1 FROM capture_seen_events WHERE event_id=?", (event.event_id,)
    ).fetchone()
    existing_explicit_lineage = (
        connection.execute(
            "SELECT 1 FROM capture_explicit_backfill_lineage WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if event.is_explicit_backfill
        and event.source_id in _PROMOTION_BACKFILL_SOURCE_CODES
        else None
    )
    if seen_event is not None and (
        not event.is_explicit_backfill or existing_explicit_lineage is not None
    ):
        return CaptureStageReport(False, True, False, False)
    non_model = event.content_type in _NON_MODEL_CONTENT_TYPES
    if event.event_type == "message_deleted":
        changed = _apply_tombstone(connection, event)
        tombstone = changed
    elif non_model:
        changed = _apply_non_model_message(connection, event)
        tombstone = False
    elif event.stream == "market":
        changed = _apply_market_message(connection, event)
        tombstone = False
    else:
        changed = _apply_group_message(connection, event)
        tombstone = False
    explicit_current_reparse = False
    if (
        event.is_explicit_backfill
        and event.event_type != "message_deleted"
        and not changed
        and not (event.stream == "coin" and event.is_forwarded)
    ):
        if event.stream == "market":
            current = connection.execute(
                """
                SELECT event_time_utc FROM capture_market_messages
                WHERE source_id=? AND message_id=?
                """,
                (event.source_id, event.message_id),
            ).fetchone()
            if current is not None:
                _mark_dirty_market(
                    connection,
                    source_id=event.source_id,
                    message_id=event.message_id,
                    event_time_utc=str(current["event_time_utc"]),
                    available_at_utc=event.available_at_utc,
                )
                explicit_current_reparse = True
        else:
            current = connection.execute(
                """
                SELECT event_time_utc FROM coin_group_staged_messages
                WHERE group_number=? AND message_id=?
                """,
                (_GROUP_NUMBERS[event.source_id], event.message_id),
            ).fetchone()
            if current is not None:
                _mark_dirty_group(
                    connection,
                    _GROUP_NUMBERS[event.source_id],
                    event.available_at_utc,
                    str(current["event_time_utc"]),
                )
                explicit_current_reparse = True
    if seen_event is None:
        connection.execute(
            "INSERT INTO capture_seen_events(event_id,stream,available_at_utc,expires_at_utc) VALUES(?,?,?,?)",
            (
                event.event_id,
                event.stream,
                event.available_at_utc,
                _expiry(event.available_at_utc),
            ),
        )
    if event.event_type == "message_deleted":
        status = "FILTERED"
        disposition = "DELETE_APPLIED" if changed else "DELETE_SUPPRESSED"
        terminal_at = event.available_at_utc
    elif non_model:
        status = "FILTERED"
        disposition = f"NON_MODEL_{event.content_type.upper()}"
        terminal_at = event.available_at_utc
    elif changed or explicit_current_reparse:
        status = "PENDING"
        disposition = (
            "AWAITING_CURRENT_REPARSE"
            if explicit_current_reparse
            else "AWAITING_PARSER"
        )
        terminal_at = None
    else:
        status = "FILTERED"
        disposition = (
            "FORWARDED_UNSUPPORTED"
            if event.stream == "coin" and event.is_forwarded
            else "CURRENT_REVISION_UNCHANGED"
        )
        terminal_at = event.available_at_utc
    origin = (
        "explicit_backfill"
        if event.is_explicit_backfill
        else "reconcile"
        if event.is_backfill
        else "live"
    )
    # An explicit replay can deliberately revisit an event which was already
    # accepted by the routine stream.  The generic row describes that first
    # durable event; the separate explicit-backfill ledger describes the
    # replay.  Do not duplicate or rewrite the generic identity here.
    connection.execute(
        """
        INSERT OR IGNORE INTO capture_event_lineage(
          event_id,stream,source_id,message_id,event_type,origin,
          event_time_utc,available_at_utc,status,disposition_code,
          terminal_at_utc,expires_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event.event_id,
            event.stream,
            event.source_id,
            event.message_id,
            event.event_type,
            origin,
            event.event_time_utc,
            event.available_at_utc,
            status,
            disposition,
            terminal_at,
            _expiry(event.available_at_utc),
        ),
    )
    if (
        event.is_explicit_backfill
        and event.source_id in _PROMOTION_BACKFILL_SOURCE_CODES
    ):
        if status not in _EXPLICIT_BACKFILL_STATUSES:  # pragma: no cover - invariant
            raise CaptureEventContractError("capture_explicit_backfill_status_invalid")
        connection.execute(
            """
            INSERT INTO capture_explicit_backfill_lineage(
              event_id,stream,source_id,message_id,event_type,event_time_utc,
              available_at_utc,status,disposition_code,terminal_at_utc,
              expires_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event.event_id,
                event.stream,
                event.source_id,
                event.message_id,
                event.event_type,
                event.event_time_utc,
                event.available_at_utc,
                status,
                disposition,
                terminal_at,
                _expiry(event.available_at_utc),
            ),
        )
    return CaptureStageReport(
        seen_event is None,
        seen_event is not None,
        changed,
        tombstone,
    )


def record_capture_rejection(
    connection: sqlite3.Connection,
    *,
    stream: str,
    record_bytes: bytes,
    reason: str,
    seen_at_utc: str,
) -> None:
    """Persist only a digest and bounded reason for an invalid raw record."""

    digest = sha256(record_bytes).hexdigest()
    safe_reason = re.sub(r"[^A-Za-z0-9_:+.-]", "_", str(reason))[:160] or "invalid_record"
    connection.execute(
        """
        INSERT INTO capture_rejected_records(
          record_sha256,stream,reason,first_seen_at_utc,last_seen_at_utc,occurrences,expires_at_utc
        ) VALUES(?,?,?,?,?,1,?)
        ON CONFLICT(record_sha256) DO UPDATE SET
          last_seen_at_utc=excluded.last_seen_at_utc,
          occurrences=capture_rejected_records.occurrences+1,
          expires_at_utc=excluded.expires_at_utc
        """,
        (digest, stream, safe_reason, seen_at_utc, seen_at_utc, _expiry(seen_at_utc)),
    )


def _retract_fact(connection: sqlite3.Connection, event_key: bytes, available: str) -> int:
    row = connection.execute(
        "SELECT quality_state,attributes_json,event_time_utc,inserted_at_utc "
        "FROM market_observations WHERE event_key=?",
        (event_key,),
    ).fetchone()
    if row is None:
        return 0
    try:
        attributes = json.loads(str(row["attributes_json"] or "{}"))
    except (TypeError, ValueError):
        attributes = {}
    if str(row["quality_state"]) == "REJECTED" and attributes.get("resolution_reason") == _RETRACTION_REASON:
        return 0
    attributes["resolution_reason"] = _RETRACTION_REASON
    attributes["reconciled_by"] = CAPTURE_ADAPTER_VERSION
    safe_available = max(str(row["event_time_utc"]), available)
    export_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='market_fact_export_ledger'"
    ).fetchone()
    export = (
        connection.execute(
            """
            SELECT status,reason_code FROM market_fact_export_ledger
            WHERE event_key=?
            """,
            (event_key,),
        ).fetchone()
        if export_table is not None
        else None
    )
    never_exported_dependency = (
        export is not None
        and str(export["status"]) == "REJECTED"
        and "market_fact_projection_offer_dependency_missing"
        in str(export["reason_code"] or "")
    )
    inserted_at = (
        str(row["inserted_at_utc"])
        if never_exported_dependency
        else _utc_now()
    )
    connection.execute(
        """
        UPDATE market_observations
        SET available_at_utc=?,parse_confidence=0,quality_state='REJECTED',
            quality_policy_version='capture-current-revision-v1',attributes_json=?,
            inserted_at_utc=?
        WHERE event_key=?
        """,
        (
            safe_available,
            json.dumps(attributes, sort_keys=True, separators=(",", ":")),
            inserted_at,
            event_key,
        ),
    )
    return 1


def projection_reconciliation_pending(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1 FROM capture_projection_reconciliations
        WHERE reconciliation_code=? AND completed_at_utc IS NULL
        """,
        (_V8_RECONCILIATION_CODE,),
    ).fetchone()
    return row is not None


def _projection_rows(connection: sqlite3.Connection, source_id: str, message_id: int) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT event_key,bucket_utc FROM capture_projection_keys WHERE source_id=? AND message_id=?",
        (source_id, message_id),
    ).fetchall()


def _clear_projection(
    staging: sqlite3.Connection,
    market: sqlite3.Connection,
    *,
    source_id: str,
    message_id: int,
    available_at_utc: str,
) -> int:
    rows = _projection_rows(staging, source_id, message_id)
    count = sum(_retract_fact(market, bytes(row["event_key"]), available_at_utc) for row in rows)
    staging.execute(
        "DELETE FROM capture_projection_keys WHERE source_id=? AND message_id=?",
        (source_id, message_id),
    )
    return count


def _remember_projection(
    staging: sqlite3.Connection,
    *,
    source_id: str,
    message_id: int,
    event_keys: tuple[bytes, ...],
    bucket_utc: str | None = None,
) -> None:
    staging.executemany(
        "INSERT OR IGNORE INTO capture_projection_keys(source_id,message_id,event_key,bucket_utc) VALUES(?,?,?,?)",
        [(source_id, message_id, key, bucket_utc) for key in event_keys],
    )


def _finish_capture_lineage(
    connection: sqlite3.Connection,
    *,
    stream: str,
    source_id: str,
    message_id: int,
    status: str,
    disposition_code: str,
    completed_at_utc: str,
    parse_all_pending: bool = False,
) -> int:
    if status not in {"PARSED", "FILTERED"}:
        raise CaptureEventContractError("capture_lineage_terminal_invalid")
    changed = 0
    for table in ("capture_event_lineage", "capture_explicit_backfill_lineage"):
        rows = connection.execute(
            f"SELECT rowid AS lineage_rowid,event_id FROM {table} "
            "WHERE stream=? AND source_id=? "
            "AND message_id=? AND status='PENDING' "
            "ORDER BY lineage_rowid",
            (stream, source_id, int(message_id)),
        ).fetchall()
        if not rows:
            continue
        current_event_id = str(rows[-1]["event_id"])
        if status == "PARSED" and not parse_all_pending and len(rows) > 1:
            superseded = connection.execute(
                f"UPDATE {table} SET status='FILTERED',"
                "disposition_code='SUPERSEDED_BY_NEWER_REVISION',"
                "terminal_at_utc=? WHERE stream=? AND source_id=? "
                "AND message_id=? AND status='PENDING' AND event_id<>?",
                (
                    completed_at_utc,
                    stream,
                    source_id,
                    int(message_id),
                    current_event_id,
                ),
            )
            changed += max(0, int(superseded.rowcount or 0))
        target = connection.execute(
            f"UPDATE {table} SET status=?,disposition_code=?,terminal_at_utc=? "
            "WHERE stream=? AND source_id=? AND message_id=? AND status='PENDING'"
            + ("" if status != "PARSED" or parse_all_pending else " AND event_id=?"),
            (
                status,
                disposition_code,
                completed_at_utc,
                stream,
                source_id,
                int(message_id),
                *(
                    (current_event_id,)
                    if status == "PARSED" and not parse_all_pending
                    else ()
                ),
            ),
        )
        changed += max(0, int(target.rowcount or 0))
    return changed


def _public_keys(row: sqlite3.Row) -> tuple[bytes, ...]:
    code = str(row["source_id"])
    if bool(row["is_forwarded"]) or should_ignore_public_message(code, str(row["message_text"]), is_forwarded=bool(row["is_forwarded"])):
        return ()
    parsed = parse_public_message(code, str(row["message_text"]))
    compact = source_for_code(code).compact_latest_per_minute
    return tuple(
        derive_event_key(
            "public-telegram-compact-v1" if compact else "public-telegram-message-v1",
            code,
            str(row["event_time_utc"])[:16] if compact else int(row["message_id"]),
            index,
        )
        for index, _ in enumerate(parsed)
    )


def _project_public_row(
    staging: sqlite3.Connection,
    market: sqlite3.Connection,
    row: sqlite3.Row,
) -> tuple[int, tuple[bytes, ...]]:
    source_id = str(row["source_id"])
    result = ingest_public_message(
        market,
        source_code=source_id,
        message=PublicTelegramMessage(
            message_id=int(row["message_id"]),
            published_at_utc=str(row["event_time_utc"]),
            available_at_utc=str(row["available_at_utc"]),
            text=str(row["message_text"]),
            is_forwarded=bool(row["is_forwarded"]),
        ),
        # Durable replay reconciles all affected MELTED_FLOW facts once per
        # causal batch below.  The direct/public ingest API keeps its normal
        # per-message behavior for live callers.
        link_melted_flow_trades=source_id != "MELTED_FLOW",
    )
    keys = _public_keys(row)
    _remember_projection(
        staging,
        source_id=str(row["source_id"]),
        message_id=int(row["message_id"]),
        event_keys=keys,
        bucket_utc=(str(row["event_time_utc"])[:16] if str(row["source_id"]) == "XAUUSD" else None),
    )
    return result.event_count, (keys if source_id == "MELTED_FLOW" else ())


def _primary_source_event_id(message_id: int) -> str:
    return f"market-capture-v1:{_PRIMARY_SOURCE_CODE}:{int(message_id)}"


def _record_primary_outcome(
    staging: sqlite3.Connection,
    *,
    message_id: int,
    decision: PrivateGoldTradeDecision,
    as_of_utc: str,
) -> None:
    """Persist lifecycle state separately from the immutable offer fact."""

    staging.execute(
        """
        INSERT INTO capture_primary_trade_outcomes(
          source_id,message_id,status,reason,finalize_after_utc,
          finalized_at_utc,offered_quantity,executed_quantity,
          remaining_quantity,evidence_event_id,updated_at_utc,expires_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id,message_id) DO UPDATE SET
          status=excluded.status,
          reason=excluded.reason,
          finalize_after_utc=excluded.finalize_after_utc,
          finalized_at_utc=excluded.finalized_at_utc,
          offered_quantity=excluded.offered_quantity,
          executed_quantity=excluded.executed_quantity,
          remaining_quantity=excluded.remaining_quantity,
          evidence_event_id=excluded.evidence_event_id,
          updated_at_utc=excluded.updated_at_utc,
          expires_at_utc=excluded.expires_at_utc
        """,
        (
            _PRIMARY_SOURCE_CODE,
            int(message_id),
            decision.status,
            decision.reason,
            decision.finalize_after_utc,
            as_of_utc if decision.finalized else None,
            decision.offer_quantity,
            decision.traded_quantity,
            decision.remaining_quantity,
            decision.evidence_event_id,
            as_of_utc,
            _expiry(as_of_utc),
        ),
    )


def _project_primary_row(
    staging: sqlite3.Connection,
    market: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    as_of_utc: str,
) -> tuple[int, int, set[str], bool, bool]:
    if bool(row["is_forwarded"]):
        return 0, 0, set(), True, False
    revision_rows = staging.execute(
        """
        SELECT * FROM capture_market_message_revisions
        WHERE source_id=? AND message_id=? AND available_at_utc<=?
        ORDER BY COALESCE(edited_at_utc,event_time_utc),available_at_utc,event_id
        """,
        (_PRIMARY_SOURCE_CODE, int(row["message_id"]), as_of_utc),
    ).fetchall()
    if not revision_rows:
        raise CaptureEventContractError("private_gold_revision_history_missing")
    baseline = revision_rows[0]
    # Offered economics are immutable.  Edits may only contribute lifecycle
    # evidence; they must never rewrite the original price or quantity.
    observations = private_gold_observations(
        PrivateGoldOfferInput(
            source_event_id=_primary_source_event_id(int(row["message_id"])),
            published_at_utc=str(baseline["event_time_utc"]),
            available_at_utc=str(baseline["available_at_utc"]),
            trade_status="NONE",
            text=str(baseline["message_text"]),
        )
    )
    offer_rows = tuple(item for item in observations if item.event_type == "OFFER")
    for item in offer_rows:
        upsert_observation(market, item)
    decision = extract_private_gold_trade(
        (
            PrivateGoldRevision(
                event_id=str(item["event_id"]),
                event_type=str(item["event_type"]),
                published_at_utc=str(item["event_time_utc"]),
                available_at_utc=str(item["available_at_utc"]),
                edited_at_utc=(
                    str(item["edited_at_utc"])
                    if item["edited_at_utc"] is not None
                    else None
                ),
                text=str(item["message_text"]),
            )
            for item in revision_rows
        ),
        as_of_utc=as_of_utc,
    )
    _record_primary_outcome(
        staging,
        message_id=int(row["message_id"]),
        decision=decision,
        as_of_utc=as_of_utc,
    )
    trade_rows = ()
    if (
        decision.finalized
        and decision.status in {"FULL", "PARTIAL"}
        and decision.evidence_event_id is not None
        and decision.event_time_utc is not None
        and decision.available_at_utc is not None
        and decision.traded_quantity is not None
    ):
        evidence = next(
            (
                item
                for item in revision_rows
                if str(item["event_id"]) == decision.evidence_event_id
            ),
            None,
        )
        if evidence is not None:
            candidates = private_gold_observations(
                PrivateGoldOfferInput(
                    # The lifecycle outcome must retain the immutable root
                    # offer identity.  The evidence revision remains in the
                    # trade attributes; using it as the source identity would
                    # derive an unrelated root_offer_event_key and make the
                    # archive correctly reject the orphaned outcome.
                    source_event_id=_primary_source_event_id(int(row["message_id"])),
                    published_at_utc=decision.published_at_utc,
                    available_at_utc=decision.available_at_utc,
                    edited_at_utc=decision.event_time_utc,
                    trade_status=decision.status,
                    traded_quantity=decision.traded_quantity,
                    text=str(evidence["message_text"]),
                )
            )
            trade_rows = tuple(
                replace(
                    item,
                    parser_version=PRIVATE_GOLD_TRADE_REVISION_VERSION,
                    quality_policy_version="private-gold-trade-revision-v1",
                    attributes={
                        **item.attributes,
                        "trade_evidence": decision.reason,
                        "trade_extractor_version": PRIVATE_GOLD_TRADE_REVISION_VERSION,
                        "remaining_quantity": decision.remaining_quantity,
                        "offer_quantity": decision.offer_quantity,
                    },
                )
                for item in candidates
                if item.event_type == "TRADE"
            )
            for item in trade_rows:
                upsert_observation(market, item)
    projected_rows = (*offer_rows, *trade_rows)
    _remember_projection(
        staging,
        source_id=_PRIMARY_SOURCE_CODE,
        message_id=int(row["message_id"]),
        event_keys=tuple(item.event_key for item in projected_rows),
        bucket_utc=str(row["event_time_utc"])[:16],
    )
    affected_minutes = {
        normalize_utc(item.event_time_utc, field_name="capture_primary_projection_time")[:16]
        for item in projected_rows
    }
    return (
        len(projected_rows),
        len(trade_rows),
        affected_minutes,
        decision.finalized,
        decision.status == "AMBIGUOUS",
    )


def _refresh_private_minutes(
    market: sqlite3.Connection,
    *,
    affected_minutes: set[str],
    as_of_utc: str,
) -> int:
    if not affected_minutes:
        return 0
    # A source revision can only change its own event-time minute.  Bounding
    # the rebuild this way keeps a live 10-15 second cycle inexpensive even
    # when the three-day raw window contains hundreds of thousands of offers.
    placeholders = ",".join("?" for _ in affected_minutes)
    minute_values = tuple(sorted(affected_minutes))
    rows = market.execute(
        f"""
        SELECT DISTINCT settlement_term,trade_form,substr(event_time_utc,1,16)||':00Z' AS minute_utc
        FROM market_observations
        WHERE source_code='PRIVATE_GOLD_CHANNEL'
          AND instrument='MELTED_GOLD_PRIVATE'
          AND trade_form IN ('PAPER_NORMAL','PAPER_REVERSE','PAPER_SWIM')
          AND event_type IN ('OFFER','TRADE') AND quality_state='ELIGIBLE'
          AND is_conditional=0 AND available_at_utc<=?
          AND substr(event_time_utc,1,16) IN ({placeholders})
        ORDER BY minute_utc
        """,
        (as_of_utc, *minute_values),
    ).fetchall()
    # Existing derived rows are current-state materializations.  Reject only
    # affected minutes before recreating those still supported by active facts.
    for row in market.execute(
        f"""
        SELECT event_key FROM market_observations
        WHERE source_code=? AND quality_state='ELIGIBLE'
          AND substr(event_time_utc,1,16) IN ({placeholders})
        """,
        (PRIVATE_GOLD_MINUTE_SOURCE_CODE, *minute_values),
    ).fetchall():
        _retract_fact(market, bytes(row["event_key"]), as_of_utc)
    as_of = datetime.fromisoformat(as_of_utc.replace("Z", "+00:00"))
    requests: list[tuple[str, str, str]] = []
    for row in rows:
        minute = str(row["minute_utc"])
        if datetime.fromisoformat(minute.replace("Z", "+00:00")).replace(second=59) > as_of:
            continue
        requests.append(
            (
                str(row["settlement_term"]),
                str(row["trade_form"]).removeprefix("PAPER_"),
                minute,
            )
        )
    return len(
        refresh_private_gold_paper_minutes(
            market,
            minute_books=requests,
            available_at_utc=as_of_utc,
        )
    )


def _bounded_group_reply_graph(
    staging: sqlite3.Connection,
    *,
    as_of_utc: str,
) -> tuple[frozenset[tuple[int, int]], str]:
    """Keep the active six-hour group window plus every required ancestor."""

    as_of = datetime.fromisoformat(as_of_utc.replace("Z", "+00:00"))
    cutoff = (as_of - COIN_GROUP_ACTIVE_REPLAY_WINDOW).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    ancestor_cutoff = (
        as_of
        - COIN_GROUP_ACTIVE_REPLAY_WINDOW
        - timedelta(seconds=MAX_REPLY_AGE_SECONDS)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows = staging.execute(
        """
        SELECT group_number,message_id,event_time_utc,reply_to_message_id
        FROM coin_group_staged_messages
        WHERE available_at_utc<=? AND expires_at_utc>?
        """,
        (as_of_utc, as_of_utc),
    ).fetchall()
    parents = {
        (int(row["group_number"]), int(row["message_id"])): (
            (int(row["group_number"]), int(row["reply_to_message_id"]))
            if row["reply_to_message_id"] is not None
            else None
        )
        for row in rows
    }
    event_times = {
        (int(row["group_number"]), int(row["message_id"])): str(
            row["event_time_utc"]
        )
        for row in rows
    }
    included = {
        (int(row["group_number"]), int(row["message_id"]))
        for row in rows
        if str(row["event_time_utc"]) >= cutoff
    }
    frontier = list(included)
    while frontier:
        parent = parents.get(frontier.pop())
        if (
            parent is None
            or parent in included
            or parent not in parents
            or event_times[parent] < ancestor_cutoff
        ):
            continue
        included.add(parent)
        frontier.append(parent)
    return frozenset(included), cutoff


def _missing_offer_reply_graph(
    staging: sqlite3.Connection,
    market: sqlite3.Connection,
    *,
    as_of_utc: str,
) -> frozenset[tuple[int, int]]:
    """Find only newly parseable roots and their captured reply descendants."""

    rows = staging.execute(
        """
        SELECT group_number,message_id,event_time_utc,available_at_utc,
               reply_to_message_id,message_text
        FROM coin_group_staged_messages
        WHERE available_at_utc<=? AND expires_at_utc>?
        ORDER BY event_time_utc,group_number,message_id
        """,
        (as_of_utc, as_of_utc),
    ).fetchall()
    by_key = {
        (int(row["group_number"]), int(row["message_id"])): row for row in rows
    }
    included: set[tuple[int, int]] = set()
    for key, row in by_key.items():
        try:
            parsed = parse_coin_group_offers(
                CoinGroupMessageInput(
                    group_number=key[0],
                    source_event_id=key[1],
                    published_at_utc=str(row["event_time_utc"]),
                    available_at_utc=str(row["available_at_utc"]),
                    text=str(row["message_text"]),
                )
            )
        except (TypeError, ValueError):
            continue
        missing = any(
            market.execute(
                "SELECT 1 FROM market_observations WHERE event_key=?",
                (derive_event_key("coin-group-offer-v1", key[0], key[1], index),),
            ).fetchone()
            is None
            for index in range(len(parsed))
        )
        if missing:
            included.add(key)
    if not included:
        return frozenset()
    changed = True
    while changed:
        changed = False
        for key, row in by_key.items():
            parent = (
                (key[0], int(row["reply_to_message_id"]))
                if row["reply_to_message_id"] is not None
                else None
            )
            if key not in included and parent in included:
                included.add(key)
                changed = True
    return frozenset(included)


def project_capture_changes(
    staging: sqlite3.Connection,
    market: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
    group_additional_anchors: Iterable[CoinPriceAnchor] = (),
    group_parser_feedback: Mapping[bytes, CoinGroupParserFeedback] | None = None,
    max_market_messages: int | None = None,
) -> CaptureProjectionReport:
    """Project every dirty current revision; callers commit both databases."""

    as_of = normalize_utc(as_of_utc, field_name="capture_projection_as_of_utc")
    if max_market_messages is not None and max_market_messages < 1:
        raise CaptureEventContractError("capture_market_projection_limit_invalid")
    initialize_market_store(market)
    projection_reconciliation = projection_reconciliation_pending(staging)
    due_primary = staging.execute(
        """
        SELECT deadline.source_id,deadline.message_id,
               deadline.finalize_after_utc,current.event_time_utc
        FROM capture_primary_trade_deadlines AS deadline
        LEFT JOIN capture_market_messages AS current
          ON current.source_id=deadline.source_id
         AND current.message_id=deadline.message_id
        WHERE deadline.finalized_at_utc IS NULL
          AND deadline.finalize_after_utc<=?
        ORDER BY deadline.finalize_after_utc,deadline.message_id
        """,
        (as_of,),
    ).fetchall()
    for deadline in due_primary:
        _mark_dirty_market(
            staging,
            source_id=str(deadline["source_id"]),
            message_id=int(deadline["message_id"]),
            event_time_utc=(
                str(deadline["event_time_utc"])
                if deadline["event_time_utc"] is not None
                else None
            ),
            available_at_utc=as_of,
        )
    dirty_query = (
        "SELECT * FROM capture_dirty_market_messages "
        "WHERE available_at_utc<=? "
        "ORDER BY available_at_utc,source_id,message_id"
    )
    dirty_parameters: tuple[object, ...] = (as_of,)
    if max_market_messages is not None:
        dirty_query += " LIMIT ?"
        dirty_parameters = (as_of, max_market_messages)
    dirty = staging.execute(dirty_query, dirty_parameters).fetchall()
    projected = upserted = retracted = 0
    private_trades = private_finalized = private_ambiguous = 0
    primary_changed = False
    primary_minutes: set[str] = set()
    changed_melted_flow_keys: list[bytes] = []
    for item in dirty:
        source_id = str(item["source_id"])
        retracted += _clear_projection(
            staging,
            market,
            source_id=source_id,
            message_id=int(item["message_id"]),
            available_at_utc=str(item["available_at_utc"]),
        )
        row = staging.execute(
            "SELECT * FROM capture_market_messages WHERE source_id=? AND message_id=? AND available_at_utc<=?",
            (source_id, int(item["message_id"]), as_of),
        ).fetchone()
        if row is None:
            primary_changed = primary_changed or source_id == _PRIMARY_SOURCE_CODE
            if source_id == _PRIMARY_SOURCE_CODE and item["event_time_utc"]:
                primary_minutes.add(str(item["event_time_utc"])[:16])
            if source_id == _PRIMARY_SOURCE_CODE:
                staging.execute(
                    """
                    UPDATE capture_primary_trade_deadlines
                    SET finalized_at_utc=COALESCE(finalized_at_utc,?)
                    WHERE source_id=? AND message_id=?
                    """,
                    (as_of, source_id, int(item["message_id"])),
                )
            _finish_capture_lineage(
                staging,
                stream="market",
                source_id=source_id,
                message_id=int(item["message_id"]),
                status="FILTERED",
                disposition_code="CURRENT_ROW_UNAVAILABLE_AT_PARSE",
                completed_at_utc=as_of,
            )
            continue
        projected += 1
        if source_id == _PRIMARY_SOURCE_CODE:
            primary_changed = True
            if item["event_time_utc"]:
                primary_minutes.add(str(item["event_time_utc"])[:16])
            try:
                counts = _project_primary_row(
                    staging,
                    market,
                    row,
                    as_of_utc=as_of,
                )
            except MarketStoreContractError as exc:
                # Capture text is untrusted input.  A value outside the
                # canonical magnitude policy is an input rejection, not a
                # process-wide dependency failure.  Terminalize only this
                # narrow policy error; every other store-contract violation
                # remains fail-closed because it can indicate a code defect.
                if not str(exc).startswith("price_out_of_canonical_range:"):
                    raise
                _finish_capture_lineage(
                    staging,
                    stream="market",
                    source_id=source_id,
                    message_id=int(item["message_id"]),
                    status="FILTERED",
                    disposition_code=_PRIMARY_PRICE_POLICY_REJECTION,
                    completed_at_utc=as_of,
                )
                staging.execute(
                    """
                    UPDATE capture_primary_trade_deadlines
                    SET finalized_at_utc=?
                    WHERE source_id=? AND message_id=?
                    """,
                    (as_of, source_id, int(item["message_id"])),
                )
                continue
            upserted += counts[0]
            private_trades += counts[1]
            primary_minutes.update(counts[2])
            private_finalized += int(counts[3])
            private_ambiguous += int(counts[4])
            if counts[3]:
                staging.execute(
                    """
                    UPDATE capture_primary_trade_deadlines
                    SET finalized_at_utc=?
                    WHERE source_id=? AND message_id=?
                    """,
                    (as_of, source_id, int(item["message_id"])),
                )
        else:
            public_upserted, flow_keys = _project_public_row(staging, market, row)
            upserted += public_upserted
            changed_melted_flow_keys.extend(flow_keys)
        _finish_capture_lineage(
            staging,
            stream="market",
            source_id=source_id,
            message_id=int(item["message_id"]),
            status="PARSED",
            disposition_code=(
                "FORWARDED_FILTERED"
                if bool(row["is_forwarded"])
                else "PARSER_EXECUTED"
            ),
            completed_at_utc=as_of,
            parse_all_pending=source_id == _PRIMARY_SOURCE_CODE,
        )
    if changed_melted_flow_keys:
        link_melted_flow_trade_sides(
            market,
            changed_event_keys=tuple(changed_melted_flow_keys),
        )
    if dirty:
        staging.executemany(
            "DELETE FROM capture_dirty_market_messages WHERE source_id=? AND message_id=?",
            [(str(row["source_id"]), int(row["message_id"])) for row in dirty],
        )
    refreshed = (
        _refresh_private_minutes(
            market,
            affected_minutes=primary_minutes,
            as_of_utc=as_of,
        )
        if primary_changed
        else 0
    )
    dirty_groups = staging.execute(
        "SELECT group_number,minimum_event_time_utc FROM capture_dirty_groups WHERE available_at_utc<=?",
        (as_of,),
    ).fetchall()
    explicit_group_rows = staging.execute(
        """
        SELECT source_id,message_id
        FROM capture_explicit_backfill_lineage
        WHERE stream='coin' AND status='PENDING' AND available_at_utc<=?
        ORDER BY source_id,message_id
        """,
        (as_of,),
    ).fetchall()
    explicit_group_keys = frozenset(
        (_GROUP_NUMBERS[str(row["source_id"])], int(row["message_id"]))
        for row in explicit_group_rows
    )
    generic_group_rows = staging.execute(
        """
        SELECT source_id,message_id
        FROM capture_event_lineage
        WHERE stream='coin' AND status='PENDING' AND available_at_utc<=?
        ORDER BY source_id,message_id
        """,
        (as_of,),
    ).fetchall()
    generic_group_keys = frozenset(
        (_GROUP_NUMBERS[str(row["source_id"])], int(row["message_id"]))
        for row in generic_group_rows
    )
    pending_group_keys = explicit_group_keys | generic_group_keys
    current_pending_group_keys = frozenset(
        (int(row["group_number"]), int(row["message_id"]))
        for row in staging.execute(
            """
            SELECT group_number,message_id
            FROM coin_group_staged_messages
            WHERE available_at_utc<=? AND expires_at_utc>?
            """,
            (as_of, as_of),
        ).fetchall()
        if (int(row["group_number"]), int(row["message_id"]))
        in pending_group_keys
    )
    current_explicit_group_keys = current_pending_group_keys & explicit_group_keys
    group_report = None
    if dirty_groups or projection_reconciliation or pending_group_keys:
        repair_group_keys = (
            _missing_offer_reply_graph(staging, market, as_of_utc=as_of)
            if projection_reconciliation
            else frozenset()
        )
        if dirty_groups:
            included_group_keys, group_cutoff = _bounded_group_reply_graph(
                staging,
                as_of_utc=as_of,
            )
        else:
            included_group_keys = frozenset()
            group_cutoff = as_of
        included_group_keys = (
            included_group_keys
            | repair_group_keys
            | current_explicit_group_keys
        )
        dirty_horizon = min(
            (
                str(row["minimum_event_time_utc"])
                for row in dirty_groups
                if row["minimum_event_time_utc"] is not None
            ),
            default=group_cutoff,
        )
        if included_group_keys:
            group_report = process_coin_group_staging(
                staging,
                market,
                as_of_utc=as_of,
                additional_anchors=group_additional_anchors,
                parser_feedback=group_parser_feedback,
                reconciliation_horizon_utc=max(group_cutoff, dirty_horizon),
                included_message_keys=included_group_keys,
                reconcile_missing_current_facts=bool(dirty_groups),
            )
        parsed_pending_group_keys = pending_group_keys & included_group_keys
        for group, message_id in parsed_pending_group_keys:
            _finish_capture_lineage(
                staging,
                stream="coin",
                source_id=f"GROUP_{group}",
                message_id=message_id,
                status="PARSED",
                disposition_code="PARSER_EXECUTED",
                completed_at_utc=as_of,
            )
        unavailable_pending_group_keys = pending_group_keys - parsed_pending_group_keys
        for group, message_id in unavailable_pending_group_keys:
            is_current = (group, message_id) in current_pending_group_keys
            _finish_capture_lineage(
                staging,
                stream="coin",
                source_id=f"GROUP_{group}",
                message_id=message_id,
                status="FILTERED",
                disposition_code=(
                    "OUTSIDE_ACTIVE_REPLAY_WINDOW"
                    if is_current
                    else "CURRENT_ROW_UNAVAILABLE_AT_PARSE"
                ),
                completed_at_utc=as_of,
            )
        staging.executemany(
            "DELETE FROM capture_dirty_groups WHERE group_number=?",
            [(int(row["group_number"]),) for row in dirty_groups],
        )
        if projection_reconciliation:
            staging.execute(
                """
                UPDATE capture_projection_reconciliations
                SET completed_at_utc=?
                WHERE reconciliation_code=? AND completed_at_utc IS NULL
                """,
                (as_of, _V8_RECONCILIATION_CODE),
            )
    raw_purged = purge_capture_staging(staging, as_of_utc=as_of)
    return CaptureProjectionReport(
        market_messages_reprojected=projected,
        market_facts_upserted=upserted,
        market_facts_retracted=retracted + (group_report.retracted_facts if group_report else 0),
        private_paper_minutes_refreshed=refreshed,
        private_trade_facts_upserted=private_trades,
        private_trade_messages_finalized=private_finalized,
        private_trade_messages_ambiguous=private_ambiguous,
        group_pipeline=group_report,
        raw_rows_purged=raw_purged,
    )


def purge_capture_staging(connection: sqlite3.Connection, *, as_of_utc: datetime | str) -> int:
    as_of = normalize_utc(as_of_utc, field_name="capture_purge_as_of_utc")
    counts = 0
    # Projection keys contain transport message IDs and share the raw horizon.
    expired = connection.execute(
        "SELECT source_id,message_id FROM capture_market_messages WHERE expires_at_utc<=?",
        (as_of,),
    ).fetchall()
    if expired:
        connection.executemany(
            "DELETE FROM capture_projection_keys WHERE source_id=? AND message_id=?",
            [(str(row["source_id"]), int(row["message_id"])) for row in expired],
        )
    for table in (
        "capture_market_message_revisions",
        "capture_primary_trade_outcomes",
        "capture_primary_trade_deadlines",
        "capture_market_messages",
        "capture_seen_events",
        "capture_tombstones",
        "capture_rejected_records",
        "capture_explicit_backfill_lineage",
        "capture_event_lineage",
    ):
        result = connection.execute(f"DELETE FROM {table} WHERE expires_at_utc<=?", (as_of,))
        counts += max(0, int(result.rowcount or 0))
    counts += purge_expired_coin_group_staging(connection, as_of_utc=as_of)
    return counts


__all__ = [
    "CAPTURE_ADAPTER_SCHEMA_VERSION",
    "CAPTURE_ADAPTER_VERSION",
    "COIN_GROUP_ACTIVE_REPLAY_WINDOW",
    "MARKET_BACKFILL_REPLAY_WINDOW",
    "CaptureEvent",
    "CaptureEventContractError",
    "CaptureProjectionReport",
    "CaptureStageReport",
    "decode_capture_event",
    "decode_coin_group_event",
    "decode_market_channel_event",
    "initialize_capture_adapter",
    "project_capture_changes",
    "purge_capture_staging",
    "record_capture_rejection",
    "stage_capture_event",
]
