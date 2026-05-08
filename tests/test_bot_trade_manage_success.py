import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_manage import handle_expire_offer
from models.offer import OfferStatus


class FakeSession:
    def __init__(self, offer=None):
        self.offer = offer
        self.commits = 0

    async def scalar(self, stmt):
        return 1

    async def get(self, model, offer_id):
        return self.offer

    async def commit(self):
        self.commits += 1


class FakeSessionFactory:
    def __init__(self, first_session, second_session):
        self.sessions = [first_session, second_session]

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


class BotTradeManageSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_expire_offer_expires_offer_and_removes_buttons(self):
        offer = SimpleNamespace(user_id=4, status=OfferStatus.ACTIVE, channel_message_id=77)
        final_session = FakeSession(offer)
        factory = FakeSessionFactory(FakeSession(), final_session)
        callback = make_callback()
        bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
        settings_obj = SimpleNamespace(channel_id=-100, offer_expire_rate_per_minute=5, offer_expire_daily_limit_after_threshold=99)

        with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
            "bot.handlers.trade_manage.track_expire_rate", new=AsyncMock(return_value=1)
        ), patch("bot.handlers.trade_manage.track_daily_expire", new=AsyncMock(return_value={"count": 0})), patch(
            "bot.handlers.trade_manage.AsyncSessionLocal", new=factory
        ), patch("bot.handlers.trade_manage.settings", settings_obj):
            await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=SimpleNamespace(id=4), bot=bot)

        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertEqual(final_session.commits, 1)
        bot.edit_message_reply_markup.assert_awaited_once_with(chat_id=-100, message_id=77, reply_markup=None)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        callback.answer.assert_awaited_with("✅ لفظ شما منقضی شد")


if __name__ == "__main__":
    unittest.main()