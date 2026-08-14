"""Bind the static identity of one isolated Telegram polling lane."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core.telegram_bot_identity_context import (
    bind_telegram_callback_bot_identity,
    reset_telegram_callback_bot_identity,
)


class TelegramBotIdentityMiddleware(BaseMiddleware):
    def __init__(self, identity: str):
        self.identity = identity

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        token = bind_telegram_callback_bot_identity(self.identity)
        try:
            return await handler(event, data)
        finally:
            reset_telegram_callback_bot_identity(token)
