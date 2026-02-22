import logging
from aiogram import Router, F, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional, List
from sqlalchemy import select
from datetime import datetime

from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.trade import Trade, TradeType, TradeStatus
from core.config import settings
from core.db import AsyncSessionLocal
from bot.utils.redis_helpers import check_double_click
from core.utils import check_user_limits, increment_user_counter, create_user_notification
from core.enums import NotificationLevel, NotificationCategory, UserRole
from bot.callbacks import ChannelTradeCallback
from api.routers.realtime import publish_event


logger = logging.getLogger(__name__)

router = Router()

def build_lot_buttons(offer_id: int, remaining: int, lot_sizes: list[int]) -> InlineKeyboardMarkup:
    """ساخت دکمه‌های لات بر اساس باقیمانده و لات‌های موجود"""
    all_amounts = [remaining] + sorted(lot_sizes, reverse=True)
    # حذف تکراری‌ها با حفظ ترتیب
    seen = set()
    unique_amounts = []
    for a in all_amounts:
        if a not in seen and a > 0:
            seen.add(a)
            unique_amounts.append(a)
    
    buttons = []
    for amount in unique_amounts:
        buttons.append(InlineKeyboardButton(
            text=f"{amount} عدد",
            callback_data=ChannelTradeCallback(offer_id=offer_id, amount=amount).pack()
        ))
    
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None


