"""Characterization tests for remaining admin_users unlinked limit guards under Stage P1-00b.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seeds:
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-LIMIT-START-L1252
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-TRADES-L1312
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-COMMODITIES-L1336
  - BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-REQUESTS-L1360

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
    handle_set_commodities,
    handle_set_requests,
    handle_set_trades,
    handle_user_limit_start,
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


class AdminUsersUnlinkedLimitGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterizes unlinked user (user=None) guard paths for admin_users limit routes."""

    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.state = ObservableFSMContext()
        self.callback = SimpleNamespace(
            id="cb_admin_users_unlinked_limit_test",
            data=GuardedCallbackData("user_limit_unparseable_sentinel"),
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

    async def test_user_limit_start_without_user_stops_before_parse_session_state_or_delivery(
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
            "bot.handlers.admin_users.get_limit_duration_keyboard",
        ) as mock_duration_kb:
            result = await handle_user_limit_start(self.callback, None, self.state)

            self.assertIsNone(result)
            mock_session_local.assert_not_called()
            mock_answer_runtime.assert_not_called()
            mock_edit_runtime.assert_not_called()
            mock_duration_kb.assert_not_called()

            self.state.update_data.assert_not_called()
            self.state.set_state.assert_not_called()
            self.state.clear.assert_not_called()

            self.callback.answer.assert_not_called()
            self.callback.message.edit_text.assert_not_called()

            self.assertEqual(len(self.fence["denied_connects"]), denied_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

    async def test_set_trades_without_user_stops_before_parse_session_state_or_delivery(
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
        ) as mock_edit_runtime:
            result = await handle_set_trades(self.callback, None, self.state)

            self.assertIsNone(result)
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

    async def test_set_commodities_without_user_stops_before_parse_session_state_or_delivery(
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
        ) as mock_edit_runtime:
            result = await handle_set_commodities(self.callback, None, self.state)

            self.assertIsNone(result)
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

    async def test_set_requests_without_user_stops_before_parse_session_state_or_delivery(
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
        ) as mock_edit_runtime:
            result = await handle_set_requests(self.callback, None, self.state)

            self.assertIsNone(result)
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
