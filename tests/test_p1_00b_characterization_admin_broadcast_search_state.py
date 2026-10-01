"""Stage P1-00b characterization tests for process_selected_recipient_search state handling.

Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-064
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Characterizes the linked SUPER_ADMIN process_selected_recipient_search handler:
- Matched recipients with selected marker formatting, state update, and delivery contract.
- Empty search results with no-match notice, selected count, and navigation keyboard.
- Malformed selected_user_ids raising ValueError prior to session creation, search, or delivery.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from aiogram.types import InlineKeyboardMarkup

from bot.handlers.admin_broadcast import process_selected_recipient_search
from core.enums import UserRole
from core.services.telegram_admin_broadcast_service import TelegramAdminBroadcastRecipient


class AdminBroadcastSearchStateCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _create_mock_session_factory(self) -> tuple[MagicMock, AsyncMock]:
        mock_session = AsyncMock()
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock(return_value=mock_cm)
        return mock_factory, mock_session

    async def test_matched_recipients_preserve_selected_marker_and_search_delivery(self):
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        mock_session_factory, mock_session = self._create_mock_session_factory()
        mock_search_recipients = AsyncMock()
        mock_answer_incoming = AsyncMock()

        user = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        message = SimpleNamespace(text="  ali  ")
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"selected_user_ids": [101]}),
            update_data=AsyncMock(),
        )

        recipient_selected = TelegramAdminBroadcastRecipient(
            user_id=101,
            telegram_id=9001,
            account_name="alice_acc",
            full_name="Alice Smith",
        )
        recipient_unselected = TelegramAdminBroadcastRecipient(
            user_id=102,
            telegram_id=9002,
            account_name="bob_acc",
            full_name="Bob Jones",
        )
        mock_search_recipients.return_value = (recipient_selected, recipient_unselected)

        with patch(
            "bot.handlers.admin_broadcast.AsyncSessionLocal",
            new=mock_session_factory,
        ), patch(
            "bot.handlers.admin_broadcast.search_telegram_admin_broadcast_recipients",
            new=mock_search_recipients,
        ), patch(
            "bot.handlers.admin_broadcast.answer_incoming_message_via_runtime",
            new=mock_answer_incoming,
        ):
            await process_selected_recipient_search(
                message=message,  # type: ignore[arg-type]
                state=state,      # type: ignore[arg-type]
                user=user,        # type: ignore[arg-type]
            )

        mock_session_factory.assert_called_once_with()
        mock_search_recipients.assert_awaited_once_with(mock_session, query="ali", limit=10)
        state.get_data.assert_awaited_once_with()
        state.update_data.assert_awaited_once_with(last_search_query="ali")

        mock_answer_incoming.assert_awaited_once()
        call_args = mock_answer_incoming.await_args.args
        call_kwargs = mock_answer_incoming.await_args.kwargs

        self.assertIs(call_args[0], message)
        self.assertIs(call_args[1], user)
        self.assertEqual(call_args[2], "نتایج جستجو برای «ali»\nانتخاب\u200cشده\u200cها: 1")
        self.assertEqual(call_kwargs.get("source_key"), "admin-broadcast-search-results")

        reply_markup = call_kwargs.get("reply_markup")
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        keyboard = reply_markup.inline_keyboard

        self.assertEqual(len(keyboard), 5)
        self.assertEqual(keyboard[0][0].text, "✅ alice_acc")
        self.assertEqual(keyboard[0][0].callback_data, "tgb:tu:101")
        self.assertEqual(keyboard[1][0].text, "◻️ bob_acc")
        self.assertEqual(keyboard[1][0].callback_data, "tgb:tu:102")
        self.assertEqual(keyboard[2][0].text, "جستجوی جدید")
        self.assertEqual(keyboard[2][0].callback_data, "tgb:search_again")
        self.assertEqual(keyboard[3][0].text, "ادامه و نوشتن پیام")
        self.assertEqual(keyboard[3][0].callback_data, "tgb:selected_done")
        self.assertEqual(keyboard[4][0].text, "لغو")
        self.assertEqual(keyboard[4][0].callback_data, "tgb:cancel")

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_empty_results_preserve_selected_count_and_no_match_delivery(self):
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        mock_session_factory, mock_session = self._create_mock_session_factory()
        mock_search_recipients = AsyncMock(return_value=())
        mock_answer_incoming = AsyncMock()

        user = SimpleNamespace(id=2, role=UserRole.SUPER_ADMIN)
        message = SimpleNamespace(text="unknown_person")
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"selected_user_ids": [201, 202]}),
            update_data=AsyncMock(),
        )

        with patch(
            "bot.handlers.admin_broadcast.AsyncSessionLocal",
            new=mock_session_factory,
        ), patch(
            "bot.handlers.admin_broadcast.search_telegram_admin_broadcast_recipients",
            new=mock_search_recipients,
        ), patch(
            "bot.handlers.admin_broadcast.answer_incoming_message_via_runtime",
            new=mock_answer_incoming,
        ):
            await process_selected_recipient_search(
                message=message,  # type: ignore[arg-type]
                state=state,      # type: ignore[arg-type]
                user=user,        # type: ignore[arg-type]
            )

        mock_session_factory.assert_called_once_with()
        mock_search_recipients.assert_awaited_once_with(mock_session, query="unknown_person", limit=10)
        state.get_data.assert_awaited_once_with()
        state.update_data.assert_awaited_once_with(last_search_query="unknown_person")

        mock_answer_incoming.assert_awaited_once()
        call_args = mock_answer_incoming.await_args.args
        call_kwargs = mock_answer_incoming.await_args.kwargs

        self.assertIs(call_args[0], message)
        self.assertIs(call_args[1], user)
        self.assertEqual(
            call_args[2],
            "برای «unknown_person» کاربر مجاز و متصل به بات پیدا نشد.\nانتخاب\u200cشده\u200cها: 2",
        )
        self.assertEqual(call_kwargs.get("source_key"), "admin-broadcast-search-results")

        reply_markup = call_kwargs.get("reply_markup")
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        keyboard = reply_markup.inline_keyboard

        self.assertEqual(len(keyboard), 3)
        self.assertEqual(keyboard[0][0].text, "جستجوی جدید")
        self.assertEqual(keyboard[0][0].callback_data, "tgb:search_again")
        self.assertEqual(keyboard[1][0].text, "ادامه و نوشتن پیام")
        self.assertEqual(keyboard[1][0].callback_data, "tgb:selected_done")
        self.assertEqual(keyboard[2][0].text, "لغو")
        self.assertEqual(keyboard[2][0].callback_data, "tgb:cancel")

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_malformed_selected_id_fails_before_session_or_delivery(self):
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        mock_session_factory, _ = self._create_mock_session_factory()
        mock_search_recipients = AsyncMock()
        mock_answer_incoming = AsyncMock()

        user = SimpleNamespace(id=3, role=UserRole.SUPER_ADMIN)
        message = SimpleNamespace(text="query_text")
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"selected_user_ids": ["malformed-id"]}),
            update_data=AsyncMock(),
        )

        with patch(
            "bot.handlers.admin_broadcast.AsyncSessionLocal",
            new=mock_session_factory,
        ), patch(
            "bot.handlers.admin_broadcast.search_telegram_admin_broadcast_recipients",
            new=mock_search_recipients,
        ), patch(
            "bot.handlers.admin_broadcast.answer_incoming_message_via_runtime",
            new=mock_answer_incoming,
        ):
            with self.assertRaises(ValueError):
                await process_selected_recipient_search(
                    message=message,  # type: ignore[arg-type]
                    state=state,      # type: ignore[arg-type]
                    user=user,        # type: ignore[arg-type]
                )

        state.get_data.assert_awaited_once_with()
        mock_session_factory.assert_not_called()
        mock_search_recipients.assert_not_called()
        mock_search_recipients.assert_not_awaited()
        state.update_data.assert_not_awaited()
        mock_answer_incoming.assert_not_called()
        mock_answer_incoming.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
