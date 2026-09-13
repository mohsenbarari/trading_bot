"""Direct-characterization tests for bot.handlers.panel.back_to_user_panel_from_customers.

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

from bot.handlers.panel import (  # noqa: E402
    back_to_user_panel_from_customers,
)


class CharacterizePanelBackToUserPanelFromCustomersTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.back_to_user_panel_from_customers."""

    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_back_to_user_panel_from_customers_unauthenticated_ack_only(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-panel-back-anon", data="user_panel_customer:back")

        await back_to_user_panel_from_customers(callback, None)

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        mock_edit_callback_message_via_runtime.assert_not_awaited()

    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_back_to_user_panel_from_customers_authenticated(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(id="cb-panel-back-auth", data="user_panel_customer:back")
        user = SimpleNamespace(id=42, username="sample_user")

        call_order: list[str] = []

        async def record_edit(*args: object, **kwargs: object) -> None:
            call_order.append("edit_callback_message_via_runtime")

        async def record_answer(*args: object, **kwargs: object) -> None:
            call_order.append("answer_callback_query_via_runtime")

        mock_edit_callback_message_via_runtime.side_effect = record_edit
        mock_answer_callback_query_via_runtime.side_effect = record_answer

        await back_to_user_panel_from_customers(callback, user)

        expected_text = "👤 **پنل کاربر**\n\nاز دکمه\u200cهای پایین پیام استفاده کنید."
        mock_edit_callback_message_via_runtime.assert_awaited_once_with(
            callback,
            user,
            expected_text,
            parse_mode="Markdown",
        )
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)

        call_args, call_kwargs = mock_edit_callback_message_via_runtime.await_args
        self.assertIs(call_args[0], callback)
        self.assertIs(call_args[1], user)
        self.assertEqual(call_args[2], expected_text)
        self.assertEqual(call_kwargs.get("parse_mode"), "Markdown")

        answer_call_args, _ = mock_answer_callback_query_via_runtime.await_args
        self.assertIs(answer_call_args[0], callback)

        self.assertEqual(
            call_order,
            ["edit_callback_message_via_runtime", "answer_callback_query_via_runtime"],
        )


if __name__ == "__main__":
    unittest.main()
