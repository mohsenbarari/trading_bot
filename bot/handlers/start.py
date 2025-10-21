from aiogram import Router, types, F
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from typing import Optional

from core.db import AsyncSessionLocal
from core.config import settings  # <-- اصلاح: settings ایمپورت شد
from models.invitation import Invitation
from models.user import User
from bot.states import Registration
from bot.keyboards import get_share_contact_keyboard, get_persistent_menu_keyboard

router = Router()

@router.message(CommandStart(deep_link=True))
async def handle_start_with_token(message: types.Message, command: CommandObject, state: FSMContext, user: Optional[User]):
    if user:
        await message.answer(
            "شما قبلاً ثبت‌نام کرده‌اید. برای دسترسی به پنل از دکمه زیر استفاده کنید.",
            # آدرس Mini App به تابع پاس داده می‌شود
            reply_markup=get_persistent_menu_keyboard(settings.frontend_url)
        )
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
        await message.answer(
            "✅ لینک دعوت شما معتبر است. لطفاً برای تکمیل ثبت‌نام، شماره تماس خود را به اشتراک بگذارید.",
            reply_markup=get_share_contact_keyboard()
        )

@router.message(CommandStart(deep_link=False))
async def handle_start_without_token(message: types.Message, user: Optional[User]):
    if user:
        await message.answer(
            f"سلام {user.full_name}! به پنل کاربری خود خوش آمدید. برای دسترسی به امکانات از دکمه زیر استفاده کنید.",
            # آدرس Mini App به تابع پاس داده می‌شود
            reply_markup=get_persistent_menu_keyboard(settings.frontend_url)
        )
    else:
        # اگر کاربر وجود ندارد، هیچ پاسخی نمی‌دهیم تا handler پیش‌فرض آن را مدیریت کند
        pass

@router.message(Registration.awaiting_contact, F.contact)
async def handle_contact(message: types.Message, state: FSMContext):
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
        return

    async with AsyncSessionLocal() as session:
        inv_stmt = select(Invitation).where(Invitation.token == token)
        invitation: Optional[Invitation] = (await session.execute(inv_stmt)).scalar_one_or_none()

        if not invitation or invitation.is_used:
            await message.answer("خطا! لینک دعوت شما دیگر معتبر نیست.", reply_markup=types.ReplyKeyboardRemove())
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
        await message.answer(
            f"✅ خوش آمدید، {message.from_user.full_name}! ثبت‌نام شما با موفقیت انجام شد.\n"
            "برای دسترسی به امکانات، از دکمه زیر استفاده کنید.",
            # آدرس Mini App به تابع پاس داده می‌شود
            reply_markup=get_persistent_menu_keyboard(settings.frontend_url)
        )
