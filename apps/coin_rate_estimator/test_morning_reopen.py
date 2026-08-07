from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import morning_reopen as reopen_module
from morning_reopen import (
    METHOD_NAME,
    build_morning_reopen_anchor,
    is_morning_reopen_window,
    morning_open_truth_label,
    select_reopen_cash_tomorrow_ratio,
    tehran_clock_utc,
    widen_tolerance,
)


TEHRAN = timezone(timedelta(hours=3, minutes=30))


class MorningReopenTests(unittest.TestCase):
    def test_reopen_ratio_snapshot_cache_is_copy_safe(self) -> None:
        reopen_module._REOPEN_RATIO_SNAPSHOT_CACHE.clear()
        end = tehran_clock_utc("2026-08-07", 10, 0)
        result = {"status": "OBSERVED", "ratio": 1.003, "sample_days": [{"day": "x"}]}
        with patch.object(
            reopen_module,
            "_select_reopen_cash_tomorrow_ratio_uncached",
            return_value=result,
        ) as raw:
            first = select_reopen_cash_tomorrow_ratio(
                Path("/tmp/reopen-cache.sqlite3"),
                commodity="امام",
                end=end,
            )
            first["sample_days"].append({"day": "mutated"})
            second = select_reopen_cash_tomorrow_ratio(
                Path("/tmp/reopen-cache.sqlite3"),
                commodity="امام",
                end=end,
            )
        self.assertEqual(raw.call_count, 1)
        self.assertEqual(second["sample_days"], [{"day": "x"}])

    def test_window_requires_explicit_enable(self) -> None:
        end = tehran_clock_utc("2026-08-05", 10, 0)
        self.assertFalse(is_morning_reopen_window(end, model={}))
        self.assertFalse(
            is_morning_reopen_window(end, model={"morning_reopen": {"enabled": False}})
        )
        self.assertTrue(
            is_morning_reopen_window(end, model={"morning_reopen": {"enabled": True}})
        )
        self.assertFalse(
            is_morning_reopen_window(
                end,
                has_live_anchor=True,
                model={"morning_reopen": {"enabled": True}},
            )
        )

    def test_blend_and_wider_band(self) -> None:
        reopen = build_morning_reopen_anchor(
            intrinsic=100_000_000,
            structural=112_000_000,
            transferred=110_000_000,
            anchor_age_seconds=20 * 3600,
            current_herat=105_000,
            anchor_herat=100_000,
            current_melted=50_000_000,
            anchor_melted=48_000_000,
            current_usdt=104_000,
            anchor_usdt=100_000,
            settlement="TOMORROW",
            model={"morning_reopen": {"enabled": True}},
        )
        self.assertEqual(reopen["method"], METHOD_NAME)
        self.assertGreater(reopen["estimated_price_toman"], 0)
        self.assertGreaterEqual(reopen["band_multiplier"], 1.6)
        tol = widen_tolerance(
            {
                "lower_price_toman": 108_000_000,
                "upper_price_toman": 112_000_000,
                "lower_project_price": 108_000,
                "upper_project_price": 112_000,
            },
            multiplier=2.0,
            center_toman=110_000_000,
        )
        self.assertLess(tol["lower_price_toman"], 108_000_000)
        self.assertGreater(tol["upper_price_toman"], 112_000_000)

    def test_truth_prefers_trades_then_weighted_offers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "conversation.sqlite3"
            connection = sqlite3.connect(db)
            connection.executescript(
                """
                CREATE TABLE messages(
                  import_id TEXT, message_id TEXT, event_time_utc TEXT,
                  PRIMARY KEY(import_id, message_id)
                );
                CREATE TABLE offers(
                  id INTEGER PRIMARY KEY, import_id TEXT, message_id TEXT,
                  commodity TEXT, price REAL, quantity REAL, side TEXT,
                  settlement TEXT, trade_form TEXT, confidence REAL
                );
                CREATE TABLE confirmed_trades(
                  id INTEGER PRIMARY KEY, event_time_utc TEXT, commodity TEXT,
                  price REAL, quantity REAL, settlement TEXT, trade_form TEXT,
                  confidence REAL, training_eligible INTEGER
                );
                """
            )
            day = "2026-08-05"
            # Early weak offer then later stronger offer (project units).
            connection.execute(
                "INSERT INTO messages VALUES ('i','1','2026-08-05T06:35:00Z')"
            )
            connection.execute(
                "INSERT INTO messages VALUES ('i','2','2026-08-05T06:50:00Z')"
            )
            connection.execute(
                """
                INSERT INTO offers(
                  id, import_id, message_id, commodity, price, quantity, side,
                  settlement, trade_form, confidence
                ) VALUES
                  (1,'i','1','امام',170000,1,'SELL','TOMORROW','PHYSICAL',0.9),
                  (2,'i','2','امام',180000,1,'SELL','TOMORROW','PHYSICAL',0.9)
                """
            )
            connection.commit()
            offer_truth = morning_open_truth_label(
                db, day=day, commodity="امام", settlement="TOMORROW"
            )
            self.assertEqual(offer_truth["status"], "OBSERVED")
            self.assertEqual(offer_truth["source"], "RECENCY_WEIGHTED_OFFERS_30M")
            # Later offer should pull the average above the midpoint.
            self.assertGreater(offer_truth["truth_price_toman"], 175_000_000)

            connection.execute(
                """
                INSERT INTO confirmed_trades(
                  id, event_time_utc, commodity, price, quantity, settlement,
                  trade_form, confidence, training_eligible
                ) VALUES (1,'2026-08-05T06:40:00Z','امام',176000,1,'TOMORROW','PHYSICAL',0.95,1)
                """
            )
            connection.commit()
            connection.close()
            trade_truth = morning_open_truth_label(
                db, day=day, commodity="امام", settlement="TOMORROW"
            )
            self.assertEqual(trade_truth["source"], "RECENCY_WEIGHTED_CONFIRMED_TRADES_30M")
            self.assertEqual(trade_truth["truth_project_price"], 176000)


if __name__ == "__main__":
    unittest.main()
