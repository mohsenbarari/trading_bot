# core/logging_config.py
"""Central logging setup for API, bot, and background workers.

Logs are emitted to stdout/stderr so Docker remains the transport and storage
boundary. The code only defines configurable setting names; real environment
values stay in the gitignored `.env` files on each server.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from starlette.requests import Request
from starlette.responses import Response

from core.request_context import clear_request_context, get_request_context, set_request_context

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

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "x_api_key",
    "x_dev_api_key",
    "otp",
    "code",
)

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_API_KEY_RE = re.compile(r"(?i)(api[_-]?key|x-api-key|x-dev-api-key|authorization)(\s*[:=]\s*)([^\s,;]+)")
_OTP_RE = re.compile(r"(?i)\b(otp|code)(\s*[:=]\s*)\d{4,8}\b")
_MOBILE_RE = re.compile(r"(?<!\d)(09\d{2})\d{4}(\d{3})(?!\d)")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mask_mobile(value: str) -> str:
    return _MOBILE_RE.sub(r"\1****\2", value)


def redact(value: Any) -> Any:
    """Best-effort redaction for secrets and common PII in log fields."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            key_str = str(key)
            if any(part in key_str.lower() for part in _SENSITIVE_KEY_PARTS):
                redacted[key_str] = "[REDACTED]"
            else:
                redacted[key_str] = redact(nested)
        return redacted
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        sanitized = _BEARER_RE.sub("Bearer [REDACTED]", value)
        sanitized = _JWT_RE.sub("[REDACTED_JWT]", sanitized)
        sanitized = _API_KEY_RE.sub(r"\1\2[REDACTED]", sanitized)
        sanitized = _OTP_RE.sub(r"\1\2[REDACTED]", sanitized)
        sanitized = _mask_mobile(sanitized)
        return sanitized
    return value


class RequestContextFilter(logging.Filter):
    """Inject request and actor context stored in contextvars."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        context = get_request_context()
        for key, value in context.items():
            if value is not None and not hasattr(record, key):
                setattr(record, key, value)
        if not hasattr(record, "service"):
            setattr(record, "service", SERVICE_NAME)
        return True


class JsonLogFormatter(logging.Formatter):
    """Dependency-free JSON formatter for application logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _utc_now_iso(),
            "level": record.levelname,
            "service": getattr(record, "service", SERVICE_NAME),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }

        try:
            from core.config import settings

            payload.setdefault("server_mode", getattr(settings, "server_mode", None))
            payload.setdefault("environment", getattr(settings, "environment", None))
            release_sha = getattr(settings, "release_sha", None)
            if release_sha:
                payload.setdefault("release_sha", release_sha)
        except Exception:
            pass

        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = redact(value)

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
    """Human-readable formatter for local debugging."""

    def format(self, record: logging.LogRecord) -> str:
        record.msg = redact(record.getMessage())
        record.args = ()
        return super().format(record)


def _coerce_log_level(raw_level: str | None) -> int:
    level_name = (raw_level or "INFO").upper()
    return getattr(logging, level_name, logging.INFO)


def configure_logging(service_name: str) -> None:
    """Configure root logging once for a service process."""
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

    # The app emits its own compact access log with request_id and duration.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    for inherited_logger in ("uvicorn", "uvicorn.error", "aiogram"):
        logging.getLogger(inherited_logger).handlers.clear()
        logging.getLogger(inherited_logger).propagate = True


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or None
    if request.client:
        return request.client.host
    return None


def install_request_logging_middleware(app: Any) -> None:
    """Attach request-id context and compact access logs to a FastAPI app."""
    request_logger = logging.getLogger("api.request")

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        set_request_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            client_ip=_client_ip(request),
        )
        start_time = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            request_logger.exception(
                "HTTP request failed",
                extra={
                    "event": "http.request.failed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            log_method = request_logger.warning if status_code >= 500 else request_logger.info
            log_method(
                "HTTP request completed",
                extra={
                    "event": "http.request.completed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            clear_request_context()
