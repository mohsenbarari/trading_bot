"""Internal cross-server forwarding for authoritative offer expiry."""
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


logger = logging.getLogger(__name__)


def _json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


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
        "offer_public_id": payload.get("offer_public_id"),
        "command_id": payload.get("command_id"),
    }


async def forward_offer_expiry_to_home_server(target_server: str, payload: dict[str, Any]) -> Tuple[int, Any]:
    target_url = peer_server_url_for(target_server)
    source_server = current_server()
    log_context = _safe_forward_log_context(target_server, payload)
    if not target_url:
        log_trading_event(
            logger,
            "offer_expiry_forward.peer_unavailable",
            level="warning",
            action="offer_expiry_forward",
            result="failure",
            **log_context,
        )
        return 503, {"detail": "سرور مرجع لفظ در دسترس نیست."}

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
        async with httpx.AsyncClient(timeout=settings.trade_forward_timeout_seconds, verify=_tls_verify_setting()) as client:
            response = await client.post(
                f"{target_url}/api/offers/internal/expire",
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        log_trading_event(
            logger,
            "offer_expiry_forward.timeout",
            level="warning",
            action="offer_expiry_forward",
            result="failure",
            error_class="TimeoutException",
            **log_context,
        )
        return 504, {"detail": "مهلت ارتباط با سرور مرجع لفظ تمام شد. لطفاً دوباره تلاش کنید."}
    except httpx.RequestError as exc:
        log_trading_event(
            logger,
            "offer_expiry_forward.request_error",
            level="warning",
            action="offer_expiry_forward",
            result="failure",
            error_class=type(exc).__name__,
            **log_context,
        )
        return 503, {"detail": "ارتباط با سرور مرجع لفظ برقرار نشد. لطفاً دوباره تلاش کنید."}

    try:
        body = response.json()
    except ValueError:
        log_trading_event(
            logger,
            "offer_expiry_forward.invalid_json_response",
            level="warning",
            action="offer_expiry_forward",
            result="failure",
            status_code=response.status_code,
            **log_context,
            **summarize_response_body(response.text),
        )
        return response.status_code, {"detail": "پاسخ نامعتبر از سرور مرجع لفظ"}

    expected_command_id = str(payload.get("command_id") or "").strip()
    acknowledged_command_id = (
        str(body.get("command_id") or "").strip() if isinstance(body, dict) else ""
    )
    if (
        response.status_code < 400
        and expected_command_id
        and acknowledged_command_id != expected_command_id
    ):
        log_trading_event(
            logger,
            "offer_expiry_forward.legacy_success_without_receipt_ack",
            level="warning",
            action="offer_expiry_forward",
            result="success",
            status_code=response.status_code,
            **log_context,
        )

    log_trading_event(
        logger,
        "offer_expiry_forward.response",
        action="offer_expiry_forward",
        result="success" if response.status_code < 400 else "denied",
        status_code=response.status_code,
        **log_context,
    )
    return response.status_code, body
