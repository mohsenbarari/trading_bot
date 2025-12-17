# src/infrastructure/database/repositories/sqlalchemy_user_repo.py
"""پیاده‌سازی User Repository با SQLAlchemy"""

from typing import Optional, List
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.user import UserEntity, UserRole
from src.core.repositories.user_repository import IUserRepository


class SQLAlchemyUserRepository(IUserRepository):
    """
    پیاده‌سازی Repository کاربر با SQLAlchemy
    
    این کلاس وابستگی به SQLAlchemy دارد اما از Core ایزوله است.
    """
    
    def __init__(self, session: AsyncSession, model_class):
        self._session = session
        self._model = model_class
    
    async def get_by_id(self, user_id: int) -> Optional[UserEntity]:
        result = await self._session.get(self._model, user_id)
        return self._to_entity(result) if result else None
    
    async def get_by_telegram_id(self, telegram_id: int) -> Optional[UserEntity]:
        stmt = select(self._model).where(self._model.telegram_id == telegram_id)
        result = await self._session.scalar(stmt)
        return self._to_entity(result) if result else None
    
    async def get_by_mobile(self, mobile_number: str) -> Optional[UserEntity]:
        stmt = select(self._model).where(self._model.mobile_number == mobile_number)
        result = await self._session.scalar(stmt)
        return self._to_entity(result) if result else None
    
    async def get_all(self, limit: int = 100, offset: int = 0) -> List[UserEntity]:
        stmt = select(self._model).offset(offset).limit(limit)
        result = await self._session.scalars(stmt)
        return [self._to_entity(r) for r in result.all()]
    
    async def create(self, user: UserEntity) -> UserEntity:
        model = self._to_model(user)
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return self._to_entity(model)
    
    async def update(self, user: UserEntity) -> UserEntity:
        model = await self._session.get(self._model, user.id)
        if model:
            model.account_name = user.account_name
            model.mobile_number = user.mobile_number
            model.full_name = user.full_name
            model.address = user.address
            model.has_bot_access = user.has_bot_access
            model.trading_restricted_until = user.trading_restricted_until
            await self._session.commit()
            await self._session.refresh(model)
            return self._to_entity(model)
        return user
    
    async def delete(self, user_id: int) -> bool:
        model = await self._session.get(self._model, user_id)
        if model:
            await self._session.delete(model)
            await self._session.commit()
            return True
        return False
    
    async def count_by_role(self, role: str) -> int:
        stmt = select(func.count(self._model.id)).where(self._model.role == role)
        result = await self._session.scalar(stmt)
        return result or 0
    
    def _to_entity(self, model) -> UserEntity:
        """تبدیل ORM Model به Entity"""
        return UserEntity(
            id=model.id,
            account_name=model.account_name,
            mobile_number=model.mobile_number,
            telegram_id=model.telegram_id,
            username=model.username,
            full_name=model.full_name,
            address=model.address,
            role=UserRole(model.role.value) if model.role else UserRole.MEMBER,
            has_bot_access=model.has_bot_access,
            trading_restricted_until=model.trading_restricted_until,
            max_daily_trades=model.max_daily_trades,
            max_active_commodities=model.max_active_commodities,
            max_daily_requests=model.max_daily_requests,
            limitations_expire_at=model.limitations_expire_at,
            trades_count=model.trades_count or 0,
            commodities_traded_count=model.commodities_traded_count or 0,
            channel_messages_count=model.channel_messages_count or 0,
            created_at=model.created_at,
        )
    
    def _to_model(self, entity: UserEntity):
        """تبدیل Entity به ORM Model"""
        return self._model(
            account_name=entity.account_name,
            mobile_number=entity.mobile_number,
            telegram_id=entity.telegram_id,
            username=entity.username,
            full_name=entity.full_name,
            address=entity.address,
            role=entity.role,
            has_bot_access=entity.has_bot_access,
        )
