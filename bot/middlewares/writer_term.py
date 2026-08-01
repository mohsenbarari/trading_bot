"""Fail closed before processing a Telegram update without an active writer term."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core.application_writer_term import ApplicationWriterTermError
from core.db import require_application_writer_term


logger = logging.getLogger(__name__)


class WriterTermMiddleware(BaseMiddleware):
    """Check the local Writer Witness term before every bot update.

    The default-off term policy performs no lease-file I/O.  When a fenced
    runtime opts in, this middleware is registered before the Redis-based
    contention and authentication middleware so an expired term cannot open a
    DB session or trigger a Telegram side effect from an inbound update.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            require_application_writer_term()
        except ApplicationWriterTermError as exc:
            logger.warning(
                "Bot update refused because the local writer term is invalid",
                extra={
                    "event": "bot.writer_term.update_refused",
                    "error_class": type(exc).__name__,
                },
            )
            return None
        return await handler(event, data)
