"""Stage P1-00b characterization tests for process_selected_recipient_search unlinked-user guard.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This test characterizes the unlinked user guard (user=None) returning None before
message text access, FSM state get/update/set/clear operations, AsyncSessionLocal session creation,
recipient search execution, or outgoing message delivery via runtime.
This is only the silent unlinked-user guard before message text, FSM state, session/search, and delivery.
It does not characterize a linked/super-admin search result, callback denial, confirmation,
persistence, retry, concurrency/reload, any Offer/Request-origin-matrix cell, executed-seed promotion,
canonical blocker reduction, Stage acceptance, or production readiness.
P1-00 remains COMPLETE and P1-00b remains IN_PROGRESS.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.admin_broadcast import process_selected_recipient_search


class _StrictMessageSentinel:
    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected Message attribute access: {name}")


class _StrictStateSentinel:
    def __init__(self):
        self.set_state = AsyncMock()
        self.get_data = AsyncMock()
        self.update_data = AsyncMock()
        self.clear = AsyncMock()

    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected FSMContext attribute access: {name}")


class AdminBroadcastProcessSelectedRecipientSearchUnlinkedGuardCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_process_selected_recipient_search_without_user_returns_before_message_state_session_or_delivery(
        self,
    ):
        strict_message = _StrictMessageSentinel()
        strict_state = _StrictStateSentinel()

        spy_session_local = MagicMock()
        spy_search_recipients = AsyncMock()
        spy_answer_incoming = AsyncMock()

        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_broadcast.AsyncSessionLocal",
            new=spy_session_local,
        ), patch(
            "bot.handlers.admin_broadcast.search_telegram_admin_broadcast_recipients",
            new=spy_search_recipients,
        ), patch(
            "bot.handlers.admin_broadcast.answer_incoming_message_via_runtime",
            new=spy_answer_incoming,
        ):
            result = await process_selected_recipient_search(
                message=strict_message,  # type: ignore[arg-type]
                state=strict_state,      # type: ignore[arg-type]
                user=None,
            )

        self.assertIsNone(result)

        spy_session_local.assert_not_called()
        spy_search_recipients.assert_not_called()
        spy_search_recipients.assert_not_awaited()
        spy_answer_incoming.assert_not_called()
        spy_answer_incoming.assert_not_awaited()

        strict_state.set_state.assert_not_awaited()
        strict_state.get_data.assert_not_awaited()
        strict_state.update_data.assert_not_awaited()
        strict_state.clear.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
