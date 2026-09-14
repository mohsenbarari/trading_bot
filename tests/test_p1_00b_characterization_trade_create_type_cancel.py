"""Characterization tests for Trade wizard back-to-type and trade-cancel callback handlers."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from unittest import mock

from bot.handlers.trade_create import (
    handle_back_to_type,
    handle_trade_cancel,
)
from bot.states import Trade


class TestTradeCreateTypeCancelCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_handlers_unauthenticated_guard(self):
        callback_type = object()
        state_type = mock.AsyncMock()

        callback_cancel = object()
        state_cancel = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.trade_create.get_trade_type_keyboard",
        ) as mock_get_keyboard, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_edit, mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer:
            result_type = await handle_back_to_type(callback_type, state_type, None)
            result_cancel = await handle_trade_cancel(callback_cancel, state_cancel, None)

        self.assertIsNone(result_type)
        self.assertIsNone(result_cancel)

        self.assertEqual(mock_answer.await_count, 2)
        mock_answer.assert_has_awaits([
            mock.call(callback_type),
            mock.call(callback_cancel),
        ])

        mock_get_keyboard.assert_not_called()
        mock_edit.assert_not_called()

        self.assertEqual(state_type.mock_calls, [])
        state_type.update_data.assert_not_called()
        state_type.set_state.assert_not_called()

        self.assertEqual(state_cancel.mock_calls, [])
        state_cancel.get_state.assert_not_called()
        state_cancel.clear.assert_not_called()

    async def test_handle_back_to_type_authenticated_success(self):
        callback = object()
        user = object()
        keyboard_sentinel = object()
        observed_order: list[str] = []

        async def fake_update_data(*args, **kwargs):
            observed_order.append("update data")
            return None

        def fake_get_trade_type_keyboard():
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
        state.update_data.side_effect = fake_update_data
        state.set_state.side_effect = fake_set_state

        with mock.patch(
            "bot.handlers.trade_create.get_trade_type_keyboard",
            side_effect=fake_get_trade_type_keyboard,
        ) as mock_get_keyboard, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_edit,
        ) as mock_edit, mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer:
            result = await handle_back_to_type(callback, state, user)

        state.update_data.assert_awaited_once_with(wizard_return_to_review=False)
        mock_get_keyboard.assert_called_once_with()
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            "📈 **ثبت لفظ جدید**\n\nنوع معامله را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=keyboard_sentinel,
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_trade_type)
        mock_answer.assert_awaited_once_with(callback)
        self.assertEqual(
            state.mock_calls,
            [
                mock.call.update_data(wizard_return_to_review=False),
                mock.call.set_state(Trade.awaiting_trade_type),
            ],
        )
        self.assertEqual(
            observed_order,
            ["update data", "keyboard construction", "edit", "state transition", "acknowledgement"],
        )
        self.assertIsNone(result)

    async def test_handle_trade_cancel_authenticated_success(self):
        callback = object()
        user = object()
        observed_order: list[str] = []

        async def fake_get_state():
            observed_order.append("get state")
            return Trade.awaiting_price.state

        async def fake_clear():
            observed_order.append("clear")
            return None

        async def fake_edit(*args, **kwargs):
            observed_order.append("edit")
            return None

        async def fake_answer(*args, **kwargs):
            observed_order.append("acknowledgement")
            return None

        state = mock.AsyncMock()
        state.get_state.side_effect = fake_get_state
        state.clear.side_effect = fake_clear

        with mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_edit,
        ) as mock_edit, mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer:
            result = await handle_trade_cancel(callback, state, user)

        state.get_state.assert_awaited_once_with()
        state.clear.assert_awaited_once_with()
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            "❌ فرآیند ثبت لفظ لغو شد.",
        )
        mock_answer.assert_awaited_once_with(callback)
        self.assertEqual(
            state.mock_calls,
            [
                mock.call.get_state(),
                mock.call.clear(),
            ],
        )
        self.assertEqual(
            observed_order,
            ["get state", "clear", "edit", "acknowledgement"],
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
