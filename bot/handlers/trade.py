# bot/handlers/trade.py
"""
هندلرهای مربوط به ثبت لفظ معاملاتی

=== بخش‌بندی فایل ===
خط 1-50:     Imports و Constants
خط 50-150:   Utility Functions (کیبوردها، اعتبارسنجی)
خط 150-400:  Button Flow Handlers (ثبت لفظ با دکمه)
خط 400-700:  Preview & Confirm (پیش‌نمایش و تایید)
خط 700-900:  Offer Management (منقضی کردن، مدیریت)
خط 900-1150: Channel Trade Handlers (معاملات کانال)
خط 1150-1400: Text Offer Handler (لفظ متنی)
==============================

برای refactoring آینده، هر بخش را می‌توان به فایل جداگانه منتقل کرد.
"""

# ============================================
# IMPORTS
# ============================================
from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from typing import Optional

from models.user import User
from models.commodity import Commodity
from models.offer import Offer, OfferType, OfferStatus
from bot.states import Trade
from core.config import settings
from core.enums import UserRole
from core.db import AsyncSessionLocal

# ============================================
# ROUTER
# ============================================
router = Router()


# ============================================
# SECTION 1: UTILITY FUNCTIONS
# کیبوردها، اعتبارسنجی، توابع کمکی
# ============================================

def get_trade_type_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب نوع معامله (خرید/فروش)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 خرید", callback_data="trade_type_buy"),
            InlineKeyboardButton(text="🔴 فروش", callback_data="trade_type_sell")
        ],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
    ])


def get_lot_type_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب یکجا یا خُرد"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 یکجا", callback_data="lot_type_wholesale"),
            InlineKeyboardButton(text="🔢 خُرد", callback_data="lot_type_split")
        ],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
    ])


def suggest_lot_combination(total: int, user_lots: list[int]) -> list[int]:
    """پیشنهاد ترکیب بهینه اگر ترکیب کاربر درست نباشد"""
    from core.trading_settings import get_trading_settings
    settings = get_trading_settings()
    MIN_LOT = settings.lot_min_size
    MAX_LOTS = settings.lot_max_count
    
    # اگر جمع کمتر است، بزرگترین عدد را افزایش بده
    user_sum = sum(user_lots)
    diff = total - user_sum
    
    if diff == 0:
        return user_lots
    
    suggested = sorted(user_lots, reverse=True)
    
    if diff > 0:
        # کمبود - به بزرگترین اضافه کن
        suggested[0] += diff
    else:
        # اضافه - از بزرگترین کم کن
        for i in range(len(suggested)):
            reduction = min(suggested[i] - MIN_LOT, -diff)
            if reduction > 0:
                suggested[i] -= reduction
                diff += reduction
            if diff == 0:
                break
    
    # فیلتر کردن موارد کوچکتر از MIN_LOT
    suggested = [x for x in suggested if x >= MIN_LOT]
    
    # اگر هنوز جمع نمی‌شود، None بده
    if sum(suggested) != total:
        return None
    
    return sorted(suggested, reverse=True)


def validate_lot_sizes(total: int, lot_sizes: list[int]) -> tuple[bool, str, list[int]]:
    """اعتبارسنجی ترکیب لات‌ها
    Returns: (is_valid, error_message, suggested_lots)
    """
    from core.trading_settings import get_trading_settings
    settings = get_trading_settings()
    MIN_LOT = settings.lot_min_size
    MAX_LOTS = settings.lot_max_count
    
    if len(lot_sizes) > MAX_LOTS:
        return False, f"❌ حداکثر {MAX_LOTS} بخش مجاز است.", None
    
    for lot in lot_sizes:
        if lot < MIN_LOT:
            return False, f"❌ هر بخش باید حداقل {MIN_LOT} عدد باشد.", None
    
    lot_sum = sum(lot_sizes)
    
    if lot_sum != total:
        suggested = suggest_lot_combination(total, lot_sizes)
        if suggested:
            return False, f"❌ جمع ترکیب ({lot_sum}) با کل ({total}) برابر نیست.\n\n💡 پیشنهاد: {' '.join(map(str, suggested))}", suggested
        else:
            return False, f"❌ جمع ترکیب ({lot_sum}) با کل ({total}) برابر نیست.", None
    
    return True, "", lot_sizes



