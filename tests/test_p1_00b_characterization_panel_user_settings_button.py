"""Direct-characterization tests for bot.handlers.panel.handle_user_settings_button.

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

from aiogram.types import InlineKeyboardMarkup  # noqa: E402
from bot.handlers.block_manage import BlockMenuCallback  # noqa: E402
from bot.handlers.panel import handle_user_settings_button  # noqa: E402


class CharacterizePanelUserSettingsButtonTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.handle_user_settings_button."""

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("core.services.block_service.get_block_status", new_callable=AsyncMock)
    @patch("core.db.AsyncSessionLocal")
    async def test_handle_user_settings_button_no_user_early_returns(
        self,
        mock_session_maker: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        message = SimpleNamespace(text="⚙️ تنظیمات کاربری", answer=AsyncMock())
        state = SimpleNamespace()

        await handle_user_settings_button(message, state, None)

        mock_session_maker.assert_not_called()
        mock_get_block_status.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_called()
        message.answer.assert_not_called()

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("core.services.block_service.get_block_status", new_callable=AsyncMock)
    @patch("core.db.AsyncSessionLocal")
    async def test_handle_user_settings_button_eligible_user_can_block_true(
        self,
        mock_session_maker: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        sentinel_session = AsyncMock(name="sentinel_async_session_enabled")
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=sentinel_session)
        session_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session_maker.return_value = session_cm

        user = SimpleNamespace(id=98765)
        message = SimpleNamespace(text="⚙️ تنظیمات کاربری", answer=AsyncMock())
        state = SimpleNamespace()

        block_status_payload = {
            "can_block": True,
            "max_blocked": 25,
            "current_blocked": 4,
            "remaining": 21,
            "blocked_users": [],
        }
        mock_get_block_status.return_value = block_status_payload

        await handle_user_settings_button(message, state, user)

        mock_session_maker.assert_called_once_with()
        session_cm.__aenter__.assert_awaited_once_with()
        session_cm.__aexit__.assert_awaited_once()

        mock_get_block_status.assert_awaited_once_with(sentinel_session, user.id)
        self.assertIs(mock_get_block_status.await_args[0][0], sentinel_session)
        self.assertEqual(mock_get_block_status.await_args[0][1], user.id)

        mock_answer_incoming_message_via_runtime.assert_awaited_once()
        call_args, call_kwargs = mock_answer_incoming_message_via_runtime.await_args

        self.assertEqual(len(call_args), 3)
        self.assertIs(call_args[0], message)
        self.assertIs(call_args[1], user)

        expected_text = (
            "⚙️ **تنظیمات کاربری**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🚫 **مدیریت بلاک:**\n"
            "   • وضعیت: ✅ فعال\n"
            "   • مسدود شده: 4 از 25\n"
        )
        self.assertEqual(call_args[2], expected_text)
        self.assertEqual(
            set(call_kwargs.keys()),
            {"source_key", "parse_mode", "reply_markup"},
        )
        self.assertEqual(call_kwargs["source_key"], "panel-user-settings")
        self.assertEqual(call_kwargs["parse_mode"], "Markdown")

        reply_markup = call_kwargs["reply_markup"]
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 1)
        row = reply_markup.inline_keyboard[0]
        self.assertEqual(len(row), 1)
        button = row[0]
        self.assertEqual(button.text, "🚫 مدیریت کاربران مسدود")
        self.assertEqual(
            button.callback_data,
            BlockMenuCallback(action="main").pack(),
        )
        self.assertEqual(button.callback_data, "block_menu:main")

        message.answer.assert_not_called()

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("core.services.block_service.get_block_status", new_callable=AsyncMock)
    @patch("core.db.AsyncSessionLocal")
    async def test_handle_user_settings_button_eligible_user_can_block_false(
        self,
        mock_session_maker: MagicMock,
        mock_get_block_status: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        sentinel_session = AsyncMock(name="sentinel_async_session_disabled")
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=sentinel_session)
        session_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session_maker.return_value = session_cm

        user = SimpleNamespace(id=54321)
        message = SimpleNamespace(text="⚙️ تنظیمات کاربری", answer=AsyncMock())
        state = SimpleNamespace()

        block_status_payload = {
            "can_block": False,
            "max_blocked": 15,
            "current_blocked": 7,
            "remaining": 8,
            "blocked_users": [],
        }
        mock_get_block_status.return_value = block_status_payload

        await handle_user_settings_button(message, state, user)

        mock_session_maker.assert_called_once_with()
        session_cm.__aenter__.assert_awaited_once_with()
        session_cm.__aexit__.assert_awaited_once()

        mock_get_block_status.assert_awaited_once_with(sentinel_session, user.id)
        self.assertIs(mock_get_block_status.await_args[0][0], sentinel_session)
        self.assertEqual(mock_get_block_status.await_args[0][1], user.id)

        mock_answer_incoming_message_via_runtime.assert_awaited_once()
        call_args, call_kwargs = mock_answer_incoming_message_via_runtime.await_args

        self.assertEqual(len(call_args), 3)
        self.assertIs(call_args[0], message)
        self.assertIs(call_args[1], user)

        expected_text = (
            "⚙️ **تنظیمات کاربری**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🚫 **مدیریت بلاک:**\n"
            "   • وضعیت: ❌ غیرفعال\n"
            "   • مسدود شده: 7 از 15\n"
        )
        self.assertEqual(call_args[2], expected_text)
        self.assertEqual(
            set(call_kwargs.keys()),
            {"source_key", "parse_mode", "reply_markup"},
        )
        self.assertEqual(call_kwargs["source_key"], "panel-user-settings")
        self.assertEqual(call_kwargs["parse_mode"], "Markdown")

        reply_markup = call_kwargs["reply_markup"]
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 1)
        row = reply_markup.inline_keyboard[0]
        self.assertEqual(len(row), 1)
        button = row[0]
        self.assertEqual(button.text, "🚫 مدیریت کاربران مسدود")
        self.assertEqual(
            button.callback_data,
            BlockMenuCallback(action="main").pack(),
        )
        self.assertEqual(button.callback_data, "block_menu:main")

        message.answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
