"""Characterization tests for trade history show-my message router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-TRADE-MESSAGE-TRADE-HISTORY-SHOW-MY-TRADE-HISTORY-L231

This characterization test file exercises actual registered message-router dispatch
for seed BOT-TRADE-MESSAGE-TRADE-HISTORY-SHOW-MY-TRADE-HISTORY-L231
(bot.handlers.trade_history.show_my_trade_history, filter F.text == "📊 تاریخچه معاملات من").

Freezes two related scenarios only:
1. unauthenticated short-circuiting without side effects
2. eligible-user empty-self-history success ordering

Excludes callback history handlers, nonempty-history formatting,
database/query behavior inside get_trade_history, export behavior,
alternate message text, product/source changes, docs/index reconciliation,
seed/blocker closure, and Stage closure.

This is partial evidence only: this partial unit closes no seed, blocker,
reconciliation, or Stage.
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

from bot.handlers import trade_history
from core.telegram_delivery_queue_contract import TelegramDeliveryAction


class TestTradeHistoryShowMyRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_show_my_trade_history_router_unauthenticated_short_circuit(self):
        event = aiogram_types.Message(
            message_id=101,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=aiogram_types.Chat(id=1001, type="private"),
            from_user=aiogram_types.User(
                id=1001,
                is_bot=False,
                first_name="UnauthUser",
            ),
            text="📊 تاریخچه معاملات من",
        )

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_history.get_trade_history",
                new_callable=mock.AsyncMock,
            ) as mock_get_trade_history,
            mock.patch(
                "bot.handlers.trade_history.format_trade_history",
                side_effect=trade_history.format_trade_history,
            ) as mock_format_history,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history_keyboard",
                side_effect=trade_history.get_trade_history_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_history.answer_incoming_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_incoming,
            mock.patch.object(
                aiogram_types.Message,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_message_answer,
        ):
            result = await trade_history.router.propagate_event(
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
        mock_get_trade_history.assert_not_called()
        mock_format_history.assert_not_called()
        mock_get_keyboard.assert_not_called()
        mock_answer_incoming.assert_not_called()
        mock_message_answer.assert_not_called()

    async def test_show_my_trade_history_router_empty_self_history_success_ordering(self):
        event = aiogram_types.Message(
            message_id=102,
            date=datetime(2026, 9, 15, 12, 1, 0, tzinfo=timezone.utc),
            chat=aiogram_types.Chat(id=73, type="private"),
            from_user=aiogram_types.User(
                id=73,
                is_bot=False,
                first_name="EligibleUser",
            ),
            text="📊 تاریخچه معاملات من",
        )
        runtime_user = SimpleNamespace(id=73)
        sentinel_keyboard = object()
        expected_empty_text = (
            "📊 تاریخچه معاملات کل شما\n\n⚠️ معامله" + chr(0x200C) + "ای یافت نشد."
        )

        order_tracker = mock.Mock()
        order_recorder: list[str] = []

        async def _record_get_trade_history(*args, **kwargs):
            order_tracker.get_trade_history(*args, **kwargs)
            order_recorder.append("get_trade_history")
            return None, []

        async def _record_update_data(*args, **kwargs):
            order_tracker.update_data(*args, **kwargs)
            order_recorder.append("state.update_data")

        real_format_trade_history = trade_history.format_trade_history

        def _record_format_trade_history(*args, **kwargs):
            order_tracker.format_trade_history(*args, **kwargs)
            order_recorder.append("format_trade_history")
            return real_format_trade_history(*args, **kwargs)

        def _record_get_keyboard(*args, **kwargs):
            order_tracker.get_trade_history_keyboard(*args, **kwargs)
            order_recorder.append("get_trade_history_keyboard")
            return sentinel_keyboard

        async def _record_answer_incoming(*args, **kwargs):
            order_tracker.answer_incoming_message_via_runtime(*args, **kwargs)
            order_recorder.append("answer_incoming_message_via_runtime")

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(side_effect=_record_update_data),
            set_state=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_history.get_trade_history",
                new_callable=mock.AsyncMock,
                side_effect=_record_get_trade_history,
            ) as mock_get_trade_history,
            mock.patch(
                "bot.handlers.trade_history.format_trade_history",
                side_effect=_record_format_trade_history,
            ) as mock_format_history,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history_keyboard",
                side_effect=_record_get_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_history.answer_incoming_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_answer_incoming,
            ) as mock_answer_incoming,
            mock.patch.object(
                aiogram_types.Message,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_message_answer,
        ):
            result = await trade_history.router.propagate_event(
                "message",
                event,
                state=mock_state,
                user=runtime_user,
                raw_state=None,
            )

        self.assertIsNone(result)

        self.assertEqual(
            order_recorder,
            [
                "get_trade_history",
                "state.update_data",
                "format_trade_history",
                "get_trade_history_keyboard",
                "answer_incoming_message_via_runtime",
            ],
        )
        order_tracker.assert_has_calls(
            [
                mock.call.get_trade_history(73, 73, months=3),
                mock.call.update_data(history_months=3),
                mock.call.format_trade_history([], None, 73),
                mock.call.get_trade_history_keyboard(73),
                mock.call.answer_incoming_message_via_runtime(
                    event,
                    runtime_user,
                    expected_empty_text,
                    source_key="history-main",
                    action=TelegramDeliveryAction.TRADE_NONCRITICAL,
                    reply_markup=sentinel_keyboard,
                ),
            ],
            any_order=False,
        )

        mock_get_trade_history.assert_awaited_once_with(73, 73, months=3)
        self.assertEqual(mock_get_trade_history.await_args.args, (73, 73))
        self.assertEqual(mock_get_trade_history.await_args.kwargs, {"months": 3})

        mock_state.update_data.assert_awaited_once_with(history_months=3)
        self.assertEqual(mock_state.update_data.await_count, 1)
        mock_state.clear.assert_not_called()
        mock_state.set_state.assert_not_called()

        mock_format_history.assert_called_once_with([], None, 73)
        self.assertEqual(mock_format_history.call_args.args, ([], None, 73))
        self.assertEqual(mock_format_history.call_args.kwargs, {})

        mock_get_keyboard.assert_called_once_with(73)
        self.assertEqual(mock_get_keyboard.call_args.args, (73,))
        self.assertEqual(mock_get_keyboard.call_args.kwargs, {})

        mock_answer_incoming.assert_awaited_once_with(
            event,
            runtime_user,
            expected_empty_text,
            source_key="history-main",
            action=TelegramDeliveryAction.TRADE_NONCRITICAL,
            reply_markup=sentinel_keyboard,
        )
        self.assertEqual(
            mock_answer_incoming.await_args.args,
            (event, runtime_user, expected_empty_text),
        )
        self.assertIs(mock_answer_incoming.await_args.args[0], event)
        self.assertIs(mock_answer_incoming.await_args.args[1], runtime_user)
        self.assertEqual(mock_answer_incoming.await_args.args[2], expected_empty_text)
        self.assertEqual(
            mock_answer_incoming.await_args.kwargs,
            {
                "source_key": "history-main",
                "action": TelegramDeliveryAction.TRADE_NONCRITICAL,
                "reply_markup": sentinel_keyboard,
            },
        )
        self.assertIs(
            mock_answer_incoming.await_args.kwargs["action"],
            TelegramDeliveryAction.TRADE_NONCRITICAL,
        )
        self.assertIs(
            mock_answer_incoming.await_args.kwargs["reply_markup"],
            sentinel_keyboard,
        )

        mock_message_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
