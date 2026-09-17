"""Characterization tests for remaining panel unlinked route guards under Stage P1-00b.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seeds:
  - BOT-TG-MESSAGE-PANEL-HANDLE-BACK-TO-MAIN-MENU-L1450
  - BOT-TG-MESSAGE-PANEL-SHOW-MY-PROFILE-AND-CHANGE-KEYBOARD-L211

Disclaimer: This test-only unit does not update inventories, reduce blockers,
promote evidence, or close P1-00b.
"""

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
    handle_back_to_main_menu,
    show_my_profile_and_change_keyboard,
)


class PanelRemainingUnlinkedRouteGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterizes unlinked user (user=None) guard paths for remaining panel routes."""

    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.message = SimpleNamespace(
            message_id=42,
            text="",
            from_user=SimpleNamespace(id=987654321),
            chat=SimpleNamespace(id=987654321),
            bot=SimpleNamespace(),
        )
        self.state = SimpleNamespace(
            clear=AsyncMock(),
            get_data=AsyncMock(return_value={}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
            get_state=AsyncMock(),
            set_data=AsyncMock(),
        )

    async def test_back_to_main_menu_text_route_without_user_stops_before_runtime(
        self,
    ) -> None:
        self.message.text = "🔙 بازگشت"
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.panel.configured_telegram_delivery_runtime",
        ) as mock_configured_runtime, patch(
            "bot.handlers.panel.delete_previous_anchor",
            new=AsyncMock(),
        ) as mock_delete_anchor, patch(
            "bot.handlers.panel.user_facing_webapp_url",
        ) as mock_webapp_url, patch(
            "bot.handlers.panel.build_persistent_navigation_keyboard",
            new=AsyncMock(),
        ) as mock_build_nav_kb, patch(
            "bot.handlers.panel.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_incoming, patch(
            "bot.handlers.panel.set_anchor",
        ) as mock_set_anchor:
            result = await handle_back_to_main_menu(self.message, self.state, None)

            self.assertIsNone(result)
            mock_configured_runtime.assert_not_called()
            mock_delete_anchor.assert_not_called()
            mock_webapp_url.assert_not_called()
            mock_build_nav_kb.assert_not_called()
            mock_answer_incoming.assert_not_called()
            mock_set_anchor.assert_not_called()

            self.state.clear.assert_not_called()
            self.state.get_data.assert_not_called()
            self.state.update_data.assert_not_called()
            self.state.set_state.assert_not_called()
            self.state.get_state.assert_not_called()
            self.state.set_data.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

    async def test_user_panel_text_route_without_user_stops_before_policy_session_or_delivery(
        self,
    ) -> None:
        self.message.text = "👤 پنل کاربر"
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.panel.is_user_global_web_locked",
        ) as mock_is_user_locked, patch(
            "bot.handlers.panel.configured_telegram_delivery_runtime",
        ) as mock_configured_runtime, patch(
            "bot.handlers.panel.delete_previous_anchor",
            new=AsyncMock(),
        ) as mock_delete_anchor, patch(
            "bot.handlers.panel.AsyncSessionLocal",
        ) as mock_session_local, patch(
            "bot.handlers.panel._can_use_customer_panel",
            new=AsyncMock(),
        ) as mock_can_use_panel, patch(
            "bot.handlers.panel._user_panel_show_overtime_preference",
            new=AsyncMock(),
        ) as mock_show_overtime, patch(
            "bot.handlers.panel.attach_customer_management_names",
            new=AsyncMock(),
        ) as mock_attach_names, patch(
            "bot.handlers.panel.user_display_name",
        ) as mock_user_display_name, patch(
            "bot.handlers.panel.build_user_panel_navigation_keyboard",
            new=AsyncMock(),
        ) as mock_build_user_kb, patch(
            "bot.handlers.panel.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_incoming, patch(
            "bot.handlers.panel.set_anchor",
        ) as mock_set_anchor:
            result = await show_my_profile_and_change_keyboard(
                self.message, self.state, None
            )

            self.assertIsNone(result)
            mock_is_user_locked.assert_not_called()
            mock_configured_runtime.assert_not_called()
            mock_delete_anchor.assert_not_called()
            mock_session_local.assert_not_called()
            mock_can_use_panel.assert_not_called()
            mock_show_overtime.assert_not_called()
            mock_attach_names.assert_not_called()
            mock_user_display_name.assert_not_called()
            mock_build_user_kb.assert_not_called()
            mock_answer_incoming.assert_not_called()
            mock_set_anchor.assert_not_called()

            self.state.clear.assert_not_called()
            self.state.get_data.assert_not_called()
            self.state.update_data.assert_not_called()
            self.state.set_state.assert_not_called()
            self.state.get_state.assert_not_called()
            self.state.set_data.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
