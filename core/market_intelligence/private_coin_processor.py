"""Docker-native shadow processor for Account 2 coin-group capture spools.

The processor is deliberately downstream from capture: it only reads durable
JSONL, keeps raw text in its three-day staging SQLite, and writes redacted
Market Store facts.  Live mode requires both the operator-feedback sidecar and
the causal estimator-prediction ledger; silently parsing without either input
would change unnamed-instrument decisions compared with production.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Mapping

from .capture_event_adapter import (
    CAPTURE_ADAPTER_VERSION,
    COIN_GROUP_ACTIVE_REPLAY_WINDOW,
    CaptureEventContractError,
    decode_capture_event,
    initialize_capture_adapter,
    project_capture_changes,
    record_capture_rejection,
    stage_capture_event,
)
from .coin_group_calibration_corpus import (
    CALIBRATION_CORPUS_VERSION,
    append_coin_group_feedback_revisions,
)
from .coin_group_feedback import (
    CoinGroupParserFeedback,
    load_coin_group_parser_feedback,
)
from .coin_group_pipeline import COIN_GROUP_PIPELINE_VERSION
from .coin_group_staging import connect_coin_group_staging
from .coin_group_trades import MAX_REPLY_AGE_SECONDS
from .coin_groups import COIN_GROUP_PARSER_VERSION
from .coin_prediction_anchors import load_coin_prediction_anchors
from .market_contracts import normalize_utc
from .market_store import connect_market_store, initialize_market_store
from .private_pipeline_foundation import atomic_json_write, utc_text


PROCESSOR_HEARTBEAT_SCHEMA = "market_coin_processor/1.0"
PROCESSOR_VERSION = "market-coin-processor-v1-shadow"
MAX_RECORD_BYTES = 256 * 1024
MAX_RECORDS_PER_CYCLE = 20_000
SPOOL_NAME = re.compile(r"^events-\d{4}-\d{2}-\d{2}\.jsonl$")
_PREDICTION_REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "prediction_time_utc",
        "created_at_utc",
        "model_id",
        "commodity",
        "settlement",
        "estimated_price_toman",
    }
)


class CoinProcessorError(RuntimeError):
    """An operator-safe, payload-free processor failure."""


@dataclass(frozen=True, slots=True)
class CoinProcessorPaths:
    spool_directory: Path
    staging_database: Path
    market_database: Path
    corpus_database: Path
    feedback_database: Path | None
    prediction_database: Path | None


def _external_file(value: str, *, reason: str) -> Path:
    supplied = Path(value).expanduser()
    if supplied.is_symlink():
        raise CoinProcessorError(reason)
    path = supplied.resolve()
    if not path.is_file():
        raise CoinProcessorError(reason)
    return path


def _validate_prediction_schema(path: Path) -> None:
    connection = sqlite3.connect(
        path.as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(coin_estimate_predictions)"
            )
        }
        if not _PREDICTION_REQUIRED_COLUMNS.issubset(columns):
            raise CoinProcessorError("coin_processor_prediction_schema_invalid")
    finally:
        connection.close()


def _paths(*, mode: str, state_directory: Path) -> CoinProcessorPaths:
    capture_supplied = Path(
        os.environ.get("MARKET_PIPELINE_CAPTURE_ROOT", "/var/lib/market-data/capture")
    ).expanduser()
    if capture_supplied.is_symlink():
        raise CoinProcessorError("coin_processor_capture_root_invalid")
    capture_root = capture_supplied.resolve()
    spool_supplied = Path(
        os.environ.get(
            "MARKET_PROCESSOR_COIN_SPOOL_DIR",
            str(capture_root / "account2"),
        )
    ).expanduser()
    if spool_supplied.is_symlink():
        raise CoinProcessorError("coin_processor_spool_unavailable")
    spool = spool_supplied.resolve()
    if not spool.is_dir():
        raise CoinProcessorError("coin_processor_spool_unavailable")
    feedback_value = os.environ.get("MARKET_PROCESSOR_FEEDBACK_DB", "").strip()
    prediction_value = os.environ.get("MARKET_PROCESSOR_PREDICTION_DB", "").strip()
    if mode == "live" and (not feedback_value or not prediction_value):
        raise CoinProcessorError("coin_processor_causal_inputs_required")
    if mode == "live":
        feedback = _external_file(
            feedback_value, reason="coin_processor_feedback_unavailable"
        )
        prediction = _external_file(
            prediction_value,
            reason="coin_processor_prediction_ledger_unavailable",
        )
        _validate_prediction_schema(prediction)
    else:
        feedback_supplied = (
            Path(feedback_value).expanduser() if feedback_value else None
        )
        prediction_supplied = (
            Path(prediction_value).expanduser() if prediction_value else None
        )
        feedback_candidate = (
            feedback_supplied.resolve()
            if feedback_supplied is not None and not feedback_supplied.is_symlink()
            else None
        )
        prediction_candidate = (
            prediction_supplied.resolve()
            if prediction_supplied is not None and not prediction_supplied.is_symlink()
            else None
        )
        feedback = (
            feedback_candidate
            if feedback_candidate is not None
            and feedback_candidate.is_file()
            and not feedback_candidate.is_symlink()
            else None
        )
        prediction = (
            prediction_candidate
            if prediction_candidate is not None
            and prediction_candidate.is_file()
            and not prediction_candidate.is_symlink()
            else None
        )
    state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return CoinProcessorPaths(
        spool_directory=spool,
        staging_database=state_directory / "capture-staging.sqlite3",
        market_database=state_directory / "shadow-market.sqlite3",
        corpus_database=state_directory / "calibration-corpus.sqlite3",
        feedback_database=feedback,
        prediction_database=prediction,
    )


def _spool_files(directory: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(directory.iterdir())
        if SPOOL_NAME.fullmatch(path.name)
        and path.is_file()
        and not path.is_symlink()
    )


def _cursor(
    connection: sqlite3.Connection,
    *,
    path: Path,
    device: int,
    inode: int,
    size: int,
) -> int:
    row = connection.execute(
        "SELECT device,inode,byte_offset FROM capture_file_cursors "
        "WHERE stream='coin' AND file_path=?",
        (str(path),),
    ).fetchone()
    if row is None:
        return 0
    offset = int(row["byte_offset"])
    if int(row["device"]) != device or int(row["inode"]) != inode or offset > size:
        return 0
    return offset


def _save_cursor(
    connection: sqlite3.Connection,
    *,
    path: Path,
    device: int,
    inode: int,
    offset: int,
    updated_at_utc: str,
) -> None:
    connection.execute(
        """
        INSERT INTO capture_file_cursors(
          stream,file_path,device,inode,byte_offset,updated_at_utc
        ) VALUES('coin',?,?,?,?,?)
        ON CONFLICT(stream,file_path) DO UPDATE SET
          device=excluded.device,inode=excluded.inode,
          byte_offset=excluded.byte_offset,updated_at_utc=excluded.updated_at_utc
        """,
        (str(path), device, inode, offset, updated_at_utc),
    )


def _safe_rejection_reason(exc: BaseException) -> str:
    if isinstance(exc, CaptureEventContractError):
        return str(exc) or "capture_contract_invalid"
    if isinstance(exc, UnicodeDecodeError):
        return "capture_utf8_invalid"
    return "capture_json_invalid"


def _ingest_file(
    connection: sqlite3.Connection,
    *,
    path: Path,
    now_utc: str,
    remaining_records: int,
) -> dict[str, int]:
    stat = path.stat()
    size = int(stat.st_size)
    offset = _cursor(
        connection,
        path=path,
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        size=size,
    )
    counters = {
        "records": 0,
        "accepted": 0,
        "duplicates": 0,
        "changes": 0,
        "tombstones": 0,
        "rejected": 0,
    }
    with path.open("rb") as handle:
        handle.seek(offset)
        while counters["records"] < remaining_records and handle.tell() < size:
            start = handle.tell()
            raw = handle.readline(min(MAX_RECORD_BYTES + 2, size - start))
            if not raw:
                break
            if not raw.endswith(b"\n"):
                if len(raw) <= MAX_RECORD_BYTES:
                    handle.seek(start)
                    break
                complete = False
                while handle.tell() < size:
                    tail = handle.readline(
                        min(MAX_RECORD_BYTES + 2, size - handle.tell())
                    )
                    if tail.endswith(b"\n"):
                        complete = True
                        break
                if not complete:
                    handle.seek(start)
                    break
                record_capture_rejection(
                    connection,
                    stream="coin",
                    record_bytes=f"oversize:{path.name}:{start}".encode("ascii"),
                    reason="capture_record_too_large",
                    seen_at_utc=now_utc,
                )
                counters["records"] += 1
                counters["rejected"] += 1
                offset = handle.tell()
                continue
            counters["records"] += 1
            offset = handle.tell()
            try:
                document = json.loads(raw.decode("utf-8"))
                report = stage_capture_event(
                    connection,
                    decode_capture_event(document, stream="coin"),
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                CaptureEventContractError,
            ) as exc:
                record_capture_rejection(
                    connection,
                    stream="coin",
                    record_bytes=raw,
                    reason=_safe_rejection_reason(exc),
                    seen_at_utc=now_utc,
                )
                counters["rejected"] += 1
                continue
            counters["accepted"] += int(report.accepted)
            counters["duplicates"] += int(report.duplicate)
            counters["changes"] += int(report.staged_change)
            counters["tombstones"] += int(report.tombstone_applied)
    _save_cursor(
        connection,
        path=path,
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        offset=offset,
        updated_at_utc=now_utc,
    )
    return counters


def _load_causal_inputs(
    staging: sqlite3.Connection,
    paths: CoinProcessorPaths,
    *,
    mode: str,
    as_of_utc: str,
) -> tuple[Mapping[bytes, CoinGroupParserFeedback], tuple, dict[str, int]]:
    feedback = (
        load_coin_group_parser_feedback(
            paths.feedback_database,
            immutable=True,
        )
        if paths.feedback_database is not None
        else {}
    )
    if mode == "live" and paths.feedback_database is not None:
        # The loader intentionally returns an empty mapping for a malformed or
        # missing store.  The path was already checked, so verify its schema to
        # distinguish a valid empty corpus from silent contract drift.
        connection = sqlite3.connect(
            paths.feedback_database.as_uri() + "?mode=ro&immutable=1", uri=True
        )
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='coin_group_parser_feedback'"
            ).fetchone()
            if table is None:
                raise CoinProcessorError("coin_processor_feedback_schema_invalid")
        finally:
            connection.close()
    earliest = staging.execute(
        "SELECT MIN(event_time_utc) FROM coin_group_staged_messages "
        "WHERE available_at_utc<=? AND expires_at_utc>?",
        (as_of_utc, as_of_utc),
    ).fetchone()[0]
    dirty = staging.execute(
        "SELECT 1 FROM capture_dirty_groups WHERE available_at_utc<=? LIMIT 1",
        (as_of_utc,),
    ).fetchone()
    if earliest is None or paths.prediction_database is None or dirty is None:
        anchors: tuple = ()
        anchor_stats = {"rows_seen": 0, "rows_rejected": 0, "anchors": 0}
    else:
        as_of_stamp = datetime.fromisoformat(as_of_utc.replace("Z", "+00:00"))
        active_lower = as_of_stamp - COIN_GROUP_ACTIVE_REPLAY_WINDOW - timedelta(
            seconds=MAX_REPLY_AGE_SECONDS
        )
        earliest_stamp = datetime.fromisoformat(str(earliest).replace("Z", "+00:00"))
        bounded_earliest = (
            max(active_lower, earliest_stamp)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        loaded = load_coin_prediction_anchors(
            paths.prediction_database,
            earliest_event_time_utc=bounded_earliest,
            as_of_utc=as_of_utc,
            immutable=True,
        )
        anchors = loaded.anchors
        anchor_stats = {
            "rows_seen": loaded.rows_seen,
            "rows_rejected": loaded.rows_rejected,
            "anchors": len(loaded.anchors),
        }
    return feedback, anchors, {
        "feedback_rows": len(feedback),
        **anchor_stats,
    }


def _corpus_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


def process_coin_spool_cycle(
    *,
    paths: CoinProcessorPaths,
    mode: str,
    now_utc: str | None = None,
) -> dict[str, object]:
    """Run one restart-safe shadow cycle and return redacted counters only."""

    now = normalize_utc(
        now_utc or datetime.now(timezone.utc),
        field_name="coin_processor_now_utc",
    )
    staging = connect_coin_group_staging(paths.staging_database)
    market = connect_market_store(paths.market_database)
    corpus = _corpus_connection(paths.corpus_database)
    totals = {
        "records": 0,
        "accepted": 0,
        "duplicates": 0,
        "changes": 0,
        "tombstones": 0,
        "rejected": 0,
    }
    try:
        initialize_capture_adapter(staging)
        initialize_market_store(market)
        for path in _spool_files(paths.spool_directory):
            remaining = MAX_RECORDS_PER_CYCLE - totals["records"]
            if remaining <= 0:
                break
            report = _ingest_file(
                staging,
                path=path,
                now_utc=now,
                remaining_records=remaining,
            )
            for key, value in report.items():
                totals[key] += value
        # The raw event and byte cursor are one durable restart boundary.
        staging.commit()
        feedback, anchors, causal = _load_causal_inputs(
            staging,
            paths,
            mode=mode,
            as_of_utc=now,
        )
        corpus_report = append_coin_group_feedback_revisions(
            corpus,
            feedback.values(),
            parser_version_before=COIN_GROUP_PARSER_VERSION,
            appended_at_utc=now,
        )
        projection = project_capture_changes(
            staging,
            market,
            as_of_utc=now,
            group_additional_anchors=anchors,
            group_parser_feedback=feedback,
        )
        market.commit()
        staging.commit()
        corpus.commit()
    except BaseException:
        market.rollback()
        staging.rollback()
        corpus.rollback()
        raise
    finally:
        corpus.close()
        market.close()
        staging.close()
    group = projection.group_pipeline
    return {
        **totals,
        **causal,
        "corpus_revisions_appended": corpus_report.revisions_appended,
        "corpus_idempotent_replays": corpus_report.idempotent_replays,
        "group_messages_seen": group.staged_messages_seen if group else 0,
        "group_eligible_offers": group.eligible_offers if group else 0,
        "group_eligible_trades": group.eligible_trades if group else 0,
        "group_pending_or_rejected": (
            group.pending_or_rejected_offers + group.pending_or_rejected_trades
            if group
            else 0
        ),
        "raw_rows_purged": projection.raw_rows_purged,
    }


def run_coin_processor_service(
    *,
    role: str,
    mode: str,
    release_sha: str,
    state_directory: Path,
    stop: threading.Event,
) -> int:
    if role != "market-processor" or mode not in {"fixture", "live"}:
        raise CoinProcessorError("coin_processor_role_or_mode_invalid")
    paths = _paths(mode=mode, state_directory=state_directory)
    try:
        interval = float(os.environ.get("MARKET_PROCESSOR_INTERVAL_SECONDS", "1"))
    except ValueError as exc:
        raise CoinProcessorError("coin_processor_interval_invalid") from exc
    if not 0.25 <= interval <= 30:
        raise CoinProcessorError("coin_processor_interval_invalid")
    started_at = utc_text()
    health_path = state_directory / "health.json"

    def cycle_and_write(*, stopped: bool = False) -> None:
        counters = process_coin_spool_cycle(paths=paths, mode=mode)
        atomic_json_write(
            health_path,
            {
                "schema": PROCESSOR_HEARTBEAT_SCHEMA,
                "version": PROCESSOR_VERSION,
                "role": role,
                "mode": mode,
                "release_sha": release_sha,
                "pid": os.getpid(),
                "started_at_utc": started_at,
                "updated_at_utc": utc_text(),
                "status": (
                    f"{mode}-stopped"
                    if stopped
                    else ("live-shadow-ready" if mode == "live" else "fixture-ready")
                ),
                "sources": {"GROUP_1": "ready", "GROUP_2": "ready"},
                "adapter_version": CAPTURE_ADAPTER_VERSION,
                "parser_version": COIN_GROUP_PARSER_VERSION,
                "pipeline_version": COIN_GROUP_PIPELINE_VERSION,
                "calibration_corpus_version": CALIBRATION_CORPUS_VERSION,
                "shadow_only": True,
                "counters": counters,
            },
        )

    cycle_and_write()
    if os.environ.get(
        "MARKET_PROCESSOR_ONESHOT",
        os.environ.get("MARKET_PROCESSOR_FIXTURE_ONESHOT", ""),
    ).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return 0
    while not stop.wait(interval):
        cycle_and_write()
    cycle_and_write(stopped=True)
    return 0


__all__ = [
    "CoinProcessorError",
    "CoinProcessorPaths",
    "PROCESSOR_HEARTBEAT_SCHEMA",
    "PROCESSOR_VERSION",
    "process_coin_spool_cycle",
    "run_coin_processor_service",
]
