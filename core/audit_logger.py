"""Structured audit logging helpers for sensitive business actions."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

from core.log_redaction import redact
from core.metrics import record_business_action
from core.request_context import get_request_context


AuditResult = Literal["success", "failure", "denied", "noop"]

_ALLOWED_RESULTS = {"success", "failure", "denied", "noop"}
_AUDIT_LOGGER = logging.getLogger("audit")


def _safe_mapping(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    redacted = redact(dict(value))
    if not isinstance(redacted, dict):
        return None
    return {str(key): item for key, item in redacted.items() if item is not None}


def _safe_scalar(value: Any) -> Any:
    redacted = redact(value)
    if isinstance(redacted, (str, int, float, bool)) or redacted is None:
        return redacted
    return str(redacted)


def _normalize_result(result: str) -> str:
    return result if result in _ALLOWED_RESULTS else "failure"


def audit_log(
    action: str,
    *,
    target_type: str,
    target_id: Any | None = None,
    result: AuditResult | str = "success",
    actor_id: Any | None = None,
    actor_role: str | None = None,
    before_summary: Mapping[str, Any] | None = None,
    after_summary: Mapping[str, Any] | None = None,
    reason: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Emit one append-style audit event to the configured logging sink.

    The first sink is stdout JSON via ``core.logging_config``. The schema is
    intentionally strict so later Loki/SIEM routing can filter by log_class.
    """

    context = get_request_context()
    payload: dict[str, Any] = {
        "event": f"audit.{action}",
        "log_class": "audit",
        "audit": True,
        "action": action,
        "target_type": target_type,
        "target_id": _safe_scalar(target_id),
        "result": _normalize_result(str(result)),
        "actor_id": _safe_scalar(actor_id if actor_id is not None else context.get("actor_id")),
        "actor_role": _safe_scalar(actor_role if actor_role is not None else context.get("actor_role")),
        "request_id": _safe_scalar(context.get("request_id")),
        "client_ip": _safe_scalar(context.get("client_ip")),
        "before_summary": _safe_mapping(before_summary),
        "after_summary": _safe_mapping(after_summary),
        "reason": _safe_scalar(reason),
    }
    if extra:
        payload["audit_extra"] = _safe_mapping(extra)

    payload = {key: value for key, value in payload.items() if value is not None}
    record_business_action(action=action, result=payload["result"])
    _AUDIT_LOGGER.info("Audit event recorded", extra=payload)
