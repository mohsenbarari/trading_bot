from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from bot.utils.redis_helpers import mark_deleted_telegram_user
from core import telegram_gateway
from core.config import settings
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.chat_room_service import sync_mandatory_channel_for_user_state_change
from core.registration_identity import (
    canonical_account_name_sql,
    canonical_mobile_number_sql,
    normalize_account_name,
    normalize_mobile_number,
)
from core.registration_contracts import InvitationDerivedState
from core.services.invitation_identity_reservation_service import release_invitation_identity
from core.services.invitation_lifecycle_service import derive_invitation_state, soft_revoke_invitation
from core.services.invitation_transition_lock_service import lock_invitation_for_transition
from core.services.offer_expiry_service import OfferExpiryReason, OfferExpirySourceSurface
from core.services.session_service import deactivate_active_sessions, publish_session_revocation
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.utils import send_telegram_notification, utc_now_naive
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.invitation import Invitation
from models.offer import Offer, OfferStatus
from models.trade import Trade
from models.user import User


logger = logging.getLogger(__name__)

REMOVAL_TELEGRAM_MESSAGE = (
    "ℹ️ *اطلاعیه*\n\n"
    "حساب کاربری شما از پروژه حذف شده است.\n"
    "دسترسی وب و ربات شما بلافاصله غیرفعال شد.\n\n"
    "اگر در آینده دوباره به پروژه برگردید، مدیر باید مجدداً برای شما دسترسی ایجاد کند."
)


@dataclass(slots=True)
class DeletedUserResult:
    user_id: int
    telegram_id: Optional[int]
    revoked_session_count: int


@dataclass(slots=True)
class _DeletedUserEffect:
    user_id: int
    telegram_id: Optional[int]
    revoked_sessions: list


async def remove_user_from_telegram_channel(telegram_id: int) -> None:
    """Remove a linked user from the Telegram channel without leaving them banned."""
    if current_server() != SERVER_FOREIGN:
        logger.info(
            "Skipping Telegram channel removal outside foreign server",
            extra={
                "event": "user.telegram_channel_remove_skipped_non_foreign",
                "server_mode": current_server(),
            },
        )
        return
    if not settings.channel_id or not settings.bot_token:
        return

    await telegram_gateway.ban_chat_member(
        settings.channel_id,
        telegram_id,
        revoke_messages=False,
        bot_token=settings.bot_token,
        idempotency_key=f"user-channel-remove-ban:{telegram_id}",
    )
    await telegram_gateway.unban_chat_member(
        settings.channel_id,
        telegram_id,
        only_if_banned=True,
        bot_token=settings.bot_token,
        idempotency_key=f"user-channel-remove-unban:{telegram_id}",
    )


def _utcnow_naive():
    from core.utils import utc_now

    return utc_now().replace(tzinfo=None)


def _scalars_all(result) -> list:
    try:
        return list(result.scalars().all())
    except (AttributeError, TypeError):
        return []


async def _invalidate_accountant_invitation(db: AsyncSession, invitation_token: str, now) -> None:
    await _soft_revoke_pending_invitation(db, invitation_token=invitation_token, now=now)


async def _invalidate_customer_invitation(db: AsyncSession, invitation_token: str, now) -> None:
    await _soft_revoke_pending_invitation(db, invitation_token=invitation_token, now=now)


async def _soft_revoke_pending_invitation(
    db: AsyncSession,
    *,
    invitation_token: str,
    now,
) -> None:
    invitation = await lock_invitation_for_transition(
        db,
        invitation_token=invitation_token,
    )
    if invitation is None:
        return
    state = derive_invitation_state(invitation, now=now)
    if state == InvitationDerivedState.PENDING:
        soft_revoke_invitation(invitation, revoked_at=now)
        await release_invitation_identity(db, invitation_id=invitation.id)


