"""Characterization tests for registered manual quantity message router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Exact seed characterized:
- BOT-TRADE-MESSAGE-TRADE-CREATE-HANDLE-MANUAL-QUANTITY-L929

This unit executes actual registered message-router dispatch for the
five active-FSM scenarios:
1. user=None short-circuits before stale-wizard handoff or any settings, normalization, state, keyboard, or interaction work
2. an eligible user whose stale-wizard handoff returns true stops before normalization and all quantity-flow work
3. an eligible user entering the valid Persian-numeric text '۲۰' advances through the exact handoff/normalization/settings/state/keyboard/interaction/state-transition ordering to Trade.awaiting_lot_type
4. an eligible user with wizard_return_to_review=True and is_wholesale=False entering valid Persian-numeric text '۲۰' saves quantity, resets lot_sizes, sets wizard_edit_field='lot_sizes', and reopens _show_lot_sizes_prompt with edit=False
5. an eligible user with wizard_return_to_review=True and is_wholesale=True entering valid Persian-numeric text '۲۰' saves quantity and reopens _show_wizard_review with edit=False without lot-size reset/prompt work

This partial characterization unit executes registered router dispatch under live
aiogram filter rules. These assertions do not close any seed, blocker
(P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE), reconciliation, or Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest import mock

from aiogram import types as aiogram_types

from bot.handlers import trade_create
from bot.states import Trade
from bot.utils import offer_parser
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.trading_settings import TradingSettings

_REAL_NORMALIZE_DIGITS = offer_parser.normalize_digits


class TestTradeCreateManualQuantityRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_manual_quantity_router_unauthenticated_active_state_short_circuit(
        self,
    ):
        chat = aiogram_types.Chat(id=1001, type="private")
        from_user = aiogram_types.User(
            id=999,
            is_bot=False,
            first_name="UnauthUser",
        )
        event = aiogram_types.Message(
            message_id=2001,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
            from_user=from_user,
            text="۲۰",
        )

        state = SimpleNamespace(
            get_data=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
                new_callable=mock.AsyncMock,
            ) as mock_handoff,
            mock.patch(
                "bot.utils.offer_parser.normalize_digits",
                wraps=_REAL_NORMALIZE_DIGITS,
            ) as mock_normalize_digits,
            mock.patch(
                "core.trading_settings.get_trading_settings",
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
            ) as mock_get_lot_type_keyboard,
            mock.patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.Message,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_message_answer,
        ):
            result = await trade_create.router.propagate_event(
                "message",
                event,
                state=state,
                user=None,
                raw_state=Trade.awaiting_quantity.state,
            )

        self.assertIsNone(result)
        mock_handoff.assert_not_called()
        mock_normalize_digits.assert_not_called()
        mock_get_trading_settings.assert_not_called()
        state.get_data.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_get_lot_type_keyboard.assert_not_called()
        mock_answer_runtime.assert_not_called()
        mock_message_answer.assert_not_called()

    async def test_manual_quantity_router_stale_builder_handoff_precedes_quantity_work(
        self,
    ):
        chat = aiogram_types.Chat(id=1002, type="private")
        from_user = aiogram_types.User(
            id=54321,
            is_bot=False,
            first_name="EligibleUser",
        )
        event = aiogram_types.Message(
            message_id=2002,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
            from_user=from_user,
            text="خ ربع 30تا 75800",
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")
        state = SimpleNamespace(
            get_data=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
                new_callable=mock.AsyncMock,
                return_value=True,
            ) as mock_handoff,
            mock.patch(
                "bot.utils.offer_parser.normalize_digits",
                wraps=_REAL_NORMALIZE_DIGITS,
            ) as mock_normalize_digits,
            mock.patch(
                "core.trading_settings.get_trading_settings",
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
            ) as mock_get_lot_type_keyboard,
            mock.patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.Message,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_message_answer,
        ):
            result = await trade_create.router.propagate_event(
                "message",
                event,
                state=state,
                user=runtime_user,
                raw_state=Trade.awaiting_quantity.state,
            )

        self.assertIsNone(result)
        mock_handoff.assert_awaited_once_with(event, state, runtime_user)
        self.assertIs(mock_handoff.await_args.args[0], event)
        self.assertIs(mock_handoff.await_args.args[1], state)
        self.assertIs(mock_handoff.await_args.args[2], runtime_user)

        mock_normalize_digits.assert_not_called()
        mock_get_trading_settings.assert_not_called()
        state.get_data.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_get_lot_type_keyboard.assert_not_called()
        mock_answer_runtime.assert_not_called()
        mock_message_answer.assert_not_called()

    async def test_manual_quantity_router_eligible_valid_persian_numeric_ordering_through_awaiting_lot_type(
        self,
    ):
        chat = aiogram_types.Chat(id=1003, type="private")
        from_user = aiogram_types.User(
            id=54321,
            is_bot=False,
            first_name="EligibleUser",
        )
        event = aiogram_types.Message(
            message_id=2003,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
            from_user=from_user,
            text="۲۰",
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")
        keyboard_sentinel = aiogram_types.InlineKeyboardMarkup(inline_keyboard=[])

        call_order: list[str] = []

        async def track_handoff(message, state_arg, user_arg, bot=None):
            call_order.append("_handoff_stale_wizard_state_to_text_offer")
            return False

        def track_normalize_digits(text: str) -> str:
            call_order.append(f"normalize_digits with '{text}'")
            return _REAL_NORMALIZE_DIGITS(text)

        def track_get_trading_settings():
            call_order.append("get_trading_settings")
            return TradingSettings(offer_min_quantity=5, offer_max_quantity=50)

        async def track_get_data():
            call_order.append("state.get_data")
            return {
                "trade_type_fa": "🟢 خرید",
                "commodity_name": "سکه",
                "wizard_return_to_review": False,
            }

        async def track_update_data(**kwargs):
            call_order.append(f"state.update_data(quantity={kwargs.get('quantity')})")

        def track_get_lot_type_keyboard(*args, **kwargs):
            call_order.append("get_lot_type_keyboard")
            return keyboard_sentinel

        async def track_answer_runtime(*args, **kwargs):
            call_order.append("answer_incoming_message_via_runtime")

        async def track_set_state(target_state):
            call_order.append("state.set_state(Trade.awaiting_lot_type)")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(side_effect=track_update_data),
            set_state=mock.AsyncMock(side_effect=track_set_state),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
                new_callable=mock.AsyncMock,
                side_effect=track_handoff,
            ) as mock_handoff,
            mock.patch(
                "bot.utils.offer_parser.normalize_digits",
                wraps=_REAL_NORMALIZE_DIGITS,
                side_effect=track_normalize_digits,
            ) as mock_normalize_digits,
            mock.patch(
                "core.trading_settings.get_trading_settings",
                side_effect=track_get_trading_settings,
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
                side_effect=track_get_lot_type_keyboard,
            ) as mock_get_lot_type_keyboard,
            mock.patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=track_answer_runtime,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.Message,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_message_answer,
        ):
            result = await trade_create.router.propagate_event(
                "message",
                event,
                state=state,
                user=runtime_user,
                raw_state=Trade.awaiting_quantity.state,
            )

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "_handoff_stale_wizard_state_to_text_offer",
                "normalize_digits with '۲۰'",
                "get_trading_settings",
                "state.get_data",
                "state.update_data(quantity=20)",
                "get_lot_type_keyboard",
                "answer_incoming_message_via_runtime",
                "state.set_state(Trade.awaiting_lot_type)",
            ],
        )

        mock_handoff.assert_awaited_once_with(event, state, runtime_user)
        mock_normalize_digits.assert_called_once_with("۲۰")
        mock_get_trading_settings.assert_called_once_with()
        state.get_data.assert_awaited_once_with()
        state.update_data.assert_awaited_once_with(quantity=20)
        mock_get_lot_type_keyboard.assert_called_once_with()

        expected_text = (
            "📈 **ثبت لفظ جدید**\n\n"
            "نوع معامله: 🟢 خرید\n"
            "کالا: سکه\n"
            "تعداد: 20\n\n"
            "📦 نحوه معامله را انتخاب کنید:"
        )
        mock_answer_runtime.assert_awaited_once_with(
            event,
            runtime_user,
            expected_text,
            source_key="offer-lot-type-prompt",
            action=TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
            parse_mode="Markdown",
            reply_markup=keyboard_sentinel,
            temporary_context_keyboard=True,
        )
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertIs(mock_answer_runtime.await_args.args[1], runtime_user)
        self.assertEqual(mock_answer_runtime.await_args.args[2], expected_text)
        self.assertEqual(
            mock_answer_runtime.await_args.kwargs,
            {
                "source_key": "offer-lot-type-prompt",
                "action": TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
                "parse_mode": "Markdown",
                "reply_markup": keyboard_sentinel,
                "temporary_context_keyboard": True,
            },
        )
        self.assertIs(
            mock_answer_runtime.await_args.kwargs["reply_markup"],
            keyboard_sentinel,
        )

        state.set_state.assert_awaited_once_with(Trade.awaiting_lot_type)
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_message_answer.assert_not_called()

    async def test_manual_quantity_router_return_to_review_retail_resets_lot_sizes_and_reopens_lot_size_prompt(
        self,
    ):
        chat = aiogram_types.Chat(id=1004, type="private")
        from_user = aiogram_types.User(
            id=54321,
            is_bot=False,
            first_name="EligibleUser",
        )
        event = aiogram_types.Message(
            message_id=2004,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
            from_user=from_user,
            text="۲۰",
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")

        call_order: list[str] = []

        async def track_handoff(message, state_arg, user_arg, bot=None):
            call_order.append("_handoff_stale_wizard_state_to_text_offer")
            return False

        def track_normalize_digits(text: str) -> str:
            call_order.append(f"normalize_digits with '{text}'")
            return _REAL_NORMALIZE_DIGITS(text)

        def track_get_trading_settings():
            call_order.append("get_trading_settings")
            return TradingSettings(offer_min_quantity=5, offer_max_quantity=50)

        async def track_get_data():
            call_order.append("state.get_data")
            return {
                "wizard_return_to_review": True,
                "is_wholesale": False,
            }

        async def track_update_data(**kwargs):
            if "quantity" in kwargs:
                call_order.append(f"state.update_data(quantity={kwargs.get('quantity')})")
            else:
                call_order.append(
                    "state.update_data(lot_sizes=None, wizard_edit_field='lot_sizes')"
                )

        async def track_show_lot_sizes_prompt(message, state_arg, *, edit, user=None):
            call_order.append("_show_lot_sizes_prompt")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(side_effect=track_update_data),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
                new_callable=mock.AsyncMock,
                side_effect=track_handoff,
            ) as mock_handoff,
            mock.patch(
                "bot.utils.offer_parser.normalize_digits",
                wraps=_REAL_NORMALIZE_DIGITS,
                side_effect=track_normalize_digits,
            ) as mock_normalize_digits,
            mock.patch(
                "core.trading_settings.get_trading_settings",
                side_effect=track_get_trading_settings,
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create._show_lot_sizes_prompt",
                new_callable=mock.AsyncMock,
                side_effect=track_show_lot_sizes_prompt,
            ) as mock_show_lot_sizes_prompt,
            mock.patch(
                "bot.handlers.trade_create._show_wizard_review",
                new_callable=mock.AsyncMock,
            ) as mock_show_wizard_review,
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
            ) as mock_get_lot_type_keyboard,
            mock.patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.Message,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_message_answer,
        ):
            result = await trade_create.router.propagate_event(
                "message",
                event,
                state=state,
                user=runtime_user,
                raw_state=Trade.awaiting_quantity.state,
            )

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "_handoff_stale_wizard_state_to_text_offer",
                "normalize_digits with '۲۰'",
                "get_trading_settings",
                "state.get_data",
                "state.update_data(quantity=20)",
                "state.update_data(lot_sizes=None, wizard_edit_field='lot_sizes')",
                "_show_lot_sizes_prompt",
            ],
        )

        mock_handoff.assert_awaited_once_with(event, state, runtime_user)
        self.assertIs(mock_handoff.await_args.args[0], event)
        self.assertIs(mock_handoff.await_args.args[1], state)
        self.assertIs(mock_handoff.await_args.args[2], runtime_user)

        mock_normalize_digits.assert_called_once_with("۲۰")
        mock_get_trading_settings.assert_called_once_with()
        state.get_data.assert_awaited_once_with()

        self.assertEqual(
            state.update_data.await_args_list,
            [
                mock.call(quantity=20),
                mock.call(lot_sizes=None, wizard_edit_field="lot_sizes"),
            ],
        )

        mock_show_lot_sizes_prompt.assert_awaited_once_with(
            event,
            state,
            edit=False,
            user=runtime_user,
        )
        self.assertIs(mock_show_lot_sizes_prompt.await_args.args[0], event)
        self.assertIs(mock_show_lot_sizes_prompt.await_args.args[1], state)
        self.assertEqual(
            mock_show_lot_sizes_prompt.await_args.kwargs,
            {"edit": False, "user": runtime_user},
        )
        self.assertIs(
            mock_show_lot_sizes_prompt.await_args.kwargs["user"],
            runtime_user,
        )
        self.assertFalse(mock_show_lot_sizes_prompt.await_args.kwargs["edit"])

        mock_show_wizard_review.assert_not_called()
        mock_get_lot_type_keyboard.assert_not_called()
        mock_answer_runtime.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_message_answer.assert_not_called()

    async def test_manual_quantity_router_return_to_review_wholesale_reopens_wizard_review(
        self,
    ):
        chat = aiogram_types.Chat(id=1005, type="private")
        from_user = aiogram_types.User(
            id=54321,
            is_bot=False,
            first_name="EligibleUser",
        )
        event = aiogram_types.Message(
            message_id=2005,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
            from_user=from_user,
            text="۲۰",
        )

        runtime_user = SimpleNamespace(id=54321, first_name="EligibleUser")

        call_order: list[str] = []

        async def track_handoff(message, state_arg, user_arg, bot=None):
            call_order.append("_handoff_stale_wizard_state_to_text_offer")
            return False

        def track_normalize_digits(text: str) -> str:
            call_order.append(f"normalize_digits with '{text}'")
            return _REAL_NORMALIZE_DIGITS(text)

        def track_get_trading_settings():
            call_order.append("get_trading_settings")
            return TradingSettings(offer_min_quantity=5, offer_max_quantity=50)

        async def track_get_data():
            call_order.append("state.get_data")
            return {
                "wizard_return_to_review": True,
                "is_wholesale": True,
            }

        async def track_update_data(**kwargs):
            call_order.append(f"state.update_data(quantity={kwargs.get('quantity')})")

        async def track_show_wizard_review(message, state_arg, *, edit, user=None):
            call_order.append("_show_wizard_review")

        state = SimpleNamespace(
            get_data=mock.AsyncMock(side_effect=track_get_data),
            update_data=mock.AsyncMock(side_effect=track_update_data),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            clear=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
                new_callable=mock.AsyncMock,
                side_effect=track_handoff,
            ) as mock_handoff,
            mock.patch(
                "bot.utils.offer_parser.normalize_digits",
                wraps=_REAL_NORMALIZE_DIGITS,
                side_effect=track_normalize_digits,
            ) as mock_normalize_digits,
            mock.patch(
                "core.trading_settings.get_trading_settings",
                side_effect=track_get_trading_settings,
            ) as mock_get_trading_settings,
            mock.patch(
                "bot.handlers.trade_create._show_wizard_review",
                new_callable=mock.AsyncMock,
                side_effect=track_show_wizard_review,
            ) as mock_show_wizard_review,
            mock.patch(
                "bot.handlers.trade_create._show_lot_sizes_prompt",
                new_callable=mock.AsyncMock,
            ) as mock_show_lot_sizes_prompt,
            mock.patch(
                "bot.handlers.trade_create.get_lot_type_keyboard",
            ) as mock_get_lot_type_keyboard,
            mock.patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.Message,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_message_answer,
        ):
            result = await trade_create.router.propagate_event(
                "message",
                event,
                state=state,
                user=runtime_user,
                raw_state=Trade.awaiting_quantity.state,
            )

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "_handoff_stale_wizard_state_to_text_offer",
                "normalize_digits with '۲۰'",
                "get_trading_settings",
                "state.get_data",
                "state.update_data(quantity=20)",
                "_show_wizard_review",
            ],
        )

        mock_handoff.assert_awaited_once_with(event, state, runtime_user)
        self.assertIs(mock_handoff.await_args.args[0], event)
        self.assertIs(mock_handoff.await_args.args[1], state)
        self.assertIs(mock_handoff.await_args.args[2], runtime_user)

        mock_normalize_digits.assert_called_once_with("۲۰")
        mock_get_trading_settings.assert_called_once_with()
        state.get_data.assert_awaited_once_with()

        state.update_data.assert_awaited_once_with(quantity=20)
        self.assertEqual(
            state.update_data.await_args_list,
            [mock.call(quantity=20)],
        )

        mock_show_wizard_review.assert_awaited_once_with(
            event,
            state,
            edit=False,
            user=runtime_user,
        )
        self.assertIs(mock_show_wizard_review.await_args.args[0], event)
        self.assertIs(mock_show_wizard_review.await_args.args[1], state)
        self.assertEqual(
            mock_show_wizard_review.await_args.kwargs,
            {"edit": False, "user": runtime_user},
        )
        self.assertIs(
            mock_show_wizard_review.await_args.kwargs["user"],
            runtime_user,
        )
        self.assertFalse(mock_show_wizard_review.await_args.kwargs["edit"])

        mock_show_lot_sizes_prompt.assert_not_called()
        mock_get_lot_type_keyboard.assert_not_called()
        mock_answer_runtime.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.clear.assert_not_called()
        mock_message_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
