from __future__ import annotations

import unittest

from core.market_intelligence.group_offer_parser import (
    enrich_records,
    explicit_commodity,
    offer_context,
)
from core.market_intelligence.group_trade_parser import (
    analyze_reply_trades,
    reply_price_adjustment,
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

    def test_reply_price_tail_cannot_expand_into_an_unrelated_market_price(self) -> None:
        offer = {"commodity": "امام", "price": 182_300}

        adjustment = reply_price_adjustment("۲۲۰ برکت", offer)

        self.assertEqual(
            adjustment,
            {
                "price": 182_220,
                "price_raw": "220",
                "price_method": "reply_contextual_tail",
            },
        )

    def test_reply_full_price_outside_linked_market_is_rejected(self) -> None:
        offer = {"commodity": "امام", "price": 182_300}

        self.assertIsNone(reply_price_adjustment("۲۲۰۰۰۰ برکت", offer))

    def test_reply_chain_uses_the_linked_price_prefix_for_a_negotiated_tail(self) -> None:
        messages = [
            {
                "message_id": 1,
                "date_utc": "2026-08-02T07:15:55+00:00",
                "from_name": "seller",
                "text": "۱۸۲۳۰۰ ف ۵",
                "reply_to_message_id": None,
            },
            {
                "message_id": 2,
                "date_utc": "2026-08-02T07:17:06+00:00",
                "from_name": "buyer",
                "text": "یه چیزی پایینتر برکت",
                "reply_to_message_id": 1,
            },
            {
                "message_id": 3,
                "date_utc": "2026-08-02T07:17:20+00:00",
                "from_name": "seller",
                "text": "۲۲۰ برکت",
                "reply_to_message_id": 2,
            },
        ]
        offers = {
            1: [
                {
                    "commodity": "امام",
                    "price": 182_300,
                    "price_raw": "182300",
                    "price_method": "full",
                    "quantity": 5,
                    "side": "SELL",
                    "settlement": "TOMORROW",
                    "trade_form": "PHYSICAL",
                    "confidence": 0.91,
                }
            ]
        }

        trades = analyze_reply_trades(messages, offers)["accepted_trades"]

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["price"], 182_220)
        self.assertEqual(trades[0]["price_method"], "reply_contextual_tail")
        self.assertTrue(trades[0]["training_eligible"])


if __name__ == "__main__":
    unittest.main()
