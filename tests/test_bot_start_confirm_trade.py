import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_confirm_trade
from models.offer import OfferStatus, OfferType
from models.trade import TradeStatus, TradeType


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, value):
        self.value = value
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        return FakeExecuteResult(self.value)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback(data="confirm_trade_5"):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )


class BotStartConfirmTradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_confirm_trade_requires_registered_user(self):
        callback = make_callback()

        await handle_confirm_trade(callback, user=None)

        callback.answer.assert_awaited_once()
        self.assertIn("ابتدا ثبت", callback.answer.await_args.args[0])
        self.assertEqual(callback.answer.await_args.kwargs, {"show_alert": True})

    async def test_handle_confirm_trade_handles_missing_inactive_and_self_offer(self):
        callback = make_callback()
        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))):
            await handle_confirm_trade(callback, user=SimpleNamespace(id=9))
        self.assertIn("لفظ یافت نشد", callback.message.edit_text.await_args.args[0])

        inactive_offer = SimpleNamespace(status=OfferStatus.CANCELLED)
        callback = make_callback()
        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(inactive_offer))):
            await handle_confirm_trade(callback, user=SimpleNamespace(id=9))
        self.assertIn("دیگر فعال نیست", callback.message.edit_text.await_args.args[0])

        self_offer = SimpleNamespace(status=OfferStatus.ACTIVE, user_id=9)
        callback = make_callback()
        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(self_offer))):
            await handle_confirm_trade(callback, user=SimpleNamespace(id=9))
        self.assertIn("لفظ خودتان", callback.message.edit_text.await_args.args[0])

    async def test_handle_confirm_trade_creates_trade_and_completes_offer(self):
        offer = SimpleNamespace(
            id=5,
            status=OfferStatus.ACTIVE,
            offer_type=OfferType.BUY,
            user_id=1,
            commodity_id=12,
            quantity=4,
            price=450000,
            commodity=SimpleNamespace(name="سکه"),
            user=SimpleNamespace(account_name="owner"),
        )
        session = FakeSession(offer)
        callback = make_callback()
        user = SimpleNamespace(id=2, account_name="responder")

        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(session)):
            await handle_confirm_trade(callback, user=user)

        self.assertEqual(session.commits, 1)
        self.assertEqual(len(session.added), 1)
        trade = session.added[0]
        self.assertEqual(trade.offer_id, 5)
        self.assertEqual(trade.trade_type, TradeType.SELL)
        self.assertEqual(trade.status, TradeStatus.COMPLETED)
        self.assertEqual(offer.status, OfferStatus.COMPLETED)
        self.assertIn("معامله با موفقیت ثبت شد", callback.message.edit_text.await_args.args[0])
        callback.answer.assert_awaited_with("✅ معامله ثبت شد!")


if __name__ == "__main__":
    unittest.main()