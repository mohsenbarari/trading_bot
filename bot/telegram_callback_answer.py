"""Runtime-owned answerCallbackQuery adapter for bot handlers."""
from __future__ import annotations

from typing import Any

from core.db import AsyncSessionLocal
from core.server_routing import current_server
from core.services.telegram_callback_queue_service import (
    enqueue_telegram_callback_answer,
)
from core.telegram_callback_receipt_context import (
    current_telegram_callback_received_at,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)


class TelegramCallbackReceiptMissingError(RuntimeError):
    """Raised when queue mode cannot prove the callback's edge receipt time."""


_UNSET = object()


async def answer_callback_query_via_runtime(
    callback: Any,
    text: Any = _UNSET,
    *,
    show_alert: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    """Preserve legacy behavior or persist one direct M0 callback answer."""
    if (
        configured_telegram_delivery_runtime().mode
        != TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        args = () if text is _UNSET else (text,)
        kwargs = {} if show_alert is _UNSET else {"show_alert": show_alert}
        return await callback.answer(*args, **kwargs)

    received_at = current_telegram_callback_received_at()
    if received_at is None:
        raise TelegramCallbackReceiptMissingError(
            "telegram_callback_edge_receipt_missing"
        )

    async def _enqueue(db):
        result = await enqueue_telegram_callback_answer(
            db,
            current_server=current_server(),
            callback_query_id=callback.id,
            received_at=received_at,
            text=None if text is _UNSET else text,
            show_alert=False if show_alert is _UNSET else show_alert,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)
