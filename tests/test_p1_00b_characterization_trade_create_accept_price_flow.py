"""Characterization tests for Trade wizard accept suggested lots and price input handlers.

Exact seeds characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-ACCEPT-SUGGESTED-LOTS-L1089
- BOT-TRADE-MESSAGE-TRADE-CREATE-HANDLE-PRICE-INPUT-L1118

Under Assignment WA-P1-00B-FREEZE-20260904-001 for blocker
P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest import mock

from bot.callbacks import AcceptLotsCallback
from bot.handlers.trade_create import (
    handle_accept_suggested_lots,
    handle_price_input,
)
from core.services.trade_service import normalize_offer_price_input

SEED_ACCEPT_SUGGESTED_LOTS = (
    "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-ACCEPT-SUGGESTED-LOTS-L1089"
)
SEED_PRICE_INPUT = (
    "BOT-TRADE-MESSAGE-TRADE-CREATE-HANDLE-PRICE-INPUT-L1118"
)


class TestTradeCreateAcceptPriceFlowCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_guards_combined(self):
        callback_accept = SimpleNamespace()
        state_accept = mock.AsyncMock()
        callback_data = AcceptLotsCallback(lots="10_20")

        message_price = SimpleNamespace(text="197")
        state_price = mock.AsyncMock()
        bot_sentinel = object()

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_callback, mock.patch(
            "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_handoff, mock.patch(
            "bot.handlers.trade_create.validate_lot_sizes",
        ) as mock_validate_lot_sizes, mock.patch(
            "bot.handlers.trade_create.normalize_offer_price_input",
        ) as mock_normalize_price, mock.patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_incoming, mock.patch(
            "bot.handlers.trade_create._show_price_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_price_prompt, mock.patch(
            "bot.handlers.trade_create._show_notes_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_notes_prompt, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_wizard_review:
            result_accept = await handle_accept_suggested_lots(
                callback_accept,
                state_accept,
                None,
                callback_data,
            )
            result_price = await handle_price_input(
                message_price,
                state_price,
                None,
                bot_sentinel,
            )

        self.assertIsNone(result_accept)
        self.assertIsNone(result_price)

        mock_answer_callback.assert_awaited_once_with(callback_accept)
        self.assertEqual(state_accept.mock_calls, [])
        self.assertEqual(state_price.mock_calls, [])

        mock_handoff.assert_not_called()
        mock_validate_lot_sizes.assert_not_called()
        mock_normalize_price.assert_not_called()
        mock_answer_incoming.assert_not_called()
        mock_price_prompt.assert_not_called()
        mock_notes_prompt.assert_not_called()
        mock_wizard_review.assert_not_called()

    async def test_authenticated_accept_suggested_lots_flow(self):
        message_sentinel = object()
        callback = SimpleNamespace(message=message_sentinel)
        principal = object()
        callback_data = AcceptLotsCallback(lots="10_20")

        observed_order: list[str] = []

        async def fake_get_data():
            observed_order.append("get_data")
            return {"quantity": 30, "wizard_return_to_review": False}

        def fake_validate_lot_sizes(total, lot_sizes):
            observed_order.append("validate_lot_sizes")
            return True, "", [10, 20]

        async def fake_update_data(*args, **kwargs):
            observed_order.append("update_data")
            return None

        async def fake_answer_callback(*args, **kwargs):
            observed_order.append("answer_callback_query_via_runtime")
            return None

        async def fake_price_prompt(*args, **kwargs):
            observed_order.append("_show_price_prompt")
            return None

        state = mock.AsyncMock()
        state.get_data.side_effect = fake_get_data
        state.update_data.side_effect = fake_update_data

        with mock.patch(
            "bot.handlers.trade_create.validate_lot_sizes",
            side_effect=fake_validate_lot_sizes,
        ) as mock_validate_lot_sizes, mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_callback,
        ) as mock_answer_callback, mock.patch(
            "bot.handlers.trade_create._show_price_prompt",
            new_callable=mock.AsyncMock,
            side_effect=fake_price_prompt,
        ) as mock_price_prompt, mock.patch(
            "bot.handlers.trade_create._show_notes_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_notes_prompt, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_wizard_review, mock.patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_incoming:
            result = await handle_accept_suggested_lots(
                callback,
                state,
                principal,
                callback_data,
            )

        self.assertIsNone(result)
        self.assertEqual(
            observed_order,
            [
                "get_data",
                "validate_lot_sizes",
                "update_data",
                "answer_callback_query_via_runtime",
                "_show_price_prompt",
            ],
        )

        state.get_data.assert_awaited_once_with()
        mock_validate_lot_sizes.assert_called_once_with(30, [10, 20])
        state.update_data.assert_awaited_once_with(lot_sizes=[10, 20])
        mock_answer_callback.assert_awaited_once_with(callback)
        mock_price_prompt.assert_awaited_once_with(
            message_sentinel,
            state,
            edit=True,
            user=principal,
        )

        mock_notes_prompt.assert_not_called()
        mock_wizard_review.assert_not_called()
        mock_answer_incoming.assert_not_called()
        self.assertEqual(
            state.mock_calls,
            [
                mock.call.get_data(),
                mock.call.update_data(lot_sizes=[10, 20]),
            ],
        )

    async def test_authenticated_valid_persian_price_input_flow(self):
        message = SimpleNamespace(text="۱۹۷")
        principal = object()
        bot_sentinel = object()

        observed_order: list[str] = []
        recorded_normalize_returns: list[tuple[int | None, str]] = []

        async def fake_handoff(*args, **kwargs):
            observed_order.append("handoff")
            return False

        def wrapped_normalize(price: str) -> tuple[int | None, str]:
            observed_order.append("normalize")
            ret = normalize_offer_price_input(price)
            recorded_normalize_returns.append(ret)
            return ret

        async def fake_get_data():
            observed_order.append("get_data")
            return {"wizard_return_to_review": False}

        async def fake_update_data(*args, **kwargs):
            observed_order.append("update_data")
            return None

        async def fake_notes_prompt(*args, **kwargs):
            observed_order.append("_show_notes_prompt")
            return None

        state = mock.AsyncMock()
        state.get_data.side_effect = fake_get_data
        state.update_data.side_effect = fake_update_data

        with mock.patch(
            "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
            new_callable=mock.AsyncMock,
            side_effect=fake_handoff,
        ) as mock_handoff, mock.patch(
            "bot.handlers.trade_create.normalize_offer_price_input",
            side_effect=wrapped_normalize,
        ) as mock_normalize, mock.patch(
            "bot.handlers.trade_create._show_notes_prompt",
            new_callable=mock.AsyncMock,
            side_effect=fake_notes_prompt,
        ) as mock_notes_prompt, mock.patch(
            "bot.handlers.trade_create._show_price_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_price_prompt, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_wizard_review, mock.patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_incoming, mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_callback, mock.patch(
            "bot.handlers.trade_create.validate_lot_sizes",
        ) as mock_validate_lot_sizes:
            result = await handle_price_input(
                message,
                state,
                principal,
                bot_sentinel,
            )

        self.assertIsNone(result)
        mock_normalize.assert_called_once_with("197")
        self.assertEqual(recorded_normalize_returns, [(197000, "")])
        self.assertEqual(recorded_normalize_returns[0][0], 197000)

        self.assertEqual(
            observed_order,
            [
                "handoff",
                "normalize",
                "get_data",
                "update_data",
                "_show_notes_prompt",
            ],
        )

        mock_handoff.assert_awaited_once_with(
            message,
            state,
            principal,
            bot_sentinel,
        )
        state.get_data.assert_awaited_once_with()
        state.update_data.assert_awaited_once_with(price=197000)
        mock_notes_prompt.assert_awaited_once_with(
            message,
            state,
            edit=False,
            user=principal,
        )

        mock_answer_incoming.assert_not_called()
        mock_price_prompt.assert_not_called()
        mock_wizard_review.assert_not_called()
        mock_answer_callback.assert_not_called()
        mock_validate_lot_sizes.assert_not_called()
        self.assertEqual(
            state.mock_calls,
            [
                mock.call.get_data(),
                mock.call.update_data(price=197000),
            ],
        )


if __name__ == "__main__":
    unittest.main()
