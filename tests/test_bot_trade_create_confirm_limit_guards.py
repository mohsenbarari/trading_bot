import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_trade_confirm
from bot.callbacks import TradeActionCallback


def make_callback():
    return SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())


class BotTradeCreateConfirmLimitGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.market_patcher = patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True))
        self.market_patcher.start()
        self.addCleanup(self.market_patcher.stop)

    async def test_handle_trade_confirm_handles_channel_message_and_trade_limits(self):
        user = SimpleNamespace(limitations_expire_at=datetime.utcnow() + timedelta(days=1), id=1)
        state = SimpleNamespace(get_data=AsyncMock(return_value={"quantity": 12}), clear=AsyncMock())

        callback = make_callback()
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.check_user_limits", side_effect=[(False, "سقف پیام"), (True, None)]
        ), patch("bot.handlers.trade_create.to_jalali_str", return_value="1405/02/18 - 12:00"):
            await handle_trade_confirm(callback, state, user=user, bot=SimpleNamespace())
        self.assertIn("سقف پیام", callback.message.edit_text.await_args.args[0])
        self.assertIn("1405/02/18 - 12:00", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()

        user = SimpleNamespace(limitations_expire_at=datetime.utcnow() + timedelta(days=1), id=1)
        state = SimpleNamespace(get_data=AsyncMock(return_value={"quantity": 12}), clear=AsyncMock())
        callback = make_callback()
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.check_user_limits", side_effect=[(True, None), (False, "سقف معامله")]
        ), patch("bot.handlers.trade_create.to_jalali_str", return_value="1405/02/18 - 12:00"):
            await handle_trade_confirm(callback, state, user=user, bot=SimpleNamespace())
        self.assertIn("سقف معامله", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()