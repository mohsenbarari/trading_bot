# bot/middlewares/auth.py (نسخه نهایی و اصلاح شده)
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from models.user import User

class AuthMiddleware(BaseMiddleware):
    """
    این میدل‌ور، session دیتابیس را به handler ها تزریق کرده و کاربر را احراز هویت می‌کند.
    """
    def __init__(self, session_pool: async_sessionmaker[AsyncSession]):
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        user_telegram_obj = None
        if isinstance(event, (Message, CallbackQuery)):
            user_telegram_obj = event.from_user
        
        if not user_telegram_obj:
            return await handler(event, data)

        async with self.session_pool() as session:
            # کاربر را از دیتابیس خودمان پیدا می‌کنیم
            user = (await session.execute(
                select(User).where(User.telegram_id == user_telegram_obj.id)
            )).scalar_one_or_none()
            
            # آبجکت کاربر دیتابیس (که ممکن است None باشد) را به data اضافه می‌کنیم
            # تا در تمام handler ها در دسترس باشد.
            data["user"] = user
            
            # بررسی دسترسی بات
            if user and not user.has_bot_access:
                # کاربر دسترسی به بات ندارد - پیام مناسب نمایش بده
                restricted_message = (
                    "⚠️ دسترسی شما به ربات محدود شده است.\n\n"
                    "لطفاً از طریق MiniApp به سیستم دسترسی پیدا کنید.\n"
                    "برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
                )
                if isinstance(event, Message):
                    await event.answer(restricted_message)
                elif isinstance(event, CallbackQuery):
                    await event.answer(restricted_message, show_alert=True)
                return  # جلوی ادامه پردازش را بگیر
            
            return await handler(event, data)