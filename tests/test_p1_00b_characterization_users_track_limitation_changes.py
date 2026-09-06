from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from api.routers.users import track_limitation_changes


class TestTrackLimitationChanges(unittest.TestCase):
    def test_no_update_case_preserves_attributes_and_does_not_reset_counters(self):
        user = SimpleNamespace(
            max_daily_trades=5,
            max_active_commodities=10,
            max_daily_requests=15,
            limitations_expire_at=None,
        )
        with patch("api.routers.users.reset_user_counters_in_memory") as mock_reset:
            messages, limitation_needed, unlimit_needed = track_limitation_changes(
                user, {}
            )

        self.assertEqual(messages, [])
        self.assertFalse(limitation_needed)
        self.assertFalse(unlimit_needed)
        self.assertEqual(user.max_daily_trades, 5)
        self.assertEqual(user.max_active_commodities, 10)
        self.assertEqual(user.max_daily_requests, 15)
        self.assertIsNone(user.limitations_expire_at)
        mock_reset.assert_not_called()

    def test_non_null_limit_updates_mutates_fields_and_resets_counters(self):
        user = SimpleNamespace(
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            limitations_expire_at=None,
        )
        update_data = {
            "max_daily_requests": 30,
            "max_daily_trades": 10,
            "max_active_commodities": 20,
        }
        with patch("api.routers.users.reset_user_counters_in_memory") as mock_reset:
            messages, limitation_needed, unlimit_needed = track_limitation_changes(
                user, update_data
            )

        self.assertEqual(user.max_daily_trades, 10)
        self.assertEqual(user.max_active_commodities, 20)
        self.assertEqual(user.max_daily_requests, 30)
        self.assertEqual(
            messages,
            [
                "مجموع تعداد معاملات: 10",
                "مجموع تعداد کالای معامله شده: 20",
                "مجموع ارسال لفظ در کانال: 30",
            ],
        )
        self.assertTrue(limitation_needed)
        self.assertFalse(unlimit_needed)
        mock_reset.assert_called_once_with(user)

    def test_clearing_all_existing_limits_mutates_to_none_unlimits_and_resets(self):
        user = SimpleNamespace(
            max_daily_trades=5,
            max_active_commodities=10,
            max_daily_requests=15,
            limitations_expire_at=None,
        )
        update_data = {
            "max_daily_trades": None,
            "max_active_commodities": None,
            "max_daily_requests": None,
        }
        with patch("api.routers.users.reset_user_counters_in_memory") as mock_reset:
            messages, limitation_needed, unlimit_needed = track_limitation_changes(
                user, update_data
            )

        self.assertEqual(messages, [])
        self.assertFalse(limitation_needed)
        self.assertTrue(unlimit_needed)
        self.assertIsNone(user.max_daily_trades)
        self.assertIsNone(user.max_active_commodities)
        self.assertIsNone(user.max_daily_requests)
        mock_reset.assert_called_once_with(user)

    def test_partial_clearing_while_another_limit_remains_does_not_reset(self):
        user = SimpleNamespace(
            max_daily_trades=5,
            max_active_commodities=10,
            max_daily_requests=None,
            limitations_expire_at=None,
        )
        update_data = {"max_daily_trades": None}
        with patch("api.routers.users.reset_user_counters_in_memory") as mock_reset:
            messages, limitation_needed, unlimit_needed = track_limitation_changes(
                user, update_data
            )

        self.assertEqual(messages, [])
        self.assertFalse(limitation_needed)
        self.assertFalse(unlimit_needed)
        self.assertIsNone(user.max_daily_trades)
        self.assertEqual(user.max_active_commodities, 10)
        self.assertIsNone(user.max_daily_requests)
        mock_reset.assert_not_called()

    def test_limitations_expire_at_routing_with_aware_utc_datetime(self):
        user = SimpleNamespace(
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            limitations_expire_at=None,
        )
        aware_dt = datetime(2026, 5, 20, 10, 15, 30, tzinfo=timezone.utc)
        update_data = {"limitations_expire_at": aware_dt}
        with patch("api.routers.users.reset_user_counters_in_memory") as mock_reset:
            messages, limitation_needed, unlimit_needed = track_limitation_changes(
                user, update_data
            )

        self.assertEqual(messages, [])
        self.assertFalse(limitation_needed)
        self.assertFalse(unlimit_needed)
        self.assertEqual(user.limitations_expire_at, datetime(2026, 5, 20, 10, 15, 30))
        self.assertIsNone(user.limitations_expire_at.tzinfo)
        mock_reset.assert_not_called()


if __name__ == "__main__":
    unittest.main()
