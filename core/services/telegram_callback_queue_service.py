"""Direct M0 ingress for deadline-bound Telegram callback answers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.telegram_delivery_queue_service import (
    TELEGRAM_PRIMARY_BOT_IDENTITY,
    TELEGRAM_PUBLISHER_BOT_IDENTITIES,
    TelegramDeliveryEnqueueResult,
    enqueue_telegram_delivery_job,
)
from core.telegram_delivery_callback_contract import (
    TELEGRAM_CALLBACK_ANSWERED_AT_EDGE_REASON,
    build_telegram_callback_answer_payload,
    telegram_callback_delivery_deadline,
    telegram_callback_destination_key,
    telegram_callback_feeder,
    telegram_callback_source_natural_id,
    telegram_callback_template_version,
)
from core.telegram_delivery_queue_contract import (
    CLAIMABLE_DELIVERY_STATES,
    FINAL_DELIVERY_STATES,
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord


async def enqueue_telegram_callback_answer(
    db: AsyncSession,
    *,
    current_server: str,
    callback_query_id: Any,
    received_at: datetime,
    action: TelegramDeliveryAction = TelegramDeliveryAction.CALLBACK_DEADLINE,
    text: Any = None,
    show_alert: bool = False,
    bot_identity: str = TELEGRAM_PRIMARY_BOT_IDENTITY,
    answered_at_edge: bool = False,
) -> TelegramDeliveryEnqueueResult:
    """Insert one callback answer on the bot that received that callback."""
    owner = str(bot_identity or "").strip()
    if owner not in {
        TELEGRAM_PRIMARY_BOT_IDENTITY,
        *TELEGRAM_PUBLISHER_BOT_IDENTITIES,
    }:
        raise ValueError("telegram_callback_bot_identity_invalid")
    payload = build_telegram_callback_answer_payload(
        callback_query_id=callback_query_id,
        text=text,
        show_alert=show_alert,
    )
    result = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=telegram_callback_feeder(action),
        source_natural_id=telegram_callback_source_natural_id(
            payload["callback_query_id"]
        ),
        source_version=1,
        action=action,
        bot_identity=owner,
        destination_key=telegram_callback_destination_key(
            payload["callback_query_id"]
        ),
        destination_class=TelegramDestinationClass.PRIVATE,
        method="answerCallbackQuery",
        payload=payload,
        template_version=telegram_callback_template_version(action),
        delivery_deadline_at=telegram_callback_delivery_deadline(received_at),
    )
    if answered_at_edge:
        await mark_telegram_callback_answered_at_edge(db, result.job)
    return result


async def mark_telegram_callback_answered_at_edge(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    *,
    now: datetime | None = None,
) -> TelegramDeliveryJobRecord:
    """Keep the durable witness and stop the worker from answering again."""
    if str(job.method or "") != "answerCallbackQuery":
        raise ValueError("telegram_callback_edge_mark_method_invalid")
    current_state = TelegramDeliveryState(
        str(getattr(job.state, "value", job.state))
    )
    if current_state in FINAL_DELIVERY_STATES:
        return job
    if current_state not in CLAIMABLE_DELIVERY_STATES:
        return job
    current_time = now or utc_now()
    job.state = TelegramDeliveryState.SENT
    job.sent_at = current_time
    job.terminal_at = current_time
    job.updated_at = current_time
    job.next_retry_at = None
    job.last_error_class = None
    job.last_error_message = None
    job.provider_ok = True
    job.outcome_reason = TELEGRAM_CALLBACK_ANSWERED_AT_EDGE_REASON
    await db.flush()
    return job
