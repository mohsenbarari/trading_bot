# trading_bot/bot/handlers/admin_users.py

from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, or_, func
from typing import Optional
import jdatetime
import pytz
import asyncio
import logging
from datetime import datetime, timedelta

from core.db import AsyncSessionLocal
from models.user import User
from core.enums import UserRole, NotificationLevel, NotificationCategory
from core.utils import normalize_account_name, normalize_persian_numerals, to_jalali_str, create_user_notification, send_telegram_notification
from bot.keyboards import (
    get_users_management_keyboard, 
    get_admin_panel_keyboard, 
    get_users_list_inline_keyboard,
    get_user_profile_return_keyboard,
    get_user_role_edit_keyboard,
    get_user_delete_confirm_keyboard,
    get_user_settings_keyboard,
    get_block_duration_keyboard,
    get_limit_duration_keyboard,
    get_skip_keyboard
)
from bot.states import UserManagement, UserLimitations

logger = logging.getLogger(__name__)
router = Router()
USERS_PER_PAGE = 10

# --- توابع کمکی مدیریت پیام ---

async def safe_delete_message(bot: Bot, chat_id: int, message_id: int, delay: int = 0):
    """پیام را با تأخیر اختیاری حذف می‌کند و خطاها را نادیده می‌گیرد."""
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

async def update_anchor(state: FSMContext, new_message_id: int, bot: Bot, chat_id: int):
    """
    لنگر محتوا (Content Anchor) را آپدیت می‌کند.
    پیام قبلی محتوا را با تاخیر ۳۰ ثانیه حذف می‌کند.
    """
    data = await state.get_data()
    old_anchor_id = data.get("anchor_id")
    
    # 1. ثبت لنگر جدید
    await state.update_data(anchor_id=new_message_id)
    
    # 2. حذف لنگر قدیمی با تاخیر ۳۰ ثانیه
    if old_anchor_id and old_anchor_id != new_message_id:
        asyncio.create_task(safe_delete_message(bot, chat_id, old_anchor_id, delay=30))

async def clear_state_retain_anchors(state: FSMContext):
    """
    استیت را پاک می‌کند اما لنگرها (منو و محتوا) را حفظ می‌کند.
    """
    data = await state.get_data()
    anchor_id = data.get("anchor_id")
    menu_id = data.get("users_menu_id") 
    
    await state.clear()
    
    updates = {}
    if anchor_id: updates["anchor_id"] = anchor_id
    if menu_id: updates["users_menu_id"] = menu_id
    
    if updates:
        await state.update_data(**updates)

async def delete_user_message(message: types.Message):
    """پیام کاربر را بلافاصله حذف می‌کند."""
    try:
        await message.delete()
    except Exception:
        pass

# --- توابع نمایش (Views) ---

async def show_users_list(bot: Bot, chat_id: int, state: FSMContext, page: int, message_id_to_edit: int = None):
    """لیست کاربران را نمایش می‌دهد."""
    try:
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

        if message_id_to_edit:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id_to_edit, text=text, reply_markup=keyboard, parse_mode="Markdown")
            except Exception:
                msg = await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="Markdown")
                await update_anchor(state, msg.message_id, bot, chat_id)
        else:
            msg = await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="Markdown")
            await update_anchor(state, msg.message_id, bot, chat_id)
            
    except Exception as e:
        logger.error(f"Error showing users list: {e}")
        error_msg = await bot.send_message(chat_id, "❌ خطایی در دریافت لیست کاربران رخ داد.")
        asyncio.create_task(safe_delete_message(bot, chat_id, error_msg.message_id, delay=30))