async def _soft_revoke_pending_invitations_for_user_identity(
    db: AsyncSession,
    *,
    mobile_number: str,
    account_name: str,
    now,
) -> None:
    normalized_mobile = normalize_mobile_number(mobile_number)
    normalized_account = normalize_account_name(account_name)
    candidate_stmt = (
        select(Invitation.id)
        .where(
            or_(
                literal_column(canonical_mobile_number_sql("invitations.mobile_number"))
                == normalized_mobile,
                literal_column(canonical_account_name_sql("invitations.account_name"))
                == normalized_account,
            )
        )
        .order_by(Invitation.id)
    )
    invitation_ids = list((await db.execute(candidate_stmt)).scalars().all())
    for invitation_id in invitation_ids:
        invitation = await lock_invitation_for_transition(db, invitation_id=invitation_id)
        if invitation is None:
            continue
        if derive_invitation_state(invitation, now=now) == InvitationDerivedState.PENDING:
            soft_revoke_invitation(invitation, revoked_at=now)
            await release_invitation_identity(db, invitation_id=invitation.id)


async def _lock_accountant_relation_transition(
    db: AsyncSession,
    *,
    relation_id: int,
    invitation_token: str,
) -> tuple[Invitation | None, AccountantRelation | None]:
    invitation = await lock_invitation_for_transition(
        db,
        invitation_token=invitation_token,
    )
    relation = (
        await db.execute(
            select(AccountantRelation)
            .options(joinedload(AccountantRelation.accountant_user))
            .where(AccountantRelation.id == relation_id)
            .with_for_update(of=AccountantRelation)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if relation is not None and relation.invitation_token != invitation_token:
        raise RuntimeError("accountant_relation_invitation_changed_during_transition_lock")
    return invitation, relation


async def _lock_customer_relation_transition(
    db: AsyncSession,
    *,
    relation_id: int,
    invitation_token: str,
) -> tuple[Invitation | None, CustomerRelation | None]:
    invitation = await lock_invitation_for_transition(
        db,
        invitation_token=invitation_token,
    )
    relation = (
        await db.execute(
            select(CustomerRelation)
            .options(joinedload(CustomerRelation.customer_user))
            .where(CustomerRelation.id == relation_id)
            .with_for_update(of=CustomerRelation)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if relation is not None and relation.invitation_token != invitation_token:
        raise RuntimeError("customer_relation_invitation_changed_during_transition_lock")
    return invitation, relation


async def _revoke_pending_relation_invitation(
    db: AsyncSession,
    *,
    invitation: Invitation | None,
    now,
) -> None:
    if invitation is None:
        raise RuntimeError("pending_relation_invitation_missing")
    if derive_invitation_state(invitation, now=now) != InvitationDerivedState.PENDING:
        raise RuntimeError("pending_relation_invitation_transition_conflict")
    soft_revoke_invitation(invitation, revoked_at=now)
    await release_invitation_identity(db, invitation_id=invitation.id)


async def _close_owned_accountant_relations(
    db: AsyncSession,
    user: User,
    *,
    processed_user_ids: set[int],
    effects: list[_DeletedUserEffect],
) -> None:
    now = _utcnow_naive()
    stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.accountant_user))
        .where(
            AccountantRelation.owner_user_id == user.id,
            AccountantRelation.deleted_at.is_(None),
        )
        .order_by(AccountantRelation.id.asc())
    )
    candidates = _scalars_all(await db.execute(stmt))
    for candidate in candidates:
        invitation, relation = await _lock_accountant_relation_transition(
            db,
            relation_id=candidate.id,
            invitation_token=candidate.invitation_token,
        )
        if relation is None or relation.deleted_at is not None:
            continue
        if (
            relation.status == AccountantRelationStatus.ACTIVE
            and relation.accountant_user is not None
            and not relation.accountant_user.is_deleted
        ):
            await _delete_user_account_in_transaction(
                db,
                relation.accountant_user,
                processed_user_ids=processed_user_ids,
                effects=effects,
            )

        if relation.status == AccountantRelationStatus.PENDING:
            await _revoke_pending_relation_invitation(
                db,
                invitation=invitation,
                now=now,
            )
            relation.status = AccountantRelationStatus.REVOKED
        elif relation.status == AccountantRelationStatus.ACTIVE:
            relation.status = AccountantRelationStatus.DELETED

        if relation.status in (
            AccountantRelationStatus.REVOKED,
            AccountantRelationStatus.DELETED,
            AccountantRelationStatus.EXPIRED,
        ):
            relation.deleted_at = relation.deleted_at or now


async def _close_linked_accountant_relations(db: AsyncSession, user: User) -> None:
    now = _utcnow_naive()
    stmt = (
        select(AccountantRelation)
        .where(
            AccountantRelation.accountant_user_id == user.id,
            AccountantRelation.deleted_at.is_(None),
        )
        .order_by(AccountantRelation.id.asc())
    )
    candidates = _scalars_all(await db.execute(stmt))
    for candidate in candidates:
        invitation, relation = await _lock_accountant_relation_transition(
            db,
            relation_id=candidate.id,
            invitation_token=candidate.invitation_token,
        )
        if relation is None or relation.deleted_at is not None:
            continue
        if relation.status == AccountantRelationStatus.PENDING:
            await _revoke_pending_relation_invitation(
                db,
                invitation=invitation,
                now=now,
            )
            relation.status = AccountantRelationStatus.REVOKED
        elif relation.status == AccountantRelationStatus.ACTIVE:
            relation.status = AccountantRelationStatus.DELETED
        relation.deleted_at = relation.deleted_at or now


async def _close_owned_customer_relations(
    db: AsyncSession,
    user: User,
    *,
    processed_user_ids: set[int],
    effects: list[_DeletedUserEffect],
) -> None:
    now = _utcnow_naive()
    stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.customer_user))
        .where(
            CustomerRelation.owner_user_id == user.id,
            CustomerRelation.deleted_at.is_(None),
        )
        .order_by(CustomerRelation.id.asc())
    )
    candidates = _scalars_all(await db.execute(stmt))
    for candidate in candidates:
        invitation, relation = await _lock_customer_relation_transition(
            db,
            relation_id=candidate.id,
            invitation_token=candidate.invitation_token,
        )
        if relation is None or relation.deleted_at is not None:
            continue
        if (
            relation.status == CustomerRelationStatus.ACTIVE
            and relation.customer_user is not None
            and not relation.customer_user.is_deleted
        ):
            await _delete_user_account_in_transaction(
                db,
                relation.customer_user,
                processed_user_ids=processed_user_ids,
                effects=effects,
            )

        if relation.status == CustomerRelationStatus.PENDING:
            await _revoke_pending_relation_invitation(
                db,
                invitation=invitation,
                now=now,
            )
            relation.status = CustomerRelationStatus.REVOKED
        elif relation.status == CustomerRelationStatus.ACTIVE:
            relation.status = CustomerRelationStatus.DELETED

        if relation.status in (
            CustomerRelationStatus.REVOKED,
            CustomerRelationStatus.DELETED,
            CustomerRelationStatus.EXPIRED,
        ):
            relation.deleted_at = relation.deleted_at or now


