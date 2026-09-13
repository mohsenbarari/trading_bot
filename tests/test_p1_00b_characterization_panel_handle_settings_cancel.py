"""Direct-characterization tests for bot.handlers.panel.handle_settings_cancel.

This is direct characterization only and does not update the executed-seed registry,
reduce the canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import (  # noqa: E402
    handle_settings_cancel,
)
from core.enums import UserRole  # noqa: E402


class CharacterizePanelHandleSettingsCancelTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.handle_settings_cancel."""

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    async def test_handle_settings_cancel_unauthenticated_denied(
        self,
        mock_get_settings_text: AsyncMock,
        mock_get_settings_keyboard: Mock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_cancel_edit", id="cb_unauth")
        state = SimpleNamespace(clear=AsyncMock())

        result = await handle_settings_cancel(callback, state, None)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback)
        self.assertEqual(mock_answer_callback.await_args.args, (callback,))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})
        state.clear.assert_not_called()
        state.clear.assert_not_awaited()
        mock_get_settings_text.assert_not_called()
        mock_get_settings_text.assert_not_awaited()
        mock_get_settings_keyboard.assert_not_called()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    async def test_handle_settings_cancel_non_super_admin_denied(
        self,
        mock_get_settings_text: AsyncMock,
        mock_get_settings_keyboard: Mock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_cancel_edit", id="cb_non_super_admin")
        state = SimpleNamespace(clear=AsyncMock())
        user = SimpleNamespace(id=42, role=UserRole.STANDARD, username="standard_user")

        result = await handle_settings_cancel(callback, state, user)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback)
        self.assertEqual(mock_answer_callback.await_args.args, (callback,))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})
        state.clear.assert_not_called()
        state.clear.assert_not_awaited()
        mock_get_settings_text.assert_not_called()
        mock_get_settings_text.assert_not_awaited()
        mock_get_settings_keyboard.assert_not_called()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    async def test_handle_settings_cancel_super_admin_authorized_success(
        self,
        mock_get_settings_text: AsyncMock,
        mock_get_settings_keyboard: Mock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        sentinel_callback = SimpleNamespace(data="settings_cancel_edit", id="cb_super_admin_101")
        sentinel_user = SimpleNamespace(id=101, role=UserRole.SUPER_ADMIN, username="super_admin_101")
        sentinel_settings_text = "⚙️ **تنظیمات سیستم**\nSENTINEL_TEXT_VERIFICATION"
        sentinel_keyboard = SimpleNamespace(inline_keyboard=[["SENTINEL_KEYBOARD_ROW"]])

        call_order: list[str] = []

        async def record_clear() -> None:
            call_order.append("state.clear")

        async def record_get_settings_text() -> str:
            call_order.append("get_settings_text")
            return sentinel_settings_text

        def record_get_settings_keyboard() -> object:
            call_order.append("get_settings_keyboard")
            return sentinel_keyboard

        async def record_edit_callback(*args: object, **kwargs: object) -> None:
            call_order.append("edit_callback_message_via_runtime")

        async def record_answer_callback(*args: object, **kwargs: object) -> None:
            call_order.append("answer_callback_query_via_runtime")

        state = SimpleNamespace(clear=AsyncMock(side_effect=record_clear))
        mock_get_settings_text.side_effect = record_get_settings_text
        mock_get_settings_keyboard.side_effect = record_get_settings_keyboard
        mock_edit_callback.side_effect = record_edit_callback
        mock_answer_callback.side_effect = record_answer_callback

        result = await handle_settings_cancel(sentinel_callback, state, sentinel_user)

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "state.clear",
                "get_settings_text",
                "get_settings_keyboard",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )

        state.clear.assert_awaited_once_with()
        mock_get_settings_text.assert_awaited_once_with()
        mock_get_settings_keyboard.assert_called_once_with()

        mock_edit_callback.assert_awaited_once()
        call_args, call_kwargs = mock_edit_callback.await_args
        self.assertEqual(len(call_args), 3)
        self.assertIs(call_args[0], sentinel_callback)
        self.assertIs(call_args[1], sentinel_user)
        self.assertIs(call_args[2], sentinel_settings_text)
        self.assertEqual(call_kwargs.get("parse_mode"), "Markdown")
        self.assertIs(call_kwargs.get("reply_markup"), sentinel_keyboard)
        self.assertEqual(set(call_kwargs.keys()), {"parse_mode", "reply_markup"})

        mock_edit_callback.assert_awaited_once_with(
            sentinel_callback,
            sentinel_user,
            sentinel_settings_text,
            parse_mode="Markdown",
            reply_markup=sentinel_keyboard,
        )

        mock_answer_callback.assert_awaited_once_with(sentinel_callback, "انصراف")
        self.assertEqual(mock_answer_callback.await_args.args, (sentinel_callback, "انصراف"))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
