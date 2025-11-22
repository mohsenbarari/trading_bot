# trading_bot/run_bot.py

import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from core.config import settings
from core.db import AsyncSessionLocal
from core.enums import UserRole, NotificationLevel, NotificationCategory # <-- ایمپورت جدید
from models.invitation import Invitation
from models.user import User
from bot.middlewares.auth import AuthMiddleware
from bot.handlers import start, panel, default, admin, admin_commodities, admin_users
from core.utils import create_user_notification 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def monitor_expired_invitations(bot: Bot):
    """تسک پس‌زمینه برای بررسی لینک‌های منقضی شده."""
    logger.info("--> Expiry Monitor Started...")
    while True:
        try:
            async with AsyncSessionLocal() as session:
                stmt = select(Invitation).where(
                    Invitation.expires_at < datetime.utcnow(),
                    Invitation.is_used == False
                )
                result = await session.execute(stmt)
                expired_invites = result.scalars().all()

                if expired_invites:
                    admin_stmt = select(User).where(User.role == UserRole.SUPER_ADMIN)
                    admin_result = await session.execute(admin_stmt)
                    admins = admin_result.scalars().all()

                    for invite in expired_invites:
                        msg_text = (
                            f"⚠️ **گزارش انقضای دعوت‌نامه**\n\n"
                            f"لینک دعوت مربوط به:\n"
                            f"👤 نام کاربری: `{invite.account_name}`\n"
                            f"📱 موبایل: `{invite.mobile_number}`\n"
                            f"بدون تکمیل ثبت‌نام منقضی شد و از سیستم حذف گردید."
                        )

                        for admin_user in admins:
                            # الف: ارسال پیام در تلگرام
                            try:
                                await bot.send_message(chat_id=admin_user.telegram_id, text=msg_text)
                            except Exception as e:
                                logger.warning(f"Failed to send msg to admin {admin_user.id}: {e}")

                            # ب: ذخیره اعلان با سطح WARNING و دسته‌بندی SYSTEM
                            await create_user_notification(
                                db=session, 
                                user_id=admin_user.id, 
                                message=msg_text,
                                level=NotificationLevel.WARNING,      # 👈 تعیین سطح
                                category=NotificationCategory.SYSTEM  # 👈 تعیین دسته
                            )
                        
                        await session.delete(invite)
                        await session.commit()

        except Exception as e:
            logger.error(f"Error in monitor task: {e}")

        await asyncio.sleep(5)

async def main():
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="Markdown"))
    dp = Dispatcher(storage=MemoryStorage())

    auth_middleware = AuthMiddleware(session_pool=AsyncSessionLocal)
    dp.message.middleware(auth_middleware)
    dp.callback_query.middleware(auth_middleware)

    dp.include_router(start.router)
    dp.include_router(panel.router)
    dp.include_router(admin.router)
    dp.include_router(admin_commodities.router)
    dp.include_router(admin_users.router)
    dp.include_router(default.router) 

    asyncio.create_task(monitor_expired_invitations(bot))

    logger.info("--> Starting Bot polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("--> Bot polling stopped.")