async def _close_linked_customer_relations(db: AsyncSession, user: User) -> None:
    now = _utcnow_naive()
    stmt = (
        select(CustomerRelation)
        .where(
            CustomerRelation.customer_user_id == user.id,
            CustomerRelation.deleted_at.is_(None),
        )
        .order_by(CustomerRelation.id.asc())
    )
    candidates = _scalars_all(await db.execute(stmt))
    for candidate in candidates:
        invitation, relation = await _lock_customer_relation_transition(
            db,
            relation_id=candidate.id,
            invitation_token=candidate.invitation_token,
        )
        if relation is None or relation.deleted_at is not None:
            continue
        if relation.status == CustomerRelationStatus.PENDING:
            await _revoke_pending_relation_invitation(
                db,
                invitation=invitation,
                now=now,
            )
            relation.status = CustomerRelationStatus.REVOKED
        elif relation.status == CustomerRelationStatus.ACTIVE:
            relation.status = CustomerRelationStatus.DELETED
        relation.deleted_at = relation.deleted_at or now


async def _delete_user_account_in_transaction(
    db: AsyncSession,
    user: User,
    *,
    processed_user_ids: set[int],
    effects: list[_DeletedUserEffect],
) -> None:
    if user.id in processed_user_ids or user.is_deleted:
        return
    processed_user_ids.add(user.id)

    telegram_id = user.telegram_id
    mobile_number = user.mobile_number
    account_name = user.account_name

    await _close_owned_accountant_relations(db, user, processed_user_ids=processed_user_ids, effects=effects)
    await _close_linked_accountant_relations(db, user)
    await _close_owned_customer_relations(db, user, processed_user_ids=processed_user_ids, effects=effects)
    await _close_linked_customer_relations(db, user)

    offered_trades = _scalars_all(
        await db.execute(select(Trade).where(Trade.offer_user_id == user.id))
    )
    for trade in offered_trades:
        trade.offer_user_mobile = mobile_number

    responded_trades = _scalars_all(
        await db.execute(select(Trade).where(Trade.responder_user_id == user.id))
    )
    for trade in responded_trades:
        trade.responder_user_mobile = mobile_number

    active_offers = _scalars_all(
        await db.execute(
            select(Offer).where(Offer.user_id == user.id, Offer.status == OfferStatus.ACTIVE)
        )
    )
    expired_at = utc_now_naive()
    for offer in active_offers:
        offer.status = OfferStatus.EXPIRED
        offer.expire_reason = OfferExpiryReason.USER_DELETED
        offer.expired_at = expired_at
        offer.expired_by_user_id = None
        offer.expired_by_actor_user_id = None
        offer.expire_source_surface = OfferExpirySourceSurface.SYSTEM.value
        offer.expire_source_server = current_server()

    await _soft_revoke_pending_invitations_for_user_identity(
        db,
        mobile_number=mobile_number,
        account_name=account_name,
        now=_utcnow_naive(),
    )

    revoked_sessions = await deactivate_active_sessions(db, user.id)
    user.soft_delete()
    await sync_mandatory_channel_for_user_state_change(
        db,
        user=user,
        previous_is_deleted=False,
        previous_deleted_at=None,
    )
    effects.append(
        _DeletedUserEffect(
            user_id=user.id,
            telegram_id=telegram_id,
            revoked_sessions=revoked_sessions,
        )
    )


