from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import joinedload
from typing import List, Optional

from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from core.db import get_db
from core.services.accountant_relation_service import get_active_accountant_relation_for_accountant
from models.user import User
from api.deps import get_current_user

import schemas

router = APIRouter(
    tags=["Public Users"],
    dependencies=[Depends(get_current_user)]
)


def _serialize_public_user(
    user: User,
    *,
    resolved_from_accountant_id: int | None = None,
    highlight_accountant_user_id: int | None = None,
    highlight_accountant_relation_display_name: str | None = None,
) -> schemas.UserPublicRead:
    public_user = schemas.UserPublicRead.model_validate(user, from_attributes=True)
    return public_user.model_copy(update={
        "resolved_from_accountant_id": resolved_from_accountant_id,
        "highlight_accountant_user_id": highlight_accountant_user_id,
        "highlight_accountant_relation_display_name": highlight_accountant_relation_display_name,
    })


async def _resolve_public_search_rows(
    db: AsyncSession,
    rows: list[User],
    *,
    current_user_id: int,
) -> list[schemas.UserPublicRead]:
    user_ids = [user.id for user in rows if getattr(user, "id", None) is not None]
    if not user_ids:
        return []

    relation_stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.owner_user))
        .where(
            AccountantRelation.accountant_user_id.in_(user_ids),
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
    )
    relations = list((await db.execute(relation_stmt)).scalars().all())
    relation_by_accountant_id = {
        relation.accountant_user_id: relation
        for relation in relations
        if relation.accountant_user_id is not None
    }

    serialized_rows: list[schemas.UserPublicRead] = []
    seen_user_ids: set[int] = set()
    for user in rows:
        relation = relation_by_accountant_id.get(user.id)
        if relation and relation.owner_user and not relation.owner_user.is_deleted:
            owner_user = relation.owner_user
            if owner_user.id == current_user_id or owner_user.id in seen_user_ids:
                continue
            serialized_rows.append(
                _serialize_public_user(
                    owner_user,
                    resolved_from_accountant_id=user.id,
                    highlight_accountant_user_id=user.id,
                    highlight_accountant_relation_display_name=relation.relation_display_name,
                )
            )
            seen_user_ids.add(owner_user.id)
            continue

        if user.id == current_user_id or user.id in seen_user_ids:
            continue
        serialized_rows.append(_serialize_public_user(user))
        seen_user_ids.add(user.id)

    return serialized_rows

@router.get("/search", response_model=List[schemas.UserPublicRead])
async def search_public_users(
    q: Optional[str] = Query(None, min_length=1),
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """جستجوی عمومی بین کاربران سیستم بر اساس نام، نام کاربری یا شماره فیلتر شده"""
    query = select(User).where(User.is_deleted == False, User.id != current_user.id)

    if q:
        search_pattern = f"%{q}%"
        query = query.where(
            or_(
                User.full_name.ilike(search_pattern),
                User.account_name.ilike(search_pattern),
                User.username.ilike(search_pattern),
                User.mobile_number.ilike(search_pattern)
            )
        )
        
    query = query.order_by(User.id.desc()).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    return await _resolve_public_search_rows(db, users, current_user_id=current_user.id)

@router.get("/{user_id}", response_model=schemas.UserPublicRead)
async def read_public_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """دریافت اطلاعات عمومی یک کاربر (قابل دسترسی برای همه کاربران لاگین شده)"""
    relation = await get_active_accountant_relation_for_accountant(db, user_id)
    if relation and relation.owner_user and not relation.owner_user.is_deleted:
        return _serialize_public_user(
            relation.owner_user,
            resolved_from_accountant_id=user_id,
            highlight_accountant_user_id=user_id,
            highlight_accountant_relation_display_name=relation.relation_display_name,
        )

    user = await db.get(User, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return _serialize_public_user(user)
