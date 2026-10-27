"""Fail-closed, privacy-minimized parity replay from one capture owner.

The harness in this module is deliberately not a promotion gate.  It freezes
one exact prefix of each live capture spool, clones one consistent legacy
database seed into two isolated lanes, and lets each pinned code release replay
the same bytes.  Only redacted comparison evidence survives the temporary
workspace.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from typing import Any, Iterator, Mapping, Sequence

from .capture_event_adapter import CaptureEventContractError, decode_capture_event
from .private_pipeline_contracts import content_hash
from .shadow_parity import sign_parity_report, verify_parity_report, write_private_json


CONTRACT = "market_single_owner_parity/1.0"
EVIDENCE_MODE = "SINGLE_OWNER_FROZEN_REPLAY"
MAX_RECORD_BYTES = 256 * 1024
MAX_LIVE_WINDOW_AGE_SECONDS = 25 * 60
MAX_SUBPROCESS_OUTPUT_BYTES = 8 * 1024 * 1024
_SPOOL_NAME = re.compile(r"^events-\d{4}-\d{2}-\d{2}\.jsonl$")
_SAFE_COUNTERS = {
    "records",
    "accepted",
    "duplicates",
    "changes",
    "tombstones",
    "rejected",
    "stale_market_skipped",
    "market_messages_reprojected",
    "market_facts_upserted",
    "market_facts_retracted",
    "private_paper_minutes_refreshed",
    "private_trade_facts_upserted",
    "private_trade_messages_finalized",
    "private_trade_messages_ambiguous",
    "group_messages_seen",
    "group_eligible_offers",
    "group_eligible_trades",
    "group_pending_or_rejected",
    "raw_rows_purged",
}


class SingleOwnerParityError(RuntimeError):
    """A safety invariant or replay precondition failed."""


def _utc(value: str | datetime, *, field: str) -> datetime:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except ValueError as exc:
        raise SingleOwnerParityError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SingleOwnerParityError(f"{field}_timezone_required")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _existing_directory(path: Path, *, field: str) -> Path:
    supplied = path.expanduser()
    try:
        info = supplied.lstat()
    except OSError as exc:
        raise SingleOwnerParityError(f"{field}_unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SingleOwnerParityError(f"{field}_invalid")
    return supplied.resolve()


def _existing_file(path: Path, *, field: str) -> Path:
    supplied = path.expanduser()
    try:
        info = supplied.lstat()
    except OSError as exc:
        raise SingleOwnerParityError(f"{field}_unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SingleOwnerParityError(f"{field}_invalid")
    return supplied.resolve()


def _outside_repository(path: Path, *, repository_root: Path, field: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(repository_root.resolve())
    except ValueError:
        return resolved
    raise SingleOwnerParityError(f"{field}_inside_repository")


def read_private_key(path: Path, *, field: str) -> bytes:
    key_path = _existing_file(path, field=field)
    info = key_path.stat()
    mode = stat.S_IMODE(info.st_mode)
    if mode not in {0o400, 0o440, 0o600, 0o640} or mode & 0o007:
        raise SingleOwnerParityError(f"{field}_permissions_invalid")
    if info.st_size > 4096:
        raise SingleOwnerParityError(f"{field}_size_invalid")
    value = key_path.read_bytes().strip()
    if len(value) < 32:
        raise SingleOwnerParityError(f"{field}_too_short")
    return value


def _hmac_ref(key: bytes, namespace: bytes, value: bytes) -> str:
    return hmac.new(key, namespace + b"\0" + value, sha256).hexdigest()


@contextmanager
def exclusive_existing_lock(path: Path, *, timeout_seconds: float) -> Iterator[None]:
    lock_path = _existing_file(path, field="baseline_writer_lock")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags)
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise SingleOwnerParityError("baseline_writer_lock_busy") from exc
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def sqlite_online_backup(source: Path, destination: Path) -> None:
    source_path = _existing_file(source, field="baseline_store")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise SingleOwnerParityError("database_backup_destination_exists")
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            f"{source_path.as_uri()}?mode=ro", uri=True, timeout=30
        )
        source_connection.execute("PRAGMA query_only=ON")
        destination_connection = sqlite3.connect(destination)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        result = destination_connection.execute("PRAGMA quick_check").fetchone()
        if result is None or str(result[0]).lower() != "ok":
            raise SingleOwnerParityError("database_backup_integrity_failed")
    except sqlite3.Error as exc:
        raise SingleOwnerParityError("database_backup_failed") from exc
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
    destination.chmod(0o600)


def _copy_exact_prefix(source: Path, destination: Path) -> tuple[int, int, int]:
    """Copy the byte prefix visible at open time, ignoring later appends."""

    source_info = source.lstat()
    if stat.S_ISLNK(source_info.st_mode) or not stat.S_ISREG(source_info.st_mode):
        raise SingleOwnerParityError("capture_spool_file_invalid")
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    destination_fd: int | None = None
    try:
        opened = os.fstat(source_fd)
        if not stat.S_ISREG(opened.st_mode):
            raise SingleOwnerParityError("capture_spool_file_invalid")
        size = int(opened.st_size)
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        remaining = size
        while remaining:
            chunk = os.read(source_fd, min(1024 * 1024, remaining))
            if not chunk:
                raise SingleOwnerParityError("capture_spool_truncated_during_freeze")
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise SingleOwnerParityError("capture_spool_freeze_write_failed")
                view = view[written:]
            remaining -= len(chunk)
        os.fsync(destination_fd)
        os.fchmod(destination_fd, 0o400)
        return int(opened.st_dev), int(opened.st_ino), size
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)


def freeze_spools(
    *,
    market_spool_dir: Path,
    coin_spool_dir: Path,
    destination_root: Path,
) -> tuple[dict[str, Path], list[tuple[str, Path, int, int, int]]]:
    streams = {
        "market": _existing_directory(market_spool_dir, field="market_spool_dir"),
        "coin": _existing_directory(coin_spool_dir, field="coin_spool_dir"),
    }
    frozen: dict[str, Path] = {}
    records: list[tuple[str, Path, int, int, int]] = []
    for stream, source_dir in streams.items():
        target_dir = destination_root / "capture" / stream
        target_dir.mkdir(parents=True, mode=0o700)
        os.chmod(target_dir, 0o700)
        frozen[stream] = target_dir
        for source in sorted(source_dir.iterdir()):
            if not _SPOOL_NAME.fullmatch(source.name):
                continue
            target = target_dir / source.name
            device, inode, size = _copy_exact_prefix(source, target)
            records.append((stream, target, device, inode, size))
        directory_fd = os.open(target_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    if not records:
        raise SingleOwnerParityError("capture_spools_empty")
    return frozen, records


def build_capture_manifest(
    frozen_records: Sequence[tuple[str, Path, int, int, int]],
    *,
    identity_key: bytes,
    window_start: datetime,
    window_end: datetime,
    replay_root: Path,
) -> tuple[dict[str, Any], str, dict[str, Path]]:
    items: list[dict[str, Any]] = []
    duplicate_refs: Counter[str] = Counter()
    partial_tail_count = 0
    total_complete = 0
    frozen_digest = hmac.new(identity_key, b"frozen-spools-v1\0", sha256)
    replay_directories: dict[str, Path] = {}
    for stream in ("market", "coin"):
        directory = replay_root / stream
        directory.mkdir(parents=True, mode=0o700)
        os.chmod(directory, 0o700)
        replay_directories[stream] = directory
    for stream, path, _device, _inode, expected_size in frozen_records:
        frozen_digest.update(stream.encode("ascii") + b"\0")
        frozen_digest.update(path.name.encode("ascii") + b"\0")
        data = path.read_bytes()
        if len(data) != expected_size:
            raise SingleOwnerParityError("frozen_spool_size_changed")
        frozen_digest.update(len(data).to_bytes(8, "big"))
        frozen_digest.update(data)
        replay_path = replay_directories[stream] / path.name
        with replay_path.open("xb") as replay_handle:
            lines = data.splitlines(keepends=True)
            for index, raw in enumerate(lines):
                if not raw.endswith(b"\n"):
                    if index != len(lines) - 1:
                        raise SingleOwnerParityError("capture_spool_incomplete_middle_record")
                    partial_tail_count += 1
                    continue
                if len(raw) > MAX_RECORD_BYTES:
                    raise SingleOwnerParityError("capture_spool_complete_record_too_large")
                total_complete += 1
                try:
                    decoded = json.loads(raw.decode("utf-8"))
                    event = decode_capture_event(decoded, stream=stream)
                except (UnicodeDecodeError, json.JSONDecodeError, CaptureEventContractError) as exc:
                    raise SingleOwnerParityError("capture_spool_complete_record_invalid") from exc
                available = _utc(event.available_at_utc, field="capture_available_at")
                occurred_value = (
                    event.edited_at_utc
                    if event.event_type == "message_edited" and event.edited_at_utc
                    else event.event_time_utc
                ) or event.available_at_utc
                occurred = _utc(occurred_value, field="capture_occurred_at")
                if not (window_start <= available <= window_end):
                    continue
                replay_handle.write(raw)
                event_ref = _hmac_ref(
                    identity_key,
                    b"capture-event-v1",
                    f"{stream}:{event.event_id}".encode("utf-8"),
                )
                duplicate_refs[event_ref] += 1
                items.append(
                    {
                        "event_ref": event_ref,
                        "stream": stream,
                        "source_code": event.source_id,
                        "event_type": event.event_type,
                        "occurred_at_utc": _stamp(occurred),
                        "available_at_utc": _stamp(available),
                    }
                )
            replay_handle.flush()
            os.fsync(replay_handle.fileno())
        replay_path.chmod(0o400)
    items.sort(
        key=lambda item: (
            item["available_at_utc"],
            item["stream"],
            item["event_ref"],
        )
    )
    manifest = {
        "contract": "market_single_owner_capture_manifest/1.0",
        "window_start_utc": _stamp(window_start),
        "window_end_utc": _stamp(window_end),
        "frozen_file_count": len(frozen_records),
        "complete_record_count": total_complete,
        "window_record_count": len(items),
        "replay_record_count": len(items),
        "duplicate_event_count": sum(max(0, count - 1) for count in duplicate_refs.values()),
        "partial_tail_count": partial_tail_count,
        "events": items,
    }
    for directory in replay_directories.values():
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return manifest, frozen_digest.hexdigest(), replay_directories


def code_identity(code_root: Path) -> dict[str, Any]:
    root = _existing_directory(code_root, field="code_root")
    required = root / "scripts" / "ingest_capture_event_spools.py"
    _existing_file(required, field="ingest_script")
    paths = sorted(
        path
        for base in (root / "core" / "market_intelligence", root / "scripts")
        if base.is_dir()
        for path in base.rglob("*.py")
        if path.is_file() and not path.is_symlink()
    )
    if not paths:
        raise SingleOwnerParityError("code_identity_files_missing")
    digest = sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return {"sha256": digest.hexdigest(), "python_file_count": len(paths)}


def _clone_seed(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise SingleOwnerParityError("lane_seed_destination_exists")
    shutil.copyfile(source, destination)
    destination.chmod(0o600)


def _minimal_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "APP_ENV_FILE": "/dev/null",
        "TZ": "UTC",
    }


_FROZEN_INGEST_PROGRAM = r"""
import importlib.util
from datetime import datetime as RealDateTime
import sys

