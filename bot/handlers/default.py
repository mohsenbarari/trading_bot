# bot/handlers/default.py
from aiogram import Router, types
from typing import Optional
from models.user import User
from bot.utils.redis_helpers import is_deleted_telegram_user

router = Router()

@router.message()
async def handle_unauthorized_messages(message: types.Message, user: Optional[User]):
    """
    این handler به تمام پیام‌هایی که توسط سایر handlerها مدیریت نشده‌اند پاسخ می‌دهد.
    """
    if not user:
        if message.from_user and await is_deleted_telegram_user(message.from_user.id):
            return
        await message.answer("⛔️ بات برای شما غیرفعال است. لطفاً از طریق لینک دعوت معتبر ثبت‌نام کنید.")
    # اگر کاربر `user` وجود داشته باشد (یعنی مجاز باشد)، به پیام او پاسخی نمی‌دهیم.