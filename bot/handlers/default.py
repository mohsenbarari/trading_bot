# bot/handlers/default.py (فایل جدید)
from aiogram import Router, types
from typing import Optional
from models.user import User

router = Router()

@router.message()
async def handle_unauthorized_messages(message: types.Message, user: Optional[User]):
    """
    این handler به تمام پیام‌هایی که توسط سایر handlerها مدیریت نشده‌اند پاسخ می‌دهد.
    """
    if not user:
        await message.answer("⛔️ بات برای شما غیرفعال است. لطفاً از طریق لینک دعوت معتبر ثبت‌نام کنید.")
    # اگر کاربر `user` وجود داشته باشد (یعنی مجاز باشد)، به پیام او پاسخی نمی‌دهیم.