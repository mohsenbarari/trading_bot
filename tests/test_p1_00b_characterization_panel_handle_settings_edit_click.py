"""Direct-characterization tests for bot.handlers.panel.handle_settings_edit_click.

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
from bot.handlers.panel import (  # noqa: E402
    handle_settings_edit_click,
)
from bot.states import TradingSettingsEdit  # noqa: E402
from core.enums import UserRole  # noqa: E402


class CharacterizePanelHandleSettingsEditClickTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.handle_settings_edit_click."""

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("core.trading_settings.get_trading_settings_async", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    async def test_handle_settings_edit_click_unauthenticated_denied(
        self,
        mock_reject: AsyncMock,
        mock_get_trading_settings: AsyncMock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_edit_offer_min_quantity", id="cb_unauth")
        state = SimpleNamespace(
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )

        result = await handle_settings_edit_click(callback, state, None)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback, "دسترسی ندارید")
        mock_reject.assert_not_called()
        mock_reject.assert_not_awaited()
        mock_get_trading_settings.assert_not_called()
        mock_get_trading_settings.assert_not_awaited()
        state.update_data.assert_not_called()
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_called()
        state.set_state.assert_not_awaited()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("core.trading_settings.get_trading_settings_async", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    async def test_handle_settings_edit_click_non_super_admin_denied(
        self,
        mock_reject: AsyncMock,
        mock_get_trading_settings: AsyncMock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_edit_offer_min_quantity", id="cb_non_super_admin")
        state = SimpleNamespace(
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        user = SimpleNamespace(id=42, role=UserRole.STANDARD, username="standard_user")

        result = await handle_settings_edit_click(callback, state, user)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback, "دسترسی ندارید")
        mock_reject.assert_not_called()
        mock_reject.assert_not_awaited()
        mock_get_trading_settings.assert_not_called()
        mock_get_trading_settings.assert_not_awaited()
        state.update_data.assert_not_called()
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_called()
        state.set_state.assert_not_awaited()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("core.trading_settings.get_trading_settings_async", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    async def test_handle_settings_edit_click_super_admin_non_authoritative_short_circuit(
        self,
        mock_reject: AsyncMock,
        mock_get_trading_settings: AsyncMock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_edit_offer_min_quantity", id="cb_non_auth")
        state = SimpleNamespace(
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        user = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, username="super_admin_user")
        mock_reject.return_value = True

        result = await handle_settings_edit_click(callback, state, user)

        self.assertIsNone(result)
        mock_reject.assert_awaited_once_with(callback, "update")
        mock_get_trading_settings.assert_not_called()
        mock_get_trading_settings.assert_not_awaited()
        state.update_data.assert_not_called()
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_called()
        state.set_state.assert_not_awaited()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()
        mock_answer_callback.assert_not_called()
        mock_answer_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("core.trading_settings.get_trading_settings_async", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    async def test_handle_settings_edit_click_super_admin_authorized_success(
        self,
        mock_reject: AsyncMock,
        mock_get_trading_settings: AsyncMock,
        mock_edit_callback: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_edit_offer_min_quantity", id="cb_auth")
        state = SimpleNamespace(
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        user = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, username="super_admin_user")
        sentinel_settings = SimpleNamespace(offer_min_quantity=17)

        call_order: list[str] = []

        async def record_reject(cb: object, op: str) -> bool:
            call_order.append("authority_check")
            return False

        async def record_get_trading_settings() -> object:
            call_order.append("settings_lookup")
            return sentinel_settings

        async def record_update_data(**kwargs: object) -> None:
            call_order.append("state.update_data")

        async def record_set_state(st: object) -> None:
            call_order.append("state.set_state")

        async def record_edit_callback(*args: object, **kwargs: object) -> None:
            call_order.append("edit_callback_message_via_runtime")

        async def record_answer_callback(*args: object, **kwargs: object) -> None:
            call_order.append("answer_callback_query_via_runtime")

        mock_reject.side_effect = record_reject
        mock_get_trading_settings.side_effect = record_get_trading_settings
        state.update_data.side_effect = record_update_data
        state.set_state.side_effect = record_set_state
        mock_edit_callback.side_effect = record_edit_callback
        mock_answer_callback.side_effect = record_answer_callback

        result = await handle_settings_edit_click(callback, state, user)

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "authority_check",
                "settings_lookup",
                "state.update_data",
                "state.set_state",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )

        mock_reject.assert_awaited_once_with(callback, "update")
        mock_get_trading_settings.assert_awaited_once_with()
        state.update_data.assert_awaited_once_with(editing_setting="offer_min_quantity")
        state.set_state.assert_awaited_once_with(TradingSettingsEdit.awaiting_value)

        mock_edit_callback.assert_awaited_once()
        call_args, call_kwargs = mock_edit_callback.await_args
        self.assertIs(call_args[0], callback)
        self.assertIs(call_args[1], user)

        expected_text = (
            "✏️ **ویرایش تنظیم**\n\n"
            "📌 **حداقل تعداد کالا**\n"
            "مقدار فعلی: `17`\n\n"
            "مقدار جدید را وارد کنید:"
        )
        self.assertEqual(call_args[2], expected_text)
        self.assertEqual(call_kwargs.get("parse_mode"), "Markdown")

        reply_markup = call_kwargs.get("reply_markup")
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 1)
        self.assertEqual(len(reply_markup.inline_keyboard[0]), 1)
        button = reply_markup.inline_keyboard[0][0]
        self.assertEqual(button.text, "❌ انصراف")
        self.assertEqual(button.callback_data, "settings_cancel_edit")

        mock_answer_callback.assert_awaited_once_with(callback)


if __name__ == "__main__":
    unittest.main()
