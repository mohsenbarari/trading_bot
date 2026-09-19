"""Characterization tests for remaining admin_users unlinked cancel/delete guards under Stage P1-00b.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seeds:
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-SEARCH-CANCEL-L657
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-DELETE-REQUEST-L1186
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-DELETE-CONFIRM-L1203
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-LIMIT-CANCEL-L1563

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
    handle_limit_cancel,
    handle_user_delete_confirm,
    handle_user_delete_request,
    handle_user_search_cancel,
)


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


class ObservableFSMContext:
    """Observable FSM context fake for monitoring state mutations."""

    def __init__(self) -> None:
        self.update_data = AsyncMock()
        self.set_state = AsyncMock()
        self.clear = AsyncMock()
        self.get_data = AsyncMock(return_value={})
        self.get_state = AsyncMock(return_value=None)
        self.set_data = AsyncMock()


class AdminUsersUnlinkedCancelDeleteGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterizes unlinked user (user=None) guard paths for admin_users cancel and delete routes."""

    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.state = ObservableFSMContext()
        self.callback = SimpleNamespace(
            id="cb_admin_users_unlinked_cancel_delete_test",
            data=GuardedCallbackData("user_cancel_delete_unparseable_sentinel"),
            message=SimpleNamespace(
                message_id=88,
                chat=SimpleNamespace(id=987654321),
                edit_text=AsyncMock(),
            ),
            from_user=SimpleNamespace(
                id=987654321, is_bot=False, first_name="UnlinkedAdmin"
            ),
            answer=AsyncMock(),
            bot=SimpleNamespace(),
        )

    async def test_user_search_cancel_without_user_stops_before_state_navigation_or_delivery(
        self,
    ) -> None:
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_users.clear_state_retain_anchors",
            new=AsyncMock(),
        ) as mock_clear_state, patch(
            "bot.handlers.admin_users.answer_callback_message_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_msg_runtime, patch(
            "bot.handlers.admin_users.build_users_management_navigation_keyboard",
            new=AsyncMock(),
        ) as mock_build_nav, patch(
            "bot.handlers.admin_users.update_anchor",
            new=AsyncMock(),
        ) as mock_update_anchor, patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_runtime:
            result = await handle_user_search_cancel(self.callback, self.state, None)

            self.assertIsNone(result)
            mock_clear_state.assert_not_called()
            mock_answer_msg_runtime.assert_not_called()
            mock_build_nav.assert_not_called()
            mock_update_anchor.assert_not_called()
            mock_answer_runtime.assert_not_called()

            self.state.update_data.assert_not_called()
            self.state.set_state.assert_not_called()
            self.state.clear.assert_not_called()

            self.callback.answer.assert_not_called()
            self.callback.message.edit_text.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

    async def test_user_delete_request_without_user_stops_before_parse_session_redirect_or_delivery(
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
            "bot.handlers.admin_users._show_user_delete_webapp_redirect",
            new=AsyncMock(),
        ) as mock_show_redirect:
            result = await handle_user_delete_request(self.callback, None)

            self.assertIsNone(result)
            mock_session_local.assert_not_called()
            mock_answer_runtime.assert_not_called()
            mock_show_redirect.assert_not_called()

            self.callback.answer.assert_not_called()
            self.callback.message.edit_text.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

    async def test_user_delete_confirm_without_user_stops_before_parse_session_state_navigation_or_delivery(
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
            "bot.handlers.admin_users._show_user_delete_webapp_redirect",
            new=AsyncMock(),
        ) as mock_show_redirect, patch(
            "bot.handlers.admin_users.show_users_list",
            new=AsyncMock(),
        ) as mock_show_users_list:
            result = await handle_user_delete_confirm(self.callback, None, self.state)

            self.assertIsNone(result)
            mock_session_local.assert_not_called()
            mock_answer_runtime.assert_not_called()
            mock_show_redirect.assert_not_called()
            mock_show_users_list.assert_not_called()

            self.state.update_data.assert_not_called()
            self.state.set_state.assert_not_called()
            self.state.clear.assert_not_called()

            self.callback.answer.assert_not_called()
            self.callback.message.edit_text.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

    async def test_limit_cancel_without_user_stops_before_parse_session_state_or_delivery(
        self,
    ) -> None:
        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_users.clear_state_retain_anchors",
            new=AsyncMock(),
        ) as mock_clear_state, patch(
            "bot.handlers.admin_users.AsyncSessionLocal",
        ) as mock_session_local, patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as mock_answer_runtime, patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as mock_edit_runtime:
            result = await handle_limit_cancel(self.callback, None, self.state)

            self.assertIsNone(result)
            mock_clear_state.assert_not_called()
            mock_session_local.assert_not_called()
            mock_answer_runtime.assert_not_called()
            mock_edit_runtime.assert_not_called()

            self.state.update_data.assert_not_called()
            self.state.set_state.assert_not_called()
            self.state.clear.assert_not_called()

            self.callback.answer.assert_not_called()
            self.callback.message.edit_text.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
