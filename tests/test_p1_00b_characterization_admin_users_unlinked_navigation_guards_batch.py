"""Characterization tests for remaining admin_users unlinked navigation guards under Stage P1-00b.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario: P1-00B-BATCH-V1.admin-users-unlinked-navigation-guards
Seeds:
  - BOT-ADMIN-MESSAGE-ADMIN-USERS-HANDLE-USERS-MENU-L489
  - BOT-ADMIN-MESSAGE-ADMIN-USERS-HANDLE-USERS-LIST-COMMAND-L512
  - BOT-ADMIN-MESSAGE-ADMIN-USERS-HANDLE-BACK-TO-ADMIN-L602

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

from bot.handlers.admin_users import (
    handle_back_to_admin,
    handle_users_list_command,
    handle_users_menu,
)


class GuardedMessage:
    """Guarded message sentinel that fails if downstream delivery or interaction is attempted."""

    def __init__(self, message_id: int = 101, chat_id: int = 202) -> None:
        self.message_id = message_id
        self.chat = SimpleNamespace(id=chat_id)
        self.bot = SimpleNamespace()

    def answer(self, *args, **kwargs):
        raise AssertionError(
            "message.answer called unexpectedly on unlinked user guard path"
        )

    def reply(self, *args, **kwargs):
        raise AssertionError(
            "message.reply called unexpectedly on unlinked user guard path"
        )

    def delete(self, *args, **kwargs):
        raise AssertionError(
            "message.delete called unexpectedly on unlinked user guard path"
        )

    def edit_text(self, *args, **kwargs):
        raise AssertionError(
            "message.edit_text called unexpectedly on unlinked user guard path"
        )

    def __getattr__(self, name: str):
        raise AssertionError(f"Accidental message attribute access: {name}")


class GuardedState:
    """Guarded FSMContext sentinel that fails if state operations are attempted."""

    def get_data(self, *args, **kwargs):
        raise AssertionError(
            "state.get_data called unexpectedly on unlinked user guard path"
        )

    def update_data(self, *args, **kwargs):
        raise AssertionError(
            "state.update_data called unexpectedly on unlinked user guard path"
        )

    def set_data(self, *args, **kwargs):
        raise AssertionError(
            "state.set_data called unexpectedly on unlinked user guard path"
        )

    def clear(self, *args, **kwargs):
        raise AssertionError(
            "state.clear called unexpectedly on unlinked user guard path"
        )

    def set_state(self, *args, **kwargs):
        raise AssertionError(
            "state.set_state called unexpectedly on unlinked user guard path"
        )

    def get_state(self, *args, **kwargs):
        raise AssertionError(
            "state.get_state called unexpectedly on unlinked user guard path"
        )

    def __getattr__(self, name: str):
        raise AssertionError(f"Accidental state attribute access: {name}")


class AdminUsersUnlinkedNavigationGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterizes unlinked user (user=None) guard paths for admin_users navigation routes."""

    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.unlinked_user = None
        self.message = GuardedMessage()
        self.state = GuardedState()

    async def test_users_menu_without_user_stops_before_delivery_or_state(
        self,
    ) -> None:
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_users.delete_user_message",
            new=AsyncMock(),
        ) as mock_delete_msg, patch(
            "bot.handlers.admin_users.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_runtime, patch(
            "bot.handlers.admin_users.build_users_management_navigation_keyboard",
            new=AsyncMock(),
        ) as mock_build_kb, patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
        ) as mock_runtime, patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
        ) as mock_session_local:
            result = await handle_users_menu(
                self.message, self.unlinked_user, self.state
            )

            self.assertIsNone(result)
            mock_delete_msg.assert_not_called()
            mock_answer_runtime.assert_not_called()
            mock_build_kb.assert_not_called()
            mock_runtime.assert_not_called()
            mock_session_local.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

    async def test_users_list_command_without_user_stops_before_state_runtime_or_render(
        self,
    ) -> None:
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_users.delete_user_message",
            new=AsyncMock(),
        ) as mock_delete_msg, patch(
            "bot.handlers.admin_users.clear_state_retain_anchors",
            new=AsyncMock(),
        ) as mock_clear_state, patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
        ) as mock_runtime, patch(
            "bot.handlers.admin_users.show_users_list",
            new=AsyncMock(),
        ) as mock_show_list, patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
        ) as mock_session_local:
            result = await handle_users_list_command(
                self.message, self.unlinked_user, self.state
            )

            self.assertIsNone(result)
            mock_delete_msg.assert_not_called()
            mock_clear_state.assert_not_called()
            mock_runtime.assert_not_called()
            mock_show_list.assert_not_called()
            mock_session_local.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

    async def test_back_to_admin_without_user_stops_before_state_runtime_or_delivery(
        self,
    ) -> None:
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_users.delete_user_message",
            new=AsyncMock(),
        ) as mock_delete_msg, patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
        ) as mock_runtime, patch(
            "bot.handlers.admin_users.safe_delete_message",
            new=AsyncMock(),
        ) as mock_safe_delete, patch(
            "bot.handlers.admin_users.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_runtime, patch(
            "bot.handlers.admin_users.build_admin_panel_navigation_keyboard",
            new=AsyncMock(),
        ) as mock_build_kb, patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
        ) as mock_session_local:
            result = await handle_back_to_admin(
                self.message, self.unlinked_user, self.state
            )

            self.assertIsNone(result)
            mock_delete_msg.assert_not_called()
            mock_runtime.assert_not_called()
            mock_safe_delete.assert_not_called()
            mock_answer_runtime.assert_not_called()
            mock_build_kb.assert_not_called()
            mock_session_local.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
