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

        state = SimpleNamespace(clear=AsyncMock())
        with patch("bot.handlers.panel.show_support_contact", new=AsyncMock()) as support_handler:
            message = SimpleNamespace(text="☎️ پشتیبانی")
            result = await handoff_navigation_button(message, state, user)
        self.assertTrue(result)
        state.clear.assert_awaited_once_with()
        support_handler.assert_awaited_once_with(message, user)

        state = SimpleNamespace(clear=AsyncMock())
        with patch(
            "bot.handlers.admin_broadcast.start_telegram_admin_broadcast",
            new=AsyncMock(),
        ) as broadcast_handler:
            message = SimpleNamespace(text="📣 ارسال پیام همگانی بات")
            result = await handoff_navigation_button(message, state, user)
        self.assertTrue(result)
        state.clear.assert_awaited_once_with()
        broadcast_handler.assert_awaited_once_with(message, state, user)

    async def test_handoff_navigation_button_ignores_non_navigation_text(self):
        user = SimpleNamespace(role="super_admin")
        state = SimpleNamespace(clear=AsyncMock())

        result = await handoff_navigation_button(SimpleNamespace(text="123"), state, user)
        self.assertFalse(result)
        state.clear.assert_not_awaited()

    async def test_repeat_offer_button_escapes_stale_fsm_safely(self):
        user = SimpleNamespace(id=7, role="standard")
        state = SimpleNamespace(clear=AsyncMock())
        message = SimpleNamespace(
            text="🔁 خ ن سکه 10 عدد 100000",
            bot=SimpleNamespace(),
        )

        with patch(
            "bot.handlers.trade_create.handle_repeat_offer_button",
            new=AsyncMock(),
        ) as repeat_handler:
            result = await handoff_navigation_button(message, state, user)

        self.assertTrue(result)
        state.clear.assert_awaited_once_with()
        repeat_handler.assert_awaited_once_with(message, state, user, message.bot)


if __name__ == "__main__":
    unittest.main()
