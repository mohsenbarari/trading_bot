# bot/handlers/panel.py (هماهنگ شده با کیبورد دائمی)
from aiogram import Router, types, F
from aiogram.filters import Command
from models.user import User
from bot.keyboards import get_main_menu_keyboard
from typing import Optional
from core.enums import UserRole
from core.config import settings

router = Router()

# این handler اکنون هم به دستور /panel و هم به دکمه متنی "پنل کاربر" پاسخ می‌دهد
@router.message(Command("panel"))
@router.message(F.text == "👤 پنل کاربر")
async def show_my_profile(message: types.Message, user: Optional[User]):
    """
    اطلاعات پروفایل کاربر را نمایش می‌دهد.
    """
    if not user:
        return
        
    if not user.has_bot_access:
        await message.answer("شما دسترسی لازم برای استفاده از این بخش را ندارید.")
        return

    profile_text = (
        f"👤 **پروفایل شما**\n\n"
        f"🔸 **نام کاربری:** `{user.account_name}`\n"
        f"🔹 **نام تلگرام:** {user.full_name}\n"
        f"🔹 **آیدی تلگرام:** `{user.telegram_id}`\n"
        f"🔹 **سطح دسترسی:** {user.role.value}"
    )
    await message.answer(profile_text, parse_mode="Markdown")
    
    # اگر کاربر ادمین ارشد باشد، یک منوی اینلاین جداگانه برای اقدامات خاص
    # مانند "ساخت توکن دعوت" نمایش داده می‌شود تا کیبورد اصلی شلوغ نشود.
    if user.role == UserRole.SUPER_ADMIN:
        inline_keyboard = get_main_menu_keyboard(user.role)
        if inline_keyboard:
            await message.answer("اقدامات مدیریتی:", reply_markup=inline_keyboard)


@router.message(F.text == "📈 معامله")
async def handle_trade_button(message: types.Message, user: Optional[User]):
    """
    به کلیک روی دکمه "معامله" پاسخ می‌دهد.
    """
    if not user or user.role == UserRole.WATCH:
        # برای کاربران "تماشا" این دکمه نمایش داده نمی‌شود، اما این بررسی امنیتی باقی می‌ماند
        return
    await message.answer("🚧 بخش معاملات در حال توسعه است و به زودی فعال خواهد شد.")

@router.message(F.text == "⚙️ تنظیمات")
async def handle_settings_button(message: types.Message, user: Optional[User]):
    """
    به کلیک روی دکمه "تنظیمات" پاسخ می‌دهد.
    """
    if not user or user.role == UserRole.WATCH:
        return
    await message.answer("🚧 بخش تنظیمات در حال توسعه است و به زودی فعال خواهد شد.")
