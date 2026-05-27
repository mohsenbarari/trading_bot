import json
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
import unittest

from core.services import market_schedule_service
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

    def test_timezone_and_parsing_helpers_fall_back_safely(self):
        self.assertEqual(
            market_schedule_service.get_market_timezone_name(TradingSettings(market_timezone="   ")),
            "Asia/Tehran",
        )
        self.assertEqual(
            market_schedule_service.get_market_timezone_name(TradingSettings(market_timezone="Bad/Timezone")),
            "Asia/Tehran",
        )
        self.assertEqual(
            str(market_schedule_service.get_market_timezone(TradingSettings(market_timezone="Asia/Tehran"))),
            "Asia/Tehran",
        )
        self.assertIsNone(market_schedule_service._parse_local_time(None))
        self.assertIsNone(market_schedule_service._parse_local_time("   "))
        self.assertIsNone(market_schedule_service._parse_local_time("not-a-time"))
        self.assertEqual(market_schedule_service._parse_local_time("08:45"), time(8, 45))

    def test_helper_normalization_and_transition_candidates_cover_invalid_inputs(self):
        settings = TradingSettings.model_construct(
            market_schedule_enabled=True,
            market_open_time_local="09:00",
            market_close_time_local="17:00",
            market_closed_weekdays=[0, "2", "bad", -1, 9],
            market_timezone="Asia/Tehran",
        )
        tz = ZoneInfo("Asia/Tehran")
        valid_override = MarketScheduleOverride(
            date=date(2026, 5, 26),
            override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
        )
        overrides_by_date = market_schedule_service._override_mapping(
            [valid_override, type("NoDateOverride", (), {"date": None})()]
        )

        self.assertEqual(market_schedule_service._normalize_closed_weekdays(settings), {0, 2})
        self.assertEqual(overrides_by_date, {date(2026, 5, 26): valid_override})
        self.assertEqual(
            market_schedule_service._coerce_local_datetime(datetime(2026, 5, 24, 10, 0), tz),
            datetime(2026, 5, 24, 10, 0, tzinfo=tz),
        )
        aware_utc = datetime(2026, 5, 24, 6, 30, tzinfo=timezone.utc)
        self.assertEqual(
            market_schedule_service._coerce_local_datetime(aware_utc, tz),
            aware_utc.astimezone(tz),
        )

        candidates = market_schedule_service._candidate_transition_datetimes(
            datetime(2026, 5, 24, 8, 0, tzinfo=tz),
            settings,
            {},
        )
        self.assertIn(datetime(2026, 5, 24, 9, 0, tzinfo=tz), candidates)
        self.assertIn(datetime(2026, 5, 24, 17, 0, tzinfo=tz), candidates)
        self.assertIn(datetime(2026, 5, 25, 0, 0, tzinfo=tz), candidates)

    def test_reason_and_next_transition_cover_after_close_and_invalid_custom_hours(self):
        tz = ZoneInfo("Asia/Tehran")
        settings = TradingSettings(
            market_schedule_enabled=True,
            market_open_time_local="09:00",
            market_close_time_local="17:00",
        )
        after_close = datetime(2026, 5, 24, 18, 0, tzinfo=tz)
        day_rule = market_schedule_service._resolve_day_rule(after_close.date(), settings, {})
        self.assertEqual(market_schedule_service._reason_for_local_time(after_close, day_rule), "after_daily_window_close")
        self.assertEqual(
            market_schedule_service._next_transition_at(after_close, settings, {}),
            datetime(2026, 5, 25, 9, 0, tzinfo=tz),
        )

        invalid_custom_rule = market_schedule_service._resolve_day_rule(
            date(2026, 5, 25),
            settings,
            {
                date(2026, 5, 25): MarketScheduleOverride(
                    date=date(2026, 5, 25),
                    override_type=MarketScheduleOverrideType.CUSTOM_HOURS,
                    open_time_local=time(15, 0),
                    close_time_local=time(12, 0),
                )
            },
        )
        self.assertEqual(invalid_custom_rule.source, "invalid_schedule")
        self.assertFalse(market_schedule_service._is_open_for_rule(after_close, invalid_custom_rule))


if __name__ == "__main__":
    unittest.main()