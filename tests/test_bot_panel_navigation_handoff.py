import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import handoff_navigation_button


class BotPanelNavigationHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_handoff_navigation_button_routes_known_buttons(self):
        user = SimpleNamespace(role="super_admin")
        state = SimpleNamespace(clear=AsyncMock())

        with patch("bot.handlers.panel.show_my_profile_and_change_keyboard", new=AsyncMock()) as profile_handler:
            message = SimpleNamespace(text="👤 پنل کاربر")
            result = await handoff_navigation_button(message, state, user)
        self.assertTrue(result)
        state.clear.assert_awaited_once_with()
        profile_handler.assert_awaited_once_with(message, state, user)

        state = SimpleNamespace(clear=AsyncMock())
        with patch("bot.handlers.admin_commodities.handle_manage_commodities", new=AsyncMock()) as commodities_handler:
            message = SimpleNamespace(text="📦 مدیریت کالاها")
            result = await handoff_navigation_button(message, state, user)
        self.assertTrue(result)
        state.clear.assert_awaited_once_with()
        commodities_handler.assert_awaited_once_with(message, user, state)

        state = SimpleNamespace(clear=AsyncMock())
        with patch("bot.handlers.panel.show_colleagues_list", new=AsyncMock()) as colleagues_handler:
            message = SimpleNamespace(text="👥 لیست همکاران")
            result = await handoff_navigation_button(message, state, user)
        self.assertTrue(result)
        state.clear.assert_awaited_once_with()
        colleagues_handler.assert_awaited_once_with(message, state, user)

    async def test_handoff_navigation_button_ignores_non_navigation_text(self):
        user = SimpleNamespace(role="super_admin")
        state = SimpleNamespace(clear=AsyncMock())

        result = await handoff_navigation_button(SimpleNamespace(text="123"), state, user)
        self.assertFalse(result)
        state.clear.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
