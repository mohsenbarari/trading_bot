# trading_bot/bot/handlers/start.py (کامل و اصلاح شده)

from aiogram import Router, types, F, Bot  # <--- ۱. Bot به اینجا اضافه شد
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from typing import Optional
import logging
import asyncio
from core.db import AsyncSessionLocal
from core.config import settings
from models.invitation import Invitation
from models.user import User
from bot.states import Registration
from bot.keyboards import get_share_contact_keyboard, get_persistent_menu_keyboard

logger = logging.getLogger(__name__)

router = Router()

# --- توابع کمکی مدیریت پیام ---
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
# ---------------------------------

@router.message(CommandStart(deep_link=True))
async def handle_start_with_token(message: types.Message, command: CommandObject, state: FSMContext, user: Optional[User]):
    
    await handle_rolling_anchor(message, state)
    
    if user:
        anchor_msg = await message.answer(
            "شما قبلاً ثبت‌نام کرده‌اید. برای دسترسی به پنل از دکمه زیر استفاده کنید.",
            reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
        )
        await state.update_data(anchor_message_id=anchor_msg.message_id)
        return
        
    token = command.args
    async with AsyncSessionLocal() as session:
        inv_stmt = select(Invitation).where(Invitation.token == token)
        invitation = (await session.execute(inv_stmt)).scalar_one_or_none()
        if not invitation or invitation.is_used:
            await message.answer("لینک دعوت شما نامعتبر یا منقضی شده است.", reply_markup=types.ReplyKeyboardRemove())
            return
            
        await state.update_data(token=token, mobile_number=invitation.mobile_number)
        await state.set_state(Registration.awaiting_contact)
        
        anchor_msg = await message.answer(
            "✅ لینک دعوت شما معتبر است. لطفاً برای تکمیل ثبت‌نام، شماره تماس خود را به اشتراک بگذارید.",
            reply_markup=get_share_contact_keyboard()
        )
        await state.update_data(anchor_message_id=anchor_msg.message_id)

@router.message(CommandStart(deep_link=False))
async def handle_start_without_token(message: types.Message, state: FSMContext, user: Optional[User]):
    
    await handle_rolling_anchor(message, state)
    
    if user:
        logger.warning(f"DEBUG: Building keyboard with URL: '{settings.frontend_url}'")
        
        anchor_msg = await message.answer(
            f"سلام {user.full_name}! به پنل کاربری خود خوش آمدید. برای دسترسی به امکانات از دکمه زیر استفاده کنید.",
            reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
        )
        await state.update_data(anchor_message_id=anchor_msg.message_id)
    else:
        pass # اجازه می‌دهیم هندلر default اجرا شود

@router.message(Registration.awaiting_contact, F.contact)
async def handle_contact(message: types.Message, state: FSMContext):
    
    await handle_rolling_anchor(message, state)
    
    shared_contact = message.contact
    user_phone_number = shared_contact.phone_number
    
    if not user_phone_number.startswith('+'):
        user_phone_number = '+' + user_phone_number

    state_data = await state.get_data()
    expected_phone_number = state_data.get("mobile_number")
    token = state_data.get("token")
    await state.clear() 

    if not user_phone_number.endswith(expected_phone_number[-10:]) or shared_contact.user_id != message.from_user.id:
        await message.answer(
            "❌ شماره تماس شما با شماره ثبت شده برای این لینک دعوت مطابقت ندارد. ثبت‌نام انجام نشد.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.update_data(anchor_message_id=None)
        return

    async with AsyncSessionLocal() as session:
        inv_stmt = select(Invitation).where(Invitation.token == token)
        invitation: Optional[Invitation] = (await session.execute(inv_stmt)).scalar_one_or_none()

        if not invitation or invitation.is_used:
            await message.answer("خطا! لینک دعوت شما دیگر معتبر نیست.", reply_markup=types.ReplyKeyboardRemove())
            await state.update_data(anchor_message_id=None)
            return

        new_user = User(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            account_name=invitation.account_name,
            mobile_number=invitation.mobile_number,
            role=invitation.role,
            has_bot_access=True
        )

        invitation.is_used = True
        session.add(new_user)
        await session.commit()
        
        anchor_msg = await message.answer(
            f"✅ خوش آمدید، {message.from_user.full_name}! ثبت‌نام شما با موفقیت انجام شد.\n"
            "برای دسترسی به امکانات، از دکمه‌های زیر استفاده کنید.",
            reply_markup=get_persistent_menu_keyboard(invitation.role, settings.frontend_url)
        )
        await state.update_data(anchor_message_id=anchor_msg.message_id)