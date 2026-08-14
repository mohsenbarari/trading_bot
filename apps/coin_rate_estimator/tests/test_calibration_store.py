from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from live_server import StateStore, prepare_calibration_store, refresh_estimate
from online_recalibration import ensure_schema, record_predictions
from test_estimator import make_conversation_db, make_market_db, model


class CalibrationStoreTests(unittest.TestCase):
    def test_migrates_legacy_ledger_once_without_writing_the_conversation_db(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation_db = Path(directory) / "conversation.sqlite3"
            calibration_db = Path(directory) / "online_calibration.sqlite3"
            connection = sqlite3.connect(conversation_db)
            try:
                ensure_schema(connection)
                record_predictions(
                    connection,
                    prediction_time=datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc),
                    settlement="CASH",
                    rates=[
                        {
                            "commodity_name": "امام",
                            "estimated_price_toman": 180_000_000,
                        }
                    ],
                    group_live_enabled=True,
                )
                connection.execute(
                    """
                    INSERT INTO coin_online_residual_state(
                        commodity,settlement,sample_count,residual_mean,
                        residual_abs_mean,last_actual_utc,updated_at_utc
                    ) VALUES ('امام','CASH',3,0.002,0.004,
                              '2026-08-11T06:59:00Z','2026-08-11T07:00:00Z')
                    """
                )
                connection.commit()
            finally:
                connection.close()

            migrated = prepare_calibration_store(calibration_db, conversation_db)
            self.assertEqual(migrated["status"], "READY")
            self.assertEqual(
                migrated["copied_rows"]["coin_estimate_predictions"], 1
            )
            self.assertEqual(
                migrated["copied_rows"]["coin_online_residual_state"], 1
            )

            source = sqlite3.connect(f"file:{conversation_db}?mode=ro", uri=True)
            target = sqlite3.connect(f"file:{calibration_db}?mode=ro", uri=True)
            try:
                self.assertEqual(
                    source.execute("SELECT COUNT(*) FROM coin_estimate_predictions").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    target.execute("SELECT COUNT(*) FROM coin_estimate_predictions").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    target.execute("SELECT COUNT(*) FROM coin_online_residual_state").fetchone()[0],
                    1,
                )
            finally:
                source.close()
                target.close()

            repeated = prepare_calibration_store(calibration_db, conversation_db)
            self.assertEqual(
                repeated["copied_rows"]["coin_estimate_predictions"], 0
            )

    def test_live_refresh_writes_only_to_the_calibration_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_db = root / "market.sqlite3"
            conversation_db = root / "conversation.sqlite3"
            calibration_db = root / "online_calibration.sqlite3"
            make_market_db(market_db)
            make_conversation_db(conversation_db)
            prepare_calibration_store(calibration_db, conversation_db)

            result = refresh_estimate(
                model(),
                market_db,
                conversation_db,
                root / "state.json",
                StateStore(),
                calibration_db=calibration_db,
                end=datetime(2026, 7, 20, 10, 2, tzinfo=timezone.utc),
                shadow_model_path=root / "missing-shadow.json",
                shadow_state_path=root / "shadow-state.json",
                research_shadow_model_path=root / "missing-research.json",
                research_shadow_state_path=root / "research-state.json",
                ml_shadow_model_path=root / "missing-ml.joblib",
                ml_shadow_state_path=root / "ml-state.json",
            )

            source = sqlite3.connect(f"file:{conversation_db}?mode=ro", uri=True)
            target = sqlite3.connect(f"file:{calibration_db}?mode=ro", uri=True)
            try:
                source_tables = {
                    row[0]
                    for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                prediction_rows = target.execute(
                    "SELECT COUNT(*) FROM coin_estimate_predictions"
                ).fetchone()[0]
            finally:
                source.close()
                target.close()

            self.assertEqual(result["service_status"], "RUNNING")
            self.assertNotIn("coin_estimate_predictions", source_tables)
            self.assertGreater(prediction_rows, 0)
