from __future__ import annotations

import sqlite3
import unittest

from scripts.bridge_staging_market_inputs import (
    BRIDGE_VERSION,
    _commodity_code,
    _external_observation,
    _legacy_observation,
)


def _row(columns: list[str], values: list[object]) -> sqlite3.Row:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE sample (" + ",".join(f'[{c}]' for c in columns) + ")")
    placeholders = ",".join("?" for _ in values)
    connection.execute("INSERT INTO sample VALUES (" + placeholders + ")", values)
    result = connection.execute("SELECT * FROM sample").fetchone()
    connection.close()
    return result


class StagingMarketInputBridgeTests(unittest.TestCase):
    def test_legacy_herat_toman_is_converted_to_irt_and_spot_xau_is_mid(self) -> None:
        columns = [
            "source_code", "instrument", "event_type", "side", "price_num", "price_unit",
            "currency", "quantity_num", "quantity_unit", "event_time_utc", "raw_post_id",
            "event_index", "message_id", "settlement_term", "trade_form", "parse_confidence",
        ]
        herat = _legacy_observation(
            _row(columns, [
                "USD_HERAT", "USD_HERAT", "OFFER", "BUY", 18800, "TOMAN_PER_USD", "TOMAN",
                None, None, "2026-08-05T10:00:00Z", 1, 0, 100, "TOMORROW", "PAPER", 0.97,
            ]),
            available_at="2026-08-05T11:00:00Z",
        )
        self.assertEqual(herat.instrument, "USD_HERAT")
        self.assertEqual(herat.price, 188000)
        self.assertEqual(herat.price_unit, "IRT_PER_USD")
        self.assertEqual(herat.trade_form, "PAPER_NORMAL")
        self.assertEqual(herat.available_at_utc, "2026-08-05T11:00:00Z")

        xau = _legacy_observation(
            _row(columns, [
                "XAUUSD", "XAUUSD", "QUOTE", "UNKNOWN", 3340, "USD_PER_TROY_OUNCE", "USD",
                None, None, "2026-08-05T10:00:00Z", 2, 0, 101, "SPOT", "NOT_APPLICABLE", 1.0,
            ]),
            available_at="2026-08-05T11:00:00Z",
        )
        self.assertEqual(xau.side, "MID")
        self.assertEqual(xau.event_type, "QUOTE")

    def test_external_quote_uses_normalized_unit_and_never_herat_code(self) -> None:
        columns = ["id", "instrument_code", "observed_at_utc", "quote_kind", "normalized_price_num"]
        observation = _external_observation(
            _row(columns, [7, "USDT_IRT", "2026-08-05T10:00:00Z", "MID", 188500]),
            available_at="2026-08-05T11:00:00Z",
        )
        self.assertEqual(observation.source_code, "WALLEX_PUBLIC_API")
        self.assertEqual(observation.instrument, "USDT_IRT")
        self.assertEqual(observation.price_unit, "IRT_PER_USDT")
        self.assertEqual(observation.price, 188500)
        self.assertNotIn("herat", observation.attributes)

    def test_commodity_mapping_is_explicit_and_unresolved_is_not_imam(self) -> None:
        self.assertEqual(_commodity_code("ربع تاریخ پایین"), "QUARTER_LOW_DATE")
        self.assertEqual(_commodity_code("نیم بهار"), "HALF_BAHAR")
        self.assertEqual(_commodity_code(""), "UNRESOLVED")
        self.assertNotEqual(_commodity_code(""), "IMAM")
        self.assertTrue(BRIDGE_VERSION.startswith("staging-market-input-bridge-"))


if __name__ == "__main__":
    unittest.main()
