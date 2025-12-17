# src/interfaces/telegram_bot/dependencies.py
"""
Dependency Injection برای Telegram Bot

این ماژول Factory های سرویس‌ها را برای استفاده در هندلرهای بات فراهم می‌کند.
"""

from typing import AsyncGenerator
from src.core.services.user_service import UserService
from src.core.services.offer_service import OfferService
from src.infrastructure.database.repositories.sqlalchemy_user_repo import SQLAlchemyUserRepository

# این‌ها بعداً از کانفیگ می‌آیند
_session_factory = None
_user_model = None
_offer_model = None
_max_active_offers = 3


def configure_dependencies(
    session_factory,
    user_model,
    offer_model=None,
    max_active_offers: int = 3
):
    """پیکربندی وابستگی‌ها"""
    global _session_factory, _user_model, _offer_model, _max_active_offers
    _session_factory = session_factory
    _user_model = user_model
    _offer_model = offer_model
    _max_active_offers = max_active_offers


async def get_user_service() -> UserService:
    """
    Factory برای UserService
    
    استفاده در هندلرهای بات:
    ```python
    from src.interfaces.telegram_bot.dependencies import get_user_service
    
    @router.message(Command("profile"))
    async def handle_profile(message: types.Message):
        service = await get_user_service()
        user = await service.get_by_telegram(message.from_user.id)
        ...
    ```
    """
    if _session_factory is None:
        raise RuntimeError("Dependencies not configured. Call configure_dependencies() first.")
    
    async with _session_factory() as session:
        repo = SQLAlchemyUserRepository(session, _user_model)
        return UserService(repo)


class BotServiceContainer:
    """
    Container برای نگهداری سرویس‌ها در طول یک درخواست
    
    استفاده:
    ```python
    async with BotServiceContainer() as container:
        user = await container.user_service.get_user(1)
        offer = await container.offer_service.get_offer(1)
    ```
    """
    
    def __init__(self):
        self._session = None
        self._user_service = None
        self._offer_service = None
    
    async def __aenter__(self):
        if _session_factory is None:
            raise RuntimeError("Dependencies not configured")
        
        self._session = _session_factory()
        session = await self._session.__aenter__()
        
        user_repo = SQLAlchemyUserRepository(session, _user_model)
        self._user_service = UserService(user_repo)
        
        # OfferService در صورت نیاز اضافه می‌شود
        
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.__aexit__(exc_type, exc_val, exc_tb)
    
    @property
    def user_service(self) -> UserService:
        return self._user_service
