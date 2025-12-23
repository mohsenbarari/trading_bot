from aiogram import Router, F, types, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional, List, Union
import math
from sqlalchemy import select

from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.commodity import Commodity
from core.config import settings
from core.db import AsyncSessionLocal
from bot.callbacks import (
    TradeTypeCallback,
    CommodityCallback,
    PageCallback,
    QuantityCallback,
    LotTypeCallback,
    TradeActionCallback,
    ACTION_NOOP
)

# ============================================
# KEYBOARDS & UTILS
# ============================================

def get_trade_type_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب نوع معامله (خرید/فروش)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 خریدارم", callback_data=TradeTypeCallback(type="buy").pack()),
            InlineKeyboardButton(text="🔴 فروشنده‌ام", callback_data=TradeTypeCallback(type="sell").pack())
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="panel_back")]
    ])


def get_lot_type_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب یکجا یا خُرد"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 یکجا (Wholesale)", callback_data=LotTypeCallback(type="wholesale").pack()),
            InlineKeyboardButton(text="🍰 خُرد (Retail)", callback_data=LotTypeCallback(type="retail").pack())
        ],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=TradeActionCallback(action="back_to_quantity").pack())]
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
                callback_data=CommodityCallback(id=commodity.id).pack()
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
            pagination_row.append(InlineKeyboardButton(text="➡️ قبلی", callback_data=PageCallback(trade_type=trade_type, page=page-1).pack()))
        pagination_row.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data=ACTION_NOOP))
        if page < total_pages:
            pagination_row.append(InlineKeyboardButton(text="⬅️ بعدی", callback_data=PageCallback(trade_type=trade_type, page=page+1).pack()))
        keyboard_rows.append(pagination_row)
    
    # دکمه‌های کنترل
    keyboard_rows.append([
        InlineKeyboardButton(text="🔙 بازگشت", callback_data=TradeActionCallback(action="back_to_type").pack()),
        InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_quantity_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب سریع تعداد"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10", callback_data=QuantityCallback(value="10").pack()),
            InlineKeyboardButton(text="20", callback_data=QuantityCallback(value="20").pack()),
            InlineKeyboardButton(text="30", callback_data=QuantityCallback(value="30").pack()),
            InlineKeyboardButton(text="40", callback_data=QuantityCallback(value="40").pack()),
            InlineKeyboardButton(text="50", callback_data=QuantityCallback(value="50").pack()),
        ],
        [InlineKeyboardButton(text="✏️ ورود دستی", callback_data=QuantityCallback(value="manual").pack())],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=TradeActionCallback(action="back_to_commodity").pack())]
    ])


def get_confirm_keyboard():
    """کیبورد تایید نهایی"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تایید و ارسال به کانال", callback_data=TradeActionCallback(action="confirm").pack())],
        [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
    ])
