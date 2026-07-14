"""Canonical, authority-aware bulk cancellation for active offers."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from core.config import settings
from core.offer_expiry_contracts import (
    OfferExpiryCommandIdentityError,
    build_offer_expiry_forward_payload,
)
from core.offer_expiry_forwarding import forward_offer_expiry_to_home_server
from core.server_routing import current_server, normalize_server
from core.services.offer_expiry_gate import try_acquire_offer_expiry_gate
from core.services.offer_expiry_service import (
    OfferAlreadyInactiveError,
    OfferExpiryCommand,
    OfferExpirySourceSurface,
    OfferNotAuthoritativeError,
    expire_offer_authoritatively,
    is_offer_expiry_lock_busy,
)
from models.offer import Offer, OfferStatus


logger = logging.getLogger(__name__)


class OfferCancelAllItemStatus(str, Enum):
    CANCELLED = "cancelled"
    ALREADY_INACTIVE = "already_inactive"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OfferCancelAllCandidate:
    offer_id: int
    offer_public_id: str
    home_server: str
    snapshot_offer: Offer = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class OfferCancelAllItemResult:
    offer_public_id: str
    home_server: str
    status: OfferCancelAllItemStatus
    error_code: str | None = None
    detail: str | None = None
    retryable: bool = False
    replayed: bool = False
    local_offer: Offer | None = field(default=None, repr=False, compare=False)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "offer_public_id": self.offer_public_id,
            "home_server": self.home_server,
            "status": self.status.value,
            "retryable": self.retryable,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.detail:
            payload["detail"] = self.detail
        if self.replayed:
            payload["replayed"] = True
        return payload


@dataclass(frozen=True, slots=True)
class OfferCancelAllResult:
    items: tuple[OfferCancelAllItemResult, ...]
    remaining_active_count: int | None

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def cancelled_count(self) -> int:
        return sum(item.status == OfferCancelAllItemStatus.CANCELLED for item in self.items)

    @property
    def already_inactive_count(self) -> int:
        return sum(item.status == OfferCancelAllItemStatus.ALREADY_INACTIVE for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status == OfferCancelAllItemStatus.FAILED for item in self.items)

    @property
    def complete(self) -> bool:
        return self.failed_count == 0

    @property
    def locally_cancelled_offers(self) -> tuple[Offer, ...]:
        return tuple(
            item.local_offer
            for item in self.items
            if item.status == OfferCancelAllItemStatus.CANCELLED and item.local_offer is not None
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            # Keep the existing field for older WebApp clients.
            "cancelled_count": self.cancelled_count,
            "already_inactive_count": self.already_inactive_count,
            "failed_count": self.failed_count,
            "total_count": self.total_count,
            "remaining_active_count": self.remaining_active_count,
            "complete": self.complete,
            "results": [item.to_payload() for item in self.items],
        }


SessionFactory = Callable[[], Any]
OfferExpiryForwarder = Callable[[str, dict[str, Any]], Awaitable[tuple[int, Any]]]


def format_offer_cancel_all_bot_message(result: OfferCancelAllResult) -> str:
    if result.total_count == 0:
        return "شما هیچ لفظ فعالی ندارید."
    if result.failed_count:
        return (
            "⚠️ لغو همه لفظ‌ها کامل نشد.\n\n"
            f"لغوشده: {result.cancelled_count}\n"
            f"از قبل غیرفعال: {result.already_inactive_count}\n"
            f"لغونشده: {result.failed_count}\n\n"
            "لفظ‌های لغونشده همچنان ممکن است فعال باشند. لطفاً دوباره تلاش کنید."
        )
    if result.already_inactive_count:
        return (
            "✅ لغو لفظ‌های فعال تکمیل شد.\n\n"
            f"لغوشده: {result.cancelled_count}\n"
            f"از قبل غیرفعال: {result.already_inactive_count}"
        )
    return f"✅ تمام لفظ‌های فعال شما ({result.cancelled_count} لفظ) منقضی شدند."


async def _load_active_candidates(
    session_factory: SessionFactory,
    *,
    owner_user_id: int,
) -> tuple[OfferCancelAllCandidate, ...]:
    async with session_factory() as db:
        rows = await db.execute(
            select(Offer)
            .where(
                Offer.user_id == owner_user_id,
                Offer.status == OfferStatus.ACTIVE,
            )
            .options(selectinload(Offer.commodity))
            .order_by(Offer.offer_public_id.asc(), Offer.id.asc())
        )
        offers = rows.scalars().all()
    return tuple(
        OfferCancelAllCandidate(
            offer_id=int(offer.id),
            offer_public_id=str(getattr(offer, "offer_public_id", None) or ""),
            home_server=normalize_server(getattr(offer, "home_server", None), current_server()),
            snapshot_offer=offer,
        )
        for offer in offers
    )


def _item(
    candidate: OfferCancelAllCandidate,
    status: OfferCancelAllItemStatus,
    **kwargs: Any,
) -> OfferCancelAllItemResult:
    return OfferCancelAllItemResult(
        offer_public_id=candidate.offer_public_id,
        home_server=candidate.home_server,
        status=status,
        **kwargs,
    )


async def _read_offer_status(
    session_factory: SessionFactory,
    *,
    candidate: OfferCancelAllCandidate,
) -> OfferStatus | None:
    async with session_factory() as db:
        return await db.scalar(select(Offer.status).where(Offer.id == candidate.offer_id))


async def _classify_local_conflict(
    session_factory: SessionFactory,
    *,
    candidate: OfferCancelAllCandidate,
) -> OfferCancelAllItemResult:
    status = await _read_offer_status(session_factory, candidate=candidate)
    if status is not None and status != OfferStatus.ACTIVE:
        return _item(candidate, OfferCancelAllItemStatus.ALREADY_INACTIVE)
    return _item(
        candidate,
        OfferCancelAllItemStatus.FAILED,
        error_code="conflict",
        detail="این لفظ هم‌زمان در حال تغییر است.",
        retryable=True,
    )


async def _cancel_local_candidate(
    session_factory: SessionFactory,
    *,
    candidate: OfferCancelAllCandidate,
    owner_user_id: int,
    actor_user_id: int,
    source_surface: OfferExpirySourceSurface | str,
    expire_reason: str,
) -> OfferCancelAllItemResult:
    try:
        async with session_factory() as db:
            try:
                row = await db.execute(
                    select(Offer)
                    .where(Offer.id == candidate.offer_id)
                    .options(selectinload(Offer.commodity))
                    .with_for_update(nowait=True)
                )
                offer = row.scalar_one_or_none()
            except (OperationalError, DBAPIError) as exc:
                await db.rollback()
                if is_offer_expiry_lock_busy(exc):
                    return _item(
                        candidate,
                        OfferCancelAllItemStatus.FAILED,
                        error_code="lock_busy",
                        detail="این لفظ هم‌زمان در حال تغییر است.",
                        retryable=True,
                    )
                raise

            if offer is None:
                await db.rollback()
                return _item(
                    candidate,
                    OfferCancelAllItemStatus.FAILED,
                    error_code="not_found",
                    detail="لفظ یافت نشد.",
                )
            if offer.user_id != owner_user_id:
                await db.rollback()
                return _item(
                    candidate,
                    OfferCancelAllItemStatus.FAILED,
                    error_code="owner_mismatch",
                    detail="مالک لفظ با درخواست همخوانی ندارد.",
                )
            if normalize_server(getattr(offer, "home_server", None), current_server()) != current_server():
                await db.rollback()
                return _item(
                    candidate,
                    OfferCancelAllItemStatus.FAILED,
                    error_code="not_authoritative",
                    detail="این سرور مرجع لفظ نیست.",
                    retryable=True,
                )
            if offer.status != OfferStatus.ACTIVE:
                await db.rollback()
                return _item(candidate, OfferCancelAllItemStatus.ALREADY_INACTIVE)

            await expire_offer_authoritatively(
                db,
                offer,
                OfferExpiryCommand(
                    reason=expire_reason,
                    source_surface=source_surface,
                    source_server=current_server(),
                    expired_by_user_id=owner_user_id,
                    expired_by_actor_user_id=actor_user_id,
                ),
                commit=False,
            )
            await db.commit()
            return _item(
                candidate,
                OfferCancelAllItemStatus.CANCELLED,
                local_offer=offer,
            )
    except (OfferAlreadyInactiveError, StaleDataError):
        return await _classify_local_conflict(session_factory, candidate=candidate)
    except OfferNotAuthoritativeError:
        return _item(
            candidate,
            OfferCancelAllItemStatus.FAILED,
            error_code="not_authoritative",
            detail="این سرور مرجع لفظ نیست.",
            retryable=True,
        )


async def _cancel_remote_candidate(
    *,
    candidate: OfferCancelAllCandidate,
    owner_user_id: int,
    actor_user_id: int,
    source_surface: OfferExpirySourceSurface | str,
    expire_reason: str,
    include_command_identity: bool,
    forwarder: OfferExpiryForwarder,
) -> OfferCancelAllItemResult:
    try:
        payload = build_offer_expiry_forward_payload(
            candidate.snapshot_offer,
            owner_user_id=owner_user_id,
            actor_user_id=actor_user_id,
            source_surface=source_surface,
            source_server=current_server(),
            expire_reason=expire_reason,
            include_command_identity=include_command_identity,
        )
    except OfferExpiryCommandIdentityError:
        return _item(
            candidate,
            OfferCancelAllItemStatus.FAILED,
            error_code="identity_invalid",
            detail="شناسه عمومی لفظ برای انتقال معتبر نیست.",
        )

    status_code, body = await forwarder(candidate.home_server, payload)
    if status_code < 400:
        replayed = bool(body.get("replayed")) if isinstance(body, dict) else False
        return _item(
            candidate,
            OfferCancelAllItemStatus.CANCELLED,
            replayed=replayed,
        )
    if status_code == 400:
        return _item(candidate, OfferCancelAllItemStatus.ALREADY_INACTIVE)

    detail = body.get("detail") if isinstance(body, dict) else None
    return _item(
        candidate,
        OfferCancelAllItemStatus.FAILED,
        error_code=f"remote_{status_code}",
        detail=str(detail or "لغو لفظ روی سرور مرجع تأیید نشد."),
        retryable=status_code in {409, 429, 502, 503, 504},
    )


async def _read_remaining_active_count(
    session_factory: SessionFactory,
    *,
    owner_user_id: int,
    ignored_remote_offer_ids: tuple[int, ...],
) -> int:
    async with session_factory() as db:
        query = select(func.count(Offer.id)).where(
            Offer.user_id == owner_user_id,
            Offer.status == OfferStatus.ACTIVE,
        )
        if ignored_remote_offer_ids:
            query = query.where(Offer.id.not_in(ignored_remote_offer_ids))
        return int(await db.scalar(query) or 0)


async def cancel_all_active_offers_authoritatively(
    *,
    owner_user_id: int,
    actor_user_id: int | None,
    source_surface: OfferExpirySourceSurface | str,
    expire_reason: str,
    session_factory: SessionFactory | None = None,
    forwarder: OfferExpiryForwarder = forward_offer_expiry_to_home_server,
    include_command_identity: bool | None = None,
) -> OfferCancelAllResult:
    if session_factory is None:
        from core.db import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    actor_id = int(actor_user_id or owner_user_id)
    command_identity_enabled = (
        bool(settings.offer_expiry_command_receipts_enabled)
        if include_command_identity is None
        else bool(include_command_identity)
    )
    candidates = await _load_active_candidates(
        session_factory,
        owner_user_id=owner_user_id,
    )

    results: list[OfferCancelAllItemResult] = []
    ignored_remote_offer_ids: list[int] = []
    for candidate in candidates:
        lease = await try_acquire_offer_expiry_gate(offer_id=candidate.offer_id)
        if not lease.acquired:
            results.append(
                _item(
                    candidate,
                    OfferCancelAllItemStatus.FAILED,
                    error_code="gate_busy",
                    detail="این لفظ هم‌زمان در حال غیرفعال شدن است.",
                    retryable=True,
                )
            )
            continue
        try:
            try:
                if candidate.home_server == current_server():
                    item = await _cancel_local_candidate(
                        session_factory,
                        candidate=candidate,
                        owner_user_id=owner_user_id,
                        actor_user_id=actor_id,
                        source_surface=source_surface,
                        expire_reason=expire_reason,
                    )
                else:
                    item = await _cancel_remote_candidate(
                        candidate=candidate,
                        owner_user_id=owner_user_id,
                        actor_user_id=actor_id,
                        source_surface=source_surface,
                        expire_reason=expire_reason,
                        include_command_identity=command_identity_enabled,
                        forwarder=forwarder,
                    )
                    if item.status != OfferCancelAllItemStatus.FAILED:
                        ignored_remote_offer_ids.append(candidate.offer_id)
            except Exception as exc:
                logger.warning(
                    "offer_cancel_all_item_failed",
                    extra={
                        "event": "offer_cancel_all.item_failed",
                        "offer_id": candidate.offer_id,
                        "home_server": candidate.home_server,
                        "error_class": type(exc).__name__,
                    },
                )
                item = _item(
                    candidate,
                    OfferCancelAllItemStatus.FAILED,
                    error_code="unexpected_error",
                    detail="لغو این لفظ تأیید نشد.",
                    retryable=True,
                )
            results.append(item)
        finally:
            await lease.release()

    try:
        remaining_active_count = await _read_remaining_active_count(
            session_factory,
            owner_user_id=owner_user_id,
            ignored_remote_offer_ids=tuple(ignored_remote_offer_ids),
        )
    except Exception as exc:
        logger.warning(
            "offer_cancel_all_remaining_count_failed",
            extra={
                "event": "offer_cancel_all.remaining_count_failed",
                "owner_user_id": owner_user_id,
                "error_class": type(exc).__name__,
            },
        )
        remaining_active_count = None
    return OfferCancelAllResult(
        items=tuple(results),
        remaining_active_count=remaining_active_count,
    )
