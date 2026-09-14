"""Direct-characterization tests for bot.handlers.block_manage navigation callbacks.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seeds:
- BOT-IDENTITY-CALLBACK-QUERY-BLOCK-MANAGE-HANDLE-BACK-L466
- BOT-IDENTITY-CALLBACK-QUERY-BLOCK-MANAGE-BACK-TO-USER-PANEL-FROM-BLOCK-MENU-L489

This partial unit does not update registries, reduce or close blockers or seeds, or close the Stage.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from aiogram.types import InlineKeyboardMarkup  # noqa: E402
from bot.handlers.block_manage import (  # noqa: E402
    BlockMenuCallback,
    back_to_user_panel_from_block_menu,
    handle_back,
)


class CharacterizeBlockManageNavigationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing handle_back and back_to_user_panel_from_block_menu."""

    @patch("bot.handlers.block_manage.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_handle_back_unauthenticated_ack_only(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-back-anon", data="block_menu:back")
        state = MagicMock()
        state.clear = AsyncMock()

        await handle_back(callback, state, None)

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        state.clear.assert_not_awaited()
        mock_async_session_local.assert_not_called()
        mock_get_block_status.assert_not_awaited()
        mock_safe_edit_text.assert_not_awaited()
        mock_edit_callback_message_via_runtime.assert_not_awaited()

    @patch("bot.handlers.block_manage.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_back_to_user_panel_from_block_menu_unauthenticated_ack_only(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-panel-anon", data="block_menu:panel")
        state = MagicMock()
        state.clear = AsyncMock()

        await back_to_user_panel_from_block_menu(callback, state, None)

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        state.clear.assert_not_awaited()
        mock_async_session_local.assert_not_called()
        mock_get_block_status.assert_not_awaited()
        mock_safe_edit_text.assert_not_awaited()
        mock_edit_callback_message_via_runtime.assert_not_awaited()

    @patch("bot.handlers.block_manage.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_handle_back_authenticated_flow(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-back-auth", data="block_menu:back")
        user = SimpleNamespace(id=42)
        state = MagicMock()
        state.clear = AsyncMock()
        status_fixture = {
            "can_block": True,
            "current_blocked": 1,
            "max_blocked": 3,
            "remaining": 2,
        }

        sentinel_session = SimpleNamespace(id="sentinel-session-back")
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock()
        session_cm.__aexit__ = AsyncMock()
        mock_async_session_local.return_value = session_cm

        flow_order: list[str] = []

        async def record_clear():
            flow_order.append("state_clear")

        async def record_enter():
            flow_order.append("session_enter")
            return sentinel_session

        async def record_get_block_status(db, user_id):
            flow_order.append("get_block_status")
            return status_fixture

        async def record_exit(*args):
            flow_order.append("session_exit")
            return False

        async def record_safe_edit_text(*args, **kwargs):
            flow_order.append("safe_edit_text")

        async def record_answer(cb):
            flow_order.append("answer_callback_query_via_runtime")

        state.clear.side_effect = record_clear
        session_cm.__aenter__.side_effect = record_enter
        session_cm.__aexit__.side_effect = record_exit
        mock_get_block_status.side_effect = record_get_block_status
        mock_safe_edit_text.side_effect = record_safe_edit_text
        mock_answer_callback_query_via_runtime.side_effect = record_answer

        await handle_back(callback, state, user)

        self.assertEqual(
            flow_order,
            [
                "state_clear",
                "session_enter",
                "get_block_status",
                "session_exit",
                "safe_edit_text",
                "answer_callback_query_via_runtime",
            ],
        )

        state.clear.assert_awaited_once_with()
        mock_async_session_local.assert_called_once_with()
        session_cm.__aenter__.assert_awaited_once_with()
        session_cm.__aexit__.assert_awaited_once_with(None, None, None)
        mock_get_block_status.assert_awaited_once_with(sentinel_session, 42)

        mock_safe_edit_text.assert_awaited_once()
        call_args, call_kwargs = mock_safe_edit_text.await_args
        self.assertIs(call_args[0], callback)
        self.assertIs(call_args[1], user)

        zwnj = chr(0x200C)
        expected_text = (
            "🚫 **مدیریت کاربران مسدود**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "✅ قابلیت مسدود کردن: فعال\n"
            "📊 مسدود شده: 1 از 3\n"
            f"💡 باقی{zwnj}مانده: 2\n"
        )
        self.assertEqual(call_args[2], expected_text)
        self.assertEqual(call_kwargs.get("source_key"), "block-menu-back")
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

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        mock_edit_callback_message_via_runtime.assert_not_awaited()

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_back_to_user_panel_from_block_menu_authenticated_flow(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
        mock_async_session_local: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-panel-auth", data="block_menu:panel")
        user = SimpleNamespace(id=42)
        state = MagicMock()
        state.clear = AsyncMock()

        flow_order: list[str] = []

        async def record_clear():
            flow_order.append("state_clear")

        async def record_edit(*args, **kwargs):
            flow_order.append("edit_callback_message_via_runtime")

        async def record_answer(cb):
            flow_order.append("answer_callback_query_via_runtime")

        state.clear.side_effect = record_clear
        mock_edit_callback_message_via_runtime.side_effect = record_edit
        mock_answer_callback_query_via_runtime.side_effect = record_answer

        await back_to_user_panel_from_block_menu(callback, state, user)

        self.assertEqual(
            flow_order,
            [
                "state_clear",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )

        state.clear.assert_awaited_once_with()
        mock_edit_callback_message_via_runtime.assert_awaited_once()
        call_args, call_kwargs = mock_edit_callback_message_via_runtime.await_args
        self.assertIs(call_args[0], callback)
        self.assertIs(call_args[1], user)

        zwnj = chr(0x200C)
        expected_text = f"👤 **پنل کاربر**\n\nاز دکمه{zwnj}های پایین پیام استفاده کنید."
        self.assertEqual(call_args[2], expected_text)
        self.assertEqual(
            call_kwargs,
            {
                "source_key": "block-panel-back",
                "parse_mode": "Markdown",
            },
        )

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        mock_async_session_local.assert_not_called()
        mock_get_block_status.assert_not_awaited()
        mock_safe_edit_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
