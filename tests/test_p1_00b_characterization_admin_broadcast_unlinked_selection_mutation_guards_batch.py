"""Stage P1-00b characterization tests for unlinked selection mutation guards.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This test characterizes the unlinked user guard (user=None) returning None after
awaiting answer_callback_query_via_runtime(callback, "عدم دسترسی", show_alert=True),
before callback payload/attribute inspection; FSM get, set, update, or clear;
audience/group/user selection mutation; keyboard construction; navigation to message entry;
edit delivery; success acknowledgement; database/Redis creation; or network connection.
This characterization does not promote seeds, resolve the blocker, or alter Stage P1-00b state.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.admin_broadcast import (
    finish_selected_recipients,
    toggle_group_recipient,
    toggle_selected_user,
)


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


class AdminBroadcastUnlinkedSelectionMutationGuardsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.strict_callback = _StrictCallbackSentinel()
        self.strict_state = _StrictStateSentinel()

        self.spy_answer_callback = AsyncMock()
        self.spy_edit_callback = AsyncMock()
        self.spy_edit_reply_markup = AsyncMock()
        self.spy_groups_keyboard = MagicMock()
        self.spy_search_results_keyboard = MagicMock()
        self.spy_ask_for_message_text = AsyncMock()
        self.spy_session_local = MagicMock()

        for target, mock_obj in (
            ("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", self.spy_answer_callback),
            ("bot.handlers.admin_broadcast.edit_callback_message_via_runtime", self.spy_edit_callback),
            ("bot.handlers.admin_broadcast.edit_callback_reply_markup_via_runtime", self.spy_edit_reply_markup),
            ("bot.handlers.admin_broadcast._groups_keyboard", self.spy_groups_keyboard),
            ("bot.handlers.admin_broadcast._search_results_keyboard", self.spy_search_results_keyboard),
            ("bot.handlers.admin_broadcast._ask_for_message_text", self.spy_ask_for_message_text),
            ("bot.handlers.admin_broadcast.AsyncSessionLocal", self.spy_session_local),
        ):
            patcher = patch(target, new=mock_obj)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_toggle_group_recipient_without_user_denies_before_payload_state_keyboard_or_delivery(self):
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        result = await toggle_group_recipient(
            callback=self.strict_callback,  # type: ignore[arg-type]
            state=self.strict_state,        # type: ignore[arg-type]
            user=None,
        )

        self.assertIsNone(result)

        self.spy_answer_callback.assert_awaited_once_with(
            self.strict_callback,
            "عدم دسترسی",
            show_alert=True,
        )

        self.spy_groups_keyboard.assert_not_called()
        self.spy_edit_reply_markup.assert_not_called()
        self.spy_edit_callback.assert_not_called()
        self.spy_search_results_keyboard.assert_not_called()
        self.spy_ask_for_message_text.assert_not_called()
        self.spy_session_local.assert_not_called()

        self.strict_state.set_state.assert_not_awaited()
        self.strict_state.get_data.assert_not_awaited()
        self.strict_state.update_data.assert_not_awaited()
        self.strict_state.clear.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_toggle_selected_user_without_user_denies_before_payload_state_session_keyboard_or_delivery(self):
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        result = await toggle_selected_user(
            callback=self.strict_callback,  # type: ignore[arg-type]
            state=self.strict_state,        # type: ignore[arg-type]
            user=None,
        )

        self.assertIsNone(result)

        self.spy_answer_callback.assert_awaited_once_with(
            self.strict_callback,
            "عدم دسترسی",
            show_alert=True,
        )

        self.spy_search_results_keyboard.assert_not_called()
        self.spy_session_local.assert_not_called()
        self.spy_edit_callback.assert_not_called()
        self.spy_edit_reply_markup.assert_not_called()
        self.spy_groups_keyboard.assert_not_called()
        self.spy_ask_for_message_text.assert_not_called()

        self.strict_state.set_state.assert_not_awaited()
        self.strict_state.get_data.assert_not_awaited()
        self.strict_state.update_data.assert_not_awaited()
        self.strict_state.clear.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_finish_selected_recipients_without_user_denies_before_state_navigation_or_delivery(self):
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        result = await finish_selected_recipients(
            callback=self.strict_callback,  # type: ignore[arg-type]
            state=self.strict_state,        # type: ignore[arg-type]
            user=None,
        )

        self.assertIsNone(result)

        self.spy_answer_callback.assert_awaited_once_with(
            self.strict_callback,
            "عدم دسترسی",
            show_alert=True,
        )

        self.spy_ask_for_message_text.assert_not_called()
        self.spy_edit_callback.assert_not_called()
        self.spy_edit_reply_markup.assert_not_called()
        self.spy_groups_keyboard.assert_not_called()
        self.spy_search_results_keyboard.assert_not_called()
        self.spy_session_local.assert_not_called()

        self.strict_state.set_state.assert_not_awaited()
        self.strict_state.get_data.assert_not_awaited()
        self.strict_state.update_data.assert_not_awaited()
        self.strict_state.clear.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