@router.callback_query(ChannelTradeCallback.filter())
async def handle_channel_trade(callback: types.CallbackQuery, callback_data: ChannelTradeCallback, user: Optional[User], bot: Bot):
    """کلیک روی دکمه پست کانال - دابل‌کلیک برای تایید"""
    if not user:
        await callback.answer()
        return
    
    # ===== بررسی مسدودیت کاربر =====
    if user.trading_restricted_until and user.trading_restricted_until > datetime.utcnow():
        await callback.answer("⛔ حساب شما مسدود است", show_alert=True)
        return
    
    # پارس callback_data
    offer_id = callback_data.offer_id
    trade_amount = callback_data.amount
    
    # ===== بررسی محدودیت کاربر =====
    # باید قبل از قفل offer انجام شود
    allowed, error_msg = check_user_limits(user, 'trade', trade_amount or 1)
    if not allowed:
        await callback.answer(f"⚠️ {error_msg}", show_alert=True)
        return
    
    async with AsyncSessionLocal() as session:
        # اول قفل را بگیر، سپس روابط را بارگذاری کن
        # FOR UPDATE با LEFT OUTER JOIN سازگار نیست
        stmt = select(Offer).where(Offer.id == offer_id).with_for_update()
        offer = (await session.execute(stmt)).scalar_one_or_none()
        
        if offer:
            # بارگذاری روابط بعد از گرفتن قفل
            await session.refresh(offer, ["user", "commodity"])
        
        if not offer:
            await callback.answer()
            return
        
        if offer.status != OfferStatus.ACTIVE:
            await callback.answer()
            return
        
        if offer.user_id == user.id:
            await callback.answer()
            return
        
        # ===== بررسی بلاک =====
        from core.services.block_service import is_blocked
        is_blocked_check, blocker_id = await is_blocked(session, offer.user_id, user.id)
        if is_blocked_check:
            if blocker_id == user.id:
                # کاربر فعلی این لفظ‌دهنده را بلاک کرده
                await callback.answer("⚠️ شما این کاربر را مسدود کرده‌اید!", show_alert=True)
            else:
                # لفظ‌دهنده کاربر فعلی را بلاک کرده
                await callback.answer("❌ این لفظ در دسترس نیست.", show_alert=True)
            return
        # =======================
        
        # تعداد واقعی معامله
        actual_amount = trade_amount or offer.remaining_quantity or offer.quantity
        
        # بررسی اینکه تعداد درخواستی از باقیمانده بیشتر نباشد
        remaining = offer.remaining_quantity or offer.quantity
        if actual_amount > remaining:
            await callback.answer("❌ این تعداد دیگر موجود نیست")
            return
        
        # بررسی دابل‌کلیک با Redis (0.5 ثانیه)
        is_confirmed = await check_double_click(user.id, offer_id, actual_amount, timeout=0.5)
        
        if is_confirmed:
            # دابل‌کلیک - انجام معامله
            
            # نوع معامله از دید پاسخ‌دهنده
            trade_type = TradeType.SELL if offer.offer_type == OfferType.BUY else TradeType.BUY
            
            # محاسبه شماره معامله (آخرین شماره + 1، شروع از 10000)
            from sqlalchemy import func as sql_func
            max_trade_number = await session.scalar(
                select(sql_func.max(Trade.trade_number))
            )
            new_trade_number = (max_trade_number or 9999) + 1
            
            # ایجاد معامله
            new_trade = Trade(
                trade_number=new_trade_number,
                offer_id=offer.id,
                offer_user_id=offer.user_id,
                offer_user_mobile=offer.user.mobile_number,
                responder_user_id=user.id,
                responder_user_mobile=user.mobile_number,
                commodity_id=offer.commodity_id,
                trade_type=trade_type,
                quantity=actual_amount,
                price=offer.price,
                status=TradeStatus.COMPLETED
            )
            session.add(new_trade)
            
            # بروزرسانی لفظ
            new_remaining = remaining - actual_amount
            offer.remaining_quantity = new_remaining
            
            # بروزرسانی لات‌ها
            new_lot_sizes = list(offer.lot_sizes) if offer.lot_sizes else []
            if actual_amount in new_lot_sizes:
                new_lot_sizes.remove(actual_amount)
            offer.lot_sizes = new_lot_sizes if new_lot_sizes else None
            
            # اگر باقیمانده صفر شد، لفظ تکمیل شود
            if new_remaining <= 0:
                offer.status = OfferStatus.COMPLETED
            
            # تلاش برای commit با retry در صورت تداخل trade_number
            from sqlalchemy.exc import IntegrityError
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await session.commit()
                    break
                except IntegrityError as e:
                    if "trade_number" in str(e) and attempt < max_retries - 1:
                        await session.rollback()
                        # محاسبه مجدد شماره معامله
                        max_trade_number = await session.scalar(
                            select(sql_func.max(Trade.trade_number))
                        )
                        new_trade.trade_number = (max_trade_number or 9999) + 1
                        session.add(new_trade)
                    else:
                        raise
            
            # افزایش شمارنده معاملات
            # فقط پاسخ‌دهنده (کسی که روی لفظ دیگران معامله می‌کند) شمارنده‌اش افزایش می‌یابد
            # صاحب لفظ شمارنده‌اش افزایش نمی‌یابد (چون او فقط لفظ داده، فعالانه معامله نکرده)
            from core.utils import increment_user_counter
            await increment_user_counter(session, user, 'trade', actual_amount)
            
            # ارسال رویداد SSE برای آپدیت یا تکمیل لفظ
            if new_remaining <= 0:
                await publish_event("offer:completed", {"id": offer.id})
            else:
                await publish_event("offer:updated", {
                    "id": offer.id,
                    "remaining_quantity": new_remaining,
                    "lot_sizes": offer.lot_sizes
                })
            
            # ارسال رویداد معامله جدید (اختیاری - برای داشبورد)
            # await publish_event("trade:created", { ... })

            # اطلاعات معامله

            offer_type_fa = "خرید" if offer.offer_type == OfferType.BUY else "فروش"
            respond_type_fa = "فروش" if offer.offer_type == OfferType.BUY else "خرید"
            offer_emoji = "🟢" if offer.offer_type == OfferType.BUY else "🔴"
            respond_emoji = "🔴" if offer.offer_type == OfferType.BUY else "🟢"
            
            # تاریخ و زمان شمسی
            import jdatetime
            from datetime import timezone, timedelta
            iran_tz = timezone(timedelta(hours=3, minutes=30))
            now = datetime.now(iran_tz)
            jalali_dt = jdatetime.datetime.fromgregorian(datetime=now)
            trade_datetime = jalali_dt.strftime("%Y/%m/%d   %H:%M")
            
            # لینک پروفایل طرفین
            responder_profile_link = f"https://t.me/{settings.bot_username}?start=profile_{user.id}"
            offer_owner_profile_link = f"https://t.me/{settings.bot_username}?start=profile_{offer.user_id}"
            
            # پیام برای پاسخ‌دهنده (کاربر فعلی)
            responder_msg = (
                f"{respond_emoji} **{respond_type_fa}**\n\n"
                f"💰 فی: {offer.price:,}\n"
                f"📦 تعداد: {actual_amount}\n"
                f"🏷️ کالا: {offer.commodity.name}\n"
                f"👤 طرف معامله: [{offer.user.account_name}]({offer_owner_profile_link})\n"
                f"🔢 شماره معامله: {new_trade_number}\n"
                f"🕐 زمان معامله: {trade_datetime}"
            )
            
            # پیام برای لفظ‌دهنده
            offer_owner_msg = (
                f"{offer_emoji} **{offer_type_fa}**\n\n"
                f"💰 فی: {offer.price:,}\n"
                f"📦 تعداد: {actual_amount}\n"
                f"🏷️ کالا: {offer.commodity.name}\n"
                f"👤 طرف معامله: [{user.account_name}]({responder_profile_link})\n"
                f"🔢 شماره معامله: {new_trade_number}\n"
                f"🕐 زمان معامله: {trade_datetime}"
            )
            
            # ارسال پیام به هر دو کاربر
            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=responder_msg,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.debug(f"Failed to send message to responder: {e}")
            
            try:
                await bot.send_message(
                    chat_id=offer.user.telegram_id,
                    text=offer_owner_msg,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.debug(f"Failed to send message to offer owner: {e}")
            
            # ارسال نوتیفیکیشن به اپ برای هر دو کاربر
            # نوتیفیکیشن ساده‌تر برای اپ (بدون Markdown)
            notif_msg_responder = (
                f"{respond_emoji} {respond_type_fa}\n"
                f"💰 فی: {offer.price:,} | 📦 تعداد: {actual_amount}\n"
                f"🏷️ کالا: {offer.commodity.name}\n"
                f"👤 طرف معامله: {offer.user.account_name}\n"
                f"🔢 شماره: {new_trade_number}"
            )
            
            notif_msg_owner = (
                f"{offer_emoji} {offer_type_fa}\n"
                f"💰 فی: {offer.price:,} | 📦 تعداد: {actual_amount}\n"
                f"🏷️ کالا: {offer.commodity.name}\n"
                f"👤 طرف معامله: {user.account_name}\n"
                f"🔢 شماره: {new_trade_number}"
            )
            
            try:
                # تابع کمکی برای نوتیفیکیشن با مدیریت خطا
                async def safe_create_notif(user_id, msg):
                    try:
                        await create_user_notification(
                            session, user_id, msg,
                            level=NotificationLevel.SUCCESS,
                            category=NotificationCategory.TRADE
                        )
                    except Exception as e:
                        logger.debug(f"Failed to create notification: {e}")

                await safe_create_notif(user.id, notif_msg_responder)
                await safe_create_notif(offer.user_id, notif_msg_owner)
            except Exception as e:
                logger.debug(f"Failed to create notifications: {e}")
            
            # بروزرسانی دکمه‌های پست کانال
            try:
                if new_remaining <= 0:
                    # لفظ تکمیل - حذف دکمه‌ها
                    await callback.message.edit_reply_markup(reply_markup=None)
                else:
                    # ساخت دکمه‌های جدید
                    new_keyboard = build_lot_buttons(offer_id, new_remaining, new_lot_sizes)
                    await callback.message.edit_reply_markup(reply_markup=new_keyboard)
            except Exception as e:
                logger.debug(f"Failed to update channel buttons: {e}")
            
            await callback.answer()
        else:
            # کلیک اول - Redis ثبت کرده، راهنمایی به کاربر
            await callback.answer("برای تایید دوباره کلیک کنید ☑️", show_alert=False)
