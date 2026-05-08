import unittest
from datetime import datetime
from types import SimpleNamespace

from api.routers.users import track_limitation_changes


def make_user(**overrides):
    data = {
        "max_daily_trades": None,
        "max_active_commodities": None,
        "max_daily_requests": None,
        "limitations_expire_at": None,
        "trades_count": 9,
        "commodities_traded_count": 8,
        "channel_messages_count": 7,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class UsersRouterTrackLimitationsTests(unittest.TestCase):
    def test_track_limitation_changes_applies_limits_and_resets_counters(self):
        user = make_user()
        changes, limitation_needed, unlimit_needed = track_limitation_changes(
            user,
            {
                "max_daily_trades": 3,
                "max_daily_requests": 5,
                "limitations_expire_at": datetime(2026, 1, 1, 12, 0, 0),
            },
        )

        self.assertTrue(limitation_needed)
        self.assertFalse(unlimit_needed)
        self.assertIn("مجموع تعداد معاملات: 3", changes)
        self.assertIn("مجموع ارسال لفظ در کانال: 5", changes)
        self.assertEqual(user.trades_count, 0)
        self.assertEqual(user.commodities_traded_count, 0)
        self.assertEqual(user.channel_messages_count, 0)
        self.assertIsNotNone(user.limitations_expire_at)

    def test_track_limitation_changes_detects_unlimit_flow(self):
        user = make_user(max_daily_trades=3)
        changes, limitation_needed, unlimit_needed = track_limitation_changes(
            user,
            {
                "max_daily_trades": None,
                "max_active_commodities": None,
                "max_daily_requests": None,
            },
        )

        self.assertEqual(changes, [])
        self.assertFalse(limitation_needed)
        self.assertTrue(unlimit_needed)
        self.assertEqual(user.trades_count, 0)


if __name__ == "__main__":
    unittest.main()