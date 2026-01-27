# api/routers/blocks.py
"""
API برای مدیریت بلاک کاربران
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from core.db import get_db
from models.user import User
from .auth import get_current_user
from core.services.block_service import (
    block_user,
    unblock_user,
    is_blocked,
    get_blocked_users,
    get_block_status,
    search_users_for_block,
    is_blocked_by
)

router = APIRouter(
    prefix="/blocks",
    tags=["User Blocks"]
)


# ===== Schemas =====

class BlockedUserResponse(BaseModel):
    id: int
    account_name: str
    mobile_number: str
    full_name: Optional[str] = None
    blocked_at: datetime
    
    class Config:
        from_attributes = True


class BlockStatusResponse(BaseModel):
    can_block: bool
    max_blocked: int
    current_blocked: int
    remaining: int


class UserSearchResult(BaseModel):
    id: int
    account_name: str
    mobile_number: str
    full_name: Optional[str] = None
    is_blocked: bool


class MessageResponse(BaseModel):
    success: bool
    message: str


# ===== Endpoints =====

@router.get("/status", response_model=BlockStatusResponse)
async def get_my_block_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    دریافت وضعیت قابلیت بلاک
    - can_block: آیا قابلیت فعال است؟
    - max_blocked: حداکثر تعداد مجاز
    - current_blocked: تعداد فعلی
    - remaining: تعداد باقی‌مانده
    """
    status_data = await get_block_status(db, current_user.id)
    return status_data


@router.get("/", response_model=List[BlockedUserResponse])
async def get_my_blocked_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    لیست کاربران مسدود شده توسط من
    """
    blocked = await get_blocked_users(db, current_user.id)
    return blocked


@router.post("/{user_id}", response_model=MessageResponse)
async def block_a_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    مسدود کردن کاربر
    """
    # چک وجود کاربر هدف
    target_user = await db.get(User, user_id)
    if not target_user or target_user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="کاربر یافت نشد."
        )
    
    success, message = await block_user(db, current_user.id, user_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message
        )
    
    return {"success": True, "message": message}


@router.delete("/{user_id}", response_model=MessageResponse)
async def unblock_a_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    رفع مسدودیت کاربر
    """
    success, message = await unblock_user(db, current_user.id, user_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message
        )
    
    return {"success": True, "message": message}


@router.get("/check/{user_id}")
async def check_block_status(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    بررسی وضعیت بلاک با یک کاربر
    - is_blocked: آیا بلاک وجود دارد؟
    - blocked_by_me: آیا من این کاربر را بلاک کردم؟
    - blocked_me: آیا این کاربر من را بلاک کرده؟ (این اطلاعات محرمانه است - برای admin)
    """
    blocked_by_me = await is_blocked_by(db, current_user.id, user_id)
    
    return {
        "user_id": user_id,
        "is_blocked_by_me": blocked_by_me
    }


@router.get("/search", response_model=List[UserSearchResult])
async def search_users(
    q: str = Query(..., min_length=2, description="شماره موبایل یا نام کاربری (حداقل 2 کاراکتر)"),
    limit: int = Query(10, le=20),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    جستجوی کاربران برای بلاک/رفع بلاک
    """
    users = await search_users_for_block(db, q, current_user.id, limit)
    return users
