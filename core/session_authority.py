"""Cross-server session authority checks.

Session rows are intentionally local to their home server. This module lets an
edge server ask the authoritative home server whether a user still has active
sessions before creating OTP/login state locally.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.server_routing import current_server, normalize_server, peer_server_url_for
from core.services.session_service import deactivate_session, get_active_sessions, promote_next_primary
from core.sync_transport import assert_runtime_sync_transport_allowed, runtime_sync_tls_verify_setting
from core.trade_forwarding import sign_internal_payload
from core.utils import utc_now

logger = logging.getLogger(__name__)

ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE = (
    "شما دارای نشست فعال هستید. ابتدا از حساب کاربری خود خارج شوید و سپس مجدد درخواست ورود دهید."
)
SESSION_AUTHORITY_UNAVAILABLE_MESSAGE = (
    "امکان بررسی نشست فعال روی سرور مرجع وجود ندارد. لطفاً کمی بعد دوباره تلاش کنید."
)


def _json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def _is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    now = utc_now()
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now


async def deactivate_expired_active_sessions(db: AsyncSession, user_id: int) -> int:
    """Deactivate expired active sessions before counting authoritative state."""
    sessions = await get_active_sessions(db, user_id)
    expired_count = 0
    primary_was_expired = False

    for session in sessions:
        if not _is_expired(getattr(session, "expires_at", None)):
            continue
        primary_was_expired = primary_was_expired or bool(getattr(session, "is_primary", False))
        await deactivate_session(db, session)
        expired_count += 1

    if primary_was_expired:
        await promote_next_primary(db, user_id)

    return expired_count


async def inspect_local_session_authority(db: AsyncSession, user) -> dict[str, Any]:
    """Return authoritative local active-session state for a user."""
    user_id = int(getattr(user, "id"))
    expired_count = await deactivate_expired_active_sessions(db, user_id)
    if expired_count:
        await db.commit()

    active_sessions = await get_active_sessions(db, user_id)
    active_count = len(active_sessions)
    home_server = normalize_server(getattr(user, "home_server", None), current_server())
    return {
        "user_id": user_id,
        "home_server": home_server,
        "source_server": current_server(),
        "active_session_count": active_count,
        "expired_sessions_deactivated": expired_count,
        "can_transfer_home": active_count == 0,
    }


async def fetch_remote_session_authority(target_server: str, user_id: int) -> tuple[int, dict[str, Any]]:
    """Ask a remote home server whether a user has active sessions."""
    target_url = peer_server_url_for(target_server)
    if not target_url:
        return 503, {"detail": SESSION_AUTHORITY_UNAVAILABLE_MESSAGE}

    payload = {
        "user_id": int(user_id),
        "source_server": current_server(),
    }
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
        assert_runtime_sync_transport_allowed()
        async with httpx.AsyncClient(
            timeout=settings.trade_forward_timeout_seconds,
            verify=runtime_sync_tls_verify_setting(),
        ) as client:
            response = await client.post(
                f"{target_url}/api/sessions/internal/authority-check",
                content=body,
                headers=headers,
            )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.warning(
            "Remote session authority check failed",
            extra={
                "event": "session.authority.remote_check_failed",
                "target_server": target_server,
                "user_id_hash": hashlib.sha256(str(user_id).encode()).hexdigest()[:16],
                "error_type": type(exc).__name__,
            },
        )
        return 503, {"detail": SESSION_AUTHORITY_UNAVAILABLE_MESSAGE}

    try:
        body_data = response.json()
    except ValueError:
        body_data = {"detail": response.text or SESSION_AUTHORITY_UNAVAILABLE_MESSAGE}
    return response.status_code, body_data if isinstance(body_data, dict) else {"detail": body_data}


async def assert_login_allowed_for_server(
    db: AsyncSession,
    user,
    *,
    requested_server: str,
) -> None:
    """Fail closed when login is attempted away from the user's session home."""
    _ = db  # reserved for future local policy checks; keeps call sites explicit
    user_home = normalize_server(getattr(user, "home_server", None), requested_server)
    requested_home = normalize_server(requested_server)
    if user_home == requested_home:
        return

    status_code, body = await fetch_remote_session_authority(user_home, int(getattr(user, "id")))
    if status_code != 200:
        raise HTTPException(status_code=503, detail=SESSION_AUTHORITY_UNAVAILABLE_MESSAGE)

    try:
        active_count = int(body.get("active_session_count") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=503, detail=SESSION_AUTHORITY_UNAVAILABLE_MESSAGE)
    if active_count > 0:
        raise HTTPException(status_code=409, detail=ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE)

    if body.get("can_transfer_home") is not True:
        raise HTTPException(status_code=503, detail=SESSION_AUTHORITY_UNAVAILABLE_MESSAGE)


def verify_session_authority_signature(
    body: bytes,
    *,
    timestamp: str | None,
    signature: str | None,
    api_key: str | None,
) -> bool:
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
