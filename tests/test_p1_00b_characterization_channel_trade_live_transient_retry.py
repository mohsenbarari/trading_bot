"""Hermetic characterization tests for channel trade live transient retry path.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN

Included scenarios:
1. One transient deadlock attempt followed by success at authoritative execution boundary.
2. One non-transient failure (ValueError) without retry or success side effects.
3. Retryable exhaustion across three attempts stopping before success side effects.

Evidenced exclusions:
This bounded characterization test module does not claim durable Trade or OfferRequest
persistence, parser/service correctness, origin policy/persona/tier coverage,
notification audience correctness, a retry of a post-commit side effect,
public-handler equivalence, other origin cells, seed/blocker closure,
index reconciliation, or Stage closure.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Requirement 1: Install network fence before importing application handler modules.
install_p1_00_network_fence()

import api.routers.trades
from api.deps import EffectiveOwnerActor
from api.routers.trades import (
    TRADE_TRANSIENT_RETRY_ATTEMPTS,
    TRADE_TRANSIENT_RETRY_BASE_DELAY_SECONDS,
    TradeCreate,
    _execute_trade_authoritatively_with_transient_retry,
)
from bot.callbacks import ChannelTradeCallback
from bot.handlers.trade_execute import handle_channel_trade
from core.enums import UserRole
from fastapi import BackgroundTasks
from models.offer_request import OfferRequestSourceSurface
from models.user import User
from sqlalchemy.exc import OperationalError
from tests.test_p1_00b_characterization_channel_trade_live_handler_paths import (
    FakeSession,
    make_callback,
    make_offer,
    make_user,
    patch_channel_trade_boundaries,
)


class FakeDBAPIDeadlockError(Exception):
    def __init__(self, message: str = "deadlock detected", pgcode: str = "40P01"):
        super().__init__(message)
        self.pgcode = pgcode
        self.sqlstate = pgcode
        self.code = pgcode


def make_retryable_operational_error(message: str = "deadlock detected") -> OperationalError:
    return OperationalError(
        statement="UPDATE offers SET remaining_quantity = remaining_quantity - 2 WHERE id = 7",
        params={},
        orig=FakeDBAPIDeadlockError(message, "40P01"),
    )


class TransientRetryTrackingSession(FakeSession):
    def __init__(self, offer, reloaded_user: User | None = None):
        super().__init__(offer)
        self.expunge_all_calls = 0
        self.reloaded_user = reloaded_user
        self.get_calls: list[tuple[type, int]] = []

    def expunge_all(self):
        self.expunge_all_calls += 1

    async def get(self, entity_cls, ident):
        self.get_calls.append((entity_cls, ident))
        if entity_cls is User:
            return self.reloaded_user
        return None


def extract_log_payload(call_entry: tuple[tuple, dict]) -> dict:
    args, kwargs = call_entry
    data = dict(kwargs)
    if "extra" in data and isinstance(data["extra"], dict):
        data.update(data["extra"])
    pos_args = list(args)
    if pos_args and (
        isinstance(pos_args[0], logging.Logger)
        or hasattr(pos_args[0], "isEnabledFor")
        or hasattr(pos_args[0], "warning")
        or not isinstance(pos_args[0], str)
    ):
        pos_args.pop(0)
    positional_names = ["event", "action", "result", "offer_id", "error_class", "has_idempotency_key"]
    for idx, val in enumerate(pos_args):
        if idx < len(positional_names) and positional_names[idx] not in data:
            data[positional_names[idx]] = val
        elif idx == 0 and "event" not in data and isinstance(val, str):
            data["event"] = val
    return data


@contextmanager
def patch_hermetic_sleep_and_logging():
    log_calls: list[tuple[tuple, dict]] = []
    mock_sleep = AsyncMock()

    def record_log(*args, **kwargs):
        log_calls.append((args, kwargs))

    real_sleep = asyncio.sleep

    async def selective_sleep(delay, *args, **kwargs):
        frame = inspect.currentframe()
        try:
            caller = frame.f_back
            caller_module = caller.f_globals.get("__name__", "") if caller else ""
            if "trades" in caller_module:
                return await mock_sleep(delay, *args, **kwargs)
            if delay > 0:
                return await real_sleep(0)
            return await real_sleep(delay, *args, **kwargs)
        finally:
            del frame

    patches = [
        patch("asyncio.sleep", new=selective_sleep),
    ]
    if hasattr(api.routers.trades, "sleep"):
        patches.append(patch.object(api.routers.trades, "sleep", new=mock_sleep))

    if hasattr(api.routers.trades, "log_trading_event"):
        patches.append(patch.object(api.routers.trades, "log_trading_event", side_effect=record_log))
    if hasattr(api.routers.trades, "logger") and hasattr(api.routers.trades.logger, "warning"):
        patches.append(patch.object(api.routers.trades.logger, "warning", side_effect=record_log))

    try:
        import core.trading_observability
        patches.append(patch.object(core.trading_observability, "log_trading_event", side_effect=record_log))
    except Exception:
        pass

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield mock_sleep, log_calls


@contextmanager
def patch_live_transient_retry_boundaries(
    session: TransientRetryTrackingSession,
    authoritative_mock: AsyncMock,
):
    with patch_channel_trade_boundaries(session, None) as mocks:
        with patch(
            "bot.handlers.trade_execute._execute_trade_authoritatively_with_transient_retry",
            new=_execute_trade_authoritatively_with_transient_retry,
        ), patch(
            "api.routers.trades._execute_trade_authoritatively",
            new=authoritative_mock,
        ):
            yield mocks


class TestP100bChannelTradeLiveTransientRetry(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_handler_deadlock_retry_succeeds_once(self):
        user = make_user(user_id=5, telegram_id=555, account_name="buyer_user")
        reloaded_user = make_user(user_id=5, telegram_id=555, account_name="buyer_user_reloaded")
        offer = make_offer(offer_id=7, offer_public_id="ofr_bot_local_7")
        session = TransientRetryTrackingSession(offer, reloaded_user=reloaded_user)
        events: list[str] = []
        callback = make_callback(chat_id=200, message_id=50, events=events)
        bot = SimpleNamespace(send_message=AsyncMock())
        callback_data = ChannelTradeCallback(offer_id=7, amount=2)
        deadlock_error = make_retryable_operational_error("deadlock detected on attempt 1")

        def record_background_task():
            events.append("background_task")

        async def authoritative_side_effect(*args, **kwargs):
            if authoritative_mock.await_count == 1:
                raise deadlock_error
            kwargs["background_tasks"].add_task(record_background_task)
            return {"trade_id": 88, "status": "committed"}

        authoritative_mock = AsyncMock(side_effect=authoritative_side_effect)

        with patch_hermetic_sleep_and_logging() as (mock_sleep, log_calls):
            with patch_live_transient_retry_boundaries(session, authoritative_mock) as mocks:
                await handle_channel_trade(
                    callback,
                    callback_data,
                    user=user,
                    bot=bot,
                    trade_contention_preconfirmed=True,
                    trade_contention_pre_gated=True,
                )

        # 1. Authoritative execution boundary called exactly twice
        self.assertEqual(authoritative_mock.await_count, 2)
        call_1 = authoritative_mock.call_args_list[0]
        call_2 = authoritative_mock.call_args_list[1]

        # 2. Same TradeCreate object and stable idempotency across attempts
        trade_data_1: TradeCreate = call_1.kwargs["trade_data"]
        trade_data_2: TradeCreate = call_2.kwargs["trade_data"]
        self.assertIs(trade_data_1, trade_data_2)
        self.assertEqual(trade_data_1.offer_id, 7)
        self.assertEqual(trade_data_1.offer_public_id, "ofr_bot_local_7")
        self.assertEqual(trade_data_1.quantity, 2)
        self.assertEqual(trade_data_1.idempotency_key, "telegram_callback:5:ofr_bot_local_7:2:remaining:5:50")

        # 3. Exact request surface, server, pre-gated, edge, background, and db arguments
        for c in [call_1, call_2]:
            self.assertEqual(c.kwargs["request_source_surface"], OfferRequestSourceSurface.TELEGRAM_BOT)
            self.assertEqual(c.kwargs["request_source_server"], "foreign")
            self.assertTrue(c.kwargs["request_pre_gated"])
            self.assertIsInstance(c.kwargs["edge_received_at"], datetime)
            self.assertIs(c.kwargs["db"], session)
            self.assertIsInstance(c.kwargs["background_tasks"], BackgroundTasks)

        # 4. Context rehydration on attempt 2
        context_1: EffectiveOwnerActor = call_1.kwargs["context"]
        self.assertIs(context_1.owner_user, user)
        self.assertIs(context_1.actor_user, user)
        self.assertIsNone(context_1.relation)
        self.assertFalse(context_1.is_accountant_context)

        context_2: EffectiveOwnerActor = call_2.kwargs["context"]
        self.assertIs(context_2.owner_user, reloaded_user)
        self.assertIs(context_2.actor_user, reloaded_user)
        self.assertIsNone(context_2.relation)
        self.assertFalse(context_2.is_accountant_context)

        # 5. Independent retry mechanics: one rollback, one expunge_all, owner reload, log, and sleep
        self.assertEqual(session.rollbacks, 1)
        self.assertEqual(session.expunge_all_calls, 1)
        self.assertEqual(session.get_calls, [(User, 5)])
        self.assertEqual(len(log_calls), 1)
        log_data = extract_log_payload(log_calls[0])
        self.assertEqual(log_data.get("event"), "trade_execute.transient_retry")
        self.assertEqual(log_data.get("action"), "trade_execute")
        self.assertEqual(log_data.get("result"), "attempt")
        self.assertEqual(log_data.get("offer_id"), 7)
        self.assertEqual(log_data.get("error_class"), "OperationalError")
        self.assertTrue(log_data.get("has_idempotency_key"))
        mock_sleep.assert_awaited_once_with(TRADE_TRANSIENT_RETRY_BASE_DELAY_SECONDS)

        # 6. Callback success acknowledgement, markup cleanup, suggestion removal, background tasks
        callback.answer.assert_awaited_once_with("معامله ثبت شد ✅", show_alert=False)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        mocks.remove_mock.assert_awaited_once_with(7, 200, 50)
        self.assertEqual(events, ["answer", "markup", "background_task"])

        # 7. Independence checks: no handler add/commit, extra rollback, forward, queue refresh, etc.
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.added, [])
        mocks.double_click_mock.assert_not_awaited()
        mocks.forward_mock.assert_not_awaited()
        mocks.queue_refresh_mock.assert_not_awaited()
        mocks.build_lot_mock.assert_not_called()
        mocks.send_suggestion_mock.assert_not_awaited()
        mocks.build_buttons_mock.assert_not_called()
        mocks.upsert_record_mock.assert_not_awaited()
        mocks.schedule_cleanup_mock.assert_not_awaited()
        mocks.schedule_reset_mock.assert_not_awaited()

    async def test_non_transient_failure_does_not_retry_or_emit_success_side_effects(self):
        user = make_user(user_id=5, telegram_id=555)
        offer = make_offer(offer_id=7, offer_public_id="ofr_bot_local_7")
        session = TransientRetryTrackingSession(offer)
        events: list[str] = []
        callback = make_callback(chat_id=200, message_id=50, events=events)
        bot = SimpleNamespace(send_message=AsyncMock())
        callback_data = ChannelTradeCallback(offer_id=7, amount=2)
        non_transient_error = ValueError("non_transient_business_failure")
        authoritative_mock = AsyncMock(side_effect=non_transient_error)

        with patch_hermetic_sleep_and_logging() as (mock_sleep, log_calls):
            with patch_live_transient_retry_boundaries(session, authoritative_mock) as mocks:
                with self.assertRaises(ValueError) as cm:
                    await handle_channel_trade(
                        callback,
                        callback_data,
                        user=user,
                        bot=bot,
                        trade_contention_preconfirmed=True,
                        trade_contention_pre_gated=True,
                    )
                self.assertIs(cm.exception, non_transient_error)

        # 1. Authoritative execution boundary called exactly once
        self.assertEqual(authoritative_mock.await_count, 1)

        # 2. No retry mechanics
        self.assertEqual(session.rollbacks, 0)
        self.assertEqual(session.expunge_all_calls, 0)
        self.assertEqual(session.get_calls, [])
        mock_sleep.assert_not_awaited()
        self.assertEqual(len(log_calls), 0)

        # 3. No success acknowledgement, markup cleanup, suggestion removal, or background tasks
        callback.answer.assert_not_awaited()
        callback.message.edit_reply_markup.assert_not_awaited()
        mocks.remove_mock.assert_not_awaited()
        self.assertEqual(events, [])
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.added, [])
        mocks.forward_mock.assert_not_awaited()
        mocks.queue_refresh_mock.assert_not_awaited()

    async def test_transient_retry_exhaustion_stops_before_success_side_effects(self):
        user = make_user(user_id=5, telegram_id=555)
        reloaded_user = make_user(user_id=5, telegram_id=555, account_name="buyer_user_reloaded")
        offer = make_offer(offer_id=7, offer_public_id="ofr_bot_local_7")
        session = TransientRetryTrackingSession(offer, reloaded_user=reloaded_user)
        events: list[str] = []
        callback = make_callback(chat_id=200, message_id=50, events=events)
        bot = SimpleNamespace(send_message=AsyncMock())
        callback_data = ChannelTradeCallback(offer_id=7, amount=2)

        error1 = make_retryable_operational_error("deadlock on attempt 1")
        error2 = make_retryable_operational_error("deadlock on attempt 2")
        error3 = make_retryable_operational_error("deadlock on attempt 3")
        authoritative_mock = AsyncMock(side_effect=[error1, error2, error3])

        with patch_hermetic_sleep_and_logging() as (mock_sleep, log_calls):
            with patch_live_transient_retry_boundaries(session, authoritative_mock) as mocks:
                with self.assertRaises(OperationalError) as cm:
                    await handle_channel_trade(
                        callback,
                        callback_data,
                        user=user,
                        bot=bot,
                        trade_contention_preconfirmed=True,
                        trade_contention_pre_gated=True,
                    )
                self.assertIs(cm.exception, error3)

        # 1. Exactly TRADE_TRANSIENT_RETRY_ATTEMPTS authoritative attempts
        self.assertEqual(authoritative_mock.await_count, TRADE_TRANSIENT_RETRY_ATTEMPTS)
        self.assertEqual(TRADE_TRANSIENT_RETRY_ATTEMPTS, 3)

        # 2. Stable TradeCreate and idempotency across all attempts
        first_trade_data = authoritative_mock.call_args_list[0].kwargs["trade_data"]
        for call_item in authoritative_mock.call_args_list:
            td: TradeCreate = call_item.kwargs["trade_data"]
            self.assertIs(td, first_trade_data)
            self.assertEqual(td.offer_id, 7)
            self.assertEqual(td.offer_public_id, "ofr_bot_local_7")
            self.assertEqual(td.quantity, 2)
            self.assertEqual(td.idempotency_key, "telegram_callback:5:ofr_bot_local_7:2:remaining:5:50")
            self.assertEqual(call_item.kwargs["request_source_surface"], OfferRequestSourceSurface.TELEGRAM_BOT)
            self.assertEqual(call_item.kwargs["request_source_server"], "foreign")
            self.assertTrue(call_item.kwargs["request_pre_gated"])

        # 3. Context rehydration on attempts 2 and 3
        self.assertIs(authoritative_mock.call_args_list[0].kwargs["context"].owner_user, user)
        self.assertIs(authoritative_mock.call_args_list[1].kwargs["context"].owner_user, reloaded_user)
        self.assertIs(authoritative_mock.call_args_list[2].kwargs["context"].owner_user, reloaded_user)

        # 4. Exactly two retry cycles (rollback, expunge_all, reload, log, sleep with scaled delay)
        self.assertEqual(session.rollbacks, 2)
        self.assertEqual(session.expunge_all_calls, 2)
        self.assertEqual(session.get_calls, [(User, 5), (User, 5)])
        self.assertEqual(len(log_calls), 2)
        for l_call in log_calls:
            payload = extract_log_payload(l_call)
            self.assertEqual(payload.get("event"), "trade_execute.transient_retry")
            self.assertEqual(payload.get("action"), "trade_execute")
            self.assertEqual(payload.get("result"), "attempt")
            self.assertEqual(payload.get("offer_id"), 7)
            self.assertEqual(payload.get("error_class"), "OperationalError")
            self.assertTrue(payload.get("has_idempotency_key"))

        self.assertEqual(mock_sleep.await_count, 2)
        mock_sleep.assert_has_awaits([
            call(TRADE_TRANSIENT_RETRY_BASE_DELAY_SECONDS * 1),
            call(TRADE_TRANSIENT_RETRY_BASE_DELAY_SECONDS * 2),
        ])

        # 5. Zero success side effects
        callback.answer.assert_not_awaited()
        callback.message.edit_reply_markup.assert_not_awaited()
        mocks.remove_mock.assert_not_awaited()
        self.assertEqual(events, [])
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.added, [])
        mocks.forward_mock.assert_not_awaited()
        mocks.queue_refresh_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
