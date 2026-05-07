"""Internal cross-server forwarding for authoritative trade execution."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Tuple

import httpx

from core.config import settings
from core.server_routing import current_server, peer_server_url_for


def _json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def sign_internal_payload(body: str, timestamp: int) -> str:
    api_key = settings.sync_api_key or ""
    message = f"{timestamp}:{body}"
    return hmac.new(api_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_internal_signature(body: bytes, timestamp: str | None, signature: str | None, api_key: str | None) -> bool:
    if not settings.sync_api_key or api_key != settings.sync_api_key or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > 60:
        return False
    expected = sign_internal_payload(body.decode(), ts)
    return hmac.compare_digest(expected, signature)


async def forward_trade_to_home_server(target_server: str, payload: dict[str, Any]) -> Tuple[int, Any]:
    target_url = peer_server_url_for(target_server)
    if not target_url:
        return 503, {"detail": "سرور مرجع معامله در دسترس نیست."}

    body = _json_body(payload)
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.sync_api_key or "",
        "X-Timestamp": str(timestamp),
        "X-Signature": sign_internal_payload(body, timestamp),
        "X-Source-Server": current_server(),
    }

    try:
        async with httpx.AsyncClient(timeout=settings.trade_forward_timeout_seconds, verify=False) as client:
            response = await client.post(
                f"{target_url}/api/trades/internal/execute",
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        return 504, {"detail": "مهلت ارتباط با سرور مرجع معامله تمام شد. لطفاً دوباره تلاش کنید."}
    except httpx.RequestError:
        return 503, {"detail": "ارتباط با سرور مرجع معامله برقرار نشد. لطفاً دوباره تلاش کنید."}

    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"detail": response.text or "پاسخ نامعتبر از سرور مرجع معامله"}
