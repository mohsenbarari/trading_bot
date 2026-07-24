from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.market_intelligence.anchor_transfer import read_market_context


class AnchorTransferSourceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "market.sqlite3"
        connection = sqlite3.connect(self.database)
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
                side TEXT NOT NULL,
                price_num REAL NOT NULL,
                price_unit TEXT NOT NULL,
                parse_confidence REAL NOT NULL
            );
            CREATE TABLE external_market_observations (
                id INTEGER PRIMARY KEY,
                observed_at_utc TEXT NOT NULL,
                instrument_code TEXT NOT NULL,
                quote_kind TEXT NOT NULL,
                normalized_price_num REAL
            );
            """
        )
        rows = (
            (
                "MELTED_GOLD",
                "آبشده نقدی",
                "UNKNOWN",
                "PHYSICAL",
                80_000_000,
                "IRT_PER_MESGHAL_750",
            ),
            (
                "MELTED_GOLD",
                "آبشده حواله",
                "UNKNOWN",
                "PAPER",
                80_500_000,
                "IRT_PER_MESGHAL_750",
            ),
            (
                "GOLD_COIN",
                "سکه نقدی",
                "UNKNOWN",
                "PHYSICAL",
                186_000_000,
                "TOMAN_PER_COIN",
            ),
            (
                "GOLD_COIN",
                "سکه حواله",
                "UNKNOWN",
                "PAPER",
                189_000_000,
                "TOMAN_PER_COIN",
            ),
            (
                "GOLD_COIN",
                "سکه نقدی",
                "TOMORROW",
                "PHYSICAL",
                199_000_000,
                "TOMAN_PER_COIN",
            ),
            (
                "USD_HERAT",
                "دلار هرات نامشخص فیزیکی",
                "UNKNOWN",
                "PHYSICAL",
                100_000,
                "IRT_PER_USD",
            ),
            (
                "USD_HERAT",
                "دلار هرات فردایی کاغذی",
                "TOMORROW",
                "PAPER",
                101_000,
                "IRT_PER_USD",
            ),
            (
                "XAUUSD",
                "اونس جهانی",
                "SPOT",
                "NOT_APPLICABLE",
                3_500,
                "USD_PER_TROY_OUNCE",
            ),
        )
        connection.executemany(
            """
            INSERT INTO price_events(
                event_time_utc, instrument, market_label, settlement_term,
                trade_form, event_type, side, price_num, price_unit,
                parse_confidence
            ) VALUES(
                '2026-07-24T11:59:50Z', ?, ?, ?, ?,
                'QUOTE', 'UNKNOWN', ?, ?, 0.99
            )
            """,
            rows,
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_cash_accepts_unknown_settlement_for_explicit_physical_feeds(
        self,
    ) -> None:
        context = read_market_context(
            self.database,
            as_of="2026-07-24T12:00:00Z",
            settlement="CASH",
        )
        self.assertEqual(context.generic_imam.value, 186_000_000)
        self.assertEqual(context.generic_imam.source, "سکه نقدی")
        self.assertEqual(context.usd.value, 100_000)
        self.assertNotEqual(context.usd.source, "USDT_IRT_PROXY")

    def test_tomorrow_uses_havale_paper_proxy_not_synthetic_physical(
        self,
    ) -> None:
        context = read_market_context(
            self.database,
            as_of="2026-07-24T12:00:00Z",
            settlement="TOMORROW",
        )
        self.assertEqual(context.generic_imam.value, 189_000_000)
        self.assertEqual(context.generic_imam.source, "سکه حواله")
        self.assertNotEqual(context.generic_imam.value, 199_000_000)
        self.assertEqual(context.usd.value, 101_000)


if __name__ == "__main__":
    unittest.main()
