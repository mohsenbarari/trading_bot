import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import Trade, handle_trade_button
from core.enums import UserRole


class BotTradeCreateStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_trade_button_handles_watch_role_restriction_and_success(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(
            clear=AsyncMock(),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )

        await handle_trade_button(message, state, user=None)
        message.answer.assert_not_awaited()

        watch_user = SimpleNamespace(role=UserRole.WATCH, trading_restricted_until=None)
        await handle_trade_button(message, state, user=watch_user)
        self.assertIn("دسترسی", message.answer.await_args_list[-1].args[0])

        restricted_user = SimpleNamespace(
            role=UserRole.STANDARD,
            trading_restricted_until=datetime.utcnow() + timedelta(days=1, hours=2, minutes=3),
        )
        with patch("bot.handlers.trade_create.to_jalali_str", return_value="1405/02/18 - 12:00"):
            await handle_trade_button(message, state, user=restricted_user)
        restricted_text = message.answer.await_args_list[-1].args[0]
        self.assertIn("حساب شما مسدود است", restricted_text)
        self.assertIn("1405/02/18 - 12:00", restricted_text)

        closed_user = SimpleNamespace(role=UserRole.STANDARD, trading_restricted_until=None)
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=False)):
            await handle_trade_button(message, state, user=closed_user)
        self.assertEqual(message.answer.await_args_list[-1].args[0], "بعلت بسته بودن بازار درخواست شما ثبت نشد\nلطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید.")

        allowed_user = SimpleNamespace(role=UserRole.STANDARD, trading_restricted_until=None)
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)):
            await handle_trade_button(message, state, user=allowed_user)
        state.clear.assert_awaited_once()
        state.update_data.assert_awaited_once_with(wizard_return_to_review=False)
        state.set_state.assert_awaited_once_with(Trade.awaiting_trade_type)
        self.assertIn("نوع معامله را انتخاب کنید", message.answer.await_args_list[-1].args[0])
        button_texts = [
            button.text
            for row in message.answer.await_args_list[-1].kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("🟢 خرید", button_texts)
        self.assertIn("🔴 فروش", button_texts)


if __name__ == "__main__":
    unittest.main()
