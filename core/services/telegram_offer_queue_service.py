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

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from core.config import settings
from core.server_routing import SERVER_FOREIGN
from core.services.offer_publication_state_service import (
    canonical_telegram_publication_identity,
    ensure_telegram_publication_publisher_identity,
)
from core.services.telegram_publisher_dispatch_service import (
    TelegramPublisherDispatchError,
    get_or_create_telegram_publisher_dispatch_command,
    healthy_telegram_publisher_lane_identities,
    select_telegram_publisher_lane_for_job,
)
from core.services.telegram_delivery_queue_service import (
    TELEGRAM_PRIMARY_BOT_IDENTITY,
    TelegramDeliveryEnqueueResult,
    enqueue_telegram_delivery_job,
    telegram_delivery_database_now,
)
from core.services.telegram_offer_channel_service import (
    CHANNEL_LIFECYCLE_METADATA_KEY,
    project_offer_channel_lifecycle,
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
    EDIT_CATCH_UP_FRESH_COUNT,
    EDIT_STALE_AFTER_SECONDS,
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from models.offer import Offer, OfferStatus
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)
from models.telegram_delivery_feeder_state import TelegramDeliveryFeederState
from models.telegram_delivery_job import TelegramDeliveryJobRecord


OFFER_QUEUE_TEMPLATE_VERSION = "offer-channel-v1"
OFFER_PUBLICATION_DEADLINE_SAFETY_SECONDS = 5.0
_OFFER_EDIT_FEEDER_ACTIONS = frozenset(
    set(OFFER_FRESHNESS_ACTIONS)
    - set(OFFER_PUBLISH_ACTIONS)
    - {TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT}
)


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
    """Keep legacy offers on their publishing token until lane ownership exists.

    The former channel-editor route could edit a post created by ``primary``.
    Telegram rejects that active-post lifecycle in practice, so editor enablement
    must not influence offer publish/edit routing before immutable publisher
    ownership is introduced in a later migration stage.
    """
    return TELEGRAM_PRIMARY_BOT_IDENTITY


def _telegram_b2b_dispatch_enabled() -> bool:
    return bool(
        getattr(settings, "telegram_multi_publisher_enabled", False)
        and getattr(settings, "telegram_b2b_dispatch_enabled", False)
    )


def _normalized_fresh_success_counts(value: Any) -> dict[int, int]:
    if not isinstance(value, dict):
        raise TelegramOfferQueueError(
            "telegram_offer_edit_fairness_state_invalid"
        )
    normalized: dict[int, int] = {}
    for raw_rank, raw_count in value.items():
        try:
            rank = int(raw_rank)
            count = int(raw_count)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TelegramOfferQueueError(
                "telegram_offer_edit_fairness_state_invalid"
            ) from exc
        if rank < 0 or count < 0 or count > EDIT_CATCH_UP_FRESH_COUNT:
            raise TelegramOfferQueueError(
                "telegram_offer_edit_fairness_state_invalid"
            )
        normalized[rank] = count
    return normalized


