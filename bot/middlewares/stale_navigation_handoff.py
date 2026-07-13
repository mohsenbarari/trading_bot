"""Route known reply-keyboard actions around stale FSM message handlers."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update


def _extract_message(event: TelegramObject) -> Message | None:
    if isinstance(event, Message):
        return event
    if isinstance(event, Update):
        return event.message
    return None


class StaleNavigationHandoffMiddleware(BaseMiddleware):
    """Give persistent navigation buttons priority when an old FSM is active."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message = _extract_message(event)
        state = data.get("state")
        user = data.get("user")
        if message is None or state is None or user is None:
            return await handler(event, data)
        if await state.get_state() is None:
            return await handler(event, data)

        from bot.handlers.panel import handoff_navigation_button

        if await handoff_navigation_button(message, state, user):
            return None
        return await handler(event, data)
