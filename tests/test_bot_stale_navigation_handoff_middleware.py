import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, Message

from bot.middlewares.stale_navigation_handoff import StaleNavigationHandoffMiddleware


class BotStaleNavigationHandoffMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_fsm_navigation_is_handed_off_before_router_dispatch(self):
        middleware = StaleNavigationHandoffMiddleware()
        handler = AsyncMock(return_value="next")
        message = MagicMock(spec=Message)
        state = SimpleNamespace(get_state=AsyncMock(return_value="InvitationCreation:awaiting_account_name"))
        user = SimpleNamespace(id=1)

        with patch(
            "bot.handlers.panel.handoff_navigation_button",
            new=AsyncMock(return_value=True),
        ) as handoff:
            result = await middleware(handler, message, {"state": state, "user": user})

        self.assertIsNone(result)
        handoff.assert_awaited_once_with(message, state, user)
        handler.assert_not_awaited()

    async def test_idle_fsm_bypasses_navigation_handoff(self):
        middleware = StaleNavigationHandoffMiddleware()
        handler = AsyncMock(return_value="next")
        message = MagicMock(spec=Message)
        state = SimpleNamespace(get_state=AsyncMock(return_value=None))

        with patch("bot.handlers.panel.handoff_navigation_button", new=AsyncMock()) as handoff:
            result = await middleware(handler, message, {"state": state, "user": SimpleNamespace(id=1)})

        self.assertEqual(result, "next")
        handoff.assert_not_awaited()
        handler.assert_awaited_once()

    async def test_unknown_stale_fsm_text_continues_to_original_handler(self):
        middleware = StaleNavigationHandoffMiddleware()
        handler = AsyncMock(return_value="next")
        message = MagicMock(spec=Message)
        state = SimpleNamespace(get_state=AsyncMock(return_value="CustomerInvite:awaiting_management_name"))

        with patch(
            "bot.handlers.panel.handoff_navigation_button",
            new=AsyncMock(return_value=False),
        ) as handoff:
            result = await middleware(handler, message, {"state": state, "user": SimpleNamespace(id=1)})

        self.assertEqual(result, "next")
        handoff.assert_awaited_once()
        handler.assert_awaited_once()

    async def test_callback_query_bypasses_message_navigation_handoff(self):
        middleware = StaleNavigationHandoffMiddleware()
        handler = AsyncMock(return_value="next")
        callback = MagicMock(spec=CallbackQuery)
        state = SimpleNamespace(get_state=AsyncMock(return_value="InvitationCreation:awaiting_role"))

        result = await middleware(handler, callback, {"state": state, "user": SimpleNamespace(id=1)})

        self.assertEqual(result, "next")
        state.get_state.assert_not_awaited()
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
