"""Stage P1-00b characterization tests for admin_commodities unlinked edit/delete guards.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario: P1-00B-BATCH-V1.admin-commodities-unlinked-edit-delete-guards

Characterizes unlinked user guard (user=None) returning None after
awaiting answer_callback_query_via_runtime(callback, "این گزینه برای حساب شما فعال نیست.", show_alert=True),
before any callback attribute access, payload parsing, FSM state mutation,
keyboard construction, edit delivery, Redis construction, or network connection.
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.admin_commodities import (
    handle_alias_delete_start,
    handle_alias_edit_start,
    handle_commodity_edit_start,
)


class _StrictCallbackSentinel:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Unexpected CallbackQuery attribute access: {name}")


class _StrictStateSentinel:
    def __init__(self) -> None:
        self.set_state = AsyncMock()
        self.get_data = AsyncMock()
        self.update_data = AsyncMock()
        self.clear = AsyncMock()

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Unexpected FSMContext attribute access: {name}")


class AdminCommoditiesUnlinkedEditDeleteGuardsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.strict_callback = _StrictCallbackSentinel()
        self.strict_state = _StrictStateSentinel()
        self.fence = install_p1_00_network_fence()
        self.denied_connects_before = len(self.fence["denied_connects"])
        self.live_redis_before = len(self.fence["live_redis_constructs"])

    def _assert_common_denial_invariants(
        self,
        spy_answer_callback: AsyncMock,
        result: Any,
    ) -> None:
        self.assertIsNone(result)
        spy_answer_callback.assert_awaited_once_with(
            self.strict_callback,
            "این گزینه برای حساب شما فعال نیست.",
            show_alert=True,
        )
        self.strict_state.set_state.assert_not_awaited()
        self.strict_state.get_data.assert_not_awaited()
        self.strict_state.update_data.assert_not_awaited()
        self.strict_state.clear.assert_not_awaited()
        self.assertEqual(len(self.fence["denied_connects"]), self.denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), self.live_redis_before)

    async def test_alias_edit_start_unlinked_alerts_before_parsing_state_keyboard_navigation_or_delivery(
        self,
    ) -> None:
        spy_answer_callback = AsyncMock()
        spy_keyboard = MagicMock()
        spy_edit_callback = AsyncMock()

        with patch(
            "bot.handlers.admin_commodities.answer_callback_query_via_runtime",
            new=spy_answer_callback,
        ), patch(
            "bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard",
            new=spy_keyboard,
        ), patch(
            "bot.handlers.admin_commodities.edit_callback_message_via_runtime",
            new=spy_edit_callback,
        ):
            result = await handle_alias_edit_start(
                query=self.strict_callback,  # type: ignore[arg-type]
                user=None,
                state=self.strict_state,  # type: ignore[arg-type]
            )

        self._assert_common_denial_invariants(spy_answer_callback, result)
        spy_keyboard.assert_not_called()
        spy_edit_callback.assert_not_awaited()

    async def test_alias_delete_start_unlinked_alerts_before_parsing_state_keyboard_navigation_or_delivery(
        self,
    ) -> None:
        spy_answer_callback = AsyncMock()
        spy_keyboard = MagicMock()
        spy_edit_callback = AsyncMock()

        with patch(
            "bot.handlers.admin_commodities.answer_callback_query_via_runtime",
            new=spy_answer_callback,
        ), patch(
            "bot.handlers.admin_commodities.get_alias_delete_confirm_keyboard",
            new=spy_keyboard,
        ), patch(
            "bot.handlers.admin_commodities.edit_callback_message_via_runtime",
            new=spy_edit_callback,
        ):
            result = await handle_alias_delete_start(
                query=self.strict_callback,  # type: ignore[arg-type]
                user=None,
                state=self.strict_state,  # type: ignore[arg-type]
            )

        self._assert_common_denial_invariants(spy_answer_callback, result)
        spy_keyboard.assert_not_called()
        spy_edit_callback.assert_not_awaited()

    async def test_commodity_edit_start_unlinked_alerts_before_parsing_state_keyboard_navigation_or_delivery(
        self,
    ) -> None:
        spy_answer_callback = AsyncMock()
        spy_keyboard = MagicMock()
        spy_edit_callback = AsyncMock()

        with patch(
            "bot.handlers.admin_commodities.answer_callback_query_via_runtime",
            new=spy_answer_callback,
        ), patch(
            "bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard",
            new=spy_keyboard,
        ), patch(
            "bot.handlers.admin_commodities.edit_callback_message_via_runtime",
            new=spy_edit_callback,
        ):
            result = await handle_commodity_edit_start(
                query=self.strict_callback,  # type: ignore[arg-type]
                user=None,
                state=self.strict_state,  # type: ignore[arg-type]
            )

        self._assert_common_denial_invariants(spy_answer_callback, result)
        spy_keyboard.assert_not_called()
        spy_edit_callback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
