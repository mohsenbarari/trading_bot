"""Dedicated, non-user-facing routers for publisher B2B envelopes."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from core.db import AsyncSessionLocal
from core.server_routing import current_server
from core.telegram_publisher_b2b_runtime import (
    accept_primary_b2b_acknowledgement,
    accept_publisher_b2b_dispatch,
)
from core.utils import utc_now


def _sender_id(message: Message) -> int:
    sender = getattr(message, "from_user", None)
    value = getattr(sender, "id", None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("telegram_b2b_sender_missing")
    return value


def build_publisher_b2b_router(*, identity: str, expected_primary_bot_id: int) -> Router:
    router = Router(name=f"publisher-b2b:{identity}")

    @router.message(F.text.startswith("tbq1|dispatch|"))
    async def receive_dispatch(message: Message) -> None:
        async with AsyncSessionLocal() as db:
            result = await accept_publisher_b2b_dispatch(
                db, current_server=current_server(), publisher_bot_identity=identity,
                expected_primary_bot_id=expected_primary_bot_id, sender_bot_id=_sender_id(message),
                text=str(message.text or ""), received_at=utc_now(),
            )
            await db.commit()
        await message.answer(result.acknowledgement_text)

    return router


def build_primary_b2b_router(*, publisher_bot_ids: dict[str, int]) -> Router:
    router = Router(name="primary-b2b")

    @router.message(F.text.startswith("tbq1|ack|"))
    async def receive_ack(message: Message) -> None:
        async with AsyncSessionLocal() as db:
            await accept_primary_b2b_acknowledgement(
                db, current_server=current_server(), sender_bot_id=_sender_id(message),
                publisher_bot_ids=publisher_bot_ids, text=str(message.text or ""),
                received_at=utc_now(),
            )
            await db.commit()

    return router
