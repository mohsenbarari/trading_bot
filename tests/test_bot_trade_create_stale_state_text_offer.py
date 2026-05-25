import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import (
    handle_lot_sizes_input,
    handle_manual_quantity,
    handle_notes_input,
    handle_price_input,
)


class BotTradeCreateStaleStateTextOfferTests(unittest.IsolatedAsyncioTestCase):
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
