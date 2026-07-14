"""Safe observability helpers for the trading money path."""
from __future__ import annotations

import hashlib
from typing import Any

from core.metrics import normalize_status_class, registry
from core.server_routing import normalize_server


TRADING_LOG_CLASS = "trading"

_ALLOWED_ACTIONS = {
    "offer_create",
    "offer_expiry_command",
    "offer_expiry_forward",
    "offer_idempotent_replay",
    "trade_execute",
    "trade_idempotent_replay",
    "trade_forward",
    "trade_internal_execute",
    "trade_commit",
    "trading_side_effect",
}

_ALLOWED_RESULTS = {
    "attempt",
    "success",
    "failure",
    "denied",
    "replay",
    "conflict",
    "error",
    "noop",
    "slow",
    "timing",
}

_ALLOWED_SIDE_EFFECTS = {
    "active_offer_count_cache",
    "notification",
    "offer_channel_buttons_remove",
    "offer_channel_state_apply",
    "offer_expiry_post_commit",
    "realtime_publish",
    "telegram_channel_buttons",
    "telegram_message",
    "web_push_schedule",
}

_ALLOWED_REASONS = {
    "bad_signature",
    "create_offer",
    "create_offer_final",
    "create_offer_guard_repair",
    "cancel_all_active_offers",
    "idempotency_integrity_conflict",
    "invalid_source_server",
    "manual_expire",
    "missing_actor",
    "missing_responder",
    "remote_home",
    "republish_old_offer",
    "side_effect_failure",
    "wrong_authoritative_server",
}

_ALLOWED_PHASES = {
    "allocated_trade_number",
    "built_execution_plan",
    "built_response",
    "checked_idempotency",
    "committed",
    "flushed_trade_state",
    "loaded_response_context",
    "locked_offer",
    "prepared_side_effects",
    "published_realtime",
    "validated_amount",
}


def _safe_choice(value: Any, allowed: set[str], *, fallback: str) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_")
    return candidate if candidate in allowed else fallback


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _safe_opaque_hash(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]


def summarize_response_body(text: str | bytes | None) -> dict[str, Any]:
    if text is None:
        return {"response_body_size": 0, "response_body_sha256": None}
    if isinstance(text, bytes):
        raw = text
    else:
        raw = str(text).encode("utf-8", errors="replace")
    if not raw:
        return {"response_body_size": 0, "response_body_sha256": None}
    return {
        "response_body_size": len(raw),
        "response_body_sha256": hashlib.sha256(raw).hexdigest(),
    }


def safe_trading_log_context(
    *,
    action: str,
    result: str,
    event: str | None = None,
    offer_id: Any = None,
    trade_id: Any = None,
    trade_number: Any = None,
    source_server: Any = None,
    target_server: Any = None,
    status_code: Any = None,
    error_class: Any = None,
    has_idempotency_key: Any = None,
    delegated_actor: Any = None,
    chain_length: Any = None,
    side_effect: Any = None,
    reason: Any = None,
    response_body_size: Any = None,
    response_body_sha256: Any = None,
    phase: Any = None,
    phase_duration_ms: Any = None,
    total_duration_ms: Any = None,
    request_source_server: Any = None,
    command_id: Any = None,
    offer_public_id: Any = None,
    dedupe_key: Any = None,
) -> dict[str, Any]:
    safe_action = _safe_choice(action, _ALLOWED_ACTIONS, fallback="trading_side_effect")
    safe_result = _safe_choice(result, _ALLOWED_RESULTS, fallback="error")
    context: dict[str, Any] = {
        "event": event or f"trading.{safe_action}.{safe_result}",
        "log_class": TRADING_LOG_CLASS,
        "action": safe_action,
        "result": safe_result,
    }

    for key, value in (
        ("offer_id", _safe_int(offer_id)),
        ("trade_id", _safe_int(trade_id)),
        ("trade_number", _safe_int(trade_number)),
        ("status_code", _safe_int(status_code)),
        ("chain_length", _safe_int(chain_length)),
        ("response_body_size", _safe_int(response_body_size)),
    ):
        if value is not None:
            context[key] = value

    if source_server:
        context["source_server"] = normalize_server(str(source_server), default="unknown")
    if target_server:
        context["target_server"] = normalize_server(str(target_server), default="unknown")
    if request_source_server:
        context["request_source_server"] = normalize_server(str(request_source_server), default="unknown")
    for key, value in (
        ("command_id_hash", _safe_opaque_hash(command_id)),
        ("offer_public_id_hash", _safe_opaque_hash(offer_public_id)),
        ("dedupe_key_hash", _safe_opaque_hash(dedupe_key)),
    ):
        if value is not None:
            context[key] = value
    if error_class:
        context["error_class"] = str(error_class)[:80]
    if has_idempotency_key is not None:
        context["has_idempotency_key"] = _safe_bool(has_idempotency_key)
    if delegated_actor is not None:
        context["delegated_actor"] = _safe_bool(delegated_actor)
    if status_code is not None:
        context["status_class"] = normalize_status_class(status_code)
    if side_effect:
        context["side_effect"] = _safe_choice(side_effect, _ALLOWED_SIDE_EFFECTS, fallback="notification")
    if reason:
        context["reason"] = _safe_choice(reason, _ALLOWED_REASONS, fallback="side_effect_failure")
    if response_body_sha256:
        context["response_body_sha256"] = str(response_body_sha256)[:64]
    if phase:
        context["phase"] = _safe_choice(phase, _ALLOWED_PHASES, fallback="built_response")
    for key, value in (
        ("phase_duration_ms", _safe_float(phase_duration_ms)),
        ("total_duration_ms", _safe_float(total_duration_ms)),
    ):
        if value is not None:
            context[key] = round(value, 2)

    return context


def record_trading_event_metric(*, action: str, result: str) -> None:
    registry.counter(
        "trading_bot_trading_events_total",
        "Trading money-path events by low-cardinality action and result.",
        action=_safe_choice(action, _ALLOWED_ACTIONS, fallback="trading_side_effect"),
        result=_safe_choice(result, _ALLOWED_RESULTS, fallback="error"),
    )


def record_trading_side_effect_metric(*, side_effect: str, result: str) -> None:
    registry.counter(
        "trading_bot_trading_side_effects_total",
        "Trading side-effect outcomes by low-cardinality side effect and result.",
        side_effect=_safe_choice(side_effect, _ALLOWED_SIDE_EFFECTS, fallback="notification"),
        result=_safe_choice(result, _ALLOWED_RESULTS, fallback="error"),
    )


def log_trading_event(logger: Any, message: str, *, level: str = "info", **context: Any) -> dict[str, Any]:
    extra = safe_trading_log_context(**context)
    record_trading_event_metric(action=extra["action"], result=extra["result"])
    side_effect = extra.get("side_effect")
    if side_effect:
        record_trading_side_effect_metric(side_effect=str(side_effect), result=extra["result"])

    log_method = getattr(logger, str(level or "info").lower(), logger.info)
    log_method(message, extra=extra)
    return extra
