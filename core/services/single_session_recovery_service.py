"""Core state helpers for the web-only single-session recovery flow."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from core.utils import utc_now_naive
from models.session import (
    SessionLoginRequest,
    SingleSessionRecoveryAdminTarget,
    SingleSessionRecoveryRequest,
    SingleSessionRecoveryStatus,
)
from models.user import User, UserRole


INLINE_ACTION_WINDOW_SECONDS = 30
CHAT_ACTION_WINDOW_SECONDS = 2 * 60 * 60
ADMIN_RECOVERY_ROLES = frozenset({UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER})

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
    return now or utc_now_naive()


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


def get_recovery_requester_display_name(user: User | object) -> str:
    for field_name in ("account_name", "full_name", "mobile_number"):
        value = getattr(user, field_name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    user_id = getattr(user, "id", None)
    if user_id is not None:
        return f"کاربر {user_id}"
    return "کاربر"


def build_initial_recovery_request_text(display_name: str) -> str:
    return (
        f"کاربر [{display_name}] بعلت عدم دسترسی به دستگاه قبلی خود؛ درخواست منقضی کردن "
        f"اطلاعات لاگین خود از دستگاه قدیم و لاگین کردن در دستگاه جدید را دارد."
    )


def build_identity_submitted_text(display_name: str) -> str:
    return f"کاربر [{display_name}] مدارک احراز هویت خود را برای بررسی ارسال کرده است."


def build_identity_requested_sms_text() -> str:
    return (
        "برای ادامه بررسی درخواست ورود از دستگاه جدید، تیم مدیریت نیاز به احراز هویت شما از طریق "
        "ارسال تصویر کارت شناسایی دارد. برای ادامه، وارد سامانه شوید و مدارک را ارسال کنید."
    )


def build_identity_submitted_sms_text() -> str:
    return "مدارک احراز هویت شما برای تیم مدیریت ارسال شد و در حال بررسی است. نتیجه از طریق پیامک به شما اعلام میشود."


def build_recovery_approved_sms_text(*, after_identity_review: bool) -> str:
    if after_identity_review:
        return (
            "درخواست شما پس از بررسی مدارک احراز هویت تایید شد. اطلاعات لاگین دستگاه قبلی شما منقضی شد و اکنون میتوانید وارد سامانه شوید."
        )
    return (
        "درخواست شما برای ورود از دستگاه جدید تایید شد. اطلاعات لاگین دستگاه قبلی شما منقضی شد و اکنون میتوانید وارد سامانه شوید."
    )


def build_recovery_rejected_sms_text(*, after_identity_review: bool) -> str:
    if after_identity_review:
        return "درخواست شما پس از بررسی مدارک احراز هویت توسط تیم مدیریت رد شد. در صورت نیاز میتوانید دوباره درخواست خود را ثبت کنید."
    return "درخواست شما برای ورود از دستگاه جدید توسط تیم مدیریت رد شد. در صورت نیاز میتوانید دوباره درخواست خود را ثبت کنید."


def build_recovery_expired_sms_text() -> str:
    return (
        "مهلت بررسی درخواست شما برای ورود از دستگاه جدید به پایان رسید و پاسخی از تیم مدیریت دریافت نشد. "
        "در صورت نیاز میتوانید دوباره درخواست خود را ثبت کنید."
    )


def should_show_inline_recovery_prompt(
    recovery_request: SingleSessionRecoveryRequest,
    admin_target: SingleSessionRecoveryAdminTarget | object,
    *,
    now: Optional[datetime] = None,
) -> bool:
    current_time = _utcnow(now)
    if recovery_request.inline_action_expires_at.replace(tzinfo=None) <= current_time:
        return False
    if recovery_request.status == SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW:
        return True
    if recovery_request.status == SingleSessionRecoveryStatus.IDENTITY_SUBMITTED:
        return getattr(admin_target, "current_action_message_id", None) is not None
    return False


def build_recovery_message_action_payload(
    *,
    recovery_request: SingleSessionRecoveryRequest,
    requester_user: User | object,
    current_action_message_id: int,
) -> dict[str, object]:
    prompt_type = "identity_submitted"
    can_request_identity = False
    if recovery_request.status == SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW:
        prompt_type = "initial_request"
        can_request_identity = True

    return {
        "recovery_id": str(recovery_request.id),
        "status": recovery_request.status.value,
        "prompt_type": prompt_type,
        "expires_at": recovery_request.chat_action_expires_at.isoformat()
        if recovery_request.chat_action_expires_at
        else None,
        "can_approve": True,
        "can_reject": True,
        "can_request_identity": can_request_identity,
        "current_action_message_id": current_action_message_id,
        "user_id": getattr(requester_user, "id", None),
        "user_name": get_recovery_requester_display_name(requester_user),
    }


async def list_recovery_admin_users(db: AsyncSession) -> list[User]:
    result = await db.execute(
        select(User).where(
            and_(
                User.is_deleted == False,
                User.role.in_(tuple(ADMIN_RECOVERY_ROLES)),
            )
        )
    )
    users = list(result.scalars().all())
    return sorted(
        users,
        key=lambda user: (0 if user.role == UserRole.SUPER_ADMIN else 1, getattr(user, "id", 0)),
    )


async def list_recovery_admin_targets(
    db: AsyncSession,
    recovery_request_id,
) -> list[SingleSessionRecoveryAdminTarget]:
    result = await db.execute(
        select(SingleSessionRecoveryAdminTarget)
        .options(
            joinedload(SingleSessionRecoveryAdminTarget.recovery_request).joinedload(
                SingleSessionRecoveryRequest.user
            ),
            joinedload(SingleSessionRecoveryAdminTarget.recovery_request).joinedload(
                SingleSessionRecoveryRequest.session_login_request
            ),
        )
        .where(SingleSessionRecoveryAdminTarget.recovery_request_id == recovery_request_id)
        .order_by(SingleSessionRecoveryAdminTarget.created_at.asc())
    )
    return list(result.scalars().all())


async def get_recovery_admin_target(
    db: AsyncSession,
    *,
    recovery_id,
    admin_user_id: int,
) -> Optional[SingleSessionRecoveryAdminTarget]:
    result = await db.execute(
        select(SingleSessionRecoveryAdminTarget)
        .options(
            joinedload(SingleSessionRecoveryAdminTarget.recovery_request).joinedload(
                SingleSessionRecoveryRequest.user
            ),
            joinedload(SingleSessionRecoveryAdminTarget.recovery_request).joinedload(
                SingleSessionRecoveryRequest.session_login_request
            ),
        )
        .where(
            and_(
                SingleSessionRecoveryAdminTarget.recovery_request_id == recovery_id,
                SingleSessionRecoveryAdminTarget.admin_user_id == admin_user_id,
            )
        )
        .limit(1)
    )
    return result.scalars().first()


async def list_pending_admin_recovery_targets(
    db: AsyncSession,
    *,
    admin_user_id: int,
    now: Optional[datetime] = None,
) -> list[tuple[SingleSessionRecoveryAdminTarget, SingleSessionRecoveryRequest, User]]:
    current_time = _utcnow(now)
    result = await db.execute(
        select(SingleSessionRecoveryAdminTarget, SingleSessionRecoveryRequest, User)
        .join(
            SingleSessionRecoveryRequest,
            SingleSessionRecoveryAdminTarget.recovery_request_id == SingleSessionRecoveryRequest.id,
        )
        .join(User, User.id == SingleSessionRecoveryRequest.user_id)
        .where(
            and_(
                SingleSessionRecoveryAdminTarget.admin_user_id == admin_user_id,
                SingleSessionRecoveryRequest.status.in_(
                    (
                        SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
                        SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
                    )
                ),
                SingleSessionRecoveryRequest.inline_action_expires_at > current_time,
            )
        )
        .order_by(desc(SingleSessionRecoveryRequest.created_at))
    )

    rows: list[tuple[SingleSessionRecoveryAdminTarget, SingleSessionRecoveryRequest, User]] = []
    for admin_target, recovery_request, requester_user in result.all():
        if should_show_inline_recovery_prompt(recovery_request, admin_target, now=current_time):
            rows.append((admin_target, recovery_request, requester_user))
    return rows


async def build_recovery_action_map_for_admin_messages(
    db: AsyncSession,
    *,
    admin_user_id: int,
    message_ids: Sequence[int],
    now: Optional[datetime] = None,
) -> dict[int, dict[str, object]]:
    normalized_message_ids = sorted({int(message_id) for message_id in message_ids if int(message_id) > 0})
    if not normalized_message_ids:
        return {}

    current_time = _utcnow(now)
    result = await db.execute(
        select(SingleSessionRecoveryAdminTarget, SingleSessionRecoveryRequest, User)
        .join(
            SingleSessionRecoveryRequest,
            SingleSessionRecoveryAdminTarget.recovery_request_id == SingleSessionRecoveryRequest.id,
        )
        .join(User, User.id == SingleSessionRecoveryRequest.user_id)
        .where(
            and_(
                SingleSessionRecoveryAdminTarget.admin_user_id == admin_user_id,
                SingleSessionRecoveryAdminTarget.current_action_message_id.in_(normalized_message_ids),
                SingleSessionRecoveryRequest.chat_action_expires_at > current_time,
                SingleSessionRecoveryRequest.status.in_(
                    (
                        SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
                        SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
                    )
                ),
            )
        )
    )

    action_map: dict[int, dict[str, object]] = {}
    for admin_target, recovery_request, requester_user in result.all():
        current_action_message_id = getattr(admin_target, "current_action_message_id", None)
        if current_action_message_id is None:
            continue
        action_map[int(current_action_message_id)] = build_recovery_message_action_payload(
            recovery_request=recovery_request,
            requester_user=requester_user,
            current_action_message_id=int(current_action_message_id),
        )
    return action_map


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
