"""Signed forwarding for Telegram-origin customer invitations."""

from __future__ import annotations

import logging
import time
from typing import Any, Tuple

import httpx

from core.config import settings
from core.log_redaction import mask_mobile
from core.server_routing import SERVER_IRAN, current_server, peer_server_url_for
from core.trade_forwarding import _json_body, _tls_verify_setting, sign_internal_payload
from core.trading_observability import summarize_response_body


logger = logging.getLogger(__name__)


def _safe_log_context(payload: dict[str, Any]) -> dict[str, Any]:
    mobile_number = str(payload.get("mobile_number") or "")
    context = {
        "source_server": current_server(),
        "target_server": SERVER_IRAN,
        "has_idempotency_key": bool(payload.get("idempotency_key")),
        "mobile_masked": mask_mobile(mobile_number),
    }
    owner_user_id = payload.get("owner_user_id")
    if owner_user_id is not None:
        context["owner_user_id"] = int(owner_user_id)
    return context


async def forward_customer_invite_to_iran(
    payload: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> Tuple[int, Any]:
    target_url = peer_server_url_for(SERVER_IRAN)
    log_context = _safe_log_context(payload)
    if not target_url:
        logger.warning(
            "Customer invite forward peer unavailable",
            extra={"event": "customer_invite.forward.peer_unavailable", **log_context},
        )
        return 503, {"detail": "سرور ایران برای دعوت مشتری در دسترس نیست."}

    body = _json_body(payload)
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.sync_api_key or "",
        "X-Timestamp": str(timestamp),
        "X-Signature": sign_internal_payload(body, timestamp),
        "X-Source-Server": current_server(),
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds if timeout_seconds is not None else settings.trade_forward_timeout_seconds,
            verify=_tls_verify_setting(),
        ) as client:
            response = await client.post(
                f"{target_url}/api/customers/internal/owner-relations",
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        logger.warning(
            "Customer invite forward timed out",
            extra={"event": "customer_invite.forward.timeout", **log_context},
        )
        return 504, {"detail": "مهلت ارتباط با سرور ایران تمام شد. لطفاً دوباره تلاش کنید."}
    except httpx.RequestError as exc:
        logger.warning(
            "Customer invite forward request failed",
            extra={
                "event": "customer_invite.forward.request_error",
                "error_type": type(exc).__name__,
                **log_context,
            },
        )
        return 503, {"detail": "ارتباط با سرور ایران برقرار نشد. لطفاً دوباره تلاش کنید."}

    try:
        response_body = response.json()
    except ValueError:
        logger.warning(
            "Customer invite forward received invalid JSON",
            extra={
                "event": "customer_invite.forward.invalid_json_response",
                "status_code": response.status_code,
                **log_context,
                **summarize_response_body(response.text),
            },
        )
        return response.status_code, {"detail": "پاسخ نامعتبر از سرور ایران"}

    logger.info(
        "Customer invite forward completed",
        extra={
            "event": "customer_invite.forward.response",
            "status_code": response.status_code,
            "result": "success" if response.status_code < 400 else "denied",
            **log_context,
        },
    )
    return response.status_code, response_body
