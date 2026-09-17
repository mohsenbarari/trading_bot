"""Characterization tests for panel admin settings access guards under Stage P1-00b."""

import os

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.panel import (
    handle_admin_settings_button,
    handle_settings_new_value,
    handle_settings_reset_confirm,
)
from core.enums import UserRole


class PanelAdminSettingsAccessGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterizes access guard paths for unaccepted panel admin settings handlers."""

    def setUp(self) -> None:
        self.user = SimpleNamespace(
            id=101,
            telegram_id=987654321,
            role=UserRole.STANDARD,
        )
        self.message = SimpleNamespace(
            message_id=42,
            text="⚙️ تنظیمات سیستم",
            from_user=SimpleNamespace(id=987654321),
            chat=SimpleNamespace(id=987654321),
        )
        self.callback = SimpleNamespace(
            id="cb_admin_settings_guard_fixture",
            data="settings_reset_confirm",
            message=SimpleNamespace(message_id=42),
            from_user=SimpleNamespace(id=987654321),
        )
        self.state = SimpleNamespace(
            clear=AsyncMock(),
            get_data=AsyncMock(return_value={"editing_setting": "offer_min_quantity"}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )

    async def test_admin_settings_entry_non_super_admin_returns_without_state_or_delivery(
        self,
    ) -> None:
        with patch(
            "bot.handlers.panel.get_settings_text",
            new=AsyncMock(),
        ) as mock_get_settings_text, patch(
            "bot.handlers.panel.get_settings_keyboard",
            new=MagicMock(),
        ) as mock_get_settings_keyboard, patch(
            "bot.handlers.panel.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_incoming:
            result = await handle_admin_settings_button(
                self.message, self.state, self.user
            )

            self.assertIsNone(result)
            self.state.clear.assert_not_called()
            mock_get_settings_text.assert_not_called()
            mock_get_settings_keyboard.assert_not_called()
            mock_answer_incoming.assert_not_called()

    async def test_settings_new_value_non_super_admin_returns_before_navigation_or_settings_work(
        self,
    ) -> None:
        with patch(
            "bot.handlers.panel.handoff_navigation_button",
            new=AsyncMock(),
        ) as mock_handoff, patch(
            "core.trading_settings.load_trading_settings_async",
            new=AsyncMock(),
        ) as mock_load_settings, patch(
            "core.trading_settings.save_trading_settings_async",
            new=AsyncMock(),
        ) as mock_save_settings, patch(
            "core.trading_settings.refresh_settings_cache_async",
            new=AsyncMock(),
        ) as mock_refresh_cache, patch(
            "bot.handlers.panel._reject_settings_message_if_not_authoritative",
            new=AsyncMock(),
        ) as mock_reject_message, patch(
            "bot.handlers.panel.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_incoming, patch(
            "bot.handlers.panel.get_settings_text",
            new=AsyncMock(),
        ) as mock_get_settings_text, patch(
            "bot.handlers.panel.get_settings_keyboard",
            new=MagicMock(),
        ) as mock_get_settings_keyboard:
            result = await handle_settings_new_value(
                self.message, self.state, self.user
            )

            self.assertIsNone(result)
            mock_handoff.assert_not_called()
            self.state.get_data.assert_not_called()
            self.state.clear.assert_not_called()
            mock_load_settings.assert_not_called()
            mock_save_settings.assert_not_called()
            mock_refresh_cache.assert_not_called()
            mock_reject_message.assert_not_called()
            mock_answer_incoming.assert_not_called()
            mock_get_settings_text.assert_not_called()
            mock_get_settings_keyboard.assert_not_called()

    async def test_settings_reset_confirm_non_super_admin_acknowledges_before_authority_or_save_work(
        self,
    ) -> None:
        with patch(
            "bot.handlers.panel.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_callback, patch(
            "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
            new=AsyncMock(),
        ) as mock_reject_callback, patch(
            "core.trading_settings.TradingSettings",
        ) as mock_trading_settings, patch(
            "core.trading_settings.save_trading_settings_async",
            new=AsyncMock(),
        ) as mock_save_settings, patch(
            "core.trading_settings.refresh_settings_cache_async",
            new=AsyncMock(),
        ) as mock_refresh_cache, patch(
            "bot.handlers.panel.get_settings_text",
            new=AsyncMock(),
        ) as mock_get_settings_text, patch(
            "bot.handlers.panel.get_settings_keyboard",
            new=MagicMock(),
        ) as mock_get_settings_keyboard, patch(
            "bot.handlers.panel.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as mock_edit_callback:
            result = await handle_settings_reset_confirm(self.callback, self.user)

            self.assertIsNone(result)
            mock_answer_callback.assert_awaited_once_with(self.callback)
            mock_reject_callback.assert_not_called()
            mock_trading_settings.assert_not_called()
            mock_save_settings.assert_not_called()
            mock_refresh_cache.assert_not_called()
            mock_get_settings_text.assert_not_called()
            mock_get_settings_keyboard.assert_not_called()
            mock_edit_callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
