"""Transactional wake-up hints for the durable Telegram delivery queue.

PostgreSQL delivers ``pg_notify`` only after the surrounding transaction
commits.  Notifications are therefore safe latency hints: the outbox and queue
tables remain authoritative, and bounded polling still recovers a lost hint or
listener outage.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import engine
from core.services.telegram_delivery_queue_service import (
    SUPPORTED_TELEGRAM_BOT_IDENTITIES,
)


logger = logging.getLogger(__name__)

TELEGRAM_NOTIFICATION_OUTBOX_WAKEUP_CHANNEL = (
    "telegram_notification_outbox_ready_v1"
)
TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL = "telegram_delivery_queue_ready_v1"
_NOTIFICATION_OUTBOX_PAYLOAD = "ready"
_LISTENER_HEALTH_INTERVAL_SECONDS = 30.0
_LISTENER_RETRY_MIN_SECONDS = 0.5
_LISTENER_RETRY_MAX_SECONDS = 5.0

_notification_outbox_event = asyncio.Event()
_delivery_queue_events: dict[str, asyncio.Event] = {}


def notification_outbox_wakeup_event() -> asyncio.Event:
    return _notification_outbox_event


def delivery_queue_wakeup_event(bot_identity: str) -> asyncio.Event:
    identity = str(bot_identity or "").strip()
    if identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
        raise ValueError("telegram_delivery_wakeup_identity_invalid")
    event = _delivery_queue_events.get(identity)
    if event is None:
        event = asyncio.Event()
        _delivery_queue_events[identity] = event
    return event


async def wait_for_telegram_wakeup(
    event: asyncio.Event,
    *,
    timeout_seconds: float,
) -> bool:
    timeout = max(0.1, float(timeout_seconds))
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except TimeoutError:
        return False
    return True


async def emit_notification_outbox_wakeup(db: AsyncSession) -> None:
    await db.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {
            "channel": TELEGRAM_NOTIFICATION_OUTBOX_WAKEUP_CHANNEL,
            "payload": _NOTIFICATION_OUTBOX_PAYLOAD,
        },
    )


async def emit_delivery_queue_wakeup(
    db: AsyncSession,
    *,
    bot_identity: str,
) -> None:
    identity = str(bot_identity or "").strip()
    if identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
        raise ValueError("telegram_delivery_wakeup_identity_invalid")
    await db.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {
            "channel": TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL,
            "payload": identity,
        },
    )


def _handle_wakeup_notification(
    _connection: Any,
    _process_id: int,
    channel: str,
    payload: str,
) -> None:
    if (
        channel == TELEGRAM_NOTIFICATION_OUTBOX_WAKEUP_CHANNEL
        and payload == _NOTIFICATION_OUTBOX_PAYLOAD
    ):
        _notification_outbox_event.set()
        return
    if channel != TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL:
        return
    identity = str(payload or "").strip()
    if identity in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
        delivery_queue_wakeup_event(identity).set()


async def _remove_listeners(driver_connection: Any) -> None:
    for channel in (
        TELEGRAM_NOTIFICATION_OUTBOX_WAKEUP_CHANNEL,
        TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL,
    ):
        try:
            await driver_connection.remove_listener(
                channel,
                _handle_wakeup_notification,
            )
        # Cleanup must never mask task cancellation or trigger a reconnect loop
        # when asyncpg reports a closed/broken connection with a driver-specific
        # exception class.
        except Exception:
            pass


async def telegram_delivery_queue_wakeup_listener_loop() -> None:
    """Reconnect forever; bounded polling remains the fail-safe fallback."""

    retry_seconds = _LISTENER_RETRY_MIN_SECONDS
    while True:
        try:
            async with engine.connect() as connection:
                raw_connection = await connection.get_raw_connection()
                driver_connection = getattr(
                    raw_connection,
                    "driver_connection",
                    None,
                )
                if driver_connection is None:
                    raise RuntimeError("telegram_wakeup_driver_connection_missing")
                await driver_connection.add_listener(
                    TELEGRAM_NOTIFICATION_OUTBOX_WAKEUP_CHANNEL,
                    _handle_wakeup_notification,
                )
                await driver_connection.add_listener(
                    TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL,
                    _handle_wakeup_notification,
                )
                retry_seconds = _LISTENER_RETRY_MIN_SECONDS
                logger.info(
                    "Telegram delivery wake-up listener started",
                    extra={
                        "event": "telegram_delivery_queue_wakeup.listener_started",
                        "channel_count": 2,
                    },
                )
                try:
                    while True:
                        await asyncio.sleep(_LISTENER_HEALTH_INTERVAL_SECONDS)
                        await driver_connection.fetchval("SELECT 1")
                finally:
                    await _remove_listeners(driver_connection)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Telegram delivery wake-up listener unavailable; polling fallback remains active",
                extra={
                    "event": "telegram_delivery_queue_wakeup.listener_retry",
                    "error_class": type(exc).__name__,
                    "retry_delay_seconds": retry_seconds,
                },
            )
            await asyncio.sleep(retry_seconds)
            retry_seconds = min(
                _LISTENER_RETRY_MAX_SECONDS,
                retry_seconds * 2.0,
            )


def reset_telegram_wakeup_events_for_test() -> None:
    _notification_outbox_event.clear()
    for event in _delivery_queue_events.values():
        event.clear()
    _delivery_queue_events.clear()
