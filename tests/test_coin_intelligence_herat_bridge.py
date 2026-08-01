from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.market_intelligence.anchor_transfer import read_market_context
from core.market_intelligence.producer import select_effective_usd_average


AS_OF = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
ANCHOR_AT = "2026-08-01T10:00:00Z"
CURRENT_AT = "2026-08-01T12:00:00Z"
CASH_AS_OF = datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc)


def _create_database(
    path: Path,
    *,
    anchor_usdt: float = 100_000,
    current_usdt: float = 100_000,
    include_herat_anchor: bool = True,
    include_fresh_herat: bool = False,
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE price_events (
                id INTEGER PRIMARY KEY,
                event_time_utc TEXT NOT NULL,
                instrument TEXT NOT NULL,
                market_label TEXT NOT NULL,
                settlement_term TEXT NOT NULL,
                trade_form TEXT NOT NULL,
                event_type TEXT NOT NULL,
                side TEXT,
                price_num REAL NOT NULL,
                price_unit TEXT NOT NULL,
                parse_confidence REAL NOT NULL,
                quantity_num REAL
            );
            CREATE TABLE external_market_observations (
                id INTEGER PRIMARY KEY,
                observed_at_utc TEXT NOT NULL,
                instrument_code TEXT NOT NULL,
                quote_kind TEXT NOT NULL,
                normalized_price_num REAL,
                interval_seconds INTEGER NOT NULL DEFAULT 0,
                volume_value REAL
            );
            """
        )
        if include_herat_anchor:
            connection.executemany(
                """
                INSERT INTO price_events(
                    event_time_utc, instrument, market_label,
                    settlement_term, trade_form, event_type, side,
                    price_num, price_unit, parse_confidence, quantity_num
                ) VALUES (?, 'USD_HERAT', ?, ?, ?,
                          'TRADE', 'UNKNOWN', 100000, 'IRT_PER_USD', 0.99, 1)
                """,
                (
                    (
                        ANCHOR_AT,
                        "دلار هرات نامشخص فیزیکی",
                        "UNKNOWN",
                        "PHYSICAL",
                    ),
                    (
                        ANCHOR_AT,
                        "دلار هرات فردایی کاغذی",
                        "TOMORROW",
                        "PAPER",
                    ),
                ),
            )
        if include_fresh_herat:
            connection.execute(
                """
                INSERT INTO price_events(
                    event_time_utc, instrument, market_label,
                    settlement_term, trade_form, event_type, side,
                    price_num, price_unit, parse_confidence, quantity_num
                ) VALUES ('2026-08-01T11:59:40Z', 'USD_HERAT', ?,
                          'TOMORROW', 'PAPER', 'OFFER', 'SELL',
                          101000, 'IRT_PER_USD', 0.99, 1)
                """,
                ("دلار هرات فردایی کاغذی",),
            )
        connection.executemany(
            """
            INSERT INTO external_market_observations(
                observed_at_utc, instrument_code, quote_kind,
                normalized_price_num, interval_seconds, volume_value
            ) VALUES (?, 'USDT_IRT', 'MID', ?, 0, 1)
            """,
            (
                (ANCHOR_AT, anchor_usdt),
                (CURRENT_AT, current_usdt),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _create_cash_basis_database(
    path: Path,
    *,
    current_tomorrow: float,
) -> None:
    _create_database(path, include_herat_anchor=False)
    connection = sqlite3.connect(path)
    try:
        connection.executemany(
            """
            INSERT INTO price_events(
                event_time_utc, instrument, market_label,
                settlement_term, trade_form, event_type, side,
                price_num, price_unit, parse_confidence, quantity_num
            ) VALUES (?, 'USD_HERAT', ?, ?, ?, 'TRADE', 'UNKNOWN',
                      ?, 'IRT_PER_USD', 0.99, 1)
            """,
            (
                (
                    "2026-08-01T13:30:00Z",
                    "دلار هرات نامشخص فیزیکی",
                    "UNKNOWN",
                    "PHYSICAL",
                    100_000,
                ),
                (
                    "2026-08-01T13:29:50Z",
                    "دلار هرات فردایی کاغذی",
                    "TOMORROW",
                    "PAPER",
                    102_000,
                ),
                (
                    "2026-08-01T15:59:50Z",
                    "دلار هرات فردایی کاغذی",
                    "TOMORROW",
                    "PAPER",
                    current_tomorrow,
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


class EffectiveHeratBridgeTests(unittest.TestCase):
    def _select(self, database: Path) -> dict:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            return select_effective_usd_average(
                connection,
                "TOMORROW",
                AS_OF,
            )
        finally:
            connection.close()

    def test_usdt_moves_stale_herat_anchor_only_outside_deadband(self) -> None:
        cases = (
            ("UP", 102_000, 102_000),
            ("DOWN", 98_000, 98_000),
            ("NEUTRAL", 100_050, 100_000),
        )
        for trend, current_usdt, expected_herat in cases:
            with self.subTest(trend=trend), tempfile.TemporaryDirectory() as tmp:
                database = Path(tmp) / "market.sqlite3"
                _create_database(database, current_usdt=current_usdt)

                result = self._select(database)

                self.assertEqual(result["status"], "ESTIMATED")
                self.assertAlmostEqual(result["average_price"], expected_herat)
                self.assertEqual(result["usdt_trend"], trend)
                self.assertFalse(result["is_usdt_proxy"])
                self.assertEqual(result["anchor_price"], 100_000)

    def test_fresh_real_herat_wins_over_usdt_movement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "market.sqlite3"
            _create_database(
                database,
                current_usdt=110_000,
                include_fresh_herat=True,
            )

            result = self._select(database)

        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["average_price"], 101_000)
        self.assertEqual(result["price_source"], "USD_HERAT")
        self.assertFalse(result["is_estimated"])

    def test_usdt_is_never_a_direct_replacement_without_herat_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "market.sqlite3"
            _create_database(
                database,
                current_usdt=123_456,
                include_herat_anchor=False,
            )

            result = self._select(database)

        self.assertEqual(result["status"], "NO_DATA")
        self.assertIsNone(result["average_price"])
        self.assertEqual(
            result["fallback_rejected"],
            "DIRECT_USDT_PRICE_SUBSTITUTION_FORBIDDEN",
        )

    def test_cash_uses_tomorrow_basis_asymmetric_beta_and_banking_time(self) -> None:
        cases = (
            ("UP", 103_000, 0.90, 100_525),
            ("DOWN", 101_000, 1.05, 98_575),
            ("NEUTRAL", 102_050, 0.0, 99_625),
        )
        for direction, current_tomorrow, beta, expected in cases:
            with self.subTest(direction=direction), tempfile.TemporaryDirectory() as tmp:
                database = Path(tmp) / "market.sqlite3"
                _create_cash_basis_database(
                    database,
                    current_tomorrow=current_tomorrow,
                )
                connection = sqlite3.connect(database)
                connection.row_factory = sqlite3.Row
                try:
                    result = select_effective_usd_average(
                        connection,
                        "CASH",
                        CASH_AS_OF,
                    )
                finally:
                    connection.close()

                self.assertEqual(result["status"], "ESTIMATED")
                self.assertAlmostEqual(result["average_price"], expected)
                self.assertEqual(result["market_direction"], direction)
                self.assertEqual(result["cash_direction_beta"], beta)
                self.assertEqual(result["banking_session_state"], "AFTER_BANKING_CLOSE")
                self.assertAlmostEqual(result["cash_closed_hours"], 2.5)
                self.assertAlmostEqual(result["cash_time_widening_toman"], 375)
                self.assertEqual(
                    result["market_movement_driver"],
                    "USD_HERAT_TOMORROW",
                )

    def test_anchor_transfer_uses_the_same_directional_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "market.sqlite3"
            _create_database(database, current_usdt=98_000)

            context = read_market_context(
                database,
                as_of=AS_OF,
                settlement="CASH",
                maximum_primary_age_seconds=60,
            )

        self.assertEqual(context.usd.status, "BRIDGED")
        self.assertAlmostEqual(context.usd.value, 97_900)
        self.assertEqual(context.usd.anchor_value, 100_000)
        self.assertEqual(context.usd.reference_trend, "DOWN")
        self.assertAlmostEqual(context.usd.reference_applied_return, -0.021)
        self.assertEqual(context.usd.cash_direction_beta, 1.05)
        self.assertEqual(
            context.usd.market_movement_driver,
            "USD_HERAT_TOMORROW",
        )
        self.assertNotEqual(context.usd.source, "USDT_IRT_PROXY")

    def test_anchor_transfer_rejects_usdt_without_herat_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "market.sqlite3"
            _create_database(database, include_herat_anchor=False)

            context = read_market_context(
                database,
                as_of=AS_OF,
                settlement="CASH",
                maximum_primary_age_seconds=60,
            )

        self.assertEqual(context.usd.status, "NO_DATA")
        self.assertIsNone(context.usd.value)


if __name__ == "__main__":
    unittest.main()
