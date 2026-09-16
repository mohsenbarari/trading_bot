"""Characterization tests for panel settings entry prompts under Stage P1-00b."""

import os

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.handlers.panel import handle_settings_edit_click, handle_settings_reset
from bot.states import TradingSettingsEdit
from core.enums import UserRole
from core.trading_settings import TradingSettings


class PanelSettingsEntryPromptsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterizes prompt rendering for settings edit and reset entry callbacks."""

    def setUp(self) -> None:
        self.user = SimpleNamespace(
            id=101,
            telegram_id=987654321,
            role=UserRole.SUPER_ADMIN,
        )
        self.callback = SimpleNamespace(
            id="cb_settings_prompt_fixture",
            data="settings_edit_offer_min_quantity",
            message=SimpleNamespace(message_id=42),
        )

    async def test_settings_edit_click_renders_current_value_prompt(self) -> None:
        self.callback.data = "settings_edit_offer_min_quantity"
        state = SimpleNamespace(
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        ts = TradingSettings(offer_min_quantity=17)

        call_order: list[str] = []

        async def fake_reject(cb, op):
            call_order.append("reject")
            return False

        async def fake_get_settings():
            call_order.append("get_settings")
            return ts

        async def fake_update_data(**kwargs):
            call_order.append("update_data")

        async def fake_set_state(st):
            call_order.append("set_state")

        async def fake_edit(cb, usr, text, **kwargs):
            call_order.append("edit")

        async def fake_answer(cb, *args, **kwargs):
            call_order.append("answer")

        with patch(
            "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
            new=AsyncMock(side_effect=fake_reject),
        ) as mock_reject, patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(side_effect=fake_get_settings),
        ) as mock_get_settings, patch(
            "bot.handlers.panel.edit_callback_message_via_runtime",
            new=AsyncMock(side_effect=fake_edit),
        ) as mock_edit, patch(
            "bot.handlers.panel.answer_callback_query_via_runtime",
            new=AsyncMock(side_effect=fake_answer),
        ) as mock_answer:
            state.update_data.side_effect = fake_update_data
            state.set_state.side_effect = fake_set_state

            result = await handle_settings_edit_click(self.callback, state, self.user)

            self.assertIsNone(result)
            mock_reject.assert_awaited_once_with(self.callback, "update")
            mock_get_settings.assert_awaited_once_with()
            state.update_data.assert_awaited_once_with(editing_setting="offer_min_quantity")
            state.set_state.assert_awaited_once_with(TradingSettingsEdit.awaiting_value)

            expected_text = (
                "✏️ **ویرایش تنظیم**\n\n"
                "📌 **حداقل تعداد کالا**\n"
                "مقدار فعلی: `17`\n\n"
                "مقدار جدید را وارد کنید:"
            )
            expected_cancel_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ انصراف", callback_data="settings_cancel_edit"
                        )
                    ]
                ]
            )

            mock_edit.assert_awaited_once_with(
                self.callback,
                self.user,
                expected_text,
                parse_mode="Markdown",
                reply_markup=expected_cancel_kb,
            )
            mock_answer.assert_awaited_once_with(self.callback)
            self.assertEqual(
                call_order,
                ["reject", "get_settings", "update_data", "set_state", "edit", "answer"],
            )

    async def test_settings_reset_renders_confirmation_prompt(self) -> None:
        self.callback.data = "settings_reset_all"

        call_order: list[str] = []

        async def fake_reject(cb, op):
            call_order.append("reject")
            return False

        async def fake_edit(cb, usr, text, **kwargs):
            call_order.append("edit")

        async def fake_answer(cb, *args, **kwargs):
            call_order.append("answer")

        with patch(
            "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
            new=AsyncMock(side_effect=fake_reject),
        ) as mock_reject, patch(
            "bot.handlers.panel.edit_callback_message_via_runtime",
            new=AsyncMock(side_effect=fake_edit),
        ) as mock_edit, patch(
            "bot.handlers.panel.answer_callback_query_via_runtime",
            new=AsyncMock(side_effect=fake_answer),
        ) as mock_answer:
            result = await handle_settings_reset(self.callback, self.user)

            self.assertIsNone(result)
            mock_reject.assert_awaited_once_with(self.callback, "reset")

            expected_text = (
                "⚠️ **هشدار**\n\n"
                "آیا مطمئن هستید که می\u200cخواهید تمام تنظیمات را به مقادیر پیش\u200cفرض بازنشانی کنید؟"
            )
            expected_confirm_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ بله، بازنشانی کن",
                            callback_data="settings_reset_confirm",
                        ),
                        InlineKeyboardButton(
                            text="❌ خیر",
                            callback_data="settings_reset_cancel",
                        ),
                    ]
                ]
            )

            mock_edit.assert_awaited_once_with(
                self.callback,
                self.user,
                expected_text,
                parse_mode="Markdown",
                reply_markup=expected_confirm_kb,
            )
            mock_answer.assert_awaited_once_with(self.callback)
            self.assertEqual(call_order, ["reject", "edit", "answer"])


if __name__ == "__main__":
    unittest.main()
