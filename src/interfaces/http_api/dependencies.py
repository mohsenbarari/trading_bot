# src/interfaces/http_api/dependencies.py
"""
Dependency Injection برای FastAPI

این ماژول Dependency های سرویس‌ها را برای استفاده در روترهای API فراهم می‌کند.
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import AsyncGenerator

from src.core.services.user_service import UserService
from src.core.services.offer_service import OfferService
from src.infrastructure.database.repositories.sqlalchemy_user_repo import SQLAlchemyUserRepository

# این‌ها در startup اپلیکیشن تنظیم می‌شوند
_get_session = None
_user_model = None
_offer_model = None
_max_active_offers = 3


def configure_api_dependencies(
    get_session_func,
    user_model,
    offer_model=None,
    max_active_offers: int = 3
):
    """پیکربندی وابستگی‌ها برای API"""
    global _get_session, _user_model, _offer_model, _max_active_offers
    _get_session = get_session_func
    _user_model = user_model
    _offer_model = offer_model
    _max_active_offers = max_active_offers


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency برای Session دیتابیس"""
    if _get_session is None:
        raise RuntimeError("API Dependencies not configured")
    
    async for session in _get_session():
        yield session


async def get_user_service(
    session: AsyncSession = Depends(get_db_session)
) -> UserService:
    """
    Dependency برای UserService
    
    استفاده در روترها:
    ```python
    from src.interfaces.http_api.dependencies import get_user_service
    
    @router.get("/users/{user_id}")
    async def get_user(
        user_id: int,
        service: UserService = Depends(get_user_service)
    ):
        return await service.get_user(user_id)
    ```
    """
    repo = SQLAlchemyUserRepository(session, _user_model)
    return UserService(repo)


# می‌توان سرویس‌های بیشتری اضافه کرد
# async def get_offer_service(
#     session: AsyncSession = Depends(get_db_session)
# ) -> OfferService:
#     user_repo = SQLAlchemyUserRepository(session, _user_model)
#     offer_repo = SQLAlchemyOfferRepository(session, _offer_model)
#     return OfferService(offer_repo, user_repo, _max_active_offers)
