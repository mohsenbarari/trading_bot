import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_text_offer
from core.enums import UserRole


class BotTradeCreateTextOfferGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_text_offer_handles_watch_restriction_and_active_state_guard(self):
        message = SimpleNamespace(text="خ ربع 30تا 75800", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None))

        await handle_text_offer(message, state, user=None, bot=SimpleNamespace())
        message.answer.assert_not_awaited()

        watch_user = SimpleNamespace(role=UserRole.WATCH, trading_restricted_until=None)
        await handle_text_offer(message, state, user=watch_user, bot=SimpleNamespace())
        self.assertIn("دسترسی", message.answer.await_args_list[-1].args[0])

        restricted_user = SimpleNamespace(
            role=UserRole.STANDARD,
            trading_restricted_until=datetime.utcnow() + timedelta(days=1),
        )
        with patch("bot.handlers.trade_create.to_jalali_str", return_value="1405/02/18 - 12:00"):
            await handle_text_offer(message, state, user=restricted_user, bot=SimpleNamespace())
        self.assertIn("حساب شما مسدود است", message.answer.await_args_list[-1].args[0])

        state = SimpleNamespace(get_state=AsyncMock(return_value="busy"))
        with patch("bot.utils.offer_parser.parse_offer_text", new=AsyncMock()) as parse_mock:
            await handle_text_offer(
                SimpleNamespace(text="خ ربع 30تا 75800", answer=AsyncMock()),
                state,
                user=SimpleNamespace(role=UserRole.STANDARD, trading_restricted_until=None),
                bot=SimpleNamespace(),
            )
        parse_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()