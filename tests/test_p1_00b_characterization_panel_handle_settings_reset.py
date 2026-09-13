"""Direct-characterization tests for bot.handlers.panel.handle_settings_reset.

This characterization alone does not update the seed registry,
reduce the canonical blocker, promote evidence, or close P1-00b.
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
from bot.handlers.panel import (  # noqa: E402
    handle_settings_reset,
)
from core.enums import UserRole  # noqa: E402


class CharacterizePanelHandleSettingsResetTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.handle_settings_reset."""

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    async def test_handle_settings_reset_unauthenticated_denied(
        self,
        mock_reject: AsyncMock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_reset_all", id="cb_unauth")

        result = await handle_settings_reset(callback, None)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback, "دسترسی ندارید")
        self.assertEqual(mock_answer_callback.await_args.args, (callback, "دسترسی ندارید"))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})
        mock_reject.assert_not_called()
        mock_reject.assert_not_awaited()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    async def test_handle_settings_reset_non_super_admin_denied(
        self,
        mock_reject: AsyncMock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_reset_all", id="cb_non_super_admin")
        user = SimpleNamespace(id=42, role=UserRole.STANDARD, username="standard_user")

        result = await handle_settings_reset(callback, user)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback, "دسترسی ندارید")
        self.assertEqual(mock_answer_callback.await_args.args, (callback, "دسترسی ندارید"))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})
        mock_reject.assert_not_called()
        mock_reject.assert_not_awaited()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    async def test_handle_settings_reset_super_admin_non_authoritative_short_circuit(
        self,
        mock_reject: AsyncMock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_reset_all", id="cb_non_auth")
        user = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, username="super_admin_user")
        mock_reject.return_value = True

        result = await handle_settings_reset(callback, user)

        self.assertIsNone(result)
        mock_reject.assert_awaited_once_with(callback, "reset")
        self.assertEqual(mock_reject.await_args.args, (callback, "reset"))
        self.assertEqual(mock_reject.await_args.kwargs, {})
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()
        mock_answer_callback.assert_not_called()
        mock_answer_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    async def test_handle_settings_reset_super_admin_authorized_success(
        self,
        mock_reject: AsyncMock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        sentinel_callback = SimpleNamespace(data="settings_reset_all", id="cb_auth_101")
        sentinel_user = SimpleNamespace(id=101, role=UserRole.SUPER_ADMIN, username="super_admin_101")

        call_order: list[str] = []

        async def record_reject(cb: object, op: str) -> bool:
            call_order.append("authority_check")
            return False

        async def record_edit_callback(*args: object, **kwargs: object) -> None:
            call_order.append("edit_callback_message_via_runtime")

        async def record_answer_callback(*args: object, **kwargs: object) -> None:
            call_order.append("answer_callback_query_via_runtime")

        mock_reject.side_effect = record_reject
        mock_edit_callback.side_effect = record_edit_callback
        mock_answer_callback.side_effect = record_answer_callback

        result = await handle_settings_reset(sentinel_callback, sentinel_user)

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "authority_check",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )

        mock_reject.assert_awaited_once_with(sentinel_callback, "reset")
        self.assertEqual(mock_reject.await_args.args, (sentinel_callback, "reset"))
        self.assertEqual(mock_reject.await_args.kwargs, {})

        mock_edit_callback.assert_awaited_once()
        call_args, call_kwargs = mock_edit_callback.await_args
        self.assertEqual(len(call_args), 3)
        self.assertIs(call_args[0], sentinel_callback)
        self.assertIs(call_args[1], sentinel_user)

        expected_warning_text = (
            "⚠️ **هشدار**\n\n"
            "آیا مطمئن هستید که می\u200cخواهید تمام تنظیمات را به مقادیر پیش\u200cفرض بازنشانی کنید؟"
        )
        self.assertEqual(call_args[2], expected_warning_text)
        self.assertEqual(call_kwargs.get("parse_mode"), "Markdown")
        self.assertEqual(set(call_kwargs.keys()), {"parse_mode", "reply_markup"})

        reply_markup = call_kwargs.get("reply_markup")
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 1)
        row = reply_markup.inline_keyboard[0]
        self.assertEqual(len(row), 2)
        self.assertEqual(row[0].text, "✅ بله، بازنشانی کن")
        self.assertEqual(row[0].callback_data, "settings_reset_confirm")
        self.assertEqual(row[1].text, "❌ خیر")
        self.assertEqual(row[1].callback_data, "settings_reset_cancel")

        mock_answer_callback.assert_awaited_once_with(sentinel_callback)
        self.assertEqual(mock_answer_callback.await_args.args, (sentinel_callback,))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
