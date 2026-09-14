"""Characterization tests for Trade wizard cancel callback handler."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from unittest import mock

from bot.handlers.trade_create import handle_wizard_cancel
from bot.states import Trade


class TradeCreateWizardCancelCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_current_wizard_clears_then_acknowledges_and_edits(self):
        callback = object()
        user = object()
        observed_order: list[str] = []

        async def fake_get_state():
            observed_order.append("state read")
            return Trade.awaiting_wizard_review.state

        async def fake_clear():
            observed_order.append("state clear")
            return None

        async def fake_answer(*args, **kwargs):
            observed_order.append("acknowledgement")
            return None

        async def fake_edit(*args, **kwargs):
            observed_order.append("edit")
            return None

        state = mock.AsyncMock()
        state.get_state.side_effect = fake_get_state
        state.clear.side_effect = fake_clear

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_edit,
        ) as mock_edit:
            result = await handle_wizard_cancel(callback, state, user)

        self.assertIsNone(result)
        state.get_state.assert_awaited_once_with()
        state.clear.assert_awaited_once_with()
        mock_answer.assert_awaited_once_with(callback)
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            "❌ فرآیند ثبت آفر لغو شد.",
        )
        self.assertEqual(
            state.mock_calls,
            [mock.call.get_state(), mock.call.clear()],
        )
        self.assertEqual(
            observed_order,
            ["state read", "state clear", "acknowledgement", "edit"],
        )

    async def test_missing_user_returns_without_state_or_delivery(self):
        callback = object()
        state = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_edit:
            result = await handle_wizard_cancel(callback, state, None)

        self.assertIsNone(result)
        state.get_state.assert_not_called()
        state.clear.assert_not_called()
        self.assertEqual(state.mock_calls, [])
        mock_answer.assert_not_called()
        mock_edit.assert_not_called()

    async def test_stale_wizard_alerts_without_clearing_or_editing(self):
        callback = object()
        user = object()
        observed_order: list[str] = []

        async def fake_get_state():
            observed_order.append("state read")
            return "Trade:awaiting_unknown_or_stale"

        async def fake_answer(*args, **kwargs):
            observed_order.append("alert")
            return None

        state = mock.AsyncMock()
        state.get_state.side_effect = fake_get_state

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_edit:
            result = await handle_wizard_cancel(callback, state, user)

        self.assertIsNone(result)
        state.get_state.assert_awaited_once_with()
        state.clear.assert_not_called()
        self.assertEqual(state.mock_calls, [mock.call.get_state()])
        mock_answer.assert_awaited_once_with(
            callback,
            "این فرآیند دیگر فعال نیست.",
            show_alert=True,
        )
        mock_edit.assert_not_called()
        self.assertEqual(
            observed_order,
            ["state read", "alert"],
        )


if __name__ == "__main__":
    unittest.main()
