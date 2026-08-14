"""Capture callback receipt time before auth, DB work, or handler routing."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject, Update

from core.telegram_callback_receipt_context import (
    bind_telegram_callback_received_at,
    reset_telegram_callback_received_at,
)
from core.utils import utc_now


CALLBACK_RECEIVED_AT_DATA_KEY = "telegram_callback_received_at"


def _extract_callback_query(event: TelegramObject) -> CallbackQuery | None:
    if isinstance(event, CallbackQuery):
        return event
    if isinstance(event, Update):
        return event.callback_query
    return None


class CallbackReceiptMiddleware(BaseMiddleware):
    """Bind a trustworthy deadline origin at the first dispatcher boundary."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        callback = _extract_callback_query(event)
        if callback is None:
            return await handler(event, data)

        received_at = utc_now()
        data[CALLBACK_RECEIVED_AT_DATA_KEY] = received_at
        token = bind_telegram_callback_received_at(received_at)
        try:
            return await handler(event, data)
        finally:
            reset_telegram_callback_received_at(token)
