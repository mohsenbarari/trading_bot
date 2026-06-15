from __future__ import annotations

from typing import Iterable

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis import pool
from core.services.session_service import force_clear_sessions
from models.session import SessionLoginRequest, SingleSessionRecoveryRequest, UserSession

try:
    import redis.asyncio as redis
except Exception:  # pragma: no cover - dependency exists in app runtime
    redis = None


async def redis_delete_keys(keys: Iterable[str]) -> int:
    if redis is None:
        return 0
    client = redis.Redis(connection_pool=pool, decode_responses=True)
    try:
        actual = []
        for key in sorted(set(keys)):
            if await client.exists(key):
                actual.append(key)
        if actual:
            await client.delete(*actual)
        return len(actual)
    finally:
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()
        else:  # pragma: no cover - compatibility fallback
            await client.close()


async def collect_login_limit_keys(user_id: int, mobile_number: str) -> list[str]:
    keys = [
        f"otp:{mobile_number}",
        f"otp_limit:{mobile_number}",
        f"sms_limit:{mobile_number}",
        f"banned:{mobile_number}",
    ]
    if redis is None:
        return keys
    client = redis.Redis(connection_pool=pool, decode_responses=True)
    try:
        async for key in client.scan_iter(f"session_req:{user_id}:*"):
            keys.append(key)
    finally:
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()
        else:  # pragma: no cover - compatibility fallback
            await client.close()
    return keys


async def reset_user_session_state(
    db: AsyncSession,
    *,
    user_id: int,
    mobile_number: str,
    delete_session_rows: bool = True,
    clear_login_limits: bool = True,
) -> dict[str, int | bool]:
    cleared = await force_clear_sessions(db, user_id)

    login_requests_result = await db.execute(
        delete(SessionLoginRequest).where(SessionLoginRequest.user_id == user_id)
    )
    recovery_requests_result = await db.execute(
        delete(SingleSessionRecoveryRequest).where(SingleSessionRecoveryRequest.user_id == user_id)
    )

    session_rows_deleted = 0
    if delete_session_rows:
        session_rows_result = await db.execute(delete(UserSession).where(UserSession.user_id == user_id))
        session_rows_deleted = int(getattr(session_rows_result, "rowcount", 0) or 0)

    await db.commit()

    redis_deleted = 0
    if clear_login_limits:
        redis_deleted = await redis_delete_keys(await collect_login_limit_keys(user_id, mobile_number))

    return {
        "revoked_active_sessions": int(cleared),
        "deleted_login_requests": int(getattr(login_requests_result, "rowcount", 0) or 0),
        "deleted_recovery_requests": int(getattr(recovery_requests_result, "rowcount", 0) or 0),
        "deleted_session_rows": session_rows_deleted,
        "deleted_redis_keys": int(redis_deleted),
        "delete_session_rows": bool(delete_session_rows),
        "clear_login_limits": bool(clear_login_limits),
    }
