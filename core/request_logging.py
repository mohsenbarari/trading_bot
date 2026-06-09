"""FastAPI request correlation and sanitized access logging."""
from __future__ import annotations

import logging
import ipaddress
import re
import time
import uuid
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from core.error_tracking import capture_exception
from core.metrics import normalize_http_route, record_http_request
from core.request_context import clear_request_context, set_request_context

REQUEST_ID_HEADER = "X-Request-ID"

_logger = logging.getLogger("api.request")

_SENSITIVE_PATH_PARTS = (
    "/auth",
    "/files",
    "/invitations",
    "/sessions",
    "/recovery",
    "/password",
    "/token",
    "/otp",
    "/upload",
    "/media",
)

_TOKENISH_PATH_SEGMENT_RE = re.compile(r"^(?=.{12,}$)(?=.*(?:\d|[._~+=%]))[A-Za-z0-9._~+=%-]+$")
_REDACT_NEXT_SEGMENT_AFTER = frozenset(
    {
        "accept",
        "files",
        "media",
        "otp",
        "password",
        "recovery",
        "reset",
        "upload-sessions",
        "verify",
    }
)
_SAFE_SEGMENTS_AFTER_SECRET_MARKER = frozenset(
    {
        "approve",
        "cancel",
        "chunk",
        "finalize",
        "identity",
        "pending",
        "reject",
        "request-identity",
        "status",
    }
)
_REDACTED_PATH_SEGMENT = "[REDACTED]"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

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
    if not candidate or not _REQUEST_ID_RE.fullmatch(candidate):
        return str(uuid.uuid4())
    return candidate


@lru_cache(maxsize=1)
def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    try:
        from core.config import settings

        raw_value = getattr(settings, "trusted_proxy_cidrs", "") or ""
    except Exception:
        raw_value = ""
    networks: list[ipaddress._BaseNetwork] = []
    for item in raw_value.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _parse_ip_address(value: str | None) -> ipaddress._BaseAddress | None:
    if not value:
        return None
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted_proxy(client_host: str | None) -> bool:
    client_ip = _parse_ip_address(client_host)
    if client_ip is None:
        return False
    return any(client_ip in network for network in _trusted_proxy_networks())


def client_ip_from_request(request: Request) -> str | None:
    direct_host = request.client.host if request.client else None
    if _is_trusted_proxy(direct_host):
        real_ip = _parse_ip_address(request.headers.get("x-real-ip"))
        if real_ip is not None:
            return str(real_ip)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            for item in forwarded_for.split(","):
                forwarded_ip = _parse_ip_address(item)
                if forwarded_ip is not None:
                    return str(forwarded_ip)
    if direct_host:
        return direct_host
    return None


def is_sensitive_path(path: str) -> bool:
    lowered = path.lower()
    return any(part in lowered for part in _SENSITIVE_PATH_PARTS)


def redact_sensitive_path_segments(path: str) -> str:
    segments = path.split("/")
    redacted: list[str] = []
    redact_next_segment = False
    for segment in segments:
        if not segment:
            redacted.append(segment)
            continue
        if segment.startswith("{") and segment.endswith("}"):
            redacted.append(segment)
            redact_next_segment = False
            continue
        lowered = segment.lower()
        if (
            redact_next_segment
            and lowered not in _SAFE_SEGMENTS_AFTER_SECRET_MARKER
        ) or _TOKENISH_PATH_SEGMENT_RE.match(segment):
            redacted.append(_REDACTED_PATH_SEGMENT)
        else:
            redacted.append(segment)
        redact_next_segment = lowered in _REDACT_NEXT_SEGMENT_AFTER
    return "/".join(redacted)


def should_log_request_path(path: str) -> bool:
    lowered = path.lower()
    if any(lowered.startswith(prefix) for prefix in _STATIC_PATH_PREFIXES):
        return False
    return not any(lowered.endswith(suffix) for suffix in _STATIC_PATH_SUFFIXES)


def request_log_extra(
    *,
    request_id: str,
    request: Request,
    path: str,
    status_code: int,
    duration_ms: float,
) -> dict[str, Any]:
    return {
        "event": "http.request.completed",
        "log_class": "access",
        "request_id": request_id,
        "method": request.method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "client_ip": client_ip_from_request(request),
        "sensitive_route": is_sensitive_path(request.url.path),
    }


def request_route_template(request: Request, path: str) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    return normalize_http_route(route_path or path)


def safe_request_log_path(request: Request, path: str) -> str:
    route_path = request_route_template(request, path)
    if is_sensitive_path(path):
        return redact_sensitive_path_segments(route_path)
    return route_path


def install_request_logging_middleware(app: Any) -> None:
    """Install request id propagation and sanitized access logs."""

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
        request_id = make_request_id(request.headers.get(REQUEST_ID_HEADER))
        path = request.url.path
        start_time = time.perf_counter()
        status_code = 500
        initial_path = (
            redact_sensitive_path_segments(normalize_http_route(path))
            if is_sensitive_path(path)
            else normalize_http_route(path)
        )

        set_request_context(
            request_id=request_id,
            method=request.method,
            path=initial_path,
            client_ip=client_ip_from_request(request),
            log_class="access",
        )

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            safe_path = safe_request_log_path(request, path)
            set_request_context(path=safe_path)
            error_id = capture_exception(
                exc,
                source="api.request",
                extra={
                    "method": request.method,
                    "path": safe_path,
                    "status_code": status_code,
                    "sensitive_route": is_sensitive_path(path),
                },
            )
            _logger.exception(
                "HTTP request failed",
                extra={
                    "event": "http.request.failed",
                    "log_class": "access",
                    "request_id": request_id,
                    "method": request.method,
                    "path": safe_path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": client_ip_from_request(request),
                    "sensitive_route": is_sensitive_path(path),
                    "error_fingerprint": error_id,
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            route_template = request_route_template(request, path)
            safe_path = safe_request_log_path(request, path)
            set_request_context(path=safe_path)
            record_http_request(
                method=request.method,
                route=route_template,
                status_code=status_code,
                duration_ms=duration_ms,
            )
            if should_log_request_path(path):
                extra = request_log_extra(
                    request_id=request_id,
                    request=request,
                    path=safe_path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
                log_method = _logger.warning if status_code >= 500 else _logger.info
                log_method("HTTP request completed", extra=extra)
            clear_request_context()