async def get_user_profile_text(target_user: User) -> str:
    # استفاده از تابع کمکی to_jalali_str برای تبدیل تاریخ
    join_date = to_jalali_str(target_user.created_at, "%Y/%m/%d - %H:%M") if target_user.created_at else "نامشخص"

    restriction_text = "✅ آزاد"
    if target_user.trading_restricted_until:
        # فرض بر این است که زمان در دیتابیس به صورت UTC ذخیره شده است (naive)
        # برای مقایسه، از datetime.utcnow() استفاده می‌کنیم که naive است.
        if target_user.trading_restricted_until > datetime.utcnow():
            if target_user.trading_restricted_until.year > 2100:
                restriction_text = "⛔ مسدود دائم"
            else:
                jalali_str = to_jalali_str(target_user.trading_restricted_until, "%Y/%m/%d - %H:%M")
                restriction_text = f"⛔ تا {jalali_str}"
        else:
            restriction_text = "✅ آزاد (منقضی شده)"
    
    # نمایش محدودیت‌ها
    limitations_text = ""
    if target_user.max_daily_trades or target_user.max_active_commodities or target_user.max_daily_requests:
        limitations_parts = []
        if target_user.max_daily_trades:
            limitations_parts.append(f"معاملات روزانه: {target_user.max_daily_trades}")
        if target_user.max_active_commodities:
            limitations_parts.append(f"کالاهای فعال: {target_user.max_active_commodities}")
        if target_user.max_daily_requests:
            limitations_parts.append(f"درخواست‌های روزانه: {target_user.max_daily_requests}")
        
        limitations_text = "\n⚠️ **محدودیت‌های فعال:**\n" + "\n".join([f"   • {part}" for part in limitations_parts])
        
        if target_user.limitations_expire_at:
            expire_str = to_jalali_str(target_user.limitations_expire_at, "%Y/%m/%d - %H:%M")
            limitations_text += f"\n   📅 انقضا: {expire_str}"

    profile_text = (
        f"👤 **پروفایل کاربر**\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"🆔 **نام کاربری:** `{target_user.account_name or '---'}`\n"
        f"📱 **شماره موبایل:** `{target_user.mobile_number or '---'}`\n"
        f"🔰 **سطح دسترسی:** {target_user.role.value}\n"
        f"🤖 **دسترسی بات:** {'✅ فعال' if target_user.has_bot_access else '❌ غیرفعال'}\n"
        f"🔒 **وضعیت حساب:** {restriction_text}\n"
        f"📅 **تاریخ عضویت:** {join_date}\n"
        f"{limitations_text}"
    )
    return profile_text

# --- هندلرها ---

