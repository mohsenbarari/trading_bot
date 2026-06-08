"""FastAPI request correlation and sanitized access logging."""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from core.request_context import clear_request_context, set_request_context

REQUEST_ID_HEADER = "X-Request-ID"

_logger = logging.getLogger("api.request")

_SENSITIVE_PATH_PARTS = (
    "/auth",
    "/sessions",
    "/recovery",
    "/password",
    "/token",
    "/otp",
    "/upload",
    "/media",
)

_STATIC_PATH_PREFIXES = (
    "/assets/",
    "/favicon",
    "/manifest.webmanifest",
    "/sw.js",
    "/workbox-",
)

_STATIC_PATH_SUFFIXES = (
    ".css",
    ".js",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
)


def make_request_id(raw_request_id: str | None = None) -> str:
    candidate = (raw_request_id or "").strip()
    if not candidate:
        return str(uuid.uuid4())
    return candidate[:128]


def client_ip_from_request(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or None
    if request.client:
        return request.client.host
    return None


def is_sensitive_path(path: str) -> bool:
    lowered = path.lower()
    return any(part in lowered for part in _SENSITIVE_PATH_PARTS)


def should_log_request_path(path: str) -> bool:
    lowered = path.lower()
    if any(lowered.startswith(prefix) for prefix in _STATIC_PATH_PREFIXES):
        return False
    return not any(lowered.endswith(suffix) for suffix in _STATIC_PATH_SUFFIXES)


def request_log_extra(
    *,
    request_id: str,
    request: Request,
    status_code: int,
    duration_ms: float,
) -> dict[str, Any]:
    return {
        "event": "http.request.completed",
        "log_class": "access",
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "client_ip": client_ip_from_request(request),
        "sensitive_route": is_sensitive_path(request.url.path),
    }


def install_request_logging_middleware(app: Any) -> None:
    """Install request id propagation and sanitized access logs."""

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
        request_id = make_request_id(request.headers.get(REQUEST_ID_HEADER))
        path = request.url.path
        start_time = time.perf_counter()
        status_code = 500

        set_request_context(
            request_id=request_id,
            method=request.method,
            path=path,
            client_ip=client_ip_from_request(request),
            log_class="access",
        )

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            _logger.exception(
                "HTTP request failed",
                extra={
                    "event": "http.request.failed",
                    "log_class": "access",
                    "request_id": request_id,
                    "method": request.method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": client_ip_from_request(request),
                    "sensitive_route": is_sensitive_path(path),
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            if should_log_request_path(path):
                extra = request_log_extra(
                    request_id=request_id,
                    request=request,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
                log_method = _logger.warning if status_code >= 500 else _logger.info
                log_method("HTTP request completed", extra=extra)
            clear_request_context()
