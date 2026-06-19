from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from bot.utils.redis_helpers import mark_deleted_telegram_user
from core import telegram_gateway
from core.config import settings
from core.services.chat_room_service import sync_mandatory_channel_for_user_state_change
from core.services.session_service import deactivate_active_sessions, publish_session_revocation
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


async def _invalidate_accountant_invitation(db: AsyncSession, invitation_token: str, now) -> None:
    invitation_stmt = select(Invitation).where(Invitation.token == invitation_token)
    invitation = (await db.execute(invitation_stmt)).scalar_one_or_none()
    if invitation:
        invitation.is_used = True
        invitation.expires_at = now


async def _invalidate_customer_invitation(db: AsyncSession, invitation_token: str, now) -> None:
    invitation_stmt = select(Invitation).where(Invitation.token == invitation_token)
    invitation = (await db.execute(invitation_stmt)).scalar_one_or_none()
    if invitation:
        invitation.is_used = True
        invitation.expires_at = now


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
    )
    relations = list((await db.execute(stmt)).scalars().all())
    for relation in relations:
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
            relation.status = AccountantRelationStatus.REVOKED
            await _invalidate_accountant_invitation(db, relation.invitation_token, now)
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
    stmt = select(AccountantRelation).where(
        AccountantRelation.accountant_user_id == user.id,
        AccountantRelation.deleted_at.is_(None),
    )
    relations = list((await db.execute(stmt)).scalars().all())
    for relation in relations:
        if relation.status == AccountantRelationStatus.PENDING:
            relation.status = AccountantRelationStatus.REVOKED
            await _invalidate_accountant_invitation(db, relation.invitation_token, now)
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
    )
    relations = list((await db.execute(stmt)).scalars().all())
    for relation in relations:
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
            relation.status = CustomerRelationStatus.REVOKED
            await _invalidate_customer_invitation(db, relation.invitation_token, now)
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
    stmt = select(CustomerRelation).where(
        CustomerRelation.customer_user_id == user.id,
        CustomerRelation.deleted_at.is_(None),
    )
    relations = list((await db.execute(stmt)).scalars().all())
    for relation in relations:
        if relation.status == CustomerRelationStatus.PENDING:
            relation.status = CustomerRelationStatus.REVOKED
            await _invalidate_customer_invitation(db, relation.invitation_token, now)
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

    await db.execute(
        update(Trade)
        .where(Trade.offer_user_id == user.id)
        .values(offer_user_mobile=mobile_number)
    )
    await db.execute(
        update(Trade)
        .where(Trade.responder_user_id == user.id)
        .values(responder_user_mobile=mobile_number)
    )
    await db.execute(
        update(Offer)
        .where(Offer.user_id == user.id, Offer.status == OfferStatus.ACTIVE)
        .values(status=OfferStatus.EXPIRED, expire_reason="user_deleted", expired_at=utc_now_naive())
    )
    invitation_result = await db.execute(
        select(Invitation).where(
            or_(Invitation.mobile_number == mobile_number, Invitation.account_name == account_name)
        )
    )
    for invitation in invitation_result.scalars().all():
        await db.delete(invitation)

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

    try:
        await _delete_user_account_in_transaction(
            db,
            user,
            processed_user_ids=set(),
            effects=effects,
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

            try:
                await send_telegram_notification(effect.telegram_id, REMOVAL_TELEGRAM_MESSAGE)
            except Exception as exc:
                logger.warning(f"Failed to send deletion notice to telegram user {effect.telegram_id}: {exc}")

        await publish_session_revocation(effect.user_id, effect.revoked_sessions)

        if effect.telegram_id:
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
