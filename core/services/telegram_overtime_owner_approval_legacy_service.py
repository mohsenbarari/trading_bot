"""Legacy-runtime durable delivery for overtime owner approval prompts.

Queue-v1 owns the shared ``telegram_delivery_jobs`` table.  Until that runtime
is explicitly activated, the already-authoritative legacy notification outbox
must carry the same private prompt; otherwise a queue job has no consumer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from core.offer_lifecycle import read_normal_lifetime_minutes
from core.server_routing import SERVER_FOREIGN, normalize_server
from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
    enqueue_telegram_notification_once,
)
from core.services.telegram_offer_channel_service import (
    INVISIBLE_CHANNEL_PADDING,
    build_offer_channel_message,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    build_overtime_owner_approval_payload,
    build_overtime_owner_approval_reply_markup,
    offer_is_lot_based,
    overtime_owner_approval_delivery_deadline,
)
from core.trading_settings import get_trading_settings_async
from models.offer import Offer
from models.offer_request import OfferRequest, OfferRequestStatus
from models.telegram_notification_outbox import TelegramNotificationOutbox
from models.user import User


LEGACY_OVERTIME_OWNER_APPROVAL_SOURCE = "overtime_owner_approval_legacy"
LEGACY_OVERTIME_OWNER_APPROVAL_VERSION = "overtime-owner-approval-legacy-v1"


@dataclass(frozen=True, slots=True)
class LegacyOvertimeOwnerApprovalEnqueueOutcome:
    enqueued: bool
    outbox_id: int | None = None
    outbox_created: bool = False
    undeliverable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyOvertimeOwnerApprovalPreflight:
    dispatchable: bool
    reason: str
    reply_markup: Mapping[str, Any] | None = None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def is_legacy_overtime_owner_approval_outbox(outbox: Any) -> bool:
    return str(getattr(outbox, "source_type", "") or "") == LEGACY_OVERTIME_OWNER_APPROVAL_SOURCE


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def enqueue_legacy_overtime_owner_approval_delivery(
    db: AsyncSession,
    *,
    ledger: OfferRequest,
    offer: Offer,
    normal_lifetime_minutes: int,
) -> LegacyOvertimeOwnerApprovalEnqueueOutcome:
    status = str(getattr(ledger.result_status, "value", ledger.result_status) or "")
    if status != OfferRequestStatus.OVERTIME_DELIVERING.value:
        raise ValueError("legacy_overtime_owner_approval_requires_delivering")

    owner_id = _positive_int(getattr(ledger, "offer_owner_user_id", None))
    if owner_id is None:
        return LegacyOvertimeOwnerApprovalEnqueueOutcome(False, undeliverable_reason="overtime_owner_missing")
    owner = await db.get(User, owner_id)
    if owner is None:
        return LegacyOvertimeOwnerApprovalEnqueueOutcome(False, undeliverable_reason="overtime_owner_user_missing")
    telegram_id = _positive_int(getattr(owner, "telegram_id", None))
    if telegram_id is None:
        return LegacyOvertimeOwnerApprovalEnqueueOutcome(False, undeliverable_reason="overtime_owner_unlinked")
    access = await evaluate_bot_access(db, owner)
    if not access.allowed:
        return LegacyOvertimeOwnerApprovalEnqueueOutcome(
            False,
            undeliverable_reason=str(access.reason or "overtime_owner_access_denied"),
        )

    request_id = str(getattr(ledger, "request_public_id", "") or "").strip()
    deadline = overtime_owner_approval_delivery_deadline(
        offer,
        normal_lifetime_minutes=int(normal_lifetime_minutes),
    )
    offer_text = build_offer_channel_message(offer).replace(INVISIBLE_CHANNEL_PADDING, "").rstrip()
    payload = build_overtime_owner_approval_payload(
        chat_id=telegram_id,
        request_public_id=request_id,
        offer_text=offer_text,
        requested_quantity=getattr(ledger, "requested_quantity", None),
        include_quantity_line=offer_is_lot_based(offer),
    )
    result = await enqueue_telegram_notification_once(
        db,
        recipient=TelegramNotificationRecipient(user_id=owner_id, telegram_id=telegram_id),
        text=str(payload["text"]),
        source_type=LEGACY_OVERTIME_OWNER_APPROVAL_SOURCE,
        source_id=request_id,
        parse_mode="Markdown",
        extra_payload={
            "contract_version": LEGACY_OVERTIME_OWNER_APPROVAL_VERSION,
            "request_public_id": request_id,
            "offer_public_id": str(getattr(ledger, "offer_public_id", "") or ""),
            "reply_markup": payload["reply_markup"],
            "delivery_deadline_at": _aware_utc(deadline).isoformat(),
            "normal_lifetime_minutes": int(normal_lifetime_minutes),
        },
    )
    return LegacyOvertimeOwnerApprovalEnqueueOutcome(
        True,
        outbox_id=int(result.outbox.id),
        outbox_created=bool(result.created),
    )


async def _invalidate_and_promote(
    db: AsyncSession,
    ledger: OfferRequest,
    *,
    reason: str,
    now: datetime,
) -> None:
    from core.services.offer_overtime_request_service import invalidate_request, promote_next_for_owner

    status = str(getattr(ledger.result_status, "value", ledger.result_status) or "")
    if status != OfferRequestStatus.OVERTIME_DELIVERING.value:
        return
    owner_id = _positive_int(getattr(ledger, "offer_owner_user_id", None))
    home = normalize_server(getattr(ledger, "request_home_server", None), SERVER_FOREIGN)
    await invalidate_request(db, ledger, reason=reason, now=now, flush=True)
    if owner_id is None:
        return
    settings = await get_trading_settings_async()
    await promote_next_for_owner(
        db,
        request_home_server=home,
        offer_owner_user_id=owner_id,
        normal_lifetime_minutes=read_normal_lifetime_minutes(settings),
        now=now,
        flush=True,
    )


async def prepare_legacy_overtime_owner_approval_dispatch(
    db: AsyncSession,
    outbox: TelegramNotificationOutbox,
    *,
    now: datetime,
) -> LegacyOvertimeOwnerApprovalPreflight:
    if not is_legacy_overtime_owner_approval_outbox(outbox):
        raise ValueError("legacy_overtime_owner_approval_source_mismatch")
    payload = getattr(outbox, "extra_payload", None)
    if not isinstance(payload, Mapping):
        raise ValueError("legacy_overtime_owner_approval_payload_invalid")
    request_id = str(payload.get("request_public_id") or "").strip()
    if not request_id or request_id != str(getattr(outbox, "source_id", "") or "").strip():
        raise ValueError("legacy_overtime_owner_approval_request_mismatch")
    expected_markup = build_overtime_owner_approval_reply_markup(request_public_id=request_id)
    if payload.get("reply_markup") != expected_markup:
        raise ValueError("legacy_overtime_owner_approval_markup_invalid")
    try:
        deadline = datetime.fromisoformat(str(payload.get("delivery_deadline_at") or ""))
    except ValueError as exc:
        raise ValueError("legacy_overtime_owner_approval_deadline_invalid") from exc

    from core.services.offer_overtime_request_service import load_overtime_request_by_public_id

    ledger = await load_overtime_request_by_public_id(db, request_id, for_update=True)
    if ledger is None:
        return LegacyOvertimeOwnerApprovalPreflight(False, "overtime_owner_request_missing")
    status = str(getattr(ledger.result_status, "value", ledger.result_status) or "")
    if status != OfferRequestStatus.OVERTIME_DELIVERING.value:
        return LegacyOvertimeOwnerApprovalPreflight(False, "overtime_owner_request_not_delivering")
    if normalize_server(getattr(ledger, "request_home_server", None), SERVER_FOREIGN) != SERVER_FOREIGN:
        raise ValueError("legacy_overtime_owner_approval_home_mismatch")
    if _positive_int(getattr(ledger, "offer_owner_user_id", None)) != _positive_int(
        getattr(outbox, "recipient_user_id", None)
    ):
        raise ValueError("legacy_overtime_owner_approval_owner_mismatch")
    if _aware_utc(now) >= _aware_utc(deadline):
        await _invalidate_and_promote(
            db,
            ledger,
            reason="overtime_owner_approval_delivery_deadline_passed",
            now=now,
        )
        return LegacyOvertimeOwnerApprovalPreflight(False, "overtime_owner_delivery_expired")
    return LegacyOvertimeOwnerApprovalPreflight(True, "current", expected_markup)


async def apply_legacy_overtime_owner_approval_sent(
    db: AsyncSession,
    outbox: TelegramNotificationOutbox,
    *,
    telegram_message_id: int,
    now: datetime,
) -> None:
    from core.services.offer_overtime_request_service import load_overtime_request_by_public_id, mark_presented

    ledger = await load_overtime_request_by_public_id(db, str(outbox.source_id), for_update=True)
    if ledger is None:
        raise ValueError("legacy_overtime_owner_approval_request_missing_after_send")
    await mark_presented(
        db,
        ledger,
        presented_at=now,
        telegram_message_id=int(telegram_message_id),
        flush=True,
    )


async def apply_legacy_overtime_owner_approval_terminal_failure(
    db: AsyncSession,
    outbox: TelegramNotificationOutbox,
    *,
    reason: str,
    now: datetime,
) -> None:
    from core.services.offer_overtime_request_service import load_overtime_request_by_public_id

    ledger = await load_overtime_request_by_public_id(db, str(outbox.source_id), for_update=True)
    if ledger is not None:
        await _invalidate_and_promote(db, ledger, reason=reason, now=now)
