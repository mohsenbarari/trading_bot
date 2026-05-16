"""Core state helpers for the web-only single-session recovery flow."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.session import (
    SessionLoginRequest,
    SingleSessionRecoveryRequest,
    SingleSessionRecoveryStatus,
)


INLINE_ACTION_WINDOW_SECONDS = 30
CHAT_ACTION_WINDOW_SECONDS = 2 * 60 * 60

ACTIVE_SINGLE_SESSION_RECOVERY_STATUSES = frozenset(
    {
        SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
        SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED,
        SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
    }
)

TERMINAL_SINGLE_SESSION_RECOVERY_STATUSES = frozenset(
    {
        SingleSessionRecoveryStatus.APPROVED,
        SingleSessionRecoveryStatus.REJECTED,
        SingleSessionRecoveryStatus.CANCELLED,
        SingleSessionRecoveryStatus.EXPIRED,
    }
)


class InvalidSingleSessionRecoveryTransition(ValueError):
    """Raised when a recovery request is moved through an invalid state transition."""


def _utcnow(now: Optional[datetime] = None) -> datetime:
    return now or datetime.utcnow()


def _extend_action_windows(
    recovery_request: SingleSessionRecoveryRequest,
    *,
    now: datetime,
    inline_window_seconds: int = INLINE_ACTION_WINDOW_SECONDS,
    chat_window_seconds: int = CHAT_ACTION_WINDOW_SECONDS,
) -> None:
    recovery_request.inline_action_expires_at = now + timedelta(seconds=inline_window_seconds)
    recovery_request.chat_action_expires_at = now + timedelta(seconds=chat_window_seconds)


def _close_action_windows(
    recovery_request: SingleSessionRecoveryRequest,
    *,
    now: datetime,
) -> None:
    recovery_request.inline_action_expires_at = now
    recovery_request.chat_action_expires_at = now


def is_active_recovery_status(status: SingleSessionRecoveryStatus) -> bool:
    return status in ACTIVE_SINGLE_SESSION_RECOVERY_STATUSES


def is_terminal_recovery_status(status: SingleSessionRecoveryStatus) -> bool:
    return status in TERMINAL_SINGLE_SESSION_RECOVERY_STATUSES


def _coerce_recovery_result(value) -> Optional[SingleSessionRecoveryRequest]:
    if value is None:
        return None
    status = getattr(value, "status", None)
    if isinstance(status, SingleSessionRecoveryStatus):
        return value
    if isinstance(status, str):
        try:
            SingleSessionRecoveryStatus(status)
            return value
        except ValueError:
            return None
    return None


def _ensure_status(
    recovery_request: SingleSessionRecoveryRequest,
    *,
    allowed_statuses: set[SingleSessionRecoveryStatus],
    action_label: str,
) -> None:
    if recovery_request.status not in allowed_statuses:
        raise InvalidSingleSessionRecoveryTransition(
            f"Cannot {action_label} from status {recovery_request.status.value}"
        )


async def get_active_recovery_request_for_login_request(
    db: AsyncSession,
    login_request_id,
) -> Optional[SingleSessionRecoveryRequest]:
    stmt = (
        select(SingleSessionRecoveryRequest)
        .where(
            and_(
                SingleSessionRecoveryRequest.session_login_request_id == login_request_id,
                SingleSessionRecoveryRequest.status.in_(
                    [status.value for status in ACTIVE_SINGLE_SESSION_RECOVERY_STATUSES]
                ),
            )
        )
        .order_by(desc(SingleSessionRecoveryRequest.created_at))
        .limit(1)
    )
    return _coerce_recovery_result((await db.execute(stmt)).scalar_one_or_none())


async def get_latest_recovery_request_for_login_request(
    db: AsyncSession,
    login_request_id,
) -> Optional[SingleSessionRecoveryRequest]:
    stmt = (
        select(SingleSessionRecoveryRequest)
        .where(SingleSessionRecoveryRequest.session_login_request_id == login_request_id)
        .order_by(desc(SingleSessionRecoveryRequest.created_at))
        .limit(1)
    )
    return _coerce_recovery_result((await db.execute(stmt)).scalar_one_or_none())


async def create_recovery_request(
    db: AsyncSession,
    login_request: SessionLoginRequest,
    *,
    now: Optional[datetime] = None,
    inline_window_seconds: int = INLINE_ACTION_WINDOW_SECONDS,
    chat_window_seconds: int = CHAT_ACTION_WINDOW_SECONDS,
) -> SingleSessionRecoveryRequest:
    existing = await get_active_recovery_request_for_login_request(db, login_request.id)
    if existing is not None:
        return existing

    current_time = _utcnow(now)
    recovery_request = SingleSessionRecoveryRequest(
        user_id=login_request.user_id,
        session_login_request_id=login_request.id,
        requester_device_name=login_request.requester_device_name,
        requester_ip=login_request.requester_ip,
        status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
        inline_action_expires_at=current_time + timedelta(seconds=inline_window_seconds),
        chat_action_expires_at=current_time + timedelta(seconds=chat_window_seconds),
    )
    db.add(recovery_request)
    if hasattr(db, "flush"):
        await db.flush()
    return recovery_request


def request_identity_verification(
    recovery_request: SingleSessionRecoveryRequest,
    *,
    now: Optional[datetime] = None,
) -> SingleSessionRecoveryRequest:
    _ensure_status(
        recovery_request,
        allowed_statuses={
            SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
        },
        action_label="request identity verification",
    )
    current_time = _utcnow(now)
    recovery_request.status = SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED
    recovery_request.identity_requested_at = current_time
    _extend_action_windows(recovery_request, now=current_time)
    return recovery_request


def submit_identity_material(
    recovery_request: SingleSessionRecoveryRequest,
    *,
    now: Optional[datetime] = None,
) -> SingleSessionRecoveryRequest:
    _ensure_status(
        recovery_request,
        allowed_statuses={SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED},
        action_label="submit identity material",
    )
    current_time = _utcnow(now)
    recovery_request.status = SingleSessionRecoveryStatus.IDENTITY_SUBMITTED
    recovery_request.identity_submitted_at = current_time
    _extend_action_windows(recovery_request, now=current_time)
    return recovery_request


def approve_recovery_request(
    recovery_request: SingleSessionRecoveryRequest,
    *,
    decided_by_user_id: int,
    now: Optional[datetime] = None,
) -> SingleSessionRecoveryRequest:
    _ensure_status(
        recovery_request,
        allowed_statuses={
            SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
        },
        action_label="approve recovery request",
    )
    current_time = _utcnow(now)
    recovery_request.status = SingleSessionRecoveryStatus.APPROVED
    recovery_request.decided_at = current_time
    recovery_request.decided_by_user_id = decided_by_user_id
    _close_action_windows(recovery_request, now=current_time)
    return recovery_request


def reject_recovery_request(
    recovery_request: SingleSessionRecoveryRequest,
    *,
    decided_by_user_id: int,
    now: Optional[datetime] = None,
) -> SingleSessionRecoveryRequest:
    _ensure_status(
        recovery_request,
        allowed_statuses={
            SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED,
            SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
        },
        action_label="reject recovery request",
    )
    current_time = _utcnow(now)
    recovery_request.status = SingleSessionRecoveryStatus.REJECTED
    recovery_request.decided_at = current_time
    recovery_request.decided_by_user_id = decided_by_user_id
    _close_action_windows(recovery_request, now=current_time)
    return recovery_request


def cancel_recovery_request(
    recovery_request: SingleSessionRecoveryRequest,
    *,
    now: Optional[datetime] = None,
) -> SingleSessionRecoveryRequest:
    _ensure_status(
        recovery_request,
        allowed_statuses=set(ACTIVE_SINGLE_SESSION_RECOVERY_STATUSES),
        action_label="cancel recovery request",
    )
    current_time = _utcnow(now)
    recovery_request.status = SingleSessionRecoveryStatus.CANCELLED
    recovery_request.cancelled_at = current_time
    _close_action_windows(recovery_request, now=current_time)
    return recovery_request


def expire_recovery_request(
    recovery_request: SingleSessionRecoveryRequest,
    *,
    now: Optional[datetime] = None,
) -> SingleSessionRecoveryRequest:
    _ensure_status(
        recovery_request,
        allowed_statuses=set(ACTIVE_SINGLE_SESSION_RECOVERY_STATUSES),
        action_label="expire recovery request",
    )
    current_time = _utcnow(now)
    recovery_request.status = SingleSessionRecoveryStatus.EXPIRED
    _close_action_windows(recovery_request, now=current_time)
    return recovery_request