code_root, script_path, frozen_now, *arguments = sys.argv[1:]
sys.path.insert(0, code_root)
spec = importlib.util.spec_from_file_location("single_owner_lane_ingest", script_path)
if spec is None or spec.loader is None:
    raise SystemExit(91)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fixed = RealDateTime.fromisoformat(frozen_now.replace("Z", "+00:00"))


class FrozenDateTime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return fixed.replace(tzinfo=None)
        return fixed.astimezone(tz)


module.datetime = FrozenDateTime
raise SystemExit(module.main(arguments))
"""


def run_lane_ingest(
    *,
    code_root: Path,
    python_executable: Path,
    runtime_root: Path,
    market_spool_dir: Path,
    coin_spool_dir: Path,
    maximum_records: int,
    timeout_seconds: int,
    replay_as_of_utc: str,
) -> dict[str, int]:
    script = code_root / "scripts" / "ingest_capture_event_spools.py"
    ingest_arguments = [
        "--runtime-root",
        str(runtime_root),
        "--market-spool-dir",
        str(market_spool_dir),
        "--coin-spool-dir",
        str(coin_spool_dir),
        "--maximum-records",
        str(maximum_records),
    ]
    command = [
        str(python_executable),
        "-c",
        _FROZEN_INGEST_PROGRAM,
        str(code_root),
        str(script),
        replay_as_of_utc,
        *ingest_arguments,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=code_root,
            env=_minimal_environment(),
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SingleOwnerParityError("lane_ingest_execution_failed") from exc
    if len(completed.stdout) > MAX_SUBPROCESS_OUTPUT_BYTES or len(completed.stderr) > MAX_SUBPROCESS_OUTPUT_BYTES:
        raise SingleOwnerParityError("lane_ingest_output_too_large")
    try:
        output = json.loads(completed.stdout.decode("utf-8").strip())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SingleOwnerParityError("lane_ingest_output_invalid") from exc
    if completed.returncode != 0 or not isinstance(output, dict) or output.get("status") != "INGESTED":
        raise SingleOwnerParityError("lane_ingest_failed")
    counters: dict[str, int] = {}
    for key in sorted(_SAFE_COUNTERS):
        value = output.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SingleOwnerParityError("lane_ingest_counter_invalid")
        counters[key] = value
    return counters


def _lane_fact_activity(path: Path, *, replay_started_at_utc: str) -> tuple[int, dict[str, int]]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            """
            SELECT parser_version,COUNT(*) AS fact_count
            FROM market_observations
            WHERE inserted_at_utc>=?
            GROUP BY parser_version
            ORDER BY parser_version
            """,
            (replay_started_at_utc,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise SingleOwnerParityError("lane_fact_read_failed") from exc
    finally:
        connection.close()
    versions = {str(row["parser_version"]): int(row["fact_count"]) for row in rows}
    return sum(versions.values()), versions


def compare_market_stores(
    baseline_path: Path,
    candidate_path: Path,
    *,
    identity_key: bytes,
    issue_limit: int = 100,
) -> dict[str, Any]:
    """Compare final lane semantics without retaining financial field values."""

    connection = sqlite3.connect(f"{baseline_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute(
            "ATTACH DATABASE ? AS candidate",
            (f"file:{candidate_path.resolve().as_posix()}?mode=ro",),
        )
        baseline_count = int(
            connection.execute("SELECT COUNT(*) FROM main.market_observations").fetchone()[0]
        )
        candidate_count = int(
            connection.execute("SELECT COUNT(*) FROM candidate.market_observations").fetchone()[0]
        )
        common_count = 0
        counts: Counter[str] = Counter()
        severity_counts: Counter[int] = Counter()
        sources: dict[str, Counter[str]] = {}
        instruments: dict[str, Counter[str]] = {}
        issues: list[dict[str, Any]] = []

        def record(
            code: str,
            severity: int,
            row: sqlite3.Row,
            *,
            source_code: str,
            instrument: str,
        ) -> None:
            counts[code] += 1
            severity_counts[severity] += 1
            sources.setdefault(code, Counter())[source_code] += 1
            instruments.setdefault(code, Counter())[instrument] += 1
            if len(issues) < issue_limit:
                raw = row[0]
                key_bytes = bytes(raw) if isinstance(raw, (bytes, bytearray, memoryview)) else str(raw).encode("utf-8")
                issues.append(
                    {
                        "code": code,
                        "severity": severity,
                        "fact_ref": _hmac_ref(identity_key, b"market-fact-v1", key_bytes),
                    }
                )

        for row in connection.execute(
            "SELECT b.event_key,b.source_code,b.instrument FROM main.market_observations b "
            "LEFT JOIN candidate.market_observations c ON c.event_key=b.event_key "
            "WHERE c.event_key IS NULL ORDER BY b.event_key",
        ):
            record(
                "CANDIDATE_FACT_MISSING",
                2,
                row,
                source_code=str(row[1]),
                instrument=str(row[2]),
            )
        for row in connection.execute(
            "SELECT c.event_key,c.source_code,c.instrument FROM candidate.market_observations c "
            "LEFT JOIN main.market_observations b ON b.event_key=c.event_key "
            "WHERE b.event_key IS NULL ORDER BY c.event_key",
        ):
            record(
                "CANDIDATE_FACT_ADDED",
                2,
                row,
                source_code=str(row[1]),
                instrument=str(row[2]),
            )
        common_rows = connection.execute(
            """
            SELECT b.event_key,
                   b.source_code,b.source_family,b.instrument,b.market_label,
                   b.settlement_term,b.trade_form,b.event_type,b.side,b.price_num,
                   b.price_unit,b.currency,b.quantity_num,b.quantity_unit,
                   b.quality_state,b.is_conditional,
                   c.source_code,c.source_family,c.instrument,c.market_label,
                   c.settlement_term,c.trade_form,c.event_type,c.side,c.price_num,
                   c.price_unit,c.currency,c.quantity_num,c.quantity_unit,
                   c.quality_state,c.is_conditional
            FROM main.market_observations b
            JOIN candidate.market_observations c ON c.event_key=b.event_key
            ORDER BY b.event_key
            """
        )
        for row in common_rows:
            common_count += 1
            source_code = str(row[16])
            instrument = str(row[18])
            if (row[10], row[11], row[13]) != (row[25], row[26], row[28]):
                record(
                    "FACT_UNIT_MISMATCH",
                    1,
                    row,
                    source_code=source_code,
                    instrument=instrument,
                )
            if (row[7], row[14], row[15]) != (row[22], row[29], row[30]):
                record(
                    "FACT_LIFECYCLE_MISMATCH",
                    2,
                    row,
                    source_code=source_code,
                    instrument=instrument,
                )
            if (
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[8],
                row[9],
                row[12],
            ) != (
                row[16],
                row[17],
                row[18],
                row[19],
                row[20],
                row[21],
                row[23],
                row[24],
                row[27],
            ):
                record(
                    "FACT_PARSER_MISMATCH",
                    2,
                    row,
                    source_code=source_code,
                    instrument=instrument,
                )
    except sqlite3.Error as exc:
        raise SingleOwnerParityError("lane_fact_compare_failed") from exc
    finally:
        connection.close()
    return {
        "baseline_fact_count": baseline_count,
        "candidate_fact_count": candidate_count,
        "common_fact_count": common_count,
        "difference_counts": dict(sorted(counts.items())),
        "difference_counts_by_source": {
            code: dict(sorted(values.items())) for code, values in sorted(sources.items())
        },
        "difference_counts_by_instrument": {
            code: dict(sorted(values.items()))
            for code, values in sorted(instruments.items())
        },
        "issue_count": sum(counts.values()),
        "severity_1_count": severity_counts[1],
        "severity_2_count": severity_counts[2],
        "issues_truncated": sum(counts.values()) > len(issues),
        "issues": issues,
    }


def compare_facts(
    baseline: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, Mapping[str, Any]],
    *,
    issue_limit: int = 100,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    severity_counts: Counter[int] = Counter()

    def add(code: str, severity: int, fact_ref: str) -> None:
        counts[code] += 1
        severity_counts[severity] += 1
        if len(issues) < issue_limit:
            issues.append({"code": code, "severity": severity, "fact_ref": fact_ref})

    baseline_keys = set(baseline)
    candidate_keys = set(candidate)
    for fact_ref in sorted(baseline_keys - candidate_keys):
        add("CANDIDATE_FACT_MISSING", 2, fact_ref)
    for fact_ref in sorted(candidate_keys - baseline_keys):
        add("CANDIDATE_FACT_ADDED", 2, fact_ref)
    for fact_ref in sorted(baseline_keys & candidate_keys):
        left = baseline[fact_ref]
        right = candidate[fact_ref]
        if left["unit"] != right["unit"]:
            add("FACT_UNIT_MISMATCH", 1, fact_ref)
        if left["lifecycle"] != right["lifecycle"]:
            add("FACT_LIFECYCLE_MISMATCH", 2, fact_ref)
        if left["economic"] != right["economic"]:
            add("FACT_PARSER_MISMATCH", 2, fact_ref)
    return {
        "baseline_fact_count": len(baseline),
        "candidate_fact_count": len(candidate),
        "common_fact_count": len(baseline_keys & candidate_keys),
        "difference_counts": dict(sorted(counts.items())),
        "issue_count": sum(counts.values()),
        "severity_1_count": severity_counts[1],
        "severity_2_count": severity_counts[2],
        "issues_truncated": sum(counts.values()) > len(issues),
        "issues": issues,
    }


_SNAPSHOT_PROGRAM = r"""
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core.market_intelligence.market_snapshot import build_market_snapshot
c = sqlite3.connect('file:' + Path(sys.argv[2]).resolve().as_posix() + '?mode=ro', uri=True)
c.row_factory = sqlite3.Row
c.execute('PRAGMA query_only=ON')
try:
    value = build_market_snapshot(c, as_of_utc=sys.argv[3])
