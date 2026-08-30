"""Docker-native shadow processor for both market capture accounts.

The processor is deliberately downstream from capture: it reads the Account 1
market-channel and Account 2 coin-group durable JSONL spools, keeps raw text in
its three-day staging SQLite, and writes redacted shadow Market Store facts.
Live mode requires both coin-group causal sidecars; silently parsing without
either input would change unnamed-instrument decisions compared with production.
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
    projection_reconciliation_pending,
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
    mark_coin_group_parser_feedback_applied,
)
from .coin_group_pipeline import COIN_GROUP_PIPELINE_VERSION
from .coin_group_review_projection import (
    CoinGroupReviewProjectionError,
    project_coin_group_reviews,
    reconcile_pending_trades_from_reviewed_roots,
)
from .coin_group_staging import connect_coin_group_staging
from .coin_group_trades import MAX_REPLY_AGE_SECONDS
from .coin_groups import COIN_GROUP_PARSER_VERSION
from .coin_prediction_anchors import load_coin_prediction_anchors
from .external_quote_capture import (
    EXTERNAL_CAPTURE_VERSION,
    ExternalQuoteCaptureError,
    decode_quote_event,
)
from .market_input_materializer import (
    INPUT_LEDGER_VERSION,
    materialize_input_snapshot,
)
from .market_fact_projection import export_market_store_facts
from .market_contracts import normalize_utc
from .market_store import connect_market_store, initialize_market_store, upsert_observation
from .private_gold import PRIVATE_GOLD_PARSER_VERSION
from .private_gold_trade_revisions import PRIVATE_GOLD_TRADE_REVISION_VERSION
from .private_pipeline_foundation import atomic_json_write, utc_text
from .public_telegram.parser import PARSER_VERSION as PUBLIC_MARKET_PARSER_VERSION
from .research_archive import ResearchArchiveError, ResearchArchiveKey


PROCESSOR_HEARTBEAT_SCHEMA = "market_processor/4.0"
PROCESSOR_VERSION = "market-processor-v4-fact-archive-shadow"
MAX_RECORD_BYTES = 256 * 1024
MAX_RECORDS_PER_CYCLE = 20_000
SPOOL_NAME = re.compile(r"^events-\d{4}-\d{2}-\d{2}\.jsonl$")
PROCESSOR_SOURCES = frozenset(
    {
        "GROUP_1",
        "GROUP_2",
        "MELTED_PRIMARY_FLOW",
        "MELTED_AGGREGATE",
        "MELTED_FLOW",
        "USD_HERAT",
        "XAUUSD",
        "WALLEX_PUBLIC_API",
        "BINANCE_PAXG_PUBLIC_API",
    }
)
COIN_PROCESSOR_SOURCES = frozenset({"GROUP_1", "GROUP_2"})
MARKET_PROCESSOR_SOURCES = frozenset(
    {
        "MELTED_PRIMARY_FLOW",
        "MELTED_AGGREGATE",
        "MELTED_FLOW",
        "USD_HERAT",
        "XAUUSD",
    }
)
EXTERNAL_PROCESSOR_SOURCES = frozenset(
    {"WALLEX_PUBLIC_API", "BINANCE_PAXG_PUBLIC_API"}
)
TEMPORARY_PUBLIC_MELTED_SOURCES = frozenset(
    {"MELTED_AGGREGATE", "MELTED_FLOW"}
)
TEMPORARY_PUBLIC_MELTED_RETENTION = timedelta(days=3)
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
    market_spool_directory: Path | None = None
    external_spool_directory: Path | None = None


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
    market_spool_supplied = Path(
        os.environ.get(
            "MARKET_PROCESSOR_MARKET_SPOOL_DIR",
            str(capture_root / "account1"),
        )
    ).expanduser()
    if market_spool_supplied.is_symlink():
        raise CoinProcessorError("market_processor_spool_unavailable")
    market_spool_candidate = market_spool_supplied.resolve()
    if mode == "live" and not market_spool_candidate.is_dir():
        raise CoinProcessorError("market_processor_spool_unavailable")
    market_spool = (
        market_spool_candidate if market_spool_candidate.is_dir() else None
    )
    external_spool_supplied = Path(
        os.environ.get(
            "MARKET_PROCESSOR_EXTERNAL_SPOOL_DIR",
            str(capture_root / "external"),
        )
    ).expanduser()
    if external_spool_supplied.is_symlink():
        raise CoinProcessorError("external_processor_spool_unavailable")
    external_spool_candidate = external_spool_supplied.resolve()
    if mode == "live" and not external_spool_candidate.is_dir():
        raise CoinProcessorError("external_processor_spool_unavailable")
    external_spool = (
        external_spool_candidate if external_spool_candidate.is_dir() else None
    )
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
        market_spool_directory=market_spool,
        external_spool_directory=external_spool,
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
    stream: str,
    path: Path,
    device: int,
    inode: int,
    size: int,
) -> int:
    row = connection.execute(
        "SELECT device,inode,byte_offset FROM capture_file_cursors "
        "WHERE stream=? AND file_path=?",
        (stream, str(path)),
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
    stream: str,
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
        ) VALUES(?,?,?,?,?,?)
        ON CONFLICT(stream,file_path) DO UPDATE SET
          device=excluded.device,inode=excluded.inode,
          byte_offset=excluded.byte_offset,updated_at_utc=excluded.updated_at_utc
        """,
        (stream, str(path), device, inode, offset, updated_at_utc),
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
    stream: str,
    path: Path,
    now_utc: str,
    remaining_records: int,
) -> dict[str, int]:
    stat = path.stat()
    size = int(stat.st_size)
    offset = _cursor(
        connection,
        stream=stream,
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
                    stream=stream,
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
                    decode_capture_event(document, stream=stream),
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                CaptureEventContractError,
            ) as exc:
                record_capture_rejection(
                    connection,
                    stream=stream,
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
        stream=stream,
        path=path,
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        offset=offset,
        updated_at_utc=now_utc,
    )
    return counters


