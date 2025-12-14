# trading_bot/bot/handlers/start.py
"""هندلرهای شروع و ثبت‌نام"""

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from typing import Optional
import logging

from core.db import AsyncSessionLocal
from core.config import settings
from models.invitation import Invitation
from models.user import User
from bot.states import Registration
from bot.keyboards import get_share_contact_keyboard, get_persistent_menu_keyboard
from bot.message_manager import (
    set_anchor, 
    delete_previous_anchor,
    DeleteDelay
)

logger = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart(deep_link=True))
async def handle_start_with_token(message: types.Message, command: CommandObject, state: FSMContext, user: Optional[User]):
    
    token = command.args
    
    # --- بررسی لینک پروفایل عمومی ---
    if token and token.startswith("profile_"):
        try:
            await message.delete()
        except Exception:
            pass
        
        try:
            target_user_id = int(token.replace("profile_", ""))
            async with AsyncSessionLocal() as session:
                stmt = select(User).where(User.id == target_user_id)
                target_user = (await session.execute(stmt)).scalar_one_or_none()
                
                if target_user:
                    profile_text = (
                        f"👤 پروفایل عمومی\n\n"
                        f"🔸 نام کاربری: {target_user.account_name}\n"
                        f"📞 شماره تماس: {target_user.mobile_number}\n"
                        f"📍 آدرس: {target_user.address or 'ثبت نشده'}"
                    )
                    await delete_previous_anchor(message.bot, message.chat.id, delay=0)
                    
                    # دکمه تاریخچه معاملات (فقط برای کاربران لاگین شده)
                    if user:
                        profile_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="📊 تاریخچه معاملات", callback_data=f"trade_history_{target_user_id}")]
                        ])
                    else:
                        profile_keyboard = None
                    
                    anchor_msg = await message.answer(
                        profile_text,
                        reply_markup=profile_keyboard
                    )
                    if user:
                        set_anchor(message.chat.id, anchor_msg.message_id)
                else:
                    await message.answer("❌ کاربر یافت نشد.")
        except (ValueError, Exception):
            await message.answer("❌ لینک نامعتبر است.")
        return
    
    # --- حذف پیام و لنگر برای سایر حالات ---
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    # --- کاربر قبلاً ثبت‌نام کرده ---
    if user:
        anchor_msg = await message.answer(
            "شما قبلاً ثبت‌نام کرده‌اید. برای دسترسی به پنل از دکمه زیر استفاده کنید.",
            reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
        )
        set_anchor(message.chat.id, anchor_msg.message_id)
        return
        
    # --- لینک دعوت ---
    async with AsyncSessionLocal() as session:
        inv_stmt = select(Invitation).where(Invitation.token == token)
        invitation = (await session.execute(inv_stmt)).scalar_one_or_none()
        if not invitation or invitation.is_used:
            bot_response = await message.answer("لینک دعوت شما نامعتبر یا منقضی شده است.", reply_markup=types.ReplyKeyboardRemove())
            return
            
        await state.update_data(token=token, mobile_number=invitation.mobile_number)
        await state.set_state(Registration.awaiting_contact)
        
        anchor_msg = await message.answer(
            "✅ لینک دعوت شما معتبر است. لطفاً برای تکمیل ثبت‌نام، شماره تماس خود را به اشتراک بگذارید.",
            reply_markup=get_share_contact_keyboard()
        )
        set_anchor(message.chat.id, anchor_msg.message_id)


@router.message(CommandStart(deep_link=False))
async def handle_start_without_token(message: types.Message, state: FSMContext, user: Optional[User]):
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    if user:
        logger.warning(f"DEBUG: Building keyboard with URL: '{settings.frontend_url}'")
        
        anchor_msg = await message.answer(
            f"سلام {user.full_name}! به پنل کاربری خود خوش آمدید. برای دسترسی به امکانات از دکمه زیر استفاده کنید.",
            reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
        )
        set_anchor(message.chat.id, anchor_msg.message_id)
    else:
        pass  # اجازه می‌دهیم هندلر default اجرا شود


@router.message(Registration.awaiting_contact, F.contact)
async def handle_contact(message: types.Message, state: FSMContext):
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    shared_contact = message.contact
    user_phone_number = shared_contact.phone_number
    
    if not user_phone_number.startswith('+'):
        user_phone_number = '+' + user_phone_number

    state_data = await state.get_data()
    expected_phone_number = state_data.get("mobile_number")
    token = state_data.get("token")

    if not user_phone_number.endswith(expected_phone_number[-10:]) or shared_contact.user_id != message.from_user.id:
        await state.clear()
        bot_response = await message.answer(
            "❌ شماره تماس شما با شماره ثبت شده برای این لینک دعوت مطابقت ندارد. ثبت‌نام انجام نشد.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return

    # ذخیره اطلاعات و رفتن به مرحله آدرس
    await state.update_data(phone_verified=True)
    await state.set_state(Registration.awaiting_address)
    
    anchor_msg = await message.answer(
        "✅ شماره تماس تایید شد!\n\n"
        "📍 لطفاً آدرس خود را وارد کنید:\n"
        "(شهر، منطقه، خیابان اصلی)",
        reply_markup=types.ReplyKeyboardRemove()
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


@router.message(Registration.awaiting_address)
async def handle_address(message: types.Message, state: FSMContext):
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    address = message.text.strip()
    
    if len(address) < 10:
        bot_response = await message.answer(
            "❌ آدرس وارد شده کوتاه است. لطفاً آدرس کامل‌تری وارد کنید."
        )
        return
    
    state_data = await state.get_data()
    token = state_data.get("token")
    
    await state.clear()

    async with AsyncSessionLocal() as session:
        inv_stmt = select(Invitation).where(Invitation.token == token)
        invitation: Optional[Invitation] = (await session.execute(inv_stmt)).scalar_one_or_none()

        if not invitation or invitation.is_used:
            bot_response = await message.answer("خطا! لینک دعوت شما دیگر معتبر نیست.", reply_markup=types.ReplyKeyboardRemove())
            return

        new_user = User(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            account_name=invitation.account_name,
            mobile_number=invitation.mobile_number,
            address=address,
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
        set_anchor(message.chat.id, anchor_msg.message_id)