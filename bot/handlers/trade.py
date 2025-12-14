# bot/handlers/trade.py
"""هندلرهای مربوط به ثبت لفظ معاملاتی"""

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
from bot.message_manager import schedule_message_delete
from core.config import settings
from core.enums import UserRole
from core.db import AsyncSessionLocal

router = Router()

def get_trade_type_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب نوع معامله (خرید/فروش)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 خرید", callback_data="trade_type_buy"),
            InlineKeyboardButton(text="🔴 فروش", callback_data="trade_type_sell")
        ],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
    ])


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


# --- هندلر دکمه معامله ---
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
    
    schedule_message_delete(message)


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
    
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n"
        f"کالا: {commodity_name}\n"
        f"تعداد: {quantity}\n\n"
        f"💰 قیمت را وارد کنید (5 یا 6 رقم):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
        ])
    )
    
    
    await state.set_state(Trade.awaiting_price)
    await callback.answer()


# --- ورود دستی تعداد ---
@router.message(Trade.awaiting_quantity)
async def handle_manual_quantity(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    try:
        quantity = int(message.text.strip())
        if quantity <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح مثبت وارد کنید.")
        schedule_message_delete(message)
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
        f"💰 قیمت را وارد کنید (5 یا 6 رقم):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ انصراف", callback_data="trade_cancel")]
        ])
    )
    
    
    await state.set_state(Trade.awaiting_price)
    schedule_message_delete(message)


# --- ورود قیمت ---
@router.message(Trade.awaiting_price)
async def handle_price_input(message: types.Message, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    price_text = message.text.strip()
    
    # اعتبارسنجی: فقط 5 یا 6 رقم
    if not price_text.isdigit() or len(price_text) not in [5, 6]:
        err_msg = await message.answer("❌ قیمت باید 5 یا 6 رقم باشد (مثال: 75800 یا 758000)")
        schedule_message_delete(message)
        return
    
    price = int(price_text)
    
    data = await state.get_data()
    trade_type = data.get("trade_type", "buy")
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    commodity_name = data.get("commodity_name", "نامشخص")
    quantity = data.get("quantity", 1)
    
    await state.update_data(price=price)
    
    # نمایش پیش‌نمایش و تایید
    preview = (
        f"📈 **پیش‌نمایش لفظ معاملاتی**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 معامله‌گر: {user.full_name}\n"
        f"📦 نوع: {trade_type_fa}\n"
        f"🏷️ کالا: {commodity_name}\n"
        f"🔢 تعداد: {quantity}\n"
        f"💰 قیمت: {price:,}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"آیا اطلاعات بالا صحیح است?"
    )
    
    await message.answer(
        preview,
        parse_mode="Markdown",
        reply_markup=get_confirm_keyboard()
    )



# --- تایید و ارسال به کانال ---
@router.callback_query(F.data == "trade_confirm")
async def handle_trade_confirm(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    data = await state.get_data()
    trade_type = data.get("trade_type", "buy")
    trade_type_fa = data.get("trade_type_fa", "🟢 خرید")
    commodity_name = data.get("commodity_name", "نامشخص")
    quantity = data.get("quantity", 1)
    price = data.get("price", 0)
    
    # ساخت پیام کانال - فرمت مختصر
    trade_emoji = "🟢" if trade_type == "buy" else "🔴"
    trade_label = "خرید" if trade_type == "buy" else "فروش"
    
    # لینک پروفایل کاربر در بات
    profile_link = f"https://t.me/{settings.bot_username}?start=profile_{user.id}"
    
    channel_message = (
        f"{trade_emoji}{trade_label} {commodity_name} {quantity} عدد {price:,}\n"
        f"\n"
        f"[{user.account_name}]({profile_link})"
    )
    
    # ارسال به کانال
    if settings.channel_id:
        try:
            sent_msg = await bot.send_message(
                chat_id=settings.channel_id,
                text=channel_message,
                parse_mode="Markdown"
            )
            
            # ذخیره لفظ در دیتابیس
            async with AsyncSessionLocal() as session:
                new_offer = Offer(
                    user_id=user.id,
                    offer_type=OfferType.BUY if trade_type == "buy" else OfferType.SELL,
                    commodity_id=data.get("commodity_id"),
                    quantity=quantity,
                    price=price,
                    status=OfferStatus.ACTIVE,
                    channel_message_id=sent_msg.message_id
                )
                session.add(new_offer)
                await session.commit()
            
            await callback.message.edit_text(
                "✅ لفظ شما با موفقیت در کانال ارسال شد!",
                parse_mode="Markdown"
            )
            # این پیام باقی می‌ماند و حذف نمی‌شود
            
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