def _ingest_external_file(
    staging: sqlite3.Connection,
    market: sqlite3.Connection,
    *,
    path: Path,
    now_utc: str,
    remaining_records: int,
) -> dict[str, int]:
    """Ingest minimized API quotes without mixing them into Telegram staging."""

    stat = path.stat()
    size = int(stat.st_size)
    offset = _cursor(
        staging,
        stream="external",
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
                    staging,
                    stream="external",
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
                _event_id, observation = decode_quote_event(document)
                existing = market.execute(
                    "SELECT 1 FROM market_observations WHERE event_key=?",
                    (observation.event_key,),
                ).fetchone()
                upsert_observation(market, observation)
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                ExternalQuoteCaptureError,
                ValueError,
            ) as exc:
                record_capture_rejection(
                    staging,
                    stream="external",
                    record_bytes=raw,
                    reason=(
                        str(exc)
                        if isinstance(exc, ExternalQuoteCaptureError)
                        else "external_capture_record_invalid"
                    ),
                    seen_at_utc=now_utc,
                )
                counters["rejected"] += 1
                continue
            if existing is None:
                counters["accepted"] += 1
                counters["changes"] += 1
            else:
                counters["duplicates"] += 1
    _save_cursor(
        staging,
        stream="external",
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
    reconciliation_pending = projection_reconciliation_pending(staging)
    if (
        earliest is None
        or paths.prediction_database is None
        or (dirty is None and not reconciliation_pending)
    ):
        anchors: tuple = ()
        anchor_stats = {"rows_seen": 0, "rows_rejected": 0, "anchors": 0}
    else:
        as_of_stamp = datetime.fromisoformat(as_of_utc.replace("Z", "+00:00"))
        active_lower = as_of_stamp - COIN_GROUP_ACTIVE_REPLAY_WINDOW - timedelta(
            seconds=MAX_REPLY_AGE_SECONDS
        )
        earliest_stamp = datetime.fromisoformat(str(earliest).replace("Z", "+00:00"))
        anchor_start = (
            earliest_stamp
            if reconciliation_pending
            else max(active_lower, earliest_stamp)
        )
        bounded_earliest = (
            anchor_start
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        loaded = load_coin_prediction_anchors(
            paths.prediction_database,
            earliest_event_time_utc=bounded_earliest,
            as_of_utc=as_of_utc,
            immutable=mode != "live",
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


def _purge_temporary_public_melted(
    market: sqlite3.Connection,
    *,
    as_of_utc: str,
) -> int:
    cutoff = (
        datetime.fromisoformat(as_of_utc.replace("Z", "+00:00"))
        - TEMPORARY_PUBLIC_MELTED_RETENTION
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    placeholders = ",".join("?" for _ in TEMPORARY_PUBLIC_MELTED_SOURCES)
    parameters = (*sorted(TEMPORARY_PUBLIC_MELTED_SOURCES), cutoff)
    purged = 0
    for table in ("market_observations", "market_observations_archive"):
        result = market.execute(
            f"DELETE FROM {table} WHERE source_code IN ({placeholders}) "
            "AND available_at_utc<=?",
            parameters,
        )
        purged += max(0, int(result.rowcount or 0))
    return purged


def _archive_connection():
    import psycopg2

    secret_path = Path(
        os.environ.get(
            "MARKET_POSTGRES_PASSWORD_FILE",
            "/run/secrets/market_postgres_password",
        )
    )
    try:
        password = secret_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CoinProcessorError("market_processor_archive_secret_unavailable") from exc
    if not password:
        raise CoinProcessorError("market_processor_archive_secret_unavailable")
    try:
        return psycopg2.connect(
            host=os.environ.get("MARKET_POSTGRES_HOST", "market-database"),
            port=int(os.environ.get("MARKET_POSTGRES_PORT", "5432")),
            user=os.environ.get("MARKET_POSTGRES_USER", "market_data"),
            password=password,
            dbname=os.environ.get("MARKET_POSTGRES_DB", "market_archive"),
            connect_timeout=5,
            application_name="market-processor-archive",
        )
    except psycopg2.Error as exc:
        raise CoinProcessorError("market_processor_archive_unavailable") from exc


def _research_archive_key() -> ResearchArchiveKey | None:
    if os.environ.get("MARKET_RESEARCH_ARCHIVE_ENABLED", "0").strip() != "1":
        return None
    path = Path(
        os.environ.get(
            "MARKET_RESEARCH_ENCRYPTION_KEY_FILE",
            "/run/secrets/market_research_encryption_key",
        )
    )
    key_id = os.environ.get(
        "MARKET_RESEARCH_ENCRYPTION_KEY_ID", "market-research:v1"
    ).strip()
    try:
        return ResearchArchiveKey.from_file(path, key_id=key_id)
    except ResearchArchiveError as exc:
        raise CoinProcessorError(str(exc)) from exc


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
    totals: dict[str, int] = {
        "records": 0,
        "accepted": 0,
        "duplicates": 0,
        "changes": 0,
        "tombstones": 0,
        "rejected": 0,
    }
    stream_totals = {
        stream: {key: 0 for key in totals}
        for stream in ("market", "coin", "external")
    }
    applied_feedback_keys: tuple[bytes, ...] = ()
    review_projection = None
    trade_reconciliation = None
    try:
        initialize_capture_adapter(staging)
        initialize_market_store(market)
        spool_sources = (
            (("market", paths.market_spool_directory),)
            if paths.market_spool_directory is not None
            else ()
        ) + (("coin", paths.spool_directory),)
        # Each account receives its own cycle budget.  A high-volume XAU feed
        # must never starve the two coin groups, or vice versa.
        for stream, directory in spool_sources:
            for path in _spool_files(directory):
                remaining = MAX_RECORDS_PER_CYCLE - stream_totals[stream]["records"]
                if remaining <= 0:
                    break
                report = _ingest_file(
                    staging,
                    stream=stream,
                    path=path,
                    now_utc=now,
                    remaining_records=remaining,
                )
                for key, value in report.items():
                    stream_totals[stream][key] += value
                    totals[key] += value
        if paths.external_spool_directory is not None:
            for path in _spool_files(paths.external_spool_directory):
                remaining = MAX_RECORDS_PER_CYCLE - stream_totals["external"]["records"]
                if remaining <= 0:
                    break
                report = _ingest_external_file(
                    staging,
                    market,
                    path=path,
                    now_utc=now,
                    remaining_records=remaining,
                )
                for key, value in report.items():
                    stream_totals["external"][key] += value
                    totals[key] += value
            # Commit the idempotent fact before its byte cursor.  A crash in
            # between replays the same opaque event key; the inverse order
            # could skip an observation permanently.
            market.commit()
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
        pipeline_applied = set(
            projection.group_pipeline.applied_feedback_event_keys
            if projection.group_pipeline is not None
            else ()
        )
        pending_exact_reviews = tuple(
            item
            for item in feedback.values()
            if item.applied_revision < item.review_revision
            and item.event_key not in pipeline_applied
        )
        try:
            review_projection = project_coin_group_reviews(
                market,
                pending_exact_reviews,
            )
        except CoinGroupReviewProjectionError as exc:
            raise CoinProcessorError(str(exc)) from exc
        applied_feedback_keys = tuple(
            sorted(pipeline_applied | set(review_projection.event_keys))
        )
        if pending_exact_reviews:
            try:
                trade_reconciliation = reconcile_pending_trades_from_reviewed_roots(
                    market,
                    cutoff_utc=min(
                        item.source_event_time_utc for item in pending_exact_reviews
                    ),
                )
            except CoinGroupReviewProjectionError as exc:
                raise CoinProcessorError(str(exc)) from exc
        temporary_public_melted_purged = _purge_temporary_public_melted(
            market,
            as_of_utc=now,
        )
        input_snapshot = materialize_input_snapshot(market, as_of_utc=now)
        outcome_counts = {
            str(row["status"]): int(row["total"])
            for row in staging.execute(
                "SELECT status,COUNT(*) AS total "
                "FROM capture_primary_trade_outcomes GROUP BY status"
            ).fetchall()
        }
        market.commit()
        staging.commit()
        corpus.commit()
        archive_report = None
        if os.environ.get("MARKET_PROCESSOR_ARCHIVE_ENABLED", "0").strip() == "1":
            research_key = _research_archive_key()
            archive = _archive_connection()
            try:
                with archive:
                    archive_report = export_market_store_facts(
                        market,
                        archive,
                        capture_staging=staging,
                        research_key=research_key,
                    )
                # Advance the local export ledger only after PostgreSQL has
                # committed the fact and outbox item in one transaction.
                market.commit()
            except BaseException:
                market.rollback()
                raise
            finally:
                archive.close()
    except BaseException:
        market.rollback()
        staging.rollback()
        corpus.rollback()
        raise
    finally:
        corpus.close()
        market.close()
        staging.close()
    if paths.feedback_database is not None and applied_feedback_keys:
        mark_coin_group_parser_feedback_applied(
            paths.feedback_database,
            applied_feedback_keys,
        )
    group = projection.group_pipeline
    return {
        **totals,
        "stream_records": {
            stream: values["records"] for stream, values in stream_totals.items()
        },
        "stream_rejected": {
            stream: values["rejected"] for stream, values in stream_totals.items()
        },
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
        "feedback_reviews_projected": (
            review_projection.projected if review_projection else 0
        ),
        "feedback_reviews_unchanged": (
            review_projection.unchanged if review_projection else 0
        ),
        "feedback_reviews_marked_applied": len(applied_feedback_keys),
        "reviewed_root_trades_projected": (
            trade_reconciliation.projected if trade_reconciliation else 0
        ),
        "reviewed_root_trades_eligible": (
            trade_reconciliation.eligible if trade_reconciliation else 0
        ),
        "reviewed_root_trades_rejected": (
            trade_reconciliation.rejected if trade_reconciliation else 0
        ),
        "market_messages_reprojected": projection.market_messages_reprojected,
        "market_facts_upserted": projection.market_facts_upserted,
        "market_facts_retracted": projection.market_facts_retracted,
        "private_paper_minutes_refreshed": projection.private_paper_minutes_refreshed,
        "private_trade_facts_upserted": projection.private_trade_facts_upserted,
        "private_trade_messages_finalized": projection.private_trade_messages_finalized,
        "private_trade_messages_ambiguous": projection.private_trade_messages_ambiguous,
        "private_trade_outcomes": outcome_counts,
        "temporary_public_melted_facts_purged": temporary_public_melted_purged,
        "input_snapshot_hash": input_snapshot.hash_hex,
        "input_snapshot_inserted": input_snapshot.inserted,
        "input_component_count": len(input_snapshot.components),
        "input_component_no_data": sum(
            component.consumed_value is None
            for component in input_snapshot.components
        ),
        "raw_rows_purged": projection.raw_rows_purged,
        "archive_selected": archive_report.selected if archive_report else 0,
        "archive_published": archive_report.published if archive_report else 0,
        "archive_unchanged": archive_report.unchanged if archive_report else 0,
        "archive_rejected": archive_report.rejected if archive_report else 0,
        "research_contexts_required": (
            archive_report.research_contexts_required if archive_report else 0
        ),
        "research_contexts_archived": (
            archive_report.research_contexts_archived if archive_report else 0
        ),
        "research_contexts_unavailable": (
            archive_report.research_contexts_unavailable if archive_report else 0
        ),
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
    last_projection_causal_inputs = {
        "feedback_rows": 0,
        "prediction_rows_seen": 0,
        "prediction_rows_rejected": 0,
        "anchors": 0,
    }
    try:
        previous_health = json.loads(health_path.read_text(encoding="utf-8"))
        previous_causal = previous_health.get("last_projection_causal_inputs")
        if (
            previous_health.get("schema") == PROCESSOR_HEARTBEAT_SCHEMA
            and previous_health.get("release_sha") == release_sha
            and isinstance(previous_causal, dict)
            and all(
                isinstance(previous_causal.get(field), int)
                and int(previous_causal[field]) >= 0
                for field in last_projection_causal_inputs
            )
        ):
            last_projection_causal_inputs = {
                field: int(previous_causal[field])
                for field in last_projection_causal_inputs
            }
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        pass

    def cycle_and_write(*, stopped: bool = False) -> None:
        nonlocal last_projection_causal_inputs
        counters = process_coin_spool_cycle(paths=paths, mode=mode)
        if (
            int(counters["changes"])
            or int(counters["tombstones"])
            or int(counters["feedback_reviews_projected"])
        ):
            last_projection_causal_inputs = {
                "feedback_rows": int(counters["feedback_rows"]),
                "prediction_rows_seen": int(counters["rows_seen"]),
                "prediction_rows_rejected": int(counters["rows_rejected"]),
                "anchors": int(counters["anchors"]),
            }
        active_sources = set(COIN_PROCESSOR_SOURCES)
        if paths.market_spool_directory is not None:
            active_sources.update(MARKET_PROCESSOR_SOURCES)
        if paths.external_spool_directory is not None:
            active_sources.update(EXTERNAL_PROCESSOR_SOURCES)
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
                "sources": {
                    source: "ready"
                    for source in sorted(active_sources)
                },
                "adapter_version": CAPTURE_ADAPTER_VERSION,
                "parser_version": COIN_GROUP_PARSER_VERSION,
                "public_parser_version": PUBLIC_MARKET_PARSER_VERSION,
                "private_gold_parser_version": PRIVATE_GOLD_PARSER_VERSION,
                "private_gold_trade_version": PRIVATE_GOLD_TRADE_REVISION_VERSION,
                "external_capture_version": EXTERNAL_CAPTURE_VERSION,
                "input_ledger_version": INPUT_LEDGER_VERSION,
                "pipeline_version": COIN_GROUP_PIPELINE_VERSION,
                "calibration_corpus_version": CALIBRATION_CORPUS_VERSION,
                "shadow_only": True,
                "counters": counters,
                "last_projection_causal_inputs": last_projection_causal_inputs,
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
