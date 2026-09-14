"""Characterization tests for trade_create handle_text_offer entrypoint.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-OFFER-MESSAGE-TRADE-CREATE-HANDLE-TEXT-OFFER-L3047
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest import mock

from bot.handlers.trade_create import handle_text_offer


class TestTradeCreateTextOfferCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_guard(self) -> None:
        message = SimpleNamespace(
            text="فروش آبشده ۱۰ مثقال نقدی",
            message_id=101,
            chat=SimpleNamespace(id=555),
        )
        state = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_prepare:
            result = await handle_text_offer(message, state=state, user=None)

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [])
        mock_prepare.assert_not_called()

    async def test_authenticated_non_idle_suppression(self) -> None:
        principal = SimpleNamespace(id=37, telegram_id=777)
        message = SimpleNamespace(
            text="فروش آبشده ۱۰ مثقال نقدی",
            message_id=102,
            chat=SimpleNamespace(id=555),
        )
        state = mock.AsyncMock()
        state.get_state.return_value = "Trade:awaiting_custom_price"

        with mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_prepare:
            result = await handle_text_offer(message, state=state, user=principal)

        self.assertIsNone(result)
        state.get_state.assert_awaited_once_with()
        self.assertEqual(state.mock_calls, [mock.call.get_state()])
        mock_prepare.assert_not_called()

    async def test_authenticated_idle_delegation_empty_text_normalization(self) -> None:
        principal = SimpleNamespace(id=37, telegram_id=777)
        message = SimpleNamespace(
            text=None,
            message_id=103,
            chat=SimpleNamespace(id=555),
        )
        state = mock.AsyncMock()
        state.get_state.return_value = None

        with mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_prepare:
            result = await handle_text_offer(message, state=state, user=principal)

        self.assertIsNone(result)
        state.get_state.assert_awaited_once_with()
        self.assertEqual(state.mock_calls, [mock.call.get_state()])
        mock_prepare.assert_awaited_once_with(
            message,
            state,
            principal,
            "",
            edit_response=False,
            wizard_source=False,
        )
        self.assertIs(mock_prepare.await_args.args[0], message)
        self.assertIs(mock_prepare.await_args.args[1], state)
        self.assertIs(mock_prepare.await_args.args[2], principal)
        self.assertEqual(mock_prepare.await_args.args[3], "")
        self.assertIs(mock_prepare.await_args.kwargs["edit_response"], False)
        self.assertIs(mock_prepare.await_args.kwargs["wizard_source"], False)


if __name__ == "__main__":
    unittest.main()
