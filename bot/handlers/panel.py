# bot/handlers/panel.py
"""هندلرهای پنل کاربر و مدیریت"""

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from models.user import User
from bot.keyboards import (
    get_user_panel_keyboard, 
    get_admin_panel_keyboard, 
    get_persistent_menu_keyboard,
)
from bot.message_manager import (
    set_anchor, 
    delete_previous_anchor,
    DeleteDelay
)
from typing import Optional
from core.enums import UserRole
from core.config import settings

router = Router()


# --- هندلر پنل کاربر ---
@router.message(Command("panel"))
@router.message(F.text == "👤 پنل کاربر")
async def show_my_profile_and_change_keyboard(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    if not user.has_bot_access:
        await message.answer("شما دسترسی لازم برای استفاده از این بخش را ندارید.")
        return

    # حذف پیام کاربر و لنگر قبلی
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    profile_text = (
        f"👤 **پروفایل شما**\n\n"
        f"🔸 **نام کاربری:** `{user.account_name}`\n"
        f"🔹 **نام تلگرام:** {user.full_name}\n"
        f"🔹 **آیدی تلگرام:** `{user.telegram_id}`\n"
        f"🔹 **سطح دسترسی:** {user.role.value}"
    )
    
    anchor_msg = await message.answer(
        profile_text, 
        parse_mode="Markdown",
        reply_markup=get_user_panel_keyboard() 
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


# --- هندلر پنل مدیریت ---
@router.message(F.text == "🔐 پنل مدیریت")
async def show_admin_panel_and_change_keyboard(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    anchor_msg = await message.answer(
        "وارد پنل مدیریت شدید.",
        reply_markup=get_admin_panel_keyboard()
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


# --- هندلر دکمه تنظیمات کاربری ---
@router.message(F.text == "⚙️ تنظیمات کاربری")
async def handle_user_settings_button(message: types.Message, user: Optional[User]):
    if not user: return
    
    bot_response = await message.answer("🚧 بخش تنظیمات کاربری (بات) در حال توسعه است.")


# --- هندلر دکمه تنظیمات مدیریت ---
@router.message(F.text == "⚙️ تنظیمات مدیریت")
async def handle_admin_settings_button(message: types.Message, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return

    bot_response = await message.answer("🚧 بخش تنظیمات مدیریت (بات) در حال توسعه است.")


# --- هندلر دکمه بازگشت ---
@router.message(F.text == "🔙 بازگشت")
async def handle_back_to_main_menu(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    anchor_msg = await message.answer(
        "به منوی اصلی بازگشتید.",
        reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
    )
    set_anchor(message.chat.id, anchor_msg.message_id)