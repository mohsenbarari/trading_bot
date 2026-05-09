import sys
import unittest
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError

from bot.handlers.trade_execute import handle_channel_trade
from models.offer import OfferStatus, OfferType


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class RetrySession:
    def __init__(self, offer, scalar_values=None, commit_side_effects=None):
        self.offer = offer
        self.scalar_values = list(scalar_values or [10000])
        self.commit_side_effects = list(commit_side_effects or [None])
        self.added = []
        self.commits = 0
        self.rollbacks = 0

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
        next_effect = self.commit_side_effects.pop(0) if self.commit_side_effects else None
        if next_effect is not None:
            raise next_effect

    async def rollback(self):
        self.rollbacks += 1


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback(chat_id=200, edit_side_effect=None):
    return SimpleNamespace(
        id="cb1",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_id=50,
            edit_reply_markup=AsyncMock(side_effect=edit_side_effect),
        ),
    )


def make_offer(quantity=5, remaining_quantity=5, lot_sizes=None):
    return SimpleNamespace(
        id=7,
        status=OfferStatus.ACTIVE,
        user_id=9,
        offer_type=OfferType.BUY,
        quantity=quantity,
        remaining_quantity=remaining_quantity,
        is_wholesale=False,
        lot_sizes=list(lot_sizes if lot_sizes is not None else [2, 3]),
        home_server=None,
        channel_message_id=77,
        price=150000,
        commodity_id=12,
        commodity=SimpleNamespace(name="سکه"),
        user=SimpleNamespace(account_name="owner", mobile_number="0912", telegram_id=999),
    )


class OfferWithFailingNotificationOwner:
    def __init__(self):
        self.id = 7
        self.status = OfferStatus.ACTIVE
        self.offer_type = OfferType.BUY
        self.quantity = 5
        self.remaining_quantity = 5
        self.is_wholesale = False
        self.lot_sizes = [5]
        self.home_server = None
        self.channel_message_id = 77
        self.price = 150000
        self.commodity_id = 12
        self.commodity = SimpleNamespace(name="سکه")
        self.user = SimpleNamespace(account_name="owner", mobile_number="0912", telegram_id=999)
        self._user_id_accesses = 0

    @property
    def user_id(self):
        self._user_id_accesses += 1
        if self._user_id_accesses >= 5:
            raise RuntimeError("offer owner id unavailable")
        return 9


def make_jdatetime_module():
    module = ModuleType("jdatetime")
    module.datetime = SimpleNamespace(
        fromgregorian=lambda datetime: SimpleNamespace(strftime=lambda fmt: "1405/02/18   12:00")
    )
    return module


class BotTradeExecuteRemainingPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_remote_home_tolerates_pending_and_clear_exceptions(self):
        user = SimpleNamespace(id=5, telegram_id=555, trading_restricted_until=None)
        bot = SimpleNamespace()
        offer = make_offer()
        base_patches = [
            patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)),
            patch("bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(RetrySession(offer))),
            patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))),
            patch("bot.handlers.trade_execute.is_remote_home", return_value=True),
            patch("bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100)),
        ]

        callback = make_callback(edit_side_effect=RuntimeError("pending boom"))
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], base_patches[4], patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=False)
        ), patch("bot.handlers.trade_execute.get_available_trade_amounts", return_value=[2, 3]), patch(
            "bot.handlers.trade_execute.build_trade_amount_buttons", return_value="KB"
        ):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        callback.answer.assert_awaited_with("برای تایید دوباره روی همان دکمه بزنید ☑️", show_alert=False)

        callback = make_callback(edit_side_effect=RuntimeError("clear boom"))
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], base_patches[4], patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("bot.handlers.trade_execute.forward_trade_to_home_server", new=AsyncMock(return_value=(200, {"ok": True}))), patch(
            "bot.handlers.trade_execute.remove_trade_suggestion_record", new=AsyncMock()
        ), patch("bot.handlers.trade_execute.current_server", return_value="foreign"):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        callback.answer.assert_awaited_with("معامله ثبت شد ✅", show_alert=False)

    async def test_handle_channel_trade_local_completed_trade_retries_and_tolerates_side_effect_failures(self):
        user = SimpleNamespace(id=5, telegram_id=555, mobile_number="0935", account_name="buyer", trading_restricted_until=None)
        offer = make_offer(quantity=5, remaining_quantity=5, lot_sizes=[5])
        session = RetrySession(
            offer,
            scalar_values=[10000, 10001],
            commit_side_effects=[IntegrityError("stmt", {}, Exception("trade_number conflict")), None],
        )
        callback = make_callback(edit_side_effect=RuntimeError("clear private"))
        bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=[RuntimeError("responder fail"), RuntimeError("owner fail")])
        )
        jdatetime_mod = make_jdatetime_module()

        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(session)
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "bot.handlers.trade_execute.is_remote_home", return_value=False
        ), patch("bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(True, None, 5, [5])), patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("core.utils.increment_user_counter", new=AsyncMock()) as increment_mock, patch(
            "bot.handlers.trade_execute.publish_event", new=AsyncMock()
        ) as publish_mock, patch(
            "bot.handlers.trade_execute.create_user_notification",
            new=AsyncMock(side_effect=[RuntimeError("notif 1"), RuntimeError("notif 2")]),
        ) as notif_mock, patch(
            "bot.handlers.trade_execute.update_offer_channel_markup", new=AsyncMock(side_effect=RuntimeError("update fail"))
        ), patch(
            "bot.handlers.trade_execute.remove_trade_suggestion_record", new=AsyncMock()
        ) as remove_mock, patch(
            "bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ), patch.dict(sys.modules, {"jdatetime": jdatetime_mod}):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=5), user=user, bot=bot)

        self.assertEqual(session.rollbacks, 1)
        self.assertEqual(session.commits, 2)
        self.assertEqual(session.added[0].trade_number, 10002)
        self.assertEqual(offer.remaining_quantity, 0)
        self.assertEqual(offer.status, OfferStatus.COMPLETED)
        self.assertIsNone(offer.lot_sizes)
        increment_mock.assert_awaited_once_with(session, user, "trade", 5)
        publish_mock.assert_awaited_once_with("offer:completed", {"id": 7})
        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(notif_mock.await_count, 2)
        remove_mock.assert_not_awaited()
        callback.answer.assert_awaited_once_with()

    async def test_handle_channel_trade_local_pending_tolerates_suggestion_state_failure(self):
        user = SimpleNamespace(id=5, telegram_id=555, trading_restricted_until=None)
        callback = make_callback(edit_side_effect=RuntimeError("pending state fail"))

        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(RetrySession(make_offer()))
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "bot.handlers.trade_execute.is_remote_home", return_value=False
        ), patch("bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(True, None, 2, [2, 3])), patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=False)
        ), patch("bot.handlers.trade_execute.build_trade_amount_buttons", return_value="KB"), patch(
            "bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100)
        ):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=SimpleNamespace())

        callback.answer.assert_awaited_with("برای تایید دوباره روی همان دکمه بزنید ☑️", show_alert=False)

    async def test_handle_channel_trade_local_notification_outer_try_is_tolerated(self):
        user = SimpleNamespace(id=5, telegram_id=555, mobile_number="0935", account_name="buyer", trading_restricted_until=None)
        offer = OfferWithFailingNotificationOwner()
        session = RetrySession(offer, scalar_values=[10000], commit_side_effects=[None])
        callback = make_callback(chat_id=-100)
        bot = SimpleNamespace(send_message=AsyncMock())
        jdatetime_mod = make_jdatetime_module()

        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(session)
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "bot.handlers.trade_execute.is_remote_home", return_value=False
        ), patch("bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(True, None, 5, [5])), patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("core.utils.increment_user_counter", new=AsyncMock()), patch(
            "bot.handlers.trade_execute.publish_event", new=AsyncMock()
        ), patch("bot.handlers.trade_execute.create_user_notification", new=AsyncMock()) as notif_mock, patch(
            "bot.handlers.trade_execute.update_offer_channel_markup", new=AsyncMock()
        ), patch(
            "bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ), patch.dict(sys.modules, {"jdatetime": jdatetime_mod}):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=5), user=user, bot=bot)

        self.assertEqual(notif_mock.await_count, 1)
        callback.answer.assert_awaited_once_with()

    async def test_handle_channel_trade_raises_non_retryable_integrity_error(self):
        user = SimpleNamespace(id=5, telegram_id=555, mobile_number="0935", account_name="buyer", trading_restricted_until=None)
        offer = make_offer(quantity=5, remaining_quantity=5, lot_sizes=[5])
        error = IntegrityError("stmt", {}, Exception("other constraint"))
        session = RetrySession(offer, scalar_values=[10000], commit_side_effects=[error])
        callback = make_callback(chat_id=-100)
        jdatetime_mod = make_jdatetime_module()

        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(session)
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "bot.handlers.trade_execute.is_remote_home", return_value=False
        ), patch("bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(True, None, 5, [5])), patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch(
            "bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ), patch.dict(sys.modules, {"jdatetime": jdatetime_mod}):
            with self.assertRaises(IntegrityError):
                await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=5), user=user, bot=SimpleNamespace(send_message=AsyncMock()))


if __name__ == "__main__":
    unittest.main()