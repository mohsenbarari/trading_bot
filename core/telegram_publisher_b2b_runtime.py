"""Narrow runtime adapters for Telegram publisher B2B messages.

These adapters deliberately contain no aiogram routing policy.  A primary or
publisher dispatcher supplies only the immutable edge facts and owns sending
the returned ACK.  Durable validation remains in the dispatch service.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core.services.telegram_publisher_dispatch_service import (
    TelegramPublisherInboundDispatchResult,
    accept_telegram_publisher_acknowledgement,
    accept_telegram_publisher_dispatch,
)


async def accept_publisher_b2b_dispatch(
    db: Any,
    *,
    current_server: str,
    publisher_bot_identity: str,
    expected_primary_bot_id: int,
    sender_bot_id: int,
    text: str,
    received_at: datetime,
) -> TelegramPublisherInboundDispatchResult:
    """Validate one primary→publisher command and return its safe ACK text."""
    return await accept_telegram_publisher_dispatch(
        db,
        current_server=current_server,
        publisher_bot_identity=publisher_bot_identity,
        expected_primary_bot_id=expected_primary_bot_id,
        sender_bot_id=sender_bot_id,
        text=text,
        now=received_at,
    )


async def accept_primary_b2b_acknowledgement(
    db: Any,
    *,
    current_server: str,
    sender_bot_id: int,
    publisher_bot_ids: dict[str, int],
    text: str,
    received_at: datetime,
) -> bool:
    """Validate one publisher→primary ACK without creating another command."""
    return await accept_telegram_publisher_acknowledgement(
        db,
        current_server=current_server,
        sender_bot_id=sender_bot_id,
        publisher_bot_ids=publisher_bot_ids,
        text=text,
        now=received_at,
    )
