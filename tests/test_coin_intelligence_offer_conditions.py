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
            {"PAYMENT_DEADLINE", "PAYMENT_RAIL", "SETTLEMENT_PROCESS", "QUANTITY_EXECUTION"},
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