finally:
    c.close()
print(json.dumps(value, sort_keys=True, separators=(',', ':')))
"""


def build_lane_snapshot(
    *,
    code_root: Path,
    python_executable: Path,
    market_store: Path,
    as_of_utc: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(python_executable), "-c", _SNAPSHOT_PROGRAM, str(code_root), str(market_store), as_of_utc],
            cwd=code_root,
            env=_minimal_environment(),
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SingleOwnerParityError("lane_snapshot_execution_failed") from exc
    if completed.returncode != 0 or len(completed.stdout) > MAX_SUBPROCESS_OUTPUT_BYTES:
        raise SingleOwnerParityError("lane_snapshot_failed")
    try:
        value = json.loads(completed.stdout.decode("utf-8").strip())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SingleOwnerParityError("lane_snapshot_output_invalid") from exc
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("signals"), dict)
        or not isinstance(value.get("rates"), dict)
    ):
        raise SingleOwnerParityError("lane_snapshot_contract_invalid")
    return value


def compare_snapshots(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    same_fact_inputs: bool,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    baseline_signals = baseline.get("signals", {})
    candidate_signals = candidate.get("signals", {})
    signal_names = sorted(set(baseline_signals) | set(candidate_signals))
    signal_mismatches = 0
    signal_value_mismatches = 0
    signal_value_schema_mismatches = 0
    value_fields = (
        "status",
        "price_unit",
        "latest_price",
        "weighted_median_price",
        "mean_price",
        "median_price",
        "minimum_price",
        "maximum_price",
    )
    for name in signal_names:
        if content_hash(baseline_signals.get(name)) == content_hash(candidate_signals.get(name)):
            continue
        signal_mismatches += 1
        left = baseline_signals.get(name)
        right = candidate_signals.get(name)
        left_fields = (
            {field for field in value_fields if field in left}
            if isinstance(left, Mapping)
            else set()
        )
        right_fields = (
            {field for field in value_fields if field in right}
            if isinstance(right, Mapping)
            else set()
        )
        shared_fields = left_fields & right_fields
        left_values = (
            {field: left.get(field) for field in shared_fields}
            if isinstance(left, Mapping)
            else None
        )
        right_values = (
            {field: right.get(field) for field in shared_fields}
            if isinstance(right, Mapping)
            else None
        )
        value_mismatch = content_hash(left_values) != content_hash(right_values)
        value_schema_mismatch = left_fields != right_fields
        signal_value_mismatches += int(value_mismatch)
        signal_value_schema_mismatches += int(value_schema_mismatch)
        external = name in {"XAUUSD", "USDT_IRT"}
        issues.append(
            {
                "code": (
                    "CONSUMED_EXTERNAL_VALUE_MISMATCH"
                    if external and value_mismatch
                    else "SNAPSHOT_VALUE_SCHEMA_MISMATCH"
                    if value_schema_mismatch
                    else "SNAPSHOT_METADATA_MISMATCH"
                    if external
                    else "SNAPSHOT_FEATURE_MISMATCH"
                ),
                "severity": 1 if external and value_mismatch else 2,
                "component": name,
            }
        )

    def rates(document: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in document.get("rates", {}).get("items", []):
            if not isinstance(item, dict):
                raise SingleOwnerParityError("lane_snapshot_rate_invalid")
            key = f"{item.get('commodity_code')}:{item.get('settlement_term')}"
            result[key] = item
        return result

    baseline_rates = rates(baseline)
    candidate_rates = rates(candidate)
    rate_mismatches = 0
    rate_severity = 1 if same_fact_inputs else 2
    for name in sorted(set(baseline_rates) | set(candidate_rates)):
        if content_hash(baseline_rates.get(name)) == content_hash(candidate_rates.get(name)):
            continue
        rate_mismatches += 1
        issues.append(
            {
                "code": (
                    "SAME_INPUT_RATE_OUTPUT_MISMATCH"
                    if same_fact_inputs
                    else "RATE_OUTPUT_MISMATCH"
                ),
                "severity": rate_severity,
                "component": name,
            }
        )
    return {
        "signal_count": len(signal_names),
        "signal_mismatch_count": signal_mismatches,
        "signal_value_mismatch_count": signal_value_mismatches,
        "signal_value_schema_mismatch_count": signal_value_schema_mismatches,
        "rate_count": len(set(baseline_rates) | set(candidate_rates)),
        "rate_mismatch_count": rate_mismatches,
        "same_fact_inputs": same_fact_inputs,
        "severity_1_count": sum(item["severity"] == 1 for item in issues),
        "severity_2_count": sum(item["severity"] == 2 for item in issues),
        "issues": issues[:100],
        "issues_truncated": len(issues) > 100,
    }


def _write_manifest(path: Path, document: Mapping[str, Any]) -> None:
    write_private_json(path, document)


def run_single_owner_parity(
    *,
    repository_root: Path,
    baseline_code_root: Path,
    candidate_code_root: Path,
    baseline_market_store: Path,
    baseline_staging_store: Path,
    baseline_writer_lock: Path,
    market_spool_dir: Path,
    coin_spool_dir: Path,
    scratch_root: Path,
    artifact_dir: Path,
    identity_key: bytes,
    signing_key: bytes,
    signing_key_id: str,
    window_start: datetime,
    window_end: datetime,
    python_executable: Path,
    maximum_records: int,
    lock_timeout_seconds: float,
    subprocess_timeout_seconds: int,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = _utc(window_start, field="window_start")
    end = _utc(window_end, field="window_end")
    if start >= end:
        raise SingleOwnerParityError("window_order_invalid")
    if end > now + timedelta(seconds=5):
        raise SingleOwnerParityError("window_end_in_future")
    if start < now - timedelta(seconds=MAX_LIVE_WINDOW_AGE_SECONDS):
        raise SingleOwnerParityError("window_start_too_old_for_deterministic_replay")
    if maximum_records <= 0:
        raise SingleOwnerParityError("maximum_records_invalid")
    scratch = _outside_repository(
        _existing_directory(scratch_root, field="scratch_root"),
        repository_root=repository_root,
        field="scratch_root",
    )
    artifacts = _outside_repository(
        artifact_dir,
        repository_root=repository_root,
        field="artifact_dir",
    )
    if artifacts.exists():
        raise SingleOwnerParityError("artifact_dir_exists")
    baseline_root = _existing_directory(baseline_code_root, field="baseline_code_root")
    candidate_root = _existing_directory(candidate_code_root, field="candidate_code_root")
    python_path = _existing_file(python_executable, field="python_executable")
    baseline_market = _existing_file(baseline_market_store, field="baseline_market_store")
    baseline_staging = _existing_file(baseline_staging_store, field="baseline_staging_store")
    baseline_identity = code_identity(baseline_root)
    candidate_identity = code_identity(candidate_root)
    manifest: dict[str, Any]
    report: dict[str, Any]
    temporary_path: Path | None = None
    with tempfile.TemporaryDirectory(prefix="single-owner-parity-", dir=scratch) as directory:
        temporary_path = Path(directory)
        os.chmod(temporary_path, 0o700)
        with exclusive_existing_lock(baseline_writer_lock, timeout_seconds=lock_timeout_seconds):
            seed = temporary_path / "seed"
            sqlite_online_backup(baseline_market, seed / "market.sqlite3")
            sqlite_online_backup(baseline_staging, seed / "capture.sqlite3")
        _frozen, frozen_records = freeze_spools(
            market_spool_dir=market_spool_dir,
            coin_spool_dir=coin_spool_dir,
            destination_root=temporary_path,
        )
        manifest, frozen_input_hmac, replay_spools = build_capture_manifest(
            frozen_records,
            identity_key=identity_key,
            window_start=start,
            window_end=end,
            replay_root=temporary_path / "replay",
        )
        lanes: dict[str, dict[str, Any]] = {}
        lane_runtimes: dict[str, Path] = {}
        lane_code_roots: dict[str, Path] = {}
        lane_snapshots: dict[str, dict[str, Any]] = {}
        replay_started_at = _stamp(datetime.now(timezone.utc) - timedelta(seconds=1))
        for lane_name, code_root in (("baseline", baseline_root), ("candidate", candidate_root)):
            runtime = temporary_path / lane_name
            _clone_seed(seed / "market.sqlite3", runtime / "market" / "market.sqlite3")
            _clone_seed(seed / "capture.sqlite3", runtime / "staging" / "capture.sqlite3")
            counters = run_lane_ingest(
                code_root=code_root,
                python_executable=python_path,
                runtime_root=runtime,
                market_spool_dir=replay_spools["market"],
                coin_spool_dir=replay_spools["coin"],
                maximum_records=maximum_records,
                timeout_seconds=subprocess_timeout_seconds,
                replay_as_of_utc=_stamp(end),
            )
            activity_count, versions = _lane_fact_activity(
                runtime / "market" / "market.sqlite3",
                replay_started_at_utc=replay_started_at,
            )
            lanes[lane_name] = {
                "ingest_counters": counters,
                "replay_written_fact_count": activity_count,
                "parser_versions": versions,
            }
            lane_runtimes[lane_name] = runtime
            lane_code_roots[lane_name] = code_root
        fact_comparison = compare_market_stores(
            lane_runtimes["baseline"] / "market" / "market.sqlite3",
            lane_runtimes["candidate"] / "market" / "market.sqlite3",
            identity_key=identity_key,
        )
        snapshot_evaluation_at = _stamp(end)
        for lane_name in ("baseline", "candidate"):
            lane_snapshots[lane_name] = build_lane_snapshot(
                code_root=lane_code_roots[lane_name],
                python_executable=python_path,
                market_store=lane_runtimes[lane_name] / "market" / "market.sqlite3",
                as_of_utc=snapshot_evaluation_at,
                timeout_seconds=subprocess_timeout_seconds,
            )
        snapshot_comparison = compare_snapshots(
            lane_snapshots["baseline"],
            lane_snapshots["candidate"],
            same_fact_inputs=fact_comparison["issue_count"] == 0,
        )
        severity_1_count = (
            fact_comparison["severity_1_count"]
            + snapshot_comparison["severity_1_count"]
        )
        severity_2_count = (
            fact_comparison["severity_2_count"]
            + snapshot_comparison["severity_2_count"]
        )
        report_body = {
            "contract": CONTRACT,
            "evidence_mode": EVIDENCE_MODE,
            "window_start_utc": _stamp(start),
            "window_end_utc": _stamp(end),
            "snapshot_evaluation_at_utc": snapshot_evaluation_at,
            "capture_manifest_hash": content_hash(manifest),
            "capture_window_record_count": manifest["window_record_count"],
            "replay_record_count": manifest["replay_record_count"],
            "capture_complete_record_count": manifest["complete_record_count"],
            "capture_duplicate_event_count": manifest["duplicate_event_count"],
            "capture_partial_tail_count": manifest["partial_tail_count"],
            "frozen_input_hmac": frozen_input_hmac,
            "baseline_code_identity": baseline_identity,
            "candidate_code_identity": candidate_identity,
            "lanes": lanes,
            "fact_comparison": fact_comparison,
            "snapshot_comparison": snapshot_comparison,
            "severity_1_count": severity_1_count,
            "severity_2_count": severity_2_count,
            "snapshot_timeline_complete": False,
            "full_market_session": False,
            "cutover_performed": False,
            "raw_artifacts_retained": False,
            "promotion_recommendation": "HOLD_STAGE12_LIVE_PARITY_REQUIRED",
        }
        report = sign_parity_report(report_body, key=signing_key, key_id=signing_key_id)
        if not verify_parity_report(report, key=signing_key):
            raise SingleOwnerParityError("signed_report_verification_failed")
    if temporary_path is None or temporary_path.exists():
        raise SingleOwnerParityError("ephemeral_workspace_cleanup_failed")
    artifacts.mkdir(parents=True, mode=0o700)
    os.chmod(artifacts, 0o700)
    try:
        _write_manifest(artifacts / "capture-manifest.json", manifest)
        write_private_json(artifacts / "report.json", report)
    except BaseException:
        shutil.rmtree(artifacts, ignore_errors=True)
        raise
    return {
        "status": "pass",
        "report_hash": report["report_hash"],
        "capture_window_record_count": report["capture_window_record_count"],
        "baseline_fact_count": fact_comparison["baseline_fact_count"],
        "candidate_fact_count": fact_comparison["candidate_fact_count"],
        "severity_1_count": severity_1_count,
        "severity_2_count": severity_2_count,
        "promotion_recommendation": report["promotion_recommendation"],
        "cutover_performed": False,
    }