@router.message(F.text == "👥 مدیریت کاربران")
async def handle_users_menu(message: types.Message, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    await delete_user_message(message)
    
    msg = await message.answer(
        "👥 **مدیریت کاربران**\n\n"
        "لطفاً گزینه مورد نظر را انتخاب کنید:",
        reply_markup=get_users_management_keyboard(),
        parse_mode="Markdown"
    )
    
    await state.update_data(users_menu_id=msg.message_id)

@router.message(F.text == "📋 لیست کاربران")
async def handle_users_list_command(message: types.Message, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    await delete_user_message(message)
    await clear_state_retain_anchors(state)
    
    await show_users_list(message.bot, message.chat.id, state, page=1)

@router.callback_query(F.data.startswith("users_page_"))
async def handle_users_pagination(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    page = int(callback.data.split("_")[-1])
    await show_users_list(callback.bot, callback.message.chat.id, state, page, message_id_to_edit=callback.message.message_id)
    await callback.answer()

@router.callback_query(F.data.startswith("user_profile_"))
async def handle_view_user_profile(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return

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
                    if button.callback_data and "users_page_" in button.callback_data:
                        current_page = int(button.callback_data.split("_")[-1])
                        break
                else: continue
                break
    except Exception:
        pass 
    
    await callback.message.edit_text(
        profile_text,
        reply_markup=get_user_profile_return_keyboard(user_id=target_user.id, back_to_page=current_page),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(F.text == "🔙 بازگشت به پنل مدیریت")
async def handle_back_to_admin(message: types.Message, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    await delete_user_message(message)
    
    data = await state.get_data()
    content_anchor = data.get("anchor_id")
    menu_anchor = data.get("users_menu_id")
    
    await state.clear()
    
    if content_anchor:
        asyncio.create_task(safe_delete_message(message.bot, message.chat.id, content_anchor, delay=30))
    
    if menu_anchor:
        asyncio.create_task(safe_delete_message(message.bot, message.chat.id, menu_anchor, delay=30))
    
    msg = await message.answer(
        "به پنل مدیریت بازگشتید.",
        reply_markup=get_admin_panel_keyboard()
    )

# --- جستجوی کاربر ---

@router.message(F.text == "🔍 جستجوی کاربر")
async def start_search_user(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    await delete_user_message(message)
    
    await state.set_state(UserManagement.awaiting_search_query)
    
    # 👇 اصلاح شد: استفاده از دکمه لغو اختصاصی
    cancel_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="❌ لغو عملیات", callback_data="user_search_cancel")]
    ])
    
    msg = await message.answer(
        "🔎 لطفاً **نام کاربری (Account Name)** یا **شماره موبایل** کاربر را وارد کنید:\n\n"
        "(برای لغو از دکمه شیشه‌ای زیر استفاده کنید)",
        reply_markup=cancel_kb, # 👈 استفاده از کیبورد اختصاصی
        parse_mode="Markdown"
    )
    await update_anchor(state, msg.message_id, message.bot, message.chat.id)

# 👇 هندلر جدید برای لغو عملیات جستجوی کاربر
@router.callback_query(F.data == "user_search_cancel")
async def handle_user_search_cancel(query: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    await clear_state_retain_anchors(state)
    
    # بازگشت به منوی مدیریت کاربران (و حفظ منطق Rolling Anchor)
    msg = await query.message.answer(
        "👥 **مدیریت کاربران**\n\n"
        "لطفاً گزینه مورد نظر را انتخاب کنید:",
        reply_markup=get_users_management_keyboard(),
        parse_mode="Markdown"
    )
    
    # پیام فرم جستجو (که لنگر قبلی بود) ۳۰ ثانیه بعد توسط این تابع حذف می‌شود
    await update_anchor(state, msg.message_id, query.bot, query.message.chat.id)
    await query.answer("عملیات لغو شد")

@router.message(UserManagement.awaiting_search_query)
async def process_search_query(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await state.clear()
        return
    
    await delete_user_message(message)
    query_text = message.text.strip()
    
    query_text_normalized_account = normalize_account_name(query_text)
    query_text_normalized_mobile = normalize_persian_numerals(query_text)
    
    await clear_state_retain_anchors(state)

    if not query_text:
        msg = await message.answer(
            "❌ متن جستجو نمی‌تواند خالی باشد.", 
            reply_markup=get_users_management_keyboard()
        )
        await update_anchor(state, msg.message_id, message.bot, message.chat.id)
        return

    searching_msg = await message.answer("⏳ در حال جستجو...")
    await update_anchor(state, searching_msg.message_id, message.bot, message.chat.id)

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(
            or_(
                User.account_name == query_text_normalized_account,
                User.mobile_number == query_text_normalized_mobile
            )
        )
        user_found = (await session.execute(stmt)).scalar_one_or_none()

    if not user_found:
        msg = await message.answer(
            f"❌ کاربری با نام کاربری یا شماره موبایل **'{query_text}'** یافت نشد.",
            reply_markup=get_users_management_keyboard(),
            parse_mode="Markdown"
        )
        await update_anchor(state, msg.message_id, message.bot, message.chat.id)
    else:
        profile_text = await get_user_profile_text(user_found)
        msg = await message.answer(
            profile_text,
            reply_markup=get_user_profile_return_keyboard(user_id=user_found.id, back_to_page=1),
            parse_mode="Markdown"
        )
        await update_anchor(state, msg.message_id, message.bot, message.chat.id)

# --- هندلرهای مدیریت کاربر (ویرایش نقش، دسترسی بات، حذف) ---

@router.callback_query(F.data.startswith("user_settings_"))
async def handle_user_settings(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    # برای نمایش منوی تنظیمات، متن پیام را تغییر نمی‌دهیم (یا می‌توانیم همان پروفایل را نگه داریم)
    # اما کیبورد را عوض می‌کنیم.
    # بهتر است متن پروفایل را دوباره بگیریم تا اگر تغییری کرده (مثلاً وضعیت بات) به‌روز باشد.
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
    if not target_user:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    profile_text = await get_user_profile_text(target_user)
    
    # بررسی وضعیت مسدودی برای نمایش دکمه مناسب
    is_restricted = False
    # مقایسه با datetime.utcnow() (naive)
    if target_user.trading_restricted_until and target_user.trading_restricted_until > datetime.utcnow():
        is_restricted = True
    
    # بررسی وجود محدودیت
    has_limitations = (
        target_user.max_daily_trades is not None or
        target_user.max_active_commodities is not None or
        target_user.max_daily_requests is not None
    )

    try:
        await callback.message.edit_text(
            profile_text,
            reply_markup=get_user_settings_keyboard(target_user_id, is_restricted=is_restricted, has_limitations=has_limitations),
            parse_mode="Markdown"
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("user_block_"))
async def handle_user_block_actions(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    # هندل کردن درخواست اولیه بلاک (نمایش منوی زمان)
    if callback.data.startswith("user_block_") and not callback.data.startswith("user_block_apply_"):
        target_user_id = int(callback.data.split("_")[-1])
        await callback.message.edit_text(
            "⏳ **مدت زمان مسدودیت را انتخاب کنید:**",
            reply_markup=get_block_duration_keyboard(target_user_id),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    # هندل کردن اعمال بلاک
    if callback.data.startswith("user_block_apply_"):
        parts = callback.data.split("_")
        target_user_id = int(parts[3])
        minutes = int(parts[4])
        
        async with AsyncSessionLocal() as session:
            stmt = select(User).where(User.id == target_user_id)
            target_user = (await session.execute(stmt)).scalar_one_or_none()
            
            if target_user:
                if minutes == 0:
                    # نامحدود (100 سال) - استفاده از utcnow (naive)
                    target_user.trading_restricted_until = datetime.utcnow() + timedelta(days=36500)
                    msg_text = "⛔ کاربر به صورت **دائم** مسدود شد."
                    is_permanent = True
                else:
                    # استفاده از utcnow (naive)
                    target_user.trading_restricted_until = datetime.utcnow() + timedelta(minutes=minutes)
                    msg_text = f"⛔ کاربر به مدت **{minutes} دقیقه** مسدود شد."
                    is_permanent = False
                
                await session.commit()
                
                # --- Send Notification to blocked user ---
                jalali_date = to_jalali_str(target_user.trading_restricted_until)
                if is_permanent:
                    block_message = (
                        f"⛔ *اخطار مسدودیت حساب*\n\n"
                        f"حساب کاربری شما به صورت *دائمی* مسدود شده است.\n"
                        f"برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
                    )
                else:
                    block_message = (
                        f"⛔ *اخطار مسدودیت حساب*\n\n"
                        f"حساب کاربری شما موقتاً مسدود شده است.\n\n"
                        f"📅 *پایان مسدودیت:* {jalali_date}\n\n"
                        f"تا زمان رفع مسدودیت امکان انجام معاملات وجود ندارد."
                    )
                # In-app notification
                await create_user_notification(
                    session, target_user.id, block_message,
                    level=NotificationLevel.WARNING,
                    category=NotificationCategory.SYSTEM
                )
                # Telegram notification
                await send_telegram_notification(target_user.telegram_id, block_message)
                
                # بررسی وجود محدودیت
                has_limitations = (
                    target_user.max_daily_trades is not None or
                    target_user.max_active_commodities is not None or
                    target_user.max_daily_requests is not None
                )
                
                # بازگشت به تنظیمات
                profile_text = await get_user_profile_text(target_user)
                await callback.message.edit_text(
                    profile_text,
                    reply_markup=get_user_settings_keyboard(target_user.id, is_restricted=True, has_limitations=has_limitations),
                    parse_mode="Markdown"
                )
                await callback.answer(msg_text, show_alert=True)
            else:
                await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return


@router.callback_query(F.data.startswith("user_unblock_"))
async def handle_user_unblock(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            target_user.trading_restricted_until = None
            await session.commit()
            
            # بررسی وجود محدودیت
            has_limitations = (
                target_user.max_daily_trades is not None or
                target_user.max_active_commodities is not None or
                target_user.max_daily_requests is not None
            )
            
            # بازگشت به تنظیمات
            profile_text = await get_user_profile_text(target_user)
            await callback.message.edit_text(
                profile_text,
                reply_markup=get_user_settings_keyboard(target_user.id, is_restricted=False, has_limitations=has_limitations),
                parse_mode="Markdown"
            )
            await callback.answer("✅ رفع مسدودیت انجام شد.", show_alert=True)
        else:
            await callback.answer("❌ کاربر یافت نشد.", show_alert=True)


@router.callback_query(F.data.startswith("user_unlimit_"))
async def handle_user_unlimit(callback: types.CallbackQuery, user: Optional[User]):
    """رفع محدودیت‌های کاربر"""
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            # حذف تمام محدودیت‌ها
            target_user.max_daily_trades = None
            target_user.max_active_commodities = None
            target_user.max_daily_requests = None
            target_user.limitations_expire_at = None
            await session.commit()
            
            # بررسی وضعیت مسدودی
            is_restricted = False
            if target_user.trading_restricted_until and target_user.trading_restricted_until > datetime.utcnow():
                is_restricted = True
            
            # بازگشت به تنظیمات
            profile_text = await get_user_profile_text(target_user)
            await callback.message.edit_text(
                profile_text,
                reply_markup=get_user_settings_keyboard(target_user.id, is_restricted=is_restricted, has_limitations=False),
                parse_mode="Markdown"
            )
            await callback.answer("✅ محدودیت‌ها برداشته شد.", show_alert=True)
        else:
            await callback.answer("❌ کاربر یافت نشد.", show_alert=True)


@router.callback_query(F.data.startswith("user_edit_role_"))
async def handle_user_edit_role(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    await callback.message.edit_text(
        "🎭 لطفاً نقش جدید کاربر را انتخاب کنید:",
        reply_markup=get_user_role_edit_keyboard(target_user_id)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("set_user_role_"))
async def handle_set_user_role(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    # format: set_user_role_{user_id}_{role_name}
    parts = callback.data.split("_")
    target_user_id = int(parts[3])
    role_name = "_".join(parts[4:])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            target_user.role = UserRole[role_name]
            await session.commit()
            
            # بازگشت به پروفایل (یا تنظیمات؟ معمولاً بعد از تغییر نقش به پروفایل برمی‌گردیم تا نتیجه را ببینیم)
            # اما چون دکمه بازگشت در کیبورد نقش‌ها به پروفایل برمی‌گردد، اینجا هم به پروفایل برمی‌گردیم.
            profile_text = await get_user_profile_text(target_user)
            try:
                await callback.message.edit_text(
                    profile_text,
                    reply_markup=get_user_profile_return_keyboard(user_id=target_user.id),
                    parse_mode="Markdown"
                )
            except TelegramBadRequest:
                pass
            await callback.answer("✅ نقش کاربر تغییر کرد.")
        else:
            await callback.answer("❌ کاربر یافت نشد.", show_alert=True)

@router.callback_query(F.data.startswith("user_toggle_bot_"))
async def handle_user_toggle_bot(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            target_user.has_bot_access = not target_user.has_bot_access
            await session.commit()
            
            # بازگشت به منوی تنظیمات (چون از آنجا آمده‌ایم)
            profile_text = await get_user_profile_text(target_user)
            
            # بررسی وضعیت مسدودی
            is_restricted = False
            if target_user.trading_restricted_until and target_user.trading_restricted_until > datetime.utcnow():
                is_restricted = True
            
            # بررسی وجود محدودیت
            has_limitations = (
                target_user.max_daily_trades is not None or
                target_user.max_active_commodities is not None or
                target_user.max_daily_requests is not None
            )
            
            try:
                await callback.message.edit_text(
                    profile_text,
                    reply_markup=get_user_settings_keyboard(user_id=target_user.id, is_restricted=is_restricted, has_limitations=has_limitations),
                    parse_mode="Markdown"
                )
            except TelegramBadRequest:
                pass
            
            status = "فعال" if target_user.has_bot_access else "غیرفعال"
            await callback.answer(f"✅ دسترسی بات {status} شد.", show_alert=True)
        else:
            await callback.answer("❌ کاربر یافت نشد.", show_alert=True)

@router.callback_query(F.data.startswith("user_ask_delete_"))
async def handle_user_delete_request(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    await callback.message.edit_text(
        "⚠️ **آیا از حذف این کاربر اطمینان دارید؟**\n\n"
        "این عملیات غیرقابل بازگشت است.",
        reply_markup=get_user_delete_confirm_keyboard(target_user_id),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("user_delete_confirm_"))
async def handle_user_delete_confirm(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            await session.delete(target_user)
            await session.commit()
            
            await callback.answer("✅ کاربر با موفقیت حذف شد.")
            
            # بازگشت به لیست کاربران
            await show_users_list(callback.bot, callback.message.chat.id, state, page=1, message_id_to_edit=callback.message.message_id)
        else:
            await callback.answer("❌ کاربر یافت نشد یا قبلاً حذف شده است.", show_alert=True)
            await show_users_list(callback.bot, callback.message.chat.id, state, page=1, message_id_to_edit=callback.message.message_id)

# --- هندلرهای محدودسازی کاربر (رویکرد جدید با دکمه‌ها) ---

def get_limit_panel_text(max_trades, max_commodities, max_requests):
    """ساخت متن پنل محدودیت‌ها"""
    trades_str = str(max_trades) if max_trades else "---"
    commodities_str = str(max_commodities) if max_commodities else "---"
    requests_str = str(max_requests) if max_requests else "---"
    
    return (
        "⚠️ **تنظیم محدودیت‌ها**\n\n"
        f"📊 تعداد معاملات روزانه: **{trades_str}**\n"
        f"📦 تعداد سکه فعال: **{commodities_str}**\n"
        f"📨 تعداد درخواست روزانه: **{requests_str}**\n\n"
        "برای تنظیم هر مورد روی دکمه مربوطه کلیک کنید.\n"
        "پس از اتمام، دکمه **تایید** را بزنید."
    )

@router.callback_query(F.data.startswith("user_limit_"))
async def handle_user_limit_start(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    # Handle duration selection
    if callback.data.startswith("user_limit_dur_"):
        parts = callback.data.split("_")
        target_user_id = int(parts[3])
        minutes = int(parts[4])
        
        # ذخیره مدت زمان
        if minutes == 0:
            expire_at = datetime.utcnow() + timedelta(days=36500)
        else:
            expire_at = datetime.utcnow() + timedelta(minutes=minutes)
        
        await state.update_data(
            limit_target_user_id=target_user_id,
            limit_expire_at=expire_at,
            limit_max_trades=None,
            limit_max_commodities=None,
            limit_max_requests=None
        )
        
        # نمایش پنل محدودیت‌ها
        from bot.keyboards import get_limit_settings_keyboard
        await callback.message.edit_text(
            get_limit_panel_text(None, None, None),
            reply_markup=get_limit_settings_keyboard(target_user_id),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    # Initial request (show duration keyboard)
    target_user_id = int(callback.data.split("_")[-1])
    await callback.message.edit_text(
        "⏳ **مدت زمان محدودیت را انتخاب کنید:**",
        reply_markup=get_limit_duration_keyboard(target_user_id),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("limit_set_trades_"))
async def handle_set_trades(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    await state.update_data(limit_editing="trades")
    await state.set_state(UserLimitations.awaiting_limit_value)
    
    await callback.message.edit_text(
        "📊 **حداکثر تعداد معاملات روزانه** را وارد کنید:\n\n"
        "(یک عدد وارد کنید)",
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("limit_set_commodities_"))
async def handle_set_commodities(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    await state.update_data(limit_editing="commodities")
    await state.set_state(UserLimitations.awaiting_limit_value)
    
    await callback.message.edit_text(
        "📦 **حداکثر تعداد سکه فعال** را وارد کنید:\n\n"
        "(یک عدد وارد کنید)",
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("limit_set_requests_"))
async def handle_set_requests(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    await state.update_data(limit_editing="requests")
    await state.set_state(UserLimitations.awaiting_limit_value)
    
    await callback.message.edit_text(
        "📨 **حداکثر تعداد درخواست روزانه** را وارد کنید:\n\n"
        "(یک عدد وارد کنید)",
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(UserLimitations.awaiting_limit_value)
async def process_limit_value(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await state.clear()
        return
    
    await delete_user_message(message)
    
    try:
        value = int(message.text.strip())
        if value < 0:
            raise ValueError
    except ValueError:
        temp_msg = await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید.")
        asyncio.create_task(safe_delete_message(message.bot, message.chat.id, temp_msg.message_id, delay=3))
        return
    
    data = await state.get_data()
    editing = data.get("limit_editing")
    target_user_id = data.get("limit_target_user_id")
    
    # ذخیره مقدار در state
    if editing == "trades":
        await state.update_data(limit_max_trades=value)
    elif editing == "commodities":
        await state.update_data(limit_max_commodities=value)
    elif editing == "requests":
        await state.update_data(limit_max_requests=value)
    
    await state.set_state(None)  # خروج از FSM
    
    # بازگشت به پنل با مقادیر به‌روز شده
    data = await state.get_data()
    max_trades = data.get("limit_max_trades")
    max_commodities = data.get("limit_max_commodities")
    max_requests = data.get("limit_max_requests")
    
    from bot.keyboards import get_limit_settings_keyboard
    msg = await message.answer(
        get_limit_panel_text(max_trades, max_commodities, max_requests),
        reply_markup=get_limit_settings_keyboard(target_user_id, max_trades, max_commodities, max_requests),
        parse_mode="Markdown"
    )
    await update_anchor(state, msg.message_id, message.bot, message.chat.id)

@router.callback_query(F.data.startswith("limit_confirm_"))
async def handle_limit_confirm(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    """تایید و اعمال محدودیت‌ها"""
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    data = await state.get_data()
    target_user_id = data.get("limit_target_user_id")
    expire_at = data.get("limit_expire_at")
    max_trades = data.get("limit_max_trades")
    max_commodities = data.get("limit_max_commodities")
    max_requests = data.get("limit_max_requests")
    
    # اگر هیچ محدودیتی تنظیم نشده
    if not max_trades and not max_commodities and not max_requests:
        await callback.answer("⚠️ لطفاً حداقل یک محدودیت تنظیم کنید.", show_alert=True)
        return
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            target_user.max_daily_trades = max_trades
            target_user.max_active_commodities = max_commodities
            target_user.max_daily_requests = max_requests
            target_user.limitations_expire_at = expire_at
            await session.commit()
            
            # --- Send Notification to limited user ---
            limitations_changed = []
            if max_trades is not None:
                limitations_changed.append(f"تعداد ترید روزانه: {max_trades}")
            if max_commodities is not None:
                limitations_changed.append(f"تعداد کالای فعال: {max_commodities}")
            if max_requests is not None:
                limitations_changed.append(f"تعداد درخواست روزانه: {max_requests}")
            
            if limitations_changed:
                expire_jalali = to_jalali_str(expire_at) if expire_at else "نامحدود"
                limitation_message = (
                    f"⚠️ *اعمال محدودیت*\n\n"
                    f"محدودیت‌های زیر برای حساب شما اعمال شده است:\n\n"
                )
                for lim in limitations_changed:
                    limitation_message += f"• {lim}\n"
                limitation_message += f"\n📅 *اعتبار تا:* {expire_jalali}"
                
                await create_user_notification(
                    session, target_user.id, limitation_message,
                    level=NotificationLevel.WARNING,
                    category=NotificationCategory.SYSTEM
                )
                await send_telegram_notification(target_user.telegram_id, limitation_message)
            
            await clear_state_retain_anchors(state)
            
            profile_text = await get_user_profile_text(target_user)
            await callback.message.edit_text(
                profile_text,
                reply_markup=get_user_profile_return_keyboard(user_id=target_user.id),
                parse_mode="Markdown"
            )
            await callback.answer("✅ محدودیت‌ها اعمال شد.", show_alert=True)
        else:
            await callback.answer("❌ کاربر یافت نشد.", show_alert=True)

@router.callback_query(F.data.startswith("limit_cancel_"))
async def handle_limit_cancel(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    """انصراف از اعمال محدودیت"""
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    await clear_state_retain_anchors(state)
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            is_restricted = target_user.trading_restricted_until and target_user.trading_restricted_until > datetime.utcnow()
            has_limitations = (
                target_user.max_daily_trades is not None or
                target_user.max_active_commodities is not None or
                target_user.max_daily_requests is not None
            )
            
            profile_text = await get_user_profile_text(target_user)
            await callback.message.edit_text(
                profile_text,
                reply_markup=get_user_settings_keyboard(target_user.id, is_restricted=is_restricted, has_limitations=has_limitations),
                parse_mode="Markdown"
            )
    
    await callback.answer("عملیات لغو شد.")