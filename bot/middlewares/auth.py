# bot/middlewares/auth.py (نسخه نهایی و قطعی)
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from sqlalchemy import select
from core.db import AsyncSessionLocal
from models.user import User

class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        # مشخص می‌کنیم که آبجکت کاربر از کدام نوع آپدیت باید استخراج شود
        if isinstance(event, Message):
            user_telegram_obj = event.from_user
        elif isinstance(event, CallbackQuery):
            user_telegram_obj = event.from_user
        else:
            # برای سایر انواع آپدیت، کاری انجام نمی‌دهیم
            return await handler(event, data)

        # اگر به هر دلیلی آبجکت کاربر تلگرام وجود نداشت، ادامه می‌دهیم
        if not user_telegram_obj:
            return await handler(event, data)

        # حالا با استفاده از آبجکت کاربر تلگرام، کاربر را از دیتابیس خودمان پیدا می‌کنیم
        async with AsyncSessionLocal() as session:
            user = (await session.execute(
                select(User).where(User.telegram_id == user_telegram_obj.id)
            )).scalar_one_or_none()
            
            # آبجکت کاربر دیتابیس (که ممکن است None باشد) را به data اضافه می‌کنیم
            data["user"] = user
        
        return await handler(event, data)