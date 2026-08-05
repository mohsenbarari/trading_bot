"""Durable overtime request workflow on the shared OfferRequest ledger.

Stage 4 owns create, queue, promote, cancel, reject, and decision-timeout
transitions. Trade commit after owner approval is Stage 5. Stage 8 wires
bot-origin ``OVERTIME_DELIVERING`` rows onto the Telegram delivery queue;
WebApp surfaces remain later stages.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Awaitable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.offer_lifecycle import (
    OfferRequestIntakePhase,
    classify_request_intake_phase,
    compute_lifecycle_deadlines,
    project_offer_lifecycle,
    read_overtime_minutes_snapshot,
)
from core.server_routing import current_server, normalize_server
from core.services.offer_request_ledger_service import (
    OfferRequestTerminalStateError,
    apply_offer_request_decision,
    load_offer_request_by_idempotency,
    normalize_offer_request_source_surface,
    normalize_offer_request_status,
)
from core.utils import utc_now
from models.offer import Offer, OfferStatus
from models.offer_request import (
    OVERTIME_COOLDOWN_TRIGGER_STATUSES,
    OVERTIME_NONTERMINAL_STATUSES,
    OVERTIME_OWNER_OCCUPYING_STATUSES,
    OVERTIME_TERMINAL_STATUSES,
    OfferRequest,
    OfferRequestSourceSurface,
    OfferRequestStatus,
    OfferRequestWorkflow,
)


OVERTIME_DECISION_SECONDS = 30
OVERTIME_COOLDOWN_SECONDS = 30
MAX_OUTSTANDING_REQUESTS_PER_REQUESTER = 3

#: Inventory M16 / M17 / M20 / M20b — returned as ``detail`` on structured errors.
SAME_OFFER_BUSY_MESSAGE = (
    "درخواست دیگری برای این لفظ در حال بررسی است؛ لطفاً {remaining} ثانیه دیگر دوباره تلاش کنید."
)
COOLDOWN_MESSAGE = (
    "برای ارسال مجدد درخواست روی این لفظ، لطفاً {remaining} ثانیه دیگر تلاش کنید."
)
REQUESTER_LIMIT_MESSAGE = (
    "شما هم‌زمان ۳ درخواست باز دارید. لطفاً تا تعیین تکلیف یکی از آن‌ها صبر کنید."
)
REQUESTER_OWNER_LIMIT_MESSAGE = (
    "فعلاً نمی‌توانید روی این لفظ درخواست بدهید. لطفاً کمی بعد دوباره تلاش کنید."
)
NOT_OWNER_MESSAGE = "فقط صاحب این لفظ می‌تواند درباره این درخواست تصمیم بگیرد."
DECISION_EXPIRED_MESSAGE = "مهلت پاسخ به این درخواست تمام شده است."
ALREADY_TERMINAL_MESSAGE = "این درخواست قبلاً تعیین تکلیف شده است."
IDEMPOTENCY_REQUIRED_MESSAGE = "کلید تکرار درخواست الزامی است."
INTAKE_REJECTED_MESSAGE = "این لفظ دیگر فعال نیست."


class OvertimeRequestErrorCode(str, Enum):
    IDEMPOTENCY_REQUIRED = "idempotency_required"
    INTAKE_REJECTED = "intake_rejected"
    SAME_OFFER_BUSY = "same_offer_busy"
    COOLDOWN_ACTIVE = "cooldown_active"
    REQUESTER_LIMIT = "requester_limit"
    REQUESTER_OWNER_LIMIT = "requester_owner_limit"
    NOT_OWNER = "not_owner"
    DECISION_EXPIRED = "decision_expired"
    ALREADY_TERMINAL = "already_terminal"
    ILLEGAL_TRANSITION = "illegal_transition"
    NOT_FOUND = "not_found"
    NOT_REQUESTER = "not_requester"
    OFFER_INVALID = "offer_invalid"


class OvertimeRequestError(Exception):
    def __init__(
        self,
        code: OvertimeRequestErrorCode,
        detail: str,
        *,
        remaining_seconds: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.remaining_seconds = remaining_seconds


#: Legal overtime transitions. COMPLETED_TRADE is applied by Stage 5 after a
#: successful trade commit and is therefore listed as a terminal target from
#: PRESENTED only through ``record_completed_trade``.
LEGAL_OVERTIME_TRANSITIONS: dict[OfferRequestStatus, frozenset[OfferRequestStatus]] = {
    OfferRequestStatus.OVERTIME_QUEUED: frozenset(
        {
            OfferRequestStatus.OVERTIME_DELIVERING,
            OfferRequestStatus.OVERTIME_PRESENTED,
            OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER,
            OfferRequestStatus.OVERTIME_INVALIDATED,
            OfferRequestStatus.OVERTIME_DELIVERY_EXPIRED,
        }
    ),
    OfferRequestStatus.OVERTIME_DELIVERING: frozenset(
        {
            OfferRequestStatus.OVERTIME_PRESENTED,
            OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER,
            OfferRequestStatus.OVERTIME_INVALIDATED,
            OfferRequestStatus.OVERTIME_DELIVERY_EXPIRED,
        }
    ),
    OfferRequestStatus.OVERTIME_PRESENTED: frozenset(
        {
            OfferRequestStatus.OVERTIME_REJECTED_BY_OWNER,
            OfferRequestStatus.OVERTIME_DECISION_EXPIRED,
            OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER,
            OfferRequestStatus.OVERTIME_INVALIDATED,
            OfferRequestStatus.COMPLETED_TRADE,
        }
    ),
}

_TERMINAL_LIKE = frozenset(OVERTIME_TERMINAL_STATUSES) | {
    OfferRequestStatus.COMPLETED_TRADE,
    OfferRequestStatus.DUPLICATE_REPLAY,
    OfferRequestStatus.FAILED_INTERNAL,
}


@dataclass(frozen=True)
class OvertimeRequestCreateCommand:
    offer: Offer
    requester_user_id: int
    actor_user_id: int
    requested_quantity: int
    idempotency_key: str
    request_source_surface: OfferRequestSourceSurface | str
    request_source_server: str
    receipt_at: datetime
    normal_lifetime_minutes: int
    request_home_server: str | None = None
    customer_relation_id: int | None = None
    customer_owner_user_id: int | None = None
    customer_tier_snapshot: str | None = None
    customer_management_name_snapshot: str | None = None
    customer_commission_rate_snapshot: Any = None
    customer_commission_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class OvertimeRequestResult:
    ledger: OfferRequest
    duplicate_replay: bool = False
    promoted: bool = False


def assert_legal_overtime_transition(
    current: OfferRequestStatus | str,
    new: OfferRequestStatus | str,
) -> None:
    current_status = normalize_offer_request_status(current)
    new_status = normalize_offer_request_status(new)
    if current_status == new_status:
        return
    allowed = LEGAL_OVERTIME_TRANSITIONS.get(current_status, frozenset())
    if new_status not in allowed:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.ILLEGAL_TRANSITION,
            f"illegal overtime transition: {current_status.value} -> {new_status.value}",
        )


def decision_deadline_at(
    presented_at: datetime,
    *,
    seconds: int = OVERTIME_DECISION_SECONDS,
) -> datetime:
    return presented_at + timedelta(seconds=int(seconds))


def remaining_seconds_until(deadline: datetime | None, *, now: datetime | None = None) -> int:
    if deadline is None:
        return 0
    current = now or utc_now()
    # Compare in UTC-aware space when possible.
    left = deadline
    right = current
    if getattr(left, "tzinfo", None) is None and getattr(right, "tzinfo", None) is not None:
        from datetime import timezone

        left = left.replace(tzinfo=timezone.utc)
    if getattr(right, "tzinfo", None) is None and getattr(left, "tzinfo", None) is not None:
        from datetime import timezone

        right = right.replace(tzinfo=timezone.utc)
    return max(0, int((left - right).total_seconds()))


def cooldown_remaining_seconds(
    last_terminal_at: datetime | None,
    *,
    now: datetime | None = None,
    window_seconds: int = OVERTIME_COOLDOWN_SECONDS,
) -> int:
    if last_terminal_at is None:
        return 0
    ends = last_terminal_at + timedelta(seconds=int(window_seconds))
    return remaining_seconds_until(ends, now=now)


def _format_remaining_message(template: str, remaining: int) -> str:
    return template.format(remaining=max(1, int(remaining)))


def _status_value(status: Any) -> str:
    return str(getattr(status, "value", status) or "")


async def load_overtime_request_by_public_id(
    db: AsyncSession,
    request_public_id: str,
    *,
    for_update: bool = False,
) -> OfferRequest | None:
    public_id = (request_public_id or "").strip()
    if not public_id:
        return None
    stmt = select(OfferRequest).where(OfferRequest.request_public_id == public_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_active_request_for_offer(
    db: AsyncSession,
    *,
    request_home_server: str,
    offer_public_id: str,
    for_update: bool = False,
) -> OfferRequest | None:
    stmt = select(OfferRequest).where(
        OfferRequest.request_home_server == normalize_server(request_home_server, current_server()),
        OfferRequest.offer_public_id == offer_public_id,
        OfferRequest.result_status.in_(OVERTIME_NONTERMINAL_STATUSES),
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def count_requester_outstanding(
    db: AsyncSession,
    *,
    requester_user_id: int,
    offer_owner_user_id: int | None = None,
) -> tuple[int, int]:
    """Return (total_outstanding, outstanding_against_owner)."""
    total_result = await db.execute(
        select(func.count(OfferRequest.id)).where(
            OfferRequest.requester_user_id == requester_user_id,
            OfferRequest.result_status.in_(OVERTIME_NONTERMINAL_STATUSES),
        )
    )
    total = int(total_result.scalar_one() or 0)
    if offer_owner_user_id is None:
        return total, 0
    owner_result = await db.execute(
        select(func.count(OfferRequest.id)).where(
            OfferRequest.requester_user_id == requester_user_id,
            OfferRequest.offer_owner_user_id == offer_owner_user_id,
            OfferRequest.result_status.in_(OVERTIME_NONTERMINAL_STATUSES),
        )
    )
    return total, int(owner_result.scalar_one() or 0)


async def _cooldown_remaining_for_requester_offer(
    db: AsyncSession,
    *,
    requester_user_id: int,
    offer_public_id: str,
    now: datetime,
) -> int:
    result = await db.execute(
        select(OfferRequest.decided_at)
        .where(
            OfferRequest.requester_user_id == requester_user_id,
            OfferRequest.offer_public_id == offer_public_id,
            OfferRequest.result_status.in_(OVERTIME_COOLDOWN_TRIGGER_STATUSES),
            OfferRequest.decided_at.is_not(None),
        )
        .order_by(OfferRequest.decided_at.desc(), OfferRequest.id.desc())
        .limit(1)
    )
    last_decided_at = result.scalar_one_or_none()
    return cooldown_remaining_seconds(last_decided_at, now=now)


async def _next_queue_sequence(
    db: AsyncSession,
    *,
    request_home_server: str,
    offer_owner_user_id: int,
) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(OfferRequest.queue_sequence), 0)).where(
            OfferRequest.request_home_server == request_home_server,
            OfferRequest.offer_owner_user_id == offer_owner_user_id,
            OfferRequest.workflow_kind == OfferRequestWorkflow.OVERTIME,
        )
    )
    return int(result.scalar_one() or 0) + 1


async def _owner_has_occupying_request(
    db: AsyncSession,
    *,
    request_home_server: str,
    offer_owner_user_id: int,
) -> bool:
    result = await db.execute(
        select(OfferRequest.id).where(
            OfferRequest.request_home_server == request_home_server,
            OfferRequest.offer_owner_user_id == offer_owner_user_id,
            OfferRequest.result_status.in_(OVERTIME_OWNER_OCCUPYING_STATUSES),
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def next_queued_for_owner(
    db: AsyncSession,
    *,
    request_home_server: str,
    offer_owner_user_id: int,
    for_update: bool = False,
) -> OfferRequest | None:
    stmt = (
        select(OfferRequest)
        .where(
            OfferRequest.request_home_server == normalize_server(request_home_server, current_server()),
            OfferRequest.offer_owner_user_id == offer_owner_user_id,
            OfferRequest.result_status == OfferRequestStatus.OVERTIME_QUEUED,
        )
        .order_by(OfferRequest.queue_sequence.asc(), OfferRequest.id.asc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _apply_overtime_transition(
    ledger: OfferRequest,
    *,
    new_status: OfferRequestStatus,
    decided_at: datetime | None = None,
    terminal_reason: str | None = None,
    decided_by_user_id: int | None = None,
    public_failure_code: str | None = None,
    public_failure_message: str | None = None,
    internal_failure_code: str | None = None,
    internal_failure_context: dict[str, Any] | None = None,
    resulting_trade_id: int | None = None,
) -> OfferRequest:
    current = normalize_offer_request_status(ledger.result_status)
    assert_legal_overtime_transition(current, new_status)
    return apply_offer_request_decision(
        ledger,
        result_status=new_status,
        decided_at=decided_at,
        terminal_reason=terminal_reason,
        decided_by_user_id=decided_by_user_id,
        public_failure_code=public_failure_code,
        public_failure_message=public_failure_message,
        internal_failure_code=internal_failure_code,
        internal_failure_context=internal_failure_context,
        resulting_trade_id=resulting_trade_id,
    )


def _classify_intake_for_offer(
    offer: Offer,
    *,
    receipt_at: datetime,
    normal_lifetime_minutes: int,
) -> OfferRequestIntakePhase:
    overtime = read_overtime_minutes_snapshot(offer)
    normal_deadline, final_deadline = compute_lifecycle_deadlines(
        getattr(offer, "created_at", None),
        normal_lifetime_minutes=normal_lifetime_minutes,
        overtime_minutes_snapshot=overtime,
    )
    return classify_request_intake_phase(
        receipt_at=receipt_at,
        normal_deadline_at=normal_deadline,
        final_deadline_at=final_deadline,
        overtime_minutes_snapshot=overtime,
    )


async def create_overtime_request(
    db: AsyncSession,
    command: OvertimeRequestCreateCommand,
    *,
    flush: bool = True,
    now: datetime | None = None,
) -> OvertimeRequestResult:
    """Create a durable overtime request on the offer home server.

    Does not commit. Caller holds transactional responsibility and any offer
    row locks. Idempotent on ``(home, idempotency_key)``.
    """
    key = (command.idempotency_key or "").strip()
    if not key:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.IDEMPOTENCY_REQUIRED,
            IDEMPOTENCY_REQUIRED_MESSAGE,
        )
    if int(command.requested_quantity) <= 0:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.OFFER_INVALID,
            "requested_quantity must be positive",
        )

    offer = command.offer
    offer_public_id = (getattr(offer, "offer_public_id", None) or "").strip()
    if not offer_public_id:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.OFFER_INVALID,
            "offer_public_id is required",
        )
    home = normalize_server(
        command.request_home_server or getattr(offer, "home_server", None),
        current_server(),
    )
    current = now or utc_now()

    existing = await load_offer_request_by_idempotency(
        db,
        request_home_server=home,
        idempotency_key=key,
    )
    if existing is not None:
        return OvertimeRequestResult(ledger=existing, duplicate_replay=True)

    intake = _classify_intake_for_offer(
        offer,
        receipt_at=command.receipt_at,
        normal_lifetime_minutes=command.normal_lifetime_minutes,
    )
    if intake != OfferRequestIntakePhase.APPROVAL:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.INTAKE_REJECTED,
            INTAKE_REJECTED_MESSAGE,
        )

    if _status_value(getattr(offer, "status", None)) != OfferStatus.ACTIVE.value:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.OFFER_INVALID,
            INTAKE_REJECTED_MESSAGE,
        )

    owner_user_id = int(getattr(offer, "user_id"))
    if int(command.requester_user_id) == owner_user_id:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.OFFER_INVALID,
            "نمی‌توانید روی لفظ خودتان معامله کنید.",
        )

    active = await get_active_request_for_offer(
        db,
        request_home_server=home,
        offer_public_id=offer_public_id,
    )
    if active is not None:
        remaining = remaining_seconds_until(
            getattr(active, "decision_deadline_at", None),
            now=current,
        )
        if remaining <= 0:
            # Queued/delivering rows may not have a decision deadline yet.
            remaining = OVERTIME_DECISION_SECONDS
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.SAME_OFFER_BUSY,
            _format_remaining_message(SAME_OFFER_BUSY_MESSAGE, remaining),
            remaining_seconds=remaining,
        )

    cooldown_left = await _cooldown_remaining_for_requester_offer(
        db,
        requester_user_id=int(command.requester_user_id),
        offer_public_id=offer_public_id,
        now=current,
    )
    if cooldown_left > 0:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.COOLDOWN_ACTIVE,
            _format_remaining_message(COOLDOWN_MESSAGE, cooldown_left),
            remaining_seconds=cooldown_left,
        )

    total_open, owner_open = await count_requester_outstanding(
        db,
        requester_user_id=int(command.requester_user_id),
        offer_owner_user_id=owner_user_id,
    )
    if total_open >= MAX_OUTSTANDING_REQUESTS_PER_REQUESTER:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.REQUESTER_LIMIT,
            REQUESTER_LIMIT_MESSAGE,
        )
    if owner_open >= 1:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.REQUESTER_OWNER_LIMIT,
            REQUESTER_OWNER_LIMIT_MESSAGE,
        )

    sequence = await _next_queue_sequence(
        db,
        request_home_server=home,
        offer_owner_user_id=owner_user_id,
    )
    source_surface = normalize_offer_request_source_surface(command.request_source_surface)
    ledger = OfferRequest(
        request_home_server=home,
        local_offer_id=getattr(offer, "id", None),
        offer_public_id=offer_public_id,
        requester_user_id=int(command.requester_user_id),
        actor_user_id=int(command.actor_user_id),
        request_source_surface=source_surface,
        request_source_server=normalize_server(command.request_source_server, current_server()),
        requested_quantity=int(command.requested_quantity),
        idempotency_key=key,
        workflow_kind=OfferRequestWorkflow.OVERTIME,
        offer_owner_user_id=owner_user_id,
        queue_sequence=sequence,
        result_status=OfferRequestStatus.OVERTIME_QUEUED,
        received_at=command.receipt_at,
        customer_relation_id=command.customer_relation_id,
        customer_owner_user_id=command.customer_owner_user_id,
        customer_tier_snapshot=command.customer_tier_snapshot,
        customer_management_name_snapshot=command.customer_management_name_snapshot,
        customer_commission_rate_snapshot=command.customer_commission_rate_snapshot,
        customer_commission_context=command.customer_commission_context,
    )
    db.add(ledger)
    if flush:
        await db.flush()

    promoted = await promote_next_for_owner(
        db,
        request_home_server=home,
        offer_owner_user_id=owner_user_id,
        normal_lifetime_minutes=command.normal_lifetime_minutes,
        now=current,
        flush=flush,
    )
    return OvertimeRequestResult(
        ledger=ledger,
        duplicate_replay=False,
        promoted=promoted is ledger,
    )


async def _revalidate_offer_for_promotion(
    offer: Offer | None,
    *,
    normal_lifetime_minutes: int,
    now: datetime,
) -> str | None:
    """Return an invalidation reason, or None when promotion may proceed."""
    if offer is None:
        return "offer_missing"
    if _status_value(getattr(offer, "status", None)) != OfferStatus.ACTIVE.value:
        return "offer_not_active"
    projection = project_offer_lifecycle(
        offer,
        normal_lifetime_minutes=normal_lifetime_minutes,
        as_of=now,
        has_final_tail_request=False,
    )
    # A queued request may only become actionable while the offer still accepts
    # overtime intake or is still inside its public lifetime. Past final, Stage 6
    # invalidates the queue; Stage 4 refuses promotion.
    if projection.final_deadline_at is not None:
        deadline = projection.final_deadline_at
        current = now
        if getattr(deadline, "tzinfo", None) is None and getattr(current, "tzinfo", None) is not None:
            from datetime import timezone

            deadline = deadline.replace(tzinfo=timezone.utc)
        if current >= deadline:
            return "final_deadline_passed"
    return None


async def promote_next_for_owner(
    db: AsyncSession,
    *,
    request_home_server: str,
    offer_owner_user_id: int,
    normal_lifetime_minutes: int,
    now: datetime | None = None,
    load_offer: Callable[[AsyncSession, int], Awaitable[Offer | None]] | None = None,
    flush: bool = True,
) -> OfferRequest | None:
    """Promote the next queued request for an owner scope, if the seat is free.

    WebApp-origin requests become PRESENTED (clock starts). Bot-origin requests
    become DELIVERING (occupying; clock starts later when a message id lands).
    Always advances the earliest ``queue_sequence`` so FIFO is preserved.
    """
    home = normalize_server(request_home_server, current_server())
    current = now or utc_now()
    if await _owner_has_occupying_request(
        db,
        request_home_server=home,
        offer_owner_user_id=offer_owner_user_id,
    ):
        return None

    candidate = await next_queued_for_owner(
        db,
        request_home_server=home,
        offer_owner_user_id=offer_owner_user_id,
        for_update=True,
    )
    if candidate is None:
        return None

    offer = None
    local_offer_id = getattr(candidate, "local_offer_id", None)
    if local_offer_id is not None:
        if load_offer is not None:
            offer = await load_offer(db, int(local_offer_id))
        else:
            offer = await db.get(Offer, int(local_offer_id))

    invalid_reason = await _revalidate_offer_for_promotion(
        offer,
        normal_lifetime_minutes=normal_lifetime_minutes,
        now=current,
    )
    if invalid_reason is not None:
        _apply_overtime_transition(
            candidate,
            new_status=OfferRequestStatus.OVERTIME_INVALIDATED,
            decided_at=current,
            terminal_reason=invalid_reason,
            internal_failure_code="overtime_promote_revalidation_failed",
            internal_failure_context={"reason": invalid_reason},
        )
        if flush:
            await db.flush()
        # Try the next queued row after invalidating this one.
        return await promote_next_for_owner(
            db,
            request_home_server=home,
            offer_owner_user_id=offer_owner_user_id,
            normal_lifetime_minutes=normal_lifetime_minutes,
            now=current,
            load_offer=load_offer,
            flush=flush,
        )

    surface = normalize_offer_request_source_surface(candidate.request_source_surface)
    if surface == OfferRequestSourceSurface.TELEGRAM_BOT:
        _apply_overtime_transition(
            candidate,
            new_status=OfferRequestStatus.OVERTIME_DELIVERING,
        )
        if flush:
            await db.flush()
        # Lazy import keeps the ledger module free of queue package cycles.
        from core.services.telegram_overtime_owner_approval_queue_service import (
            enqueue_overtime_owner_approval_delivery,
        )

        if offer is None:
            await invalidate_request(
                db,
                candidate,
                reason="overtime_promote_offer_missing_for_delivery",
                now=current,
                flush=flush,
            )
            return await promote_next_for_owner(
                db,
                request_home_server=home,
                offer_owner_user_id=offer_owner_user_id,
                normal_lifetime_minutes=normal_lifetime_minutes,
                now=current,
                load_offer=load_offer,
                flush=flush,
            )
        enqueue_outcome = await enqueue_overtime_owner_approval_delivery(
            db,
            current_server=current_server(),
            ledger=candidate,
            offer=offer,
            normal_lifetime_minutes=normal_lifetime_minutes,
            now=current,
        )
        if enqueue_outcome.undeliverable_reason:
            await invalidate_request(
                db,
                candidate,
                reason=str(enqueue_outcome.undeliverable_reason),
                now=current,
                flush=flush,
            )
            return await promote_next_for_owner(
                db,
                request_home_server=home,
                offer_owner_user_id=offer_owner_user_id,
                normal_lifetime_minutes=normal_lifetime_minutes,
                now=current,
                load_offer=load_offer,
                flush=flush,
            )
        from core.services.telegram_overtime_requester_status_service import (
            schedule_requester_status_presented_edit,
        )

        await schedule_requester_status_presented_edit(db, candidate)
        return candidate

    candidate.presented_at = current
    candidate.decision_deadline_at = decision_deadline_at(current)
    _apply_overtime_transition(
        candidate,
        new_status=OfferRequestStatus.OVERTIME_PRESENTED,
    )
    if flush:
        await db.flush()
    from core.services.telegram_overtime_requester_status_service import (
        schedule_requester_status_presented_edit,
    )

    await schedule_requester_status_presented_edit(db, candidate)
    return candidate


async def mark_presented(
    db: AsyncSession,
    ledger: OfferRequest,
    *,
    presented_at: datetime | None = None,
    telegram_message_id: int | None = None,
    flush: bool = True,
) -> OfferRequest:
    """Start the owner decision clock (bot path after Telegram accepts)."""
    current = normalize_offer_request_status(ledger.result_status)
    if current == OfferRequestStatus.OVERTIME_PRESENTED:
        return ledger
    stamp = presented_at or utc_now()
    ledger.presented_at = stamp
    ledger.decision_deadline_at = decision_deadline_at(stamp)
    if telegram_message_id is not None:
        ledger.telegram_message_id = int(telegram_message_id)
    _apply_overtime_transition(ledger, new_status=OfferRequestStatus.OVERTIME_PRESENTED)
    if flush:
        await db.flush()
    from core.services.telegram_overtime_requester_status_service import (
        schedule_requester_status_presented_edit,
    )

    await schedule_requester_status_presented_edit(db, ledger)
    return ledger


async def cancel_by_requester(
    db: AsyncSession,
    ledger: OfferRequest,
    *,
    requester_user_id: int,
    now: datetime | None = None,
    flush: bool = True,
    promote_next: bool = True,
    normal_lifetime_minutes: int | None = None,
) -> OfferRequest:
    if int(getattr(ledger, "requester_user_id", 0) or 0) != int(requester_user_id):
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.NOT_REQUESTER,
            ALREADY_TERMINAL_MESSAGE,
        )
    current = normalize_offer_request_status(ledger.result_status)
    if current in _TERMINAL_LIKE:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.ALREADY_TERMINAL,
            ALREADY_TERMINAL_MESSAGE,
        )
    stamp = now or utc_now()
    owner_id = getattr(ledger, "offer_owner_user_id", None)
    home = getattr(ledger, "request_home_server", None)
    _apply_overtime_transition(
        ledger,
        new_status=OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER,
        decided_at=stamp,
        terminal_reason="requester_cancelled",
    )
    if flush:
        await db.flush()
    if promote_next and owner_id is not None and home and normal_lifetime_minutes is not None:
        await promote_next_for_owner(
            db,
            request_home_server=home,
            offer_owner_user_id=int(owner_id),
            normal_lifetime_minutes=int(normal_lifetime_minutes),
            now=stamp,
            flush=flush,
        )
    return ledger


async def reject_by_owner(
    db: AsyncSession,
    ledger: OfferRequest,
    *,
    decided_by_user_id: int,
    now: datetime | None = None,
    flush: bool = True,
    promote_next: bool = True,
    normal_lifetime_minutes: int | None = None,
) -> OfferRequest:
    await _ensure_owner_may_decide(ledger, decided_by_user_id=decided_by_user_id, now=now)
    stamp = now or utc_now()
    owner_id = getattr(ledger, "offer_owner_user_id", None)
    home = getattr(ledger, "request_home_server", None)
    _apply_overtime_transition(
        ledger,
        new_status=OfferRequestStatus.OVERTIME_REJECTED_BY_OWNER,
        decided_at=stamp,
        decided_by_user_id=int(decided_by_user_id),
        terminal_reason="owner_rejected",
    )
    if flush:
        await db.flush()
    if promote_next and owner_id is not None and home and normal_lifetime_minutes is not None:
        await promote_next_for_owner(
            db,
            request_home_server=home,
            offer_owner_user_id=int(owner_id),
            normal_lifetime_minutes=int(normal_lifetime_minutes),
            now=stamp,
            flush=flush,
        )
    return ledger


async def expire_decision(
    db: AsyncSession,
    ledger: OfferRequest,
    *,
    now: datetime | None = None,
    flush: bool = True,
    promote_next: bool = True,
    normal_lifetime_minutes: int | None = None,
) -> OfferRequest:
    current = normalize_offer_request_status(ledger.result_status)
    if current in _TERMINAL_LIKE:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.ALREADY_TERMINAL,
            ALREADY_TERMINAL_MESSAGE,
        )
    if current != OfferRequestStatus.OVERTIME_PRESENTED:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.ILLEGAL_TRANSITION,
            f"decision expiry requires presented status, got {current.value}",
        )
    stamp = now or utc_now()
    deadline = getattr(ledger, "decision_deadline_at", None)
    if deadline is not None and remaining_seconds_until(deadline, now=stamp) > 0:
        # Not yet at/after deadline; caller should wait.
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.ILLEGAL_TRANSITION,
            "decision deadline has not been reached",
        )
    owner_id = getattr(ledger, "offer_owner_user_id", None)
    home = getattr(ledger, "request_home_server", None)
    _apply_overtime_transition(
        ledger,
        new_status=OfferRequestStatus.OVERTIME_DECISION_EXPIRED,
        decided_at=stamp,
        terminal_reason="decision_timeout",
    )
    if flush:
        await db.flush()
    if promote_next and owner_id is not None and home and normal_lifetime_minutes is not None:
        await promote_next_for_owner(
            db,
            request_home_server=home,
            offer_owner_user_id=int(owner_id),
            normal_lifetime_minutes=int(normal_lifetime_minutes),
            now=stamp,
            flush=flush,
        )
    return ledger


async def _ensure_owner_may_decide(
    ledger: OfferRequest,
    *,
    decided_by_user_id: int,
    now: datetime | None = None,
) -> None:
    current = normalize_offer_request_status(ledger.result_status)
    if current in _TERMINAL_LIKE:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.ALREADY_TERMINAL,
            ALREADY_TERMINAL_MESSAGE,
        )
    if current != OfferRequestStatus.OVERTIME_PRESENTED:
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.ILLEGAL_TRANSITION,
            f"owner decision requires presented status, got {current.value}",
        )
    owner_id = getattr(ledger, "offer_owner_user_id", None)
    if owner_id is None or int(owner_id) != int(decided_by_user_id):
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.NOT_OWNER,
            NOT_OWNER_MESSAGE,
        )
    stamp = now or utc_now()
    deadline = getattr(ledger, "decision_deadline_at", None)
    if deadline is None or remaining_seconds_until(deadline, now=stamp) <= 0:
        # Exact deadline and after are expired (decision 37).
        raise OvertimeRequestError(
            OvertimeRequestErrorCode.DECISION_EXPIRED,
            DECISION_EXPIRED_MESSAGE,
        )


async def claim_owner_approval(
    ledger: OfferRequest,
    *,
    decided_by_user_id: int,
    now: datetime | None = None,
) -> OfferRequest:
    """Validate that the owner may approve; leave status for Stage 5 trade commit.

    Returns the same ledger after authorization checks. The caller must then
    commit the trade and call ``record_completed_trade``.
    """
    await _ensure_owner_may_decide(
        ledger,
        decided_by_user_id=decided_by_user_id,
        now=now,
    )
    return ledger


def record_completed_trade(
    ledger: OfferRequest,
    *,
    resulting_trade_id: int,
    decided_by_user_id: int,
    decided_at: datetime | None = None,
) -> OfferRequest:
    """Terminal success after Stage 5 commits the authoritative trade."""
    return _apply_overtime_transition(
        ledger,
        new_status=OfferRequestStatus.COMPLETED_TRADE,
        decided_at=decided_at or utc_now(),
        decided_by_user_id=int(decided_by_user_id),
        terminal_reason="completed_trade",
        resulting_trade_id=int(resulting_trade_id),
    )


async def invalidate_request(
    db: AsyncSession,
    ledger: OfferRequest,
    *,
    reason: str,
    now: datetime | None = None,
    flush: bool = True,
) -> OfferRequest:
    current = normalize_offer_request_status(ledger.result_status)
    if current in _TERMINAL_LIKE:
        return ledger
    if current not in {
        OfferRequestStatus.OVERTIME_QUEUED,
        OfferRequestStatus.OVERTIME_DELIVERING,
        OfferRequestStatus.OVERTIME_PRESENTED,
    }:
        return ledger
    _apply_overtime_transition(
        ledger,
        new_status=OfferRequestStatus.OVERTIME_INVALIDATED,
        decided_at=now or utc_now(),
        terminal_reason=reason,
        internal_failure_code="overtime_invalidated",
        internal_failure_context={"reason": reason},
    )
    if flush:
        await db.flush()
    return ledger


async def list_nonterminal_overtime_requests(
    db: AsyncSession,
    *,
    local_offer_id: int | None = None,
    offer_public_id: str | None = None,
    offer_owner_user_id: int | None = None,
    requester_user_id: int | None = None,
    request_home_server: str | None = None,
    for_update: bool = False,
) -> list[OfferRequest]:
    """Load nonterminal overtime rows matching any provided filter.

    Filters combine with AND. At least one identity filter is required so a bare
    call cannot scan the whole ledger.
    """
    if (
        local_offer_id is None
        and not (offer_public_id or "").strip()
        and offer_owner_user_id is None
        and requester_user_id is None
    ):
        raise ValueError("list_nonterminal_overtime_requests requires an identity filter")

    stmt = select(OfferRequest).where(
        OfferRequest.workflow_kind == OfferRequestWorkflow.OVERTIME,
        OfferRequest.result_status.in_(OVERTIME_NONTERMINAL_STATUSES),
    )
    if local_offer_id is not None:
        stmt = stmt.where(OfferRequest.local_offer_id == int(local_offer_id))
    public_id = (offer_public_id or "").strip()
    if public_id:
        stmt = stmt.where(OfferRequest.offer_public_id == public_id)
    if offer_owner_user_id is not None:
        stmt = stmt.where(OfferRequest.offer_owner_user_id == int(offer_owner_user_id))
    if requester_user_id is not None:
        stmt = stmt.where(OfferRequest.requester_user_id == int(requester_user_id))
    if request_home_server is not None:
        stmt = stmt.where(
            OfferRequest.request_home_server
            == normalize_server(request_home_server, current_server())
        )
    stmt = stmt.order_by(OfferRequest.id.asc())
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def invalidate_overtime_requests(
    db: AsyncSession,
    *,
    reason: str,
    local_offer_id: int | None = None,
    offer_public_id: str | None = None,
    offer_owner_user_id: int | None = None,
    requester_user_id: int | None = None,
    request_home_server: str | None = None,
    now: datetime | None = None,
    promote_next: bool = True,
    normal_lifetime_minutes: int | None = None,
    flush: bool = True,
) -> list[OfferRequest]:
    """Invalidate matching nonterminal overtime rows and optionally free owner seats.

    Promotion runs only after invalidation in the same transaction, and only for
    scopes that lost an occupying/queued row. ``promote_next_for_owner`` still
    revalidates each candidate offer before presenting it.
    """
    stamp = now or utc_now()
    rows = await list_nonterminal_overtime_requests(
        db,
        local_offer_id=local_offer_id,
        offer_public_id=offer_public_id,
        offer_owner_user_id=offer_owner_user_id,
        requester_user_id=requester_user_id,
        request_home_server=request_home_server,
        for_update=True,
    )
    scopes: set[tuple[int, str]] = set()
    invalidated: list[OfferRequest] = []
    for row in rows:
        before = normalize_offer_request_status(row.result_status)
        await invalidate_request(db, row, reason=reason, now=stamp, flush=False)
        after = normalize_offer_request_status(row.result_status)
        if before != after and after == OfferRequestStatus.OVERTIME_INVALIDATED:
            invalidated.append(row)
            owner_id = getattr(row, "offer_owner_user_id", None)
            home = getattr(row, "request_home_server", None)
            if owner_id is not None and home:
                scopes.add((int(owner_id), normalize_server(home, current_server())))
    if flush:
        await db.flush()
    if (
        promote_next
        and scopes
        and normal_lifetime_minutes is not None
    ):
        for owner_id, home in sorted(scopes):
            await promote_next_for_owner(
                db,
                request_home_server=home,
                offer_owner_user_id=owner_id,
                normal_lifetime_minutes=int(normal_lifetime_minutes),
                now=stamp,
                flush=flush,
            )
    return invalidated


async def invalidate_overtime_requests_for_offer(
    db: AsyncSession,
    offer: Offer | object,
    *,
    reason: str,
    now: datetime | None = None,
    promote_next: bool = True,
    normal_lifetime_minutes: int | None = None,
    flush: bool = True,
) -> list[OfferRequest]:
    local_id = getattr(offer, "id", None)
    public_id = (getattr(offer, "offer_public_id", None) or "").strip() or None
    home = normalize_server(getattr(offer, "home_server", None), current_server())
    return await invalidate_overtime_requests(
        db,
        reason=reason,
        local_offer_id=int(local_id) if local_id is not None else None,
        offer_public_id=public_id,
        request_home_server=home,
        now=now,
        promote_next=promote_next,
        normal_lifetime_minutes=normal_lifetime_minutes,
        flush=flush,
    )


async def invalidate_overtime_requests_for_user(
    db: AsyncSession,
    *,
    user_id: int,
    reason: str,
    request_home_server: str | None = None,
    now: datetime | None = None,
    promote_next: bool = True,
    normal_lifetime_minutes: int | None = None,
    flush: bool = True,
) -> list[OfferRequest]:
    """Invalidate every nonterminal overtime row where the user is owner or requester."""
    home = normalize_server(request_home_server, current_server())
    as_owner = await invalidate_overtime_requests(
        db,
        reason=reason,
        offer_owner_user_id=int(user_id),
        request_home_server=home,
        now=now,
        promote_next=False,
        flush=False,
    )
    as_requester = await invalidate_overtime_requests(
        db,
        reason=reason,
        requester_user_id=int(user_id),
        request_home_server=home,
        now=now,
        promote_next=False,
        flush=False,
    )
    # Deduplicate by id while preserving order.
    seen: set[int] = set()
    merged: list[OfferRequest] = []
    scopes: set[tuple[int, str]] = set()
    for row in (*as_owner, *as_requester):
        row_id = int(getattr(row, "id", 0) or 0)
        if row_id and row_id in seen:
            continue
        if row_id:
            seen.add(row_id)
        merged.append(row)
        owner_id = getattr(row, "offer_owner_user_id", None)
        row_home = getattr(row, "request_home_server", None)
        if owner_id is not None and row_home:
            scopes.add((int(owner_id), normalize_server(row_home, current_server())))
    if flush:
        await db.flush()
    stamp = now or utc_now()
    if promote_next and scopes and normal_lifetime_minutes is not None:
        for owner_id, row_home in sorted(scopes):
            await promote_next_for_owner(
                db,
                request_home_server=row_home,
                offer_owner_user_id=owner_id,
                normal_lifetime_minutes=int(normal_lifetime_minutes),
                now=stamp,
                flush=flush,
            )
    return merged
