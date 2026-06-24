# core/utils.py
"""
توابع کمکی مشترک

این ماژول شامل توابع کمکی برای:
- مدیریت Timezone و تاریخ شمسی
- ارسال نوتیفیکیشن
- مدیریت محدودیت‌های کاربر
"""
import asyncio
import inspect as py_inspect
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Any, TYPE_CHECKING

import jdatetime
import pytz
import redis.asyncio as redis
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import telegram_gateway
from core.enums import NotificationLevel, NotificationCategory
from core.redis import pool
from models.notification import Notification

# Type hints for User model (avoid circular import)
if TYPE_CHECKING:
    from models.user import User

__all__ = [
    # Timezone & Date
    "utc_now",
    "utc_now_naive",
    "to_iran_time",
    "get_iran_time",
    "format_iran_datetime",
    "to_jalali_str",
    "parse_jalali_str",
    # String normalization
    "normalize_persian_numerals",
    "normalize_account_name",
    "unique_user_ids",
    # Telegram
    "send_deletable_message",
    "send_telegram_notification",
    # Notifications
    "create_user_notification",
    # User limits
    "check_user_limits",
    "increment_user_counter",
    "reset_user_counters",
]

logger = logging.getLogger(__name__)


# ===== TIMEZONE UTILITIES =====
# اصل معماری: "Store UTC, Display Local"
# همه datetime ها در دیتابیس UTC ذخیره می‌شوند
# تبدیل به ساعت ایران فقط در لحظه نمایش انجام می‌شود

# Timezone ایران با استفاده از pytz (استاندارد)
IRAN_TZ = pytz.timezone('Asia/Tehran')


def utc_now() -> datetime:
    """
    تاریخ و زمان فعلی به UTC.
    این تابع جایگزین datetime.utcnow() است که در Python 3.12 deprecated شده.
    
    Returns:
        datetime: زمان فعلی به UTC (timezone-aware)
    """
    return datetime.now(timezone.utc)


