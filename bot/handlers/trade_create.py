from aiogram import Router, F, types, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from typing import Optional

from models.user import User
from models.customer import Customer
from models.offer import Offer, OfferType, OfferStatus
from models.commodity import Commodity
from bot.states import Trade
from core.config import settings
from core.enums import UserRole
from core.db import AsyncSessionLocal
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
    ACTION_NOOP
)

# Import shared validation functions
from core.services.trade_service import (
    validate_lot_sizes,
    validate_quantity,
    validate_price
)
from datetime import datetime
from core.utils import to_jalali_str, check_user_limits, increment_user_counter

router = Router()

@router.message(F.text == "📈 معامله")
async def handle_trade_button(message: types.Message, state: FSMContext, user: Optional[User]):
    """شروع فرآیند معامله"""
    if not user:
        return
    
    # بررسی نقش کاربر
    if user.role == UserRole.WATCH:
        await message.answer("⛔️ شما دسترسی به بخش معاملات را ندارید.")
        return
    
    # بررسی مسدودیت
    if user.trading_restricted_until:
        now = datetime.utcnow()
        if user.trading_restricted_until > now:
            # محاسبه زمان باقیمانده
            remaining = user.trading_restricted_until - now
            total_seconds = int(remaining.total_seconds())
            
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            
            # فرمت dd:hh:mm
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            
            # تاریخ انقضا به شمسی
            expiry_jalali = to_jalali_str(user.trading_restricted_until, "%Y/%m/%d - %H:%M")
            
            await message.answer(
                f"⛔️ **حساب شما مسدود است**\n\n"
                f"📅 تاریخ رفع مسدودیت: {expiry_jalali}\n"
                f"⏳ زمان باقی‌مانده: {countdown}\n\n"
                f"تا رفع مسدودیت امکان انتشار لفظ در کانال را ندارید.",
                parse_mode="Markdown"
            )
            return
    
    # پاک کردن state قبلی
    await state.clear()
    
    # نمایش انتخاب نوع معامله
    await message.answer(
        "📈 **ثبت لفظ جدید**\n\nنوع معامله را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=get_trade_type_keyboard()
    )


# --- انتخاب نوع معامله ---
# --- انتخاب نوع معامله ---
@router.callback_query(TradeTypeCallback.filter())
async def handle_trade_type_selection(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], callback_data: TradeTypeCallback):
    if not user:
        return
    
    trade_type = callback_data.type
    trade_type_fa = "🟢 خرید" if trade_type == "buy" else "🔴 فروش"
    
    await state.update_data(trade_type=trade_type, trade_type_fa=trade_type_fa)
    
    keyboard = await get_commodities_keyboard(trade_type)
    
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n\n"
        f"کالای مورد نظر را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback.answer()


# --- صفحه‌بندی کالاها ---
@router.callback_query(PageCallback.filter())
async def handle_commodity_page(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], callback_data: PageCallback):
    if not user:
        return
    
    trade_type = callback_data.trade_type
    page = callback_data.page

    data = await state.get_data()
    trade_type_fa = "🟢 خرید" if trade_type == "buy" else "🔴 فروش"
    
    keyboard = await get_commodities_keyboard(trade_type, page=page)
    
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n\n"
        f"کالای مورد نظر را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback.answer()


# --- انتخاب کالا ---
@router.callback_query(CommodityCallback.filter())
async def handle_commodity_selection(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], callback_data: CommodityCallback):
    if not user:
        return
    
    commodity_id = callback_data.id
    
    # گرفتن اطلاعات کالا
    async with AsyncSessionLocal() as session:
        stmt = select(Commodity).where(Commodity.id == commodity_id)
        result = await session.execute(stmt)
        commodity = result.scalar_one_or_none()
    
    if not commodity:
        await callback.answer("❌ کالا یافت نشد!", show_alert=True)
        return
    
    data = await state.get_data()
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    
    await state.update_data(
        commodity_id=commodity_id,
        commodity_name=commodity.name
    )
    
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n"
        f"کالا: {commodity.name}\n\n"
        f"تعداد را انتخاب کنید یا عدد دلخواه را وارد کنید:",
        parse_mode="Markdown",
        reply_markup=get_quantity_keyboard()
    )
    
    await state.set_state(Trade.awaiting_quantity)
    await callback.answer()


