"""Direct characterization tests for overtime requester cancel entry guards.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-REQUESTER-CANCEL-L74

Provenance & Scope:
This module provides direct characterization for the entry guards and valid-callback
missing/non-OVERTIME-ledger and requester-mismatch pre-mutation guards of the real
`handle_overtime_requester_cancel` handler. It does not update registries or
close the seed, blocker, or Stage. Evidenced exclusions apply: this unit does not
characterize matched-requester routing, local cancellation, remote
forwarding, database mutation, message editing, success, origin policy,
notification audience, or retry behavior.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Requirement 1: Install the fail-closed network fence before handler imports.
install_p1_00_network_fence()

from bot.handlers.offer_overtime_callbacks import (
    handle_overtime_requester_cancel,
)
from core.offer_overtime_bot_copy import (
    M33_OWNER_ALREADY_TERMINAL_MESSAGE,
)
from core.telegram_delivery_overtime_requester_status_contract import (
    parse_overtime_requester_cancel_callback_data,
)
from models.offer_request import (
    OfferRequestWorkflow,
)


class _MockAsyncSessionContext:
    def __init__(self, session: MagicMock, order: list[str]) -> None:
        self.session = session
        self.order = order

    async def __aenter__(self) -> MagicMock:
        self.order.append("session_enter")
        return self.session

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> bool:
        self.order.append("session_exit")
        return False


class OvertimeRequesterCancelGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_unauthenticated_guard_answers_m33_without_reading_callback_data_or_downstream_work(
        self,
    ):
        # Requirement 2: user is None, callback fixture lacks .data attribute.
        callback = SimpleNamespace()
        user = None
        bot = MagicMock()

        fail_session = MagicMock(
            side_effect=AssertionError("AsyncSessionLocal must not be called")
        )
        mock_forward = AsyncMock()
        mock_edit = AsyncMock()

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
            ) as mock_answer,
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_requester_cancel_callback_data",
            ) as mock_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                fail_session,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                mock_forward,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                mock_edit,
            ),
        ):
            await handle_overtime_requester_cancel(callback, user, bot)

            self.assertFalse(hasattr(callback, "data"))
            mock_answer.assert_awaited_once_with(
                callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True
            )
            mock_parser.assert_not_called()
            fail_session.assert_not_called()
            mock_forward.assert_not_awaited()
            mock_edit.assert_not_awaited()
            bot.assert_not_called()

    async def test_malformed_callback_guard_answers_m33_without_session_or_downstream_work(
        self,
    ):
        # Requirement 3: authenticated user, malformed callback data.
        callback = SimpleNamespace(data="otc:12:cancel")
        user = SimpleNamespace(id=71)
        bot = MagicMock()

        fail_session = MagicMock(
            side_effect=AssertionError("AsyncSessionLocal must not be called")
        )
        mock_forward = AsyncMock()
        mock_edit = AsyncMock()

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
            ) as mock_answer,
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_requester_cancel_callback_data",
                wraps=parse_overtime_requester_cancel_callback_data,
            ) as wrapped_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                fail_session,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                mock_forward,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                mock_edit,
            ),
        ):
            await handle_overtime_requester_cancel(callback, user, bot)

            wrapped_parser.assert_called_once_with("otc:12:cancel")
            parsed_result = parse_overtime_requester_cancel_callback_data(
                "otc:12:cancel"
            )
            self.assertIsNone(parsed_result)
            self.assertIsNone(
                wrapped_parser.spy_return
                if hasattr(wrapped_parser, "spy_return")
                else parsed_result
            )
            mock_answer.assert_awaited_once_with(
                callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True
            )
            fail_session.assert_not_called()
            mock_forward.assert_not_awaited()
            mock_edit.assert_not_awaited()
            bot.assert_not_called()


    async def test_valid_callback_pre_mutation_ledger_and_requester_guards_answer_m33_with_single_unlocked_load(
        self,
    ):
        cases = (
            ("missing_ledger", None),
            (
                "direct_workflow_ledger",
                SimpleNamespace(
                    workflow_kind=OfferRequestWorkflow.DIRECT,
                    requester_user_id=71,
                ),
            ),
            (
                "mismatched_requester_ledger",
                SimpleNamespace(
                    workflow_kind=OfferRequestWorkflow.OVERTIME,
                    requester_user_id=72,
                ),
            ),
        )

        for case_name, case_ledger in cases:
            with self.subTest(case=case_name):
                callback = SimpleNamespace(
                    data="otc:req_0123456789abcdef:cancel"
                )
                user = SimpleNamespace(id=71)
                bot = MagicMock()
                order: list[str] = []

                mock_session = MagicMock()
                mock_session.execute = AsyncMock()
                mock_session.get = AsyncMock()
                mock_session.rollback = AsyncMock()
                mock_session.commit = AsyncMock()

                session_ctx = _MockAsyncSessionContext(mock_session, order)
                session_factory = MagicMock(return_value=session_ctx)

                def tracked_parse(callback_data: Any) -> str | None:
                    order.append("parse")
                    return parse_overtime_requester_cancel_callback_data(
                        callback_data
                    )

                mock_parser = MagicMock(side_effect=tracked_parse)

                async def tracked_load(
                    session: Any, public_id: str, *, for_update: bool = False
                ) -> Any:
                    order.append("load")
                    return case_ledger

                mock_load = AsyncMock(side_effect=tracked_load)

                async def tracked_answer(
                    cb: Any, text: str, *, alert: bool = False
                ) -> None:
                    order.append("answer")

                mock_answer = AsyncMock(side_effect=tracked_answer)

                mock_normalize_server = MagicMock()
                mock_current_server = MagicMock()
                mock_get_settings = AsyncMock()
                mock_cancel = AsyncMock()
                mock_forward = AsyncMock()
                mock_edit = AsyncMock()
                mock_edit_owner = AsyncMock()

                with (
                    patch(
                        "bot.handlers.offer_overtime_callbacks.parse_overtime_requester_cancel_callback_data",
                        mock_parser,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                        session_factory,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                        mock_load,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks._answer",
                        mock_answer,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.normalize_server",
                        mock_normalize_server,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.current_server",
                        mock_current_server,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                        mock_get_settings,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.cancel_by_requester",
                        mock_cancel,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                        mock_forward,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                        mock_edit,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.edit_owner_overtime_approval_message",
                        mock_edit_owner,
                    ),
                ):
                    await handle_overtime_requester_cancel(callback, user, bot)

                self.assertEqual(
                    order,
                    ["parse", "session_enter", "load", "answer", "session_exit"],
                )
                mock_parser.assert_called_once_with(
                    "otc:req_0123456789abcdef:cancel"
                )
                self.assertEqual(
                    parse_overtime_requester_cancel_callback_data(
                        "otc:req_0123456789abcdef:cancel"
                    ),
                    "req_0123456789abcdef",
                )
                session_factory.assert_called_once_with()
                mock_load.assert_awaited_once_with(
                    mock_session, "req_0123456789abcdef", for_update=False
                )
                self.assertEqual(mock_load.await_count, 1)
                self.assertFalse(
                    any(
                        call.kwargs.get("for_update") is True
                        for call in mock_load.await_args_list
                    )
                )
                mock_answer.assert_awaited_once_with(
                    callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True
                )
                self.assertEqual(mock_answer.await_count, 1)
                mock_normalize_server.assert_not_called()
                mock_current_server.assert_not_called()
                mock_session.execute.assert_not_called()
                mock_session.get.assert_not_called()
                mock_session.rollback.assert_not_called()
                mock_session.commit.assert_not_called()
                mock_get_settings.assert_not_called()
                mock_cancel.assert_not_called()
                mock_forward.assert_not_called()
                mock_edit.assert_not_called()
                mock_edit_owner.assert_not_called()
                bot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
