"""Characterization tests for overtime owner approve-executor failure branches.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-OWNER-DECISION-L191

Included branches:
1. The authoritative trade executor raises a real fastapi.HTTPException with a
   nonempty string detail after successful approval claim.
2. The authoritative trade executor returns a real fastapi.responses.JSONResponse
   after successful approval claim.

Evidenced exclusions:
Approve success, BackgroundTasks execution/failure, approval-claim errors,
requester missing, remote-home/reject/entry guards, expiry/terminal owner errors,
origin SUCCESS/policy/notification/retry cells, product behavior, reconciliation,
blocker closure, and Stage closure.
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
from fastapi import BackgroundTasks, HTTPException
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


class OvertimeOwnerDecisionApproveExecutionFailuresCharacterizationTests(
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

    async def test_http_exception_executor_failure_commits_terminal_edits_then_alerts_detail(self):
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
        mock_bg_tasks_cls = MagicMock(return_value=bg_tasks_spy)

        async def _failing_executor(*, trade_data, background_tasks, **kwargs):
            raise HTTPException(status_code=409, detail="executor refused")

        mock_executor = AsyncMock(side_effect=_failing_executor)

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
        ):
            await handle_overtime_owner_decision(callback, user, bot)

        # Exact parser, locked load, routing, settings, and requester lookup
        spy_parse.assert_called_once_with("ota:req_0123456789abcdef:approve")
        mock_load_ledger.assert_awaited_once_with(session, "req_0123456789abcdef", for_update=True)
        spy_normalize_server.assert_called_once_with(SERVER_FOREIGN, SERVER_FOREIGN)
        mock_get_settings.assert_awaited_once_with()
        spy_read_lifetime.assert_called_once_with(trading_settings)
        self.assertEqual(recorded_lifetimes, [2])
        session.get.assert_awaited_once_with(User, 42)

        # Successful claim_owner_approval call
        mock_claim.assert_awaited_once()
        claim_args = mock_claim.call_args
        self.assertIs(claim_args.args[0], ledger)
        self.assertEqual(claim_args.kwargs["decided_by_user_id"], 77)
        self.assertIsInstance(claim_args.kwargs["now"], datetime)

        # BackgroundTasks construction and injection without execution
        mock_bg_tasks_cls.assert_called_once_with()
        mock_executor.assert_awaited_once()
        exec_kwargs = mock_executor.call_args.kwargs
        self.assertIs(exec_kwargs["background_tasks"], bg_tasks_spy)
        bg_tasks_spy.assert_not_called()
        bg_tasks_spy.assert_not_awaited()
        self.assertEqual(bg_tasks_spy.call_count, 0)
        self.assertEqual(bg_tasks_spy.await_count, 0)

        # TradeCreate and EffectiveOwnerActor contracts
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
        self.assertEqual(exec_kwargs["request_source_surface"], OfferRequestSourceSurface.WEBAPP)
        self.assertEqual(exec_kwargs["request_source_server"], SERVER_FOREIGN)
        self.assertIs(exec_kwargs["overtime_approval_ledger"], ledger)
        self.assertEqual(exec_kwargs["overtime_decided_by_user_id"], 77)

        # Scope and mutations
        mock_session_local.assert_called_once_with()
        session_ctx.__aenter__.assert_awaited_once()
        session_ctx.__aexit__.assert_awaited_once()
        session.rollback.assert_not_called()
        mock_reject.assert_not_called()

        # Helper calls, terminal edits, commit, and answer order
        mock_empty_kb.assert_called_once_with()
        spy_terminal_text.assert_called_once_with()
        self.assertEqual(call_order, ["owner_edit", "requester_edit", "commit", "answer"])
        mock_edit_owner_fn.assert_awaited_once_with(
            callback,
            user,
            M37_OWNER_REVALIDATION_FAILED_MESSAGE,
            source_key="ot-owner-reval",
            reply_markup=empty_keyboard_sentinel,
            session=session,
            commit=False,
        )
        mock_edit_requester_fn.assert_awaited_once_with(
            session=session,
            user=requester,
            ledger=ledger,
            text=M14_REQUESTER_STATUS_TERMINAL_FAILURE,
            keep_cancel=False,
            bot=bot,
        )
        session.commit.assert_awaited_once()
        mock_answer_fn.assert_awaited_once_with(callback, "executor refused", alert=True)

        # Approved-path suppression
        for edit_call in mock_edit_owner_fn.call_args_list:
            self.assertNotIn(M29_OWNER_STATUS_APPROVED, edit_call.args)
            self.assertNotIn(M13_REQUESTER_STATUS_APPROVED, edit_call.args)
        for answer_call in mock_answer_fn.call_args_list:
            self.assertNotIn(M29_OWNER_STATUS_APPROVED, answer_call.args)
            self.assertNotIn(M13_REQUESTER_STATUS_APPROVED, answer_call.args)
        self.assertNotEqual(
            mock_edit_requester_fn.call_args.kwargs["text"],
            M13_REQUESTER_STATUS_APPROVED,
        )

    async def test_json_response_executor_failure_commits_terminal_edits_then_alerts_revalidation(self):
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
        mock_bg_tasks_cls = MagicMock(return_value=bg_tasks_spy)
        json_failure_payload = {"status": "rejected", "reason": "limit_exceeded"}

        async def _json_returning_executor(*, trade_data, background_tasks, **kwargs):
            return JSONResponse(content=json_failure_payload, status_code=409)

        mock_executor = AsyncMock(side_effect=_json_returning_executor)

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
        ):
            await handle_overtime_owner_decision(callback, user, bot)

        # Exact parser, locked load, routing, settings, and requester lookup
        spy_parse.assert_called_once_with("ota:req_0123456789abcdef:approve")
        mock_load_ledger.assert_awaited_once_with(session, "req_0123456789abcdef", for_update=True)
        spy_normalize_server.assert_called_once_with(SERVER_FOREIGN, SERVER_FOREIGN)
        mock_get_settings.assert_awaited_once_with()
        spy_read_lifetime.assert_called_once_with(trading_settings)
        self.assertEqual(recorded_lifetimes, [2])
        session.get.assert_awaited_once_with(User, 42)

        # Successful claim_owner_approval call
        mock_claim.assert_awaited_once()
        claim_args = mock_claim.call_args
        self.assertIs(claim_args.args[0], ledger)
        self.assertEqual(claim_args.kwargs["decided_by_user_id"], 77)
        self.assertIsInstance(claim_args.kwargs["now"], datetime)

        # BackgroundTasks construction and injection without execution
        mock_bg_tasks_cls.assert_called_once_with()
        mock_executor.assert_awaited_once()
        exec_kwargs = mock_executor.call_args.kwargs
        self.assertIs(exec_kwargs["background_tasks"], bg_tasks_spy)
        bg_tasks_spy.assert_not_called()
        bg_tasks_spy.assert_not_awaited()
        self.assertEqual(bg_tasks_spy.call_count, 0)
        self.assertEqual(bg_tasks_spy.await_count, 0)

        # TradeCreate and EffectiveOwnerActor contracts
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
        self.assertEqual(exec_kwargs["request_source_surface"], OfferRequestSourceSurface.WEBAPP)
        self.assertEqual(exec_kwargs["request_source_server"], SERVER_FOREIGN)
        self.assertIs(exec_kwargs["overtime_approval_ledger"], ledger)
        self.assertEqual(exec_kwargs["overtime_decided_by_user_id"], 77)

        # Scope and mutations
        mock_session_local.assert_called_once_with()
        session_ctx.__aenter__.assert_awaited_once()
        session_ctx.__aexit__.assert_awaited_once()
        session.rollback.assert_not_called()
        mock_reject.assert_not_called()

        # Helper calls, terminal edits, commit, and answer order
        mock_empty_kb.assert_called_once_with()
        spy_terminal_text.assert_called_once_with()
        self.assertEqual(call_order, ["owner_edit", "requester_edit", "commit", "answer"])
        mock_edit_owner_fn.assert_awaited_once_with(
            callback,
            user,
            M37_OWNER_REVALIDATION_FAILED_MESSAGE,
            source_key="ot-owner-json",
            reply_markup=empty_keyboard_sentinel,
            session=session,
            commit=False,
        )
        mock_edit_requester_fn.assert_awaited_once_with(
            session=session,
            user=requester,
            ledger=ledger,
            text=M14_REQUESTER_STATUS_TERMINAL_FAILURE,
            keep_cancel=False,
            bot=bot,
        )
        session.commit.assert_awaited_once()
        mock_answer_fn.assert_awaited_once_with(
            callback,
            M37_OWNER_REVALIDATION_FAILED_MESSAGE,
            alert=True,
        )

        # Payload containment: response body is not surfaced in user-facing message or answer
        answer_text = mock_answer_fn.call_args.args[1]
        self.assertNotIn("limit_exceeded", answer_text)
        self.assertNotIn("rejected", answer_text)

        # Approved-path suppression
        for edit_call in mock_edit_owner_fn.call_args_list:
            self.assertNotIn(M29_OWNER_STATUS_APPROVED, edit_call.args)
            self.assertNotIn(M13_REQUESTER_STATUS_APPROVED, edit_call.args)
        for answer_call in mock_answer_fn.call_args_list:
            self.assertNotIn(M29_OWNER_STATUS_APPROVED, answer_call.args)
            self.assertNotIn(M13_REQUESTER_STATUS_APPROVED, answer_call.args)
        self.assertNotEqual(
            mock_edit_requester_fn.call_args.kwargs["text"],
            M13_REQUESTER_STATUS_APPROVED,
        )
