"""Bounded observability for the offer-overtime lifecycle.

Logs and metrics carry opaque ids and status/reason labels only. Requester
identity, mobile numbers, and Telegram callback payloads are never included.
"""
from __future__ import annotations

import logging
from typing import Any

from core.metrics import record_overtime_lifecycle_event, record_overtime_signal
from core.server_routing import normalize_server

logger = logging.getLogger(__name__)

OVERTIME_LOG_CLASS = "overtime"

_ALLOWED_EVENTS = {
    "classify",
    "create",
    "queue",
    "promote",
    "present",
    "decide",
    "cancel",
    "invalidate",
    "decision_timeout",
    "forward_recover",
    "telegram_delivery",
    "stale_preference_snapshot",
    "silent_owner_expiry",
    "reconcile",
}

_ALLOWED_RESULTS = {
    "attempt",
    "success",
    "denied",
    "replay",
    "conflict",
    "error",
    "noop",
    "pending",
    "queued",
    "presented",
    "cancelled",
    "rejected",
    "timeout",
    "invalidated",
    "completed",
    "repaired",
    "detected",
}


def _safe_choice(value: Any, allowed: set[str], *, fallback: str) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_")
    return candidate if candidate in allowed else fallback


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if normalized >= 0 else None


def _safe_text(value: Any, *, max_length: int = 80) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:max_length]


def safe_overtime_log_context(
    *,
    event: str,
    result: str,
    request_public_id: Any = None,
    offer_public_id: Any = None,
    offer_owner_user_id: Any = None,
    request_home_server: Any = None,
    status: Any = None,
    terminal_reason: Any = None,
    issue: Any = None,
    count: Any = None,
) -> dict[str, Any]:
    safe_event = _safe_choice(event, _ALLOWED_EVENTS, fallback="reconcile")
    safe_result = _safe_choice(result, _ALLOWED_RESULTS, fallback="error")
    context: dict[str, Any] = {
        "event": f"offer_overtime.{safe_event}.{safe_result}",
        "log_class": OVERTIME_LOG_CLASS,
        "overtime_event": safe_event,
        "result": safe_result,
    }
    request_id = _safe_text(request_public_id, max_length=40)
    offer_id = _safe_text(offer_public_id, max_length=40)
    if request_id is not None:
        context["request_public_id"] = request_id
    if offer_id is not None:
        context["offer_public_id"] = offer_id
    owner_id = _safe_int(offer_owner_user_id)
    if owner_id is not None:
        context["offer_owner_user_id"] = owner_id
    if request_home_server:
        context["request_home_server"] = normalize_server(
            str(request_home_server),
            default="unknown",
        )
    status_text = _safe_text(status, max_length=64)
    if status_text is not None:
        context["status"] = status_text
    reason = _safe_text(terminal_reason, max_length=64)
    if reason is not None:
        context["terminal_reason"] = reason
    issue_text = _safe_text(issue, max_length=80)
    if issue_text is not None:
        context["issue"] = issue_text
    count_value = _safe_int(count)
    if count_value is not None:
        context["count"] = count_value
    return context


def log_overtime_event(
    message: str,
    *,
    event: str,
    result: str,
    level: str = "info",
    **context: Any,
) -> dict[str, Any]:
    extra = safe_overtime_log_context(event=event, result=result, **context)
    record_overtime_lifecycle_event(
        event=str(extra["overtime_event"]),
        result=str(extra["result"]),
    )
    log_method = getattr(logger, str(level or "info").lower(), logger.info)
    log_method(message, extra=extra)
    return extra


def emit_overtime_signal(*, signal: str, count: int = 1) -> None:
    record_overtime_signal(signal=signal, count=count)
