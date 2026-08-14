"""Queue-side edits for bot overtime requester status (promotion M10→M11)."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.offer_overtime_bot_copy import M11_REQUESTER_STATUS_PRESENTED
from core.server_routing import current_server
from core.services.telegram_interaction_outbox_service import (
    enqueue_private_interaction_edit_once,
)
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
)
from core.telegram_delivery_overtime_requester_status_contract import (
    build_overtime_requester_cancel_reply_markup,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from models.offer_request import OfferRequest
from models.user import User

logger = logging.getLogger(__name__)


async def schedule_requester_status_presented_edit(
    db: AsyncSession,
    ledger: OfferRequest,
) -> None:
    """When a queued bot request is promoted, edit M10 → M11 and keep cancel."""
    receipt = getattr(ledger, "requester_status_outbox_id", None)
    public_id = str(getattr(ledger, "request_public_id", "") or "").strip()
    if not isinstance(receipt, int) or receipt <= 0 or not public_id:
        # Legacy negative message-id sentinel is handled only from bot handlers.
        return
    requester_id = getattr(ledger, "requester_user_id", None)
    if requester_id is None:
        return
    requester = await db.get(User, int(requester_id))
    if requester is None:
        return
    telegram_id = getattr(requester, "telegram_id", None)
    sync_version = getattr(requester, "sync_version", None) or 1
    if not isinstance(telegram_id, int) or telegram_id <= 0:
        return
    try:
        await enqueue_private_interaction_edit_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=int(requester.id),
                telegram_id=int(telegram_id),
            ),
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            source_id=f"ot-status-promote:{public_id}",
            logical_message_key=f"overtime-requester-status-promote:{public_id}",
            source_receipt_id=int(receipt),
            text=M11_REQUESTER_STATUS_PRESENTED,
            user_sync_version=int(sync_version),
            reply_markup=build_overtime_requester_cancel_reply_markup(
                request_public_id=public_id,
            ),
        )
    except Exception as exc:
        logger.debug("requester status promote edit skipped: %s", exc)
