"""Scrubbed error tracking for unexpected exceptions."""

from __future__ import annotations

import hashlib
import logging
import traceback
from types import TracebackType
from typing import Any

from core.log_redaction import redact
from core.request_context import get_request_context


_logger = logging.getLogger("error.tracking")


def _runtime_metadata() -> dict[str, Any]:
    try:
        from core.config import settings

        return {
            "environment": getattr(settings, "environment", None),
            "release_sha": getattr(settings, "release_sha", None),
            "server_mode": getattr(settings, "server_mode", None),
        }
    except Exception:
        return {}


def _project_frames(tb: TracebackType | None, *, limit: int = 8) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for frame in traceback.extract_tb(tb or None):
        filename = frame.filename
        if "/site-packages/" in filename or "/dist-packages/" in filename:
            continue
        frames.append(
            {
                "file": filename.rsplit("/", 1)[-1],
                "function": frame.name,
                "line": frame.lineno,
            }
        )
    return frames[-limit:]


def error_fingerprint(exc: BaseException, *, source: str | None = None) -> str:
    frames = _project_frames(exc.__traceback__)
    frame_key = "|".join(f"{item['file']}:{item['function']}" for item in frames)
    raw = f"{source or 'app'}|{type(exc).__module__}.{type(exc).__name__}|{frame_key}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def capture_exception(
    exc: BaseException,
    *,
    source: str,
    handled: bool = False,
    severity: str = "error",
    extra: dict[str, Any] | None = None,
) -> str:
    """Record a scrubbed grouped error event and optionally forward to Sentry.

    The event is emitted to structured logs first. If ``sentry_sdk`` is present
    and configured by deployment code, a minimal sanitized event is forwarded.
    """

    context = get_request_context()
    fingerprint = error_fingerprint(exc, source=source)
    frames = _project_frames(exc.__traceback__)
    payload = {
        "event": "error.exception.captured",
        "log_class": "error",
        "error_fingerprint": fingerprint,
        "error_source": source,
        "handled": handled,
        "severity": severity,
        "exception_type": f"{type(exc).__module__}.{type(exc).__name__}",
        "exception_message": redact(str(exc)),
        "frames": frames,
        "request_id": context.get("request_id"),
        "actor_id": context.get("actor_id"),
        "actor_role": context.get("actor_role"),
        "path": context.get("path"),
        "method": context.get("method"),
        "job_name": context.get("job_name"),
        "run_id": context.get("run_id"),
        "bot_event_type": context.get("bot_event_type"),
        "service": context.get("service"),
        "extra": redact(extra or {}),
        **_runtime_metadata(),
    }
    payload = {key: value for key, value in payload.items() if value not in (None, {}, [])}
    _logger.error("Exception captured", extra=payload)

    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.set_tag("error_source", source)
        sentry_sdk.set_tag("error_fingerprint", fingerprint)
        if context.get("request_id"):
            sentry_sdk.set_tag("request_id", context.get("request_id"))
        if context.get("actor_role"):
            sentry_sdk.set_tag("actor_role", context.get("actor_role"))
        sentry_sdk.capture_exception(exc)
    except Exception:
        pass

    return fingerprint
