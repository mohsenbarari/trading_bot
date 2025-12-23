# trading_bot/run_bot.py

import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select, delete
from models.notification import Notification, NotificationLevel, NotificationCategory
from core.config import settings
from core.db import AsyncSessionLocal
from core.enums import UserRole, NotificationLevel, NotificationCategory # <-- ایمپورت جدید
from models.invitation import Invitation
from models.user import User
from models.offer import Offer, OfferStatus
from bot.middlewares.auth import AuthMiddleware
from bot.handlers import start, panel, default, admin, admin_commodities, admin_users, trade, trade_history
from core.utils import create_user_notification, send_telegram_notification

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def monitor_expired_restrictions(bot: Bot):
    """تسک پس‌زمینه برای بررسی انقضای مسدودیت‌ها و محدودیت‌ها."""
    logger.info("--> Restriction/Limitation Expiry Monitor Started...")
    while True:
        try:
            async with AsyncSessionLocal() as session:
                now = datetime.utcnow()
                
                # چک مسدودیت‌های منقضی شده
                stmt = select(User).where(
                    User.trading_restricted_until != None,
                    User.trading_restricted_until <= now,
                    User.trading_restricted_until > now - timedelta(minutes=2)  # فقط تازه منقضی شده‌ها
                )
                result = await session.execute(stmt)
                expired_blocks = result.scalars().all()
                
                for user in expired_blocks:
                    msg = (
                        "ℹ️ *منقضی شدن مسدودیت*\n\n"
                        "مسدودیت حساب شما به پایان رسید."
                    )
                    await create_user_notification(
                        session, user.id, msg,
                        level=NotificationLevel.INFO,
                        category=NotificationCategory.SYSTEM
                    )
                    await send_telegram_notification(user.telegram_id, msg)
                    
                    # پاک کردن مسدودیت
                    user.trading_restricted_until = None
                    await session.commit()
                    logger.info(f"Block expired for user {user.id}")
                
                # چک محدودیت‌های منقضی شده
                stmt2 = select(User).where(
                    User.limitations_expire_at != None,
                    User.limitations_expire_at <= now,
                    User.limitations_expire_at > now - timedelta(minutes=2)
                )
                result2 = await session.execute(stmt2)
                expired_limits = result2.scalars().all()
                
                for user in expired_limits:
                    msg = (
                        "ℹ️ *منقضی شدن محدودیت*\n\n"
                        "محدودیت‌های حساب شما به پایان رسید."
                    )
                    await create_user_notification(
                        session, user.id, msg,
                        level=NotificationLevel.INFO,
                        category=NotificationCategory.SYSTEM
                    )
                    await send_telegram_notification(user.telegram_id, msg)
                    
                    # پاک کردن محدودیت‌ها و ریست شمارنده‌ها
                    user.max_daily_trades = None
                    user.max_active_commodities = None
                    user.max_daily_requests = None
                    user.limitations_expire_at = None
                    user.trades_count = 0
                    user.commodities_traded_count = 0
                    user.channel_messages_count = 0
                    await session.commit()
                    logger.info(f"Limitations expired for user {user.id}")
                    
        except Exception as e:
            logger.error(f"Error in restriction monitor: {e}")
        
        await asyncio.sleep(60)  # هر ۱ دقیقه چک کن

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

        await asyncio.sleep(60)

async def cleanup_old_notifications(retention_days: int = 90):
    """
    تسک پس‌زمینه برای حذف نوتیفیکیشن‌های قدیمی‌تر از ۹۰ روز.
    این تسک هر ۲۴ ساعت یک‌بار اجرا می‌شود.
    """
    logger.info(f"--> Notification Cleanup Task Started (Retention: {retention_days} days)...")
    
    while True:
        try:
            # محاسبه تاریخ آستانه (مثلاً امروز منهای ۹۰ روز)
            cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
            
            async with AsyncSessionLocal() as session:
                # دستور حذف
                stmt = delete(Notification).where(Notification.created_at < cutoff_date)
                result = await session.execute(stmt)
                deleted_count = result.rowcount
                
                await session.commit()
                
                if deleted_count > 0:
                    logger.info(f"🧹 Cleanup: Deleted {deleted_count} old notifications created before {cutoff_date.date()}.")
                
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")

        # خوابیدن برای ۲۴ ساعت (۸۶۴۰۰ ثانیه)
        # چون این عملیات سنگین نیست ولی نیازی هم نیست تند تند اجرا شود
        await asyncio.sleep(86400)


async def monitor_expired_offers(bot: Bot):
    """تسک پس‌زمینه برای منقضی کردن لفظ‌های قدیمی بر اساس تنظیمات."""
    logger.info("--\> Offer Expiry Monitor Started...")
    
    while True:
        try:
            from core.trading_settings import get_trading_settings
            ts = get_trading_settings()
            
            # زمان منقضی شدن (دقیقه)
            expiry_minutes = ts.offer_expiry_minutes
            cutoff_time = datetime.utcnow() - timedelta(minutes=expiry_minutes)
            
            async with AsyncSessionLocal() as session:
                # پیدا کردن لفظ‌های منقضی شده
                stmt = select(Offer).where(
                    Offer.status == OfferStatus.ACTIVE,
                    Offer.created_at < cutoff_time
                )
                result = await session.execute(stmt)
                expired_offers = result.scalars().all()
                
                for offer in expired_offers:
                    offer.status = OfferStatus.EXPIRED
                    
                    # حذف دکمه از پست کانال
                    if offer.channel_message_id and settings.channel_id:
                        try:
                            await bot.edit_message_reply_markup(
                                chat_id=settings.channel_id,
                                message_id=offer.channel_message_id,
                                reply_markup=None
                            )
                        except:
                            pass
                    
                    logger.info(f"Offer {offer.id} expired automatically")
                
                if expired_offers:
                    await session.commit()
                    logger.info(f"Expired {len(expired_offers)} offers")
                    
        except Exception as e:
            logger.error(f"Error in offer expiry monitor: {e}")
        
        # هر 30 ثانیه چک کن
        await asyncio.sleep(30)

async def main():
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="Markdown"))
    dp = Dispatcher(storage=MemoryStorage())

    auth_middleware = AuthMiddleware(session_pool=AsyncSessionLocal)
    dp.message.middleware(auth_middleware)
    dp.callback_query.middleware(auth_middleware)

    dp.include_router(start.router)
    # trade.router replaced by split handlers
    from bot.handlers import trade_create, trade_execute, trade_manage
    dp.include_router(trade_create.router)   # Creation & Text logic
    dp.include_router(trade_execute.router) # Channel execution
    dp.include_router(trade_manage.router)  # Management (Expire)
    
    dp.include_router(trade_history.router)  # تاریخچه معاملات
    dp.include_router(panel.router)
    dp.include_router(admin.router)
    dp.include_router(admin_commodities.router)
    dp.include_router(admin_users.router)
    dp.include_router(default.router) 

    asyncio.create_task(monitor_expired_invitations(bot))
    asyncio.create_task(monitor_expired_restrictions(bot))
    asyncio.create_task(monitor_expired_offers(bot))
    asyncio.create_task(cleanup_old_notifications())

    logger.info("--> Starting Bot polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("--> Bot polling stopped.")