"""Internal cross-server forwarding for authoritative offer expiry."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Tuple

import httpx

from core.config import settings
from core.server_routing import current_server, normalize_server, peer_server_url_for
from core.services.offer_expiry_command_receipt_service import OfferExpiryReceiptOutcome
from core.trade_forwarding import sign_internal_payload
from core.trading_observability import log_trading_event, summarize_response_body


logger = logging.getLogger(__name__)

OFFER_EXPIRY_RECEIPT_OUTCOMES = frozenset(
    outcome.value for outcome in OfferExpiryReceiptOutcome
)


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

    expected_command_id = str(payload.get("command_id") or "").strip()
    expected_offer_public_id = str(payload.get("offer_public_id") or "").strip()
    response_is_success = 200 <= response.status_code < 300
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
        if response.status_code < 400 and expected_command_id:
            return 503, {
                "detail": "پاسخ سرور مرجع قابل تأیید نبود. لطفاً دوباره تلاش کنید."
            }
        return response.status_code, {"detail": "پاسخ نامعتبر از سرور مرجع لفظ"}

    acknowledged_command_id = (
        str(body.get("command_id") or "").strip() if isinstance(body, dict) else ""
    )
    acknowledged_offer_public_id = (
        str(body.get("offer_public_id") or "").strip() if isinstance(body, dict) else ""
    )
    acknowledged_outcome = (
        str(body.get("outcome") or "").strip() if isinstance(body, dict) else ""
    )
    receipt_contract_valid = bool(
        isinstance(body, dict)
        and acknowledged_command_id == expected_command_id
        and acknowledged_offer_public_id == expected_offer_public_id
        and acknowledged_outcome in OFFER_EXPIRY_RECEIPT_OUTCOMES
        and body.get("expired") is True
        and type(body.get("replayed")) is bool
    )
    if (
        response_is_success
        and expected_command_id
        and not receipt_contract_valid
    ):
        log_trading_event(
            logger,
            "offer_expiry_forward.receipt_ack_invalid",
            level="error",
            action="offer_expiry_forward",
            result="failure",
            status_code=response.status_code,
            receipt_ack_present=bool(acknowledged_command_id),
            receipt_outcome_valid=acknowledged_outcome in OFFER_EXPIRY_RECEIPT_OUTCOMES,
            **log_context,
        )
        return 503, {
            "detail": "پاسخ سرور مرجع قابل تأیید نبود. لطفاً دوباره تلاش کنید."
        }

    if expected_command_id and not response_is_success and response.status_code < 400:
        log_trading_event(
            logger,
            "offer_expiry_forward.unexpected_success_status",
            level="error",
            action="offer_expiry_forward",
            result="failure",
            status_code=response.status_code,
            **log_context,
        )
        return 503, {
            "detail": "پاسخ سرور مرجع قابل تأیید نبود. لطفاً دوباره تلاش کنید."
        }

    log_trading_event(
        logger,
        "offer_expiry_forward.response",
        action="offer_expiry_forward",
        result="success" if response_is_success else "denied",
        status_code=response.status_code,
        **log_context,
    )
    return response.status_code, body
