"""Signed foreign-to-Iran transport for Telegram registration and account linking."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from core.config import settings
from core.registration_contracts import TelegramRegistrationCommand
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, peer_server_url_for
from core.telegram_account_link_contracts import TelegramAccountLinkCommand
from core.trade_forwarding import _json_body, _tls_verify_setting, sign_internal_payload
from core.trading_observability import summarize_response_body


logger = logging.getLogger(__name__)


async def _post_signed_iran_command(
    *,
    path: str,
    payload: dict[str, Any],
    command_id: object,
    event: str,
    timeout_seconds: float | None,
) -> tuple[int, Any]:
    context = {
        "event": event,
        "source_server": current_server(),
        "target_server": SERVER_IRAN,
        "command_id": str(command_id),
        "has_idempotency_key": bool(payload.get("idempotency_key")),
    }
    if current_server() != SERVER_FOREIGN:
        logger.warning("Telegram registration command rejected outside foreign", extra=context)
        return 403, {"detail": "این فرمان فقط از سرور تلگرام قابل ارسال است"}
    target_url = peer_server_url_for(SERVER_IRAN)
    if not target_url:
        logger.warning("Telegram registration Iran peer unavailable", extra=context)
        return 503, {"detail": "سرور ایران در دسترس نیست"}

    body = _json_body(payload)
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.sync_api_key or "",
        "X-Timestamp": str(timestamp),
        "X-Signature": sign_internal_payload(body, timestamp),
        "X-Source-Server": SERVER_FOREIGN,
    }
    try:
        async with httpx.AsyncClient(
            timeout=(
                timeout_seconds
                if timeout_seconds is not None
                else settings.trade_forward_timeout_seconds
            ),
            verify=_tls_verify_setting(),
        ) as client:
            response = await client.post(
                f"{target_url}{path}",
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        logger.warning("Telegram registration command timed out", extra=context)
        return 504, {"detail": "مهلت ارتباط با سرور ایران تمام شد"}
    except httpx.RequestError as exc:
        logger.warning(
            "Telegram registration command transport failed",
            extra={**context, "error_type": type(exc).__name__},
        )
        return 503, {"detail": "ارتباط با سرور ایران برقرار نشد"}

    try:
        response_body = response.json()
    except ValueError:
        logger.warning(
            "Telegram registration command returned invalid JSON",
            extra={
                **context,
                "status_code": response.status_code,
                **summarize_response_body(response.text),
            },
        )
        return response.status_code, {"detail": "پاسخ نامعتبر از سرور ایران"}
    return response.status_code, response_body


async def forward_telegram_registration_command(
    command: TelegramRegistrationCommand,
    *,
    timeout_seconds: float | None = None,
) -> tuple[int, Any]:
    return await _post_signed_iran_command(
        path="/api/auth/internal/telegram-registration/reconcile",
        payload=command.model_dump(mode="json"),
        command_id=command.command_id,
        event="telegram_registration.forward_attempt",
        timeout_seconds=timeout_seconds,
    )


async def forward_telegram_account_link_command(
    command: TelegramAccountLinkCommand,
    *,
    timeout_seconds: float | None = None,
) -> tuple[int, Any]:
    return await _post_signed_iran_command(
        path="/api/auth/internal/telegram-link/complete",
        payload=command.model_dump(mode="json"),
        command_id=command.command_id,
        event="telegram_account_link.forward_attempt",
        timeout_seconds=timeout_seconds,
    )
