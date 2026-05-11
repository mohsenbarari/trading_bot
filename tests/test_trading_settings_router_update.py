import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.trading_settings import TradingSettingsUpdate, update_settings


class FakeSettings(SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


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
    return FakeSettings(**data)


class TradingSettingsRouterUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_settings_rejects_invalid_min_max_and_save_failure(self):
        current = make_settings(offer_min_quantity=10, offer_max_quantity=5)
        with patch("api.routers.trading_settings.load_trading_settings_async", new=AsyncMock(return_value=current)):
            with self.assertRaises(HTTPException) as exc_info:
                await update_settings(TradingSettingsUpdate())
        self.assertEqual(exc_info.exception.status_code, 400)

        current = make_settings()
        updates = TradingSettingsUpdate(offer_expiry_minutes=45)
        with patch(
            "api.routers.trading_settings.load_trading_settings_async",
            new=AsyncMock(return_value=current),
        ), patch("api.routers.trading_settings.save_trading_settings_async", new=AsyncMock(return_value=False)):
            with self.assertRaises(HTTPException) as exc_info:
                await update_settings(updates)
        self.assertEqual(exc_info.exception.status_code, 500)

    async def test_update_settings_merges_only_non_none_fields_and_returns_reloaded_settings(self):
        current = make_settings()
        updated = make_settings(offer_expiry_minutes=45, offer_max_quantity=50, anti_abuse_weekly_base=9)
        updates = TradingSettingsUpdate(offer_expiry_minutes=45, offer_max_quantity=None, anti_abuse_weekly_base=9)

        with patch(
            "api.routers.trading_settings.load_trading_settings_async",
            new=AsyncMock(side_effect=[current, updated]),
        ) as load_mock, patch(
            "api.routers.trading_settings.save_trading_settings_async",
            new=AsyncMock(return_value=True),
        ) as save_mock:
            result = await update_settings(updates)

        load_mock.assert_awaited()
        save_mock.assert_awaited_once_with(
            {
                "invitation_expiry_days": 7,
                "offer_expiry_minutes": 45,
                "offer_min_quantity": 1,
                "offer_max_quantity": 50,
                "max_active_offers": 10,
                "offer_expire_rate_per_minute": 3,
                "offer_expire_daily_limit_after_threshold": 20,
                "anti_abuse_daily_base": 2,
                "anti_abuse_weekly_base": 9,
                "anti_abuse_monthly_base": 7,
                "invitation_expiry_minutes": 10080,
                "lot_min_size": 1,
                "lot_max_count": 10,
            }
        )
        self.assertEqual(result.offer_expiry_minutes, 45)
        self.assertEqual(result.offer_max_quantity, 50)
        self.assertEqual(result.anti_abuse_weekly_base, 9)


if __name__ == "__main__":
    unittest.main()