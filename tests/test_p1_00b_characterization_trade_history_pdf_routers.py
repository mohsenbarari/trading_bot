"""Characterization tests for trade history PDF export callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seeds:
- BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-EXPORT-PDF-L440
- BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-EXPORT-PROFILE-TRADE-PDF-L498

Exercises registered callback_query router dispatch for seed L440 (export_pdf with
ExportHistoryCallback(format="pdf", target_user_id=...)) and seed L498
(export_profile_trade_pdf with ProfileTradePdfCallback(target_user_id=...)).

Freezes three scenarios:
1. Ordinary PDF unauthenticated suppression: user=None suppresses all progress answering,
   state access, access checks, history query, generation, delivery, and cleanup.
2. Ordinary PDF authenticated delivery using FSM history_months=6, freezing exact call
   order, arguments, FSInputFile/filename construction, source_key, Persian caption,
   temp-file cleanup, no state mutation, and no callback-message fallback.
3. Profile PDF authenticated delivery with fixed 3-month window, freezing exact call
   order, arguments, FSInputFile/filename construction, source_key, Persian caption,
   temp-file cleanup, untouched FSM state, and no callback-message fallback.

Excludes PDF rendering internals, get_trade_history and profile-access internals,
empty/error branches already exercised by direct tests, Excel behavior,
product/source changes, docs/index reconciliation, seed/blocker closure,
origin-matrix closure, and Stage closure.

Explicit statement: this partial unit closes no seed, blocker, origin cell,
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

from bot.callbacks import ExportHistoryCallback, ProfileTradePdfCallback
from bot.handlers import trade_history


class TestTradeHistoryPdfRoutersCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    def _make_callback_event(
        self,
        *,
        callback_id: str,
        user_id: int,
        chat_id: int,
        message_id: int,
        packed_data: str,
    ) -> aiogram_types.CallbackQuery:
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

    def _make_state_mock(self, *, get_data_return=None):
        return SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(return_value=get_data_return or {}),
        )

    async def test_export_pdf_router_unauthenticated_suppresses_all_work(self):
        packed_data = ExportHistoryCallback(format="pdf", target_user_id=2002).pack()
        event = self._make_callback_event(
            callback_id="cb_unauth_pdf",
            user_id=2001,
            chat_id=2001,
            message_id=4001,
            packed_data=packed_data,
        )
        mock_bot = mock.AsyncMock()
        mock_state = self._make_state_mock()

        with (
            mock.patch("bot.handlers.trade_history.answer_callback_query_via_runtime", new_callable=mock.AsyncMock) as mock_answer_cb,
            mock.patch("bot.handlers.trade_history._ensure_history_profile_access", new_callable=mock.AsyncMock) as mock_access,
            mock.patch("bot.handlers.trade_history.get_trade_history", new_callable=mock.AsyncMock) as mock_get_history,
            mock.patch("bot.handlers.trade_history.generate_pdf", new_callable=mock.AsyncMock) as mock_gen_pdf,
            mock.patch("bot.handlers.trade_history.user_display_name") as mock_display_name,
            mock.patch("bot.handlers.trade_history._history_download_filename") as mock_down_fn,
            mock.patch("bot.handlers.trade_history.FSInputFile") as mock_fs_file,
            mock.patch("bot.handlers.trade_history.send_private_document_via_runtime", new_callable=mock.AsyncMock) as mock_send_doc,
            mock.patch("bot.handlers.trade_history.answer_callback_message_via_runtime", new_callable=mock.AsyncMock) as mock_answer_msg,
            mock.patch("bot.handlers.trade_history.os.remove") as mock_os_remove,
            mock.patch.object(aiogram_types.CallbackQuery, "answer", new_callable=mock.AsyncMock) as mock_direct_answer,
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
        mock_answer_cb.assert_not_called()
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()
        mock_access.assert_not_called()
        mock_get_history.assert_not_called()
        mock_gen_pdf.assert_not_called()
        mock_display_name.assert_not_called()
        mock_down_fn.assert_not_called()
        mock_fs_file.assert_not_called()
        mock_send_doc.assert_not_called()
        mock_answer_msg.assert_not_called()
        mock_os_remove.assert_not_called()
        mock_direct_answer.assert_not_called()

    async def test_export_pdf_router_success_uses_state_months_and_cleans_up(self):
        packed_target_id = 2005
        runtime_user_id = 2004
        packed_data = ExportHistoryCallback(format="pdf", target_user_id=packed_target_id).pack()
        event = self._make_callback_event(
            callback_id="cb_auth_pdf_success",
            user_id=runtime_user_id,
            chat_id=runtime_user_id,
            message_id=4002,
            packed_data=packed_data,
        )
        runtime_user = SimpleNamespace(id=runtime_user_id)
        mock_bot = mock.AsyncMock()

        target_user = SimpleNamespace(id=packed_target_id, account_name="pdf_target")
        trades = [SimpleNamespace(id=101), SimpleNamespace(id=102)]
        generated_path = "/tmp/test_trade_history.pdf"
        resolved_display = "همکار نمونه"
        export_fn = "trade_history_همکار_نمونه_20260915120000.pdf"
        sentinel_input_file = SimpleNamespace(path=generated_path, filename=export_fn)
        progress_text = "⏳ در حال ایجاد فایل PDF..."
        expected_caption = f"📊 تاریخچه معاملات {resolved_display}\n📅 6 ماه اخیر"

        order: list[str] = []

        async def _record_answer_cb(*a, **kw):
            order.append("answer_cb")

        async def _record_get_data(*a, **kw):
            order.append("get_data")
            return {"history_months": 6}

        async def _record_access(*a, **kw):
            order.append("access")
            return True

        async def _record_get_history(*a, **kw):
            order.append("get_history")
            return target_user, trades

        async def _record_gen_pdf(*a, **kw):
            order.append("gen_pdf")
            return generated_path

        def _record_display_name(*a, **kw):
            order.append("display_name")
            return resolved_display

        def _record_down_fn(*a, **kw):
            order.append("down_fn")
            return export_fn

        def _record_fs_file(*a, **kw):
            order.append("fs_file")
            return sentinel_input_file

        async def _record_send_doc(*a, **kw):
            order.append("send_doc")

        def _record_os_remove(*a, **kw):
            order.append("os_remove")

        mock_state = self._make_state_mock()
        mock_state.get_data.side_effect = _record_get_data

        with (
            mock.patch("bot.handlers.trade_history.answer_callback_query_via_runtime", new_callable=mock.AsyncMock, side_effect=_record_answer_cb) as mock_answer_cb,
            mock.patch("bot.handlers.trade_history._ensure_history_profile_access", new_callable=mock.AsyncMock, side_effect=_record_access) as mock_access,
            mock.patch("bot.handlers.trade_history.get_trade_history", new_callable=mock.AsyncMock, side_effect=_record_get_history) as mock_get_history,
            mock.patch("bot.handlers.trade_history.generate_pdf", new_callable=mock.AsyncMock, side_effect=_record_gen_pdf) as mock_gen_pdf,
            mock.patch("bot.handlers.trade_history.user_display_name", side_effect=_record_display_name) as mock_display_name,
            mock.patch("bot.handlers.trade_history._history_download_filename", side_effect=_record_down_fn) as mock_down_fn,
            mock.patch("bot.handlers.trade_history.FSInputFile", side_effect=_record_fs_file) as mock_fs_file,
            mock.patch("bot.handlers.trade_history.send_private_document_via_runtime", new_callable=mock.AsyncMock, side_effect=_record_send_doc) as mock_send_doc,
            mock.patch("bot.handlers.trade_history.answer_callback_message_via_runtime", new_callable=mock.AsyncMock) as mock_answer_msg,
            mock.patch("bot.handlers.trade_history.os.remove", side_effect=_record_os_remove) as mock_os_remove,
            mock.patch.object(aiogram_types.CallbackQuery, "answer", new_callable=mock.AsyncMock) as mock_direct_answer,
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
            order,
            [
                "answer_cb",
                "get_data",
                "access",
                "get_history",
                "gen_pdf",
                "display_name",
                "down_fn",
                "fs_file",
                "send_doc",
                "os_remove",
            ],
        )

        mock_answer_cb.assert_awaited_once_with(event, progress_text)
        self.assertIs(mock_answer_cb.await_args.args[0], event)
        self.assertEqual(mock_answer_cb.await_args.args[1], progress_text)

        mock_state.get_data.assert_awaited_once_with()
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()

        mock_access.assert_awaited_once_with(user=runtime_user, target_user_id=packed_target_id, callback=event)
        self.assertIs(mock_access.await_args.kwargs["user"], runtime_user)
        self.assertEqual(mock_access.await_args.kwargs["target_user_id"], packed_target_id)
        self.assertIs(mock_access.await_args.kwargs["callback"], event)

        mock_get_history.assert_awaited_once_with(runtime_user.id, packed_target_id, months=6)
        mock_gen_pdf.assert_awaited_once_with(trades, target_user, runtime_user, months=6)
        mock_display_name.assert_called_once_with(target_user, "پروفایل من")
        mock_down_fn.assert_called_once_with(resolved_display, "pdf")
        mock_fs_file.assert_called_once_with(generated_path, filename=export_fn)

        mock_send_doc.assert_awaited_once_with(
            event,
            runtime_user,
            sentinel_input_file,
            filename=export_fn,
            bot=mock_bot,
            source_key="history-pdf-document",
            caption=expected_caption,
        )
        self.assertIs(mock_send_doc.await_args.args[0], event)
        self.assertIs(mock_send_doc.await_args.args[1], runtime_user)
        self.assertIs(mock_send_doc.await_args.args[2], sentinel_input_file)
        self.assertEqual(
            mock_send_doc.await_args.kwargs,
            {
                "filename": export_fn,
                "bot": mock_bot,
                "source_key": "history-pdf-document",
                "caption": expected_caption,
            },
        )

        mock_os_remove.assert_called_once_with(generated_path)
        mock_answer_msg.assert_not_called()
        mock_direct_answer.assert_not_called()

    async def test_export_profile_trade_pdf_router_success_forces_three_months_and_cleans_up(self):
        packed_target_id = 3008
        runtime_user_id = 3007
        packed_data = ProfileTradePdfCallback(target_user_id=packed_target_id).pack()
        event = self._make_callback_event(
            callback_id="cb_auth_profile_pdf_success",
            user_id=runtime_user_id,
            chat_id=runtime_user_id,
            message_id=4003,
            packed_data=packed_data,
        )
        runtime_user = SimpleNamespace(id=runtime_user_id)
        mock_bot = mock.AsyncMock()

        target_user = SimpleNamespace(id=packed_target_id, account_name="profile_target")
        trades = [SimpleNamespace(id=201), SimpleNamespace(id=202)]
        generated_path = "/tmp/test_profile_history.pdf"
        resolved_display = "کاربر هدف"
        export_fn = "trade_history_کاربر_هدف_20260915120000.pdf"
        sentinel_input_file = SimpleNamespace(path=generated_path, filename=export_fn)
        progress_text = "⏳ در حال ایجاد فایل PDF..."
        expected_caption = f"📊 تاریخچه معاملات {resolved_display}\n📅 ۳ ماه اخیر"

        order: list[str] = []

        async def _record_answer_cb(*a, **kw):
            order.append("answer_cb")

        async def _record_access(*a, **kw):
            order.append("access")
            return True

        async def _record_get_history(*a, **kw):
            order.append("get_history")
            return target_user, trades

        async def _record_gen_pdf(*a, **kw):
            order.append("gen_pdf")
            return generated_path

        def _record_display_name(*a, **kw):
            order.append("display_name")
            return resolved_display

        def _record_down_fn(*a, **kw):
            order.append("down_fn")
            return export_fn

        def _record_fs_file(*a, **kw):
            order.append("fs_file")
            return sentinel_input_file

        async def _record_send_doc(*a, **kw):
            order.append("send_doc")

        def _record_os_remove(*a, **kw):
            order.append("os_remove")

        mock_state = self._make_state_mock()

        with (
            mock.patch("bot.handlers.trade_history.answer_callback_query_via_runtime", new_callable=mock.AsyncMock, side_effect=_record_answer_cb) as mock_answer_cb,
            mock.patch("bot.handlers.trade_history._ensure_history_profile_access", new_callable=mock.AsyncMock, side_effect=_record_access) as mock_access,
            mock.patch("bot.handlers.trade_history.get_trade_history", new_callable=mock.AsyncMock, side_effect=_record_get_history) as mock_get_history,
            mock.patch("bot.handlers.trade_history.generate_pdf", new_callable=mock.AsyncMock, side_effect=_record_gen_pdf) as mock_gen_pdf,
            mock.patch("bot.handlers.trade_history.user_display_name", side_effect=_record_display_name) as mock_display_name,
            mock.patch("bot.handlers.trade_history._history_download_filename", side_effect=_record_down_fn) as mock_down_fn,
            mock.patch("bot.handlers.trade_history.FSInputFile", side_effect=_record_fs_file) as mock_fs_file,
            mock.patch("bot.handlers.trade_history.send_private_document_via_runtime", new_callable=mock.AsyncMock, side_effect=_record_send_doc) as mock_send_doc,
            mock.patch("bot.handlers.trade_history.answer_callback_message_via_runtime", new_callable=mock.AsyncMock) as mock_answer_msg,
            mock.patch("bot.handlers.trade_history.os.remove", side_effect=_record_os_remove) as mock_os_remove,
            mock.patch.object(aiogram_types.CallbackQuery, "answer", new_callable=mock.AsyncMock) as mock_direct_answer,
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
            order,
            [
                "answer_cb",
                "access",
                "get_history",
                "gen_pdf",
                "display_name",
                "down_fn",
                "fs_file",
                "send_doc",
                "os_remove",
            ],
        )

        mock_answer_cb.assert_awaited_once_with(event, progress_text)
        self.assertIs(mock_answer_cb.await_args.args[0], event)
        self.assertEqual(mock_answer_cb.await_args.args[1], progress_text)

        mock_state.get_data.assert_not_called()
        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()

        mock_access.assert_awaited_once_with(user=runtime_user, target_user_id=packed_target_id, callback=event)
        self.assertIs(mock_access.await_args.kwargs["user"], runtime_user)
        self.assertEqual(mock_access.await_args.kwargs["target_user_id"], packed_target_id)
        self.assertIs(mock_access.await_args.kwargs["callback"], event)

        mock_get_history.assert_awaited_once_with(runtime_user.id, packed_target_id, months=3)
        mock_gen_pdf.assert_awaited_once_with(trades, target_user, runtime_user, months=3)
        mock_display_name.assert_called_once_with(target_user, "پروفایل من")
        mock_down_fn.assert_called_once_with(resolved_display, "pdf")
        mock_fs_file.assert_called_once_with(generated_path, filename=export_fn)

        mock_send_doc.assert_awaited_once_with(
            event,
            runtime_user,
            sentinel_input_file,
            filename=export_fn,
            bot=mock_bot,
            source_key="history-profile-pdf-document",
            caption=expected_caption,
        )
        self.assertIs(mock_send_doc.await_args.args[0], event)
        self.assertIs(mock_send_doc.await_args.args[1], runtime_user)
        self.assertIs(mock_send_doc.await_args.args[2], sentinel_input_file)
        self.assertEqual(
            mock_send_doc.await_args.kwargs,
            {
                "filename": export_fn,
                "bot": mock_bot,
                "source_key": "history-profile-pdf-document",
                "caption": expected_caption,
            },
        )

        mock_os_remove.assert_called_once_with(generated_path)
        mock_answer_msg.assert_not_called()
        mock_direct_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