async def delete_user_account(db: AsyncSession, user: User) -> DeletedUserResult:
    """Soft-delete a user and revoke all current access in one shared flow."""
    if user.is_deleted:
        raise ValueError("User already deleted")

    effects: list[_DeletedUserEffect] = []
    queue_mode = (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    )

    try:
        await _delete_user_account_in_transaction(
            db,
            user,
            processed_user_ids=set(),
            effects=effects,
        )
        if queue_mode:
            from core.services.telegram_notification_outbox_service import (
                TelegramNotificationRecipient,
                enqueue_account_deletion_telegram_notification_once,
            )

            await db.flush()
            for effect in effects:
                if not effect.telegram_id:
                    continue
                deleted_user = await db.get(User, effect.user_id)
                if deleted_user is None:
                    raise RuntimeError("Deleted user effect lost its source user")
                user_sync_version = int(
                    getattr(deleted_user, "sync_version", 0) or 0
                )
                await enqueue_account_deletion_telegram_notification_once(
                    db,
                    recipient=TelegramNotificationRecipient(
                        user_id=int(effect.user_id),
                        telegram_id=int(effect.telegram_id),
                    ),
                    source_id=(
                        f"account-deleted:{effect.user_id}:{user_sync_version}"
                    ),
                    text=REMOVAL_TELEGRAM_MESSAGE,
                    user=deleted_user,
                    user_sync_version=user_sync_version,
                )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    for effect in effects:
        if effect.telegram_id:
            try:
                await mark_deleted_telegram_user(effect.telegram_id)
            except Exception as exc:
                logger.warning(f"Failed to mark deleted telegram user {effect.telegram_id}: {exc}")

            if current_server() == SERVER_FOREIGN and not queue_mode:
                try:
                    await send_telegram_notification(effect.telegram_id, REMOVAL_TELEGRAM_MESSAGE)
                except Exception as exc:
                    logger.warning(f"Failed to send deletion notice to telegram user {effect.telegram_id}: {exc}")

        await publish_session_revocation(effect.user_id, effect.revoked_sessions)

        if (
            effect.telegram_id
            and current_server() == SERVER_FOREIGN
            and not queue_mode
        ):
            try:
                await remove_user_from_telegram_channel(effect.telegram_id)
            except Exception as exc:
                logger.warning(f"Failed to remove telegram user {effect.telegram_id} from channel: {exc}")

    primary_effect = next((effect for effect in effects if effect.user_id == user.id), None)
    if primary_effect is None:
        raise RuntimeError("Primary deleted user effect was not recorded")

    return DeletedUserResult(
        user_id=user.id,
        telegram_id=primary_effect.telegram_id,
        revoked_session_count=len(primary_effect.revoked_sessions),
    )