# --- انتخاب سریع تعداد ---
@router.callback_query(Trade.awaiting_quantity, QuantityCallback.filter())
async def handle_quick_quantity(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], callback_data: QuantityCallback):
    if not user:
        return
    
    value = callback_data.value
    
    if value == "manual":
        await callback.message.answer("✏️ لطفاً تعداد مورد نظر را به عدد وارد کنید:")
        await callback.answer()
        return

    quantity = int(value)
    
    data = await state.get_data()
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    commodity_name = data.get("commodity_name", "نامشخص")
    
    await state.update_data(quantity=quantity)
    
    # پرسش یکجا یا خُرد
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n"
        f"کالا: {commodity_name}\n"
        f"تعداد: {quantity}\n\n"
        f"📦 نحوه معامله را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=get_lot_type_keyboard()
    )
    
    await state.set_state(Trade.awaiting_lot_type)
    await callback.answer()


# --- ورود دستی تعداد ---
@router.message(Trade.awaiting_quantity)
async def handle_manual_quantity(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    from core.trading_settings import get_trading_settings
    ts = get_trading_settings()
    
    try:
        quantity = int(message.text.strip())
        if quantity <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح مثبت وارد کنید.")
        return
    
    # اعتبارسنجی حداقل و حداکثر
    if quantity < ts.offer_min_quantity:
        await message.answer(f"❌ حداقل تعداد باید {ts.offer_min_quantity} باشد.")
        return
    
    if quantity > ts.offer_max_quantity:
        await message.answer(f"❌ حداکثر تعداد می‌تواند {ts.offer_max_quantity} باشد.")
        return
    
    data = await state.get_data()
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    commodity_name = data.get("commodity_name", "نامشخص")
    
    await state.update_data(quantity=quantity)
    
    msg = await message.answer(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n"
        f"کالا: {commodity_name}\n"
        f"تعداد: {quantity}\n\n"
        f"📦 نحوه معامله را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=get_lot_type_keyboard()
    )
    
    await state.set_state(Trade.awaiting_lot_type)


# --- انتخاب یکجا ---
@router.callback_query(Trade.awaiting_lot_type, LotTypeCallback.filter(F.type == "wholesale"))
async def handle_lot_wholesale(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    
    await state.update_data(is_wholesale=True, lot_sizes=None)
    
    await callback.message.edit_text(
        "💰 قیمت را وارد کنید (5 یا 6 رقم):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
        ])
    )
    
    await state.set_state(Trade.awaiting_price)
    await callback.answer()


# --- انتخاب خُرد ---
@router.callback_query(Trade.awaiting_lot_type, LotTypeCallback.filter(F.type == "retail")) 
async def handle_lot_split(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    
    data = await state.get_data()
    quantity = data.get("quantity", 1)
    
    await state.update_data(is_wholesale=False)
    
    await callback.message.edit_text(
        f"🔢 ترکیب بخش‌ها را با فاصله وارد کنید:\n"
        f"(مثال: 10 15 25)\n\n"
        f"⚠️ جمع باید برابر {quantity} باشد\n"
        f"⚠️ هر بخش حداقل 5 عدد\n"
        f"⚠️ حداکثر 3 بخش",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
        ])
    )
    
    await state.set_state(Trade.awaiting_lot_sizes)
    await callback.answer()


# --- ورود ترکیب بخش‌ها ---
@router.message(Trade.awaiting_lot_sizes)
async def handle_lot_sizes_input(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    data = await state.get_data()
    quantity = data.get("quantity", 1)
    
    try:
        lot_sizes = [int(x.strip()) for x in message.text.strip().split()]
        if not lot_sizes:
            raise ValueError()
    except ValueError:
        await message.answer("❌ لطفاً اعداد را با فاصله وارد کنید (مثال: 10 15 25)")
        return
    
    is_valid, error_msg, suggested = validate_lot_sizes(quantity, lot_sizes)
    
    if not is_valid:
        keyboard = None
        if suggested:
            suggested_str = " ".join(map(str, suggested))
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"✅ قبول: {suggested_str}", callback_data=AcceptLotsCallback(lots=suggested_str.replace(' ', '_')).pack())],
                [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
            ])
        await message.answer(error_msg, reply_markup=keyboard)
        return
    
    lot_sizes = sorted(lot_sizes, reverse=True)
    await state.update_data(lot_sizes=lot_sizes)
    
    await message.answer(
        "💰 قیمت را وارد کنید (5 یا 6 رقم):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
        ])
    )
    
    await state.set_state(Trade.awaiting_price)


# --- قبول پیشنهاد ترکیب ---
@router.callback_query(AcceptLotsCallback.filter())
async def handle_accept_suggested_lots(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], callback_data: AcceptLotsCallback):
    if not user:
        await callback.answer()
        return
    
    lots_str = callback_data.lots
    lot_sizes = [int(x) for x in lots_str.split("_")]
    
    await state.update_data(lot_sizes=lot_sizes)
    
    await callback.message.edit_text(
        "💰 قیمت را وارد کنید (5 یا 6 رقم):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
        ])
    )
    
    await state.set_state(Trade.awaiting_price)
    await callback.answer()

