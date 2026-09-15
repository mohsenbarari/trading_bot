"""Characterization tests for text offer confirmation callback wrappers.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact seeds characterized:
- BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-CONFIRM-L3447
- BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-WARNING-CONFIRM-L3470

These wrapper assertions explicitly do not execute or characterize _handle_trade_confirm_core,
and do not close either seed (BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-CONFIRM-L3447
or BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-WARNING-CONFIRM-L3470), the blocker
P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE, or Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from unittest import mock

from bot.callbacks import TextOfferActionCallback
from bot.handlers.trade_create import (
    handle_text_offer_confirm,
    handle_text_offer_warning_confirm,
)
import core.utils

SEED_HANDLE_TEXT_OFFER_CONFIRM = (
    "BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-CONFIRM-L3447"
)
SEED_HANDLE_TEXT_OFFER_WARNING_CONFIRM = (
    "BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-WARNING-CONFIRM-L3470"
)


class TestTradeCreateTextOfferConfirmWrappersCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_stale_callback_short_circuits_both_wrappers_without_core_delegation(
        self,
    ):
        sentinel_callback_normal = object()
        sentinel_state_normal = object()
        sentinel_user_normal = object()
        sentinel_bot_normal = object()

        sentinel_callback_warning = object()
        sentinel_state_warning = object()
        sentinel_user_warning = object()
        sentinel_bot_warning = object()

        with (
            mock.patch(
                "bot.handlers.trade_create._reject_stale_text_offer_callback",
                new_callable=mock.AsyncMock,
            ) as mock_stale_guard,
            mock.patch(
                "bot.handlers.trade_create._handle_trade_confirm_core",
                new_callable=mock.AsyncMock,
            ) as mock_core,
        ):
            mock_stale_guard.return_value = True

            result_normal = await handle_text_offer_confirm(
                sentinel_callback_normal,
                sentinel_state_normal,
                sentinel_user_normal,
                sentinel_bot_normal,
            )

            self.assertIsNone(result_normal)
            mock_stale_guard.assert_awaited_once_with(
                sentinel_callback_normal,
                sentinel_state_normal,
            )
            mock_core.assert_not_called()

            mock_stale_guard.reset_mock()
            mock_core.reset_mock()

            result_warning = await handle_text_offer_warning_confirm(
                sentinel_callback_warning,
                sentinel_state_warning,
                sentinel_user_warning,
                sentinel_bot_warning,
            )

            self.assertIsNone(result_warning)
            mock_stale_guard.assert_awaited_once_with(
                sentinel_callback_warning,
                sentinel_state_warning,
            )
            mock_core.assert_not_called()

    async def test_normal_confirm_delegates_exactly_after_stale_guard(self):
        sentinel_callback = object()
        sentinel_state = object()
        sentinel_user = object()
        sentinel_bot = object()

        call_order: list[str] = []

        async def fake_stale_guard(callback, state):
            call_order.append("stale_guard")
            return False

        async def fake_core(*args, **kwargs):
            call_order.append("core")
            return None

        with (
            mock.patch(
                "bot.handlers.trade_create._reject_stale_text_offer_callback",
                new_callable=mock.AsyncMock,
                side_effect=fake_stale_guard,
            ) as mock_stale_guard,
            mock.patch(
                "bot.handlers.trade_create._handle_trade_confirm_core",
                new_callable=mock.AsyncMock,
                side_effect=fake_core,
            ) as mock_core,
        ):
            result = await handle_text_offer_confirm(
                sentinel_callback,
                sentinel_state,
                sentinel_user,
                sentinel_bot,
            )

        self.assertIsNone(result)
        self.assertEqual(call_order, ["stale_guard", "core"])
        mock_stale_guard.assert_awaited_once_with(sentinel_callback, sentinel_state)
        mock_core.assert_awaited_once()

        call_args = mock_core.call_args
        self.assertEqual(len(call_args.args), 4)
        self.assertIs(call_args.args[0], sentinel_callback)
        self.assertIs(call_args.args[1], sentinel_state)
        self.assertIs(call_args.args[2], sentinel_user)
        self.assertIs(call_args.args[3], sentinel_bot)

        expected_warning_confirm_data = TextOfferActionCallback(
            action="confirm_warning"
        ).pack()
        expected_cancel_data = TextOfferActionCallback(action="cancel").pack()
        self.assertEqual(expected_warning_confirm_data, "text_offer:confirm_warning")
        self.assertEqual(expected_cancel_data, "text_offer:cancel")

        self.assertEqual(len(call_args.kwargs), 6)
        self.assertIs(
            call_args.kwargs["check_user_limits_fn"],
            core.utils.check_user_limits,
        )
        self.assertIs(
            call_args.kwargs["to_jalali_str_fn"],
            core.utils.to_jalali_str,
        )
        self.assertEqual(
            call_args.kwargs["success_message_text"],
            "✅ لفظ شما با موفقیت در کانال منتشر شد!",
        )
        self.assertEqual(
            call_args.kwargs["unexpected_error_prefix"],
            "❌ خطا در ارسال به کانال",
        )
        self.assertEqual(
            call_args.kwargs["warning_confirm_callback_data"],
            expected_warning_confirm_data,
        )
        self.assertEqual(
            call_args.kwargs["cancel_callback_data"],
            expected_cancel_data,
        )
        self.assertNotIn("warning_acknowledged", call_args.kwargs)

    async def test_warning_confirm_delegates_exactly_with_warning_acknowledged(
        self,
    ):
        sentinel_callback = object()
        sentinel_state = object()
        sentinel_user = object()
        sentinel_bot = object()

        call_order: list[str] = []

        async def fake_stale_guard(callback, state):
            call_order.append("stale_guard")
            return False

        async def fake_core(*args, **kwargs):
            call_order.append("core")
            return None

        with (
            mock.patch(
                "bot.handlers.trade_create._reject_stale_text_offer_callback",
                new_callable=mock.AsyncMock,
                side_effect=fake_stale_guard,
            ) as mock_stale_guard,
            mock.patch(
                "bot.handlers.trade_create._handle_trade_confirm_core",
                new_callable=mock.AsyncMock,
                side_effect=fake_core,
            ) as mock_core,
        ):
            result = await handle_text_offer_warning_confirm(
                sentinel_callback,
                sentinel_state,
                sentinel_user,
                sentinel_bot,
            )

        self.assertIsNone(result)
        self.assertEqual(call_order, ["stale_guard", "core"])
        mock_stale_guard.assert_awaited_once_with(sentinel_callback, sentinel_state)
        mock_core.assert_awaited_once()

        call_args = mock_core.call_args
        self.assertEqual(len(call_args.args), 4)
        self.assertIs(call_args.args[0], sentinel_callback)
        self.assertIs(call_args.args[1], sentinel_state)
        self.assertIs(call_args.args[2], sentinel_user)
        self.assertIs(call_args.args[3], sentinel_bot)

        expected_warning_confirm_data = TextOfferActionCallback(
            action="confirm_warning"
        ).pack()
        expected_cancel_data = TextOfferActionCallback(action="cancel").pack()
        self.assertEqual(expected_warning_confirm_data, "text_offer:confirm_warning")
        self.assertEqual(expected_cancel_data, "text_offer:cancel")

        self.assertEqual(len(call_args.kwargs), 7)
        self.assertIs(
            call_args.kwargs["check_user_limits_fn"],
            core.utils.check_user_limits,
        )
        self.assertIs(
            call_args.kwargs["to_jalali_str_fn"],
            core.utils.to_jalali_str,
        )
        self.assertEqual(
            call_args.kwargs["success_message_text"],
            "✅ لفظ شما با موفقیت در کانال منتشر شد!",
        )
        self.assertEqual(
            call_args.kwargs["unexpected_error_prefix"],
            "❌ خطا در ارسال به کانال",
        )
        self.assertEqual(
            call_args.kwargs["warning_confirm_callback_data"],
            expected_warning_confirm_data,
        )
        self.assertEqual(
            call_args.kwargs["cancel_callback_data"],
            expected_cancel_data,
        )
        self.assertIs(call_args.kwargs["warning_acknowledged"], True)


if __name__ == "__main__":
    unittest.main()
