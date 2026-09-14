"""Characterization tests for handle_text_offer_inference_suggestion_confirm guards.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed ID: BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-INFERENCE-SUGGESTION-CONFIRM-L3305
"""

import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.trade_create import handle_text_offer_inference_suggestion_confirm


class TestTradeCreateInferenceSuggestionConfirmCharacterization(unittest.IsolatedAsyncioTestCase):
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
                "bot.handlers.trade_create._show_text_offer_preview",
                new=AsyncMock(),
            ) as mock_preview,
        ):
            result = await handle_text_offer_inference_suggestion_confirm(
                callback,
                state,
                user=None,
            )

        self.assertIsNone(result)
        mock_answer.assert_awaited_once_with(callback)
        self.assertEqual(state.mock_calls, [])
        mock_preview.assert_not_awaited()

    async def test_authenticated_stale_message_alerts_and_aborts_without_preview_or_state_mutation(self):
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
                "bot.handlers.trade_create._show_text_offer_preview",
                new=AsyncMock(),
            ) as mock_preview,
        ):
            result = await handle_text_offer_inference_suggestion_confirm(
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
        mock_preview.assert_not_awaited()

    async def test_authenticated_empty_candidates_alerts_and_aborts_without_preview_or_state_mutation(self):
        callback = SimpleNamespace(message=SimpleNamespace(message_id=93))
        user = SimpleNamespace(id=1)
        state = mock.AsyncMock()
        state.get_data.return_value = {
            "text_offer_inference_message_id": 93,
            "text_offer_inference_mode": "model",
            "text_offer_inference_candidates": [],
            "text_offer_inference_draft": {
                "trade_type": "buy",
                "settlement_type": "cash",
                "quantity": 5,
                "price": 186800,
                "is_wholesale": True,
                "lot_sizes": None,
                "notes": None,
            },
            "text_offer_inference_decision_key": "a" * 64,
        }

        with (
            patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new=AsyncMock(),
            ) as mock_answer,
            patch(
                "bot.handlers.trade_create._show_text_offer_preview",
                new=AsyncMock(),
            ) as mock_preview,
        ):
            result = await handle_text_offer_inference_suggestion_confirm(
                callback,
                state,
                user=user,
            )

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [mock.call.get_data()])
        mock_answer.assert_awaited_once_with(
            callback,
            "این پیشنهاد دیگر معتبر نیست.",
            show_alert=True,
        )
        mock_preview.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
