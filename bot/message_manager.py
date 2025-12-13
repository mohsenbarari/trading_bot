# bot/message_manager.py
"""سیستم متمرکز مدیریت حذف پیام‌ها"""

import asyncio
from typing import Optional
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from enum import Enum

# زمان‌های حذف (به ثانیه)
class DeleteDelay(Enum):
    DEFAULT = 120       # 2 دقیقه - پیش‌فرض
    NOTIFICATION = 86400  # 1 روز
    INVITATION = 259200   # 3 روز
    NEVER = 0           # هرگز حذف نشود


# نگهداری message_id که کیبورد به آن لنگر زده
_anchor_messages: dict[int, int] = {}  # chat_id -> message_id


def set_anchor(chat_id: int, message_id: int):
    """تنظیم پیام لنگر کیبورد"""
    _anchor_messages[chat_id] = message_id


def get_anchor(chat_id: int) -> Optional[int]:
    """دریافت پیام لنگر فعلی"""
    return _anchor_messages.get(chat_id)


def clear_anchor(chat_id: int):
    """پاک کردن لنگر"""
    _anchor_messages.pop(chat_id, None)


def is_anchor(chat_id: int, message_id: int) -> bool:
    """بررسی اینکه آیا پیام لنگر است یا خیر"""
    return _anchor_messages.get(chat_id) == message_id


async def _delete_message_task(bot: Bot, chat_id: int, message_id: int, delay: int):
    """تسک پس‌زمینه برای حذف پیام"""
    await asyncio.sleep(delay)
    
    # اگر پیام لنگر است، حذف نکن
    if is_anchor(chat_id, message_id):
        return
    
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass  # پیام قبلاً حذف شده یا دسترسی نداریم
    except Exception:
        pass


def schedule_delete(
    bot: Bot, 
    chat_id: int, 
    message_id: int, 
    delay: DeleteDelay | int = DeleteDelay.DEFAULT
):
    """
    زمان‌بندی حذف یک پیام
    
    Args:
        bot: شیء Bot
        chat_id: آیدی چت
        message_id: آیدی پیام
        delay: تاخیر حذف (DeleteDelay یا عدد ثانیه)
    """
    if isinstance(delay, DeleteDelay):
        delay_seconds = delay.value
    else:
        delay_seconds = delay
    
    if delay_seconds <= 0:
        return  # حذف نشود
    
    asyncio.create_task(_delete_message_task(bot, chat_id, message_id, delay_seconds))


def schedule_message_delete(message: Message, delay: DeleteDelay | int = DeleteDelay.DEFAULT):
    """
    زمان‌بندی حذف یک Message object
    """
    schedule_delete(message.bot, message.chat.id, message.message_id, delay)


async def delete_previous_anchor(bot: Bot, chat_id: int, delay: int = 0):
    """
    حذف لنگر قبلی و پاک کردن آن
    
    Args:
        bot: شیء Bot
        chat_id: آیدی چت
        delay: تاخیر حذف (پیش‌فرض: فوری)
    """
    previous_anchor = get_anchor(chat_id)
    if previous_anchor:
        clear_anchor(chat_id)
        if delay > 0:
            schedule_delete(bot, chat_id, previous_anchor, delay)
        else:
            try:
                await bot.delete_message(chat_id, previous_anchor)
            except TelegramBadRequest:
                pass
            except Exception:
                pass


async def handle_user_message(message: Message, is_new_anchor: bool = False):
    """
    مدیریت پیام کاربر - حذف پیام کاربر و لنگر قبلی اگر لازم باشد
    
    Args:
        message: پیام کاربر
        is_new_anchor: آیا پیام بعدی لنگر جدید خواهد بود؟
    """
    # همیشه پیام کاربر را برای حذف زمان‌بندی کن
    schedule_message_delete(message, DeleteDelay.DEFAULT)
    
    # اگر قرار است لنگر جدید تنظیم شود، لنگر قبلی را هم حذف کن
    if is_new_anchor:
        await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
