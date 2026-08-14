"""Forwarding-server retention for ambiguous trade-forward timeouts.

Home-server authority and idempotent replay already exist. This module only
retains intent on the forwarding server after an uncertain send (timeout), so
the client can see inventory M18 and a background reconciler can replay the
same signed payload without creating a local overtime ledger row.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.redis import get_redis_client
from core.server_routing import current_server, normalize_server
from core.trading_observability import log_trading_event
from core.utils import utc_now

logger = logging.getLogger(__name__)

#: Inventory M18 — exact approved copy for uncertain cross-server delivery.
AMBIGUOUS_FORWARD_PENDING_MESSAGE = "⏳ در حال بررسی درخواست..."

PENDING_KEY_PREFIX = "trade_forward_pending"
PENDING_TTL_SECONDS = 15 * 60
RECONCILE_MAX_ATTEMPTS = 6


def _pending_redis_key(idempotency_key: str, *, source_server: str | None = None) -> str:
    server = normalize_server(source_server, current_server())
    return f"{PENDING_KEY_PREFIX}:{server}:{idempotency_key.strip()}"


def _serialize_pending(payload: dict[str, Any], *, home_server: str) -> str:
    body = {
        "home_server": normalize_server(home_server),
        "payload": payload,
        "created_at": utc_now().isoformat(),
        "attempts": 0,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str)


async def mark_trade_forward_pending(
    *,
    idempotency_key: str | None,
    home_server: str,
    payload: dict[str, Any],
) -> bool:
    """Retain one pending marker for an uncertain forward. Returns False if skipped."""
    key = (idempotency_key or "").strip()
    if not key:
        return False
    redis_key = _pending_redis_key(key)
    try:
        client = get_redis_client()
        # Prefer first-writer: a later timeout for the same key keeps the original payload.
        created = await client.set(
            redis_key,
            _serialize_pending(payload, home_server=home_server),
            ex=PENDING_TTL_SECONDS,
            nx=True,
        )
        if not created:
            await client.expire(redis_key, PENDING_TTL_SECONDS)
        log_trading_event(
            logger,
            "trade_forward.pending_marked",
            action="trade_forward",
            result="pending",
            source_server=current_server(),
            target_server=normalize_server(home_server),
            has_idempotency_key=True,
        )
        return True
    except Exception as exc:
        log_trading_event(
            logger,
            "trade_forward.pending_mark_failed",
            level="warning",
            action="trade_forward",
            result="failure",
            error_class=type(exc).__name__,
            source_server=current_server(),
            has_idempotency_key=True,
        )
        return False


async def get_trade_forward_pending(idempotency_key: str | None) -> dict[str, Any] | None:
    key = (idempotency_key or "").strip()
    if not key:
        return None
    try:
        raw = await get_redis_client().get(_pending_redis_key(key))
    except Exception:
        return None
    if not raw:
        return None
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
    except (TypeError, ValueError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


async def clear_trade_forward_pending(idempotency_key: str | None) -> None:
    key = (idempotency_key or "").strip()
    if not key:
        return
    try:
        await get_redis_client().delete(_pending_redis_key(key))
    except Exception as exc:
        log_trading_event(
            logger,
            "trade_forward.pending_clear_failed",
            level="warning",
            action="trade_forward",
            result="failure",
            error_class=type(exc).__name__,
            source_server=current_server(),
            has_idempotency_key=True,
        )


def ambiguous_forward_pending_response(
    *,
    idempotency_key: str,
    offer_public_id: str | None = None,
) -> dict[str, Any]:
    return {
        "detail": AMBIGUOUS_FORWARD_PENDING_MESSAGE,
        "workflow": "forward_pending",
        "pending": True,
        "idempotency_key": idempotency_key,
        "offer_public_id": offer_public_id,
    }


async def reconcile_trade_forward_pending(idempotency_key: str | None) -> tuple[int, Any] | None:
    """Replay the retained payload against the home server once.

    Returns ``(status_code, body)`` when a definite answer arrives, otherwise
    ``None`` (still uncertain or no marker). Never creates a local ledger row.
    """
    from core.trade_forwarding import forward_trade_to_home_server

    pending = await get_trade_forward_pending(idempotency_key)
    if pending is None:
        return None
    payload = pending.get("payload")
    home = pending.get("home_server")
    if not isinstance(payload, dict) or not home:
        await clear_trade_forward_pending(idempotency_key)
        return None
    attempts = int(pending.get("attempts") or 0)
    if attempts >= RECONCILE_MAX_ATTEMPTS:
        log_trading_event(
            logger,
            "trade_forward.pending_reconcile_exhausted",
            level="warning",
            action="trade_forward",
            result="failure",
            source_server=current_server(),
            target_server=normalize_server(home),
            has_idempotency_key=True,
        )
        return None

    status_code, body = await forward_trade_to_home_server(home, payload)
    # Refresh attempt count while still pending.
    try:
        pending["attempts"] = attempts + 1
        await get_redis_client().set(
            _pending_redis_key(str(idempotency_key).strip()),
            json.dumps(pending, ensure_ascii=False, separators=(",", ":"), default=str),
            ex=PENDING_TTL_SECONDS,
        )
    except Exception:
        pass

    if status_code == 504:
        return None
    if status_code >= 500:
        return None

    await clear_trade_forward_pending(idempotency_key)
    log_trading_event(
        logger,
        "trade_forward.pending_reconciled",
        action="trade_forward",
        result="success" if status_code < 400 else "denied",
        source_server=current_server(),
        target_server=normalize_server(home),
        status_code=status_code,
        has_idempotency_key=True,
    )
    return status_code, body
