import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.panel import (
    handle_settings_cancel,
    handle_settings_reset_cancel,
)
from core.enums import UserRole


SHARED_USER = SimpleNamespace(
    id=7001,
    telegram_id=7001001,
    role=UserRole.SUPER_ADMIN,
    account_name="panel_super_admin",
    mobile_number="+989120000001",
    full_name="Panel Super Admin",
    address="Admin Office",
)

SHARED_CALLBACK = SimpleNamespace(
    id="cb_panel_settings_shared_query",
    data="settings_shared_cancel_query",
    message=SimpleNamespace(message_id=9001),
)

SHARED_SETTINGS_TEXT = (
    "⚙️ **تنظیمات سیستم**\n"
    "━━━━━━━━━━━━━━━━━━━\n\n"
    "📨 **دعوتنامه:**\n"
    "   • مدت اعتبار: `7` روز\n"
)

SHARED_SETTINGS_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔄 بازنشانی به پیشفرض",
                callback_data="settings_reset_all",
            )
        ]
    ]
)


class PanelSettingsCancelRefreshCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    shared_user = SHARED_USER
    shared_callback = SHARED_CALLBACK
    shared_text = SHARED_SETTINGS_TEXT
    shared_keyboard = SHARED_SETTINGS_KEYBOARD

    async def test_settings_edit_cancel_clears_and_refreshes_admin_settings(self):
        order: list[str] = []

        def on_clear():
            order.append("clear")

        def on_get_text():
            order.append("get_settings_text")
            return self.shared_text

        def on_get_keyboard():
            order.append("get_settings_keyboard")
            return self.shared_keyboard

        def on_edit(*args, **kwargs):
            order.append("edit_callback_message_via_runtime")

        def on_answer(*args, **kwargs):
            order.append("answer_callback_query_via_runtime")

        mock_clear = AsyncMock(side_effect=on_clear)
        state = SimpleNamespace(clear=mock_clear)

        mock_get_text = AsyncMock(side_effect=on_get_text)
        mock_get_keyboard = Mock(side_effect=on_get_keyboard)
        mock_edit = AsyncMock(side_effect=on_edit)
        mock_answer = AsyncMock(side_effect=on_answer)

        with patch("bot.handlers.panel.get_settings_text", new=mock_get_text), \
             patch("bot.handlers.panel.get_settings_keyboard", new=mock_get_keyboard), \
             patch("bot.handlers.panel.edit_callback_message_via_runtime", new=mock_edit), \
             patch("bot.handlers.panel.answer_callback_query_via_runtime", new=mock_answer):
            result = await handle_settings_cancel(
                self.shared_callback,
                state,
                self.shared_user,
            )

        self.assertIsNone(result)
        mock_clear.assert_awaited_once_with()
        mock_get_text.assert_awaited_once_with()
        mock_get_keyboard.assert_called_once_with()
        mock_edit.assert_awaited_once_with(
            self.shared_callback,
            self.shared_user,
            self.shared_text,
            parse_mode="Markdown",
            reply_markup=self.shared_keyboard,
        )
        self.assertIs(mock_edit.await_args.args[0], self.shared_callback)
        self.assertIs(mock_edit.await_args.args[1], self.shared_user)
        self.assertEqual(mock_edit.await_args.args[2], self.shared_text)
        self.assertEqual(mock_edit.await_args.kwargs.get("parse_mode"), "Markdown")
        self.assertIs(mock_edit.await_args.kwargs.get("reply_markup"), self.shared_keyboard)

        mock_answer.assert_awaited_once_with(
            self.shared_callback,
            "انصراف",
        )
        self.assertIs(mock_answer.await_args.args[0], self.shared_callback)
        self.assertEqual(mock_answer.await_args.args[1], "انصراف")

        self.assertEqual(
            order,
            [
                "clear",
                "get_settings_text",
                "get_settings_keyboard",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )

    async def test_settings_reset_cancel_refreshes_admin_settings_without_mutation(self):
        order: list[str] = []

        def on_get_text():
            order.append("get_settings_text")
            return self.shared_text

        def on_get_keyboard():
            order.append("get_settings_keyboard")
            return self.shared_keyboard

        def on_edit(*args, **kwargs):
            order.append("edit_callback_message_via_runtime")

        def on_answer(*args, **kwargs):
            order.append("answer_callback_query_via_runtime")

        mock_get_text = AsyncMock(side_effect=on_get_text)
        mock_get_keyboard = Mock(side_effect=on_get_keyboard)
        mock_edit = AsyncMock(side_effect=on_edit)
        mock_answer = AsyncMock(side_effect=on_answer)

        with patch("bot.handlers.panel.get_settings_text", new=mock_get_text), \
             patch("bot.handlers.panel.get_settings_keyboard", new=mock_get_keyboard), \
             patch("bot.handlers.panel.edit_callback_message_via_runtime", new=mock_edit), \
             patch("bot.handlers.panel.answer_callback_query_via_runtime", new=mock_answer):
            result = await handle_settings_reset_cancel(
                self.shared_callback,
                self.shared_user,
            )

        self.assertIsNone(result)
        mock_get_text.assert_awaited_once_with()
        mock_get_keyboard.assert_called_once_with()
        mock_edit.assert_awaited_once_with(
            self.shared_callback,
            self.shared_user,
            self.shared_text,
            parse_mode="Markdown",
            reply_markup=self.shared_keyboard,
        )
        self.assertIs(mock_edit.await_args.args[0], self.shared_callback)
        self.assertIs(mock_edit.await_args.args[1], self.shared_user)
        self.assertEqual(mock_edit.await_args.args[2], self.shared_text)
        self.assertEqual(mock_edit.await_args.kwargs.get("parse_mode"), "Markdown")
        self.assertIs(mock_edit.await_args.kwargs.get("reply_markup"), self.shared_keyboard)

        mock_answer.assert_awaited_once_with(
            self.shared_callback,
            "لغو شد",
        )
        self.assertIs(mock_answer.await_args.args[0], self.shared_callback)
        self.assertEqual(mock_answer.await_args.args[1], "لغو شد")

        self.assertEqual(
            order,
            [
                "get_settings_text",
                "get_settings_keyboard",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )


if __name__ == "__main__":
    unittest.main()
