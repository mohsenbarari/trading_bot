import logging
from aiogram import Router, F, types, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from typing import Optional
from datetime import datetime

from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.commodity import Commodity
from bot.states import Trade
from core.config import settings
from core.enums import UserRole
from core.db import AsyncSessionLocal
from core.services.trade_service import (
    validate_lot_sizes,
    validate_quantity,
    validate_price,
    get_available_trade_amounts,
)
from core.utils import to_jalali_str, check_user_limits, increment_user_counter
from bot.handlers.trade_utils import (
    get_trade_type_keyboard,
    get_lot_type_keyboard,
    get_commodities_keyboard,
    get_quantity_keyboard,
    get_confirm_keyboard
)
from bot.callbacks import (
    TradeTypeCallback,
    CommodityCallback,
    PageCallback,
    QuantityCallback,
    LotTypeCallback,
    TradeActionCallback,
    AcceptLotsCallback,
    SkipNotesCallback,
    TextOfferActionCallback,
    TextOfferActionCallback,
    ACTION_NOOP
)


logger = logging.getLogger(__name__)

router = Router()

# Button-based trade creation wizard removed - using text-based creation only

# ============================================
# TEXT OFFER HANDLER
# ============================================

def _get_offer_suggestion(original_text: str, error_message: str) -> str:
    """پیشنهاد فرمت صحیح بر اساس نوع خطا"""
    import re
    
    # نمونه‌های صحیح
    examples = [
        "خ ربع 30تا 75800",
        "فروش نیم 50عدد 758000",
        "خرید 40تا 87000: فقط نقدی",
        "ف 30تا 75800 15 15"
    ]
    
    hint = "💡 **فرمت صحیح:**\n"
    hint += "`[خ/ف/خرید/فروش] [کالا] [تعداد]تا [قیمت]`\n\n"
    
    # پیشنهادات بر اساس نوع خطا
    if "تعداد" in error_message:
        hint += "📌 تعداد باید با `تا` یا `عدد` همراه باشد\n"
        hint += "مثال: `30تا` یا `30 عدد`\n"
    
    elif "قیمت" in error_message:
        if "چندین" in error_message:
            hint += "📌 فقط یک عدد 5 یا 6 رقمی (قیمت) مجاز است\n"
        else:
            hint += "📌 قیمت باید 5 یا 6 رقم باشد\n"
        hint += "مثال: `75800` یا `758000`\n"
    
    elif "خرید" in error_message or "فروش" in error_message:
        hint += "📌 فقط یک نشانگر معامله مجاز است\n"
        hint += "استفاده کنید از: `خ` یا `ف` یا `خرید` یا `فروش`\n"
    
    elif "بخش" in error_message or "جمع" in error_message:
        hint += "📌 برای خُرده‌فروشی:\n"
        hint += "- حداکثر 3 بخش\n"
        hint += "- هر بخش حداقل 5 عدد\n"
        hint += "- جمع بخش‌ها = تعداد کل\n"
        hint += "مثال: `خ 30تا 75800 15 15`\n"
    
    elif "کاراکتر" in error_message:
        hint += "📌 از علائم خاص استفاده نکنید\n"
        hint += "فقط: حروف، اعداد، فاصله، `-` `/` `,`\n"
    
    elif "حداقل" in error_message or "حداکثر" in error_message:
        from core.trading_settings import get_trading_settings
        ts = get_trading_settings()
        hint += f"📌 تعداد مجاز: {ts.offer_min_quantity} تا {ts.offer_max_quantity}\n"
    
    else:
        hint += "📌 نمونه‌های صحیح:\n"
        for ex in examples[:2]:
            hint += f"  `{ex}`\n"
    
    return hint

# فیلتر: پیام‌هایی که خ/ف/خرید/فروش دارند
def has_trade_indicator(text: str) -> bool:
    """چک می‌کند آیا متن حاوی نشانگر معامله است"""
    import re
    if not text:
        return False
    offer_part = text.split(':')[0]  # فقط قبل از توضیحات
    # خ یا ف مستقل یا خرید/فروش
    pattern = r'(?<![آ-ی])[خف](?![آ-ی])|خرید|فروش'
    return bool(re.search(pattern, offer_part))


