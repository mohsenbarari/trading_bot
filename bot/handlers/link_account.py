import asyncio
import logging
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from core.db import get_db

router = Router()
logger = logging.getLogger(__name__)

class LinkState(StatesGroup):
    waiting_for_contact = State()

@router.message(Command("link"))
async def cmd_link(message: types.Message, state: FSMContext):
    """
    Start account linking process.
    """
    await message.answer(
        "🔗 برای اتصال حساب کاربری وب به تلگرام، لطفاً شماره موبایل خود را ارسال کنید.\n"
        "روی دکمه زیر کلیک کنید:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="📱 ارسال شماره همراه", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    await state.set_state(LinkState.waiting_for_contact)

@router.message(LinkState.waiting_for_contact, F.contact)
async def handle_contact(message: types.Message, state: FSMContext):
    contact = message.contact
    
    # Security check: verify contact belongs to sender
    if contact.user_id != message.from_user.id:
        await message.answer("❌ لطفاً شماره خودتان را ارسال کنید.")
        return

    # Normalize phone number (remove +98, add 0)
    phone = contact.phone_number
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("98"):
        phone = "0" + phone[2:]
        
    logger.info(f"Linking attempt for phone: {phone} by tg_id: {message.from_user.id}")
    
    async for db in get_db():
        # Find user by phone
        stmt = select(User).where(User.mobile_number == phone)
        user = (await db.execute(stmt)).scalar_one_or_none()
        
        if not user:
            await message.answer("❌ کاربری با این شماره یافت نشد. لطفاً ابتدا در وب ثبت‌نام کنید یا از لینک دعوت استفاده کنید.", reply_markup=types.ReplyKeyboardRemove())
            await state.clear()
            return
            
        if user.telegram_id and user.telegram_id != message.from_user.id:
            await message.answer("❌ این شماره قبلاً به یک اکانت تلگرام دیگر متصل شده است.", reply_markup=types.ReplyKeyboardRemove())
            await state.clear()
            return
            
        if user.telegram_id == message.from_user.id:
            await message.answer("✅ حساب شما قبلاً متصل شده است.", reply_markup=types.ReplyKeyboardRemove())
            await state.clear()
            return
            
        # Update user
        user.telegram_id = message.from_user.id
        user.username = message.from_user.username
        # Also ensure full_name is set if it was temporary
        if user.full_name == user.account_name and message.from_user.full_name:
             user.full_name = message.from_user.full_name
             
        user.has_bot_access = True
        
        try:
            await db.commit()
            await message.answer(
                f"✅ حساب کاربری **{user.account_name}** با موفقیت به تلگرام متصل شد.\n"
                "اکنون می‌توانید از تمام امکانات ربات استفاده کنید.",
                reply_markup=types.ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
        except Exception as e:
            await db.rollback()
            logger.error(f"Link error: {e}")
            await message.answer("❌ خطا در اتصال حساب.", reply_markup=types.ReplyKeyboardRemove())
            
        await state.clear()
        return
