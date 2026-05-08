import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_text_offer_confirm


def make_callback():
    return SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())


class BotTradeCreateTextOfferConfirmGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_text_offer_confirm_handles_missing_user_and_limit_guards(self):
        callback = make_callback()
        state = SimpleNamespace(get_data=AsyncMock(return_value={}), clear=AsyncMock())
        await handle_text_offer_confirm(callback, state, user=None, bot=SimpleNamespace())
        callback.answer.assert_awaited_once_with()

        user = SimpleNamespace(id=1, limitations_expire_at=datetime.utcnow() + timedelta(days=1))
        callback = make_callback()
        state = SimpleNamespace(get_data=AsyncMock(return_value={"quantity": 12}), clear=AsyncMock())
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "core.utils.check_user_limits", side_effect=[(False, "سقف پیام"), (True, None)]
        ), patch("core.utils.to_jalali_str", return_value="1405/02/18 - 12:00"):
            await handle_text_offer_confirm(callback, state, user=user, bot=SimpleNamespace())
        self.assertIn("سقف پیام", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()

        user = SimpleNamespace(id=1, limitations_expire_at=datetime.utcnow() + timedelta(days=1))
        callback = make_callback()
        state = SimpleNamespace(get_data=AsyncMock(return_value={"quantity": 12}), clear=AsyncMock())
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "core.utils.check_user_limits", side_effect=[(True, None), (False, "سقف معامله")]
        ), patch("core.utils.to_jalali_str", return_value="1405/02/18 - 12:00"):
            await handle_text_offer_confirm(callback, state, user=user, bot=SimpleNamespace())
        self.assertIn("سقف معامله", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()