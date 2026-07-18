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
from datetime import datetime, timedelta, timezone

from core.admin_authority import admin_write_rejection_message, check_shared_admin_write_authority
from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import current_server
from core.services.user_account_status_service import get_user_account_status, transition_user_account_status
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.services.user_management_context_service import (
    apply_user_management_order,
    attach_user_management_relation_context,
)
from models.user import User
from core.enums import UserRole, NotificationLevel, NotificationCategory, UserAccountStatus
from core.utils import normalize_account_name, normalize_persian_numerals, to_jalali_str, create_user_notification, send_telegram_notification
from bot.keyboards import (
    get_users_list_inline_keyboard,
    get_user_profile_return_keyboard,
    get_user_role_edit_keyboard,
    get_user_delete_webapp_redirect_keyboard,
    get_user_settings_keyboard,
    get_block_duration_keyboard,
    get_limit_duration_keyboard,
    get_skip_keyboard,
    get_block_settings_keyboard,
    get_max_block_options_keyboard
)
from bot.repeat_offer import (
    build_admin_panel_navigation_keyboard,
    build_users_management_navigation_keyboard,
)
from bot.telegram_callback_answer import answer_callback_query_via_runtime
from bot.telegram_interaction_message import answer_incoming_message_via_runtime
from bot.states import UserManagement, UserLimitations
from bot.utils.customer_display import attach_customer_management_names, user_display_name

logger = logging.getLogger(__name__)
router = Router()
USERS_PER_PAGE = 10
ADMIN_MANAGEMENT_ROLES = {UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER}
ADMIN_ROLE_VALUES = {UserRole.SUPER_ADMIN.value, UserRole.MIDDLE_MANAGER.value}


def _users_admin_write_decision(operation: str):
    return check_shared_admin_write_authority(
        "users",
        operation=operation,
        surface="telegram_bot_admin",
    )


async def _reject_users_callback_if_not_authoritative(callback: types.CallbackQuery, operation: str) -> bool:
    decision = _users_admin_write_decision(operation)
    if decision.ok:
        return False
    await answer_callback_query_via_runtime(callback, f"❌ {admin_write_rejection_message(decision)}", show_alert=True)
    return True


async def _reject_users_message_if_not_authoritative(
    message: types.Message,
    user: Optional[User],
    operation: str,
) -> bool:
    decision = _users_admin_write_decision(operation)
    if decision.ok:
        return False
    await answer_incoming_message_via_runtime(
        message,
        user,
        f"❌ {admin_write_rejection_message(decision)}",
        source_key="admin-users-not-authoritative",
    )
    return True


def _can_open_user_management(user: Optional[User]) -> bool:
    return bool(user and user.role in ADMIN_MANAGEMENT_ROLES)


def _can_edit_target_role(user: Optional[User]) -> bool:
    return bool(user and user.role == UserRole.SUPER_ADMIN)


def _is_admin_role_value(role: object) -> bool:
    normalized = getattr(role, "value", role)
    return normalized in ADMIN_ROLE_VALUES


def _can_manage_target_user(actor: Optional[User], target_user: Optional[User]) -> bool:
    if not actor or not target_user:
        return False
    if actor.role == UserRole.SUPER_ADMIN:
        return True
    return not _is_admin_role_value(target_user.role)


def _build_webapp_user_profile_url(user_id: int) -> str | None:
    frontend_url = (getattr(settings, "frontend_url", "") or "").strip().rstrip("/")
    if not frontend_url:
        return None
    return f"{frontend_url}/admin/users/{user_id}"


def _target_user_display_name(target_user: User) -> str:
    target_user_id = getattr(target_user, "id", None)
    fallback = getattr(target_user, "mobile_number", None) or f"User {target_user_id}"
    return user_display_name(target_user, fallback)


async def _show_user_delete_webapp_redirect(callback: types.CallbackQuery, target_user: User) -> None:
    profile_url = _build_webapp_user_profile_url(target_user.id)
    display_name = _target_user_display_name(target_user)
    link_line = (
        f"\n\nلینک مستقیم پروفایل:\n{profile_url}"
        if profile_url
        else "\n\nلینک وب اپ در تنظیمات سرور ثبت نشده است."
    )
    await callback.message.edit_text(
        "حذف کاربر از داخل بات برای حفظ مرجعیت داده غیرفعال است.\n\n"
        f"کاربر: {display_name}\n"
        "برای حذف، پروفایل همین کاربر را در وب اپ باز کنید و حذف را از همانجا انجام دهید."
        f"{link_line}",
        reply_markup=get_user_delete_webapp_redirect_keyboard(target_user.id, profile_url),
    )
    await answer_callback_query_via_runtime(callback)


def _apply_user_management_scope(stmt, actor: Optional[User]):
    if actor and actor.role == UserRole.MIDDLE_MANAGER:
        return stmt.where(User.role.notin_([UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER]))
    return stmt

# --- توابع کمکی مدیریت پیام ---

