"""Runtime-owned answerCallbackQuery adapter for bot handlers."""
from __future__ import annotations

import logging
from typing import Any

from core.db import AsyncSessionLocal
from core.server_routing import current_server
from core.services.telegram_callback_queue_service import (
    enqueue_telegram_callback_answer,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.telegram_callback_receipt_context import (
    current_telegram_callback_received_at,
)
from core.telegram_bot_identity_context import current_telegram_callback_bot_identity
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)


class TelegramCallbackReceiptMissingError(RuntimeError):
    """Raised when queue mode cannot prove the callback's edge receipt time."""


_UNSET = object()
logger = logging.getLogger(__name__)


async def answer_callback_query_via_runtime(
    callback: Any,
    text: Any = _UNSET,
    *,
    show_alert: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
    action: TelegramDeliveryAction = TelegramDeliveryAction.CALLBACK_DEADLINE,
):
    """Answer the spinner on the edge, then keep one durable witness."""
    args = () if text is _UNSET else (text,)
    kwargs = {} if show_alert is _UNSET else {"show_alert": show_alert}
    if (
        configured_telegram_delivery_runtime().mode
        != TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        return await callback.answer(*args, **kwargs)

    received_at = current_telegram_callback_received_at()
    if received_at is None:
        raise TelegramCallbackReceiptMissingError(
            "telegram_callback_edge_receipt_missing"
        )

    answered_at_edge = False
    try:
        await callback.answer(*args, **kwargs)
        answered_at_edge = True
    except Exception:
        logger.warning(
            "Edge callback answer failed; durable recovery remains queued",
            extra={"event": "telegram.callback_edge_answer_failed"},
        )

    async def _enqueue(db):
        result = await enqueue_telegram_callback_answer(
            db,
            current_server=current_server(),
            callback_query_id=callback.id,
            received_at=received_at,
            action=action,
            text=None if text is _UNSET else text,
            show_alert=False if show_alert is _UNSET else show_alert,
            bot_identity=current_telegram_callback_bot_identity(),
            answered_at_edge=answered_at_edge,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)
