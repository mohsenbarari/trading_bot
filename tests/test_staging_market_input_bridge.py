from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
)
from scripts.bridge_staging_market_inputs import (
    BRIDGE_VERSION,
    _LEGACY_SKIP_ERRORS,
    _SOURCE_SKIP_ERROR_PREFIXES,
    _commodity_code,
    _external_observation,
    _legacy_observation,
    _write_source_rows,
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
    def test_systemd_bridge_relies_on_writer_lock_without_collector_ordering(self) -> None:
        service_path = (
            Path(__file__).resolve().parents[1]
            / "deploy/coin_intelligence/systemd/coin-intelligence-staging-market-input-bridge.service"
        )
        service = service_path.read_text(encoding="utf-8")

        self.assertIn("After=network-online.target\n", service)
        self.assertNotIn("After=network-online.target coin-public-market", service)
        self.assertIn("flock --exclusive --timeout 300", service)

    def test_legacy_herat_toman_stays_toman(self) -> None:
        columns = [
            "source_code", "instrument", "event_type", "side", "price_num", "price_unit",
            "currency", "quantity_num", "quantity_unit", "event_time_utc", "raw_post_id",
            "event_index", "message_id", "settlement_term", "trade_form", "parse_confidence",
        ]
        herat = _legacy_observation(
            _row(columns, [
                "USD_HERAT", "USD_HERAT", "OFFER", "BUY", 188000, "TOMAN_PER_USD", "TOMAN",
                None, None, "2026-08-05T10:00:00Z", 1, 0, 100, "TOMORROW", "PAPER", 0.97,
            ]),
            available_at="2026-08-05T11:00:00Z",
        )
        self.assertEqual(herat.instrument, "USD_HERAT")
        self.assertEqual(herat.price, 188000)
        self.assertEqual(herat.price_unit, "TOMAN_PER_USD")
        self.assertEqual(herat.currency, "TOMAN")
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

    def test_legacy_melted_mislabeled_irt_stays_toman_scale(self) -> None:
        columns = [
            "source_code", "instrument", "event_type", "side", "price_num", "price_unit",
            "currency", "quantity_num", "quantity_unit", "event_time_utc", "raw_post_id",
            "event_index", "message_id", "settlement_term", "trade_form", "parse_confidence",
        ]
        melted = _legacy_observation(
            _row(columns, [
                "MELTED_AGGREGATE", "MELTED_AGGREGATE", "QUOTE", "MID", 80_620_000,
                "IRT_PER_MESGHAL_750", "IRT", None, None, "2026-08-05T10:00:00Z", 3, 0, 102,
                "UNKNOWN", "PHYSICAL", 0.99,
            ]),
            available_at="2026-08-05T11:00:00Z",
        )
        self.assertEqual(melted.instrument, "MELTED_GOLD_AGGREGATE")
        self.assertEqual(melted.price, 80_620_000)
        self.assertEqual(melted.price_unit, "TOMAN_PER_MESGHAL_750")
        self.assertEqual(melted.currency, "TOMAN")

    def test_legacy_uses_parsed_instrument_not_feed_code(self) -> None:
        columns = [
            "source_code", "instrument", "event_type", "side", "price_num", "price_unit",
            "currency", "quantity_num", "quantity_unit", "event_time_utc", "raw_post_id",
            "event_index", "message_id", "settlement_term", "trade_form", "parse_confidence",
        ]
        coin = _legacy_observation(
            _row(columns, [
                "MELTED_AGGREGATE", "GOLD_COIN", "QUOTE", "MID", 120_000_000,
                "TOMAN_PER_COIN", "TOMAN", None, None, "2026-08-05T10:00:00Z", 4, 0, 103,
                "UNKNOWN", "PHYSICAL", 0.95,
            ]),
            available_at="2026-08-05T11:00:00Z",
        )
        self.assertEqual(coin.source_code, "MELTED_AGGREGATE")
        self.assertEqual(coin.instrument, "COIN_PUBLIC_CHANNEL")
        self.assertEqual(coin.price_unit, "TOMAN_PER_COIN")
        normalized = coin.normalized()
        self.assertEqual(normalized.instrument, "COIN_PUBLIC_CHANNEL")

        union = _legacy_observation(
            _row(columns, [
                "MELTED_AGGREGATE", "GOLD_UNION_QUOTE", "QUOTE", "MID", 80_500_000,
                "IRT_PER_MESGHAL_750", "IRT", None, None, "2026-08-05T10:00:00Z", 5, 0, 104,
                "UNKNOWN", "PHYSICAL", 0.9,
            ]),
            available_at="2026-08-05T11:00:00Z",
        )
        self.assertEqual(union.instrument, "MELTED_GOLD_UNION")
        self.assertEqual(union.normalized().price_unit, "TOMAN_PER_MESGHAL_750")

    def test_out_of_range_legacy_coin_does_not_starve_following_melted_rows(self) -> None:
        columns = [
            "id", "source_code", "instrument", "event_type", "side", "price_num",
            "price_unit", "currency", "quantity_num", "quantity_unit", "event_time_utc",
            "raw_post_id", "event_index", "message_id", "settlement_term", "trade_form",
            "parse_confidence",
        ]
        poison_coin = _row(columns, [
            1, "MELTED_AGGREGATE", "GOLD_COIN", "QUOTE", "MID", 30_500_000,
            "TOMAN_PER_COIN", "TOMAN", None, None, "2026-08-23T08:00:00Z",
            1, 0, 100, "UNKNOWN", "PHYSICAL", 0.95,
        ])
        valid_melted = _row(columns, [
            2, "MELTED_AGGREGATE", "MELTED_GOLD", "QUOTE", "MID", 94_300_000,
            "TOMAN_PER_MESGHAL_750", "TOMAN", None, None, "2026-08-23T08:00:01Z",
            2, 0, 101, "UNKNOWN", "PAPER", 0.99,
        ])

        with tempfile.TemporaryDirectory() as directory:
            destination = connect_market_store(Path(directory) / "market.sqlite3")
            try:
                initialize_market_store(destination)
                imported, latest, skipped = _write_source_rows(
                    destination,
                    [poison_coin, valid_melted],
                    _legacy_observation,
                    available_at="2026-08-23T08:01:00Z",
                    skip_errors=_LEGACY_SKIP_ERRORS,
                    skip_error_prefixes=_SOURCE_SKIP_ERROR_PREFIXES,
                )
                stored = destination.execute(
                    "SELECT instrument, price_num FROM market_observations"
                ).fetchall()
            finally:
                destination.close()

        self.assertEqual((imported, latest, skipped), (1, 2, 1))
        self.assertEqual(
            [(row["instrument"], row["price_num"]) for row in stored],
            [("MELTED_GOLD_AGGREGATE", 94_300_000.0)],
        )

    def test_external_quote_keeps_toman_without_times_ten(self) -> None:
        columns = ["id", "instrument_code", "observed_at_utc", "quote_kind", "normalized_price_num"]
        observation = _external_observation(
            _row(columns, [7, "USDT_IRT", "2026-08-05T10:00:00Z", "MID", 188500]),
            available_at="2026-08-05T11:00:00Z",
        )
        self.assertEqual(observation.source_code, "WALLEX_PUBLIC_API")
        self.assertEqual(observation.instrument, "USDT_IRT")
        self.assertEqual(observation.price_unit, "TOMAN_PER_USDT")
        self.assertEqual(observation.price, 188500)
        self.assertEqual(observation.currency, "TOMAN")
        self.assertNotIn("herat", observation.attributes)

    def test_commodity_mapping_is_explicit_and_unresolved_is_not_imam(self) -> None:
        self.assertEqual(_commodity_code("ربع تاریخ پایین"), "QUARTER_LOW_DATE")
        self.assertEqual(_commodity_code("نیم بهار"), "HALF_BAHAR")
        self.assertEqual(_commodity_code(""), "UNRESOLVED")
        self.assertNotEqual(_commodity_code(""), "IMAM")
        self.assertTrue(BRIDGE_VERSION.startswith("staging-market-input-bridge-"))


if __name__ == "__main__":
    unittest.main()
