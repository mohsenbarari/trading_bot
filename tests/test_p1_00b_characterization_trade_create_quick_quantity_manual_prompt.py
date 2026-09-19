"""Stage P1-00b characterization tests for handle_quick_quantity manual-quantity prompt.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-QUICK-QUANTITY-L869
Scenario: trade-create-quick-quantity-manual-prompt

This unit characterizes the manual-value path of handle_quick_quantity directly:
with a linked user and QuantityCallback(value="manual"), it first acknowledges
the callback via answer_callback_query_via_runtime(callback), then delivers the exact
manual quantity prompt via answer_callback_message_via_runtime with source_key
"offer-manual-quantity-prompt" and action TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
returning None without reading or mutating FSM state, loading trading settings, editing messages,
building lot keyboards, prompting wizard review, or performing network I/O.

Exclusions:
- user=None guard
- numeric values and validation/bounds branches
- handle_manual_quantity
- message-event dispatch
- settings values
- FSM transitions or mutations
- wizard review/edit behavior
- persistence, retry, custom Session/table setup
- subprocess/reload, origin-matrix closure, production, deploy, and external network access.

This characterization does not promote the seed, resolve the blocker, or alter Stage P1-00b state.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.callbacks import QuantityCallback
from bot.handlers.trade_create import handle_quick_quantity
from core.telegram_delivery_queue_contract import TelegramDeliveryAction


class _StrictCallbackSentinel:
    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected CallbackQuery attribute access: {name}")


class _StrictStateSentinel:
    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected FSMContext attribute access: {name}")


class TradeCreateQuickQuantityManualPromptCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_value_acknowledges_then_delivers_exact_prompt_without_state_or_settings(self):
        strict_callback = _StrictCallbackSentinel()
        strict_state = _StrictStateSentinel()
        user = SimpleNamespace(id=1001, username="trader_user")
        callback_data = QuantityCallback(value="manual")

        call_order: list[str] = []

        async def _record_answer_query(*args, **kwargs):
            call_order.append("answer_callback_query_via_runtime")
            return None

        async def _record_answer_msg(*args, **kwargs):
            call_order.append("answer_callback_message_via_runtime")
            return None

        spy_answer_query = AsyncMock(side_effect=_record_answer_query)
        spy_answer_msg = AsyncMock(side_effect=_record_answer_msg)
        spy_edit_msg = AsyncMock(side_effect=AssertionError("edit_callback_message_via_runtime must not be called"))
        spy_lot_prompt = AsyncMock(side_effect=AssertionError("_show_lot_sizes_prompt must not be called"))
        spy_wizard_prompt = AsyncMock(side_effect=AssertionError("_show_wizard_review must not be called"))
        spy_trading_settings = AsyncMock(side_effect=AssertionError("get_trading_settings_async must not be called"))
        spy_lot_type_keyboard = Mock(side_effect=AssertionError("get_lot_type_keyboard must not be called"))

        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        with patch("bot.handlers.trade_create.answer_callback_query_via_runtime", new=spy_answer_query), \
             patch("bot.handlers.trade_create.answer_callback_message_via_runtime", new=spy_answer_msg), \
             patch("bot.handlers.trade_create.edit_callback_message_via_runtime", new=spy_edit_msg), \
             patch("bot.handlers.trade_create._show_lot_sizes_prompt", new=spy_lot_prompt), \
             patch("bot.handlers.trade_create._show_wizard_review", new=spy_wizard_prompt), \
             patch("bot.handlers.trade_create.get_lot_type_keyboard", new=spy_lot_type_keyboard), \
             patch("core.trading_settings.get_trading_settings_async", new=spy_trading_settings):
            result = await handle_quick_quantity(
                callback=strict_callback,  # type: ignore[arg-type]
                state=strict_state,        # type: ignore[arg-type]
                user=user,                 # type: ignore[arg-type]
                callback_data=callback_data,
            )

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            ["answer_callback_query_via_runtime", "answer_callback_message_via_runtime"],
        )

        spy_answer_query.assert_awaited_once_with(strict_callback)
        self.assertIs(spy_answer_query.await_args.args[0], strict_callback)
        self.assertEqual(spy_answer_query.await_args.kwargs, {})

        self.assertEqual(spy_answer_msg.await_count, 1)
        self.assertEqual(len(spy_answer_msg.await_args.args), 3)
        self.assertIs(spy_answer_msg.await_args.args[0], strict_callback)
        self.assertIs(spy_answer_msg.await_args.args[1], user)
        self.assertEqual(spy_answer_msg.await_args.args[2], "✏️ لطفاً تعداد مورد نظر را به عدد وارد کنید:")
        self.assertEqual(
            spy_answer_msg.await_args.kwargs,
            {
                "source_key": "offer-manual-quantity-prompt",
                "action": TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
            },
        )
        self.assertIs(spy_answer_msg.await_args.kwargs["action"], TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE)

        spy_edit_msg.assert_not_called()
        spy_lot_prompt.assert_not_called()
        spy_wizard_prompt.assert_not_called()
        spy_lot_type_keyboard.assert_not_called()
        spy_trading_settings.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
