"""Characterization tests for overtime owner approve-decision success and background tasks execution/failure branches.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-OWNER-DECISION-L191

Included branches:
1. The authoritative trade executor returns a non-JSON success result, leading to approved
   owner message edit, approved requester status edit, exactly one database commit, successful
   await of the BackgroundTasks instance, and final non-alert approved callback answer.
2. The authoritative trade executor returns a non-JSON success result, approved owner/requester
   edits and database commit succeed, the awaited BackgroundTasks instance raises RuntimeError
   which is swallowed and logged at debug level, and final non-alert approved callback answer succeeds.

Evidenced exclusions:
approval-claim errors, requester missing, remote-home/reject/entry guards, expiry/terminal owner errors,
executor HTTPException/JSONResponse failures already accepted at d005fae49a683a348916760869fd0f4ad83e0d19,
origin SUCCESS/policy/notification/retry cells, reconciliation, product behavior, blocker closure,
and Stage closure.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Bot, types
from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse

from api.deps import EffectiveOwnerActor
from api.routers.trades import TradeCreate
from bot.handlers.offer_overtime_callbacks import handle_overtime_owner_decision
from bot.overtime_request_status import (
    empty_inline_keyboard,
    terminal_requester_status_text,
)
from core.offer_lifecycle import read_normal_lifetime_minutes
from core.offer_overtime_bot_copy import (
    M13_REQUESTER_STATUS_APPROVED,
    M14_REQUESTER_STATUS_TERMINAL_FAILURE,
    M29_OWNER_STATUS_APPROVED,
    M30_OWNER_STATUS_REJECTED,
    M37_OWNER_REVALIDATION_FAILED_MESSAGE,
)
from core.server_routing import SERVER_FOREIGN, normalize_server
from core.telegram_delivery_overtime_owner_approval_contract import (
    parse_overtime_owner_approval_callback_data,
)
from core.trading_settings import TradingSettings
from models.offer_request import (
    OfferRequest,
    OfferRequestSourceSurface,
    OfferRequestWorkflow,
)
from models.user import User


class OvertimeOwnerDecisionApproveSuccessBackgroundCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def _build_fixtures(self):
        callback = MagicMock(spec=types.CallbackQuery)
        callback.data = "ota:req_0123456789abcdef:approve"

        user = User(
            id=77,
            account_name="owner_user_77",
            mobile_number="+989121111111",
            full_name="Owner User",
            address="Tehran",
        )

        requester = User(
            id=42,
            account_name="requester_user_42",
            mobile_number="+989122222222",
            full_name="Requester User",
            address="Isfahan",
        )

        received_at = datetime(2026, 9, 14, 4, 0, 0, tzinfo=timezone.utc)
        ledger = OfferRequest(
            id=10,
            request_public_id="req_0123456789abcdef",
            request_home_server=SERVER_FOREIGN,
            local_offer_id=101,
            offer_public_id="off_1234567890abcdef",
            requester_user_id=42,
            actor_user_id=42,
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_source_server=SERVER_FOREIGN,
            requested_quantity=5,
            idempotency_key="idem_key_123",
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            received_at=received_at,
            requester_status_outbox_id=1234,
        )

        trading_settings = TradingSettings(
            offer_expiry_minutes=2,
            offer_min_quantity=5,
            offer_max_quantity=50,
        )

        bot = MagicMock(spec=Bot)
        return callback, user, requester, ledger, trading_settings, received_at, bot

    def _setup_session_mock(self, requester: User):
        session = AsyncMock()
        session.get = AsyncMock(return_value=requester)
        session.commit = AsyncMock()
        session.rollback = AsyncMock()

        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session)
        session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_session_local = MagicMock(return_value=session_ctx)
        return session, session_ctx, mock_session_local

    def _assert_common_pipeline(
        self,
        *,
        session,
        session_ctx,
        mock_session_local,
        spy_parse,
        mock_load_ledger,
        spy_normalize_server,
        mock_get_settings,
        spy_read_lifetime,
        recorded_lifetimes,
        trading_settings,
        requester,
        mock_claim,
        ledger,
        mock_bg_tasks_cls,
        mock_executor,
        bg_tasks_spy,
        received_at,
        mock_reject,
        mock_empty_kb,
        empty_keyboard_sentinel,
        spy_terminal_text,
        mock_edit_owner_fn,
        mock_edit_requester_fn,
        callback,
        user,
        bot,
        mock_answer_fn,
    ):
        spy_parse.assert_called_once_with("ota:req_0123456789abcdef:approve")
        mock_load_ledger.assert_awaited_once_with(
            session, "req_0123456789abcdef", for_update=True
        )
        spy_normalize_server.assert_called_once_with(SERVER_FOREIGN, SERVER_FOREIGN)
        mock_get_settings.assert_awaited_once_with()
        spy_read_lifetime.assert_called_once_with(trading_settings)
        self.assertEqual(recorded_lifetimes, [2])
        session.get.assert_awaited_once_with(User, 42)

        mock_claim.assert_awaited_once()
        claim_args = mock_claim.call_args
        self.assertIs(claim_args.args[0], ledger)
        self.assertEqual(claim_args.kwargs["decided_by_user_id"], 77)
        self.assertIsInstance(claim_args.kwargs["now"], datetime)

        mock_bg_tasks_cls.assert_called_once_with()
        mock_executor.assert_awaited_once()
        exec_kwargs = mock_executor.call_args.kwargs

        expected_executor_keys = {
            "trade_data",
            "background_tasks",
            "db",
            "context",
            "edge_received_at",
            "request_source_surface",
            "request_source_server",
            "overtime_approval_ledger",
            "overtime_decided_by_user_id",
        }
        self.assertEqual(set(exec_kwargs.keys()), expected_executor_keys)
        self.assertNotIn("request_pre_gated", exec_kwargs)
        self.assertNotIn("max_attempts", exec_kwargs)

        self.assertIs(exec_kwargs["background_tasks"], bg_tasks_spy)
        trade_data = exec_kwargs["trade_data"]
        self.assertIsInstance(trade_data, TradeCreate)
        self.assertEqual(trade_data.offer_id, 101)
        self.assertEqual(trade_data.offer_public_id, "off_1234567890abcdef")
        self.assertEqual(trade_data.quantity, 5)
        self.assertEqual(trade_data.idempotency_key, "idem_key_123")

        context = exec_kwargs["context"]
        self.assertIsInstance(context, EffectiveOwnerActor)
        self.assertIs(context.owner_user, requester)
        self.assertIs(context.actor_user, requester)
        self.assertIsNone(context.relation)
        self.assertIs(context.is_accountant_context, False)

        self.assertIs(exec_kwargs["db"], session)
        self.assertEqual(exec_kwargs["edge_received_at"], received_at)
        self.assertEqual(
            exec_kwargs["request_source_surface"], OfferRequestSourceSurface.WEBAPP
        )
        self.assertEqual(exec_kwargs["request_source_server"], SERVER_FOREIGN)
        self.assertIs(exec_kwargs["overtime_approval_ledger"], ledger)
        self.assertEqual(exec_kwargs["overtime_decided_by_user_id"], 77)

        mock_session_local.assert_called_once_with()
        session_ctx.__aenter__.assert_awaited_once()
        session_ctx.__aexit__.assert_awaited_once()
        session.commit.assert_awaited_once()
        session.rollback.assert_not_called()
        mock_reject.assert_not_called()

        mock_empty_kb.assert_called_once_with()
        spy_terminal_text.assert_called_once_with(approved=True)

        mock_edit_owner_fn.assert_awaited_once_with(
            callback,
            user,
            M29_OWNER_STATUS_APPROVED,
            source_key="ot-owner-ok",
            reply_markup=empty_keyboard_sentinel,
            session=session,
            commit=False,
        )
        mock_edit_requester_fn.assert_awaited_once_with(
            session=session,
            user=requester,
            ledger=ledger,
            text=M13_REQUESTER_STATUS_APPROVED,
            keep_cancel=False,
            bot=bot,
        )
        mock_answer_fn.assert_awaited_once_with(
            callback, M29_OWNER_STATUS_APPROVED, alert=False
        )

        for edit_call in mock_edit_owner_fn.call_args_list:
            self.assertNotIn(M37_OWNER_REVALIDATION_FAILED_MESSAGE, edit_call.args)
            self.assertNotIn(M30_OWNER_STATUS_REJECTED, edit_call.args)
        for answer_call in mock_answer_fn.call_args_list:
            self.assertNotIn(M37_OWNER_REVALIDATION_FAILED_MESSAGE, answer_call.args)
            self.assertNotIn(M30_OWNER_STATUS_REJECTED, answer_call.args)
        self.assertNotEqual(
            mock_edit_requester_fn.call_args.kwargs["text"],
            M14_REQUESTER_STATUS_TERMINAL_FAILURE,
        )

    async def test_approve_success_commits_runs_background_then_answers(self):
        (
            callback,
            user,
            requester,
            ledger,
            trading_settings,
            received_at,
            bot,
        ) = self._build_fixtures()
        session, session_ctx, mock_session_local = self._setup_session_mock(requester)

        call_order: list[str] = []

        async def _mock_edit_owner(*args, **kwargs):
            call_order.append("owner_edit")

        async def _mock_edit_requester(*args, **kwargs):
            call_order.append("requester_edit")

        async def _mock_commit():
            call_order.append("commit")

        async def _mock_bg_success():
            call_order.append("background_tasks")

        async def _mock_answer(*args, **kwargs):
            call_order.append("answer")

        session.commit.side_effect = _mock_commit

        mock_load_ledger = AsyncMock(return_value=ledger)
        mock_get_settings = AsyncMock(return_value=trading_settings)
        mock_claim = AsyncMock(return_value=ledger)
        mock_edit_owner_fn = AsyncMock(side_effect=_mock_edit_owner)
        mock_edit_requester_fn = AsyncMock(side_effect=_mock_edit_requester)
        mock_answer_fn = AsyncMock(side_effect=_mock_answer)
        mock_reject = AsyncMock()

        empty_keyboard_sentinel = MagicMock(name="empty_keyboard_sentinel")
        mock_empty_kb = MagicMock(return_value=empty_keyboard_sentinel)
        spy_terminal_text = MagicMock(wraps=terminal_requester_status_text)
        spy_parse = MagicMock(wraps=parse_overtime_owner_approval_callback_data)
        spy_normalize_server = MagicMock(wraps=normalize_server)

        recorded_lifetimes: list[int] = []

        def _record_lifetime(ts):
            val = read_normal_lifetime_minutes(ts)
            recorded_lifetimes.append(val)
            return val

        spy_read_lifetime = MagicMock(side_effect=_record_lifetime)

        bg_tasks_spy = AsyncMock(spec=BackgroundTasks)
        bg_tasks_spy.side_effect = _mock_bg_success
        mock_bg_tasks_cls = MagicMock(return_value=bg_tasks_spy)

        authoritative_trade_result = MagicMock(name="non_json_success_trade_result")
        mock_executor = AsyncMock(return_value=authoritative_trade_result)

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_owner_approval_callback_data",
                spy_parse,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.normalize_server",
                spy_normalize_server,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.read_normal_lifetime_minutes",
                spy_read_lifetime,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.empty_inline_keyboard",
                mock_empty_kb,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.terminal_requester_status_text",
                spy_terminal_text,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.BackgroundTasks",
                mock_bg_tasks_cls,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                return_value=SERVER_FOREIGN,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                mock_session_local,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                mock_load_ledger,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                mock_get_settings,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.claim_owner_approval",
                mock_claim,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                mock_executor,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                mock_edit_owner_fn,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_requester_overtime_status",
                mock_edit_requester_fn,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                mock_answer_fn,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.reject_by_owner",
                mock_reject,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.logger"
            ) as mock_logger,
        ):
            await handle_overtime_owner_decision(callback, user, bot)

        self.assertFalse(isinstance(authoritative_trade_result, JSONResponse))
        self.assertEqual(
            call_order,
            ["owner_edit", "requester_edit", "commit", "background_tasks", "answer"],
        )
        bg_tasks_spy.assert_awaited_once_with()
        mock_logger.debug.assert_not_called()

        self._assert_common_pipeline(
            session=session,
            session_ctx=session_ctx,
            mock_session_local=mock_session_local,
            spy_parse=spy_parse,
            mock_load_ledger=mock_load_ledger,
            spy_normalize_server=spy_normalize_server,
            mock_get_settings=mock_get_settings,
            spy_read_lifetime=spy_read_lifetime,
            recorded_lifetimes=recorded_lifetimes,
            trading_settings=trading_settings,
            requester=requester,
            mock_claim=mock_claim,
            ledger=ledger,
            mock_bg_tasks_cls=mock_bg_tasks_cls,
            mock_executor=mock_executor,
            bg_tasks_spy=bg_tasks_spy,
            received_at=received_at,
            mock_reject=mock_reject,
            mock_empty_kb=mock_empty_kb,
            empty_keyboard_sentinel=empty_keyboard_sentinel,
            spy_terminal_text=spy_terminal_text,
            mock_edit_owner_fn=mock_edit_owner_fn,
            mock_edit_requester_fn=mock_edit_requester_fn,
            callback=callback,
            user=user,
            bot=bot,
            mock_answer_fn=mock_answer_fn,
        )

    async def test_approve_background_failure_is_swallowed_after_commit_then_answers(self):
        (
            callback,
            user,
            requester,
            ledger,
            trading_settings,
            received_at,
            bot,
        ) = self._build_fixtures()
        session, session_ctx, mock_session_local = self._setup_session_mock(requester)

        call_order: list[str] = []
        bg_error = RuntimeError("background task failed sentinel")

        async def _mock_edit_owner(*args, **kwargs):
            call_order.append("owner_edit")

        async def _mock_edit_requester(*args, **kwargs):
            call_order.append("requester_edit")

        async def _mock_commit():
            call_order.append("commit")

        async def _mock_bg_fail():
            call_order.append("background_tasks")
            raise bg_error

        async def _mock_answer(*args, **kwargs):
            call_order.append("answer")

        session.commit.side_effect = _mock_commit

        mock_load_ledger = AsyncMock(return_value=ledger)
        mock_get_settings = AsyncMock(return_value=trading_settings)
        mock_claim = AsyncMock(return_value=ledger)
        mock_edit_owner_fn = AsyncMock(side_effect=_mock_edit_owner)
        mock_edit_requester_fn = AsyncMock(side_effect=_mock_edit_requester)
        mock_answer_fn = AsyncMock(side_effect=_mock_answer)
        mock_reject = AsyncMock()

        empty_keyboard_sentinel = MagicMock(name="empty_keyboard_sentinel")
        mock_empty_kb = MagicMock(return_value=empty_keyboard_sentinel)
        spy_terminal_text = MagicMock(wraps=terminal_requester_status_text)
        spy_parse = MagicMock(wraps=parse_overtime_owner_approval_callback_data)
        spy_normalize_server = MagicMock(wraps=normalize_server)

        recorded_lifetimes: list[int] = []

        def _record_lifetime(ts):
            val = read_normal_lifetime_minutes(ts)
            recorded_lifetimes.append(val)
            return val

        spy_read_lifetime = MagicMock(side_effect=_record_lifetime)

        bg_tasks_spy = AsyncMock(spec=BackgroundTasks)
        bg_tasks_spy.side_effect = _mock_bg_fail
        mock_bg_tasks_cls = MagicMock(return_value=bg_tasks_spy)

        authoritative_trade_result = MagicMock(name="non_json_success_trade_result")
        mock_executor = AsyncMock(return_value=authoritative_trade_result)

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_owner_approval_callback_data",
                spy_parse,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.normalize_server",
                spy_normalize_server,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.read_normal_lifetime_minutes",
                spy_read_lifetime,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.empty_inline_keyboard",
                mock_empty_kb,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.terminal_requester_status_text",
                spy_terminal_text,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.BackgroundTasks",
                mock_bg_tasks_cls,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                return_value=SERVER_FOREIGN,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                mock_session_local,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                mock_load_ledger,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                mock_get_settings,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.claim_owner_approval",
                mock_claim,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                mock_executor,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                mock_edit_owner_fn,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_requester_overtime_status",
                mock_edit_requester_fn,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                mock_answer_fn,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.reject_by_owner",
                mock_reject,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.logger"
            ) as mock_logger,
        ):
            await handle_overtime_owner_decision(callback, user, bot)

        self.assertFalse(isinstance(authoritative_trade_result, JSONResponse))
        self.assertEqual(
            call_order,
            ["owner_edit", "requester_edit", "commit", "background_tasks", "answer"],
        )
        bg_tasks_spy.assert_awaited_once_with()
        mock_logger.debug.assert_called_once_with(
            "overtime approve background tasks failed: %s", bg_error
        )

        self._assert_common_pipeline(
            session=session,
            session_ctx=session_ctx,
            mock_session_local=mock_session_local,
            spy_parse=spy_parse,
            mock_load_ledger=mock_load_ledger,
            spy_normalize_server=spy_normalize_server,
            mock_get_settings=mock_get_settings,
            spy_read_lifetime=spy_read_lifetime,
            recorded_lifetimes=recorded_lifetimes,
            trading_settings=trading_settings,
            requester=requester,
            mock_claim=mock_claim,
            ledger=ledger,
            mock_bg_tasks_cls=mock_bg_tasks_cls,
            mock_executor=mock_executor,
            bg_tasks_spy=bg_tasks_spy,
            received_at=received_at,
            mock_reject=mock_reject,
            mock_empty_kb=mock_empty_kb,
            empty_keyboard_sentinel=empty_keyboard_sentinel,
            spy_terminal_text=spy_terminal_text,
            mock_edit_owner_fn=mock_edit_owner_fn,
            mock_edit_requester_fn=mock_edit_requester_fn,
            callback=callback,
            user=user,
            bot=bot,
            mock_answer_fn=mock_answer_fn,
        )
