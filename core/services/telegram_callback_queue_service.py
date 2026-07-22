"""Direct M0 ingress for deadline-bound Telegram callback answers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.telegram_delivery_queue_service import (
    TELEGRAM_PRIMARY_BOT_IDENTITY,
    TelegramDeliveryEnqueueResult,
    enqueue_telegram_delivery_job,
)
from core.telegram_delivery_callback_contract import (
    build_telegram_callback_answer_payload,
    telegram_callback_delivery_deadline,
    telegram_callback_destination_key,
    telegram_callback_feeder,
    telegram_callback_source_natural_id,
    telegram_callback_template_version,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
)


async def enqueue_telegram_callback_answer(
    db: AsyncSession,
    *,
    current_server: str,
    callback_query_id: Any,
    received_at: datetime,
    action: TelegramDeliveryAction = TelegramDeliveryAction.CALLBACK_DEADLINE,
    text: Any = None,
    show_alert: bool = False,
) -> TelegramDeliveryEnqueueResult:
    """Insert one callback answer directly into the primary M0 queue."""
    payload = build_telegram_callback_answer_payload(
        callback_query_id=callback_query_id,
        text=text,
        show_alert=show_alert,
    )
    return await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=telegram_callback_feeder(action),
        source_natural_id=telegram_callback_source_natural_id(
            payload["callback_query_id"]
        ),
        source_version=1,
        action=action,
        bot_identity=TELEGRAM_PRIMARY_BOT_IDENTITY,
        destination_key=telegram_callback_destination_key(
            payload["callback_query_id"]
        ),
        destination_class=TelegramDestinationClass.PRIVATE,
        method="answerCallbackQuery",
        payload=payload,
        template_version=telegram_callback_template_version(action),
        delivery_deadline_at=telegram_callback_delivery_deadline(received_at),
    )
