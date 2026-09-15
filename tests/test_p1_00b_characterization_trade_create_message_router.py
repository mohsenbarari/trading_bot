"""Characterization tests for trade creation message router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-OFFER-MESSAGE-TRADE-CREATE-HANDLE-TRADE-BUTTON-L645

This characterization test file executes actual registered message-router dispatch
for seed BOT-OFFER-MESSAGE-TRADE-CREATE-HANDLE-TRADE-BUTTON-L645
(bot.handlers.trade_create.handle_trade_button, filter F.text == "📈 معامله").

Freezes two related scenarios only:
1. unauthenticated short-circuiting
2. eligible-user success ordering

This is partial evidence only: this partial unit closes no seed, blocker,
reconciliation, or Stage P1-00b.
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
from core.telegram_delivery_queue_contract import TelegramDeliveryAction


class TestTradeCreateMessageRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_trade_button_router_unauthenticated_short_circuit(self):
        event = aiogram_types.Message(
            message_id=101,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=aiogram_types.Chat(id=1001, type="private"),
            from_user=aiogram_types.User(
                id=1001,
                is_bot=False,
                first_name="UnauthUser",
            ),
            text="📈 معامله",
        )

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create._bot_trade_access_denial_reason",
                new_callable=mock.AsyncMock,
            ) as mock_denial_reason,
            mock.patch(
                "bot.handlers.trade_create._bot_market_is_open",
                new_callable=mock.AsyncMock,
            ) as mock_market_is_open,
            mock.patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_incoming,
            mock.patch.object(
                aiogram_types.Message,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_message_answer,
        ):
            result = await trade_create.router.propagate_event(
                "message",
                event,
                state=mock_state,
                user=None,
                raw_state=None,
            )

        self.assertIsNone(result)
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_denial_reason.assert_not_called()
        mock_market_is_open.assert_not_called()
        mock_answer_incoming.assert_not_called()
        mock_message_answer.assert_not_called()

    async def test_trade_button_router_eligible_success_ordering(self):
        event = aiogram_types.Message(
            message_id=102,
            date=datetime(2026, 9, 15, 12, 1, 0, tzinfo=timezone.utc),
            chat=aiogram_types.Chat(id=2002, type="private"),
            from_user=aiogram_types.User(
                id=2002,
                is_bot=False,
                first_name="EligibleUser",
            ),
            text="📈 معامله",
        )
        runtime_user = SimpleNamespace(
            id=2002,
            trading_restricted_until=None,
        )
        sentinel_keyboard = object()

        order_tracker = mock.Mock()
        order_recorder: list[str] = []

        async def _record_clear(*args, **kwargs):
            order_tracker.clear(*args, **kwargs)
            order_recorder.append("state.clear")

        async def _record_update_data(*args, **kwargs):
            order_tracker.update_data(*args, **kwargs)
            order_recorder.append("state.update_data")

        async def _record_answer(*args, **kwargs):
            order_tracker.answer_incoming_message_via_runtime(*args, **kwargs)
            order_recorder.append("answer_incoming_message_via_runtime")

        async def _record_set_state(*args, **kwargs):
            order_tracker.set_state(*args, **kwargs)
            order_recorder.append("state.set_state")

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(side_effect=_record_clear),
            update_data=mock.AsyncMock(side_effect=_record_update_data),
            set_state=mock.AsyncMock(side_effect=_record_set_state),
        )

        with (
            mock.patch(
                "bot.handlers.trade_create._bot_trade_access_denial_reason",
                new_callable=mock.AsyncMock,
                return_value=None,
            ) as mock_denial_reason,
            mock.patch(
                "bot.handlers.trade_create._bot_market_is_open",
                new_callable=mock.AsyncMock,
                return_value=True,
            ) as mock_market_is_open,
            mock.patch(
                "bot.handlers.trade_create.get_trade_type_keyboard",
                return_value=sentinel_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_answer,
            ) as mock_answer_incoming,
            mock.patch.object(
                aiogram_types.Message,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_message_answer,
        ):
            result = await trade_create.router.propagate_event(
                "message",
                event,
                state=mock_state,
                user=runtime_user,
                raw_state=None,
            )

        self.assertIsNone(result)

        mock_denial_reason.assert_awaited_once_with(runtime_user)
        self.assertEqual(mock_denial_reason.await_args.args, (runtime_user,))
        self.assertIs(mock_denial_reason.await_args.args[0], runtime_user)

        mock_market_is_open.assert_awaited_once_with()
        self.assertEqual(mock_market_is_open.await_args.args, ())
        self.assertEqual(mock_market_is_open.await_args.kwargs, {})

        mock_get_keyboard.assert_called_once_with()
        self.assertEqual(mock_get_keyboard.call_args.args, ())
        self.assertEqual(mock_get_keyboard.call_args.kwargs, {})

        mock_message_answer.assert_not_called()

        self.assertEqual(
            order_recorder,
            [
                "state.clear",
                "state.update_data",
                "answer_incoming_message_via_runtime",
                "state.set_state",
            ],
        )
        order_tracker.assert_has_calls(
            [
                mock.call.clear(),
                mock.call.update_data(wizard_return_to_review=False),
                mock.call.answer_incoming_message_via_runtime(
                    event,
                    runtime_user,
                    "📈 ثبت آفر\n\nنوع معامله را انتخاب کنید:",
                    source_key="offer-wizard-start",
                    action=TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
                    reply_markup=sentinel_keyboard,
                    temporary_context_keyboard=True,
                ),
                mock.call.set_state(Trade.awaiting_trade_type),
            ],
            any_order=False,
        )

        mock_state.clear.assert_awaited_once_with()
        mock_state.update_data.assert_awaited_once_with(wizard_return_to_review=False)
        mock_state.set_state.assert_awaited_once_with(Trade.awaiting_trade_type)

        self.assertEqual(
            mock_answer_incoming.await_args.args,
            (
                event,
                runtime_user,
                "📈 ثبت آفر\n\nنوع معامله را انتخاب کنید:",
            ),
        )
        self.assertIs(mock_answer_incoming.await_args.args[0], event)
        self.assertIs(mock_answer_incoming.await_args.args[1], runtime_user)
        self.assertEqual(
            mock_answer_incoming.await_args.kwargs,
            {
                "source_key": "offer-wizard-start",
                "action": TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
                "reply_markup": sentinel_keyboard,
                "temporary_context_keyboard": True,
            },
        )
        self.assertIs(
            mock_answer_incoming.await_args.kwargs["reply_markup"],
            sentinel_keyboard,
        )


if __name__ == "__main__":
    unittest.main()
