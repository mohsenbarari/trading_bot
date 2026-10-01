"""Characterization test for admin_users toggle block unlinked guard under Stage P1-00b.

Stage: P1-00b
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-064
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649

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

from bot.handlers.admin_users import handle_admin_toggle_block


class GuardedCallbackData(str):
    """Sentinel string payload that fails if callback-data parsing/splitting is attempted."""

    def split(self, *args, **kwargs):
        raise AssertionError(
            "callback.data split was called prematurely before unlinked user check"
        )

    def startswith(self, *args, **kwargs):
        raise AssertionError(
            "callback.data startswith was called prematurely before unlinked user check"
        )

    def rsplit(self, *args, **kwargs):
        raise AssertionError(
            "callback.data rsplit was called prematurely before unlinked user check"
        )

    def partition(self, *args, **kwargs):
        raise AssertionError(
            "callback.data partition was called prematurely before unlinked user check"
        )


class AdminUsersToggleBlockUnlinkedCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterizes unlinked user (user=None) guard path for handle_admin_toggle_block."""

    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.callback = SimpleNamespace(
            id="cb_admin_users_toggle_block_unlinked_test",
            data=GuardedCallbackData("admin_toggle_block_unparseable_sentinel"),
            message=SimpleNamespace(
                message_id=101,
                chat=SimpleNamespace(id=987654321),
                edit_text=AsyncMock(),
            ),
            from_user=SimpleNamespace(
                id=987654321, is_bot=False, first_name="UnlinkedAdmin"
            ),
            answer=AsyncMock(),
            bot=SimpleNamespace(),
        )

    async def test_toggle_block_without_user_stops_before_parse_session_or_delivery(
        self,
    ) -> None:
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
        ) as mock_session_local, patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_runtime, patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as mock_edit_runtime, patch(
            "bot.handlers.admin_users.get_block_settings_keyboard",
        ) as mock_block_settings_kb:
            result = await handle_admin_toggle_block(self.callback, None)

            self.assertIsNone(result)
            mock_session_local.assert_not_called()
            mock_answer_runtime.assert_not_called()
            mock_edit_runtime.assert_not_called()
            mock_block_settings_kb.assert_not_called()

            self.callback.answer.assert_not_called()
            self.callback.message.edit_text.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