@router.message(F.text.func(has_trade_indicator))
async def handle_text_offer(message: types.Message, state: FSMContext, user: Optional[User], bot: Bot):
    """پردازش لفظ متنی (خ/ف)"""
    if not user:
        return
    
    # بررسی نقش کاربر
    if user.role == UserRole.WATCH:
        await message.answer("⛔️ شما دسترسی به بخش معاملات را ندارید.")
        return
    
    # بررسی مسدودیت
    if user.trading_restricted_until:
        from datetime import datetime
        now = datetime.utcnow()
        if user.trading_restricted_until > now:
            # محاسبه زمان باقیمانده
            remaining = user.trading_restricted_until - now
            total_seconds = int(remaining.total_seconds())
            
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str(user.trading_restricted_until, "%Y/%m/%d - %H:%M")
            
            await message.answer(
                f"⛔️ **حساب شما مسدود است**\n\n"
                f"📅 تاریخ رفع مسدودیت: {expiry_jalali}\n"
                f"⏳ زمان باقی‌مانده: {countdown}\n\n"
                f"تا رفع مسدودیت امکان انتشار لفظ در کانال را ندارید.",
                parse_mode="Markdown"
            )
            return
    
    # اگر در state دیگری هستیم، پردازش نکن
    current_state = await state.get_state()
    if current_state is not None:
        return
    
    from bot.utils.offer_parser import parse_offer_text, ParsedOffer
    
    result, error = await parse_offer_text(message.text)
    
    # اگر لفظ نیست (خ/ف ندارد)، نادیده بگیر
    if result is None and error is None:
        return
    
    # اگر خطا دارد، پیام خطا با پیشنهاد بده
    if error:
        # ساخت پیام راهنما بر اساس نوع خطا
        suggestion = _get_offer_suggestion(message.text, error.message)
        error_msg = f"{error.message}\n\n{suggestion}"
        await message.answer(error_msg)
        return
    
    # بررسی تعداد لفظ‌های فعال
    from core.trading_settings import get_trading_settings
    ts = get_trading_settings()
    
    async with AsyncSessionLocal() as session:
        from sqlalchemy import func
        active_count = await session.scalar(
            select(func.count(Offer.id)).where(
                Offer.user_id == user.id,
                Offer.status == OfferStatus.ACTIVE
            )
        )
        if active_count >= ts.max_active_offers:
            await message.answer(
                f"❌ شما حداکثر {ts.max_active_offers} لفظ فعال دارید.\n"
                f"لطفاً ابتدا یکی از لفظ‌های قبلی را منقضی کنید."
            )
            return
    
    # ذخیره اطلاعات در state
    await state.update_data(
        trade_type=result.trade_type,
        commodity_id=result.commodity_id,
        commodity_name=result.commodity_name,
        quantity=result.quantity,
        price=result.price,
        is_wholesale=result.is_wholesale,
        lot_sizes=result.lot_sizes,
        notes=result.notes
    )
    
    # نمایش پیش‌نمایش
    trade_emoji = "🟢" if result.trade_type == "buy" else "🔴"
    trade_label = "خرید" if result.trade_type == "buy" else "فروش"
    invisible_padding = "\u2800" * 35
    
    channel_text = f"{trade_emoji}{trade_label} {result.commodity_name} {result.quantity} عدد {result.price:,}"
    if result.notes:
        channel_text += f"\nتوضیحات: {result.notes}"
    channel_text += f"\n{invisible_padding}"
    
    lot_info = "یکجا" if result.is_wholesale else f"خُرد {result.lot_sizes}"
    
    preview = (
        f"**پیش‌نمایش لفظ:**\n\n"
        f"{channel_text}\n\n"
        f"📦 نوع: {lot_info}\n\n"
        f"آیا تایید می‌کنید؟"
    )
    
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تایید و ارسال", callback_data=TextOfferActionCallback(action="confirm").pack()),
            InlineKeyboardButton(text="❌ انصراف", callback_data=TextOfferActionCallback(action="cancel").pack())
        ]
    ])
    
    await message.answer(preview, parse_mode="Markdown", reply_markup=confirm_kb)
    await state.set_state(Trade.awaiting_text_confirm)


