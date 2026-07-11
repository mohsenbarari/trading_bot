"""Signed Iran-to-foreign transport for one Web-login OTP delivery attempt."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from core.config import settings
from core.registration_contracts import TelegramOTPDeliveryCommand
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, peer_server_url_for
from core.trade_forwarding import _json_body, _tls_verify_setting, sign_internal_payload
from core.trading_observability import summarize_response_body


logger = logging.getLogger(__name__)


async def forward_telegram_otp_delivery(
    command: TelegramOTPDeliveryCommand,
    *,
    timeout_seconds: float | None = None,
) -> tuple[int, Any]:
    context = {
        "event": "otp.telegram_delivery_attempt",
        "source_server": current_server(),
        "target_server": SERVER_FOREIGN,
        "otp_request_id": str(command.otp_request_id),
    }
    if current_server() != SERVER_IRAN:
        logger.warning("Telegram OTP forwarding rejected outside Iran", extra=context)
        return 403, {"detail": "OTP delivery is Iran-authoritative"}
    target_url = peer_server_url_for(SERVER_FOREIGN)
    if not target_url:
        logger.warning("Telegram OTP foreign peer unavailable", extra=context)
        return 503, {"detail": "Telegram delivery peer unavailable"}

    body = _json_body(command.model_dump(mode="json"))
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.sync_api_key or "",
        "X-Timestamp": str(timestamp),
        "X-Signature": sign_internal_payload(body, timestamp),
        "X-Source-Server": SERVER_IRAN,
    }
    try:
        async with httpx.AsyncClient(
            timeout=(
                timeout_seconds
                if timeout_seconds is not None
                else min(5.0, float(settings.trade_forward_timeout_seconds))
            ),
            verify=_tls_verify_setting(),
        ) as client:
            response = await client.post(
                f"{target_url}/api/auth/internal/telegram-otp/deliver",
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        logger.warning("Telegram OTP delivery acknowledgement timed out", extra=context)
        return 504, {"detail": "Telegram delivery acknowledgement timed out"}
    except httpx.RequestError as exc:
        logger.warning(
            "Telegram OTP delivery transport failed",
            extra={**context, "error_type": type(exc).__name__},
        )
        return 503, {"detail": "Telegram delivery transport failed"}

    try:
        return response.status_code, response.json()
    except ValueError:
        logger.warning(
            "Telegram OTP delivery returned invalid JSON",
            extra={
                **context,
                "status_code": response.status_code,
                **summarize_response_body(response.text),
            },
        )
        return response.status_code, {"detail": "Invalid Telegram delivery response"}
