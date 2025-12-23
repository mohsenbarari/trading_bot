import asyncio
import json
from datetime import datetime, timezone, timedelta
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from models.notification import Notification
from core.enums import NotificationLevel, NotificationCategory


import redis.asyncio as redis
from core.redis import pool 

# ===== TIMEZONE UTILITIES =====
# اصل معماری: "Store UTC, Display Local"
# همه datetime ها در دیتابیس UTC ذخیره می‌شوند
# تبدیل به ساعت ایران فقط در لحظه نمایش انجام می‌شود

# Timezone ایران (UTC+3:30)
IRAN_OFFSET = timedelta(hours=3, minutes=30)
IRAN_TZ = timezone(IRAN_OFFSET)


def utc_now() -> datetime:
    """
    تاریخ و زمان فعلی به UTC.
    این تابع جایگزین datetime.utcnow() است که در Python 3.12 deprecated شده.
    
    Returns:
        datetime: زمان فعلی به UTC (timezone-aware)
    """
    return datetime.now(timezone.utc)


def to_iran_time(dt: datetime) -> datetime:
    """
    تبدیل datetime از UTC به ساعت ایران.
    فقط برای نمایش به کاربر استفاده شود.
    
    Args:
        dt: datetime به UTC (naive یا aware)
        
    Returns:
        datetime: زمان به ساعت ایران
    """
    if dt is None:
        return None
    # اگر naive است، فرض می‌کنیم UTC است
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IRAN_TZ)


def format_iran_datetime(dt: datetime, include_time: bool = True) -> str:
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

# نگاشت اعداد فارسی
PERSIAN_NUM_MAP = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
}

def normalize_persian_numerals(text: str) -> str:
    if not text: return text
    for p, e in PERSIAN_NUM_MAP.items():
        text = text.replace(p, e)
    return text

def normalize_account_name(text: str) -> str:
    if not text: return text
    normalized_text = normalize_persian_numerals(text)
    return normalized_text.lower()

async def send_deletable_message(bot: Bot, chat_id: int, text: str, delay_seconds: int = 30, **kwargs):
    async def _delete_task(message: Message, delay: int):
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except TelegramBadRequest:
            pass

    try:
        msg = await bot.send_message(chat_id, text, **kwargs)
        asyncio.create_task(_delete_task(msg, delay_seconds))
    except TelegramBadRequest as e:
        print(f"Error sending deletable message: {e}")

# --- 👇 تابع اصلی برای ایجاد نوتیفیکیشن و افزایش شمارنده ---
async def create_user_notification(
    db: AsyncSession, 
    user_id: int, 
    message: str,
    level: NotificationLevel = NotificationLevel.INFO,      
    category: NotificationCategory = NotificationCategory.SYSTEM 
):
    # 1. ذخیره در دیتابیس (مانند قبل)
    new_notif = Notification(
        user_id=user_id, 
        message=message, 
        is_read=False,
        level=level,       
        category=category
    )
    db.add(new_notif)
    await db.commit()
    await db.refresh(new_notif) # 👈 رفرش می‌کنیم تا created_at و id را بگیریم
    
    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            # الف: افزایش شمارنده (مانند قبل)
            count_key = f"user:{user_id}:unread_count"
            await redis_client.incr(count_key)
            
            # ب: 👇 انتشار پیام در کانال اختصاصی کاربر (Pub/Sub) 👇
            channel_key = f"notifications:{user_id}"
            
            # داده‌ای که به فرانت می‌فرستیم
            payload = {
                "id": new_notif.id,
                "message": new_notif.message,
                "is_read": False,
                "created_at": new_notif.created_at.isoformat(),
                "level": new_notif.level.value,
                "category": new_notif.category.value
            }
            
            # انتشار در کانال
            await redis_client.publish(channel_key, json.dumps(payload))
            
    except Exception as e:
        print(f"⚠️ Redis Error: {e}")

    return new_notif

# --- تابع ارسال پیام مستقیم به تلگرام ---
import httpx
import os

async def send_telegram_notification(telegram_id: int, message: str):
    """
    ارسال پیام مستقیم به تلگرام کاربر از طریق API تلگرام.
    این تابع از API side فراخوانی می‌شود (نه Bot side).
    """
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        print("⚠️ BOT_TOKEN not found in environment")
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": telegram_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print(f"✅ Telegram notification sent to {telegram_id}")
                return True
            else:
                print(f"⚠️ Telegram API Error: {response.status_code} - {response.text}")
                return False
    except Exception as e:
        print(f"⚠️ Error sending Telegram notification: {e}")
        return False