@router.callback_query(Trade.awaiting_text_confirm, TextOfferActionCallback.filter(F.action == "confirm"))
async def handle_text_offer_confirm(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    """تایید و ارسال لفظ متنی به کانال (از لاجیک مشترک handle_trade_confirm استفاده می‌کند)"""
    
    if not user:
        await callback.answer()
        return
    
    from core.utils import check_user_limits, to_jalali_str
    from core.trading_settings import get_trading_settings
    from datetime import datetime
    
    ts = get_trading_settings()
    
    # بررسی محدودیت ارسال لفظ
    allowed, error_msg = check_user_limits(user, 'channel_message')
    if not allowed:
        if user.limitations_expire_at:
            remaining = user.limitations_expire_at - datetime.utcnow()
            total_seconds = max(0, int(remaining.total_seconds()))
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str(user.limitations_expire_at, "%Y/%m/%d - %H:%M")
            error_msg += f"\n\n📅 رفع محدودیت: {expiry_jalali}\n⏳ زمان باقی‌مانده: {countdown}"
        
        await callback.message.edit_text(f"⚠️ **محدودیت**\n\n{error_msg}", parse_mode="Markdown")
        await state.clear()
        await callback.answer()
        return
    
    data = await state.get_data()
    quantity = data.get("quantity", 1)
    
    # بررسی محدودیت معاملات و کالا
    allowed, error_msg = check_user_limits(user, 'trade', quantity)
    if not allowed:
        if user.limitations_expire_at:
            remaining = user.limitations_expire_at - datetime.utcnow()
            total_seconds = max(0, int(remaining.total_seconds()))
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str(user.limitations_expire_at, "%Y/%m/%d - %H:%M")
            error_msg += f"\n\n📅 رفع محدودیت: {expiry_jalali}\n⏳ زمان باقی‌مانده: {countdown}"
        
        await callback.message.edit_text(f"⚠️ **محدودیت**\n\n{error_msg}", parse_mode="Markdown")
        await state.clear()
        await callback.answer()
        return
    
    # بررسی تعداد لفظ‌های فعال
    async with AsyncSessionLocal() as session:
        from sqlalchemy import func
        active_count = await session.scalar(
            select(func.count(Offer.id)).where(
                Offer.user_id == user.id,
                Offer.status == OfferStatus.ACTIVE
            )
        )
        if active_count >= ts.max_active_offers:
            await callback.message.edit_text(
                f"❌ شما حداکثر {ts.max_active_offers} لفظ فعال دارید.\n"
                f"لطفاً ابتدا یکی از لفظ‌های قبلی را منقضی کنید.",
                parse_mode="Markdown"
            )
            await state.clear()
            await callback.answer()
            return
    
    trade_type = data.get("trade_type")
    commodity_id = data.get("commodity_id")
    commodity_name = data.get("commodity_name")
    price = data.get("price")
    is_wholesale = data.get("is_wholesale", True)
    lot_sizes = data.get("lot_sizes")
    notes = data.get("notes")
    
    # ===== اعتبارسنجی قیمت رقابتی =====
    from core.services.trade_service import validate_competitive_price
    async with AsyncSessionLocal() as session:
        is_valid_comp, err_comp = await validate_competitive_price(
            db=session,
            offer_type=trade_type,
            commodity_id=commodity_id,
            quantity=quantity,
            proposed_price=price,
            user_id=user.id
        )
        if not is_valid_comp:
            await callback.message.edit_text(err_comp, parse_mode="Markdown")
            await state.clear()
            await callback.answer()
            return
    # =====================================
    
    # ساخت پیام کانال
    trade_emoji = "🟢" if trade_type == "buy" else "🔴"
    trade_label = "خرید" if trade_type == "buy" else "فروش"
    invisible_padding = "\u2800" * 35
    
    channel_message = f"{trade_emoji}{trade_label} {commodity_name} {quantity} عدد {price:,}"
    if notes:
        channel_message += f"\nتوضیحات: {notes}"
    channel_message += f"\n{invisible_padding}"
    
    # ارسال به کانال
    if settings.channel_id:
        try:
            async with AsyncSessionLocal() as session:
                new_offer = Offer(
                    user_id=user.id,
                    offer_type=OfferType.BUY if trade_type == "buy" else OfferType.SELL,
                    commodity_id=commodity_id,
                    quantity=quantity,
                    remaining_quantity=quantity,
                    price=price,
                    is_wholesale=is_wholesale,
                    lot_sizes=lot_sizes,
                    notes=notes,
                    status=OfferStatus.ACTIVE
                )
                session.add(new_offer)
                await session.commit()
                await session.refresh(new_offer)
                offer_id = new_offer.id
            
            # ساخت دکمه‌های معامله
            from bot.callbacks import ChannelTradeCallback, ExpireOfferCallback
            
            if is_wholesale or not lot_sizes:
                trade_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"{quantity} عدد",
                        callback_data=ChannelTradeCallback(offer_id=offer_id, amount=quantity).pack()
                    )]
                ])
            else:
                buttons = []
                all_amounts = get_available_trade_amounts(
                    quantity=quantity,
                    remaining_quantity=quantity,
                    is_wholesale=False,
                    lot_sizes=sorted(lot_sizes, reverse=True),
                )
                seen = set()
                unique_amounts = []
                for a in all_amounts:
                    if a not in seen:
                        seen.add(a)
                        unique_amounts.append(a)
                
                for amount in unique_amounts:
                    buttons.append(InlineKeyboardButton(
                        text=f"{amount} عدد",
                        callback_data=ChannelTradeCallback(offer_id=offer_id, amount=amount).pack()
                    ))
                trade_keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
            
            sent_msg = await bot.send_message(
                chat_id=settings.channel_id,
                text=channel_message,
                reply_markup=trade_keyboard
            )
            
            async with AsyncSessionLocal() as session:
                offer = await session.get(Offer, offer_id)
                if offer:
                    offer.channel_message_id = sent_msg.message_id
                    await session.commit()
                
                # افزایش شمارنده لفظ‌های ارسالی
                from core.utils import increment_user_counter
                db_user = await session.get(User, user.id)
                if db_user:
                    await increment_user_counter(session, db_user, 'channel_message')
            
            await callback.message.edit_text(
                "✅ لفظ شما با موفقیت در کانال منتشر شد!",
                parse_mode="Markdown"
            )
            
            # پیام لفظ با دکمه منقضی کردن
            offer_preview = (
                f"**لفظ شما:**\n\n"
                f"{channel_message}"
            )
            expire_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ منقضی کردن", callback_data=ExpireOfferCallback(offer_id=offer_id).pack())]
            ])
            await bot.send_message(
                chat_id=callback.from_user.id,
                text=offer_preview,
                parse_mode="Markdown",
                reply_markup=expire_keyboard
            )
            
        except Exception as e:
            # ===== Rollback =====
            try:
                # اگر offer ذخیره شده بود، منقضی‌اش کن
                if 'offer_id' in locals():
                     async with AsyncSessionLocal() as session:
                        offer = await session.get(Offer, offer_id)
                        if offer:
                            offer.status = OfferStatus.EXPIRED
                            await session.commit()
            except Exception as rollback_error:
                logger.debug(f"Rollback failed: {rollback_error}")
            
            await callback.message.edit_text(f"❌ خطا در ارسال به کانال: {str(e)}")
    else:
        await callback.message.edit_text("❌ کانال تنظیم نشده است.")
    
    await state.clear()
    await callback.answer()


@router.callback_query(Trade.awaiting_text_confirm, TextOfferActionCallback.filter(F.action == "cancel"))
async def handle_text_offer_cancel(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    """انصراف از لفظ متنی"""
    await callback.message.edit_text("❌ لفظ لغو شد.")
    await state.clear()
    await callback.answer()

