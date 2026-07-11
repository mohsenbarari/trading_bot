"""Security guard for public invitation lookup and validation routes."""

from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request, Response

from core.config import settings
from core.redis import get_redis_client
from core.request_logging import client_ip_from_request


PUBLIC_INVITATION_RATE_WINDOW_SECONDS = 60
PUBLIC_INVITATION_RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""
PUBLIC_INVITATION_SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def public_invitation_http_exception(*, status_code: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers=dict(PUBLIC_INVITATION_SECURITY_HEADERS),
    )


def _rate_limit_key(request: Request) -> str:
    client_ip = client_ip_from_request(request) or "unknown"
    route = request.scope.get("route")
    route_path = str(getattr(route, "path", "") or request.url.path)
    material = f"{client_ip}:{route_path}".encode("utf-8", errors="replace")
    digest = hashlib.sha256(material).hexdigest()
    return f"invitation:public-rate:{digest}"


async def enforce_public_invitation_access(request: Request, response: Response) -> None:
    security_headers = dict(PUBLIC_INVITATION_SECURITY_HEADERS)
    response.headers.update(security_headers)

    limit = max(int(getattr(settings, "invitation_public_rate_limit_per_minute", 30) or 0), 0)
    if limit == 0:
        raise HTTPException(
            status_code=503,
            detail="سرویس بررسی دعوت‌نامه موقتاً در دسترس نیست",
            headers=security_headers,
        )

    key = _rate_limit_key(request)
    try:
        redis_client = get_redis_client()
        count = int(
            await redis_client.eval(
                PUBLIC_INVITATION_RATE_LIMIT_SCRIPT,
                1,
                key,
                PUBLIC_INVITATION_RATE_WINDOW_SECONDS,
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="سرویس بررسی دعوت‌نامه موقتاً در دسترس نیست",
            headers=security_headers,
        ) from exc

    if count > limit:
        rate_limited_headers = {
            **security_headers,
            "Retry-After": str(PUBLIC_INVITATION_RATE_WINDOW_SECONDS),
        }
        response.headers.update(rate_limited_headers)
        raise HTTPException(
            status_code=429,
            detail="تعداد درخواست‌ها بیش از حد مجاز است",
            headers=rate_limited_headers,
        )
