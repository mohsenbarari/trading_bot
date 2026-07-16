"""Persist Telegram-owned usernames observed by the foreign bot."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from models.user import User


logger = logging.getLogger(__name__)

USERNAME_REFRESH_STALE_RETRIES = 2


def normalize_observed_telegram_username(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("telegram username observation must be a string or null")
    normalized = value.strip().lstrip("@").strip()
    return normalized or None


async def _reload_linked_user(
    db: AsyncSession,
    *,
    user_id: int,
    telegram_id: int,
) -> User | None:
    result = await db.execute(
        select(User)
        .where(
            User.id == user_id,
            User.telegram_id == telegram_id,
            User.is_deleted.is_(False),
        )
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def refresh_observed_telegram_username(
    db: AsyncSession,
    *,
    user: User,
    telegram_id: int,
    observed_username: str | None,
) -> User:
    """Update only a changed Telegram username without blocking bot usage on failure."""
    normalized_username = normalize_observed_telegram_username(observed_username)
    user_id = int(user.id)
    normalized_telegram_id = int(telegram_id)
    candidate = user

    for attempt in range(USERNAME_REFRESH_STALE_RETRIES + 1):
        current_username = getattr(candidate, "username", None)
        if current_username == normalized_username:
            return candidate

        candidate.username = normalized_username
        try:
            await db.commit()
            return candidate
        except StaleDataError:
            await db.rollback()
            candidate = await _reload_linked_user(
                db,
                user_id=user_id,
                telegram_id=normalized_telegram_id,
            )
            if candidate is None:
                logger.warning(
                    "Telegram username refresh lost its linked user during a stale retry",
                    extra={
                        "event": "telegram.username_refresh_user_missing",
                        "user_id": user_id,
                        "telegram_id": normalized_telegram_id,
                    },
                )
                return user
            if attempt >= USERNAME_REFRESH_STALE_RETRIES:
                logger.warning(
                    "Telegram username refresh exhausted stale retries",
                    extra={
                        "event": "telegram.username_refresh_stale_exhausted",
                        "user_id": user_id,
                        "telegram_id": normalized_telegram_id,
                    },
                )
                return candidate
        except Exception as exc:
            try:
                await db.rollback()
            except Exception:
                logger.warning(
                    "Telegram username refresh rollback failed",
                    extra={
                        "event": "telegram.username_refresh_rollback_failed",
                        "user_id": user_id,
                        "error_type": type(exc).__name__,
                    },
                )
            logger.warning(
                "Telegram username refresh failed without blocking the bot interaction",
                extra={
                    "event": "telegram.username_refresh_failed",
                    "user_id": user_id,
                    "telegram_id": normalized_telegram_id,
                    "error_type": type(exc).__name__,
                },
            )
            try:
                refreshed = await _reload_linked_user(
                    db,
                    user_id=user_id,
                    telegram_id=normalized_telegram_id,
                )
            except Exception:
                refreshed = None
            return refreshed or user

    return candidate
