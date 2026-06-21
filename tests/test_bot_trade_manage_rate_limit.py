import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_manage import handle_expire_offer
from models.offer import OfferStatus


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_reply_markup=AsyncMock()))


class FakeSession:
    def __init__(self, offer=None):
        self.offer = offer

    async def scalar(self, stmt):
        return 1

    async def get(self, model, offer_id, *args, **kwargs):
        return self.offer


class FakeSessionFactory:
    def __init__(self, *sessions):
        self.sessions = list(sessions)

    def __call__(self):
        session = self.sessions.pop(0)

        class _Context:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Context()


class BotTradeManageRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_expire_offer_returns_silently_without_user(self):
        callback = make_callback()

        await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=None, bot=SimpleNamespace())

        callback.answer.assert_awaited_once_with()

    async def test_handle_expire_offer_blocks_when_minute_rate_limit_exceeded(self):
        callback = make_callback()
        user = SimpleNamespace(id=4)
        settings_obj = SimpleNamespace(offer_expire_rate_per_minute=2, offer_expire_daily_limit_after_threshold=99)
        offer = SimpleNamespace(id=5, user_id=4, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=None)
        factory = FakeSessionFactory(FakeSession(offer))

        with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
            "bot.utils.redis_helpers.track_expire_rate", new=AsyncMock(return_value=3)
        ), patch("bot.handlers.trade_manage.AsyncSessionLocal", new=factory), patch(
            "bot.handlers.trade_manage.current_server",
            return_value="foreign",
        ):
            await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=user, bot=SimpleNamespace())

        self.assertIn("حداکثر 2", callback.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
