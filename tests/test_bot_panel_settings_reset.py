import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import handle_settings_cancel, handle_settings_reset, handle_settings_reset_confirm, handle_settings_reset_cancel
from core.enums import UserRole


class FakeState:
    def __init__(self):
        self.cleared = 0

    async def clear(self):
        self.cleared += 1


class FakeTradingSettings:
    def model_dump(self):
        return {"offer_expiry_minutes": 15}


class BotPanelSettingsResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_cancel_and_reset_flows(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        state = FakeState()
        await handle_settings_cancel(callback, state, user=None)
        callback.answer.assert_awaited_once_with()

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        state = FakeState()
        with patch("bot.handlers.panel.get_settings_text", new=AsyncMock(return_value="TEXT")), patch(
            "bot.handlers.panel.get_settings_keyboard", return_value="KB"
        ):
            await handle_settings_cancel(callback, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(state.cleared, 1)

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        await handle_settings_reset(callback, user=None)
        self.assertIn("دسترسی", callback.answer.await_args.args[0])

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        await handle_settings_reset(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.message.edit_text.assert_awaited_once()

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        with patch("core.trading_settings.TradingSettings", FakeTradingSettings), patch(
            "core.trading_settings.save_trading_settings_async", new=AsyncMock(return_value=True)
        ), patch("core.trading_settings.refresh_settings_cache_async", new=AsyncMock()), patch(
            "bot.handlers.panel.get_settings_text", new=AsyncMock(return_value="TEXT")
        ), patch("bot.handlers.panel.get_settings_keyboard", return_value="KB"):
            await handle_settings_reset_confirm(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertIn("بازنشانی شد", callback.answer.await_args.args[0])

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        with patch("bot.handlers.panel.get_settings_text", new=AsyncMock(return_value="TEXT")), patch(
            "bot.handlers.panel.get_settings_keyboard", return_value="KB"
        ):
            await handle_settings_reset_cancel(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("لغو شد")


if __name__ == "__main__":
    unittest.main()