# --- توابع کمکی تاریخ و زمان (ایران/جلالی) ---
import jdatetime
import pytz
from datetime import datetime

IRAN_TZ = pytz.timezone('Asia/Tehran')

def get_iran_time() -> datetime:
    """زمان فعلی ایران را برمی‌گرداند."""
    return datetime.now(IRAN_TZ)

def to_iran_time(dt: datetime) -> datetime:
    """یک آبجکت datetime را به تایم‌زون ایران تبدیل می‌کند."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # اگر naive است، فرض می‌کنیم UTC است
        dt = pytz.utc.localize(dt)
    return dt.astimezone(IRAN_TZ)

def to_jalali_str(dt: datetime, format: str = "%Y/%m/%d %H:%M") -> str | None:
    """تاریخ میلادی را به رشته شمسی تبدیل می‌کند."""
    if dt is None:
        return None
    iran_dt = to_iran_time(dt)
    # حذف timezone info برای سازگاری با jdatetime
    iran_dt_naive = iran_dt.replace(tzinfo=None)
    return jdatetime.datetime.fromgregorian(datetime=iran_dt_naive).strftime(format)

def parse_jalali_str(date_str: str) -> datetime | None:
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


# --- توابع کمکی برای شمارنده‌های محدودیت ---

def check_user_limits(user, action_type: str, quantity: int = 1) -> tuple[bool, str | None]:
    """
    بررسی می‌کند که آیا کاربر مجاز به انجام عملیات است یا خیر.
    
    action_type: 'trade' یا 'channel_message'
    quantity: تعداد کالا (برای trade)
    
    Returns: (allowed: bool, error_message: str | None)
    """
    from datetime import datetime
    
    # اگر محدودیت ندارد، همیشه مجاز است
    if user.limitations_expire_at is None:
        return (True, None)
    
    # اگر محدودیت منقضی شده، مجاز است
    if user.limitations_expire_at <= datetime.utcnow():
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


async def increment_user_counter(session: AsyncSession, user, action_type: str, quantity: int = 1) -> None:
    """
    شمارنده مربوط به عملیات را به صورت اتمیک افزایش می‌دهد.
    
    این تابع از UPDATE اتمیک در سطح دیتابیس استفاده می‌کند
    تا از Race Condition در محیط‌های با همزمانی بالا جلوگیری شود.
    
    action_type: 'trade' یا 'channel_message'
    quantity: تعداد کالا (برای trade)
    """
    import logging
    from sqlalchemy import update
    from models.user import User
    
    logger = logging.getLogger(__name__)
    
    logger.info(f"increment_user_counter called: action_type={action_type}, quantity={quantity}, user_id={user.id if user else 'None'}")
    
    try:
        if action_type == 'trade':
            # آپدیت اتمیک در سطح دیتابیس - جلوگیری از Race Condition
            stmt = (
                update(User)
                .where(User.id == user.id)
                .values(
                    trades_count=User.trades_count + 1,
                    commodities_traded_count=User.commodities_traded_count + quantity
                )
            )
            await session.execute(stmt)
            logger.info(f"Atomic update: trades_count += 1, commodities_traded_count += {quantity}")
            
        elif action_type == 'channel_message':
            # آپدیت اتمیک برای شمارنده پیام‌ها
            stmt = (
                update(User)
                .where(User.id == user.id)
                .values(channel_messages_count=User.channel_messages_count + 1)
            )
            await session.execute(stmt)
            logger.info(f"Atomic update: channel_messages_count += 1")
        
        await session.commit()
        
        # بروزرسانی مقادیر در آبجکت فعلی (برای استفاده در کد بعدی)
        await session.refresh(user)
        
        logger.info("Session committed successfully")
    except Exception as e:
        logger.error(f"Error in atomic update: {e}")
        await session.rollback()
        raise


async def reset_user_counters(session: AsyncSession, user) -> None:
    """
    شمارنده‌های کاربر را به صورت اتمیک صفر می‌کند.
    """
    from sqlalchemy import update
    from models.user import User
    
    stmt = (
        update(User)
        .where(User.id == user.id)
        .values(
            trades_count=0,
            commodities_traded_count=0,
            channel_messages_count=0
        )
    )
    await session.execute(stmt)
    await session.commit()
    await session.refresh(user)