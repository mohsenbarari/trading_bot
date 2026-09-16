"""Direct-characterization tests for bot.handlers.panel.unblock_user_from_user_panel.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: BOT-TG-CALLBACK-QUERY-PANEL-UNBLOCK-USER-FROM-USER-PANEL-L606
Canonical gap: docs/refactor/two-server/section-1/inventory/surface-bot.json#BOT-TG-CALLBACK-QUERY-PANEL-UNBLOCK-USER-FROM-USER-PANEL-L606

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
    UserPanelBlockCallback,
    unblock_user_from_user_panel,
)


class CharacterizePanelUnblockUserFromUserPanelTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.unblock_user_from_user_panel."""

    @patch("bot.handlers.panel.get_user_panel_blocked_keyboard")
    @patch("core.services.block_service.get_blocked_users", new_callable=AsyncMock)
    @patch("core.services.block_service.unblock_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.panel.AsyncSessionLocal")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_anonymous_callback_acknowledged_without_services(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_get_blocked_users: AsyncMock,
        mock_get_user_panel_blocked_keyboard: MagicMock,
    ) -> None:
        callback_message = SimpleNamespace(edit_text=AsyncMock(spec=[]))
        callback = SimpleNamespace(
            id="cb-panel-unblock-anon",
            data="user_panel_block:unblock:202",
            message=callback_message,
            answer=AsyncMock(spec=[]),
        )
        callback_data = UserPanelBlockCallback(action="unblock", user_id=202)

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await unblock_user_from_user_panel(callback, callback_data, None)

        self.assertIsNone(result)
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        answer_call_args, answer_call_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(len(answer_call_args), 1)
        self.assertIs(answer_call_args[0], callback)
        self.assertEqual(answer_call_kwargs, {})

        mock_reject_delegated_block_management.assert_not_called()
        mock_reject_delegated_block_management.assert_not_awaited()
        mock_async_session_local.assert_not_called()
        mock_unblock_user.assert_not_called()
        mock_unblock_user.assert_not_awaited()
        mock_get_blocked_users.assert_not_called()
        mock_get_blocked_users.assert_not_awaited()
        mock_get_user_panel_blocked_keyboard.assert_not_called()
        mock_edit_callback_message_via_runtime.assert_not_called()
        mock_edit_callback_message_via_runtime.assert_not_awaited()

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()
        callback.message.edit_text.assert_not_called()
        callback.message.edit_text.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)

    @patch("bot.handlers.panel.get_user_panel_blocked_keyboard")
    @patch("core.services.block_service.get_blocked_users", new_callable=AsyncMock)
    @patch("core.services.block_service.unblock_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.panel.AsyncSessionLocal")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_delegated_account_rejected_before_handler_session(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_get_blocked_users: AsyncMock,
        mock_get_user_panel_blocked_keyboard: MagicMock,
    ) -> None:
        callback_message = SimpleNamespace(edit_text=AsyncMock(spec=[]))
        callback = SimpleNamespace(
            id="cb-panel-unblock-delegated",
            data="user_panel_block:unblock:202",
            message=callback_message,
            answer=AsyncMock(spec=[]),
        )
        callback_data = UserPanelBlockCallback(action="unblock", user_id=202)
        requester = SimpleNamespace(id=101, username="test_delegated_user")

        mock_reject_delegated_block_management.return_value = True

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await unblock_user_from_user_panel(callback, callback_data, requester)

        self.assertIsNone(result)
        mock_reject_delegated_block_management.assert_awaited_once_with(callback, requester)
        guard_args, guard_kwargs = mock_reject_delegated_block_management.await_args
        self.assertEqual(len(guard_args), 2)
        self.assertIs(guard_args[0], callback)
        self.assertIs(guard_args[1], requester)
        self.assertEqual(guard_kwargs, {})

        mock_async_session_local.assert_not_called()
        mock_unblock_user.assert_not_called()
        mock_unblock_user.assert_not_awaited()
        mock_get_blocked_users.assert_not_called()
        mock_get_blocked_users.assert_not_awaited()
        mock_get_user_panel_blocked_keyboard.assert_not_called()
        mock_answer_callback_query_via_runtime.assert_not_called()
        mock_answer_callback_query_via_runtime.assert_not_awaited()
        mock_edit_callback_message_via_runtime.assert_not_called()
        mock_edit_callback_message_via_runtime.assert_not_awaited()

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()
        callback.message.edit_text.assert_not_called()
        callback.message.edit_text.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)

    @patch("bot.handlers.panel.get_user_panel_blocked_keyboard")
    @patch("core.services.block_service.get_blocked_users", new_callable=AsyncMock)
    @patch("core.services.block_service.unblock_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.panel.AsyncSessionLocal")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_authorized_success_updates_remaining_blocked_list(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_get_blocked_users: AsyncMock,
        mock_get_user_panel_blocked_keyboard: MagicMock,
    ) -> None:
        callback_message = SimpleNamespace(edit_text=AsyncMock(spec=[]))
        callback = SimpleNamespace(
            id="cb-panel-unblock-authorized",
            data="user_panel_block:unblock:202",
            message=callback_message,
            answer=AsyncMock(spec=[]),
        )
        callback_data = UserPanelBlockCallback(action="unblock", user_id=202)
        requester = SimpleNamespace(id=101, username="test_authorized_user")

        sentinel_session = MagicMock(name="sentinel_async_session")
        session_context = AsyncMock()
        session_context.__aenter__.return_value = sentinel_session
        session_context.__aexit__.return_value = None
        mock_async_session_local.return_value = session_context

        mock_reject_delegated_block_management.return_value = False
        success_message = "✅ مسدودیت کاربر برداشته شد."
        mock_unblock_user.return_value = (True, success_message)

        remaining_blocked_users: list[dict[str, Any]] = [{
            "id": 303,
            "account_name": "blocked_account_remaining",
            "mobile_number": "09120000000",
            "full_name": "Remaining Blocked User",
            "blocked_at": "2026-09-16T00:00:00Z",
        }]
        mock_get_blocked_users.return_value = remaining_blocked_users

        sentinel_keyboard = MagicMock(name="sentinel_inline_keyboard")
        mock_get_user_panel_blocked_keyboard.return_value = sentinel_keyboard

        execution_trace: list[str] = []

        async def trace_guard(*args: Any, **kwargs: Any) -> bool:
            execution_trace.append("guard")
            return False

        async def trace_session_enter() -> Any:
            execution_trace.append("session_enter")
            return sentinel_session

        async def trace_unblock(*args: Any, **kwargs: Any) -> tuple[bool, str]:
            execution_trace.append("unblock_user")
            return True, success_message

        async def trace_get_blocked(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            execution_trace.append("get_blocked_users")
            return remaining_blocked_users

        async def trace_session_exit(*args: Any, **kwargs: Any) -> None:
            execution_trace.append("session_exit")
            return None

        async def trace_answer(*args: Any, **kwargs: Any) -> None:
            execution_trace.append("answer_callback")

        async def trace_edit(*args: Any, **kwargs: Any) -> None:
            execution_trace.append("edit_callback")

        mock_reject_delegated_block_management.side_effect = trace_guard
        session_context.__aenter__.side_effect = trace_session_enter
        mock_unblock_user.side_effect = trace_unblock
        mock_get_blocked_users.side_effect = trace_get_blocked
        session_context.__aexit__.side_effect = trace_session_exit
        mock_answer_callback_query_via_runtime.side_effect = trace_answer
        mock_edit_callback_message_via_runtime.side_effect = trace_edit

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await unblock_user_from_user_panel(callback, callback_data, requester)

        self.assertIsNone(result)

        self.assertEqual(
            execution_trace,
            [
                "guard",
                "session_enter",
                "unblock_user",
                "get_blocked_users",
                "session_exit",
                "answer_callback",
                "edit_callback",
            ],
        )

        mock_reject_delegated_block_management.assert_awaited_once_with(callback, requester)
        guard_args, guard_kwargs = mock_reject_delegated_block_management.await_args
        self.assertIs(guard_args[0], callback)
        self.assertIs(guard_args[1], requester)
        self.assertEqual(guard_kwargs, {})

        mock_async_session_local.assert_called_once_with()
        session_context.__aenter__.assert_awaited_once_with()
        session_context.__aexit__.assert_awaited_once()

        mock_unblock_user.assert_awaited_once_with(sentinel_session, requester.id, callback_data.user_id)
        unblock_args, unblock_kwargs = mock_unblock_user.await_args
        self.assertIs(unblock_args[0], sentinel_session)
        self.assertEqual(unblock_args[1], 101)
        self.assertEqual(unblock_args[2], 202)
        self.assertEqual(unblock_kwargs, {})

        mock_get_blocked_users.assert_awaited_once_with(sentinel_session, requester.id)
        get_blocked_args, get_blocked_kwargs = mock_get_blocked_users.await_args
        self.assertIs(get_blocked_args[0], sentinel_session)
        self.assertEqual(get_blocked_args[1], 101)
        self.assertEqual(get_blocked_kwargs, {})

        mock_get_user_panel_blocked_keyboard.assert_called_once_with(remaining_blocked_users)
        keyboard_args, keyboard_kwargs = mock_get_user_panel_blocked_keyboard.call_args
        self.assertIs(keyboard_args[0], remaining_blocked_users)
        self.assertEqual(keyboard_kwargs, {})

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(
            callback,
            success_message,
            show_alert=False,
        )
        answer_args, answer_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(len(answer_args), 2)
        self.assertIs(answer_args[0], callback)
        self.assertEqual(answer_args[1], success_message)
        self.assertEqual(answer_kwargs, {"show_alert": False})

        expected_edit_text = (
            "📋 **کاربران مسدود شده**\n\n"
            "برای رفع مسدودیت روی نام کاربر بزنید:"
        )
        mock_edit_callback_message_via_runtime.assert_awaited_once_with(
            callback,
            requester,
            expected_edit_text,
            parse_mode="Markdown",
            reply_markup=sentinel_keyboard,
        )
        edit_args, edit_kwargs = mock_edit_callback_message_via_runtime.await_args
        self.assertEqual(len(edit_args), 3)
        self.assertIs(edit_args[0], callback)
        self.assertIs(edit_args[1], requester)
        self.assertEqual(edit_args[2], expected_edit_text)
        self.assertEqual(
            edit_kwargs,
            {
                "parse_mode": "Markdown",
                "reply_markup": sentinel_keyboard,
            },
        )

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()
        callback.message.edit_text.assert_not_called()
        callback.message.edit_text.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)


if __name__ == "__main__":
    unittest.main()
