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
from core.enums import SettlementType
from bot.callbacks import (
    TradeTypeCallback,
    TradeSettlementCallback,
    CommodityCallback,
    PageCallback,
    QuantityCallback,
    LotTypeCallback,
    TradeActionCallback,
    TradeWizardActionCallback,
    TradeWizardEditCallback,
    ACTION_NOOP
)

# ============================================
# KEYBOARDS & UTILS
# ============================================

def _wizard_back_button(*, return_to_review: bool, fallback_action: str) -> InlineKeyboardButton:
    if return_to_review:
        return InlineKeyboardButton(
            text="🔙 بازگشت به خلاصه",
            callback_data=TradeWizardActionCallback(action="review").pack(),
        )
    return InlineKeyboardButton(
        text="❌ انصراف" if fallback_action == "cancel" else "🔙 بازگشت",
        callback_data=TradeActionCallback(action=fallback_action).pack(),
    )


def get_trade_type_keyboard(*, return_to_review: bool = False) -> InlineKeyboardMarkup:
    """کیبورد انتخاب نوع معامله (خرید/فروش)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 خرید", callback_data=TradeTypeCallback(type="buy").pack()),
            InlineKeyboardButton(text="🔴 فروش", callback_data=TradeTypeCallback(type="sell").pack())
        ],
        [_wizard_back_button(return_to_review=return_to_review, fallback_action="cancel")],
    ])


def get_settlement_type_keyboard(*, return_to_review: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="☀️ نقدی",
                callback_data=TradeSettlementCallback(type=SettlementType.CASH.value).pack(),
            ),
            InlineKeyboardButton(
                text="➡️ فردایی",
                callback_data=TradeSettlementCallback(type=SettlementType.TOMORROW.value).pack(),
            ),
        ],
        [_wizard_back_button(return_to_review=return_to_review, fallback_action="back_to_type")],
    ])


def get_lot_type_keyboard(*, return_to_review: bool = False) -> InlineKeyboardMarkup:
    """کیبورد انتخاب یکجا یا خُرد"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 یکجا", callback_data=LotTypeCallback(type="wholesale").pack()),
            InlineKeyboardButton(text="خُرد", callback_data=LotTypeCallback(type="retail").pack())
        ],
        [_wizard_back_button(return_to_review=return_to_review, fallback_action="back_to_quantity")],
    ])


async def get_commodities_keyboard(
    trade_type: str,
    page: int = 1,
    limit: int = 9,
    *,
    return_to_review: bool = False,
) -> InlineKeyboardMarkup:
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
        _wizard_back_button(return_to_review=return_to_review, fallback_action="back_to_settlement"),
        InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack()),
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_quantity_keyboard(
    min_quantity: int = 5,
    max_quantity: int = 50,
    *,
    return_to_review: bool = False,
) -> InlineKeyboardMarkup:
    """کیبورد انتخاب سریع تعداد مطابق بازه تنظیمات سیستم"""
    if min_quantity > max_quantity:
        min_quantity, max_quantity = max_quantity, min_quantity

    quick_values: List[int] = []
    for candidate in [10, 20, 30, 40, 50]:
        if min_quantity <= candidate <= max_quantity:
            quick_values.append(candidate)

    if min_quantity <= max_quantity and min_quantity not in quick_values:
        quick_values.insert(0, min_quantity)
    if max_quantity not in quick_values:
        quick_values.append(max_quantity)

    quick_values = sorted(dict.fromkeys(quick_values))
    keyboard_rows = []

    for idx in range(0, len(quick_values), 5):
        row_values = quick_values[idx:idx + 5]
        keyboard_rows.append(
            [
                InlineKeyboardButton(text=str(value), callback_data=QuantityCallback(value=str(value)).pack())
                for value in row_values
            ]
        )

    keyboard_rows.append([InlineKeyboardButton(text="✏️ ورود دستی", callback_data=QuantityCallback(value="manual").pack())])
    keyboard_rows.append([
        _wizard_back_button(return_to_review=return_to_review, fallback_action="back_to_commodity")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def get_wizard_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ ادامه با همین متن",
            callback_data=TradeWizardActionCallback(action="continue").pack(),
        )],
        [InlineKeyboardButton(
            text="✏️ اصلاح گزینه‌ها",
            callback_data=TradeWizardActionCallback(action="edit").pack(),
        )],
        [InlineKeyboardButton(
            text="❌ لغو",
            callback_data=TradeWizardActionCallback(action="cancel").pack(),
        )],
    ])


def get_wizard_edit_keyboard(*, is_wholesale: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="خرید / فروش", callback_data=TradeWizardEditCallback(field="trade_type").pack()),
            InlineKeyboardButton(text="نقدی / فردایی", callback_data=TradeWizardEditCallback(field="settlement_type").pack()),
        ],
        [
            InlineKeyboardButton(text="کالا", callback_data=TradeWizardEditCallback(field="commodity").pack()),
            InlineKeyboardButton(text="تعداد", callback_data=TradeWizardEditCallback(field="quantity").pack()),
        ],
        [
            InlineKeyboardButton(text="یکجا / خُرد", callback_data=TradeWizardEditCallback(field="lot_type").pack()),
            InlineKeyboardButton(text="قیمت", callback_data=TradeWizardEditCallback(field="price").pack()),
        ],
    ]
    if not is_wholesale:
        rows.append([
            InlineKeyboardButton(text="ترکیب بخش‌بندی", callback_data=TradeWizardEditCallback(field="lot_sizes").pack()),
            InlineKeyboardButton(text="توضیحات", callback_data=TradeWizardEditCallback(field="notes").pack()),
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="توضیحات", callback_data=TradeWizardEditCallback(field="notes").pack()),
        ])
    rows.append([
        InlineKeyboardButton(
            text="🔙 بازگشت به متن آفر",
            callback_data=TradeWizardActionCallback(action="review").pack(),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_confirm_keyboard():
    """کیبورد تایید نهایی"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تایید و ارسال به کانال", callback_data=TradeActionCallback(action="confirm").pack())],
        [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
    ])
