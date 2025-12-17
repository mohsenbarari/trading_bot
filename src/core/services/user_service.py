# src/core/services/user_service.py
"""سرویس کاربر - بدون وابستگی به ORM یا Framework"""

from typing import Optional, List
from src.core.repositories.user_repository import IUserRepository
from src.core.entities.user import UserEntity
from src.core.schemas.user_schemas import UserCreate, UserUpdate, UserResponse, UserBrief
from src.core.exceptions.user_exceptions import UserNotFoundError, UserAlreadyExistsError


class UserService:
    """
    سرویس کاربر - Use Cases
    
    این کلاس فقط با Repository Interface و Pydantic Schemas کار می‌کند.
    هیچ وابستگی به aiogram، FastAPI یا SQLAlchemy ندارد.
    """
    
    def __init__(self, user_repo: IUserRepository):
        self._repo = user_repo
    
    async def get_user(self, user_id: int) -> UserResponse:
        """دریافت کاربر با آیدی"""
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(user_id)
        return self._to_response(user)
    
    async def get_by_telegram(self, telegram_id: int) -> Optional[UserResponse]:
        """دریافت کاربر با آیدی تلگرام"""
        user = await self._repo.get_by_telegram_id(telegram_id)
        if user:
            return self._to_response(user)
        return None
    
    async def get_by_mobile(self, mobile: str) -> Optional[UserResponse]:
        """دریافت کاربر با شماره موبایل"""
        user = await self._repo.get_by_mobile(mobile)
        if user:
            return self._to_response(user)
        return None
    
    async def list_users(self, limit: int = 100, offset: int = 0) -> List[UserBrief]:
        """لیست کاربران"""
        users = await self._repo.get_all(limit, offset)
        return [
            UserBrief(
                id=u.id,
                account_name=u.account_name,
                mobile_number=u.mobile_number,
                role=u.role
            )
            for u in users
        ]
    
    async def register_user(self, data: UserCreate) -> UserResponse:
        """ثبت کاربر جدید"""
        existing = await self._repo.get_by_telegram_id(data.telegram_id)
        if existing:
            raise UserAlreadyExistsError(f"telegram_id={data.telegram_id}")
        
        entity = UserEntity(
            telegram_id=data.telegram_id,
            account_name=data.account_name,
            mobile_number=data.mobile_number,
            username=data.username,
            full_name=data.full_name,
            address=data.address,
        )
        created = await self._repo.create(entity)
        return self._to_response(created)
    
    async def update_user(self, user_id: int, data: UserUpdate) -> UserResponse:
        """بروزرسانی کاربر"""
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(user_id)
        
        if data.account_name is not None:
            user.account_name = data.account_name
        if data.full_name is not None:
            user.full_name = data.full_name
        if data.address is not None:
            user.address = data.address
        if data.has_bot_access is not None:
            user.has_bot_access = data.has_bot_access
        
        updated = await self._repo.update(user)
        return self._to_response(updated)
    
    def _to_response(self, entity: UserEntity) -> UserResponse:
        """تبدیل Entity به Response"""
        return UserResponse(
            id=entity.id,
            telegram_id=entity.telegram_id,
            account_name=entity.account_name,
            mobile_number=entity.mobile_number,
            full_name=entity.full_name,
            address=entity.address,
            username=entity.username,
            role=entity.role,
            has_bot_access=entity.has_bot_access,
            trading_restricted_until=entity.trading_restricted_until,
            trades_count=entity.trades_count,
            created_at=entity.created_at,
        )
