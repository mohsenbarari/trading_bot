"""Per-update receipt timestamp for deadline-bound Telegram callbacks."""
from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import datetime, timezone


_callback_received_at: ContextVar[datetime | None] = ContextVar(
    "telegram_callback_received_at",
    default=None,
)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("telegram_callback_received_at_invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def bind_telegram_callback_received_at(received_at: datetime) -> Token:
    """Bind one normalized edge timestamp for the current async context."""
    return _callback_received_at.set(_utc(received_at))


def reset_telegram_callback_received_at(token: Token) -> None:
    _callback_received_at.reset(token)


def current_telegram_callback_received_at() -> datetime | None:
    return _callback_received_at.get()