async def safe_delete_message(bot: Bot, chat_id: int, message_id: int, delay: int = 0):
    """پیام را با تأخیر اختیاری حذف می‌کند و خطاها را نادیده می‌گیرد."""
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def schedule_temporary_message_cleanup(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    delay: int,
    source_id: str,
) -> None:
    if (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        from core.services.telegram_scheduled_operation_service import (
            enqueue_temporary_message_cleanup_once,
        )

        async with AsyncSessionLocal() as session:
            await enqueue_temporary_message_cleanup_once(
                session,
                current_server=current_server(),
                chat_id=chat_id,
                message_id=message_id,
                source_id=source_id,
                due_at=datetime.now(timezone.utc)
                + timedelta(seconds=max(0, int(delay))),
            )
            await session.commit()
        return
    asyncio.create_task(
        safe_delete_message(bot, chat_id, message_id, delay=delay)
    )

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

async def send_delayed_removal_notification(
    user_id: int,
    telegram_id: int,
    is_block: bool,
    delay_seconds: int = 120,
    include_telegram: bool = True,
):
    """ارسال نوتیفیکیشن رفع مسدودیت/محدودیت با تاخیر (پیش‌فرض ۲ دقیقه)
    
    قبل از ارسال بررسی می‌کند که آیا کاربر هنوز رفع محدودیت/مسدودیت است یا خیر.
    اگر مجدداً محدود شده باشد، نوتیفیکیشن ارسال نمی‌شود.
    """
    await asyncio.sleep(delay_seconds)
    
    # بررسی وضعیت فعلی کاربر قبل از ارسال نوتیفیکیشن
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            return  # کاربر حذف شده
        
        if is_block:
            # بررسی مسدودیت: اگر مجدداً مسدود شده، نوتیفیکیشن ارسال نشود
            if user.trading_restricted_until and user.trading_restricted_until > datetime.utcnow():
                return  # هنوز مسدود است، نوتیفیکیشن رفع مسدودیت ارسال نشود
            
            msg = (
                "ℹ️ *رفع مسدودیت توسط مدیر*\n\n"
                "مسدودیت حساب شما توسط مدیر رفع شد."
            )
        else:
            # بررسی محدودیت: اگر مجدداً محدود شده، نوتیفیکیشن ارسال نشود
            has_limitations = (
                user.max_daily_trades is not None or
                user.max_active_commodities is not None or
                user.max_daily_requests is not None
            )
            if has_limitations:
                return  # هنوز محدود است، نوتیفیکیشن رفع محدودیت ارسال نشود
            
            msg = (
                "ℹ️ *رفع محدودیت توسط مدیر*\n\n"
                "محدودیت‌های حساب شما توسط مدیر رفع شد."
            )
        
        await create_user_notification(
            session, user_id, msg,
            level=NotificationLevel.INFO,
            category=NotificationCategory.SYSTEM
        )
    if include_telegram:
        await send_telegram_notification(telegram_id, msg)


async def enqueue_delayed_removal_telegram_notification(
    session,
    *,
    user: User,
    is_block: bool,
    delay_seconds: int = 120,
) -> None:
    if user.telegram_id is None:
        return
    from core.services.telegram_notification_outbox_service import (
        TelegramNotificationRecipient,
        enqueue_delayed_restriction_telegram_notification_once,
    )

    kind = "block" if is_block else "limitations"
    msg = (
        "ℹ️ *رفع مسدودیت توسط مدیر*\n\nمسدودیت حساب شما توسط مدیر رفع شد."
        if is_block
        else "ℹ️ *رفع محدودیت توسط مدیر*\n\nمحدودیت‌های حساب شما توسط مدیر رفع شد."
    )
    due_at = datetime.now(timezone.utc) + timedelta(
        seconds=max(0, int(delay_seconds))
    )
    await enqueue_delayed_restriction_telegram_notification_once(
        session,
        recipient=TelegramNotificationRecipient(
            user_id=int(user.id),
            telegram_id=int(user.telegram_id),
        ),
        source_id=(
            f"delayed-restriction:{kind}:{user.id}:{due_at.isoformat()}"
        ),
        text=msg,
        restriction_kind=kind,
        not_before=due_at,
        user_sync_version=int(user.sync_version or 0),
    )

# --- توابع نمایش (Views) ---

async def show_users_list(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    page: int,
    message_id_to_edit: int = None,
    actor: Optional[User] = None,
):
    """لیست کاربران را نمایش می‌دهد."""
    try:
        async with AsyncSessionLocal() as session:
            # فقط کاربران فعال (حذف نشده) نمایش داده شوند
            count_stmt = select(func.count()).select_from(User).where(User.is_deleted == False)
            count_stmt = _apply_user_management_scope(count_stmt, actor)
            total_count = (await session.execute(count_stmt)).scalar()
            
            offset = (page - 1) * USERS_PER_PAGE
            stmt = select(User).where(User.is_deleted == False)
            stmt = _apply_user_management_scope(stmt, actor)
            stmt = apply_user_management_order(stmt).offset(offset).limit(USERS_PER_PAGE)
            users = (await session.execute(stmt)).scalars().all()
            await attach_user_management_relation_context(session, users)

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
    async with AsyncSessionLocal() as session:
        await attach_customer_management_names(session, [target_user])

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
        f"🆔 **نام کاربری:** `{_target_user_display_name(target_user)}`\n"
        f"📱 **شماره موبایل:** `{target_user.mobile_number or '---'}`\n"
        f"🔰 **سطح دسترسی:** {target_user.role.value}\n"
        f"🔁 **وضعیت حساب:** {'✅ فعال' if get_user_account_status(target_user) == UserAccountStatus.ACTIVE else '⛔ غیرفعال'}\n"
        f"🔒 **وضعیت معاملات:** {restriction_text}\n"
        f"📅 **تاریخ عضویت:** {join_date}\n"
        f"{limitations_text}"
    )
    return profile_text

