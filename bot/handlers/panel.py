# bot/handlers/panel.py (کامل و اصلاح شده نهایی)

from aiogram import Router, types, F, Bot  # <--- ۱. Bot به اینجا اضافه شد
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
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
import asyncio

router = Router()

# --- توابع کمکی ---
async def delete_message_after_delay(message: types.Message, delay: int = 30):
    """پیام خود پیام را پس از چند ثانیه حذف می‌کند."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass

async def delete_message_by_id_after_delay(bot: Bot, chat_id: int, message_id: int, delay: int = 30):
    """پیام با ID مشخص را پس از چند ثانیه حذف می‌کند."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

async def handle_rolling_anchor(message: types.Message, state: FSMContext):
    """پیام کاربر و لنگر قبلی را برای حذف زمان‌بندی می‌کند."""
    asyncio.create_task(delete_message_after_delay(message))
    
    data = await state.get_data()
    last_anchor_id = data.get("anchor_message_id")
    if last_anchor_id:
        asyncio.create_task(delete_message_by_id_after_delay(message.bot, message.chat.id, last_anchor_id))
    
    await state.update_data(anchor_message_id=None)
# ---------------------

# --- هندلر پنل کاربر ---
@router.message(Command("panel"))
@router.message(F.text == "👤 پنل کاربر")
async def show_my_profile_and_change_keyboard(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    if not user.has_bot_access:
        await message.answer("شما دسترسی لازم برای استفاده از این بخش را ندارید.")
        return

    await handle_rolling_anchor(message, state)
    
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
    await state.update_data(anchor_message_id=anchor_msg.message_id)

# --- هندلر پنل مدیریت ---
@router.message(F.text == "🔐 پنل مدیریت")
async def show_admin_panel_and_change_keyboard(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    
    await handle_rolling_anchor(message, state)
    
    anchor_msg = await message.answer(
        "وارد پنل مدیریت شدید.",
        reply_markup=get_admin_panel_keyboard()
    )
    await state.update_data(anchor_message_id=anchor_msg.message_id)

# --- هندلر دکمه معامله (پیام موقت) ---
@router.message(F.text == "📈 معامله")
async def handle_trade_button(message: types.Message, user: Optional[User]):
    if not user or user.role == UserRole.WATCH:
        return
    
    bot_response = await message.answer("🚧 بخش معاملات در حال توسعه است و به زودی فعال خواهد شد.")
    asyncio.create_task(delete_message_after_delay(message, 30))
    asyncio.create_task(delete_message_after_delay(bot_response, 30))

# --- هندلر دکمه تنظیمات کاربری (پیام موقت) ---
@router.message(F.text == "⚙️ تنظیمات کاربری")
async def handle_user_settings_button(message: types.Message, user: Optional[User]):
    if not user: return
    
    bot_response = await message.answer("🚧 بخش تنظیمات کاربری (بات) در حال توسعه است.")
    asyncio.create_task(delete_message_after_delay(message, 30))
    asyncio.create_task(delete_message_after_delay(bot_response, 30))

# --- هندلر دکمه تنظیمات مدیریت (پیام موقت) ---
@router.message(F.text == "⚙️ تنظیمات مدیریت")
async def handle_admin_settings_button(message: types.Message, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return

    bot_response = await message.answer("🚧 بخش تنظیمات مدیریت (بات) در حال توسعه است.")
    asyncio.create_task(delete_message_after_delay(message, 30))
    asyncio.create_task(delete_message_after_delay(bot_response, 30))


# --- هندلر دکمه بازگشت ---
@router.message(F.text == "🔙 بازگشت")
async def handle_back_to_main_menu(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    
    await handle_rolling_anchor(message, state)
    
    anchor_msg = await message.answer(
        "به منوی اصلی بازگشتید.",
        reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
    )
    await state.update_data(anchor_message_id=anchor_msg.message_id)