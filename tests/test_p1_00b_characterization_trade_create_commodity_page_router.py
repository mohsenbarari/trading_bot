"""Characterization tests for registered commodity page callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Exact seed characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-COMMODITY-PAGE-L781

This unit executes actual registered callback-router dispatch for the
three active-FSM scenarios:
1. unauthenticated valid buy-page callback short-circuiting without state or interaction work
2. eligible user with mismatched trade_type receiving the expired page alert without keyboard or edit work
3. eligible user with matching buy trade_type and wizard_return_to_review=False receiving the requested page through exact keyboard, edit, and answer ordering

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

from bot.callbacks import PageCallback
from bot.handlers import trade_create
from bot.states import Trade


class TestTradeCreateCommodityPageRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_commodity_page_router_unauthenticated_active_state_short_circuit(
        self,
    ):
        packed_data = PageCallback(trade_type="buy", page=2).pack()
        chat = aiogram_types.Chat(id=1001, type="private")
        message = aiogram_types.Message(
            message_id=2001,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-unauth-page",
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
                "bot.handlers.trade_create.get_commodities_keyboard",
                new_callable=mock.AsyncMock,
            ) as mock_get_commodities_keyboard,
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
                raw_state=Trade.awaiting_commodity.state,
            )

        self.assertIsNone(result)
        state.get_data.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_get_commodities_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_answer_runtime.assert_not_called()
        mock_callback_answer.assert_not_called()

    async def test_commodity_page_router_eligible_stale_trade_type_rejected(
        self,
    ):
        packed_data = PageCallback(trade_type="sell", page=2).pack()
        chat = aiogram_types.Chat(id=1002, type="private")
        message = aiogram_types.Message(
            message_id=2002,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-auth-stale-page",
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
            get_data=mock.AsyncMock(
                return_value={
                    "trade_type": "buy",
                    "wizard_return_to_review": False,
                }
            ),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create.get_commodities_keyboard",
                new_callable=mock.AsyncMock,
            ) as mock_get_commodities_keyboard,
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
                raw_state=Trade.awaiting_commodity.state,
            )

        self.assertIsNone(result)
        state.get_data.assert_awaited_once_with()
        mock_answer_runtime.assert_awaited_once_with(
            event,
            "این صفحه منقضی شده است.",
            show_alert=True,
        )
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(mock_answer_runtime.await_args.args[1], "این صفحه منقضی شده است.")
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {"show_alert": True})

        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_get_commodities_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_callback_answer.assert_not_called()

    async def test_commodity_page_router_eligible_valid_buy_page_ordering(
        self,
    ):
        packed_data = PageCallback(trade_type="buy", page=3).pack()
        chat = aiogram_types.Chat(id=1003, type="private")
        message = aiogram_types.Message(
            message_id=2003,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-auth-valid-page",
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
            return {
                "trade_type": "buy",
                "wizard_return_to_review": False,
            }

        async def track_get_commodities_keyboard(*args, **kwargs):
            call_order.append("get_commodities_keyboard")
            return keyboard_sentinel

        async def track_edit_runtime(*args, **kwargs):
            call_order.append("edit_callback_message_via_runtime")

        async def track_answer_runtime(*args, **kwargs):
            call_order.append("answer_callback_query_via_runtime")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create.get_commodities_keyboard",
                new_callable=mock.AsyncMock,
                side_effect=track_get_commodities_keyboard,
            ) as mock_get_commodities_keyboard,
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
                raw_state=Trade.awaiting_commodity.state,
            )

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "get_data",
                "get_commodities_keyboard",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )

        state.get_data.assert_awaited_once_with()

        mock_get_commodities_keyboard.assert_awaited_once_with(
            "buy",
            page=3,
            return_to_review=False,
        )

        expected_edit_text = (
            "📈 **ثبت لفظ جدید**\n\n"
            "نوع معامله: 🟢 خرید\n\n"
            "کالای مورد نظر را انتخاب کنید:"
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

        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(len(mock_answer_runtime.await_args.args), 1)
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {})

        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_callback_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
