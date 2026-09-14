"""Direct characterization tests for overtime owner decision approve claim errors.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-OWNER-DECISION-L191

Provenance & Scope:
This module provides direct characterization for the approve branch of the real
`handle_overtime_owner_decision` handler when `claim_owner_approval` raises
an `OvertimeRequestError`. It does not update registries or close the seed,
blocker, or Stage.

Included Scenarios:
- OvertimeRequestErrorCode.NOT_OWNER error mapping to M34_OWNER_NOT_OWNER_MESSAGE (alert=True)
- OvertimeRequestErrorCode.DECISION_EXPIRED error mapping to M32_OWNER_DECISION_EXPIRED_MESSAGE (alert=True)
- OvertimeRequestErrorCode.ALREADY_TERMINAL error mapping to M33_OWNER_ALREADY_TERMINAL_MESSAGE (alert=True)
- OvertimeRequestErrorCode.ILLEGAL_TRANSITION with nonempty detail mapping to that detail (alert=True)
- OvertimeRequestErrorCode.ILLEGAL_TRANSITION with empty detail fallback mapping to M33_OWNER_ALREADY_TERMINAL_MESSAGE (alert=True)
- Exact suppression verification: trade execution, BackgroundTasks construction/execution, Telegram
  message edits, requester status edits, empty inline keyboard generation, session commit, and session rollback.

Evidenced Exclusions:
Requester-missing behavior, service-level claim correctness, parser correctness beyond the valid callback boundary,
origin SUCCESS/policy/notification/retry cells, other seed handlers, reconciliation, blocker closure,
Stage closure, product behavior changes, runtime/network/Redis/DB mutation, deployment, production, DNS, secrets,
and Web Writer work.
"""

from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Install the fail-closed network fence before importing handler or application modules.
install_p1_00_network_fence()

from aiogram import Bot, types
from bot.handlers.offer_overtime_callbacks import handle_overtime_owner_decision
from core.offer_lifecycle import read_normal_lifetime_minutes
from core.offer_overtime_bot_copy import (
    M32_OWNER_DECISION_EXPIRED_MESSAGE,
    M33_OWNER_ALREADY_TERMINAL_MESSAGE,
    M34_OWNER_NOT_OWNER_MESSAGE,
)
from core.server_routing import SERVER_FOREIGN
from core.services.offer_overtime_request_service import (
    OvertimeRequestError,
    OvertimeRequestErrorCode,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    parse_overtime_owner_approval_callback_data,
)
from core.trading_settings import TradingSettings
from models.offer_request import (
    OfferRequest,
    OfferRequestSourceSurface,
    OfferRequestWorkflow,
)
from models.user import User, UserRole


