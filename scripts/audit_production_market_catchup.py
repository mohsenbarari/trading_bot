#!/usr/bin/env python3
"""Value-free, read-only proof of PRIVATE_PRIMARY capture-to-model catch-up.

The command has three deliberately separate modes:

``web``
    Reads the Web host's capture spools/state, processor state/Market Store and
    PostgreSQL fact archive.  It emits only counters, timestamps, sequence
    watermarks and opaque set digests.

``bot``
    Reads the Bot host's receiver, adapter Market Store and estimator
    publication state.  It emits the same fact-set digests plus model-lineage
    counters, never Market values or Telegram content.

``verify``
    Compares the two value-free artifacts.  Optional previous artifacts turn
    the same check into a live-tail proof: a source which advanced at capture
    must be consumed, and any newly accepted parser output must reach the Bot.

Upstream Telegram message/time gaps and naturally sparse sources are not an
error.  Missing *internal* capture sequences, an unconsumed durable record, an
unaccounted parser disposition, an unresolved quarantine/rejection, or a fact
which did not reach the estimator's Market Store are fail-closed errors.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.capture_event_adapter import decode_capture_event
from core.market_intelligence.private_capture import (
    CaptureState,
    CaptureRuntimeError,
    QUARANTINE_RESOLUTION_SCHEMA,
    QUARANTINE_RESOLUTION_EVIDENCE_SCHEMA,
    REPLAY_MANIFEST_SCHEMA,
    quarantine_row_fingerprint,
    value_free_set_digest,
)
from core.market_intelligence.external_quote_capture import (
    RAW_RETENTION as EXTERNAL_CAPTURE_RETENTION,
    decode_quote_event,
)
from core.market_intelligence.market_fact_projection import (
    MarketFactProjectionError,
    observation_fact_semantics,
)
from core.market_intelligence.private_pipeline_contracts import (
    EstimatorSnapshotV2,
    content_hash,
    load_source_registry,
)


WEB_SCHEMA = "production_market_catchup_web/1.3"
BOT_SCHEMA = "production_market_catchup_bot/1.1"
VERIFICATION_SCHEMA = "production_market_catchup_verification/1.2"
SETTLE_SCHEMA = "production_market_catchup_settle/1.0"
CUTOFF_UTC = "2026-08-25T09:33:00Z"
BACKFILL_SOURCES = frozenset(
    {"MELTED_PRIMARY_FLOW", "GROUP_1", "GROUP_2"}
)
LIVE_CAPTURE_SOURCES = frozenset(
    {
        "MELTED_PRIMARY_FLOW",
        "MELTED_AGGREGATE",
        "MELTED_FLOW",
        "USD_HERAT",
        "XAUUSD",
        "GROUP_1",
        "GROUP_2",
        "WALLEX_PUBLIC_API",
        "BINANCE_PAXG_PUBLIC_API",
    }
)
ACCOUNT1_SOURCES = frozenset(
    {
        "MELTED_PRIMARY_FLOW",
        "MELTED_AGGREGATE",
        "MELTED_FLOW",
        "USD_HERAT",
        "XAUUSD",
    }
)
ACCOUNT2_SOURCES = frozenset({"GROUP_1", "GROUP_2"})
EXTERNAL_SOURCES = frozenset(
    {"WALLEX_PUBLIC_API", "BINANCE_PAXG_PUBLIC_API"}
)
CAPTURE_TO_FACT_SOURCE = {
    "MELTED_PRIMARY_FLOW": "PRIVATE_GOLD_CHANNEL",
    **{
        source: source
        for source in LIVE_CAPTURE_SOURCES
        if source != "MELTED_PRIMARY_FLOW"
    },
}
FACT_TO_CAPTURE_SOURCE = {
    fact: capture for capture, fact in CAPTURE_TO_FACT_SOURCE.items()
}
MODEL_INPUT_COMPONENTS = {
    "MELTED_PRIMARY_FLOW": "SOURCE_INPUT_MELTED_PRIMARY",
    "GROUP_1": "SOURCE_INPUT_GROUP_1",
    "GROUP_2": "SOURCE_INPUT_GROUP_2",
    "MELTED_AGGREGATE": "SOURCE_INPUT_MELTED_AGGREGATE",
    "MELTED_FLOW": "SOURCE_INPUT_MELTED_FLOW",
    "USD_HERAT": "SOURCE_INPUT_USD_HERAT",
    "XAUUSD": "SOURCE_INPUT_XAUUSD",
    "WALLEX_PUBLIC_API": "SOURCE_INPUT_WALLEX",
    "BINANCE_PAXG_PUBLIC_API": "SOURCE_INPUT_BINANCE_PAXG",
}
TERMINAL_LINEAGE_DISPOSITIONS = {
    "PARSED": frozenset(
        {"PARSER_EXECUTED", "FORWARDED_FILTERED", "EXTERNAL_MATERIALIZED"}
    ),
    "FILTERED": frozenset(
        {
            "DELETE_APPLIED",
            "DELETE_SUPPRESSED",
            "FORWARDED_UNSUPPORTED",
            "CURRENT_REVISION_UNCHANGED",
            "CURRENT_ROW_UNAVAILABLE_AT_PARSE",
            "OUTSIDE_ACTIVE_REPLAY_WINDOW",
            "NON_MODEL_MEDIA_ONLY",
            "NON_MODEL_SERVICE",
            "SUPERSEDED_BY_NEWER_REVISION",
        }
    ),
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_SOURCE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
SAFE_STREAM = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
MAX_HEALTH_AGE_SECONDS = 120
MIN_LIVE_TAIL_WINDOW_SECONDS = 20
MAX_LIVE_TAIL_WINDOW_SECONDS = 300
MAX_EVIDENCE_AGE_SECONDS = 300
MAX_EVIDENCE_PAIR_SKEW_SECONDS = 120
MAX_EVIDENCE_FUTURE_SKEW_SECONDS = 5
LEGACY_GROUP_ORIGIN_CONTRACTS = frozenset(
    {
        ("2.0", "3.0.0-docker"),
        ("2.1", "3.1.0-docker"),
    }
)


class CatchupAuditError(RuntimeError):
    """Stable, content-free refusal reason."""


def _fail(reason: str) -> None:
    raise CatchupAuditError(reason)


def _utc(value: object, *, reason: str) -> datetime:
    if not isinstance(value, str):
        _fail(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(reason)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(reason)
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def _set_digest(rows: Iterable[Sequence[object]]) -> tuple[int, str]:
    normalized = sorted(tuple(str(item) for item in row) for row in rows)
    return len(normalized), sha256(_canonical(normalized)).hexdigest()


def _read_json(path: Path, *, reason: str) -> Mapping[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
            _fail(reason)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(reason)
    if not isinstance(value, Mapping):
        _fail(reason)
    return value


def _write_exclusive(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _sqlite(path: Path) -> sqlite3.Connection:
    try:
        if path.is_symlink() or not path.is_file():
            _fail("sqlite_state_unavailable")
        connection = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=30
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            _fail("sqlite_integrity_failed")
        return connection
    except sqlite3.Error as exc:
        raise CatchupAuditError("sqlite_state_unreadable") from exc


def _tables(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    )


def _parse_runtime_binding(path: Path, *, release_sha: str) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
            _fail("runtime_env_unavailable")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        _fail("runtime_env_unreadable")
    selected: dict[str, str] = {}
    wanted = {
        "MARKET_PIPELINE_RELEASE_SHA",
        "MARKET_PIPELINE_FEED_MODE",
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY",
        "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC",
        "MARKET_CAPTURE_BACKFILL_SOURCE_CODES",
    }
    seen: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            _fail("runtime_env_invalid")
        key, value = line.split("=", 1)
        if key in seen:
            _fail("runtime_env_duplicate_key")
        seen.add(key)
        if key in wanted:
            selected[key] = value.strip()
    if set(selected) != wanted:
        _fail("runtime_env_binding_incomplete")
    try:
        configured_sources = frozenset(
            item.strip()
            for item in selected["MARKET_CAPTURE_BACKFILL_SOURCE_CODES"].split(",")
            if item.strip()
        )
    except AttributeError:
        _fail("runtime_env_backfill_sources_invalid")
    if (
        selected["MARKET_PIPELINE_RELEASE_SHA"] != release_sha
        or selected["MARKET_PIPELINE_FEED_MODE"] != "PRIVATE_PRIMARY"
        or selected["MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY"] != "1"
        or selected["MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC"] != CUTOFF_UTC
        or configured_sources != BACKFILL_SOURCES
    ):
        _fail("runtime_env_catchup_binding_invalid")
    # Archive ownership is a fixed, release-bound Compose invariant rather
    # than a value rendered into the role env.  The audit proves that
    # invariant from the concrete PostgreSQL archive and end-to-end lineage
    # below; requiring the same fixed value in the env would make every
    # canonical primary release unverifiable even while the running service
    # is correctly configured with archive writes enabled.
    public_binding = {
        "release_sha": release_sha,
        "feed_mode": "PRIVATE_PRIMARY",
        "backfill_cutoff_utc": CUTOFF_UTC,
        "backfill_sources": sorted(BACKFILL_SOURCES),
        "archive_enabled": True,
    }
    return {
        **public_binding,
        "binding_sha256": sha256(_canonical(public_binding)).hexdigest(),
    }


def _validate_health(
    path: Path,
    *,
    role: str,
    account: str | None,
    release_sha: str,
    expected_sources: frozenset[str],
    observed_at: datetime,
) -> Mapping[str, Any]:
    value = _read_json(path, reason="runtime_health_unreadable")
    if (
        value.get("role") != role
        or (account is not None and value.get("account") != account)
        or value.get("release_sha") != release_sha
        or value.get("mode") != "live"
    ):
        _fail("runtime_health_binding_invalid")
    status = value.get("status")
    if not isinstance(status, str) or status != "live-ready":
        _fail("runtime_health_not_ready")
    updated = _utc(value.get("updated_at_utc"), reason="runtime_health_time_invalid")
    age = (observed_at - updated).total_seconds()
    if age < -5 or age > MAX_HEALTH_AGE_SECONDS:
        _fail("runtime_health_stale")
    sources = value.get("sources")
    if not isinstance(sources, Mapping) or set(sources) != set(expected_sources):
        _fail("runtime_health_source_inventory_invalid")
    if account is not None and (
        value.get("durable_write") is not True
        or isinstance(value.get("outbox"), bool)
        or not isinstance(value.get("outbox"), int)
        or int(value["outbox"]) != 0
    ):
        _fail("runtime_health_durability_invalid")
    return value


def _validate_processor_health(
    path: Path, *, release_sha: str, observed_at: datetime
) -> Mapping[str, Any]:
    value = _read_json(path, reason="processor_health_unreadable")
    if (
        value.get("role") != "market-processor"
        or value.get("release_sha") != release_sha
        or value.get("mode") != "live"
        or value.get("status") != "live-shadow-ready"
    ):
        _fail("processor_health_binding_invalid")
    updated = _utc(value.get("updated_at_utc"), reason="processor_health_time_invalid")
    age = (observed_at - updated).total_seconds()
    if age < -5 or age > MAX_HEALTH_AGE_SECONDS:
        _fail("processor_health_stale")
    sources = value.get("sources")
    if (
        not isinstance(sources, Mapping)
        or set(sources) != set(LIVE_CAPTURE_SOURCES)
        or any(status != "ready" for status in sources.values())
    ):
        _fail("processor_source_inventory_invalid")
    return value


@dataclass(frozen=True, slots=True)
class SpoolRecord:
    stream: str
    source: str
    sequence: int
    event_id: str
    event_time_utc: str | None
    available_at_utc: str
    explicit_backfill: bool
    message_id: int | None
    event_type: str | None
    file_name: str
    device: int
    inode: int
    end_offset: int
    external_event_key: str | None = None
    origin: str | None = None


def _is_relevant(event_time: str, available: str) -> bool:
    cutoff = _utc(CUTOFF_UTC, reason="cutoff_invalid")
    return (
        _utc(event_time, reason="spool_event_time_invalid") >= cutoff
        or _utc(available, reason="spool_available_time_invalid") >= cutoff
    )


def _scan_spool(directory: Path, *, stream: str) -> tuple[SpoolRecord, ...]:
    if directory.is_symlink() or not directory.is_dir():
        _fail("capture_spool_unavailable")
    records: list[SpoolRecord] = []
    seen_sequences: set[int] = set()
    for path in sorted(directory.glob("events-????-??-??.jsonl")):
        if path.is_symlink() or not path.is_file():
            _fail("capture_spool_file_invalid")
        info = path.stat()
        with path.open("rb") as handle:
            while True:
                raw = handle.readline(256 * 1024 + 2)
                if not raw:
                    break
                end = handle.tell()
                if not raw.endswith(b"\n"):
                    _fail("capture_spool_partial_tail")
                try:
                    document = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    _fail("capture_spool_record_invalid")
                if not isinstance(document, Mapping):
                    _fail("capture_spool_record_invalid")
                producer = document.get("producer")
                if not isinstance(producer, Mapping):
                    _fail("capture_spool_record_invalid")
                sequence = producer.get("capture_sequence")
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                    _fail("capture_spool_sequence_invalid")
                if sequence in seen_sequences:
                    _fail("capture_spool_sequence_duplicate")
                seen_sequences.add(sequence)
                external_key: str | None = None
                if stream == "external":
                    try:
                        event_id, observation = decode_quote_event(document)
                    except Exception as exc:
                        raise CatchupAuditError("capture_spool_contract_invalid") from exc
                    source = observation.source_code
                    event_time = str(observation.event_time_utc)
                    available = str(observation.available_at_utc)
                    external_key = observation.event_key.hex()
                    explicit = False
                    origin = "external"
                    message_id = None
                    event_type = None
                else:
                    try:
                        event = decode_capture_event(document, stream=stream)
                    except Exception as exc:
                        raise CatchupAuditError("capture_spool_contract_invalid") from exc
                    source = event.source_id
                    event_id = event.event_id
                    event_time = (
                        str(event.event_time_utc)
                        if event.event_time_utc is not None
                        else None
                    )
                    available = event.available_at_utc
                    explicit = (
                        producer.get("origin") == "explicit_backfill"
                        or producer.get("explicit_backfill") is True
                    )
                    origin = str(producer.get("origin") or "").strip()
                    if not origin:
                        message = document.get("message")
                        legacy_group = (
                            stream == "coin"
                            and document.get("schema") == "coin_group_event"
                            and producer.get("name") == "coin_group_capture"
                            and (
                                str(document.get("schema_version")),
                                str(producer.get("version")),
                            )
                            in LEGACY_GROUP_ORIGIN_CONTRACTS
                            and isinstance(message, Mapping)
                        )
                        if legacy_group:
                            # The exact v3.0/v3.1 group contracts predate
                            # producer.origin.  Their message-level
                            # is_backfill bit is the authenticated historical
                            # transport contract; deletes have no bit and were
                            # emitted only by the live handler.  No other
                            # schema/version pair receives this compatibility.
                            origin = (
                                "reconcile"
                                if message.get("is_backfill") is True
                                else "live"
                            )
                    if origin not in {"live", "reconcile", "explicit_backfill"}:
                        _fail("capture_spool_origin_invalid")
                    message_id = int(event.message_id)
                    event_type = str(event.event_type)
                if source not in LIVE_CAPTURE_SOURCES:
                    _fail("capture_spool_source_invalid")
                if not _is_relevant(event_time or available, available):
                    continue
                records.append(
                    SpoolRecord(
                        stream=stream,
                        source=source,
                        sequence=sequence,
                        event_id=str(event_id),
                        event_time_utc=event_time,
                        available_at_utc=available,
                        explicit_backfill=explicit,
                        message_id=message_id,
                        event_type=event_type,
                        file_name=path.name,
                        device=int(info.st_dev),
                        inode=int(info.st_ino),
                        end_offset=end,
                        external_event_key=external_key,
                        origin=origin,
                    )
                )
    return tuple(records)


def _capture_replay_manifest(
    connection: sqlite3.Connection, run: sqlite3.Row
) -> tuple[int, str, tuple[sqlite3.Row, ...]]:
    rows = tuple(
        connection.execute(
            "SELECT account,source_code,message_id,revision_sha256,event_id,"
            "event_type,origin,content_type,event_time_utc,available_at_utc,"
            "capture_status,marker_sha256 FROM capture_replay_manifest_entries "
            "WHERE run_id=? ORDER BY source_code,message_id,revision_sha256,event_id",
            (str(run["run_id"]),),
        ).fetchall()
    )
    material = json.dumps(
        {
            "schema": REPLAY_MANIFEST_SCHEMA,
            "run_id": str(run["run_id"]),
            "entries": [list(row) for row in rows],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(rows), sha256(material).hexdigest(), rows


def build_quarantine_resolution_evidence(
    *,
    account: str,
    replay_run: Mapping[str, object],
    manifest_entries: Sequence[Mapping[str, object]],
    backfill_statuses: Mapping[str, Mapping[str, object]],
    terminal_entries: Mapping[str, Sequence[Mapping[str, object]]],
    archive_rows: Mapping[str, Sequence[Sequence[object]]],
    ack_rows: Mapping[str, Sequence[Sequence[object]]],
    store_rows: Mapping[str, Sequence[Sequence[object]]],
    target_fingerprints: Sequence[str],
    artifacts: Mapping[str, str],
    generated_at: datetime,
) -> dict[str, object]:
    """Build resolution evidence only from independently inspected row sets.

    No downstream digest is accepted as input.  Each is recomputed from the
    concrete terminal/archive/ACK/Store rows supplied by the audit adapters.
    Missing, extra, duplicate or tampered rows fail before a bundle exists.
    """

    required_run = {
        "run_id",
        "release_sha",
        "cutoff_utc",
        "upper_bound_utc",
        "source_inventory",
        "manifest_count",
        "manifest_sha256",
    }
    if set(replay_run) != required_run or account not in {"account1", "account2"}:
        _fail("resolution_evidence_replay_invalid")
    inventory_value = replay_run.get("source_inventory")
    if (
        not isinstance(inventory_value, list)
        or inventory_value != sorted(set(inventory_value))
        or any(not isinstance(source, str) for source in inventory_value)
    ):
        _fail("resolution_evidence_inventory_invalid")
    inventory = frozenset(inventory_value)
    if any(set(values) != inventory for values in (
        backfill_statuses,
        terminal_entries,
        archive_rows,
        ack_rows,
        store_rows,
    )):
        _fail("resolution_evidence_inventory_invalid")
    manifest_columns = (
        "account",
        "source_code",
        "message_id",
        "revision_sha256",
        "event_id",
        "event_type",
        "origin",
        "content_type",
        "event_time_utc",
        "available_at_utc",
        "capture_status",
        "marker_sha256",
    )
    normalized_manifest: list[list[object]] = []
    manifest_by_source: dict[str, list[str]] = {source: [] for source in inventory}
    for entry in manifest_entries:
        if not isinstance(entry, Mapping) or not set(manifest_columns).issubset(entry):
            _fail("resolution_evidence_manifest_invalid")
        source = str(entry["source_code"])
        event_id = str(entry["event_id"])
        if source not in inventory or str(entry["account"]) != account:
            _fail("resolution_evidence_manifest_invalid")
        normalized_manifest.append([entry[column] for column in manifest_columns])
        manifest_by_source[source].append(event_id)
    normalized_manifest.sort(
        key=lambda row: (str(row[1]), int(row[2]), str(row[3]), str(row[4]))
    )
    manifest_document = {
        "schema": REPLAY_MANIFEST_SCHEMA,
        "run_id": str(replay_run["run_id"]),
        "entries": normalized_manifest,
    }
    if (
        len(normalized_manifest) != int(replay_run["manifest_count"])
        or sha256(
            json.dumps(
                manifest_document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        != str(replay_run["manifest_sha256"])
    ):
        _fail("resolution_evidence_manifest_tampered")

    sources: dict[str, object] = {}
    for source in sorted(inventory):
        manifest_ids = manifest_by_source[source]
        if len(manifest_ids) != len(set(manifest_ids)):
            _fail("resolution_evidence_manifest_duplicate")
        terminal = terminal_entries[source]
        terminal_ids: list[str] = []
        terminal_rows: list[tuple[str, str, str]] = []
        parsed = filtered = 0
        dispositions: dict[str, int] = {}
        for entry in terminal:
            if not isinstance(entry, Mapping) or set(entry) != {
                "event_id",
                "status",
                "disposition_code",
            }:
                _fail("resolution_evidence_terminal_invalid")
            event_id = str(entry["event_id"])
            status = str(entry["status"])
            disposition = str(entry["disposition_code"])
            if disposition not in TERMINAL_LINEAGE_DISPOSITIONS.get(
                status, frozenset()
            ):
                _fail("resolution_evidence_terminal_invalid")
            terminal_ids.append(event_id)
            terminal_rows.append((event_id, status, disposition))
            if status == "PARSED":
                parsed += 1
            else:
                filtered += 1
            dispositions[disposition] = dispositions.get(disposition, 0) + 1
        if len(terminal_ids) != len(set(terminal_ids)) or set(terminal_ids) != set(
            manifest_ids
        ):
            _fail("resolution_evidence_manifest_terminal_mismatch")
        status = backfill_statuses[source]
        if set(status) != {
            "attempted",
            "accepted",
            "duplicate",
            "quarantined",
            "exhaustion",
        }:
            _fail("resolution_evidence_backfill_invalid")
        attempted = status["attempted"]
        accepted = status["accepted"]
        duplicate = status["duplicate"]
        quarantined = status["quarantined"]
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (attempted, accepted, duplicate, quarantined)
            )
            or attempted != accepted + duplicate + quarantined
            or quarantined != 0
            or attempted != len(manifest_ids)
            or status["exhaustion"] not in {"cutoff_crossed", "source_exhausted"}
        ):
            _fail("resolution_evidence_backfill_invalid")
        manifest_summary = _lineage_summary((event_id,) for event_id in manifest_ids)
        terminal_identity = _lineage_summary((event_id,) for event_id in terminal_ids)
        terminal_detail = {
            **_lineage_summary(terminal_rows),
            "parsed": parsed,
            "filtered": filtered,
            "dispositions": dispositions,
        }
        stage_rows: dict[str, list[tuple[str, ...]]] = {}
        for stage, values in (
            ("archive", archive_rows[source]),
            ("ack", ack_rows[source]),
            ("store", store_rows[source]),
        ):
            normalized = [tuple(str(item) for item in row) for row in values]
            if len(normalized) != len(set(normalized)):
                _fail(f"resolution_evidence_{stage}_duplicate")
            stage_rows[stage] = normalized
        if (
            set(stage_rows["archive"]) != set(stage_rows["ack"])
            or set(stage_rows["archive"]) != set(stage_rows["store"])
        ):
            _fail("resolution_evidence_downstream_mismatch")
        sources[source] = {
            "backfill": dict(status),
            "manifest_identity": manifest_summary,
            "terminal_identity": terminal_identity,
            "terminal_dispositions": terminal_detail,
            "archive": _lineage_summary(stage_rows["archive"]),
            "ack": _lineage_summary(stage_rows["ack"]),
            "store": _lineage_summary(stage_rows["store"]),
        }
    if (
        not target_fingerprints
        or len(target_fingerprints) != len(set(target_fingerprints))
        or any(HEX64.fullmatch(value) is None for value in target_fingerprints)
        or set(artifacts)
        != {"web_sha256", "bot_sha256", "verification_sha256"}
        or any(HEX64.fullmatch(value) is None for value in artifacts.values())
        or len(set(artifacts.values())) != 3
    ):
        _fail("resolution_evidence_binding_invalid")
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        _fail("resolution_evidence_time_invalid")
    return {
        "schema": QUARANTINE_RESOLUTION_EVIDENCE_SCHEMA,
        "account": account,
        "generated_at_utc": _stamp(generated_at),
        "replay": dict(replay_run),
        "sources": sources,
        "targets": sorted(target_fingerprints),
        "artifacts": dict(artifacts),
    }


def _capture_quarantine_state(
    connection: sqlite3.Connection,
    *,
    expected_account: str,
    expected_sources: frozenset[str],
) -> dict[str, int]:
    required = {
        "capture_event_quarantine",
        "capture_replay_runs",
        "capture_replay_manifest_entries",
        "capture_quarantine_resolutions",
    }
    if not required.issubset(_tables(connection)):
        _fail("capture_quarantine_resolution_schema_missing")

    runs: dict[str, tuple[sqlite3.Row, int, str, tuple[sqlite3.Row, ...]]] = {}
    for run in connection.execute(
        "SELECT * FROM capture_replay_runs ORDER BY started_at_utc,run_id"
    ):
        run_id = str(run["run_id"])
        try:
            inventory = json.loads(str(run["source_inventory_json"]))
        except json.JSONDecodeError:
            _fail("capture_replay_inventory_invalid")
        if (
            str(run["schema"]) != REPLAY_MANIFEST_SCHEMA
            or str(run["account"]) != expected_account
            or not isinstance(inventory, list)
            or not inventory
            or inventory != sorted(set(inventory))
            or not set(inventory).issubset(expected_sources)
            or sha256(
                json.dumps(inventory, separators=(",", ":")).encode("ascii")
            ).hexdigest()
            != str(run["source_inventory_sha256"])
            or not HEX40.fullmatch(str(run["release_sha"]))
            or not HEX64.fullmatch(run_id)
        ):
            _fail("capture_replay_binding_invalid")
        cutoff = _utc(str(run["cutoff_utc"]), reason="capture_replay_cutoff_invalid")
        upper = _utc(
            str(run["upper_bound_utc"]), reason="capture_replay_upper_bound_invalid"
        )
        started = _utc(
            str(run["started_at_utc"]), reason="capture_replay_start_time_invalid"
        )
        if cutoff != _utc(CUTOFF_UTC, reason="cutoff_invalid") or upper < cutoff:
            _fail("capture_replay_bounds_invalid")
        count, digest, entries = _capture_replay_manifest(connection, run)
        if run["completed_at_utc"] is None:
            _fail("capture_replay_incomplete")
        completed = _utc(
            str(run["completed_at_utc"]),
            reason="capture_replay_complete_time_invalid",
        )
        if completed < started:
            _fail("capture_replay_time_order_invalid")
        if int(run["manifest_count"]) != count or str(run["manifest_sha256"]) != digest:
            _fail("capture_replay_manifest_tampered")
        for entry in entries:
            event_time = _utc(
                str(entry["event_time_utc"]),
                reason="capture_replay_event_time_invalid",
            )
            _utc(
                str(entry["available_at_utc"]),
                reason="capture_replay_available_time_invalid",
            )
            durable = connection.execute(
                "SELECT source_code,available_at_utc FROM capture_seen "
                "WHERE event_id=?",
                (str(entry["event_id"]),),
            ).fetchone()
            if (
                str(entry["account"]) != expected_account
                or str(entry["source_code"]) not in inventory
                or int(entry["message_id"]) < 1
                or not HEX64.fullmatch(str(entry["revision_sha256"]))
                or not HEX64.fullmatch(str(entry["marker_sha256"]))
                or not re.fullmatch(
                    r"[A-Za-z0-9._:-]{16,160}", str(entry["event_id"])
                )
                or str(entry["event_type"])
                not in {
                    "message_created",
                    "message_snapshot",
                    "message_edited",
                    "message_deleted",
                }
                or str(entry["origin"])
                not in {"live", "reconcile", "explicit_backfill"}
                or str(entry["content_type"])
                not in {"text", "caption", "media_only", "service", "deleted"}
                or str(entry["capture_status"]) not in {"accepted", "duplicate"}
                or not cutoff <= event_time <= upper
                or durable is None
                or str(durable["source_code"]) != str(entry["source_code"])
                or _utc(
                    str(durable["available_at_utc"]),
                    reason="capture_replay_durable_time_invalid",
                )
                != _utc(
                    str(entry["available_at_utc"]),
                    reason="capture_replay_available_time_invalid",
                )
            ):
                _fail("capture_replay_manifest_entry_invalid")
        runs[run_id] = (run, count, digest, entries)

    resolution_fingerprints: set[str] = set()
    for resolution in connection.execute(
        "SELECT * FROM capture_quarantine_resolutions ORDER BY resolved_at_utc,resolution_id"
    ):
        resolution_schema = str(resolution["schema"])
        if resolution_schema == "capture_quarantine_resolution/1.0":
            # Retain the append-only historical row, but never treat the old
            # self-asserted manifest-copy contract as authoritative.
            continue
        kind = str(resolution["quarantine_kind"])
        if (
            resolution_schema != QUARANTINE_RESOLUTION_SCHEMA
            or str(resolution["account"]) != expected_account
            or kind not in {"legacy", "event"}
        ):
            _fail("capture_quarantine_resolution_invalid")
        try:
            fingerprint = quarantine_row_fingerprint(
                account=expected_account,
                kind=kind,
                marker_sha256=str(resolution["marker_sha256"]),
                reason_code=str(resolution["reason_code"]),
                occurrences=int(resolution["observed_occurrences"]),
                last_seen_at_utc=str(resolution["observed_last_seen_at_utc"]),
                source_code=(
                    str(resolution["source_code"])
                    if resolution["source_code"] is not None
                    else None
                ),
                message_id=(
                    int(resolution["message_id"])
                    if resolution["message_id"] is not None
                    else None
                ),
                revision_sha256=(
                    str(resolution["revision_sha256"])
                    if resolution["revision_sha256"] is not None
                    else None
                ),
            )
        except (CaptureRuntimeError, TypeError, ValueError):
            _fail("capture_quarantine_resolution_invalid")
        if fingerprint != str(resolution["quarantine_fingerprint"]):
            _fail("capture_quarantine_resolution_fingerprint_invalid")
        run_value = runs.get(str(resolution["replay_run_id"]))
        if run_value is None:
            _fail("capture_quarantine_resolution_replay_invalid")
        run, manifest_count, manifest_digest, entries = run_value
        try:
            evidence_raw = str(resolution["evidence_json"]).encode("utf-8")
            evidence = json.loads(evidence_raw)
        except (TypeError, UnicodeEncodeError, json.JSONDecodeError):
            _fail("capture_quarantine_resolution_evidence_invalid")
        if (
            not isinstance(evidence, Mapping)
            or set(evidence)
            != {
                "schema",
                "account",
                "generated_at_utc",
                "replay",
                "sources",
                "targets",
                "artifacts",
            }
            or evidence.get("schema") != QUARANTINE_RESOLUTION_EVIDENCE_SCHEMA
            or evidence.get("account") != expected_account
            or sha256(evidence_raw).hexdigest()
            != str(resolution["evidence_sha256"])
        ):
            _fail("capture_quarantine_resolution_evidence_invalid")
        evidence_generated_at = _utc(
            evidence.get("generated_at_utc"),
            reason="capture_quarantine_resolution_evidence_time_invalid",
        )
        replay = evidence.get("replay")
        inventory = frozenset(json.loads(str(run["source_inventory_json"])))
        if evidence_generated_at < _utc(
            str(run["completed_at_utc"]),
            reason="capture_replay_complete_time_invalid",
        ):
            _fail("capture_quarantine_resolution_evidence_stale")
        if (
            not isinstance(replay, Mapping)
            or set(replay)
            != {
                "run_id",
                "release_sha",
                "cutoff_utc",
                "upper_bound_utc",
                "source_inventory",
                "manifest_count",
                "manifest_sha256",
            }
            or replay.get("run_id") != str(run["run_id"])
            or replay.get("release_sha") != str(run["release_sha"])
            or replay.get("cutoff_utc") != str(run["cutoff_utc"])
            or replay.get("upper_bound_utc") != str(run["upper_bound_utc"])
            or replay.get("source_inventory") != sorted(inventory)
            or replay.get("manifest_count") != manifest_count
            or replay.get("manifest_sha256") != manifest_digest
        ):
            _fail("capture_quarantine_resolution_evidence_invalid")
        targets = evidence.get("targets")
        if (
            not isinstance(targets, list)
            or len(targets) != len(set(targets))
            or fingerprint not in targets
            or any(not isinstance(item, str) or HEX64.fullmatch(item) is None for item in targets)
        ):
            _fail("capture_quarantine_resolution_target_set_invalid")
        sources = evidence.get("sources")
        if not isinstance(sources, Mapping) or set(sources) != inventory:
            _fail("capture_quarantine_resolution_evidence_invalid")
        terminal_material: list[tuple[str, str, str]] = []
        archive_material: list[tuple[str, str, str]] = []
        manifest_identity_rows: list[tuple[str]] = []
        terminal_count = archive_count = 0
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
                _fail("capture_quarantine_resolution_evidence_invalid")
            source_entries = [
                (str(entry["event_id"]),)
                for entry in entries
                if str(entry["source_code"]) == source
            ]
            manifest_identity_rows.extend(source_entries)
            local_manifest = value_free_set_digest(source_entries)
            manifest_identity = item.get("manifest_identity")
            terminal_identity = item.get("terminal_identity")
            if (
                not isinstance(manifest_identity, Mapping)
                or set(manifest_identity) != {"count", "digest"}
                or not isinstance(terminal_identity, Mapping)
                or set(terminal_identity) != {"count", "digest"}
                or (manifest_identity.get("count"), manifest_identity.get("digest"))
                != local_manifest
                or terminal_identity != manifest_identity
            ):
                _fail("capture_quarantine_resolution_manifest_terminal_mismatch")
            backfill = item.get("backfill")
            status = connection.execute(
                "SELECT attempted,accepted,duplicate,quarantined,exhaustion,status,cutoff_utc "
                "FROM capture_backfill_status WHERE source_code=?",
                (source,),
            ).fetchone()
            if (
                status is None
                or not isinstance(backfill, Mapping)
                or set(backfill)
                != {"attempted", "accepted", "duplicate", "quarantined", "exhaustion"}
                or dict(backfill)
                != {
                    "attempted": int(status["attempted"]),
                    "accepted": int(status["accepted"]),
                    "duplicate": int(status["duplicate"]),
                    "quarantined": int(status["quarantined"]),
                    "exhaustion": str(status["exhaustion"]),
                }
                or str(status["status"]) != "complete"
                or str(status["cutoff_utc"]) != str(run["cutoff_utc"])
                or int(status["quarantined"]) != 0
                or int(status["attempted"]) != local_manifest[0]
                or int(status["attempted"])
                != int(status["accepted"]) + int(status["duplicate"])
                or str(status["exhaustion"])
                not in {"cutoff_crossed", "source_exhausted"}
            ):
                _fail("capture_quarantine_resolution_backfill_invalid")
            terminal = item.get("terminal_dispositions")
            if not _valid_terminal_disposition_counts(terminal) or int(
                terminal["count"]
            ) != local_manifest[0]:
                _fail("capture_quarantine_resolution_terminal_invalid")
            for stage in ("archive", "ack", "store"):
                if not _valid_summary(item.get(stage)):
                    _fail("capture_quarantine_resolution_downstream_invalid")
            archive = item["archive"]
            if archive != item["ack"] or archive != item["store"]:
                _fail("capture_quarantine_resolution_downstream_invalid")
            terminal_count += local_manifest[0]
            archive_count += int(archive["count"])
            terminal_material.append((source, str(local_manifest[0]), local_manifest[1]))
            archive_material.append(
                (source, str(archive["count"]), str(archive["digest"]))
            )
        manifest_identity_count, manifest_identity_sha256 = value_free_set_digest(
            manifest_identity_rows
        )
        _, terminal_sha256 = value_free_set_digest(terminal_material)
        _, archive_sha256 = value_free_set_digest(archive_material)
        artifacts = evidence.get("artifacts")
        if (
            int(resolution["manifest_count"]) != manifest_count
            or str(resolution["manifest_sha256"]) != manifest_digest
            or int(resolution["manifest_identity_count"])
            != manifest_identity_count
            or str(resolution["manifest_identity_sha256"])
            != manifest_identity_sha256
            or int(resolution["terminal_count"]) != terminal_count
            or str(resolution["terminal_sha256"]) != terminal_sha256
            or int(resolution["archive_count"]) != archive_count
            or int(resolution["ack_count"]) != archive_count
            or int(resolution["store_count"]) != archive_count
            or str(resolution["archive_sha256"]) != archive_sha256
            or str(resolution["ack_sha256"]) != archive_sha256
            or str(resolution["store_sha256"]) != archive_sha256
            or not isinstance(artifacts, Mapping)
            or set(artifacts)
            != {"web_sha256", "bot_sha256", "verification_sha256"}
            or any(HEX64.fullmatch(str(value)) is None for value in artifacts.values())
            or len(set(artifacts.values())) != 3
            or str(resolution["web_artifact_sha256"]) != artifacts["web_sha256"]
            or str(resolution["bot_artifact_sha256"]) != artifacts["bot_sha256"]
            or str(resolution["verification_artifact_sha256"])
            != artifacts["verification_sha256"]
            or str(resolution["cutoff_utc"]) != str(run["cutoff_utc"])
            or str(resolution["upper_bound_utc"]) != str(run["upper_bound_utc"])
        ):
            _fail("capture_quarantine_resolution_lineage_invalid")
        observed_last_seen = _utc(
            str(resolution["observed_last_seen_at_utc"]),
            reason="capture_quarantine_resolution_time_invalid",
        )
        run_cutoff = _utc(
            str(run["cutoff_utc"]), reason="capture_replay_cutoff_invalid"
        )
        run_upper = _utc(
            str(run["upper_bound_utc"]),
            reason="capture_replay_upper_bound_invalid",
        )
        run_started = _utc(
            str(run["started_at_utc"]),
            reason="capture_replay_start_time_invalid",
        )
        if not run_cutoff <= observed_last_seen <= run_upper or run_started < observed_last_seen:
            _fail("capture_quarantine_resolution_bounds_invalid")
        if kind == "legacy":
            if (
                resolution["source_code"] is not None
                or resolution["message_id"] is not None
                or resolution["revision_sha256"] is not None
                or inventory != expected_sources
            ):
                _fail("capture_quarantine_resolution_account_replay_invalid")
        elif not any(
            str(entry["source_code"]) == str(resolution["source_code"])
            and int(entry["message_id"]) == int(resolution["message_id"])
            and str(entry["revision_sha256"])
            == str(resolution["revision_sha256"])
            for entry in entries
        ):
            _fail("capture_quarantine_resolution_lineage_invalid")
        identity = {
            key: resolution[key]
            for key in (
                "schema",
                "account",
                "quarantine_kind",
                "marker_sha256",
                "reason_code",
                "source_code",
                "message_id",
                "revision_sha256",
                "observed_occurrences",
                "observed_last_seen_at_utc",
                "quarantine_fingerprint",
                "replay_run_id",
                "cutoff_utc",
                "upper_bound_utc",
                "manifest_count",
                "manifest_sha256",
                "manifest_identity_count",
                "manifest_identity_sha256",
                "terminal_count",
                "terminal_sha256",
                "archive_count",
                "archive_sha256",
                "ack_count",
                "ack_sha256",
                "store_count",
                "store_sha256",
                "evidence_sha256",
                "web_artifact_sha256",
                "bot_artifact_sha256",
                "verification_artifact_sha256",
            )
        }
        resolution_id = sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if resolution_id != str(resolution["resolution_id"]):
            _fail("capture_quarantine_resolution_id_invalid")
        resolved_at = _utc(
            str(resolution["resolved_at_utc"]),
            reason="capture_quarantine_resolution_time_invalid",
        )
        if resolved_at < evidence_generated_at:
            _fail("capture_quarantine_resolution_evidence_future")
        resolution_fingerprints.add(fingerprint)

    unresolved = {source: 0 for source in expected_sources}
    # Legacy rows are account-bound because their mutable source column is not
    # an oracle.  Only a completed full-account replay can resolve their exact
    # marker/reason/occurrence/last-seen fingerprint.
    for row in connection.execute(
        "SELECT * FROM capture_quarantine "
        "WHERE first_seen_at_utc>=? OR last_seen_at_utc>=?",
        (CUTOFF_UTC, CUTOFF_UTC),
    ):
        try:
            fingerprint = quarantine_row_fingerprint(
                account=expected_account,
                kind="legacy",
                marker_sha256=str(row["record_sha256"]),
                reason_code=str(row["reason_code"]),
                occurrences=int(row["occurrences"]),
                last_seen_at_utc=str(row["last_seen_at_utc"]),
            )
        except (CaptureRuntimeError, TypeError, ValueError):
            _fail("capture_quarantine_legacy_binding_invalid")
        if fingerprint in resolution_fingerprints:
            continue
        source = str(row["source_code"] or "")
        if source not in unresolved:
            _fail("capture_quarantine_unscoped_unrecoverable")
        unresolved[source] += int(row["occurrences"])
    for row in connection.execute(
        "SELECT * FROM capture_event_quarantine "
        "WHERE first_seen_at_utc>=? OR last_seen_at_utc>=?",
        (CUTOFF_UTC, CUTOFF_UTC),
    ):
        source = str(row["source_code"])
        if source not in unresolved or str(row["account"]) != expected_account:
            _fail("capture_event_quarantine_binding_invalid")
        try:
            fingerprint = quarantine_row_fingerprint(
                account=expected_account,
                kind="event",
                marker_sha256=str(row["marker_sha256"]),
                reason_code=str(row["reason_code"]),
                occurrences=int(row["occurrences"]),
                last_seen_at_utc=str(row["last_seen_at_utc"]),
                source_code=source,
                message_id=int(row["message_id"]),
                revision_sha256=str(row["revision_sha256"]),
            )
        except (CaptureRuntimeError, TypeError, ValueError):
            _fail("capture_event_quarantine_binding_invalid")
        if fingerprint not in resolution_fingerprints:
            unresolved[source] += int(row["occurrences"])
    return unresolved


def _capture_state(
    path: Path,
    *,
    expected_account: str,
    expected_sources: frozenset[str],
) -> tuple[int, set[tuple[str, int, str]], dict[str, Mapping[str, object]], dict[str, int]]:
    connection = _sqlite(path)
    try:
        metadata = connection.execute(
            "SELECT schema_version,account FROM capture_metadata WHERE singleton=1"
        ).fetchone()
        if metadata is None or str(metadata["account"]) != expected_account:
            _fail("capture_state_account_invalid")
        head_row = connection.execute(
            "SELECT value FROM capture_kv WHERE key='capture_sequence'"
        ).fetchone()
        head = int(head_row[0]) if head_row else 0
        if int(connection.execute("SELECT COUNT(*) FROM capture_outbox").fetchone()[0]):
            _fail("capture_outbox_not_drained")
        seen = {
            (str(row["event_id"]), int(row["capture_sequence"]), str(row["source_code"]))
            for row in connection.execute(
                "SELECT event_id,capture_sequence,source_code FROM capture_seen "
                "WHERE available_at_utc>=?",
                (CUTOFF_UTC,),
            )
        }
        statuses: dict[str, Mapping[str, object]] = {}
        if "capture_backfill_status" not in _tables(connection):
            _fail("capture_backfill_status_missing")
        for source in sorted(BACKFILL_SOURCES & expected_sources):
            row = connection.execute(
                "SELECT * FROM capture_backfill_status WHERE source_code=?",
                (source,),
            ).fetchone()
            if row is None:
                _fail("capture_backfill_status_missing")
            value = dict(row)
            attempted = int(value["attempted"])
            accepted = int(value["accepted"])
            duplicate = int(value["duplicate"])
            quarantined = int(value["quarantined"])
            if (
                value.get("status") != "complete"
                or _utc(
                    str(value.get("cutoff_utc")),
                    reason="capture_backfill_cutoff_invalid",
                )
                != _utc(CUTOFF_UTC, reason="cutoff_invalid")
                or value.get("exhaustion") not in {"cutoff_crossed", "source_exhausted"}
                or attempted != accepted + duplicate + quarantined
                or quarantined != 0
            ):
                _fail("capture_backfill_incomplete_or_unaccounted")
            statuses[source] = {
                "status": "complete",
                "cutoff_utc": CUTOFF_UTC,
                "attempted": attempted,
                "accepted": accepted,
                "duplicate": duplicate,
                "quarantined": quarantined,
                "exhaustion": str(value["exhaustion"]),
                "started_at_utc": str(value["started_at_utc"]),
            }
        metric_sources = {
            str(row["source_code"])
            for row in connection.execute(
                "SELECT source_code FROM capture_source_metrics"
            )
        }
        if metric_sources != set(expected_sources):
            _fail("capture_source_inventory_invalid")
        # Resolutions are append-only and exact-fingerprint bound.  Any later
        # recurrence changes the row fingerprint and automatically reopens the
        # blocker.  Legacy generic markers remain unrecoverable by design.
        quarantine_counts = _capture_quarantine_state(
            connection,
            expected_account=expected_account,
            expected_sources=expected_sources,
        )
        if any(quarantine_counts.values()):
            _fail("capture_quarantine_unresolved")
        return head, seen, statuses, quarantine_counts
    except (sqlite3.Error, TypeError, ValueError, CaptureRuntimeError) as exc:
        raise CatchupAuditError("capture_state_invalid") from exc
    finally:
        connection.close()


def _external_state(path: Path) -> tuple[int, set[tuple[str, int]]]:
    connection = _sqlite(path)
    try:
        head = int(
            connection.execute(
                "SELECT sequence FROM external_capture_metadata WHERE singleton=1"
            ).fetchone()[0]
        )
        if int(
            connection.execute("SELECT COUNT(*) FROM external_capture_outbox").fetchone()[0]
        ):
            _fail("external_capture_outbox_not_drained")
        cutoff_expiry = _stamp(
            _utc(CUTOFF_UTC, reason="cutoff_invalid") + EXTERNAL_CAPTURE_RETENTION
        )
        seen = {
            (str(row["event_id"]), int(row["sequence"]))
            for row in connection.execute(
                "SELECT event_id,sequence FROM external_capture_seen "
                "WHERE expires_at_utc>=?",
                (cutoff_expiry,),
            )
        }
        return head, seen
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise CatchupAuditError("external_capture_state_invalid") from exc
    finally:
        connection.close()


def _verify_capture_sequences(
    records: Sequence[SpoolRecord], *, head: int, seen: set[tuple[Any, ...]], external: bool
) -> None:
    if not records and not seen:
        return
    if not records or not seen:
        _fail("capture_durable_prefix_mismatch")
    sequences = sorted(record.sequence for record in records)
    durable_sequences = sorted(int(item[1]) for item in seen)
    if len(durable_sequences) != len(set(durable_sequences)):
        _fail("capture_durable_seen_sequence_duplicate")
    if sequences != durable_sequences:
        _fail("capture_durable_prefix_mismatch")
    if sequences != list(range(durable_sequences[0], head + 1)):
        _fail("internal_capture_sequence_gap")
    if sequences[-1] != head:
        _fail("capture_spool_head_mismatch")
    for record in records:
        key = (
            (record.event_id, record.sequence)
            if external
            else (record.event_id, record.sequence, record.source)
        )
        if key not in seen:
            _fail("capture_durable_seen_missing")


def _processor_consumption(
    staging_path: Path,
    market_path: Path,
    records: Sequence[SpoolRecord],
    *,
    observed_at: datetime | None = None,
    required_terminal_sources: frozenset[str] = frozenset(),
) -> tuple[
    dict[str, set[str]],
    dict[str, Mapping[str, object]],
    dict[str, Mapping[str, object]],
]:
    staging = _sqlite(staging_path)
    market = _sqlite(market_path)
    try:
        cursor_rows = staging.execute(
            "SELECT stream,file_path,device,inode,byte_offset FROM capture_file_cursors"
        ).fetchall()
        cursor_index: dict[tuple[str, str, int, int], int] = {}
        for row in cursor_rows:
            key = (
                str(row["stream"]),
                Path(str(row["file_path"])).name,
                int(row["device"]),
                int(row["inode"]),
            )
            if key in cursor_index:
                _fail("processor_cursor_ambiguous")
            cursor_index[key] = int(row["byte_offset"])
        for record in records:
            offset = cursor_index.get(
                (record.stream, record.file_name, record.device, record.inode)
            )
            if offset is None or offset < record.end_offset:
                _fail("processor_capture_not_consumed")

        if int(
            staging.execute(
                "SELECT COUNT(*) FROM capture_rejected_records "
                "WHERE last_seen_at_utc>=?",
                (CUTOFF_UTC,),
            ).fetchone()[0]
        ):
            _fail("processor_capture_rejection_unresolved")
        pending_market = int(
            staging.execute(
                "SELECT COUNT(*) FROM capture_dirty_market_messages"
            ).fetchone()[0]
        )
        pending_groups = int(
            staging.execute("SELECT COUNT(*) FROM capture_dirty_groups").fetchone()[0]
        )
        pending_reconciliation = int(
            staging.execute(
                "SELECT COUNT(*) FROM capture_projection_reconciliations "
                "WHERE completed_at_utc IS NULL"
            ).fetchone()[0]
        )
        if pending_market or pending_groups or pending_reconciliation:
            _fail("processor_projection_pending")

        seen_events = {
            str(row[0])
            for row in staging.execute(
                "SELECT event_id FROM capture_seen_events WHERE available_at_utc>=?",
                (CUTOFF_UTC,),
            )
        }
        consumed: dict[str, set[str]] = {source: set() for source in LIVE_CAPTURE_SOURCES}
        for record in records:
            if record.stream == "external":
                if record.external_event_key is None:
                    _fail("external_event_key_missing")
                present = market.execute(
                    "SELECT 1 FROM market_observations WHERE event_key=? "
                    "UNION ALL SELECT 1 FROM market_observations_archive WHERE event_key=? LIMIT 1",
                    (bytes.fromhex(record.external_event_key), bytes.fromhex(record.external_event_key)),
                ).fetchone()
                if present is None:
                    _fail("external_capture_not_materialized")
            elif record.event_id not in seen_events:
                _fail("processor_capture_event_missing")
            consumed[record.source].add(record.event_id)

        if "capture_explicit_backfill_lineage" not in _tables(staging):
            _fail("explicit_backfill_lineage_missing")
        if not {
            "capture_event_lineage",
            "capture_event_lineage_control",
        }.issubset(_tables(staging)):
            _fail("capture_event_lineage_missing")
        control = staging.execute(
            "SELECT enabled_at_utc FROM capture_event_lineage_control "
            "WHERE singleton=1"
        ).fetchone()
        if control is None:
            _fail("capture_event_lineage_control_missing")
        lineage_epoch = _utc(
            str(control["enabled_at_utc"]), reason="capture_event_lineage_epoch_invalid"
        )
        audit_time = (observed_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        if lineage_epoch > audit_time:
            _fail("capture_event_lineage_epoch_future")
        general_records = {
            record.event_id: record
            for record in records
            if record.stream in {"market", "coin"}
            and _utc(
                record.available_at_utc,
                reason="capture_event_lineage_available_time_invalid",
            )
            >= lineage_epoch
        }
        general_rows = {
            str(row["event_id"]): row
            for row in staging.execute(
                "SELECT event_id,stream,source_id,message_id,event_type,origin,"
                "event_time_utc,available_at_utc,status,disposition_code,"
                "terminal_at_utc FROM capture_event_lineage"
            )
            if str(row["event_id"]) in general_records
        }
        if set(general_rows) != set(general_records):
            _fail("capture_event_lineage_incomplete")
        for event_id, record in general_records.items():
            row = general_rows[event_id]
            status = str(row["status"])
            disposition = str(row["disposition_code"])
            if (
                status not in {"PARSED", "FILTERED"}
                or row["terminal_at_utc"] is None
                or str(row["stream"]) != record.stream
                or str(row["source_id"]) != record.source
                or int(row["message_id"]) != record.message_id
                or str(row["event_type"]) != record.event_type
                or str(row["origin"]) != record.origin
                or str(row["available_at_utc"]) != record.available_at_utc
                or (
                    str(row["event_time_utc"])
                    if row["event_time_utc"] is not None
                    else None
                )
                != record.event_time_utc
                or disposition
                not in TERMINAL_LINEAGE_DISPOSITIONS.get(status, frozenset())
            ):
                _fail("capture_event_lineage_not_terminal")
            terminal = _utc(
                str(row["terminal_at_utc"]),
                reason="capture_event_lineage_terminal_time_invalid",
            )
            if terminal < _utc(
                record.available_at_utc,
                reason="capture_event_lineage_available_time_invalid",
            ):
                _fail("capture_event_lineage_terminal_before_capture")
            if (
                terminal - audit_time
            ).total_seconds() > MAX_EVIDENCE_FUTURE_SKEW_SECONDS:
                _fail("capture_event_lineage_terminal_future")
        terminal_summary: dict[str, Mapping[str, object]] = {}
        for source in sorted(ACCOUNT1_SOURCES | ACCOUNT2_SOURCES):
            source_rows: list[tuple[str, ...]] = []
            counts = {"PARSED": 0, "FILTERED": 0}
            dispositions: dict[str, int] = {}
            for event_id, record in general_records.items():
                if record.source != source:
                    continue
                row = general_rows[event_id]
                status = str(row["status"])
                disposition = str(row["disposition_code"])
                counts[status] += 1
                dispositions[disposition] = dispositions.get(disposition, 0) + 1
                source_rows.append(
                    (
                        event_id,
                        record.stream,
                        record.source,
                        str(record.message_id),
                        record.event_type,
                        record.origin,
                        record.event_time_utc or "",
                        record.available_at_utc,
                        status,
                        disposition,
                    )
                )
            terminal_summary[source] = {
                **_lineage_summary(source_rows),
                "epoch_utc": _stamp(lineage_epoch),
                "parsed": counts["PARSED"],
                "filtered": counts["FILTERED"],
                "pending": 0,
                "dispositions": dispositions,
            }
            if source in required_terminal_sources and not source_rows:
                _fail("capture_event_lineage_source_missing")
        # External quotes have no text parser, but they do have an equivalent
        # durable terminal contract: an fsynced spool event present in
        # ``external_capture_seen`` must resolve to its exact derived event_key
        # in the hot or archived Market Store.  Record that materialization as
        # an explicit terminal disposition rather than treating a generic
        # non-zero observation count as lineage.
        for source in sorted(EXTERNAL_SOURCES):
            source_rows: list[tuple[str, ...]] = []
            for record in records:
                if record.stream != "external" or record.source != source:
                    continue
                if record.external_event_key is None:
                    _fail("external_event_key_missing")
                event_key = bytes.fromhex(record.external_event_key)
                materialized = market.execute(
                    "SELECT 1 FROM market_observations WHERE event_key=? "
                    "UNION ALL SELECT 1 FROM market_observations_archive "
                    "WHERE event_key=? LIMIT 1",
                    (event_key, event_key),
                ).fetchone()
                if materialized is None:
                    _fail("external_capture_not_materialized")
                source_rows.append(
                    (
                        record.event_id,
                        record.source,
                        str(record.sequence),
                        record.event_time_utc or "",
                        record.available_at_utc,
                        record.external_event_key,
                        "PARSED",
                        "EXTERNAL_MATERIALIZED",
                    )
                )
            terminal_summary[source] = {
                **_lineage_summary(source_rows),
                "epoch_utc": CUTOFF_UTC,
                "parsed": len(source_rows),
                "filtered": 0,
                "pending": 0,
                "dispositions": (
                    {"EXTERNAL_MATERIALIZED": len(source_rows)}
                    if source_rows
                    else {}
                ),
            }
            if source in required_terminal_sources and not source_rows:
                _fail("external_terminal_lineage_source_missing")
        explicit_records = {
            record.event_id: record for record in records if record.explicit_backfill
        }
        lineage_rows = staging.execute(
            "SELECT event_id,stream,source_id,message_id,event_type,event_time_utc,"
            "available_at_utc,status,disposition_code,terminal_at_utc "
            "FROM capture_explicit_backfill_lineage"
        ).fetchall()
        relevant_rows = {
            str(row["event_id"]): row
            for row in lineage_rows
            if str(row["event_id"]) in explicit_records
        }
        if set(relevant_rows) != set(explicit_records):
            _fail("explicit_backfill_lineage_incomplete")
        explicit_summary: dict[str, Mapping[str, object]] = {}
        for source in sorted(BACKFILL_SOURCES):
            source_rows: list[tuple[str, ...]] = []
            counts = {"PARSED": 0, "FILTERED": 0}
            dispositions: dict[str, int] = {}
            for event_id, record in explicit_records.items():
                if record.source != source:
                    continue
                row = relevant_rows[event_id]
                status = str(row["status"])
                disposition = str(row["disposition_code"])
                if (
                    status not in counts
                    or row["terminal_at_utc"] is None
                    or str(row["stream"]) != record.stream
                    or str(row["source_id"]) != record.source
                    or int(row["message_id"]) != record.message_id
                    or str(row["event_type"]) != record.event_type
                    or str(row["available_at_utc"]) != record.available_at_utc
                    or (
                        str(row["event_time_utc"])
                        if row["event_time_utc"] is not None
                        else None
                    )
                    != record.event_time_utc
                    or disposition
                    not in TERMINAL_LINEAGE_DISPOSITIONS.get(status, frozenset())
                ):
                    _fail("explicit_backfill_lineage_not_terminal")
                terminal = _utc(
                    str(row["terminal_at_utc"]),
                    reason="explicit_backfill_terminal_time_invalid",
                )
                if terminal < _utc(
                    record.available_at_utc,
                    reason="explicit_backfill_available_time_invalid",
                ):
                    _fail("explicit_backfill_terminal_before_capture")
                if (
                    terminal - audit_time
                ).total_seconds() > MAX_EVIDENCE_FUTURE_SKEW_SECONDS:
                    _fail("explicit_backfill_terminal_future")
                if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", disposition):
                    _fail("explicit_backfill_disposition_invalid")
                counts[status] += 1
                dispositions[disposition] = dispositions.get(disposition, 0) + 1
                source_rows.append(
                    (
                        event_id,
                        record.stream,
                        record.source,
                        str(record.message_id),
                        str(record.event_type),
                        record.event_time_utc or "",
                        record.available_at_utc,
                        status,
                        disposition,
                    )
                )
            explicit_summary[source] = {
                **_lineage_summary(source_rows),
                "parsed": counts["PARSED"],
                "filtered": counts["FILTERED"],
                "dispositions": dispositions,
                "pending": 0,
            }
        return consumed, explicit_summary, terminal_summary
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise CatchupAuditError("processor_state_invalid") from exc
    finally:
        market.close()
        staging.close()


def _fact_sources() -> frozenset[str]:
    registry = load_source_registry().by_code()
    expected = frozenset(CAPTURE_TO_FACT_SOURCE.values())
    if not expected.issubset(registry):
        _fail("source_registry_incomplete")
    if any(not registry[source].transfer_to_bot for source in expected):
        _fail("source_registry_transfer_disabled")
    return expected


def _local_export_lineage(
    market_path: Path,
) -> tuple[
    dict[str, list[tuple[str, ...]]],
    dict[str, list[tuple[str, ...]]],
    dict[str, dict[str, int]],
    dict[str, str | None],
]:
    connection = _sqlite(market_path)
    try:
        if not {
            "market_fact_export_ledger",
            "market_fact_export_semantics",
            "market_fact_export_history",
        }.issubset(_tables(connection)):
            _fail("market_fact_export_ledger_missing")
        output: dict[str, list[tuple[str, ...]]] = {
            source: [] for source in _fact_sources()
        }
        quality: dict[str, dict[str, int]] = {source: {} for source in _fact_sources()}
        latest: dict[str, str | None] = {source: None for source in _fact_sources()}
        observation_columns = (
            "o.id,o.event_key,o.source_code,o.source_family,o.event_time_utc,"
            "o.available_at_utc,o.tehran_datetime,o.tehran_date,o.tehran_minute,"
            "o.tehran_weekday,o.instrument,o.market_label,o.settlement_term,"
            "o.trade_form,o.event_type,o.side,o.price_value,o.price_num,"
            "o.price_unit,o.currency,o.quantity_value,o.quantity_num,"
            "o.quantity_unit,o.parse_confidence,o.parser_version,"
            "o.quality_state,o.quality_policy_version,o.is_conditional,"
            "o.attributes_json,o.inserted_at_utc"
        )
        rows: list[sqlite3.Row] = []
        parameters = (*sorted(_fact_sources()), CUTOFF_UTC, CUTOFF_UTC)
        for table in ("market_observations", "market_observations_archive"):
            rows.extend(
                connection.execute(
                    f"""
                    SELECT {observation_columns},
                           l.status AS export_status,l.fact_id,l.fact_revision,
                           l.reason_code,
                           l.observation_inserted_at_utc AS ledger_inserted_at_utc,
                           s.observation_inserted_at_utc AS semantics_inserted_at_utc,
                           s.fact_id AS semantics_fact_id,
                           s.fact_revision AS semantics_fact_revision,
                           s.source_sequence,s.delivery_sequence,
                           s.payload_hash AS semantics_payload_hash,
                           s.quality_state AS semantics_quality_state,
                           s.semantic_fingerprint,s.envelope_hash
                    FROM {table} o
                    LEFT JOIN market_fact_export_ledger l
                      ON l.event_key=o.event_key
                    LEFT JOIN market_fact_export_semantics s
                      ON s.event_key=o.event_key
                    WHERE o.source_code IN (?,?,?,?,?,?,?,?,?)
                      AND (o.event_time_utc>=? OR o.available_at_utc>=?)
                    """,
                    parameters,
                ).fetchall()
            )
        seen_keys: set[str] = set()
        for row in rows:
            event_key = bytes(row["event_key"]).hex()
            if event_key in seen_keys:
                _fail("processor_observation_duplicate")
            seen_keys.add(event_key)
            source = str(row["source_code"])
            state = str(row["quality_state"])
            quality[source][state] = quality[source].get(state, 0) + 1
            available = str(row["available_at_utc"])
            latest[source] = max(latest[source] or available, available)
            if row["export_status"] != "SUCCESS":
                _fail("processor_export_unresolved")
            fact_id = str(row["fact_id"] or "")
            revision = int(row["fact_revision"] or 0)
            if not HEX64.fullmatch(fact_id) or revision < 1:
                _fail("processor_export_identity_invalid")
            if (
                str(row["ledger_inserted_at_utc"] or "")
                != str(row["inserted_at_utc"])
                or str(row["semantics_inserted_at_utc"] or "")
                != str(row["inserted_at_utc"])
                or str(row["semantics_fact_id"] or "") != fact_id
                or int(row["semantics_fact_revision"] or 0) != revision
                or int(row["source_sequence"] or 0) < 1
            ):
                _fail("processor_export_ledger_stale")
            expected_payload, expected_quality, expected_fingerprint = (
                observation_fact_semantics(
                    connection,
                    row,
                    source_sequence=int(row["source_sequence"]),
                )
            )
            envelope_hash = str(row["envelope_hash"] or "")
            if (
                str(row["semantics_payload_hash"] or "") != expected_payload
                or str(row["semantics_quality_state"] or "") != expected_quality
                or str(row["semantic_fingerprint"] or "")
                != expected_fingerprint
                or HEX64.fullmatch(envelope_hash) is None
            ):
                _fail("processor_export_semantics_stale")
            current_history = connection.execute(
                """
                SELECT source_sequence,delivery_sequence,payload_hash,quality_state,
                       semantic_fingerprint,envelope_hash
                FROM market_fact_export_history
                WHERE fact_id=? AND fact_revision=? AND event_key=?
                """,
                (fact_id, revision, row["event_key"]),
            ).fetchone()
            if (
                current_history is None
                or int(current_history["source_sequence"])
                != int(row["source_sequence"])
                or int(current_history["delivery_sequence"])
                != int(row["delivery_sequence"])
                or str(current_history["payload_hash"]) != expected_payload
                or str(current_history["quality_state"]) != expected_quality
                or str(current_history["semantic_fingerprint"])
                != expected_fingerprint
                or str(current_history["envelope_hash"]) != envelope_hash
            ):
                _fail("processor_export_history_stale")
            output[source].append(
                (
                    fact_id,
                    source,
                    str(int(row["source_sequence"])),
                    str(revision),
                    expected_payload,
                    event_key,
                    str(int(row["delivery_sequence"])),
                    expected_quality,
                    envelope_hash,
                )
            )
        revision_output: dict[str, list[tuple[str, ...]]] = {
            source: [] for source in _fact_sources()
        }
        event_sources = {
            bytes(row["event_key"]): str(row["source_code"]) for row in rows
        }
        revisions_by_fact: dict[str, list[int]] = {}
        for history in connection.execute(
            "SELECT * FROM market_fact_export_history ORDER BY fact_id,fact_revision"
        ):
            event_key_bytes = bytes(history["event_key"])
            source = event_sources.get(event_key_bytes)
            if source is None:
                continue
            fact_id = str(history["fact_id"])
            revision = int(history["fact_revision"])
            source_sequence = int(history["source_sequence"])
            delivery_sequence = int(history["delivery_sequence"])
            payload_hash = str(history["payload_hash"])
            quality_state = str(history["quality_state"])
            fingerprint = str(history["semantic_fingerprint"])
            envelope_hash = str(history["envelope_hash"])
            if (
                HEX64.fullmatch(fact_id) is None
                or revision < 1
                or source_sequence < 1
                or delivery_sequence < 1
                or HEX64.fullmatch(payload_hash) is None
                or quality_state
                not in {"ELIGIBLE", "REVIEW", "REJECTED", "AUDIT_ONLY"}
                or HEX64.fullmatch(fingerprint) is None
                or HEX64.fullmatch(envelope_hash) is None
            ):
                _fail("processor_export_history_invalid")
            revisions_by_fact.setdefault(fact_id, []).append(revision)
            revision_output[source].append(
                (
                    fact_id,
                    source,
                    str(source_sequence),
                    str(revision),
                    payload_hash,
                    event_key_bytes.hex(),
                    str(delivery_sequence),
                    quality_state,
                    envelope_hash,
                )
            )
        for fact_id, revisions in revisions_by_fact.items():
            if revisions != list(range(1, max(revisions) + 1)):
                _fail("processor_export_history_gap")
        return output, revision_output, quality, latest
    except (sqlite3.Error, TypeError, ValueError, MarketFactProjectionError) as exc:
        raise CatchupAuditError("processor_export_state_invalid") from exc
    finally:
        connection.close()


def _command(arguments: Sequence[str]) -> str:
    completed = subprocess.run(
        list(arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        _fail("postgres_inspection_failed")
    return completed.stdout


def _postgres_facts(
    *, container: str, user: str, database: str
) -> tuple[
    dict[str, list[tuple[str, ...]]],
    dict[str, list[tuple[str, ...]]],
    dict[str, int],
    dict[str, int],
]:
    if not SAFE_CONTAINER.fullmatch(container):
        _fail("postgres_container_invalid")
    identity = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    if not identity.fullmatch(user) or not identity.fullmatch(database):
        _fail("postgres_identity_invalid")
    sources = ",".join(f"'{source}'" for source in sorted(_fact_sources()))
    sql = f"""
      SELECT encode(f.fact_id,'hex'),f.source_code,f.source_sequence,
             f.fact_revision,encode(f.payload_hash,'hex'),encode(f.event_key,'hex'),
             o.delivery_sequence,f.quality_state,encode(o.envelope_hash,'hex'),
             CASE WHEN o.acknowledged_at_utc IS NULL THEN 0 ELSE 1 END
      FROM market_data.market_facts f
      JOIN market_data.market_fact_outbox o
        ON o.fact_id=f.fact_id AND o.fact_revision=f.fact_revision
      WHERE f.source_code IN ({sources})
        AND (f.occurred_at_utc>=TIMESTAMPTZ '{CUTOFF_UTC}'
             OR f.available_at_utc>=TIMESTAMPTZ '{CUTOFF_UTC}')
      ORDER BY f.source_code,f.source_sequence;
    """
    raw = _command(
        [
            "docker", "exec", container, "psql", "-XAt", "-F", "\t",
            "-U", user, "-d", database, "-c", sql,
        ]
    )
    facts: dict[str, list[tuple[str, ...]]] = {source: [] for source in _fact_sources()}
    stream_heads: dict[str, int] = {}
    targeted_open = 0
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) != 10:
            _fail("postgres_fact_evidence_invalid")
        (
            fact_id, source, source_seq, revision, payload_hash, event_key,
            delivery, quality_state, envelope_hash, ack,
        ) = fields
        if (
            source not in facts
            or not all(
                HEX64.fullmatch(item)
                for item in (fact_id, payload_hash, event_key, envelope_hash)
            )
            or int(source_seq) < 1
            or int(revision) < 1
            or int(delivery) < 1
            or quality_state not in {"ELIGIBLE", "REVIEW", "REJECTED", "AUDIT_ONLY"}
            or ack not in {"0", "1"}
        ):
            _fail("postgres_fact_evidence_invalid")
        if ack != "1":
            targeted_open += 1
        stream = load_source_registry().by_code()[source].fact_stream_id
        stream_heads[stream] = max(stream_heads.get(stream, 0), int(delivery))
        facts[source].append(
            (
                fact_id, source, source_seq, revision, payload_hash, event_key,
                delivery, quality_state, envelope_hash,
            )
        )
    history_sql = f"""
      SELECT encode(f.fact_id,'hex'),f.source_code,f.source_sequence,
             r.fact_revision,encode(r.payload_hash,'hex'),encode(f.event_key,'hex'),
             o.delivery_sequence,r.quality_state,encode(o.envelope_hash,'hex'),
             CASE WHEN o.acknowledged_at_utc IS NULL THEN 0 ELSE 1 END,
             o.envelope->>'fact_id',o.envelope->>'fact_revision',
             o.envelope->>'payload_hash',o.envelope->>'source_code',
             o.envelope->>'source_sequence',o.envelope->>'event_key',
             o.envelope->>'quality_state',r.parser_version,
             o.envelope->>'parser_version',o.envelope->'payload'
      FROM market_data.market_facts f
      JOIN market_data.market_fact_revisions r ON r.fact_id=f.fact_id
      JOIN market_data.market_fact_outbox o
        ON o.fact_id=r.fact_id AND o.fact_revision=r.fact_revision
      WHERE f.source_code IN ({sources})
        AND (f.occurred_at_utc>=TIMESTAMPTZ '{CUTOFF_UTC}'
             OR f.available_at_utc>=TIMESTAMPTZ '{CUTOFF_UTC}')
      ORDER BY f.source_code,f.source_sequence,r.fact_revision;
    """
    history_raw = _command(
        [
            "docker", "exec", container, "psql", "-XAt", "-F", "\t",
            "-U", user, "-d", database, "-c", history_sql,
        ]
    )
    revisions: dict[str, list[tuple[str, ...]]] = {
        source: [] for source in _fact_sources()
    }
    revisions_by_fact: dict[str, list[int]] = {}
    for line in history_raw.splitlines():
        fields = line.split("\t")
        if len(fields) != 20:
            _fail("postgres_fact_revision_evidence_invalid")
        (
            fact_id, source, source_seq, revision, payload_hash, event_key,
            delivery, quality_state, envelope_hash, ack, envelope_fact_id,
            envelope_revision, envelope_payload_hash, envelope_source,
            envelope_source_sequence, envelope_event_key, envelope_quality,
            revision_parser, envelope_parser, envelope_payload,
        ) = fields
        try:
            historical_payload = json.loads(envelope_payload)
        except (TypeError, json.JSONDecodeError):
            _fail("postgres_fact_revision_evidence_invalid")
        if (
            source not in revisions
            or not all(
                HEX64.fullmatch(item)
                for item in (fact_id, payload_hash, event_key, envelope_hash)
            )
            or int(source_seq) < 1
            or int(revision) < 1
            or int(delivery) < 1
            or quality_state not in {"ELIGIBLE", "REVIEW", "REJECTED", "AUDIT_ONLY"}
            or ack != "1"
            or envelope_fact_id != fact_id
            or int(envelope_revision) != int(revision)
            or envelope_payload_hash != payload_hash
            or envelope_source != source
            or int(envelope_source_sequence) != int(source_seq)
            or envelope_event_key != event_key
            or envelope_quality != quality_state
            or envelope_parser != revision_parser
            or content_hash(historical_payload) != payload_hash
        ):
            _fail("postgres_fact_revision_evidence_invalid")
        revisions_by_fact.setdefault(fact_id, []).append(int(revision))
        stream = load_source_registry().by_code()[source].fact_stream_id
        stream_heads[stream] = max(stream_heads.get(stream, 0), int(delivery))
        revisions[source].append(
            (
                fact_id, source, source_seq, revision, payload_hash, event_key,
                delivery, quality_state, envelope_hash,
            )
        )
    for fact_id, fact_revisions in revisions_by_fact.items():
        if fact_revisions != list(range(1, max(fact_revisions) + 1)):
            _fail("postgres_fact_revision_gap")
    checkpoint_sql = (
        "SELECT stream_id,highest_contiguous_sequence "
        "FROM market_data.market_fact_delivery_checkpoints ORDER BY stream_id;"
    )
    checkpoint_raw = _command(
        [
            "docker", "exec", container, "psql", "-XAt", "-F", "\t",
            "-U", user, "-d", database, "-c", checkpoint_sql,
        ]
    )
    checkpoints: dict[str, int] = {}
    for line in checkpoint_raw.splitlines():
        fields = line.split("\t")
        if len(fields) != 2 or not SAFE_STREAM.fullmatch(fields[0]) or int(fields[1]) < 0:
            _fail("postgres_checkpoint_evidence_invalid")
        checkpoints[fields[0]] = int(fields[1])
    if targeted_open:
        _fail("target_fact_outbox_unacknowledged")
    for stream, head in stream_heads.items():
        if checkpoints.get(stream, -1) < head:
            _fail("target_fact_checkpoint_behind")
    return facts, revisions, stream_heads, checkpoints


def _lineage_summary(rows: Sequence[tuple[str, ...]]) -> dict[str, object]:
    count, digest = _set_digest(rows)
    return {"count": count, "digest": digest}


def collect_web(
    *,
    release_sha: str,
    runtime_env: Path,
    account1_db: Path,
    account2_db: Path,
    external_db: Path,
    account1_spool: Path,
    account2_spool: Path,
    external_spool: Path,
    processor_staging: Path,
    processor_market: Path,
    account1_health: Path,
    account2_health: Path,
    external_health: Path,
    processor_health: Path,
    postgres_container: str,
    postgres_user: str,
    postgres_database: str,
    now: datetime | None = None,
) -> dict[str, object]:
    if not HEX40.fullmatch(release_sha):
        _fail("release_sha_invalid")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    binding = _parse_runtime_binding(runtime_env, release_sha=release_sha)
    h1 = _validate_health(
        account1_health,
        role="market-capture-account1",
        account="account1",
        release_sha=release_sha,
        expected_sources=ACCOUNT1_SOURCES,
        observed_at=observed_at,
    )
    h2 = _validate_health(
        account2_health,
        role="market-capture-account2",
        account="account2",
        release_sha=release_sha,
        expected_sources=ACCOUNT2_SOURCES,
        observed_at=observed_at,
    )
    he = _validate_health(
        external_health,
        role="market-capture-external",
        account=None,
        release_sha=release_sha,
        expected_sources=EXTERNAL_SOURCES,
        observed_at=observed_at,
    )
    for source in sorted(EXTERNAL_SOURCES):
        source_health = he["sources"][source]
        if (
            not isinstance(source_health, Mapping)
            or isinstance(source_health.get("success"), bool)
            or not isinstance(source_health.get("success"), int)
            or int(source_health["success"]) < 1
        ):
            _fail("external_capture_poll_unhealthy")
        available = _utc(
            source_health.get("last_available_at_utc"),
            reason="external_capture_time_invalid",
        )
        age = (observed_at - available).total_seconds()
        if age < -5 or age > MAX_HEALTH_AGE_SECONDS:
            _fail("external_capture_data_stale")
    _validate_processor_health(
        processor_health, release_sha=release_sha, observed_at=observed_at
    )

    records1 = _scan_spool(account1_spool, stream="market")
    records2 = _scan_spool(account2_spool, stream="coin")
    recordse = _scan_spool(external_spool, stream="external")
    head1, seen1, status1, quarantine1 = _capture_state(
        account1_db, expected_account="account1", expected_sources=ACCOUNT1_SOURCES
    )
    head2, seen2, status2, quarantine2 = _capture_state(
        account2_db, expected_account="account2", expected_sources=ACCOUNT2_SOURCES
    )
    heade, seene = _external_state(external_db)
    _verify_capture_sequences(records1, head=head1, seen=seen1, external=False)
    _verify_capture_sequences(records2, head=head2, seen=seen2, external=False)
    _verify_capture_sequences(recordse, head=heade, seen=seene, external=True)
    records = (*records1, *records2, *recordse)
    consumed, explicit_lineage, terminal_lineage = _processor_consumption(
        processor_staging,
        processor_market,
        records,
        observed_at=observed_at,
        required_terminal_sources=LIVE_CAPTURE_SOURCES,
    )
    (
        local_exports,
        local_revisions,
        quality,
        parsed_latest,
    ) = _local_export_lineage(processor_market)
    (
        postgres_facts,
        postgres_revisions,
        producer_heads,
        acknowledged_heads,
    ) = _postgres_facts(
        container=postgres_container, user=postgres_user, database=postgres_database
    )

    # Every local successful export must be the exact current archived fact.
    for fact_source in sorted(_fact_sources()):
        local = sorted(local_exports[fact_source])
        remote = sorted(postgres_facts[fact_source])
        if local != remote:
            _fail("processor_to_archive_lineage_mismatch")
        if sorted(local_revisions[fact_source]) != sorted(
            postgres_revisions[fact_source]
        ):
            _fail("processor_to_archive_revision_lineage_mismatch")

    statuses = {**status1, **status2}
    sources: dict[str, object] = {}
    health_documents = {
        **{source: h1["sources"][source] for source in ACCOUNT1_SOURCES},
        **{source: h2["sources"][source] for source in ACCOUNT2_SOURCES},
        **{source: he["sources"][source] for source in EXTERNAL_SOURCES},
    }
    for source in sorted(LIVE_CAPTURE_SOURCES):
        source_records = [record for record in records if record.source == source]
        source_terminal = terminal_lineage.get(source)
        terminal_required = 0
        if source_terminal is not None:
            terminal_epoch = _utc(
                source_terminal["epoch_utc"],
                reason="capture_event_lineage_epoch_invalid",
            )
            terminal_required = sum(
                _utc(
                    record.available_at_utc,
                    reason="capture_event_lineage_available_time_invalid",
                )
                >= terminal_epoch
                for record in source_records
            )
        captured_rows = [
            (
                record.source,
                record.sequence,
                record.event_id,
                record.event_time_utc or "",
                record.available_at_utc,
            )
            for record in source_records
        ]
        explicit_rows = [record for record in source_records if record.explicit_backfill]
        fact_source = CAPTURE_TO_FACT_SOURCE[source]
        sources[source] = {
            "capture": {
                **_lineage_summary(captured_rows),
                "head_sequence": max((record.sequence for record in source_records), default=0),
                "last_available_at_utc": max(
                    (record.available_at_utc for record in source_records), default=None
                ),
                "explicit_backfill_accepted": len(explicit_rows),
                "terminal_required": terminal_required,
                "observed": bool(source_records),
            },
            "processor": {
                **_lineage_summary((event_id,) for event_id in consumed[source]),
                "consumed": len(consumed[source]),
                "head_sequence": max(
                    (record.sequence for record in source_records), default=0
                ),
            },
            "parsed": {
                **_lineage_summary(local_exports[fact_source]),
                "quality_counts": quality[fact_source],
                "last_available_at_utc": parsed_latest[fact_source],
            },
            "archive": _lineage_summary(postgres_facts[fact_source]),
            "revision_history": _lineage_summary(
                postgres_revisions[fact_source]
            ),
            "configured": True,
            "health_observed": bool(health_documents[source]),
        }
        sources[source]["terminal_lineage"] = terminal_lineage[source]
        if source in BACKFILL_SOURCES:
            sources[source]["explicit_backfill_lineage"] = explicit_lineage[source]
        if len(consumed[source]) != len(source_records):
            _fail("capture_processor_count_mismatch")

    return {
        "schema": WEB_SCHEMA,
        "role": "web",
        "observed_at_utc": _stamp(observed_at),
        "release_sha": release_sha,
        "binding": binding,
        "source_inventory": sorted(LIVE_CAPTURE_SOURCES),
        "backfill": {source: statuses[source] for source in sorted(BACKFILL_SOURCES)},
        "sources": sources,
        "transport": {
            "producer_heads": producer_heads,
            "acknowledged_heads": acknowledged_heads,
        },
        "quarantine": {
            "account1": sum(quarantine1.values()),
            "account2": sum(quarantine2.values()),
            "backfill": sum(int(item["quarantined"]) for item in statuses.values()),
            "processor_rejected": 0,
            "export_rejected": 0,
        },
        "upstream_time_gaps_allowed": True,
        "secrets_disclosed": False,
    }


def _receiver_fact_rows(
    connection: sqlite3.Connection,
    projections: Mapping[str, sqlite3.Row],
) -> dict[str, list[tuple[str, ...]]]:
    """Bind durable receiver identities to adapter lineage without payloads.

    Receiver payloads are deliberately compacted after their retention window.
    Identity, revision, hash, delivery and adapter projection remain durable, so
    catch-up proof must not depend on retained raw payload JSON.
    """

    facts: dict[str, list[tuple[str, ...]]] = {source: [] for source in _fact_sources()}
    stream_sources: dict[str, str] = {}
    for definition in load_source_registry().sources:
        if definition.source_code not in facts:
            continue
        if definition.fact_stream_id in stream_sources:
            _fail("receiver_stream_source_ambiguous")
        stream_sources[definition.fact_stream_id] = definition.source_code
    for fact_id, projection in projections.items():
        stream = str(projection["stream_id"])
        source = stream_sources.get(stream)
        if source is None:
            continue
        occurred = str(projection["occurred_at_utc"])
        available = str(projection["available_at_utc"])
        if not _is_relevant(occurred, available):
            continue
        latest = connection.execute(
            "SELECT stream_id,source_sequence,fact_revision,payload_hash,"
            "payload_json,payload_compacted_at_utc "
            "FROM fact_latest WHERE fact_id=?",
            (fact_id,),
        ).fetchone()
        delivery = connection.execute(
            "SELECT delivery_sequence,payload_hash,payload_json,"
            "payload_compacted_at_utc FROM fact_deliveries "
            "WHERE fact_id=? AND fact_revision=?",
            (fact_id, int(projection["fact_revision"])),
        ).fetchone()
        if (
            latest is None
            or delivery is None
            or str(latest["stream_id"]) != stream
            or int(latest["source_sequence"]) != int(projection["source_sequence"])
            or int(latest["fact_revision"]) != int(projection["fact_revision"])
            or str(latest["payload_hash"]) != str(projection["payload_hash"])
            or str(delivery["payload_hash"]) != str(projection["payload_hash"])
        ):
            _fail("receiver_adapter_identity_mismatch")
        quality_state = str(projection["quality_state"] or "")
        envelope_hash = str(projection["envelope_hash"] or "")
        if (
            quality_state
            not in {"ELIGIBLE", "REVIEW", "REJECTED", "AUDIT_ONLY"}
            or HEX64.fullmatch(envelope_hash) is None
        ):
            _fail("receiver_adapter_semantics_missing")
        retained_payload = str(latest["payload_json"] or "") or str(
            delivery["payload_json"] or ""
        )
        if retained_payload:
            try:
                envelope = json.loads(retained_payload)
            except (TypeError, json.JSONDecodeError):
                _fail("receiver_adapter_envelope_invalid")
            if (
                not isinstance(envelope, Mapping)
                or envelope.get("fact_id") != fact_id
                or int(envelope.get("fact_revision") or 0)
                != int(projection["fact_revision"])
                or envelope.get("payload_hash") != str(projection["payload_hash"])
                or envelope.get("source_code") != source
                or envelope.get("event_key") != bytes(projection["event_key"]).hex()
                or envelope.get("quality_state") != quality_state
                or content_hash(envelope) != envelope_hash
            ):
                _fail("receiver_adapter_envelope_invalid")
        elif (
            latest["payload_compacted_at_utc"] is None
            or delivery["payload_compacted_at_utc"] is None
        ):
            _fail("receiver_adapter_payload_missing_without_compaction")
        event_key = bytes(projection["event_key"]).hex()
        if not HEX64.fullmatch(event_key):
            _fail("receiver_adapter_event_key_invalid")
        facts[source].append(
            (
                fact_id,
                source,
                str(int(projection["source_sequence"])),
                str(int(projection["fact_revision"])),
                str(projection["payload_hash"]),
                event_key,
                str(int(delivery["delivery_sequence"])),
                quality_state,
                envelope_hash,
            )
        )
    return facts


def _receiver_revision_rows(
    receiver: sqlite3.Connection,
    store: sqlite3.Connection,
) -> dict[str, list[tuple[str, ...]]]:
    """Prove every retained revision through receiver and adapter ledgers."""

    required = {
        "private_fact_adapter_deliveries",
        "private_fact_adapter_projection_revisions",
    }
    if not required.issubset(_tables(store)):
        _fail("adapter_revision_history_missing")
    facts: dict[str, list[tuple[str, ...]]] = {
        source: [] for source in _fact_sources()
    }
    stream_sources = {
        definition.fact_stream_id: definition.source_code
        for definition in load_source_registry().sources
        if definition.source_code in facts
    }
    rows = store.execute(
        "SELECT * FROM private_fact_adapter_projection_revisions "
        "WHERE occurred_at_utc>=? OR available_at_utc>=? "
        "ORDER BY stream_id,delivery_sequence",
        (CUTOFF_UTC, CUTOFF_UTC),
    ).fetchall()
    seen_receiver: set[tuple[str, int]] = set()
    revisions_by_fact: dict[str, list[int]] = {}
    for row in rows:
        stream = str(row["stream_id"])
        source = stream_sources.get(stream)
        if source is None:
            # The Store also carries legitimate derived/model-component
            # streams (currently the private-gold minute series).  They are
            # outside this capture-to-model promotion inventory, just as
            # ``_receiver_fact_rows`` already treats them, and must not be
            # misclassified as an invalid source.
            continue
        fact_id = str(row["fact_id"])
        revision = int(row["fact_revision"])
        delivery_sequence = int(row["delivery_sequence"])
        payload_hash = str(row["payload_hash"])
        quality_state = str(row["quality_state"] or "")
        envelope_hash = str(row["envelope_hash"] or "")
        event_key = bytes(row["event_key"]).hex()
        if (
            HEX64.fullmatch(fact_id) is None
            or HEX64.fullmatch(payload_hash) is None
            or HEX64.fullmatch(event_key) is None
            or revision < 1
            or int(row["source_sequence"]) < 1
            or delivery_sequence < 1
            or quality_state
            not in {"ELIGIBLE", "REVIEW", "REJECTED", "AUDIT_ONLY"}
            or HEX64.fullmatch(envelope_hash) is None
            or str(row["status"])
            not in {"APPLIED", "AUDIT_ONLY", "REJECTED"}
        ):
            _fail("adapter_revision_history_invalid")
        delivery = receiver.execute(
            "SELECT fact_id,fact_revision,payload_hash,payload_json,"
            "payload_compacted_at_utc FROM fact_deliveries "
            "WHERE stream_id=? AND delivery_sequence=?",
            (stream, delivery_sequence),
        ).fetchone()
        adapter_delivery = store.execute(
            "SELECT fact_id,fact_revision,payload_hash,status "
            "FROM private_fact_adapter_deliveries "
            "WHERE stream_id=? AND delivery_sequence=?",
            (stream, delivery_sequence),
        ).fetchone()
        if (
            delivery is None
            or adapter_delivery is None
            or str(delivery["fact_id"]) != fact_id
            or int(delivery["fact_revision"]) != revision
            or str(delivery["payload_hash"]) != payload_hash
            or str(adapter_delivery["fact_id"]) != fact_id
            or int(adapter_delivery["fact_revision"]) != revision
            or str(adapter_delivery["payload_hash"]) != payload_hash
            or str(adapter_delivery["status"]) != str(row["status"])
        ):
            _fail("receiver_adapter_revision_mismatch")
        retained_payload = str(delivery["payload_json"] or "")
        if retained_payload:
            try:
                envelope = json.loads(retained_payload)
            except (TypeError, json.JSONDecodeError):
                _fail("receiver_revision_envelope_invalid")
            if (
                not isinstance(envelope, Mapping)
                or envelope.get("fact_id") != fact_id
                or int(envelope.get("fact_revision") or 0) != revision
                or envelope.get("payload_hash") != payload_hash
                or envelope.get("source_code") != source
                or int(envelope.get("source_sequence") or 0)
                != int(row["source_sequence"])
                or envelope.get("event_key") != event_key
                or envelope.get("quality_state") != quality_state
                or content_hash(envelope) != envelope_hash
            ):
                _fail("receiver_revision_envelope_invalid")
        elif delivery["payload_compacted_at_utc"] is None:
            _fail("receiver_revision_payload_missing_without_compaction")
        key = (stream, delivery_sequence)
        if key in seen_receiver:
            _fail("receiver_revision_duplicate")
        seen_receiver.add(key)
        revisions_by_fact.setdefault(fact_id, []).append(revision)
        facts[source].append(
            (
                fact_id,
                source,
                str(int(row["source_sequence"])),
                str(revision),
                payload_hash,
                event_key,
                str(delivery_sequence),
                quality_state,
                envelope_hash,
            )
        )
    for fact_id, revisions in revisions_by_fact.items():
        if revisions != list(range(1, max(revisions) + 1)):
            _fail("receiver_adapter_revision_gap")
    return facts


def _index_snapshot_inputs(snapshot: EstimatorSnapshotV2) -> dict[str, object]:
    """Index unique inputs while allowing legitimate non-source components."""

    components = [trace.component for trace in snapshot.inputs]
    if len(components) != len(set(components)):
        _fail("estimator_input_component_duplicate")
    indexed = {trace.component: trace for trace in snapshot.inputs}
    if not set(MODEL_INPUT_COMPONENTS.values()).issubset(indexed):
        _fail("estimator_source_input_inventory_incomplete")
    return indexed


def collect_bot(
    *,
    release_sha: str,
    receiver_db: Path,
    market_store_db: Path,
    estimator_state_db: Path,
    snapshot_path: Path,
    now: datetime | None = None,
) -> dict[str, object]:
    if not HEX40.fullmatch(release_sha):
        _fail("release_sha_invalid")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    receiver = _sqlite(receiver_db)
    store = _sqlite(market_store_db)
    estimator = _sqlite(estimator_state_db)
    try:
        projection_rows = {
            str(row["fact_id"]): row
            for row in store.execute("SELECT * FROM private_fact_adapter_projections")
        }
        receiver_facts = _receiver_fact_rows(receiver, projection_rows)
        receiver_revisions = _receiver_revision_rows(receiver, store)
        checkpoint_rows = receiver.execute(
            "SELECT stream_id,highest_contiguous_sequence FROM fact_checkpoints"
        ).fetchall()
        checkpoints = {str(row[0]): int(row[1]) for row in checkpoint_rows}
        target_streams = tuple(
            sorted(
                load_source_registry().by_code()[source].fact_stream_id
                for source in _fact_sources()
            )
        )
        placeholders = ",".join("?" for _ in target_streams)
        receiver_rejected = int(
            receiver.execute(
                f"SELECT COUNT(*) FROM fact_rejections "
                f"WHERE stream_id IN ({placeholders}) AND rejected_at_utc>=?",
                (*target_streams, CUTOFF_UTC),
            ).fetchone()[0]
        )
        adapter_rejected = int(
            store.execute(
                f"SELECT COUNT(*) FROM private_fact_adapter_rejections "
                f"WHERE stream_id IN ({placeholders}) AND rejected_at_utc>=?",
                (*target_streams, CUTOFF_UTC),
            ).fetchone()[0]
        )
        if receiver_rejected:
            _fail("receiver_rejection_unresolved")
        if adapter_rejected:
            _fail("adapter_rejection_unresolved")
        model_rows: dict[str, list[tuple[str, ...]]] = {
            source: [] for source in _fact_sources()
        }
        audit_only: dict[str, int] = {source: 0 for source in _fact_sources()}
        latest_adapted: datetime | None = None
        for source, facts in receiver_facts.items():
            for fact_row in facts:
                (
                    fact_id,
                    _,
                    source_sequence,
                    revision,
                    payload_hash,
                    event_key,
                    delivery,
                    _,
                    _,
                ) = fact_row
                projection = projection_rows.get(fact_id)
                if (
                    projection is None
                    or str(projection["stream_id"])
                    != load_source_registry().by_code()[source].fact_stream_id
                    or int(projection["source_sequence"]) != int(source_sequence)
                    or int(projection["fact_revision"]) != int(revision)
                    or str(projection["payload_hash"]) != payload_hash
                    or bytes(projection["event_key"]).hex() != event_key
                    or str(projection["status"]) == "REJECTED"
                ):
                    _fail("receiver_to_adapter_lineage_mismatch")
                stream = str(projection["stream_id"])
                if checkpoints.get(stream, -1) < int(delivery):
                    _fail("receiver_checkpoint_behind")
                adapted = _utc(
                    str(projection["adapted_at_utc"]),
                    reason="adapter_time_invalid",
                )
                latest_adapted = max(latest_adapted or adapted, adapted)
                observations = store.execute(
                    "SELECT source_code,quality_state,attributes_json,available_at_utc "
                    "FROM market_observations WHERE event_key=? "
                    "UNION ALL SELECT source_code,quality_state,attributes_json,available_at_utc "
                    "FROM market_observations_archive WHERE event_key=?",
                    (bytes.fromhex(event_key), bytes.fromhex(event_key)),
                ).fetchall()
                if len(observations) > 1:
                    _fail("adapter_market_store_observation_duplicated")
                if str(projection["status"]) == "AUDIT_ONLY":
                    previously_applied = store.execute(
                        "SELECT 1 FROM private_fact_adapter_projection_revisions "
                        "WHERE fact_id=? AND fact_revision<? AND status='APPLIED' "
                        "LIMIT 1",
                        (fact_id, int(revision)),
                    ).fetchone()
                    if previously_applied is not None:
                        if not observations:
                            _fail("adapter_audit_only_retraction_missing")
                        retired = observations[0]
                        try:
                            retired_attributes = json.loads(
                                str(retired["attributes_json"])
                            )
                        except (TypeError, json.JSONDecodeError):
                            _fail("adapter_audit_only_retraction_invalid")
                        if (
                            str(retired["source_code"]) != source
                            or str(retired["quality_state"]) != "IGNORED"
                            or not isinstance(retired_attributes, Mapping)
                            or retired_attributes.get("transfer_fact_id") != fact_id
                            or int(retired_attributes.get("fact_revision") or 0)
                            != int(revision)
                            or retired_attributes.get("adapter_disposition")
                            != "AUDIT_ONLY"
                        ):
                            _fail("adapter_audit_only_retraction_invalid")
                    elif observations and str(observations[0]["quality_state"]) != "IGNORED":
                        _fail("adapter_audit_only_model_visible")
                    audit_only[source] += 1
                    continue
                observation = observations[0] if observations else None
                if observation is None or str(observation["source_code"]) != source:
                    _fail("adapter_market_store_observation_missing")
                try:
                    attributes = json.loads(str(observation["attributes_json"]))
                except (TypeError, json.JSONDecodeError):
                    _fail("adapter_market_store_lineage_invalid")
                if (
                    not isinstance(attributes, Mapping)
                    or attributes.get("transfer_fact_id") != fact_id
                    or int(attributes.get("fact_revision") or 0) != int(revision)
                ):
                    _fail("adapter_market_store_lineage_invalid")
                model_rows[source].append(
                    (fact_id, revision, event_key, str(observation["quality_state"]))
                )

        snapshot_value = _read_json(snapshot_path, reason="estimator_snapshot_unreadable")
        try:
            snapshot = EstimatorSnapshotV2.model_validate(snapshot_value)
        except Exception as exc:
            raise CatchupAuditError("estimator_snapshot_contract_invalid") from exc
        if snapshot.feed_mode != "PRIVATE_PRIMARY":
            _fail("estimator_snapshot_lane_invalid")
        generated = snapshot.generated_at_utc.astimezone(timezone.utc)
        if latest_adapted is not None and generated < latest_adapted.replace(microsecond=0):
            _fail("estimator_snapshot_before_adapter_catchup")
        publication = estimator.execute(
            "SELECT snapshot_id,input_snapshot_hash,payload_json,published_at_utc "
            "FROM estimator_snapshot_publications WHERE snapshot_version=?",
            (snapshot.snapshot_version,),
        ).fetchone()
        if (
            publication is None
            or str(publication["snapshot_id"]) != snapshot.snapshot_id
            or str(publication["input_snapshot_hash"]) != snapshot.input_snapshot_hash
            or publication["published_at_utc"] is None
        ):
            _fail("estimator_publication_lineage_invalid")
        try:
            retained = EstimatorSnapshotV2.model_validate_json(str(publication["payload_json"]))
        except Exception as exc:
            raise CatchupAuditError("estimator_publication_contract_invalid") from exc
        if retained != snapshot:
            _fail("estimator_publication_payload_mismatch")

        input_rows: list[tuple[str, ...]] = []
        fact_source_by_id = {
            fact[0]: source
            for source, facts in receiver_facts.items()
            for fact in facts
        }
        consumed_by_source: dict[str, list[tuple[str, ...]]] = {
            source: [] for source in LIVE_CAPTURE_SOURCES
        }
        input_by_component = _index_snapshot_inputs(snapshot)
        for trace in snapshot.inputs:
            if trace.source_fact_id is None:
                continue
            projection = projection_rows.get(trace.source_fact_id)
            if (
                projection is None
                or bytes(projection["event_key"]).hex() != trace.source_event_key
                or int(projection["fact_revision"]) != trace.fact_revision
                or str(projection["status"]) != "APPLIED"
            ):
                _fail("estimator_input_trace_lineage_invalid")
            input_rows.append(
                (
                    trace.component,
                    trace.source_fact_id,
                    str(trace.fact_revision),
                    str(trace.source_event_key),
                )
            )
        for capture_source, component in MODEL_INPUT_COMPONENTS.items():
            trace = input_by_component[component]
            fact_source = CAPTURE_TO_FACT_SOURCE[capture_source]
            if (
                trace.source_fact_id is None
                or trace.source_event_key is None
                or trace.fact_revision is None
                or tuple(trace.source_codes) != (fact_source,)
                or fact_source_by_id.get(trace.source_fact_id) != fact_source
            ):
                _fail("estimator_source_input_not_consumed")
            consumed_by_source[capture_source].append(
                (
                    trace.component,
                    trace.source_fact_id,
                    str(trace.fact_revision),
                    trace.source_event_key,
                )
            )

        sources: dict[str, object] = {}
        for capture_source in sorted(LIVE_CAPTURE_SOURCES):
            fact_source = CAPTURE_TO_FACT_SOURCE[capture_source]
            sources[capture_source] = {
                "received_facts": _lineage_summary(receiver_facts[fact_source]),
                "revision_history": _lineage_summary(
                    receiver_revisions[fact_source]
                ),
                "model_visible": _lineage_summary(model_rows[fact_source]),
                "snapshot_input_traced": _lineage_summary(
                    consumed_by_source[capture_source]
                ),
                "audit_only": audit_only[fact_source],
            }
        return {
            "schema": BOT_SCHEMA,
            "role": "bot",
            "observed_at_utc": _stamp(observed_at),
            "release_sha": release_sha,
            "source_inventory": sorted(LIVE_CAPTURE_SOURCES),
            "sources": sources,
            "receiver_checkpoints": checkpoints,
            "snapshot": {
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_version": snapshot.snapshot_version,
                "feed_mode": snapshot.feed_mode,
                "status": snapshot.status,
                "generated_at_utc": _stamp(generated),
                "input_snapshot_hash": snapshot.input_snapshot_hash,
                "input_lineage": _lineage_summary(input_rows),
            },
            "quarantine": {
                "receiver_rejected": receiver_rejected,
                "adapter_rejected": adapter_rejected,
            },
            "secrets_disclosed": False,
        }
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise CatchupAuditError("bot_state_invalid") from exc
    finally:
        estimator.close()
        store.close()
        receiver.close()


def _valid_summary(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and not isinstance(value.get("count"), bool)
        and isinstance(value.get("count"), int)
        and int(value["count"]) >= 0
        and HEX64.fullmatch(str(value.get("digest") or "")) is not None
    )


def _valid_terminal_disposition_counts(value: object) -> bool:
    if not _valid_summary(value) or not isinstance(value, Mapping):
        return False
    parsed = value.get("parsed")
    filtered = value.get("filtered")
    dispositions = value.get("dispositions")
    if (
        isinstance(parsed, bool)
        or not isinstance(parsed, int)
        or parsed < 0
        or isinstance(filtered, bool)
        or not isinstance(filtered, int)
        or filtered < 0
        or not isinstance(dispositions, Mapping)
    ):
        return False
    classified = {"PARSED": 0, "FILTERED": 0}
    for disposition, count in dispositions.items():
        if (
            not isinstance(disposition, str)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            return False
        statuses = tuple(
            status
            for status, allowed in TERMINAL_LINEAGE_DISPOSITIONS.items()
            if disposition in allowed
        )
        if len(statuses) != 1:
            return False
        classified[statuses[0]] += count
    return (
        classified["PARSED"] == parsed
        and classified["FILTERED"] == filtered
        and parsed + filtered == int(value["count"])
    )


def _validate_artifact(value: Mapping[str, Any], *, schema: str, role: str) -> None:
    if (
        value.get("schema") != schema
        or value.get("role") != role
        or value.get("source_inventory") != sorted(LIVE_CAPTURE_SOURCES)
        or value.get("secrets_disclosed") is not False
        or not HEX40.fullmatch(str(value.get("release_sha") or ""))
        or not isinstance(value.get("sources"), Mapping)
        or set(value["sources"]) != set(LIVE_CAPTURE_SOURCES)
    ):
        _fail("catchup_artifact_invalid")
    _utc(value.get("observed_at_utc"), reason="catchup_artifact_time_invalid")
    quarantine = value.get("quarantine")
    if (
        not isinstance(quarantine, Mapping)
        or not quarantine
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count != 0
            for count in quarantine.values()
        )
    ):
        _fail("catchup_artifact_quarantine_invalid")
    sources = value["sources"]
    assert isinstance(sources, Mapping)
    if role == "web":
        public_binding = {
            "release_sha": value["release_sha"],
            "feed_mode": "PRIVATE_PRIMARY",
            "backfill_cutoff_utc": CUTOFF_UTC,
            "backfill_sources": sorted(BACKFILL_SOURCES),
            "archive_enabled": True,
        }
        binding = value.get("binding")
        expected_binding = {
            **public_binding,
            "binding_sha256": sha256(_canonical(public_binding)).hexdigest(),
        }
        backfill = value.get("backfill")
        if (
            binding != expected_binding
            or value.get("upstream_time_gaps_allowed") is not True
            or not isinstance(backfill, Mapping)
            or set(backfill) != set(BACKFILL_SOURCES)
        ):
            _fail("catchup_web_binding_invalid")
        for source in sorted(LIVE_CAPTURE_SOURCES):
            item = sources[source]
            if not isinstance(item, Mapping):
                _fail("catchup_source_evidence_invalid")
            capture = item.get("capture")
            processor = item.get("processor")
            parsed = item.get("parsed")
            archive = item.get("archive")
            revision_history = item.get("revision_history")
            if (
                not _valid_summary(capture)
                or not _valid_summary(processor)
                or not _valid_summary(parsed)
                or not _valid_summary(archive)
                or not _valid_summary(revision_history)
                or item.get("configured") is not True
                or item.get("health_observed") is not True
                or int(capture["count"]) < 1
                or int(parsed["count"]) < 1
                or int(archive["count"]) < 1
                or int(revision_history["count"]) < int(archive["count"])
                or int(processor.get("consumed", -1)) != int(capture["count"])
                or int(processor.get("head_sequence", -1))
                != int(capture.get("head_sequence", -2))
            ):
                _fail("catchup_source_evidence_invalid")
            terminal = item.get("terminal_lineage")
            if (
                not _valid_summary(terminal)
                or int(terminal["count"]) < 1
                or int(terminal.get("pending", -1)) != 0
                or int(terminal.get("parsed", -1))
                + int(terminal.get("filtered", -1))
                != int(terminal["count"])
                or int(capture.get("terminal_required", -1))
                != int(terminal["count"])
                or not _valid_terminal_disposition_counts(terminal)
            ):
                _fail("catchup_terminal_lineage_invalid")
            epoch = _utc(
                terminal.get("epoch_utc"),
                reason="capture_event_lineage_epoch_invalid",
            )
            observed = _utc(
                value.get("observed_at_utc"),
                reason="catchup_artifact_time_invalid",
            )
            if epoch > observed:
                _fail("capture_event_lineage_epoch_future")
            if source in BACKFILL_SOURCES:
                status = backfill[source]
                lineage = item.get("explicit_backfill_lineage")
                if not isinstance(status, Mapping) or not isinstance(lineage, Mapping):
                    _fail("catchup_backfill_evidence_invalid")
                attempted = status.get("attempted")
                accepted = status.get("accepted")
                duplicate = status.get("duplicate")
                quarantined = status.get("quarantined")
                explicit_count = capture.get("explicit_backfill_accepted")
                if (
                    any(
                        isinstance(count, bool) or not isinstance(count, int) or count < 0
                        for count in (
                            attempted,
                            accepted,
                            duplicate,
                            quarantined,
                            explicit_count,
                        )
                    )
                    or status.get("status") != "complete"
                    or status.get("cutoff_utc") != CUTOFF_UTC
                    or status.get("exhaustion") not in {"cutoff_crossed", "source_exhausted"}
                    or attempted < 1
                    or accepted + duplicate < 1
                    or attempted != accepted + duplicate + quarantined
                    or quarantined != 0
                    or explicit_count < 1
                    or not _valid_summary(lineage)
                    or int(lineage["count"]) != explicit_count
                    or int(lineage.get("pending", -1)) != 0
                    or int(lineage.get("parsed", -1)) + int(lineage.get("filtered", -1))
                    != explicit_count
                    or not _valid_terminal_disposition_counts(lineage)
                ):
                    _fail("catchup_backfill_evidence_invalid")
    else:
        snapshot = value.get("snapshot")
        if (
            not isinstance(snapshot, Mapping)
            or snapshot.get("feed_mode") != "PRIVATE_PRIMARY"
            or not _valid_summary(snapshot.get("input_lineage"))
        ):
            _fail("catchup_bot_snapshot_invalid")
        for source in sorted(LIVE_CAPTURE_SOURCES):
            item = sources[source]
            if (
                not isinstance(item, Mapping)
                or not _valid_summary(item.get("received_facts"))
                or not _valid_summary(item.get("revision_history"))
                or not _valid_summary(item.get("model_visible"))
                or not _valid_summary(item.get("snapshot_input_traced"))
                or isinstance(item.get("audit_only"), bool)
                or not isinstance(item.get("audit_only"), int)
                or int(item["audit_only"]) < 0
                or int(item["received_facts"]["count"]) < 1
                or int(item["revision_history"]["count"])
                < int(item["received_facts"]["count"])
                or int(item["model_visible"]["count"]) < 1
                or int(item["snapshot_input_traced"]["count"]) < 1
            ):
                _fail("catchup_source_evidence_invalid")


def verify(
    *,
    web: Mapping[str, Any],
    bot: Mapping[str, Any],
    expected_release_sha: str,
    previous_web: Mapping[str, Any] | None = None,
    previous_bot: Mapping[str, Any] | None = None,
    web_sha256: str | None = None,
    bot_sha256: str | None = None,
    previous_web_sha256: str | None = None,
    previous_bot_sha256: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    _validate_artifact(web, schema=WEB_SCHEMA, role="web")
    _validate_artifact(bot, schema=BOT_SCHEMA, role="bot")
    if (
        not HEX40.fullmatch(expected_release_sha)
        or web["release_sha"] != bot["release_sha"]
        or web["release_sha"] != expected_release_sha
    ):
        _fail("catchup_release_mismatch")
    if web.get("upstream_time_gaps_allowed") is not True:
        _fail("upstream_gap_policy_missing")
    sources = web["sources"]
    bot_sources = bot["sources"]
    assert isinstance(sources, Mapping) and isinstance(bot_sources, Mapping)
    advanced: list[str] = []
    parsed_advanced: list[str] = []
    for source in sorted(LIVE_CAPTURE_SOURCES):
        web_source = sources[source]
        bot_source = bot_sources[source]
        if not isinstance(web_source, Mapping) or not isinstance(bot_source, Mapping):
            _fail("catchup_source_evidence_invalid")
        archive = web_source.get("archive")
        received = bot_source.get("received_facts")
        if archive != received:
            _fail("web_bot_fact_lineage_mismatch")
        web_revisions = web_source.get("revision_history")
        bot_revisions = bot_source.get("revision_history")
        if web_revisions != bot_revisions:
            _fail("web_bot_fact_revision_lineage_mismatch")
        parsed = web_source.get("parsed")
        if not isinstance(parsed, Mapping) or not isinstance(archive, Mapping):
            _fail("catchup_source_evidence_invalid")
        if int(parsed.get("count", -1)) != int(archive.get("count", -2)):
            _fail("parsed_archive_count_mismatch")
        visible = bot_source.get("model_visible")
        if not isinstance(visible, Mapping):
            _fail("model_lineage_invalid")
        if int(visible.get("count", -1)) + int(
            bot_source.get("audit_only", -1)
        ) != int(received.get("count", -2)):
            _fail("model_lineage_count_mismatch")

    if previous_web is None or previous_bot is None:
        _fail("live_tail_pair_required")
    _validate_artifact(previous_web, schema=WEB_SCHEMA, role="web")
    _validate_artifact(previous_bot, schema=BOT_SCHEMA, role="bot")
    if (
        previous_web["release_sha"] != expected_release_sha
        or previous_bot["release_sha"] != expected_release_sha
        or previous_web.get("binding") != web.get("binding")
    ):
        _fail("live_tail_binding_drift")
    evidence_documents = {
        "previous_web": previous_web,
        "previous_bot": previous_bot,
        "web": web,
        "bot": bot,
    }
    supplied_digests = {
        "previous_web": previous_web_sha256,
        "previous_bot": previous_bot_sha256,
        "web": web_sha256,
        "bot": bot_sha256,
    }
    evidence_artifacts: dict[str, dict[str, str]] = {}
    evidence_times: dict[str, datetime] = {}
    for label, document in evidence_documents.items():
        observed = _utc(
            document["observed_at_utc"], reason="live_tail_time_invalid"
        )
        digest = supplied_digests[label] or sha256(_canonical(document)).hexdigest()
        if HEX64.fullmatch(digest) is None:
            _fail("catchup_artifact_digest_invalid")
        evidence_times[label] = observed
        evidence_artifacts[label] = {
            "sha256": digest,
            "observed_at_utc": _stamp(observed),
        }

    start = max(evidence_times["previous_web"], evidence_times["previous_bot"])
    end = min(evidence_times["web"], evidence_times["bot"])
    window_seconds = (end - start).total_seconds()
    if window_seconds < MIN_LIVE_TAIL_WINDOW_SECONDS:
        _fail("live_tail_observation_window_too_short")
    if window_seconds > MAX_LIVE_TAIL_WINDOW_SECONDS:
        _fail("live_tail_observation_window_too_old")
    if (
        evidence_times["previous_web"] > evidence_times["web"]
        or evidence_times["previous_bot"] > evidence_times["bot"]
        or abs(
            (
                evidence_times["previous_web"]
                - evidence_times["previous_bot"]
            ).total_seconds()
        )
        > MAX_EVIDENCE_PAIR_SKEW_SECONDS
        or abs(
            (evidence_times["web"] - evidence_times["bot"]).total_seconds()
        )
        > MAX_EVIDENCE_PAIR_SKEW_SECONDS
    ):
        _fail("live_tail_artifact_time_incoherent")
    previous_sources = previous_web["sources"]
    previous_bot_sources = previous_bot["sources"]
    assert isinstance(previous_sources, Mapping)
    assert isinstance(previous_bot_sources, Mapping)
    for source in sorted(LIVE_CAPTURE_SOURCES):
        before = previous_sources[source]
        after = sources[source]
        before_bot = previous_bot_sources[source]
        after_bot = bot_sources[source]
        assert isinstance(before, Mapping) and isinstance(after, Mapping)
        assert isinstance(before_bot, Mapping) and isinstance(after_bot, Mapping)
        before_head = int(before["capture"]["head_sequence"])
        after_head = int(after["capture"]["head_sequence"])
        before_processor_head = int(before["processor"]["head_sequence"])
        after_processor_head = int(after["processor"]["head_sequence"])
        if after_head < before_head or after_processor_head < before_processor_head:
            _fail("live_tail_regression")
        if after_head > before_head:
            advanced.append(source)
            if after_processor_head != after_head:
                _fail("live_tail_capture_not_consumed")
        before_parsed = before["parsed"]
        after_parsed = after["parsed"]
        before_archive = before["archive"]
        after_archive = after["archive"]
        before_received = before_bot["received_facts"]
        after_received = after_bot["received_facts"]
        if (
            int(after_parsed["count"]) < int(before_parsed["count"])
            or int(after_received["count"]) < int(before_received["count"])
        ):
            _fail("live_tail_fact_regression")
        parsed_changed = after_parsed != before_parsed
        archive_changed = after_archive != before_archive
        received_changed = after_received != before_received
        if parsed_changed:
            parsed_advanced.append(source)
            if not archive_changed or not received_changed:
                _fail("live_tail_fact_not_transferred")

    verified_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for observed in evidence_times.values():
        age = (verified_at - observed).total_seconds()
        if (
            age < -MAX_EVIDENCE_FUTURE_SKEW_SECONDS
            or age > MAX_EVIDENCE_AGE_SECONDS
        ):
            _fail("catchup_artifact_stale_or_future")
    evidence_binding_sha256 = sha256(_canonical(evidence_artifacts)).hexdigest()
    return {
        "schema": VERIFICATION_SCHEMA,
        "status": "PASS",
        "verified_at_utc": _stamp(verified_at),
        "release_sha": web["release_sha"],
        "cutoff_utc": CUTOFF_UTC,
        "backfill_sources": sorted(BACKFILL_SOURCES),
        "live_source_inventory": sorted(LIVE_CAPTURE_SOURCES),
        "live_tail_observed": True,
        "live_advanced_sources": advanced,
        "live_parser_output_advanced_sources": parsed_advanced,
        "evidence_artifacts": evidence_artifacts,
        "evidence_binding_sha256": evidence_binding_sha256,
        "upstream_time_gaps_allowed": True,
        "internal_sequence_gaps": 0,
        "unresolved_quarantines": 0,
        "unresolved_rejections": 0,
        "secrets_disclosed": False,
    }


def settle_live_tail_window(
    *,
    previous_web: Mapping[str, Any],
    previous_bot: Mapping[str, Any],
    expected_release_sha: str,
    previous_web_sha256: str,
    previous_bot_sha256: str,
    maximum_window_seconds: int = MAX_LIVE_TAIL_WINDOW_SECONDS,
    now_fn: Any | None = None,
    sleep_fn: Any | None = None,
) -> dict[str, object]:
    """Wait only long enough to make the second evidence pair meaningful.

    The receipt deliberately contains no Market value, message content or
    source-specific counter.  It binds the exact first pair and proves that a
    second pair may now be collected inside the verifier's 20..300 second
    live-tail window.  The maximum sleep is the fixed 20 second lower bound;
    stale evidence is rejected rather than extending the wait.
    """

    _validate_artifact(previous_web, schema=WEB_SCHEMA, role="web")
    _validate_artifact(previous_bot, schema=BOT_SCHEMA, role="bot")
    if (
        not HEX40.fullmatch(expected_release_sha)
        or previous_web.get("release_sha") != expected_release_sha
        or previous_bot.get("release_sha") != expected_release_sha
        or not HEX64.fullmatch(previous_web_sha256)
        or not HEX64.fullmatch(previous_bot_sha256)
        or isinstance(maximum_window_seconds, bool)
        or not isinstance(maximum_window_seconds, int)
        or not MIN_LIVE_TAIL_WINDOW_SECONDS
        <= maximum_window_seconds
        <= MAX_LIVE_TAIL_WINDOW_SECONDS
    ):
        _fail("live_tail_settle_binding_invalid")
    web_observed = _utc(
        previous_web.get("observed_at_utc"), reason="live_tail_time_invalid"
    )
    bot_observed = _utc(
        previous_bot.get("observed_at_utc"), reason="live_tail_time_invalid"
    )
    if abs((web_observed - bot_observed).total_seconds()) > (
        MAX_EVIDENCE_PAIR_SKEW_SECONDS
    ):
        _fail("live_tail_artifact_time_incoherent")
    reference = max(web_observed, bot_observed)
    not_before = reference + timedelta(seconds=MIN_LIVE_TAIL_WINDOW_SECONDS)
    deadline = reference + timedelta(seconds=maximum_window_seconds)
    clock = now_fn or (lambda: datetime.now(timezone.utc))
    sleeper = sleep_fn or time.sleep
    started = clock().astimezone(timezone.utc)
    if started < reference - timedelta(seconds=MAX_EVIDENCE_FUTURE_SKEW_SECONDS):
        _fail("live_tail_artifact_stale_or_future")
    if started > deadline:
        _fail("live_tail_settle_window_expired")
    requested_wait = max(0.0, (not_before - started).total_seconds())
    if requested_wait > MIN_LIVE_TAIL_WINDOW_SECONDS:
        _fail("live_tail_settle_wait_invalid")
    if requested_wait:
        sleeper(requested_wait)
    completed = clock().astimezone(timezone.utc)
    if completed < not_before or completed > deadline:
        _fail("live_tail_settle_window_invalid")
    evidence = {
        "previous_web": {
            "sha256": previous_web_sha256,
            "observed_at_utc": _stamp(web_observed),
        },
        "previous_bot": {
            "sha256": previous_bot_sha256,
            "observed_at_utc": _stamp(bot_observed),
        },
    }
    return {
        "schema": SETTLE_SCHEMA,
        "status": "PASS",
        "release_sha": expected_release_sha,
        "started_at_utc": _stamp(started),
        "completed_at_utc": _stamp(completed),
        "not_before_utc": _stamp(not_before),
        "deadline_utc": _stamp(deadline),
        "minimum_window_seconds": MIN_LIVE_TAIL_WINDOW_SECONDS,
        "maximum_window_seconds": maximum_window_seconds,
        "waited_seconds": round(requested_wait, 6),
        "previous_evidence": evidence,
        "previous_evidence_binding_sha256": sha256(
            _canonical(evidence)
        ).hexdigest(),
        "read_only": True,
        "payload_values_included": False,
        "pii_included": False,
        "secrets_disclosed": False,
    }


def _artifact(
    path: Path, *, schema: str, role: str, expected_sha256: str
) -> Mapping[str, Any]:
    if not HEX64.fullmatch(expected_sha256):
        _fail("catchup_artifact_digest_invalid")
    try:
        actual = sha256(path.read_bytes()).hexdigest()
    except OSError:
        _fail("catchup_artifact_unreadable")
    if actual != expected_sha256:
        _fail("catchup_artifact_digest_mismatch")
    value = _read_json(path, reason="catchup_artifact_unreadable")
    _validate_artifact(value, schema=schema, role=role)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    web = sub.add_parser("web")
    web.add_argument("--release-sha", required=True)
    web.add_argument("--runtime-env", type=Path, required=True)
    web.add_argument("--account1-db", type=Path, required=True)
    web.add_argument("--account2-db", type=Path, required=True)
    web.add_argument("--external-db", type=Path, required=True)
    web.add_argument("--account1-spool", type=Path, required=True)
    web.add_argument("--account2-spool", type=Path, required=True)
    web.add_argument("--external-spool", type=Path, required=True)
    web.add_argument("--processor-staging", type=Path, required=True)
    web.add_argument("--processor-market", type=Path, required=True)
    web.add_argument("--account1-health", type=Path, required=True)
    web.add_argument("--account2-health", type=Path, required=True)
    web.add_argument("--external-health", type=Path, required=True)
    web.add_argument("--processor-health", type=Path, required=True)
    web.add_argument("--postgres-container", required=True)
    web.add_argument("--postgres-user", required=True)
    web.add_argument("--postgres-database", required=True)
    web.add_argument("--output", type=Path, required=True)

    bot = sub.add_parser("bot")
    bot.add_argument("--release-sha", required=True)
    bot.add_argument("--receiver-db", type=Path, required=True)
    bot.add_argument("--market-store-db", type=Path, required=True)
    bot.add_argument("--estimator-state-db", type=Path, required=True)
    bot.add_argument("--snapshot", type=Path, required=True)
    bot.add_argument("--output", type=Path, required=True)

    check = sub.add_parser("verify")
    check.add_argument("--release-sha", required=True)
    check.add_argument("--web", type=Path, required=True)
    check.add_argument("--web-sha256", required=True)
    check.add_argument("--bot", type=Path, required=True)
    check.add_argument("--bot-sha256", required=True)
    check.add_argument("--previous-web", type=Path, required=True)
    check.add_argument("--previous-web-sha256", required=True)
    check.add_argument("--previous-bot", type=Path, required=True)
    check.add_argument("--previous-bot-sha256", required=True)
    check.add_argument("--output", type=Path, required=True)
    settle = sub.add_parser("settle")
    settle.add_argument("--release-sha", required=True)
    settle.add_argument("--previous-web", type=Path, required=True)
    settle.add_argument("--previous-web-sha256", required=True)
    settle.add_argument("--previous-bot", type=Path, required=True)
    settle.add_argument("--previous-bot-sha256", required=True)
    settle.add_argument(
        "--maximum-window-seconds",
        type=int,
        default=MAX_LIVE_TAIL_WINDOW_SECONDS,
    )
    settle.add_argument("--output", type=Path, required=True)
    apply_resolution = sub.add_parser("apply-resolution")
    apply_resolution.add_argument(
        "--account", choices=("account1", "account2"), required=True
    )
    apply_resolution.add_argument("--capture-db", type=Path, required=True)
    apply_resolution.add_argument("--evidence", type=Path, required=True)
    apply_resolution.add_argument("--evidence-sha256", required=True)
    apply_resolution.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "apply-resolution":
        if args.capture_db.is_symlink() or not args.capture_db.is_file():
            _fail("capture_resolution_database_invalid")
        state = CaptureState(args.capture_db, account=args.account)
        try:
            try:
                report = state.apply_quarantine_resolution_evidence(
                    args.evidence,
                    expected_sha256=args.evidence_sha256,
                )
            except CaptureRuntimeError as exc:
                _fail(str(exc))
        finally:
            state.close()
        value = {
            "schema": "capture_quarantine_resolution_application/1.0",
            "status": "PASS",
            "account": args.account,
            "evidence_sha256": args.evidence_sha256,
            **report,
            "secrets_disclosed": False,
        }
    elif args.command == "settle":
        value = settle_live_tail_window(
            previous_web=_artifact(
                args.previous_web,
                schema=WEB_SCHEMA,
                role="web",
                expected_sha256=args.previous_web_sha256,
            ),
            previous_bot=_artifact(
                args.previous_bot,
                schema=BOT_SCHEMA,
                role="bot",
                expected_sha256=args.previous_bot_sha256,
            ),
            expected_release_sha=args.release_sha,
            previous_web_sha256=args.previous_web_sha256,
            previous_bot_sha256=args.previous_bot_sha256,
            maximum_window_seconds=args.maximum_window_seconds,
        )
    elif args.command == "web":
        value = collect_web(
            release_sha=args.release_sha,
            runtime_env=args.runtime_env,
            account1_db=args.account1_db,
            account2_db=args.account2_db,
            external_db=args.external_db,
            account1_spool=args.account1_spool,
            account2_spool=args.account2_spool,
            external_spool=args.external_spool,
            processor_staging=args.processor_staging,
            processor_market=args.processor_market,
            account1_health=args.account1_health,
            account2_health=args.account2_health,
            external_health=args.external_health,
            processor_health=args.processor_health,
            postgres_container=args.postgres_container,
            postgres_user=args.postgres_user,
            postgres_database=args.postgres_database,
        )
        _validate_artifact(value, schema=WEB_SCHEMA, role="web")
    elif args.command == "bot":
        value = collect_bot(
            release_sha=args.release_sha,
            receiver_db=args.receiver_db,
            market_store_db=args.market_store_db,
            estimator_state_db=args.estimator_state_db,
            snapshot_path=args.snapshot,
        )
        _validate_artifact(value, schema=BOT_SCHEMA, role="bot")
    else:
        previous_web = _artifact(
            args.previous_web,
            schema=WEB_SCHEMA,
            role="web",
            expected_sha256=args.previous_web_sha256,
        )
        previous_bot = _artifact(
            args.previous_bot,
            schema=BOT_SCHEMA,
            role="bot",
            expected_sha256=args.previous_bot_sha256,
        )
        value = verify(
            web=_artifact(
                args.web,
                schema=WEB_SCHEMA,
                role="web",
                expected_sha256=args.web_sha256,
            ),
            bot=_artifact(
                args.bot,
                schema=BOT_SCHEMA,
                role="bot",
                expected_sha256=args.bot_sha256,
            ),
            previous_web=previous_web,
            previous_bot=previous_bot,
            expected_release_sha=args.release_sha,
            web_sha256=args.web_sha256,
            bot_sha256=args.bot_sha256,
            previous_web_sha256=args.previous_web_sha256,
            previous_bot_sha256=args.previous_bot_sha256,
        )
    _write_exclusive(args.output, value)
    print(
        json.dumps(
            {
                "status": "PASS",
                "schema": value["schema"],
                "artifact_sha256": sha256(args.output.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CatchupAuditError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        raise SystemExit(2)
