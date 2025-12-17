# src/core/repositories/user_repository.py
"""User Repository Interface"""

from typing import Protocol, Optional, List
from src.core.entities.user import UserEntity


class IUserRepository(Protocol):
    """اینترفیس Repository کاربر - بدون وابستگی به SQLAlchemy"""
    
    async def get_by_id(self, user_id: int) -> Optional[UserEntity]:
        """دریافت کاربر با آیدی"""
        ...
    
    async def get_by_telegram_id(self, telegram_id: int) -> Optional[UserEntity]:
        """دریافت کاربر با آیدی تلگرام"""
        ...
    
    async def get_by_mobile(self, mobile_number: str) -> Optional[UserEntity]:
        """دریافت کاربر با شماره موبایل"""
        ...
    
    async def get_all(self, limit: int = 100, offset: int = 0) -> List[UserEntity]:
        """دریافت همه کاربران"""
        ...
    
    async def create(self, user: UserEntity) -> UserEntity:
        """ایجاد کاربر جدید"""
        ...
    
    async def update(self, user: UserEntity) -> UserEntity:
        """بروزرسانی کاربر"""
        ...
    
    async def delete(self, user_id: int) -> bool:
        """حذف کاربر"""
        ...
    
    async def count_by_role(self, role: str) -> int:
        """شمارش کاربران با نقش مشخص"""
        ...
