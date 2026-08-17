"""Signed cross-server forwarding for requester overtime cancellation.

The interaction surface accepts the user's cancellation.  The server that
owns the offer-request ledger remains the single writer and performs the
terminal transition under its database lock.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Tuple

import httpx

from core.config import settings
from core.server_routing import current_server, normalize_server, peer_server_url_for
from core.trade_forwarding import sign_internal_payload
from core.trading_observability import log_trading_event, summarize_response_body
from models.offer_request import OfferRequestStatus


logger = logging.getLogger(__name__)


def _json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def _tls_verify_setting() -> bool | str:
    ca_bundle = (settings.trade_forward_ca_bundle or "").strip()
    if ca_bundle:
        return ca_bundle
    return bool(settings.trade_forward_verify_tls)


def _safe_log_context(target_server: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_server": current_server(),
        "target_server": normalize_server(target_server, default=""),
        "command_id": payload.get("request_public_id"),
    }


async def forward_overtime_requester_cancel(
    target_server: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> Tuple[int, Any]:
    """Forward cancellation and validate the authoritative success receipt."""
    target_url = peer_server_url_for(target_server)
    context = _safe_log_context(target_server, payload)
    if not target_url:
        log_trading_event(
            logger,
            "overtime_cancel_forward.peer_unavailable",
            level="warning",
            action="overtime_cancel_forward",
            result="failure",
            **context,
        )
        return 503, {"detail": "مرجع درخواست در دسترس نیست. لطفاً دوباره تلاش کنید."}

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
            timeout=(
                timeout_seconds
                if timeout_seconds is not None
                else settings.trade_forward_timeout_seconds
            ),
            verify=_tls_verify_setting(),
        ) as client:
            response = await client.post(
                f"{target_url}/api/trades/internal/overtime-requests/cancel",
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        log_trading_event(
            logger,
            "overtime_cancel_forward.timeout",
            level="warning",
            action="overtime_cancel_forward",
            result="failure",
            error_class="TimeoutException",
            **context,
        )
        return 504, {"detail": "مهلت لغو درخواست تمام شد. لطفاً دوباره تلاش کنید."}
    except httpx.RequestError as exc:
        log_trading_event(
            logger,
            "overtime_cancel_forward.request_error",
            level="warning",
            action="overtime_cancel_forward",
            result="failure",
            error_class=type(exc).__name__,
            **context,
        )
        return 503, {"detail": "ارتباط برای لغو درخواست برقرار نشد. لطفاً دوباره تلاش کنید."}

    try:
        response_body = response.json()
    except ValueError:
        log_trading_event(
            logger,
            "overtime_cancel_forward.invalid_json_response",
            level="warning",
            action="overtime_cancel_forward",
            result="failure",
            status_code=response.status_code,
            **context,
            **summarize_response_body(response.text),
        )
        return 503, {"detail": "پاسخ لغو درخواست قابل تأیید نبود. لطفاً دوباره تلاش کنید."}

    success = 200 <= response.status_code < 300
    expected_public_id = str(payload.get("request_public_id") or "").strip()
    receipt_valid = bool(
        success
        and isinstance(response_body, dict)
        and str(response_body.get("request_public_id") or "").strip()
        == expected_public_id
        and response_body.get("result_status")
        == OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER.value
        and type(response_body.get("replayed")) is bool
    )
    if success and not receipt_valid:
        log_trading_event(
            logger,
            "overtime_cancel_forward.receipt_ack_invalid",
            level="error",
            action="overtime_cancel_forward",
            result="failure",
            status_code=response.status_code,
            **context,
        )
        return 503, {"detail": "پاسخ لغو درخواست قابل تأیید نبود. لطفاً دوباره تلاش کنید."}

    log_trading_event(
        logger,
        "overtime_cancel_forward.response",
        action="overtime_cancel_forward",
        result="success" if success else "denied",
        status_code=response.status_code,
        **context,
    )
    return response.status_code, response_body
