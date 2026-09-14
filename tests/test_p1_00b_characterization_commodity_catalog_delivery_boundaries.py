from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from bot.callbacks import CommodityCatalogPageCallback
from bot.handlers import commodity_catalog
from bot.message_manager import DeleteDelay
from core.enums import UserAccountStatus
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)


class CommodityCatalogDeliveryBoundariesCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _make_valid_user(self) -> SimpleNamespace:
        return SimpleNamespace(
            is_deleted=False,
            account_status=UserAccountStatus.ACTIVE,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )

    def _make_message(self, chat_id: int = 45) -> SimpleNamespace:
        return SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=chat_id),
        )

    async def test_show_commodity_catalog_queue_v1_suppresses_local_anchor(self):
        user = self._make_valid_user()
        message = self._make_message(chat_id=45)
        rendered_text = "📦 فهرست کالاهای آزمایشی"
        rendered_keyboard = SimpleNamespace(inline_keyboard=[])
        sent_anchor_message = SimpleNamespace(message_id=901)
        queue_v1_runtime = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )

        with patch(
            "bot.handlers.commodity_catalog.delete_previous_anchor",
            new=AsyncMock(),
        ) as delete_anchor, patch(
            "bot.handlers.commodity_catalog._render_catalog",
            new=AsyncMock(return_value=(rendered_text, rendered_keyboard)),
        ) as render_mock, patch(
            "bot.handlers.commodity_catalog.answer_incoming_message_via_runtime",
            new=AsyncMock(return_value=sent_anchor_message),
        ) as answer_mock, patch(
            "bot.handlers.commodity_catalog.configured_telegram_delivery_runtime",
            return_value=queue_v1_runtime,
        ), patch(
            "bot.handlers.commodity_catalog.set_anchor",
        ) as set_anchor:
            await commodity_catalog.show_commodity_catalog(message, user=user)

        delete_anchor.assert_awaited_once_with(
            message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value
        )
        render_mock.assert_awaited_once_with(1)
        answer_mock.assert_awaited_once_with(
            message, user, rendered_text, reply_markup=rendered_keyboard
        )
        set_anchor.assert_not_called()

    async def test_show_commodity_catalog_double_rejection_returns_without_anchor(self):
        user = self._make_valid_user()
        message = self._make_message(chat_id=45)
        rendered_text = "📦 فهرست کالاهای آزمایشی"
        rendered_keyboard = SimpleNamespace(inline_keyboard=[])
        first_error = TelegramBadRequest(
            method=SendMessage(chat_id=45, text=rendered_text),
            message="Primary send failed",
        )
        fallback_error = TelegramBadRequest(
            method=SendMessage(
                chat_id=45, text=commodity_catalog._CATALOG_SEND_ERROR_TEXT
            ),
            message="Fallback send failed",
        )

        with patch(
            "bot.handlers.commodity_catalog.delete_previous_anchor",
            new=AsyncMock(),
        ) as delete_anchor, patch(
            "bot.handlers.commodity_catalog._render_catalog",
            new=AsyncMock(return_value=(rendered_text, rendered_keyboard)),
        ) as render_mock, patch(
            "bot.handlers.commodity_catalog.answer_incoming_message_via_runtime",
            new=AsyncMock(side_effect=[first_error, fallback_error]),
        ) as answer_mock, patch(
            "bot.handlers.commodity_catalog.logger.warning",
        ) as warning_mock, patch(
            "bot.handlers.commodity_catalog.set_anchor",
        ) as set_anchor:
            await commodity_catalog.show_commodity_catalog(message, user=user)

        delete_anchor.assert_awaited_once_with(
            message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value
        )
        render_mock.assert_awaited_once_with(1)
        self.assertEqual(answer_mock.await_count, 2)
        self.assertEqual(
            answer_mock.await_args_list,
            [
                call(message, user, rendered_text, reply_markup=rendered_keyboard),
                call(message, user, commodity_catalog._CATALOG_SEND_ERROR_TEXT),
            ],
        )
        self.assertEqual(warning_mock.call_count, 2)
        self.assertEqual(
            warning_mock.call_args_list,
            [
                call("Failed to send commodity catalog message", exc_info=True),
                call("Failed to send commodity catalog fallback message", exc_info=True),
            ],
        )
        set_anchor.assert_not_called()

    async def test_paginate_commodity_catalog_rejected_edit_swallowed_and_callback_acknowledged(self):
        user = self._make_valid_user()
        callback = SimpleNamespace(
            id="cb_query_303",
            message=SimpleNamespace(
                chat=SimpleNamespace(id=45),
                message_id=88,
            ),
        )
        callback_data = CommodityCatalogPageCallback(page=3)
        rendered_page_text = "📦 فهرست کالاهای صفحه ۳"
        rendered_page_keyboard = SimpleNamespace(inline_keyboard=[])
        edit_error = TelegramBadRequest(
            method=SendMessage(chat_id=45, text=rendered_page_text),
            message="Edit message failed",
        )

        with patch(
            "bot.handlers.commodity_catalog._render_catalog",
            new=AsyncMock(return_value=(rendered_page_text, rendered_page_keyboard)),
        ) as render_mock, patch(
            "bot.handlers.commodity_catalog.edit_callback_message_via_runtime",
            new=AsyncMock(side_effect=edit_error),
        ) as edit_mock, patch(
            "bot.handlers.commodity_catalog.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as answer_callback_mock, patch(
            "bot.handlers.commodity_catalog.set_anchor",
        ) as set_anchor:
            await commodity_catalog.paginate_commodity_catalog(
                callback,
                callback_data=callback_data,
                user=user,
            )

        render_mock.assert_awaited_once_with(3)
        edit_mock.assert_awaited_once_with(
            callback,
            user,
            rendered_page_text,
            reply_markup=rendered_page_keyboard,
        )
        answer_callback_mock.assert_awaited_once_with(callback)
        set_anchor.assert_not_called()
