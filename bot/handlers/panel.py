from aiogram import Router, types, F
from aiogram.filters import Command
from models.user import User
from bot.keyboards import get_main_menu_keyboard, get_mini_app_keyboard
from typing import Optional
from core.enums import UserRole  # <-- اصلاح ۱: UserRole ایمپورت شد
from core.config import settings # <-- اصلاح ۲: settings ایمپورت شد

router = Router()

@router.message(Command("panel"))
@router.message(F.text == "پنل کاربری")
async def show_panel(message: types.Message, user: Optional[User]):
    if not user:
        return
    
    # آدرس Mini App به صورت پویا از تنظیمات خوانده می‌شود
    mini_app_url = settings.frontend_url 
    
    if user.has_bot_access:
        keyboard = get_main_menu_keyboard(user.role)
        await message.answer(f"سلام {user.full_name}!\nبه پنل کاربری خود خوش آمدید.", reply_markup=keyboard)
    else:
        # آدرس به تابع پاس داده می‌شود
        keyboard = get_mini_app_keyboard(mini_app_url)
        await message.answer(
            f"سلام {user.full_name}!\nبرای دسترسی به امکانات، لطفاً از طریق پنل امن وارد شوید.",
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
        f"🔸 **نام کاربری:** `{user.account_name}`\n"
        f"🔹 **نام تلگرام:** {user.full_name}\n"
        f"🔹 **آیدی تلگرام:** `{user.telegram_id}`\n"
        f"🔹 **سطح دسترسی:** {user.role.value}"
    )
    await callback_query.message.answer(profile_text, parse_mode="Markdown")

@router.callback_query(F.data == "start_trade")
async def handle_trade_button(callback_query: types.CallbackQuery, user: Optional[User]):
    if not user or user.role == UserRole.WATCH:
        await callback_query.answer("دسترسی غیرمجاز.", show_alert=True)
        return
    await callback_query.answer()
    await callback_query.message.answer("🚧 بخش معاملات در حال توسعه است و به زودی فعال خواهد شد.")

@router.callback_query(F.data == "settings")
async def handle_settings_button(callback_query: types.CallbackQuery, user: Optional[User]):
    if not user or user.role == UserRole.WATCH:
        await callback_query.answer("دسترسی غیرمجاز.", show_alert=True)
        return
    await callback_query.answer()
    await callback_query.message.answer("🚧 بخش تنظیمات در حال توسعه است و به زودی فعال خواهد شد.")