# --- هندلرها ---

@router.message(F.text == "👥 مدیریت کاربران")
async def handle_users_menu(message: types.Message, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return
    await delete_user_message(message)
    
    msg = await message.answer(
        "👥 **مدیریت کاربران**\n\n"
        "لطفاً گزینه مورد نظر را انتخاب کنید:",
        reply_markup=await build_users_management_navigation_keyboard(user),
        parse_mode="Markdown"
    )
    
    await state.update_data(users_menu_id=msg.message_id)

@router.message(F.text == "📋 لیست کاربران")
async def handle_users_list_command(message: types.Message, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return
    await delete_user_message(message)
    await clear_state_retain_anchors(state)
    
    await show_users_list(message.bot, message.chat.id, state, page=1, actor=user)

@router.callback_query(F.data.startswith("users_page_"))
async def handle_users_pagination(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return
    
    page = int(callback.data.split("_")[-1])
    await show_users_list(callback.bot, callback.message.chat.id, state, page, message_id_to_edit=callback.message.message_id, actor=user)
    await answer_callback_query_via_runtime(callback)

@router.callback_query(F.data.startswith("user_profile_"))
async def handle_view_user_profile(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return

    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        await attach_customer_management_names(session, [target_user])
        await attach_customer_management_names(session, [target_user])
    
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
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
    
    # بررسی وضعیت مسدودی و محدودیت
    is_restricted = target_user.trading_restricted_until and target_user.trading_restricted_until > datetime.utcnow()
    has_limitations = (
        target_user.max_daily_trades is not None or
        target_user.max_active_commodities is not None or
        target_user.max_daily_requests is not None
    )
    
    await callback.message.edit_text(
        profile_text,
        reply_markup=get_user_profile_return_keyboard(
            user_id=target_user.id, 
            back_to_page=current_page,
            is_restricted=is_restricted,
            has_limitations=has_limitations
        ),
        parse_mode="Markdown"
    )
    await answer_callback_query_via_runtime(callback)

@router.message(F.text == "🔙 بازگشت به پنل مدیریت")
async def handle_back_to_admin(message: types.Message, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return
    await delete_user_message(message)
    
    data = await state.get_data()
    content_anchor = data.get("anchor_id")
    menu_anchor = data.get("users_menu_id")
    queue_mode = (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    )
    
    await state.clear()
    
    if content_anchor and not queue_mode:
        asyncio.create_task(safe_delete_message(message.bot, message.chat.id, content_anchor, delay=30))
    
    if menu_anchor and not queue_mode:
        asyncio.create_task(safe_delete_message(message.bot, message.chat.id, menu_anchor, delay=30))
    
    await answer_incoming_message_via_runtime(
        message,
        user,
        "به پنل مدیریت بازگشتید.",
        source_key="admin-users-back-to-panel",
        reply_markup=await build_admin_panel_navigation_keyboard(user),
        set_persistent_anchor=True,
    )

# --- جستجوی کاربر ---

@router.message(F.text == "🔍 جستجوی کاربر")
async def start_search_user(message: types.Message, state: FSMContext, user: Optional[User]):
    if not _can_open_user_management(user):
        return
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
    if not _can_open_user_management(user):
        return
    
    await clear_state_retain_anchors(state)
    
    # بازگشت به منوی مدیریت کاربران (و حفظ منطق Rolling Anchor)
    msg = await query.message.answer(
        "👥 **مدیریت کاربران**\n\n"
        "لطفاً گزینه مورد نظر را انتخاب کنید:",
        reply_markup=await build_users_management_navigation_keyboard(user),
        parse_mode="Markdown"
    )
    
    # پیام فرم جستجو (که لنگر قبلی بود) ۳۰ ثانیه بعد توسط این تابع حذف می‌شود
    await update_anchor(state, msg.message_id, query.bot, query.message.chat.id)
    await answer_callback_query_via_runtime(query, "عملیات لغو شد")

@router.message(UserManagement.awaiting_search_query)
async def process_search_query(message: types.Message, state: FSMContext, user: Optional[User]):
    if not _can_open_user_management(user):
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
            reply_markup=await build_users_management_navigation_keyboard(user)
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
        await attach_customer_management_names(session, [user_found])

    if not user_found or not _can_manage_target_user(user, user_found):
        msg = await message.answer(
            f"❌ کاربری با نام کاربری یا شماره موبایل **'{query_text}'** یافت نشد.",
            reply_markup=await build_users_management_navigation_keyboard(user),
            parse_mode="Markdown"
        )
        await update_anchor(state, msg.message_id, message.bot, message.chat.id)
    else:
        profile_text = await get_user_profile_text(user_found)
        is_restricted = user_found.trading_restricted_until and user_found.trading_restricted_until > datetime.utcnow()
        has_limitations = (
            user_found.max_daily_trades is not None or
            user_found.max_active_commodities is not None or
            user_found.max_daily_requests is not None
        )
        msg = await message.answer(
            profile_text,
            reply_markup=get_user_profile_return_keyboard(
                user_id=user_found.id, 
                back_to_page=1,
                is_restricted=is_restricted,
                has_limitations=has_limitations
            ),
            parse_mode="Markdown"
        )
        await update_anchor(state, msg.message_id, message.bot, message.chat.id)

# --- هندلرهای مدیریت کاربر (ویرایش نقش، وضعیت حساب، حذف) ---

@router.callback_query(F.data.startswith("user_settings_"))
async def handle_user_settings(callback: types.CallbackQuery, user: Optional[User]):
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    # برای نمایش منوی تنظیمات، متن پیام را تغییر نمی‌دهیم (یا می‌توانیم همان پروفایل را نگه داریم)
    # اما کیبورد را عوض می‌کنیم.
    # بهتر است متن پروفایل را دوباره بگیریم تا اگر تغییری کرده (مثلاً وضعیت بات) به‌روز باشد.
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
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
            reply_markup=get_user_settings_keyboard(
                target_user_id,
                account_status=target_user.account_status,
                is_restricted=is_restricted, 
                has_limitations=has_limitations,
                can_block=target_user.can_block_users,
                max_blocked=target_user.max_blocked_users,
                can_edit_role=_can_edit_target_role(user),
            ),
            parse_mode="Markdown"
        )
    except TelegramBadRequest:
        pass
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data.startswith("user_block_") & ~F.data.startswith("user_block_settings_"))
async def handle_user_block_actions(callback: types.CallbackQuery, user: Optional[User]):
    if not _can_open_user_management(user):
        return
    
    # هندل کردن درخواست اولیه بلاک (نمایش منوی زمان)
    if callback.data.startswith("user_block_") and not callback.data.startswith("user_block_apply_"):
        target_user_id = int(callback.data.split("_")[-1])
        async with AsyncSessionLocal() as session:
            target_user = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
        if not target_user:
            await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
            return
        if not _can_manage_target_user(user, target_user):
            await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
            return
        await callback.message.edit_text(
            "⏳ **مدت زمان مسدودیت را انتخاب کنید:**",
            reply_markup=get_block_duration_keyboard(target_user_id),
            parse_mode="Markdown"
        )
        await answer_callback_query_via_runtime(callback)
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
                if not _can_manage_target_user(user, target_user):
                    await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
                    return
                if await _reject_users_callback_if_not_authoritative(callback, "block"):
                    return
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
                    reply_markup=get_user_settings_keyboard(target_user.id, account_status=target_user.account_status, is_restricted=True, has_limitations=has_limitations, can_edit_role=_can_edit_target_role(user)),
                    parse_mode="Markdown"
                )
                await answer_callback_query_via_runtime(callback, msg_text, show_alert=True)
            else:
                await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return


@router.callback_query(F.data.startswith("user_unblock_"))
async def handle_user_unblock(callback: types.CallbackQuery, user: Optional[User]):
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            if not _can_manage_target_user(user, target_user):
                await answer_callback_query_via_runtime(
                    callback,
                    "❌ شما مجاز به مدیریت این کاربر نیستید.",
                    show_alert=True,
                )
                return
            if await _reject_users_callback_if_not_authoritative(callback, "unblock"):
                return
            telegram_id = target_user.telegram_id  # ذخیره قبل از commit
            target_user.trading_restricted_until = None
            await session.commit()
            
            # ارسال نوتیفیکیشن با تاخیر ۲ دقیقه
            queue_mode = (
                configured_telegram_delivery_runtime().mode
                == TelegramDeliveryRuntimeMode.QUEUE_V1
            )
            if queue_mode:
                await enqueue_delayed_removal_telegram_notification(
                    session,
                    user=target_user,
                    is_block=True,
                )
                await session.commit()
            asyncio.create_task(
                send_delayed_removal_notification(
                    target_user.id,
                    telegram_id,
                    is_block=True,
                    include_telegram=not queue_mode,
                )
            )
            
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
                reply_markup=get_user_settings_keyboard(target_user.id, account_status=target_user.account_status, is_restricted=False, has_limitations=has_limitations, can_edit_role=_can_edit_target_role(user)),
                parse_mode="Markdown"
            )
            await answer_callback_query_via_runtime(
                callback,
                "✅ رفع مسدودیت انجام شد.",
                show_alert=True,
            )
        else:
            await answer_callback_query_via_runtime(
                callback,
                "❌ کاربر یافت نشد.",
                show_alert=True,
            )


@router.callback_query(F.data.startswith("user_unlimit_"))
async def handle_user_unlimit(callback: types.CallbackQuery, user: Optional[User]):
    """رفع محدودیت‌های کاربر"""
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            if not _can_manage_target_user(user, target_user):
                await answer_callback_query_via_runtime(
                    callback,
                    "❌ شما مجاز به مدیریت این کاربر نیستید.",
                    show_alert=True,
                )
                return
            if await _reject_users_callback_if_not_authoritative(callback, "unlimit"):
                return
            telegram_id = target_user.telegram_id  # ذخیره قبل از commit
            # حذف تمام محدودیت‌ها
            target_user.max_daily_trades = None
            target_user.max_active_commodities = None
            target_user.max_daily_requests = None
            target_user.limitations_expire_at = None
            # ریست شمارنده‌ها
            from core.user_counter_sync import reset_user_counters_in_memory

            reset_user_counters_in_memory(target_user)
            await session.commit()
            
            # ارسال نوتیفیکیشن با تاخیر ۲ دقیقه
            queue_mode = (
                configured_telegram_delivery_runtime().mode
                == TelegramDeliveryRuntimeMode.QUEUE_V1
            )
            if queue_mode:
                await enqueue_delayed_removal_telegram_notification(
                    session,
                    user=target_user,
                    is_block=False,
                )
                await session.commit()
            asyncio.create_task(
                send_delayed_removal_notification(
                    target_user.id,
                    telegram_id,
                    is_block=False,
                    include_telegram=not queue_mode,
                )
            )
            
            # بررسی وضعیت مسدودی
            is_restricted = False
            if target_user.trading_restricted_until and target_user.trading_restricted_until > datetime.utcnow():
                is_restricted = True
            
            # بازگشت به تنظیمات
            profile_text = await get_user_profile_text(target_user)
            await callback.message.edit_text(
                profile_text,
                reply_markup=get_user_settings_keyboard(target_user.id, account_status=target_user.account_status, is_restricted=is_restricted, has_limitations=False, can_edit_role=_can_edit_target_role(user)),
                parse_mode="Markdown"
            )
            await answer_callback_query_via_runtime(
                callback,
                "✅ محدودیت‌ها برداشته شد.",
                show_alert=True,
            )
        else:
            await answer_callback_query_via_runtime(
                callback,
                "❌ کاربر یافت نشد.",
                show_alert=True,
            )


@router.callback_query(F.data.startswith("user_edit_role_"))
async def handle_user_edit_role(callback: types.CallbackQuery, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    await callback.message.edit_text(
        "🎭 لطفاً نقش جدید کاربر را انتخاب کنید:",
        reply_markup=get_user_role_edit_keyboard(target_user_id)
    )
    await answer_callback_query_via_runtime(callback)

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
            if await _reject_users_callback_if_not_authoritative(callback, "role_update"):
                return
            target_user.role = UserRole[role_name]
            await session.commit()
            
            # بازگشت به پروفایل (یا تنظیمات؟ معمولاً بعد از تغییر نقش به پروفایل برمی‌گردیم تا نتیجه را ببینیم)
            # اما چون دکمه بازگشت در کیبورد نقش‌ها به پروفایل برمی‌گردد، اینجا هم به پروفایل برمی‌گردیم.
            profile_text = await get_user_profile_text(target_user)
            is_restricted = target_user.trading_restricted_until and target_user.trading_restricted_until > datetime.utcnow()
            has_limitations = (
                target_user.max_daily_trades is not None or
                target_user.max_active_commodities is not None or
                target_user.max_daily_requests is not None
            )
            try:
                await callback.message.edit_text(
                    profile_text,
                    reply_markup=get_user_profile_return_keyboard(
                        user_id=target_user.id,
                        is_restricted=is_restricted,
                        has_limitations=has_limitations
                    ),
                    parse_mode="Markdown"
                )
            except TelegramBadRequest:
                pass
            await answer_callback_query_via_runtime(callback, "✅ نقش کاربر تغییر کرد.")
        else:
            await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)

@router.callback_query(F.data.startswith("user_toggle_account_status_"))
async def handle_user_toggle_account_status(callback: types.CallbackQuery, user: Optional[User]):
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            if not _can_manage_target_user(user, target_user):
                await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
                return
            if await _reject_users_callback_if_not_authoritative(callback, "account_status_update"):
                return

            current_status = get_user_account_status(target_user)
            target_status = UserAccountStatus.INACTIVE if current_status == UserAccountStatus.ACTIVE else UserAccountStatus.ACTIVE
            await transition_user_account_status(session, target_user, target_status)
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
                    reply_markup=get_user_settings_keyboard(user_id=target_user.id, account_status=target_user.account_status, is_restricted=is_restricted, has_limitations=has_limitations, can_edit_role=_can_edit_target_role(user)),
                    parse_mode="Markdown"
                )
            except TelegramBadRequest:
                pass
            
            status = "فعال" if get_user_account_status(target_user) == UserAccountStatus.ACTIVE else "غیرفعال"
            await answer_callback_query_via_runtime(callback, f"✅ وضعیت حساب {status} شد.", show_alert=True)
        else:
            await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)

