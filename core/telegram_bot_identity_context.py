"""Request-scoped Telegram bot identity for callback delivery ownership."""
from __future__ import annotations

from contextvars import ContextVar, Token


_bot_identity: ContextVar[str] = ContextVar(
    "telegram_callback_bot_identity", default="primary"
)


def current_telegram_callback_bot_identity() -> str:
    return _bot_identity.get()


def bind_telegram_callback_bot_identity(identity: str) -> Token[str]:
    return _bot_identity.set(str(identity or "").strip() or "primary")


def reset_telegram_callback_bot_identity(token: Token[str]) -> None:
    _bot_identity.reset(token)