class OvertimeOwnerDecisionApproveClaimErrorsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def _create_fresh_fixtures(self):
        owner = User(
            id=77,
            account_name="owner_user_77",
            mobile_number="+989120000077",
            full_name="Owner User",
            address="Tehran, District 1, Owner Office",
            role=UserRole.STANDARD,
        )
        requester = User(
            id=42,
            account_name="requester_user_42",
            mobile_number="+989120000042",
            full_name="Requester User",
            address="Tehran, District 2, Requester Office",
            role=UserRole.STANDARD,
        )
        ledger = OfferRequest(
            id=501,
            request_public_id="req_0123456789abcdef",
            request_home_server=SERVER_FOREIGN,
            request_source_server=SERVER_FOREIGN,
            request_source_surface=OfferRequestSourceSurface.TELEGRAM_BOT,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=42,
            offer_public_id="off_0123456789abcdef",
            local_offer_id=201,
            requested_quantity=10,
        )
        callback = MagicMock(spec=types.CallbackQuery)
        callback.data = "ota:req_0123456789abcdef:approve"
        ts = TradingSettings()
        bot = MagicMock(spec=Bot)
        return owner, requester, ledger, callback, ts, bot

    async def _run_and_assert_claim_error_boundary(
        self,
        *,
        claim_error: OvertimeRequestError,
        expected_answer_text: str,
    ) -> None:
        owner, requester, ledger, callback, ts, bot = self._create_fresh_fixtures()

        session = MagicMock()
        session.get = AsyncMock(return_value=requester)
        session.commit = AsyncMock()
        session.rollback = AsyncMock()

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session_factory = MagicMock(return_value=mock_session_cm)

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
            ) as mock_answer,
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_owner_approval_callback_data",
                wraps=parse_overtime_owner_approval_callback_data,
            ) as mock_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                mock_session_factory,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new_callable=AsyncMock,
                return_value=ledger,
            ) as mock_load_ledger,
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                return_value=SERVER_FOREIGN,
            ) as mock_current_server,
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new_callable=AsyncMock,
                return_value=ts,
            ) as mock_get_settings,
            patch(
                "bot.handlers.offer_overtime_callbacks.read_normal_lifetime_minutes",
                wraps=read_normal_lifetime_minutes,
            ) as mock_read_lifetime,
            patch(
                "bot.handlers.offer_overtime_callbacks.claim_owner_approval",
                new_callable=AsyncMock,
                side_effect=claim_error,
            ) as mock_claim,
            patch(
                "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                new_callable=AsyncMock,
            ) as mock_trade,
            patch(
                "bot.handlers.offer_overtime_callbacks.BackgroundTasks",
            ) as mock_background_tasks,
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_edit_callback,
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_requester_overtime_status",
                new_callable=AsyncMock,
            ) as mock_edit_requester,
            patch(
                "bot.handlers.offer_overtime_callbacks.empty_inline_keyboard",
            ) as mock_empty_keyboard,
        ):
            await handle_overtime_owner_decision(callback, owner, bot)

            # 1. Callback parser boundary
            mock_parser.assert_called_once_with("ota:req_0123456789abcdef:approve")

            # 2. Session lifecycle boundary (normal exit without exception suppression)
            mock_session_factory.assert_called_once_with()
            mock_session_cm.__aenter__.assert_awaited_once_with()
            mock_session_cm.__aexit__.assert_awaited_once_with(None, None, None)

            # 3. Locked ledger lookup boundary
            mock_load_ledger.assert_awaited_once_with(
                session, "req_0123456789abcdef", for_update=True
            )

            # 4. Server routing check
            mock_current_server.assert_called()

            # 5. Trading settings & normal lifetime readout
            mock_get_settings.assert_awaited_once_with()
            mock_read_lifetime.assert_called_once_with(ts)

            # 6. Requester lookup with explicit user id
            session.get.assert_awaited_once_with(User, 42)

            # 7. Exact claim call contract
            mock_claim.assert_awaited_once()
            args, kwargs = mock_claim.await_args
            self.assertEqual(len(args), 1)
            self.assertIs(args[0], ledger)
            self.assertEqual(set(kwargs.keys()), {"decided_by_user_id", "now"})
            self.assertEqual(kwargs["decided_by_user_id"], 77)
            self.assertIsInstance(kwargs["now"], datetime)
            self.assertFalse(isinstance(kwargs["now"], bool))

            # 8. Alert answer contract
            mock_answer.assert_awaited_once_with(
                callback, expected_answer_text, alert=True
            )

            # 9. Complete downstream suppression assertions
            mock_trade.assert_not_called()
            mock_background_tasks.assert_not_called()
            mock_edit_callback.assert_not_called()
            mock_edit_requester.assert_not_called()
            mock_empty_keyboard.assert_not_called()
            session.commit.assert_not_called()
            session.rollback.assert_not_called()

    async def test_not_owner_claim_error_alerts_m34_and_stops_approve_side_effects(
        self,
    ):
        noncanonical_detail = "deliberate-noncanonical-not-owner-detail-xyz"
        claim_error = OvertimeRequestError(
            OvertimeRequestErrorCode.NOT_OWNER,
            noncanonical_detail,
        )
        await self._run_and_assert_claim_error_boundary(
            claim_error=claim_error,
            expected_answer_text=M34_OWNER_NOT_OWNER_MESSAGE,
        )
        self.assertNotEqual(M34_OWNER_NOT_OWNER_MESSAGE, noncanonical_detail)

    async def test_decision_expired_claim_error_alerts_m32_and_stops_approve_side_effects(
        self,
    ):
        noncanonical_detail = "deliberate-noncanonical-decision-expired-detail-xyz"
        claim_error = OvertimeRequestError(
            OvertimeRequestErrorCode.DECISION_EXPIRED,
            noncanonical_detail,
        )
        await self._run_and_assert_claim_error_boundary(
            claim_error=claim_error,
            expected_answer_text=M32_OWNER_DECISION_EXPIRED_MESSAGE,
        )
        self.assertNotEqual(M32_OWNER_DECISION_EXPIRED_MESSAGE, noncanonical_detail)

    async def test_terminal_and_other_claim_errors_use_exact_fallback_alerts_without_side_effects(
        self,
    ):
        subtest_scenarios = [
            (
                "already_terminal_mapped_to_m33",
                OvertimeRequestError(
                    OvertimeRequestErrorCode.ALREADY_TERMINAL,
                    "deliberate-noncanonical-already-terminal-detail-xyz",
                ),
                M33_OWNER_ALREADY_TERMINAL_MESSAGE,
            ),
            (
                "illegal_transition_with_nonempty_detail_uses_verbatim_detail",
                OvertimeRequestError(
                    OvertimeRequestErrorCode.ILLEGAL_TRANSITION,
                    "خطای غیرمجاز اختصاصی وضعیت معامله",
                ),
                "خطای غیرمجاز اختصاصی وضعیت معامله",
            ),
            (
                "illegal_transition_with_empty_detail_falls_back_to_m33",
                OvertimeRequestError(
                    OvertimeRequestErrorCode.ILLEGAL_TRANSITION,
                    "",
                ),
                M33_OWNER_ALREADY_TERMINAL_MESSAGE,
            ),
        ]

        for case_name, claim_error, expected_text in subtest_scenarios:
            with self.subTest(case=case_name):
                await self._run_and_assert_claim_error_boundary(
                    claim_error=claim_error,
                    expected_answer_text=expected_text,
                )


if __name__ == "__main__":
    unittest.main()
