import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.trading_settings import get_settings


def make_settings(**overrides):
    data = {
        "invitation_expiry_days": 7,
        "offer_expiry_minutes": 15,
        "offer_min_quantity": 1,
        "offer_max_quantity": 50,
        "max_active_offers": 10,
        "offer_expire_rate_per_minute": 3,
        "offer_expire_daily_limit_after_threshold": 20,
        "invitation_expiry_minutes": 10080,
        "lot_min_size": 1,
        "lot_max_count": 10,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TradingSettingsRouterReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_settings_maps_loaded_settings_to_response(self):
        settings = make_settings(offer_expiry_minutes=30, lot_max_count=8)

        with patch("api.routers.trading_settings.load_trading_settings_async", new=AsyncMock(return_value=settings)):
            result = await get_settings()

        self.assertEqual(result.offer_expiry_minutes, 30)
        self.assertEqual(result.lot_max_count, 8)
        self.assertEqual(result.invitation_expiry_minutes, 10080)


if __name__ == "__main__":
    unittest.main()