"""Stage P1-00b characterization tests for handle_quick_quantity unlinked-user guard.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-QUICK-QUANTITY-L869

This test characterizes the unlinked user guard (user=None) returning immediately
without inspecting callback_data.value, loading settings, reading/writing FSM state,
answering callbacks, delivering interaction messages, or performing network operations.
This characterization does not promote the seed, resolve the blocker, or alter Stage P1-00b state.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.trade_create import handle_quick_quantity


class _StrictCallbackSentinel:
    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected CallbackQuery attribute access: {name}")


class _StrictStateSentinel:
    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected FSMContext attribute access: {name}")


class _StrictCallbackDataSentinel:
    @property
    def value(self):
        raise AssertionError("Unexpected callback_data.value access during unlinked user guard")

    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected QuantityCallback attribute access: {name}")


class TradeCreateQuickQuantityUnlinkedUserGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_user_returns_before_callback_data_state_or_delivery_work(self):
        strict_callback = _StrictCallbackSentinel()
        strict_state = _StrictStateSentinel()
        strict_callback_data = _StrictCallbackDataSentinel()

        spy_answer_query = AsyncMock(side_effect=AssertionError("answer_callback_query_via_runtime must not be called"))
        spy_answer_msg = AsyncMock(side_effect=AssertionError("answer_callback_message_via_runtime must not be called"))
        spy_edit_msg = AsyncMock(side_effect=AssertionError("edit_callback_message_via_runtime must not be called"))
        spy_lot_prompt = AsyncMock(side_effect=AssertionError("_show_lot_sizes_prompt must not be called"))
        spy_wizard_prompt = AsyncMock(side_effect=AssertionError("_show_wizard_review must not be called"))
        spy_trading_settings = AsyncMock(side_effect=AssertionError("get_trading_settings_async must not be called"))

        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        with patch("bot.handlers.trade_create.answer_callback_query_via_runtime", new=spy_answer_query), \
             patch("bot.handlers.trade_create.answer_callback_message_via_runtime", new=spy_answer_msg), \
             patch("bot.handlers.trade_create.edit_callback_message_via_runtime", new=spy_edit_msg), \
             patch("bot.handlers.trade_create._show_lot_sizes_prompt", new=spy_lot_prompt), \
             patch("bot.handlers.trade_create._show_wizard_review", new=spy_wizard_prompt), \
             patch("core.trading_settings.get_trading_settings_async", new=spy_trading_settings):
            result = await handle_quick_quantity(
                callback=strict_callback,  # type: ignore[arg-type]
                state=strict_state,        # type: ignore[arg-type]
                user=None,
                callback_data=strict_callback_data,  # type: ignore[arg-type]
            )

        self.assertIsNone(result)

        spy_answer_query.assert_not_called()
        spy_answer_msg.assert_not_called()
        spy_edit_msg.assert_not_called()
        spy_lot_prompt.assert_not_called()
        spy_wizard_prompt.assert_not_called()
        spy_trading_settings.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
