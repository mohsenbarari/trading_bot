"""Characterization tests for missing commodity callback selection handling.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Exact seed characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-COMMODITY-SELECTION-L812

Single characterized scenario:
- An eligible linked user selects a missing Commodity (id=23), the exact
  Commodity query returns None from scalar_one_or_none(), and the handler sends
  the current missing-commodity alert before settings, FSM, quantity, prompt,
  or edit work.

Exclusions:
- eligible ordinary-commodity success scenario
- handle_commodity_page scenarios
- unauthenticated user branch
- pack commodity branch
- return-to-review branch
- product changes
- source changes
- docs/index reconciliation
- seed closure
- blocker closure
- Stage closure

This partial characterization unit executes registered router dispatch under live
aiogram filter rules. These assertions do not close any seed, blocker
(P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE), reconciliation, or Stage P1-00b.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock

from aiogram import types as aiogram_types
from sqlalchemy.sql import Select

from bot.callbacks import CommodityCallback
from bot.handlers import trade_create
from bot.states import Trade
from models.commodity import Commodity


class BoundedCommodityScalarResult:
    """Result object exposing only scalar_one_or_none for this exact closure."""

    def scalar_one_or_none(self) -> None:
        return None


class QueryAwareCommoditySession:
    """Session adapter accepting exactly one Commodity select for id=23."""

    def __init__(self, order_tracker: list[str]) -> None:
        self._order_tracker = order_tracker
        self._execute_count = 0

    @property
    def execute_count(self) -> int:
        return self._execute_count

    async def execute(self, stmt: Any) -> BoundedCommodityScalarResult:
        if self._execute_count > 0:
            raise AssertionError("QueryAwareCommoditySession received more than one execute call")
        self._execute_count += 1

        if not isinstance(stmt, Select):
            raise AssertionError(f"Expected SQLAlchemy Select statement, got {type(stmt)!r}")

        column_descriptions = getattr(stmt, "column_descriptions", [])
        if len(column_descriptions) != 1 or column_descriptions[0].get("entity") is not Commodity:
            raise AssertionError(
                f"Expected Select entity to be authoritative Commodity, got {column_descriptions!r}"
            )

        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            raise AssertionError("Expected whereclause filtering by Commodity.id")

        left_col = getattr(whereclause, "left", None)
        left_name = getattr(left_col, "name", None) or getattr(left_col, "key", None)
        if left_name != "id":
            raise AssertionError(f"Expected filter column 'id', got {left_name!r}")

        bound_value = getattr(getattr(whereclause, "right", None), "value", None)
        if bound_value != 23:
            raise AssertionError(f"Expected bound Commodity.id == 23, got {bound_value!r}")

        self._order_tracker.append("db.execute")
        return BoundedCommodityScalarResult()


class QueryAwareCommoditySessionContext:
    """Async context manager proving enter/exit ordering for AsyncSessionLocal."""

    def __init__(self, session: QueryAwareCommoditySession, order_tracker: list[str]) -> None:
        self._session = session
        self._order_tracker = order_tracker
        self._entered = False
        self._exited = False

    @property
    def entered(self) -> bool:
        return self._entered

    @property
    def exited(self) -> bool:
        return self._exited

    async def __aenter__(self) -> QueryAwareCommoditySession:
        if self._entered:
            raise AssertionError("Session context already entered")
        self._entered = True
        self._order_tracker.append("db.enter")
        return self._session

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if not self._entered or self._exited:
            raise AssertionError("Invalid session context exit lifecycle")
        self._exited = True
        self._order_tracker.append("db.exit")
        return False


class TradeCreateCommoditySelectionMissingCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_missing_commodity_alerts_before_settings_state_or_prompt_work(
        self,
    ) -> None:
        packed_data = CommodityCallback(id=23).pack()
        chat = aiogram_types.Chat(id=1001, type="private")
        message = aiogram_types.Message(
            message_id=2001,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-missing-commodity-select",
            from_user=aiogram_types.User(
                id=54321,
                is_bot=False,
                first_name="EligibleUser",
            ),
            chat_instance="test_chat_instance_commodity",
            message=message,
            data=packed_data,
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")

        order_tracker: list[str] = []

        session = QueryAwareCommoditySession(order_tracker)
        session_context = QueryAwareCommoditySessionContext(session, order_tracker)

        async def track_answer_runtime(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("answer_callback_query_via_runtime")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create.AsyncSessionLocal",
                return_value=session_context,
            ),
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=track_answer_runtime,
            ) as mock_answer_runtime,
            mock.patch(
                "core.trading_settings.get_trading_settings_async",
                new_callable=mock.AsyncMock,
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create.get_quantity_keyboard",
            ) as mock_get_quantity_keyboard,
            mock.patch(
                "bot.handlers.trade_create._show_wizard_review",
                new_callable=mock.AsyncMock,
            ) as mock_show_wizard_review,
            mock.patch(
                "bot.handlers.trade_create._show_price_prompt",
                new_callable=mock.AsyncMock,
            ) as mock_show_price_prompt,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit_runtime,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_callback_answer,
        ):
            result = await trade_create.router.propagate_event(
                "callback_query",
                event,
                state=state,
                user=runtime_user,
                raw_state=Trade.awaiting_commodity.state,
            )

        self.assertIsNone(result)

        expected_order = [
            "db.enter",
            "db.execute",
            "db.exit",
            "answer_callback_query_via_runtime",
        ]
        self.assertEqual(order_tracker, expected_order)

        self.assertEqual(session.execute_count, 1)
        self.assertTrue(session_context.entered)
        self.assertTrue(session_context.exited)

        mock_answer_runtime.assert_awaited_once_with(
            event,
            "❌ کالا یافت نشد!",
            show_alert=True,
        )
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(mock_answer_runtime.await_args.args[1], "❌ کالا یافت نشد!")
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {"show_alert": True})

        mock_get_trading_settings.assert_not_called()
        state.get_data.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()

        mock_get_quantity_keyboard.assert_not_called()
        mock_show_wizard_review.assert_not_called()
        mock_show_price_prompt.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_callback_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
