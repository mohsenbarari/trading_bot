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
import stat
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
HEX64 = frozenset("0123456789abcdef")
QUARANTINE_RESOLUTION_SCHEMA = "capture_quarantine_resolution/1.1"
QUARANTINE_RESOLUTION_EVIDENCE_SCHEMA = (
    "capture_quarantine_resolution_evidence/1.0"
)
REPLAY_MANIFEST_SCHEMA = "capture_replay_manifest/1.0"

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


def value_free_set_digest(rows: Iterable[Sequence[object]]) -> tuple[int, str]:
    """Return the audit-compatible digest of a value-free identity set."""

    normalized = sorted(tuple(str(item) for item in row) for row in rows)
    material = (
        json.dumps(
            normalized,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    return len(normalized), sha256(material).hexdigest()


def _read_digest_bound_json(
    path: Path, *, expected_sha256: str
) -> tuple[Mapping[str, Any], bytes]:
    """Read one non-symlink evidence file through a stable file descriptor."""

    if (
        len(expected_sha256) != 64
        or any(character not in HEX64 for character in expected_sha256)
    ):
        raise CaptureRuntimeError("capture_resolution_evidence_digest_invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CaptureRuntimeError("capture_resolution_evidence_unreadable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 4_000_000:
            raise CaptureRuntimeError("capture_resolution_evidence_unreadable")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 128 * 1024))
            if not chunk:
                raise CaptureRuntimeError("capture_resolution_evidence_unreadable")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise CaptureRuntimeError("capture_resolution_evidence_changed")
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise CaptureRuntimeError("capture_resolution_evidence_changed")
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if sha256(raw).hexdigest() != expected_sha256:
        raise CaptureRuntimeError("capture_resolution_evidence_digest_mismatch")
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_key")
            value[key] = item
        return value

    try:
        document = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CaptureRuntimeError("capture_resolution_evidence_invalid") from exc
    if not isinstance(document, Mapping):
        raise CaptureRuntimeError("capture_resolution_evidence_invalid")
    return document, raw


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


@dataclass(frozen=True, slots=True)
class QuarantineEventIdentity:
    """Value-free identity for one real Telegram revision.

    ``revision_sha256`` is calculated in memory from Telegram revision
    metadata.  Neither message text nor sender identity is retained here.
    """

    account: str
    source_code: str
    message_id: int
    revision_sha256: str
    event_type: str
    origin: str

    def material(self) -> dict[str, object]:
        return {
            "account": self.account,
            "source_code": self.source_code,
            "message_id": self.message_id,
            "revision_sha256": self.revision_sha256,
            "event_type": self.event_type,
            "origin": self.origin,
        }

    def validate(self) -> None:
        if (
            self.account not in ACCOUNT_SOURCES
            or self.source_code not in ACCOUNT_SOURCES[self.account]
            or isinstance(self.message_id, bool)
            or self.message_id < 1
            or len(self.revision_sha256) != 64
            or any(character not in HEX64 for character in self.revision_sha256)
            or self.event_type
            not in {"message_created", "message_snapshot", "message_edited", "message_deleted"}
            or self.origin not in {"live", "reconcile", "explicit_backfill"}
        ):
            raise CaptureRuntimeError("capture_quarantine_event_identity_invalid")

    @property
    def marker_sha256(self) -> str:
        self.validate()
        return sha256(canonical_json(self.material())).hexdigest()


def quarantine_row_fingerprint(
    *,
    account: str,
    kind: str,
    marker_sha256: str,
    reason_code: str,
    occurrences: int,
    last_seen_at_utc: str,
    source_code: str | None = None,
    message_id: int | None = None,
    revision_sha256: str | None = None,
) -> str:
    """Fingerprint the exact immutable view of a quarantine row.

    A later occurrence changes ``occurrences`` and ``last_seen_at_utc`` and
    therefore invalidates any earlier resolution without updating or deleting
    the resolution ledger.  The marker already commits to the event identity;
    the fingerprint itself is deliberately the exact account + marker + reason
    + occurrence count + last-seen contract for both old and new rows.
    """

    if account not in ACCOUNT_SOURCES or kind not in {"legacy", "event"}:
        raise CaptureRuntimeError("capture_quarantine_fingerprint_invalid")
    if (
        len(marker_sha256) != 64
        or any(character not in HEX64 for character in marker_sha256)
        or isinstance(occurrences, bool)
        or occurrences < 1
    ):
        raise CaptureRuntimeError("capture_quarantine_fingerprint_invalid")
    if kind == "event" and (
        source_code not in ACCOUNT_SOURCES[account]
        or isinstance(message_id, bool)
        or not isinstance(message_id, int)
        or message_id < 1
        or not isinstance(revision_sha256, str)
        or len(revision_sha256) != 64
        or any(character not in HEX64 for character in revision_sha256)
    ):
        raise CaptureRuntimeError("capture_quarantine_fingerprint_invalid")
    if kind == "legacy" and any(
        value is not None for value in (source_code, message_id, revision_sha256)
    ):
        raise CaptureRuntimeError("capture_quarantine_fingerprint_invalid")
    normalized_last_seen = utc_text(
        parse_utc(last_seen_at_utc, field="capture_quarantine_last_seen")
    )
    material: dict[str, object] = {
        "account": account,
        "marker_sha256": marker_sha256,
        "reason_code": _safe_reason(reason_code),
        "occurrences": occurrences,
        "last_seen_at_utc": normalized_last_seen,
    }
    return sha256(canonical_json(material)).hexdigest()


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
            CREATE TABLE IF NOT EXISTS capture_context_diagnostics(
              source_code TEXT NOT NULL,
              reason_code TEXT NOT NULL,
              occurrences INTEGER NOT NULL CHECK(occurrences > 0),
              first_seen_at_utc TEXT NOT NULL,
              last_seen_at_utc TEXT NOT NULL,
              expires_at_utc TEXT NOT NULL,
              PRIMARY KEY(source_code,reason_code)
            );
            CREATE INDEX IF NOT EXISTS idx_capture_context_diagnostics_expiry
              ON capture_context_diagnostics(expires_at_utc);
            CREATE TABLE IF NOT EXISTS capture_backfill_status(
              source_code TEXT PRIMARY KEY,
              cutoff_utc TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('running','complete')),
              run_attempts INTEGER NOT NULL CHECK(run_attempts > 0),
              attempted INTEGER NOT NULL DEFAULT 0 CHECK(attempted >= 0),
              accepted INTEGER NOT NULL DEFAULT 0 CHECK(accepted >= 0),
              duplicate INTEGER NOT NULL DEFAULT 0 CHECK(duplicate >= 0),
              quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined >= 0),
              started_at_utc TEXT NOT NULL,
              updated_at_utc TEXT NOT NULL,
              completed_at_utc TEXT,
              exhaustion TEXT CHECK(
                exhaustion IS NULL OR
                exhaustion IN ('cutoff_crossed','source_exhausted')
              )
            );
            CREATE TABLE IF NOT EXISTS capture_event_quarantine(
              account TEXT NOT NULL,
              source_code TEXT NOT NULL,
              message_id INTEGER NOT NULL CHECK(message_id > 0),
              revision_sha256 TEXT NOT NULL CHECK(length(revision_sha256)=64),
              event_type TEXT NOT NULL,
              origin TEXT NOT NULL,
              marker_sha256 TEXT NOT NULL CHECK(length(marker_sha256)=64),
              reason_code TEXT NOT NULL,
              byte_count INTEGER NOT NULL CHECK(byte_count >= 0),
              first_seen_at_utc TEXT NOT NULL,
              last_seen_at_utc TEXT NOT NULL,
              occurrences INTEGER NOT NULL CHECK(occurrences > 0),
              expires_at_utc TEXT NOT NULL,
              PRIMARY KEY(
                account,source_code,message_id,revision_sha256,
                event_type,origin,reason_code
              )
            );
            CREATE INDEX IF NOT EXISTS idx_capture_event_quarantine_expiry
              ON capture_event_quarantine(expires_at_utc);
            CREATE TABLE IF NOT EXISTS capture_replay_runs(
              run_id TEXT PRIMARY KEY CHECK(length(run_id)=64),
              schema TEXT NOT NULL,
              account TEXT NOT NULL,
              cutoff_utc TEXT NOT NULL,
              upper_bound_utc TEXT NOT NULL,
              source_inventory_json TEXT NOT NULL,
              source_inventory_sha256 TEXT NOT NULL
                CHECK(length(source_inventory_sha256)=64),
              release_sha TEXT NOT NULL CHECK(length(release_sha)=40),
              started_at_utc TEXT NOT NULL,
              completed_at_utc TEXT,
              manifest_count INTEGER CHECK(manifest_count IS NULL OR manifest_count>=0),
              manifest_sha256 TEXT CHECK(
                manifest_sha256 IS NULL OR length(manifest_sha256)=64
              ),
              CHECK(
                (completed_at_utc IS NULL AND manifest_count IS NULL
                 AND manifest_sha256 IS NULL)
                OR
                (completed_at_utc IS NOT NULL AND manifest_count IS NOT NULL
                 AND manifest_sha256 IS NOT NULL)
              )
            );
            CREATE TRIGGER IF NOT EXISTS capture_replay_run_no_rewrite
              BEFORE UPDATE ON capture_replay_runs
              WHEN OLD.completed_at_utc IS NOT NULL
                OR NEW.run_id<>OLD.run_id
                OR NEW.schema<>OLD.schema
                OR NEW.account<>OLD.account
                OR NEW.cutoff_utc<>OLD.cutoff_utc
                OR NEW.upper_bound_utc<>OLD.upper_bound_utc
                OR NEW.source_inventory_json<>OLD.source_inventory_json
                OR NEW.source_inventory_sha256<>OLD.source_inventory_sha256
                OR NEW.release_sha<>OLD.release_sha
                OR NEW.started_at_utc<>OLD.started_at_utc
                OR NEW.completed_at_utc IS NULL
                OR NEW.manifest_count IS NULL
                OR NEW.manifest_sha256 IS NULL
              BEGIN SELECT RAISE(ABORT,'capture_replay_run_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS capture_replay_run_no_delete
              BEFORE DELETE ON capture_replay_runs
              BEGIN SELECT RAISE(ABORT,'capture_replay_run_append_only'); END;
            CREATE TABLE IF NOT EXISTS capture_replay_manifest_entries(
              run_id TEXT NOT NULL REFERENCES capture_replay_runs(run_id),
              account TEXT NOT NULL,
              source_code TEXT NOT NULL,
              message_id INTEGER NOT NULL CHECK(message_id>0),
              revision_sha256 TEXT NOT NULL CHECK(length(revision_sha256)=64),
              event_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              origin TEXT NOT NULL,
              content_type TEXT NOT NULL,
              event_time_utc TEXT,
              available_at_utc TEXT NOT NULL,
              capture_status TEXT NOT NULL CHECK(
                capture_status IN ('accepted','duplicate')
              ),
              marker_sha256 TEXT NOT NULL CHECK(length(marker_sha256)=64),
              PRIMARY KEY(run_id,event_id),
              UNIQUE(
                run_id,account,source_code,message_id,revision_sha256,
                event_type,origin
              )
            );
            CREATE TRIGGER IF NOT EXISTS capture_replay_manifest_no_update
              BEFORE UPDATE ON capture_replay_manifest_entries
              BEGIN SELECT RAISE(ABORT,'capture_replay_manifest_append_only'); END;
            CREATE TRIGGER IF NOT EXISTS capture_replay_manifest_no_delete
              BEFORE DELETE ON capture_replay_manifest_entries
              BEGIN SELECT RAISE(ABORT,'capture_replay_manifest_append_only'); END;
            CREATE TABLE IF NOT EXISTS capture_quarantine_resolutions(
              resolution_id TEXT PRIMARY KEY CHECK(length(resolution_id)=64),
              schema TEXT NOT NULL,
              account TEXT NOT NULL,
              quarantine_kind TEXT NOT NULL CHECK(
                quarantine_kind IN ('legacy','event')
              ),
              marker_sha256 TEXT NOT NULL CHECK(length(marker_sha256)=64),
              reason_code TEXT NOT NULL,
              source_code TEXT,
              message_id INTEGER,
              revision_sha256 TEXT,
              observed_occurrences INTEGER NOT NULL CHECK(observed_occurrences>0),
              observed_last_seen_at_utc TEXT NOT NULL,
              quarantine_fingerprint TEXT NOT NULL
                CHECK(length(quarantine_fingerprint)=64),
              replay_run_id TEXT NOT NULL REFERENCES capture_replay_runs(run_id),
              cutoff_utc TEXT NOT NULL,
              upper_bound_utc TEXT NOT NULL,
              manifest_count INTEGER NOT NULL CHECK(manifest_count>0),
              manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64),
              manifest_identity_count INTEGER NOT NULL
                CHECK(manifest_identity_count>0),
              manifest_identity_sha256 TEXT NOT NULL
                CHECK(length(manifest_identity_sha256)=64),
              terminal_count INTEGER NOT NULL CHECK(terminal_count>0),
              terminal_sha256 TEXT NOT NULL CHECK(length(terminal_sha256)=64),
              archive_count INTEGER NOT NULL CHECK(archive_count>=0),
              archive_sha256 TEXT NOT NULL CHECK(length(archive_sha256)=64),
              ack_count INTEGER NOT NULL CHECK(ack_count>=0),
              ack_sha256 TEXT NOT NULL CHECK(length(ack_sha256)=64),
              store_count INTEGER NOT NULL CHECK(store_count>=0),
              store_sha256 TEXT NOT NULL CHECK(length(store_sha256)=64),
              evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
              evidence_json TEXT NOT NULL,
              web_artifact_sha256 TEXT NOT NULL
                CHECK(length(web_artifact_sha256)=64),
              bot_artifact_sha256 TEXT NOT NULL
                CHECK(length(bot_artifact_sha256)=64),
              verification_artifact_sha256 TEXT NOT NULL
                CHECK(length(verification_artifact_sha256)=64),
              resolved_at_utc TEXT NOT NULL,
              UNIQUE(quarantine_fingerprint,replay_run_id)
            );
            CREATE TRIGGER IF NOT EXISTS capture_quarantine_resolution_no_update
              BEFORE UPDATE ON capture_quarantine_resolutions
              BEGIN SELECT RAISE(ABORT,'capture_quarantine_resolution_append_only'); END;
            CREATE TRIGGER IF NOT EXISTS capture_quarantine_resolution_no_delete
              BEFORE DELETE ON capture_quarantine_resolutions
              BEGIN SELECT RAISE(ABORT,'capture_quarantine_resolution_append_only'); END;
            """
        )
        # The resolution ledger first shipped without independently bound
        # evidence columns.  Preserve those rows byte-for-byte, but extend the
        # table so only schema 1.1 rows can become authoritative.  Nullable
        # migration columns are intentional: historical 1.0 rows remain
        # visible as superseded, non-authoritative evidence and are never
        # rewritten or deleted.
        resolution_columns = {
            str(row["name"])
            for row in self.connection.execute(
                "PRAGMA table_info(capture_quarantine_resolutions)"
            )
        }
        for name, definition in (
            ("manifest_identity_count", "INTEGER"),
            ("manifest_identity_sha256", "TEXT"),
            ("terminal_count", "INTEGER"),
            ("archive_count", "INTEGER"),
            ("ack_count", "INTEGER"),
            ("store_count", "INTEGER"),
            ("evidence_sha256", "TEXT"),
            ("evidence_json", "TEXT"),
            ("web_artifact_sha256", "TEXT"),
            ("bot_artifact_sha256", "TEXT"),
            ("verification_artifact_sha256", "TEXT"),
        ):
            if name not in resolution_columns:
                self.connection.execute(
                    f"ALTER TABLE capture_quarantine_resolutions "
                    f"ADD COLUMN {name} {definition}"
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

    @staticmethod
    def _backfill_cutoff(cutoff: datetime) -> datetime:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise CaptureRuntimeError("capture_backfill_cutoff_timezone_required")
        return cutoff.astimezone(timezone.utc)

    def backfill_covers(self, source_code: str, cutoff: datetime) -> bool:
        if source_code not in ACCOUNT_SOURCES[self.account]:
            raise CaptureRuntimeError("capture_backfill_source_invalid")
        requested = self._backfill_cutoff(cutoff)
        status = self.connection.execute(
            "SELECT cutoff_utc,status,quarantined FROM capture_backfill_status "
            "WHERE source_code=?",
            (source_code,),
        ).fetchone()
        if status is None:
            return False
        completed = parse_utc(
            status["cutoff_utc"], field="capture_backfill_completed_cutoff"
        )
        # A completed earlier cutoff is a superset of a later request.
        return (
            str(status["status"]) == "complete"
            and int(status["quarantined"]) == 0
            and completed <= requested
        )

    def begin_backfill(
        self, source_code: str, cutoff: datetime, *, now: datetime | None = None
    ) -> None:
        if source_code not in ACCOUNT_SOURCES[self.account]:
            raise CaptureRuntimeError("capture_backfill_source_invalid")
        requested = self._backfill_cutoff(cutoff)
        moment = now or utc_now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise CaptureRuntimeError("capture_backfill_now_timezone_required")
        existing = self.connection.execute(
            "SELECT run_attempts FROM capture_backfill_status WHERE source_code=?",
            (source_code,),
        ).fetchone()
        attempts = int(existing["run_attempts"]) + 1 if existing is not None else 1
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                INSERT INTO capture_backfill_status(
                  source_code,cutoff_utc,status,run_attempts,attempted,accepted,
                  duplicate,quarantined,started_at_utc,updated_at_utc,
                  completed_at_utc,exhaustion
                ) VALUES(?,?,'running',?,0,0,0,0,?,?,NULL,NULL)
                ON CONFLICT(source_code) DO UPDATE SET
                  cutoff_utc=excluded.cutoff_utc,
                  status='running',
                  run_attempts=excluded.run_attempts,
                  attempted=0,
                  accepted=0,
                  duplicate=0,
                  quarantined=0,
                  started_at_utc=excluded.started_at_utc,
                  updated_at_utc=excluded.updated_at_utc,
                  completed_at_utc=NULL,
                  exhaustion=NULL
                """,
                (
                    source_code,
                    utc_text(requested),
                    attempts,
                    utc_text(moment),
                    utc_text(moment),
                ),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def note_backfill_outcome(
        self,
        source_code: str,
        cutoff: datetime,
        outcome: str,
        *,
        now: datetime | None = None,
    ) -> None:
        if source_code not in ACCOUNT_SOURCES[self.account]:
            raise CaptureRuntimeError("capture_backfill_source_invalid")
        if outcome not in {"accepted", "duplicate", "quarantined"}:
            raise CaptureRuntimeError("capture_backfill_outcome_invalid")
        requested = utc_text(self._backfill_cutoff(cutoff))
        moment = now or utc_now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise CaptureRuntimeError("capture_backfill_now_timezone_required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT status,cutoff_utc FROM capture_backfill_status WHERE source_code=?",
                (source_code,),
            ).fetchone()
            if (
                row is None
                or str(row["status"]) != "running"
                or str(row["cutoff_utc"]) != requested
            ):
                raise CaptureRuntimeError("capture_backfill_not_running")
            self.connection.execute(
                f"UPDATE capture_backfill_status SET attempted=attempted+1,"
                f"{outcome}={outcome}+1,updated_at_utc=? WHERE source_code=?",
                (utc_text(moment), source_code),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def mark_backfill_complete(
        self,
        source_code: str,
        cutoff: datetime,
        *,
        expected_attempted: int | None = None,
        exhaustion: str,
        now: datetime | None = None,
    ) -> None:
        if source_code not in ACCOUNT_SOURCES[self.account]:
            raise CaptureRuntimeError("capture_backfill_source_invalid")
        requested = self._backfill_cutoff(cutoff)
        moment = now or utc_now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise CaptureRuntimeError("capture_backfill_now_timezone_required")
        if exhaustion not in {"cutoff_crossed", "source_exhausted"}:
            raise CaptureRuntimeError("capture_backfill_exhaustion_invalid")
        key = f"backfill_not_before:{source_code}"
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            status = self.connection.execute(
                "SELECT * FROM capture_backfill_status WHERE source_code=?",
                (source_code,),
            ).fetchone()
            if status is None or str(status["status"]) != "running":
                raise CaptureRuntimeError("capture_backfill_not_running")
            if parse_utc(
                status["cutoff_utc"], field="capture_backfill_active_cutoff"
            ) != requested:
                raise CaptureRuntimeError("capture_backfill_cutoff_mismatch")
            attempted = int(status["attempted"])
            accounted = sum(
                int(status[field])
                for field in ("accepted", "duplicate", "quarantined")
            )
            if attempted != accounted:
                raise CaptureRuntimeError("capture_backfill_counter_mismatch")
            if expected_attempted is not None and attempted != int(expected_attempted):
                raise CaptureRuntimeError("capture_backfill_attempted_mismatch")
            row = self.connection.execute(
                "SELECT value FROM capture_kv WHERE key=?", (key,)
            ).fetchone()
            if row is None or parse_utc(
                row[0], field="capture_backfill_completed_cutoff"
            ) > requested:
                self.connection.execute(
                    "INSERT INTO capture_kv(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, utc_text(requested)),
                )
            self.connection.execute(
                "UPDATE capture_backfill_status SET status='complete',"
                "updated_at_utc=?,completed_at_utc=?,exhaustion=? "
                "WHERE source_code=?",
                (utc_text(moment), utc_text(moment), exhaustion, source_code),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def backfill_status(self, source_code: str) -> dict[str, Any] | None:
        if source_code not in ACCOUNT_SOURCES[self.account]:
            raise CaptureRuntimeError("capture_backfill_source_invalid")
        row = self.connection.execute(
            "SELECT * FROM capture_backfill_status WHERE source_code=?",
            (source_code,),
        ).fetchone()
        if row is None:
            return None
        return {
            "status": str(row["status"]),
            "cutoff_utc": str(row["cutoff_utc"]),
            "run_attempts": int(row["run_attempts"]),
            "attempted": int(row["attempted"]),
            "accepted": int(row["accepted"]),
            "duplicate": int(row["duplicate"]),
            "quarantined": int(row["quarantined"]),
            "started_at_utc": str(row["started_at_utc"]),
            "updated_at_utc": str(row["updated_at_utc"]),
            "completed_at_utc": (
                str(row["completed_at_utc"])
                if row["completed_at_utc"] is not None
                else None
            ),
            "exhaustion": (
                str(row["exhaustion"]) if row["exhaustion"] is not None else None
            ),
        }

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

    def note_event_quarantine(
        self,
        identity: QuarantineEventIdentity,
        reason: object,
        *,
        now: datetime | None = None,
    ) -> None:
        """Record one value-free, source- and revision-bound failure.

        The legacy quarantine table is deliberately left untouched.  Its
        generic handler markers cannot distinguish sources or Telegram
        revisions.  New failures therefore use a separate backward-compatible
        table whose primary key carries the complete safe identity.
        """

        identity.validate()
        if identity.account != self.account:
            raise CaptureRuntimeError("capture_quarantine_account_mismatch")
        moment = now or utc_now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise CaptureRuntimeError("capture_quarantine_now_timezone_required")
        marker = identity.marker_sha256
        reason_code = _safe_reason(reason)
        marker_bytes = canonical_json(identity.material())
        expires = utc_text(moment + RAW_RETENTION)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                INSERT INTO capture_event_quarantine(
                  account,source_code,message_id,revision_sha256,event_type,
                  origin,marker_sha256,reason_code,byte_count,
                  first_seen_at_utc,last_seen_at_utc,occurrences,expires_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)
                ON CONFLICT(
                  account,source_code,message_id,revision_sha256,
                  event_type,origin,reason_code
                ) DO UPDATE SET
                  last_seen_at_utc=excluded.last_seen_at_utc,
                  occurrences=capture_event_quarantine.occurrences+1,
                  expires_at_utc=excluded.expires_at_utc
                """,
                (
                    identity.account,
                    identity.source_code,
                    identity.message_id,
                    identity.revision_sha256,
                    identity.event_type,
                    identity.origin,
                    marker,
                    reason_code,
                    len(marker_bytes),
                    utc_text(moment),
                    utc_text(moment),
                    expires,
                ),
            )
            self._increment_metric(identity.source_code, "quarantined", moment)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def begin_replay_run(
        self,
        *,
        cutoff: datetime,
        upper_bound: datetime,
        source_codes: Iterable[str],
        release_sha: str,
        now: datetime | None = None,
    ) -> str:
        """Create or resume one fixed-bound, value-free replay manifest."""

        lower = self._backfill_cutoff(cutoff)
        upper = self._backfill_cutoff(upper_bound)
        if upper < lower:
            raise CaptureRuntimeError("capture_replay_bounds_invalid")
        sources = tuple(sorted(set(source_codes)))
        if not sources or not set(sources).issubset(ACCOUNT_SOURCES[self.account]):
            raise CaptureRuntimeError("capture_replay_source_inventory_invalid")
        if len(release_sha) != 40 or any(character not in HEX64 for character in release_sha):
            raise CaptureRuntimeError("capture_replay_release_invalid")
        inventory_json = json.dumps(sources, separators=(",", ":"))
        inventory_hash = sha256(inventory_json.encode("ascii")).hexdigest()
        static_material = {
            "schema": REPLAY_MANIFEST_SCHEMA,
            "account": self.account,
            "cutoff_utc": utc_text(lower),
            "source_inventory_sha256": inventory_hash,
            "release_sha": release_sha,
        }
        started = now or utc_now()
        if started.tzinfo is None or started.utcoffset() is None:
            raise CaptureRuntimeError("capture_replay_now_timezone_required")
        required_through = self.quarantine_required_through(lower, sources)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            # A SIGKILL/restart must resume the *same* upper bound.  Creating a
            # fresh run at every process start silently widens the point-in-time
            # evidence window and makes an interrupted proof unauditable.
            matching = self.connection.execute(
                "SELECT * FROM capture_replay_runs WHERE schema=? AND account=? "
                "AND cutoff_utc=? AND source_inventory_sha256=? AND release_sha=? "
                "ORDER BY started_at_utc,run_id",
                (
                    REPLAY_MANIFEST_SCHEMA,
                    self.account,
                    utc_text(lower),
                    inventory_hash,
                    release_sha,
                ),
            ).fetchall()
            for existing in matching:
                if str(existing["source_inventory_json"]) != inventory_json:
                    raise CaptureRuntimeError("capture_replay_run_conflict")

            # At most one generation may be open.  An interrupted process must
            # finish that exact fixed window before another generation starts.
            open_runs = [row for row in matching if row["completed_at_utc"] is None]
            if len(open_runs) > 1:
                raise CaptureRuntimeError("capture_replay_run_ambiguous")
            if open_runs:
                self.connection.commit()
                return str(open_runs[0]["run_id"])

            # A completed generation is reusable only if it was started no
            # earlier than the newest current quarantine occurrence and its
            # point-in-time upper bound covers that occurrence.  Therefore a
            # recurrence invalidates the prior resolution without mutating it,
            # while an ordinary restart still reuses the completed proof.
            covering = []
            for existing in matching:
                existing_started = parse_utc(
                    existing["started_at_utc"],
                    field="capture_replay_started_at",
                )
                existing_upper = parse_utc(
                    existing["upper_bound_utc"],
                    field="capture_replay_upper_bound",
                )
                if required_through is None or (
                    existing_started >= required_through
                    and existing_upper >= required_through
                ):
                    covering.append(existing)
            if covering:
                existing = covering[-1]
                self.connection.commit()
                return str(existing["run_id"])

            if required_through is not None and (
                upper < required_through or started < required_through
            ):
                raise CaptureRuntimeError("capture_replay_generation_stale")

            material = {
                **static_material,
                "upper_bound_utc": utc_text(upper),
                # Allows a later generation with the same explicit upper bound
                # after a fresh quarantine occurrence, without weakening
                # restart idempotence (the open run is selected above).
                "started_at_utc": utc_text(started),
            }
            run_id = sha256(canonical_json(material)).hexdigest()
            existing = self.connection.execute(
                "SELECT * FROM capture_replay_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    """
                    INSERT INTO capture_replay_runs(
                      run_id,schema,account,cutoff_utc,upper_bound_utc,
                      source_inventory_json,source_inventory_sha256,release_sha,
                      started_at_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        REPLAY_MANIFEST_SCHEMA,
                        self.account,
                        utc_text(lower),
                        utc_text(upper),
                        inventory_json,
                        inventory_hash,
                        release_sha,
                        utc_text(started),
                    ),
                )
            else:  # pragma: no cover - SHA-256 collision/hostile pre-seed only
                raise CaptureRuntimeError("capture_replay_run_conflict")
            self.connection.commit()
            return run_id
        except BaseException:
            self.connection.rollback()
            raise

    def replay_run_complete(self, run_id: str) -> bool:
        row = self.connection.execute(
            "SELECT completed_at_utc FROM capture_replay_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return row is not None and row["completed_at_utc"] is not None

    def replay_run_upper_bound(self, run_id: str) -> datetime:
        row = self.connection.execute(
            "SELECT upper_bound_utc FROM capture_replay_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise CaptureRuntimeError("capture_replay_run_missing")
        return parse_utc(row["upper_bound_utc"], field="capture_replay_upper_bound")

    def replay_source_manifest_count(self, run_id: str, source_code: str) -> int:
        if source_code not in ACCOUNT_SOURCES[self.account]:
            raise CaptureRuntimeError("capture_replay_entry_source_invalid")
        run = self.connection.execute(
            "SELECT source_inventory_json FROM capture_replay_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if run is None or source_code not in json.loads(
            str(run["source_inventory_json"])
        ):
            raise CaptureRuntimeError("capture_replay_entry_source_invalid")
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM capture_replay_manifest_entries "
                "WHERE run_id=? AND source_code=?",
                (run_id, source_code),
            ).fetchone()[0]
        )

    def event_available_at(self, event_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT available_at_utc FROM capture_seen WHERE event_id=?",
            (str(event_id),),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def record_replay_manifest_entry(
        self,
        *,
        run_id: str,
        identity: QuarantineEventIdentity,
        event_id: str,
        content_type: str,
        event_time_utc: str | None,
        available_at_utc: str,
        capture_status: str,
    ) -> None:
        identity.validate()
        if identity.account != self.account or capture_status not in {
            "accepted",
            "duplicate",
        }:
            raise CaptureRuntimeError("capture_replay_entry_invalid")
        run = self.connection.execute(
            "SELECT * FROM capture_replay_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None or run["completed_at_utc"] is not None:
            raise CaptureRuntimeError("capture_replay_run_not_open")
        sources = json.loads(str(run["source_inventory_json"]))
        if identity.source_code not in sources:
            raise CaptureRuntimeError("capture_replay_entry_source_invalid")
        lower = parse_utc(run["cutoff_utc"], field="capture_replay_cutoff")
        upper = parse_utc(run["upper_bound_utc"], field="capture_replay_upper_bound")
        available = parse_utc(available_at_utc, field="capture_replay_available_at")
        event_time = (
            parse_utc(event_time_utc, field="capture_replay_event_time")
            if event_time_utc is not None
            else available
        )
        if event_time < lower or event_time > upper:
            raise CaptureRuntimeError("capture_replay_entry_out_of_bounds")
        durable = self.connection.execute(
            "SELECT source_code,available_at_utc FROM capture_seen WHERE event_id=?",
            (str(event_id),),
        ).fetchone()
        if (
            durable is None
            or str(durable["source_code"]) != identity.source_code
            or utc_text(
                parse_utc(
                    durable["available_at_utc"],
                    field="capture_replay_durable_available_at",
                )
            )
            != utc_text(available)
        ):
            raise CaptureRuntimeError("capture_replay_durable_event_missing")
        row = (
            run_id,
            self.account,
            identity.source_code,
            identity.message_id,
            identity.revision_sha256,
            str(event_id),
            identity.event_type,
            identity.origin,
            str(content_type),
            utc_text(event_time) if event_time_utc is not None else None,
            utc_text(available),
            capture_status,
            identity.marker_sha256,
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            try:
                self.connection.execute(
                    "INSERT INTO capture_replay_manifest_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    row,
                )
            except sqlite3.IntegrityError:
                existing = self.connection.execute(
                    "SELECT * FROM capture_replay_manifest_entries "
                    "WHERE run_id=? AND event_id=?",
                    (run_id, str(event_id)),
                ).fetchone()
                # A crash after the durable append but before replay
                # completion changes a retry from ``accepted`` to
                # ``duplicate``.  That is the same durable event, not a
                # manifest conflict; every identity/bound field must still be
                # byte-identical.
                existing_values = tuple(existing) if existing is not None else ()
                if (
                    existing is None
                    or existing_values[:11] != row[:11]
                    or existing_values[12:] != row[12:]
                ):
                    raise CaptureRuntimeError("capture_replay_entry_conflict")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def legacy_quarantine_since(self, cutoff: datetime) -> bool:
        boundary = utc_text(self._backfill_cutoff(cutoff))
        return (
            self.connection.execute(
                "SELECT 1 FROM capture_quarantine "
                "WHERE first_seen_at_utc>=? OR last_seen_at_utc>=? LIMIT 1",
                (boundary, boundary),
            ).fetchone()
            is not None
        )

    def quarantine_replay_sources(self, cutoff: datetime) -> frozenset[str]:
        boundary = utc_text(self._backfill_cutoff(cutoff))
        if self.legacy_quarantine_since(cutoff):
            return ACCOUNT_SOURCES[self.account]
        return frozenset(
            str(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT source_code FROM capture_event_quarantine "
                "WHERE first_seen_at_utc>=? OR last_seen_at_utc>=?",
                (boundary, boundary),
            )
            if str(row[0]) in ACCOUNT_SOURCES[self.account]
        )

    def quarantine_required_through(
        self,
        cutoff: datetime,
        source_codes: Iterable[str],
    ) -> datetime | None:
        """Return the newest current quarantine occurrence in replay scope.

        Legacy rows deliberately ignore their historical ``source_code``.  A
        full-account inventory is the only scope allowed to cover them.
        Event-aware rows retain their exact source attribution.
        """

        boundary = utc_text(self._backfill_cutoff(cutoff))
        sources = tuple(sorted(set(source_codes)))
        if not sources or not set(sources).issubset(ACCOUNT_SOURCES[self.account]):
            raise CaptureRuntimeError("capture_replay_source_inventory_invalid")
        last_seen_values: list[datetime] = []
        if frozenset(sources) == ACCOUNT_SOURCES[self.account]:
            for row in self.connection.execute(
                "SELECT last_seen_at_utc FROM capture_quarantine "
                "WHERE first_seen_at_utc>=? OR last_seen_at_utc>=?",
                (boundary, boundary),
            ):
                last_seen_values.append(
                    parse_utc(row[0], field="capture_quarantine_last_seen")
                )
        placeholders = ",".join("?" for _ in sources)
        for row in self.connection.execute(
            "SELECT last_seen_at_utc FROM capture_event_quarantine "
            "WHERE source_code IN ("
            + placeholders
            + ") AND (first_seen_at_utc>=? OR last_seen_at_utc>=?)",
            (*sources, boundary, boundary),
        ):
            last_seen_values.append(
                parse_utc(row[0], field="capture_quarantine_last_seen")
            )
        return max(last_seen_values) if last_seen_values else None

    def complete_replay_run(
        self, run_id: str, *, now: datetime | None = None
    ) -> tuple[int, str]:
        run = self.connection.execute(
            "SELECT * FROM capture_replay_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise CaptureRuntimeError("capture_replay_run_missing")
        rows = self.connection.execute(
            "SELECT account,source_code,message_id,revision_sha256,event_id,"
            "event_type,origin,content_type,event_time_utc,available_at_utc,"
            "capture_status,marker_sha256 FROM capture_replay_manifest_entries "
            "WHERE run_id=? ORDER BY source_code,message_id,revision_sha256,event_id",
            (run_id,),
        ).fetchall()
        manifest_hash = sha256(
            canonical_json(
                {
                    "schema": REPLAY_MANIFEST_SCHEMA,
                    "run_id": run_id,
                    "entries": [list(row) for row in rows],
                }
            )
        ).hexdigest()
        count = len(rows)
        if run["completed_at_utc"] is not None:
            if (
                int(run["manifest_count"]) != count
                or str(run["manifest_sha256"]) != manifest_hash
            ):
                raise CaptureRuntimeError("capture_replay_manifest_tampered")
            return count, manifest_hash
        sources = tuple(json.loads(str(run["source_inventory_json"])))
        for source in sources:
            status = self.backfill_status(str(source))
            source_manifest_count = self.replay_source_manifest_count(
                run_id, str(source)
            )
            if (
                status is None
                or status["status"] != "complete"
                or status["cutoff_utc"] != str(run["cutoff_utc"])
                or int(status["quarantined"]) != 0
                or int(status["attempted"])
                != int(status["accepted"])
                + int(status["duplicate"])
                + int(status["quarantined"])
                or source_manifest_count
                != int(status["accepted"]) + int(status["duplicate"])
            ):
                raise CaptureRuntimeError("capture_replay_source_incomplete")
        completed = now or utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            changed = self.connection.execute(
                "UPDATE capture_replay_runs SET completed_at_utc=?,"
                "manifest_count=?,manifest_sha256=? "
                "WHERE run_id=? AND completed_at_utc IS NULL",
                (utc_text(completed), count, manifest_hash, run_id),
            ).rowcount
            if changed != 1:
                raise CaptureRuntimeError("capture_replay_completion_race")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return count, manifest_hash

    @staticmethod
    def _evidence_summary(value: object, *, allow_zero: bool) -> tuple[int, str]:
        if not isinstance(value, Mapping) or set(value) != {"count", "digest"}:
            raise CaptureRuntimeError("capture_resolution_evidence_invalid")
        count = value.get("count")
        digest = value.get("digest")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < (0 if allow_zero else 1)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in HEX64 for character in digest)
        ):
            raise CaptureRuntimeError("capture_resolution_evidence_invalid")
        return count, digest

    def apply_quarantine_resolution_evidence(
        self,
        evidence_path: Path,
        *,
        expected_sha256: str,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Apply a value-free, independently audited resolution bundle.

        This method deliberately cannot derive terminal/archive/ACK/Store
        digests from the local replay manifest.  It accepts only a stable-file-
        descriptor read whose raw digest is supplied by the independent audit.
        Missing, extra, duplicate or stale targets keep the operation closed.
        """

        evidence, raw = _read_digest_bound_json(
            evidence_path, expected_sha256=expected_sha256
        )
        if set(evidence) != {
            "schema",
            "account",
            "generated_at_utc",
            "replay",
            "sources",
            "targets",
            "artifacts",
        } or evidence.get("schema") != QUARANTINE_RESOLUTION_EVIDENCE_SCHEMA:
            raise CaptureRuntimeError("capture_resolution_evidence_invalid")
        if evidence.get("account") != self.account:
            raise CaptureRuntimeError("capture_resolution_evidence_account_mismatch")
        evidence_generated_at = parse_utc(
            evidence.get("generated_at_utc"), field="resolution_evidence_time"
        )
        replay = evidence.get("replay")
        if not isinstance(replay, Mapping) or set(replay) != {
            "run_id",
            "release_sha",
            "cutoff_utc",
            "upper_bound_utc",
            "source_inventory",
            "manifest_count",
            "manifest_sha256",
        }:
            raise CaptureRuntimeError("capture_resolution_evidence_invalid")
        replay_run_id = str(replay.get("run_id") or "")
        run = self.connection.execute(
            "SELECT * FROM capture_replay_runs WHERE run_id=?", (replay_run_id,)
        ).fetchone()
        if run is None or run["completed_at_utc"] is None:
            raise CaptureRuntimeError("capture_quarantine_resolution_replay_incomplete")
        if evidence_generated_at < parse_utc(
            run["completed_at_utc"], field="capture_replay_completed_at"
        ):
            raise CaptureRuntimeError("capture_resolution_evidence_stale")
        try:
            inventory_list = json.loads(str(run["source_inventory_json"]))
        except json.JSONDecodeError as exc:
            raise CaptureRuntimeError("capture_replay_source_inventory_invalid") from exc
        if (
            replay.get("release_sha") != str(run["release_sha"])
            or replay.get("cutoff_utc") != str(run["cutoff_utc"])
            or replay.get("upper_bound_utc") != str(run["upper_bound_utc"])
            or replay.get("source_inventory") != inventory_list
            or replay.get("manifest_count") != int(run["manifest_count"])
            or replay.get("manifest_sha256") != str(run["manifest_sha256"])
            or int(run["manifest_count"]) < 1
        ):
            raise CaptureRuntimeError("capture_resolution_evidence_replay_mismatch")
        inventory = frozenset(str(item) for item in inventory_list)
        sources = evidence.get("sources")
        if not isinstance(sources, Mapping) or set(sources) != inventory:
            raise CaptureRuntimeError("capture_resolution_evidence_source_mismatch")

        manifest_rows = self.connection.execute(
            "SELECT source_code,event_id,message_id,revision_sha256 "
            "FROM capture_replay_manifest_entries WHERE run_id=? "
            "ORDER BY source_code,event_id",
            (replay_run_id,),
        ).fetchall()
        manifest_by_source: dict[str, list[tuple[str]]] = {
            source: [] for source in inventory
        }
        for row in manifest_rows:
            source = str(row["source_code"])
            if source not in manifest_by_source:
                raise CaptureRuntimeError("capture_resolution_evidence_source_mismatch")
            manifest_by_source[source].append((str(row["event_id"]),))

        manifest_total = terminal_total = archive_total = 0
        archive_material: list[tuple[str, str, str]] = []
        terminal_material: list[tuple[str, str, str]] = []
        for source in sorted(inventory):
            item = sources[source]
            if not isinstance(item, Mapping) or set(item) != {
                "backfill",
                "manifest_identity",
                "terminal_identity",
                "terminal_dispositions",
                "archive",
                "ack",
                "store",
            }:
                raise CaptureRuntimeError("capture_resolution_evidence_invalid")
            status = self.backfill_status(source)
            backfill = item.get("backfill")
            if status is None or not isinstance(backfill, Mapping) or set(backfill) != {
                "attempted",
                "accepted",
                "duplicate",
                "quarantined",
                "exhaustion",
            }:
                raise CaptureRuntimeError("capture_resolution_evidence_backfill_invalid")
            expected_backfill = {
                "attempted": int(status["attempted"]),
                "accepted": int(status["accepted"]),
                "duplicate": int(status["duplicate"]),
                "quarantined": int(status["quarantined"]),
                "exhaustion": str(status["exhaustion"]),
            }
            if (
                dict(backfill) != expected_backfill
                or status["status"] != "complete"
                or status["cutoff_utc"] != str(run["cutoff_utc"])
                or expected_backfill["exhaustion"]
                not in {"cutoff_crossed", "source_exhausted"}
                or expected_backfill["quarantined"] != 0
                or expected_backfill["attempted"]
                != expected_backfill["accepted"]
                + expected_backfill["duplicate"]
            ):
                raise CaptureRuntimeError("capture_resolution_evidence_backfill_invalid")
            local_manifest = value_free_set_digest(manifest_by_source[source])
            manifest_summary = self._evidence_summary(
                item.get("manifest_identity"), allow_zero=True
            )
            terminal_summary = self._evidence_summary(
                item.get("terminal_identity"), allow_zero=True
            )
            if (
                manifest_summary != local_manifest
                or terminal_summary != local_manifest
                or manifest_summary[0] != expected_backfill["attempted"]
            ):
                raise CaptureRuntimeError("capture_resolution_manifest_terminal_mismatch")
            dispositions = item.get("terminal_dispositions")
            if not isinstance(dispositions, Mapping) or set(dispositions) != {
                "count",
                "digest",
                "parsed",
                "filtered",
                "dispositions",
            }:
                raise CaptureRuntimeError("capture_resolution_terminal_invalid")
            disposition_count = dispositions.get("count")
            parsed = dispositions.get("parsed")
            filtered = dispositions.get("filtered")
            disposition_map = dispositions.get("dispositions")
            if (
                any(
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                    for value in (disposition_count, parsed, filtered)
                )
                or disposition_count != manifest_summary[0]
                or parsed + filtered != disposition_count
                or not isinstance(disposition_map, Mapping)
                or any(
                    not isinstance(code, str)
                    or not code.isupper()
                    or isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < 1
                    for code, count in disposition_map.items()
                )
                or sum(int(count) for count in disposition_map.values())
                != disposition_count
                or not isinstance(dispositions.get("digest"), str)
                or len(str(dispositions["digest"])) != 64
                or any(character not in HEX64 for character in str(dispositions["digest"]))
            ):
                raise CaptureRuntimeError("capture_resolution_terminal_invalid")
            archive = self._evidence_summary(item.get("archive"), allow_zero=True)
            ack = self._evidence_summary(item.get("ack"), allow_zero=True)
            store = self._evidence_summary(item.get("store"), allow_zero=True)
            if archive != ack or archive != store:
                raise CaptureRuntimeError("capture_resolution_downstream_mismatch")
            manifest_total += manifest_summary[0]
            terminal_total += terminal_summary[0]
            archive_total += archive[0]
            terminal_material.append((source, str(terminal_summary[0]), terminal_summary[1]))
            archive_material.append((source, str(archive[0]), archive[1]))

        if manifest_total != int(run["manifest_count"]) or terminal_total != manifest_total:
            raise CaptureRuntimeError("capture_resolution_manifest_terminal_mismatch")
        artifacts = evidence.get("artifacts")
        if not isinstance(artifacts, Mapping) or set(artifacts) != {
            "web_sha256",
            "bot_sha256",
            "verification_sha256",
        } or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in HEX64 for character in value)
            for value in artifacts.values()
        ) or len(set(artifacts.values())) != 3:
            raise CaptureRuntimeError("capture_resolution_artifact_binding_invalid")

        cutoff = parse_utc(run["cutoff_utc"], field="capture_replay_cutoff")
        upper = parse_utc(run["upper_bound_utc"], field="capture_replay_upper_bound")
        target_rows: dict[str, tuple[str, sqlite3.Row]] = {}
        for row in self.connection.execute(
            "SELECT * FROM capture_quarantine "
            "WHERE first_seen_at_utc>=? OR last_seen_at_utc>=?",
            (utc_text(cutoff), utc_text(cutoff)),
        ):
            fingerprint = quarantine_row_fingerprint(
                account=self.account,
                kind="legacy",
                marker_sha256=str(row["record_sha256"]),
                reason_code=str(row["reason_code"]),
                occurrences=int(row["occurrences"]),
                last_seen_at_utc=str(row["last_seen_at_utc"]),
            )
            if fingerprint in target_rows:
                raise CaptureRuntimeError("capture_resolution_target_set_mismatch")
            target_rows[fingerprint] = ("legacy", row)
        for row in self.connection.execute(
            "SELECT * FROM capture_event_quarantine "
            "WHERE first_seen_at_utc>=? OR last_seen_at_utc>=?",
            (utc_text(cutoff), utc_text(cutoff)),
        ):
            if str(row["account"]) != self.account:
                raise CaptureRuntimeError("capture_resolution_target_set_mismatch")
            source = str(row["source_code"])
            fingerprint = quarantine_row_fingerprint(
                account=self.account,
                kind="event",
                marker_sha256=str(row["marker_sha256"]),
                reason_code=str(row["reason_code"]),
                occurrences=int(row["occurrences"]),
                last_seen_at_utc=str(row["last_seen_at_utc"]),
                source_code=source,
                message_id=int(row["message_id"]),
                revision_sha256=str(row["revision_sha256"]),
            )
            if fingerprint in target_rows:
                raise CaptureRuntimeError("capture_resolution_target_set_mismatch")
            target_rows[fingerprint] = ("event", row)
        targets = evidence.get("targets")
        if (
            not isinstance(targets, list)
            or not targets
            or any(
                not isinstance(item, str)
                or len(item) != 64
                or any(character not in HEX64 for character in item)
                for item in targets
            )
            or len(targets) != len(set(targets))
            or set(targets) != set(target_rows)
        ):
            raise CaptureRuntimeError("capture_resolution_target_set_mismatch")
        if any(kind == "legacy" for kind, _ in target_rows.values()):
            # ``occurrences`` records repeated reconcile observations of the
            # same generic legacy marker; it is history, not unique-event
            # cardinality.  Legacy resolution is therefore bound to exhaustive
            # full-account replay and independent terminal/downstream equality,
            # never to an occurrences-derived minimum.
            if inventory != ACCOUNT_SOURCES[self.account]:
                raise CaptureRuntimeError(
                    "capture_quarantine_resolution_account_replay_required"
                )
        for kind, row in target_rows.values():
            last_seen = parse_utc(
                row["last_seen_at_utc"], field="capture_quarantine_last_seen"
            )
            if not cutoff <= last_seen <= upper:
                raise CaptureRuntimeError("capture_quarantine_resolution_bounds_invalid")
            if kind == "event":
                replayed = self.connection.execute(
                    "SELECT 1 FROM capture_replay_manifest_entries WHERE run_id=? "
                    "AND source_code=? AND message_id=? AND revision_sha256=? LIMIT 1",
                    (
                        replay_run_id,
                        str(row["source_code"]),
                        int(row["message_id"]),
                        str(row["revision_sha256"]),
                    ),
                ).fetchone()
                if replayed is None:
                    raise CaptureRuntimeError(
                        "capture_quarantine_resolution_revision_not_replayed"
                    )

        resolved_at = now or utc_now()
        if resolved_at.tzinfo is None or resolved_at.utcoffset() is None:
            raise CaptureRuntimeError("capture_quarantine_resolution_time_invalid")
        if resolved_at.astimezone(timezone.utc) < evidence_generated_at:
            raise CaptureRuntimeError("capture_resolution_evidence_future")
        manifest_identity_count, manifest_identity_sha256 = value_free_set_digest(
            (str(row["event_id"]),) for row in manifest_rows
        )
        _, terminal_sha256 = value_free_set_digest(terminal_material)
        terminal_count = terminal_total
        _, archive_sha256 = value_free_set_digest(archive_material)
        archive_count = archive_total
        evidence_json = raw.decode("utf-8")
        inserted = 0
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for fingerprint in sorted(target_rows):
                kind, row = target_rows[fingerprint]
                source = str(row["source_code"]) if kind == "event" else None
                message_id = int(row["message_id"]) if kind == "event" else None
                revision = str(row["revision_sha256"]) if kind == "event" else None
                marker = str(
                    row["marker_sha256"] if kind == "event" else row["record_sha256"]
                )
                document = {
                    "schema": QUARANTINE_RESOLUTION_SCHEMA,
                    "account": self.account,
                    "quarantine_kind": kind,
                    "marker_sha256": marker,
                    "reason_code": str(row["reason_code"]),
                    "source_code": source,
                    "message_id": message_id,
                    "revision_sha256": revision,
                    "observed_occurrences": int(row["occurrences"]),
                    "observed_last_seen_at_utc": str(row["last_seen_at_utc"]),
                    "quarantine_fingerprint": fingerprint,
                    "replay_run_id": replay_run_id,
                    "cutoff_utc": str(run["cutoff_utc"]),
                    "upper_bound_utc": str(run["upper_bound_utc"]),
                    "manifest_count": int(run["manifest_count"]),
                    "manifest_sha256": str(run["manifest_sha256"]),
                    "manifest_identity_count": manifest_identity_count,
                    "manifest_identity_sha256": manifest_identity_sha256,
                    "terminal_count": terminal_count,
                    "terminal_sha256": terminal_sha256,
                    "archive_count": archive_count,
                    "archive_sha256": archive_sha256,
                    "ack_count": archive_count,
                    "ack_sha256": archive_sha256,
                    "store_count": archive_count,
                    "store_sha256": archive_sha256,
                    "evidence_sha256": expected_sha256,
                    "web_artifact_sha256": str(artifacts["web_sha256"]),
                    "bot_artifact_sha256": str(artifacts["bot_sha256"]),
                    "verification_artifact_sha256": str(
                        artifacts["verification_sha256"]
                    ),
                }
                resolution_id = sha256(canonical_json(document)).hexdigest()
                existing = self.connection.execute(
                    "SELECT 1 FROM capture_quarantine_resolutions WHERE resolution_id=?",
                    (resolution_id,),
                ).fetchone()
                if existing is not None:
                    continue
                columns = (
                    "resolution_id,schema,account,quarantine_kind,marker_sha256,"
                    "reason_code,source_code,message_id,revision_sha256,"
                    "observed_occurrences,observed_last_seen_at_utc,"
                    "quarantine_fingerprint,replay_run_id,cutoff_utc,upper_bound_utc,"
                    "manifest_count,manifest_sha256,manifest_identity_count,"
                    "manifest_identity_sha256,terminal_count,terminal_sha256,"
                    "archive_count,archive_sha256,ack_count,ack_sha256,"
                    "store_count,store_sha256,evidence_sha256,"
                    "web_artifact_sha256,bot_artifact_sha256,"
                    "verification_artifact_sha256,evidence_json,resolved_at_utc"
                )
                values = (
                    resolution_id,
                    *document.values(),
                    evidence_json,
                    utc_text(resolved_at),
                )
                self.connection.execute(
                    f"INSERT INTO capture_quarantine_resolutions({columns}) "
                    f"VALUES({','.join('?' for _ in values)})",
                    values,
                )
                inserted += 1
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise CaptureRuntimeError("capture_quarantine_resolution_conflict") from exc
        except BaseException:
            self.connection.rollback()
            raise
        return {"resolved": len(target_rows), "inserted": inserted, "unresolved": 0}

    def resolve_replayed_quarantines(
        self, replay_run_id: str, *, now: datetime | None = None
    ) -> dict[str, int]:
        del replay_run_id, now
        raise CaptureRuntimeError("capture_quarantine_resolution_evidence_required")

    def note_context_filter(
        self,
        reason: object,
        *,
        source_code: str,
        now: datetime | None = None,
    ) -> None:
        """Persist a redacted, non-blocking diagnostic for reply-only context.

        Context ancestors can predate an explicitly requested backfill window.
        Their content and Telegram identifiers are deliberately not retained
        here.  A locally unusable ancestor must not make an otherwise valid,
        in-window child disappear or masquerade as an unresolved input event.
        """

        if source_code not in ACCOUNT_SOURCES[self.account]:
            raise CaptureRuntimeError("capture_context_source_invalid")
        moment = now or utc_now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise CaptureRuntimeError("capture_context_now_timezone_required")
        reason_code = _safe_reason(reason)
        expires = utc_text(moment + RAW_RETENTION)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                INSERT INTO capture_context_diagnostics(
                  source_code,reason_code,occurrences,first_seen_at_utc,
                  last_seen_at_utc,expires_at_utc
                ) VALUES(?,?,1,?,?,?)
                ON CONFLICT(source_code,reason_code) DO UPDATE SET
                  occurrences=capture_context_diagnostics.occurrences+1,
                  last_seen_at_utc=excluded.last_seen_at_utc,
                  expires_at_utc=excluded.expires_at_utc
                """,
                (
                    source_code,
                    reason_code,
                    utc_text(moment),
                    utc_text(moment),
                    expires,
                ),
            )
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
            # Quarantine rows are value-free evidence.  They are retained
            # permanently so an expiry sweep cannot masquerade as resolution.
            # A separately appended, exact-fingerprint resolution is the only
            # way the promotion audit may treat one as closed.
            quarantine = 0
            context_diagnostics = self.connection.execute(
                "DELETE FROM capture_context_diagnostics WHERE expires_at_utc < ?",
                (cutoff,),
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
                "context_diagnostics": int(context_diagnostics),
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
                "explicit_backfill": self.backfill_status(str(row["source_code"])),
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
    "QuarantineEventIdentity",
    "QUARANTINE_RESOLUTION_SCHEMA",
    "QUARANTINE_RESOLUTION_EVIDENCE_SCHEMA",
    "RAW_RETENTION",
    "REPLAY_MANIFEST_SCHEMA",
    "ROLE_ACCOUNT",
    "StageResult",
    "atomic_json",
    "load_fixture_events",
    "parse_utc",
    "process_fixture_events",
    "quarantine_row_fingerprint",
    "value_free_set_digest",
    "utc_now",
    "utc_text",
    "validate_ingress",
]
