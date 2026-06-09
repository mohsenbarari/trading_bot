"""Bot update logging context middleware."""
from __future__ import annotations

import hashlib
from typing import Any, Awaitable, Callable
import time
from uuid import uuid4

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from core.error_tracking import capture_exception
from core.metrics import record_bot_update
from core.request_context import clear_request_context, set_request_context


def _inner_event(event: TelegramObject) -> TelegramObject | None:
    if isinstance(event, Update):
        return event.message or event.callback_query or event.edited_message
    return event


def _event_type(event: TelegramObject | None) -> str:
    if isinstance(event, Message):
        return "message"
    if isinstance(event, CallbackQuery):
        return "callback_query"
    return type(event).__name__ if event is not None else "unknown"


def _telegram_update_id(event: TelegramObject) -> int | None:
    if isinstance(event, Update):
        return event.update_id
    return None


def _hashed_telegram_user_id(raw_user_id: int | None) -> str | None:
    if raw_user_id is None:
        return None
    try:
        from core.config import settings

        salt = getattr(settings, "observability_telegram_user_hash_salt", None) or settings.jwt_secret_key
    except Exception:
        salt = None
    if not salt:
        return None
    digest = hashlib.sha256(f"{salt}:{raw_user_id}".encode("utf-8", errors="replace")).hexdigest()
    return f"tg:{digest[:16]}"


class BotLoggingContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        inner = _inner_event(event)
        telegram_user = getattr(inner, "from_user", None)
        db_user = data.get("user")
        actor_id = getattr(db_user, "id", None)
        actor_role = getattr(getattr(db_user, "role", None), "value", None)
        event_type = _event_type(inner)
        correlation_id = str(uuid4())
        telegram_update_id = _telegram_update_id(event)
        start_time = time.perf_counter()

        set_request_context(
            log_class="bot",
            bot_correlation_id=correlation_id,
            bot_update_id=str(telegram_update_id) if telegram_update_id is not None else None,
            bot_event_type=event_type,
            telegram_user_id=_hashed_telegram_user_id(getattr(telegram_user, "id", None)),
            actor_id=actor_id,
            actor_role=actor_role,
        )
        try:
            result = await handler(event, data)
            record_bot_update(event_type=event_type, result="success", duration_ms=round((time.perf_counter() - start_time) * 1000, 2))
            return result
        except Exception as exc:
            record_bot_update(event_type=event_type, result="failure", duration_ms=round((time.perf_counter() - start_time) * 1000, 2))
            capture_exception(exc, source="bot.update", extra={"bot_event_type": event_type})
            raise
        finally:
            clear_request_context()
