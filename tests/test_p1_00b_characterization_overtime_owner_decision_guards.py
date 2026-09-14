"""Direct characterization tests for overtime owner decision entry guards.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-OWNER-DECISION-L191

Provenance & Scope:
This module provides direct characterization for the entry guards of the real
`handle_overtime_owner_decision` handler. It does not update registries or
close the seed, blocker, or Stage. Evidenced exclusions apply: it does not
characterize valid OVERTIME reject/approve paths, owner authorization errors,
remote-home handling, expiry, requester status editing, background tasks,
notification/origin cells, or runtime/product behavior.
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Requirement 1: Install the fail-closed network fence before handler imports.
install_p1_00_network_fence()

from bot.handlers.offer_overtime_callbacks import handle_overtime_owner_decision
from core.offer_overtime_bot_copy import (
    M33_OWNER_ALREADY_TERMINAL_MESSAGE,
    M34_OWNER_NOT_OWNER_MESSAGE,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    parse_overtime_owner_approval_callback_data,
)
from models.offer_request import OfferRequestWorkflow


class OvertimeOwnerDecisionGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_unauthenticated_guard_answers_m34_without_reading_callback_data_or_downstream_work(
        self,
    ):
        # Requirement 2: user is None, callback fixture lacks .data attribute.
        callback = SimpleNamespace()
        user = None
        bot = MagicMock()

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
            ) as mock_answer,
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_owner_approval_callback_data"
            ) as mock_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal"
            ) as mock_session_factory,
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new_callable=AsyncMock,
            ) as mock_load_ledger,
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new_callable=AsyncMock,
            ) as mock_settings,
            patch(
                "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                new_callable=AsyncMock,
            ) as mock_trade,
        ):
            await handle_overtime_owner_decision(callback, user, bot)

            mock_answer.assert_awaited_once_with(
                callback, M34_OWNER_NOT_OWNER_MESSAGE, alert=True
            )
            mock_parser.assert_not_called()
            mock_session_factory.assert_not_called()
            mock_load_ledger.assert_not_called()
            mock_settings.assert_not_called()
            mock_trade.assert_not_called()

    async def test_malformed_callback_guard_answers_m33_without_session_or_downstream_work(
        self,
    ):
        # Requirement 3: authenticated user, malformed callback data.
        user = SimpleNamespace(id=71)
        callback = SimpleNamespace(data="ota:not-a-req:approve")
        bot = MagicMock()

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
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal"
            ) as mock_session_factory,
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new_callable=AsyncMock,
            ) as mock_load_ledger,
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new_callable=AsyncMock,
            ) as mock_settings,
            patch(
                "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                new_callable=AsyncMock,
            ) as mock_trade,
        ):
            await handle_overtime_owner_decision(callback, user, bot)

            wrapped_parser.assert_called_once_with("ota:not-a-req:approve")
            self.assertIsNone(wrapped_parser.spy_return if hasattr(wrapped_parser, "spy_return") else parse_overtime_owner_approval_callback_data("ota:not-a-req:approve"))
            mock_answer.assert_awaited_once_with(
                callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True
            )
            mock_session_factory.assert_not_called()
            mock_load_ledger.assert_not_called()
            mock_settings.assert_not_called()
            mock_trade.assert_not_called()

    async def test_valid_callback_invalid_ledger_variants_subtests(
        self,
    ):
        # Requirements 4 & 5: valid callback data with missing ledger and non-overtime ledger.
        test_cases = [
            ("missing_ledger", None),
            (
                "direct_workflow_ledger",
                SimpleNamespace(workflow_kind=OfferRequestWorkflow.DIRECT),
            ),
        ]

        for case_name, ledger_value in test_cases:
            with self.subTest(case=case_name):
                user = SimpleNamespace(id=71)
                callback = SimpleNamespace(
                    data="ota:req_0123456789abcdef:approve"
                )
                bot = MagicMock()

                call_order: list[str] = []

                def tracking_parser(data: str):
                    call_order.append("parse")
                    return parse_overtime_owner_approval_callback_data(data)

                session = MagicMock()
                session.get = AsyncMock()
                session.rollback = AsyncMock()
                session.commit = AsyncMock()

                async def session_enter():
                    call_order.append("session_enter")
                    return session

                async def session_exit(exc_type, exc_val, exc_tb):
                    call_order.append("session_exit")
                    return None

                mock_session_cm = MagicMock()
                mock_session_cm.__aenter__ = AsyncMock(
                    side_effect=session_enter
                )
                mock_session_cm.__aexit__ = AsyncMock(side_effect=session_exit)
                mock_session_factory = MagicMock(return_value=mock_session_cm)

                async def tracking_load(db, public_id, *, for_update=False):
                    call_order.append("load")
                    return ledger_value

                async def tracking_answer(cb, text, *, alert=False):
                    call_order.append("answer")

                with (
                    patch(
                        "bot.handlers.offer_overtime_callbacks._answer",
                        new_callable=AsyncMock,
                        side_effect=tracking_answer,
                    ) as mock_answer,
                    patch(
                        "bot.handlers.offer_overtime_callbacks.parse_overtime_owner_approval_callback_data",
                        side_effect=tracking_parser,
                    ) as mock_parser,
                    patch(
                        "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                        mock_session_factory,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                        new_callable=AsyncMock,
                        side_effect=tracking_load,
                    ) as mock_load_ledger,
                    patch(
                        "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                        new_callable=AsyncMock,
                    ) as mock_settings,
                    patch(
                        "bot.handlers.offer_overtime_callbacks._execute_trade_authoritatively_with_transient_retry",
                        new_callable=AsyncMock,
                    ) as mock_trade,
                ):
                    await handle_overtime_owner_decision(callback, user, bot)

                    # Assert parser was called with exact payload and returned expected tuple
                    mock_parser.assert_called_once_with(
                        "ota:req_0123456789abcdef:approve"
                    )
                    real_parsed = parse_overtime_owner_approval_callback_data(
                        "ota:req_0123456789abcdef:approve"
                    )
                    self.assertEqual(
                        real_parsed, ("req_0123456789abcdef", "approve")
                    )

                    # Assert one session scope
                    mock_session_factory.assert_called_once_with()
                    mock_session_cm.__aenter__.assert_awaited_once_with()
                    mock_session_cm.__aexit__.assert_awaited_once_with(
                        None, None, None
                    )

                    # Assert ledger load with exact arguments
                    mock_load_ledger.assert_awaited_once_with(
                        session, "req_0123456789abcdef", for_update=True
                    )

                    # Assert exact terminal answer
                    mock_answer.assert_awaited_once_with(
                        callback,
                        M33_OWNER_ALREADY_TERMINAL_MESSAGE,
                        alert=True,
                    )

                    # Requirement 4: prove parse then session enter then load(for_update=True) then answer then session exit ordering
                    self.assertEqual(
                        call_order,
                        [
                            "parse",
                            "session_enter",
                            "load",
                            "answer",
                            "session_exit",
                        ],
                    )

                    # Requirement 5: independently assert absence of session.get, rollback, commit, settings, and trade execution
                    session.get.assert_not_called()
                    session.rollback.assert_not_called()
                    session.commit.assert_not_called()
                    mock_settings.assert_not_called()
                    mock_trade.assert_not_called()


if __name__ == "__main__":
    unittest.main()
