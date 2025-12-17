# src/core/entities/user.py
"""موجودیت کاربر - بدون وابستگی به ORM"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from enum import Enum


class UserRole(str, Enum):
    """نقش‌های کاربر"""
    ADMIN = "admin"
    MEMBER = "member"


@dataclass
class UserEntity:
    """موجودیت کاربر - Pure Python"""
    id: Optional[int] = None
    account_name: Optional[str] = None
    mobile_number: Optional[str] = None
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    address: Optional[str] = None
    role: UserRole = UserRole.MEMBER
    has_bot_access: bool = True
    trading_restricted_until: Optional[datetime] = None
    max_daily_trades: Optional[int] = None
    max_active_commodities: Optional[int] = None
    max_daily_requests: Optional[int] = None
    limitations_expire_at: Optional[datetime] = None
    trades_count: int = 0
    commodities_traded_count: int = 0
    channel_messages_count: int = 0
    created_at: Optional[datetime] = None
