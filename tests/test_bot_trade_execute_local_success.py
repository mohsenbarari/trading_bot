import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import handle_channel_trade
from models.offer import OfferStatus, OfferType
from models.trade import TradeStatus, TradeType


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, offer, scalar_values=None):
        self.offer = offer
        self.scalar_values = list(scalar_values or [10000])
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        return FakeExecuteResult(self.offer)

    async def refresh(self, offer, attrs):
        return None

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

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


def make_callback(chat_id=200):
    return SimpleNamespace(
        id="cb1",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=50, edit_reply_markup=AsyncMock()),
    )


def make_offer():
    return SimpleNamespace(
        id=7,
        status=OfferStatus.ACTIVE,
        user_id=9,
        offer_type=OfferType.BUY,
        quantity=5,
        remaining_quantity=5,
        is_wholesale=False,
        lot_sizes=[2, 3],
        home_server=None,
        channel_message_id=77,
        price=150000,
        commodity_id=12,
        commodity=SimpleNamespace(name="سکه"),
        user=SimpleNamespace(account_name="owner", mobile_number="0912", telegram_id=999),
    )


class BotTradeExecuteLocalSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_creates_trade_updates_offer_and_notifies_users(self):
        user = SimpleNamespace(id=5, telegram_id=555, mobile_number="0935", account_name="buyer", trading_restricted_until=None)
        offer = make_offer()
        session = FakeSession(offer)
        callback = make_callback(chat_id=200)
        bot = SimpleNamespace(send_message=AsyncMock())

        jdatetime_mod = ModuleType("jdatetime")
        jdatetime_mod.datetime = SimpleNamespace(
            fromgregorian=lambda datetime: SimpleNamespace(strftime=lambda fmt: "1405/02/18   12:00")
        )

        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(session)
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "bot.handlers.trade_execute.is_remote_home", return_value=False
        ), patch("bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(True, None, 2, [2, 3])), patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("core.utils.increment_user_counter", new=AsyncMock()) as increment_mock, patch(
            "bot.handlers.trade_execute.publish_event", new=AsyncMock()
        ) as publish_mock, patch("bot.handlers.trade_execute.create_user_notification", new=AsyncMock()) as notif_mock, patch(
            "bot.handlers.trade_execute.update_offer_channel_markup", new=AsyncMock()
        ) as update_markup_mock, patch("bot.handlers.trade_execute.remove_trade_suggestion_record", new=AsyncMock()) as remove_mock, patch(
            "bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ), patch.dict(sys.modules, {"jdatetime": jdatetime_mod}):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)

        self.assertGreaterEqual(session.commits, 1)
        self.assertEqual(len(session.added), 1)
        trade = session.added[0]
        self.assertEqual(trade.trade_type, TradeType.SELL)
        self.assertEqual(trade.status, TradeStatus.COMPLETED)
        self.assertEqual(offer.remaining_quantity, 3)
        self.assertEqual(offer.lot_sizes, [3])
        increment_mock.assert_awaited_once_with(session, user, "trade", 2)
        publish_mock.assert_awaited_once_with("offer:updated", {"id": 7, "remaining_quantity": 3, "lot_sizes": [3]})
        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(notif_mock.await_count, 2)
        update_markup_mock.assert_awaited_once_with(bot, offer)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        remove_mock.assert_awaited_once_with(7, 200, 50)
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()