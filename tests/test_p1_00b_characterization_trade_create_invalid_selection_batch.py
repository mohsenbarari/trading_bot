"""Hermetic characterization tests for trade create invalid selection guards.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Exact seeds characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-TRADE-TYPE-SELECTION-L709
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-SETTLEMENT-TYPE-SELECTION-L743

Scenarios:
1. trade-type-invalid-selection-alert-no-state-mutation
2. settlement-type-invalid-selection-alert-no-state-mutation
"""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.callbacks import TradeSettlementCallback, TradeTypeCallback
from bot.handlers.trade_create import (
    handle_settlement_type_selection,
    handle_trade_type_selection,
)
from core.enums import SettlementType


class TradeCreateInvalidSelectionGuardCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.user = SimpleNamespace(id=1001, is_active=True)
        self.callback = SimpleNamespace(id="cb-invalid-selection-fixture")
        self.state = SimpleNamespace(
            get_data=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
        )

    async def test_trade_type_selection_invalid_value_alerts_without_state_mutation(
        self,
    ) -> None:
        callback_data = TradeTypeCallback(type="invalid")
        self.assertNotIn(callback_data.type, {"buy", "sell"})

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_runtime:
            result = await handle_trade_type_selection(
                self.callback,
                self.state,
                self.user,
                callback_data,
            )

        self.assertIsNone(result)
        mock_answer_runtime.assert_awaited_once_with(
            self.callback,
            "انتخاب نامعتبر است.",
            show_alert=True,
        )
        self.assertIs(mock_answer_runtime.await_args.args[0], self.callback)
        self.assertEqual(mock_answer_runtime.await_args.args[1], "انتخاب نامعتبر است.")
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {"show_alert": True})

        self.state.get_data.assert_not_awaited()
        self.state.update_data.assert_not_awaited()
        self.state.set_state.assert_not_awaited()

    async def test_settlement_type_selection_invalid_value_alerts_without_state_mutation(
        self,
    ) -> None:
        callback_data = TradeSettlementCallback(type="invalid")
        self.assertNotIn(
            callback_data.type,
            {SettlementType.CASH.value, SettlementType.TOMORROW.value},
        )

        with mock.patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_runtime:
            result = await handle_settlement_type_selection(
                self.callback,
                self.state,
                self.user,
                callback_data,
            )

        self.assertIsNone(result)
        mock_answer_runtime.assert_awaited_once_with(
            self.callback,
            "انتخاب نامعتبر است.",
            show_alert=True,
        )
        self.assertIs(mock_answer_runtime.await_args.args[0], self.callback)
        self.assertEqual(mock_answer_runtime.await_args.args[1], "انتخاب نامعتبر است.")
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {"show_alert": True})

        self.state.get_data.assert_not_awaited()
        self.state.update_data.assert_not_awaited()
        self.state.set_state.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
