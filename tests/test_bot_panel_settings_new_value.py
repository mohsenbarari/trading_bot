import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import handle_settings_new_value
from core.enums import UserRole


class FakeState:
    def __init__(self, data=None):
        self.data = data or {}
        self.cleared = 0

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared += 1


class FakeSettings:
    def __init__(self):
        self.values = {"offer_expiry_minutes": 15}

    def model_dump(self):
        return dict(self.values)


class BotPanelSettingsNewValueTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_settings_new_value_validates_and_saves(self):
        message = SimpleNamespace(text="5", answer=AsyncMock())
        state = FakeState()
        await handle_settings_new_value(message, state, user=None)
        message.answer.assert_not_awaited()

        message = SimpleNamespace(text="👤 پنل کاربر", answer=AsyncMock())
        state = FakeState({"editing_setting": "offer_expiry_minutes"})
        with patch("bot.handlers.panel.handoff_navigation_button", new=AsyncMock(return_value=True)) as handoff_mock:
            await handle_settings_new_value(message, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        handoff_mock.assert_awaited_once_with(message, state, unittest.mock.ANY)
        message.answer.assert_not_awaited()

        message = SimpleNamespace(text="x", answer=AsyncMock())
        state = FakeState({"editing_setting": "offer_expiry_minutes"})
        await handle_settings_new_value(message, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertIn("عدد صحیح مثبت", message.answer.await_args.args[0])

        message = SimpleNamespace(text="0", answer=AsyncMock())
        state = FakeState({"editing_setting": "offer_expiry_minutes"})
        await handle_settings_new_value(message, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertIn("عدد صحیح مثبت", message.answer.await_args.args[0])

        message = SimpleNamespace(text="5", answer=AsyncMock())
        state = FakeState({})
        await handle_settings_new_value(message, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(state.cleared, 1)

        message = SimpleNamespace(text="8", answer=AsyncMock())
        state = FakeState({"editing_setting": "offer_expiry_minutes"})
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "core.trading_settings.load_trading_settings_async", new=AsyncMock(return_value=FakeSettings())
        ), patch(
            "core.trading_settings.save_trading_settings_async", new=AsyncMock(return_value=True)
        ), patch("core.trading_settings.refresh_settings_cache_async", new=AsyncMock()), patch(
            "bot.handlers.panel.get_settings_text", new=AsyncMock(return_value="TEXT")
        ), patch("bot.handlers.panel.get_settings_keyboard", return_value="KB"):
            await handle_settings_new_value(message, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(state.cleared, 1)
        self.assertIn("تغییر کرد", message.answer.await_args_list[0].args[0])

        message = SimpleNamespace(text="8", answer=AsyncMock())
        state = FakeState({"editing_setting": "offer_expiry_minutes"})
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "core.trading_settings.load_trading_settings_async", new=AsyncMock(return_value=FakeSettings())
        ), patch(
            "core.trading_settings.save_trading_settings_async", new=AsyncMock(return_value=False)
        ), patch("bot.handlers.panel.get_settings_text", new=AsyncMock(return_value="TEXT")), patch(
            "bot.handlers.panel.get_settings_keyboard", return_value="KB"
        ):
            await handle_settings_new_value(message, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertIn("خطا در ذخیره", message.answer.await_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
