# bot/middlewares/auth.py (نسخه نهایی و اصلاح شده)
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery, Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from bot.onboarding import (
    BOT_ONBOARDING_BLOCK_MESSAGE,
    CUSTOMER_TUTORIAL_ACK_CALLBACK,
    OFFER_TUTORIAL_ACK_CALLBACK,
    build_onboarding_keyboard,
    is_allowed_onboarding_callback,
    onboarding_text_for_step,
    pending_onboarding_step,
    user_requires_bot_onboarding,
)
from core.services.user_account_status_service import is_user_global_web_locked
from models.user import User


def _get_event_and_from_user(event: TelegramObject):
    """برگرداندن (event واقعی برای پاسخ، from_user). وقتی روی dp.update ثبت شده، event از نوع Update است."""
    if isinstance(event, Update):
        inner = event.message or event.callback_query or event.edited_message
        if inner and hasattr(inner, "from_user"):
            return inner, inner.from_user
        return None, None
    if isinstance(event, (Message, CallbackQuery)):
        return event, event.from_user
    return None, None


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
        inner_event, user_telegram_obj = _get_event_and_from_user(event)
        
        if not user_telegram_obj:
            return await handler(event, data)

        async with self.session_pool() as session:
            # کاربر را از دیتابیس خودمان پیدا می‌کنیم (فقط کاربران فعال)
            user = (await session.execute(
                select(User).where(User.telegram_id == user_telegram_obj.id, User.is_deleted == False)
            )).scalar_one_or_none()
            
            # آبجکت کاربر دیتابیس (که ممکن است None باشد) را به data اضافه می‌کنیم
            # تا در تمام handler ها در دسترس باشد.
            data["user"] = user
            
            # بررسی قفل سراسری حساب بعد از پایان مهلت غیرفعال‌سازی
            if user and is_user_global_web_locked(user):
                restricted_message = (
                    "⛔ دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است.\n\n"
                    "پس از فعال‌سازی مجدد حساب، دسترسی شما باز می‌شود."
                )
                if inner_event is not None:
                    if isinstance(inner_event, Message):
                        await inner_event.answer(restricted_message)
                    elif isinstance(inner_event, CallbackQuery):
                        await inner_event.answer(restricted_message, show_alert=True)
                return

            if user and user_requires_bot_onboarding(user):
                if isinstance(inner_event, CallbackQuery):
                    if is_allowed_onboarding_callback(user, getattr(inner_event, "data", None)):
                        return await handler(event, data)
                    await inner_event.answer(BOT_ONBOARDING_BLOCK_MESSAGE, show_alert=True)
                elif isinstance(inner_event, Message):
                    pending_step = pending_onboarding_step(user)
                    await inner_event.answer(
                        onboarding_text_for_step(pending_step or 1),
                        reply_markup=build_onboarding_keyboard(pending_step or 1),
                    )
                return

            return await handler(event, data)
