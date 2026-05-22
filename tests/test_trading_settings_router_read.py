import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.trading_settings import get_market_state, get_settings


def make_settings(**overrides):
    data = {
        "invitation_expiry_days": 7,
        "offer_expiry_minutes": 15,
        "offer_min_quantity": 1,
        "offer_max_quantity": 50,
        "max_active_offers": 10,
        "offer_expire_rate_per_minute": 3,
        "offer_expire_daily_limit_after_threshold": 20,
        "anti_abuse_daily_base": 2,
        "anti_abuse_weekly_base": 5,
        "anti_abuse_monthly_base": 7,
        "invitation_expiry_minutes": 10080,
        "lot_min_size": 1,
        "lot_max_count": 10,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TradingSettingsRouterReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_settings_maps_loaded_settings_to_response(self):
        settings = make_settings(offer_expiry_minutes=30, lot_max_count=8, anti_abuse_monthly_base=11)

        with patch("api.routers.trading_settings.load_trading_settings_async", new=AsyncMock(return_value=settings)):
            result = await get_settings()

        self.assertEqual(result.offer_expiry_minutes, 30)
        self.assertEqual(result.lot_max_count, 8)
        self.assertEqual(result.invitation_expiry_minutes, 10080)
        self.assertEqual(result.anti_abuse_monthly_base, 11)

    async def test_get_market_state_maps_runtime_view_to_response(self):
        runtime_view = SimpleNamespace(
            is_open=False,
            active_web_notice_visible=True,
            offers_since_last_open=2,
            last_transition_at=None,
            next_transition_at=None,
        )

        with patch(
            "api.routers.trading_settings.get_market_runtime_view",
            new=AsyncMock(return_value=runtime_view),
        ):
            result = await get_market_state(db=SimpleNamespace())

        self.assertFalse(result.is_open)
        self.assertTrue(result.active_web_notice_visible)
        self.assertEqual(result.offers_since_last_open, 2)


if __name__ == "__main__":
    unittest.main()