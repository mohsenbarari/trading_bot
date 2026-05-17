import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import show_admin_panel_and_change_keyboard
from core.enums import UserRole


class BotPanelAdminMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_admin_panel_requires_admin_role_and_renders_menu(self):
        message = SimpleNamespace(bot=SimpleNamespace(), chat=SimpleNamespace(id=10), answer=AsyncMock())
        await show_admin_panel_and_change_keyboard(message, state=SimpleNamespace(), user=None)
        message.answer.assert_not_awaited()

        message = SimpleNamespace(bot=SimpleNamespace(), chat=SimpleNamespace(id=10), answer=AsyncMock(return_value=SimpleNamespace(message_id=66)))
        with patch("bot.handlers.panel.delete_previous_anchor", new=AsyncMock()) as delete_anchor, patch(
            "bot.handlers.panel.get_admin_panel_keyboard", return_value="KB"
        ), patch("bot.handlers.panel.set_anchor") as set_anchor:
            await show_admin_panel_and_change_keyboard(message, state=SimpleNamespace(), user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))

        delete_anchor.assert_awaited_once()
        self.assertIn("پنل مدیریت", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(10, 66)


if __name__ == "__main__":
    unittest.main()