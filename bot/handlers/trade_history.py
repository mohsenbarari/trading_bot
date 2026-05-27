# bot/handlers/trade_history.py
"""هندلرهای تاریخچه معاملات"""

from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import joinedload
from typing import Optional
from datetime import datetime, timedelta
from importlib import import_module
import os
import tempfile

from models.user import User
from models.trade import Trade, TradeType, TradeStatus
from models.offer import Offer, OfferType
from models.commodity import Commodity
from core.db import AsyncSessionLocal
from core.services.trade_history_export_service import (
    build_trade_history_date_range_label,
    build_trade_history_export_rows,
    generate_trade_history_pdf_file,
    resolve_counterparty_account_name_for_perspective,
)
from core.utils import to_jalali_str

from bot.callbacks import (
    TradeHistoryCallback, HistoryPageCallback, 
    ExportHistoryCallback, ProfileCallback
)

router = Router()


def get_trade_history_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """کیبورد تاریخچه معاملات"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 دانلود Excel", callback_data=ExportHistoryCallback(format="excel", target_user_id=target_user_id).pack()),
            InlineKeyboardButton(text="📄 دانلود PDF", callback_data=ExportHistoryCallback(format="pdf", target_user_id=target_user_id).pack())
        ],
        [
            InlineKeyboardButton(text="📅 ۱ ماه", callback_data=HistoryPageCallback(months=1, target_user_id=target_user_id).pack()),
            InlineKeyboardButton(text="📅 ۳ ماه", callback_data=HistoryPageCallback(months=3, target_user_id=target_user_id).pack()),
            InlineKeyboardButton(text="📅 ۶ ماه", callback_data=HistoryPageCallback(months=6, target_user_id=target_user_id).pack()),
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=ProfileCallback(target_user_id=target_user_id).pack())]
    ])


async def get_trade_history(current_user_id: int, target_user_id: int, months: int = 3):
    """دریافت تاریخچه معاملات بین دو کاربر"""
    from_date = datetime.utcnow() - timedelta(days=months * 30)
    
    async with AsyncSessionLocal() as session:
        # اگر target_user_id == current_user_id باشد، یعنی تاریخچه کل کاربر را می‌خواهد
        is_self_history = (target_user_id == current_user_id or target_user_id == 0)
        
        target_user = None
        if not is_self_history:
            target_stmt = select(User).where(User.id == target_user_id)
            target_user = (await session.execute(target_stmt)).scalar_one_or_none()
            if not target_user:
                return None, []
        
        # دریافت معاملات
        stmt = (
            select(Trade)
            .options(
                joinedload(Trade.commodity), 
                joinedload(Trade.offer_user),
                joinedload(Trade.responder_user)
            )
            .order_by(Trade.created_at.desc()) # از جدید به قدیم
        )
        
        if is_self_history:
            stmt = stmt.where(
                and_(
                    Trade.created_at >= from_date,
                    or_(Trade.offer_user_id == current_user_id, Trade.responder_user_id == current_user_id)
                )
            )
        else:
            stmt = stmt.where(
                and_(
                    Trade.created_at >= from_date,
                    or_(
                        and_(Trade.offer_user_id == current_user_id, Trade.responder_user_id == target_user_id),
                        and_(Trade.offer_user_id == target_user_id, Trade.responder_user_id == current_user_id)
                    )
                )
            )
            
        result = await session.execute(stmt)
        trades = result.scalars().all()
        
        return target_user, trades


def format_trade_history(trades, target_user, current_user_id: int) -> str:
    """فرمت‌بندی تاریخچه معاملات"""
    is_self = target_user is None
    
    title = "📊 تاریخچه معاملات کل شما" if is_self else f"📊 تاریخچه معاملات با {target_user.account_name}"
    
    if not trades:
        return f"{title}\n\n⚠️ معامله‌ای یافت نشد."
    
    text = f"{title}\n\n"
    
    for trade in trades[:20]:  # حداکثر 20 معامله
        # تشخیص نوع معامله از دید کاربر فعلی
        if trade.responder_user_id == current_user_id:
            # کاربر فعلی پاسخ‌دهنده بود - trade_type همان نوع عمل اوست
            is_buy = trade.trade_type == TradeType.BUY
            counterparty = getattr(getattr(trade, "offer_user", None), "account_name", "")
        else:
            # کاربر فعلی لفظ‌دهنده بود - عکس trade_type
            is_buy = trade.trade_type != TradeType.BUY
            counterparty = getattr(getattr(trade, "responder_user", None), "account_name", "")
        
        trade_emoji = "🟢" if is_buy else "🔴"
        trade_label = "خرید" if is_buy else "فروش"
        
        date_str = to_jalali_str(trade.created_at, "%Y/%m/%d %H:%M") if trade.created_at else "نامشخص"
        
        text += (
            f"{trade_emoji} {trade_label} {trade.commodity.name} "
            f"{trade.quantity} عدد {trade.price:,}\n"
            f"   📅 {date_str}\n"
        )
        if is_self:
            text += f"   👤 طرف معامله: {counterparty}\n"
        text += "\n"
    
    if len(trades) > 20:
        text += f"... و {len(trades) - 20} معامله دیگر"
    
    return text


@router.message(F.text == "📊 تاریخچه معاملات من")
async def show_my_trade_history(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    target_user, trades = await get_trade_history(user.id, user.id, months=3)
    await state.update_data(history_months=3)
    
    text = format_trade_history(trades, None, user.id)
    
    # برای تاریخچه شخصی، دکمه بازگشت باید به پنل اصلی برگردد
    # اما get_trade_history_keyboard نیاز به target_user_id دارد. 0 را به عنوان نشانه خود استفاده می‌کنیم.
    await message.answer(
        text,
        reply_markup=get_trade_history_keyboard(user.id) # استفاده از آیدی خود کاربر
    )

# --- فیلتر زمانی ---
@router.callback_query(HistoryPageCallback.filter())
async def change_history_months(callback: types.CallbackQuery, callback_data: HistoryPageCallback, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    months = callback_data.months
    target_user_id = callback_data.target_user_id
    await state.update_data(history_months=months)
    
    target_user, trades = await get_trade_history(user.id, target_user_id, months=months)
    
    # اگر target_user_id آیدی خود کاربر باشد، یعنی تاریخچه کل است
    is_self = (target_user_id == user.id)
    text = format_trade_history(trades, target_user if not is_self else None, user.id)
    
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_trade_history_keyboard(target_user_id)
        )
    except TelegramBadRequest:
        pass  # پیام تغییر نکرده
    await callback.answer()


async def show_trade_history(callback: types.CallbackQuery, callback_data: TradeHistoryCallback, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer("لطفاً ابتدا ثبت نام کنید!", show_alert=True)
        return

    target_user_id = callback_data.target_user_id
    target_user, trades = await get_trade_history(user.id, target_user_id, months=3)
    if target_user is None and not trades and target_user_id not in {user.id, 0}:
        await callback.answer("کاربر یافت نشد!", show_alert=True)
        return

    await state.update_data(history_months=3, history_target_id=target_user_id)
    is_self = target_user_id in {user.id, 0}
    text = format_trade_history(trades, None if is_self else target_user, user.id)
    await callback.message.edit_text(text, reply_markup=get_trade_history_keyboard(target_user_id))
    await callback.answer()


async def filter_trade_history(callback: types.CallbackQuery, callback_data: HistoryPageCallback, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer("لطفاً ابتدا ثبت نام کنید!", show_alert=True)
        return

    target_user_id = callback_data.target_user_id
    target_user, trades = await get_trade_history(user.id, target_user_id, months=callback_data.months)
    if target_user is None and not trades and target_user_id not in {user.id, 0}:
        await callback.answer("کاربر یافت نشد!", show_alert=True)
        return

    await state.update_data(history_months=callback_data.months, history_target_id=target_user_id)
    is_self = target_user_id in {user.id, 0}
    text = format_trade_history(trades, None if is_self else target_user, user.id)
    try:
        await callback.message.edit_text(text, reply_markup=get_trade_history_keyboard(target_user_id))
    except TelegramBadRequest:
        pass
    await callback.answer()


# --- بقیه هندلرها (Excel, PDF, ...) ---

async def generate_excel(trades, target_user, current_user) -> str:
    """ایجاد فایل Excel با پشتیبانی RTL"""
    openpyxl = import_module("openpyxl")
    openpyxl_styles = import_module("openpyxl.styles")

    Workbook = openpyxl.Workbook
    Font = openpyxl_styles.Font
    Alignment = openpyxl_styles.Alignment
    PatternFill = openpyxl_styles.PatternFill
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade History"
    ws.sheet_view.rightToLeft = True  # راست به چپ
    
    # هدر
    is_self = target_user is None
    if is_self:
        headers = ["قیمت", "تعداد", "کالا", "نوع", "طرف معامله", "ساعت", "تاریخ"]
    else:
        headers = ["قیمت", "تعداد", "کالا", "نوع", "ساعت", "تاریخ"]
        
    header_fill = PatternFill(start_color="2C5282", end_color="2C5282", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")
    
    export_rows = build_trade_history_export_rows(trades, current_user.id)

    # داده‌ها
    for row_idx, (trade, export_row) in enumerate(zip(trades, export_rows), 2):
        row_data = [
            export_row.price,
            export_row.quantity,
            export_row.commodity_name,
            export_row.trade_type_label,
        ]
        if is_self:
            row_data.append(resolve_counterparty_account_name_for_perspective(trade, current_user.id))
            
        row_data.extend([
            export_row.time_label,
            export_row.date_label,
        ])
        
        for col_idx, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = Alignment(horizontal="center")
    
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(temp_file.name)
    return temp_file.name


async def generate_pdf(trades, target_user, current_user, months: Optional[int] = None) -> str:
    """ایجاد فایل PDF با استفاده از سرویس مشترک خروجی تاریخچه معاملات"""
    export_rows = build_trade_history_export_rows(trades, current_user.id)
    display_name = target_user.account_name if target_user else "پروفایل من"

    from_date = None
    to_date = None
    if months and months > 0:
        today = datetime.now(timezone.utc).date()
        to_date = today
        from_date = today - timedelta(days=months * 30)

    date_range_label = build_trade_history_date_range_label(from_date, to_date)
    return generate_trade_history_pdf_file(
        subject_name=display_name,
        date_range_label=date_range_label,
        rows=export_rows,
    )


# --- دانلود Excel ---
@router.callback_query(ExportHistoryCallback.filter(F.format == "excel"))
async def export_excel(callback: types.CallbackQuery, callback_data: ExportHistoryCallback, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    await callback.answer("⏳ در حال ایجاد فایل Excel...")
    
    data = await state.get_data()
    months = data.get("history_months", 3)
    target_user_id = callback_data.target_user_id
    
    target_user, trades = await get_trade_history(user.id, target_user_id, months=months)
    
    if not trades:
        msg = await callback.message.answer("⚠️ معامله‌ای برای دانلود وجود ندارد.")
        return
    
    try:
        filename = await generate_excel(trades, target_user, user)
        
        display_name = target_user.account_name if target_user else "پروفایل من"
        
        # ارسال فایل
        doc_msg = await bot.send_document(
            chat_id=callback.message.chat.id,
            document=FSInputFile(filename, filename=f"trade_history_{display_name}.xlsx"),
            caption=f"📊 تاریخچه معاملات {display_name}\n📅 {months} ماه اخیر"
        )
        
        # حذف فایل موقت
        os.remove(filename)
        
    except Exception as e:
        msg = await callback.message.answer(f"❌ خطا در ایجاد فایل: {str(e)}")


# --- دانلود PDF ---
@router.callback_query(ExportHistoryCallback.filter(F.format == "pdf"))
async def export_pdf(callback: types.CallbackQuery, callback_data: ExportHistoryCallback, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    await callback.answer("⏳ در حال ایجاد فایل PDF...")
    
    data = await state.get_data()
    months = data.get("history_months", 3)
    target_user_id = callback_data.target_user_id
    
    target_user, trades = await get_trade_history(user.id, target_user_id, months=months)
    
    if not trades:
        msg = await callback.message.answer("⚠️ معامله‌ای برای دانلود وجود ندارد.")
        return
    
    try:
        filename = await generate_pdf(trades, target_user, user, months=months)
        
        display_name = target_user.account_name if target_user else "پروفایل من"
        
        # ارسال فایل
        doc_msg = await bot.send_document(
            chat_id=callback.message.chat.id,
            document=FSInputFile(filename, filename=f"trade_history_{display_name}.pdf"),
            caption=f"📊 تاریخچه معاملات {display_name}\n📅 {months} ماه اخیر"
        )
        
        # حذف فایل موقت
        os.remove(filename)
        
    except Exception as e:
        msg = await callback.message.answer(f"❌ خطا در ایجاد فایل: {str(e)}")


# --- بازگشت به پروفایل ---
@router.callback_query(ProfileCallback.filter())
async def back_to_profile(callback: types.CallbackQuery, callback_data: ProfileCallback, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    target_user_id = callback_data.target_user_id
    
    # اگر آیدی خودش بود، به منوی پنل برگردد
    if target_user_id == user.id:
        from bot.handlers.panel import show_my_profile_and_change_keyboard
        # ما یک هندلر برای مسیج داریم، اینجا باید کالبک را هندل کنیم.
        # ساده‌ترین راه این است که متن پروفایل را اینجا بازنویسی کنیم.
        from core.config import settings as core_settings
        profile_link = f"https://t.me/{core_settings.bot_username}?start=profile_{user.id}"
        profile_text = (
            f"👤 **پروفایل شما**\n\n"
            f"🔸 **نام کاربری:** `{user.account_name}`\n"
            f"🔹 **نام تلگرام:** {user.full_name}\n"
            f"🔹 **آیدی تلگرام:** `{user.telegram_id}`\n"
            f"🔹 **سطح دسترسی:** {user.role.value}\n\n"
            f"🔗 **لینک پروفایل عمومی:**\n"
            f"`{profile_link}`"
        )
        from bot.keyboards import get_user_panel_keyboard
        await callback.message.edit_text(
            profile_text,
            parse_mode="Markdown",
            reply_markup=get_user_panel_keyboard(user.role)
        )
        await callback.answer()
        return

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
    
    if target_user:
        profile_text = (
            f"👤 پروفایل عمومی\n\n"
            f"🔸 نام کاربری: {target_user.account_name}\n"
            f"📞 شماره تماس: {target_user.mobile_number}\n"
            f"📍 آدرس: {target_user.address or 'ثبت نشده'}"
        )
        
        await callback.message.edit_text(
            profile_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 تاریخچه معاملات", callback_data=TradeHistoryCallback(target_user_id=target_user_id).pack())]
            ])
        )
    
    await callback.answer()


@router.callback_query(TradeHistoryCallback.filter())
async def show_mutual_trade_history(callback: types.CallbackQuery, callback_data: TradeHistoryCallback, state: FSMContext, user: Optional[User]):
    """نمایش تاریخچه معاملات بین دو کاربر از طریق کالبک"""
    if not user:
        return
    
    target_user_id = callback_data.target_user_id
    await state.update_data(history_months=3)
    
    target_user, trades = await get_trade_history(user.id, target_user_id, months=3)
    
    if not target_user and target_user_id != user.id:
        await callback.answer("کاربر یافت نشد", show_alert=True)
        return
        
    is_self = (target_user_id == user.id)
    text = format_trade_history(trades, target_user if not is_self else None, user.id)
    
    await callback.message.edit_text(
        text,
        reply_markup=get_trade_history_keyboard(target_user_id)
    )
    await callback.answer()
