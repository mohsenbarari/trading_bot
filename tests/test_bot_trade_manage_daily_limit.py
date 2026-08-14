import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_manage import handle_expire_offer
from models.offer import OfferStatus


class FakeSession:
    def __init__(self, offer=None):
        self.offer = offer

    async def scalar(self, stmt):
        return 9

    async def get(self, model, offer_id, *args, **kwargs):
        return self.offer


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_reply_markup=AsyncMock()))


class BotTradeManageDailyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_expire_offer_blocks_when_daily_limit_is_exhausted(self):
        callback = make_callback()
        user = SimpleNamespace(id=4)
        settings_obj = SimpleNamespace(offer_expire_rate_per_minute=5, offer_expire_daily_limit_after_threshold=3)
        offer = SimpleNamespace(id=5, user_id=4, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=None)
        lease = SimpleNamespace(acquired=True, release=AsyncMock())

        with patch("bot.handlers.trade_manage.get_trading_settings", return_value=settings_obj), patch(
            "bot.handlers.trade_manage.try_acquire_offer_expiry_gate",
            new=AsyncMock(return_value=lease),
        ), patch(
            "bot.utils.redis_helpers.track_expire_rate", new=AsyncMock(return_value=1)
        ), patch("bot.handlers.trade_manage.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(offer))), patch(
            "bot.utils.redis_helpers.track_daily_expire", new=AsyncMock(return_value={"count": 3})
        ), patch(
            "bot.handlers.trade_manage.current_server",
            return_value="foreign",
        ):
            await handle_expire_offer(callback, SimpleNamespace(offer_id=5), user=user, bot=SimpleNamespace())

        self.assertIn("امروز 3 لفظ", callback.answer.await_args.args[0])
        self.assertIn("لفظ", callback.answer.await_args.args[0])
        lease.release.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
