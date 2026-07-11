import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

import schemas
from api.deps import get_effective_owner_actor_context
from core.audit_logger import audit_log
from core.config import settings
from core.db import get_db
from core.public_webapp_url import public_webapp_url_for_links
from core.services.accountant_relation_service import (
    EffectiveOwnerActor,
    create_or_reuse_owner_accountant_relation,
    list_owner_accountant_relations,
    unlink_owner_accountant_relation,
    update_owner_accountant_relation,
)
from core.invitation_sms_policy import invitation_sms_enabled, invitation_sms_status
from core.registration_contracts import InvitationSMSStatus
from models.invitation import InvitationKind
from core.services.customer_relation_service import is_user_customer
from core.services.session_service import get_active_sessions, logout_session
from core.sms import send_accountant_invitation_sms
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.session import UserSession
from models.user import User


router = APIRouter()


def build_accountant_registration_link(invitation_token: str) -> str | None:
    return f"{public_webapp_url_for_links()}/register?token={invitation_token}"


def serialize_accountant_relation(relation) -> dict:
    return {
        "id": relation.id,
        "owner_user_id": relation.owner_user_id,
        "accountant_user_id": relation.accountant_user_id,
        "accountant_account_name": getattr(getattr(relation, "accountant_user", None), "account_name", None),
        "global_account_name": relation.global_account_name,
        "relation_display_name": relation.relation_display_name,
        "duty_description": relation.duty_description,
        "mobile_number": relation.mobile_number,
        "status": relation.status,
        "invitation_token": relation.invitation_token,
        "registration_link": build_accountant_registration_link(relation.invitation_token),
        "bot_registration_link": None,
        "web_registration_link": build_accountant_registration_link(relation.invitation_token),
        "expires_at": relation.expires_at,
        "activated_at": relation.activated_at,
        "deleted_at": relation.deleted_at,
        "created_at": relation.created_at,
    }


def audit_actor_context(context: EffectiveOwnerActor) -> dict:
    actor_user = getattr(context, "actor_user", None) or context.owner_user
    return {
        "actor_id": getattr(actor_user, "id", None),
        "actor_role": getattr(getattr(actor_user, "role", None), "value", getattr(actor_user, "role", None)),
    }


async def ensure_owner_context(context: EffectiveOwnerActor, db: AsyncSession) -> None:
    if context.is_accountant_context:
        raise HTTPException(status_code=403, detail="Accountants cannot manage owner accountants")
    actor_user = getattr(context, "actor_user", None) or context.owner_user
    if getattr(actor_user, "is_customer", False) is True:
        raise HTTPException(status_code=403, detail="Customers cannot manage owner accountants")
    if isinstance(actor_user, User) and await is_user_customer(db, actor_user.id):
        raise HTTPException(status_code=403, detail="Customers cannot manage owner accountants")


