import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import get_settings_keyboard, get_settings_text


class BotPanelSettingsHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_helpers_build_keyboard_and_render_text(self):
        keyboard = get_settings_keyboard()
        self.assertGreaterEqual(len(keyboard.inline_keyboard), 5)

        ts = SimpleNamespace(
            invitation_expiry_days=3,
            offer_expiry_minutes=15,
            offer_min_quantity=1,
            offer_max_quantity=5,
            max_active_offers=2,
            offer_expire_rate_per_minute=4,
            offer_expire_daily_limit_after_threshold=9,
            lot_min_size=1,
            lot_max_count=5,
        )
        with patch("core.trading_settings.get_trading_settings_async", new=AsyncMock(return_value=ts)):
            text = await get_settings_text()

        self.assertIn("تنظیمات سیستم", text)
        self.assertIn("دعوت", text)
        self.assertIn("لفظ معاملاتی", text)


if __name__ == "__main__":
    unittest.main()