async def get_commodities_keyboard(trade_type: str, page: int = 1, limit: int = 9) -> InlineKeyboardMarkup:
    """کیبورد لیست کالاها با pagination"""
    async with AsyncSessionLocal() as session:
        # شمارش کل کالاها
        count_stmt = select(Commodity)
        result = await session.execute(count_stmt)
        all_commodities = result.scalars().all()
        total_count = len(all_commodities)
        
        # محاسبه offset
        offset = (page - 1) * limit
        
        # گرفتن کالاهای صفحه فعلی
        stmt = select(Commodity).order_by(Commodity.name).offset(offset).limit(limit)
        result = await session.execute(stmt)
        commodities = result.scalars().all()
    
    keyboard_rows = []
    
    # دکمه‌های کالا (3 در هر ردیف)
    commodity_buttons = []
    for commodity in commodities:
        commodity_buttons.append(
            InlineKeyboardButton(
                text=commodity.name,
                callback_data=f"trade_commodity_{commodity.id}"
            )
        )
    
    # تقسیم به ردیف‌های 3 تایی
    for i in range(0, len(commodity_buttons), 3):
        keyboard_rows.append(commodity_buttons[i:i+3])
    
    # دکمه‌های pagination
    total_pages = (total_count + limit - 1) // limit
    if total_pages > 1:
        pagination_row = []
        if page > 1:
            pagination_row.append(InlineKeyboardButton(text="➡️ قبلی", callback_data=f"trade_page_{page-1}"))
        pagination_row.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            pagination_row.append(InlineKeyboardButton(text="⬅️ بعدی", callback_data=f"trade_page_{page+1}"))
        keyboard_rows.append(pagination_row)
    
    # دکمه‌های کنترل
    keyboard_rows.append([
        InlineKeyboardButton(text="🔙 بازگشت", callback_data="trade_back_to_type"),
        InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_quantity_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب سریع تعداد"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10", callback_data="trade_qty_10"),
            InlineKeyboardButton(text="20", callback_data="trade_qty_20"),
            InlineKeyboardButton(text="30", callback_data="trade_qty_30"),
            InlineKeyboardButton(text="40", callback_data="trade_qty_40"),
            InlineKeyboardButton(text="50", callback_data="trade_qty_50"),
        ],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
    ])