async def load_offer_edit_fresh_success_counts(
    db: AsyncSession,
) -> dict[int, int]:
    row = (
        await db.execute(
            select(TelegramDeliveryFeederState).where(
                TelegramDeliveryFeederState.feeder_kind
                == TelegramFeederKind.OFFER_EDIT.value
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise TelegramOfferQueueError(
            "telegram_offer_edit_fairness_state_missing"
        )
    return _normalized_fresh_success_counts(row.fresh_success_counts)


async def record_offer_edit_delivery_success(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    *,
    now: datetime,
) -> dict[int, int]:
    if _enum_value(getattr(job, "feeder_kind", None)) != TelegramFeederKind.OFFER_EDIT.value:
        raise TelegramOfferQueueError(
            "telegram_offer_edit_fairness_feeder_invalid"
        )
    try:
        rank = int(getattr(job, "feeder_rank", None))
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramOfferQueueError(
            "telegram_offer_edit_fairness_rank_invalid"
        ) from exc
    if rank < 0:
        raise TelegramOfferQueueError(
            "telegram_offer_edit_fairness_rank_invalid"
        )

    first_enqueued_at = getattr(job, "eligible_at", None) or getattr(
        job, "created_at", None
    )
    if not isinstance(first_enqueued_at, datetime):
        raise TelegramOfferQueueError(
            "telegram_offer_edit_first_enqueued_at_missing"
        )
    first_enqueued_at = _normalized_time(first_enqueued_at)
    current_time = _normalized_time(now)
    if first_enqueued_at > current_time:
        raise TelegramOfferQueueError(
            "telegram_offer_edit_first_enqueued_at_future"
        )

    row = (
        await db.execute(
            select(TelegramDeliveryFeederState)
            .where(
                TelegramDeliveryFeederState.feeder_kind
                == TelegramFeederKind.OFFER_EDIT.value
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise TelegramOfferQueueError(
            "telegram_offer_edit_fairness_state_missing"
        )
    counts = _normalized_fresh_success_counts(row.fresh_success_counts)
    is_stale = (
        current_time - first_enqueued_at
    ).total_seconds() >= EDIT_STALE_AFTER_SECONDS
    counts[rank] = (
        0
        if is_stale
        else min(EDIT_CATCH_UP_FRESH_COUNT, counts.get(rank, 0) + 1)
    )
    row.fresh_success_counts = {
        str(item_rank): item_count
        for item_rank, item_count in sorted(counts.items())
    }
    row.updated_at = current_time
    await db.flush()
    return counts


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
    from core.offer_lifecycle import publication_freshness_deadline_at, read_overtime_minutes_snapshot

    created_at = getattr(offer, "created_at", None)
    if not isinstance(created_at, datetime):
        raise TelegramOfferQueueError("telegram_offer_queue_created_at_invalid")
    expiry_minutes = int(offer_expiry_minutes)
    if expiry_minutes <= 0:
        raise TelegramOfferQueueError("telegram_offer_queue_expiry_invalid")
    try:
        return publication_freshness_deadline_at(
            created_at,
            normal_lifetime_minutes=expiry_minutes,
            overtime_minutes_snapshot=read_overtime_minutes_snapshot(offer),
            safety_seconds=OFFER_PUBLICATION_DEADLINE_SAFETY_SECONDS,
        )
    except ValueError as exc:
        raise TelegramOfferQueueError("telegram_offer_queue_expiry_invalid") from exc


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


async def _offer_edit_first_enqueued_at(
    db: AsyncSession,
    *,
    offer_public_id: str,
    rendered_source_version: int | None,
    now: datetime,
) -> datetime:
    rendered_version = _positive_int(rendered_source_version) or 0
    first_enqueued_at = (
        await db.execute(
            select(
                func.min(
                    func.coalesce(
                        TelegramDeliveryJobRecord.eligible_at,
                        TelegramDeliveryJobRecord.created_at,
                    )
                )
            ).where(
                TelegramDeliveryJobRecord.feeder_kind
                == TelegramFeederKind.OFFER_EDIT,
                TelegramDeliveryJobRecord.source_natural_id == offer_public_id,
                TelegramDeliveryJobRecord.source_version > rendered_version,
                TelegramDeliveryJobRecord.action_kind.in_(
                    tuple(_OFFER_EDIT_FEEDER_ACTIONS)
                ),
            )
        )
    ).scalar_one_or_none()
    if first_enqueued_at is None:
        return _normalized_time(now)
    if not isinstance(first_enqueued_at, datetime):
        raise TelegramOfferQueueError(
            "telegram_offer_edit_first_enqueued_at_invalid"
        )
    normalized = _normalized_time(first_enqueued_at)
    if normalized > _normalized_time(now):
        raise TelegramOfferQueueError(
            "telegram_offer_edit_first_enqueued_at_future"
        )
    return normalized


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
    current_time = _normalized_time(
        now if now is not None else await telegram_delivery_database_now(db)
    )
    channel_id = _nonzero_int(
        expected_channel_id,
        reason="telegram_offer_queue_channel_invalid",
    )
    offer_public_id = str(getattr(offer, "offer_public_id", "") or "").strip()
    source_version = _positive_int(getattr(offer, "version_id", None))
    if not offer_public_id or source_version is None:
        raise TelegramOfferQueueError("telegram_offer_queue_source_invalid")

    b2b_dispatch_enabled = _telegram_b2b_dispatch_enabled()
    if state is None:
        state = await get_or_create_telegram_publication_state(
            db,
            offer,
            publisher_bot_identity=None if b2b_dispatch_enabled else TELEGRAM_PRIMARY_BOT_IDENTITY,
        )

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
    queue_bot_identity = configured_offer_edit_bot_identity()
    if is_publish:
        publisher = str(getattr(state, "publisher_bot_identity", "") or "").strip()
        if not publisher and b2b_dispatch_enabled:
            try:
                selection = await select_telegram_publisher_lane_for_job(
                    db,
                    healthy_publishers=healthy_telegram_publisher_lane_identities(),
                    round_robin_sequence=_positive_int(
                        getattr(offer, "id", None)
                    )
                    or source_version,
                )
            except TelegramPublisherDispatchError as exc:
                raise TelegramOfferQueueError(str(exc)) from exc
            publisher = ensure_telegram_publication_publisher_identity(
                state,
                publisher_bot_identity=selection.publisher_bot_identity,
            )
        queue_bot_identity = publisher or TELEGRAM_PRIMARY_BOT_IDENTITY
    elif str(getattr(state, "publisher_bot_identity", "") or "").strip().startswith(
        "publisher_"
    ):
        raise TelegramOfferQueueError("telegram_offer_queue_publisher_lifecycle_pending")
    feeder = (
        TelegramFeederKind.OFFER_CONTROL
        if is_publish
        else (
            TelegramFeederKind.TRADE
            if selected_action == TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT
            else TelegramFeederKind.OFFER_EDIT
        )
    )
    first_edit_enqueued_at = None
    if feeder == TelegramFeederKind.OFFER_EDIT:
        first_edit_enqueued_at = await _offer_edit_first_enqueued_at(
            db,
            offer_public_id=offer_public_id,
            rendered_source_version=getattr(state, "offer_version_id", None),
            now=current_time,
        )
    queue_result = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=feeder,
        source_natural_id=offer_public_id,
        source_version=source_version,
        action=selected_action,
        bot_identity=queue_bot_identity,
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
        eligible_at=first_edit_enqueued_at,
        freshness_deadline_at=freshness_deadline_at,
        source_order_at=(
            _normalized_time(getattr(offer, "created_at", None))
            if feeder == TelegramFeederKind.OFFER_EDIT
            and isinstance(getattr(offer, "created_at", None), datetime)
            else None
        ),
    )
    if is_publish and queue_bot_identity.startswith("publisher_"):
        try:
            await get_or_create_telegram_publisher_dispatch_command(
                db,
                current_server=current_server,
                job=queue_result.job,
                publisher_bot_identity=queue_bot_identity,
                now=current_time,
            )
        except TelegramPublisherDispatchError as exc:
            raise TelegramOfferQueueError(str(exc)) from exc
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
    catch_up_due_ranks: frozenset[int] = frozenset(),
    now: datetime | None = None,
) -> list[TelegramOfferQueueCandidate]:
    current_time = _normalized_time(
        now if now is not None else await telegram_delivery_database_now(db)
    )
    state = aliased(OfferPublicationState)
    queued_job = aliased(TelegramDeliveryJobRecord)
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
    first_edit_enqueued_at = (
        select(
            func.min(
                func.coalesce(
                    queued_job.eligible_at,
                    queued_job.created_at,
                )
            )
        )
        .where(
            queued_job.feeder_kind == TelegramFeederKind.OFFER_EDIT,
            queued_job.source_natural_id == Offer.offer_public_id,
            queued_job.source_version > func.coalesce(state.offer_version_id, 0),
            queued_job.action_kind.in_(tuple(_OFFER_EDIT_FEEDER_ACTIONS)),
        )
        .correlate(Offer, state)
        .scalar_subquery()
    )
    is_stale = first_edit_enqueued_at <= current_time - timedelta(
        seconds=EDIT_STALE_AFTER_SECONDS
    )
    due_ranks = tuple(sorted({int(rank) for rank in catch_up_due_ranks if int(rank) >= 0}))
    catch_up_stale = (
        and_(edit_rank.in_(due_ranks), is_stale)
        if due_ranks
        else false()
    )
    freshness_bucket = case(
        (catch_up_stale, 0),
        (is_stale, 2),
        else_=1,
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
            # Internal action rank is resolved before handoff. Fresh edits stay
            # ahead of stale edits within the same rank, except for the exact
            # one-in-twenty catch-up slot. Newer Offers lead each bucket.
            .order_by(
                edit_rank.asc(),
                freshness_bucket.asc(),
                Offer.created_at.desc(),
                Offer.id.desc(),
            )
            .limit(max(1, int(limit)))
            .with_for_update(of=Offer, skip_locked=True)
        )
    ).all()
    return [TelegramOfferQueueCandidate(offer=row[0], state=row[1]) for row in rows]


_LIFECYCLE_CHANNEL_EDIT_ACTIONS = {
    "overtime": TelegramDeliveryAction.OVERTIME_CHANNEL_EDIT,
    "final_tail": TelegramDeliveryAction.FINAL_TAIL_CHANNEL_EDIT,
}


def _publication_metadata_dict(state: OfferPublicationState | None) -> dict[str, Any]:
    raw = getattr(state, "state_metadata", None) if state is not None else None
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


async def enqueue_offer_lifecycle_channel_handoffs(
    db: AsyncSession,
    *,
    current_server: str,
    expected_channel_id: int,
    offer_expiry_minutes: int | None,
    limit: int,
    now: datetime | None = None,
) -> list[TelegramOfferQueueHandoffResult]:
    """Enqueue ACTIVE channel edits when wall-clock overtime/final-tail changes.

    Pure phase transitions do not bump ``Offer.version_id``, so the normal edit
    feeder cannot observe them. Track the last rendered phase on
    ``OfferPublicationState.state_metadata`` and enqueue dedicated lifecycle
    actions that share the current offer version (freshness-safe) while keeping
    distinct dedupe keys per phase.
    """
    if offer_expiry_minutes is None or int(offer_expiry_minutes) <= 0:
        return []

    results: list[TelegramOfferQueueHandoffResult] = []
    clock = _normalized_time(
        now if now is not None else await telegram_delivery_database_now(db)
    )
    channel_id = _nonzero_int(
        expected_channel_id,
        reason="telegram_offer_queue_channel_invalid",
    )
    rows = (
        await db.execute(
            select(Offer, OfferPublicationState)
            .join(
                OfferPublicationState,
                and_(
                    OfferPublicationState.offer_public_id == Offer.offer_public_id,
                    OfferPublicationState.surface
                    == OfferPublicationSurface.TELEGRAM_CHANNEL,
                ),
            )
            .options(selectinload(Offer.commodity))
            .where(
                Offer.status == OfferStatus.ACTIVE,
                Offer.version_id.is_not(None),
                OfferPublicationState.telegram_message_id.is_not(None),
                OfferPublicationState.telegram_chat_id == channel_id,
            )
            .order_by(
                func.coalesce(Offer.updated_at, Offer.created_at).asc(),
                Offer.id.asc(),
            )
            .limit(max(1, int(limit)))
            .with_for_update(of=Offer, skip_locked=True)
        )
    ).all()

    for offer, state in rows:
        projection = project_offer_channel_lifecycle(
            offer,
            normal_lifetime_minutes=int(offer_expiry_minutes),
            as_of=clock,
        )
        phase = projection.phase.value
        action = _LIFECYCLE_CHANNEL_EDIT_ACTIONS.get(phase)
        if action is None:
            continue
        metadata = _publication_metadata_dict(state)
        if str(metadata.get(CHANNEL_LIFECYCLE_METADATA_KEY) or "") == phase:
            continue
        try:
            handoff = await enqueue_current_offer_delivery(
                db,
                current_server=current_server,
                offer=offer,
                state=state,
                expected_channel_id=channel_id,
                offer_expiry_minutes=offer_expiry_minutes,
                action=action,
                now=clock,
            )
        except TelegramOfferQueueError:
            continue
        if handoff.queue_result is not None and handoff.skipped_reason is None:
            metadata[CHANNEL_LIFECYCLE_METADATA_KEY] = phase
            state.state_metadata = metadata
            await db.flush()
        results.append(handoff)
    return results
