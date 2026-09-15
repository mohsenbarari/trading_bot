"""Characterization tests for registered trade type callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Exact seed characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TRADE-TYPE-SELECTION-L709

This unit executes actual registered callback-router dispatch for the
two active-FSM scenarios:
1. unauthenticated short-circuiting
2. eligible-user buy-selection ordering through the settlement-type transition

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

from bot.callbacks import TradeTypeCallback
from bot.handlers import trade_create
from bot.states import Trade


class TestTradeCreateTradeTypeRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_trade_type_router_unauthenticated_active_state_short_circuit(self):
        packed_data = TradeTypeCallback(type="buy").pack()
        chat = aiogram_types.Chat(id=1001, type="private")
        message = aiogram_types.Message(
            message_id=2001,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-unauth-trade-type-buy",
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
        )

        with (
            mock.patch(
                "bot.handlers.trade_create.get_settlement_type_keyboard",
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_create._answer_stale_trade_creation_callback",
                new_callable=mock.AsyncMock,
            ) as mock_stale_answer,
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
                raw_state=Trade.awaiting_trade_type.state,
            )

        self.assertIsNone(result)
        state.get_data.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        mock_get_keyboard.assert_not_called()
        mock_answer_runtime.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_callback_answer.assert_not_called()
        mock_stale_answer.assert_not_called()

    async def test_trade_type_router_eligible_buy_success_ordering(self):
        packed_data = TradeTypeCallback(type="buy").pack()
        chat = aiogram_types.Chat(id=1002, type="private")
        message = aiogram_types.Message(
            message_id=2002,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-auth-trade-type-buy",
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

        async def track_get_data():
            call_order.append("get_data")
            return {"wizard_return_to_review": False}

        async def track_update_data(**kwargs):
            call_order.append("update_data")

        async def track_answer_runtime(cb, *args, **kwargs):
            call_order.append("answer_callback_query_via_runtime")

        def track_get_keyboard(*args, **kwargs):
            call_order.append("get_settlement_type_keyboard")
            return keyboard_sentinel

        async def track_edit_runtime(cb, usr, text, *args, **kwargs):
            call_order.append("edit_callback_message_via_runtime")

        async def track_set_state(st):
            call_order.append("set_state")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(side_effect=track_update_data),
            set_state=mock.AsyncMock(side_effect=track_set_state),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create.get_settlement_type_keyboard",
                side_effect=track_get_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                side_effect=track_answer_runtime,
            ) as mock_answer_runtime,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                side_effect=track_edit_runtime,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_create._answer_stale_trade_creation_callback",
                new_callable=mock.AsyncMock,
            ) as mock_stale_answer,
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
                raw_state=Trade.awaiting_trade_type.state,
            )

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "get_data",
                "update_data",
                "answer_callback_query_via_runtime",
                "get_settlement_type_keyboard",
                "edit_callback_message_via_runtime",
                "set_state",
            ],
        )

        state.get_data.assert_awaited_once_with()
        state.update_data.assert_awaited_once_with(
            trade_type="buy",
            trade_type_fa="🟢 خرید",
        )

        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)

        mock_get_keyboard.assert_called_once_with()

        expected_edit_text = (
            "📈 **ثبت لفظ جدید**\n\n"
            "نوع معامله: 🟢 خرید\n\n"
            "نوع تسویه را انتخاب کنید:"
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

        state.set_state.assert_awaited_once_with(Trade.awaiting_settlement_type)
        self.assertIs(
            state.set_state.await_args.args[0],
            Trade.awaiting_settlement_type,
        )

        mock_callback_answer.assert_not_called()
        mock_stale_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
