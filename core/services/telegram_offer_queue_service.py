"""Authoritative Offer feeder adapter for the shared Telegram queue.

This module owns no Telegram client.  It translates the current Offer and its
canonical publication identity into immutable queue jobs.  Repeated scans are
safe because the queue dedupe identity contains the Offer public id, version,
action, and destination.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from core.config import settings
from core.server_routing import SERVER_FOREIGN
from core.services.offer_publication_state_service import (
    canonical_telegram_publication_identity,
)
from core.services.telegram_delivery_queue_service import (
    TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY,
    TELEGRAM_PRIMARY_BOT_IDENTITY,
    TelegramDeliveryEnqueueResult,
    enqueue_telegram_delivery_job,
)
from core.services.telegram_offer_publication_service import (
    get_or_create_telegram_publication_state,
)
from core.telegram_delivery_offer_freshness import (
    OFFER_ACTIVE_EDIT_ACTIONS,
    OFFER_FRESHNESS_ACTIONS,
    OFFER_PUBLISH_ACTIONS,
    OFFER_TERMINAL_EDIT_ACTIONS,
    build_authoritative_offer_delivery_payload,
    telegram_channel_destination_key,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.utils import utc_now
from models.offer import Offer, OfferStatus
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord


OFFER_QUEUE_TEMPLATE_VERSION = "offer-channel-v1"
OFFER_PUBLICATION_DEADLINE_SAFETY_SECONDS = 5.0


class TelegramOfferQueueError(ValueError):
    """Raised before an unsafe or incomplete Offer queue handoff."""


@dataclass(frozen=True, slots=True)
class TelegramOfferQueueHandoffResult:
    offer_public_id: str
    action: TelegramDeliveryAction | None
    queue_result: TelegramDeliveryEnqueueResult | None
    skipped_reason: str | None = None


@dataclass(slots=True)
class TelegramOfferQueueCandidate:
    offer: Offer
    state: OfferPublicationState | None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _nonzero_int(value: Any, *, reason: str) -> int:
    if isinstance(value, bool):
        raise TelegramOfferQueueError(reason)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramOfferQueueError(reason) from exc
    if parsed == 0:
        raise TelegramOfferQueueError(reason)
    return parsed


def _normalized_time(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def configured_offer_edit_bot_identity() -> str:
    if bool(getattr(settings, "telegram_delivery_queue_channel_editor_enabled", False)):
        return TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY
    return TELEGRAM_PRIMARY_BOT_IDENTITY


def offer_delivery_action(
    offer: Offer,
    state: OfferPublicationState | None,
) -> TelegramDeliveryAction | None:
    """Return the one current publication/edit action for an Offer."""
    status = _enum_value(getattr(offer, "status", None))
    state_message_id = _positive_int(getattr(state, "telegram_message_id", None))

    if state_message_id is None:
        return (
            TelegramDeliveryAction.OFFER_PUBLISH
            if status == OfferStatus.ACTIVE.value
            else None
        )
    if status == OfferStatus.COMPLETED.value:
        return TelegramDeliveryAction.TRADED_OFFER_EDIT
    if status == OfferStatus.EXPIRED.value:
        return TelegramDeliveryAction.EXPIRED_OFFER_EDIT
    if status == OfferStatus.CANCELLED.value:
        return TelegramDeliveryAction.CANCELLED_OFFER_EDIT
    if status != OfferStatus.ACTIVE.value:
        raise TelegramOfferQueueError("telegram_offer_queue_status_invalid")

    quantity = _positive_int(getattr(offer, "quantity", None))
    remaining = _positive_int(getattr(offer, "remaining_quantity", None))
    if quantity is None:
        raise TelegramOfferQueueError("telegram_offer_queue_quantity_invalid")
    # A zero remaining quantity should already be terminal. Fail closed rather
    # than rendering active trade buttons for a corrupt intermediate snapshot.
    raw_remaining = getattr(offer, "remaining_quantity", None)
    if raw_remaining == 0:
        raise TelegramOfferQueueError("telegram_offer_queue_active_zero_remaining")
    if remaining is not None and remaining < quantity:
        return TelegramDeliveryAction.PARTIAL_OFFER_EDIT
    return TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT


def offer_publication_freshness_deadline(
    offer: Offer,
    *,
    offer_expiry_minutes: int,
) -> datetime:
    created_at = getattr(offer, "created_at", None)
    if not isinstance(created_at, datetime):
        raise TelegramOfferQueueError("telegram_offer_queue_created_at_invalid")
    expiry_minutes = int(offer_expiry_minutes)
    if expiry_minutes <= 0:
        raise TelegramOfferQueueError("telegram_offer_queue_expiry_invalid")
    return _normalized_time(created_at) + timedelta(
        minutes=expiry_minutes,
        seconds=-OFFER_PUBLICATION_DEADLINE_SAFETY_SECONDS,
    )


async def _supersede_obsolete_offer_jobs(
    db: AsyncSession,
    *,
    offer_public_id: str,
    source_version: int,
    now: datetime,
) -> int:
    rows = list(
        (
            await db.execute(
                select(TelegramDeliveryJobRecord)
                .where(
                    TelegramDeliveryJobRecord.source_natural_id == offer_public_id,
                    TelegramDeliveryJobRecord.action_kind.in_(tuple(OFFER_FRESHNESS_ACTIONS)),
                    TelegramDeliveryJobRecord.source_version < source_version,
                    TelegramDeliveryJobRecord.state.in_(
                        (
                            TelegramDeliveryState.PENDING,
                            TelegramDeliveryState.PENDING_RETRY,
                        )
                    ),
                    TelegramDeliveryJobRecord.dispatch_started_at.is_(None),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for row in rows:
        row.state = TelegramDeliveryState.SUPERSEDED
        row.next_retry_at = None
        row.outcome_reason = "offer_queue_newer_source_version"
        row.terminal_at = now
        row.updated_at = now
    return len(rows)


async def enqueue_current_offer_delivery(
    db: AsyncSession,
    *,
    current_server: str,
    offer: Offer,
    state: OfferPublicationState | None,
    expected_channel_id: int,
    offer_expiry_minutes: int | None,
    action: TelegramDeliveryAction | None = None,
    publication_freshness_deadline_at: datetime | None = None,
    now: datetime | None = None,
) -> TelegramOfferQueueHandoffResult:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramOfferQueueError("telegram_offer_queue_handoff_is_foreign_only")
    current_time = now or utc_now()
    channel_id = _nonzero_int(
        expected_channel_id,
        reason="telegram_offer_queue_channel_invalid",
    )
    offer_public_id = str(getattr(offer, "offer_public_id", "") or "").strip()
    source_version = _positive_int(getattr(offer, "version_id", None))
    if not offer_public_id or source_version is None:
        raise TelegramOfferQueueError("telegram_offer_queue_source_invalid")

    if state is None:
        state = await get_or_create_telegram_publication_state(db, offer)

    selected_action = action or offer_delivery_action(offer, state)
    if selected_action is None:
        await _supersede_obsolete_offer_jobs(
            db,
            offer_public_id=offer_public_id,
            source_version=source_version,
            now=current_time,
        )
        return TelegramOfferQueueHandoffResult(
            offer_public_id=offer_public_id,
            action=None,
            queue_result=None,
            skipped_reason="offer_not_publishable_and_never_published",
        )
    if selected_action not in OFFER_FRESHNESS_ACTIONS:
        raise TelegramOfferQueueError("telegram_offer_queue_action_not_supported")

    message_id: int | None = None
    if selected_action not in OFFER_PUBLISH_ACTIONS:
        identity = canonical_telegram_publication_identity(state)
        if identity.destination_chat_id != channel_id:
            raise TelegramOfferQueueError("telegram_offer_queue_canonical_channel_mismatch")
        message_id = identity.message_id

    freshness_deadline_at: datetime | None = None
    if selected_action in OFFER_PUBLISH_ACTIONS:
        freshness_deadline_at = (
            _normalized_time(publication_freshness_deadline_at)
            if isinstance(publication_freshness_deadline_at, datetime)
            else offer_publication_freshness_deadline(
                offer,
                offer_expiry_minutes=int(offer_expiry_minutes or 0),
            )
        )
        if current_time >= freshness_deadline_at:
            return TelegramOfferQueueHandoffResult(
                offer_public_id=offer_public_id,
                action=selected_action,
                queue_result=None,
                skipped_reason="offer_publication_deadline_passed",
            )

    await _supersede_obsolete_offer_jobs(
        db,
        offer_public_id=offer_public_id,
        source_version=source_version,
        now=current_time,
    )
    is_publish = selected_action in OFFER_PUBLISH_ACTIONS
    feeder = (
        TelegramFeederKind.OFFER_CONTROL
        if is_publish
        else (
            TelegramFeederKind.TRADE
            if selected_action == TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT
            else TelegramFeederKind.OFFER_EDIT
        )
    )
    queue_result = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=feeder,
        source_natural_id=offer_public_id,
        source_version=source_version,
        action=selected_action,
        bot_identity=(
            TELEGRAM_PRIMARY_BOT_IDENTITY
            if is_publish
            else configured_offer_edit_bot_identity()
        ),
        destination_key=telegram_channel_destination_key(channel_id),
        destination_class=TelegramDestinationClass.CHANNEL,
        method="sendMessage" if is_publish else "editMessageText",
        payload=build_authoritative_offer_delivery_payload(
            offer,
            action=selected_action,
            expected_channel_id=channel_id,
            message_id=message_id,
        ),
        template_version=OFFER_QUEUE_TEMPLATE_VERSION,
        freshness_deadline_at=freshness_deadline_at,
    )
    return TelegramOfferQueueHandoffResult(
        offer_public_id=offer_public_id,
        action=selected_action,
        queue_result=queue_result,
    )


async def load_offer_publication_queue_candidates(
    db: AsyncSession,
    *,
    limit: int,
) -> list[TelegramOfferQueueCandidate]:
    state = aliased(OfferPublicationState)
    rows = (
        await db.execute(
            select(Offer, state)
            .outerjoin(
                state,
                and_(
                    state.offer_public_id == Offer.offer_public_id,
                    state.surface == OfferPublicationSurface.TELEGRAM_CHANNEL,
                ),
            )
            .options(selectinload(Offer.commodity))
            .where(
                Offer.status == OfferStatus.ACTIVE,
                or_(
                    state.id.is_(None),
                    state.telegram_message_id.is_(None),
                ),
                or_(
                    state.id.is_(None),
                    state.status.in_(
                        (
                            OfferPublicationStatus.PENDING,
                            OfferPublicationStatus.FAILED,
                            OfferPublicationStatus.LAGGED,
                        )
                    ),
                ),
            )
            .order_by(Offer.created_at.asc(), Offer.id.asc())
            .limit(max(1, int(limit)))
            .with_for_update(of=Offer, skip_locked=True)
        )
    ).all()
    return [TelegramOfferQueueCandidate(offer=row[0], state=row[1]) for row in rows]


async def load_offer_edit_queue_candidates(
    db: AsyncSession,
    *,
    limit: int,
) -> list[TelegramOfferQueueCandidate]:
    state = aliased(OfferPublicationState)
    edit_rank = case(
        (
            and_(
                Offer.status == OfferStatus.ACTIVE,
                Offer.remaining_quantity.is_not(None),
                Offer.remaining_quantity < Offer.quantity,
            ),
            0,
        ),
        (Offer.status == OfferStatus.COMPLETED, 1),
        (Offer.status == OfferStatus.EXPIRED, 2),
        (Offer.status == OfferStatus.CANCELLED, 3),
        else_=4,
    )
    rows = (
        await db.execute(
            select(Offer, state)
            .join(
                state,
                and_(
                    state.offer_public_id == Offer.offer_public_id,
                    state.surface == OfferPublicationSurface.TELEGRAM_CHANNEL,
                ),
            )
            .options(selectinload(Offer.commodity))
            .where(
                state.telegram_message_id.is_not(None),
                or_(
                    state.offer_version_id.is_(None),
                    state.offer_version_id != Offer.version_id,
                ),
            )
            # Internal action rank is resolved before handing work to the main
            # queue. Within one rank, the newest Offer is always released first.
            .order_by(edit_rank.asc(), Offer.created_at.desc(), Offer.id.desc())
            .limit(max(1, int(limit)))
            .with_for_update(of=Offer, skip_locked=True)
        )
    ).all()
    return [TelegramOfferQueueCandidate(offer=row[0], state=row[1]) for row in rows]
