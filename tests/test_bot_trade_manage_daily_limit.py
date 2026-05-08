import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_manage import handle_expire_offer


class FakeSession:
    async def scalar(self, stmt):
        return 9


class FakeSessionContext:
    async def __aenter__(self):
        return FakeSession()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_reply_markup=AsyncMock()))


class BotTradeManageDailyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_expire_offer_blocks_when_daily_limit_is_exhausted(self):
        callback = make_callback()
        user = SimpleNamespace(id=4)
        settings_obj = SimpleNamespace(offer_expire_rate_per_minute=5, offer_expire_daily_limit_after_threshold=3)

        with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
            "bot.handlers.trade_manage.track_expire_rate", new=AsyncMock(return_value=1)
        ), patch("bot.handlers.trade_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.trade_manage.track_daily_expire", new=AsyncMock(return_value={"count": 3})
        ):
            await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=user, bot=SimpleNamespace())

        self.assertIn("امروز 3 لفظ", callback.answer.await_args.args[0])
        self.assertIn("لفظ", callback.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()