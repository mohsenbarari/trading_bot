"""Privacy-minimized export of legacy Market Store facts for Stage 11.

The exporter reads a transactionally pinned SQLite snapshot through an
attached read-only database.  It never selects raw Telegram text, sender data,
links, credentials, or channel metadata.  Output bundles are bounded so the
PostgreSQL importer does not need to hold an unbounded source in memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Iterator, Mapping, Sequence

from pydantic import ValidationError

from .market_fact_projection import (
    MarketFactProjectionError,
    _quality,
    _reason_codes,
    observation_payload,
)
from .market_history_backfill import (
    HistoryFactRecordV1,
    build_bundle,
)
from .private_pipeline_contracts import content_hash, load_source_registry


EXPORT_CONTRACT = "market_history_export_manifest/1.0"
EXPORT_VERSION = "market-history-export-v1"
SOURCE_SYSTEM = "LEGACY_MARKET_STORE_V1"
MAX_BUNDLE_RECORDS = 10_000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_BUNDLES = 4_096
_SOURCE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_COMMON_COLUMNS = (
    "id",
    "event_key",
    "source_code",
    "source_family",
    "event_time_utc",
    "available_at_utc",
    "tehran_datetime",
    "tehran_date",
    "tehran_minute",
    "tehran_weekday",
    "instrument",
    "market_label",
    "settlement_term",
    "trade_form",
    "event_type",
    "side",
    "price_value",
    "price_num",
    "price_unit",
    "currency",
    "quantity_value",
    "quantity_num",
    "quantity_unit",
    "parse_confidence",
    "parser_version",
    "quality_state",
    "quality_policy_version",
    "is_conditional",
    "attributes_json",
    "inserted_at_utc",
)


class MarketHistoryExportError(RuntimeError):
    """Payload-free export failure."""


def _projection_failure_code(source_code: str, exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors(include_url=False, include_context=False, include_input=False)[0]
        location = "_".join(str(item) for item in error.get("loc", ())) or "record"
        error_type = re.sub(r"[^a-z0-9_]+", "_", str(error.get("type", "invalid")))
        return f"history_export_projection_failed:{source_code}:{location}:{error_type}"
    sqlite_name = (
        str(getattr(exc, "sqlite_errorname", "")).lower()
        if isinstance(exc, sqlite3.Error)
        else ""
    )
    suffix = sqlite_name or type(exc).__name__.lower()
    return (
        "history_export_projection_failed:"
        f"{source_code}:{suffix}"
    )


@dataclass(frozen=True, slots=True)
class HistoryExportReport:
    source_record_count: int
    bundle_count: int
    source_counts: dict[str, int]
    source_min_occurred_at_utc: dict[str, str]
    source_max_occurred_at_utc: dict[str, str]
    excluded_existing_counts: dict[str, int]
    omitted_unlinked_outcome_counts: dict[str, int]
    manifest: dict[str, Any]
    manifest_sha256: str | None
    output_directory: str | None


def _utc_text(value: datetime | str, *, field_name: str) -> str:
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
    except ValueError as exc:
        raise MarketHistoryExportError(f"{field_name}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketHistoryExportError(f"{field_name}_timezone_required")
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _source_retention_mode(source_code: str) -> str:
    source = load_source_registry().by_code().get(source_code)
    if source is None or not source.transfer_to_bot:
        raise MarketHistoryExportError("history_export_source_not_transferable")
    return "PERMANENT_ARCHIVE" if source.permanent_archive else "TRANSIENT_SEED"


def _attach_observation_union(
    connection: sqlite3.Connection,
    *,
    schema: str,
    view_name: str,
    path: Path,
) -> None:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise MarketHistoryExportError("history_export_source_path_invalid")
    connection.execute(
        f"ATTACH DATABASE ? AS {schema}",
        (f"file:{path.resolve().as_posix()}?mode=ro",),
    )
    tables = {
        str(row[0])
        for row in connection.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type='table'"
        )
    }
    if not {"market_observations", "market_observations_archive"}.issubset(tables):
        raise MarketHistoryExportError("history_export_source_schema_invalid")
    columns = ",".join(_COMMON_COLUMNS)
    connection.execute(
        f"""
        CREATE TEMP VIEW {view_name} AS
        SELECT {columns}
        FROM {schema}.market_observations
        UNION ALL
        SELECT {columns}
        FROM {schema}.market_observations_archive archived
        WHERE NOT EXISTS (
          SELECT 1 FROM {schema}.market_observations current
          WHERE current.event_key=archived.event_key
        )
        """
    )


def _connect_union_view(
    path: Path,
    *,
    exclusion_store: Path | None,
    temporary_directory: Path | None,
) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        if temporary_directory is None:
            # Small offline fixtures may remain in memory. Operational exports
            # must provide a protected disk-backed directory through the CLI;
            # otherwise a full-history sort can consume the container limit.
            connection.execute("PRAGMA temp_store=MEMORY")
        else:
            scratch = temporary_directory.resolve()
            if (
                not temporary_directory.is_absolute()
                or temporary_directory.is_symlink()
                or not scratch.is_dir()
                or stat.S_IMODE(scratch.stat().st_mode) != 0o700
                or scratch.stat().st_uid != os.geteuid()
                or any(scratch.iterdir())
            ):
                raise MarketHistoryExportError(
                    "history_export_temporary_directory_invalid"
                )
            connection.execute("PRAGMA temp_store=FILE")
            escaped = str(scratch).replace("'", "''")
            connection.execute(f"PRAGMA temp_store_directory='{escaped}'")
            configured = connection.execute(
                "PRAGMA temp_store_directory"
            ).fetchone()
            if (
                connection.execute("PRAGMA temp_store").fetchone()[0] != 1
                or configured is None
                or Path(str(configured[0])).resolve() != scratch
            ):
                raise MarketHistoryExportError(
                    "history_export_disk_temp_store_unavailable"
                )
        _attach_observation_union(
            connection,
            schema="source",
            view_name="market_observations",
            path=path,
        )
        if exclusion_store is not None:
            if exclusion_store.resolve() == path.resolve():
                raise MarketHistoryExportError("history_export_exclusion_store_invalid")
            _attach_observation_union(
                connection,
                schema="exclusion",
                view_name="excluded_market_observations",
                path=exclusion_store,
            )
        connection.execute("BEGIN")
    except (sqlite3.Error, MarketHistoryExportError):
        connection.close()
        raise
    return connection


def _record_identity(row: sqlite3.Row) -> str:
    return sha256(
        b"legacy-market-store-record/1.0\0"
        + str(row["source_code"]).encode("ascii")
        + b"\0"
        + bytes(row["event_key"])
    ).hexdigest()


def _bounded_parser_version(value: str) -> tuple[str, bool]:
    normalized = value.strip()
    if not normalized:
        raise MarketHistoryExportError("history_export_parser_version_empty")
    if len(normalized) <= 96:
        return normalized, False
    return f"legacy-parser-sha256:{sha256(normalized.encode('utf-8')).hexdigest()}", True


def _history_record(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    allow_transient_seed: bool,
) -> dict[str, Any]:
    event_key = bytes(row["event_key"]).hex()
    attributes = json.loads(str(row["attributes_json"] or "{}"))
    if not isinstance(attributes, dict):
        raise MarketHistoryExportError("history_export_attributes_invalid")
    parser_version, parser_version_normalized = _bounded_parser_version(
        str(row["parser_version"])
    )
    reason_codes = list(_reason_codes(row, attributes))
    if parser_version_normalized:
        reason_codes.append("PARSER_VERSION_NORMALIZED")
    value: dict[str, Any] = {
        "contract": "market_history_fact/1.0",
        "lineage": {
            "source_record_id_hash": _record_identity(row),
            "source_revision": 1,
        },
        "event_key": event_key,
        "origin_event_key": event_key,
        "source_code": str(row["source_code"]),
        "occurred_at_utc": str(row["event_time_utc"]),
        "available_at_utc": str(row["available_at_utc"]),
        "parser_version": parser_version,
        "quality_state": _quality(str(row["quality_state"])),
        "quality_reason_codes": reason_codes,
        "payload": observation_payload(connection, row),
    }
    return HistoryFactRecordV1.model_validate(
        value,
        context={"allow_transient_seed": allow_transient_seed},
    ).model_dump(mode="json")


def _is_unlinked_private_gold_outcome(row: sqlite3.Row) -> bool:
    if (
        str(row["source_code"]) != "PRIVATE_GOLD_CHANNEL"
        or str(row["event_type"]) != "TRADE"
    ):
        return False
    try:
        attributes = json.loads(str(row["attributes_json"] or "{}"))
    except (TypeError, ValueError) as exc:
        raise MarketHistoryExportError("history_export_attributes_invalid") from exc
    return not (
        isinstance(attributes, dict)
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(attributes.get("root_offer_event_key") or ""),
        )
    )


def _rows_for_source(
    connection: sqlite3.Connection,
    *,
    source_code: str,
    start_utc: str,
    end_utc: str,
    exclude_existing: bool,
) -> Iterator[sqlite3.Row]:
    connection.execute(
        "CREATE TEMP TABLE IF NOT EXISTS history_export_dependencies("
        "event_key BLOB PRIMARY KEY)"
    )
    connection.execute("DELETE FROM history_export_dependencies")
    dependency_keys: set[bytes] = set()
    for row in connection.execute(
        """
        SELECT attributes_json FROM market_observations
        WHERE source_code=? AND event_type='TRADE'
          AND event_time_utc>=? AND event_time_utc<?
        """,
        (source_code, start_utc, end_utc),
    ):
        try:
            attributes = json.loads(str(row["attributes_json"] or "{}"))
            key = str(attributes.get("root_offer_event_key") or "")
        except (TypeError, ValueError, AttributeError) as exc:
            raise MarketHistoryExportError("history_export_attributes_invalid") from exc
        if re.fullmatch(r"[0-9a-f]{64}", key):
            raw = bytes.fromhex(key)
            in_window = connection.execute(
                """
                SELECT 1 FROM market_observations
                WHERE event_key=? AND source_code=?
                  AND event_time_utc>=? AND event_time_utc<?
                """,
                (raw, source_code, start_utc, end_utc),
            ).fetchone()
            if in_window is None:
                dependency_keys.add(raw)
    for key in sorted(dependency_keys):
        dependency = connection.execute(
            "SELECT * FROM market_observations WHERE event_key=?",
            (key,),
        ).fetchone()
        if dependency is None or str(dependency["source_code"]) != source_code:
            raise MarketHistoryExportError("history_export_offer_dependency_missing")
    connection.executemany(
        "INSERT INTO history_export_dependencies(event_key) VALUES(?)",
        ((key,) for key in sorted(dependency_keys)),
    )
    exclusion_clause = (
        "AND NOT EXISTS (SELECT 1 FROM excluded_market_observations excluded "
        "WHERE excluded.source_code=market_observations.source_code "
        "AND excluded.event_key=market_observations.event_key)"
        if exclude_existing
        else ""
    )
    return iter(
        connection.execute(
            f"""
            SELECT * FROM market_observations
            WHERE source_code=? AND (
              (event_time_utc>=? AND event_time_utc<?)
              OR event_key IN (SELECT event_key FROM history_export_dependencies)
            )
            {exclusion_clause}
            ORDER BY event_time_utc,
              CASE event_type WHEN 'OFFER' THEN 0 WHEN 'TRADE' THEN 1 ELSE 2 END,
              event_key
            """,
            (source_code, start_utc, end_utc),
        )
    )


def _protected_output_directory(path: Path) -> None:
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise MarketHistoryExportError("history_export_output_path_invalid")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise MarketHistoryExportError("history_export_output_parent_invalid")
    info = parent.stat()
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise MarketHistoryExportError("history_export_output_parent_mode_invalid")
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)


def _private_json_payload(
    value: Mapping[str, Any],
    *,
    maximum_bytes: int,
    limit_reason: str,
) -> bytes:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(payload) > maximum_bytes:
        raise MarketHistoryExportError(limit_reason)
    return payload


def _write_private_payload(path: Path, payload: bytes) -> str:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)
    return sha256(payload).hexdigest()


def export_market_history(
    *,
    source_store: Path,
    source_codes: Sequence[str],
    window_start_utc: datetime | str,
    window_end_utc: datetime | str,
    source_cutoffs_utc: Mapping[str, datetime | str],
    maximum_bundle_records: int = 2_000,
    output_directory: Path | None = None,
    exclusion_store: Path | None = None,
    temporary_directory: Path | None = None,
    allow_unlinked_private_gold_outcome_omission: bool = False,
) -> HistoryExportReport:
    """Validate and optionally write bounded, fact-only history bundles."""

    if not 1 <= maximum_bundle_records <= MAX_BUNDLE_RECORDS:
        raise MarketHistoryExportError("history_export_bundle_limit_invalid")
    start = _utc_text(window_start_utc, field_name="history_export_window_start")
    end = _utc_text(window_end_utc, field_name="history_export_window_end")
    if start >= end:
        raise MarketHistoryExportError("history_export_window_invalid")
    sources = tuple(dict.fromkeys(str(item).strip().upper() for item in source_codes))
    if not sources or any(not _SOURCE_CODE.fullmatch(item) for item in sources):
        raise MarketHistoryExportError("history_export_sources_invalid")
    cutoffs = {
        source: min(
            end,
            _utc_text(
                source_cutoffs_utc.get(source, end),
                field_name="history_export_source_cutoff",
            ),
        )
        for source in sources
    }
    if any(cutoffs[source] <= start for source in sources):
        raise MarketHistoryExportError("history_export_source_cutoff_invalid")
    if (
        output_directory is not None
        and temporary_directory is not None
        and output_directory.resolve() == temporary_directory.resolve()
    ):
        raise MarketHistoryExportError(
            "history_export_output_and_temporary_directory_conflict"
        )
    if output_directory is not None:
        _protected_output_directory(output_directory)

    connection = _connect_union_view(
        source_store,
        exclusion_store=exclusion_store,
        temporary_directory=temporary_directory,
    )
    source_counts: dict[str, int] = {}
    minimums: dict[str, str] = {}
    maximums: dict[str, str] = {}
    excluded_counts: dict[str, int] = {}
    omitted_counts: dict[str, int] = {}
    files: list[dict[str, Any]] = []

    def emit_bundle(
        source: str,
        retention_mode: str,
        part: int,
        records: list[dict[str, Any]],
    ) -> None:
        bundle = build_bundle(
            source_code=source,
            source_system=SOURCE_SYSTEM,
            retention_mode=retention_mode,
            records=records,
        ).model_dump(mode="json")
        if len(files) >= MAX_BUNDLES:
            raise MarketHistoryExportError("history_export_bundle_count_exceeded")
        bundle_payload = _private_json_payload(
            bundle,
            maximum_bytes=MAX_BUNDLE_BYTES,
            limit_reason="history_export_bundle_bytes_exceeded",
        )
        artifact_hash = str(bundle["source_artifact_hash"])
        filename = f"{source.lower()}-{part:04d}-{artifact_hash[:16]}.json"
        item = {
            "file": filename,
            "source_code": source,
            "retention_mode": bundle["retention_mode"],
            "record_count": len(bundle["records"]),
            "source_artifact_hash": artifact_hash,
        }
        if output_directory is not None:
            item["file_sha256"] = _write_private_payload(
                output_directory / filename, bundle_payload
            )
        files.append(item)

    try:
        for source in sources:
            retention_mode = _source_retention_mode(source)
            excluded_counts[source] = (
                int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM market_observations source
                        WHERE source.source_code=?
                          AND source.event_time_utc>=? AND source.event_time_utc<?
                          AND EXISTS (
                            SELECT 1 FROM excluded_market_observations excluded
                            WHERE excluded.source_code=source.source_code
                              AND excluded.event_key=source.event_key
                          )
                        """,
                        (source, start, cutoffs[source]),
                    ).fetchone()[0]
                )
                if exclusion_store is not None
                else 0
            )
            rows = _rows_for_source(
                connection,
                source_code=source,
                start_utc=start,
                end_utc=cutoffs[source],
                exclude_existing=exclusion_store is not None,
            )
            count = 0
            omitted = 0
            part = 0
            chunk: list[dict[str, Any]] = []
            minimum: str | None = None
            maximum: str | None = None
            for row in rows:
                if _is_unlinked_private_gold_outcome(row):
                    if not allow_unlinked_private_gold_outcome_omission:
                        raise MarketHistoryExportError(
                            "history_export_unlinked_private_gold_outcome"
                        )
                    omitted += 1
                    continue
                record = _history_record(
                    connection,
                    row,
                    allow_transient_seed=retention_mode == "TRANSIENT_SEED",
                )
                occurred = str(record["occurred_at_utc"])
                minimum = occurred if minimum is None else min(minimum, occurred)
                maximum = occurred if maximum is None else max(maximum, occurred)
                chunk.append(record)
                count += 1
                if len(chunk) == maximum_bundle_records:
                    part += 1
                    emit_bundle(source, retention_mode, part, chunk)
                    chunk = []
            if chunk:
                part += 1
                emit_bundle(source, retention_mode, part, chunk)
            if count == 0 or minimum is None or maximum is None:
                raise MarketHistoryExportError("history_export_source_empty")
            source_counts[source] = count
            omitted_counts[source] = omitted
            minimums[source] = minimum
            maximums[source] = maximum
    except (sqlite3.Error, ValueError, TypeError, MarketFactProjectionError) as exc:
        raise MarketHistoryExportError(_projection_failure_code(source, exc)) from exc
    finally:
        connection.rollback()
        connection.close()
    if temporary_directory is not None and any(
        temporary_directory.resolve().iterdir()
    ):
        raise MarketHistoryExportError(
            "history_export_temporary_directory_not_empty_after_close"
        )

    manifest: dict[str, Any] = {
        "contract": EXPORT_CONTRACT,
        "export_version": EXPORT_VERSION,
        "source_system": SOURCE_SYSTEM,
        "window_start_utc": start,
        "window_end_utc": end,
        "contains_raw_telegram_history": False,
        "contains_participant_identity": False,
        "source_counts": dict(sorted(source_counts.items())),
        "source_min_occurred_at_utc": dict(sorted(minimums.items())),
        "source_max_occurred_at_utc": dict(sorted(maximums.items())),
        "excluded_existing_counts": dict(sorted(excluded_counts.items())),
        "omitted_unlinked_outcome_counts": dict(sorted(omitted_counts.items())),
        "source_cutoffs_utc": dict(sorted(cutoffs.items())),
        "bundle_count": len(files),
        "bundle_manifest_hash": content_hash(files),
        "bundles": files,
    }
    manifest_sha: str | None = None
    manifest_payload = _private_json_payload(
        manifest,
        maximum_bytes=MAX_MANIFEST_BYTES,
        limit_reason="history_export_manifest_bytes_exceeded",
    )
    if output_directory is not None:
        manifest_sha = _write_private_payload(
            output_directory / "manifest.json", manifest_payload
        )
        directory_descriptor = os.open(output_directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    return HistoryExportReport(
        source_record_count=sum(source_counts.values()),
        bundle_count=len(files),
        source_counts=source_counts,
        source_min_occurred_at_utc=minimums,
        source_max_occurred_at_utc=maximums,
        excluded_existing_counts=excluded_counts,
        omitted_unlinked_outcome_counts=omitted_counts,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        output_directory=str(output_directory) if output_directory is not None else None,
    )


__all__ = [
    "EXPORT_CONTRACT",
    "EXPORT_VERSION",
    "HistoryExportReport",
    "MarketHistoryExportError",
    "export_market_history",
]