def utc_now_naive() -> datetime:
    """Return the current UTC time as a naive datetime.

    Use this for legacy database columns that still store naive UTC timestamps.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_iran_time() -> datetime:
    """زمان فعلی ایران را برمی‌گرداند."""
    return datetime.now(IRAN_TZ)


def to_iran_time(dt: Optional[datetime]) -> Optional[datetime]:
    """
    تبدیل datetime از UTC به ساعت ایران.
    فقط برای نمایش به کاربر استفاده شود.
    
    Args:
        dt: datetime به UTC (naive یا aware)
        
    Returns:
        datetime: زمان به ساعت ایران یا None
    """
    if dt is None:
        return None
    # اگر naive است، فرض می‌کنیم UTC است
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(IRAN_TZ)


def format_iran_datetime(dt: Optional[datetime], include_time: bool = True) -> str:
    """
    فرمت کردن datetime به صورت خوانا برای کاربران ایرانی.
    
    Args:
        dt: datetime به UTC
        include_time: آیا ساعت هم نمایش داده شود
        
    Returns:
        str: تاریخ و ساعت به فرمت ایرانی
    """
    if dt is None:
        return "---"
    
    iran_dt = to_iran_time(dt)
    
    if include_time:
        return iran_dt.strftime("%Y-%m-%d %H:%M")
    else:
        return iran_dt.strftime("%Y-%m-%d")


def to_jalali_str(dt: Optional[datetime], format: str = "%Y/%m/%d %H:%M") -> Optional[str]:
    """تاریخ میلادی را به رشته شمسی تبدیل می‌کند."""
    if dt is None:
        return None
    iran_dt = to_iran_time(dt)
    # حذف timezone info برای سازگاری با jdatetime
    iran_dt_naive = iran_dt.replace(tzinfo=None)
    return jdatetime.datetime.fromgregorian(datetime=iran_dt_naive).strftime(format)


def parse_jalali_str(date_str: str) -> Optional[datetime]:
    """رشته تاریخ شمسی را به datetime میلادی (UTC) تبدیل می‌کند."""
    if not date_str:
        return None
    try:
        # فرض فرمت: 1403/09/14 12:30
        # اگر فقط تاریخ باشد، ساعت 00:00 در نظر گرفته می‌شود
        if " " not in date_str:
            date_str += " 00:00"
            
        j_dt = jdatetime.datetime.strptime(date_str, "%Y/%m/%d %H:%M")
        # تبدیل به میلادی
        g_dt = j_dt.togregorian()
        # تنظیم تایم‌زون ایران
        g_dt = IRAN_TZ.localize(g_dt)
        # تبدیل به UTC
        return g_dt.astimezone(pytz.utc)
    except Exception:
        return None


# ===== STRING NORMALIZATION =====

# نگاشت اعداد فارسی
PERSIAN_NUM_MAP = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
}


def normalize_persian_numerals(text: str) -> str:
    """تبدیل اعداد فارسی/عربی به انگلیسی."""
    if not text:
        return text
    for persian, english in PERSIAN_NUM_MAP.items():
        text = text.replace(persian, english)
    return text


def normalize_account_name(text: str) -> str:
    """نرمال‌سازی نام حساب (تبدیل اعداد فارسی و lowercase)."""
    if not text:
        return text
    normalized_text = normalize_persian_numerals(text)
    return normalized_text.lower()


def unique_user_ids(user_ids: list[object]) -> list[int]:
    """Return positive user ids once while preserving input order."""
    unique_ids: list[int] = []
    seen_ids: set[int] = set()

    for raw_value in user_ids:
        try:
            user_id = int(raw_value)
        except (TypeError, ValueError):
            continue

        if user_id <= 0 or user_id in seen_ids:
            continue

        seen_ids.add(user_id)
        unique_ids.append(user_id)

    return unique_ids


# ===== TELEGRAM UTILITIES =====

async def send_deletable_message(
    bot: Bot,
    chat_id: int,
    text: str,
    delay_seconds: int = 30,
    **kwargs: Any
) -> None:
    """
    ارسال پیام به تلگرام که بعد از مدت مشخصی حذف می‌شود.
    
    Args:
        bot: شیء Bot تلگرام
        chat_id: آیدی چت
        text: متن پیام
        delay_seconds: تأخیر قبل از حذف (ثانیه)
        **kwargs: آرگومان‌های اضافی برای send_message
    """
    async def _delete_task(message: Message, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except TelegramBadRequest:
            pass

    try:
        msg = await bot.send_message(chat_id, text, **kwargs)
        asyncio.create_task(_delete_task(msg, delay_seconds))
    except TelegramBadRequest as e:
        logger.warning(f"Error sending deletable message: {e}")


async def send_telegram_notification(telegram_id: int, message: str) -> bool:
    """
    ارسال پیام مستقیم به تلگرام کاربر از طریق API تلگرام.
    این تابع از API side فراخوانی می‌شود (نه Bot side).
    
    Args:
        telegram_id: آیدی تلگرام کاربر
        message: متن پیام
        
    Returns:
        True اگر ارسال موفق بود
    """
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.warning("BOT_TOKEN not found in environment")
        return False
    
    try:
        result = await telegram_gateway.send_message(
            telegram_id,
            message,
            parse_mode="Markdown",
            bot_token=bot_token,
            idempotency_key=f"user-notification:{telegram_id}",
        )
        if result.ok:
            logger.info(f"Telegram notification sent to {telegram_id}")
            return True
        logger.warning(f"Telegram API Error: {result.status_code} - {result.response_text or result.error}")
        return False
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
        return False


# ===== NOTIFICATION UTILITIES =====

async def create_user_notification(
    db: AsyncSession,
    user_id: int,
    message: str,
    level: NotificationLevel = NotificationLevel.INFO,
    category: NotificationCategory = NotificationCategory.SYSTEM,
    extra_payload: dict | None = None,
    dedupe_key: str | None = None,
) -> Notification:
    """
    ایجاد نوتیفیکیشن برای کاربر و افزایش شمارنده.
    
    Args:
        db: AsyncSession دیتابیس
        user_id: آیدی کاربر
        message: متن نوتیفیکیشن
        level: سطح نوتیفیکیشن
        category: دسته‌بندی نوتیفیکیشن
        
    Returns:
        Notification: شیء نوتیفیکیشن ایجاد شده
    """
    try:
        from core.production_test_isolation import should_suppress_user_notification

        if await should_suppress_user_notification(db, user_id):
            logger.warning(
                "Suppressed user notification during production test isolation",
                extra={
                    "event": "production_test_isolation.notification_suppressed",
                    "user_id": user_id,
                    "category": getattr(category, "value", category),
                    "dedupe_key": dedupe_key,
                },
            )
            return Notification(
                user_id=user_id,
                message=message,
                is_read=False,
                level=level,
                category=category,
                dedupe_key=dedupe_key,
                extra_payload=extra_payload or None,
            )
    except Exception:
        logger.exception(
            "Failed to evaluate production test isolation notification suppression; continuing",
            extra={"event": "production_test_isolation.notification_suppression_failed", "user_id": user_id},
        )

    new_notif = Notification(
        user_id=user_id,
        message=message,
        is_read=False,
        level=level,
        category=category,
        dedupe_key=dedupe_key,
        extra_payload=extra_payload or None,
    )
    db.add(new_notif)
    await db.commit()
    await db.refresh(new_notif)
    
    # داده‌ای که به فرانت می‌فرستیم
    payload = {
        "id": new_notif.id,
        "message": new_notif.message,
        "is_read": False,
        "created_at": new_notif.created_at.isoformat(),
        "level": new_notif.level.value,
        "category": new_notif.category.value,
        "dedupe_key": new_notif.dedupe_key,
        "extra_payload": new_notif.extra_payload,
    }
    if extra_payload:
        payload.update(extra_payload)
    
    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            # الف: افزایش شمارنده
            count_key = f"user:{user_id}:unread_count"
            await redis_client.incr(count_key)
    except Exception as e:
        logger.warning(f"Redis Increment Error: {e}")
    
    # انتشار در کانال (با فرمت جدید)
    await publish_user_event(user_id, "message", payload)
    try:
        from core.web_push import schedule_notification_web_push

        schedule_notification_web_push(new_notif.id, extra_payload)
    except Exception as e:
        logger.warning(f"Web Push Schedule Error: {e}")

    return new_notif


async def publish_user_event(user_id: int, event: str, data: dict) -> None:
    """
    انتشار یک رویداد SSE برای کاربر خاص.
    
    Args:
        user_id: آیدی کاربر
        event: نام رویداد (مثلاً 'message', 'chat:typing')
        data: داده‌های رویداد (dict)
    """
    try:
        from core.db import AsyncSessionLocal
        from core.production_test_isolation import should_suppress_user_notification

        async with AsyncSessionLocal() as session:
            if await should_suppress_user_notification(session, user_id):
                logger.warning(
                    "Suppressed user realtime event during production test isolation",
                    extra={
                        "event": "production_test_isolation.realtime_suppressed",
                        "user_id": user_id,
                        "realtime_event": event,
                    },
                )
                return
    except Exception:
        logger.exception(
            "Failed to evaluate production test isolation realtime suppression; continuing",
            extra={"event": "production_test_isolation.realtime_suppression_failed", "user_id": user_id},
        )

    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            channel_key = f"notifications:{user_id}"
            
            # لفاف‌پیچی استاندارد برای پردازش در stream_notifications
            sse_payload = {
                "event": event,
                "data": data
            }
            
            await redis_client.publish(channel_key, json.dumps(sse_payload))
            
    except Exception as e:
        logger.warning(f"Redis Publish Error: {e}")


# ===== USER LIMITS UTILITIES =====

def check_user_limits(
    user: "User",
    action_type: str,
    quantity: int = 1
) -> Tuple[bool, Optional[str]]:
    """
    بررسی می‌کند که آیا کاربر مجاز به انجام عملیات است یا خیر.
    
    Args:
        user: شیء User
        action_type: 'trade' یا 'channel_message'
        quantity: تعداد کالا (برای trade)
    
    Returns:
        (allowed: bool, error_message: str | None)
    """
    # اگر محدودیت ندارد، همیشه مجاز است
    if user.limitations_expire_at is None:
        return (True, None)
    
    # اگر محدودیت منقضی شده، مجاز است
    if user.limitations_expire_at <= utc_now_naive():
        return (True, None)
    
    if action_type == 'trade':
        # بررسی تعداد معاملات
        if user.max_daily_trades is not None:
            if user.trades_count >= user.max_daily_trades:
                return (False, f"شما به حداکثر تعداد معاملات مجاز ({user.max_daily_trades}) رسیده‌اید.")
        
        # بررسی تعداد کالا
        if user.max_active_commodities is not None:
            if user.commodities_traded_count + quantity > user.max_active_commodities:
                remaining = user.max_active_commodities - user.commodities_traded_count
                return (False, f"شما به حداکثر تعداد کالای مجاز رسیده‌اید. باقی‌مانده: {max(0, remaining)}")
    
    elif action_type == 'channel_message':
        # بررسی تعداد ارسال لفظ
        if user.max_daily_requests is not None:
            if user.channel_messages_count >= user.max_daily_requests:
                return (False, f"شما به حداکثر تعداد ارسال لفظ مجاز ({user.max_daily_requests}) رسیده‌اید.")
    
    return (True, None)


async def increment_user_counter(
    session: AsyncSession,
    user: "User",
    action_type: str,
    quantity: int = 1
) -> None:
    """
    شمارنده مربوط به عملیات را با قفل ردیف افزایش می‌دهد.
    
    این تابع از SELECT ... FOR UPDATE و تغییر ORM استفاده می‌کند تا هم
    Race Condition کنترل شود و هم listenerهای sync ردیف change_log بسازند.
    
    Args:
        session: AsyncSession دیتابیس
        user: شیء User
        action_type: 'trade' یا 'channel_message'
        quantity: تعداد کالا (برای trade)
    """
    from models.user import User as UserModel
    
    logger.info(f"increment_user_counter called: action_type={action_type}, quantity={quantity}, user_id={user.id if user else 'None'}")
    
    try:
        result = await session.execute(
            select(UserModel).where(UserModel.id == user.id).with_for_update()
        )
        locked_user = result.scalar_one_or_none()
        if py_inspect.isawaitable(locked_user):
            locked_user = await locked_user
        if locked_user is None:
            logger.warning("Counter update skipped because user was not found")
            await session.commit()
            return

        if action_type == 'trade':
            locked_user.trades_count = (locked_user.trades_count or 0) + 1
            locked_user.commodities_traded_count = (
                (locked_user.commodities_traded_count or 0) + quantity
            )
            logger.info(f"Locked ORM update: trades_count += 1, commodities_traded_count += {quantity}")
            
        elif action_type == 'channel_message':
            locked_user.channel_messages_count = (locked_user.channel_messages_count or 0) + 1
            logger.info("Locked ORM update: channel_messages_count += 1")
        
        await session.commit()
        logger.info("Session committed successfully")
    except Exception as e:
        logger.error(f"Error in locked counter update: {e}")
        await session.rollback()
        # Don't raise - counter update failure should not break the main flow


async def reset_user_counters(session: AsyncSession, user: "User") -> None:
    """
    شمارنده‌های کاربر را با قفل ردیف صفر می‌کند.
    
    Args:
        session: AsyncSession دیتابیس
        user: شیء User
    """
    from models.user import User as UserModel
    
    result = await session.execute(
        select(UserModel).where(UserModel.id == user.id).with_for_update()
    )
    locked_user = result.scalar_one_or_none()
    if py_inspect.isawaitable(locked_user):
        locked_user = await locked_user
    if locked_user is not None:
        locked_user.trades_count = 0
        locked_user.commodities_traded_count = 0
        locked_user.channel_messages_count = 0
    await session.commit()
    await session.refresh(user)
