"""Signed foreign-to-Iran transport for the offer overtime preference."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from core.config import settings
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, peer_server_url_for
from core.services.offer_overtime_preference_service import BOT_SAVE_UNAVAILABLE_MESSAGE
from core.trade_forwarding import _json_body, _tls_verify_setting, sign_internal_payload
from core.trading_observability import summarize_response_body


logger = logging.getLogger(__name__)

OFFER_OVERTIME_PREFERENCE_INTERNAL_PATH = "/api/auth/internal/offer-overtime/update"


async def forward_offer_overtime_preference_to_iran(
    payload: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> tuple[int, Any]:
    """POST the preference command to Iran. Never writes locally.

    Every definite failure path returns the same approved unavailable copy so
    the bot never names servers or invents a deferred success.
    """
    context = {
        "event": "offer_overtime_preference.forward",
        "source_server": current_server(),
        "target_server": SERVER_IRAN,
        "user_id": int(payload.get("user_id") or 0),
    }
    if current_server() != SERVER_FOREIGN:
        logger.warning("Offer overtime preference forward rejected outside foreign", extra=context)
        return 403, {"detail": BOT_SAVE_UNAVAILABLE_MESSAGE}

    target_url = peer_server_url_for(SERVER_IRAN)
    if not target_url:
        logger.warning("Offer overtime preference Iran peer unavailable", extra=context)
        return 503, {"detail": BOT_SAVE_UNAVAILABLE_MESSAGE}

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
                f"{target_url}{OFFER_OVERTIME_PREFERENCE_INTERNAL_PATH}",
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        logger.warning("Offer overtime preference forward timed out", extra=context)
        return 504, {"detail": BOT_SAVE_UNAVAILABLE_MESSAGE}
    except httpx.RequestError as exc:
        logger.warning(
            "Offer overtime preference forward failed",
            extra={**context, "error_type": type(exc).__name__},
        )
        return 503, {"detail": BOT_SAVE_UNAVAILABLE_MESSAGE}

    try:
        response_body = response.json()
    except ValueError:
        logger.warning(
            "Offer overtime preference forward returned invalid JSON",
            extra={
                **context,
                "status_code": response.status_code,
                **summarize_response_body(response.text),
            },
        )
        return response.status_code, {"detail": BOT_SAVE_UNAVAILABLE_MESSAGE}

    if response.status_code < 200 or response.status_code >= 300:
        detail = None
        if isinstance(response_body, dict):
            detail = response_body.get("detail")
        if not isinstance(detail, str) or not detail:
            detail = BOT_SAVE_UNAVAILABLE_MESSAGE
        return response.status_code, {"detail": detail}

    return response.status_code, response_body
