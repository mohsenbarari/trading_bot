# bot/handlers/trade_history.py
"""هندلرهای تاریخچه معاملات"""

from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import joinedload
from typing import Optional
from datetime import datetime, timedelta
import os
import tempfile

from models.user import User
from models.trade import Trade, TradeType
from models.commodity import Commodity
from bot.message_manager import schedule_message_delete, set_anchor, delete_previous_anchor, DeleteDelay
from core.db import AsyncSessionLocal
import jdatetime

router = Router()


def get_trade_history_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """کیبورد تاریخچه معاملات"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 دانلود Excel", callback_data=f"export_excel_{target_user_id}"),
            InlineKeyboardButton(text="📄 دانلود PDF", callback_data=f"export_pdf_{target_user_id}")
        ],
        [
            InlineKeyboardButton(text="📅 ۱ ماه", callback_data=f"history_1m_{target_user_id}"),
            InlineKeyboardButton(text="📅 ۳ ماه", callback_data=f"history_3m_{target_user_id}"),
            InlineKeyboardButton(text="📅 ۶ ماه", callback_data=f"history_6m_{target_user_id}"),
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"back_to_profile_{target_user_id}")]
    ])


async def get_trade_history(current_user_id: int, target_user_id: int, months: int = 3):
    """دریافت تاریخچه معاملات بین دو کاربر"""
    from_date = datetime.utcnow() - timedelta(days=months * 30)
    
    async with AsyncSessionLocal() as session:
        # دریافت کاربر هدف
        target_stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(target_stmt)).scalar_one_or_none()
        
        if not target_user:
            return None, []
        
        # دریافت معاملات هر دو کاربر
        stmt = (
            select(Trade)
            .options(joinedload(Trade.commodity), joinedload(Trade.user))
            .where(
                and_(
                    Trade.created_at >= from_date,
                    or_(
                        Trade.user_id == current_user_id,
                        Trade.user_id == target_user_id
                    )
                )
            )
            .order_by(Trade.created_at.desc())
        )
        result = await session.execute(stmt)
        trades = result.scalars().all()
        
        return target_user, trades


def format_trade_history(trades, target_user) -> str:
    """فرمت‌بندی تاریخچه معاملات"""
    if not trades:
        return f"📊 تاریخچه معاملات با {target_user.account_name}\n\n⚠️ معامله‌ای یافت نشد."
    
    text = f"📊 تاریخچه معاملات با {target_user.account_name}\n\n"
    
    for trade in trades[:20]:  # حداکثر 20 معامله
        trade_emoji = "🟢" if trade.trade_type == TradeType.BUY else "🔴"
        trade_label = "خرید" if trade.trade_type == TradeType.BUY else "فروش"
        
        # تبدیل به تاریخ شمسی
        jalali_date = jdatetime.datetime.fromgregorian(datetime=trade.created_at)
        date_str = jalali_date.strftime("%Y/%m/%d")
        
        text += (
            f"{trade_emoji} {trade_label} {trade.commodity.name} "
            f"{trade.quantity} عدد {trade.price:,} - {date_str}\n"
            f"   👤 {trade.user.account_name}\n\n"
        )
    
    if len(trades) > 20:
        text += f"... و {len(trades) - 20} معامله دیگر"
    
    return text


async def generate_excel(trades, target_user, current_user) -> str:
    """ایجاد فایل Excel"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade History"
    
    # هدر
    headers = ["تاریخ", "نوع", "کالا", "تعداد", "قیمت", "معامله‌گر"]
    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")
    
    # داده‌ها
    for row, trade in enumerate(trades, 2):
        jalali_date = jdatetime.datetime.fromgregorian(datetime=trade.created_at)
        
        ws.cell(row=row, column=1, value=jalali_date.strftime("%Y/%m/%d %H:%M"))
        ws.cell(row=row, column=2, value="خرید" if trade.trade_type == TradeType.BUY else "فروش")
        ws.cell(row=row, column=3, value=trade.commodity.name)
        ws.cell(row=row, column=4, value=trade.quantity)
        ws.cell(row=row, column=5, value=trade.price)
        ws.cell(row=row, column=6, value=trade.user.account_name)
    
    # عرض ستون‌ها
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 10
    ws.column_dimensions['C'].width = 15
    ws.column_dimensions['D'].width = 10
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 15
    
    # ذخیره
    filename = tempfile.mktemp(suffix=".xlsx")
    wb.save(filename)
    
    return filename


