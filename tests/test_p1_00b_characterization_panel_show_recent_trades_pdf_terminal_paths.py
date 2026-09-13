"""Direct-characterization tests for bot.handlers.panel.show_recent_trades_pdf terminal paths.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: BOT-TG-MESSAGE-PANEL-SHOW-RECENT-TRADES-PDF-L534
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This characterization does not update the registry, reduce blockers, close the seed,
change product behavior, or close P1-00b.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import show_recent_trades_pdf  # noqa: E402


class CharacterizePanelShowRecentTradesPdfTerminalPathsTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing terminal paths of show_recent_trades_pdf."""

    async def test_show_recent_trades_pdf_no_user_early_returns(self) -> None:
        message = SimpleNamespace(
            message_id=101,
            chat=SimpleNamespace(id=5001),
            answer=AsyncMock(),
            answer_document=AsyncMock(),
        )
        state = Mock()

        with (
            patch("bot.handlers.panel.datetime") as mock_datetime,
            patch(
                "bot.handlers.panel._load_recent_user_trades",
                new_callable=AsyncMock,
            ) as mock_load_recent_user_trades,
            patch(
                "bot.handlers.panel.build_trade_history_date_range_label",
            ) as mock_build_date_range_label,
            patch(
                "bot.handlers.panel.build_trade_history_export_rows",
            ) as mock_build_export_rows,
            patch(
                "bot.handlers.panel.generate_trade_history_pdf_file",
            ) as mock_generate_pdf_file,
            patch(
                "bot.handlers.panel._history_download_filename",
            ) as mock_history_download_filename,
            patch(
                "bot.handlers.panel.FSInputFile",
            ) as mock_fs_input_file,
            patch(
                "bot.handlers.panel.user_display_name",
            ) as mock_user_display_name,
            patch(
                "bot.handlers.panel.send_private_document_via_runtime",
                new_callable=AsyncMock,
            ) as mock_send_private_document_via_runtime,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer_incoming_message_via_runtime,
            patch("bot.handlers.panel.os.path.exists") as mock_path_exists,
            patch("bot.handlers.panel.os.remove") as mock_os_remove,
        ):
            result = await show_recent_trades_pdf(message, state, None)

        self.assertIsNone(result)
        mock_datetime.now.assert_not_called()
        mock_datetime.assert_not_called()
        mock_load_recent_user_trades.assert_not_called()
        mock_load_recent_user_trades.assert_not_awaited()
        mock_build_date_range_label.assert_not_called()
        mock_build_export_rows.assert_not_called()
        mock_generate_pdf_file.assert_not_called()
        mock_history_download_filename.assert_not_called()
        mock_fs_input_file.assert_not_called()
        mock_user_display_name.assert_not_called()
        mock_send_private_document_via_runtime.assert_not_called()
        mock_send_private_document_via_runtime.assert_not_awaited()
        mock_answer_incoming_message_via_runtime.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()
        mock_path_exists.assert_not_called()
        mock_os_remove.assert_not_called()
        message.answer.assert_not_called()
        message.answer.assert_not_awaited()
        message.answer_document.assert_not_called()
        message.answer_document.assert_not_awaited()
        self.assertEqual(state.mock_calls, [])

    async def test_show_recent_trades_pdf_empty_history_characterization(
        self,
    ) -> None:
        message = SimpleNamespace(
            message_id=202,
            chat=SimpleNamespace(id=5002),
            answer=AsyncMock(),
            answer_document=AsyncMock(),
        )
        state = Mock()
        user = SimpleNamespace(
            id=102,
            account_name="empty_history_user",
        )

        frozen_now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
        expected_from_date = date(2026, 9, 7)
        expected_to_date = date(2026, 9, 13)

        mock_datetime = Mock()
        mock_datetime.now = Mock(return_value=frozen_now)
        mock_load_recent_user_trades = AsyncMock(return_value=[])

        with (
            patch("bot.handlers.panel.datetime", mock_datetime),
            patch(
                "bot.handlers.panel._load_recent_user_trades",
                mock_load_recent_user_trades,
            ),
            patch(
                "bot.handlers.panel.build_trade_history_date_range_label",
            ) as mock_build_date_range_label,
            patch(
                "bot.handlers.panel.build_trade_history_export_rows",
            ) as mock_build_export_rows,
            patch(
                "bot.handlers.panel.generate_trade_history_pdf_file",
            ) as mock_generate_pdf_file,
            patch(
                "bot.handlers.panel._history_download_filename",
            ) as mock_history_download_filename,
            patch(
                "bot.handlers.panel.FSInputFile",
            ) as mock_fs_input_file,
            patch(
                "bot.handlers.panel.user_display_name",
            ) as mock_user_display_name,
            patch(
                "bot.handlers.panel.send_private_document_via_runtime",
                new_callable=AsyncMock,
            ) as mock_send_private_document_via_runtime,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer_incoming_message_via_runtime,
            patch("bot.handlers.panel.os.path.exists") as mock_path_exists,
            patch("bot.handlers.panel.os.remove") as mock_os_remove,
        ):
            result = await show_recent_trades_pdf(message, state, user)

        self.assertIsNone(result)

        mock_datetime.now.assert_called_once_with(timezone.utc)

        mock_load_recent_user_trades.assert_awaited_once_with(
            user.id,
            from_date=expected_from_date,
            to_date=expected_to_date,
        )
        self.assertEqual(mock_load_recent_user_trades.call_count, 1)
        self.assertEqual(mock_load_recent_user_trades.await_count, 1)
        loader_args, loader_kwargs = mock_load_recent_user_trades.await_args
        self.assertEqual(len(loader_args), 1)
        self.assertEqual(loader_args[0], user.id)
        self.assertEqual(
            loader_kwargs,
            {
                "from_date": expected_from_date,
                "to_date": expected_to_date,
            },
        )

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            "⚠️ در هفت روز گذشته معامله\u200cای برای دانلود وجود ندارد.",
            source_key="panel-recent-trades-empty",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_count, 1
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.await_count, 1
        )
        answer_args, answer_kwargs = (
            mock_answer_incoming_message_via_runtime.await_args
        )
        self.assertEqual(len(answer_args), 3)
        self.assertIs(answer_args[0], message)
        self.assertIs(answer_args[1], user)
        self.assertEqual(
            answer_args[2],
            "⚠️ در هفت روز گذشته معامله\u200cای برای دانلود وجود ندارد.",
        )
        self.assertEqual(
            answer_kwargs,
            {"source_key": "panel-recent-trades-empty"},
        )

        mock_build_date_range_label.assert_not_called()
        mock_build_export_rows.assert_not_called()
        mock_generate_pdf_file.assert_not_called()
        mock_history_download_filename.assert_not_called()
        mock_fs_input_file.assert_not_called()
        mock_user_display_name.assert_not_called()
        mock_send_private_document_via_runtime.assert_not_called()
        mock_send_private_document_via_runtime.assert_not_awaited()
        mock_path_exists.assert_not_called()
        mock_os_remove.assert_not_called()
        message.answer.assert_not_called()
        message.answer.assert_not_awaited()
        message.answer_document.assert_not_called()
        message.answer_document.assert_not_awaited()
        self.assertEqual(state.mock_calls, [])

    async def test_show_recent_trades_pdf_document_send_error_cleanup_characterization(
        self,
    ) -> None:
        message = SimpleNamespace(
            message_id=303,
            chat=SimpleNamespace(id=5003),
            answer=AsyncMock(),
            answer_document=AsyncMock(),
        )
        state = Mock()
        user = SimpleNamespace(
            id=103,
            account_name="recent_user",
        )

        frozen_now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
        expected_from_date = date(2026, 9, 7)
        expected_to_date = date(2026, 9, 13)

        trade_sentinel = SimpleNamespace(
            trade_id=9901, description="opaque_trade_sentinel"
        )
        mock_load_recent_user_trades = AsyncMock(return_value=[trade_sentinel])

        mock_datetime = Mock()
        mock_datetime.now = Mock(return_value=frozen_now)

        date_range_label_sentinel = "DATE_RANGE_LABEL_SENTINEL_20260907_20260913"
        export_rows_sentinel = [SimpleNamespace(row_id="ROW_SENTINEL_1")]
        pdf_path = "/tmp/p1-00b-recent.pdf"
        export_filename = "trade_history_recent_user_fixed.pdf"
        fs_input_file_sentinel = SimpleNamespace(
            path=pdf_path,
            filename=export_filename,
        )

        mock_build_date_range_label = Mock(
            return_value=date_range_label_sentinel
        )
        mock_build_export_rows = Mock(return_value=export_rows_sentinel)
        mock_generate_pdf_file = Mock(return_value=pdf_path)
        mock_history_download_filename = Mock(return_value=export_filename)
        mock_fs_input_file = Mock(return_value=fs_input_file_sentinel)
        mock_user_display_name = Mock()

        send_error = RuntimeError("document lane unavailable")
        mock_send_private_document_via_runtime = AsyncMock(
            side_effect=send_error
        )
        mock_answer_incoming_message_via_runtime = AsyncMock()

        mock_path_exists = Mock(return_value=True)
        mock_os_remove = Mock()

        with (
            patch("bot.handlers.panel.datetime", mock_datetime),
            patch(
                "bot.handlers.panel._load_recent_user_trades",
                mock_load_recent_user_trades,
            ),
            patch(
                "bot.handlers.panel.build_trade_history_date_range_label",
                mock_build_date_range_label,
            ),
            patch(
                "bot.handlers.panel.build_trade_history_export_rows",
                mock_build_export_rows,
            ),
            patch(
                "bot.handlers.panel.generate_trade_history_pdf_file",
                mock_generate_pdf_file,
            ),
            patch(
                "bot.handlers.panel._history_download_filename",
                mock_history_download_filename,
            ),
            patch(
                "bot.handlers.panel.FSInputFile",
                mock_fs_input_file,
            ),
            patch(
                "bot.handlers.panel.user_display_name",
                mock_user_display_name,
            ),
            patch(
                "bot.handlers.panel.send_private_document_via_runtime",
                mock_send_private_document_via_runtime,
            ),
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                mock_answer_incoming_message_via_runtime,
            ),
            patch("bot.handlers.panel.os.path.exists", mock_path_exists),
            patch("bot.handlers.panel.os.remove", mock_os_remove),
        ):
            result = await show_recent_trades_pdf(message, state, user)

        self.assertIsNone(result)

        mock_datetime.now.assert_called_once_with(timezone.utc)

        mock_load_recent_user_trades.assert_awaited_once_with(
            user.id,
            from_date=expected_from_date,
            to_date=expected_to_date,
        )
        self.assertEqual(mock_load_recent_user_trades.call_count, 1)
        self.assertEqual(mock_load_recent_user_trades.await_count, 1)
        loader_args, loader_kwargs = mock_load_recent_user_trades.await_args
        self.assertEqual(len(loader_args), 1)
        self.assertEqual(loader_args[0], user.id)
        self.assertEqual(
            loader_kwargs,
            {
                "from_date": expected_from_date,
                "to_date": expected_to_date,
            },
        )

        mock_user_display_name.assert_not_called()

        mock_build_date_range_label.assert_called_once_with(
            expected_from_date,
            expected_to_date,
        )
        date_label_args, date_label_kwargs = (
            mock_build_date_range_label.call_args
        )
        self.assertEqual(
            date_label_args,
            (expected_from_date, expected_to_date),
        )
        self.assertEqual(date_label_kwargs, {})

        mock_build_export_rows.assert_called_once_with(
            [trade_sentinel],
            user.id,
        )
        rows_args, rows_kwargs = mock_build_export_rows.call_args
        self.assertEqual(len(rows_args), 2)
        self.assertEqual(rows_args[0], [trade_sentinel])
        self.assertEqual(rows_args[1], user.id)
        self.assertEqual(rows_kwargs, {})

        mock_generate_pdf_file.assert_called_once_with(
            subject_name="recent_user",
            date_range_label=date_range_label_sentinel,
            rows=export_rows_sentinel,
            include_counterparty=False,
            include_offer_notes=True,
        )
        pdf_args, pdf_kwargs = mock_generate_pdf_file.call_args
        self.assertEqual(pdf_args, ())
        self.assertEqual(
            pdf_kwargs,
            {
                "subject_name": "recent_user",
                "date_range_label": date_range_label_sentinel,
                "rows": export_rows_sentinel,
                "include_counterparty": False,
                "include_offer_notes": True,
            },
        )

        mock_history_download_filename.assert_called_once_with(
            "recent_user",
            "pdf",
        )
        filename_args, filename_kwargs = (
            mock_history_download_filename.call_args
        )
        self.assertEqual(filename_args, ("recent_user", "pdf"))
        self.assertEqual(filename_kwargs, {})

        mock_fs_input_file.assert_called_once_with(
            pdf_path,
            filename=export_filename,
        )
        fs_args, fs_kwargs = mock_fs_input_file.call_args
        self.assertEqual(fs_args, (pdf_path,))
        self.assertEqual(fs_kwargs, {"filename": export_filename})

        mock_send_private_document_via_runtime.assert_awaited_once_with(
            message,
            user,
            fs_input_file_sentinel,
            filename=export_filename,
            caption="📄 معاملات اخیر شما در هفت روز گذشته",
            source_key="panel-recent-trades-pdf",
        )
        self.assertEqual(
            mock_send_private_document_via_runtime.call_count, 1
        )
        self.assertEqual(
            mock_send_private_document_via_runtime.await_count, 1
        )
        send_args, send_kwargs = (
            mock_send_private_document_via_runtime.await_args
        )
        self.assertEqual(len(send_args), 3)
        self.assertIs(send_args[0], message)
        self.assertIs(send_args[1], user)
        self.assertIs(send_args[2], fs_input_file_sentinel)
        self.assertEqual(
            send_kwargs,
            {
                "filename": export_filename,
                "caption": "📄 معاملات اخیر شما در هفت روز گذشته",
                "source_key": "panel-recent-trades-pdf",
            },
        )

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            "❌ خطا در ایجاد فایل معاملات اخیر: document lane unavailable",
            source_key="panel-recent-trades-export-error",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_count, 1
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.await_count, 1
        )
        answer_args, answer_kwargs = (
            mock_answer_incoming_message_via_runtime.await_args
        )
        self.assertEqual(len(answer_args), 3)
        self.assertIs(answer_args[0], message)
        self.assertIs(answer_args[1], user)
        self.assertEqual(
            answer_args[2],
            "❌ خطا در ایجاد فایل معاملات اخیر: document lane unavailable",
        )
        self.assertEqual(
            answer_kwargs,
            {"source_key": "panel-recent-trades-export-error"},
        )

        mock_path_exists.assert_called_once_with(pdf_path)
        mock_os_remove.assert_called_once_with(pdf_path)

        message.answer.assert_not_called()
        message.answer.assert_not_awaited()
        message.answer_document.assert_not_called()
        message.answer_document.assert_not_awaited()
        self.assertEqual(state.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
