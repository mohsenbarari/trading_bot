"""Hermetic characterization tests for channel trade live handler paths.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed IDs:
- BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-L810
- BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-PUBLIC-L829

This bounded characterization test module provides live-path evidence exercising
the exported handlers `handle_channel_trade` and `handle_channel_trade_public`
through real `_handle_channel_trade` and real `_execute_confirmed_channel_trade_via_shared_command`.
This bounded evidence does not reconcile recent-characterization.json, reduce canonical seed
counts by itself, prove final trade persistence/transient retry or origin SUCCESS/policy/notification/side-effect-retry cells,
close either seed/blocker, or mark P1-00b COMPLETE.
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Requirement 1: Install network fence before importing handler module.
install_p1_00_network_fence()

from bot.callbacks import ChannelTradeCallback, ChannelTradePublicCallback
from bot.handlers.trade_execute import (
    handle_channel_trade,
    handle_channel_trade_public,
)
from core.enums import UserAccountStatus, UserRole
from core.services.bot_access_policy import BotAccessDecision
from models.offer import Offer, OfferStatus, OfferType
from models.offer_request import OfferRequestSourceSurface
from models.user import User


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, offer):
        self.offer = offer
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.statements = []
        self.expunged = []
        self.refreshed = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        return FakeExecuteResult(self.offer)

    async def refresh(self, offer, attrs):
        self.refreshed.append((offer, list(attrs)))

    def expunge(self, value):
        self.expunged.append(value)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeSessionContext:
    def __init__(self, session: FakeSession):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback(chat_id: int = 200, message_id: int = 50, events: list[str] | None = None) -> SimpleNamespace:
    callback_answer = AsyncMock()
    message_edit_markup = AsyncMock()
    if events is not None:
        callback_answer.side_effect = lambda *args, **kwargs: events.append("answer")
        message_edit_markup.side_effect = lambda *args, **kwargs: events.append("markup")

    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        message_id=message_id,
        edit_reply_markup=message_edit_markup,
    )
    return SimpleNamespace(
        id="cb1",
        from_user=SimpleNamespace(id=300),
        answer=callback_answer,
        message=message,
    )


def make_user(user_id: int = 5, telegram_id: int = 555, account_name: str = "buyer_user") -> User:
    return User(
        id=user_id,
        telegram_id=telegram_id,
        mobile_number="09351234567",
        account_name=account_name,
        full_name="Standard Buyer",
        address="Tehran",
        role=UserRole.STANDARD,
        account_status=UserAccountStatus.ACTIVE,
        is_deleted=False,
        trading_restricted_until=None,
        home_server="foreign",
    )


def make_offer(
    offer_id: int = 7,
    offer_public_id: str = "ofr_bot_local_7",
    seller_user_id: int = 9,
) -> Offer:
    seller = User(
        id=seller_user_id,
        telegram_id=999,
        mobile_number="09121234567",
        account_name="seller_user",
        full_name="Standard Seller",
        address="Tehran",
        role=UserRole.STANDARD,
        account_status=UserAccountStatus.ACTIVE,
        is_deleted=False,
        trading_restricted_until=None,
        home_server="foreign",
    )
    offer = Offer(
        id=offer_id,
        offer_public_id=offer_public_id,
        status=OfferStatus.ACTIVE,
        user_id=seller_user_id,
        offer_type=OfferType.BUY,
        quantity=5,
        remaining_quantity=5,
        is_wholesale=False,
        lot_sizes=[2, 3],
        home_server="foreign",
        channel_message_id=77,
        price=150000,
        commodity_id=12,
    )
    offer.user = seller
    offer.commodity = SimpleNamespace(name="سکه")
    return offer


@contextmanager
def patch_channel_trade_boundaries(session: FakeSession, execute_side_effect):
    with patch(
        "bot.handlers.trade_execute.AsyncSessionLocal",
        return_value=FakeSessionContext(session),
    ), patch(
        "bot.handlers.trade_execute.check_user_limits",
        return_value=(True, None),
    ) as check_limits_mock, patch(
        "bot.handlers.trade_execute.evaluate_bot_access",
        new=AsyncMock(return_value=BotAccessDecision(allowed=True)),
    ) as bot_access_mock, patch(
        "bot.handlers.trade_execute.is_trade_blocked_by_principals",
        new=AsyncMock(return_value=(False, None, 0, 0)),
    ) as blocked_mock, patch(
        "bot.handlers.trade_execute.is_remote_home",
        return_value=False,
    ) as remote_mock, patch(
        "bot.handlers.trade_execute.validate_offer_trade_amount",
        return_value=(True, "", 2, [2, 3]),
    ) as validate_mock, patch(
        "bot.handlers.trade_execute.current_server",
        return_value="foreign",
    ) as current_server_mock, patch(
        "bot.handlers.trade_execute.settings",
        SimpleNamespace(channel_id=-100, bot_username="botname"),
    ), patch(
        "bot.handlers.trade_execute._execute_trade_authoritatively_with_transient_retry",
        new=AsyncMock(side_effect=execute_side_effect),
    ) as execute_mock, patch(
        "bot.handlers.trade_execute.remove_trade_suggestion_record",
        new=AsyncMock(),
    ) as remove_mock, patch(
        "bot.handlers.trade_execute.check_double_click",
        new=AsyncMock(),
    ) as double_click_mock, patch(
        "bot.handlers.trade_execute.forward_trade_to_home_server",
        new=AsyncMock(),
    ) as forward_mock, patch(
        "bot.handlers.trade_execute._queue_authoritative_channel_offer_refresh",
        new=AsyncMock(),
    ) as queue_refresh_mock, patch(
        "bot.handlers.trade_execute.build_lot_unavailable_suggestion_payload",
        return_value={},
    ) as build_lot_mock, patch(
        "bot.handlers.trade_execute.send_or_update_trade_suggestion_message",
        new=AsyncMock(),
    ) as send_suggestion_mock, patch(
        "bot.handlers.trade_execute.build_trade_amount_buttons",
        return_value=None,
    ) as build_buttons_mock, patch(
        "bot.handlers.trade_execute.upsert_trade_suggestion_record",
        new=AsyncMock(),
    ) as upsert_record_mock, patch(
        "bot.handlers.trade_execute.schedule_trade_suggestion_cleanup",
        new=AsyncMock(),
    ) as schedule_cleanup_mock, patch(
        "bot.handlers.trade_execute.schedule_trade_suggestion_pending_reset",
        new=AsyncMock(),
    ) as schedule_reset_mock:
        yield SimpleNamespace(
            check_limits_mock=check_limits_mock,
            bot_access_mock=bot_access_mock,
            blocked_mock=blocked_mock,
            remote_mock=remote_mock,
            validate_mock=validate_mock,
            current_server_mock=current_server_mock,
            execute_mock=execute_mock,
            remove_mock=remove_mock,
            double_click_mock=double_click_mock,
            forward_mock=forward_mock,
            queue_refresh_mock=queue_refresh_mock,
            build_lot_mock=build_lot_mock,
            send_suggestion_mock=send_suggestion_mock,
            build_buttons_mock=build_buttons_mock,
            upsert_record_mock=upsert_record_mock,
            schedule_cleanup_mock=schedule_cleanup_mock,
            schedule_reset_mock=schedule_reset_mock,
        )


class TestP100bChannelTradeLiveHandlerPaths(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_legacy_id_local_success(self):
        user = make_user()
        offer = make_offer()
        session = FakeSession(offer)
        events: list[str] = []
        callback = make_callback(chat_id=200, message_id=50, events=events)
        bot = SimpleNamespace(send_message=AsyncMock())
        callback_data = ChannelTradeCallback(offer_id=7, amount=2)

        def record_background_task():
            events.append("background_task")

        async def execute_trade_side_effect(*args, **kwargs):
            kwargs["background_tasks"].add_task(record_background_task)
            return {"trade_id": 88, "status": "committed"}

        with patch_channel_trade_boundaries(session, execute_trade_side_effect) as mocks:
            await handle_channel_trade(
                callback,
                callback_data,
                user=user,
                bot=bot,
                trade_contention_preconfirmed=True,
                trade_contention_pre_gated=True,
            )

        # 1. SQL statement assertions: lookup by Offer.id without FOR UPDATE
        self.assertGreaterEqual(len(session.statements), 1)
        self.assertIn("WHERE offers.id =", session.statements[0])
        self.assertNotIn("WHERE offers.offer_public_id =", session.statements[0])
        for statement in session.statements:
            self.assertNotIn("FOR UPDATE", statement.upper())

        # 2. Refresh of user and commodity
        self.assertEqual(session.refreshed, [(offer, ["user", "commodity"])])

        # 3. Traversal into authoritative executor with exact TradeCreate and arguments
        mocks.execute_mock.assert_awaited_once()
        exec_kwargs = mocks.execute_mock.await_args.kwargs
        trade_data = exec_kwargs["trade_data"]
        self.assertEqual(trade_data.offer_id, 7)
        self.assertEqual(trade_data.offer_public_id, "ofr_bot_local_7")
        self.assertEqual(trade_data.quantity, 2)
        self.assertEqual(trade_data.idempotency_key, "telegram_callback:5:ofr_bot_local_7:2:remaining:5:50")

        context = exec_kwargs["context"]
        self.assertIs(context.owner_user, user)
        self.assertIs(context.actor_user, user)
        self.assertIsNone(context.relation)
        self.assertFalse(context.is_accountant_context)

        self.assertEqual(exec_kwargs["request_source_surface"], OfferRequestSourceSurface.TELEGRAM_BOT)
        self.assertEqual(exec_kwargs["request_source_server"], "foreign")
        self.assertTrue(exec_kwargs["request_pre_gated"])
        self.assertIsNotNone(exec_kwargs["edge_received_at"])
        self.assertIsInstance(exec_kwargs["edge_received_at"], datetime)
        self.assertIs(exec_kwargs["db"], session)

        # 4. Session expunge
        self.assertEqual(session.expunged, [offer])

        # 5. Telegram callback acknowledgement, markup cleanup, suggestion record removal, background task
        callback.answer.assert_awaited_once_with("معامله ثبت شد ✅", show_alert=False)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        mocks.remove_mock.assert_awaited_once_with(7, 200, 50)
        self.assertEqual(events, ["answer", "markup", "background_task"])

        # 6. Independence checks: avoided helpers and commit boundaries
        mocks.double_click_mock.assert_not_awaited()
        mocks.forward_mock.assert_not_awaited()
        mocks.queue_refresh_mock.assert_not_awaited()
        mocks.build_lot_mock.assert_not_called()
        mocks.send_suggestion_mock.assert_not_awaited()
        mocks.build_buttons_mock.assert_not_called()
        mocks.upsert_record_mock.assert_not_awaited()
        mocks.schedule_cleanup_mock.assert_not_awaited()
        mocks.schedule_reset_mock.assert_not_awaited()
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.added, [])
        self.assertEqual(session.rollbacks, 0)

    async def test_handle_channel_trade_public_identity_local_success(self):
        user = make_user()
        offer = make_offer()
        session = FakeSession(offer)
        events: list[str] = []
        callback = make_callback(chat_id=200, message_id=50, events=events)
        bot = SimpleNamespace(send_message=AsyncMock())
        callback_data = ChannelTradePublicCallback(offer_public_id="ofr_bot_local_7", amount=2)

        def record_background_task():
            events.append("background_task")

        async def execute_trade_side_effect(*args, **kwargs):
            kwargs["background_tasks"].add_task(record_background_task)
            return {"trade_id": 88, "status": "committed"}

        with patch_channel_trade_boundaries(session, execute_trade_side_effect) as mocks:
            await handle_channel_trade_public(
                callback,
                callback_data,
                user=user,
                bot=bot,
                trade_contention_preconfirmed=True,
                trade_contention_pre_gated=False,
            )

        # 1. SQL statement assertions: lookup by Offer.offer_public_id without FOR UPDATE
        self.assertGreaterEqual(len(session.statements), 1)
        self.assertIn("WHERE offers.offer_public_id =", session.statements[0])
        self.assertNotIn("WHERE offers.id =", session.statements[0])
        for statement in session.statements:
            self.assertNotIn("FOR UPDATE", statement.upper())

        # 2. Refresh of user and commodity
        self.assertEqual(session.refreshed, [(offer, ["user", "commodity"])])

        # 3. Traversal into authoritative executor with exact TradeCreate and arguments
        mocks.execute_mock.assert_awaited_once()
        exec_kwargs = mocks.execute_mock.await_args.kwargs
        trade_data = exec_kwargs["trade_data"]
        self.assertEqual(trade_data.offer_id, 7)
        self.assertEqual(trade_data.offer_public_id, "ofr_bot_local_7")
        self.assertEqual(trade_data.quantity, 2)
        self.assertEqual(trade_data.idempotency_key, "telegram_callback:5:ofr_bot_local_7:2:remaining:5:50")

        context = exec_kwargs["context"]
        self.assertIs(context.owner_user, user)
        self.assertIs(context.actor_user, user)
        self.assertIsNone(context.relation)
        self.assertFalse(context.is_accountant_context)

        self.assertEqual(exec_kwargs["request_source_surface"], OfferRequestSourceSurface.TELEGRAM_BOT)
        self.assertEqual(exec_kwargs["request_source_server"], "foreign")
        self.assertFalse(exec_kwargs["request_pre_gated"])
        self.assertIsNotNone(exec_kwargs["edge_received_at"])
        self.assertIsInstance(exec_kwargs["edge_received_at"], datetime)
        self.assertIs(exec_kwargs["db"], session)

        # 4. Session expunge
        self.assertEqual(session.expunged, [offer])

        # 5. Telegram callback acknowledgement, markup cleanup, suggestion record removal, background task
        callback.answer.assert_awaited_once_with("معامله ثبت شد ✅", show_alert=False)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        mocks.remove_mock.assert_awaited_once_with(7, 200, 50)
        self.assertEqual(events, ["answer", "markup", "background_task"])

        # 6. Independence checks: avoided helpers and commit boundaries
        mocks.double_click_mock.assert_not_awaited()
        mocks.forward_mock.assert_not_awaited()
        mocks.queue_refresh_mock.assert_not_awaited()
        mocks.build_lot_mock.assert_not_called()
        mocks.send_suggestion_mock.assert_not_awaited()
        mocks.build_buttons_mock.assert_not_called()
        mocks.upsert_record_mock.assert_not_awaited()
        mocks.schedule_cleanup_mock.assert_not_awaited()
        mocks.schedule_reset_mock.assert_not_awaited()
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.added, [])
        self.assertEqual(session.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
