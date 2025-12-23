from aiogram import Router, F, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional
from datetime import date, datetime, timedelta
from sqlalchemy import select, func

from models.user import User
from models.offer import Offer, OfferStatus
from core.config import settings
from core.db import AsyncSessionLocal
from core.trading_settings import get_trading_settings
from bot.utils.redis_helpers import track_expire_rate, track_daily_expire
from bot.callbacks import ExpireOfferCallback

router = Router()

@router.callback_query(ExpireOfferCallback.filter())
async def handle_expire_offer(callback: types.CallbackQuery, callback_data: ExpireOfferCallback, user: Optional[User], bot: Bot):
    if not user:
        await callback.answer()
        return
    
    ts = get_trading_settings()
    today = date.today()
    
    # بررسی محدودیت در دقیقه (با Redis)
    rate_count = await track_expire_rate(user.id, window_seconds=60)
    if rate_count > ts.offer_expire_rate_per_minute:
        await callback.answer(f"❌ حداکثر {ts.offer_expire_rate_per_minute} منقضی در دقیقه مجاز است")
        return
    
    # شمارش کل لفظ‌های امروز
    async with AsyncSessionLocal() as session:
        start_of_day = datetime.combine(today, datetime.min.time())
        
        total_offers_today = await session.scalar(
            select(func.count(Offer.id)).where(
                Offer.user_id == user.id,
                Offer.created_at >= start_of_day
            )
        ) or 0
    
    # دریافت آمار روزانه (با Redis)
    daily_data = await track_daily_expire(user.id, total_offers_today)
    
    # اگر از آستانه رد شده و 1/3 را استفاده کرده
    threshold = ts.offer_expire_daily_limit_after_threshold
    if daily_data["count"] >= threshold:
        max_allowed = total_offers_today // 3
        if daily_data["count"] >= max_allowed:
            await callback.answer(
                f"❌ شما امروز {daily_data['count']} لفظ منقضی کرده‌اید.\n"
                f"برای منقضی کردن بیشتر، باید لفظ‌های جدید ثبت کنید."
            )
            return
    
    # پارس callback_data
    offer_id = callback_data.offer_id
    
    async with AsyncSessionLocal() as session:
        offer = await session.get(Offer, offer_id)
        
        if not offer:
            await callback.answer("❌ لفظ یافت نشد")
            return
        
        if offer.user_id != user.id:
            await callback.answer("❌ شما مالک این لفظ نیستید")
            return
        
        if offer.status != OfferStatus.ACTIVE:
            await callback.answer("❌ این لفظ دیگر فعال نیست")
            return
        
        # منقضی کردن لفظ
        offer.status = OfferStatus.EXPIRED
        await session.commit()
        
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
        
        # حذف دکمه از پیام کاربر
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("✅ لفظ شما منقضی شد")
