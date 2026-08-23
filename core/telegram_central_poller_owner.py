"""Cross-process singleton for the central Telegram bot poller.

This lock is independent from the global queue-owner lease. `all` and
`primary` take it before central getUpdates. `executor` never acquires it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.db import engine as application_engine


TELEGRAM_CENTRAL_POLLER_LOCK_KEY = 0x54474250524D5259


class TelegramCentralPollerAlreadyOwnedError(RuntimeError):
    """Raised before central polling when another process owns that surface."""


class TelegramCentralPollerLeaseLostError(RuntimeError):
    """Raised if the central-poller PostgreSQL session changed."""


TELEGRAM_CENTRAL_POLLER_MONITOR_INTERVAL_SECONDS = 1.0


@dataclass(slots=True)
class TelegramCentralPollerLease:
    connection: AsyncConnection
    backend_pid: int
    lock_key: int = TELEGRAM_CENTRAL_POLLER_LOCK_KEY
    _check_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _closed: bool = False

    async def assert_held(self) -> None:
        if self._closed:
            raise TelegramCentralPollerLeaseLostError(
                "telegram_central_poller_lease_closed"
            )
        async with self._check_lock:
            try:
                class_id = (self.lock_key >> 32) & 0xFFFFFFFF
                object_id = self.lock_key & 0xFFFFFFFF
                result = await self.connection.execute(
                    text(
                        "SELECT pg_backend_pid(), EXISTS ("
                        "SELECT 1 FROM pg_locks "
                        "WHERE locktype = 'advisory' "
                        "AND pid = pg_backend_pid() "
                        "AND classid = :class_id "
                        "AND objid = :object_id "
                        "AND objsubid = 1 AND granted"
                        ")"
                    ),
                    {"class_id": class_id, "object_id": object_id},
                )
                backend_pid, lock_held = result.one()
            except Exception as exc:
                raise TelegramCentralPollerLeaseLostError(
                    "telegram_central_poller_lease_unavailable"
                ) from exc
            if int(backend_pid) != self.backend_pid:
                raise TelegramCentralPollerLeaseLostError(
                    "telegram_central_poller_session_changed"
                )
            if lock_held is not True:
                raise TelegramCentralPollerLeaseLostError(
                    "telegram_central_poller_lock_lost"
                )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": self.lock_key},
            )
            await self.connection.commit()
        finally:
            await self.connection.close()


async def acquire_telegram_central_poller_owner(
    engine: AsyncEngine | None = None,
) -> TelegramCentralPollerLease:
    """Acquire the central-bot polling lock before start_polling."""
    active_engine = engine or application_engine
    connection = await active_engine.connect()
    try:
        result = await connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_key), pg_backend_pid()"),
            {"lock_key": TELEGRAM_CENTRAL_POLLER_LOCK_KEY},
        )
        acquired, backend_pid = result.one()
        await connection.commit()
        if acquired is not True:
            raise TelegramCentralPollerAlreadyOwnedError(
                "telegram_central_poller_already_active"
            )
        return TelegramCentralPollerLease(
            connection=connection,
            backend_pid=int(backend_pid),
        )
    except BaseException:
        await connection.close()
        raise


async def telegram_central_poller_owner_is_held(
    engine: AsyncEngine | None = None,
) -> bool:
    """Read whether the central-poller advisory lock is granted.

    This does not acquire the lock. Executor health and deploy postchecks
    use it without becoming a second central poller.
    """
    active_engine = engine or application_engine
    class_id = (TELEGRAM_CENTRAL_POLLER_LOCK_KEY >> 32) & 0xFFFFFFFF
    object_id = TELEGRAM_CENTRAL_POLLER_LOCK_KEY & 0xFFFFFFFF
    async with active_engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_locks "
                "WHERE locktype = 'advisory' "
                "AND classid = :class_id "
                "AND objid = :object_id "
                "AND objsubid = 1 AND granted"
                ")"
            ),
            {"class_id": class_id, "object_id": object_id},
        )
        return result.scalar() is True


async def telegram_central_poller_owner_monitor_loop(
    lease: TelegramCentralPollerLease,
    *,
    interval_seconds: float = TELEGRAM_CENTRAL_POLLER_MONITOR_INTERVAL_SECONDS,
) -> None:
    """Fail closed if the central-poller lease is lost after startup."""
    interval = max(0.05, float(interval_seconds))
    while True:
        await asyncio.sleep(interval)
        await lease.assert_held()
