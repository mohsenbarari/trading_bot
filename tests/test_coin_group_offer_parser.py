from __future__ import annotations

import unittest

from core.market_intelligence.group_offer_parser import (
    enrich_records,
    explicit_commodity,
    offer_context,
)


class PriorOnlyAnchorTests(unittest.TestCase):
    def test_coin_group_unmarked_offer_defaults_to_tomorrow(self) -> None:
        self.assertEqual(offer_context("۱۰ تا ۱۸۷۲۰۰ ف")[1], "TOMORROW")

    def test_coin_group_explicit_cash_markers_remain_cash(self) -> None:
        for text in ("۱۰ تا نقدی ۱۸۶۴۰۰ ف", "۱۰ تا ۱۸۶۴۰۰ ف ن", "۱۰ تا نقد حاضر ۱۸۶۴۰۰ ف"):
            with self.subTest(text=text):
                self.assertEqual(offer_context(text)[1], "CASH")

    def test_low_date_aliases_map_to_canonical_products(self) -> None:
        self.assertEqual(explicit_commodity("۵تا پایین ۱۷۳۹۰۰ خ"), "بهار")
        self.assertEqual(explicit_commodity("۵ تا ربع پایین ۴۳۵۰۰ خ"), "ربع تاریخ پایین")
        self.assertEqual(explicit_commodity("۵ تا نیم تاریخ پایین ۸۱۵۰۰ خ"), "نیم تاریخ پایین")

    def test_only_prior_full_price_can_normalize_a_tail(self) -> None:
        records = [
            {"date": "2026-07-23T08:00:00+00:00", "text": "۱۸۴۲۰۰ خ"},
            {"date": "2026-07-23T08:01:00+00:00", "text": "۵۰۰ ف"},
        ]
        enriched = enrich_records(records)
        self.assertEqual(enriched[1]["extracted_offers"][0]["price"], 184_500)
        self.assertEqual(enriched[1]["extracted_offers"][0]["price_method"], "contextual_tail")

        reversed_enriched = enrich_records(list(reversed(records)))
        self.assertNotEqual(
            reversed_enriched[0]["extracted_offers"][0]["price_method"],
            "contextual_tail",
        )


if __name__ == "__main__":
    unittest.main()
