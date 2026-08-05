"""Unit-safe, offline external-market adapter tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.external_markets import (
    ExternalMarketAdapterError,
    ExternalQuoteInput,
    ime_gold_bar_irr_quote_to_observation,
    ime_imam_coin_irr_quote_to_observation,
    usdt_toman_quote_to_observation,
)
from core.market_intelligence.market_snapshot import build_market_snapshot
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)


class ExternalMarketAdapterTests(unittest.TestCase):
    def source(self, price: object, **changes: object) -> ExternalQuoteInput:
        values: dict[str, object] = {
            "source_code": "TEST_EXTERNAL",
            "source_event_id": "event-1",
            "observed_at_utc": "2026-08-04T10:00:00Z",
            "available_at_utc": "2026-08-04T10:00:05Z",
            "quote_kind": "LAST",
            "price": price,
        }
        values.update(changes)
        return ExternalQuoteInput(**values)  # type: ignore[arg-type]

    def test_usdt_toman_is_explicitly_converted_to_irt(self) -> None:
        observation = usdt_toman_quote_to_observation(self.source("188,500"))

        self.assertEqual(str(observation.price), "1885000")
        self.assertEqual(observation.price_unit, "IRT_PER_USDT")
        self.assertEqual(observation.attributes["conversion"], "toman_to_irt_x10")

    def test_ime_gold_bar_converts_weight_and_fineness_into_750_mesghal_irr(self) -> None:
        observation = ime_gold_bar_irr_quote_to_observation(self.source("25000000"))
        expected = (
            Decimal("25000000")
            / Decimal("0.1")
            * Decimal("750")
            / Decimal("995")
            * Decimal("4.3318")
        )

        self.assertEqual(observation.price, expected)
        self.assertEqual(observation.price_unit, "IRT_PER_MESGHAL_750")
        self.assertEqual(observation.attributes["input_unit"], "IRR_PER_CERTIFICATE_0_1G_995")

    def test_ime_coin_is_already_irr_per_coin_and_is_not_divided_by_ten(self) -> None:
        observation = ime_imam_coin_irr_quote_to_observation(self.source("1825000000"))

        self.assertEqual(str(observation.price), "1825000000")
        self.assertEqual(observation.price_unit, "IRT_PER_COIN")

    def test_timestamp_or_quote_kind_error_fails_closed(self) -> None:
        with self.assertRaisesRegex(ExternalMarketAdapterError, "external_available_before_observed"):
            usdt_toman_quote_to_observation(
                self.source(
                    188_500,
                    observed_at_utc="2026-08-04T10:00:00Z",
                    available_at_utc="2026-08-04T09:59:59Z",
                )
            )
        with self.assertRaisesRegex(ExternalMarketAdapterError, "external_quote_kind_unsupported"):
            usdt_toman_quote_to_observation(self.source(188_500, quote_kind="OPEN"))


class ExternalMarketSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_market_store(Path(self.tempdir.name) / "market.sqlite3")
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def test_snapshot_keeps_ime_bar_and_coin_as_separate_references(self) -> None:
        common = {
            "source_code": "IME_OFFICIAL",
            "observed_at_utc": "2026-08-04T10:00:00Z",
            "available_at_utc": "2026-08-04T10:00:10Z",
            "quote_kind": "LAST",
        }
        upsert_observation(
            self.connection,
            ime_gold_bar_irr_quote_to_observation(
                ExternalQuoteInput(source_event_id="bar", price=25_000_000, **common)
            ),
        )
        upsert_observation(
            self.connection,
            ime_imam_coin_irr_quote_to_observation(
                ExternalQuoteInput(source_event_id="coin", price=1_825_000_000, **common)
            ),
        )
        self.connection.commit()

        snapshot = build_market_snapshot(
            self.connection,
            as_of_utc=datetime(2026, 8, 4, 10, 1, tzinfo=timezone.utc),
        )

        bar = snapshot["signals"]["IME_GOLD_BAR"]
        coin = snapshot["signals"]["IME_GOLD_COIN_IMAM"]
        self.assertEqual((bar["status"], coin["status"]), ("FRESH", "FRESH"))
        self.assertEqual(bar["price_unit"], "IRT_PER_MESGHAL_750")
        self.assertEqual(coin["latest_price"], 1_825_000_000.0)


if __name__ == "__main__":
    unittest.main()
