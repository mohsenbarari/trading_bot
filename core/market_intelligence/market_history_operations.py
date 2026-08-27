"""Fail-closed Stage 11 export, validation, and staging import operations."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Mapping, Sequence

from .market_history_backfill import (
    HistoryBackfillError,
    HistoryFactRecordV1,
    HistoryImportBundleV1,
    _scan_forbidden,
    import_history_bundle,
)
from .market_history_export import (
    EXPORT_CONTRACT,
    MarketHistoryExportError,
    export_market_history,
)
from .private_pipeline_contracts import content_hash


MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_BUNDLES = 4_096
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUNDLE_NAME_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]{2,63}-[0-9]{4}-[0-9a-f]{16}\.json$"
)
STAGING_PROJECT = "market-private-pipeline-stage13-shadow"
PROTECTED_EXPORT_CONFIRMATION = "WRITE_PROTECTED_HISTORY_EXPORT"


class MarketHistoryOperationError(RuntimeError):
    """An operator-safe history operation failure."""


@dataclass(frozen=True, slots=True)
class ValidatedExport:
    manifest_sha256: str
    record_count: int
    bundle_count: int
    source_counts: dict[str, int]
    bundle_paths: tuple[Path, ...]


def _read_json(path: Path, *, maximum_bytes: int) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise MarketHistoryOperationError("history_operation_file_invalid")
        size = path.stat().st_size
        if not 1 <= size <= maximum_bytes:
            raise MarketHistoryOperationError("history_operation_file_size_invalid")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketHistoryOperationError("history_operation_json_invalid") from exc
    if not isinstance(value, dict):
        raise MarketHistoryOperationError("history_operation_json_invalid")
    return value


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise MarketHistoryOperationError("history_operation_file_read_failed") from exc
    return digest.hexdigest()


def _protected_directory(path: Path) -> Path:
    resolved = path.resolve()
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not resolved.is_dir()
        or stat.S_IMODE(resolved.stat().st_mode) != 0o700
    ):
        raise MarketHistoryOperationError("history_operation_directory_not_protected")
    return resolved


def validate_export_directory(path: Path) -> ValidatedExport:
    """Validate all bytes and contracts before any database connection is opened."""

    directory = _protected_directory(path)
    manifest_path = directory / "manifest.json"
    if stat.S_IMODE(manifest_path.stat().st_mode) != 0o600:
        raise MarketHistoryOperationError("history_operation_manifest_mode_invalid")
    manifest = _read_json(manifest_path, maximum_bytes=MAX_MANIFEST_BYTES)
    if manifest.get("contract") != EXPORT_CONTRACT:
        raise MarketHistoryOperationError("history_operation_manifest_contract_invalid")
    if manifest.get("contains_raw_telegram_history") is not False:
        raise MarketHistoryOperationError("history_operation_raw_history_forbidden")
    if manifest.get("contains_participant_identity") is not False:
        raise MarketHistoryOperationError("history_operation_identity_forbidden")
    bundles = manifest.get("bundles")
    if not isinstance(bundles, list) or not 1 <= len(bundles) <= MAX_BUNDLES:
        raise MarketHistoryOperationError("history_operation_bundle_manifest_invalid")
    if manifest.get("bundle_count") != len(bundles):
        raise MarketHistoryOperationError("history_operation_bundle_count_mismatch")
    if content_hash(bundles) != manifest.get("bundle_manifest_hash"):
        raise MarketHistoryOperationError("history_operation_bundle_manifest_hash_mismatch")
    expected_names = {"manifest.json"}
    aggregate: dict[str, int] = {}
    bundle_paths: list[Path] = []
    for item in bundles:
        if not isinstance(item, Mapping):
            raise MarketHistoryOperationError("history_operation_bundle_manifest_invalid")
        filename = str(item.get("file") or "")
        if not BUNDLE_NAME_PATTERN.fullmatch(filename):
            raise MarketHistoryOperationError("history_operation_bundle_filename_invalid")
        if filename in expected_names:
            raise MarketHistoryOperationError("history_operation_duplicate_bundle_filename")
        expected_names.add(filename)
        bundle_path = directory / filename
        if (
            bundle_path.is_symlink()
            or not bundle_path.is_file()
            or stat.S_IMODE(bundle_path.stat().st_mode) != 0o600
        ):
            raise MarketHistoryOperationError("history_operation_bundle_mode_invalid")
        expected_file_hash = str(item.get("file_sha256") or "")
        if not SHA256_PATTERN.fullmatch(expected_file_hash):
            raise MarketHistoryOperationError("history_operation_bundle_hash_invalid")
        if _file_sha256(bundle_path) != expected_file_hash:
            raise MarketHistoryOperationError("history_operation_bundle_hash_mismatch")
        bundle_value = _read_json(bundle_path, maximum_bytes=MAX_BUNDLE_BYTES)
        try:
            bundle = HistoryImportBundleV1.model_validate(bundle_value)
        except Exception as exc:
            raise MarketHistoryOperationError("history_operation_bundle_contract_invalid") from exc
        if bundle.source_code != item.get("source_code"):
            raise MarketHistoryOperationError("history_operation_bundle_source_mismatch")
        if bundle.retention_mode != item.get("retention_mode"):
            raise MarketHistoryOperationError("history_operation_retention_mode_mismatch")
        if bundle.source_artifact_hash != item.get("source_artifact_hash"):
            raise MarketHistoryOperationError("history_operation_artifact_hash_mismatch")
        if len(bundle.records) != item.get("record_count"):
            raise MarketHistoryOperationError("history_operation_record_count_mismatch")
        for raw in bundle.records:
            try:
                _scan_forbidden(raw)
                record = HistoryFactRecordV1.model_validate(
                    raw,
                    context={
                        "allow_transient_seed": bundle.retention_mode
                        == "TRANSIENT_SEED"
                    },
                )
            except Exception as exc:
                raise MarketHistoryOperationError(
                    "history_operation_record_contract_invalid"
                ) from exc
            if record.source_code != bundle.source_code:
                raise MarketHistoryOperationError("history_operation_record_source_mismatch")
            if bundle.retention_mode == "TRANSIENT_SEED" and (
                record.encrypted_raw_text is not None or record.encrypted_participants
            ):
                raise MarketHistoryOperationError(
                    "history_operation_transient_sensitive_data_forbidden"
                )
        aggregate[bundle.source_code] = aggregate.get(bundle.source_code, 0) + len(
            bundle.records
        )
        bundle_paths.append(bundle_path)
    actual_names = {item.name for item in directory.iterdir()}
    if actual_names != expected_names:
        raise MarketHistoryOperationError("history_operation_unexpected_export_file")
    source_counts = manifest.get("source_counts")
    if source_counts != dict(sorted(aggregate.items())):
        raise MarketHistoryOperationError("history_operation_source_count_mismatch")
    for field_name in (
        "excluded_existing_counts",
        "omitted_unlinked_outcome_counts",
    ):
        counts = manifest.get(field_name)
        if (
            not isinstance(counts, dict)
            or set(counts) != set(aggregate)
            or any(not isinstance(value, int) or value < 0 for value in counts.values())
        ):
            raise MarketHistoryOperationError(
                f"history_operation_{field_name}_invalid"
            )
    return ValidatedExport(
        manifest_sha256=_file_sha256(manifest_path),
        record_count=sum(aggregate.values()),
        bundle_count=len(bundle_paths),
        source_counts=aggregate,
        bundle_paths=tuple(bundle_paths),
    )


def _parse_cutoffs(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        source, separator, cutoff = value.partition("=")
        source = source.strip().upper()
        cutoff = cutoff.strip()
        if not separator or not source or not cutoff or source in result:
            raise MarketHistoryOperationError("history_operation_source_cutoff_invalid")
        result[source] = cutoff
    return result


def _database_connection():
    import psycopg2

    secret_path = Path(
        os.environ.get(
            "MARKET_POSTGRES_PASSWORD_FILE",
            "/run/secrets/market_postgres_password",
        )
    )
    try:
        if secret_path.is_symlink() or not secret_path.is_file():
            raise MarketHistoryOperationError("history_operation_database_secret_invalid")
        password = secret_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise MarketHistoryOperationError("history_operation_database_secret_invalid") from exc
    if not password:
        raise MarketHistoryOperationError("history_operation_database_secret_invalid")
    try:
        return psycopg2.connect(
            host=os.environ.get("MARKET_POSTGRES_HOST", "market-database"),
            port=int(os.environ.get("MARKET_POSTGRES_PORT", "5432")),
            user=os.environ.get("MARKET_POSTGRES_USER", "market_data"),
            password=password,
            dbname=os.environ.get("MARKET_POSTGRES_DB", "market_archive"),
            connect_timeout=5,
            application_name="market-history-staging-import",
        )
    except (ValueError, psycopg2.Error) as exc:
        raise MarketHistoryOperationError("history_operation_database_unavailable") from exc


def _verify_staging_guard(backup_receipt: Path, confirmation: str) -> None:
    if confirmation != STAGING_PROJECT:
        raise MarketHistoryOperationError("history_operation_staging_confirmation_invalid")
    if os.environ.get("COMPOSE_PROJECT_NAME") != STAGING_PROJECT:
        raise MarketHistoryOperationError("history_operation_compose_project_invalid")
    if os.environ.get("MARKET_PIPELINE_FEED_MODE") != "PRIVATE_SHADOW":
        raise MarketHistoryOperationError("history_operation_feed_mode_not_shadow")
    if (
        not backup_receipt.is_absolute()
        or backup_receipt.is_symlink()
        or not backup_receipt.is_file()
        or stat.S_IMODE(backup_receipt.stat().st_mode) != 0o600
        or backup_receipt.stat().st_size == 0
    ):
        raise MarketHistoryOperationError("history_operation_backup_receipt_invalid")


def _run_export(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output_directory) if args.output_directory else None
    if output is not None and args.confirm_protected_output != PROTECTED_EXPORT_CONFIRMATION:
        raise MarketHistoryOperationError("history_operation_export_confirmation_invalid")
    if args.exclusion_store is None:
        raise MarketHistoryOperationError("history_operation_exclusion_store_required")
    report = export_market_history(
        source_store=Path(args.source_store),
        exclusion_store=Path(args.exclusion_store),
        source_codes=args.source,
        window_start_utc=args.window_start_utc,
        window_end_utc=args.window_end_utc,
        source_cutoffs_utc=_parse_cutoffs(args.source_cutoff),
        maximum_bundle_records=args.maximum_bundle_records,
        output_directory=output,
        allow_unlinked_private_gold_outcome_omission=(
            args.allow_unlinked_private_gold_outcome_omission
        ),
    )
    return {
        "status": "pass",
        "operation": "export" if output is not None else "dry-run",
        "source_record_count": report.source_record_count,
        "bundle_count": report.bundle_count,
        "source_counts": dict(sorted(report.source_counts.items())),
        "excluded_existing_counts": dict(
            sorted(report.excluded_existing_counts.items())
        ),
        "omitted_unlinked_outcome_counts": dict(
            sorted(report.omitted_unlinked_outcome_counts.items())
        ),
        "manifest_sha256": report.manifest_sha256,
    }


def _run_validate(args: argparse.Namespace) -> dict[str, Any]:
    validated = validate_export_directory(Path(args.directory))
    return {
        "status": "pass",
        "operation": "validate",
        "manifest_sha256": validated.manifest_sha256,
        "source_record_count": validated.record_count,
        "bundle_count": validated.bundle_count,
        "source_counts": dict(sorted(validated.source_counts.items())),
    }


def _run_import(args: argparse.Namespace) -> dict[str, Any]:
    directory = Path(args.directory)
    validated = validate_export_directory(directory)
    if (
        not SHA256_PATTERN.fullmatch(args.expected_manifest_sha256)
        or validated.manifest_sha256 != args.expected_manifest_sha256
    ):
        raise MarketHistoryOperationError("history_operation_manifest_hash_mismatch")
    _verify_staging_guard(Path(args.backup_receipt), args.confirm_staging_project)
    connection = _database_connection()
    imported = duplicates = quarantined = 0
    no_op_bundles = 0
    throttled_bundles = 0
    delay = float(args.inter_bundle_delay_seconds)
    if not 0 <= delay <= 60:
        raise MarketHistoryOperationError("history_operation_bundle_delay_invalid")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM market_data.schema_migrations WHERE version=2"
            )
            if cursor.fetchone() is None:
                raise MarketHistoryOperationError("history_operation_migration_missing")
        for path in validated.bundle_paths:
            result = import_history_bundle(
                connection,
                _read_json(path, maximum_bytes=MAX_BUNDLE_BYTES),
            )
            imported += int(result["imported_revision_count"])
            duplicates += int(result["duplicate_revision_count"])
            quarantined += int(result["quarantined_revision_count"])
            no_op_bundles += int(bool(result["no_op"]))
            if int(result["quarantined_revision_count"]) != 0:
                raise MarketHistoryOperationError("history_operation_quarantine_nonzero")
            if delay > 0 and not result["no_op"]:
                throttled_bundles += 1
                time.sleep(delay)
        if args.verify_idempotent_replay:
            for path in validated.bundle_paths:
                result = import_history_bundle(
                    connection,
                    _read_json(path, maximum_bytes=MAX_BUNDLE_BYTES),
                )
                if not result["no_op"] or int(result["quarantined_revision_count"]) != 0:
                    raise MarketHistoryOperationError(
                        "history_operation_idempotent_replay_failed"
                    )
    finally:
        connection.close()
    return {
        "status": "pass",
        "operation": "staging-import",
        "manifest_sha256": validated.manifest_sha256,
        "source_record_count": validated.record_count,
        "bundle_count": validated.bundle_count,
        "imported_revision_count": imported,
        "duplicate_revision_count": duplicates,
        "quarantined_revision_count": quarantined,
        "preexisting_no_op_bundle_count": no_op_bundles,
        "throttled_bundle_count": throttled_bundles,
        "inter_bundle_delay_seconds": delay,
        "idempotent_replay_verified": bool(args.verify_idempotent_replay),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--source-store", required=True)
    export.add_argument("--exclusion-store", required=True)
    export.add_argument("--source", action="append", required=True)
    export.add_argument("--window-start-utc", required=True)
    export.add_argument("--window-end-utc", required=True)
    export.add_argument("--source-cutoff", action="append", default=[])
    export.add_argument("--maximum-bundle-records", type=int, default=2_000)
    export.add_argument("--output-directory")
    export.add_argument("--confirm-protected-output")
    export.add_argument(
        "--allow-unlinked-private-gold-outcome-omission",
        action="store_true",
    )
    export.set_defaults(handler=_run_export)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--directory", required=True)
    validate.set_defaults(handler=_run_validate)
    importer = subparsers.add_parser("import-staging")
    importer.add_argument("--directory", required=True)
    importer.add_argument("--expected-manifest-sha256", required=True)
    importer.add_argument("--backup-receipt", required=True)
    importer.add_argument("--confirm-staging-project", required=True)
    importer.add_argument("--verify-idempotent-replay", action="store_true")
    importer.add_argument("--inter-bundle-delay-seconds", type=float, default=0.0)
    importer.set_defaults(handler=_run_import)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        result = args.handler(args)
    except (MarketHistoryOperationError, MarketHistoryExportError, HistoryBackfillError) as exc:
        print(
            json.dumps(
                {"status": "fail", "reason_code": str(exc)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    except OSError:
        print(
            json.dumps(
                {"status": "fail", "reason_code": "history_operation_io_failure"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MarketHistoryOperationError",
    "ValidatedExport",
    "build_parser",
    "main",
    "validate_export_directory",
]