async def generate_pdf(trades, target_user, current_user) -> str:
    """ایجاد فایل PDF"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    
    filename = tempfile.mktemp(suffix=".pdf")
    doc = SimpleDocTemplate(filename, pagesize=A4)
    
    # داده‌های جدول
    data = [["Date", "Type", "Commodity", "Qty", "Price", "User"]]
    
    for trade in trades:
        jalali_date = jdatetime.datetime.fromgregorian(datetime=trade.created_at)
        data.append([
            jalali_date.strftime("%Y/%m/%d"),
            "Buy" if trade.trade_type == TradeType.BUY else "Sell",
            trade.commodity.name,
            str(trade.quantity),
            f"{trade.price:,}",
            trade.user.account_name
        ])
    
    # ایجاد جدول
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4F81BD')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    doc.build([table])
    
    return filename


# --- دکمه تاریخچه در پروفایل ---
@router.callback_query(F.data.startswith("trade_history_"))
async def show_trade_history(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer("لطفاً ابتدا ثبت‌نام کنید.", show_alert=True)
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    target_user, trades = await get_trade_history(user.id, target_user_id, months=3)
    
    if not target_user:
        await callback.answer("کاربر یافت نشد!", show_alert=True)
        return
    
    await state.update_data(history_months=3, history_target_id=target_user_id)
    
    text = format_trade_history(trades, target_user)
    
    await callback.message.edit_text(
        text,
        reply_markup=get_trade_history_keyboard(target_user_id)
    )
    await callback.answer()


# --- فیلتر تاریخ ---
@router.callback_query(F.data.regexp(r"history_\d+m_\d+"))
async def filter_trade_history(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    parts = callback.data.split("_")
    months = int(parts[1].replace("m", ""))
    target_user_id = int(parts[2])
    
    target_user, trades = await get_trade_history(user.id, target_user_id, months=months)
    
    if not target_user:
        await callback.answer("کاربر یافت نشد!", show_alert=True)
        return
    
    await state.update_data(history_months=months, history_target_id=target_user_id)
    
    text = format_trade_history(trades, target_user)
    
    await callback.message.edit_text(
        text,
        reply_markup=get_trade_history_keyboard(target_user_id)
    )
    await callback.answer()


# --- دانلود Excel ---
@router.callback_query(F.data.startswith("export_excel_"))
async def export_excel(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    await callback.answer("⏳ در حال ایجاد فایل Excel...")
    
    data = await state.get_data()
    months = data.get("history_months", 3)
    target_user_id = int(callback.data.split("_")[-1])
    
    target_user, trades = await get_trade_history(user.id, target_user_id, months=months)
    
    if not trades:
        await callback.message.answer("⚠️ معامله‌ای برای دانلود وجود ندارد.")
        return
    
    try:
        filename = await generate_excel(trades, target_user, user)
        
        # ارسال فایل
        await bot.send_document(
            chat_id=callback.message.chat.id,
            document=FSInputFile(filename, filename=f"trade_history_{target_user.account_name}.xlsx"),
            caption=f"📊 تاریخچه معاملات با {target_user.account_name}\n📅 {months} ماه اخیر"
        )
        
        # حذف فایل موقت
        os.remove(filename)
        
    except Exception as e:
        await callback.message.answer(f"❌ خطا در ایجاد فایل: {str(e)}")


# --- دانلود PDF ---
@router.callback_query(F.data.startswith("export_pdf_"))
async def export_pdf(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return
    
    await callback.answer("⏳ در حال ایجاد فایل PDF...")
    
    data = await state.get_data()
    months = data.get("history_months", 3)
    target_user_id = int(callback.data.split("_")[-1])
    
    target_user, trades = await get_trade_history(user.id, target_user_id, months=months)
    
    if not trades:
        await callback.message.answer("⚠️ معامله‌ای برای دانلود وجود ندارد.")
        return
    
    try:
        filename = await generate_pdf(trades, target_user, user)
        
        # ارسال فایل
        await bot.send_document(
            chat_id=callback.message.chat.id,
            document=FSInputFile(filename, filename=f"trade_history_{target_user.account_name}.pdf"),
            caption=f"📊 تاریخچه معاملات با {target_user.account_name}\n📅 {months} ماه اخیر"
        )
        
        # حذف فایل موقت
        os.remove(filename)
        
    except Exception as e:
        await callback.message.answer(f"❌ خطا در ایجاد فایل: {str(e)}")


# --- بازگشت به پروفایل ---
@router.callback_query(F.data.startswith("back_to_profile_"))
async def back_to_profile(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
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
                [InlineKeyboardButton(text="📊 تاریخچه معاملات", callback_data=f"trade_history_{target_user_id}")]
            ])
        )
    
    await callback.answer()
