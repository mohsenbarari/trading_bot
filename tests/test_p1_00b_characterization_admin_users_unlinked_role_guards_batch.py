"""Characterization tests for remaining admin_users unlinked role guards under Stage P1-00b.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seeds:
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-EDIT-ROLE-L1078
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-USER-ROLE-L1090

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
    handle_set_user_role,
    handle_user_edit_role,
)


class GuardedCallbackData(str):
    """Sentinel string payload that fails if callback-data parsing/splitting is attempted."""

    def split(self, *args, **kwargs):
        raise AssertionError(
            "callback.data split was called prematurely before unlinked user check"
        )


class AdminUsersUnlinkedRoleGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterizes unlinked user (user=None) guard paths for admin_users role routes."""

    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.callback = SimpleNamespace(
            id="cb_admin_users_unlinked_role_test",
            data=GuardedCallbackData("user_edit_role_unparseable_sentinel"),
            message=SimpleNamespace(
                message_id=77,
                chat=SimpleNamespace(id=987654321),
                edit_text=AsyncMock(),
            ),
            from_user=SimpleNamespace(
                id=987654321, is_bot=False, first_name="UnlinkedAdmin"
            ),
            answer=AsyncMock(),
            bot=SimpleNamespace(),
        )

    async def test_user_edit_role_without_user_stops_before_parse_or_delivery(
        self,
    ) -> None:
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_users.get_user_role_edit_keyboard",
        ) as mock_edit_kb, patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as mock_edit_runtime, patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_runtime, patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
        ) as mock_session_local, patch(
            "bot.handlers.admin_users.send_telegram_notification",
            new=AsyncMock(),
        ) as mock_send_notif, patch(
            "bot.handlers.admin_users.create_user_notification",
            new=AsyncMock(),
        ) as mock_create_notif:
            result = await handle_user_edit_role(self.callback, None)

            self.assertIsNone(result)
            mock_edit_kb.assert_not_called()
            mock_edit_runtime.assert_not_called()
            mock_answer_runtime.assert_not_called()
            mock_session_local.assert_not_called()
            mock_send_notif.assert_not_called()
            mock_create_notif.assert_not_called()

            self.callback.answer.assert_not_called()
            self.callback.message.edit_text.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

    async def test_set_user_role_without_user_stops_before_parse_session_or_delivery(
        self,
    ) -> None:
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
        ) as mock_session_local, patch(
            "bot.handlers.admin_users._reject_users_callback_if_not_authoritative",
            new=AsyncMock(),
        ) as mock_reject_auth, patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(),
        ) as mock_profile_text, patch(
            "bot.handlers.admin_users.get_user_profile_return_keyboard",
        ) as mock_profile_kb, patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as mock_edit_runtime, patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_runtime, patch(
            "bot.handlers.admin_users.send_telegram_notification",
            new=AsyncMock(),
        ) as mock_send_notif, patch(
            "bot.handlers.admin_users.create_user_notification",
            new=AsyncMock(),
        ) as mock_create_notif:
            result = await handle_set_user_role(self.callback, None)

            self.assertIsNone(result)
            mock_session_local.assert_not_called()
            mock_reject_auth.assert_not_called()
            mock_profile_text.assert_not_called()
            mock_profile_kb.assert_not_called()
            mock_edit_runtime.assert_not_called()
            mock_answer_runtime.assert_not_called()
            mock_send_notif.assert_not_called()
            mock_create_notif.assert_not_called()

            self.callback.answer.assert_not_called()
            self.callback.message.edit_text.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
