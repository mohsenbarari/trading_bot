import unittest
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import LEGACY_RESPOND_PATH_DISABLED_MESSAGE, handle_confirm_trade
from core.enums import UserAccountStatus, UserRole


def make_callback(data="confirm_trade_5"):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )


def make_allowed_user(user_id=2):
    return SimpleNamespace(
        id=user_id,
        role=UserRole.STANDARD,
        account_status=UserAccountStatus.ACTIVE,
        is_deleted=False,
    )


class BotStartConfirmTradeTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_confirm_handler_does_not_create_trade_directly(self):
        source = inspect.getsource(handle_confirm_trade)

        self.assertNotIn("Trade(", source)
        self.assertNotIn("TradeStatus.COMPLETED", source)
        self.assertNotIn("session.commit", source)

    async def test_handle_confirm_trade_requires_registered_user(self):
        callback = make_callback()

        await handle_confirm_trade(callback, user=None)

        callback.answer.assert_awaited_once()
        self.assertIn("ابتدا ثبت", callback.answer.await_args.args[0])
        self.assertEqual(callback.answer.await_args.kwargs, {"show_alert": True})

    async def test_handle_confirm_trade_denies_incomplete_user_state_fail_closed(self):
        callback = make_callback()

        await handle_confirm_trade(callback, user=SimpleNamespace(id=9))

        callback.answer.assert_awaited_once()
        self.assertEqual(callback.answer.await_args.kwargs, {"show_alert": True})
        self.assertIn("هنوز در ربات قابل تایید نیست", callback.answer.await_args.args[0])
        callback.message.edit_text.assert_not_awaited()

    async def test_handle_confirm_trade_is_fail_closed_and_does_not_open_db(self):
        callback = make_callback()
        user = make_allowed_user()

        with patch("bot.handlers.start.AsyncSessionLocal") as session_factory:
            await handle_confirm_trade(callback, user=user)

        session_factory.assert_not_called()
        callback.message.edit_text.assert_awaited_once_with(LEGACY_RESPOND_PATH_DISABLED_MESSAGE)
        callback.answer.assert_awaited_once_with("این مسیر دیگر فعال نیست.", show_alert=True)


if __name__ == "__main__":
    unittest.main()