@router.callback_query(F.data.startswith("user_ask_delete_"))
async def handle_user_delete_request(callback: types.CallbackQuery, user: Optional[User]):
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        target_user = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
        return

    await _show_user_delete_webapp_redirect(callback, target_user)

@router.callback_query(F.data.startswith("user_delete_confirm_"))
async def handle_user_delete_confirm(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user and not target_user.is_deleted:
            if not _can_manage_target_user(user, target_user):
                await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
                return
            await _show_user_delete_webapp_redirect(callback, target_user)
        else:
            await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد یا قبلاً حذف شده است.", show_alert=True)
            await show_users_list(callback.bot, callback.message.chat.id, state, page=1, message_id_to_edit=callback.message.message_id, actor=user)

# --- هندلرهای محدودسازی کاربر (رویکرد جدید با دکمه‌ها) ---

def get_limit_panel_text(max_trades, max_commodities, max_requests):
    """ساخت متن پنل محدودیت‌ها"""
    trades_str = str(max_trades) if max_trades else "---"
    commodities_str = str(max_commodities) if max_commodities else "---"
    requests_str = str(max_requests) if max_requests else "---"
    
    return (
        "⚠️ **تنظیم محدودیت‌ها**\n\n"
        f"📊 مجموع تعداد معاملات: **{trades_str}**\n"
        f"📦 مجموع تعداد کالای معامله شده: **{commodities_str}**\n"
        f"📨 مجموع ارسال لفظ در کانال: **{requests_str}**\n\n"
        "برای تنظیم هر مورد روی دکمه مربوطه کلیک کنید.\n"
        "پس از اتمام، دکمه **تایید** را بزنید."
    )

@router.callback_query(F.data.startswith("user_limit_"))
async def handle_user_limit_start(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return
    
    # Handle duration selection
    if callback.data.startswith("user_limit_dur_"):
        parts = callback.data.split("_")
        target_user_id = int(parts[3])
        minutes = int(parts[4])
        async with AsyncSessionLocal() as session:
            target_user = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
        if not target_user:
            await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
            return
        if not _can_manage_target_user(user, target_user):
            await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
            return
        
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
        await answer_callback_query_via_runtime(callback)
        return
    
    # Initial request (show duration keyboard)
    target_user_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        target_user = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
        return
    await callback.message.edit_text(
        "⏳ **مدت زمان محدودیت را انتخاب کنید:**",
        reply_markup=get_limit_duration_keyboard(target_user_id),
        parse_mode="Markdown"
    )
    await answer_callback_query_via_runtime(callback)

@router.callback_query(F.data.startswith("limit_set_trades_"))
async def handle_set_trades(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        target_user = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
        return
    await state.update_data(limit_editing="trades")
    await state.set_state(UserLimitations.awaiting_limit_value)
    
    await callback.message.edit_text(
        "📊 **مجموع تعداد معاملات** را وارد کنید:\n\n"
        "(یک عدد وارد کنید)",
        parse_mode="Markdown"
    )
    await answer_callback_query_via_runtime(callback)

@router.callback_query(F.data.startswith("limit_set_commodities_"))
async def handle_set_commodities(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        target_user = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
        return
    await state.update_data(limit_editing="commodities")
    await state.set_state(UserLimitations.awaiting_limit_value)
    
    await callback.message.edit_text(
        "📦 **مجموع تعداد کالای معامله شده** را وارد کنید:\n\n"
        "(یک عدد وارد کنید)",
        parse_mode="Markdown"
    )
    await answer_callback_query_via_runtime(callback)

@router.callback_query(F.data.startswith("limit_set_requests_"))
async def handle_set_requests(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        target_user = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
        return
    await state.update_data(limit_editing="requests")
    await state.set_state(UserLimitations.awaiting_limit_value)
    
    await callback.message.edit_text(
        "📨 **مجموع ارسال لفظ در کانال** را وارد کنید:\n\n"
        "(یک عدد وارد کنید)",
        parse_mode="Markdown"
    )
    await answer_callback_query_via_runtime(callback)

@router.message(UserLimitations.awaiting_limit_value)
async def process_limit_value(message: types.Message, state: FSMContext, user: Optional[User]):
    if not _can_open_user_management(user):
        await state.clear()
        return
    
    await delete_user_message(message)
    
    try:
        value = int(message.text.strip())
        if value < 0:
            raise ValueError
    except ValueError:
        temp_msg = await message.answer("❌ لطفاً یک عدد صحیح معتبر وارد کنید.")
        await schedule_temporary_message_cleanup(
            message.bot,
            chat_id=message.chat.id,
            message_id=temp_msg.message_id,
            delay=3,
            source_id=(
                f"admin-user-limit-invalid:{message.chat.id}:"
                f"{temp_msg.message_id}"
            ),
        )
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
    if not _can_open_user_management(user):
        return
    
    data = await state.get_data()
    target_user_id = data.get("limit_target_user_id")
    expire_at = data.get("limit_expire_at")
    max_trades = data.get("limit_max_trades")
    max_commodities = data.get("limit_max_commodities")
    max_requests = data.get("limit_max_requests")
    
    # اگر هیچ محدودیتی تنظیم نشده
    if not max_trades and not max_commodities and not max_requests:
        await answer_callback_query_via_runtime(callback, "⚠️ لطفاً حداقل یک محدودیت تنظیم کنید.", show_alert=True)
        return
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            if not _can_manage_target_user(user, target_user):
                await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
                return
            if await _reject_users_callback_if_not_authoritative(callback, "limit_update"):
                return
            target_user.max_daily_trades = max_trades
            target_user.max_active_commodities = max_commodities
            target_user.max_daily_requests = max_requests
            target_user.limitations_expire_at = expire_at
            # ریست شمارنده‌ها
            from core.user_counter_sync import reset_user_counters_in_memory

            reset_user_counters_in_memory(target_user)
            await session.commit()
            
            # --- Send Notification to limited user ---
            limitations_changed = []
            if max_trades is not None:
                limitations_changed.append(f"مجموع تعداد معاملات: {max_trades}")
            if max_commodities is not None:
                limitations_changed.append(f"مجموع تعداد کالای معامله شده: {max_commodities}")
            if max_requests is not None:
                limitations_changed.append(f"مجموع ارسال لفظ در کانال: {max_requests}")
            
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
            is_restricted = target_user.trading_restricted_until and target_user.trading_restricted_until > datetime.utcnow()
            has_limitations = True  # Just set limitations, so always True
            await callback.message.edit_text(
                profile_text,
                reply_markup=get_user_profile_return_keyboard(
                    user_id=target_user.id,
                    is_restricted=is_restricted,
                    has_limitations=has_limitations
                ),
                parse_mode="Markdown"
            )
            await answer_callback_query_via_runtime(callback, "✅ محدودیت‌ها اعمال شد.", show_alert=True)
        else:
            await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)

@router.callback_query(F.data.startswith("limit_cancel_"))
async def handle_limit_cancel(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    """انصراف از اعمال محدودیت"""
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    await clear_state_retain_anchors(state)
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
        if target_user:
            if not _can_manage_target_user(user, target_user):
                await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
                return
            is_restricted = target_user.trading_restricted_until and target_user.trading_restricted_until > datetime.utcnow()
            has_limitations = (
                target_user.max_daily_trades is not None or
                target_user.max_active_commodities is not None or
                target_user.max_daily_requests is not None
            )
            
            profile_text = await get_user_profile_text(target_user)
            await callback.message.edit_text(
                profile_text,
                reply_markup=get_user_settings_keyboard(
                    target_user.id, 
                    is_restricted=is_restricted, 
                    has_limitations=has_limitations,
                    can_block=target_user.can_block_users,
                    max_blocked=target_user.max_blocked_users,
                    can_edit_role=_can_edit_target_role(user),
                ),
                parse_mode="Markdown"
            )
    
    await answer_callback_query_via_runtime(callback, "عملیات لغو شد.")


# --- هندلرهای تنظیمات بلاک ادمین ---

@router.callback_query(F.data.startswith("user_block_settings_"))
async def handle_user_block_settings(callback: types.CallbackQuery, user: Optional[User]):
    """نمایش منوی تنظیمات قابلیت بلاک برای کاربر"""
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
        return
    
    text = (
        f"🚫 **تنظیمات قابلیت بلاک**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 کاربر: `{_target_user_display_name(target_user)}`\n\n"
        f"📊 قابلیت بلاک: {'✅ فعال' if target_user.can_block_users else '❌ غیرفعال'}\n"
        f"🔢 سقف بلاک: {target_user.max_blocked_users} نفر\n"
    )
    
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_block_settings_keyboard(
                target_user_id, 
                target_user.can_block_users, 
                target_user.max_blocked_users
            ),
            parse_mode="Markdown"
        )
    except TelegramBadRequest:
        pass
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data.startswith("admin_toggle_block_"))
async def handle_admin_toggle_block(callback: types.CallbackQuery, user: Optional[User]):
    """تغییر وضعیت قابلیت بلاک کاربر"""
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        await attach_customer_management_names(session, [target_user])

        if target_user:
            if not _can_manage_target_user(user, target_user):
                await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
                return
            if await _reject_users_callback_if_not_authoritative(callback, "block_capability_update"):
                return
            target_user.can_block_users = not target_user.can_block_users
            await session.commit()
            
            status = "فعال" if target_user.can_block_users else "غیرفعال"
            
            text = (
                f"🚫 **تنظیمات قابلیت بلاک**\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 کاربر: `{_target_user_display_name(target_user)}`\n\n"
                f"📊 قابلیت بلاک: {'✅ فعال' if target_user.can_block_users else '❌ غیرفعال'}\n"
                f"🔢 سقف بلاک: {target_user.max_blocked_users} نفر\n"
            )
            
            try:
                await callback.message.edit_text(
                    text,
                    reply_markup=get_block_settings_keyboard(
                        target_user_id, 
                        target_user.can_block_users, 
                        target_user.max_blocked_users
                    ),
                    parse_mode="Markdown"
                )
            except TelegramBadRequest:
                pass
            await answer_callback_query_via_runtime(callback, f"✅ قابلیت بلاک {status} شد.", show_alert=True)
        else:
            await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)


@router.callback_query(F.data.startswith("admin_set_max_block_"))
async def handle_admin_set_max_block(callback: types.CallbackQuery, user: Optional[User]):
    """نمایش گزینه‌های سقف بلاک"""
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        target_user = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔢 **سقف بلاک را انتخاب کنید:**\n\n"
        "این عدد حداکثر تعداد کاربرانی است که این کاربر می‌تواند مسدود کند.",
        reply_markup=get_max_block_options_keyboard(target_user_id),
        parse_mode="Markdown"
    )
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data.startswith("admin_max_block_set_"))
async def handle_admin_max_block_set(callback: types.CallbackQuery, user: Optional[User]):
    """اعمال سقف بلاک جدید"""
    if not _can_open_user_management(user):
        return
    
    parts = callback.data.split("_")
    target_user_id = int(parts[4])
    new_max = int(parts[5])
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        await attach_customer_management_names(session, [target_user])

        if target_user:
            if not _can_manage_target_user(user, target_user):
                await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
                return
            if await _reject_users_callback_if_not_authoritative(callback, "max_block_update"):
                return
            target_user.max_blocked_users = new_max
            await session.commit()
            
            text = (
                f"🚫 **تنظیمات قابلیت بلاک**\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 کاربر: `{_target_user_display_name(target_user)}`\n\n"
                f"📊 قابلیت بلاک: {'✅ فعال' if target_user.can_block_users else '❌ غیرفعال'}\n"
                f"🔢 سقف بلاک: {target_user.max_blocked_users} نفر\n"
            )
            
            try:
                await callback.message.edit_text(
                    text,
                    reply_markup=get_block_settings_keyboard(
                        target_user_id, 
                        target_user.can_block_users, 
                        target_user.max_blocked_users
                    ),
                    parse_mode="Markdown"
                )
            except TelegramBadRequest:
                pass
            await answer_callback_query_via_runtime(callback, f"✅ سقف بلاک به {new_max} تغییر کرد.", show_alert=True)
        else:
            await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)


