# bot/handlers/panel.py
"""هندلرهای پنل کاربر و مدیریت"""

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from models.user import User
from bot.keyboards import (
    get_user_panel_keyboard, 
    get_admin_panel_keyboard, 
    get_persistent_menu_keyboard,
)
from bot.message_manager import (
    set_anchor, 
    delete_previous_anchor,
    DeleteDelay
)
from typing import Optional
from core.enums import UserRole
from core.config import settings
from core.services.user_account_status_service import is_user_messenger_blocked

router = Router()


# --- هندلر پنل کاربر ---
@router.message(Command("panel"))
@router.message(F.text == "👤 پنل کاربر")
async def show_my_profile_and_change_keyboard(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    if is_user_messenger_blocked(user):
        await message.answer("دسترسی پیام‌رسان شما به دلیل غیرفعال بودن حساب مسدود شده است.")
        return
    if not user.has_bot_access:
        await message.answer("شما دسترسی لازم برای استفاده از این بخش را ندارید.")
        return

    # حذف پیام کاربر و لنگر قبلی
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    profile_link = f"https://t.me/{settings.bot_username}?start=profile_{user.id}"

    profile_text = (
        f"👤 **پروفایل شما**\n\n"
        f"🔸 **نام کاربری:** `{user.account_name}`\n"
        f"🔹 **نام تلگرام:** {user.full_name}\n"
        f"🔹 **آیدی تلگرام:** `{user.telegram_id}`\n"
        f"🔹 **سطح دسترسی:** {user.role.value}\n\n"
        f"🔗 **لینک پروفایل عمومی:**\n"
        f"`{profile_link}`"
    )
    
    anchor_msg = await message.answer(
        profile_text, 
        parse_mode="Markdown",
        reply_markup=get_user_panel_keyboard(user.role)
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


# --- هندلر پنل مدیریت ---
@router.message(F.text == "🔐 پنل مدیریت")
async def show_admin_panel_and_change_keyboard(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role not in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
        return
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    anchor_msg = await message.answer(
        "وارد پنل مدیریت شدید.",
        reply_markup=get_admin_panel_keyboard(user.role)
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


# --- هندلر دکمه تنظیمات کاربری ---
@router.message(F.text == "⚙️ تنظیمات کاربری")
async def handle_user_settings_button(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from core.db import AsyncSessionLocal
    from core.services.block_service import get_block_status
    
    async with AsyncSessionLocal() as session:
        block_status = await get_block_status(session, user.id)
    
    settings_text = (
        f"⚙️ **تنظیمات کاربری**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🚫 **مدیریت بلاک:**\n"
        f"   • وضعیت: {'✅ فعال' if block_status.get('can_block') else '❌ غیرفعال'}\n"
        f"   • مسدود شده: {block_status.get('current_blocked', 0)} از {block_status.get('max_blocked', 10)}\n"
    )
    
    from bot.handlers.block_manage import BlockMenuCallback
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚫 مدیریت کاربران مسدود",
            callback_data=BlockMenuCallback(action="main").pack()
        )]
    ])
    
    await message.answer(settings_text, parse_mode="Markdown", reply_markup=keyboard)


# --- هندلر دکمه تنظیمات ساده (برای کاربران عادی) ---
@router.message(F.text == "⚙️ تنظیمات")
async def handle_simple_settings_button(message: types.Message, user: Optional[User]):
    if not user: return
    
    await message.answer("🚧 بخش تنظیمات کاربری در حال توسعه است.")


# --- تنظیمات: نام‌های فارسی و کلیدها ---
SETTINGS_LABELS = {
    "invitation_expiry_days": "مدت اعتبار دعوت‌نامه (روز)",
    "offer_expiry_minutes": "مدت اعتبار لفظ (دقیقه)",
    "offer_min_quantity": "حداقل تعداد کالا",
    "offer_max_quantity": "حداکثر تعداد کالا",
    "max_active_offers": "حداکثر لفظ فعال",
    "offer_expire_rate_per_minute": "منقضی در دقیقه",
    "offer_expire_daily_limit_after_threshold": "آستانه منقضی روزانه",
}


def get_settings_keyboard():
    """کیبورد تنظیمات با دکمه ویرایش برای هر آیتم"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    buttons = [
        [InlineKeyboardButton(text="📨 دعوت‌نامه (روز)", callback_data="settings_edit_invitation_expiry_days")],
        [InlineKeyboardButton(text="📋 مدت لفظ", callback_data="settings_edit_offer_expiry_minutes")],
        [
            InlineKeyboardButton(text="🔢 حداقل تعداد", callback_data="settings_edit_offer_min_quantity"),
            InlineKeyboardButton(text="🔢 حداکثر تعداد", callback_data="settings_edit_offer_max_quantity"),
        ],
        [InlineKeyboardButton(text="📦 حداکثر لفظ فعال", callback_data="settings_edit_max_active_offers")],
        [
            InlineKeyboardButton(text="⏰ منقضی/دقیقه", callback_data="settings_edit_offer_expire_rate_per_minute"),
            InlineKeyboardButton(text="⏰ آستانه روزانه", callback_data="settings_edit_offer_expire_daily_limit_after_threshold"),
        ],
        [InlineKeyboardButton(text="🔄 بازنشانی به پیش‌فرض", callback_data="settings_reset_all")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def get_settings_text():
    """متن نمایش تنظیمات"""
    from core.trading_settings import get_trading_settings_async
    ts = await get_trading_settings_async()
    
    return (
        "⚙️ **تنظیمات سیستم**\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"📨 **دعوت‌نامه:**\n"
        f"   • مدت اعتبار: `{ts.invitation_expiry_days}` روز\n\n"
        f"📋 **لفظ معاملاتی:**\n"
        f"   • مدت اعتبار: `{ts.offer_expiry_minutes}` دقیقه\n"
        f"   • تعداد کالا: `{ts.offer_min_quantity}` - `{ts.offer_max_quantity}`\n"
        f"   • حداکثر لفظ فعال: `{ts.max_active_offers}`\n\n"
        f"⏰ **محدودیت منقضی کردن:**\n"
        f"   • در دقیقه: `{ts.offer_expire_rate_per_minute}`\n"
        f"   • آستانه روزانه: `{ts.offer_expire_daily_limit_after_threshold}`\n\n"
        f"📦 **معامله خُرد (لات):**\n"
        f"   • حداقل لات: `{ts.lot_min_size}` (برابر حداقل تعداد)\n"
        f"   • حداکثر بخش: `{ts.lot_max_count}` (ثابت)\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "👆 برای ویرایش روی دکمه کلیک کنید:"
    )


# --- هندلر دکمه تنظیمات سیستم ---
@router.message(F.text == "⚙️ تنظیمات سیستم")
async def handle_admin_settings_button(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    
    await state.clear()
    await message.answer(
        await get_settings_text(), 
        parse_mode="Markdown", 
        reply_markup=get_settings_keyboard()
    )


# --- هندلر کلیک روی دکمه ویرایش ---
@router.callback_query(F.data.startswith("settings_edit_"))
async def handle_settings_edit_click(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await callback.answer("دسترسی ندارید")
        return
    
    from bot.states import TradingSettingsEdit
    from core.trading_settings import get_trading_settings_async
    
    setting_key = callback.data.replace("settings_edit_", "")
    ts = await get_trading_settings_async()
    current_value = getattr(ts, setting_key, None)
    label = SETTINGS_LABELS.get(setting_key, setting_key)
    
    await state.update_data(editing_setting=setting_key)
    await state.set_state(TradingSettingsEdit.awaiting_value)
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="settings_cancel_edit")]
    ])
    
    await callback.message.edit_text(
        f"✏️ **ویرایش تنظیم**\n\n"
        f"📌 **{label}**\n"
        f"مقدار فعلی: `{current_value}`\n\n"
        f"مقدار جدید را وارد کنید:",
        parse_mode="Markdown",
        reply_markup=cancel_kb
    )
    await callback.answer()


# --- هندلر دریافت مقدار جدید ---
from bot.states import TradingSettingsEdit

@router.message(TradingSettingsEdit.awaiting_value)
async def handle_settings_new_value(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    
    from core.trading_settings import load_trading_settings_async, save_trading_settings_async, refresh_settings_cache_async
    
    data = await state.get_data()
    setting_key = data.get("editing_setting")
    
    if not setting_key:
        await state.clear()
        return
    
    # اعتبارسنجی عدد
    try:
        new_value = int(message.text.strip())
        if new_value < 1:
            raise ValueError()
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح مثبت وارد کنید.")
        return
    
    # ذخیره
    ts = await load_trading_settings_async()
    settings_dict = ts.model_dump()
    settings_dict[setting_key] = new_value
    
    if await save_trading_settings_async(settings_dict):
        await refresh_settings_cache_async()
        label = SETTINGS_LABELS.get(setting_key, setting_key)
        await message.answer(
            f"✅ **{label}** به `{new_value}` تغییر کرد.",
            parse_mode="Markdown"
        )
    else:
        await message.answer("❌ خطا در ذخیره تنظیمات")
    
    await state.clear()
    
    # نمایش مجدد تنظیمات
    await message.answer(
        await get_settings_text(),
        parse_mode="Markdown",
        reply_markup=get_settings_keyboard()
    )


# --- هندلر انصراف از ویرایش ---
@router.callback_query(F.data == "settings_cancel_edit")
async def handle_settings_cancel(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await callback.answer()
        return
    
    await state.clear()
    await callback.message.edit_text(
        await get_settings_text(),
        parse_mode="Markdown",
        reply_markup=get_settings_keyboard()
    )
    await callback.answer("انصراف")


# --- هندلر بازنشانی به پیش‌فرض ---
@router.callback_query(F.data == "settings_reset_all")
async def handle_settings_reset(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await callback.answer("دسترسی ندارید")
        return
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ بله، بازنشانی کن", callback_data="settings_reset_confirm"),
            InlineKeyboardButton(text="❌ خیر", callback_data="settings_reset_cancel"),
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ **هشدار**\n\n"
        "آیا مطمئن هستید که می‌خواهید تمام تنظیمات را به مقادیر پیش‌فرض بازنشانی کنید؟",
        parse_mode="Markdown",
        reply_markup=confirm_kb
    )
    await callback.answer()


@router.callback_query(F.data == "settings_reset_confirm")
async def handle_settings_reset_confirm(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await callback.answer()
        return
    
    from core.trading_settings import TradingSettings, save_trading_settings_async, refresh_settings_cache_async
    
    default_settings = TradingSettings()
    if await save_trading_settings_async(default_settings.model_dump()):
        await refresh_settings_cache_async()
        await callback.answer("✅ تنظیمات بازنشانی شد")
    else:
        await callback.answer("❌ خطا در بازنشانی")
    
    await callback.message.edit_text(
        await get_settings_text(),
        parse_mode="Markdown",
        reply_markup=get_settings_keyboard()
    )


@router.callback_query(F.data == "settings_reset_cancel")
async def handle_settings_reset_cancel(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await callback.answer()
        return
    
    await callback.message.edit_text(
        await get_settings_text(),
        parse_mode="Markdown",
        reply_markup=get_settings_keyboard()
    )
    await callback.answer("لغو شد")


# --- هندلر دکمه بازگشت ---
@router.message(F.text == "🔙 بازگشت")
async def handle_back_to_main_menu(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    anchor_msg = await message.answer(
        "به منوی اصلی بازگشتید.",
        reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
    )
    set_anchor(message.chat.id, anchor_msg.message_id)