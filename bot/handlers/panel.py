from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select
from core.db import AsyncSessionLocal
from models.user import User
from bot.keyboards import get_main_menu_keyboard

router = Router()

@router.message(Command("panel"))
async def show_panel(message: types.Message):
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        user = (await session.execute(stmt)).scalar_one_or_none()

        if not user:
            await message.answer("خطا: شما هنوز ثبت‌نام نکرده‌اید. لطفاً از لینک دعوت خود استفاده کنید.")
            return

        keyboard = get_main_menu_keyboard(user.role)
        await message.answer(f"سلام {user.full_name}!\nبه پنل کاربری خود خوش آمدید.", reply_markup=keyboard)