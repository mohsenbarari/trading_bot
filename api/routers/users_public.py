from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import List, Optional

from core.db import get_db
from models.user import User
from api.deps import get_current_active_user

import schemas

router = APIRouter(
    tags=["Public Users"],
    dependencies=[Depends(get_current_active_user)]
)

@router.get("/search", response_model=List[schemas.UserPublicRead])
async def search_public_users(
    q: Optional[str] = Query(None, min_length=1),
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
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
    return users

@router.get("/{user_id}", response_model=schemas.UserPublicRead)
async def read_public_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """دریافت اطلاعات عمومی یک کاربر (قابل دسترسی برای همه کاربران لاگین شده)"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
