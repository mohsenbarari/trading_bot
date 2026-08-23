"""Central Telegram side-effect gateway.

Telegram execution is foreign-only. Iran may create durable product data or
sync intent, but it must not call Telegram directly.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
from typing import Any, Optional

import httpx

from core.config import settings
from core.server_routing import SERVER_FOREIGN, current_server
from core.telegram_delivery_runtime_policy import (
    TelegramProviderAuthorityError,
    assert_telegram_provider_execution_authority,
)

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_MESSAGE_NOT_MODIFIED = "message is not modified"
_MISSING = object()
_CORRELATION_HASH_DOMAIN = b"telegram-delivery-correlation-v1\x00"
_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
_HTTP_CLIENT_LIMITS = httpx.Limits(
    max_connections=32,
    max_keepalive_connections=8,
)
_async_http_client: httpx.AsyncClient | None = None
_async_http_client_loop: asyncio.AbstractEventLoop | None = None
_async_http_client_factory: Any = None
_async_http_client_lock: asyncio.Lock | None = None


def _http_client_lock() -> asyncio.Lock:
    global _async_http_client_lock
    if _async_http_client_lock is None:
        _async_http_client_lock = asyncio.Lock()
    return _async_http_client_lock


def _http_client_is_usable(
    client: httpx.AsyncClient | None,
    loop: asyncio.AbstractEventLoop,
) -> bool:
    if client is None:
        return False
    if bool(getattr(client, "is_closed", False)):
        return False
    stored_loop = _async_http_client_loop
    if stored_loop is None:
        return False
    if stored_loop is not loop:
        return False
    if _async_http_client_factory is not httpx.AsyncClient:
        return False
    is_closed = getattr(stored_loop, "is_closed", None)
    if callable(is_closed) and is_closed():
        return False
    return True


async def aclose_telegram_http_client() -> None:
    """Close the recycled gateway client. Safe to call when none exists."""
    global _async_http_client, _async_http_client_loop, _async_http_client_factory
    client = _async_http_client
    _async_http_client = None
    _async_http_client_loop = None
    _async_http_client_factory = None
    closer = getattr(client, "aclose", None)
    if client is None or not callable(closer):
        return
    try:
        await closer()
    except Exception as exc:
        logger.debug(
            "Telegram gateway HTTP client close failed",
            extra={
                "event": "telegram.gateway_client_close_failed",
                "error_class": type(exc).__name__,
            },
        )


def reset_telegram_gateway_http_client_for_test() -> None:
    global _async_http_client, _async_http_client_loop, _async_http_client_factory, _async_http_client_lock
    _async_http_client = None
    _async_http_client_loop = None
    _async_http_client_factory = None
    _async_http_client_lock = None


async def get_telegram_http_client() -> httpx.AsyncClient:
    """Return one event-loop-bound client. Created on first use, not import."""
    global _async_http_client, _async_http_client_loop, _async_http_client_factory
    loop = asyncio.get_running_loop()
    if _http_client_is_usable(_async_http_client, loop):
        return _async_http_client
    async with _http_client_lock():
        if _http_client_is_usable(_async_http_client, loop):
            return _async_http_client
        await aclose_telegram_http_client()
        factory = httpx.AsyncClient
        client = factory(limits=_HTTP_CLIENT_LIMITS)
        _async_http_client = client
        _async_http_client_loop = loop
        _async_http_client_factory = factory
        return client


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
    transport_phase: Optional[str] = None

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


def _delivery_correlation_hash(value: Optional[str]) -> Optional[str]:
    """Return a stable, one-way log correlation without exposing queue identity."""
    if value is None:
        return None
    digest = hashlib.sha256()
    digest.update(_CORRELATION_HASH_DOMAIN)
    digest.update(str(value).encode("utf-8", errors="replace"))
    return digest.hexdigest()


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
        transport_phase="pre_write",
    )


def _transport_failure_phase(exc: BaseException, *, response: Any = None) -> str:
    if response is not None or getattr(exc, "response", None) is not None:
        return "response_received"
    if isinstance(
        exc,
        (ValueError, httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout),
    ):
        return "pre_write"
    return "write_unknown"


def _result_from_response(
    *,
    method: str,
    response: Any,
    idempotency_key: Optional[str],
    error: str | None = None,
) -> TelegramGatewayResult:
    return TelegramGatewayResult(
        ok=_status_ok(response),
        method=method,
        status_code=getattr(response, "status_code", None),
        response_text=_response_text(response),
        response_json=_response_json(response),
        idempotency_key=idempotency_key,
        error=error,
        transport_phase="response_received",
    )


def _document_multipart_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    encoded = str(payload.get("document_base64") or "").strip()
    filename = str(payload.get("document_filename") or "").strip()
    expected_hash = str(payload.get("document_sha256") or "").strip().lower()
    try:
        document = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("telegram_document_encoding_invalid") from exc
    if (
        not document
        or len(document) > _MAX_DOCUMENT_BYTES
        or not filename
        or len(filename) > 120
        or "/" in filename
        or "\\" in filename
        or hashlib.sha256(document).hexdigest() != expected_hash
    ):
        raise ValueError("telegram_document_contract_invalid")
    data: dict[str, str] = {}
    for key, value in payload.items():
        if key in {
            "document_base64",
            "document_filename",
            "document_sha256",
        } or value is None:
            continue
        data[key] = (
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if isinstance(value, (dict, list))
            else str(value)
        )
    return data, {
        "document": (filename, document, "application/octet-stream")
    }


def _json_request_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Omit absent optional fields at the Telegram HTTP boundary.

    Queue payloads intentionally retain ``None`` while they are hashed and
    freshness-checked.  Telegram's Bot API, however, treats JSON null for
    optional enum fields such as ``parse_mode`` as a supplied unsupported
    value instead of as an omitted field.
    """
    return {key: value for key, value in payload.items() if value is not None}


