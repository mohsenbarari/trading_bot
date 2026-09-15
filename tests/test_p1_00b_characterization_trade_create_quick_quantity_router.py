"""Characterization tests for registered quick quantity callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Exact seed characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-QUICK-QUANTITY-L869

This unit executes actual registered callback-router dispatch for the
three active-FSM scenarios:
1. valid numeric callback with user=None short-circuits without settings, state, keyboard, or interaction work
2. eligible user with non-numeric callback value receives the exact invalid-quantity alert before settings or state work
3. eligible user with valid in-range numeric callback advances through the exact settings/state/acknowledgement/keyboard/edit ordering to Trade.awaiting_lot_type

This partial characterization unit executes registered router dispatch under live
aiogram filter rules. These assertions do not close any seed, blocker
(P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE), reconciliation, or Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest import mock

from aiogram import types as aiogram_types

from bot.callbacks import QuantityCallback
from bot.handlers import trade_create
from bot.states import Trade
from core.trading_settings import TradingSettings


class TestTradeCreateQuickQuantityRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_quick_quantity_router_unauthenticated_active_state_short_circuit(
        self,
    ):
        packed_data = QuantityCallback(value="20").pack()
        chat = aiogram_types.Chat(id=1001, type="private")
        message = aiogram_types.Message(
            message_id=2001,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-unauth-qty",
            from_user=aiogram_types.User(
                id=999,
                is_bot=False,
                first_name="UnauthUser",
            ),
            chat_instance="test_chat_instance_unauth",
            message=message,
            data=packed_data,
        )

        state = SimpleNamespace(
            get_data=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "core.trading_settings.get_trading_settings_async",
                new_callable=mock.AsyncMock,
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
            ) as mock_get_lot_type_keyboard,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_callback_answer,
        ):
            result = await trade_create.router.propagate_event(
                "callback_query",
                event,
                state=state,
                user=None,
                raw_state=Trade.awaiting_quantity.state,
            )

        self.assertIsNone(result)
        mock_get_trading_settings.assert_not_called()
        state.get_data.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_get_lot_type_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_answer_runtime.assert_not_called()
        mock_callback_answer.assert_not_called()

    async def test_quick_quantity_router_invalid_numeric_rejected_before_settings_or_state_work(
        self,
    ):
        packed_data = QuantityCallback(value="not-a-number").pack()
        chat = aiogram_types.Chat(id=1002, type="private")
        message = aiogram_types.Message(
            message_id=2002,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-auth-invalid-qty",
            from_user=aiogram_types.User(
                id=54321,
                is_bot=False,
                first_name="EligibleUser",
            ),
            chat_instance="test_chat_instance_auth",
            message=message,
            data=packed_data,
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")
        state = SimpleNamespace(
            get_data=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "core.trading_settings.get_trading_settings_async",
                new_callable=mock.AsyncMock,
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
            ) as mock_get_lot_type_keyboard,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_callback_answer,
        ):
            result = await trade_create.router.propagate_event(
                "callback_query",
                event,
                state=state,
                user=runtime_user,
                raw_state=Trade.awaiting_quantity.state,
            )

        self.assertIsNone(result)
        mock_answer_runtime.assert_awaited_once_with(
            event,
            "تعداد نامعتبر است.",
            show_alert=True,
        )
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(mock_answer_runtime.await_args.args[1], "تعداد نامعتبر است.")
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {"show_alert": True})

        mock_get_trading_settings.assert_not_called()
        state.get_data.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_get_lot_type_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_callback_answer.assert_not_called()

    async def test_quick_quantity_router_eligible_valid_numeric_ordering_through_awaiting_lot_type(
        self,
    ):
        packed_data = QuantityCallback(value="20").pack()
        chat = aiogram_types.Chat(id=1003, type="private")
        message = aiogram_types.Message(
            message_id=2003,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-auth-valid-qty",
            from_user=aiogram_types.User(
                id=54321,
                is_bot=False,
                first_name="EligibleUser",
            ),
            chat_instance="test_chat_instance_auth",
            message=message,
            data=packed_data,
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")
        keyboard_sentinel = aiogram_types.InlineKeyboardMarkup(inline_keyboard=[])

        call_order: list[str] = []

        async def track_get_trading_settings():
            call_order.append("get_trading_settings_async")
            return TradingSettings(offer_min_quantity=5, offer_max_quantity=50)

        async def track_get_data():
            call_order.append("state.get_data")
            return {
                "trade_type_fa": "🟢 خرید",
                "commodity_name": "سکه",
                "wizard_return_to_review": False,
            }

        async def track_update_data(**kwargs):
            call_order.append(f"state.update_data(quantity={kwargs.get('quantity')})")

        async def track_answer_runtime(*args, **kwargs):
            call_order.append("answer_callback_query_via_runtime")

        def track_get_lot_type_keyboard(*args, **kwargs):
            call_order.append("get_lot_type_keyboard")
            return keyboard_sentinel

        async def track_edit_runtime(*args, **kwargs):
            call_order.append("edit_callback_message_via_runtime")

        async def track_set_state(target_state):
            call_order.append("state.set_state(Trade.awaiting_lot_type)")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(side_effect=track_update_data),
            set_state=mock.AsyncMock(side_effect=track_set_state),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "core.trading_settings.get_trading_settings_async",
                new_callable=mock.AsyncMock,
                side_effect=track_get_trading_settings,
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
                side_effect=track_get_lot_type_keyboard,
            ) as mock_get_lot_type_keyboard,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=track_edit_runtime,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=track_answer_runtime,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_callback_answer,
        ):
            result = await trade_create.router.propagate_event(
                "callback_query",
                event,
                state=state,
                user=runtime_user,
                raw_state=Trade.awaiting_quantity.state,
            )

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "get_trading_settings_async",
                "state.get_data",
                "state.update_data(quantity=20)",
                "answer_callback_query_via_runtime",
                "get_lot_type_keyboard",
                "edit_callback_message_via_runtime",
                "state.set_state(Trade.awaiting_lot_type)",
            ],
        )

        mock_get_trading_settings.assert_awaited_once_with()
        state.get_data.assert_awaited_once_with()
        state.update_data.assert_awaited_once_with(quantity=20)

        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(len(mock_answer_runtime.await_args.args), 1)
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {})

        mock_get_lot_type_keyboard.assert_called_once_with()

        expected_edit_text = (
            "📈 **ثبت لفظ جدید**\n\n"
            "نوع معامله: 🟢 خرید\n"
            "کالا: سکه\n"
            "تعداد: 20\n\n"
            "📦 نحوه معامله را انتخاب کنید:"
        )
        mock_edit_runtime.assert_awaited_once_with(
            event,
            runtime_user,
            expected_edit_text,
            parse_mode="Markdown",
            reply_markup=keyboard_sentinel,
        )
        self.assertIs(mock_edit_runtime.await_args.args[0], event)
        self.assertIs(mock_edit_runtime.await_args.args[1], runtime_user)
        self.assertEqual(mock_edit_runtime.await_args.args[2], expected_edit_text)
        self.assertEqual(
            mock_edit_runtime.await_args.kwargs,
            {
                "parse_mode": "Markdown",
                "reply_markup": keyboard_sentinel,
            },
        )
        self.assertIs(
            mock_edit_runtime.await_args.kwargs["reply_markup"],
            keyboard_sentinel,
        )

        state.set_state.assert_awaited_once_with(Trade.awaiting_lot_type)
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_callback_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
