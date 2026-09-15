"""Characterization tests for text offer cancel callback wrapper.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact seed characterized:
- BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-CANCEL-L3493

These wrapper assertions do not execute runtime delivery adapters or persistence
and do not close the seed (BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-CANCEL-L3493),
the blocker (P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE), reconciliation, or Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from unittest import mock

from bot.handlers.trade_create import handle_text_offer_cancel

SEED_HANDLE_TEXT_OFFER_CANCEL = (
    "BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TEXT-OFFER-CANCEL-L3493"
)


class TestTradeCreateTextOfferCancelCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_stale_callback_short_circuits_without_edit_clear_or_acknowledgement(
        self,
    ):
        sentinel_callback = object()
        sentinel_state = object()
        sentinel_user = object()

        with (
            mock.patch(
                "bot.handlers.trade_create._reject_stale_text_offer_callback",
                new_callable=mock.AsyncMock,
            ) as mock_stale_guard,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_ack,
        ):
            mock_stale_guard.return_value = True

            result = await handle_text_offer_cancel(
                sentinel_callback,
                sentinel_state,
                sentinel_user,
            )

            self.assertIsNone(result)
            mock_stale_guard.assert_awaited_once_with(
                sentinel_callback,
                sentinel_state,
            )
            mock_edit.assert_not_called()
            mock_ack.assert_not_called()

    async def test_current_callback_edits_then_clears_then_acknowledges(self):
        sentinel_callback = object()
        sentinel_user = object()
        mock_state = mock.Mock()
        mock_state.clear = mock.AsyncMock()

        call_order: list[str] = []

        async def fake_stale_guard(callback, state):
            call_order.append("guard")
            return False

        async def fake_edit(*args, **kwargs):
            call_order.append("edit")
            return None

        async def fake_clear(*args, **kwargs):
            call_order.append("clear")
            return None

        async def fake_ack(*args, **kwargs):
            call_order.append("acknowledgement")
            return None

        mock_state.clear.side_effect = fake_clear

        with (
            mock.patch(
                "bot.handlers.trade_create._reject_stale_text_offer_callback",
                new_callable=mock.AsyncMock,
                side_effect=fake_stale_guard,
            ) as mock_stale_guard,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=fake_edit,
            ) as mock_edit,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=fake_ack,
            ) as mock_ack,
        ):
            result = await handle_text_offer_cancel(
                sentinel_callback,
                mock_state,
                sentinel_user,
            )

        self.assertIsNone(result)
        self.assertEqual(
            call_order,
            ["guard", "edit", "clear", "acknowledgement"],
        )
        mock_stale_guard.assert_awaited_once_with(
            sentinel_callback,
            mock_state,
        )

        mock_edit.assert_awaited_once_with(
            sentinel_callback,
            sentinel_user,
            "❌ لفظ لغو شد.",
        )
        self.assertEqual(len(mock_edit.await_args.args), 3)
        self.assertIs(mock_edit.await_args.args[0], sentinel_callback)
        self.assertIs(mock_edit.await_args.args[1], sentinel_user)
        self.assertEqual(mock_edit.await_args.args[2], "❌ لفظ لغو شد.")
        self.assertEqual(mock_edit.await_args.kwargs, {})

        mock_state.clear.assert_awaited_once_with()
        self.assertEqual(mock_state.clear.await_args.args, ())
        self.assertEqual(mock_state.clear.await_args.kwargs, {})

        mock_ack.assert_awaited_once_with(sentinel_callback)
        self.assertEqual(len(mock_ack.await_args.args), 1)
        self.assertIs(mock_ack.await_args.args[0], sentinel_callback)
        self.assertEqual(mock_ack.await_args.kwargs, {})

    async def test_edit_failure_propagates_before_clear_or_acknowledgement(self):
        sentinel_callback = object()
        sentinel_user = object()
        mock_state = mock.Mock()
        mock_state.clear = mock.AsyncMock()

        sentinel_error = RuntimeError("edit dispatch failed")
        call_order: list[str] = []

        async def fake_stale_guard(callback, state):
            call_order.append("guard")
            return False

        async def fake_edit(*args, **kwargs):
            call_order.append("edit")
            raise sentinel_error

        with (
            mock.patch(
                "bot.handlers.trade_create._reject_stale_text_offer_callback",
                new_callable=mock.AsyncMock,
                side_effect=fake_stale_guard,
            ) as mock_stale_guard,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=fake_edit,
            ) as mock_edit,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_ack,
        ):
            with self.assertRaises(RuntimeError) as exc_context:
                await handle_text_offer_cancel(
                    sentinel_callback,
                    mock_state,
                    sentinel_user,
                )

        self.assertIs(exc_context.exception, sentinel_error)
        self.assertEqual(call_order, ["guard", "edit"])
        mock_stale_guard.assert_awaited_once_with(
            sentinel_callback,
            mock_state,
        )

        mock_edit.assert_awaited_once_with(
            sentinel_callback,
            sentinel_user,
            "❌ لفظ لغو شد.",
        )
        self.assertEqual(len(mock_edit.await_args.args), 3)
        self.assertIs(mock_edit.await_args.args[0], sentinel_callback)
        self.assertIs(mock_edit.await_args.args[1], sentinel_user)
        self.assertEqual(mock_edit.await_args.args[2], "❌ لفظ لغو شد.")
        self.assertEqual(mock_edit.await_args.kwargs, {})

        mock_state.clear.assert_not_called()
        mock_ack.assert_not_called()


if __name__ == "__main__":
    unittest.main()
