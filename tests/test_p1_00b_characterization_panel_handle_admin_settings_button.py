"""Direct-characterization tests for bot.handlers.panel.handle_admin_settings_button.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
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
    handle_admin_settings_button,
)
from core.enums import UserRole  # noqa: E402


class CharacterizePanelHandleAdminSettingsButtonTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.handle_admin_settings_button."""

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    async def test_handle_admin_settings_button_unauthenticated_noop(
        self,
        mock_get_settings_text: AsyncMock,
        mock_get_settings_keyboard: MagicMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        message = SimpleNamespace(message_id=99, text="⚙️ تنظیمات سیستم")
        state = SimpleNamespace(clear=AsyncMock())

        result = await handle_admin_settings_button(message, state, None)

        self.assertIsNone(result)
        state.clear.assert_not_called()
        state.clear.assert_not_awaited()
        mock_get_settings_text.assert_not_called()
        mock_get_settings_text.assert_not_awaited()
        mock_get_settings_keyboard.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    async def test_handle_admin_settings_button_non_super_admin_noop(
        self,
        mock_get_settings_text: AsyncMock,
        mock_get_settings_keyboard: MagicMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        message = SimpleNamespace(message_id=100, text="⚙️ تنظیمات سیستم")
        state = SimpleNamespace(clear=AsyncMock())
        user = SimpleNamespace(id=42, role=UserRole.STANDARD, username="standard_user")

        result = await handle_admin_settings_button(message, state, user)

        self.assertIsNone(result)
        state.clear.assert_not_called()
        state.clear.assert_not_awaited()
        mock_get_settings_text.assert_not_called()
        mock_get_settings_text.assert_not_awaited()
        mock_get_settings_keyboard.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    async def test_handle_admin_settings_button_super_admin_success(
        self,
        mock_get_settings_text: AsyncMock,
        mock_get_settings_keyboard: MagicMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        message = SimpleNamespace(message_id=101, text="⚙️ تنظیمات سیستم")
        user = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, username="super_admin_user")
        state = SimpleNamespace(clear=AsyncMock())

        sentinel_text = "SENTINEL_ADMIN_SETTINGS_TEXT_v1"
        sentinel_keyboard = SimpleNamespace(marker="sentinel_admin_settings_keyboard")

        call_order: list[str] = []

        async def record_clear(*args: object, **kwargs: object) -> None:
            call_order.append("state.clear")

        async def record_get_settings_text(*args: object, **kwargs: object) -> str:
            call_order.append("get_settings_text")
            return sentinel_text

        def record_get_settings_keyboard(*args: object, **kwargs: object) -> object:
            call_order.append("get_settings_keyboard")
            return sentinel_keyboard

        async def record_answer(*args: object, **kwargs: object) -> None:
            call_order.append("answer_incoming_message_via_runtime")

        state.clear.side_effect = record_clear
        mock_get_settings_text.side_effect = record_get_settings_text
        mock_get_settings_keyboard.side_effect = record_get_settings_keyboard
        mock_answer_incoming_message_via_runtime.side_effect = record_answer

        result = await handle_admin_settings_button(message, state, user)

        self.assertIsNone(result)

        state.clear.assert_awaited_once_with()
        mock_get_settings_text.assert_awaited_once_with()
        mock_get_settings_keyboard.assert_called_once_with()
        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            sentinel_text,
            source_key="panel-admin-settings",
            parse_mode="Markdown",
            reply_markup=sentinel_keyboard,
        )

        call_args, call_kwargs = mock_answer_incoming_message_via_runtime.await_args
        self.assertIs(call_args[0], message)
        self.assertIs(call_args[1], user)
        self.assertIs(call_args[2], sentinel_text)
        self.assertEqual(call_kwargs.get("source_key"), "panel-admin-settings")
        self.assertEqual(call_kwargs.get("parse_mode"), "Markdown")
        self.assertIs(call_kwargs.get("reply_markup"), sentinel_keyboard)

        self.assertEqual(
            call_order,
            [
                "state.clear",
                "get_settings_text",
                "get_settings_keyboard",
                "answer_incoming_message_via_runtime",
            ],
        )


if __name__ == "__main__":
    unittest.main()
