import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import handle_admin_settings_button, handle_settings_edit_click
from core.enums import UserRole
from bot.states import TradingSettingsEdit


class FakeState:
    def __init__(self):
        self.cleared = 0
        self.updated = []
        self.states = []

    async def clear(self):
        self.cleared += 1

    async def update_data(self, **kwargs):
        self.updated.append(kwargs)

    async def set_state(self, value):
        self.states.append(value)


class BotPanelAdminSettingsEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_settings_entry_requires_admin_and_opens_edit_prompt(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = FakeState()
        await handle_admin_settings_button(message, state, user=None)
        message.answer.assert_not_awaited()

        with patch("bot.handlers.panel.get_settings_text", new=AsyncMock(return_value="TEXT")), patch(
            "bot.handlers.panel.get_settings_keyboard", return_value="KB"
        ):
            await handle_admin_settings_button(message, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(state.cleared, 1)
        message.answer.assert_awaited_once_with("TEXT", parse_mode="Markdown", reply_markup="KB")

        callback = SimpleNamespace(data="settings_edit_offer_expiry_minutes", answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        state = FakeState()
        ts = SimpleNamespace(offer_expiry_minutes=15)
        with patch("core.trading_settings.get_trading_settings_async", new=AsyncMock(return_value=ts)):
            await handle_settings_edit_click(callback, state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(state.updated[0], {"editing_setting": "offer_expiry_minutes"})
        self.assertEqual(state.states, [TradingSettingsEdit.awaiting_value])
        callback.message.edit_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()