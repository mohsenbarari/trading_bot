"""Characterization tests for handle_text_offer_inference_choice_cancel.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed ID: BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-INFERENCE-CHOICE-CANCEL-L3432
"""

import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.trade_create import handle_text_offer_inference_choice_cancel


class TestTradeCreateInferenceChoiceCancelCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_clears_then_acknowledges_without_edit(self):
        callback = SimpleNamespace(id="cb_anon")
        state = mock.AsyncMock()
        call_order = []

        state.clear.side_effect = lambda: call_order.append("state.clear")

        with (
            patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new=AsyncMock(side_effect=lambda *a, **kw: call_order.append("answer")),
            ) as mock_answer,
            patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new=AsyncMock(side_effect=lambda *a, **kw: call_order.append("edit")),
            ) as mock_edit,
        ):
            result = await handle_text_offer_inference_choice_cancel(
                callback,
                state,
                user=None,
            )

        self.assertIsNone(result)
        self.assertEqual(call_order, ["state.clear", "answer"])
        self.assertEqual(state.mock_calls, [mock.call.clear()])
        self.assertEqual(state.clear.await_count, 1)
        mock_answer.assert_awaited_once_with(callback)
        mock_edit.assert_not_awaited()

    async def test_authenticated_clears_then_acknowledges_then_edits_exact_cancellation_response(self):
        callback = SimpleNamespace(id="cb_auth")
        user = SimpleNamespace(id=42, telegram_id=123456789)
        state = mock.AsyncMock()
        call_order = []

        state.clear.side_effect = lambda: call_order.append("state.clear")

        with (
            patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new=AsyncMock(side_effect=lambda *a, **kw: call_order.append("answer")),
            ) as mock_answer,
            patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new=AsyncMock(side_effect=lambda *a, **kw: call_order.append("edit")),
            ) as mock_edit,
        ):
            result = await handle_text_offer_inference_choice_cancel(
                callback,
                state,
                user=user,
            )

        self.assertIsNone(result)
        self.assertEqual(call_order, ["state.clear", "answer", "edit"])
        self.assertEqual(state.mock_calls, [mock.call.clear()])
        self.assertEqual(state.clear.await_count, 1)
        mock_answer.assert_awaited_once_with(callback)
        mock_edit.assert_awaited_once_with(callback, user, "❌ لفظ لغو شد.")

    async def test_authenticated_edit_failure_propagates_after_clear_and_ack_without_retry(self):
        callback = SimpleNamespace(id="cb_fail")
        user = SimpleNamespace(id=99, telegram_id=987654321)
        state = mock.AsyncMock()
        call_order = []
        expected_error = RuntimeError("simulated edit failure")

        state.clear.side_effect = lambda: call_order.append("state.clear")

        async def failing_edit(*args, **kwargs):
            call_order.append("edit")
            raise expected_error

        with (
            patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new=AsyncMock(side_effect=lambda *a, **kw: call_order.append("answer")),
            ) as mock_answer,
            patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new=AsyncMock(side_effect=failing_edit),
            ) as mock_edit,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await handle_text_offer_inference_choice_cancel(
                    callback,
                    state,
                    user=user,
                )

        self.assertIs(ctx.exception, expected_error)
        self.assertEqual(call_order, ["state.clear", "answer", "edit"])
        self.assertEqual(state.mock_calls, [mock.call.clear()])
        self.assertEqual(state.clear.await_count, 1)
        mock_answer.assert_awaited_once_with(callback)
        mock_edit.assert_awaited_once_with(callback, user, "❌ لفظ لغو شد.")


if __name__ == "__main__":
    unittest.main()
