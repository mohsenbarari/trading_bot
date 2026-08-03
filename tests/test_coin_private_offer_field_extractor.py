from __future__ import annotations

import unittest

from core.market_intelligence.group_commodity_context import (
    commodity_context_requires_abstention,
    resolve_offer_commodity,
)
from scripts.coin_intelligence_private_ingest.offer_field_extractor_v2 import extract


class CoinPrivateOfferFieldExtractorTests(unittest.TestCase):
    def test_omitted_full_coin_name_uses_project_default_imam(self) -> None:
        offers, reasons = extract("10تا نقدی 180خ")

        self.assertEqual(reasons, [])
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["commodity"], "امام")
        self.assertEqual(
            offers[0]["commodity_method"], "default_imam_omitted_commodity"
        )

        resolved = resolve_offer_commodity(
            offers[0],
            as_of_epoch=200,
            prior_offers=[
                {
                    "commodity": "بهار",
                    "commodity_method": "explicit",
                    "price": 180_000,
                    "price_method": "full",
                    "settlement": "CASH",
                    "trade_form": "PHYSICAL",
                    "confidence": 0.99,
                    "event_epoch": 100,
                }
            ],
        )
        self.assertEqual(resolved["commodity"], "امام")
        self.assertEqual(
            resolved["commodity_validation_status"], "PROJECT_DEFAULT_IMAM"
        )
        self.assertFalse(commodity_context_requires_abstention(resolved))

    def test_compact_quantity_side_and_rial_price_are_normalized(self) -> None:
        offers, reasons = extract("10تاف 182.950.000")

        self.assertEqual(reasons, [])
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["price"], 182_950)
        self.assertEqual(offers[0]["side"], "SELL")
        self.assertEqual(offers[0]["quantity"], 10)


if __name__ == "__main__":
    unittest.main()