async def post_telegram_method(
    method: str,
    payload: Mapping[str, Any],
    *,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    """Execute one Telegram Bot API method through the approved async path."""
    assert_telegram_execution_surface(operation=method)
    try:
        assert_telegram_provider_execution_authority()
    except TelegramProviderAuthorityError as exc:
        raise TelegramGatewaySurfaceError(str(exc)) from exc

    token = _resolve_bot_token(bot_token)
    if not token:
        return _missing_token_result(method, idempotency_key)

    response = None
    try:
        client = await get_telegram_http_client()
        if method == "sendDocument":
            data, files = _document_multipart_payload(payload)
            response = await client.post(
                f"{TELEGRAM_API_BASE_URL}/bot{token}/{method}",
                data=data,
                files=files,
                timeout=timeout,
            )
        else:
            response = await client.post(
                f"{TELEGRAM_API_BASE_URL}/bot{token}/{method}",
                json=_json_request_payload(payload),
                timeout=timeout,
            )
    except Exception as exc:
        received_response = (
            response if response is not None else getattr(exc, "response", None)
        )
        logger.debug(
            "Telegram gateway async request failed",
            extra={
                "event": "telegram.gateway_async_failed",
                "method": method,
                "delivery_correlation_hash": _delivery_correlation_hash(
                    idempotency_key
                ),
                "error_class": type(exc).__name__,
            },
        )
        if received_response is not None:
            return _result_from_response(
                method=method,
                response=received_response,
                idempotency_key=idempotency_key,
                error=type(exc).__name__,
            )
        return TelegramGatewayResult(
            ok=False,
            method=method,
            idempotency_key=idempotency_key,
            error=type(exc).__name__,
            transport_phase=_transport_failure_phase(exc),
        )

    return _result_from_response(
        method=method,
        response=response,
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
    assert_telegram_execution_surface(operation=method)
    try:
        assert_telegram_provider_execution_authority()
    except TelegramProviderAuthorityError as exc:
        raise TelegramGatewaySurfaceError(str(exc)) from exc

    token = _resolve_bot_token(bot_token)
    if not token:
        return _missing_token_result(method, idempotency_key)

    response = None
    try:
        if method == "sendDocument":
            data, files = _document_multipart_payload(payload)
            response = httpx.post(
                f"{TELEGRAM_API_BASE_URL}/bot{token}/{method}",
                data=data,
                files=files,
                timeout=timeout,
            )
        else:
            response = httpx.post(
                f"{TELEGRAM_API_BASE_URL}/bot{token}/{method}",
                json=_json_request_payload(payload),
                timeout=timeout,
            )
    except Exception as exc:
        received_response = (
            response if response is not None else getattr(exc, "response", None)
        )
        logger.debug(
            "Telegram gateway sync request failed",
            extra={
                "event": "telegram.gateway_sync_failed",
                "method": method,
                "delivery_correlation_hash": _delivery_correlation_hash(
                    idempotency_key
                ),
                "error_class": type(exc).__name__,
            },
        )
        if received_response is not None:
            return _result_from_response(
                method=method,
                response=received_response,
                idempotency_key=idempotency_key,
                error=type(exc).__name__,
            )
        return TelegramGatewayResult(
            ok=False,
            method=method,
            idempotency_key=idempotency_key,
            error=type(exc).__name__,
            transport_phase=_transport_failure_phase(exc),
        )

    return _result_from_response(
        method=method,
        response=response,
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


async def delete_message(
    chat_id: int,
    message_id: int,
    *,
    timeout: float = 10,
    bot_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TelegramGatewayResult:
    return await post_telegram_method(
        "deleteMessage",
        {"chat_id": chat_id, "message_id": message_id},
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
