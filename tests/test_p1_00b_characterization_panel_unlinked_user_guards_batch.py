"""Characterization tests for panel unlinked user guards under Stage P1-00b."""

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

from bot.handlers.panel import (
    back_to_user_panel_from_customers,
    handle_simple_settings_button,
    handle_user_settings_button,
    show_support_contact,
)


class PanelUnlinkedUserGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterizes unlinked user (user=None) guard paths for panel handlers."""

    def setUp(self) -> None:
        self.message = SimpleNamespace(
            message_id=42,
            text="panel_message_guard_fixture",
            from_user=SimpleNamespace(id=987654321),
            chat=SimpleNamespace(id=987654321),
        )
        self.callback = SimpleNamespace(
            id="cb_panel_unlinked_user_guard_fixture",
            data="back",
            message=SimpleNamespace(message_id=42),
            from_user=SimpleNamespace(id=987654321),
        )
        self.state = SimpleNamespace(
            clear=AsyncMock(),
            get_data=AsyncMock(return_value={}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )

    async def test_back_from_customers_without_user_acknowledges_without_edit(
        self,
    ) -> None:
        with patch(
            "bot.handlers.panel.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_callback, patch(
            "bot.handlers.panel.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as mock_edit_callback:
            result = await back_to_user_panel_from_customers(self.callback, None)

            self.assertIsNone(result)
            mock_answer_callback.assert_awaited_once_with(self.callback)
            mock_edit_callback.assert_not_called()

    async def test_user_settings_without_user_stops_before_session_or_delivery(
        self,
    ) -> None:
        with patch(
            "core.db.AsyncSessionLocal",
        ) as mock_core_session, patch(
            "bot.handlers.panel.AsyncSessionLocal",
        ) as mock_panel_session, patch(
            "core.services.block_service.get_block_status",
            new=AsyncMock(),
        ) as mock_get_block_status, patch(
            "bot.handlers.panel.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_incoming:
            result = await handle_user_settings_button(
                self.message, self.state, None
            )

            self.assertIsNone(result)
            self.state.clear.assert_not_called()
            self.state.get_data.assert_not_called()
            mock_core_session.assert_not_called()
            mock_panel_session.assert_not_called()
            mock_get_block_status.assert_not_called()
            mock_answer_incoming.assert_not_called()

    async def test_simple_settings_without_user_stops_before_delivery(
        self,
    ) -> None:
        with patch(
            "bot.handlers.panel.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_incoming:
            result = await handle_simple_settings_button(self.message, None)

            self.assertIsNone(result)
            mock_answer_incoming.assert_not_called()

    async def test_support_contact_without_user_stops_before_session_policy_or_delivery(
        self,
    ) -> None:
        with patch(
            "core.db.AsyncSessionLocal",
        ) as mock_core_session, patch(
            "bot.handlers.panel.AsyncSessionLocal",
        ) as mock_panel_session, patch(
            "bot.handlers.panel._can_view_support",
            new=AsyncMock(),
        ) as mock_can_view_support, patch(
            "bot.handlers.panel.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_incoming:
            result = await show_support_contact(self.message, None)

            self.assertIsNone(result)
            mock_core_session.assert_not_called()
            mock_panel_session.assert_not_called()
            mock_can_view_support.assert_not_called()
            mock_answer_incoming.assert_not_called()


if __name__ == "__main__":
    unittest.main()
