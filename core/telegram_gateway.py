"""Central Telegram side-effect gateway.

Telegram execution is foreign-only. Iran may create durable product data or
sync intent, but it must not call Telegram directly.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import os
from typing import Any, Optional

import httpx

from core.application_writer_term import policy_from_settings, require_active_writer_term
from core.config import settings
from core.server_routing import SERVER_FOREIGN, current_server

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_MESSAGE_NOT_MODIFIED = "message is not modified"
_MISSING = object()


class TelegramGatewaySurfaceError(RuntimeError):
    """Raised when Telegram execution is attempted outside the foreign surface."""


@dataclass(slots=True)
class TelegramGatewayResult:
    ok: bool
    method: str
    status_code: Optional[int] = None
    response_text: str = ""
    response_json: Optional[dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    error: Optional[str] = None

    @property
    def message_id(self) -> Optional[int]:
        result = self.response_json.get("result") if self.response_json else None
        if isinstance(result, Mapping):
            raw_message_id = result.get("message_id")
            try:
                return int(raw_message_id)
            except (TypeError, ValueError):
                return None
        return None


def assert_telegram_execution_surface(*, operation: str = "telegram") -> None:
    server = current_server()
    if server != SERVER_FOREIGN:
        raise TelegramGatewaySurfaceError(
            f"Telegram operation {operation!r} is only allowed on the foreign server; current={server!r}"
        )


def _resolve_bot_token(bot_token: Optional[str] = None) -> Optional[str]:
    return bot_token or settings.bot_token or os.getenv("BOT_TOKEN")


def require_telegram_gateway_writer_term() -> None:
    """Fence a provider-visible Telegram request in a term-enabled runtime.

    Bot background workers use this HTTP gateway directly rather than the
    aiogram polling client's session.  Keeping this check at their shared
    egress boundary closes the race between the bot watchdog ticks and a
    worker's next provider call.  The legacy/default path remains no-op and
    does not open a term file.
    """

    require_active_writer_term(policy_from_settings(settings))


def _response_json(response: Any) -> Optional[dict[str, Any]]:
    try:
        parsed = response.json()
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _response_text(response: Any) -> str:
    try:
        return str(response.text or "")
    except Exception:
        return ""


def _status_ok(response: Any) -> bool:
    status_code = getattr(response, "status_code", None)
    if status_code == 200:
        return True
    return TELEGRAM_MESSAGE_NOT_MODIFIED in _response_text(response).lower()


def _missing_token_result(method: str, idempotency_key: Optional[str]) -> TelegramGatewayResult:
    return TelegramGatewayResult(
        ok=False,
        method=method,
        idempotency_key=idempotency_key,
        error="missing_bot_token",
    )


async def post_telegram_method(
    method: str,
    payload: Mapping[str, Any],
    *,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    """Execute one Telegram Bot API method through the approved async path."""
    require_telegram_gateway_writer_term()
    assert_telegram_execution_surface(operation=method)

    token = _resolve_bot_token(bot_token)
    if not token:
        return _missing_token_result(method, idempotency_key)

    response = None
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TELEGRAM_API_BASE_URL}/bot{token}/{method}",
                json=dict(payload),
                timeout=timeout,
            )
    except Exception as exc:
        logger.debug(
            "Telegram gateway async request failed",
            extra={
                "event": "telegram.gateway_async_failed",
                "method": method,
                "idempotency_key": idempotency_key,
                "error_class": type(exc).__name__,
            },
        )
        return TelegramGatewayResult(
            ok=False,
            method=method,
            idempotency_key=idempotency_key,
            error=type(exc).__name__,
        )

    return TelegramGatewayResult(
        ok=_status_ok(response),
        method=method,
        status_code=getattr(response, "status_code", None),
        response_text=_response_text(response),
        response_json=_response_json(response),
        idempotency_key=idempotency_key,
    )


def post_telegram_method_sync(
    method: str,
    payload: Mapping[str, Any],
    *,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    """Execute one Telegram Bot API method through the approved sync path."""
    require_telegram_gateway_writer_term()
    assert_telegram_execution_surface(operation=method)

    token = _resolve_bot_token(bot_token)
    if not token:
        return _missing_token_result(method, idempotency_key)

    try:
        response = httpx.post(
            f"{TELEGRAM_API_BASE_URL}/bot{token}/{method}",
            json=dict(payload),
            timeout=timeout,
        )
    except Exception as exc:
        logger.debug(
            "Telegram gateway sync request failed",
            extra={
                "event": "telegram.gateway_sync_failed",
                "method": method,
                "idempotency_key": idempotency_key,
                "error_class": type(exc).__name__,
            },
        )
        return TelegramGatewayResult(
            ok=False,
            method=method,
            idempotency_key=idempotency_key,
            error=type(exc).__name__,
        )

    return TelegramGatewayResult(
        ok=_status_ok(response),
        method=method,
        status_code=getattr(response, "status_code", None),
        response_text=_response_text(response),
        response_json=_response_json(response),
        idempotency_key=idempotency_key,
    )


async def send_message(
    chat_id: int,
    text: str,
    *,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Mapping[str, Any]] = None,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await post_telegram_method(
        "sendMessage",
        payload,
        timeout=timeout,
        bot_token=bot_token,
        idempotency_key=idempotency_key,
    )


def send_message_sync(
    chat_id: int,
    text: str,
    *,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Mapping[str, Any]] = None,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return post_telegram_method_sync(
        "sendMessage",
        payload,
        timeout=timeout,
        bot_token=bot_token,
        idempotency_key=idempotency_key,
    )


async def edit_message_reply_markup(
    chat_id: int,
    message_id: int,
    *,
    reply_markup: Optional[Mapping[str, Any]] = None,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await post_telegram_method(
        "editMessageReplyMarkup",
        payload,
        timeout=timeout,
        bot_token=bot_token,
        idempotency_key=idempotency_key,
    )


async def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: Any = _MISSING,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if reply_markup is not _MISSING:
        payload["reply_markup"] = reply_markup
    return await post_telegram_method(
        "editMessageText",
        payload,
        timeout=timeout,
        bot_token=bot_token,
        idempotency_key=idempotency_key,
    )


async def ban_chat_member(
    chat_id: int,
    user_id: int,
    *,
    revoke_messages: bool = False,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    return await post_telegram_method(
        "banChatMember",
        {
            "chat_id": chat_id,
            "user_id": user_id,
            "revoke_messages": revoke_messages,
        },
        timeout=timeout,
        bot_token=bot_token,
        idempotency_key=idempotency_key,
    )


async def unban_chat_member(
    chat_id: int,
    user_id: int,
    *,
    only_if_banned: bool = True,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    return await post_telegram_method(
        "unbanChatMember",
        {
            "chat_id": chat_id,
            "user_id": user_id,
            "only_if_banned": only_if_banned,
        },
        timeout=timeout,
        bot_token=bot_token,
        idempotency_key=idempotency_key,
    )
