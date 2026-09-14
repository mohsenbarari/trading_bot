"""Characterization tests for legacy Trade confirmation callback wrappers.

Exact seeds characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TRADE-CONFIRM-L2125
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TRADE-WARNING-CONFIRM-L2141

These tests freeze the delegation contracts of handle_trade_confirm and
handle_trade_warning_confirm to _handle_trade_confirm_core under blocker
P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.

These wrapper assertions explicitly do not characterize _handle_trade_confirm_core,
do not close either seed (BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TRADE-CONFIRM-L2125
or BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TRADE-WARNING-CONFIRM-L2141), do not close
P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE, and do not close Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from unittest import mock

import bot.handlers.trade_create as trade_create
from bot.handlers.trade_create import (
    handle_trade_confirm,
    handle_trade_warning_confirm,
)

SEED_HANDLE_TRADE_CONFIRM = "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TRADE-CONFIRM-L2125"
SEED_HANDLE_TRADE_WARNING_CONFIRM = "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TRADE-WARNING-CONFIRM-L2141"


class TradeConfirmWrappersCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_trade_confirm_delegates_to_core(self):
        sentinel_callback = object()
        sentinel_state = object()
        sentinel_user = object()
        sentinel_bot = object()

        with mock.patch(
            "bot.handlers.trade_create._handle_trade_confirm_core",
            new_callable=mock.AsyncMock,
        ) as mock_core:
            result = await handle_trade_confirm(
                sentinel_callback,
                sentinel_state,
                sentinel_user,
                sentinel_bot,
            )

        self.assertIsNone(result)
        mock_core.assert_awaited_once_with(
            sentinel_callback,
            sentinel_state,
            sentinel_user,
            sentinel_bot,
            check_user_limits_fn=trade_create.check_user_limits,
            to_jalali_str_fn=trade_create.to_jalali_str,
            success_message_text="✅ لفظ شما با موفقیت در کانال ارسال شد!",
            unexpected_error_prefix="❌ لفظ ثبت نشد",
            warning_confirm_callback_data="trade:confirm_warning",
            cancel_callback_data="trade:cancel",
        )

        call_args = mock_core.call_args
        self.assertEqual(len(call_args.args), 4)
        self.assertIs(call_args.args[0], sentinel_callback)
        self.assertIs(call_args.args[1], sentinel_state)
        self.assertIs(call_args.args[2], sentinel_user)
        self.assertIs(call_args.args[3], sentinel_bot)
        self.assertIs(call_args.kwargs["check_user_limits_fn"], trade_create.check_user_limits)
        self.assertIs(call_args.kwargs["to_jalali_str_fn"], trade_create.to_jalali_str)
        self.assertEqual(
            call_args.kwargs["success_message_text"],
            "✅ لفظ شما با موفقیت در کانال ارسال شد!",
        )
        self.assertEqual(
            call_args.kwargs["unexpected_error_prefix"],
            "❌ لفظ ثبت نشد",
        )
        self.assertEqual(
            call_args.kwargs["warning_confirm_callback_data"],
            "trade:confirm_warning",
        )
        self.assertEqual(
            call_args.kwargs["cancel_callback_data"],
            "trade:cancel",
        )
        self.assertNotIn("warning_acknowledged", call_args.kwargs)

    async def test_handle_trade_warning_confirm_delegates_to_core(self):
        sentinel_callback = object()
        sentinel_state = object()
        sentinel_user = object()
        sentinel_bot = object()

        with mock.patch(
            "bot.handlers.trade_create._handle_trade_confirm_core",
            new_callable=mock.AsyncMock,
        ) as mock_core:
            result = await handle_trade_warning_confirm(
                sentinel_callback,
                sentinel_state,
                sentinel_user,
                sentinel_bot,
            )

        self.assertIsNone(result)
        mock_core.assert_awaited_once_with(
            sentinel_callback,
            sentinel_state,
            sentinel_user,
            sentinel_bot,
            check_user_limits_fn=trade_create.check_user_limits,
            to_jalali_str_fn=trade_create.to_jalali_str,
            success_message_text="✅ لفظ شما با موفقیت در کانال ارسال شد!",
            unexpected_error_prefix="❌ لفظ ثبت نشد",
            warning_confirm_callback_data="trade:confirm_warning",
            cancel_callback_data="trade:cancel",
            warning_acknowledged=True,
        )

        call_args = mock_core.call_args
        self.assertEqual(len(call_args.args), 4)
        self.assertIs(call_args.args[0], sentinel_callback)
        self.assertIs(call_args.args[1], sentinel_state)
        self.assertIs(call_args.args[2], sentinel_user)
        self.assertIs(call_args.args[3], sentinel_bot)
        self.assertIs(call_args.kwargs["check_user_limits_fn"], trade_create.check_user_limits)
        self.assertIs(call_args.kwargs["to_jalali_str_fn"], trade_create.to_jalali_str)
        self.assertEqual(
            call_args.kwargs["success_message_text"],
            "✅ لفظ شما با موفقیت در کانال ارسال شد!",
        )
        self.assertEqual(
            call_args.kwargs["unexpected_error_prefix"],
            "❌ لفظ ثبت نشد",
        )
        self.assertEqual(
            call_args.kwargs["warning_confirm_callback_data"],
            "trade:confirm_warning",
        )
        self.assertEqual(
            call_args.kwargs["cancel_callback_data"],
            "trade:cancel",
        )
        self.assertIs(call_args.kwargs.get("warning_acknowledged"), True)


if __name__ == "__main__":
    unittest.main()
