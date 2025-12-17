# src/interfaces/http_api/routers/user_router.py
"""
روتر کاربران - HTTP API

این روتر endpoint های مربوط به کاربران را فراهم می‌کند.
هیچ منطق بیزینسی در اینجا نیست - همه به سرویس‌ها delegate می‌شود.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

from src.core.services.user_service import UserService
from src.core.schemas.user_schemas import UserResponse, UserBrief, UserUpdate
from src.core.exceptions.user_exceptions import UserNotFoundError
from src.interfaces.http_api.dependencies import get_user_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    service: UserService = Depends(get_user_service)
):
    """دریافت اطلاعات کاربر"""
    try:
        return await service.get_user(user_id)
    except UserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="کاربر یافت نشد"
        )


@router.get("/telegram/{telegram_id}", response_model=UserResponse)
async def get_user_by_telegram(
    telegram_id: int,
    service: UserService = Depends(get_user_service)
):
    """دریافت کاربر با آیدی تلگرام"""
    user = await service.get_by_telegram(telegram_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="کاربر یافت نشد"
        )
    return user


@router.get("/", response_model=List[UserBrief])
async def list_users(
    limit: int = 100,
    offset: int = 0,
    service: UserService = Depends(get_user_service)
):
    """لیست کاربران"""
    return await service.list_users(limit, offset)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdate,
    service: UserService = Depends(get_user_service)
):
    """بروزرسانی کاربر"""
    try:
        return await service.update_user(user_id, data)
    except UserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="کاربر یافت نشد"
        )
