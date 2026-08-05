from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone

from online_recalibration import (
    apply_calibration,
    ensure_schema,
    reconcile_predictions,
    record_predictions,
)


UTC = timezone.utc


class OnlineRecalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE confirmed_trades (
                id INTEGER PRIMARY KEY,
                event_time_utc TEXT NOT NULL,
                commodity TEXT NOT NULL,
                price INTEGER NOT NULL,
                quantity INTEGER,
                settlement TEXT NOT NULL,
                trade_form TEXT NOT NULL,
                confidence REAL NOT NULL,
                training_eligible INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        ensure_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def _record(self, at: str, price: int, *, enabled: bool = False) -> None:
        record_predictions(
            self.connection,
            prediction_time=datetime.fromisoformat(at.replace("Z", "+00:00")),
            settlement="CASH",
            rates=[
                {
                    "commodity_name": "امام",
                    "estimated_price_toman": price,
                    "tolerance": {
                        "lower_price_toman": price - 1_000_000,
                        "upper_price_toman": price + 1_000_000,
                    },
                }
            ],
            group_live_enabled=enabled,
        )
        self.connection.commit()

    def test_reconnect_evaluates_one_pending_prediction_and_updates_state(self) -> None:
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=False)
        self._record("2026-08-05T10:01:00Z", 181_000_000, enabled=False)
        self._record("2026-08-05T10:02:00Z", 182_000_000, enabled=False)
        self.connection.execute(
            """
            INSERT INTO confirmed_trades(
                id,event_time_utc,commodity,price,quantity,settlement,
                trade_form,confidence,training_eligible
            ) VALUES (1,'2026-08-05T12:00:30Z','امام',185000,5,'CASH','PHYSICAL',0.99,1)
            """
        )
        self.connection.commit()

        result = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 12, 1, tzinfo=UTC),
            live_group_enabled=True,
            reconnect_at=datetime(2026, 8, 5, 11, 59, tzinfo=UTC),
        )
        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(result["reconnect_bridged"], 1)
        row = self.connection.execute(
            "SELECT evaluation_mode, actual_price_toman FROM coin_estimate_predictions WHERE evaluated_at_utc IS NOT NULL"
        ).fetchone()
        self.assertEqual(row["evaluation_mode"], "RECONNECT_BRIDGE")
        self.assertEqual(row["actual_price_toman"], 185000000.0)
        state = self.connection.execute(
            "SELECT sample_count, residual_mean FROM coin_online_residual_state"
        ).fetchone()
        self.assertEqual(state["sample_count"], 1)
        self.assertGreater(state["residual_mean"], 0)

    def test_correction_waits_for_three_samples_and_never_narrows_range(self) -> None:
        for index in range(3):
            prediction = datetime(2026, 8, 5, 10, index, tzinfo=UTC)
            self._record(prediction.isoformat().replace("+00:00", "Z"), 180_000_000, enabled=True)
            self.connection.execute(
                """
                INSERT INTO confirmed_trades(
                    id,event_time_utc,commodity,price,quantity,settlement,
                    trade_form,confidence,training_eligible
                ) VALUES (?,?,?,?,?,?,?,?,1)
                """,
                (index + 1, f"2026-08-05T10:0{index}:30Z", "امام", 181_000, 5, "CASH", "PHYSICAL", 0.99),
            )
            self.connection.commit()
            reconcile_predictions(
                self.connection,
                now=datetime(2026, 8, 5, 10, index, 59, tzinfo=UTC),
                live_group_enabled=True,
            )
        rate = {
            "commodity_name": "امام",
            "estimated_price_toman": 180_000_000,
            "tolerance": {
                "lower_price_toman": 179_000_000,
                "upper_price_toman": 181_000_000,
            },
        }
        info = apply_calibration(
            self.connection, commodity="امام", settlement="CASH", rate=rate
        )
        self.assertEqual(info["status"], "APPLIED")
        self.assertGreater(rate["estimated_price_toman"], 180_000_000)
        self.assertLessEqual(rate["tolerance"]["lower_price_toman"], 179_000_000)
        self.assertGreaterEqual(rate["tolerance"]["upper_price_toman"], 181_000_000)


if __name__ == "__main__":
    unittest.main()
