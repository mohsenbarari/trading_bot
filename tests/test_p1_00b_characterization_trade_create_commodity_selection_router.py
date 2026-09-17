"""Characterization tests for registered commodity selection callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Exact seed characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-COMMODITY-SELECTION-L812

Characterized scenarios:
- An eligible linked user selects one existing ordinary (non-pack) Commodity with
  wizard_return_to_review=False, and the handler completes the regular-quantity path.
- An eligible linked user selects one existing authoritative pack commodity while
  wizard_return_to_review=False, and the registered callback route stores the pack
  invariant quantity/lot shape, acknowledges once, and opens the price prompt.
- An eligible linked user with wizard_return_to_review=True selects one existing
  ordinary non-pack Commodity, and the registered callback route stores the
  selected commodity, renders the wizard review, then acknowledges the callback
  while suppressing quantity and price paths.
- An eligible linked user with wizard_return_to_review=True selects one existing
  authoritative pack Commodity, and the registered callback route stores the
  authoritative pack quantity/lot state, renders the wizard review before
  acknowledging the callback, and suppresses ordinary quantity and pack
  price-prompt paths.

Exclusions:
- handle_commodity_page scenarios
- unauthenticated user branch
- missing commodity branch
- product changes
- source changes
- docs/index reconciliation
- seed or blocker closure
- Stage P1-00b closure

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
from core.pack_commodities import PACK_FULL_COMMODITY_NAME, PACK_QUANTITY
from core.trading_settings import TradingSettings
from models.commodity import Commodity


class BoundedCommodityScalarResult:
    """Result object exposing only scalar_one_or_none for this exact closure."""

    def __init__(self, commodity: Commodity) -> None:
        self._commodity = commodity

    def scalar_one_or_none(self) -> Commodity:
        return self._commodity


class QueryAwareCommoditySession:
    """Session adapter accepting exactly one Commodity select for id=17."""

    def __init__(self, expected_commodity: Commodity, order_tracker: list[str]) -> None:
        self._expected_commodity = expected_commodity
        self._order_tracker = order_tracker
        self._execute_count = 0

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
        if bound_value != 17:
            raise AssertionError(f"Expected bound Commodity.id == 17, got {bound_value!r}")

        self._order_tracker.append("db.execute")
        return BoundedCommodityScalarResult(self._expected_commodity)


class QueryAwareCommoditySessionContext:
    """Async context manager proving enter/exit ordering for AsyncSessionLocal."""

    def __init__(self, session: QueryAwareCommoditySession, order_tracker: list[str]) -> None:
        self._session = session
        self._order_tracker = order_tracker
        self._entered = False
        self._exited = False

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


class TestTradeCreateCommoditySelectionRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_commodity_selection_router_eligible_regular_commodity_success_ordering(
        self,
    ) -> None:
        packed_data = CommodityCallback(id=17).pack()
        chat = aiogram_types.Chat(id=1001, type="private")
        message = aiogram_types.Message(
            message_id=2001,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-auth-commodity-select",
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
        commodity = Commodity(id=17, name="سکه امامی")
        trading_settings_fixture = TradingSettings(
            offer_min_quantity=5,
            offer_max_quantity=50,
        )
        keyboard_sentinel = aiogram_types.InlineKeyboardMarkup(inline_keyboard=[])

        order_tracker: list[str] = []

        session = QueryAwareCommoditySession(commodity, order_tracker)
        session_context = QueryAwareCommoditySessionContext(session, order_tracker)

        async def track_get_trading_settings_async() -> TradingSettings:
            order_tracker.append("get_trading_settings_async")
            return trading_settings_fixture

        async def track_get_data() -> dict[str, Any]:
            order_tracker.append("state.get_data")
            return {
                "trade_type_fa": "🟢 خرید",
                "wizard_return_to_review": False,
            }

        async def track_update_data(**kwargs: Any) -> None:
            order_tracker.append("state.update_data")

        async def track_answer_runtime(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("answer_callback_query_via_runtime")

        def track_get_quantity_keyboard(*args: Any, **kwargs: Any) -> aiogram_types.InlineKeyboardMarkup:
            order_tracker.append("get_quantity_keyboard")
            return keyboard_sentinel

        async def track_edit_runtime(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("edit_callback_message_via_runtime")

        async def track_set_state(target_state: Any) -> None:
            order_tracker.append("state.set_state")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(side_effect=track_update_data),
            set_state=mock.AsyncMock(side_effect=track_set_state),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create.AsyncSessionLocal",
                return_value=session_context,
            ),
            mock.patch(
                "core.trading_settings.get_trading_settings_async",
                new=mock.AsyncMock(side_effect=track_get_trading_settings_async),
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create.get_quantity_keyboard",
                side_effect=track_get_quantity_keyboard,
            ) as mock_get_quantity_keyboard,
            mock.patch(
                "bot.handlers.trade_create.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=track_edit_runtime,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=track_answer_runtime,
            ) as mock_answer_runtime,
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
            "get_trading_settings_async",
            "state.get_data",
            "state.update_data",
            "answer_callback_query_via_runtime",
            "get_quantity_keyboard",
            "edit_callback_message_via_runtime",
            "state.set_state",
        ]
        self.assertEqual(order_tracker, expected_order)

        mock_get_trading_settings.assert_awaited_once_with()
        state.get_data.assert_awaited_once_with()

        state.update_data.assert_awaited_once_with(
            commodity_id=17,
            commodity_name="سکه امامی",
        )
        self.assertEqual(
            state.update_data.await_args.kwargs,
            {"commodity_id": 17, "commodity_name": "سکه امامی"},
        )
        self.assertNotIn("quantity", state.update_data.await_args.kwargs)
        self.assertNotIn("is_wholesale", state.update_data.await_args.kwargs)
        self.assertNotIn("lot_sizes", state.update_data.await_args.kwargs)

        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(len(mock_answer_runtime.await_args.args), 1)
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {})

        mock_get_quantity_keyboard.assert_called_once_with(
            min_quantity=5,
            max_quantity=50,
        )

        expected_edit_text = (
            "📈 **ثبت لفظ جدید**\n\n"
            "نوع معامله: 🟢 خرید\n"
            "کالا: سکه امامی\n\n"
            "تعداد را انتخاب کنید یا عدد دلخواه را وارد کنید:"
        )
        mock_edit_runtime.assert_awaited_once_with(
            event,
            runtime_user,
            expected_edit_text,
            parse_mode="Markdown",
            reply_markup=keyboard_sentinel,
        )
        self.assertIs(mock_edit_runtime.await_args.args[0], event)
        self.assertIs(mock_edit_runtime.await_args.args[1], runtime_user)
        self.assertEqual(mock_edit_runtime.await_args.args[2], expected_edit_text)
        self.assertEqual(
            mock_edit_runtime.await_args.kwargs,
            {
                "parse_mode": "Markdown",
                "reply_markup": keyboard_sentinel,
            },
        )
        self.assertIs(
            mock_edit_runtime.await_args.kwargs["reply_markup"],
            keyboard_sentinel,
        )

        state.set_state.assert_awaited_once_with(Trade.awaiting_quantity)

        mock_callback_answer.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()

    async def test_commodity_selection_router_eligible_pack_commodity_opens_price_prompt(
        self,
    ) -> None:
        packed_data = CommodityCallback(id=17).pack()
        chat = aiogram_types.Chat(id=1001, type="private")
        message = aiogram_types.Message(
            message_id=2001,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-auth-commodity-select-pack",
            from_user=aiogram_types.User(
                id=54321,
                is_bot=False,
                first_name="EligibleUser",
            ),
            chat_instance="test_chat_instance_commodity_pack",
            message=message,
            data=packed_data,
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")
        commodity = Commodity(id=17, name=PACK_FULL_COMMODITY_NAME)
        trading_settings_fixture = TradingSettings(
            offer_min_quantity=5,
            offer_max_quantity=50,
        )

        order_tracker: list[str] = []

        session = QueryAwareCommoditySession(commodity, order_tracker)
        session_context = QueryAwareCommoditySessionContext(session, order_tracker)

        async def track_get_trading_settings_async() -> TradingSettings:
            order_tracker.append("get_trading_settings_async")
            return trading_settings_fixture

        async def track_get_data() -> dict[str, Any]:
            order_tracker.append("state.get_data")
            return {"wizard_return_to_review": False}

        async def track_update_data(**kwargs: Any) -> None:
            order_tracker.append("state.update_data")

        async def track_answer_runtime(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("answer_callback_query_via_runtime")

        async def track_show_price_prompt(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("_show_price_prompt")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(side_effect=track_update_data),
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
                "core.trading_settings.get_trading_settings_async",
                new=mock.AsyncMock(side_effect=track_get_trading_settings_async),
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=track_answer_runtime,
            ) as mock_answer_runtime,
            mock.patch(
                "bot.handlers.trade_create._show_price_prompt",
                new_callable=mock.AsyncMock,
                side_effect=track_show_price_prompt,
            ) as mock_show_price_prompt,
            mock.patch(
                "bot.handlers.trade_create._show_wizard_review",
                new_callable=mock.AsyncMock,
            ) as mock_show_wizard_review,
            mock.patch(
                "bot.handlers.trade_create.get_quantity_keyboard",
            ) as mock_get_quantity_keyboard,
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
            "get_trading_settings_async",
            "state.get_data",
            "state.update_data",
            "answer_callback_query_via_runtime",
            "_show_price_prompt",
        ]
        self.assertEqual(order_tracker, expected_order)

        mock_get_trading_settings.assert_awaited_once_with()
        state.get_data.assert_awaited_once_with()

        state.update_data.assert_awaited_once_with(
            commodity_id=17,
            commodity_name=PACK_FULL_COMMODITY_NAME,
            quantity=PACK_QUANTITY,
            is_wholesale=True,
            lot_sizes=None,
        )
        self.assertEqual(
            state.update_data.await_args.kwargs,
            {
                "commodity_id": 17,
                "commodity_name": PACK_FULL_COMMODITY_NAME,
                "quantity": PACK_QUANTITY,
                "is_wholesale": True,
                "lot_sizes": None,
            },
        )

        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(len(mock_answer_runtime.await_args.args), 1)
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {})

        mock_show_price_prompt.assert_awaited_once_with(
            message,
            state,
            edit=True,
            user=runtime_user,
        )
        self.assertIs(mock_show_price_prompt.await_args.args[0], message)
        self.assertIs(mock_show_price_prompt.await_args.args[1], state)
        self.assertEqual(
            mock_show_price_prompt.await_args.kwargs,
            {
                "edit": True,
                "user": runtime_user,
            },
        )
        self.assertIs(mock_show_price_prompt.await_args.kwargs["user"], runtime_user)

        mock_get_quantity_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_show_wizard_review.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_callback_answer.assert_not_called()

    async def test_commodity_selection_router_return_to_review_opens_review_before_ack(
        self,
    ) -> None:
        packed_data = CommodityCallback(id=17).pack()
        chat = aiogram_types.Chat(id=1001, type="private")
        message = aiogram_types.Message(
            message_id=2001,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-auth-commodity-select-return-to-review",
            from_user=aiogram_types.User(
                id=54321,
                is_bot=False,
                first_name="EligibleUser",
            ),
            chat_instance="test_chat_instance_commodity_return_to_review",
            message=message,
            data=packed_data,
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")
        commodity = Commodity(id=17, name="سکه امامی")
        trading_settings_fixture = TradingSettings(
            offer_min_quantity=5,
            offer_max_quantity=50,
        )

        order_tracker: list[str] = []

        session = QueryAwareCommoditySession(commodity, order_tracker)
        session_context = QueryAwareCommoditySessionContext(session, order_tracker)

        async def track_get_trading_settings_async() -> TradingSettings:
            order_tracker.append("get_trading_settings_async")
            return trading_settings_fixture

        async def track_get_data() -> dict[str, Any]:
            order_tracker.append("state.get_data")
            return {"wizard_return_to_review": True}

        async def track_update_data(**kwargs: Any) -> None:
            order_tracker.append("state.update_data")

        async def track_show_wizard_review(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("_show_wizard_review")

        async def track_answer_runtime(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("answer_callback_query_via_runtime")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(side_effect=track_update_data),
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
                "core.trading_settings.get_trading_settings_async",
                new=mock.AsyncMock(side_effect=track_get_trading_settings_async),
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create._show_wizard_review",
                new_callable=mock.AsyncMock,
                side_effect=track_show_wizard_review,
            ) as mock_show_wizard_review,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=track_answer_runtime,
            ) as mock_answer_runtime,
            mock.patch(
                "bot.handlers.trade_create._show_price_prompt",
                new_callable=mock.AsyncMock,
            ) as mock_show_price_prompt,
            mock.patch(
                "bot.handlers.trade_create.get_quantity_keyboard",
            ) as mock_get_quantity_keyboard,
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
            "get_trading_settings_async",
            "state.get_data",
            "state.update_data",
            "_show_wizard_review",
            "answer_callback_query_via_runtime",
        ]
        self.assertEqual(order_tracker, expected_order)

        mock_get_trading_settings.assert_awaited_once_with()
        state.get_data.assert_awaited_once_with()

        state.update_data.assert_awaited_once_with(
            commodity_id=17,
            commodity_name="سکه امامی",
        )
        self.assertEqual(
            state.update_data.await_args.kwargs,
            {"commodity_id": 17, "commodity_name": "سکه امامی"},
        )
        self.assertNotIn("quantity", state.update_data.await_args.kwargs)
        self.assertNotIn("is_wholesale", state.update_data.await_args.kwargs)
        self.assertNotIn("lot_sizes", state.update_data.await_args.kwargs)

        mock_show_wizard_review.assert_awaited_once_with(
            message,
            state,
            edit=True,
            user=runtime_user,
        )
        self.assertIs(mock_show_wizard_review.await_args.args[0], message)
        self.assertIs(mock_show_wizard_review.await_args.args[1], state)
        self.assertEqual(
            mock_show_wizard_review.await_args.kwargs,
            {
                "edit": True,
                "user": runtime_user,
            },
        )
        self.assertIs(mock_show_wizard_review.await_args.kwargs["user"], runtime_user)

        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(len(mock_answer_runtime.await_args.args), 1)
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {})

        mock_get_quantity_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_show_price_prompt.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_callback_answer.assert_not_called()

    async def test_commodity_selection_router_pack_return_to_review_keeps_pack_shape_and_reviews_before_ack(
        self,
    ) -> None:
        packed_data = CommodityCallback(id=17).pack()
        chat = aiogram_types.Chat(id=1001, type="private")
        message = aiogram_types.Message(
            message_id=2001,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        event = aiogram_types.CallbackQuery(
            id="cb-auth-commodity-select-pack-return-to-review",
            from_user=aiogram_types.User(
                id=54321,
                is_bot=False,
                first_name="EligibleUser",
            ),
            chat_instance="test_chat_instance_commodity_pack_return_to_review",
            message=message,
            data=packed_data,
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")
        commodity = Commodity(id=17, name=PACK_FULL_COMMODITY_NAME)
        trading_settings_fixture = TradingSettings(
            offer_min_quantity=5,
            offer_max_quantity=50,
        )

        order_tracker: list[str] = []

        session = QueryAwareCommoditySession(commodity, order_tracker)
        session_context = QueryAwareCommoditySessionContext(session, order_tracker)

        async def track_get_trading_settings_async() -> TradingSettings:
            order_tracker.append("get_trading_settings_async")
            return trading_settings_fixture

        async def track_get_data() -> dict[str, Any]:
            order_tracker.append("state.get_data")
            return {"wizard_return_to_review": True}

        async def track_update_data(**kwargs: Any) -> None:
            order_tracker.append("state.update_data")

        async def track_show_wizard_review(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("_show_wizard_review")

        async def track_answer_runtime(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("answer_callback_query_via_runtime")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(side_effect=track_update_data),
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
                "core.trading_settings.get_trading_settings_async",
                new=mock.AsyncMock(side_effect=track_get_trading_settings_async),
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create._show_wizard_review",
                new_callable=mock.AsyncMock,
                side_effect=track_show_wizard_review,
            ) as mock_show_wizard_review,
            mock.patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=track_answer_runtime,
            ) as mock_answer_runtime,
            mock.patch(
                "bot.handlers.trade_create._show_price_prompt",
                new_callable=mock.AsyncMock,
            ) as mock_show_price_prompt,
            mock.patch(
                "bot.handlers.trade_create.get_quantity_keyboard",
            ) as mock_get_quantity_keyboard,
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
            "get_trading_settings_async",
            "state.get_data",
            "state.update_data",
            "_show_wizard_review",
            "answer_callback_query_via_runtime",
        ]
        self.assertEqual(order_tracker, expected_order)

        mock_get_trading_settings.assert_awaited_once_with()
        state.get_data.assert_awaited_once_with()

        state.update_data.assert_awaited_once_with(
            commodity_id=17,
            commodity_name=PACK_FULL_COMMODITY_NAME,
            quantity=PACK_QUANTITY,
            is_wholesale=True,
            lot_sizes=None,
        )
        self.assertEqual(
            state.update_data.await_args.kwargs,
            {
                "commodity_id": 17,
                "commodity_name": PACK_FULL_COMMODITY_NAME,
                "quantity": PACK_QUANTITY,
                "is_wholesale": True,
                "lot_sizes": None,
            },
        )

        mock_show_wizard_review.assert_awaited_once_with(
            message,
            state,
            edit=True,
            user=runtime_user,
        )
        self.assertIs(mock_show_wizard_review.await_args.args[0], message)
        self.assertIs(mock_show_wizard_review.await_args.args[1], state)
        self.assertEqual(
            mock_show_wizard_review.await_args.kwargs,
            {
                "edit": True,
                "user": runtime_user,
            },
        )
        self.assertIs(mock_show_wizard_review.await_args.kwargs["user"], runtime_user)

        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(len(mock_answer_runtime.await_args.args), 1)
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {})

        mock_get_quantity_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_show_price_prompt.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_callback_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
