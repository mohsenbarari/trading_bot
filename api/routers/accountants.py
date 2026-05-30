from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import schemas
from api.deps import get_effective_owner_actor_context
from core.config import settings
from core.db import get_db
from core.services.accountant_relation_service import (
    EffectiveOwnerActor,
    create_owner_accountant_relation,
    list_owner_accountant_relations,
    unlink_owner_accountant_relation,
    update_owner_accountant_relation,
)
from core.services.customer_relation_service import is_user_customer
from core.sms import send_accountant_invitation_sms


router = APIRouter()


def build_accountant_registration_link(invitation_token: str) -> str | None:
    frontend_url = (getattr(settings, "frontend_url", "") or "").strip()
    if not frontend_url:
        return None
    return f"{frontend_url}/register?token={invitation_token}"


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
        "expires_at": relation.expires_at,
        "activated_at": relation.activated_at,
        "deleted_at": relation.deleted_at,
        "created_at": relation.created_at,
    }


async def ensure_owner_context(context: EffectiveOwnerActor, db: AsyncSession) -> None:
    if context.is_accountant_context:
        raise HTTPException(status_code=403, detail="Accountants cannot manage owner accountants")
    actor_user = getattr(context, "actor_user", None) or context.owner_user
    if hasattr(db, "execute") and await is_user_customer(db, actor_user.id):
        raise HTTPException(status_code=403, detail="Customers cannot manage owner accountants")


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
    relation, _invitation = await create_owner_accountant_relation(
        db,
        owner_user=context.owner_user,
        global_account_name=payload.account_name,
        relation_display_name=payload.relation_display_name,
        mobile_number=payload.mobile_number,
        duty_description=payload.duty_description,
    )

    registration_link = build_accountant_registration_link(relation.invitation_token)
    if registration_link:
        send_accountant_invitation_sms(
            mobile=relation.mobile_number,
            relation_display_name=relation.relation_display_name,
            web_link=registration_link,
        )

    return serialize_accountant_relation(relation)


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
    return serialize_accountant_relation(relation)