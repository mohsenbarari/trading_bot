import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.trading_settings import reset_settings
from core.trading_settings import TradingSettings


class TradingSettingsRouterResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_settings_raises_when_save_fails(self):
        with patch("api.routers.trading_settings.save_trading_settings_async", new=AsyncMock(return_value=False)), patch(
            "api.routers.trading_settings.refresh_settings_cache_async", new=AsyncMock()
        ) as refresh_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await reset_settings()

        self.assertEqual(exc_info.exception.status_code, 500)
        refresh_mock.assert_not_awaited()

    async def test_reset_settings_saves_defaults_refreshes_cache_and_returns_defaults(self):
        defaults = TradingSettings()

        with patch("api.routers.trading_settings.save_trading_settings_async", new=AsyncMock(return_value=True)) as save_mock, patch(
            "api.routers.trading_settings.refresh_settings_cache_async", new=AsyncMock()
        ) as refresh_mock:
            result = await reset_settings()

        save_mock.assert_awaited_once_with(defaults.model_dump())
        refresh_mock.assert_awaited_once()
        self.assertEqual(result.offer_expiry_minutes, defaults.offer_expiry_minutes)
        self.assertEqual(result.offer_max_quantity, defaults.offer_max_quantity)
        self.assertEqual(result.anti_abuse_daily_base, defaults.anti_abuse_daily_base)


if __name__ == "__main__":
    unittest.main()