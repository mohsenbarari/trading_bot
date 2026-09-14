"""Characterization tests for Trade create notes handlers.

Exact seeds characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-SKIP-NOTES-L1148
- BOT-TRADE-MESSAGE-TRADE-CREATE-HANDLE-NOTES-INPUT-L1159

These tests characterize unauthenticated skip notes acknowledgement, authenticated
skip notes ordering, and overlong notes rejection under P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
They explicitly do not close either seed, the blocker, or Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest import mock

from bot.handlers.trade_create import (
    handle_notes_input,
    handle_skip_notes,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction

SEED_SKIP_NOTES = "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-SKIP-NOTES-L1148"
SEED_NOTES_INPUT = "BOT-TRADE-MESSAGE-TRADE-CREATE-HANDLE-NOTES-INPUT-L1159"


class TradeCreateNotesCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_skip_acknowledges_without_state_or_delivery(self):
        callback = SimpleNamespace(message=SimpleNamespace())
        state = SimpleNamespace(
            update_data=mock.AsyncMock(),
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_review:
            result = await handle_skip_notes(callback, state, user=None)

        self.assertIsNone(result)
        mock_answer.assert_awaited_once_with(callback)
        state.update_data.assert_not_called()
        mock_review.assert_not_called()

    async def test_authenticated_skip_updates_then_acknowledges_then_reviews(self):
        callback = SimpleNamespace(message=SimpleNamespace())
        actor = SimpleNamespace(id=42, username="trader_actor")
        observed_order: list[str] = []

        async def fake_update_data(**kwargs):
            observed_order.append("update_data")
            return None

        async def fake_answer(*args, **kwargs):
            observed_order.append("answer")
            return None

        async def fake_review(*args, **kwargs):
            observed_order.append("show_wizard_review")
            return None

        state = SimpleNamespace(
            update_data=mock.AsyncMock(side_effect=fake_update_data),
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
            side_effect=fake_review,
        ) as mock_review:
            result = await handle_skip_notes(callback, state, user=actor)

        self.assertIsNone(result)
        state.update_data.assert_awaited_once_with(notes=None)
        mock_answer.assert_awaited_once_with(callback)
        mock_review.assert_awaited_once_with(
            callback.message,
            state,
            edit=True,
            user=actor,
        )
        self.assertEqual(
            observed_order,
            ["update_data", "answer", "show_wizard_review"],
        )

    async def test_overlong_notes_rejected_without_state_mutation(self):
        actor = SimpleNamespace(id=42, username="trader_actor")
        message = SimpleNamespace(text="x" * 201)
        state = SimpleNamespace(
            update_data=mock.AsyncMock(),
        )
        observed_order: list[str] = []

        async def fake_handoff(*args, **kwargs):
            observed_order.append("handoff")
            return False

        async def fake_answer(*args, **kwargs):
            observed_order.append("answer")
            return None

        with mock.patch(
            "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
            new_callable=mock.AsyncMock,
            side_effect=fake_handoff,
        ) as mock_handoff, mock.patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer,
        ) as mock_answer, mock.patch(
            "bot.handlers.trade_create._show_wizard_review",
            new_callable=mock.AsyncMock,
        ) as mock_review:
            result = await handle_notes_input(message, state, user=actor)

        self.assertIsNone(result)
        mock_handoff.assert_awaited_once_with(message, state, actor)
        mock_answer.assert_awaited_once_with(
            message,
            actor,
            "❌ توضیحات نباید بیش از 200 کاراکتر باشد.",
            source_key="offer-notes-too-long",
            action=TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
        )
        self.assertEqual(observed_order, ["handoff", "answer"])
        state.update_data.assert_not_called()
        mock_review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
