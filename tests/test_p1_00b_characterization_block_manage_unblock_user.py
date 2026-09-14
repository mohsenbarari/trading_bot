"""Direct-characterization tests for bot.handlers.block_manage handle_unblock_user.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-IDENTITY-CALLBACK-QUERY-BLOCK-MANAGE-HANDLE-UNBLOCK-USER-L408

This partial unit does not update registries, reduce blockers, close seeds, or close the Stage.
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
    handle_unblock_user,
)


class CharacterizeBlockManageUnblockUserTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing handle_unblock_user."""

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_blocked_users", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.unblock_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_handle_unblock_user_unauthenticated_ack_only(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_get_blocked_users: AsyncMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-unblock-anon", data="block_user:99:unblock")
        callback_data = BlockUserCallback(user_id=99, action="unblock")

        await handle_unblock_user(callback, callback_data, None)

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        mock_reject_delegated_block_management.assert_not_awaited()
        mock_async_session_local.assert_not_called()
        mock_unblock_user.assert_not_awaited()
        mock_get_blocked_users.assert_not_awaited()
        mock_get_block_status.assert_not_awaited()
        mock_safe_edit_text.assert_not_awaited()

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_blocked_users", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.unblock_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_handle_unblock_user_authenticated_service_rejection(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_get_blocked_users: AsyncMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-unblock-reject", data="block_user:99:unblock")
        callback_data = BlockUserCallback(user_id=99, action="unblock")
        user = SimpleNamespace(id=42)
        rejection_message = "این کاربر مسدود نشده است."

        sentinel_session = SimpleNamespace(id="sentinel-session-unblock")
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

        async def record_unblock_user(db, blocker_id, blocked_id):
            flow_order.append("unblock_user")
            return False, rejection_message

        async def record_answer(cb, message=None, show_alert=False):
            flow_order.append("answer_callback_query_via_runtime")

        mock_reject_delegated_block_management.side_effect = record_reject
        session_cm.__aenter__.side_effect = record_enter
        session_cm.__aexit__.side_effect = record_exit
        mock_unblock_user.side_effect = record_unblock_user
        mock_answer_callback_query_via_runtime.side_effect = record_answer

        await handle_unblock_user(callback, callback_data, user)

        self.assertEqual(
            flow_order,
            [
                "reject_delegated_block_management",
                "session_enter",
                "unblock_user",
                "session_exit",
                "answer_callback_query_via_runtime",
            ],
        )

        mock_reject_delegated_block_management.assert_awaited_once_with(callback, user)
        mock_async_session_local.assert_called_once_with()
        session_cm.__aenter__.assert_awaited_once_with()
        session_cm.__aexit__.assert_awaited_once_with(None, None, None)
        mock_unblock_user.assert_awaited_once_with(sentinel_session, 42, 99)
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(
            callback,
            rejection_message,
            show_alert=True,
        )
        mock_get_blocked_users.assert_not_awaited()
        mock_get_block_status.assert_not_awaited()
        mock_safe_edit_text.assert_not_awaited()

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_blocked_users", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.unblock_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_handle_unblock_user_authenticated_success_refreshes_populated_list(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_get_blocked_users: AsyncMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-unblock-success-list", data="block_user:99:unblock")
        callback_data = BlockUserCallback(user_id=99, action="unblock")
        user = SimpleNamespace(id=42)
        success_message = "✅ مسدودیت کاربر برداشته شد."
        blocked_fixture = [
            {"id": 101, "account_name": "user_alpha"},
            {"id": 102, "account_name": "user_beta"},
        ]

        sentinel_session_1 = SimpleNamespace(id="sentinel-session-unblock")
        sentinel_session_2 = SimpleNamespace(id="sentinel-session-list")

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

        async def record_unblock_user(db, blocker_id, blocked_id):
            flow_order.append("unblock_user")
            return True, success_message

        async def record_answer(cb, message=None, show_alert=False):
            flow_order.append("answer_callback_query_via_runtime")

        async def record_enter_2():
            flow_order.append("session_2_enter")
            return sentinel_session_2

        async def record_exit_2(*args):
            flow_order.append("session_2_exit")
            return False

        async def record_get_blocked_users(db, user_id):
            flow_order.append("get_blocked_users")
            return blocked_fixture

        async def record_safe_edit_text(*args, **kwargs):
            flow_order.append("safe_edit_text")

        mock_reject_delegated_block_management.side_effect = record_reject
        session_cm_1.__aenter__.side_effect = record_enter_1
        session_cm_1.__aexit__.side_effect = record_exit_1
        mock_unblock_user.side_effect = record_unblock_user
        mock_answer_callback_query_via_runtime.side_effect = record_answer
        session_cm_2.__aenter__.side_effect = record_enter_2
        session_cm_2.__aexit__.side_effect = record_exit_2
        mock_get_blocked_users.side_effect = record_get_blocked_users
        mock_safe_edit_text.side_effect = record_safe_edit_text

        await handle_unblock_user(callback, callback_data, user)

        self.assertEqual(
            flow_order,
            [
                "reject_delegated_block_management",
                "session_1_enter",
                "unblock_user",
                "session_1_exit",
                "answer_callback_query_via_runtime",
                "session_2_enter",
                "get_blocked_users",
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
        mock_unblock_user.assert_awaited_once_with(sentinel_session_1, 42, 99)
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(
            callback,
            success_message,
            show_alert=True,
        )
        mock_get_blocked_users.assert_awaited_once_with(sentinel_session_2, 42)
        mock_get_block_status.assert_not_awaited()

        mock_safe_edit_text.assert_awaited_once()
        call_args, call_kwargs = mock_safe_edit_text.await_args
        self.assertIs(call_args[0], callback)
        self.assertIs(call_args[1], user)

        expected_text = (
            "📋 **کاربران مسدود شده**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "برای رفع مسدودیت روی نام کلیک کنید:\n"
        )
        self.assertEqual(call_args[2], expected_text)
        self.assertEqual(call_kwargs.get("source_key"), "block-after-unblock-list")
        self.assertEqual(call_kwargs.get("parse_mode"), "Markdown")

        reply_markup = call_kwargs.get("reply_markup")
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 3)

        row0 = reply_markup.inline_keyboard[0]
        self.assertEqual(len(row0), 1)
        self.assertEqual(row0[0].text, "❌ user_alpha")
        self.assertEqual(row0[0].callback_data, "block_user:101:unblock")
        self.assertEqual(
            row0[0].callback_data,
            BlockUserCallback(user_id=101, action="unblock").pack(),
        )

        row1 = reply_markup.inline_keyboard[1]
        self.assertEqual(len(row1), 1)
        self.assertEqual(row1[0].text, "❌ user_beta")
        self.assertEqual(row1[0].callback_data, "block_user:102:unblock")
        self.assertEqual(
            row1[0].callback_data,
            BlockUserCallback(user_id=102, action="unblock").pack(),
        )

        row2 = reply_markup.inline_keyboard[2]
        self.assertEqual(len(row2), 1)
        self.assertEqual(row2[0].text, "🔙 بازگشت")
        self.assertEqual(row2[0].callback_data, "block_menu:back")
        self.assertEqual(
            row2[0].callback_data,
            BlockMenuCallback(action="back").pack(),
        )

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_blocked_users", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.unblock_user", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_handle_unblock_user_authenticated_success_refreshes_empty_list_to_menu(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_get_blocked_users: AsyncMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-unblock-success-empty", data="block_user:99:unblock")
        callback_data = BlockUserCallback(user_id=99, action="unblock")
        user = SimpleNamespace(id=42)
        success_message = "✅ مسدودیت کاربر برداشته شد."
        status_fixture = {
            "can_block": True,
            "current_blocked": 0,
            "max_blocked": 4,
            "remaining": 4,
        }

        sentinel_session_1 = SimpleNamespace(id="sentinel-session-unblock")
        sentinel_session_2 = SimpleNamespace(id="sentinel-session-list")
        sentinel_session_3 = SimpleNamespace(id="sentinel-session-status")

        session_cm_1 = MagicMock()
        session_cm_1.__aenter__ = AsyncMock()
        session_cm_1.__aexit__ = AsyncMock()

        session_cm_2 = MagicMock()
        session_cm_2.__aenter__ = AsyncMock()
        session_cm_2.__aexit__ = AsyncMock()

        session_cm_3 = MagicMock()
        session_cm_3.__aenter__ = AsyncMock()
        session_cm_3.__aexit__ = AsyncMock()

        mock_async_session_local.side_effect = [session_cm_1, session_cm_2, session_cm_3]

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

        async def record_unblock_user(db, blocker_id, blocked_id):
            flow_order.append("unblock_user")
            return True, success_message

        async def record_answer(cb, message=None, show_alert=False):
            flow_order.append("answer_callback_query_via_runtime")

        async def record_enter_2():
            flow_order.append("session_2_enter")
            return sentinel_session_2

        async def record_exit_2(*args):
            flow_order.append("session_2_exit")
            return False

        async def record_get_blocked_users(db, user_id):
            flow_order.append("get_blocked_users")
            return []

        async def record_enter_3():
            flow_order.append("session_3_enter")
            return sentinel_session_3

        async def record_exit_3(*args):
            flow_order.append("session_3_exit")
            return False

        async def record_get_block_status(db, user_id):
            flow_order.append("get_block_status")
            return status_fixture

        async def record_safe_edit_text(*args, **kwargs):
            flow_order.append("safe_edit_text")

        mock_reject_delegated_block_management.side_effect = record_reject
        session_cm_1.__aenter__.side_effect = record_enter_1
        session_cm_1.__aexit__.side_effect = record_exit_1
        mock_unblock_user.side_effect = record_unblock_user
        mock_answer_callback_query_via_runtime.side_effect = record_answer
        session_cm_2.__aenter__.side_effect = record_enter_2
        session_cm_2.__aexit__.side_effect = record_exit_2
        mock_get_blocked_users.side_effect = record_get_blocked_users
        session_cm_3.__aenter__.side_effect = record_enter_3
        session_cm_3.__aexit__.side_effect = record_exit_3
        mock_get_block_status.side_effect = record_get_block_status
        mock_safe_edit_text.side_effect = record_safe_edit_text

        await handle_unblock_user(callback, callback_data, user)

        self.assertEqual(
            flow_order,
            [
                "reject_delegated_block_management",
                "session_1_enter",
                "unblock_user",
                "session_1_exit",
                "answer_callback_query_via_runtime",
                "session_2_enter",
                "get_blocked_users",
                "session_2_exit",
                "session_3_enter",
                "get_block_status",
                "session_3_exit",
                "safe_edit_text",
            ],
        )

        self.assertEqual(mock_async_session_local.call_count, 3)
        mock_async_session_local.assert_has_calls([call(), call(), call()])
        session_cm_1.__aenter__.assert_awaited_once_with()
        session_cm_1.__aexit__.assert_awaited_once_with(None, None, None)
        session_cm_2.__aenter__.assert_awaited_once_with()
        session_cm_2.__aexit__.assert_awaited_once_with(None, None, None)
        session_cm_3.__aenter__.assert_awaited_once_with()
        session_cm_3.__aexit__.assert_awaited_once_with(None, None, None)

        mock_reject_delegated_block_management.assert_awaited_once_with(callback, user)
        mock_unblock_user.assert_awaited_once_with(sentinel_session_1, 42, 99)
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(
            callback,
            success_message,
            show_alert=True,
        )
        mock_get_blocked_users.assert_awaited_once_with(sentinel_session_2, 42)
        mock_get_block_status.assert_awaited_once_with(sentinel_session_3, 42)

        mock_safe_edit_text.assert_awaited_once()
        call_args, call_kwargs = mock_safe_edit_text.await_args
        self.assertIs(call_args[0], callback)
        self.assertIs(call_args[1], user)

        expected_text = (
            "🚫 **مدیریت کاربران مسدود**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "✅ لیست خالی است.\n"
            "📊 ظرفیت: 4\n"
        )
        self.assertEqual(call_args[2], expected_text)
        self.assertEqual(call_kwargs.get("source_key"), "block-after-unblock-empty")
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
