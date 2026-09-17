"""Stage P1-00b characterization tests for search_again unlinked-user guard.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-SEARCH-AGAIN-L377

This test characterizes the unlinked user guard (user=None) returning None after
awaiting answer_callback_query_via_runtime(callback, "عدم دسترسی", show_alert=True),
before any FSM set/get operation, direct InlineKeyboardMarkup construction, edit delivery,
AsyncSessionLocal/session access, callback fallback, Redis construction, or network connection.
This characterization does not promote the seed, resolve the blocker, or alter Stage P1-00b state.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.admin_broadcast import search_again


class _StrictCallbackSentinel:
    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected CallbackQuery attribute access: {name}")


class _StrictStateSentinel:
    def __init__(self):
        self.set_state = AsyncMock()
        self.get_data = AsyncMock()
        self.update_data = AsyncMock()
        self.clear = AsyncMock()

    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected FSMContext attribute access: {name}")


class AdminBroadcastSearchAgainUnlinkedGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_again_without_user_alerts_before_state_keyboard_or_delivery(self):
        strict_callback = _StrictCallbackSentinel()
        strict_state = _StrictStateSentinel()

        spy_answer_callback = AsyncMock()
        spy_edit_callback = AsyncMock()
        spy_keyboard = MagicMock()
        spy_session_local = MagicMock()

        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_broadcast.answer_callback_query_via_runtime",
            new=spy_answer_callback,
        ), patch(
            "bot.handlers.admin_broadcast.InlineKeyboardMarkup",
            new=spy_keyboard,
        ), patch(
            "bot.handlers.admin_broadcast.edit_callback_message_via_runtime",
            new=spy_edit_callback,
        ), patch(
            "bot.handlers.admin_broadcast.AsyncSessionLocal",
            new=spy_session_local,
        ):
            result = await search_again(
                callback=strict_callback,  # type: ignore[arg-type]
                state=strict_state,        # type: ignore[arg-type]
                user=None,
            )

        self.assertIsNone(result)

        spy_answer_callback.assert_awaited_once_with(
            strict_callback,
            "عدم دسترسی",
            show_alert=True,
        )

        spy_keyboard.assert_not_called()
        spy_edit_callback.assert_not_called()
        spy_session_local.assert_not_called()

        strict_state.set_state.assert_not_awaited()
        strict_state.get_data.assert_not_awaited()
        strict_state.update_data.assert_not_awaited()
        strict_state.clear.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
