"""Direct characterization tests for overtime owner decision remote-home and reject branches.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-OWNER-DECISION-L191

Included scenarios:
- remote-home fail-closed alert with no local mutation
- successful local owner reject with requester status edit
- successful local owner reject when requester lookup returns None

Evidenced exclusions:
- approve authorization error
- approve claim/execution HTTPException/JSONResponse/success
- background task execution/failure
- expiry/terminal owner errors
- origin SUCCESS/policy/notification/retry cells
- product behavior
- registry/index reconciliation
- blocker closure
- Stage closure
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Fail-closed network fence must be installed before handler imports.
install_p1_00_network_fence()

from bot.handlers.offer_overtime_callbacks import handle_overtime_owner_decision
from bot.overtime_request_status import (
    empty_inline_keyboard,
    terminal_requester_status_text,
)
from core.offer_lifecycle import read_normal_lifetime_minutes
from core.offer_overtime_bot_copy import (
    M14_REQUESTER_STATUS_TERMINAL_FAILURE,
    M30_OWNER_STATUS_REJECTED,
)
from core.server_routing import (
    SERVER_FOREIGN,
    SERVER_IRAN,
    normalize_server,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    parse_overtime_owner_approval_callback_data,
)
from core.trading_settings import TradingSettings
from models.offer_request import OfferRequestWorkflow
from models.user import User


class OvertimeOwnerDecisionRemoteHomeRejectCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def _create_mock_session(self):
        session = MagicMock()
        session.get = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()

        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(return_value=session_cm)
        return session, session_cm, session_factory

    async def test_remote_home_valid_reject_answers_reference_server_message_without_local_mutation(
        self,
    ):
        owner = SimpleNamespace(id=71)
        callback = SimpleNamespace(data="ota:req_0123456789abcdef:reject")
        bot = MagicMock()
        ledger = SimpleNamespace(
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            request_home_server="iran",
            requester_user_id=84,
        )

        session, session_cm, session_factory = self._create_mock_session()

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
            ) as mock_answer,
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_owner_approval_callback_data",
                wraps=parse_overtime_owner_approval_callback_data,
            ) as wrapped_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                session_factory,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new_callable=AsyncMock,
                return_value=ledger,
            ) as mock_load_ledger,
            patch(
                "bot.handlers.offer_overtime_callbacks.normalize_server",
                wraps=normalize_server,
            ) as wrapped_normalize,
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                return_value=SERVER_FOREIGN,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new_callable=AsyncMock,
            ) as mock_settings,
            patch(
                "bot.handlers.offer_overtime_callbacks.read_normal_lifetime_minutes",
                wraps=read_normal_lifetime_minutes,
            ) as mock_lifetime,
            patch(
                "bot.handlers.offer_overtime_callbacks.reject_by_owner",
                new_callable=AsyncMock,
            ) as mock_reject,
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_edit_owner,
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_requester_overtime_status",
                new_callable=AsyncMock,
            ) as mock_edit_requester,
            patch(
                "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                new_callable=AsyncMock,
            ) as mock_trade,
            patch(
                "bot.handlers.offer_overtime_callbacks.claim_owner_approval",
                new_callable=AsyncMock,
            ) as mock_claim,
            patch(
                "bot.handlers.offer_overtime_callbacks.BackgroundTasks",
            ) as mock_bg_tasks,
        ):
            await handle_overtime_owner_decision(callback, owner, bot)

            wrapped_parser.assert_called_once_with(
                "ota:req_0123456789abcdef:reject"
            )
            session_factory.assert_called_once_with()
            session_cm.__aenter__.assert_awaited_once_with()
            session_cm.__aexit__.assert_awaited_once_with(None, None, None)

            mock_load_ledger.assert_awaited_once_with(
                session, "req_0123456789abcdef", for_update=True
            )
            wrapped_normalize.assert_called_once_with("iran", SERVER_FOREIGN)

            mock_answer.assert_awaited_once_with(
                callback,
                "این درخواست فقط روی سرور مرجع لفظ قابل تصمیم است.",
                alert=True,
            )

            mock_settings.assert_not_called()
            mock_lifetime.assert_not_called()
            session.get.assert_not_called()
            mock_reject.assert_not_called()
            mock_edit_owner.assert_not_called()
            mock_edit_requester.assert_not_called()
            session.commit.assert_not_called()
            session.rollback.assert_not_called()
            mock_trade.assert_not_called()
            mock_claim.assert_not_called()
            mock_bg_tasks.assert_not_called()

    async def test_local_reject_with_requester_updates_both_statuses_commits_then_answers(
        self,
    ):
        owner = SimpleNamespace(id=71)
        requester = SimpleNamespace(id=84, telegram_id=987654321)
        callback = SimpleNamespace(data="ota:req_0123456789abcdef:reject")
        bot = MagicMock()
        ledger = SimpleNamespace(
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            request_home_server=SERVER_FOREIGN,
            requester_user_id=84,
        )
        trading_settings = TradingSettings(offer_expiry_minutes=17)
        sentinel_empty_keyboard = MagicMock(name="sentinel_empty_keyboard")

        session, session_cm, session_factory = self._create_mock_session()
        session.get.return_value = requester

        call_order: list[str] = []

        async def tracking_reject(db, ledg, *, decided_by_user_id, now=None, normal_lifetime_minutes=None):
            call_order.append("reject_by_owner")
            return ledg

        async def tracking_edit_owner(cb, u, text, *, source_key=None, action=None, parse_mode=None, reply_markup=None, session=None, commit=True):
            call_order.append("edit_owner")

        def tracking_terminal_text(*, cancelled=False, approved=False):
            call_order.append("terminal_text")
            return terminal_requester_status_text(cancelled=cancelled, approved=approved)

        async def tracking_edit_requester(*, session, user, ledger, text, keep_cancel=False, bot=None):
            call_order.append("edit_requester")

        async def tracking_commit():
            call_order.append("commit")

        async def tracking_answer(cb, text, *, alert=False):
            call_order.append("answer")

        session.commit.side_effect = tracking_commit

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
                side_effect=tracking_answer,
            ) as mock_answer,
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_owner_approval_callback_data",
                wraps=parse_overtime_owner_approval_callback_data,
            ) as wrapped_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                session_factory,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new_callable=AsyncMock,
                return_value=ledger,
            ) as mock_load_ledger,
            patch(
                "bot.handlers.offer_overtime_callbacks.normalize_server",
                wraps=normalize_server,
            ) as wrapped_normalize,
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                return_value=SERVER_FOREIGN,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new_callable=AsyncMock,
                return_value=trading_settings,
            ) as mock_settings,
            patch(
                "bot.handlers.offer_overtime_callbacks.read_normal_lifetime_minutes",
                wraps=read_normal_lifetime_minutes,
            ) as mock_lifetime,
            patch(
                "bot.handlers.offer_overtime_callbacks.reject_by_owner",
                new_callable=AsyncMock,
                side_effect=tracking_reject,
            ) as mock_reject,
            patch(
                "bot.handlers.offer_overtime_callbacks.empty_inline_keyboard",
                return_value=sentinel_empty_keyboard,
            ) as mock_empty_keyboard,
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new_callable=AsyncMock,
                side_effect=tracking_edit_owner,
            ) as mock_edit_owner,
            patch(
                "bot.handlers.offer_overtime_callbacks.terminal_requester_status_text",
                side_effect=tracking_terminal_text,
            ) as mock_terminal_text,
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_requester_overtime_status",
                new_callable=AsyncMock,
                side_effect=tracking_edit_requester,
            ) as mock_edit_requester,
            patch(
                "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                new_callable=AsyncMock,
            ) as mock_trade,
            patch(
                "bot.handlers.offer_overtime_callbacks.claim_owner_approval",
                new_callable=AsyncMock,
            ) as mock_claim,
            patch(
                "bot.handlers.offer_overtime_callbacks.BackgroundTasks",
            ) as mock_bg_tasks,
        ):
            await handle_overtime_owner_decision(callback, owner, bot)

            wrapped_parser.assert_called_once_with(
                "ota:req_0123456789abcdef:reject"
            )
            mock_load_ledger.assert_awaited_once_with(
                session, "req_0123456789abcdef", for_update=True
            )
            wrapped_normalize.assert_called_once_with(
                SERVER_FOREIGN, SERVER_FOREIGN
            )
            mock_settings.assert_awaited_once_with()
            mock_lifetime.assert_called_once_with(trading_settings)
            self.assertEqual(read_normal_lifetime_minutes(trading_settings), 17)

            session.get.assert_awaited_once_with(User, 84)

            mock_reject.assert_awaited_once()
            reject_args, reject_kwargs = mock_reject.await_args
            self.assertEqual(reject_args, (session, ledger))
            self.assertEqual(reject_kwargs["decided_by_user_id"], 71)
            self.assertEqual(reject_kwargs["normal_lifetime_minutes"], 17)
            self.assertIsInstance(reject_kwargs["now"], datetime)

            mock_empty_keyboard.assert_called_once_with()
            mock_edit_owner.assert_awaited_once_with(
                callback,
                owner,
                M30_OWNER_STATUS_REJECTED,
                source_key="ot-owner-reject",
                reply_markup=sentinel_empty_keyboard,
                session=session,
                commit=False,
            )

            mock_terminal_text.assert_called_once_with()
            mock_edit_requester.assert_awaited_once_with(
                session=session,
                user=requester,
                ledger=ledger,
                text=M14_REQUESTER_STATUS_TERMINAL_FAILURE,
                keep_cancel=False,
                bot=bot,
            )

            session.commit.assert_awaited_once_with()
            mock_answer.assert_awaited_once_with(
                callback, M30_OWNER_STATUS_REJECTED, alert=False
            )

            self.assertEqual(
                call_order,
                [
                    "reject_by_owner",
                    "edit_owner",
                    "terminal_text",
                    "edit_requester",
                    "commit",
                    "answer",
                ],
            )

            session.rollback.assert_not_called()
            mock_trade.assert_not_called()
            mock_claim.assert_not_called()
            mock_bg_tasks.assert_not_called()

    async def test_local_reject_without_requester_skips_requester_edit_but_commits_owner_rejection(
        self,
    ):
        owner = SimpleNamespace(id=71)
        callback = SimpleNamespace(data="ota:req_0123456789abcdef:reject")
        bot = MagicMock()
        ledger = SimpleNamespace(
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            request_home_server=SERVER_FOREIGN,
            requester_user_id=84,
        )
        trading_settings = TradingSettings(offer_expiry_minutes=17)
        sentinel_empty_keyboard = MagicMock(name="sentinel_empty_keyboard")

        session, session_cm, session_factory = self._create_mock_session()
        session.get.return_value = None

        call_order: list[str] = []

        async def tracking_reject(db, ledg, *, decided_by_user_id, now=None, normal_lifetime_minutes=None):
            call_order.append("reject_by_owner")
            return ledg

        async def tracking_edit_owner(cb, u, text, *, source_key=None, action=None, parse_mode=None, reply_markup=None, session=None, commit=True):
            call_order.append("edit_owner")

        async def tracking_commit():
            call_order.append("commit")

        async def tracking_answer(cb, text, *, alert=False):
            call_order.append("answer")

        session.commit.side_effect = tracking_commit

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
                side_effect=tracking_answer,
            ) as mock_answer,
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_owner_approval_callback_data",
                wraps=parse_overtime_owner_approval_callback_data,
            ) as wrapped_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                session_factory,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new_callable=AsyncMock,
                return_value=ledger,
            ) as mock_load_ledger,
            patch(
                "bot.handlers.offer_overtime_callbacks.normalize_server",
                wraps=normalize_server,
            ) as wrapped_normalize,
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                return_value=SERVER_FOREIGN,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new_callable=AsyncMock,
                return_value=trading_settings,
            ) as mock_settings,
            patch(
                "bot.handlers.offer_overtime_callbacks.read_normal_lifetime_minutes",
                wraps=read_normal_lifetime_minutes,
            ) as mock_lifetime,
            patch(
                "bot.handlers.offer_overtime_callbacks.reject_by_owner",
                new_callable=AsyncMock,
                side_effect=tracking_reject,
            ) as mock_reject,
            patch(
                "bot.handlers.offer_overtime_callbacks.empty_inline_keyboard",
                return_value=sentinel_empty_keyboard,
            ) as mock_empty_keyboard,
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new_callable=AsyncMock,
                side_effect=tracking_edit_owner,
            ) as mock_edit_owner,
            patch(
                "bot.handlers.offer_overtime_callbacks.terminal_requester_status_text",
            ) as mock_terminal_text,
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_requester_overtime_status",
                new_callable=AsyncMock,
            ) as mock_edit_requester,
            patch(
                "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                new_callable=AsyncMock,
            ) as mock_trade,
            patch(
                "bot.handlers.offer_overtime_callbacks.claim_owner_approval",
                new_callable=AsyncMock,
            ) as mock_claim,
            patch(
                "bot.handlers.offer_overtime_callbacks.BackgroundTasks",
            ) as mock_bg_tasks,
        ):
            await handle_overtime_owner_decision(callback, owner, bot)

            wrapped_parser.assert_called_once_with(
                "ota:req_0123456789abcdef:reject"
            )
            mock_load_ledger.assert_awaited_once_with(
                session, "req_0123456789abcdef", for_update=True
            )
            wrapped_normalize.assert_called_once_with(
                SERVER_FOREIGN, SERVER_FOREIGN
            )
            mock_settings.assert_awaited_once_with()
            mock_lifetime.assert_called_once_with(trading_settings)

            session.get.assert_awaited_once_with(User, 84)

            mock_reject.assert_awaited_once()
            reject_args, reject_kwargs = mock_reject.await_args
            self.assertEqual(reject_args, (session, ledger))
            self.assertEqual(reject_kwargs["decided_by_user_id"], 71)
            self.assertEqual(reject_kwargs["normal_lifetime_minutes"], 17)
            self.assertIsInstance(reject_kwargs["now"], datetime)

            mock_empty_keyboard.assert_called_once_with()
            mock_edit_owner.assert_awaited_once_with(
                callback,
                owner,
                M30_OWNER_STATUS_REJECTED,
                source_key="ot-owner-reject",
                reply_markup=sentinel_empty_keyboard,
                session=session,
                commit=False,
            )

            mock_terminal_text.assert_not_called()
            mock_edit_requester.assert_not_called()

            session.commit.assert_awaited_once_with()
            mock_answer.assert_awaited_once_with(
                callback, M30_OWNER_STATUS_REJECTED, alert=False
            )

            self.assertEqual(
                call_order,
                [
                    "reject_by_owner",
                    "edit_owner",
                    "commit",
                    "answer",
                ],
            )

            session.rollback.assert_not_called()
            mock_trade.assert_not_called()
            mock_claim.assert_not_called()
            mock_bg_tasks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
