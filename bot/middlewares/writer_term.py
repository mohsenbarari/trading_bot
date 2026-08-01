"""Bot update admission bound to the local Writer Witness term."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core.application_writer_term import ApplicationWriterTermError
from core.db import require_application_writer_term


logger = logging.getLogger(__name__)


class WriterTermMiddleware(BaseMiddleware):
    """Refuse an update before Redis/DB/auth middleware can touch state.

    Disabled policy is a no-op.  In a fenced writer process the middleware is
    deliberately registered first, so a lease loss cannot lead to an inbound
    update opening a DB session or triggering a Bot API side effect.
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
