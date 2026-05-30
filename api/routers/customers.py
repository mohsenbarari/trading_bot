import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

import schemas
from api.deps import get_effective_owner_actor_context
from core.config import settings
from core.db import get_db
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.services.customer_relation_service import (
    create_owner_customer_relation,
    is_user_customer,
    list_owner_customer_relations,
    load_customer_relation_invitation_map,
    unlink_owner_customer_relation,
    update_owner_customer_relation,
)
from core.services.session_service import get_active_sessions, logout_session
from core.sms import send_customer_invitation_sms
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.session import UserSession


router = APIRouter()


def build_customer_registration_link(invitation_token: str) -> str | None:
    frontend_url = (getattr(settings, "frontend_url", "") or "").strip()
    if not frontend_url:
        return None
    return f"{frontend_url}/register?token={invitation_token}"


def serialize_customer_relation(relation, invitation=None) -> dict:
    return {
        "id": relation.id,
        "owner_user_id": relation.owner_user_id,
        "customer_user_id": relation.customer_user_id,
        "customer_account_name": getattr(getattr(relation, "customer_user", None), "account_name", None),
        "invitation_account_name": getattr(invitation, "account_name", None),
        "mobile_number": getattr(invitation, "mobile_number", None),
        "management_name": relation.management_name,
        "customer_tier": relation.customer_tier,
        "commission_rate": relation.commission_rate,
        "min_trade_quantity": relation.min_trade_quantity,
        "max_trade_quantity": relation.max_trade_quantity,
        "max_daily_trades": relation.max_daily_trades,
        "max_daily_commodity_volume": relation.max_daily_commodity_volume,
        "status": relation.status,
        "invitation_token": relation.invitation_token,
        "registration_link": build_customer_registration_link(relation.invitation_token),
        "expires_at": relation.expires_at,
        "activated_at": relation.activated_at,
        "deleted_at": relation.deleted_at,
        "created_at": relation.created_at,
    }


def serialize_customer_session(session: UserSession) -> schemas.CustomerSessionRead:
    return schemas.CustomerSessionRead(
        id=str(session.id),
        device_name=session.device_name,
        device_ip=session.device_ip,
        platform=session.platform.value if hasattr(session.platform, "value") else str(session.platform),
        home_server=session.home_server or "foreign",
        is_primary=session.is_primary,
        is_active=session.is_active,
        created_at=session.created_at,
        last_active_at=session.last_active_at,
    )


async def ensure_owner_context(context: EffectiveOwnerActor, db: AsyncSession) -> None:
    if context.is_accountant_context:
        raise HTTPException(status_code=403, detail="Accountants cannot manage owner customers")
    actor_user = getattr(context, "actor_user", None) or context.owner_user
    if hasattr(db, "execute") and await is_user_customer(db, actor_user.id):
        raise HTTPException(status_code=403, detail="Customers cannot manage owner customers")


async def get_active_owner_customer_relation(
    db: AsyncSession,
    *,
    owner_user_id: int,
    relation_id: int,
) -> CustomerRelation:
    stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.customer_user))
        .where(
            CustomerRelation.id == relation_id,
            CustomerRelation.owner_user_id == owner_user_id,
        )
    )
    relation = (await db.execute(stmt)).scalar_one_or_none()
    if relation is None:
        raise HTTPException(status_code=404, detail="رابطه مشتری یافت نشد")
    if relation.deleted_at is not None or relation.status != CustomerRelationStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="فقط مشتری فعال نشست قابل مدیریت دارد")
    customer_user = relation.customer_user
    if relation.customer_user_id is None or customer_user is None or customer_user.is_deleted:
        raise HTTPException(status_code=400, detail="برای این مشتری نشست فعالی قابل مدیریت نیست")
    return relation


async def get_active_customer_session(
    db: AsyncSession,
    *,
    customer_user_id: int,
    session_id: uuid.UUID,
) -> UserSession:
    stmt = select(UserSession).where(
        and_(
            UserSession.id == session_id,
            UserSession.user_id == customer_user_id,
            UserSession.is_active == True,
        )
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="نشست یافت نشد")
    return session


@router.get("/owner-relations", response_model=list[schemas.CustomerRelationRead])
async def list_my_customers(
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relations = await list_owner_customer_relations(db, owner_user_id=context.owner_user.id)
    invitation_map = await load_customer_relation_invitation_map(
        db,
        [relation.invitation_token for relation in relations],
    )
    return [
        serialize_customer_relation(relation, invitation=invitation_map.get(relation.invitation_token))
        for relation in relations
    ]


@router.post("/owner-relations", response_model=schemas.CustomerRelationRead)
async def create_my_customer(
    payload: schemas.CustomerRelationCreate,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation, invitation = await create_owner_customer_relation(
        db,
        owner_user=context.owner_user,
        account_name=payload.account_name,
        management_name=payload.management_name,
        mobile_number=payload.mobile_number,
        customer_tier=payload.customer_tier,
        commission_rate=payload.commission_rate,
        min_trade_quantity=payload.min_trade_quantity,
        max_trade_quantity=payload.max_trade_quantity,
        max_daily_trades=payload.max_daily_trades,
        max_daily_commodity_volume=payload.max_daily_commodity_volume,
    )

    registration_link = build_customer_registration_link(relation.invitation_token)
    if registration_link:
        send_customer_invitation_sms(
            mobile=invitation.mobile_number,
            management_name=relation.management_name,
            web_link=registration_link,
        )

    return serialize_customer_relation(relation, invitation=invitation)


@router.delete("/owner-relations/{relation_id}", response_model=schemas.CustomerRelationRead)
async def unlink_my_customer(
    relation_id: int,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation = await unlink_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    invitation_map = await load_customer_relation_invitation_map(db, [relation.invitation_token])
    return serialize_customer_relation(relation, invitation=invitation_map.get(relation.invitation_token))


@router.patch("/owner-relations/{relation_id}", response_model=schemas.CustomerRelationRead)
async def update_my_customer(
    relation_id: int,
    payload: schemas.CustomerRelationUpdate,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation = await update_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
        update_data=payload.model_dump(exclude_unset=True),
    )
    invitation_map = await load_customer_relation_invitation_map(db, [relation.invitation_token])
    return serialize_customer_relation(relation, invitation=invitation_map.get(relation.invitation_token))


@router.get("/owner-relations/{relation_id}/sessions", response_model=list[schemas.CustomerSessionRead])
async def list_my_customer_sessions(
    relation_id: int,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation = await get_active_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    sessions = await get_active_sessions(db, relation.customer_user_id)
    return [serialize_customer_session(session) for session in sessions]


@router.delete("/owner-relations/{relation_id}/sessions/{session_id}", response_model=schemas.CustomerSessionTerminateResponse)
async def terminate_my_customer_session(
    relation_id: int,
    session_id: str,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    try:
        normalized_session_id = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="شناسه نشست نامعتبر است") from exc

    relation = await get_active_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    session = await get_active_customer_session(
        db,
        customer_user_id=relation.customer_user_id,
        session_id=normalized_session_id,
    )
    promoted_session = await logout_session(db, session)
    return {
        "detail": "نشست مشتری با موفقیت پایان یافت",
        "terminated_session_id": str(session.id),
        "promoted_primary_session_id": str(promoted_session.id) if promoted_session else None,
    }