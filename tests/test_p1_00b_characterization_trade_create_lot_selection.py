"""Characterization tests for Trade wizard lot selection and lot sizes input handlers."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from unittest import mock

from bot.handlers.trade_create import (
    handle_lot_sizes_input,
    handle_lot_split,
    handle_lot_wholesale,
)


class TestTradeCreateLotSelectionCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_handlers_unauthenticated_guard(self):
        callback_wholesale = object()
        state_wholesale = mock.AsyncMock()

        callback_split = object()
        state_split = mock.AsyncMock()

        message_input = object()
        state_input = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_callback, mock.patch(
            "bot.handlers.trade_create._show_price_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_price_prompt, mock.patch(
            "bot.handlers.trade_create._show_lot_sizes_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_lot_sizes_prompt, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_wizard_review, mock.patch(
            "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_handoff, mock.patch(
            "bot.handlers.trade_create.validate_lot_sizes",
        ) as mock_validate_lot_sizes, mock.patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_incoming:
            result_wholesale = await handle_lot_wholesale(
                callback_wholesale, state_wholesale, None
            )
            result_split = await handle_lot_split(
                callback_split, state_split, None
            )
            result_input = await handle_lot_sizes_input(
                message_input, state_input, None
            )

        self.assertIsNone(result_wholesale)
        self.assertIsNone(result_split)
        self.assertIsNone(result_input)

        self.assertEqual(mock_answer_callback.await_count, 2)
        mock_answer_callback.assert_has_awaits([
            mock.call(callback_wholesale),
            mock.call(callback_split),
        ])

        mock_price_prompt.assert_not_called()
        mock_lot_sizes_prompt.assert_not_called()
        mock_wizard_review.assert_not_called()
        mock_handoff.assert_not_called()
        mock_validate_lot_sizes.assert_not_called()
        mock_answer_incoming.assert_not_called()

        self.assertEqual(state_wholesale.mock_calls, [])
        self.assertEqual(state_split.mock_calls, [])
        self.assertEqual(state_input.mock_calls, [])

    async def test_handle_lot_type_choice_authenticated(self):
        principal = object()

        message_wholesale = object()
        callback_wholesale = mock.Mock(message=message_wholesale)
        state_wholesale = mock.AsyncMock()

        message_split = object()
        callback_split = mock.Mock(message=message_split)
        state_split = mock.AsyncMock()

        observed_order_wholesale: list[str] = []
        observed_order_split: list[str] = []

        async def fake_get_data_wholesale():
            observed_order_wholesale.append("get_data")
            return {"wizard_return_to_review": False}

        async def fake_update_data_wholesale(*args, **kwargs):
            observed_order_wholesale.append("update_data")
            return None

        async def fake_answer_wholesale(*args, **kwargs):
            observed_order_wholesale.append("answer_callback_query_via_runtime")
            return None

        async def fake_price_prompt(*args, **kwargs):
            observed_order_wholesale.append("_show_price_prompt")
            return None

        async def fake_update_data_split(*args, **kwargs):
            observed_order_split.append("update_data")
            return None

        async def fake_answer_split(*args, **kwargs):
            observed_order_split.append("answer_callback_query_via_runtime")
            return None

        async def fake_lot_sizes_prompt(*args, **kwargs):
            observed_order_split.append("_show_lot_sizes_prompt")
            return None

        state_wholesale.get_data.side_effect = fake_get_data_wholesale
        state_wholesale.update_data.side_effect = fake_update_data_wholesale
        state_split.update_data.side_effect = fake_update_data_split

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_wholesale,
        ) as mock_answer_wholesale, mock.patch(
            "bot.handlers.trade_create._show_price_prompt",
            new_callable=mock.AsyncMock,
            side_effect=fake_price_prompt,
        ) as mock_price_prompt, mock.patch(
            "bot.handlers.trade_create._show_lot_sizes_prompt",
            new_callable=mock.AsyncMock,
            side_effect=fake_lot_sizes_prompt,
        ) as mock_lot_sizes_prompt_wholesale, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_wizard_review_wholesale:
            result_wholesale = await handle_lot_wholesale(
                callback_wholesale, state_wholesale, principal
            )

        self.assertIsNone(result_wholesale)
        self.assertEqual(
            observed_order_wholesale,
            [
                "get_data",
                "update_data",
                "answer_callback_query_via_runtime",
                "_show_price_prompt",
            ],
        )
        state_wholesale.get_data.assert_awaited_once_with()
        state_wholesale.update_data.assert_awaited_once_with(
            is_wholesale=True, lot_sizes=None
        )
        mock_answer_wholesale.assert_awaited_once_with(callback_wholesale)
        mock_price_prompt.assert_awaited_once_with(
            message_wholesale, state_wholesale, edit=True, user=principal
        )
        mock_wizard_review_wholesale.assert_not_called()
        mock_lot_sizes_prompt_wholesale.assert_not_called()
        self.assertEqual(
            state_wholesale.mock_calls,
            [
                mock.call.get_data(),
                mock.call.update_data(is_wholesale=True, lot_sizes=None),
            ],
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_split,
        ) as mock_answer_split, mock.patch(
            "bot.handlers.trade_create._show_lot_sizes_prompt",
            new_callable=mock.AsyncMock,
            side_effect=fake_lot_sizes_prompt,
        ) as mock_lot_sizes_prompt, mock.patch(
            "bot.handlers.trade_create._show_price_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_price_prompt_split, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_wizard_review_split:
            result_split = await handle_lot_split(
                callback_split, state_split, principal
            )

        self.assertIsNone(result_split)
        self.assertEqual(
            observed_order_split,
            [
                "update_data",
                "answer_callback_query_via_runtime",
                "_show_lot_sizes_prompt",
            ],
        )
        state_split.update_data.assert_awaited_once_with(
            is_wholesale=False, lot_sizes=None
        )
        mock_answer_split.assert_awaited_once_with(callback_split)
        mock_lot_sizes_prompt.assert_awaited_once_with(
            message_split, state_split, edit=True, user=principal
        )
        state_split.get_data.assert_not_called()
        mock_price_prompt_split.assert_not_called()
        mock_wizard_review_split.assert_not_called()
        self.assertEqual(
            state_split.mock_calls,
            [
                mock.call.update_data(is_wholesale=False, lot_sizes=None),
            ],
        )

    async def test_handle_lot_sizes_input_authenticated_valid_persian_digits(self):
        principal = object()
        message = mock.Mock(text="۱۰ ۵ ۱۵")

        observed_order: list[str] = []

        async def fake_handoff(*args, **kwargs):
            observed_order.append("handoff")
            return False

        async def fake_get_data():
            observed_order.append("get_data")
            return {"quantity": 30, "wizard_return_to_review": False}

        def fake_validate_lot_sizes(*args, **kwargs):
            observed_order.append("validation")
            return True, "", [10, 5, 15]

        async def fake_update_data(*args, **kwargs):
            observed_order.append("update_data")
            return None

        async def fake_price_prompt(*args, **kwargs):
            observed_order.append("price_prompt")
            return None

        state = mock.AsyncMock()
        state.get_data.side_effect = fake_get_data
        state.update_data.side_effect = fake_update_data

        with mock.patch(
            "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
            new_callable=mock.AsyncMock,
            side_effect=fake_handoff,
        ) as mock_handoff, mock.patch(
            "bot.handlers.trade_create.validate_lot_sizes",
            side_effect=fake_validate_lot_sizes,
        ) as mock_validate_lot_sizes, mock.patch(
            "bot.handlers.trade_create._show_price_prompt",
            new_callable=mock.AsyncMock,
            side_effect=fake_price_prompt,
        ) as mock_price_prompt, mock.patch(
            "bot.handlers.trade_create._show_lot_sizes_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_lot_sizes_prompt, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_wizard_review, mock.patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_incoming:
            result = await handle_lot_sizes_input(message, state, principal)

        self.assertIsNone(result)
        self.assertEqual(
            observed_order,
            ["handoff", "get_data", "validation", "update_data", "price_prompt"],
        )
        mock_handoff.assert_awaited_once_with(message, state, principal)
        state.get_data.assert_awaited_once_with()
        mock_validate_lot_sizes.assert_called_once_with(30, [10, 5, 15])
        state.update_data.assert_awaited_once_with(lot_sizes=[15, 10, 5])
        mock_price_prompt.assert_awaited_once_with(
            message, state, edit=False, user=principal
        )
        mock_answer_incoming.assert_not_called()
        mock_lot_sizes_prompt.assert_not_called()
        mock_wizard_review.assert_not_called()
        self.assertEqual(
            state.mock_calls,
            [
                mock.call.get_data(),
                mock.call.update_data(lot_sizes=[15, 10, 5]),
            ],
        )

    async def test_handle_lot_wholesale_return_to_review_opens_wizard_review(self):
        principal = object()
        message = object()
        callback = mock.Mock(message=message)

        observed_order: list[str] = []

        async def fake_get_data():
            observed_order.append("get_data")
            return {"wizard_return_to_review": True}

        async def fake_update_data(*args, **kwargs):
            observed_order.append("update_data")
            return None

        async def fake_answer_callback(*args, **kwargs):
            observed_order.append("answer_callback_query_via_runtime")
            return None

        async def fake_wizard_review(*args, **kwargs):
            observed_order.append("_show_wizard_review")
            return None

        state = mock.AsyncMock()
        state.get_data.side_effect = fake_get_data
        state.update_data.side_effect = fake_update_data

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_callback,
        ) as mock_answer_callback, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
            side_effect=fake_wizard_review,
        ) as mock_wizard_review, mock.patch(
            "bot.handlers.trade_create._show_price_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_price_prompt, mock.patch(
            "bot.handlers.trade_create._show_lot_sizes_prompt",
            new_callable=mock.AsyncMock,
        ) as mock_lot_sizes_prompt:
            result = await handle_lot_wholesale(callback, state, principal)

        self.assertIsNone(result)
        self.assertEqual(
            observed_order,
            [
                "get_data",
                "update_data",
                "answer_callback_query_via_runtime",
                "_show_wizard_review",
            ],
        )

        state.get_data.assert_awaited_once_with()
        state.update_data.assert_awaited_once_with(
            is_wholesale=True, lot_sizes=None
        )
        mock_answer_callback.assert_awaited_once_with(callback)
        mock_wizard_review.assert_awaited_once_with(
            message, state, edit=True, user=principal
        )

        self.assertIs(callback.message, message)
        self.assertIs(mock_answer_callback.await_args.args[0], callback)
        review_call_args, review_call_kwargs = mock_wizard_review.await_args
        self.assertIs(review_call_args[0], message)
        self.assertIs(review_call_args[1], state)
        self.assertIs(review_call_kwargs["user"], principal)

        mock_price_prompt.assert_not_called()
        mock_lot_sizes_prompt.assert_not_called()

        self.assertEqual(
            state.mock_calls,
            [
                mock.call.get_data(),
                mock.call.update_data(is_wholesale=True, lot_sizes=None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