# --- ورود قیمت ---
@router.message(Trade.awaiting_price)
async def handle_price_input(message: types.Message, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    price_text = message.text.strip()
    
    # اعتبارسنجی: فقط 5 یا 6 رقم
    if not price_text.isdigit() or len(price_text) not in [5, 6]:
        await message.answer("❌ قیمت باید 5 یا 6 رقم باشد (مثال: 75800 یا 758000)")
        return
    
    price = int(price_text)
    await state.update_data(price=price)
    
    # پرسش توضیحات
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ بدون توضیحات", callback_data=SkipNotesCallback(target="notes").pack())]
    ])
    
    await message.answer(
        "📝 **توضیحات یا شرایط (اختیاری)**\n\n"
        "اگر شرایط یا توضیحات خاصی دارید وارد کنید.\n"
        "مثال: فقط نقدی، حداقل 10 عدد، ...\n\n"
        "_حداکثر 200 کاراکتر_",
        parse_mode="Markdown",
        reply_markup=skip_kb
    )
    await state.set_state(Trade.awaiting_notes)


# --- پرش از توضیحات ---
@router.callback_query(Trade.awaiting_notes, SkipNotesCallback.filter(F.target == "notes"))
async def handle_skip_notes(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    
    await state.update_data(notes=None)
    await show_trade_preview(callback.message, state, edit=True)
    await callback.answer()


# --- ورود توضیحات ---
@router.message(Trade.awaiting_notes)
async def handle_notes_input(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    notes = message.text.strip()
    
    if len(notes) > 200:
        await message.answer("❌ توضیحات نباید بیش از 200 کاراکتر باشد.")
        return
    
    await state.update_data(notes=notes)
    await show_trade_preview(message, state, edit=False)


# --- نمایش پیش‌نمایش ---
async def show_trade_preview(message_or_callback, state: FSMContext, edit: bool = False):
    """نمایش پیش‌نمایش لفظ قبل از تایید"""
    data = await state.get_data()
    trade_type = data.get("trade_type", "buy")
    commodity_name = data.get("commodity_name", "نامشخص")
    quantity = data.get("quantity", 1)
    price = data.get("price", 0)
    notes = data.get("notes")
    
    # ساخت متن شبیه کانال
    trade_emoji = "🟢" if trade_type == "buy" else "🔴"
    trade_label = "خرید" if trade_type == "buy" else "فروش"
    # کاراکتر نامرئی (Braille Blank) برای افزایش عرض بدون نمایش
    invisible_padding = "\u2800" * 35
    channel_text = f"{trade_emoji}{trade_label} {commodity_name} {quantity} عدد {price:,}"
    
    if notes:
        channel_text += f"\nتوضیحات: {notes}"
    channel_text += f"\n{invisible_padding}"
    
    preview = (
        f"**لفظ شما:**\n\n"
        f"{channel_text}\n\n"
        f"آیا تایید می‌کنید?"
    )
    
    if edit:
        await message_or_callback.edit_text(
            preview,
            parse_mode="Markdown",
            reply_markup=get_confirm_keyboard()
        )
    else:
        await message_or_callback.answer(
            preview,
            parse_mode="Markdown",
            reply_markup=get_confirm_keyboard()
        )


# ============================================
# PREVIEW & CONFIRM
# ============================================
@router.callback_query(TradeActionCallback.filter(F.action == "confirm"))
async def handle_trade_confirm(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    from core.trading_settings import get_trading_settings
    
    ts = get_trading_settings()
    
    # بررسی محدودیت‌های کاربر (لفظ، کالا، معامله)
    data = await state.get_data()
    quantity = data.get("quantity", 1)
    
    # بررسی محدودیت ارسال لفظ
    allowed, error_msg = check_user_limits(user, 'channel_message')
    if not allowed:
        # نمایش پیام با زمان باقی‌مانده
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
    
    # بررسی تعداد لفظ‌های فعال (تنظیمات عمومی)
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
    
    data = await state.get_data()
    trade_type = data.get("trade_type", "buy")
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    commodity_name = data.get("commodity_name", "نامشخص")
    quantity = data.get("quantity", 1)
    price = data.get("price", 0)
    commodity_id = data.get("commodity_id")
    is_wholesale = data.get("is_wholesale", True)
    lot_sizes = data.get("lot_sizes", None)
    notes = data.get("notes", None)
    
    # ساخت پیام کانال - فرمت مختصر (بدون نام کاربر)
    trade_emoji = "🟢" if trade_type == "buy" else "🔴"
    trade_label = "خرید" if trade_type == "buy" else "فروش"
    
    # کاراکتر نامرئی (Braille Blank) برای افزایش عرض بدون نمایش
    invisible_padding = "\u2800" * 35
    
    channel_message = f"{trade_emoji}{trade_label} {commodity_name} {quantity} عدد {price:,}"
    if notes:
        channel_message += f"\nتوضیحات: {notes}"
    channel_message += f"\n{invisible_padding}"
    
    # ارسال به کانال
    if settings.channel_id:
        try:
            # ذخیره لفظ در دیتابیس اول برای گرفتن offer_id
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
            
            # ساخت دکمه‌های معامله برای کانال
             # Import ChannelTradeCallback locally or use import from top
            from bot.callbacks import ChannelTradeCallback, ExpireOfferCallback
             
            if is_wholesale or not lot_sizes:
                # یکجا - فقط یک دکمه
                trade_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"{quantity} عدد",
                        callback_data=ChannelTradeCallback(offer_id=offer_id, amount=quantity).pack()
                    )]
                ])
            else:
                # خُرد - چند دکمه
                # ترتیب: کل + بخش‌ها (بزرگ به کوچک)
                buttons = []
                all_amounts = [quantity] + sorted(lot_sizes, reverse=True)
                # حذف تکراری‌ها با حفظ ترتیب
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
                
                # دکمه‌ها در یک ردیف
                trade_keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
            
            sent_msg = await bot.send_message(
                chat_id=settings.channel_id,
                text=channel_message,
                parse_mode="Markdown",
                reply_markup=trade_keyboard
            )
            
            # بروزرسانی channel_message_id و افزایش شمارنده
            async with AsyncSessionLocal() as session:
                offer = await session.get(Offer, offer_id)
                if offer:
                    offer.channel_message_id = sent_msg.message_id
                    await session.commit()
                
                # افزایش شمارنده لفظ‌های ارسالی
                db_user = await session.get(User, user.id)
                if db_user:
                    await increment_user_counter(session, db_user, 'channel_message')
            
            # پیام موفقیت
            await callback.message.edit_text(
                "✅ لفظ شما با موفقیت در کانال ارسال شد!",
                parse_mode="Markdown"
            )
            
            # پیام لفظ با دکمه منقضی شدن
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
            
        except TelegramBadRequest as e:
            # ===== Rollback: منقضی کردن لفظ اگر ارسال به کانال شکست خورد =====
            async with AsyncSessionLocal() as session:
                offer = await session.get(Offer, offer_id)
                if offer:
                    offer.status = OfferStatus.EXPIRED
                    await session.commit()
            
            await callback.message.edit_text(
                f"❌ خطا در ارسال به کانال: {e.message}\n\n"
                f"لفظ ثبت نشد. لطفاً دوباره تلاش کنید.",
                parse_mode="Markdown"
            )
        except Exception as e:
            # ===== Rollback برای خطاهای غیر منتظره =====
            async with AsyncSessionLocal() as session:
                offer = await session.get(Offer, offer_id)
                if offer:
                    offer.status = OfferStatus.EXPIRED
                    await session.commit()
            
            await callback.message.edit_text(
                f"❌ خطا در ارسال به کانال\n\n"
                f"لفظ ثبت نشد. لطفاً دوباره تلاش کنید.",
                parse_mode="Markdown"
            )
    else:
        await callback.message.edit_text(
            "❌ کانال تنظیم نشده است. با مدیر تماس بگیرید.",
            parse_mode="Markdown"
        )
    
    await state.clear()
    await callback.answer()


# --- بازگشت به انتخاب نوع معامله ---
@router.callback_query(TradeActionCallback.filter(F.action == "back_to_type"))
async def handle_back_to_type(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    await state.clear()
    
    await callback.message.edit_text(
        "📈 **ثبت لفظ جدید**\n\nنوع معامله را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=get_trade_type_keyboard()
    )
    await callback.answer()


# --- انصراف ---
@router.callback_query(TradeActionCallback.filter(F.action == "cancel"))
async def handle_trade_cancel(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    await state.clear()
    
    await callback.message.edit_text(
        "❌ فرآیند معامله لغو شد.",
        parse_mode="Markdown"
    )
    await callback.answer()


# --- هندلر noop برای دکمه‌های غیرفعال ---
@router.callback_query(F.data == ACTION_NOOP)
async def handle_noop(callback: types.CallbackQuery):
    await callback.answer()

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
                all_amounts = [quantity] + sorted(lot_sizes, reverse=True)
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
            except:
                pass
            
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