def get_confirm_keyboard() -> InlineKeyboardMarkup:
    """کیبورد تایید نهایی"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تایید و ارسال", callback_data="trade_confirm"),
            InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")
        ]
    ])


# ============================================
# SECTION 2: BUTTON FLOW HANDLERS
# ثبت لفظ با دکمه - فلوی اصلی
# ============================================

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
        from datetime import datetime
        if user.trading_restricted_until > datetime.utcnow():
            await message.answer("⛔️ حساب شما مسدود است و امکان معامله ندارید.")
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
@router.callback_query(F.data.in_(["trade_type_buy", "trade_type_sell"]))
async def handle_trade_type_selection(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    trade_type = "buy" if callback.data == "trade_type_buy" else "sell"
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
@router.callback_query(F.data.startswith("trade_page_"))
async def handle_commodity_page(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    page = int(callback.data.split("_")[-1])
    data = await state.get_data()
    trade_type = data.get("trade_type", "buy")
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    
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
@router.callback_query(F.data.startswith("trade_commodity_"))
async def handle_commodity_selection(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    commodity_id = int(callback.data.split("_")[-1])
    
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
@router.callback_query(Trade.awaiting_quantity, F.data.startswith("trade_qty_"))
async def handle_quick_quantity(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    quantity = int(callback.data.split("_")[-1])
    
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
    
    # پرسش یکجا یا خُرد
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
@router.callback_query(Trade.awaiting_lot_type, F.data == "lot_type_wholesale")
async def handle_lot_wholesale(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    
    data = await state.get_data()
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    commodity_name = data.get("commodity_name", "نامشخص")
    quantity = data.get("quantity", 1)
    
    await state.update_data(is_wholesale=True, lot_sizes=None)
    
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n"
        f"کالا: {commodity_name}\n"
        f"تعداد: {quantity} (یکجا)\n\n"
        f"💰 قیمت را وارد کنید (5 یا 6 رقم):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
        ])
    )
    
    await state.set_state(Trade.awaiting_price)
    await callback.answer()


# --- انتخاب خُرد ---
@router.callback_query(Trade.awaiting_lot_type, F.data == "lot_type_split")
async def handle_lot_split(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    
    data = await state.get_data()
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    commodity_name = data.get("commodity_name", "نامشخص")
    quantity = data.get("quantity", 1)
    
    await state.update_data(is_wholesale=False)
    
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n"
        f"کالا: {commodity_name}\n"
        f"تعداد کل: {quantity}\n\n"
        f"🔢 ترکیب بخش‌ها را با فاصله وارد کنید:\n"
        f"(مثال: 10 15 25)\n\n"
        f"⚠️ جمع باید برابر {quantity} باشد\n"
        f"⚠️ هر بخش حداقل 5 عدد\n"
        f"⚠️ حداکثر 3 بخش",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
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
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    commodity_name = data.get("commodity_name", "نامشخص")
    
    # پارس کردن ورودی
    try:
        lot_sizes = [int(x.strip()) for x in message.text.strip().split()]
        if not lot_sizes:
            raise ValueError()
    except ValueError:
        await message.answer("❌ لطفاً اعداد را با فاصله وارد کنید (مثال: 10 15 25)")
        return
    
    # اعتبارسنجی
    is_valid, error_msg, suggested = validate_lot_sizes(quantity, lot_sizes)
    
    if not is_valid:
        keyboard = None
        if suggested:
            # دکمه برای پذیرش پیشنهاد
            suggested_str = " ".join(map(str, suggested))
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"✅ قبول: {suggested_str}", callback_data=f"accept_lots_{suggested_str.replace(' ', '_')}")],
                [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
            ])
        await message.answer(error_msg, reply_markup=keyboard)
        return
    
    # ذخیره و رفتن به قیمت
    lot_sizes = sorted(lot_sizes, reverse=True)
    await state.update_data(lot_sizes=lot_sizes)
    
    lots_display = " + ".join(map(str, lot_sizes))
    await message.answer(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n"
        f"کالا: {commodity_name}\n"
        f"تعداد: {quantity} (ترکیب: {lots_display})\n\n"
        f"💰 قیمت را وارد کنید (5 یا 6 رقم):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
        ])
    )
    
    await state.set_state(Trade.awaiting_price)


# --- قبول پیشنهاد ترکیب ---
@router.callback_query(F.data.startswith("accept_lots_"))
async def handle_accept_suggested_lots(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    
    lots_str = callback.data.replace("accept_lots_", "")
    lot_sizes = [int(x) for x in lots_str.split("_")]
    
    data = await state.get_data()
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    commodity_name = data.get("commodity_name", "نامشخص")
    quantity = data.get("quantity", 1)
    
    await state.update_data(lot_sizes=lot_sizes)
    
    lots_display = " + ".join(map(str, lot_sizes))
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n"
        f"کالا: {commodity_name}\n"
        f"تعداد: {quantity} (ترکیب: {lots_display})\n\n"
        f"💰 قیمت را وارد کنید (5 یا 6 رقم):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
        ])
    )
    
    await state.set_state(Trade.awaiting_price)
    await callback.answer("✅ ترکیب پذیرفته شد")



# --- ورود قیمت ---
@router.message(Trade.awaiting_price)
async def handle_price_input(message: types.Message, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    price_text = message.text.strip()
    
    # اعتبارسنجی: فقط 5 یا 6 رقم
    if not price_text.isdigit() or len(price_text) not in [5, 6]:
        err_msg = await message.answer("❌ قیمت باید 5 یا 6 رقم باشد (مثال: 75800 یا 758000)")
        return
    
    price = int(price_text)
    await state.update_data(price=price)
    
    # پرسش توضیحات
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ بدون توضیحات", callback_data="skip_notes")]
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
@router.callback_query(Trade.awaiting_notes, F.data == "skip_notes")
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
# SECTION 3: PREVIEW & CONFIRM
# پیش‌نمایش و تایید لفظ - ارسال به کانال
# ============================================
@router.callback_query(F.data == "trade_confirm")
async def handle_trade_confirm(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    from core.trading_settings import get_trading_settings
    ts = get_trading_settings()
    
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
            if is_wholesale or not lot_sizes:
                # یکجا - فقط یک دکمه
                trade_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"{quantity} عدد",
                        callback_data=f"channel_trade_{offer_id}_{quantity}"
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
                        callback_data=f"channel_trade_{offer_id}_{amount}"
                    ))
                
                # دکمه‌ها در یک ردیف
                trade_keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
            
            sent_msg = await bot.send_message(
                chat_id=settings.channel_id,
                text=channel_message,
                parse_mode="Markdown",
                reply_markup=trade_keyboard
            )
            
            # بروزرسانی channel_message_id
            async with AsyncSessionLocal() as session:
                offer = await session.get(Offer, offer_id)
                if offer:
                    offer.channel_message_id = sent_msg.message_id
                    await session.commit()
            
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
                [InlineKeyboardButton(text="❌ منقضی کردن", callback_data=f"expire_offer_{offer_id}")]
            ])
            await bot.send_message(
                chat_id=callback.from_user.id,
                text=offer_preview,
                parse_mode="Markdown",
                reply_markup=expire_keyboard
            )
            
        except TelegramBadRequest as e:
            await callback.message.edit_text(
                f"❌ خطا در ارسال به کانال: {e.message}",
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
@router.callback_query(F.data == "trade_back_to_type")
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
@router.callback_query(F.data == "trade_cancel")
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
@router.callback_query(F.data == "noop")
async def handle_noop(callback: types.CallbackQuery):
    await callback.answer()


# ============================================
# SECTION 4: OFFER MANAGEMENT
# مدیریت لفظ - منقضی کردن، آمار
# ============================================

# ذخیره آمار منقضی شدن لفظ
_expire_rate_tracker: dict[int, list[float]] = {}  # user_id -> list of timestamps
_daily_expire_tracker: dict[int, dict] = {}  # user_id -> {"date": date, "count": int, "total_offers": int}


@router.callback_query(F.data.startswith("expire_offer_"))
async def handle_expire_offer(callback: types.CallbackQuery, user: Optional[User], bot: Bot):
    if not user:
        await callback.answer()
        return
    
    import time
    from datetime import date
    from core.trading_settings import get_trading_settings
    
    ts = get_trading_settings()
    current_time = time.time()
    today = date.today()
    
    # بررسی محدودیت در دقیقه
    if user.id not in _expire_rate_tracker:
        _expire_rate_tracker[user.id] = []
    
    # حذف timestampهای قدیمی‌تر از 1 دقیقه
    _expire_rate_tracker[user.id] = [t for t in _expire_rate_tracker[user.id] if current_time - t < 60]
    
    if len(_expire_rate_tracker[user.id]) >= ts.offer_expire_rate_per_minute:
        await callback.answer(f"❌ حداکثر {ts.offer_expire_rate_per_minute} منقضی در دقیقه مجاز است")
        return
    
    # بررسی محدودیت روزانه (1/3 لفظ‌ها بعد از آستانه)
    if user.id not in _daily_expire_tracker or _daily_expire_tracker[user.id]["date"] != today:
        _daily_expire_tracker[user.id] = {"date": today, "count": 0, "total_offers": 0}
    
    daily_data = _daily_expire_tracker[user.id]
    
    # شمارش کل لفظ‌های امروز
    async with AsyncSessionLocal() as session:
        from sqlalchemy import func
        from datetime import datetime, timedelta
        start_of_day = datetime.combine(today, datetime.min.time())
        
        total_offers_today = await session.scalar(
            select(func.count(Offer.id)).where(
                Offer.user_id == user.id,
                Offer.created_at >= start_of_day
            )
        )
        daily_data["total_offers"] = total_offers_today or 0
    
    # اگر از آستانه رد شده و 1/3 را استفاده کرده
    threshold = ts.offer_expire_daily_limit_after_threshold
    if daily_data["count"] >= threshold:
        max_allowed = daily_data["total_offers"] // 3
        if daily_data["count"] >= max_allowed:
            await callback.answer(
                f"❌ شما امروز {daily_data['count']} لفظ منقضی کرده‌اید.\n"
                f"برای منقضی کردن بیشتر، باید لفظ‌های جدید ثبت کنید."
            )
            return
    
    offer_id = int(callback.data.split("_")[-1])
    
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
        
        # ثبت آمار
        _expire_rate_tracker[user.id].append(current_time)
        _daily_expire_tracker[user.id]["count"] += 1
        
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


# --- ذخیره وضعیت تایید (کلیک اول) ---
_pending_confirmations: dict[tuple[int, int, int], float] = {}  # (user_id, offer_id, amount) -> timestamp


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
            callback_data=f"channel_trade_{offer_id}_{amount}"
        ))
    
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None


# ============================================
# SECTION 5: CHANNEL TRADE HANDLERS
# معاملات کانال - پردازش کلیک روی پست‌ها
# ============================================

@router.callback_query(F.data.startswith("channel_trade_"))
async def handle_channel_trade(callback: types.CallbackQuery, user: Optional[User], bot: Bot):
    """کلیک روی دکمه پست کانال - دابل‌کلیک برای تایید"""
    import time
    
    if not user:
        await callback.answer()
        return
    
    from models.trade import Trade, TradeType, TradeStatus
    from sqlalchemy.orm import joinedload
    
    # پارس callback_data: channel_trade_{offer_id}_{amount}
    parts = callback.data.split("_")
    offer_id = int(parts[2])
    trade_amount = int(parts[3]) if len(parts) > 3 else None
    
    confirmation_key = (user.id, offer_id, trade_amount or 0)
    
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
        
        # تعداد واقعی معامله
        actual_amount = trade_amount or offer.remaining_quantity or offer.quantity
        
        # بررسی اینکه تعداد درخواستی از باقیمانده بیشتر نباشد
        remaining = offer.remaining_quantity or offer.quantity
        if actual_amount > remaining:
            await callback.answer("❌ این تعداد دیگر موجود نیست")
            return
        
        # بررسی دابل‌کلیک (0.5 ثانیه)
        current_time = time.time()
        last_click = _pending_confirmations.get(confirmation_key, 0)
        
        if current_time - last_click < 0.5:  # نیم ثانیه
            # دابل‌کلیک - انجام معامله
            if confirmation_key in _pending_confirmations:
                del _pending_confirmations[confirmation_key]
            
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
            
            # اطلاعات معامله
            offer_type_fa = "خرید" if offer.offer_type == OfferType.BUY else "فروش"
            respond_type_fa = "فروش" if offer.offer_type == OfferType.BUY else "خرید"
            offer_emoji = "🟢" if offer.offer_type == OfferType.BUY else "🔴"
            respond_emoji = "🔴" if offer.offer_type == OfferType.BUY else "🟢"
            
            # تاریخ و زمان شمسی
            import jdatetime
            from datetime import datetime, timezone, timedelta
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
            except:
                pass
            
            try:
                await bot.send_message(
                    chat_id=offer.user.telegram_id,
                    text=offer_owner_msg,
                    parse_mode="Markdown"
                )
            except:
                pass
            
            # بروزرسانی دکمه‌های پست کانال
            try:
                if new_remaining <= 0:
                    # لفظ تکمیل - حذف دکمه‌ها
                    await callback.message.edit_reply_markup(reply_markup=None)
                else:
                    # ساخت دکمه‌های جدید
                    new_keyboard = build_lot_buttons(offer_id, new_remaining, new_lot_sizes)
                    await callback.message.edit_reply_markup(reply_markup=new_keyboard)
            except:
                pass
            
            await callback.answer()
        else:
            # کلیک اول - ثبت زمان
            _pending_confirmations[confirmation_key] = current_time
            await callback.answer()

# ============================================
# SECTION 6: TEXT OFFER HANDLER
# لفظ متنی - ثبت لفظ با تایپ کردن
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
            InlineKeyboardButton(text="✅ تایید و ارسال", callback_data="text_offer_confirm"),
            InlineKeyboardButton(text="❌ انصراف", callback_data="text_offer_cancel")
        ]
    ])
    
    await message.answer(preview, parse_mode="Markdown", reply_markup=confirm_kb)
    await state.set_state(Trade.awaiting_text_confirm)


@router.callback_query(Trade.awaiting_text_confirm, F.data == "text_offer_confirm")
async def handle_text_offer_confirm(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    """تایید و ارسال لفظ متنی به کانال"""
    if not user:
        await callback.answer()
        return
    
    data = await state.get_data()
    trade_type = data.get("trade_type")
    commodity_id = data.get("commodity_id")
    commodity_name = data.get("commodity_name")
    quantity = data.get("quantity")
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
            if is_wholesale or not lot_sizes:
                trade_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"{quantity} عدد",
                        callback_data=f"channel_trade_{offer_id}_{quantity}"
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
                        callback_data=f"channel_trade_{offer_id}_{amount}"
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
                [InlineKeyboardButton(text="❌ منقضی کردن", callback_data=f"expire_offer_{offer_id}")]
            ])
            await bot.send_message(
                chat_id=callback.from_user.id,
                text=offer_preview,
                parse_mode="Markdown",
                reply_markup=expire_keyboard
            )
            
        except Exception as e:
            await callback.message.edit_text(f"❌ خطا در ارسال به کانال: {str(e)}")
    else:
        await callback.message.edit_text("❌ کانال تنظیم نشده است.")
    
    await state.clear()
    await callback.answer()


@router.callback_query(Trade.awaiting_text_confirm, F.data == "text_offer_cancel")
async def handle_text_offer_cancel(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    """انصراف از لفظ متنی"""
    await callback.message.edit_text("❌ لفظ لغو شد.")
    await state.clear()
    await callback.answer()
