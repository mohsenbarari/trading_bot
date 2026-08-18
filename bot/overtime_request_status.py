"""Send and edit private overtime requester status messages (M10–M15)."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from bot.telegram_interaction_message import (
    edit_delivery_receipt_via_runtime,
)
from core.offer_overtime_bot_copy import (
    M10_REQUESTER_STATUS_QUEUED,
    M11_REQUESTER_STATUS_PRESENTED,
    M12_REQUESTER_CANCEL_BUTTON,
    M13_REQUESTER_STATUS_APPROVED,
    M14_REQUESTER_STATUS_TERMINAL_FAILURE,
    M15_REQUESTER_STATUS_CANCELLED,
    M29_OWNER_STATUS_APPROVED,
    M30_OWNER_STATUS_REJECTED,
    M31_OWNER_STATUS_CLOSED,
    M37_OWNER_REVALIDATION_FAILED_MESSAGE,
)
from core.server_routing import current_server
from core.services.telegram_interaction_outbox_service import (
    enqueue_private_interaction_edit_once,
    enqueue_private_interaction_once,
)
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
)
from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionAnchorEffect,
    TelegramInteractionResultRequirement,
)
from core.telegram_delivery_overtime_requester_status_contract import (
    build_overtime_requester_cancel_callback_data,
    build_overtime_requester_cancel_reply_markup,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from models.offer_request import OfferRequest, OfferRequestStatus
from models.user import User

logger = logging.getLogger(__name__)


async def _persist_requester_status_receipt_locally(
    session: AsyncSession,
    ledger: OfferRequest,
    receipt_id: int | None,
) -> None:
    """Persist the requester-message receipt without changing ledger authority.

    ``requester_status_outbox_id`` is explicitly local-only delivery state.  An
    ORM flush would bump ``OfferRequest.version_id`` and emit a full cross-server
    lifecycle payload, allowing a mirror's stale status to race the authoritative
    offer-home transition.  A sync-marked Core update keeps this receipt local,
    leaves the lifecycle version untouched, and skips mapper sync events.
    """
    ledger_id = getattr(ledger, "id", None)
    if not isinstance(ledger_id, int) or ledger_id <= 0:
        return

    await session.execute(
        update(OfferRequest)
        .where(OfferRequest.id == ledger_id)
        .values(requester_status_outbox_id=receipt_id)
        .execution_options(synchronize_session=False),
        execution_options={"is_sync": True},
    )
    set_committed_value(ledger, "requester_status_outbox_id", receipt_id)


def requester_status_text_for_result_status(result_status: Any) -> str:
    status = str(getattr(result_status, "value", result_status) or "")
    if status == OfferRequestStatus.OVERTIME_QUEUED.value:
        return M10_REQUESTER_STATUS_QUEUED
    return M11_REQUESTER_STATUS_PRESENTED


def terminal_requester_status_text(*, cancelled: bool = False, approved: bool = False) -> str:
    if cancelled:
        return M15_REQUESTER_STATUS_CANCELLED
    if approved:
        return M13_REQUESTER_STATUS_APPROVED
    return M14_REQUESTER_STATUS_TERMINAL_FAILURE


def cancel_inline_keyboard(request_public_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=M12_REQUESTER_CANCEL_BUTTON,
                    callback_data=build_overtime_requester_cancel_callback_data(
                        request_public_id=request_public_id,
                    ),
                )
            ]
        ]
    )


def empty_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[])


async def send_requester_overtime_status(
    *,
    bot: Any,
    session: AsyncSession,
    user: User,
    ledger: OfferRequest,
    chat_id: int | None = None,
) -> None:
    """Send M10/M11 with لغو درخواست and persist the queue receipt id when possible."""
    public_id = str(getattr(ledger, "request_public_id", "") or "").strip()
    if not public_id:
        return
    text = requester_status_text_for_result_status(ledger.result_status)
    markup = cancel_inline_keyboard(public_id)
    target_chat = chat_id or getattr(user, "telegram_id", None)
    if not target_chat:
        return

    runtime = configured_telegram_delivery_runtime()
    if runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1:
        try:
            sent = await bot.send_message(
                chat_id=int(target_chat),
                text=text,
                reply_markup=markup,
            )
            message_id = getattr(sent, "message_id", None)
            if isinstance(message_id, int) and message_id > 0:
                # Store the legacy message id as a negative sentinel in the
                # local-only receipt column without publishing a ledger update.
                await _persist_requester_status_receipt_locally(
                    session,
                    ledger,
                    -int(message_id),
                )
        except Exception as exc:
            logger.warning("overtime requester status send failed: %s", exc)
        return

    telegram_id = getattr(user, "telegram_id", None)
    sync_version = getattr(user, "sync_version", None) or 1
    if not isinstance(telegram_id, int) or telegram_id <= 0:
        return
    try:
        result = await enqueue_private_interaction_once(
            session,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=int(user.id),
                telegram_id=int(telegram_id),
            ),
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            source_id=f"ot-status:{public_id}",
            logical_message_key=f"overtime-requester-status:{public_id}",
            text=text,
            user_sync_version=int(sync_version),
            reply_markup=build_overtime_requester_cancel_reply_markup(
                request_public_id=public_id,
            ),
            result_requirement=TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID,
            anchor_effect=TelegramInteractionAnchorEffect.PRESERVE_CURRENT,
        )
        outbox = result.notification.outbox
        outbox_id = getattr(outbox, "id", None)
        if isinstance(outbox_id, int) and outbox_id > 0:
            await _persist_requester_status_receipt_locally(
                session,
                ledger,
                outbox_id,
            )
    except Exception as exc:
        logger.warning("overtime requester status enqueue failed: %s", exc)


async def edit_requester_overtime_status(
    *,
    session: AsyncSession,
    user: User,
    ledger: OfferRequest,
    text: str,
    keep_cancel: bool = False,
    bot: Any = None,
) -> None:
    """Edit the durable requester status message; never delete it."""
    public_id = str(getattr(ledger, "request_public_id", "") or "").strip()
    receipt = getattr(ledger, "requester_status_outbox_id", None)
    if not isinstance(receipt, int) or receipt == 0:
        return
    reply_markup: Any
    if keep_cancel and public_id:
        reply_markup = cancel_inline_keyboard(public_id)
    else:
        reply_markup = empty_inline_keyboard()

    if receipt < 0:
        # Legacy sentinel: negative of telegram message_id.
        runtime = configured_telegram_delivery_runtime()
        if runtime.mode == TelegramDeliveryRuntimeMode.QUEUE_V1:
            return
        message_id = -receipt
        chat_id = getattr(user, "telegram_id", None)
        if bot is None or not chat_id:
            return
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=text,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.debug("legacy overtime status edit skipped: %s", exc)
        return

    origin = SimpleNamespace(
        chat=SimpleNamespace(id=getattr(user, "telegram_id", None)),
        message_id=0,
    )
    try:
        await edit_delivery_receipt_via_runtime(
            origin,
            user,
            int(receipt),
            text,
            source_key="ot-status-edit",
            reply_markup=(
                build_overtime_requester_cancel_reply_markup(request_public_id=public_id)
                if keep_cancel and public_id
                else {"inline_keyboard": []}
            ),
            session=session,
            commit=False,
        )
    except Exception as exc:
        logger.debug("overtime status receipt edit skipped: %s", exc)


async def edit_owner_overtime_approval_message(
    *,
    session: AsyncSession,
    owner: User,
    ledger: OfferRequest,
    text: str,
    bot: Any = None,
) -> None:
    """Strip buttons and set owner terminal text on the Stage 8 approval message."""
    message_id = getattr(ledger, "telegram_message_id", None)
    chat_id = getattr(owner, "telegram_id", None)
    if not isinstance(message_id, int) or message_id <= 0 or not chat_id:
        return

    runtime = configured_telegram_delivery_runtime()
    if runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1:
        if bot is None:
            return
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=text,
                reply_markup=empty_inline_keyboard(),
            )
        except Exception as exc:
            logger.debug("legacy owner approval edit skipped: %s", exc)
        return

    sync_version = getattr(owner, "sync_version", None) or 1
    try:
        await enqueue_private_interaction_edit_once(
            session,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=int(owner.id),
                telegram_id=int(chat_id),
            ),
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            source_id=f"ot-owner-term:{ledger.request_public_id}:{message_id}",
            logical_message_key=(
                f"overtime-owner-terminal:{ledger.request_public_id}:{message_id}"
            ),
            target_message_id=int(message_id),
            text=text,
            user_sync_version=int(sync_version),
            reply_markup={"inline_keyboard": []},
        )
    except Exception as exc:
        logger.debug("owner approval terminal edit skipped: %s", exc)


def owner_terminal_text_for_reason(
    *,
    approved: bool = False,
    rejected: bool = False,
    revalidation_failed: bool = False,
) -> str:
    if revalidation_failed:
        return M37_OWNER_REVALIDATION_FAILED_MESSAGE
    if approved:
        return M29_OWNER_STATUS_APPROVED
    if rejected:
        return M30_OWNER_STATUS_REJECTED
    return M31_OWNER_STATUS_CLOSED
