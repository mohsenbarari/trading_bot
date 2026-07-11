"""Shared accountant-relation helpers for phased delegated-owner rollout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import secrets
import string

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from core.utils import normalize_account_name, normalize_persian_numerals, unique_user_ids, utc_now
from core.services.invitation_identity_reservation_service import (
    release_invitation_identities_for_tokens,
    release_invitation_identity,
)
from core.services.invitation_lifecycle_service import soft_revoke_invitation
from core.services.invitation_transition_lock_service import lock_invitation_for_transition
from models.invitation import Invitation, InvitationKind
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.user import User, UserRole


CAPACITY_TRACKED_RELATION_STATUSES = (
    AccountantRelationStatus.PENDING,
    AccountantRelationStatus.ACTIVE,
)
ACCOUNTANT_INVITATION_PREFIX = "ACCT-"
# Deprecated compatibility constant. New invitations use core.trading_settings.
ACCOUNTANT_PENDING_LIFETIME = None


@dataclass(frozen=True)
class EffectiveOwnerActor:
    owner_user: User
    actor_user: User
    relation: AccountantRelation | None
    is_accountant_context: bool


def _utcnow_naive():
    return utc_now().replace(tzinfo=None)


def _normalize_mobile_number(value: str) -> str:
    return normalize_persian_numerals((value or "").strip())


def is_accountant_invitation_token(token: str | None) -> bool:
    return bool((token or "").startswith(ACCOUNTANT_INVITATION_PREFIX))


def generate_accountant_invitation_token() -> str:
    return ACCOUNTANT_INVITATION_PREFIX + secrets.token_hex(16)


def generate_accountant_short_code() -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(8))


def get_effective_max_accountants(owner_user: User) -> int:
    raw_limit = getattr(owner_user, "max_accountants", 3)
    try:
        normalized_limit = int(raw_limit)
    except (TypeError, ValueError):
        normalized_limit = 3
    return max(0, normalized_limit)


async def sweep_expired_pending_accountant_relations(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    invitation_token: str | None = None,
) -> list[AccountantRelation]:
    now = _utcnow_naive()
    stmt = (
        select(AccountantRelation)
        .where(
            AccountantRelation.status == AccountantRelationStatus.PENDING,
            AccountantRelation.deleted_at.is_(None),
            AccountantRelation.expires_at <= now,
        )
        .order_by(
            AccountantRelation.invitation_token.asc(),
            AccountantRelation.id.asc(),
        )
    )
    if owner_user_id is not None:
        stmt = stmt.where(AccountantRelation.owner_user_id == owner_user_id)
    if invitation_token is not None:
        stmt = stmt.where(AccountantRelation.invitation_token == invitation_token)

    candidates = list((await db.execute(stmt)).scalars().all())
    expired_relations: list[AccountantRelation] = []
    missing_invitation_tokens: list[str] = []
    for candidate in candidates:
        relation_id = candidate.id
        relation_token = candidate.invitation_token
        invitation = await lock_invitation_for_transition(
            db,
            invitation_token=relation_token,
        )
        relation_stmt = (
            select(AccountantRelation)
            .where(
                AccountantRelation.id == relation_id,
                AccountantRelation.status == AccountantRelationStatus.PENDING,
                AccountantRelation.deleted_at.is_(None),
                AccountantRelation.expires_at <= now,
            )
            .with_for_update()
        )
        relation = (await db.execute(relation_stmt)).scalar_one_or_none()
        if relation is None:
            continue
        relation.status = AccountantRelationStatus.EXPIRED
        relation.deleted_at = now
        expired_relations.append(relation)
        if invitation is not None and invitation.id is not None:
            await release_invitation_identity(db, invitation_id=invitation.id)
        else:
            missing_invitation_tokens.append(relation_token)

    if missing_invitation_tokens:
        await release_invitation_identities_for_tokens(
            db,
            invitation_tokens=missing_invitation_tokens,
        )

    return expired_relations


async def get_accountant_relation_by_invitation_token(
    db: AsyncSession,
    invitation_token: str,
) -> AccountantRelation | None:
    stmt = (
        select(AccountantRelation)
        .options(
            joinedload(AccountantRelation.owner_user),
            joinedload(AccountantRelation.accountant_user),
        )
        .where(AccountantRelation.invitation_token == invitation_token)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def lock_accountant_relation_for_registration(
    db: AsyncSession,
    invitation_token: str,
) -> AccountantRelation | None:
    """Lock one relation for an outer registration transaction.

    Unlike the legacy pending lookup, this helper never sweeps or commits. The
    authoritative registration service owns validation, mutation, and the
    transaction boundary.
    """
    stmt = (
        select(AccountantRelation)
        .where(AccountantRelation.invitation_token == invitation_token)
        .with_for_update()
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def activate_accountant_relation_for_registration(
    relation: AccountantRelation,
    *,
    user_id: int,
    activated_at: datetime,
) -> None:
    if relation.status != AccountantRelationStatus.PENDING or relation.deleted_at is not None:
        raise ValueError("accountant_relation_not_pending")
    if relation.accountant_user_id is not None:
        raise ValueError("accountant_relation_already_bound")
    relation.accountant_user_id = int(user_id)
    relation.status = AccountantRelationStatus.ACTIVE
    relation.activated_at = activated_at
    relation.deleted_at = None


async def get_pending_accountant_relation_by_invitation_token(
    db: AsyncSession,
    invitation_token: str,
) -> AccountantRelation | None:
    expired_relations = await sweep_expired_pending_accountant_relations(db, invitation_token=invitation_token)
    if expired_relations:
        await db.commit()

    stmt = (
        select(AccountantRelation)
        .options(
            joinedload(AccountantRelation.owner_user),
            joinedload(AccountantRelation.accountant_user),
        )
        .where(
            AccountantRelation.invitation_token == invitation_token,
            AccountantRelation.status == AccountantRelationStatus.PENDING,
            AccountantRelation.deleted_at.is_(None),
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_active_accountant_relation_for_accountant(
    db: AsyncSession,
    accountant_user_id: int,
) -> AccountantRelation | None:
    stmt = (
        select(AccountantRelation)
        .options(
            joinedload(AccountantRelation.owner_user),
            joinedload(AccountantRelation.accountant_user),
        )
        .where(
            AccountantRelation.accountant_user_id == accountant_user_id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .order_by(AccountantRelation.id.asc())
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def is_user_accountant(db: AsyncSession, user_id: int) -> bool:
    return await get_active_accountant_relation_for_accountant(db, user_id) is not None


async def list_active_accountants_for_owner(
    db: AsyncSession,
    owner_user_id: int,
) -> list[AccountantRelation]:
    stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.accountant_user))
        .where(
            AccountantRelation.owner_user_id == owner_user_id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .order_by(AccountantRelation.created_at.asc(), AccountantRelation.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_owner_accountant_relations(
    db: AsyncSession,
    owner_user_id: int,
) -> list[AccountantRelation]:
    expired_relations = await sweep_expired_pending_accountant_relations(db, owner_user_id=owner_user_id)
    if expired_relations:
        await db.commit()

    stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.accountant_user))
        .where(
            AccountantRelation.owner_user_id == owner_user_id,
            AccountantRelation.deleted_at.is_(None),
            AccountantRelation.status.in_(CAPACITY_TRACKED_RELATION_STATUSES),
        )
        .order_by(AccountantRelation.created_at.desc(), AccountantRelation.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def build_trade_notification_audience_user_ids(
    db: AsyncSession,
    owner_user_ids: list[object],
) -> list[int]:
    """Return owner ids plus all active accountant ids for those owners."""
    normalized_owner_ids = unique_user_ids(owner_user_ids)
    if not normalized_owner_ids:
        return []

    stmt = select(AccountantRelation.accountant_user_id).where(
        AccountantRelation.owner_user_id.in_(normalized_owner_ids),
        AccountantRelation.status == AccountantRelationStatus.ACTIVE,
        AccountantRelation.deleted_at.is_(None),
        AccountantRelation.accountant_user_id.is_not(None),
    )
    accountant_ids = list((await db.execute(stmt)).scalars().all())
    return unique_user_ids([*normalized_owner_ids, *accountant_ids])


async def count_capacity_tracked_accountants_for_owner(
    db: AsyncSession,
    owner_user_id: int,
) -> int:
    stmt = select(func.count(AccountantRelation.id)).where(
        AccountantRelation.owner_user_id == owner_user_id,
        AccountantRelation.status.in_(CAPACITY_TRACKED_RELATION_STATUSES),
        AccountantRelation.deleted_at.is_(None),
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def validate_accountant_capacity(
    db: AsyncSession,
    owner_user: User,
    *,
    additional_slots: int = 1,
) -> tuple[int, int]:
    await sweep_expired_pending_accountant_relations(db, owner_user_id=owner_user.id)
    current_count = await count_capacity_tracked_accountants_for_owner(db, owner_user.id)
    max_accountants = get_effective_max_accountants(owner_user)
    requested_total = current_count + max(0, additional_slots)
    if requested_total > max_accountants:
        raise HTTPException(
            status_code=400,
            detail="Owner has reached the maximum number of accountants",
        )
    return current_count, max_accountants


async def resolve_effective_owner_actor(
    db: AsyncSession,
    current_user: User,
) -> EffectiveOwnerActor:
    relation = await get_active_accountant_relation_for_accountant(db, current_user.id)
    if not relation or relation.owner_user is None:
        return EffectiveOwnerActor(
            owner_user=current_user,
            actor_user=current_user,
            relation=None,
            is_accountant_context=False,
        )

    return EffectiveOwnerActor(
        owner_user=relation.owner_user,
        actor_user=current_user,
        relation=relation,
        is_accountant_context=relation.owner_user.id != current_user.id,
    )


async def create_owner_accountant_relation(
    db: AsyncSession,
    *,
    owner_user: User,
    global_account_name: str,
    relation_display_name: str,
    mobile_number: str,
    duty_description: str | None = None,
) -> tuple[AccountantRelation, Invitation]:
    normalized_account_name = normalize_account_name((global_account_name or "").strip())
    normalized_mobile = _normalize_mobile_number(mobile_number)
    normalized_display_name = (relation_display_name or "").strip()
    normalized_duty_description = (duty_description or "").strip() or None

    if not normalized_account_name:
        raise HTTPException(status_code=400, detail="نام کاربری نامعتبر است")
    if not normalized_display_name:
        raise HTTPException(status_code=400, detail="نام نمایشی رابطه الزامی است")
    if not normalized_mobile.startswith("09") or len(normalized_mobile) != 11:
        raise HTTPException(status_code=400, detail="شماره موبایل نامعتبر است")

    await validate_accountant_capacity(db, owner_user)

    existing_user_stmt = select(User).where(
        or_(
            User.account_name == normalized_account_name,
            User.mobile_number == normalized_mobile,
        )
    )
    existing_user = (await db.execute(existing_user_stmt)).scalar_one_or_none()
    if existing_user:
        raise HTTPException(status_code=400, detail="کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است")

    duplicate_relation_stmt = select(AccountantRelation).where(
        AccountantRelation.deleted_at.is_(None),
        AccountantRelation.status.in_(CAPACITY_TRACKED_RELATION_STATUSES),
        or_(
            AccountantRelation.mobile_number == normalized_mobile,
            AccountantRelation.global_account_name == normalized_account_name,
        ),
    )
    duplicate_relation = (await db.execute(duplicate_relation_stmt)).scalar_one_or_none()
    if duplicate_relation:
        raise HTTPException(status_code=400, detail="یک حسابدار pending یا active با این نام کاربری یا موبایل وجود دارد")

    duplicate_display_stmt = select(AccountantRelation).where(
        AccountantRelation.owner_user_id == owner_user.id,
        AccountantRelation.deleted_at.is_(None),
        AccountantRelation.relation_display_name == normalized_display_name,
    )
    duplicate_display = (await db.execute(duplicate_display_stmt)).scalar_one_or_none()
    if duplicate_display:
        raise HTTPException(status_code=400, detail="این نام نمایشی قبلاً برای یکی از حسابداران این کاربر استفاده شده است")

    invitation_token = generate_accountant_invitation_token()
    short_code = generate_accountant_short_code()
    from core.services.invitation_lifecycle_service import get_new_invitation_expiry

    expires_at = await get_new_invitation_expiry()

    invitation = Invitation(
        account_name=normalized_account_name,
        mobile_number=normalized_mobile,
        role=UserRole.WATCH,
        kind=InvitationKind.ACCOUNTANT,
        token=invitation_token,
        short_code=short_code,
        created_by_id=owner_user.id,
        expires_at=expires_at,
    )
    relation = AccountantRelation(
        owner_user_id=owner_user.id,
        accountant_user_id=None,
        created_by_user_id=owner_user.id,
        invitation_token=invitation_token,
        global_account_name=normalized_account_name,
        relation_display_name=normalized_display_name,
        duty_description=normalized_duty_description,
        mobile_number=normalized_mobile,
        status=AccountantRelationStatus.PENDING,
        expires_at=expires_at,
    )

    db.add(invitation)
    db.add(relation)
    await db.commit()
    await db.refresh(invitation)
    await db.refresh(relation)
    return relation, invitation


async def cancel_pending_accountant_relation(
    db: AsyncSession,
    *,
    owner_user_id: int,
    relation_id: int,
) -> AccountantRelation:
    candidate_stmt = select(AccountantRelation.invitation_token).where(
        AccountantRelation.id == relation_id,
        AccountantRelation.owner_user_id == owner_user_id,
    )
    invitation_token = (await db.execute(candidate_stmt)).scalar_one_or_none()
    if invitation_token is None:
        raise HTTPException(status_code=404, detail="رابطه حسابدار یافت نشد")
    invitation = await lock_invitation_for_transition(
        db,
        invitation_token=invitation_token,
    )
    relation_stmt = (
        select(AccountantRelation)
        .where(
            AccountantRelation.id == relation_id,
            AccountantRelation.owner_user_id == owner_user_id,
        )
        .with_for_update()
    )
    relation = (await db.execute(relation_stmt)).scalar_one_or_none()
    if relation is None:
        raise HTTPException(status_code=404, detail="رابطه حسابدار یافت نشد")
    if relation.deleted_at is not None or relation.status != AccountantRelationStatus.PENDING:
        raise HTTPException(status_code=400, detail="فقط حسابدار pending قابل لغو است")
    if invitation is not None and (
        invitation.is_used or getattr(invitation, "revoked_at", None) is not None
    ):
        raise HTTPException(status_code=400, detail="فقط حسابدار pending قابل لغو است")

    now = _utcnow_naive()
    relation.status = AccountantRelationStatus.REVOKED
    relation.deleted_at = now

    if invitation:
        soft_revoke_invitation(invitation, revoked_at=now)
        if getattr(invitation, "id", None) is not None:
            await release_invitation_identity(db, invitation_id=invitation.id)

    await db.commit()
    await db.refresh(relation)
    return relation


async def unlink_owner_accountant_relation(
    db: AsyncSession,
    *,
    owner_user_id: int,
    relation_id: int,
) -> AccountantRelation:
    stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.accountant_user))
        .where(
            AccountantRelation.id == relation_id,
            AccountantRelation.owner_user_id == owner_user_id,
        )
    )
    relation = (await db.execute(stmt)).scalar_one_or_none()
    if not relation:
        raise HTTPException(status_code=404, detail="رابطه حسابدار یافت نشد")

    if relation.deleted_at is not None or relation.status in (
        AccountantRelationStatus.EXPIRED,
        AccountantRelationStatus.REVOKED,
        AccountantRelationStatus.DELETED,
    ):
        raise HTTPException(status_code=400, detail="این رابطه قبلاً بسته شده است")

    if relation.status == AccountantRelationStatus.PENDING:
        return await cancel_pending_accountant_relation(
            db,
            owner_user_id=owner_user_id,
            relation_id=relation_id,
        )

    if relation.status != AccountantRelationStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="فقط حسابدار pending یا active قابل قطع ارتباط است")

    # Import lazily to avoid service-layer circular imports at module load time.
    from core.services.user_deletion_service import delete_user_account

    accountant_user = relation.accountant_user
    if accountant_user and not accountant_user.is_deleted:
        await delete_user_account(db, accountant_user)

    now = _utcnow_naive()
    relation.status = AccountantRelationStatus.DELETED
    relation.deleted_at = now

    await db.commit()
    await db.refresh(relation)
    return relation


async def update_owner_accountant_relation(
    db: AsyncSession,
    *,
    owner_user_id: int,
    relation_id: int,
    duty_description: str | None = None,
) -> AccountantRelation:
    stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.accountant_user))
        .where(
            AccountantRelation.id == relation_id,
            AccountantRelation.owner_user_id == owner_user_id,
        )
    )
    relation = (await db.execute(stmt)).scalar_one_or_none()
    if not relation:
        raise HTTPException(status_code=404, detail="رابطه حسابدار یافت نشد")
    if relation.deleted_at is not None or relation.status not in CAPACITY_TRACKED_RELATION_STATUSES:
        raise HTTPException(status_code=400, detail="فقط حسابدار pending یا active قابل ویرایش است")

    normalized_duty_description = (duty_description or "").strip() or None

    relation.duty_description = normalized_duty_description

    await db.commit()
    refreshed_stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.accountant_user))
        .where(AccountantRelation.id == relation.id)
    )
    refreshed_relation = (await db.execute(refreshed_stmt)).scalar_one()
    return refreshed_relation
