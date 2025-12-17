# src/core/schemas/user_schemas.py
"""Pydantic schemas برای کاربر"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from src.core.entities.user import UserRole


class UserBase(BaseModel):
    """فیلدهای مشترک کاربر"""
    account_name: Optional[str] = None
    mobile_number: Optional[str] = None
    full_name: Optional[str] = None
    address: Optional[str] = None


class UserCreate(UserBase):
    """ایجاد کاربر جدید"""
    telegram_id: int
    username: Optional[str] = None


class UserUpdate(BaseModel):
    """بروزرسانی کاربر"""
    account_name: Optional[str] = None
    full_name: Optional[str] = None
    address: Optional[str] = None
    has_bot_access: Optional[bool] = None


class UserResponse(UserBase):
    """پاسخ کاربر"""
    id: int
    telegram_id: int
    username: Optional[str] = None
    role: UserRole
    has_bot_access: bool
    trading_restricted_until: Optional[datetime] = None
    trades_count: int = 0
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserBrief(BaseModel):
    """خلاصه کاربر"""
    id: int
    account_name: Optional[str] = None
    mobile_number: Optional[str] = None
    role: UserRole

    class Config:
        from_attributes = True
