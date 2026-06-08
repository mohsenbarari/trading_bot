"""Central logging setup for API, bot, and background workers."""
from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

from core.log_redaction import REDACTED, is_sensitive_key, redact
from core.request_context import get_request_context

SERVICE_NAME = "app"

_RESERVED_RECORD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _coerce_log_level(raw_level: str | None) -> int:
    level_name = (raw_level or "INFO").upper()
    return getattr(logging, level_name, logging.INFO)


def _runtime_metadata() -> dict[str, Any]:
    try:
        from core.config import settings

        return {
            "server_mode": getattr(settings, "server_mode", None),
            "environment": getattr(settings, "environment", None),
            "release_sha": getattr(settings, "release_sha", None),
        }
    except Exception:
        return {}


class RequestContextFilter(logging.Filter):
    """Inject contextvars and service metadata into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in get_request_context().items():
            if value is not None and not hasattr(record, key):
                setattr(record, key, value)
        if not hasattr(record, "service"):
            setattr(record, "service", SERVICE_NAME)
        return True


class JsonLogFormatter(logging.Formatter):
    """Dependency-free JSON formatter with built-in redaction."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _utc_now_iso(),
            "level": record.levelname,
            "service": getattr(record, "service", SERVICE_NAME),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }

        for key, value in _runtime_metadata().items():
            if value:
                payload[key] = value

        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_") or key in payload:
                continue
            payload[key] = REDACTED if is_sensitive_key(key) else redact(value)

        if record.exc_info:
            payload["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": redact(str(record.exc_info[1])) if record.exc_info[1] else None,
                "stacktrace": redact("".join(traceback.format_exception(*record.exc_info))),
            }

        if record.stack_info:
            payload["stack_info"] = redact(record.stack_info)

        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


class TextLogFormatter(logging.Formatter):
    """Human-readable local formatter that still redacts sensitive values."""

    def format(self, record: logging.LogRecord) -> str:
        original_msg = record.msg
        original_args = record.args
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
            return super().format(record)
        finally:
            record.msg = original_msg
            record.args = original_args


def configure_logging(service_name: str) -> None:
    """Configure root logging for one service process."""
    global SERVICE_NAME
    SERVICE_NAME = service_name

    try:
        from core.config import settings

        log_level = _coerce_log_level(getattr(settings, "log_level", "INFO"))
        log_format = (getattr(settings, "log_format", "json") or "json").lower()
    except Exception:
        log_level = logging.INFO
        log_format = "json"

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.addFilter(RequestContextFilter())
    if log_format == "text":
        handler.setFormatter(
            TextLogFormatter(
                fmt="%(asctime)s %(levelname)s [%(service)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
    else:
        handler.setFormatter(JsonLogFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    for noisy_logger in ("uvicorn.access",):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    for inherited_logger in ("uvicorn", "uvicorn.error", "aiogram"):
        inherited = logging.getLogger(inherited_logger)
        inherited.handlers.clear()
        inherited.propagate = True

    if not os.environ.get("ERROR_TRACKING_DSN"):
        return

    try:
        from core.config import settings
        dsn = getattr(settings, "error_tracking_dsn", None)
        if dsn:
            import sentry_sdk  # type: ignore

            sentry_sdk.init(
                dsn=dsn,
                environment=getattr(settings, "environment", None),
                release=getattr(settings, "release_sha", None),
                traces_sample_rate=0.0,
                sample_rate=float(getattr(settings, "error_tracking_sample_rate", 1.0) or 1.0),
                send_default_pii=False,
            )
    except Exception:
        logging.getLogger(__name__).debug("Error tracking SDK initialization skipped", exc_info=True)
