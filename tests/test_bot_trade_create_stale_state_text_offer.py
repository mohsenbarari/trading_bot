import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import (
    handle_text_offer,
    handle_lot_sizes_input,
    handle_manual_quantity,
    handle_notes_input,
    handle_price_input,
)
from bot.handlers import trade_create
from bot.states import AdminBroadcast


class BotTradeCreateStaleStateTextOfferTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_offer_router_does_not_consume_broadcast_message_state(self):
        handler = next(
            item for item in trade_create.router.message.handlers
            if item.callback is handle_text_offer
        )
        message = SimpleNamespace(text="خرید امام 20 عدد نقد حاضر 176000")

        idle_match, _ = await handler.check(message, raw_state=None)
        broadcast_match, _ = await handler.check(
            message,
            raw_state=AdminBroadcast.awaiting_message_text,
        )

        self.assertTrue(idle_match)
        self.assertFalse(broadcast_match)

    async def test_navigation_button_handoffs_before_quantity_validation(self):
        user = SimpleNamespace(id=1)
        state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={"quantity": 30}))

        with patch("bot.handlers.panel.handoff_navigation_button", new=AsyncMock(return_value=True)) as nav_handoff:
            message = SimpleNamespace(text="👤 پنل کاربر", answer=AsyncMock())
            await handle_manual_quantity(message, state, user=user)

        nav_handoff.assert_awaited_once_with(message, state, user)
        message.answer.assert_not_awaited()

    async def test_offer_like_text_handoffs_from_stale_wizard_states(self):
        user = SimpleNamespace(id=1)

        state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={"quantity": 30}))
        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handoff_mock:
            message = SimpleNamespace(text="خ امام 30تا 75800", answer=AsyncMock())
            await handle_manual_quantity(message, state, user=user)
            state.clear.assert_awaited_once_with()
            handoff_mock.assert_awaited_once_with(message, state, user, None)
            message.answer.assert_not_awaited()

        state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={"quantity": 30}))
        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handoff_mock:
            message = SimpleNamespace(text="ف ربع 20تا 765000", answer=AsyncMock())
            await handle_lot_sizes_input(message, state, user=user)
            state.clear.assert_awaited_once_with()
            handoff_mock.assert_awaited_once_with(message, state, user, None)
            message.answer.assert_not_awaited()

        bot = SimpleNamespace(name="bot")
        state = SimpleNamespace(clear=AsyncMock())
        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handoff_mock:
            message = SimpleNamespace(text="خ نیم 12تا 123456", answer=AsyncMock())
            await handle_price_input(message, state, user=user, bot=bot)
            state.clear.assert_awaited_once_with()
            handoff_mock.assert_awaited_once_with(message, state, user, bot)
            message.answer.assert_not_awaited()

        state = SimpleNamespace(clear=AsyncMock())
        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handoff_mock:
            message = SimpleNamespace(text="ف سکه 15تا 123456", answer=AsyncMock())
            await handle_notes_input(message, state, user=user)
            state.clear.assert_awaited_once_with()
            handoff_mock.assert_awaited_once_with(message, state, user, None)
            message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