@router.callback_query(F.data.startswith("admin_max_block_custom_"))
async def handle_admin_max_block_custom(callback: types.CallbackQuery, user: Optional[User], state: FSMContext):
    """درخواست ورود عدد دلخواه برای سقف بلاک"""
    if not _can_open_user_management(user):
        return
    
    target_user_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        target_user = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
    if not target_user:
        await answer_callback_query_via_runtime(callback, "❌ کاربر یافت نشد.", show_alert=True)
        return
    if not _can_manage_target_user(user, target_user):
        await answer_callback_query_via_runtime(callback, "❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
        return
    
    await state.update_data(custom_max_block_user_id=target_user_id)
    await state.set_state(UserManagement.awaiting_custom_max_block)
    
    await callback.message.edit_text(
        "🔢 **عدد دلخواه سقف بلاک را وارد کنید:**\n\n"
        "لطفاً یک عدد بین 1 تا 100 وارد کنید.",
        parse_mode="Markdown"
    )
    await answer_callback_query_via_runtime(callback)


@router.message(UserManagement.awaiting_custom_max_block)
async def process_custom_max_block(message: types.Message, user: Optional[User], state: FSMContext):
    """پردازش عدد دلخواه سقف بلاک"""
    if not _can_open_user_management(user):
        return
    
    await delete_user_message(message)
    
    data = await state.get_data()
    target_user_id = data.get("custom_max_block_user_id")
    anchor_id = data.get("anchor_id")
    
    try:
        new_max = int(message.text.strip())
        if new_max < 1 or new_max > 100:
            raise ValueError("Out of range")
    except (ValueError, AttributeError):
        msg = await message.answer(
            "❌ لطفاً یک عدد معتبر بین 1 تا 100 وارد کنید.",
            parse_mode="Markdown"
        )
        await update_anchor(state, msg.message_id, message.bot, message.chat.id)
        return
    
    await clear_state_retain_anchors(state)
    
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == target_user_id)
        target_user = (await session.execute(stmt)).scalar_one_or_none()
        await attach_customer_management_names(session, [target_user])

        if target_user:
            if not _can_manage_target_user(user, target_user):
                msg = await message.answer("❌ شما مجاز به مدیریت این کاربر نیستید.")
                await update_anchor(state, msg.message_id, message.bot, message.chat.id)
                return
            if await _reject_users_message_if_not_authoritative(
                message,
                user,
                "max_block_update",
            ):
                return
            target_user.max_blocked_users = new_max
            await session.commit()
            
            text = (
                f"🚫 **تنظیمات قابلیت بلاک**\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 کاربر: `{_target_user_display_name(target_user)}`\n\n"
                f"📊 قابلیت بلاک: {'✅ فعال' if target_user.can_block_users else '❌ غیرفعال'}\n"
                f"🔢 سقف بلاک: {target_user.max_blocked_users} نفر\n"
            )
            
            msg = await message.answer(
                text,
                reply_markup=get_block_settings_keyboard(
                    target_user_id, 
                    target_user.can_block_users, 
                    target_user.max_blocked_users
                ),
                parse_mode="Markdown"
            )
            await update_anchor(state, msg.message_id, message.bot, message.chat.id)
        else:
            msg = await message.answer("❌ کاربر یافت نشد.")
            await update_anchor(state, msg.message_id, message.bot, message.chat.id)
