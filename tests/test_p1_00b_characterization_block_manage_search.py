"""Direct-characterization tests for bot.handlers.block_manage handle_search_query.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-IDENTITY-MESSAGE-BLOCK-MANAGE-HANDLE-SEARCH-QUERY-L314

This characterization unit does not update registries, reduce or close blockers or seeds, or close the Stage.
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
    handle_search_query,
)
from core.services.block_service import (  # noqa: E402
    BLOCK_STATUS_REASON_CUSTOMER_DELEGATED,
)


class CharacterizeBlockManageSearchTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization tests for handle_search_query."""

    @patch("bot.handlers.block_manage.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.search_users_for_block", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    async def test_handle_search_query_unauthenticated_returns_early(
        self,
        mock_async_session_local: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_search_users_for_block: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        # message without a text attribute to verify no access happens before unauthenticated return
        message = SimpleNamespace()
        state = MagicMock()
        state.clear = AsyncMock()

        await handle_search_query(message, state, None)

        state.clear.assert_not_awaited()
        mock_async_session_local.assert_not_called()
        mock_get_block_status.assert_not_awaited()
        mock_search_users_for_block.assert_not_awaited()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()

    @patch("bot.handlers.block_manage.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.search_users_for_block", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    async def test_handle_search_query_authenticated_short_query_stripped(
        self,
        mock_async_session_local: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_search_users_for_block: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        # query with surrounding whitespace: 5 characters total, stripped length is 1 (< 2)
        message = SimpleNamespace(text="  a  ")
        user = SimpleNamespace(id=42)
        state = MagicMock()
        state.clear = AsyncMock()

        await handle_search_query(message, state, user)

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            "❌ حداقل 2 کاراکتر وارد کنید.",
            source_key="block-search-short",
        )
        state.clear.assert_not_awaited()
        mock_async_session_local.assert_not_called()
        mock_get_block_status.assert_not_awaited()
        mock_search_users_for_block.assert_not_awaited()

    @patch("bot.handlers.block_manage.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.search_users_for_block", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    async def test_handle_search_query_delegated_rejection_flow(
        self,
        mock_async_session_local: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_search_users_for_block: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        message = SimpleNamespace(text="  target_query  ")
        user = SimpleNamespace(id=42)
        state = MagicMock()
        state.clear = AsyncMock()

        custom_reason = "مدیریت مسدودسازی توسط مشتری انجام میشود."
        status_fixture = {
            "reason_code": BLOCK_STATUS_REASON_CUSTOMER_DELEGATED,
            "reason_message": custom_reason,
        }

        status_session = SimpleNamespace(id="status-session-delegated")
        status_session_cm = MagicMock()
        status_session_cm.__aenter__ = AsyncMock()
        status_session_cm.__aexit__ = AsyncMock()
        mock_async_session_local.return_value = status_session_cm

        flow_order: list[str] = []

        async def record_status_enter():
            flow_order.append("status_session_enter")
            return status_session

        async def record_status_exit(*args):
            flow_order.append("status_session_exit")
            return False

        async def record_get_block_status(db, user_id):
            flow_order.append("get_block_status")
            return status_fixture

        async def record_answer(*args, **kwargs):
            flow_order.append("answer_incoming_message_via_runtime")

        async def record_clear():
            flow_order.append("state_clear")

        status_session_cm.__aenter__.side_effect = record_status_enter
        status_session_cm.__aexit__.side_effect = record_status_exit
        mock_get_block_status.side_effect = record_get_block_status
        mock_answer_incoming_message_via_runtime.side_effect = record_answer
        state.clear.side_effect = record_clear

        await handle_search_query(message, state, user)

        self.assertEqual(
            flow_order,
            [
                "status_session_enter",
                "get_block_status",
                "status_session_exit",
                "answer_incoming_message_via_runtime",
                "state_clear",
            ],
        )

        mock_async_session_local.assert_called_once_with()
        status_session_cm.__aenter__.assert_awaited_once_with()
        status_session_cm.__aexit__.assert_awaited_once_with(None, None, None)
        mock_get_block_status.assert_awaited_once_with(status_session, 42)

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            f"❌ {custom_reason}",
            source_key="block-delegated-rejection",
        )
        state.clear.assert_awaited_once_with()
        mock_search_users_for_block.assert_not_awaited()

    @patch("bot.handlers.block_manage.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.search_users_for_block", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    async def test_handle_search_query_empty_results_flow(
        self,
        mock_async_session_local: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_search_users_for_block: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        message = SimpleNamespace(text="  target_user  ")
        user = SimpleNamespace(id=42)
        state = MagicMock()
        state.clear = AsyncMock()

        status_fixture = {"can_block": True, "reason_code": None}
        status_session = SimpleNamespace(id="status-session-empty")
        search_session = SimpleNamespace(id="search-session-empty")

        status_session_cm = MagicMock()
        status_session_cm.__aenter__ = AsyncMock()
        status_session_cm.__aexit__ = AsyncMock()

        search_session_cm = MagicMock()
        search_session_cm.__aenter__ = AsyncMock()
        search_session_cm.__aexit__ = AsyncMock()

        mock_async_session_local.side_effect = [status_session_cm, search_session_cm]

        flow_order: list[str] = []

        async def record_status_enter():
            flow_order.append("status_session_enter")
            return status_session

        async def record_status_exit(*args):
            flow_order.append("status_session_exit")
            return False

        async def record_get_block_status(db, user_id):
            flow_order.append("get_block_status")
            return status_fixture

        async def record_search_enter():
            flow_order.append("search_session_enter")
            return search_session

        async def record_search_exit(*args):
            flow_order.append("search_session_exit")
            return False

        async def record_search_users(db, query, current_user_id, limit=10):
            flow_order.append("search_users_for_block")
            return []

        async def record_answer(*args, **kwargs):
            flow_order.append("answer_incoming_message_via_runtime")

        status_session_cm.__aenter__.side_effect = record_status_enter
        status_session_cm.__aexit__.side_effect = record_status_exit
        search_session_cm.__aenter__.side_effect = record_search_enter
        search_session_cm.__aexit__.side_effect = record_search_exit
        mock_get_block_status.side_effect = record_get_block_status
        mock_search_users_for_block.side_effect = record_search_users
        mock_answer_incoming_message_via_runtime.side_effect = record_answer

        await handle_search_query(message, state, user)

        self.assertEqual(
            flow_order,
            [
                "status_session_enter",
                "get_block_status",
                "status_session_exit",
                "search_session_enter",
                "search_users_for_block",
                "search_session_exit",
                "answer_incoming_message_via_runtime",
            ],
        )

        self.assertEqual(mock_async_session_local.call_args_list, [call(), call()])
        status_session_cm.__aenter__.assert_awaited_once_with()
        status_session_cm.__aexit__.assert_awaited_once_with(None, None, None)
        search_session_cm.__aenter__.assert_awaited_once_with()
        search_session_cm.__aexit__.assert_awaited_once_with(None, None, None)

        mock_get_block_status.assert_awaited_once_with(status_session, 42)
        mock_search_users_for_block.assert_awaited_once_with(
            search_session,
            "target_user",
            42,
            limit=10,
        )
        state.clear.assert_not_awaited()

        mock_answer_incoming_message_via_runtime.assert_awaited_once()
        call_args, call_kwargs = mock_answer_incoming_message_via_runtime.await_args
        self.assertEqual(
            call_args,
            (message, user, "❌ کاربری یافت نشد.\nدوباره جستجو کنید:"),
        )
        self.assertIs(call_args[0], message)
        self.assertIs(call_args[1], user)

        self.assertEqual(set(call_kwargs.keys()), {"source_key", "reply_markup"})
        self.assertEqual(call_kwargs["source_key"], "block-search-empty")

        reply_markup = call_kwargs["reply_markup"]
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 1)
        row0 = reply_markup.inline_keyboard[0]
        self.assertEqual(len(row0), 1)
        self.assertEqual(row0[0].text, "🔙 بازگشت")
        self.assertEqual(row0[0].callback_data, "block_menu:back")
        self.assertEqual(row0[0].callback_data, BlockMenuCallback(action="back").pack())

    @patch("bot.handlers.block_manage.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.search_users_for_block", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.get_block_status", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    async def test_handle_search_query_populated_results_flow(
        self,
        mock_async_session_local: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_search_users_for_block: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        message = SimpleNamespace(text="  search_term  ")
        user = SimpleNamespace(id=42)
        state = MagicMock()
        state.clear = AsyncMock()

        status_fixture = {"can_block": True, "reason_code": None}
        results_fixture = [
            {"id": 101, "account_name": "user_unblocked", "is_blocked": False},
            {"id": 102, "account_name": "user_blocked", "is_blocked": True},
        ]

        status_session = SimpleNamespace(id="status-session-populated")
        search_session = SimpleNamespace(id="search-session-populated")

        status_session_cm = MagicMock()
        status_session_cm.__aenter__ = AsyncMock()
        status_session_cm.__aexit__ = AsyncMock()

        search_session_cm = MagicMock()
        search_session_cm.__aenter__ = AsyncMock()
        search_session_cm.__aexit__ = AsyncMock()

        mock_async_session_local.side_effect = [status_session_cm, search_session_cm]

        flow_order: list[str] = []

        async def record_status_enter():
            flow_order.append("status_session_enter")
            return status_session

        async def record_status_exit(*args):
            flow_order.append("status_session_exit")
            return False

        async def record_get_block_status(db, user_id):
            flow_order.append("get_block_status")
            return status_fixture

        async def record_search_enter():
            flow_order.append("search_session_enter")
            return search_session

        async def record_search_exit(*args):
            flow_order.append("search_session_exit")
            return False

        async def record_search_users(db, query, current_user_id, limit=10):
            flow_order.append("search_users_for_block")
            return results_fixture

        async def record_state_clear():
            flow_order.append("state_clear")

        async def record_answer(*args, **kwargs):
            flow_order.append("answer_incoming_message_via_runtime")

        status_session_cm.__aenter__.side_effect = record_status_enter
        status_session_cm.__aexit__.side_effect = record_status_exit
        search_session_cm.__aenter__.side_effect = record_search_enter
        search_session_cm.__aexit__.side_effect = record_search_exit
        mock_get_block_status.side_effect = record_get_block_status
        mock_search_users_for_block.side_effect = record_search_users
        state.clear.side_effect = record_state_clear
        mock_answer_incoming_message_via_runtime.side_effect = record_answer

        await handle_search_query(message, state, user)

        self.assertEqual(
            flow_order,
            [
                "status_session_enter",
                "get_block_status",
                "status_session_exit",
                "search_session_enter",
                "search_users_for_block",
                "search_session_exit",
                "state_clear",
                "answer_incoming_message_via_runtime",
            ],
        )

        self.assertEqual(mock_async_session_local.call_args_list, [call(), call()])
        status_session_cm.__aenter__.assert_awaited_once_with()
        status_session_cm.__aexit__.assert_awaited_once_with(None, None, None)
        search_session_cm.__aenter__.assert_awaited_once_with()
        search_session_cm.__aexit__.assert_awaited_once_with(None, None, None)

        mock_get_block_status.assert_awaited_once_with(status_session, 42)
        mock_search_users_for_block.assert_awaited_once_with(
            search_session,
            "search_term",
            42,
            limit=10,
        )
        state.clear.assert_awaited_once_with()

        mock_answer_incoming_message_via_runtime.assert_awaited_once()
        call_args, call_kwargs = mock_answer_incoming_message_via_runtime.await_args

        expected_text = (
            "🔍 **نتایج جستجو**\n\n"
            "🚫 = کلیک برای مسدود کردن\n"
            "✅ = کلیک برای رفع مسدودیت\n"
        )
        self.assertEqual(call_args, (message, user, expected_text))
        self.assertIs(call_args[0], message)
        self.assertIs(call_args[1], user)

        self.assertEqual(set(call_kwargs.keys()), {"source_key", "parse_mode", "reply_markup"})
        self.assertEqual(call_kwargs["source_key"], "block-search-results")
        self.assertEqual(call_kwargs["parse_mode"], "Markdown")

        reply_markup = call_kwargs["reply_markup"]
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 3)

        row0 = reply_markup.inline_keyboard[0]
        self.assertEqual(len(row0), 1)
        self.assertEqual(row0[0].text, "🚫 user_unblocked")
        self.assertEqual(row0[0].callback_data, "block_user:101:block")
        self.assertEqual(
            row0[0].callback_data,
            BlockUserCallback(user_id=101, action="block").pack(),
        )

        row1 = reply_markup.inline_keyboard[1]
        self.assertEqual(len(row1), 1)
        self.assertEqual(row1[0].text, "✅ user_blocked (مسدود)")
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


if __name__ == "__main__":
    unittest.main()
