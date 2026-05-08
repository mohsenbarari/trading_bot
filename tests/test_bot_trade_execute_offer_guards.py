import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import handle_channel_trade
from models.offer import OfferStatus


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


class BotTradeExecuteOfferGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_handles_missing_inactive_and_own_offer(self):
        user = SimpleNamespace(id=5, trading_restricted_until=None)
        common_patches = [
            patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)),
            patch("bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100)),
        ]

        for offer in [None, SimpleNamespace(status=OfferStatus.COMPLETED), SimpleNamespace(status=OfferStatus.ACTIVE, user_id=5)]:
            callback = make_callback()
            with common_patches[0], common_patches[1], patch(
                "bot.handlers.trade_execute.AsyncSessionLocal",
                return_value=FakeSessionContext(FakeSession(offer)),
            ):
                await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=SimpleNamespace())
            callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()