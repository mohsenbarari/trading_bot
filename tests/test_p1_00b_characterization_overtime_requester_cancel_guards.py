"""Direct characterization tests for overtime requester cancel entry guards.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-REQUESTER-CANCEL-L74

Provenance & Scope:
This module provides direct characterization for the entry guards of the real
`handle_overtime_requester_cancel` handler. It does not update registries or
close the seed, blocker, or Stage. Evidenced exclusions apply: this unit does not
characterize valid-ledger lookup, requester ownership, local cancel, remote
forwarding, database mutation, message editing, success, origin policy,
notification audience, or retry behavior.
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

from bot.handlers.offer_overtime_callbacks import (
    handle_overtime_requester_cancel,
)
from core.offer_overtime_bot_copy import (
    M33_OWNER_ALREADY_TERMINAL_MESSAGE,
)
from core.telegram_delivery_overtime_requester_status_contract import (
    parse_overtime_requester_cancel_callback_data,
)


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


if __name__ == "__main__":
    unittest.main()
