"""Characterization tests for trade history export excel callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-EXPORT-EXCEL-L381

This characterization test file exercises actual registered callback-query router dispatch
for seed BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-EXPORT-EXCEL-L381
(bot.handlers.trade_history.export_excel, filter ExportHistoryCallback.filter(F.format == "excel")).

The accepted direct-handler test file tests/test_bot_trade_history_export_excel.py
tests export_excel by direct invocation and does not prove registered router dispatch.
The accepted change-months router test file is a pattern reference only.

Freezes exactly three entry/early-exit behaviors:
1. unauthenticated suppression: unauthenticated callback (user=None) returns None and suppresses all work
2. authenticated progress-answer then state-read then profile-access denial ordering: progress answer precedes FSM state read and access-gate check, and denial short-circuits further processing
3. authenticated access-granted empty-history messaging using the default three-month window: when state data omits history_months, defaults to 3 months, queries trade history, and sends the empty-history warning

Excludes document-send success/temp-file cleanup and generation-error behavior,
profile-access internals (_ensure_history_profile_access),
get_trade_history query internals, seed/blocker reconciliation, and Stage closure.
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

from bot.callbacks import ExportHistoryCallback
from bot.handlers import trade_history
from core.telegram_delivery_queue_contract import TelegramDeliveryAction


class TestTradeHistoryExportExcelRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    def _make_callback_event(
        self,
        *,
        callback_id: str,
        user_id: int,
        chat_id: int,
        message_id: int,
        target_user_id: int,
    ) -> aiogram_types.CallbackQuery:
        packed_data = ExportHistoryCallback(
            format="excel",
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

    async def test_export_excel_router_unauthenticated_suppresses_all_work(self):
        event = self._make_callback_event(
            callback_id="cb_unauth_export_excel",
            user_id=1001,
            chat_id=1001,
            message_id=3001,
            target_user_id=1002,
        )
        mock_bot = mock.AsyncMock()
        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

        with (
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_callback_query,
            mock.patch(
                "bot.handlers.trade_history._ensure_history_profile_access",
                new_callable=mock.AsyncMock,
            ) as mock_ensure_access,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history",
                new_callable=mock.AsyncMock,
            ) as mock_get_trade_history,
            mock.patch(
                "bot.handlers.trade_history.generate_excel",
                new_callable=mock.AsyncMock,
            ) as mock_generate_excel,
            mock.patch(
                "bot.handlers.trade_history.send_private_document_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_send_private_document,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_callback_message,
            mock.patch(
                "bot.handlers.trade_history.os.remove",
            ) as mock_os_remove,
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
                bot=mock_bot,
                raw_state=None,
            )

        self.assertIsNone(result)
        mock_answer_callback_query.assert_not_called()
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()
        mock_ensure_access.assert_not_called()
        mock_get_trade_history.assert_not_called()
        mock_generate_excel.assert_not_called()
        mock_send_private_document.assert_not_called()
        mock_answer_callback_message.assert_not_called()
        mock_os_remove.assert_not_called()
        mock_direct_answer.assert_not_called()

    async def test_export_excel_router_progress_precedes_state_and_access_denied_short_circuit(self):
        packed_target_id = 85
        event = self._make_callback_event(
            callback_id="cb_denied_export_excel",
            user_id=84,
            chat_id=84,
            message_id=3002,
            target_user_id=packed_target_id,
        )
        runtime_user = SimpleNamespace(id=84)
        mock_bot = mock.AsyncMock()

        order_tracker = mock.Mock()
        order_recorder: list[str] = []

        progress_text = "⏳ در حال ایجاد فایل Excel..."

        async def _record_answer_callback_query(*args, **kwargs):
            order_tracker.answer_callback_query_via_runtime(*args, **kwargs)
            order_recorder.append("answer_callback_query_via_runtime")

        async def _record_get_data(*args, **kwargs):
            order_tracker.get_data(*args, **kwargs)
            order_recorder.append("state.get_data")
            return {"history_months": 6}

        async def _record_ensure_access(*args, **kwargs):
            order_tracker._ensure_history_profile_access(*args, **kwargs)
            order_recorder.append("_ensure_history_profile_access")
            return False

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(side_effect=_record_get_data),
        )

        with (
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_answer_callback_query,
            ) as mock_answer_callback_query,
            mock.patch(
                "bot.handlers.trade_history._ensure_history_profile_access",
                new_callable=mock.AsyncMock,
                side_effect=_record_ensure_access,
            ) as mock_ensure_access,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history",
                new_callable=mock.AsyncMock,
            ) as mock_get_trade_history,
            mock.patch(
                "bot.handlers.trade_history.generate_excel",
                new_callable=mock.AsyncMock,
            ) as mock_generate_excel,
            mock.patch(
                "bot.handlers.trade_history.send_private_document_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_send_private_document,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_callback_message,
            mock.patch(
                "bot.handlers.trade_history.os.remove",
            ) as mock_os_remove,
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
                bot=mock_bot,
                raw_state=None,
            )

        self.assertIsNone(result)

        self.assertEqual(
            order_recorder,
            [
                "answer_callback_query_via_runtime",
                "state.get_data",
                "_ensure_history_profile_access",
            ],
        )

        order_tracker.assert_has_calls(
            [
                mock.call.answer_callback_query_via_runtime(
                    event,
                    progress_text,
                ),
                mock.call.get_data(),
                mock.call._ensure_history_profile_access(
                    user=runtime_user,
                    target_user_id=packed_target_id,
                    callback=event,
                ),
            ],
            any_order=False,
        )

        mock_answer_callback_query.assert_awaited_once_with(
            event,
            progress_text,
        )
        self.assertIs(mock_answer_callback_query.await_args.args[0], event)
        self.assertEqual(mock_answer_callback_query.await_args.args[1], progress_text)
        self.assertEqual(mock_answer_callback_query.await_args.kwargs, {})

        mock_state.get_data.assert_awaited_once_with()
        self.assertEqual(mock_state.get_data.await_count, 1)
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()

        mock_ensure_access.assert_awaited_once_with(
            user=runtime_user,
            target_user_id=packed_target_id,
            callback=event,
        )
        self.assertIs(mock_ensure_access.await_args.kwargs["user"], runtime_user)
        self.assertEqual(mock_ensure_access.await_args.kwargs["target_user_id"], packed_target_id)
        self.assertIs(mock_ensure_access.await_args.kwargs["callback"], event)
        self.assertEqual(mock_ensure_access.await_args.args, ())

        mock_get_trade_history.assert_not_called()
        mock_generate_excel.assert_not_called()
        mock_send_private_document.assert_not_called()
        mock_answer_callback_message.assert_not_called()
        mock_os_remove.assert_not_called()
        mock_direct_answer.assert_not_called()

    async def test_export_excel_router_empty_history_uses_default_months_and_warns(self):
        packed_target_id = 92
        event = self._make_callback_event(
            callback_id="cb_empty_export_excel",
            user_id=91,
            chat_id=91,
            message_id=3003,
            target_user_id=packed_target_id,
        )
        runtime_user = SimpleNamespace(id=91)
        mock_bot = mock.AsyncMock()
        target_user = SimpleNamespace(id=packed_target_id, account_name="counterparty_user")

        progress_text = "⏳ در حال ایجاد فایل Excel..."
        expected_empty_text = "⚠️ معامله" + chr(0x200C) + "ای برای دانلود وجود ندارد."

        order_tracker = mock.Mock()
        order_recorder: list[str] = []

        async def _record_answer_callback_query(*args, **kwargs):
            order_tracker.answer_callback_query_via_runtime(*args, **kwargs)
            order_recorder.append("answer_callback_query_via_runtime")

        async def _record_get_data(*args, **kwargs):
            order_tracker.get_data(*args, **kwargs)
            order_recorder.append("state.get_data")
            return {}

        async def _record_ensure_access(*args, **kwargs):
            order_tracker._ensure_history_profile_access(*args, **kwargs)
            order_recorder.append("_ensure_history_profile_access")
            return True

        async def _record_get_trade_history(*args, **kwargs):
            order_tracker.get_trade_history(*args, **kwargs)
            order_recorder.append("get_trade_history")
            return target_user, []

        async def _record_answer_callback_message(*args, **kwargs):
            order_tracker.answer_callback_message_via_runtime(*args, **kwargs)
            order_recorder.append("answer_callback_message_via_runtime")

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(side_effect=_record_get_data),
        )

        with (
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_answer_callback_query,
            ) as mock_answer_callback_query,
            mock.patch(
                "bot.handlers.trade_history._ensure_history_profile_access",
                new_callable=mock.AsyncMock,
                side_effect=_record_ensure_access,
            ) as mock_ensure_access,
            mock.patch(
                "bot.handlers.trade_history.get_trade_history",
                new_callable=mock.AsyncMock,
                side_effect=_record_get_trade_history,
            ) as mock_get_trade_history,
            mock.patch(
                "bot.handlers.trade_history.generate_excel",
                new_callable=mock.AsyncMock,
            ) as mock_generate_excel,
            mock.patch(
                "bot.handlers.trade_history.send_private_document_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_send_private_document,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
                side_effect=_record_answer_callback_message,
            ) as mock_answer_callback_message,
            mock.patch(
                "bot.handlers.trade_history.os.remove",
            ) as mock_os_remove,
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
                bot=mock_bot,
                raw_state=None,
            )

        self.assertIsNone(result)

        self.assertEqual(
            order_recorder,
            [
                "answer_callback_query_via_runtime",
                "state.get_data",
                "_ensure_history_profile_access",
                "get_trade_history",
                "answer_callback_message_via_runtime",
            ],
        )

        order_tracker.assert_has_calls(
            [
                mock.call.answer_callback_query_via_runtime(
                    event,
                    progress_text,
                ),
                mock.call.get_data(),
                mock.call._ensure_history_profile_access(
                    user=runtime_user,
                    target_user_id=packed_target_id,
                    callback=event,
                ),
                mock.call.get_trade_history(
                    runtime_user.id,
                    packed_target_id,
                    months=3,
                ),
                mock.call.answer_callback_message_via_runtime(
                    event,
                    runtime_user,
                    expected_empty_text,
                    source_key="history-excel-empty",
                    action=TelegramDeliveryAction.TRADE_NONCRITICAL,
                ),
            ],
            any_order=False,
        )

        mock_answer_callback_query.assert_awaited_once_with(
            event,
            progress_text,
        )
        self.assertIs(mock_answer_callback_query.await_args.args[0], event)
        self.assertEqual(mock_answer_callback_query.await_args.args[1], progress_text)
        self.assertEqual(mock_answer_callback_query.await_args.kwargs, {})

        mock_state.get_data.assert_awaited_once_with()
        self.assertEqual(mock_state.get_data.await_count, 1)
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()

        mock_ensure_access.assert_awaited_once_with(
            user=runtime_user,
            target_user_id=packed_target_id,
            callback=event,
        )
        self.assertIs(mock_ensure_access.await_args.kwargs["user"], runtime_user)
        self.assertEqual(mock_ensure_access.await_args.kwargs["target_user_id"], packed_target_id)
        self.assertIs(mock_ensure_access.await_args.kwargs["callback"], event)
        self.assertEqual(mock_ensure_access.await_args.args, ())

        mock_get_trade_history.assert_awaited_once_with(
            runtime_user.id,
            packed_target_id,
            months=3,
        )
        self.assertEqual(
            mock_get_trade_history.await_args.args,
            (runtime_user.id, packed_target_id),
        )
        self.assertEqual(mock_get_trade_history.await_args.kwargs, {"months": 3})

        mock_answer_callback_message.assert_awaited_once_with(
            event,
            runtime_user,
            expected_empty_text,
            source_key="history-excel-empty",
            action=TelegramDeliveryAction.TRADE_NONCRITICAL,
        )
        self.assertIs(mock_answer_callback_message.await_args.args[0], event)
        self.assertIs(mock_answer_callback_message.await_args.args[1], runtime_user)
        self.assertEqual(
            mock_answer_callback_message.await_args.args[2],
            expected_empty_text,
        )
        self.assertEqual(
            mock_answer_callback_message.await_args.kwargs,
            {
                "source_key": "history-excel-empty",
                "action": TelegramDeliveryAction.TRADE_NONCRITICAL,
            },
        )

        mock_generate_excel.assert_not_called()
        mock_send_private_document.assert_not_called()
        mock_os_remove.assert_not_called()
        mock_direct_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
