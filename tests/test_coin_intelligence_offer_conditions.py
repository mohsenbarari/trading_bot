from __future__ import annotations

from datetime import datetime, timezone
import unittest

from core.market_intelligence.coin_offer_conditions import (
    extract_offer_conditions,
    market_session_phase,
    masked_condition_model_text,
)


class CoinOfferConditionTests(unittest.TestCase):
    def test_condition_is_separated_from_offer_core(self) -> None:
        result = extract_offer_conditions(
            "ف 40 تا امام 192000 فردایی فیش تا 2",
            event_time_utc=datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc),
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
        )

        self.assertTrue(result.has_condition)
        self.assertIn("PAYMENT_DEADLINE", result.condition_families)
        self.assertIn("فیش تا 2", result.condition_text)
        self.assertNotIn("فیش", result.offer_core_text)
        self.assertEqual(result.deadline_clock_minute, 14 * 60)

    def test_same_phrase_has_different_composite_for_settlement(self) -> None:
        common = {
            "text": "امام 192000 فیش تا 2",
            "event_time_utc": "2026-08-19T06:45:00Z",
            "trade_form": "PHYSICAL",
        }
        cash = extract_offer_conditions(settlement_term="CASH", **common)
        tomorrow = extract_offer_conditions(settlement_term="TOMORROW", **common)

        self.assertEqual(cash.condition_families, tomorrow.condition_families)
        self.assertNotEqual(cash.composite_class, tomorrow.composite_class)

    def test_same_phrase_has_different_composite_by_market_phase(self) -> None:
        opening = extract_offer_conditions(
            "ربع 52000 فیش تا 2",
            event_time_utc="2026-08-19T06:45:00Z",  # 10:15 Tehran
            settlement_term="CASH",
            trade_form="PHYSICAL",
        )
        midday = extract_offer_conditions(
            "ربع 52000 فیش تا 2",
            event_time_utc="2026-08-19T08:30:00Z",  # 12:00 Tehran
            settlement_term="CASH",
            trade_form="PHYSICAL",
        )

        self.assertEqual(opening.market_session_phase, "OPENING_FIRST_HOUR")
        self.assertEqual(midday.market_session_phase, "MID_SESSION")
        self.assertGreater(opening.deadline_horizon_minutes, midday.deadline_horizon_minutes)
        self.assertNotEqual(opening.composite_class, midday.composite_class)

    def test_quantity_range_is_not_mistaken_for_deadline(self) -> None:
        result = extract_offer_conditions(
            "180500 خ نقد از 1 تا 5 تا",
            event_time_utc="2026-08-19T07:00:00Z",
            settlement_term="CASH",
            trade_form="PHYSICAL",
        )

        self.assertFalse(result.has_condition)
        self.assertEqual(result.deadline_horizon_bucket, "NO_DEADLINE")

    def test_multi_label_condition_keeps_axes(self) -> None:
        result = extract_offer_conditions(
            "ف امام 190000 یکجا تسویه ساتنا فیش تا 14:30",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
        )

        self.assertEqual(
            set(result.condition_families),
            {
                "PAYMENT_ACCOUNT",
                "PAYMENT_DEADLINE",
                "PAYMENT_RAIL",
                "SETTLEMENT_PROCESS",
                "QUANTITY_EXECUTION",
            },
        )
        self.assertEqual(result.deadline_clock_minute, 14 * 60 + 30)

    def test_settlement_word_without_clock_is_not_a_payment_deadline(self) -> None:
        result = extract_offer_conditions(
            "ف امام 190000 تسویه ساتنا",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
        )

        self.assertNotIn("PAYMENT_DEADLINE", result.condition_families)
        self.assertIn("SETTLEMENT_PROCESS", result.condition_families)
        self.assertEqual(result.deadline_horizon_bucket, "NO_DEADLINE")

    def test_payment_account_and_fast_receipt_are_separate_fields(self) -> None:
        result = extract_offer_conditions(
            "20 تا نقدی تک حساب فیش زود",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="CASH",
            trade_form="PHYSICAL",
        )

        self.assertIn("PAYMENT_ACCOUNT", result.condition_families)
        self.assertIn("IMMEDIATE", result.condition_families)
        self.assertNotIn("PAYMENT_DEADLINE", result.condition_families)

    def test_word_clock_is_a_payment_deadline(self) -> None:
        result = extract_offer_conditions(
            "ف امام 190000 فیش تا دو",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
        )

        self.assertIn("PAYMENT_DEADLINE", result.condition_families)
        self.assertEqual(result.deadline_clock_minute, 14 * 60)

    def test_relative_receipt_deadline_uses_offer_time(self) -> None:
        result = extract_offer_conditions(
            "20 تا نقدی فیش یه ساعته",
            event_time_utc="2026-08-19T08:00:00Z",  # 11:30 Tehran
            settlement_term="CASH",
            trade_form="PHYSICAL",
        )

        self.assertIn("PAYMENT_DEADLINE", result.condition_families)
        self.assertEqual(result.deadline_horizon_minutes, 60)
        self.assertEqual(result.deadline_clock_minute, 12 * 60 + 30)

    def test_offer_price_after_receipt_word_is_not_a_deadline(self) -> None:
        result = extract_offer_conditions(
            "فیش 190000 امام فروش",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
        )

        self.assertNotIn("PAYMENT_DEADLINE", result.condition_families)
        self.assertIsNone(result.deadline_clock_minute)

    def test_short_settlement_abbreviation_does_not_match_inside_words(self) -> None:
        result = extract_offer_conditions(
            "فیش حتما راس 190000",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
        )

        self.assertNotIn("SETTLEMENT_PROCESS", result.condition_families)

    def test_bundle_and_packaging_conditions_are_distinct(self) -> None:
        result = extract_offer_conditions(
            "ربع وکیوم تمیز 20 تا باهم",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
        )

        self.assertIn("QUANTITY_EXECUTION", result.condition_families)
        self.assertIn("ITEM_QUALITY_PACKAGING", result.condition_families)

    def test_goods_ready_is_delivery_not_generic_cash_settlement(self) -> None:
        result = extract_offer_conditions(
            "ربع نقدی جنس حاضر",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="CASH",
            trade_form="PHYSICAL",
        )

        self.assertIn("DELIVERY_HANDOFF", result.condition_families)

    def test_bank_and_account_count_constraints_are_payment_account(self) -> None:
        for text in (
            "با ملت",
            "دوتا حساب",
            "حساب 500 تومنی",
            "فیش یه قلم",
            "فیش. خوب",
            "تک خساب",
            "فیش میدم",
            "فیش",
            "ملتی",
            "ملی زود",
            "چنتا حساب",
            "با دو تا حساب",
            "ح خیلی درشت",
            "حساب رود",
            "فیش بالا",
            "3 تا حساب",
            "حساب 500",
            "ح 500 تومنی",
            "حساب میخوام",
            "کم حساب",
            "حساب الان",
            "ملت",
            "سرمایه ب سرمایه",
        ):
            with self.subTest(text=text):
                result = extract_offer_conditions(
                    f"20 تا نقدی {text}",
                    event_time_utc="2026-08-19T08:00:00Z",
                    settlement_term="CASH",
                    trade_form="PHYSICAL",
                )
                self.assertIn("PAYMENT_ACCOUNT", result.condition_families)

    def test_receipt_until_clock_with_explicit_hour_word_is_deadline(self) -> None:
        result = extract_offer_conditions(
            "10 تا نیم نقد فیش تا ساعت 13",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="CASH",
            trade_form="PHYSICAL",
        )

        self.assertIn("PAYMENT_DEADLINE", result.condition_families)
        self.assertIn("PAYMENT_ACCOUNT", result.condition_families)
        self.assertEqual(result.deadline_clock_minute, 13 * 60)

    def test_reverse_night_account_abbreviation_is_settlement_process(self) -> None:
        for text in ("ش ح", "ح شب", "ح ش", "شح", "شب خساب", "حساب امشب"):
            with self.subTest(text=text):
                result = extract_offer_conditions(
                    f"20 تا فردایی {text}",
                    event_time_utc="2026-08-19T08:00:00Z",
                    settlement_term="TOMORROW",
                    trade_form="PHYSICAL",
                )
                self.assertIn("SETTLEMENT_PROCESS", result.condition_families)

    def test_forward_night_account_abbreviation_keeps_raw_condition_span(self) -> None:
        result = extract_offer_conditions(
            "95500 خ نیم ده تا ش ح",
            event_time_utc="2026-08-20T10:00:00Z",
            settlement_term="CASH",
            trade_form="PHYSICAL",
        )

        self.assertEqual(result.condition_families, ("SETTLEMENT_PROCESS",))
        self.assertEqual(result.condition_text, "ش ح")
        self.assertEqual(result.offer_core_text, "95500 خ نیم ده تا")

    def test_single_and_night_account_abbreviations_keep_distinct_spans(self) -> None:
        result = extract_offer_conditions(
            "15 تا خ تک ح 190200 شب ح",
            event_time_utc="2026-08-20T10:00:00Z",
            settlement_term="CASH",
            trade_form="PHYSICAL",
        )

        self.assertEqual(
            result.condition_families,
            ("PAYMENT_ACCOUNT", "SETTLEMENT_PROCESS"),
        )
        self.assertEqual(result.condition_text, "تک ح | شب ح")
        self.assertEqual(result.condition_spans, ((8, 12), (20, 24)))
        self.assertEqual(result.offer_core_text, "15 تا خ 190200")

    def test_joined_one_place_does_not_consume_following_word(self) -> None:
        result = extract_offer_conditions(
            "20 تا نیم یجاشنبه",
            event_time_utc="2026-08-19T08:00:00Z",
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
        )

        self.assertNotIn("QUANTITY_EXECUTION", result.condition_families)

    def test_masked_model_text_does_not_retain_numbers(self) -> None:
        masked = masked_condition_model_text("فیش تا ۱۴:۳۰ برای 20 عدد")

        self.assertNotRegex(masked, r"\d")
        self.assertIn("<NUM>", masked)

    def test_off_day_is_separate_session_phase(self) -> None:
        self.assertEqual(
            market_session_phase(datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)),
            "OFF_DAY",
        )


if __name__ == "__main__":
    unittest.main()
