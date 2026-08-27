"""Durable single-owner capture engine for the private market pipeline.

The engine is deliberately parser-free.  It accepts only the existing
``market_channel_event/1.0`` and ``coin_group_event/2.0`` envelopes, stages an
SQLite FULL outbox, appends and fsyncs a shared daily JSONL stream, and only
then marks the event delivered internally.  A slow or unavailable parser can
therefore never apply backpressure to Telegram capture.

Stage 4 does not grant live authority.  The Telegram adapter and the Docker
entrypoint require a cutover marker on the session mount before live mode can
open a session.  Fixture mode exercises the same state/spool code without
network access or Telegram credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import copy
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable, Mapping, Sequence

from .capture_event_adapter import (
    CaptureEvent,
    CaptureEventContractError,
    decode_capture_event,
)


CAPTURE_ENGINE_SCHEMA = "market_capture_engine/1.0"
CAPTURE_ENGINE_VERSION = "private-market-capture-v1"
RAW_RETENTION = timedelta(days=3)
MAX_RECORD_BYTES = 256 * 1024

ROLE_ACCOUNT = {
    "market-capture-account1": "account1",
    "market-capture-account2": "account2",
}
ACCOUNT_STREAM = {"account1": "market", "account2": "coin"}
ACCOUNT_SOURCES = {
    "account1": frozenset(
        {
            "MELTED_PRIMARY_FLOW",
            "MELTED_AGGREGATE",
            "MELTED_FLOW",
            "USD_HERAT",
            "XAUUSD",
        }
    ),
    "account2": frozenset({"GROUP_1", "GROUP_2"}),
}


class CaptureRuntimeError(RuntimeError):
    """An operator-safe capture failure without raw payload detail."""


class CaptureSpoolCorruption(CaptureRuntimeError):
    """A non-tail spool record is corrupt and capture must stop."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None = None) -> str:
    moment = value or utc_now()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise CaptureRuntimeError("capture_timezone_required")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def parse_utc(value: object, *, field: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CaptureRuntimeError(f"{field}_invalid") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise CaptureRuntimeError(f"{field}_timezone_required")
    return moment.astimezone(timezone.utc)


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def semantic_payload_hash(value: Mapping[str, Any]) -> str:
    """Hash the logical Telegram event, excluding delivery/replay metadata.

    Telegram can redeliver the same update with a later receipt time and a
    reconciliation snapshot can represent the same create.  Those must retain
    the first authoritative availability instead of becoming conflicts.
    """

    normalized = copy.deepcopy(dict(value))
    producer = normalized.get("producer")
    if isinstance(producer, dict):
        for key in (
            "capture_sequence",
            "received_at_utc",
            "available_at_utc",
            "origin",
            "is_backfill",
            "source_clock_adjusted",
        ):
            producer.pop(key, None)
    message = normalized.get("message")
    if isinstance(message, dict):
        message.pop("is_backfill", None)
    if normalized.get("event_type") in {"message_created", "message_snapshot"}:
        normalized["event_type"] = "message_upsert"
    return sha256(canonical_json(normalized)).hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = canonical_json(document) + b"\n"
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("capture_atomic_short_write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def _safe_reason(value: object) -> str:
    normalized = "".join(
        character
        for character in str(value or "capture_record_invalid").upper()
        if character.isalnum() or character == "_"
    )
    return (normalized or "CAPTURE_RECORD_INVALID")[:96]


def _source_and_event(document: Mapping[str, Any], account: str) -> tuple[str, CaptureEvent]:
    stream = ACCOUNT_STREAM.get(account)
    if stream is None:
        raise CaptureRuntimeError("capture_account_invalid")
    try:
        event = decode_capture_event(document, stream=stream)
    except CaptureEventContractError as exc:
        raise CaptureRuntimeError(_safe_reason(exc)) from exc
    if event.source_id not in ACCOUNT_SOURCES[account]:
        raise CaptureRuntimeError("CAPTURE_SOURCE_NOT_ALLOWED_FOR_ACCOUNT")
    producer = document.get("producer")
    if not isinstance(producer, Mapping):
        raise CaptureRuntimeError("CAPTURE_PRODUCER_INVALID")
    sequence = producer.get("capture_sequence")
    if sequence is not None and sequence != 0 and sequence != "0":
        raise CaptureRuntimeError("CAPTURE_INGRESS_SEQUENCE_FORBIDDEN")
    return event.source_id, event


def validate_ingress(document: object, account: str) -> tuple[str, CaptureEvent]:
    if not isinstance(document, Mapping):
        raise CaptureRuntimeError("CAPTURE_EVENT_OBJECT_REQUIRED")
    encoded = canonical_json(document)
    if len(encoded) > MAX_RECORD_BYTES:
        raise CaptureRuntimeError("CAPTURE_EVENT_TOO_LARGE")
    return _source_and_event(document, account)


@dataclass(frozen=True, slots=True)
class StageResult:
    status: str
    event_id: str
    sequence: int
    source_code: str
    payload: dict[str, Any] | None


class CaptureState:
    """SQLite FULL outbox, dedupe state, watermarks, and redacted metrics."""

    def __init__(self, path: Path, *, account: str) -> None:
        if account not in ACCOUNT_STREAM:
            raise CaptureRuntimeError("capture_account_invalid")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path
        self.account = account
        self.connection = sqlite3.connect(path, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS capture_metadata(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              schema_version INTEGER NOT NULL,
              account TEXT NOT NULL,
              initialized_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capture_kv(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capture_outbox(
              event_id TEXT PRIMARY KEY,
              capture_sequence INTEGER NOT NULL UNIQUE CHECK(capture_sequence > 0),
              source_code TEXT NOT NULL,
              event_type TEXT NOT NULL,
              message_id INTEGER NOT NULL CHECK(message_id > 0),
              available_at_utc TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
              payload_json TEXT NOT NULL,
              enqueued_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capture_seen(
              event_id TEXT PRIMARY KEY,
              capture_sequence INTEGER NOT NULL UNIQUE CHECK(capture_sequence > 0),
              source_code TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
              available_at_utc TEXT NOT NULL,
              expires_at_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_capture_seen_expiry
              ON capture_seen(expires_at_utc);
            CREATE TABLE IF NOT EXISTS capture_messages(
              source_code TEXT NOT NULL,
              message_id INTEGER NOT NULL CHECK(message_id > 0),
              latest_event_id TEXT NOT NULL,
              latest_payload_sha256 TEXT NOT NULL CHECK(length(latest_payload_sha256)=64),
              published_at_utc TEXT,
              edited_at_utc TEXT,
              available_at_utc TEXT NOT NULL,
              deleted INTEGER NOT NULL CHECK(deleted IN (0,1)),
              expires_at_utc TEXT NOT NULL,
              PRIMARY KEY(source_code,message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_capture_messages_expiry
              ON capture_messages(expires_at_utc);
            CREATE TABLE IF NOT EXISTS capture_source_metrics(
              source_code TEXT PRIMARY KEY,
              created INTEGER NOT NULL DEFAULT 0,
              edited INTEGER NOT NULL DEFAULT 0,
              deleted INTEGER NOT NULL DEFAULT 0,
              duplicate INTEGER NOT NULL DEFAULT 0,
              quarantined INTEGER NOT NULL DEFAULT 0,
              gap_recovered INTEGER NOT NULL DEFAULT 0,
              last_update_at_utc TEXT,
              last_available_at_utc TEXT,
              last_message_id INTEGER,
              last_capture_sequence INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS capture_quarantine(
              record_sha256 TEXT NOT NULL,
              reason_code TEXT NOT NULL,
              source_code TEXT,
              byte_count INTEGER NOT NULL CHECK(byte_count >= 0),
              first_seen_at_utc TEXT NOT NULL,
              last_seen_at_utc TEXT NOT NULL,
              occurrences INTEGER NOT NULL CHECK(occurrences > 0),
              expires_at_utc TEXT NOT NULL,
              PRIMARY KEY(record_sha256,reason_code)
            );
            CREATE INDEX IF NOT EXISTS idx_capture_quarantine_expiry
              ON capture_quarantine(expires_at_utc);
            """
        )
        row = self.connection.execute(
            "SELECT schema_version,account FROM capture_metadata WHERE singleton=1"
        ).fetchone()
        if row is None:
            self.connection.execute(
                "INSERT INTO capture_metadata(singleton,schema_version,account,initialized_at_utc) "
                "VALUES(1,1,?,?)",
                (account, utc_text()),
            )
        elif int(row["schema_version"]) != 1 or str(row["account"]) != account:
            raise CaptureRuntimeError("capture_state_contract_mismatch")
        for source in sorted(ACCOUNT_SOURCES[account]):
            self.connection.execute(
                "INSERT OR IGNORE INTO capture_source_metrics(source_code) VALUES(?)",
                (source,),
            )
        self.connection.commit()
        os.chmod(path, 0o600)

    def close(self) -> None:
        self.connection.close()

    def integrity(self) -> str:
        return str(self.connection.execute("PRAGMA integrity_check").fetchone()[0])

    def sequence(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM capture_kv WHERE key='capture_sequence'"
        ).fetchone()
        return int(row[0]) if row else 0

    def ensure_sequence_floor(self, floor: int) -> None:
        current = self.sequence()
        if floor > current:
            self.connection.execute(
                "INSERT INTO capture_kv(key,value) VALUES('capture_sequence',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(int(floor)),),
            )
            self.connection.commit()

    def stage(self, document: Mapping[str, Any], *, now: datetime | None = None) -> StageResult:
        source_code, event = validate_ingress(document, self.account)
        now_value = now or utc_now()
        base_payload = json.loads(canonical_json(document))
        base_hash = semantic_payload_hash(base_payload)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            seen = self.connection.execute(
                "SELECT capture_sequence,payload_sha256 FROM capture_seen WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
            if seen is not None:
                if str(seen["payload_sha256"]) != base_hash:
                    raise CaptureRuntimeError("capture_event_identity_conflict")
                self._increment_metric(source_code, "duplicate", now_value)
                self.connection.commit()
                return StageResult(
                    "duplicate",
                    event.event_id,
                    int(seen["capture_sequence"]),
                    source_code,
                    None,
                )
            pending = self.connection.execute(
                "SELECT capture_sequence,payload_sha256,payload_json FROM capture_outbox "
                "WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
            if pending is not None:
                staged = json.loads(str(pending["payload_json"]))
                if semantic_payload_hash(staged) != base_hash:
                    raise CaptureRuntimeError("capture_pending_identity_conflict")
                self.connection.rollback()
                return StageResult(
                    "pending",
                    event.event_id,
                    int(pending["capture_sequence"]),
                    source_code,
                    staged,
                )
            sequence = self.sequence() + 1
            producer = base_payload.setdefault("producer", {})
            producer["capture_sequence"] = sequence
            payload_json = canonical_json(base_payload).decode("utf-8")
            self.connection.execute(
                "INSERT INTO capture_outbox(event_id,capture_sequence,source_code,event_type,"
                "message_id,available_at_utc,payload_sha256,payload_json,enqueued_at_utc) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    sequence,
                    source_code,
                    event.event_type,
                    event.message_id,
                    event.available_at_utc,
                    base_hash,
                    payload_json,
                    utc_text(now_value),
                ),
            )
            self.connection.execute(
                "INSERT INTO capture_kv(key,value) VALUES('capture_sequence',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(sequence),),
            )
            self.connection.commit()
            return StageResult(
                "staged", event.event_id, sequence, source_code, base_payload
            )
        except BaseException:
            self.connection.rollback()
            raise

    def pending(self) -> tuple[StageResult, ...]:
        rows = self.connection.execute(
            "SELECT event_id,capture_sequence,source_code,payload_json "
            "FROM capture_outbox ORDER BY capture_sequence"
        ).fetchall()
        return tuple(
            StageResult(
                "pending",
                str(row["event_id"]),
                int(row["capture_sequence"]),
                str(row["source_code"]),
                json.loads(str(row["payload_json"])),
            )
            for row in rows
        )

    def _increment_metric(
        self, source_code: str, field: str, now: datetime, amount: int = 1
    ) -> None:
        if field not in {
            "created",
            "edited",
            "deleted",
            "duplicate",
            "quarantined",
            "gap_recovered",
        }:
            raise CaptureRuntimeError("capture_metric_field_invalid")
        self.connection.execute(
            f"UPDATE capture_source_metrics SET {field}={field}+?,last_update_at_utc=? "
            "WHERE source_code=?",
            (int(amount), utc_text(now), source_code),
        )

    def complete(self, event_id: str, *, now: datetime | None = None) -> None:
        moment = now or utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM capture_outbox WHERE event_id=?", (event_id,)
            ).fetchone()
            if row is None:
                if self.connection.execute(
                    "SELECT 1 FROM capture_seen WHERE event_id=?", (event_id,)
                ).fetchone():
                    self.connection.rollback()
                    return
                raise CaptureRuntimeError("capture_outbox_missing_before_completion")
            document = json.loads(str(row["payload_json"]))
            message = document.get("message") or {}
            published = message.get("published_at_utc")
            edited = message.get("edited_at_utc")
            event_type = str(row["event_type"])
            metric = {
                "message_created": "created",
                "message_snapshot": "created",
                "message_edited": "edited",
                "message_deleted": "deleted",
            }.get(event_type)
            if metric is None:
                raise CaptureRuntimeError("capture_event_type_metric_unsupported")
            available = parse_utc(
                row["available_at_utc"], field="capture_available_at_utc"
            )
            expires = utc_text(available + RAW_RETENTION)
            self.connection.execute(
                "INSERT INTO capture_seen(event_id,capture_sequence,source_code,payload_sha256,"
                "available_at_utc,expires_at_utc) VALUES(?,?,?,?,?,?)",
                (
                    event_id,
                    int(row["capture_sequence"]),
                    str(row["source_code"]),
                    str(row["payload_sha256"]),
                    str(row["available_at_utc"]),
                    expires,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO capture_messages(
                  source_code,message_id,latest_event_id,latest_payload_sha256,
                  published_at_utc,edited_at_utc,available_at_utc,deleted,expires_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_code,message_id) DO UPDATE SET
                  latest_event_id=excluded.latest_event_id,
                  latest_payload_sha256=excluded.latest_payload_sha256,
                  published_at_utc=COALESCE(excluded.published_at_utc,capture_messages.published_at_utc),
                  edited_at_utc=excluded.edited_at_utc,
                  available_at_utc=MAX(excluded.available_at_utc,capture_messages.available_at_utc),
                  deleted=excluded.deleted,
                  expires_at_utc=MAX(excluded.expires_at_utc,capture_messages.expires_at_utc)
                """,
                (
                    str(row["source_code"]),
                    int(row["message_id"]),
                    event_id,
                    str(row["payload_sha256"]),
                    published,
                    edited,
                    str(row["available_at_utc"]),
                    int(event_type == "message_deleted"),
                    expires,
                ),
            )
            self._increment_metric(str(row["source_code"]), metric, moment)
            if bool((document.get("producer") or {}).get("is_backfill")) or bool(
                (document.get("message") or {}).get("is_backfill")
            ):
                self._increment_metric(
                    str(row["source_code"]), "gap_recovered", moment
                )
            self.connection.execute(
                "UPDATE capture_source_metrics SET last_available_at_utc=?,"
                "last_message_id=?,last_capture_sequence=? WHERE source_code=?",
                (
                    str(row["available_at_utc"]),
                    int(row["message_id"]),
                    int(row["capture_sequence"]),
                    str(row["source_code"]),
                ),
            )
            self.connection.execute(
                "DELETE FROM capture_outbox WHERE event_id=?", (event_id,)
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def has_message(self, source_code: str, message_id: int) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM capture_messages WHERE source_code=? AND message_id=? "
            "AND deleted=0",
            (source_code, int(message_id)),
        ).fetchone() is not None

    def highest_message_id(self, source_code: str) -> int | None:
        row = self.connection.execute(
            "SELECT MAX(message_id) FROM capture_messages WHERE source_code=?",
            (source_code,),
        ).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else None

    def message_deleted(self, source_code: str, message_id: int) -> bool:
        row = self.connection.execute(
            "SELECT deleted FROM capture_messages WHERE source_code=? AND message_id=?",
            (source_code, int(message_id)),
        ).fetchone()
        return bool(row and int(row["deleted"]))

    def note_quarantine(
        self,
        payload: bytes,
        reason: object,
        *,
        source_code: str | None = None,
        now: datetime | None = None,
    ) -> None:
        moment = now or utc_now()
        reason_code = _safe_reason(reason)
        digest = sha256(payload).hexdigest()
        expires = utc_text(moment + RAW_RETENTION)
        source = source_code if source_code in ACCOUNT_SOURCES[self.account] else None
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                INSERT INTO capture_quarantine(
                  record_sha256,reason_code,source_code,byte_count,
                  first_seen_at_utc,last_seen_at_utc,occurrences,expires_at_utc
                ) VALUES(?,?,?,?,?,?,1,?)
                ON CONFLICT(record_sha256,reason_code) DO UPDATE SET
                  last_seen_at_utc=excluded.last_seen_at_utc,
                  occurrences=capture_quarantine.occurrences+1,
                  expires_at_utc=excluded.expires_at_utc
                """,
                (
                    digest,
                    reason_code,
                    source,
                    len(payload),
                    utc_text(moment),
                    utc_text(moment),
                    expires,
                ),
            )
            if source:
                self._increment_metric(source, "quarantined", moment)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def prune(self, *, now: datetime) -> dict[str, int]:
        cutoff = utc_text(now)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            seen = self.connection.execute(
                "DELETE FROM capture_seen WHERE expires_at_utc < ?", (cutoff,)
            ).rowcount
            messages = self.connection.execute(
                "DELETE FROM capture_messages WHERE expires_at_utc < ?", (cutoff,)
            ).rowcount
            quarantine = self.connection.execute(
                "DELETE FROM capture_quarantine WHERE expires_at_utc < ?", (cutoff,)
            ).rowcount
            stale_outbox = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM capture_outbox WHERE available_at_utc < ?",
                    (utc_text(now - RAW_RETENTION),),
                ).fetchone()[0]
            )
            self.connection.commit()
            return {
                "seen": int(seen),
                "messages": int(messages),
                "quarantine": int(quarantine),
                "stale_outbox": stale_outbox,
            }
        except BaseException:
            self.connection.rollback()
            raise

    def heartbeat(
        self,
        *,
        role: str,
        release_sha: str,
        mode: str,
        started_at_utc: str,
        last_durable_append: datetime | None,
        now: datetime | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        moment = now or utc_now()
        metrics: dict[str, Any] = {}
        rows = self.connection.execute(
            "SELECT * FROM capture_source_metrics ORDER BY source_code"
        ).fetchall()
        for row in rows:
            last = (
                parse_utc(row["last_available_at_utc"], field="capture_last_available")
                if row["last_available_at_utc"]
                else None
            )
            metrics[str(row["source_code"])] = {
                "created": int(row["created"]),
                "edited": int(row["edited"]),
                "deleted": int(row["deleted"]),
                "duplicate": int(row["duplicate"]),
                "quarantined": int(row["quarantined"]),
                "gap_recovered": int(row["gap_recovered"]),
                "last_update_age_seconds": (
                    max(0.0, round((moment - last).total_seconds(), 3))
                    if last is not None
                    else None
                ),
                "last_capture_sequence": int(row["last_capture_sequence"]),
            }
        outbox = int(
            self.connection.execute("SELECT COUNT(*) FROM capture_outbox").fetchone()[0]
        )
        return {
            "schema": CAPTURE_ENGINE_SCHEMA,
            "engine_version": CAPTURE_ENGINE_VERSION,
            "role": role,
            "account": self.account,
            "mode": mode,
            "release_sha": release_sha,
            "pid": os.getpid(),
            "started_at_utc": started_at_utc,
            "updated_at_utc": utc_text(moment),
            "status": status or f"{mode}-ready",
            "durable_write": True,
            "outbox": outbox,
            "capture_sequence": self.sequence(),
            "last_durable_append_at_utc": (
                utc_text(last_durable_append) if last_durable_append else None
            ),
            "sources": metrics,
        }


class DurableEventSpool:
    """Append-only account spool with partial-tail repair and exact retention."""

    def __init__(self, root: Path, *, account: str) -> None:
        if account not in ACCOUNT_STREAM:
            raise CaptureRuntimeError("capture_account_invalid")
        self.account = account
        # Compose already gives each account an isolated capture mount.  Do
        # not add another account component or expose the sibling account to
        # this container merely to simplify host paths.
        self.directory = root
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.event_ids: set[str] = set()
        self.max_sequence = 0
        self.last_durable_append: datetime | None = None
        self._repair_and_index()

    def _event_files(self) -> tuple[Path, ...]:
        return tuple(
            path
            for path in sorted(self.directory.glob("events-*.jsonl"))
            if path.is_file() and not path.is_symlink()
        )

    def _quarantine_tail(self, path: Path, offset: int, raw: bytes) -> None:
        record = {
            "schema": "capture_spool_quarantine/1.0",
            "reason_code": "PARTIAL_TAIL_REPAIRED",
            "file_date": path.stem.removeprefix("events-"),
            "offset": int(offset),
            "byte_count": len(raw),
            "sha256": sha256(raw).hexdigest(),
            "observed_at_utc": utc_text(),
        }
        target = self.directory / f"quarantine-{utc_now():%Y-%m-%d}.jsonl"
        descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            payload = canonical_json(record) + b"\n"
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError("capture_quarantine_short_write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _repair_and_index(self) -> None:
        self.event_ids.clear()
        self.max_sequence = 0
        for path in self._event_files():
            offset = 0
            with path.open("rb") as handle:
                while True:
                    raw = handle.readline(MAX_RECORD_BYTES + 2)
                    if not raw:
                        break
                    if len(raw) > MAX_RECORD_BYTES + 1:
                        raise CaptureSpoolCorruption("capture_spool_record_too_large")
                    complete = raw.endswith(b"\n")
                    try:
                        document = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        if not complete:
                            self._quarantine_tail(path, offset, raw)
                            descriptor = os.open(path, os.O_WRONLY)
                            try:
                                os.ftruncate(descriptor, offset)
                                os.fsync(descriptor)
                            finally:
                                os.close(descriptor)
                            fsync_directory(self.directory)
                            break
                        raise CaptureSpoolCorruption("capture_spool_corrupt_middle") from exc
                    if not complete:
                        self._quarantine_tail(path, offset, raw)
                        descriptor = os.open(path, os.O_WRONLY)
                        try:
                            os.ftruncate(descriptor, offset)
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                        fsync_directory(self.directory)
                        break
                    event_id = str(document.get("event_id") or "")
                    sequence = int(
                        (document.get("producer") or {}).get("capture_sequence") or 0
                    )
                    if not event_id or event_id in self.event_ids:
                        raise CaptureSpoolCorruption("capture_spool_event_identity_invalid")
                    if sequence <= self.max_sequence:
                        raise CaptureSpoolCorruption("capture_spool_sequence_not_monotonic")
                    self.event_ids.add(event_id)
                    self.max_sequence = sequence
                    offset += len(raw)

    def append(
        self, document: Mapping[str, Any], *, now: datetime | None = None
    ) -> bool:
        event_id = str(document.get("event_id") or "")
        sequence = int((document.get("producer") or {}).get("capture_sequence") or 0)
        if not event_id or sequence <= 0:
            raise CaptureRuntimeError("capture_spool_identity_missing")
        if event_id in self.event_ids:
            return False
        if sequence <= self.max_sequence:
            raise CaptureRuntimeError("capture_spool_sequence_regression")
        moment = now or utc_now()
        target = self.directory / f"events-{moment:%Y-%m-%d}.jsonl"
        existed = target.exists()
        payload = canonical_json(document) + b"\n"
        if len(payload) > MAX_RECORD_BYTES + 1:
            raise CaptureRuntimeError("capture_spool_record_too_large")
        descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("capture_spool_short_write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(target, 0o600)
        if not existed:
            fsync_directory(self.directory)
        self.event_ids.add(event_id)
        self.max_sequence = sequence
        self.last_durable_append = moment
        return True

    def purge(self, *, now: datetime) -> dict[str, int]:
        cutoff = now - RAW_RETENTION
        purged_files = purged_records = compacted_files = 0
        for path in self._event_files():
            temporary = path.with_name(f".{path.name}.retention.tmp")
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            original = kept = 0
            try:
                with path.open("rb") as handle:
                    for raw in handle:
                        original += 1
                        document = json.loads(raw)
                        available = parse_utc(
                            (document.get("producer") or {}).get(
                                "available_at_utc"
                            ),
                            field="capture_retention_available_at_utc",
                        )
                        if available < cutoff:
                            continue
                        kept += 1
                        view = memoryview(raw)
                        while view:
                            written = os.write(descriptor, view)
                            if written <= 0:
                                raise OSError("capture_retention_short_write")
                            view = view[written:]
                os.fsync(descriptor)
            except BaseException:
                try:
                    temporary.unlink(missing_ok=True)
                finally:
                    fsync_directory(self.directory)
                raise
            finally:
                os.close(descriptor)
            removed = original - kept
            if removed <= 0:
                temporary.unlink()
                continue
            purged_records += removed
            if kept == 0:
                temporary.unlink()
                path.unlink()
                fsync_directory(self.directory)
                purged_files += 1
                continue
            os.replace(temporary, path)
            fsync_directory(self.directory)
            compacted_files += 1
        self._repair_and_index()
        return {
            "purged_files": purged_files,
            "purged_records": purged_records,
            "compacted_files": compacted_files,
        }

    def record_retention_audit(
        self,
        *,
        now: datetime,
        spool: Mapping[str, int],
        state: Mapping[str, int],
    ) -> None:
        target = self.directory / f"retention-audit-{now:%Y-%m}.jsonl"
        document = {
            "schema": "capture_retention_audit/1.0",
            "account": self.account,
            "ran_at_utc": utc_text(now),
            "spool": dict(spool),
            "state": dict(state),
        }
        descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            payload = canonical_json(document) + b"\n"
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("capture_retention_audit_short_write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fsync_directory(self.directory)


class CaptureEngine:
    """Coordinate SQLite outbox, fsynced spool, metrics, and retention."""

    def __init__(self, state: CaptureState, spool: DurableEventSpool) -> None:
        if state.account != spool.account:
            raise CaptureRuntimeError("capture_engine_account_mismatch")
        self.state = state
        self.spool = spool
        self.state.ensure_sequence_floor(self.spool.max_sequence)

    def drain(self, *, crash_point: str | None = None, crash_sequence: int = 0) -> int:
        completed = 0
        for pending in self.state.pending():
            if pending.payload is None:
                raise CaptureRuntimeError("capture_pending_payload_missing")
            appended = self.spool.append(pending.payload)
            if (
                crash_point == "after_append"
                and pending.sequence == int(crash_sequence)
                and appended
            ):
                os._exit(75)
            self.state.complete(pending.event_id)
            completed += 1
        return completed

    def accept(
        self,
        document: Mapping[str, Any],
        *,
        crash_point: str | None = None,
        crash_sequence: int = 0,
        now: datetime | None = None,
    ) -> StageResult:
        result = self.state.stage(document, now=now)
        if result.status == "duplicate":
            return result
        if (
            crash_point == "after_stage"
            and result.sequence == int(crash_sequence)
            and result.status == "staged"
        ):
            os._exit(75)
        self.drain(crash_point=crash_point, crash_sequence=crash_sequence)
        return result

    def quarantine(
        self,
        document: object,
        reason: object,
        *,
        source_code: str | None = None,
    ) -> None:
        try:
            payload = json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            payload = b"unserializable-capture-record"
        self.state.note_quarantine(payload, reason, source_code=source_code)

    def retention(self, *, now: datetime) -> dict[str, Any]:
        spool_report = self.spool.purge(now=now)
        state_report = self.state.prune(now=now)
        self.spool.record_retention_audit(
            now=now, spool=spool_report, state=state_report
        )
        return {"spool": spool_report, "state": state_report}


def load_fixture_events(path: Path) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for raw in handle:
            if len(raw) > MAX_RECORD_BYTES + 1 or not raw.endswith(b"\n"):
                raise CaptureRuntimeError("capture_fixture_record_invalid")
            try:
                document = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CaptureRuntimeError("capture_fixture_json_invalid") from exc
            if not isinstance(document, dict):
                raise CaptureRuntimeError("capture_fixture_object_required")
            events.append(document)
    return tuple(events)


def process_fixture_events(
    engine: CaptureEngine,
    events: Iterable[Mapping[str, Any]],
    *,
    crash_point: str | None = None,
    crash_sequence: int = 0,
    now: datetime | None = None,
) -> dict[str, int]:
    counters = {"accepted": 0, "duplicates": 0, "quarantined": 0}
    engine.drain(crash_point=crash_point, crash_sequence=crash_sequence)
    for document in events:
        try:
            result = engine.accept(
                document,
                crash_point=crash_point,
                crash_sequence=crash_sequence,
                now=now,
            )
        except CaptureRuntimeError as exc:
            source = None
            if isinstance(document.get("source"), Mapping):
                source = str(document["source"].get("source_id") or "").upper()
            engine.quarantine(document, exc, source_code=source)
            counters["quarantined"] += 1
            continue
        if result.status == "duplicate":
            counters["duplicates"] += 1
        else:
            counters["accepted"] += 1
    return counters


__all__ = [
    "ACCOUNT_SOURCES",
    "ACCOUNT_STREAM",
    "CAPTURE_ENGINE_SCHEMA",
    "CAPTURE_ENGINE_VERSION",
    "CaptureEngine",
    "CaptureRuntimeError",
    "CaptureSpoolCorruption",
    "CaptureState",
    "DurableEventSpool",
    "RAW_RETENTION",
    "ROLE_ACCOUNT",
    "StageResult",
    "atomic_json",
    "load_fixture_events",
    "parse_utc",
    "process_fixture_events",
    "utc_now",
    "utc_text",
    "validate_ingress",
]
