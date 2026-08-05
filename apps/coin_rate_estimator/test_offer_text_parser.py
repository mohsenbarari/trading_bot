from __future__ import annotations

import unittest
from datetime import datetime, timezone

from offer_text_parser import (
    LabeledOffer,
    SequenceContext,
    SupervisedOfferParser,
    strip_clock_from_raw_offer,
)


def example(
    offer_id: int,
    text: str,
    commodity: str,
    settlement: str,
    side: str,
    price: int,
) -> LabeledOffer:
    return LabeledOffer(
        offer_id=offer_id,
        text=text,
        commodity=commodity,
        settlement=settlement,
        trade_form="PHYSICAL",
        side=side,
        price=price,
        quantity=10,
        occurred_at_utc="2026-07-26T12:00:00Z",
        created_at_utc="2026-07-26T12:01:00Z",
        is_live_at_entry=False,
    )


class SupervisedOfferParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = SupervisedOfferParser(
            [
                example(1, "180500 خ 10تا", "امام", "TOMORROW", "BUY", 180500),
                example(2, "175500 خ پایین 5تا", "بهار", "TOMORROW", "BUY", 175500),
                example(3, "نیم 91700 ف 10تا", "نیم بهار", "TOMORROW", "SELL", 91700),
                example(4, "ربع 51200 ف 10تا", "ربع بهار", "TOMORROW", "SELL", 51200),
                example(5, "نقدی 179500 خ 5تا", "امام", "CASH", "BUY", 179500),
            ]
        )

    def test_attached_sell_and_quantity_are_extracted(self) -> None:
        parsed = self.parser.parse("180600ف20")
        self.assertEqual(parsed["side"], "SELL")
        self.assertEqual(parsed["settlement"], "TOMORROW")
        self.assertEqual(parsed["price"], 180600)
        self.assertEqual(parsed["quantity"], 20)

    def test_operator_clock_is_removed_from_stored_offer_wording(self) -> None:
        self.assertEqual(
            strip_clock_from_raw_offer("۱۵:۳۱ — ۱۰ تا ربع ۵۱۲۰۰ ف"),
            "۱۰ تا ربع ۵۱۲۰۰ ف",
        )
        self.assertEqual(
            strip_clock_from_raw_offer("۱۰ تا ربع ۵۱۲۰۰ ف، ساعت ۱۵:۳۱"),
            "۱۰ تا ربع ۵۱۲۰۰ ف",
        )

    def test_price_band_infers_commodity_when_name_is_omitted(self) -> None:
        half = self.parser.parse("91800 ف 10 تا")
        quarter = self.parser.parse("10تا 51/200 ف")
        self.assertEqual(half["commodity"], "نیم بهار")
        self.assertEqual(quarter["commodity"], "ربع بهار")
        self.assertEqual(quarter["price"], 51200)

    def test_cash_requires_explicit_cash_marker(self) -> None:
        cash = self.parser.parse("25 تا نقدی 179500 ف")
        tomorrow = self.parser.parse("25 تا 180500 ف")
        self.assertEqual(cash["settlement"], "CASH")
        self.assertEqual(tomorrow["settlement"], "TOMORROW")

    def test_clipped_ta_does_not_become_cash(self) -> None:
        clipped_ta = self.parser.parse("20 ت خ 184200")
        compact_today = self.parser.parse("178500 خ ت 4تا")
        self.assertEqual(clipped_ta["settlement"], "TOMORROW")
        self.assertEqual(compact_today["settlement"], "CASH")

    def test_compact_cash_side_and_recent_group_spellings(self) -> None:
        compact_cash = self.parser.parse("10 تا ربع 51500 فن")
        low_date = self.parser.parse("20 تا رب پایین 45500 ف بالا 80")
        date_low_full = self.parser.parse("خ ت پ 5تا 177800")
        quarter_typo = self.parser.parse("6 تا ریع 51.500ف")
        attached_half_buy = self.parser.parse("۵تا ۹۳خنیم")
        doubled_sell = self.parser.parse("20 تا فف 184400")
        self.assertEqual(compact_cash["side"], "SELL")
        self.assertEqual(compact_cash["settlement"], "CASH")
        self.assertEqual(low_date["commodity"], "ربع تاریخ پایین")
        self.assertEqual(low_date["price"], 45500)
        self.assertEqual(date_low_full["commodity"], "بهار")
        self.assertEqual(date_low_full["settlement"], "TOMORROW")
        self.assertEqual(quarter_typo["commodity"], "ربع بهار")
        self.assertEqual(quarter_typo["side"], "SELL")
        self.assertEqual(attached_half_buy["side"], "BUY")
        self.assertEqual(doubled_sell["side"], "SELL")

    def test_word_quantity_full_toman_and_missing_zero_are_normalized(self) -> None:
        gram = self.parser.parse("27500 ف گرمی ده تا")
        full_toman = self.parser.parse("10 ف 182.900.000")
        missing_zero = self.parser.parse("50 تا خ 18000 شب حساب")
        leading_quantity = self.parser.parse("50 ف 175200 بهار")
        compact_quantity_first = self.parser.parse("50 ف 180 شب حساب یکجا")
        compact_quantity_last = self.parser.parse("180ف 50")
        quantity_range = self.parser.parse("180500 خ نقد از 1 تا 5 تا")
        typo_one = self.parser.parse("بدونه نیم نقدی 92500خ")
        self.assertEqual(gram["commodity"], "یک گرمی")
        self.assertEqual(gram["quantity"], 10)
        self.assertEqual(full_toman["price"], 182900)
        self.assertEqual(full_toman["quantity"], 10)
        self.assertEqual(missing_zero["commodity"], "امام")
        self.assertEqual(missing_zero["price"], 180000)
        self.assertEqual(leading_quantity["price"], 175200)
        self.assertEqual(leading_quantity["quantity"], 50)
        self.assertEqual(compact_quantity_first["price"], 180000)
        self.assertEqual(compact_quantity_first["quantity"], 50)
        self.assertEqual(compact_quantity_last["price"], 180000)
        self.assertEqual(compact_quantity_last["quantity"], 50)
        self.assertEqual(quantity_range["quantity"], 5)
        self.assertEqual(typo_one["quantity"], 1)

    def test_ambiguous_stored_side_does_not_train_side_classifier(self) -> None:
        parser = SupervisedOfferParser(
            [
                example(
                    index,
                    f"10 تا {180000 + index * 100}",
                    "امام",
                    "TOMORROW",
                    "BUY",
                    180000 + index * 100,
                )
                for index in range(1, 9)
            ]
        )
        parsed = parser.parse("20 تا 181500")
        self.assertEqual(parsed["side"], "")
        self.assertIn("SIDE_REQUIRES_REVIEW", parsed["warnings"])

    def test_side_without_explicit_marker_always_abstains(self) -> None:
        parsed = self.parser.parse("15 تا ربع 51500")
        self.assertEqual(parsed["side"], "")
        self.assertIn("SIDE_REQUIRES_REVIEW", parsed["warnings"])

    def test_clock_uses_previous_offer_date_and_unchecks_live(self) -> None:
        previous = SequenceContext(
            offer_id=9,
            occurred_at_utc="2026-07-26T12:00:00Z",
            created_at_utc="2026-07-26T12:01:00Z",
            is_live_at_entry=False,
        )
        parsed = self.parser.parse(
            "15:31 10 تا ربع 51200 ف",
            previous=previous,
            now=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )
        self.assertEqual(parsed["offer_time"], "2026-07-26T15:31")
        self.assertEqual(parsed["offer_live"], 0)
        self.assertTrue(parsed["time_detected"])

    def test_market_session_clock_rolls_to_next_date(self) -> None:
        previous = SequenceContext(
            offer_id=9,
            occurred_at_utc="2026-07-26T12:46:00Z",  # 16:16 Tehran
            created_at_utc="2026-07-26T12:47:00Z",
            is_live_at_entry=False,
        )
        parsed = self.parser.parse(
            "10:05 10 تا 180500 خ",
            previous=previous,
            now=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )
        self.assertEqual(parsed["offer_time"], "2026-07-27T10:05")
        self.assertIn(
            "DATE_ADVANCED_FROM_SEQUENCE_ROLLOVER", parsed["warnings"]
        )

    def test_small_backtrack_keeps_previous_date_for_out_of_order_entry(self) -> None:
        previous = SequenceContext(
            offer_id=9,
            occurred_at_utc="2026-07-26T12:30:00Z",  # 16:00 Tehran
            created_at_utc="2026-07-26T12:31:00Z",
            is_live_at_entry=False,
        )
        parsed = self.parser.parse(
            "15:55 10 تا 180500 خ",
            previous=previous,
            now=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )
        self.assertEqual(parsed["offer_time"], "2026-07-26T15:55")
        self.assertIn(
            "NON_MONOTONIC_CLOCK_KEPT_ON_PREVIOUS_DATE", parsed["warnings"]
        )


if __name__ == "__main__":
    unittest.main()
