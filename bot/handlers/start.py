from aiogram import Router, types
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from sqlalchemy import select
from core.db import AsyncSessionLocal
from models.invitation import Invitation
from models.user import User
from core.enums import UserRole

router = Router()

@router.message(CommandStart())
async def handle_start(message: types.Message, command: CommandObject):
    token = command.args
    if not token:
        await message.answer("سلام! برای استفاده از این ربات نیاز به لینک دعوت دارید.")
        return

    async with AsyncSessionLocal() as session:
        user_stmt = select(User).where(User.telegram_id == message.from_user.id)
        if (await session.execute(user_stmt)).scalar_one_or_none():
            await message.answer("شما قبلاً در سیستم ثبت‌نام کرده‌اید.")
            return

        inv_stmt = select(Invitation).where(Invitation.token == token)
        invitation = (await session.execute(inv_stmt)).scalar_one_or_none()

        if not invitation or invitation.is_used:
            await message.answer("لینک دعوت شما نامعتبر یا منقضی شده است.")
            return

        new_user = User(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            role=UserRole.WATCH
        )
        invitation.is_used = True
        session.add(new_user)
        await session.commit()
        await message.answer(f"✅ خوش آمدید، {new_user.full_name}! ثبت‌نام شما با موفقیت انجام شد.")