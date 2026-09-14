"""Direct-characterization tests for bot.handlers.block_manage handle_block_user.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-IDENTITY-CALLBACK-QUERY-BLOCK-MANAGE-HANDLE-BLOCK-USER-L372

This partial unit does not update registries, reduce blockers, close seeds or close the Stage.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from aiogram.types import InlineKeyboardMarkup  # noqa: E402
from bot.handlers.block_manage import (  # noqa: E402
    BlockMenuCallback,
    BlockUserCallback,
    handle_block_user,
)


class CharacterizeBlockManageBlockUserTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing handle_block_user."""

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.block_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_handle_block_user_unauthenticated_ack_only(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_block_user: AsyncMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-block-anon", data="block_user:99:block")
        callback_data = BlockUserCallback(user_id=99, action="block")

        await handle_block_user(callback, callback_data, None)

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        mock_reject_delegated_block_management.assert_not_awaited()
        mock_async_session_local.assert_not_called()
        mock_block_user.assert_not_awaited()
        mock_get_block_status.assert_not_awaited()
        mock_safe_edit_text.assert_not_awaited()

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.block_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_handle_block_user_authenticated_service_rejection(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_block_user: AsyncMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-block-reject", data="block_user:99:block")
        callback_data = BlockUserCallback(user_id=99, action="block")
        user = SimpleNamespace(id=42)

        sentinel_session = SimpleNamespace(id="sentinel-session-block")
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock()
        session_cm.__aexit__ = AsyncMock()
        mock_async_session_local.return_value = session_cm

        flow_order: list[str] = []

        async def record_reject(cb, u):
            flow_order.append("reject_delegated_block_management")
            return False

        async def record_enter():
            flow_order.append("session_enter")
            return sentinel_session

        async def record_exit(*args):
            flow_order.append("session_exit")
            return False

        async def record_block_user(db, blocker_id, blocked_id):
            flow_order.append("block_user")
            return False, "این کاربر قبلاً مسدود شده است."

        async def record_answer(cb, message=None, show_alert=False):
            flow_order.append("answer_callback_query_via_runtime")

        mock_reject_delegated_block_management.side_effect = record_reject
        session_cm.__aenter__.side_effect = record_enter
        session_cm.__aexit__.side_effect = record_exit
        mock_block_user.side_effect = record_block_user
        mock_answer_callback_query_via_runtime.side_effect = record_answer

        await handle_block_user(callback, callback_data, user)

        self.assertEqual(
            flow_order,
            [
                "reject_delegated_block_management",
                "session_enter",
                "block_user",
                "session_exit",
                "answer_callback_query_via_runtime",
            ],
        )

        mock_reject_delegated_block_management.assert_awaited_once_with(callback, user)
        mock_async_session_local.assert_called_once_with()
        session_cm.__aenter__.assert_awaited_once_with()
        session_cm.__aexit__.assert_awaited_once_with(None, None, None)
        mock_block_user.assert_awaited_once_with(sentinel_session, 42, 99)
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(
            callback,
            "این کاربر قبلاً مسدود شده است.",
            show_alert=True,
        )
        mock_get_block_status.assert_not_awaited()
        mock_safe_edit_text.assert_not_awaited()

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.block_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_handle_block_user_authenticated_success_refresh_and_menu(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_block_user: AsyncMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-block-success", data="block_user:99:block")
        callback_data = BlockUserCallback(user_id=99, action="block")
        user = SimpleNamespace(id=42)
        status_fixture = {
            "can_block": True,
            "current_blocked": 2,
            "max_blocked": 4,
            "remaining": 2,
        }

        sentinel_session_1 = SimpleNamespace(id="sentinel-session-block")
        sentinel_session_2 = SimpleNamespace(id="sentinel-session-status")

        session_cm_1 = MagicMock()
        session_cm_1.__aenter__ = AsyncMock()
        session_cm_1.__aexit__ = AsyncMock()

        session_cm_2 = MagicMock()
        session_cm_2.__aenter__ = AsyncMock()
        session_cm_2.__aexit__ = AsyncMock()

        mock_async_session_local.side_effect = [session_cm_1, session_cm_2]

        flow_order: list[str] = []

        async def record_reject(cb, u):
            flow_order.append("reject_delegated_block_management")
            return False

        async def record_enter_1():
            flow_order.append("session_1_enter")
            return sentinel_session_1

        async def record_exit_1(*args):
            flow_order.append("session_1_exit")
            return False

        async def record_block_user(db, blocker_id, blocked_id):
            flow_order.append("block_user")
            return True, "✅ کاربر با موفقیت مسدود شد."

        async def record_answer(cb, message=None, show_alert=False):
            flow_order.append("answer_callback_query_via_runtime")

        async def record_enter_2():
            flow_order.append("session_2_enter")
            return sentinel_session_2

        async def record_exit_2(*args):
            flow_order.append("session_2_exit")
            return False

        async def record_get_block_status(db, user_id):
            flow_order.append("get_block_status")
            return status_fixture

        async def record_safe_edit_text(*args, **kwargs):
            flow_order.append("safe_edit_text")

        mock_reject_delegated_block_management.side_effect = record_reject
        session_cm_1.__aenter__.side_effect = record_enter_1
        session_cm_1.__aexit__.side_effect = record_exit_1
        mock_block_user.side_effect = record_block_user
        mock_answer_callback_query_via_runtime.side_effect = record_answer
        session_cm_2.__aenter__.side_effect = record_enter_2
        session_cm_2.__aexit__.side_effect = record_exit_2
        mock_get_block_status.side_effect = record_get_block_status
        mock_safe_edit_text.side_effect = record_safe_edit_text

        await handle_block_user(callback, callback_data, user)

        self.assertEqual(
            flow_order,
            [
                "reject_delegated_block_management",
                "session_1_enter",
                "block_user",
                "session_1_exit",
                "answer_callback_query_via_runtime",
                "session_2_enter",
                "get_block_status",
                "session_2_exit",
                "safe_edit_text",
            ],
        )

        self.assertEqual(mock_async_session_local.call_count, 2)
        mock_async_session_local.assert_has_calls([call(), call()])
        session_cm_1.__aenter__.assert_awaited_once_with()
        session_cm_1.__aexit__.assert_awaited_once_with(None, None, None)
        session_cm_2.__aenter__.assert_awaited_once_with()
        session_cm_2.__aexit__.assert_awaited_once_with(None, None, None)

        mock_reject_delegated_block_management.assert_awaited_once_with(callback, user)
        mock_block_user.assert_awaited_once_with(sentinel_session_1, 42, 99)
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(
            callback,
            "✅ کاربر با موفقیت مسدود شد.",
            show_alert=True,
        )
        mock_get_block_status.assert_awaited_once_with(sentinel_session_2, 42)

        mock_safe_edit_text.assert_awaited_once()
        call_args, call_kwargs = mock_safe_edit_text.await_args
        self.assertIs(call_args[0], callback)
        self.assertIs(call_args[1], user)

        zwnj = chr(0x200C)
        expected_text = (
            "🚫 **مدیریت کاربران مسدود**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "✅ قابلیت مسدود کردن: فعال\n"
            "📊 مسدود شده: 2 از 4\n"
            f"💡 باقی{zwnj}مانده: 2\n"
        )
        self.assertEqual(call_args[2], expected_text)
        self.assertEqual(call_kwargs.get("source_key"), "block-after-block")
        self.assertEqual(call_kwargs.get("parse_mode"), "Markdown")

        reply_markup = call_kwargs.get("reply_markup")
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 3)

        row0 = reply_markup.inline_keyboard[0]
        self.assertEqual(len(row0), 1)
        self.assertEqual(row0[0].text, "📋 لیست کاربران مسدود")
        self.assertEqual(row0[0].callback_data, "block_menu:list")
        self.assertEqual(
            row0[0].callback_data,
            BlockMenuCallback(action="list").pack(),
        )

        row1 = reply_markup.inline_keyboard[1]
        self.assertEqual(len(row1), 1)
        self.assertEqual(row1[0].text, "🔍 جستجو و مسدود کردن")
        self.assertEqual(row1[0].callback_data, "block_menu:search")
        self.assertEqual(
            row1[0].callback_data,
            BlockMenuCallback(action="search").pack(),
        )

        row2 = reply_markup.inline_keyboard[2]
        self.assertEqual(len(row2), 1)
        self.assertEqual(row2[0].text, "🔙 بازگشت به پنل کاربر")
        self.assertEqual(row2[0].callback_data, "block_menu:panel")
        self.assertEqual(
            row2[0].callback_data,
            BlockMenuCallback(action="panel").pack(),
        )


if __name__ == "__main__":
    unittest.main()
