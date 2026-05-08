import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import handle_channel_trade


def make_callback(chat_id=200):
    return SimpleNamespace(
        id="cb1",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=50, edit_reply_markup=AsyncMock()),
    )


class BotTradeExecuteBasicGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_handles_missing_user_restricted_user_and_limit_failures(self):
        callback = make_callback()
        await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=None, bot=SimpleNamespace())
        callback.answer.assert_awaited_once_with()

        callback = make_callback()
        restricted_user = SimpleNamespace(trading_restricted_until=datetime.utcnow() + timedelta(minutes=5))
        await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=restricted_user, bot=SimpleNamespace())
        self.assertIn("حساب شما مسدود است", callback.answer.await_args.args[0])
        self.assertEqual(callback.answer.await_args.kwargs, {"show_alert": True})

        callback = make_callback()
        user = SimpleNamespace(id=5, trading_restricted_until=None)
        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(False, "محدودیت")):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=SimpleNamespace())

        self.assertIn("محدودیت", callback.answer.await_args.args[0])
        self.assertEqual(callback.answer.await_args.kwargs, {"show_alert": True})


if __name__ == "__main__":
    unittest.main()