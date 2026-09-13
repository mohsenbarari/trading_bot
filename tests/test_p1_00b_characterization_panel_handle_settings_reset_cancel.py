"""Direct-characterization tests for bot.handlers.panel.handle_settings_reset_cancel.

This characterization alone does not update the seed registry,
reduce the canonical blocker, promote evidence, or close P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import (  # noqa: E402
    handle_settings_reset_cancel,
)
from core.enums import UserRole  # noqa: E402


class CharacterizePanelHandleSettingsResetCancelTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.handle_settings_reset_cancel."""

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_settings_reset_cancel_unauthenticated_denied(
        self,
        mock_edit_callback: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_get_text: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_reset_cancel", id="cb_unauth")

        result = await handle_settings_reset_cancel(callback, None)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback)
        self.assertEqual(mock_answer_callback.await_args.args, (callback,))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})

        mock_get_text.assert_not_called()
        mock_get_text.assert_not_awaited()
        mock_get_keyboard.assert_not_called()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_settings_reset_cancel_non_super_admin_denied(
        self,
        mock_edit_callback: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_get_text: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_reset_cancel", id="cb_non_super_admin")
        user = SimpleNamespace(id=42, role=UserRole.STANDARD, username="standard_user")

        result = await handle_settings_reset_cancel(callback, user)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback)
        self.assertEqual(mock_answer_callback.await_args.args, (callback,))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})

        mock_get_text.assert_not_called()
        mock_get_text.assert_not_awaited()
        mock_get_keyboard.assert_not_called()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_settings_reset_cancel_super_admin_authorized_success(
        self,
        mock_edit_callback: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_get_text: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        sentinel_callback = SimpleNamespace(
            data="settings_reset_cancel", id="cb_super_admin_cancel"
        )
        sentinel_user = SimpleNamespace(
            id=7, role=UserRole.SUPER_ADMIN, username="super_admin_cancel"
        )
        sentinel_text = "SENTINEL_RENDERED_SETTINGS_TEXT_CANCEL"
        sentinel_keyboard = SimpleNamespace(
            inline_keyboard=[["sentinel_keyboard_cancel"]]
        )

        call_order: list[str] = []

        async def record_get_text() -> str:
            call_order.append("get_settings_text")
            return sentinel_text

        def record_get_keyboard() -> object:
            call_order.append("get_settings_keyboard")
            return sentinel_keyboard

        async def record_edit_callback(*args: object, **kwargs: object) -> None:
            call_order.append("edit_callback_message_via_runtime")

        async def record_answer(*args: object, **kwargs: object) -> None:
            call_order.append("answer_callback_query_via_runtime")

        mock_get_text.side_effect = record_get_text
        mock_get_keyboard.side_effect = record_get_keyboard
        mock_edit_callback.side_effect = record_edit_callback
        mock_answer_callback.side_effect = record_answer

        result = await handle_settings_reset_cancel(sentinel_callback, sentinel_user)

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "get_settings_text",
                "get_settings_keyboard",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )

        mock_get_text.assert_awaited_once_with()
        self.assertEqual(mock_get_text.await_args.args, ())
        self.assertEqual(mock_get_text.await_args.kwargs, {})

        mock_get_keyboard.assert_called_once_with()
        self.assertEqual(mock_get_keyboard.call_args.args, ())
        self.assertEqual(mock_get_keyboard.call_args.kwargs, {})

        mock_edit_callback.assert_awaited_once()
        call_args, call_kwargs = mock_edit_callback.await_args
        self.assertEqual(len(call_args), 3)
        self.assertIs(call_args[0], sentinel_callback)
        self.assertIs(call_args[1], sentinel_user)
        self.assertIs(call_args[2], sentinel_text)
        self.assertEqual(
            call_kwargs,
            {"parse_mode": "Markdown", "reply_markup": sentinel_keyboard},
        )

        mock_answer_callback.assert_awaited_once_with(sentinel_callback, "لغو شد")
        self.assertEqual(
            mock_answer_callback.await_args.args,
            (sentinel_callback, "لغو شد"),
        )
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
