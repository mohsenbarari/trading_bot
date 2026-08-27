from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.market_intelligence.coin_group_feedback import _SCHEMA
from scripts.export_market_calibration_seed import (
    CalibrationSeedError,
    export_seed,
)


class ExportMarketCalibrationSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="market-staging-seed-")
        self.root = Path(self.temporary.name)
        self.feedback = self.root / "source-feedback.sqlite3"
        self.predictions = self.root / "source-predictions.sqlite3"
        self.destination = self.root / "staging-export"
        feedback = sqlite3.connect(self.feedback)
        feedback.executescript(_SCHEMA)
        feedback.execute(
            "INSERT INTO coin_group_parser_feedback_state VALUES(1,1,7,?)",
            ("2026-08-26T11:00:00Z",),
        )
        feedback.execute(
            "INSERT INTO coin_group_parser_feedback VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                b"e" * 32,
                "OFFER",
                1,
                "2026-08-26T10:00:00Z",
                '["commodity"]',
                1,
                "IMAM",
                "BUY",
                188000,
                2,
                "CASH",
                "PHYSICAL",
                0,
                b"r" * 32,
                1,
                "2026-08-26T11:00:00Z",
                0,
                None,
                0,
            ),
        )
        feedback.execute("CREATE TABLE raw_messages(text TEXT)")
        feedback.execute("INSERT INTO raw_messages VALUES('must-not-export')")
        feedback.commit()
        feedback.close()

        predictions = sqlite3.connect(self.predictions)
        predictions.executescript(
            """
            CREATE TABLE coin_estimate_predictions(
              id INTEGER PRIMARY KEY,
              prediction_time_utc TEXT,
              created_at_utc TEXT,
              model_id TEXT,
              commodity TEXT,
              settlement TEXT,
              estimated_price_toman INTEGER,
              debug_payload TEXT
            );
            CREATE TABLE provider_responses(raw TEXT);
            """
        )
        predictions.executemany(
            "INSERT INTO coin_estimate_predictions VALUES(?,?,?,?,?,?,?,?)",
            (
                (1, "2026-08-26T10:00:00Z", "2026-08-26T10:00:01Z", "MAIN_ONLINE", "امام", "CASH", 188000000, "secret"),
                (2, "2026-08-23T10:00:00Z", "2026-08-23T10:00:01Z", "MAIN_ONLINE", "امام", "CASH", 180000000, "old"),
                (3, "2026-08-26T10:05:00Z", "2026-08-26T10:05:01Z", "SHADOW", "امام", "CASH", 188100000, "other"),
            ),
        )
        predictions.execute("INSERT INTO provider_responses VALUES('must-not-export')")
        predictions.commit()
        predictions.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_export_is_bounded_minimized_atomic_and_repeatable(self) -> None:
        as_of = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        first = export_seed(
            feedback_source=self.feedback,
            prediction_source=self.predictions,
            destination_root=self.destination,
            window_hours=12,
            as_of=as_of,
        )
        second = export_seed(
            feedback_source=self.feedback,
            prediction_source=self.predictions,
            destination_root=self.destination,
            window_hours=12,
            as_of=as_of,
        )
        self.assertEqual(first["feedback_rows"], 1)
        self.assertEqual(first["prediction_rows"], 1)
        self.assertEqual(first["feedback_sha256"], second["feedback_sha256"])
        self.assertEqual(first["prediction_sha256"], second["prediction_sha256"])
        feedback = sqlite3.connect(self.destination / "review-decisions.sqlite3")
        prediction = sqlite3.connect(self.destination / "prediction-ledger.sqlite3")
        try:
            self.assertIsNone(
                feedback.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='raw_messages'"
                ).fetchone()
            )
            self.assertIsNone(
                prediction.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='provider_responses'"
                ).fetchone()
            )
            self.assertEqual(
                prediction.execute(
                    "SELECT model_id,commodity,estimated_price_toman,authority_epoch "
                    "FROM coin_estimate_predictions"
                ).fetchone(),
                ("MAIN_ONLINE", "امام", 188000000, "LEGACY_BASELINE"),
            )
            self.assertEqual(
                prediction.execute(
                    "SELECT active_epoch,active_feed_mode "
                    "FROM coin_estimate_prediction_authority"
                ).fetchone(),
                ("LEGACY_BASELINE", "LEGACY_BASELINE"),
            )
        finally:
            feedback.close()
            prediction.close()
        self.assertFalse(tuple(self.destination.glob("*.pending")))

    def test_destination_must_be_staging_scoped(self) -> None:
        with self.assertRaisesRegex(
            CalibrationSeedError, "calibration_seed_root_must_be_staging_scoped"
        ):
            export_seed(
                feedback_source=self.feedback,
                prediction_source=self.predictions,
                destination_root=Path("/tmp/market-calibration-export"),
                as_of=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
            )

    def test_authority_aware_primary_source_is_never_relabelled_as_legacy(self) -> None:
        predictions = sqlite3.connect(self.predictions)
        try:
            predictions.execute(
                "ALTER TABLE coin_estimate_predictions ADD COLUMN authority_epoch TEXT"
            )
            predictions.execute(
                "UPDATE coin_estimate_predictions SET authority_epoch='PRIVATE_PRIMARY:model-v1'"
            )
            predictions.execute(
                "CREATE TABLE coin_estimate_prediction_authority("
                "singleton INTEGER PRIMARY KEY,active_epoch TEXT NOT NULL,"
                "active_feed_mode TEXT NOT NULL,updated_at_utc TEXT NOT NULL)"
            )
            predictions.execute(
                "INSERT INTO coin_estimate_prediction_authority VALUES(1,?,?,?)",
                (
                    "PRIVATE_PRIMARY:model-v1",
                    "PRIVATE_PRIMARY",
                    "2026-08-26T11:00:00Z",
                ),
            )
            predictions.commit()
        finally:
            predictions.close()

        with self.assertRaisesRegex(
            CalibrationSeedError, "calibration_prediction_authority_not_legacy"
        ):
            export_seed(
                feedback_source=self.feedback,
                prediction_source=self.predictions,
                destination_root=self.destination,
                as_of=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
            )

    def test_authority_aware_export_filters_primary_and_namespaces_seed_ids(self) -> None:
        predictions = sqlite3.connect(self.predictions)
        try:
            predictions.execute(
                "ALTER TABLE coin_estimate_predictions ADD COLUMN authority_epoch TEXT"
            )
            predictions.execute(
                "UPDATE coin_estimate_predictions SET authority_epoch='LEGACY_BASELINE'"
            )
            predictions.execute(
                "INSERT INTO coin_estimate_predictions VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    4,
                    "2026-08-26T10:10:00Z",
                    "2026-08-26T10:10:01Z",
                    "MAIN_ONLINE",
                    "امام",
                    "CASH",
                    999_000_000,
                    "private",
                    "PRIVATE_PRIMARY:model-v1",
                ),
            )
            predictions.execute(
                "CREATE TABLE coin_estimate_prediction_authority("
                "singleton INTEGER PRIMARY KEY,active_epoch TEXT NOT NULL,"
                "active_feed_mode TEXT NOT NULL,updated_at_utc TEXT NOT NULL)"
            )
            predictions.execute(
                "INSERT INTO coin_estimate_prediction_authority VALUES(1,?,?,?)",
                ("LEGACY_BASELINE", "LEGACY_BASELINE", "2026-08-26T11:00:00Z"),
            )
            predictions.commit()
        finally:
            predictions.close()

        receipt = export_seed(
            feedback_source=self.feedback,
            prediction_source=self.predictions,
            destination_root=self.destination,
            as_of=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(receipt["prediction_rows"], 1)
        exported = sqlite3.connect(self.destination / "prediction-ledger.sqlite3")
        try:
            row = exported.execute(
                "SELECT id,estimated_price_toman,authority_epoch "
                "FROM coin_estimate_predictions"
            ).fetchone()
        finally:
            exported.close()
        self.assertEqual(row, (-1, 188_000_000, "LEGACY_BASELINE"))


if __name__ == "__main__":
    unittest.main()
