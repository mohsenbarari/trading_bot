"""Characterization tests for Trade wizard edit handlers.

Exact seeds characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-WIZARD-EDIT-L1236
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-WIZARD-EDIT-FIELD-L1257

These tests characterize unauthenticated edit acknowledgement, pack edit menu controls,
and invalid edit field rejection under P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
They explicitly do not close either seed, the blocker, or Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest import mock

from bot.callbacks import TradeWizardEditCallback
from bot.handlers.trade_create import (
    handle_wizard_edit,
    handle_wizard_edit_field,
)
from bot.handlers.trade_utils import get_wizard_edit_keyboard
from bot.states import Trade
from core.pack_commodities import PACK_FULL_COMMODITY_NAME

SEED_WIZARD_EDIT = "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-WIZARD-EDIT-L1236"
SEED_WIZARD_EDIT_FIELD = "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-WIZARD-EDIT-FIELD-L1257"


class TradeCreateWizardEditCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_edit_acknowledges_without_state_or_delivery(self):
        callback = SimpleNamespace(message=SimpleNamespace())
        state = SimpleNamespace(
            get_data=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_edit:
            result = await handle_wizard_edit(callback, state, user=None)

        self.assertIsNone(result)
        mock_answer.assert_awaited_once_with(callback)
        state.get_data.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        mock_edit.assert_not_called()

    async def test_pack_edit_menu_suppresses_quantity_and_lot_controls(self):
        callback = SimpleNamespace(message=SimpleNamespace())
        user = SimpleNamespace(id=42, username="trader_actor")
        observed_order: list[str] = []

        async def fake_get_data():
            observed_order.append("get_data")
            return {
                "commodity_name": PACK_FULL_COMMODITY_NAME,
                "is_wholesale": True,
            }

        async def fake_answer(*args, **kwargs):
            observed_order.append("answer")
            return None

        async def fake_edit(*args, **kwargs):
            observed_order.append("edit")
            return None

        async def fake_set_state(*args, **kwargs):
            observed_order.append("set_state")
            return None

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=fake_get_data),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(side_effect=fake_set_state),
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_edit,
        ) as mock_edit:
            result = await handle_wizard_edit(callback, state, user=user)

        self.assertIsNone(result)
        state.get_data.assert_awaited_once_with()
        mock_answer.assert_awaited_once_with(callback)

        expected_markup = get_wizard_edit_keyboard(is_wholesale=True, is_pack=True)
        prompt_text = f"کدام بخش آفر را می{chr(0x200c)}خواهید اصلاح کنید؟"
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            prompt_text,
            reply_markup=expected_markup,
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_wizard_edit)
        self.assertEqual(
            observed_order,
            ["get_data", "answer", "edit", "set_state"],
        )
        state.update_data.assert_not_called()

        markup = mock_edit.await_args.kwargs["reply_markup"]
        labels = [button.text for row in markup.inline_keyboard for button in row]
        for label in ("خرید / فروش", "نقدی / فردایی", "کالا", "قیمت", "توضیحات", "🔙 بازگشت به متن آفر"):
            self.assertIn(label, labels)
        self.assertNotIn("تعداد", labels)
        self.assertNotIn("یکجا / خُرد", labels)
        self.assertNotIn(f"ترکیب بخش{chr(0x200c)}بندی", labels)

    async def test_invalid_edit_field_alerts_without_state_or_delivery(self):
        callback = SimpleNamespace(message=SimpleNamespace())
        user = SimpleNamespace(id=42, username="trader_actor")
        callback_data = TradeWizardEditCallback(field="unsupported_field")
        state = SimpleNamespace(
            get_data=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_edit:
            result = await handle_wizard_edit_field(callback, state, user, callback_data)

        self.assertIsNone(result)
        mock_answer.assert_awaited_once_with(
            callback,
            "گزینه اصلاح نامعتبر است.",
            show_alert=True,
        )
        state.get_data.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        mock_edit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
