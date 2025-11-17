# trading_bot/api/routers/users.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from core.db import get_db
from models.user import User
from .auth import verify_super_admin_or_dev_key
import schemas

router = APIRouter(
    prefix="/users",
    tags=["Users Management"],
    dependencies=[Depends(verify_super_admin_or_dev_key)]
)

@router.get("/", response_model=List[schemas.UserRead])
async def read_all_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """دریافت لیست کاربران (فقط برای مدیر ارشد)"""
    stmt = select(User).order_by(User.id.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    users = result.scalars().all()
    return users