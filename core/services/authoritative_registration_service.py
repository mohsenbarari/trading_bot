"""Single Iran-owned transaction for invitation registration completion."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import UserAccountStatus
from core.registration_contracts import (
    RegistrationIdentityProofType,
    RegistrationSourceSurface,
    TelegramRegistrationCommand,
    TelegramRegistrationOutcome,
)
from core.server_routing import SERVER_IRAN, current_server
from core.services.accountant_relation_service import (
    activate_accountant_relation_for_registration,
    lock_accountant_relation_for_registration,
)
from core.services.chat_room_service import ensure_mandatory_channel_membership
from core.services.customer_relation_service import (
    activate_customer_relation_for_registration,
    lock_customer_relation_for_registration,
)
from core.services.invitation_identity_reservation_service import (
    NormalizedInvitationIdentity,
    invitation_identity_lock_keys,
    normalize_invitation_identity,
    release_invitation_identity,
)
from core.services.invitation_lifecycle_service import (
    complete_invitation,
    invitation_kind_from_token,
    is_post_expiry_reconciliation_allowed,
    validate_registration_address,
)
from core.services.registration_command_receipt_service import (
    RegistrationCommandReplayConflict,
    finalize_registration_command_receipt,
    prepare_registration_command_receipt,
)
from core.services.registration_notification_service import (
    enqueue_project_user_joined_telegram_outbox,
    should_announce_project_user_registration,
)
from core.services.user_account_status_service import get_user_account_status
from core.utils import normalize_account_name, normalize_persian_numerals, utc_now
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.invitation import Invitation, InvitationCompletionSurface, InvitationKind
from models.telegram_registration_command_receipt import TelegramRegistrationCommandReceipt
from models.user import User, UserRole, set_legacy_has_bot_access_compatibility


RegistrationCheckpoint = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AuthoritativeRegistrationRequest:
    invitation_token: str
    source_surface: RegistrationSourceSurface
    identity_proof_type: RegistrationIdentityProofType
    address: str
    received_at: datetime
    telegram_command: TelegramRegistrationCommand | None = None
    source_server: str | None = None

    @classmethod
    def for_web(
        cls,
        *,
        invitation_token: str,
        address: str,
        received_at: datetime | None = None,
    ) -> "AuthoritativeRegistrationRequest":
        return cls(
            invitation_token=str(invitation_token or "").strip(),
            source_surface=RegistrationSourceSurface.WEBAPP,
            identity_proof_type=RegistrationIdentityProofType.WEB_OTP,
            address=validate_registration_address(address),
            received_at=received_at or utc_now(),
        )

    @classmethod
    def for_telegram(
        cls,
        *,
        command: TelegramRegistrationCommand,
        source_server: str,
        received_at: datetime | None = None,
    ) -> "AuthoritativeRegistrationRequest":
        return cls(
            invitation_token=command.invitation_token,
            source_surface=RegistrationSourceSurface.TELEGRAM_BOT,
            identity_proof_type=RegistrationIdentityProofType.TELEGRAM_CONTACT,
            address=validate_registration_address(command.address),
            received_at=received_at or utc_now(),
            telegram_command=command,
            source_server=str(source_server or "").strip().lower(),
        )


@dataclass(frozen=True, slots=True)
class AuthoritativeRegistrationResult:
    outcome: TelegramRegistrationOutcome
    authoritative_user_id: int | None
    user: User | None = None
    invitation: Invitation | None = None
    accountant_relation: AccountantRelation | None = None
    customer_relation: CustomerRelation | None = None
    replayed: bool = False
    first_terminal_transition: bool = False
    announce_project_user: bool = False


class AuthoritativeRegistrationError(RuntimeError):
    def __init__(
        self,
        outcome: TelegramRegistrationOutcome,
        *,
        public_detail: str,
        status_code: int = 400,
    ) -> None:
        self.outcome = outcome
        self.public_detail = public_detail
        self.status_code = int(status_code)
        super().__init__(outcome.value)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def _checkpoint(callback: RegistrationCheckpoint | None, name: str) -> None:
    if callback is not None:
        await callback(name)


def _error(
    outcome: TelegramRegistrationOutcome,
    detail: str,
    *,
    status_code: int = 400,
) -> AuthoritativeRegistrationError:
    return AuthoritativeRegistrationError(outcome, public_detail=detail, status_code=status_code)


def _validate_request_shape(request: AuthoritativeRegistrationRequest) -> None:
    if current_server() != SERVER_IRAN:
        raise RuntimeError("authoritative_registration_requires_iran")
    if not request.invitation_token:
        raise ValueError("invitation_token_required")
    validate_registration_address(request.address)
    if request.received_at.tzinfo is None or request.received_at.utcoffset() is None:
        raise ValueError("received_at_timezone_required")

    if request.source_surface == RegistrationSourceSurface.WEBAPP:
        if request.identity_proof_type != RegistrationIdentityProofType.WEB_OTP:
            raise ValueError("web_identity_proof_invalid")
        if request.telegram_command is not None or request.source_server is not None:
            raise ValueError("web_request_contains_telegram_context")
        return

    if request.source_surface != RegistrationSourceSurface.TELEGRAM_BOT:
        raise ValueError("registration_source_invalid")
    if request.identity_proof_type != RegistrationIdentityProofType.TELEGRAM_CONTACT:
        raise ValueError("telegram_identity_proof_invalid")
    if request.telegram_command is None:
        raise ValueError("telegram_command_required")


async def _load_invitation_for_update(db: AsyncSession, token: str) -> Invitation | None:
    stmt = select(Invitation).where(Invitation.token == token).with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def _acquire_registration_identity_locks(
    db: AsyncSession,
    *,
    identity: NormalizedInvitationIdentity,
    telegram_id: int | None,
) -> None:
    lock_keys = list(invitation_identity_lock_keys(identity))
    if telegram_id is not None:
        lock_keys.append(f"registration:telegram-id:{int(telegram_id)}")
    for lock_key in sorted(set(lock_keys)):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )


async def _load_matching_users_for_update(
    db: AsyncSession,
    *,
    invitation: Invitation,
    identity: NormalizedInvitationIdentity,
    telegram_id: int | None,
) -> list[User]:
    conditions = [
        User.mobile_number == identity.mobile_number,
        func.lower(User.account_name) == identity.account_name,
    ]
    if telegram_id is not None:
        conditions.append(User.telegram_id == int(telegram_id))
    if invitation.registered_user_id is not None:
        conditions.append(User.id == int(invitation.registered_user_id))
    stmt = select(User).where(or_(*conditions)).order_by(User.id.asc()).with_for_update()
    return list((await db.execute(stmt)).scalars().all())


def _validate_invitation_kind(invitation: Invitation) -> InvitationKind:
    try:
        kind = InvitationKind(_enum_value(invitation.kind))
    except ValueError as exc:
        raise _error(
            TelegramRegistrationOutcome.LEGACY_STATE_AMBIGUOUS,
            "وضعیت دعوت‌نامه قدیمی نامشخص است؛ دعوت‌نامه جدید دریافت کنید",
        ) from exc
    if kind == InvitationKind.LEGACY_UNKNOWN or invitation_kind_from_token(invitation.token) != kind:
        raise _error(
            TelegramRegistrationOutcome.LEGACY_STATE_AMBIGUOUS,
            "وضعیت دعوت‌نامه قدیمی نامشخص است؛ دعوت‌نامه جدید دریافت کنید",
        )
    return kind


def _validate_invitation_time(
    request: AuthoritativeRegistrationRequest,
    invitation: Invitation,
) -> None:
    expires_at = _utc_naive(invitation.expires_at)
    received_at = _utc_naive(request.received_at)
    if request.source_surface == RegistrationSourceSurface.WEBAPP:
        if received_at > expires_at:
            raise _error(TelegramRegistrationOutcome.INVITATION_EXPIRED, "دعوت‌نامه منقضی شده است")
        return

    command = request.telegram_command
    assert command is not None
    if _utc_naive(command.invitation_expires_at_snapshot) != expires_at:
        raise _error(
            TelegramRegistrationOutcome.INVALID_IDENTITY_PROOF,
            "اطلاعات تایید هویت معتبر نیست",
        )
    if _utc_naive(command.local_completed_at) > expires_at:
        raise _error(TelegramRegistrationOutcome.INVITATION_EXPIRED, "دعوت‌نامه منقضی شده است")
    if received_at <= expires_at:
        return
    if not is_post_expiry_reconciliation_allowed(
        invitation,
        proof_completed_at=command.local_completed_at,
        received_at=request.received_at,
        grace_seconds=settings.telegram_registration_post_expiry_grace_seconds,
    ):
        raise _error(TelegramRegistrationOutcome.INVITATION_EXPIRED, "دعوت‌نامه منقضی شده است")


async def _load_relation_for_registration(
    db: AsyncSession,
    *,
    invitation: Invitation,
    kind: InvitationKind,
) -> tuple[AccountantRelation | None, CustomerRelation | None]:
    if kind == InvitationKind.ACCOUNTANT:
        relation = await lock_accountant_relation_for_registration(db, invitation.token)
        if relation is None:
            raise _error(
                TelegramRegistrationOutcome.INVALID_RELATION,
                "دعوت‌نامه حسابدار نامعتبر یا منقضی شده است",
            )
        return relation, None
    if kind == InvitationKind.CUSTOMER:
        relation = await lock_customer_relation_for_registration(db, invitation.token)
        if relation is None:
            raise _error(
                TelegramRegistrationOutcome.INVALID_RELATION,
                "دعوت‌نامه مشتری نامعتبر یا منقضی شده است",
            )
        return None, relation
    return None, None


def _validate_pending_relation(
    *,
    request: AuthoritativeRegistrationRequest,
    invitation: Invitation,
    accountant_relation: AccountantRelation | None,
    customer_relation: CustomerRelation | None,
) -> None:
    proof_time = (
        request.telegram_command.local_completed_at
        if request.telegram_command is not None
        else request.received_at
    )
    proof_time_naive = _utc_naive(proof_time)
    if accountant_relation is not None:
        if (
            accountant_relation.status != AccountantRelationStatus.PENDING
            or accountant_relation.deleted_at is not None
            or accountant_relation.accountant_user_id is not None
            or _utc_naive(accountant_relation.expires_at) < proof_time_naive
        ):
            raise _error(
                TelegramRegistrationOutcome.INVALID_RELATION,
                "دعوت‌نامه حسابدار نامعتبر یا منقضی شده است",
            )
    if customer_relation is not None:
        expires_at = customer_relation.expires_at
        if (
            customer_relation.status != CustomerRelationStatus.PENDING
            or customer_relation.deleted_at is not None
            or customer_relation.customer_user_id is not None
            or expires_at is None
            or _utc_naive(expires_at) < proof_time_naive
        ):
            raise _error(
                TelegramRegistrationOutcome.INVALID_RELATION,
                "دعوت‌نامه مشتری نامعتبر یا منقضی شده است",
            )


def _validate_telegram_projection_eligibility(
    *,
    invitation: Invitation,
    accountant_relation: AccountantRelation | None,
    customer_relation: CustomerRelation | None,
) -> None:
    if accountant_relation is not None:
        raise _error(
            TelegramRegistrationOutcome.INVALID_RELATION,
            "این دعوت‌نامه فقط از طریق وب‌اپ قابل تکمیل است",
        )
    if (
        customer_relation is not None
        and _enum_value(customer_relation.customer_tier) != CustomerTier.TIER_1.value
    ):
        raise _error(
            TelegramRegistrationOutcome.INVALID_RELATION,
            "این دعوت‌نامه فقط از طریق وب‌اپ قابل تکمیل است",
        )
    allowed_roles = {
        UserRole.STANDARD.value,
        UserRole.POLICE.value,
        UserRole.MIDDLE_MANAGER.value,
        UserRole.SUPER_ADMIN.value,
    }
    if _enum_value(invitation.role) not in allowed_roles:
        raise _error(
            TelegramRegistrationOutcome.INVALID_RELATION,
            "این دعوت‌نامه فقط از طریق وب‌اپ قابل تکمیل است",
        )


def _registration_full_name(
    invitation: Invitation,
    accountant_relation: AccountantRelation | None,
    customer_relation: CustomerRelation | None,
) -> str:
    candidate = None
    if customer_relation is not None:
        candidate = customer_relation.management_name
    elif accountant_relation is not None:
        candidate = accountant_relation.relation_display_name
    return str(candidate or "").strip() or invitation.account_name


def _matching_user_maps(
    users: list[User],
    *,
    identity: NormalizedInvitationIdentity,
    telegram_id: int | None,
) -> tuple[list[User], list[User], list[User]]:
    mobile_users = [
        user
        for user in users
        if normalize_persian_numerals(str(user.mobile_number or "")).strip() == identity.mobile_number
    ]
    account_users = [
        user
        for user in users
        if normalize_account_name(str(user.account_name or "").strip()) == identity.account_name
    ]
    telegram_users = [user for user in users if telegram_id is not None and user.telegram_id == telegram_id]
    return mobile_users, account_users, telegram_users


def _raise_for_existing_user_state(users: list[User]) -> None:
    for user in users:
        if bool(user.is_deleted) or user.deleted_at is not None:
            raise _error(TelegramRegistrationOutcome.ACCOUNT_DELETED, "این حساب کاربری در دسترس نیست")
        if get_user_account_status(user) != UserAccountStatus.ACTIVE:
            raise _error(TelegramRegistrationOutcome.ACCOUNT_INACTIVE, "حساب کاربری غیرفعال شده است")


def _validate_pending_natural_keys(
    users: list[User],
    *,
    identity: NormalizedInvitationIdentity,
    telegram_id: int | None,
) -> None:
    if not users:
        return
    _raise_for_existing_user_state(users)
    mobile_users, account_users, telegram_users = _matching_user_maps(
        users,
        identity=identity,
        telegram_id=telegram_id,
    )
    if telegram_users:
        raise _error(
            TelegramRegistrationOutcome.TELEGRAM_ID_ALREADY_USED,
            "این حساب تلگرام قبلاً به کاربر دیگری متصل شده است",
        )
    if mobile_users:
        raise _error(
            TelegramRegistrationOutcome.MOBILE_CONFLICT,
            "کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است",
        )
    if account_users:
        raise _error(
            TelegramRegistrationOutcome.ACCOUNT_NAME_CONFLICT,
            "کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است",
        )
    raise _error(TelegramRegistrationOutcome.IDENTITY_CONFLICT, "اطلاعات هویتی با حساب موجود تداخل دارد")


def _registered_user_for_completed_invitation(
    invitation: Invitation,
    users: list[User],
    *,
    identity: NormalizedInvitationIdentity,
) -> User:
    if (
        invitation.registered_user_id is None
        or invitation.completed_at is None
        or invitation.completed_via is None
    ):
        raise _error(
            TelegramRegistrationOutcome.LEGACY_STATE_AMBIGUOUS,
            "وضعیت دعوت‌نامه قدیمی نامشخص است؛ دعوت‌نامه جدید دریافت کنید",
        )
    registered_user = next(
        (user for user in users if user.id == int(invitation.registered_user_id)),
        None,
    )
    if registered_user is None:
        raise _error(
            TelegramRegistrationOutcome.AUTHORITATIVE_USER_MISSING,
            "ثبت‌نام قابل بازیابی نیست؛ با پشتیبانی تماس بگیرید",
        )
    try:
        registered_identity = normalize_invitation_identity(
            mobile_number=registered_user.mobile_number,
            account_name=registered_user.account_name,
        )
    except ValueError as exc:
        raise _error(
            TelegramRegistrationOutcome.LEGACY_STATE_AMBIGUOUS,
            "وضعیت دعوت‌نامه قدیمی نامشخص است؛ دعوت‌نامه جدید دریافت کنید",
        ) from exc
    if registered_identity != identity:
        raise _error(
            TelegramRegistrationOutcome.LEGACY_STATE_AMBIGUOUS,
            "وضعیت دعوت‌نامه قدیمی نامشخص است؛ دعوت‌نامه جدید دریافت کنید",
        )
    return registered_user


def _validate_completed_relation(
    *,
    user: User,
    accountant_relation: AccountantRelation | None,
    customer_relation: CustomerRelation | None,
) -> None:
    if accountant_relation is not None and (
        accountant_relation.status != AccountantRelationStatus.ACTIVE
        or accountant_relation.deleted_at is not None
        or accountant_relation.accountant_user_id != user.id
    ):
        raise _error(
            TelegramRegistrationOutcome.INVALID_RELATION,
            "دعوت‌نامه حسابدار نامعتبر یا منقضی شده است",
        )
    if customer_relation is not None and (
        customer_relation.status != CustomerRelationStatus.ACTIVE
        or customer_relation.deleted_at is not None
        or customer_relation.customer_user_id != user.id
    ):
        raise _error(
            TelegramRegistrationOutcome.INVALID_RELATION,
            "دعوت‌نامه مشتری نامعتبر یا منقضی شده است",
        )


def _constraint_name(exc: IntegrityError) -> str | None:
    current: Any = getattr(exc, "orig", None)
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        direct = getattr(current, "constraint_name", None)
        if direct:
            return str(direct)
        diagnostic = getattr(current, "diag", None)
        diagnosed = getattr(diagnostic, "constraint_name", None) if diagnostic is not None else None
        if diagnosed:
            return str(diagnosed)
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return None


def _integrity_conflict(exc: IntegrityError) -> AuthoritativeRegistrationError | None:
    constraint = _constraint_name(exc)
    if constraint == "users_mobile_number_key":
        return _error(
            TelegramRegistrationOutcome.MOBILE_CONFLICT,
            "کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است",
        )
    if constraint == "users_account_name_key":
        return _error(
            TelegramRegistrationOutcome.ACCOUNT_NAME_CONFLICT,
            "کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است",
        )
    if constraint == "users_telegram_id_key":
        return _error(
            TelegramRegistrationOutcome.TELEGRAM_ID_ALREADY_USED,
            "این حساب تلگرام قبلاً به کاربر دیگری متصل شده است",
        )
    return None


def _receipt_replay_result(
    receipt: TelegramRegistrationCommandReceipt,
) -> AuthoritativeRegistrationResult:
    if receipt.outcome_code is None or receipt.completed_at is None:
        raise RuntimeError("registration_receipt_incomplete")
    try:
        outcome = TelegramRegistrationOutcome(receipt.outcome_code)
    except ValueError as exc:
        raise RuntimeError("registration_receipt_outcome_invalid") from exc
    return AuthoritativeRegistrationResult(
        outcome=outcome,
        authoritative_user_id=receipt.authoritative_user_id,
        replayed=True,
        first_terminal_transition=False,
    )


async def _commit_result(
    db: AsyncSession,
    result: AuthoritativeRegistrationResult,
    *,
    checkpoint: RegistrationCheckpoint | None,
) -> AuthoritativeRegistrationResult:
    await _checkpoint(checkpoint, "before_commit")
    await db.commit()
    committed_result = replace(result, first_terminal_transition=True)
    await _checkpoint(checkpoint, "after_commit")
    return committed_result


async def complete_invitation_registration(
    db: AsyncSession,
    request: AuthoritativeRegistrationRequest,
    *,
    checkpoint: RegistrationCheckpoint | None = None,
) -> AuthoritativeRegistrationResult:
    """Complete or reconcile one invitation and own the full DB transaction."""
    _validate_request_shape(request)
    receipt: TelegramRegistrationCommandReceipt | None = None
    invitation: Invitation | None = None

    try:
        if request.source_surface == RegistrationSourceSurface.TELEGRAM_BOT:
            assert request.telegram_command is not None
            receipt, replayed = await prepare_registration_command_receipt(
                db,
                command=request.telegram_command,
                source_server=request.source_server or "",
            )
            if replayed:
                result = _receipt_replay_result(receipt)
                await db.commit()
                await _checkpoint(checkpoint, "after_commit")
                return result

        await _checkpoint(checkpoint, "before_invitation_lock")
        invitation = await _load_invitation_for_update(db, request.invitation_token)
        await _checkpoint(checkpoint, "after_invitation_lock")
        if invitation is None:
            raise _error(
                TelegramRegistrationOutcome.INVITATION_NOT_FOUND,
                "دعوت‌نامه نامعتبر است",
                status_code=404,
            )

        kind = _validate_invitation_kind(invitation)
        if invitation.revoked_at is not None:
            raise _error(TelegramRegistrationOutcome.INVITATION_REVOKED, "دعوت‌نامه لغو شده است")
        if request.source_surface == RegistrationSourceSurface.WEBAPP and invitation.is_used:
            raise _error(
                TelegramRegistrationOutcome.INVITATION_ALREADY_USED,
                "دعوت‌نامه قبلاً استفاده شده است",
            )
        _validate_invitation_time(request, invitation)

        command = request.telegram_command
        telegram_id = command.telegram_id if command is not None else None
        identity = normalize_invitation_identity(
            mobile_number=invitation.mobile_number,
            account_name=invitation.account_name,
        )
        if command is not None and command.mobile_number != identity.mobile_number:
            raise _error(
                TelegramRegistrationOutcome.CONTACT_MOBILE_MISMATCH,
                "شماره تماس با دعوت‌نامه مطابقت ندارد",
            )

        await _acquire_registration_identity_locks(
            db,
            identity=identity,
            telegram_id=telegram_id,
        )
        users = await _load_matching_users_for_update(
            db,
            invitation=invitation,
            identity=identity,
            telegram_id=telegram_id,
        )
        await _checkpoint(checkpoint, "after_natural_key_reads")
        accountant_relation, customer_relation = await _load_relation_for_registration(
            db,
            invitation=invitation,
            kind=kind,
        )

        if invitation.is_used:
            if command is None:
                raise _error(
                    TelegramRegistrationOutcome.INVITATION_ALREADY_USED,
                    "دعوت‌نامه قبلاً استفاده شده است",
                )
            user = _registered_user_for_completed_invitation(invitation, users, identity=identity)
            _raise_for_existing_user_state([user])
            _validate_completed_relation(
                user=user,
                accountant_relation=accountant_relation,
                customer_relation=customer_relation,
            )
            _validate_telegram_projection_eligibility(
                invitation=invitation,
                accountant_relation=accountant_relation,
                customer_relation=customer_relation,
            )
            _, _, telegram_users = _matching_user_maps(
                users,
                identity=identity,
                telegram_id=telegram_id,
            )
            if any(candidate.id != user.id for candidate in telegram_users):
                raise _error(
                    TelegramRegistrationOutcome.TELEGRAM_ID_ALREADY_USED,
                    "این حساب تلگرام قبلاً به کاربر دیگری متصل شده است",
                )
            if user.telegram_id == telegram_id:
                result = AuthoritativeRegistrationResult(
                    outcome=TelegramRegistrationOutcome.ALREADY_LINKED,
                    authoritative_user_id=user.id,
                    user=user,
                    invitation=invitation,
                    accountant_relation=accountant_relation,
                    customer_relation=customer_relation,
                )
            elif user.telegram_id is not None:
                raise _error(
                    TelegramRegistrationOutcome.TELEGRAM_ACCOUNT_CONFLICT,
                    "این حساب به یک حساب تلگرام دیگر متصل است",
                )
            else:
                user.telegram_id = telegram_id
                user.username = command.telegram_username
                set_legacy_has_bot_access_compatibility(user, enabled=True)
                await ensure_mandatory_channel_membership(db, user=user)
                await db.flush()
                result = AuthoritativeRegistrationResult(
                    outcome=TelegramRegistrationOutcome.LINKED_EXISTING,
                    authoritative_user_id=user.id,
                    user=user,
                    invitation=invitation,
                    accountant_relation=accountant_relation,
                    customer_relation=customer_relation,
                )
        else:
            _validate_pending_relation(
                request=request,
                invitation=invitation,
                accountant_relation=accountant_relation,
                customer_relation=customer_relation,
            )
            if command is not None:
                _validate_telegram_projection_eligibility(
                    invitation=invitation,
                    accountant_relation=accountant_relation,
                    customer_relation=customer_relation,
                )
            _validate_pending_natural_keys(
                users,
                identity=identity,
                telegram_id=telegram_id,
            )

            full_name = _registration_full_name(invitation, accountant_relation, customer_relation)
            if command is not None and full_name == invitation.account_name and command.telegram_full_name:
                full_name = command.telegram_full_name
            user = User(
                account_name=invitation.account_name,
                mobile_number=identity.mobile_number,
                role=invitation.role,
                username=command.telegram_username if command is not None else None,
                full_name=full_name,
                address=request.address,
                telegram_id=telegram_id,
                home_server="iran",
                max_sessions=1,
                must_change_password=False,
            )
            set_legacy_has_bot_access_compatibility(
                user,
                enabled=(command is not None) or (accountant_relation is None and customer_relation is None),
            )
            db.add(user)
            await db.flush()
            await _checkpoint(checkpoint, "after_user_flush")

            activated_at = utc_now()
            if accountant_relation is not None:
                activate_accountant_relation_for_registration(
                    accountant_relation,
                    user_id=user.id,
                    activated_at=activated_at,
                )
            if customer_relation is not None:
                activate_customer_relation_for_registration(
                    customer_relation,
                    user_id=user.id,
                    activated_at=activated_at,
                )
            await db.flush()
            await _checkpoint(checkpoint, "after_relation_activation")

            complete_invitation(
                invitation,
                registered_user_id=user.id,
                completed_via=(
                    InvitationCompletionSurface.TELEGRAM
                    if command is not None
                    else InvitationCompletionSurface.WEB
                ),
                completed_at=utc_now(),
            )
            await release_invitation_identity(db, invitation_id=invitation.id)
            await ensure_mandatory_channel_membership(db, user=user)
            await db.flush()
            await _checkpoint(checkpoint, "after_completion_metadata")

            announce = should_announce_project_user_registration(accountant_relation, customer_relation)
            if announce:
                await enqueue_project_user_joined_telegram_outbox(db, new_user=user)
            result = AuthoritativeRegistrationResult(
                outcome=TelegramRegistrationOutcome.CREATED,
                authoritative_user_id=user.id,
                user=user,
                invitation=invitation,
                accountant_relation=accountant_relation,
                customer_relation=customer_relation,
                announce_project_user=announce,
            )

        if receipt is not None:
            finalize_registration_command_receipt(
                receipt,
                outcome=result.outcome,
                authoritative_user_id=result.authoritative_user_id,
            )
        await db.flush()
        await _checkpoint(checkpoint, "after_receipt_outbox_insert")
        return await _commit_result(db, result, checkpoint=checkpoint)
    except RegistrationCommandReplayConflict as exc:
        await db.rollback()
        outcome = (
            TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY
            if str(exc) == TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY.value
            else TelegramRegistrationOutcome.INVALID_COMMAND
        )
        return AuthoritativeRegistrationResult(
            outcome=outcome,
            authoritative_user_id=None,
            replayed=True,
            first_terminal_transition=False,
        )
    except AuthoritativeRegistrationError as exc:
        if receipt is not None:
            finalize_registration_command_receipt(
                receipt,
                outcome=exc.outcome,
                authoritative_user_id=None,
            )
            try:
                await db.flush()
                await _checkpoint(checkpoint, "after_receipt_outbox_insert")
                result = AuthoritativeRegistrationResult(
                    outcome=exc.outcome,
                    authoritative_user_id=None,
                    invitation=invitation,
                )
                return await _commit_result(db, result, checkpoint=checkpoint)
            except Exception:
                await db.rollback()
                raise
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        conflict = _integrity_conflict(exc)
        if conflict is not None:
            raise conflict from None
        raise
    except Exception:
        await db.rollback()
        raise
