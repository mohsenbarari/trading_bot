"""Characterization tests for handle_text_offer_inference_choice guards.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed ID: BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-INFERENCE-CHOICE-L3229
"""

import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.callbacks import TextOfferInferenceCandidateCallback
from bot.handlers.trade_create import handle_text_offer_inference_choice


class TestTradeCreateInferenceChoiceCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_guard_answers_and_aborts_without_message_or_state_access(self):
        class ExplodingMessageCallback:
            @property
            def message(self):
                raise AssertionError("callback.message must not be accessed before user authentication guard")

        callback = ExplodingMessageCallback()
        callback_data = TextOfferInferenceCandidateCallback(commodity_id=71)
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
            patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as mock_edit,
        ):
            result = await handle_text_offer_inference_choice(
                callback,
                callback_data,
                state,
                user=None,
            )

        self.assertIsNone(result)
        mock_answer.assert_awaited_once_with(callback)
        self.assertEqual(state.mock_calls, [])
        mock_preview.assert_not_awaited()
        mock_edit.assert_not_awaited()

    async def test_authenticated_stale_message_alerts_and_aborts_without_preview_or_state_mutation(self):
        callback = SimpleNamespace(message=SimpleNamespace(message_id=92))
        callback_data = TextOfferInferenceCandidateCallback(commodity_id=71)
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
            patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as mock_edit,
        ):
            result = await handle_text_offer_inference_choice(
                callback,
                callback_data,
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
        mock_edit.assert_not_awaited()

    async def test_authenticated_unsupported_mode_alerts_and_aborts_without_preview_or_state_mutation(self):
        callback = SimpleNamespace(message=SimpleNamespace(message_id=93))
        callback_data = TextOfferInferenceCandidateCallback(commodity_id=71)
        user = SimpleNamespace(id=1)
        state = mock.AsyncMock()
        state.get_data.return_value = {
            "text_offer_inference_message_id": 93,
            "text_offer_inference_mode": "retired",
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
            patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as mock_edit,
        ):
            result = await handle_text_offer_inference_choice(
                callback,
                callback_data,
                state,
                user=user,
            )

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [mock.call.get_data()])
        mock_answer.assert_awaited_once_with(
            callback,
            "این انتخاب دیگر معتبر نیست.",
            show_alert=True,
        )
        mock_preview.assert_not_awaited()
        mock_edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
