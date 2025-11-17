# trading_bot/bot/handlers/admin_users.py (کامل و اصلاح شده)

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, or_, func
from typing import Optional
import jdatetime
import pytz

from core.db import AsyncSessionLocal
from models.user import User
from core.enums import UserRole
# --- ۱. ایمپورت توابع کمکی ---
from core.utils import normalize_account_name, normalize_persian_numerals
from bot.keyboards import (
    get_users_management_keyboard, 
    get_admin_panel_keyboard, 
    get_commodity_fsm_cancel_keyboard,
    get_users_list_inline_keyboard,
    get_user_profile_return_keyboard
)
from bot.states import UserManagement

router = Router()
USERS_PER_PAGE = 10

# --- ۲. تابع نرمال‌سازی تکراری حذف شد ---

# --- توابع کمکی ---
async def show_users_list(message_or_query: types.Message | types.CallbackQuery, page: int):
    # ... (بدون تغییر)
    async with AsyncSessionLocal() as session:
        count_stmt = select(func.count()).select_from(User)
        total_count = (await session.execute(count_stmt)).scalar()
        
        offset = (page - 1) * USERS_PER_PAGE
        stmt = select(User).order_by(User.id.desc()).offset(offset).limit(USERS_PER_PAGE)
        users = (await session.execute(stmt)).scalars().all()

    if not users:
        text = "📭 هیچ کاربری یافت نشد."
        keyboard = None
    else:
        text = "👥 **لیست کاربران**\n\nبرای مشاهده پروفایل، روی نام کاربر کلیک کنید:"
        keyboard = get_users_list_inline_keyboard(users, page, total_count, USERS_PER_PAGE)

    if isinstance(message_or_query, types.Message):
        await message_or_query.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    elif isinstance(message_or_query, types.CallbackQuery):
        try:
            await message_or_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        except Exception:
            await message_or_query.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


async def get_user_profile_text(target_user: User) -> str:
    # ... (بدون تغییر)
    join_date = "نامشخص"
    if target_user.created_at:
        try:
            iran_tz = pytz.timezone('Asia/Tehran')
            iran_time = target_user.created_at.astimezone(iran_tz)
            jalali_date = jdatetime.datetime.fromgregorian(datetime=iran_time)
            join_date = jalali_date.strftime("%Y/%m/%d - %H:%M")
        except Exception:
            join_date = target_user.created_at.strftime("%Y-%m-%d %H:%M") + " (UTC)"

    profile_text = (
        f"👤 **پروفایل کاربر**\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"🆔 **نام کاربری:** `{target_user.account_name or '---'}`\n"
        f"📛 **نام کامل:** {target_user.full_name or '---'}\n"
        f"📱 **شماره موبایل:** `{target_user.mobile_number or '---'}`\n"
        f"🔢 **آیدی تلگرام:** `{target_user.telegram_id}`\n"
        f"🔰 **سطح دسترسی:** {target_user.role.value}\n"
        f"📅 **تاریخ عضویت:** {join_date}\n"
    )
    return profile_text

# -------------------

# (هندلرهای ۱ تا ۵ بدون تغییر)
# ...
@router.message(F.text == "👥 مدیریت کاربران")
async def handle_users_menu(message: types.Message, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    await message.answer(
        "👥 **مدیریت کاربران**\n\n"
        "لطفاً گزینه مورد نظر را انتخاب کنید:",
        reply_markup=get_users_management_keyboard(),
        parse_mode="Markdown"
    )

@router.message(F.text == "📋 لیست کاربران")
async def handle_users_list_command(message: types.Message, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    await show_users_list(message, page=1)

@router.callback_query(F.data.startswith("users_page_"))
async def handle_users_pagination(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    
    page = int(callback.data.split("_")[-1])
    await show_users_list(callback, page)
    await callback.answer()

@router.callback_query(F.data.startswith("user_profile_"))
async def handle_view_user_profile(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return

    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
    
    if not target_user:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    profile_text = await get_user_profile_text(target_user)
    current_page = 1 
    try:
        if callback.message.reply_markup:
            for row in callback.message.reply_markup.inline_keyboard:
                for button in row:
                    if "users_page_" in button.callback_data:
                        if "users_page_" in button.callback_data:
                            current_page = int(button.callback_data.split("_")[-1])
                        break
    except Exception:
        pass 
        
    await callback.message.edit_text(
        profile_text,
        reply_markup=get_user_profile_return_keyboard(back_to_page=current_page),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(F.text == "🔙 بازگشت به پنل مدیریت")
async def handle_back_to_admin(message: types.Message, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    
    await message.answer(
        "به پنل مدیریت بازگشتید.",
        reply_markup=get_admin_panel_keyboard()
    )
# ...

# ۶. شروع جستجوی کاربر
@router.message(F.text == "🔍 جستجوی کاربر")
async def start_search_user(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    
    await state.set_state(UserManagement.awaiting_search_query)
    await message.answer(
        "🔎 لطفاً **نام کاربری (Account Name)** یا **شماره موبایل** کاربر را وارد کنید:\n\n"
        "(برای لغو از دکمه شیشه‌ای زیر استفاده کنید)",
        reply_markup=get_commodity_fsm_cancel_keyboard(),
        parse_mode="Markdown"
    )

# --- ۷. پردازش جستجو (اصلاح شده) ---
@router.message(UserManagement.awaiting_search_query)
async def process_search_query(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await state.clear()
        return

    query_text = message.text.strip()
    
    # --- ۳. نرمال‌سازی ورودی جستجو برای هر دو فیلد ---
    query_text_normalized_account = normalize_account_name(query_text)
    query_text_normalized_mobile = normalize_persian_numerals(query_text)
    
    await state.clear()

    if not query_text:
        await message.answer("❌ متن جستجو نمی‌تواند خالی باشد.", reply_markup=get_users_management_keyboard())
        return

    searching_msg = await message.answer("⏳ در حال جستجو...")

    async with AsyncSessionLocal() as session:
        # --- ۴. جستجو با مقادیر نرمال شده ---
        stmt = select(User).where(
            or_(
                User.account_name == query_text_normalized_account,
                User.mobile_number == query_text_normalized_mobile
            )
        )
        user_found = (await session.execute(stmt)).scalar_one_or_none()

    await searching_msg.delete()

    if not user_found:
        await message.answer(
            f"❌ کاربری با نام کاربری یا شماره موبایل **'{query_text}'** یافت نشد.",
            reply_markup=get_users_management_keyboard(),
            parse_mode="Markdown"
        )
    else:
        profile_text = await get_user_profile_text(user_found)
        await message.answer(
            profile_text,
            reply_markup=get_user_profile_return_keyboard(back_to_page=1),
            parse_mode="Markdown"
        )