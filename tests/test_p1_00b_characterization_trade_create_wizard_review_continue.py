"""Characterization tests for Trade wizard return-to-review and continue handlers.

Exact seeds characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-WIZARD-RETURN-TO-REVIEW-L1346
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-WIZARD-CONTINUE-L1361

These tests capture existing branch results for complete and incomplete return-to-review,
and whitespace-only continue. They make no claim of closing either seed, the canonical
blocker P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE, or Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest import mock

from bot.handlers.trade_create import (
    handle_wizard_continue,
    handle_wizard_return_to_review,
)
from bot.states import Trade

SEED_RETURN_TO_REVIEW = "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-WIZARD-RETURN-TO-REVIEW-L1346"
SEED_CONTINUE = "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-WIZARD-CONTINUE-L1361"


class TradeCreateWizardReviewContinueCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_return_to_review_renders_wizard_review(self):
        callback = SimpleNamespace(message=SimpleNamespace())
        user = SimpleNamespace(id=42, username="trader_actor")
        observed_order: list[str] = []

        valid_draft = {
            "trade_type": "buy",
            "settlement_type": "cash",
            "commodity_id": 1,
            "commodity_name": "سکه تمام",
            "quantity": 10,
            "price": 50000000,
            "is_wholesale": True,
            "lot_sizes": None,
        }

        async def fake_get_state():
            observed_order.append("get_state")
            return Trade.awaiting_wizard_review.state

        async def fake_get_data():
            observed_order.append("get_data")
            return valid_draft

        async def fake_answer(*args, **kwargs):
            observed_order.append("answer")
            return None

        async def fake_show_wizard_review(*args, **kwargs):
            observed_order.append("show_wizard_review")
            return None

        state = SimpleNamespace(
            get_state=mock.AsyncMock(side_effect=fake_get_state),
            get_data=mock.AsyncMock(side_effect=fake_get_data),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
            side_effect=fake_show_wizard_review,
        ) as mock_show_review:
            result = await handle_wizard_return_to_review(callback, state, user)

        self.assertIsNone(result)
        state.get_state.assert_awaited_once_with()
        state.get_data.assert_awaited_once_with()
        mock_answer.assert_awaited_once_with(callback)
        mock_show_review.assert_awaited_once_with(
            callback.message,
            state,
            edit=True,
            user=user,
        )
        self.assertEqual(
            observed_order,
            ["get_state", "get_data", "answer", "show_wizard_review"],
        )
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.clear.assert_not_called()

    async def test_incomplete_return_to_review_alerts_and_suppresses_render(self):
        callback = SimpleNamespace(message=SimpleNamespace())
        user = SimpleNamespace(id=42, username="trader_actor")
        observed_order: list[str] = []

        incomplete_draft = {
            "trade_type": "buy",
            "settlement_type": "cash",
            "commodity_id": 1,
            "commodity_name": "سکه تمام",
            "quantity": 10,
            "is_wholesale": True,
            "lot_sizes": None,
        }

        async def fake_get_state():
            observed_order.append("get_state")
            return Trade.awaiting_wizard_review.state

        async def fake_get_data():
            observed_order.append("get_data")
            return incomplete_draft

        async def fake_answer(*args, **kwargs):
            observed_order.append("alert")
            return None

        state = SimpleNamespace(
            get_state=mock.AsyncMock(side_effect=fake_get_state),
            get_data=mock.AsyncMock(side_effect=fake_get_data),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_show_review:
            result = await handle_wizard_return_to_review(callback, state, user)

        self.assertIsNone(result)
        state.get_state.assert_awaited_once_with()
        state.get_data.assert_awaited_once_with()
        mock_answer.assert_awaited_once_with(
            callback,
            "اطلاعات این آفر کامل نیست؛ فرآیند را از ابتدا شروع کنید.",
            show_alert=True,
        )
        mock_show_review.assert_not_called()
        self.assertEqual(observed_order, ["get_state", "get_data", "alert"])
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.clear.assert_not_called()

    async def test_whitespace_continue_alerts_and_suppresses_prepare(self):
        callback = SimpleNamespace(message=SimpleNamespace())
        user = SimpleNamespace(id=42, username="trader_actor")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(
                return_value={"generated_offer_text": "   \n\t  "}
            ),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_prepare:
            result = await handle_wizard_continue(callback, state, user)

        self.assertIsNone(result)
        state.get_data.assert_awaited_once_with()
        mock_answer.assert_awaited_once_with(
            callback,
            "متن آفر تولید نشده است.",
            show_alert=True,
        )
        mock_prepare.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.clear.assert_not_called()


if __name__ == "__main__":
    unittest.main()
