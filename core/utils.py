# trading_bot/core/utils.py (کامل و اصلاح شده)
import asyncio
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest


# نگاشت اعداد فارسی و عربی به انگلیسی
PERSIAN_NUM_MAP = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
}

def normalize_persian_numerals(text: str) -> str:
    """اعداد فارسی/عربی در یک رشته را به انگلیسی تبدیل می‌کند."""
    if not text:
        return text
    for p, e in PERSIAN_NUM_MAP.items():
        text = text.replace(p, e)
    return text

def normalize_account_name(text: str) -> str:
    """اعداد فارسی/عربی را به انگلیسی تبدیل کرده و حروف را کوچک می‌کند."""
    if not text:
        return text
    normalized_text = normalize_persian_numerals(text)
    return normalized_text.lower()


# --- تابع جدید برای حذف خودکار پیام ---
async def send_deletable_message(
    bot: Bot, 
    chat_id: int, 
    text: str, 
    delay_seconds: int = 30, 
    **kwargs
):
    """
    یک پیام ارسال می‌کند و یک وظیفه (task) مستقل برای حذف آن
    پس از X ثانیه ایجاد می‌کند.
    """
    async def _delete_task(message: Message, delay: int):
        """تسک حذف پیام پس از تاخیر"""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except TelegramBadRequest:
            # اگر پیام در این فاصله به صورت دستی حذف شده بود
            pass

    try:
        # ارسال پیام
        msg = await bot.send_message(chat_id, text, **kwargs)
        # ایجاد تسک مستقل برای حذف آن
        asyncio.create_task(_delete_task(msg, delay_seconds))
    except TelegramBadRequest as e:
        # اگر در ارسال پیام خطایی رخ داد (مثلا پارس کردن Markdown)
        print(f"Error sending deletable message: {e}")