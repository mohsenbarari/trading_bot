"""Runtime gates for production full-matrix isolation mode.

The feature is intentionally dormant unless the Redis enabled key is set. It
does not revoke sessions or mutate users; it only blocks WebApp access and
suppresses delivery side effects for users outside the allowed test cohort.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.server_routing import SERVER_IRAN, normalize_server
from models.user import User

logger = logging.getLogger(__name__)

REDIS_KEY_ENABLED = "production_test_isolation:enabled"
REDIS_KEY_REASON = "production_test_isolation:reason"
REDIS_KEY_ALLOW_USER_IDS = "production_test_isolation:allow_user_ids"
REDIS_KEY_ALLOW_ACCOUNT_PREFIXES = "production_test_isolation:allow_account_prefixes"
REDIS_KEY_ALLOW_MOBILE_PREFIXES = "production_test_isolation:allow_mobile_prefixes"

TRUTHY_VALUES = {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True, slots=True)
class ProductionTestIsolationConfig:
    enabled: bool
    reason: str | None
    allow_user_ids: frozenset[int]
    allow_account_prefixes: tuple[str, ...]
    allow_mobile_prefixes: tuple[str, ...]


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _normalize_prefixes(values: Iterable[Any]) -> tuple[str, ...]:
    prefixes = sorted({prefix for raw in values if (prefix := _decode(raw).strip())})
    return tuple(prefixes)


def _normalize_user_ids(values: Iterable[Any]) -> frozenset[int]:
    normalized: set[int] = set()
    for raw in values:
        try:
            value = int(_decode(raw).strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            normalized.add(value)
    return frozenset(normalized)


def _runtime_can_apply() -> bool:
    return normalize_server(getattr(settings, "server_mode", None)) == SERVER_IRAN


def _get_redis_client():
    from core.redis import get_redis_client

    return get_redis_client()


async def get_isolation_config() -> ProductionTestIsolationConfig:
    if not _runtime_can_apply():
        return ProductionTestIsolationConfig(False, None, frozenset(), (), ())

    try:
        redis_client = _get_redis_client()
        enabled_raw = await redis_client.get(REDIS_KEY_ENABLED)
        enabled = _decode(enabled_raw).strip().lower() in TRUTHY_VALUES
        if not enabled:
            return ProductionTestIsolationConfig(False, None, frozenset(), (), ())

        reason = _decode(await redis_client.get(REDIS_KEY_REASON)).strip() or None
        allow_user_ids = _normalize_user_ids(await redis_client.smembers(REDIS_KEY_ALLOW_USER_IDS))
        allow_account_prefixes = _normalize_prefixes(await redis_client.smembers(REDIS_KEY_ALLOW_ACCOUNT_PREFIXES))
        allow_mobile_prefixes = _normalize_prefixes(await redis_client.smembers(REDIS_KEY_ALLOW_MOBILE_PREFIXES))
        return ProductionTestIsolationConfig(
            True,
            reason,
            allow_user_ids,
            allow_account_prefixes,
            allow_mobile_prefixes,
        )
    except RuntimeError as exc:
        logger.debug(
            "Production test isolation config unavailable before Redis initialization",
            extra={
                "event": "production_test_isolation.redis_uninitialized",
                "error_class": type(exc).__name__,
            },
        )
        return ProductionTestIsolationConfig(False, None, frozenset(), (), ())
    except Exception:
        logger.exception(
            "Failed to read production test isolation config; leaving request ungated",
            extra={"event": "production_test_isolation.config_failed"},
        )
        return ProductionTestIsolationConfig(False, None, frozenset(), (), ())


async def is_isolation_enabled() -> bool:
    return (await get_isolation_config()).enabled


def user_matches_isolation_allowlist(user: User | object | None, config: ProductionTestIsolationConfig) -> bool:
    if user is None or not config.enabled:
        return False

    try:
        user_id = int(getattr(user, "id"))
    except (TypeError, ValueError):
        user_id = 0
    if user_id in config.allow_user_ids:
        return True

    account_name = str(getattr(user, "account_name", "") or "")
    if account_name and any(account_name.startswith(prefix) for prefix in config.allow_account_prefixes):
        return True

    mobile_number = str(getattr(user, "mobile_number", "") or "")
    if mobile_number and any(mobile_number.startswith(prefix) for prefix in config.allow_mobile_prefixes):
        return True

    return False


async def load_user_for_isolation(db: AsyncSession, user_id: int | None) -> User | None:
    if not user_id:
        return None
    try:
        return await db.get(User, int(user_id))
    except Exception:
        logger.exception(
            "Failed to load user for production test isolation check",
            extra={"event": "production_test_isolation.user_load_failed", "user_id": user_id},
        )
        return None


async def is_user_allowed_in_isolation(db: AsyncSession, user: User | object | None) -> bool:
    config = await get_isolation_config()
    if not config.enabled:
        return True
    return user_matches_isolation_allowlist(user, config)


async def is_user_id_allowed_in_isolation(db: AsyncSession, user_id: int | None) -> bool:
    config = await get_isolation_config()
    if not config.enabled:
        return True
    user = await load_user_for_isolation(db, user_id)
    return user_matches_isolation_allowlist(user, config)


async def should_block_webapp_user(db: AsyncSession, user: User | object | None) -> bool:
    config = await get_isolation_config()
    if not config.enabled:
        return False
    return not user_matches_isolation_allowlist(user, config)


async def should_suppress_user_notification(db: AsyncSession, user_id: int | None) -> bool:
    config = await get_isolation_config()
    if not config.enabled:
        return False
    user = await load_user_for_isolation(db, user_id)
    return not user_matches_isolation_allowlist(user, config)


async def should_suppress_web_push_for_user(db: AsyncSession, user_id: int | None) -> bool:
    return await should_suppress_user_notification(db, user_id)


def isolation_block_payload(reason: str | None = None) -> dict[str, str | bool | None]:
    return {
        "detail": "WEBAPP_TEMPORARILY_UNAVAILABLE",
        "temporary": True,
        "reason": reason,
    }
