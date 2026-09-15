"""Characterization tests for trade history change months callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-CHANGE-HISTORY-MONTHS-L253

This characterization test file exercises actual registered callback-query router dispatch
for seed BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-CHANGE-HISTORY-MONTHS-L253
(bot.handlers.trade_history.change_history_months, filter HistoryPageCallback.filter()).

Freezes three related scenarios only:
1. unauthenticated callback that short-circuits without side effects
2. authenticated other-profile callback whose access-gate result is false and which stops before state/history/rendering work
3. authenticated six-month self-history callback with an empty result that preserves the current state, query, format, keyboard, edit and callback-answer ordering

Excludes the database/profile-query and denial-alert internals of _ensure_history_profile_access,
query behavior inside get_trade_history, nonempty-history formatting,
export/filter/show handlers, TelegramBadRequest swallowing, product/source changes,
docs/index reconciliation, seed/blocker closure, and Stage closure.

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

from bot.callbacks import HistoryPageCallback
from bot.handlers import trade_history


class TestTradeHistoryChangeMonthsRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    def _make_callback_event(
        self,
        *,
        callback_id: str,
        user_id: int,
        chat_id: int,
        message_id: int,
        months: int,
        target_user_id: int,
    ) -> aiogram_types.CallbackQuery:
        packed_data = HistoryPageCallback(
            months=months,
            target_user_id=target_user_id,
        ).pack()
        chat = aiogram_types.Chat(id=chat_id, type="private")
        message = aiogram_types.Message(
            message_id=message_id,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        return aiogram_types.CallbackQuery(
            id=callback_id,
            from_user=aiogram_types.User(
                id=user_id,
                is_bot=False,
                first_name=f"User{user_id}",
            ),
            chat_instance=f"chat_inst_{callback_id}",
            message=message,
            data=packed_data,
        )

    async def test_change_history_months_router_unauthenticated_short_circuit(self):
        event = self._make_callback_event(
            callback_id="cb_unauth_months",
            user_id=1001,
            chat_id=1001,
            message_id=2001,
            months=1,
            target_user_id=1001,
        )

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_history._ensure_history_profile_access",
                new_callable=mock.AsyncMock,
            ) as mock_ensure_access,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history",
                new_callable=mock.AsyncMock,
            ) as mock_get_history,
            mock.patch(
                "bot.handlers.trade_history.format_trade_history",
                side_effect=trade_history.format_trade_history,
            ) as mock_format_history,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history_keyboard",
                side_effect=trade_history.get_trade_history_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_history.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_direct_answer,
        ):
            result = await trade_history.router.propagate_event(
                "callback_query",
                event,
                state=mock_state,
                user=None,
                raw_state=None,
            )

        self.assertIsNone(result)
        mock_ensure_access.assert_not_called()
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()
        mock_get_history.assert_not_called()
        mock_format_history.assert_not_called()
        mock_get_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_answer_runtime.assert_not_called()
        mock_direct_answer.assert_not_called()

    async def test_change_history_months_router_access_denied_short_circuit(self):
        event = self._make_callback_event(
            callback_id="cb_denied_months",
            user_id=73,
            chat_id=73,
            message_id=2002,
            months=3,
            target_user_id=74,
        )
        runtime_user = SimpleNamespace(id=73)

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_history._ensure_history_profile_access",
                new_callable=mock.AsyncMock,
                return_value=False,
            ) as mock_ensure_access,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history",
                new_callable=mock.AsyncMock,
            ) as mock_get_history,
            mock.patch(
                "bot.handlers.trade_history.format_trade_history",
                side_effect=trade_history.format_trade_history,
            ) as mock_format_history,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history_keyboard",
                side_effect=trade_history.get_trade_history_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_history.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_direct_answer,
        ):
            result = await trade_history.router.propagate_event(
                "callback_query",
                event,
                state=mock_state,
                user=runtime_user,
                raw_state=None,
            )

        self.assertIsNone(result)
        mock_ensure_access.assert_awaited_once_with(
            user=runtime_user,
            target_user_id=74,
            callback=event,
        )
        self.assertIs(mock_ensure_access.await_args.kwargs["user"], runtime_user)
        self.assertEqual(mock_ensure_access.await_args.kwargs["target_user_id"], 74)
        self.assertIs(mock_ensure_access.await_args.kwargs["callback"], event)
        self.assertEqual(mock_ensure_access.await_args.args, ())

        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()
        mock_get_history.assert_not_called()
        mock_format_history.assert_not_called()
        mock_get_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_answer_runtime.assert_not_called()
        mock_direct_answer.assert_not_called()

    async def test_change_history_months_router_empty_self_history_success_ordering(self):
        event = self._make_callback_event(
            callback_id="cb_empty_self_months",
            user_id=73,
            chat_id=73,
            message_id=2003,
            months=6,
            target_user_id=73,
        )
        runtime_user = SimpleNamespace(id=73)
        sentinel_keyboard = object()
        expected_empty_text = (
            "📊 تاریخچه معاملات کل شما\n\n⚠️ معامله" + chr(0x200C) + "ای یافت نشد."
        )

        order_tracker = mock.Mock()
        order_recorder: list[str] = []

        real_ensure_access = trade_history._ensure_history_profile_access

        async def _record_ensure_access(*args, **kwargs):
            order_tracker._ensure_history_profile_access(*args, **kwargs)
            order_recorder.append("_ensure_history_profile_access")
            return await real_ensure_access(*args, **kwargs)

        async def _record_update_data(*args, **kwargs):
            order_tracker.update_data(*args, **kwargs)
            order_recorder.append("state.update_data")

        async def _record_get_trade_history(*args, **kwargs):
            order_tracker.get_trade_history(*args, **kwargs)
            order_recorder.append("get_trade_history")
            return None, []

        real_format_trade_history = trade_history.format_trade_history

        def _record_format_trade_history(*args, **kwargs):
            order_tracker.format_trade_history(*args, **kwargs)
            order_recorder.append("format_trade_history")
            return real_format_trade_history(*args, **kwargs)

        def _record_get_keyboard(*args, **kwargs):
            order_tracker.get_trade_history_keyboard(*args, **kwargs)
            order_recorder.append("get_trade_history_keyboard")
            return sentinel_keyboard

        async def _record_edit_callback(*args, **kwargs):
            order_tracker.edit_callback_message_via_runtime(*args, **kwargs)
            order_recorder.append("edit_callback_message_via_runtime")

        async def _record_answer_runtime(*args, **kwargs):
            order_tracker.answer_callback_query_via_runtime(*args, **kwargs)
            order_recorder.append("answer_callback_query_via_runtime")

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(side_effect=_record_update_data),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_history._ensure_history_profile_access",
                new_callable=mock.AsyncMock,
                side_effect=_record_ensure_access,
            ) as mock_ensure_access,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history",
                new_callable=mock.AsyncMock,
                side_effect=_record_get_trade_history,
            ) as mock_get_history,
            mock.patch(
                "bot.handlers.trade_history.format_trade_history",
                side_effect=_record_format_trade_history,
            ) as mock_format_history,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history_keyboard",
                side_effect=_record_get_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_history.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_edit_callback,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_answer_runtime,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_direct_answer,
        ):
            result = await trade_history.router.propagate_event(
                "callback_query",
                event,
                state=mock_state,
                user=runtime_user,
                raw_state=None,
            )

        self.assertIsNone(result)

        self.assertEqual(
            order_recorder,
            [
                "_ensure_history_profile_access",
                "state.update_data",
                "get_trade_history",
                "format_trade_history",
                "get_trade_history_keyboard",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )

        order_tracker.assert_has_calls(
            [
                mock.call._ensure_history_profile_access(
                    user=runtime_user,
                    target_user_id=73,
                    callback=event,
                ),
                mock.call.update_data(history_months=6),
                mock.call.get_trade_history(73, 73, months=6),
                mock.call.format_trade_history([], None, 73),
                mock.call.get_trade_history_keyboard(73),
                mock.call.edit_callback_message_via_runtime(
                    event,
                    runtime_user,
                    expected_empty_text,
                    reply_markup=sentinel_keyboard,
                ),
                mock.call.answer_callback_query_via_runtime(event),
            ],
            any_order=False,
        )

        mock_ensure_access.assert_awaited_once_with(
            user=runtime_user,
            target_user_id=73,
            callback=event,
        )
        self.assertIs(mock_ensure_access.await_args.kwargs["user"], runtime_user)
        self.assertEqual(mock_ensure_access.await_args.kwargs["target_user_id"], 73)
        self.assertIs(mock_ensure_access.await_args.kwargs["callback"], event)
        self.assertEqual(mock_ensure_access.await_args.args, ())

        mock_state.update_data.assert_awaited_once_with(history_months=6)
        self.assertEqual(mock_state.update_data.await_count, 1)
        mock_state.clear.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()

        mock_get_history.assert_awaited_once_with(73, 73, months=6)
        self.assertEqual(mock_get_history.await_args.args, (73, 73))
        self.assertEqual(mock_get_history.await_args.kwargs, {"months": 6})

        mock_format_history.assert_called_once_with([], None, 73)
        self.assertEqual(mock_format_history.call_args.args, ([], None, 73))
        self.assertEqual(mock_format_history.call_args.kwargs, {})

        mock_get_keyboard.assert_called_once_with(73)
        self.assertEqual(mock_get_keyboard.call_args.args, (73,))
        self.assertEqual(mock_get_keyboard.call_args.kwargs, {})

        mock_edit_runtime.assert_awaited_once_with(
            event,
            runtime_user,
            expected_empty_text,
            reply_markup=sentinel_keyboard,
        )
        self.assertIs(mock_edit_runtime.await_args.args[0], event)
        self.assertIs(mock_edit_runtime.await_args.args[1], runtime_user)
        self.assertEqual(mock_edit_runtime.await_args.args[2], expected_empty_text)
        self.assertEqual(
            mock_edit_runtime.await_args.kwargs,
            {"reply_markup": sentinel_keyboard},
        )
        self.assertIs(
            mock_edit_runtime.await_args.kwargs["reply_markup"],
            sentinel_keyboard,
        )

        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(mock_answer_runtime.await_args.args, (event,))
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {})

        mock_direct_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
