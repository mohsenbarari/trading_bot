import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_manage import handle_expire_offer
from models.offer import OfferStatus


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


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_reply_markup=AsyncMock()))


class BotTradeManageOfferGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_expire_offer_handles_not_found_not_owner_and_inactive(self):
        settings_obj = SimpleNamespace(offer_expire_rate_per_minute=5, offer_expire_daily_limit_after_threshold=99)
        user = SimpleNamespace(id=4)

        for offer, expected in [
            (None, "لفظ یافت نشد"),
            (SimpleNamespace(user_id=9, status=OfferStatus.ACTIVE), "مالک این لفظ نیستید"),
            (SimpleNamespace(user_id=4, status=OfferStatus.COMPLETED), "دیگر فعال نیست"),
        ]:
            callback = make_callback()
            factory = FakeSessionFactory(FakeSession(offer))
            with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
                "bot.utils.redis_helpers.track_expire_rate", new=AsyncMock(return_value=1)
            ), patch("bot.utils.redis_helpers.track_daily_expire", new=AsyncMock(return_value={"count": 0})), patch(
                "bot.handlers.trade_manage.AsyncSessionLocal", new=factory
            ):
                await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=user, bot=SimpleNamespace())
            self.assertIn(expected, callback.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
