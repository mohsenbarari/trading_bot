# bot/handlers/panel.py (نسخه نهایی با تغییر کیبورد دائمی)

from aiogram import Router, types, F
from aiogram.filters import Command
from models.user import User
from bot.keyboards import (
    get_user_panel_keyboard, 
    get_admin_panel_keyboard, 
    get_persistent_menu_keyboard,
    get_create_token_inline_keyboard 
)
from typing import Optional
from core.enums import UserRole
from core.config import settings

router = Router()

# --- هندلر پنل کاربر: کیبورد دائمی را تغییر می‌دهد ---
@router.message(Command("panel"))
@router.message(F.text == "👤 پنل کاربر")
async def show_my_profile_and_change_keyboard(message: types.Message, user: Optional[User]):
    """
    اطلاعات پروفایل کاربر را نمایش می‌دهد و کیبورد دائمی را به منوی پنل کاربر تغییر می‌دهد.
    """
    if not user: return
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
    
    # ارسال پیام پروفایل با کیبورد جدید پنل کاربر
    await message.answer(
        profile_text, 
        parse_mode="Markdown",
        reply_markup=get_user_panel_keyboard() 
    )

# --- هندلر پنل مدیریت: کیبورد دائمی را تغییر می‌دهد ---
@router.message(F.text == "🔐 پنل مدیریت")
async def show_admin_panel_and_change_keyboard(message: types.Message, user: Optional[User]):
    """
    وارد بخش پنل مدیریت (برای مدیر ارشد) شده و کیبورد دائمی را تغییر می‌دهد.
    """
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    
    # ارسال پیام ورود به پنل مدیریت با کیبورد جدید پنل مدیریت
    await message.answer(
        "وارد پنل مدیریت شدید.",
        reply_markup=get_admin_panel_keyboard()
    )

# --- هندلر دکمه معامله (بدون تغییر) ---
@router.message(F.text == "📈 معامله")
async def handle_trade_button(message: types.Message, user: Optional[User]):
    """
    به کلیک روی دکمه "معامله" پاسخ می‌دهد.
    """
    if not user or user.role == UserRole.WATCH:
        return
    await message.answer("🚧 بخش معاملات در حال توسعه است و به زودی فعال خواهد شد.")

# --- هندلر دکمه تنظیمات کاربری (از کیبورد پنل کاربر) ---
@router.message(F.text == "⚙️ تنظیمات کاربری")
async def handle_user_settings_button(message: types.Message, user: Optional[User]):
    """
    به دکمه تنظیمات در زیرمنوی پنل کاربر پاسخ می‌دهد.
    """
    if not user: return
    # اینجا می‌توانید دکمه‌های شیشه‌ای برای تنظیمات خاص کاربر نشان دهید یا مستقیماً کاری انجام دهید
    await message.answer("🚧 بخش تنظیمات کاربری (بات) در حال توسعه است.")
    # کیبورد دائمی همچنان get_user_panel_keyboard باقی می‌ماند

# --- هندلر دکمه تنظیمات مدیریت (از کیبورد پنل مدیریت) ---
@router.message(F.text == "⚙️ تنظیمات مدیریت")
async def handle_admin_settings_button(message: types.Message, user: Optional[User]):
    """
    به دکمه تنظیمات در زیرمنوی پنل مدیریت پاسخ می‌دهد.
    """
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    # اینجا می‌توانید دکمه‌های شیشه‌ای برای تنظیمات خاص مدیریت نشان دهید
    await message.answer("🚧 بخش تنظیمات مدیریت (بات) در حال توسعه است.")
     # کیبورد دائمی همچنان get_admin_panel_keyboard باقی می‌ماند


# --- هندلر دکمه بازگشت: کیبورد را به منوی اصلی برمی‌گرداند ---
@router.message(F.text == "🔙 بازگشت")
async def handle_back_to_main_menu(message: types.Message, user: Optional[User]):
    """
    کاربر را به منوی اصلی برمی‌گرداند و کیبورد اصلی را نمایش می‌دهد.
    """
    if not user: return
    
    # کیبورد اصلی را دوباره ارسال کنید
    await message.answer(
        "به منوی اصلی بازگشتید.",
        reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
    )
