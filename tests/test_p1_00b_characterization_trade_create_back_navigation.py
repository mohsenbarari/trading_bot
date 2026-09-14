"""Characterization tests for Trade wizard back-navigation callback handlers."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import types
import unittest
from unittest import mock

from bot.handlers.trade_create import (
    handle_back_to_commodity,
    handle_back_to_quantity,
    handle_back_to_settlement,
)
from bot.states import Trade


class TestTradeCreateBackNavigationCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_handle_back_to_settlement_authenticated_success(self):
        callback = object()
        user = object()
        keyboard_sentinel = object()
        observed_order: list[str] = []

        def fake_get_settlement_keyboard():
            observed_order.append("keyboard construction")
            return keyboard_sentinel

        async def fake_edit(*args, **kwargs):
            observed_order.append("edit")
            return None

        async def fake_set_state(*args, **kwargs):
            observed_order.append("state transition")
            return None

        async def fake_answer(*args, **kwargs):
            observed_order.append("acknowledgement")
            return None

        state = mock.AsyncMock()
        state.set_state.side_effect = fake_set_state

        with mock.patch(
            "bot.handlers.trade_create.get_settlement_type_keyboard",
            side_effect=fake_get_settlement_keyboard,
        ) as mock_get_keyboard, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_edit,
        ) as mock_edit, mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer:
            result = await handle_back_to_settlement(callback, state, user)

        mock_get_keyboard.assert_called_once_with()
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            "نوع تسویه را انتخاب کنید:",
            reply_markup=keyboard_sentinel,
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_settlement_type)
        mock_answer.assert_awaited_once_with(callback)
        self.assertEqual(
            state.mock_calls,
            [mock.call.set_state(Trade.awaiting_settlement_type)],
        )
        self.assertEqual(
            observed_order,
            ["keyboard construction", "edit", "state transition", "acknowledgement"],
        )
        self.assertIsNone(result)

    async def test_handle_back_to_commodity_authenticated_success(self):
        callback = object()
        user = object()
        keyboard_sentinel = object()
        observed_order: list[str] = []

        async def fake_get_data():
            observed_order.append("data read")
            return {"trade_type": "sell"}

        async def fake_get_commodities_keyboard(trade_type):
            observed_order.append("keyboard construction")
            return keyboard_sentinel

        async def fake_edit(*args, **kwargs):
            observed_order.append("edit")
            return None

        async def fake_set_state(*args, **kwargs):
            observed_order.append("state transition")
            return None

        async def fake_answer(*args, **kwargs):
            observed_order.append("acknowledgement")
            return None

        state = mock.AsyncMock()
        state.get_data.side_effect = fake_get_data
        state.set_state.side_effect = fake_set_state

        with mock.patch(
            "bot.handlers.trade_create.get_commodities_keyboard",
            new_callable=mock.AsyncMock,
            side_effect=fake_get_commodities_keyboard,
        ) as mock_get_keyboard, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_edit,
        ) as mock_edit, mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer:
            result = await handle_back_to_commodity(callback, state, user)

        state.get_data.assert_awaited_once_with()
        mock_get_keyboard.assert_awaited_once_with("sell")
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            "کالای مورد نظر را انتخاب کنید:",
            reply_markup=keyboard_sentinel,
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_commodity)
        mock_answer.assert_awaited_once_with(callback)
        self.assertEqual(
            state.mock_calls,
            [mock.call.get_data(), mock.call.set_state(Trade.awaiting_commodity)],
        )
        self.assertEqual(
            observed_order,
            ["data read", "keyboard construction", "edit", "state transition", "acknowledgement"],
        )
        self.assertIsNone(result)

    async def test_handle_back_to_quantity_authenticated_success(self):
        callback = object()
        user = object()
        keyboard_sentinel = object()
        observed_order: list[str] = []
        settings_sentinel = types.SimpleNamespace(
            offer_min_quantity=7,
            offer_max_quantity=43,
        )

        async def fake_get_trading_settings_async():
            observed_order.append("settings read")
            return settings_sentinel

        def fake_get_quantity_keyboard(*args, **kwargs):
            observed_order.append("keyboard construction")
            return keyboard_sentinel

        async def fake_edit(*args, **kwargs):
            observed_order.append("edit")
            return None

        async def fake_set_state(*args, **kwargs):
            observed_order.append("state transition")
            return None

        async def fake_answer(*args, **kwargs):
            observed_order.append("acknowledgement")
            return None

        state = mock.AsyncMock()
        state.set_state.side_effect = fake_set_state

        with mock.patch(
            "core.trading_settings.get_trading_settings_async",
            new_callable=mock.AsyncMock,
            side_effect=fake_get_trading_settings_async,
        ) as mock_get_settings, mock.patch(
            "bot.handlers.trade_create.get_quantity_keyboard",
            side_effect=fake_get_quantity_keyboard,
        ) as mock_get_keyboard, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_edit,
        ) as mock_edit, mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer:
            result = await handle_back_to_quantity(callback, state, user)

        mock_get_settings.assert_awaited_once_with()
        mock_get_keyboard.assert_called_once_with(
            min_quantity=7,
            max_quantity=43,
        )
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            "تعداد را انتخاب کنید یا عدد دلخواه را وارد کنید:",
            reply_markup=keyboard_sentinel,
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_quantity)
        mock_answer.assert_awaited_once_with(callback)
        self.assertEqual(
            state.mock_calls,
            [mock.call.set_state(Trade.awaiting_quantity)],
        )
        self.assertEqual(
            observed_order,
            ["settings read", "keyboard construction", "edit", "state transition", "acknowledgement"],
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
