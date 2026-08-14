"""Cross-process singleton ownership for the Telegram queue executor."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.db import engine as application_engine


TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY = 0x5447515545554531


class TelegramDeliveryQueueAlreadyOwnedError(RuntimeError):
    """Raised before any lane starts when another process owns the queue."""


class TelegramDeliveryQueueOwnerLeaseLostError(RuntimeError):
    """Raised before provider entry if the owning PostgreSQL session changed."""


@dataclass(slots=True)
class TelegramDeliveryQueueOwnerLease:
    connection: AsyncConnection
    backend_pid: int
    lock_key: int = TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY
    _check_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _closed: bool = False

    async def assert_held(self) -> None:
        """Fail closed if PostgreSQL replaced the session or lost its lock."""
        if self._closed:
            raise TelegramDeliveryQueueOwnerLeaseLostError(
                "telegram_delivery_queue_owner_lease_closed"
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
                raise TelegramDeliveryQueueOwnerLeaseLostError(
                    "telegram_delivery_queue_owner_lease_unavailable"
                ) from exc
            if int(backend_pid) != self.backend_pid:
                raise TelegramDeliveryQueueOwnerLeaseLostError(
                    "telegram_delivery_queue_owner_session_changed"
                )
            if lock_held is not True:
                raise TelegramDeliveryQueueOwnerLeaseLostError(
                    "telegram_delivery_queue_owner_lock_lost"
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


async def acquire_telegram_delivery_queue_owner(
    engine: AsyncEngine | None = None,
) -> TelegramDeliveryQueueOwnerLease:
    """Acquire one session-level owner shared by all bot roles and processes."""
    active_engine = engine or application_engine
    connection = await active_engine.connect()
    try:
        result = await connection.execute(
            text(
                "SELECT pg_try_advisory_lock(:lock_key), pg_backend_pid()"
            ),
            {"lock_key": TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY},
        )
        acquired, backend_pid = result.one()
        await connection.commit()
        if acquired is not True:
            raise TelegramDeliveryQueueAlreadyOwnedError(
                "telegram_delivery_queue_process_owner_already_active"
            )
        return TelegramDeliveryQueueOwnerLease(
            connection=connection,
            backend_pid=int(backend_pid),
        )
    except BaseException:
        await connection.close()
        raise


async def telegram_delivery_queue_owner_monitor_loop(
    lease: TelegramDeliveryQueueOwnerLease,
    *,
    interval_seconds: float = 1.0,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await lease.assert_held()