def serialize_accountant_session(session: UserSession) -> schemas.AccountantSessionRead:
    return schemas.AccountantSessionRead(
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


async def get_active_owner_accountant_relation(
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
    if relation is None:
        raise HTTPException(status_code=404, detail="رابطه حسابدار یافت نشد")
    if relation.deleted_at is not None or relation.status != AccountantRelationStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="فقط حسابدار فعال نشست قابل مدیریت دارد")
    accountant_user = relation.accountant_user
    if relation.accountant_user_id is None or accountant_user is None or accountant_user.is_deleted:
        raise HTTPException(status_code=400, detail="برای این حسابدار نشست فعالی قابل مدیریت نیست")
    return relation


async def get_active_accountant_session(
    db: AsyncSession,
    *,
    accountant_user_id: int,
    session_id: uuid.UUID,
) -> UserSession:
    stmt = select(UserSession).where(
        and_(
            UserSession.id == session_id,
            UserSession.user_id == accountant_user_id,
            UserSession.is_active == True,
        )
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="نشست یافت نشد")
    return session


@router.get("/owner-relations", response_model=list[schemas.AccountantRelationRead])
async def list_my_accountants(
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relations = await list_owner_accountant_relations(db, owner_user_id=context.owner_user.id)
    return [serialize_accountant_relation(relation) for relation in relations]


@router.post("/owner-relations", response_model=schemas.AccountantRelationRead)
async def create_my_accountant(
    payload: schemas.AccountantRelationCreate,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    public_webapp_url_for_links()
    creation = await create_or_reuse_owner_accountant_relation(
        db,
        owner_user=context.owner_user,
        global_account_name=payload.account_name,
        relation_display_name=payload.relation_display_name,
        mobile_number=payload.mobile_number,
        duty_description=payload.duty_description,
    )
    relation = creation.relation

    registration_link = build_accountant_registration_link(relation.invitation_token)
    sms_enabled = bool(
        creation.created
        and registration_link
        and invitation_sms_enabled(InvitationKind.ACCOUNTANT)
    )
    sms_accepted: bool | None = None
    if (
        sms_enabled
    ):
        sms_accepted = bool(send_accountant_invitation_sms(
            mobile=relation.mobile_number,
            relation_display_name=relation.relation_display_name,
            web_link=registration_link,
        ))

    audit_log(
        "accountant.link",
        target_type="accountant_relation",
        target_id=relation.id,
        after_summary={
            "owner_user_id": relation.owner_user_id,
            "accountant_user_id": relation.accountant_user_id,
            "status": relation.status,
        },
        **audit_actor_context(context),
    )

    response = serialize_accountant_relation(relation)
    response["sms_status"] = invitation_sms_status(
        enabled=sms_enabled,
        accepted=sms_accepted,
    )
    return response


@router.get("/owner-relations/{relation_id}/sessions", response_model=list[schemas.AccountantSessionRead])
async def list_my_accountant_sessions(
    relation_id: int,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation = await get_active_owner_accountant_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    sessions = await get_active_sessions(db, relation.accountant_user_id)
    return [serialize_accountant_session(session) for session in sessions]


@router.delete("/owner-relations/{relation_id}/sessions/{session_id}", response_model=schemas.AccountantSessionTerminateResponse)
async def terminate_my_accountant_session(
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

    relation = await get_active_owner_accountant_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    session = await get_active_accountant_session(
        db,
        accountant_user_id=relation.accountant_user_id,
        session_id=normalized_session_id,
    )
    promoted_session = await logout_session(db, session)
    audit_log(
        "accountant.session_terminate",
        target_type="accountant_session",
        target_id=session.id,
        after_summary={
            "relation_id": relation.id,
            "accountant_user_id": relation.accountant_user_id,
            "promoted_primary_session_id": str(promoted_session.id) if promoted_session else None,
        },
        **audit_actor_context(context),
    )
    return {
        "detail": "نشست حسابدار با موفقیت پایان یافت",
        "terminated_session_id": str(session.id),
        "promoted_primary_session_id": str(promoted_session.id) if promoted_session else None,
    }


@router.delete("/owner-relations/{relation_id}", response_model=schemas.AccountantRelationRead)
async def cancel_my_pending_accountant(
    relation_id: int,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation = await unlink_owner_accountant_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    audit_log(
        "accountant.unlink",
        target_type="accountant_relation",
        target_id=relation.id,
        before_summary={
            "owner_user_id": relation.owner_user_id,
            "accountant_user_id": relation.accountant_user_id,
            "status": relation.status,
        },
        after_summary={"deleted_at": relation.deleted_at, "status": relation.status},
        **audit_actor_context(context),
    )
    return serialize_accountant_relation(relation)


@router.patch("/owner-relations/{relation_id}", response_model=schemas.AccountantRelationRead)
async def update_my_accountant(
    relation_id: int,
    payload: schemas.AccountantRelationUpdate,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation = await update_owner_accountant_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
        duty_description=payload.duty_description,
    )
    audit_log(
        "accountant.update",
        target_type="accountant_relation",
        target_id=relation.id,
        after_summary={"updated_fields": ["duty_description"], "status": relation.status},
        **audit_actor_context(context),
    )
    return serialize_accountant_relation(relation)
