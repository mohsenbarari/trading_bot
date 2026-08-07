from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from online_recalibration import (
    apply_calibration,
    apply_recent_realized_calibration,
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

    def test_old_normal_prediction_is_not_requeried_but_is_retained(self) -> None:
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=True)
        result = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 20, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(result["evaluated"], 0)
        row = self.connection.execute(
            "SELECT evaluated_at_utc FROM coin_estimate_predictions"
        ).fetchone()
        # A performance guard must not destroy historical training/audit data.
        self.assertIsNone(row["evaluated_at_utc"])

    def test_same_trade_evaluates_each_model_but_only_main_learns_residual(self) -> None:
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=True)
        record_predictions(
            self.connection,
            prediction_time=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
            settlement="CASH",
            rates=[
                {
                    "commodity_name": "امام",
                    "estimated_price_toman": 179_000_000,
                }
            ],
            group_live_enabled=True,
            model_id="SHADOW1_PREVIOUS",
            model_version="test",
        )
        self.connection.execute(
            """
            INSERT INTO confirmed_trades(
                id,event_time_utc,commodity,price,quantity,settlement,
                trade_form,confidence,training_eligible
            ) VALUES (1,'2026-08-05T10:00:30Z','امام',181000,5,'CASH','PHYSICAL',0.99,1)
            """
        )
        self.connection.commit()
        result = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(result["evaluated"], 2)
        rows = self.connection.execute(
            "SELECT model_id, actual_price_toman FROM coin_estimate_predictions ORDER BY model_id"
        ).fetchall()
        self.assertEqual([row["model_id"] for row in rows], ["MAIN_ONLINE", "SHADOW1_PREVIOUS"])
        self.assertEqual([row["actual_price_toman"] for row in rows], [181_000_000.0, 181_000_000.0])
        state = self.connection.execute(
            "SELECT sample_count FROM coin_online_residual_state"
        ).fetchone()
        self.assertEqual(state["sample_count"], 1)

    def test_offer_fallback_requires_a_near_synchronous_two_sided_book(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE messages (
                import_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                event_time_utc TEXT NOT NULL,
                PRIMARY KEY(import_id, message_id)
            );
            CREATE TABLE offers (
                id INTEGER PRIMARY KEY,
                import_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                commodity TEXT NOT NULL,
                price INTEGER NOT NULL,
                quantity INTEGER,
                side TEXT NOT NULL,
                settlement TEXT NOT NULL,
                trade_form TEXT NOT NULL,
                confidence REAL NOT NULL
            );
            INSERT INTO messages VALUES (1, 1, '2026-08-05T10:00:20Z');
            INSERT INTO offers VALUES (1, 1, 1, 'امام', 180000, 5, 'BUY', 'CASH', 'PHYSICAL', 0.95);
            """
        )
        self._record("2026-08-05T10:00:00Z", 180_000_000, enabled=True)
        first = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(first["evaluated"], 0)
        self.connection.executescript(
            """
            INSERT INTO messages VALUES (1, 2, '2026-08-05T10:00:40Z');
            INSERT INTO offers VALUES (2, 1, 2, 'امام', 182000, 5, 'SELL', 'CASH', 'PHYSICAL', 0.95);
            """
        )
        self.connection.commit()
        second = reconcile_predictions(
            self.connection,
            now=datetime(2026, 8, 5, 10, 1, tzinfo=UTC),
            live_group_enabled=True,
        )
        self.assertEqual(second["evaluated"], 1)
        row = self.connection.execute(
            "SELECT actual_price_toman, evaluation_mode FROM coin_estimate_predictions"
        ).fetchone()
        self.assertEqual(row["actual_price_toman"], 181_000_000.0)
        self.assertEqual(row["evaluation_mode"], "FORWARD_5M")

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

    def test_recent_realized_correction_recenters_quiet_book_from_distinct_actuals(self) -> None:
        now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
        for offset, residual in ((45, -0.008), (15, -0.010)):
            occurred = now - timedelta(minutes=offset)
            self.connection.execute(
                """
                INSERT INTO coin_estimate_predictions(
                    prediction_time_utc, commodity, settlement,
                    structural_estimated_price_toman, estimated_price_toman,
                    lower_price_toman, upper_price_toman, group_live_enabled,
                    actual_price_toman, actual_event_utc, residual_ratio,
                    evaluated_at_utc, evaluation_mode, created_at_utc
                ) VALUES (?, 'امام', 'TOMORROW', 180000000, 180000000,
                          179000000, 181000000, 1,
                          178200000, ?, ?, ?, 'FORWARD_5M', ?)
                """,
                (
                    (occurred - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                    occurred.isoformat().replace("+00:00", "Z"),
                    residual,
                    now.isoformat().replace("+00:00", "Z"),
                    now.isoformat().replace("+00:00", "Z"),
                ),
            )
        self.connection.commit()
        rate = {
            "estimated_price_toman": 180_000_000,
            "tolerance": {
                "lower_price_toman": 179_000_000,
                "upper_price_toman": 181_000_000,
            },
            "group_offer_anchor": {"status": "NO_DATA"},
        }
        info = apply_recent_realized_calibration(
            self.connection,
            commodity="امام",
            settlement="TOMORROW",
            rate=rate,
            as_of=now,
        )
        self.assertEqual(info["status"], "APPLIED")
        self.assertEqual(info["actual_event_count"], 2)
        self.assertAlmostEqual(info["correction_ratio"], -0.010)
        self.assertEqual(rate["estimated_price_toman"], 178_200_000)
        self.assertEqual(rate["estimated_project_price"], 178_200)
        self.assertLess(rate["tolerance"]["lower_price_toman"], 179_000_000)
        self.assertIn("RECENT_REALIZED_RESIDUAL", rate["method"])

    def test_recent_realized_correction_never_overrides_fresh_book(self) -> None:
        now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
        self.connection.execute(
            """
            INSERT INTO coin_estimate_predictions(
                prediction_time_utc, commodity, settlement,
                structural_estimated_price_toman, estimated_price_toman,
                lower_price_toman, upper_price_toman, group_live_enabled,
                actual_price_toman, actual_event_utc, residual_ratio,
                evaluated_at_utc, evaluation_mode, created_at_utc
            ) VALUES ('2026-08-05T11:40:00Z','امام','CASH',180000000,180000000,
                      179000000,181000000,1,178200000,'2026-08-05T11:45:00Z',-0.01,
                      '2026-08-05T11:45:01Z','FORWARD_5M','2026-08-05T11:45:01Z')
            """
        )
        self.connection.commit()
        rate = {
            "estimated_price_toman": 180_000_000,
            "group_offer_anchor": {"status": "OBSERVED"},
        }
        info = apply_recent_realized_calibration(
            self.connection,
            commodity="امام",
            settlement="CASH",
            rate=rate,
            as_of=now,
        )
        self.assertEqual(info["status"], "SKIPPED_FRESH_LIVE_GROUP_ANCHOR")
        self.assertEqual(rate["estimated_price_toman"], 180_000_000)


if __name__ == "__main__":
    unittest.main()
