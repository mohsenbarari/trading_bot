import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import handle_channel_trade
from models.offer import OfferStatus, OfferType


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, offer):
        self.offer = offer

    async def execute(self, stmt):
        return FakeExecuteResult(self.offer)

    async def refresh(self, offer, attrs):
        return None


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback():
    return SimpleNamespace(
        id="cb1",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=200), message_id=50, edit_reply_markup=AsyncMock()),
    )


def make_offer():
    return SimpleNamespace(
        id=7,
        status=OfferStatus.ACTIVE,
        user_id=9,
        offer_type=OfferType.BUY,
        quantity=5,
        remaining_quantity=5,
        is_wholesale=True,
        lot_sizes=None,
        home_server=None,
    )


class BotTradeExecuteBlockedTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_reports_self_block_and_owner_block(self):
        user = SimpleNamespace(id=5, trading_restricted_until=None)
        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(make_offer())),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(True, 5))):
            callback = make_callback()
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=SimpleNamespace())
            self.assertIn("شما این کاربر را مسدود کرده", callback.answer.await_args.args[0])

        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(make_offer())),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(True, 9))):
            callback = make_callback()
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=SimpleNamespace())
            self.assertIn("در دسترس نیست", callback.answer.await_args.args[0])
            self.assertNotIn("مسدود", callback.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()