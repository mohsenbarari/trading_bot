# bot/handlers/panel.py
from aiogram import Router, types, F
from aiogram.filters import Command
from models.user import User
from bot.keyboards import get_main_menu_keyboard, get_mini_app_keyboard # کیبورد جدید را وارد می‌کنیم
from typing import Optional

router = Router()

@router.message(Command("panel"))
async def show_panel(message: types.Message, user: Optional[User]):
    """
    بر اساس دسترسی کاربر، پنل مناسب را نمایش می‌دهد.
    """
    if not user:
        # این حالت توسط default_handler مدیریت می‌شود
        return

    if user.has_bot_access:
        # اگر کاربر دسترسی به بات دارد، منوی کامل را نشان بده
        keyboard = get_main_menu_keyboard(user.role)
        await message.answer(f"سلام {user.full_name}!\nبه پنل کاربری خود خوش آمدید.", reply_markup=keyboard)
    else:
        # اگر کاربر دسترسی به بات ندارد، فقط دکمه ورود به Mini App را نشان بده
        keyboard = get_mini_app_keyboard()
        await message.answer(
            f"سلام {user.full_name}!\n"
            "برای دسترسی به امکانات، لطفاً از طریق پنل امن تحت وب وارد شوید.",
            reply_markup=keyboard
        )

@router.callback_query(F.data == "my_profile")
async def show_my_profile(callback_query: types.CallbackQuery, user: Optional[User]):
    if not user:
        await callback_query.answer("خطا: اطلاعات شما یافت نشد.", show_alert=True)
        return
        
    if not user.has_bot_access:
        await callback_query.answer("شما دسترسی لازم برای استفاده از این بخش را ندارید.", show_alert=True)
        return

    await callback_query.answer()
    profile_text = (
        f"👤 **پروفایل شما**\n\n"
        f"🔸 **نام کاربری:** `{user.account_name}`\n"  # <-- فیلد جدید اینجا نمایش داده می‌شود
        f"🔹 **نام تلگرام:** {user.full_name}\n"
        f"🔹 **آیدی تلگرام:** `{user.telegram_id}`\n"
        f"🔹 **سطح دسترسی:** {user.role.value}"
    )
    await callback_query.message.answer(profile_text, parse_mode="Markdown")