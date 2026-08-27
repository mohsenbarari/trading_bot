import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from core.market_intelligence import market_history_export
from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_history_backfill import _scan_forbidden
from core.market_intelligence.market_history_export import (
    MarketHistoryExportError,
    _projection_failure_code,
    export_market_history,
)
from core.market_intelligence.market_history_operations import (
    MarketHistoryOperationError,
    build_parser,
    validate_export_directory,
)
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)


class MarketHistoryExportTests(unittest.TestCase):
    def test_import_throttle_is_explicit_and_bounded_by_the_operation(self):
        args = build_parser().parse_args(
            [
                "import-staging",
                "--directory",
                "/protected/history",
                "--expected-manifest-sha256",
                "1" * 64,
                "--backup-receipt",
                "/protected/backup.dump",
                "--confirm-staging-project",
                "market-private-pipeline-stage13-shadow",
                "--inter-bundle-delay-seconds",
                "5",
            ]
        )
        self.assertEqual(args.inter_bundle_delay_seconds, 5.0)

    def test_sqlite_failure_code_keeps_payload_out_of_operator_output(self):
        error = __import__("sqlite3").OperationalError("database or disk is full")
        error.sqlite_errorname = "SQLITE_FULL"
        self.assertEqual(
            _projection_failure_code("WALLEX_PUBLIC_API", error),
            "history_export_projection_failed:WALLEX_PUBLIC_API:sqlite_full",
        )

    def _observation(self, *, key: bytes, source: str, when: datetime, **changes):
        values = {
            "event_key": key,
            "source_code": source,
            "source_family": "GROUP" if source == "GROUP_1" else "TELEGRAM_PUBLIC",
            "event_time_utc": when,
            "available_at_utc": when + timedelta(seconds=1),
            "instrument": "COIN_IMAM" if source == "GROUP_1" else "MELTED_GOLD",
            "market_label": "COIN_GROUP" if source == "GROUP_1" else "MELTED_FLOW",
            "settlement_term": "CASH",
            "trade_form": "PHYSICAL",
            "event_type": "OFFER",
            "side": "SELL",
            "price": "188600" if source == "GROUP_1" else "100000000",
            "price_unit": (
                "PROJECT_THOUSAND_TOMAN"
                if source == "GROUP_1"
                else "TOMAN_PER_MESGHAL_750"
            ),
            "currency": "TOMAN",
            "quantity": "5" if source == "GROUP_1" else None,
            "quantity_unit": "COIN" if source == "GROUP_1" else None,
            "parser_version": "history-export-test-v1",
        }
        values.update(changes)
        return MarketObservation(**values)

    def _duplicate_in_archive(self, connection, key: bytes) -> None:
        columns = [
            str(row[1])
            for row in connection.execute("PRAGMA table_info(market_observations)")
        ]
        joined = ",".join(columns)
        connection.execute(
            f"INSERT INTO market_observations_archive({joined},archived_at_utc) "
            f"SELECT {joined},? FROM market_observations WHERE event_key=?",
            ("2026-08-25T09:00:00Z", key),
        )

    def test_export_unions_archive_deduplicates_and_bounds_bundles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "market.sqlite3"
            connection = connect_market_store(source_path)
            initialize_market_store(connection)
            start = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
            offer_key = derive_event_key("history-export", "offer")
            trade_key = derive_event_key("history-export", "trade")
            flow_key = derive_event_key("history-export", "flow")
            second_flow_key = derive_event_key("history-export", "flow-2")
            upsert_observation(
                connection,
                self._observation(
                    key=offer_key,
                    source="GROUP_1",
                    when=start - timedelta(minutes=1),
                ),
            )
            upsert_observation(
                connection,
                self._observation(
                    key=trade_key,
                    source="GROUP_1",
                    when=start + timedelta(minutes=1),
                    event_type="TRADE",
                    price="188700",
                    quantity="3",
                    attributes={"root_offer_event_key": offer_key.hex()},
                ),
            )
            upsert_observation(
                connection,
                self._observation(
                    key=flow_key,
                    source="MELTED_FLOW",
                    when=start + timedelta(minutes=2),
                ),
            )
            upsert_observation(
                connection,
                self._observation(
                    key=second_flow_key,
                    source="MELTED_FLOW",
                    when=start + timedelta(minutes=3),
                ),
            )
            self._duplicate_in_archive(connection, trade_key)
            connection.commit()
            connection.close()

            exclusion_path = root / "candidate.sqlite3"
            exclusion = connect_market_store(exclusion_path)
            initialize_market_store(exclusion)
            upsert_observation(
                exclusion,
                self._observation(
                    key=flow_key,
                    source="MELTED_FLOW",
                    when=start + timedelta(minutes=2),
                    parser_version="newer-parser-must-win-v2",
                ),
            )
            exclusion.commit()
            exclusion.close()

            protected = root / "protected"
            protected.mkdir(mode=0o700)
            os.chmod(protected, 0o700)
            scratch = protected / "scratch"
            scratch.mkdir(mode=0o700)
            os.chmod(scratch, 0o700)
            first = protected / "first"
            report = export_market_history(
                source_store=source_path,
                source_codes=("GROUP_1", "MELTED_FLOW"),
                window_start_utc=start,
                window_end_utc=start + timedelta(hours=1),
                source_cutoffs_utc={},
                maximum_bundle_records=1,
                output_directory=first,
                exclusion_store=exclusion_path,
                temporary_directory=scratch,
            )
            self.assertEqual(list(scratch.iterdir()), [])
            self.assertEqual(report.source_counts, {"GROUP_1": 2, "MELTED_FLOW": 1})
            self.assertEqual(report.excluded_existing_counts["MELTED_FLOW"], 1)
            self.assertEqual(report.bundle_count, 3)
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o700)
            manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["contains_raw_telegram_history"])
            self.assertFalse(manifest["contains_participant_identity"])
            for item in manifest["bundles"]:
                path = first / item["file"]
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                bundle = json.loads(path.read_text(encoding="utf-8"))
                _scan_forbidden(bundle)
            modes = {item["source_code"]: item["retention_mode"] for item in manifest["bundles"]}
            self.assertEqual(modes["GROUP_1"], "PERMANENT_ARCHIVE")
            self.assertEqual(modes["MELTED_FLOW"], "PERMANENT_ARCHIVE")

            second = protected / "second"
            repeated = export_market_history(
                source_store=source_path,
                source_codes=("GROUP_1", "MELTED_FLOW"),
                window_start_utc=start,
                window_end_utc=start + timedelta(hours=1),
                source_cutoffs_utc={},
                maximum_bundle_records=1,
                output_directory=second,
                exclusion_store=exclusion_path,
                temporary_directory=scratch,
            )
            self.assertEqual(list(scratch.iterdir()), [])
            self.assertEqual(report.manifest_sha256, repeated.manifest_sha256)
            validated = validate_export_directory(first)
            self.assertEqual(validated.manifest_sha256, report.manifest_sha256)
            self.assertEqual(validated.record_count, 3)

            common = {
                "source_store": source_path,
                "source_codes": ("GROUP_1", "MELTED_FLOW"),
                "window_start_utc": start,
                "window_end_utc": start + timedelta(hours=1),
                "source_cutoffs_utc": {},
                "maximum_bundle_records": 1,
                "exclusion_store": exclusion_path,
                "temporary_directory": scratch,
            }
            with patch.object(market_history_export, "MAX_BUNDLES", 2):
                with self.assertRaisesRegex(
                    MarketHistoryExportError,
                    "history_export_bundle_count_exceeded",
                ):
                    export_market_history(**common)
            with patch.object(market_history_export, "MAX_BUNDLE_BYTES", 100):
                with self.assertRaisesRegex(
                    MarketHistoryExportError,
                    "history_export_bundle_bytes_exceeded",
                ):
                    export_market_history(**common)
            with patch.object(market_history_export, "MAX_MANIFEST_BYTES", 100):
                with self.assertRaisesRegex(
                    MarketHistoryExportError,
                    "history_export_manifest_bytes_exceeded",
                ):
                    export_market_history(**common)

            unexpected = first / "unexpected.json"
            unexpected.write_text("{}", encoding="utf-8")
            os.chmod(unexpected, 0o600)
            with self.assertRaisesRegex(
                MarketHistoryOperationError, "history_operation_unexpected_export_file"
            ):
                validate_export_directory(first)

    def test_long_legacy_parser_version_is_hash_bounded_and_auditable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.sqlite3"
            exclusion_path = root / "exclusion.sqlite3"
            for path in (source_path, exclusion_path):
                connection = connect_market_store(path)
                initialize_market_store(connection)
                if path == source_path:
                    when = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
                    upsert_observation(
                        connection,
                        self._observation(
                            key=derive_event_key("history-export", "long-parser"),
                            source="GROUP_1",
                            when=when,
                            parser_version="legacy-" + ("v" * 100),
                        ),
                    )
                connection.commit()
                connection.close()
            scratch = root / "scratch"
            scratch.mkdir(mode=0o700)
            os.chmod(scratch, 0o700)
            report = export_market_history(
                source_store=source_path,
                exclusion_store=exclusion_path,
                source_codes=("GROUP_1",),
                window_start_utc="2026-08-25T07:00:00Z",
                window_end_utc="2026-08-25T09:00:00Z",
                source_cutoffs_utc={},
                output_directory=root / "output",
                temporary_directory=scratch,
            )
            self.assertEqual(list(scratch.iterdir()), [])
            item = report.manifest["bundles"][0]
            bundle = json.loads(
                ((root / "output") / item["file"]).read_text(encoding="utf-8")
            )
            record = bundle["records"][0]
            self.assertLessEqual(len(record["parser_version"]), 96)
            self.assertTrue(record["parser_version"].startswith("legacy-parser-sha256:"))
            self.assertIn("PARSER_VERSION_NORMALIZED", record["quality_reason_codes"])

    def test_operational_temp_store_requires_empty_protected_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.sqlite3"
            exclusion_path = root / "exclusion.sqlite3"
            when = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
            for path in (source_path, exclusion_path):
                connection = connect_market_store(path)
                initialize_market_store(connection)
                if path == source_path:
                    upsert_observation(
                        connection,
                        self._observation(
                            key=derive_event_key("history-export", "disk-temp"),
                            source="GROUP_1",
                            when=when,
                        ),
                    )
                connection.commit()
                connection.close()
            scratch = root / "scratch"
            scratch.mkdir(mode=0o700)
            os.chmod(scratch, 0o700)
            (scratch / "leftover").write_bytes(b"incomplete-prior-run")
            with self.assertRaisesRegex(
                MarketHistoryExportError,
                "history_export_temporary_directory_invalid",
            ):
                export_market_history(
                    source_store=source_path,
                    exclusion_store=exclusion_path,
                    source_codes=("GROUP_1",),
                    window_start_utc=when - timedelta(minutes=1),
                    window_end_utc=when + timedelta(minutes=1),
                    source_cutoffs_utc={},
                    temporary_directory=scratch,
                )

    def test_export_cli_requires_disk_backed_temporary_directory(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(
                    [
                        "export",
                        "--source-store",
                        "/protected/source.sqlite3",
                        "--exclusion-store",
                        "/protected/exclusion.sqlite3",
                        "--source",
                        "GROUP_1",
                        "--window-start-utc",
                        "2026-08-25T07:00:00Z",
                        "--window-end-utc",
                        "2026-08-25T09:00:00Z",
                    ]
                )

    def test_export_cli_rejects_host_tmp_scratch(self):
        args = build_parser().parse_args(
            [
                "export",
                "--source-store",
                "/protected/source.sqlite3",
                "--exclusion-store",
                "/protected/exclusion.sqlite3",
                "--temporary-directory",
                "/tmp/history-export-scratch",
                "--source",
                "GROUP_1",
                "--window-start-utc",
                "2026-08-25T07:00:00Z",
                "--window-end-utc",
                "2026-08-25T09:00:00Z",
            ]
        )
        with self.assertRaisesRegex(
            MarketHistoryOperationError,
            "history_operation_tmpfs_temporary_directory_forbidden",
        ):
            args.handler(args)

    def test_export_cli_rejects_non_tmp_memory_filesystem(self):
        args = build_parser().parse_args(
            [
                "export",
                "--source-store",
                "/protected/source.sqlite3",
                "--exclusion-store",
                "/protected/exclusion.sqlite3",
                "--temporary-directory",
                "/protected/scratch",
                "--source",
                "GROUP_1",
                "--window-start-utc",
                "2026-08-25T07:00:00Z",
                "--window-end-utc",
                "2026-08-25T09:00:00Z",
            ]
        )
        with patch(
            "core.market_intelligence.market_history_operations._filesystem_type",
            return_value="tmpfs",
        ), self.assertRaisesRegex(
            MarketHistoryOperationError,
            "history_operation_tmpfs_temporary_directory_forbidden",
        ):
            args.handler(args)

    def test_unlinked_private_gold_outcome_requires_explicit_audited_omission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.sqlite3"
            exclusion_path = root / "exclusion.sqlite3"
            when = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
            source = connect_market_store(source_path)
            initialize_market_store(source)
            upsert_observation(
                source,
                MarketObservation(
                    event_key=derive_event_key("history-export", "private-offer"),
                    source_code="PRIVATE_GOLD_CHANNEL",
                    source_family="TELEGRAM_PRIVATE",
                    event_time_utc=when,
                    available_at_utc=when + timedelta(seconds=1),
                    instrument="MELTED_GOLD_PRIVATE",
                    market_label="MELTED_PRIMARY",
                    settlement_term="CASH",
                    trade_form="PHYSICAL",
                    event_type="OFFER",
                    side="SELL",
                    price="100000000",
                    price_unit="TOMAN_PER_MESGHAL_750",
                    currency="TOMAN",
                ),
            )
            upsert_observation(
                source,
                MarketObservation(
                    event_key=derive_event_key("history-export", "unlinked-outcome"),
                    source_code="PRIVATE_GOLD_CHANNEL",
                    source_family="TELEGRAM_PRIVATE",
                    event_time_utc=when + timedelta(minutes=1),
                    available_at_utc=when + timedelta(minutes=1, seconds=1),
                    instrument="MELTED_GOLD_PRIVATE",
                    market_label="MELTED_PRIMARY",
                    settlement_term="CASH",
                    trade_form="PHYSICAL",
                    event_type="TRADE",
                    side="SELL",
                    price="100000000",
                    price_unit="TOMAN_PER_MESGHAL_750",
                    currency="TOMAN",
                ),
            )
            source.commit()
            source.close()
            exclusion = connect_market_store(exclusion_path)
            initialize_market_store(exclusion)
            exclusion.close()
            arguments = {
                "source_store": source_path,
                "exclusion_store": exclusion_path,
                "source_codes": ("PRIVATE_GOLD_CHANNEL",),
                "window_start_utc": when,
                "window_end_utc": when + timedelta(hours=1),
                "source_cutoffs_utc": {},
            }
            with self.assertRaisesRegex(
                MarketHistoryExportError,
                "history_export_unlinked_private_gold_outcome",
            ):
                export_market_history(**arguments)
            report = export_market_history(
                **arguments,
                allow_unlinked_private_gold_outcome_omission=True,
            )
            self.assertEqual(report.source_counts["PRIVATE_GOLD_CHANNEL"], 1)
            self.assertEqual(
                report.omitted_unlinked_outcome_counts["PRIVATE_GOLD_CHANNEL"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
