import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_manage import handle_expire_offer


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_reply_markup=AsyncMock()))


class BotTradeManageRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_expire_offer_returns_silently_without_user(self):
        callback = make_callback()

        await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=None, bot=SimpleNamespace())

        callback.answer.assert_awaited_once_with()

    async def test_handle_expire_offer_blocks_when_minute_rate_limit_exceeded(self):
        callback = make_callback()
        user = SimpleNamespace(id=4)
        settings_obj = SimpleNamespace(offer_expire_rate_per_minute=2, offer_expire_daily_limit_after_threshold=99)

        with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
            "bot.handlers.trade_manage.track_expire_rate", new=AsyncMock(return_value=3)
        ):
            await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=user, bot=SimpleNamespace())

        self.assertIn("حداکثر 2", callback.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()