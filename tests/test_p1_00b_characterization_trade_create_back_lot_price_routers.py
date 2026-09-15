"""Characterization tests for trade creation back-to-lot-type and back-to-price callback query routers.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Seeds and Handler Symbols:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-BACK-TO-LOT-TYPE-L2221 (bot.handlers.trade_create.handle_back_to_lot_type)
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-BACK-TO-PRICE-L2234 (bot.handlers.trade_create.handle_back_to_price)

Scenarios Covered:
1. Scenario 1 (back_to_lot_type stale): dispatch TradeActionCallback(action="back_to_lot_type") for an
   authenticated runtime user while state.get_state() returns Trade.awaiting_notes.state, an invalid state for
   this handler.
2. Scenario 2 (back_to_lot_type current): dispatch TradeActionCallback(action="back_to_lot_type") for an
   authenticated runtime user with raw_state and state.get_state() both equal to Trade.awaiting_price.state.
3. Scenario 3 (back_to_price current): dispatch TradeActionCallback(action="back_to_price") with raw_state
   Trade.awaiting_notes.state for an authenticated runtime user.

Exclusions:
- Excludes neighboring direct-call handlers back_to_settlement, back_to_commodity, and back_to_quantity.
- Excludes helper internals (e.g. _show_price_prompt implementation, keyboard builder internals).
- Excludes database, network, and runtime access.
- Excludes custom Session adapter.
- Excludes inventory reconciliation.

Hermetic boundary disclaimer:
This partial characterization unit closes no seed, blocker, reconciliation, or Stage.
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

from bot.callbacks import TradeActionCallback
from bot.handlers import trade_create
from bot.states import Trade


class TestTradeCreateBackLotPriceRoutersCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_scenario_1_back_to_lot_type_stale(self):
        callback = aiogram_types.CallbackQuery(
            id="query_s1",
            from_user=aiogram_types.User(
                id=2002,
                is_bot=False,
                first_name="RuntimeUser",
            ),
            chat_instance="instance_s1",
            message=aiogram_types.Message(
                message_id=501,
                date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
                chat=aiogram_types.Chat(id=2002, type="private"),
                from_user=aiogram_types.User(
                    id=100,
                    is_bot=True,
                    first_name="TestBot",
                ),
                text="Prompt message",
            ),
            data=TradeActionCallback(action="back_to_lot_type").pack(),
        )
        runtime_user = SimpleNamespace(id=2002)

        mock_state = SimpleNamespace(
            get_state=mock.AsyncMock(return_value=Trade.awaiting_notes.state),
            set_state=mock.AsyncMock(),
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer,
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_cb_answer,
        ):
            result = await trade_create.router.propagate_event(
                "callback_query",
                callback,
                state=mock_state,
                user=runtime_user,
                raw_state=Trade.awaiting_notes.state,
            )

        self.assertIsNone(result)
        mock_state.get_state.assert_awaited_once_with()
        mock_answer.assert_awaited_once_with(
            callback,
            "این مرحله دیگر فعال نیست.",
            show_alert=True,
        )
        mock_get_keyboard.assert_not_called()
        mock_edit.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()
        mock_cb_answer.assert_not_called()

    async def test_scenario_2_back_to_lot_type_current(self):
        callback = aiogram_types.CallbackQuery(
            id="query_s2",
            from_user=aiogram_types.User(
                id=2002,
                is_bot=False,
                first_name="RuntimeUser",
            ),
            chat_instance="instance_s2",
            message=aiogram_types.Message(
                message_id=502,
                date=datetime(2026, 9, 15, 12, 1, 0, tzinfo=timezone.utc),
                chat=aiogram_types.Chat(id=2002, type="private"),
                from_user=aiogram_types.User(
                    id=100,
                    is_bot=True,
                    first_name="TestBot",
                ),
                text="Prompt message",
            ),
            data=TradeActionCallback(action="back_to_lot_type").pack(),
        )
        runtime_user = SimpleNamespace(id=2002)
        sentinel_keyboard = object()

        observed_order: list[str] = []

        async def _fake_get_state():
            observed_order.append("get_state")
            return Trade.awaiting_price.state

        def _fake_get_lot_type_keyboard():
            observed_order.append("get_lot_type_keyboard")
            return sentinel_keyboard

        async def _fake_edit(*args, **kwargs):
            observed_order.append("edit_callback_message_via_runtime")
            return None

        async def _fake_set_state(*args, **kwargs):
            observed_order.append("state.set_state")
            return None

        async def _fake_answer(*args, **kwargs):
            observed_order.append("answer_callback_query_via_runtime")
            return None

        mock_state = SimpleNamespace(
            get_state=mock.AsyncMock(side_effect=_fake_get_state),
            set_state=mock.AsyncMock(side_effect=_fake_set_state),
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
                side_effect=_fake_get_lot_type_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_fake_edit,
            ) as mock_edit,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_fake_answer,
            ) as mock_answer,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_cb_answer,
        ):
            result = await trade_create.router.propagate_event(
                "callback_query",
                callback,
                state=mock_state,
                user=runtime_user,
                raw_state=Trade.awaiting_price.state,
            )

        self.assertIsNone(result)
        self.assertEqual(
            observed_order,
            [
                "get_state",
                "get_lot_type_keyboard",
                "edit_callback_message_via_runtime",
                "state.set_state",
                "answer_callback_query_via_runtime",
            ],
        )
        mock_state.get_state.assert_awaited_once_with()
        mock_get_keyboard.assert_called_once_with()
        mock_edit.assert_awaited_once_with(
            callback,
            runtime_user,
            "نحوه معامله را انتخاب کنید:",
            reply_markup=sentinel_keyboard,
        )
        mock_state.set_state.assert_awaited_once_with(Trade.awaiting_lot_type)
        mock_answer.assert_awaited_once_with(callback)
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()
        mock_cb_answer.assert_not_called()

    async def test_scenario_3_back_to_price_current(self):
        message_obj = aiogram_types.Message(
            message_id=503,
            date=datetime(2026, 9, 15, 12, 2, 0, tzinfo=timezone.utc),
            chat=aiogram_types.Chat(id=2002, type="private"),
            from_user=aiogram_types.User(
                id=100,
                is_bot=True,
                first_name="TestBot",
            ),
            text="Notes prompt message",
        )
        callback = aiogram_types.CallbackQuery(
            id="query_s3",
            from_user=aiogram_types.User(
                id=2002,
                is_bot=False,
                first_name="RuntimeUser",
            ),
            chat_instance="instance_s3",
            message=message_obj,
            data=TradeActionCallback(action="back_to_price").pack(),
        )
        runtime_user = SimpleNamespace(id=2002)

        observed_order: list[str] = []

        async def _fake_show_price_prompt(*args, **kwargs):
            observed_order.append("show_price_prompt")
            return None

        async def _fake_answer(*args, **kwargs):
            observed_order.append("answer_callback_query_via_runtime")
            return None

        mock_state = SimpleNamespace(
            get_state=mock.AsyncMock(return_value=Trade.awaiting_notes.state),
            set_state=mock.AsyncMock(),
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create._show_price_prompt",
                new_callable=mock.AsyncMock,
                side_effect=_fake_show_price_prompt,
            ) as mock_show_price_prompt,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_fake_answer,
            ) as mock_answer,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_cb_answer,
        ):
            result = await trade_create.router.propagate_event(
                "callback_query",
                callback,
                state=mock_state,
                user=runtime_user,
                raw_state=Trade.awaiting_notes.state,
            )

        self.assertIsNone(result)
        self.assertEqual(
            observed_order,
            ["show_price_prompt", "answer_callback_query_via_runtime"],
        )
        mock_show_price_prompt.assert_awaited_once_with(
            callback.message,
            mock_state,
            edit=True,
            user=runtime_user,
        )
        mock_answer.assert_awaited_once_with(callback)
        mock_cb_answer.assert_not_called()
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()
        mock_state.set_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
