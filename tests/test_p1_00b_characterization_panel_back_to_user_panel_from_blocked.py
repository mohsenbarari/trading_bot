"""Direct-characterization tests for bot.handlers.panel.back_to_user_panel_from_blocked.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: BOT-TG-CALLBACK-QUERY-PANEL-BACK-TO-USER-PANEL-FROM-BLOCKED-L640
Canonical gap: docs/refactor/two-server/section-1/inventory/surface-bot.json#BOT-TG-CALLBACK-QUERY-PANEL-BACK-TO-USER-PANEL-FROM-BLOCKED-L640

This characterization test suite does not update registries, reduce blockers,
change product behavior, close the seed, or close P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

FENCE = install_p1_00_network_fence()

from bot.handlers.panel import (  # noqa: E402
    back_to_user_panel_from_blocked,
)


class CharacterizePanelBackToUserPanelFromBlockedTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.back_to_user_panel_from_blocked."""

    @patch("bot.handlers.panel.AsyncSessionLocal")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_back_to_user_panel_from_blocked_unauthenticated_ack_only(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
    ) -> None:
        callback_message = SimpleNamespace(edit_text=AsyncMock(spec=[]))
        callback = SimpleNamespace(
            id="cb-panel-blocked-anon",
            data="user_panel_block:back:101",
            message=callback_message,
            answer=AsyncMock(spec=[]),
        )

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await back_to_user_panel_from_blocked(callback, None)

        self.assertIsNone(result)
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        answer_call_args, answer_call_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(len(answer_call_args), 1)
        self.assertIs(answer_call_args[0], callback)
        self.assertEqual(answer_call_kwargs, {})

        mock_edit_callback_message_via_runtime.assert_not_awaited()
        mock_edit_callback_message_via_runtime.assert_not_called()

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()
        callback.message.edit_text.assert_not_called()
        callback.message.edit_text.assert_not_awaited()

        mock_async_session_local.assert_not_called()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)

    @patch("bot.handlers.panel.AsyncSessionLocal")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_back_to_user_panel_from_blocked_authenticated(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
    ) -> None:
        callback_message = SimpleNamespace(edit_text=AsyncMock(spec=[]))
        callback = SimpleNamespace(
            id="cb-panel-blocked-auth",
            data="user_panel_block:back:101",
            message=callback_message,
            answer=AsyncMock(spec=[]),
        )
        user = SimpleNamespace(id=101, username="test_blocked_user")

        call_order: list[str] = []

        async def record_edit(*args: object, **kwargs: object) -> None:
            call_order.append("edit_callback_message_via_runtime")

        async def record_answer(*args: object, **kwargs: object) -> None:
            call_order.append("answer_callback_query_via_runtime")

        mock_edit_callback_message_via_runtime.side_effect = record_edit
        mock_answer_callback_query_via_runtime.side_effect = record_answer

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await back_to_user_panel_from_blocked(callback, user)

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            ["edit_callback_message_via_runtime", "answer_callback_query_via_runtime"],
        )

        expected_text = "👤 **پنل کاربر**\n\nاز دکمه\u200cهای پایین پیام استفاده کنید."
        mock_edit_callback_message_via_runtime.assert_awaited_once_with(
            callback,
            user,
            expected_text,
            parse_mode="Markdown",
        )
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)

        edit_call_args, edit_call_kwargs = mock_edit_callback_message_via_runtime.await_args
        self.assertEqual(len(edit_call_args), 3)
        self.assertIs(edit_call_args[0], callback)
        self.assertIs(edit_call_args[1], user)
        self.assertEqual(edit_call_args[2], expected_text)
        self.assertEqual(edit_call_kwargs, {"parse_mode": "Markdown"})

        answer_call_args, answer_call_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(len(answer_call_args), 1)
        self.assertIs(answer_call_args[0], callback)
        self.assertEqual(answer_call_kwargs, {})

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()
        callback.message.edit_text.assert_not_called()
        callback.message.edit_text.assert_not_awaited()

        mock_async_session_local.assert_not_called()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)


if __name__ == "__main__":
    unittest.main()
