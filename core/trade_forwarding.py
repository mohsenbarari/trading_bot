"""Internal cross-server forwarding for authoritative trade execution."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Tuple

import httpx

from core.config import settings
from core.server_routing import current_server, normalize_server, peer_server_url_for
from core.trading_observability import log_trading_event, summarize_response_body


logger = logging.getLogger(__name__)


def _json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def sign_internal_payload(body: str, timestamp: int) -> str:
    api_key = settings.sync_api_key or ""
    message = f"{timestamp}:{body}"
    return hmac.new(api_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_internal_signature(body: bytes, timestamp: str | None, signature: str | None, api_key: str | None) -> bool:
    if not settings.sync_api_key or api_key != settings.sync_api_key or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > 60:
        return False
    expected = sign_internal_payload(body.decode(), ts)
    return hmac.compare_digest(expected, signature)


def _tls_verify_setting() -> bool | str:
    ca_bundle = (settings.trade_forward_ca_bundle or "").strip()
    if ca_bundle:
        return ca_bundle
    return bool(settings.trade_forward_verify_tls)


def _safe_forward_log_context(target_server: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_server": current_server(),
        "target_server": normalize_server(target_server, default=""),
        "offer_id": payload.get("offer_id"),
        "has_idempotency_key": bool(payload.get("idempotency_key")),
    }


def _body_summary(text: str) -> dict[str, Any]:
    return summarize_response_body(text)


async def forward_trade_to_home_server(
    target_server: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> Tuple[int, Any]:
    target_url = peer_server_url_for(target_server)
    source_server = current_server()
    log_context = _safe_forward_log_context(target_server, payload)
    if not target_url:
        log_trading_event(
            logger,
            "trade_forward.peer_unavailable",
            level="warning",
            action="trade_forward",
            result="failure",
            **log_context,
        )
        return 503, {"detail": "سرور مرجع معامله در دسترس نیست."}

    body = _json_body(payload)
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.sync_api_key or "",
        "X-Timestamp": str(timestamp),
        "X-Signature": sign_internal_payload(body, timestamp),
        "X-Source-Server": source_server,
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds if timeout_seconds is not None else settings.trade_forward_timeout_seconds,
            verify=_tls_verify_setting(),
        ) as client:
            response = await client.post(
                f"{target_url}/api/trades/internal/execute",
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        log_trading_event(
            logger,
            "trade_forward.timeout",
            level="warning",
            action="trade_forward",
            result="failure",
            error_class="TimeoutException",
            **log_context,
        )
        return 504, {"detail": "مهلت ارتباط با سرور مرجع معامله تمام شد. لطفاً دوباره تلاش کنید."}
    except httpx.RequestError as exc:
        log_trading_event(
            logger,
            "trade_forward.request_error",
            level="warning",
            action="trade_forward",
            result="failure",
            error_class=type(exc).__name__,
            **log_context,
        )
        return 503, {"detail": "ارتباط با سرور مرجع معامله برقرار نشد. لطفاً دوباره تلاش کنید."}

    try:
        body = response.json()
    except ValueError:
        log_trading_event(
            logger,
            "trade_forward.invalid_json_response",
            level="warning",
            action="trade_forward",
            result="failure",
            status_code=response.status_code,
            **log_context,
            **_body_summary(response.text),
        )
        return response.status_code, {"detail": "پاسخ نامعتبر از سرور مرجع معامله"}

    if response.status_code >= 500:
        log_trading_event(
            logger,
            "trade_forward.remote_server_error",
            level="warning",
            action="trade_forward",
            result="failure",
            status_code=response.status_code,
            **log_context,
        )
    else:
        log_trading_event(
            logger,
            "trade_forward.response",
            action="trade_forward",
            result="success" if response.status_code < 400 else "denied",
            status_code=response.status_code,
            **log_context,
        )
    return response.status_code, body
