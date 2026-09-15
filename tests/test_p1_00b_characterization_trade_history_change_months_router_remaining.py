"""Remaining characterization tests for trade history change months callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-CHANGE-HISTORY-MONTHS-L253

Exercises actual registered callback-query router dispatch for seed
BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-CHANGE-HISTORY-MONTHS-L253
(bot.handlers.trade_history.change_history_months, filter HistoryPageCallback.filter()).

Freezes three related scenarios only:
1. authenticated nonempty other-profile success rendering
2. edit_callback_message_via_runtime raising the real aiogram TelegramBadRequest while final callback acknowledgement still occurs
3. get_trade_history failure propagation with all downstream rendering/edit/acknowledgement suppressed

Accepted predecessor selectors at commit a059bb763f57c97b7cf5dd93934a4e184d0cc4ec:
- tests/test_p1_00b_characterization_trade_history_change_months_router.py::TestTradeHistoryChangeMonthsRouterCharacterization::test_change_history_months_router_unauthenticated_short_circuit
- tests/test_p1_00b_characterization_trade_history_change_months_router.py::TestTradeHistoryChangeMonthsRouterCharacterization::test_change_history_months_router_access_denied_short_circuit
- tests/test_p1_00b_characterization_trade_history_change_months_router.py::TestTradeHistoryChangeMonthsRouterCharacterization::test_change_history_months_router_empty_self_history_success_ordering

Excludes database/profile-query and denial-alert internals of _ensure_history_profile_access,
query behavior inside get_trade_history,
unauthenticated/access-denied/empty-self scenarios already accepted at a059bb763f57c97b7cf5dd93934a4e184d0cc4ec,
export/filter/show handlers, product/source changes, docs/index reconciliation,
seed/blocker closure, and Stage closure.

This partial unit closes no seed, blocker, reconciliation, or Stage.
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
from aiogram.exceptions import TelegramBadRequest

from bot.callbacks import HistoryPageCallback
from bot.handlers import trade_history
from models.trade import TradeType


class TestTradeHistoryChangeMonthsRouterRemainingCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    def _make_event(
        self,
        *,
        callback_id: str,
        user_id: int,
        chat_id: int,
        message_id: int,
        months: int,
        target_user_id: int,
    ) -> aiogram_types.CallbackQuery:
        return aiogram_types.CallbackQuery(
            id=callback_id,
            from_user=aiogram_types.User(
                id=user_id,
                is_bot=False,
                first_name=f"User{user_id}",
            ),
            chat_instance=f"chat_inst_{callback_id}",
            message=aiogram_types.Message(
                message_id=message_id,
                date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
                chat=aiogram_types.Chat(id=chat_id, type="private"),
            ),
            data=HistoryPageCallback(
                months=months,
                target_user_id=target_user_id,
            ).pack(),
        )

    def _make_mock_state(self, update_side_effect=None):
        return SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(side_effect=update_side_effect),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

    def _assert_unrelated_fsm_not_called(self, state):
        state.clear.assert_not_called()
        state.set_state.assert_not_called()
        state.set_data.assert_not_called()
        state.get_data.assert_not_called()

    async def test_change_history_months_router_nonempty_other_profile_success_rendering(
        self,
    ):
        event = self._make_event(
            callback_id="cb_other_profile_months",
            user_id=73,
            chat_id=73,
            message_id=2004,
            months=1,
            target_user_id=74,
        )
        runtime_user = SimpleNamespace(id=73)
        target = SimpleNamespace(id=74, account_name="counterpart-74")
        trade = SimpleNamespace(
            responder_user_id=73,
            trade_type=TradeType.BUY,
            commodity=SimpleNamespace(name="سکه"),
            quantity=2,
            settlement_type=None,
            price=123456,
            created_at=None,
            offer_user=target,
        )
        sentinel_keyboard = object()
        expected_text = (
            "📊 تاریخچه معاملات با counterpart-74\n\n"
            "🟢 خرید سکه 2 عدد نقد حاضر 123,456\n"
            "   📅 نامشخص\n\n"
        )

        order_tracker = mock.Mock()
        order_recorder: list[str] = []

        async def _record_ensure_access(*args, **kwargs):
            order_tracker._ensure_history_profile_access(*args, **kwargs)
            order_recorder.append("_ensure_history_profile_access")
            return True

        async def _record_update_data(*args, **kwargs):
            order_tracker.update_data(*args, **kwargs)
            order_recorder.append("state.update_data")

        async def _record_get_trade_history(*args, **kwargs):
            order_tracker.get_trade_history(*args, **kwargs)
            order_recorder.append("get_trade_history")
            return target, [trade]

        real_format = trade_history.format_trade_history

        def _record_format(*args, **kwargs):
            order_tracker.format_trade_history(*args, **kwargs)
            order_recorder.append("format_trade_history")
            return real_format(*args, **kwargs)

        def _record_keyboard(*args, **kwargs):
            order_tracker.get_trade_history_keyboard(*args, **kwargs)
            order_recorder.append("get_trade_history_keyboard")
            return sentinel_keyboard

        async def _record_edit(*args, **kwargs):
            order_tracker.edit_callback_message_via_runtime(*args, **kwargs)
            order_recorder.append("edit_callback_message_via_runtime")

        async def _record_answer(*args, **kwargs):
            order_tracker.answer_callback_query_via_runtime(*args, **kwargs)
            order_recorder.append("answer_callback_query_via_runtime")

        mock_state = self._make_mock_state(_record_update_data)

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
                side_effect=_record_format,
            ) as mock_format_history,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history_keyboard",
                side_effect=_record_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_history.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_edit,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_answer,
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
                    target_user_id=74,
                    callback=event,
                ),
                mock.call.update_data(history_months=1),
                mock.call.get_trade_history(73, 74, months=1),
                mock.call.format_trade_history([trade], target, 73),
                mock.call.get_trade_history_keyboard(74),
                mock.call.edit_callback_message_via_runtime(
                    event,
                    runtime_user,
                    expected_text,
                    reply_markup=sentinel_keyboard,
                ),
                mock.call.answer_callback_query_via_runtime(event),
            ],
            any_order=False,
        )
        mock_ensure_access.assert_awaited_once_with(
            user=runtime_user,
            target_user_id=74,
            callback=event,
        )
        self.assertIs(mock_ensure_access.await_args.kwargs["user"], runtime_user)
        self.assertIs(mock_ensure_access.await_args.kwargs["callback"], event)
        mock_state.update_data.assert_awaited_once_with(history_months=1)
        self._assert_unrelated_fsm_not_called(mock_state)

        mock_get_history.assert_awaited_once_with(73, 74, months=1)
        mock_format_history.assert_called_once_with([trade], target, 73)
        self.assertIs(mock_format_history.call_args.args[0][0], trade)
        self.assertIs(mock_format_history.call_args.args[1], target)

        mock_get_keyboard.assert_called_once_with(74)
        mock_edit_runtime.assert_awaited_once_with(
            event,
            runtime_user,
            expected_text,
            reply_markup=sentinel_keyboard,
        )
        self.assertIs(mock_edit_runtime.await_args.args[0], event)
        self.assertIs(mock_edit_runtime.await_args.args[1], runtime_user)
        self.assertIs(
            mock_edit_runtime.await_args.kwargs["reply_markup"],
            sentinel_keyboard,
        )
        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        mock_direct_answer.assert_not_called()

    async def test_change_history_months_router_telegram_bad_request_still_answers(
        self,
    ):
        event = self._make_event(
            callback_id="cb_bad_request_months",
            user_id=73,
            chat_id=73,
            message_id=2005,
            months=3,
            target_user_id=73,
        )
        runtime_user = SimpleNamespace(id=73)
        sentinel_text = "controlled-text-sentinel-bad-request"
        sentinel_keyboard = object()
        bad_request_exc = TelegramBadRequest(
            method=mock.Mock(),
            message="Bad Request: message is not modified",
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

        def _record_format(*args, **kwargs):
            order_tracker.format_trade_history(*args, **kwargs)
            order_recorder.append("format_trade_history")
            return sentinel_text

        def _record_keyboard(*args, **kwargs):
            order_tracker.get_trade_history_keyboard(*args, **kwargs)
            order_recorder.append("get_trade_history_keyboard")
            return sentinel_keyboard

        async def _record_edit(*args, **kwargs):
            order_tracker.edit_callback_message_via_runtime(*args, **kwargs)
            order_recorder.append("edit_callback_message_via_runtime")
            raise bad_request_exc

        async def _record_answer(*args, **kwargs):
            order_tracker.answer_callback_query_via_runtime(*args, **kwargs)
            order_recorder.append("answer_callback_query_via_runtime")

        mock_state = self._make_mock_state(_record_update_data)

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
                side_effect=_record_format,
            ) as mock_format_history,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history_keyboard",
                side_effect=_record_keyboard,
            ) as mock_get_keyboard,
            mock.patch(
                "bot.handlers.trade_history.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_edit,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_answer,
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
                mock.call.update_data(history_months=3),
                mock.call.get_trade_history(73, 73, months=3),
                mock.call.format_trade_history([], None, 73),
                mock.call.get_trade_history_keyboard(73),
                mock.call.edit_callback_message_via_runtime(
                    event,
                    runtime_user,
                    sentinel_text,
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
        self.assertIs(mock_ensure_access.await_args.kwargs["callback"], event)
        mock_state.update_data.assert_awaited_once_with(history_months=3)
        self._assert_unrelated_fsm_not_called(mock_state)

        mock_get_history.assert_awaited_once_with(73, 73, months=3)
        mock_format_history.assert_called_once_with([], None, 73)
        mock_get_keyboard.assert_called_once_with(73)
        mock_edit_runtime.assert_awaited_once_with(
            event,
            runtime_user,
            sentinel_text,
            reply_markup=sentinel_keyboard,
        )
        self.assertIs(mock_edit_runtime.await_args.args[0], event)
        self.assertIs(mock_edit_runtime.await_args.args[1], runtime_user)
        self.assertIs(
            mock_edit_runtime.await_args.kwargs["reply_markup"],
            sentinel_keyboard,
        )
        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        mock_direct_answer.assert_not_called()

    async def test_change_history_months_router_history_failure_propagates(self):
        event = self._make_event(
            callback_id="cb_history_failure_months",
            user_id=73,
            chat_id=73,
            message_id=2006,
            months=6,
            target_user_id=73,
        )
        runtime_user = SimpleNamespace(id=73)

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
            raise RuntimeError("history-service-failure")

        mock_state = self._make_mock_state(_record_update_data)

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
            with self.assertRaises(RuntimeError) as exc_ctx:
                await trade_history.router.propagate_event(
                    "callback_query",
                    event,
                    state=mock_state,
                    user=runtime_user,
                    raw_state=None,
                )

        self.assertEqual(str(exc_ctx.exception), "history-service-failure")
        self.assertEqual(
            order_recorder,
            [
                "_ensure_history_profile_access",
                "state.update_data",
                "get_trade_history",
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
            ],
            any_order=False,
        )
        mock_ensure_access.assert_awaited_once_with(
            user=runtime_user,
            target_user_id=73,
            callback=event,
        )
        self.assertIs(mock_ensure_access.await_args.kwargs["user"], runtime_user)
        self.assertIs(mock_ensure_access.await_args.kwargs["callback"], event)
        mock_state.update_data.assert_awaited_once_with(history_months=6)
        self._assert_unrelated_fsm_not_called(mock_state)

        mock_get_history.assert_awaited_once_with(73, 73, months=6)
        mock_format_history.assert_not_called()
        mock_get_keyboard.assert_not_called()
        mock_edit_runtime.assert_not_called()
        mock_answer_runtime.assert_not_called()
        mock_direct_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
