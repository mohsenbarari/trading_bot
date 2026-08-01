"""Signed forwarding from the foreign Telegram bot to Iran Invitation authority."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from core.config import settings
from core.legacy_direct_fi_ir_transport_fence import (
    LegacyDirectFiIrTransportRetiredError,
    assert_legacy_direct_fi_ir_transport_retired,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, peer_server_url_for
from core.trade_forwarding import _json_body, _tls_verify_setting, sign_internal_payload
from core.trading_observability import summarize_response_body


logger = logging.getLogger(__name__)


async def forward_standard_invitation_to_iran(
    payload: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> tuple[int, Any]:
    # A local wrong-role call remains a 403 and never reaches transport.
    if current_server() != SERVER_FOREIGN:
        logger.warning(
            "Standard invitation forwarding rejected outside foreign",
            extra={"event": "invitation.standard.forward", "source_server": current_server(), "target_server": SERVER_IRAN},
        )
        return 403, {"detail": "ارسال دعوت‌نامه فقط از سرور تلگرام مجاز است."}

    # This historical FI->IR creation call has no safe direct replacement.
    # Return the existing unavailable shape before peer URL/body/HMAC/httpx.
    try:
        assert_legacy_direct_fi_ir_transport_retired(
            component="invitation-creation-forwarding",
            operation="direct peer standard-invitation command",
        )
    except LegacyDirectFiIrTransportRetiredError:
        logger.warning(
            "Standard invitation direct transport is retired",
            extra={"event": "invitation.standard.direct_transport_retired", "target_server": SERVER_IRAN},
        )
        return 503, {"detail": "سرور ایران برای ساخت دعوت‌نامه در دسترس نیست."}

    target_url = peer_server_url_for(SERVER_IRAN)
    context = {
        "event": "invitation.standard.forward",
        "source_server": current_server(),
        "target_server": SERVER_IRAN,
        "requester_user_id": int(payload.get("requester_user_id") or 0),
        "has_idempotency_key": bool(payload.get("idempotency_key")),
    }
    if not target_url:
        logger.warning("Standard invitation Iran peer unavailable", extra=context)
        return 503, {"detail": "سرور ایران برای ساخت دعوت‌نامه در دسترس نیست."}

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
                f"{target_url}/api/invitations/internal/create",
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        logger.warning("Standard invitation Iran forward timed out", extra=context)
        return 504, {"detail": "مهلت ارتباط با سرور ایران تمام شد. لطفاً دوباره تلاش کنید."}
    except httpx.RequestError as exc:
        logger.warning(
            "Standard invitation Iran forward failed",
            extra={**context, "error_type": type(exc).__name__},
        )
        return 503, {"detail": "ارتباط با سرور ایران برقرار نشد. لطفاً دوباره تلاش کنید."}

    try:
        response_body = response.json()
    except ValueError:
        logger.warning(
            "Standard invitation Iran forward returned invalid JSON",
            extra={
                **context,
                "status_code": response.status_code,
                **summarize_response_body(response.text),
            },
        )
        return response.status_code, {"detail": "پاسخ نامعتبر از سرور ایران"}
    return response.status_code, response_body
