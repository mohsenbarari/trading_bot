import asyncio
import json
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from models.notification import Notification
from core.enums import NotificationLevel, NotificationCategory


import redis.asyncio as redis
from core.redis import pool 

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