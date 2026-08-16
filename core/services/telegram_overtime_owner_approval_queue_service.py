"""Direct M0 ingress for private overtime owner-approval prompts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_delivery_queue_service import (
    TELEGRAM_PRIMARY_BOT_IDENTITY,
    TelegramDeliveryEnqueueResult,
    enqueue_telegram_delivery_job,
)
from core.services.telegram_offer_channel_service import (
    INVISIBLE_CHANNEL_PADDING,
    build_offer_channel_message,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    OVERTIME_OWNER_APPROVAL_TEMPLATE_VERSION,
    build_overtime_owner_approval_payload,
    offer_is_lot_based,
    overtime_owner_approval_delivery_deadline,
    overtime_owner_approval_destination_key,
    overtime_owner_approval_feeder,
    overtime_owner_approval_source_natural_id,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
)
from models.commodity import Commodity
from models.offer import Offer
from models.offer_request import OfferRequest, OfferRequestStatus
from models.user import User


class OvertimeOwnerApprovalQueueError(RuntimeError):
    """Raised when an overtime approval job cannot be built safely."""


@dataclass(frozen=True, slots=True)
class OvertimeOwnerApprovalEnqueueOutcome:
    """Result of attempting to place a DELIVERING request on the Telegram queue."""

    enqueued: bool
    job_id: int | None = None
    job_created: bool = False
    undeliverable_reason: str | None = None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _strip_channel_padding(offer_text: str) -> str:
    # Channel posts append invisible padding; private prompts should not.
    return offer_text.replace(INVISIBLE_CHANNEL_PADDING, "").rstrip()


async def _attach_offer_commodity(db: AsyncSession, offer: Offer) -> None:
    """Load commodity in this async session before the sync channel renderer.

    ``db.get(Offer, ..., options=[selectinload(commodity)])`` returns the
    identity-mapped instance without applying the loader when the offer is
    already present. The sync renderer must not lazy-load.
    """
    commodity_id = _positive_int(getattr(offer, "commodity_id", None))
    if commodity_id is None:
        return
    commodity = await db.get(Commodity, commodity_id)
    if commodity is None:
        return
    offer.commodity = commodity


async def enqueue_overtime_owner_approval_delivery(
    db: AsyncSession,
    *,
    current_server: str,
    ledger: OfferRequest,
    offer: Offer,
    normal_lifetime_minutes: int,
    now: datetime | None = None,
) -> OvertimeOwnerApprovalEnqueueOutcome:
    """Insert one private approval job for a Telegram-home offer request.

    Returns ``undeliverable_reason`` when the owner cannot receive Telegram
    messages at all (missing/unlinked/denied). Callers should invalidate that
    request and promote the next queued row. Soft delivery failures stay on the
    queue until the offer final deadline.
    """
    del now  # reserved for future eligibility windows
    status = str(getattr(ledger.result_status, "value", ledger.result_status) or "")
    if status != OfferRequestStatus.OVERTIME_DELIVERING.value:
        raise OvertimeOwnerApprovalQueueError(
            "overtime_owner_approval_enqueue_requires_delivering"
        )

    owner_user_id = _positive_int(ledger.offer_owner_user_id)
    if owner_user_id is None:
        return OvertimeOwnerApprovalEnqueueOutcome(
            enqueued=False,
            undeliverable_reason="overtime_owner_missing",
        )
    owner = await db.get(User, owner_user_id)
    if owner is None:
        return OvertimeOwnerApprovalEnqueueOutcome(
            enqueued=False,
            undeliverable_reason="overtime_owner_user_missing",
        )
    telegram_id = _positive_int(getattr(owner, "telegram_id", None))
    if telegram_id is None:
        return OvertimeOwnerApprovalEnqueueOutcome(
            enqueued=False,
            undeliverable_reason="overtime_owner_unlinked",
        )
    access = await evaluate_bot_access(db, owner)
    if not access.allowed:
        return OvertimeOwnerApprovalEnqueueOutcome(
            enqueued=False,
            undeliverable_reason=str(access.reason or "overtime_owner_access_denied"),
        )

    request_public_id = str(getattr(ledger, "request_public_id", "") or "").strip()
    await _attach_offer_commodity(db, offer)
    try:
        delivery_deadline_at = overtime_owner_approval_delivery_deadline(
            offer,
            normal_lifetime_minutes=int(normal_lifetime_minutes),
        )
        offer_text = _strip_channel_padding(build_offer_channel_message(offer))
        include_quantity = offer_is_lot_based(offer)
        payload = build_overtime_owner_approval_payload(
            chat_id=telegram_id,
            request_public_id=request_public_id,
            offer_text=offer_text,
            requested_quantity=getattr(ledger, "requested_quantity", None),
            include_quantity_line=include_quantity,
        )
        source_natural_id = overtime_owner_approval_source_natural_id(request_public_id)
        destination_key = overtime_owner_approval_destination_key(owner_user_id)
        source_version = _positive_int(getattr(owner, "sync_version", None)) or 1
    except (TypeError, ValueError, OverflowError) as exc:
        raise OvertimeOwnerApprovalQueueError(
            f"overtime_owner_approval_payload_invalid:{exc}"
        ) from exc

    result: TelegramDeliveryEnqueueResult = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=overtime_owner_approval_feeder(),
        source_natural_id=source_natural_id,
        source_version=source_version,
        action=TelegramDeliveryAction.OVERTIME_OWNER_APPROVAL,
        bot_identity=TELEGRAM_PRIMARY_BOT_IDENTITY,
        destination_key=destination_key,
        destination_class=TelegramDestinationClass.PRIVATE,
        method="sendMessage",
        payload=payload,
        template_version=OVERTIME_OWNER_APPROVAL_TEMPLATE_VERSION,
        delivery_deadline_at=delivery_deadline_at,
    )
    return OvertimeOwnerApprovalEnqueueOutcome(
        enqueued=True,
        job_id=int(result.job.id),
        job_created=bool(result.created),
    )
