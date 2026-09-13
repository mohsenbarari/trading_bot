"""Direct-characterization tests for bot.handlers.block_manage.start_search.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from aiogram.types import InlineKeyboardMarkup  # noqa: E402
from bot.handlers.block_manage import (  # noqa: E402
    BlockMenuCallback,
    BlockStates,
    start_search,
)


class CharacterizeBlockManageStartSearchTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.block_manage.start_search."""

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_start_search_unauthenticated_ack_only(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-start-search-none", data="block_menu:search")
        state = SimpleNamespace(set_state=AsyncMock())

        await start_search(callback, state, None)

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        mock_reject_delegated_block_management.assert_not_awaited()
        state.set_state.assert_not_awaited()
        mock_safe_edit_text.assert_not_awaited()

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_start_search_delegated_rejection_short_circuits(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-start-search-rej", data="block_menu:search")
        state = SimpleNamespace(set_state=AsyncMock())
        user = SimpleNamespace(id=42)
        mock_reject_delegated_block_management.return_value = True

        await start_search(callback, state, user)

        mock_reject_delegated_block_management.assert_awaited_once_with(callback, user)
        state.set_state.assert_not_awaited()
        mock_safe_edit_text.assert_not_awaited()
        mock_answer_callback_query_via_runtime.assert_not_awaited()

    @patch("bot.handlers.block_manage.safe_edit_text", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.reject_delegated_block_management", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_start_search_authorized_flow(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_reject_delegated_block_management: AsyncMock,
        mock_safe_edit_text: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-start-search-auth", data="block_menu:search")
        user = SimpleNamespace(id=42)
        mock_reject_delegated_block_management.return_value = False

        flow_order: list[str] = []

        async def record_set_state(target_state: object) -> None:
            flow_order.append("set_state")

        async def record_safe_edit_text(*args: object, **kwargs: object) -> None:
            flow_order.append("safe_edit_text")

        async def record_answer(cb: object) -> None:
            flow_order.append("answer_callback_query_via_runtime")

        state = SimpleNamespace(set_state=AsyncMock(side_effect=record_set_state))
        mock_safe_edit_text.side_effect = record_safe_edit_text
        mock_answer_callback_query_via_runtime.side_effect = record_answer

        await start_search(callback, state, user)

        mock_reject_delegated_block_management.assert_awaited_once_with(callback, user)
        state.set_state.assert_awaited_once_with(BlockStates.searching)
        mock_safe_edit_text.assert_awaited_once()
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)

        self.assertEqual(
            flow_order,
            ["set_state", "safe_edit_text", "answer_callback_query_via_runtime"],
        )

        call_args, call_kwargs = mock_safe_edit_text.await_args
        self.assertIs(call_args[0], callback)
        self.assertIs(call_args[1], user)
        expected_prompt = (
            "🔍 **جستجوی کاربر**\n\n"
            "شماره موبایل یا نام کاربری را وارد کنید:\n"
            "(حداقل 2 کاراکتر)"
        )
        self.assertEqual(call_args[2], expected_prompt)
        self.assertEqual(call_kwargs.get("source_key"), "block-search-start")
        self.assertEqual(call_kwargs.get("parse_mode"), "Markdown")

        reply_markup = call_kwargs.get("reply_markup")
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 1)
        row = reply_markup.inline_keyboard[0]
        self.assertEqual(len(row), 1)
        button = row[0]
        self.assertEqual(button.text, "❌ انصراف")
        self.assertEqual(
            button.callback_data,
            BlockMenuCallback(action="back").pack(),
        )


if __name__ == "__main__":
    unittest.main()
