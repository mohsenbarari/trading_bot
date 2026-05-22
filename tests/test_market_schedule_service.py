import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
import unittest

from core.services.market_schedule_service import evaluate_market_schedule
from core.trading_settings import TradingSettings
from models.market_schedule_override import MarketScheduleOverride, MarketScheduleOverrideType


class MarketScheduleServiceTests(unittest.TestCase):
    def test_schedule_disabled_returns_open_without_transition(self):
        result = evaluate_market_schedule(TradingSettings())
        self.assertTrue(result.is_open)
        self.assertEqual(result.reason, "schedule_disabled")
        self.assertIsNone(result.next_transition_at)
        self.assertEqual(result.timezone, "Asia/Tehran")

    def test_open_all_day_override_beats_weekly_closed_day(self):
        tz = ZoneInfo("Asia/Tehran")
        settings = TradingSettings(
            market_schedule_enabled=True,
            market_closed_weekdays=[0],
        )
        override = MarketScheduleOverride(
            date=date(2026, 5, 25),
            override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
        )

        result = evaluate_market_schedule(
            settings,
            current_time=datetime(2026, 5, 25, 13, 0, tzinfo=tz),
            overrides=[override],
        )

        self.assertTrue(result.is_open)
        self.assertEqual(result.reason, "override_open_all_day")
        self.assertEqual(result.next_transition_at, datetime(2026, 5, 26, 0, 0, tzinfo=tz))

    def test_custom_hours_override_controls_before_open_and_in_window(self):
        tz = ZoneInfo("Asia/Tehran")
        settings = TradingSettings(market_schedule_enabled=True)
        override = MarketScheduleOverride(
            date=date(2026, 5, 24),
            override_type=MarketScheduleOverrideType.CUSTOM_HOURS,
            open_time_local=time(12, 0),
            close_time_local=time(15, 0),
        )

        before_open = evaluate_market_schedule(
            settings,
            current_time=datetime(2026, 5, 24, 10, 0, tzinfo=tz),
            overrides=[override],
        )
        in_window = evaluate_market_schedule(
            settings,
            current_time=datetime(2026, 5, 24, 13, 30, tzinfo=tz),
            overrides=[override],
        )

        self.assertFalse(before_open.is_open)
        self.assertEqual(before_open.reason, "before_override_custom_hours_open")
        self.assertEqual(before_open.next_transition_at, datetime(2026, 5, 24, 12, 0, tzinfo=tz))
        self.assertTrue(in_window.is_open)
        self.assertEqual(in_window.reason, "override_custom_hours_open")
        self.assertEqual(in_window.next_transition_at, datetime(2026, 5, 24, 15, 0, tzinfo=tz))

    def test_closed_all_day_override_points_to_next_daily_open(self):
        tz = ZoneInfo("Asia/Tehran")
        settings = TradingSettings(
            market_schedule_enabled=True,
            market_open_time_local="09:00",
            market_close_time_local="17:00",
        )
        override = MarketScheduleOverride(
            date=date(2026, 5, 24),
            override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
        )

        result = evaluate_market_schedule(
            settings,
            current_time=datetime(2026, 5, 24, 10, 0, tzinfo=tz),
            overrides=[override],
        )

        self.assertFalse(result.is_open)
        self.assertEqual(result.reason, "override_closed_all_day")
        self.assertEqual(result.next_transition_at, datetime(2026, 5, 25, 9, 0, tzinfo=tz))

    def test_invalid_daily_window_returns_invalid_schedule(self):
        tz = ZoneInfo("Asia/Tehran")
        settings = TradingSettings(
            market_schedule_enabled=True,
            market_open_time_local="18:00",
            market_close_time_local="09:00",
        )

        result = evaluate_market_schedule(
            settings,
            current_time=datetime(2026, 5, 24, 12, 0, tzinfo=tz),
        )

        self.assertFalse(result.is_open)
        self.assertEqual(result.reason, "invalid_schedule")
        self.assertIsNone(result.next_transition_at)

    def test_schedule_fields_remain_json_friendly(self):
        settings = TradingSettings(
            market_schedule_enabled=True,
            market_open_time_local="08:30",
            market_close_time_local="17:15",
            market_closed_weekdays=[4, 5],
        )

        dumped = settings.model_dump()
        json.dumps(dumped)

        self.assertEqual(dumped["market_timezone"], "Asia/Tehran")
        self.assertEqual(dumped["market_closed_weekdays"], [4, 5])


if __name__ == "__main__":
    unittest.main()