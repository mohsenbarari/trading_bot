"""Direct characterization tests for overtime owner decision approve requester-missing scenario.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-OWNER-DECISION-L191

Included Scenario:
A valid approve callback and current-home OVERTIME ledger load successfully, requester lookup returns None, claim_owner_approval succeeds, then the handler emits exactly M37_OWNER_REVALIDATION_FAILED_MESSAGE with alert=True and returns without executor, Telegram edits, commit, rollback, TradeCreate/EffectiveOwnerActor construction, or BackgroundTasks.

Evidenced Exclusions:
Parser correctness beyond the valid boundary; claim service correctness or claim-error mapping; remote-home/reject/executor failure/success/background branches; actor lookup/context and trade construction/execution; origin SUCCESS/policy/notification/retry cells; other seed handlers; registry/index reconciliation; blocker/seed/Stage closure; product behavior; runtime/database/network/Redis/Offer mutation; deployment, production, DNS, secrets, and Web Writer work.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
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
from core.offer_overtime_bot_copy import M37_OWNER_REVALIDATION_FAILED_MESSAGE
from core.server_routing import SERVER_FOREIGN, current_server, normalize_server
from core.telegram_delivery_overtime_owner_approval_contract import (
    parse_overtime_owner_approval_callback_data,
)
from core.trading_settings import TradingSettings
from models.offer_request import OfferRequestWorkflow
from models.user import User


class OvertimeOwnerDecisionApproveRequesterMissingCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_approve_missing_requester_alerts_m37_after_claim_without_side_effects(
        self,
    ) -> None:
        # Authenticated deciding owner fixture
        owner = SimpleNamespace(id=77)

        # Current-home OVERTIME ledger fixture
        ledger = SimpleNamespace(
            request_public_id="req_0123456789abcdef",
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=42,
            request_home_server=SERVER_FOREIGN,
            local_offer_id=201,
            offer_public_id="off_0123456789abcdef",
            requested_quantity=10,
        )

        # Valid callback query fixture
        callback = MagicMock(spec=types.CallbackQuery)
        callback.data = "ota:req_0123456789abcdef:approve"

        # Bot and settings fixtures
        bot = MagicMock(spec=Bot)
        ts = TradingSettings(offer_expiry_minutes=17)

        # Async session mocks
        session = MagicMock()
        requester_lookup_time: datetime | None = None

        async def spy_session_get(model, ident):
            nonlocal requester_lookup_time
            call_order.append("session_get")
            requester_lookup_time = datetime.utcnow()
            return None

        session.get = AsyncMock(side_effect=spy_session_get)
        session.commit = AsyncMock()
        session.rollback = AsyncMock()

        mock_session_cm = MagicMock()

        async def spy_session_enter():
            call_order.append("session_enter")
            return session

        async def spy_session_exit(exc_type, exc_val, exc_tb):
            call_order.append("session_exit")
            return None

        mock_session_cm.__aenter__ = AsyncMock(side_effect=spy_session_enter)
        mock_session_cm.__aexit__ = AsyncMock(side_effect=spy_session_exit)
        mock_session_factory = MagicMock(return_value=mock_session_cm)

        call_order: list[str] = []
        parser_result: tuple[str, str] | None = None

        def spy_parser(*args, **kwargs):
            nonlocal parser_result
            call_order.append("parse_callback")
            parser_result = parse_overtime_owner_approval_callback_data(*args, **kwargs)
            return parser_result

        async def spy_load_ledger(*args, **kwargs):
            call_order.append("load_ledger")
            return ledger

        def spy_current_server():
            call_order.append("current_server")
            return SERVER_FOREIGN

        def spy_normalize_server(*args, **kwargs):
            call_order.append("normalize_server")
            return normalize_server(*args, **kwargs)

        async def spy_get_settings():
            call_order.append("get_trading_settings")
            return ts

        lifetime_yield: int | None = None

        def spy_read_lifetime(*args, **kwargs):
            nonlocal lifetime_yield
            call_order.append("read_lifetime")
            res = read_normal_lifetime_minutes(*args, **kwargs)
            lifetime_yield = res
            mock_read_lifetime.return_value = res
            return res

        async def spy_claim(*args, **kwargs):
            call_order.append("claim_owner_approval")
            return ledger

        async def spy_answer(*args, **kwargs):
            call_order.append("answer")
            return None

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_owner_approval_callback_data",
                side_effect=spy_parser,
            ) as mock_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                mock_session_factory,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new_callable=AsyncMock,
                side_effect=spy_load_ledger,
            ) as mock_load_ledger,
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                side_effect=spy_current_server,
            ) as mock_current_server,
            patch(
                "bot.handlers.offer_overtime_callbacks.normalize_server",
                side_effect=spy_normalize_server,
            ) as mock_normalize_server,
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new_callable=AsyncMock,
                side_effect=spy_get_settings,
            ) as mock_get_settings,
            patch(
                "bot.handlers.offer_overtime_callbacks.read_normal_lifetime_minutes",
                side_effect=spy_read_lifetime,
            ) as mock_read_lifetime,
            patch(
                "bot.handlers.offer_overtime_callbacks.claim_owner_approval",
                new_callable=AsyncMock,
                side_effect=spy_claim,
            ) as mock_claim,
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
                side_effect=spy_answer,
            ) as mock_answer,
            # Downstream suppression targets
            patch(
                "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                new_callable=AsyncMock,
            ) as mock_execute_trade,
            patch(
                "bot.handlers.offer_overtime_callbacks.TradeCreate",
            ) as mock_trade_create,
            patch(
                "bot.handlers.offer_overtime_callbacks.EffectiveOwnerActor",
            ) as mock_effective_owner_actor,
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
            patch(
                "bot.handlers.offer_overtime_callbacks.terminal_requester_status_text",
            ) as mock_terminal_requester_status,
            patch(
                "bot.handlers.offer_overtime_callbacks.reject_by_owner",
                new_callable=AsyncMock,
            ) as mock_reject_by_owner,
            patch(
                "bot.handlers.offer_overtime_callbacks._answer_owner_error",
                new_callable=AsyncMock,
            ) as mock_answer_owner_error,
        ):
            await handle_overtime_owner_decision(callback, owner, bot)

            # 1. Real parser receives exact data and returns exact tuple
            mock_parser.assert_called_once_with("ota:req_0123456789abcdef:approve")
            self.assertEqual(parser_result, ("req_0123456789abcdef", "approve"))

            # 2. Exactly one async session scope opened and exited normally
            mock_session_factory.assert_called_once_with()
            mock_session_cm.__aenter__.assert_awaited_once_with()
            mock_session_cm.__aexit__.assert_awaited_once_with(None, None, None)

            # 3. Locked ledger loaded with session, exact public id, and for_update=True
            mock_load_ledger.assert_awaited_once_with(
                session, "req_0123456789abcdef", for_update=True
            )

            # 4. Server routing keeps the ledger local
            self.assertEqual(mock_current_server.call_count, 2)
            mock_normalize_server.assert_called_once_with(
                SERVER_FOREIGN, SERVER_FOREIGN
            )

            # 5. TradingSettings readout and lifetime minutes yield 17
            mock_get_settings.assert_awaited_once_with()
            mock_read_lifetime.assert_called_once_with(ts)
            self.assertEqual(lifetime_yield, 17)
            self.assertEqual(mock_read_lifetime.return_value, 17)
            self.assertEqual(read_normal_lifetime_minutes(ts), 17)

            # 6. Session.get awaited exactly once with authoritative User model and requester_user_id
            session.get.assert_awaited_once_with(User, 42)

            # 7. claim_owner_approval succeeded and was awaited once with exact contract
            mock_claim.assert_awaited_once()
            claim_args, claim_kwargs = mock_claim.await_args
            self.assertEqual(len(claim_args), 1)
            self.assertIs(claim_args[0], ledger)
            self.assertEqual(set(claim_kwargs.keys()), {"decided_by_user_id", "now"})
            self.assertEqual(claim_kwargs["decided_by_user_id"], 77)
            self.assertIsInstance(claim_kwargs["now"], datetime)
            self.assertFalse(isinstance(claim_kwargs["now"], bool))
            self.assertIsNotNone(requester_lookup_time)
            self.assertGreaterEqual(claim_kwargs["now"], requester_lookup_time)

            # 8. Handler emits M37_OWNER_REVALIDATION_FAILED_MESSAGE with alert=True
            mock_answer.assert_awaited_once_with(
                callback, M37_OWNER_REVALIDATION_FAILED_MESSAGE, alert=True
            )

            # 9. Observable ordering across all milestones
            expected_order = [
                "parse_callback",
                "session_enter",
                "load_ledger",
                "current_server",
                "normalize_server",
                "current_server",
                "get_trading_settings",
                "read_lifetime",
                "session_get",
                "claim_owner_approval",
                "answer",
                "session_exit",
            ]
            self.assertEqual(call_order, expected_order)

            # 10. Complete downstream suppression
            mock_execute_trade.assert_not_called()
            mock_trade_create.assert_not_called()
            mock_effective_owner_actor.assert_not_called()
            mock_background_tasks.assert_not_called()
            mock_edit_callback.assert_not_called()
            mock_edit_requester.assert_not_called()
            mock_empty_keyboard.assert_not_called()
            mock_terminal_requester_status.assert_not_called()
            mock_reject_by_owner.assert_not_called()
            mock_answer_owner_error.assert_not_called()
            session.commit.assert_not_called()
            session.rollback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
