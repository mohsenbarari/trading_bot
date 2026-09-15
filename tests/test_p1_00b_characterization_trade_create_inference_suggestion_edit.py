"""Characterization tests for handle_text_offer_inference_suggestion_edit guards.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed IDs:
- BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-INFERENCE-SUGGESTION-EDIT-L3360
- BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-INFERENCE-SUGGESTION-EDIT-L3364
"""

import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.trade_create import handle_text_offer_inference_suggestion_edit


class TestTradeCreateInferenceSuggestionEditCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_guard_answers_and_aborts_without_message_or_state_access(self):
        class ExplodingMessageCallback:
            @property
            def message(self):
                raise AssertionError("callback.message must not be accessed before user authentication guard")

        callback = ExplodingMessageCallback()
        state = mock.AsyncMock()

        with (
            patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new=AsyncMock(),
            ) as mock_answer,
            patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as mock_edit,
        ):
            result = await handle_text_offer_inference_suggestion_edit(
                callback,
                state,
                user=None,
            )

        self.assertIsNone(result)
        mock_answer.assert_awaited_once_with(callback)
        self.assertEqual(state.mock_calls, [])
        mock_edit.assert_not_awaited()

    async def test_authenticated_stale_message_alerts_and_aborts_without_edit_or_state_mutation(self):
        callback = SimpleNamespace(message=SimpleNamespace(message_id=92))
        user = SimpleNamespace(id=1)
        state = mock.AsyncMock()
        state.get_data.return_value = {"text_offer_inference_message_id": 91}

        with (
            patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new=AsyncMock(),
            ) as mock_answer,
            patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as mock_edit,
        ):
            result = await handle_text_offer_inference_suggestion_edit(
                callback,
                state,
                user=user,
            )

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [mock.call.get_data()])
        mock_answer.assert_awaited_once_with(
            callback,
            "این انتخاب قدیمی است.",
            show_alert=True,
        )
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()
        mock_edit.assert_not_awaited()

    async def test_authenticated_invalid_edit_state_alerts_before_any_mutation_or_edit(self):
        callback = SimpleNamespace(message=SimpleNamespace(message_id=93))
        user = SimpleNamespace(id=1)
        state = mock.AsyncMock()
        state.get_data.return_value = {
            "text_offer_inference_message_id": 93,
            "text_offer_inference_mode": "edit",
            "text_offer_inference_candidates": [
                {"commodity_id": 1, "commodity_name": "سکه امامی"}
            ],
            "text_offer_inference_edit_candidates": [
                {"commodity_id": 2, "commodity_name": "نیم سکه"}
            ],
        }

        with (
            patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new=AsyncMock(),
            ) as mock_answer,
            patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as mock_edit,
        ):
            result = await handle_text_offer_inference_suggestion_edit(
                callback,
                state,
                user=user,
            )

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [mock.call.get_data()])
        mock_answer.assert_awaited_once_with(
            callback,
            "گزینهٔ دیگری در دسترس نیست.",
            show_alert=True,
        )
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()
        mock_edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
