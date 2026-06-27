# bot/handlers/panel.py
"""هندلرهای پنل کاربر و مدیریت"""

import os
from datetime import datetime, time as dt_time, timedelta, timezone
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload, selectinload
from models.user import User
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.trade import Trade
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
from core.admin_authority import admin_write_rejection_message, check_shared_admin_write_authority
from core.enums import UserAccountStatus, UserRole
from core.config import settings
from core.db import AsyncSessionLocal
from core.services.user_account_status_service import is_user_global_web_locked
from core.services.trade_history_export_service import (
    build_trade_history_date_range_label,
    build_trade_history_export_rows,
    generate_trade_history_pdf_file,
)
from bot.utils.customer_display import attach_customer_management_names, user_display_name

router = Router()


class UserPanelBlockCallback(CallbackData, prefix="user_panel_block"):
    action: str
    user_id: int


class UserPanelCustomerCallback(CallbackData, prefix="user_panel_customer"):
    action: str
    relation_id: int = 0


USER_PANEL_RECENT_TRADES_TEXT = "📄 معاملات اخیر"
USER_PANEL_BLOCKED_USERS_TEXT = "🚫 کاربران مسدود شده"
USER_PANEL_CUSTOMERS_TEXT = "👥 مشتریان"
USER_PANEL_COLLEAGUES_TEXT = "👥 لیست همکاران"


def _settings_admin_write_decision(operation: str):
    return check_shared_admin_write_authority(
        "trading_settings",
        operation=operation,
        surface="telegram_bot_admin",
    )


async def _reject_settings_callback_if_not_authoritative(callback: types.CallbackQuery, operation: str) -> bool:
    decision = _settings_admin_write_decision(operation)
    if decision.ok:
        return False
    await callback.answer(f"❌ {admin_write_rejection_message(decision)}", show_alert=True)
    return True


async def _reject_settings_message_if_not_authoritative(message: types.Message, operation: str) -> bool:
    decision = _settings_admin_write_decision(operation)
    if decision.ok:
        return False
    await message.answer(f"❌ {admin_write_rejection_message(decision)}")
    return True


async def handoff_navigation_button(message: types.Message, state: FSMContext, user: Optional[User]) -> bool:
    """Allow reply-keyboard navigation buttons to escape stale FSM states."""
    if not user:
        return False

    text = (message.text or "").strip()
    if not text:
        return False

    from bot.handlers.admin import start_invitation_creation
    from bot.handlers.admin_commodities import handle_manage_commodities
    from bot.handlers.admin_users import (
        handle_back_to_admin,
        handle_users_list_command,
        handle_users_menu,
        start_search_user,
    )
    from bot.handlers.trade_history import show_my_trade_history

    navigation_actions = {
        "/panel": lambda: show_my_profile_and_change_keyboard(message, state, user),
        "👤 پنل کاربر": lambda: show_my_profile_and_change_keyboard(message, state, user),
        "🔐 پنل مدیریت": lambda: show_admin_panel_and_change_keyboard(message, state, user),
        "⚙️ تنظیمات کاربری": lambda: handle_user_settings_button(message, state, user),
        "⚙️ تنظیمات": lambda: handle_simple_settings_button(message, user),
        "⚙️ تنظیمات سیستم": lambda: handle_admin_settings_button(message, state, user),
        USER_PANEL_COLLEAGUES_TEXT: lambda: show_colleagues_list(message, state, user),
        "📊 تاریخچه معاملات من": lambda: show_my_trade_history(message, state, user),
        USER_PANEL_RECENT_TRADES_TEXT: lambda: show_recent_trades_pdf(message, state, user),
        USER_PANEL_BLOCKED_USERS_TEXT: lambda: show_user_panel_blocked_users(message, state, user),
        USER_PANEL_CUSTOMERS_TEXT: lambda: show_user_panel_customers(message, state, user),
        "➕ ارسال لینک دعوت": lambda: start_invitation_creation(message, state, user),
        "📦 مدیریت کالاها": lambda: handle_manage_commodities(message, user, state),
        "👥 مدیریت کاربران": lambda: handle_users_menu(message, user, state),
        "📋 لیست کاربران": lambda: handle_users_list_command(message, user, state),
        "🔍 جستجوی کاربر": lambda: start_search_user(message, state, user),
        "🔙 بازگشت": lambda: handle_back_to_main_menu(message, state, user),
        "🔙 بازگشت به پنل مدیریت": lambda: handle_back_to_admin(message, user, state),
    }

    action = navigation_actions.get(text)
    if not action:
        return False

    await state.clear()
    await action()
    return True


# --- هندلر پنل کاربر ---
@router.message(Command("panel"))
@router.message(F.text == "👤 پنل کاربر")
async def show_my_profile_and_change_keyboard(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    if is_user_global_web_locked(user):
        await message.answer("دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است.")
        return

    # حذف پیام کاربر و لنگر قبلی
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)

    is_customer_user = False
    if user.role == UserRole.STANDARD:
        from core.services.customer_relation_service import is_user_customer

        async with AsyncSessionLocal() as session:
            is_customer_user = await is_user_customer(session, user.id)

    if user.role == UserRole.STANDARD and not is_customer_user:
        anchor_msg = await message.answer(
            "👤 **پنل کاربر**\n\nگزینه مورد نظر را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=get_user_panel_keyboard(user.role, standard_actions=True),
        )
        set_anchor(message.chat.id, anchor_msg.message_id)
        return
    
    async with AsyncSessionLocal() as session:
        await attach_customer_management_names(session, [user])

    profile_link = f"https://t.me/{settings.bot_username}?start=profile_{user.id}"

    profile_text = (
        f"👤 **پروفایل شما**\n\n"
        f"🔸 **نام کاربری:** `{user_display_name(user)}`\n"
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


async def _can_view_colleagues_list(session, user: User) -> bool:
    if user.role != UserRole.STANDARD:
        return False

    from core.services.accountant_relation_service import is_user_accountant
    from core.services.customer_relation_service import is_user_customer

    if await is_user_customer(session, user.id):
        return False
    if await is_user_accountant(session, user.id):
        return False
    return True


async def _load_colleagues_for_user(session, user_id: int) -> list[User]:
    active_customer_relation_exists = (
        select(CustomerRelation.id)
        .where(
            CustomerRelation.customer_user_id == User.id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .exists()
    )
    active_accountant_relation_exists = (
        select(AccountantRelation.id)
        .where(
            AccountantRelation.accountant_user_id == User.id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .exists()
    )
    stmt = (
        select(User)
        .where(
            User.id != user_id,
            User.is_deleted.is_(False),
            User.account_status == UserAccountStatus.ACTIVE,
            ~active_customer_relation_exists,
            ~active_accountant_relation_exists,
        )
        .order_by(User.account_name.asc(), User.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


def _build_colleagues_list_messages(colleagues: list[User]) -> list[str]:
    if not colleagues:
        return ["👥 لیست همکاران\n\nهیچ همکاری برای نمایش وجود ندارد."]

    header = f"👥 لیست همکاران\n\nتعداد: {len(colleagues)}"
    messages: list[str] = []
    current = header
    for index, colleague in enumerate(colleagues, start=1):
        display_name = user_display_name(colleague, getattr(colleague, "account_name", None) or f"کاربر {colleague.id}")
        line = f"\n{index}. {display_name}"
        if len(current) + len(line) > 3500:
            messages.append(current)
            current = "👥 ادامه لیست همکاران" + line
        else:
            current += line
    messages.append(current)
    return messages


@router.message(F.text == USER_PANEL_COLLEAGUES_TEXT)
async def show_colleagues_list(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return
    if is_user_global_web_locked(user):
        await message.answer("دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است.")
        return

    async with AsyncSessionLocal() as session:
        if not await _can_view_colleagues_list(session, user):
            await message.answer("این بخش فقط برای کاربران عادی فعال است.")
            return
        colleagues = await _load_colleagues_for_user(session, user.id)

    for text in _build_colleagues_list_messages(colleagues):
        await message.answer(text)


def _user_panel_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 بازگشت به پنل کاربر", callback_data=UserPanelCustomerCallback(action="back").pack())]
        ]
    )


def _safe_filename_subject(value: object, fallback: str = "history") -> str:
    subject = str(value or fallback).strip() or fallback
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in subject)


def _history_download_filename(subject_name: object, extension: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"trade_history_{_safe_filename_subject(subject_name)}_{timestamp}.{extension}"


async def _load_recent_user_trades(user_id: int, *, from_date, to_date) -> list[Trade]:
    query = (
        select(Trade)
        .options(
            selectinload(Trade.offer_user),
            selectinload(Trade.responder_user),
            selectinload(Trade.commodity),
            selectinload(Trade.offer),
        )
        .where(or_(Trade.offer_user_id == user_id, Trade.responder_user_id == user_id))
        .where(Trade.created_at >= datetime.combine(from_date, dt_time.min))
        .where(Trade.created_at < datetime.combine(to_date + timedelta(days=1), dt_time.min))
        .order_by(Trade.created_at.asc(), Trade.id.asc())
    )
    async with AsyncSessionLocal() as session:
        return list((await session.execute(query)).scalars().all())


@router.message(F.text == USER_PANEL_RECENT_TRADES_TEXT)
async def show_recent_trades_pdf(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return

    today = datetime.now(timezone.utc).date()
    from_date = today - timedelta(days=6)
    trades = await _load_recent_user_trades(user.id, from_date=from_date, to_date=today)
    if not trades:
        await message.answer("⚠️ در هفت روز گذشته معامله‌ای برای دانلود وجود ندارد.")
        return

    output_path = None
    try:
        subject_name = getattr(user, "account_name", None) or user_display_name(user, "پروفایل من")
        output_path = generate_trade_history_pdf_file(
            subject_name=subject_name,
            date_range_label=build_trade_history_date_range_label(from_date, today),
            rows=build_trade_history_export_rows(trades, user.id),
        )
        await message.answer_document(
            document=FSInputFile(output_path, filename=_history_download_filename(subject_name, "pdf")),
            caption="📄 معاملات اخیر شما در هفت روز گذشته",
        )
    except Exception as exc:
        await message.answer(f"❌ خطا در ایجاد فایل معاملات اخیر: {exc}")
    finally:
        if output_path and os.path.exists(output_path):
            os.remove(output_path)


def get_user_panel_blocked_keyboard(blocked_users: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for blocked_user in blocked_users[:10]:
        rows.append([
            InlineKeyboardButton(
                text=f"رفع مسدودیت: {blocked_user['account_name']}",
                callback_data=UserPanelBlockCallback(action="unblock", user_id=blocked_user["id"]).pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(text="🔙 بازگشت به پنل کاربر", callback_data=UserPanelBlockCallback(action="back", user_id=0).pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_user_panel_blocked_users(message: types.Message, user_id: int) -> None:
    from core.services.block_service import get_blocked_users

    async with AsyncSessionLocal() as session:
        blocked_users = await get_blocked_users(session, user_id)

    if not blocked_users:
        await message.answer(
            "📋 لیست کاربران مسدود شده شما خالی است.",
            reply_markup=_user_panel_back_keyboard(),
        )
        return

    await message.answer(
        "📋 **کاربران مسدود شده**\n\nبرای رفع مسدودیت روی نام کاربر بزنید:",
        parse_mode="Markdown",
        reply_markup=get_user_panel_blocked_keyboard(blocked_users),
    )


@router.message(F.text == USER_PANEL_BLOCKED_USERS_TEXT)
async def show_user_panel_blocked_users(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return
    await _render_user_panel_blocked_users(message, user.id)


@router.callback_query(UserPanelBlockCallback.filter(F.action == "unblock"))
async def unblock_user_from_user_panel(
    callback: types.CallbackQuery,
    callback_data: UserPanelBlockCallback,
    user: Optional[User],
):
    if not user:
        await callback.answer()
        return

    from core.services.block_service import get_blocked_users, unblock_user

    async with AsyncSessionLocal() as session:
        success, result_message = await unblock_user(session, user.id, callback_data.user_id)
        blocked_users = await get_blocked_users(session, user.id)

    await callback.answer(result_message, show_alert=not success)
    if blocked_users:
        await callback.message.edit_text(
            "📋 **کاربران مسدود شده**\n\nبرای رفع مسدودیت روی نام کاربر بزنید:",
            parse_mode="Markdown",
            reply_markup=get_user_panel_blocked_keyboard(blocked_users),
        )
    else:
        await callback.message.edit_text(
            "📋 لیست کاربران مسدود شده شما خالی است.",
            reply_markup=_user_panel_back_keyboard(),
        )


@router.callback_query(UserPanelBlockCallback.filter(F.action == "back"))
async def back_to_user_panel_from_blocked(callback: types.CallbackQuery, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    await callback.message.edit_text("👤 **پنل کاربر**\n\nاز دکمه‌های پایین پیام استفاده کنید.", parse_mode="Markdown")
    await callback.answer()


def _customer_status_label(status: object) -> str:
    value = getattr(status, "value", status)
    return {
        CustomerRelationStatus.PENDING.value: "در انتظار ثبت‌نام",
        CustomerRelationStatus.ACTIVE.value: "فعال",
        CustomerRelationStatus.EXPIRED.value: "منقضی",
        CustomerRelationStatus.REVOKED.value: "لغو شده",
        CustomerRelationStatus.DELETED.value: "حذف شده",
    }.get(str(value), str(value or "نامشخص"))


def _customer_tier_label(tier: object) -> str:
    value = getattr(tier, "value", tier)
    return {
        CustomerTier.TIER_1.value: "سطح ۱",
        CustomerTier.TIER_2.value: "سطح ۲",
    }.get(str(value), str(value or "نامشخص"))


def _customer_relation_name(relation: CustomerRelation) -> str:
    customer_user = getattr(relation, "customer_user", None)
    return (
        getattr(relation, "management_name", None)
        or getattr(customer_user, "account_name", None)
        or f"مشتری #{getattr(relation, 'id', '')}"
    )


def _customer_relation_list_label(relation: CustomerRelation) -> str:
    return (
        f"👤 {_customer_relation_name(relation)}"
        f" | {_customer_tier_label(relation.customer_tier)}"
        f" | {_customer_status_label(relation.status)}"
    )


def get_user_panel_customers_keyboard(relations: list[CustomerRelation]) -> InlineKeyboardMarkup:
    rows = []
    for relation in relations[:10]:
        rows.append([
            InlineKeyboardButton(
                text=_customer_relation_list_label(relation),
                callback_data=UserPanelCustomerCallback(action="detail", relation_id=relation.id).pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(text="➕ دعوت مشتری", callback_data=UserPanelCustomerCallback(action="invite").pack())
    ])
    rows.append([
        InlineKeyboardButton(text="🔙 بازگشت به پنل کاربر", callback_data=UserPanelCustomerCallback(action="back").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_customer_detail_keyboard(relation: CustomerRelation) -> InlineKeyboardMarkup:
    rows = []
    status_value = getattr(relation.status, "value", relation.status)
    if status_value in (CustomerRelationStatus.PENDING.value, CustomerRelationStatus.ACTIVE.value):
        action_label = "❌ لغو دعوت مشتری" if status_value == CustomerRelationStatus.PENDING.value else "❌ اخراج مشتری"
        rows.append([
            InlineKeyboardButton(
                text=action_label,
                callback_data=UserPanelCustomerCallback(action="ask_unlink", relation_id=relation.id).pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(text="🔙 بازگشت به مشتریان", callback_data=UserPanelCustomerCallback(action="list").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_customer_unlink_confirm_keyboard(relation: CustomerRelation) -> InlineKeyboardMarkup:
    status_value = getattr(relation.status, "value", relation.status)
    confirm_label = "✅ بله، لغو دعوت شود" if status_value == CustomerRelationStatus.PENDING.value else "✅ بله، اخراج شود"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=confirm_label,
                    callback_data=UserPanelCustomerCallback(action="confirm_unlink", relation_id=relation.id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ انصراف",
                    callback_data=UserPanelCustomerCallback(action="detail", relation_id=relation.id).pack(),
                )
            ],
        ]
    )


async def _load_user_panel_customer_relations(owner_user_id: int) -> list[CustomerRelation]:
    from core.services.customer_relation_service import list_owner_customer_relations

    async with AsyncSessionLocal() as session:
        return await list_owner_customer_relations(session, owner_user_id=owner_user_id)


async def _load_user_panel_customer_relation(owner_user_id: int, relation_id: int) -> CustomerRelation | None:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(CustomerRelation)
            .options(joinedload(CustomerRelation.customer_user))
            .where(
                CustomerRelation.id == relation_id,
                CustomerRelation.owner_user_id == owner_user_id,
                CustomerRelation.deleted_at.is_(None),
            )
        )
        return (await session.execute(stmt)).scalar_one_or_none()


def _customer_relation_detail_text(relation: CustomerRelation) -> str:
    customer_user = getattr(relation, "customer_user", None)
    lines = [
        f"👤 **{_customer_relation_name(relation)}**",
        "",
        f"وضعیت: {_customer_status_label(relation.status)}",
        f"سطح مشتری: {_customer_tier_label(relation.customer_tier)}",
    ]
    mobile_number = getattr(customer_user, "mobile_number", None)
    if mobile_number:
        lines.append(f"شماره: `{mobile_number}`")
    return "\n".join(lines)


async def _edit_or_answer_customers_panel(target, owner_user_id: int, *, edit: bool) -> None:
    relations = await _load_user_panel_customer_relations(owner_user_id)
    text = "👥 **مشتریان شما**\n\n"
    if relations:
        text += "برای مشاهده جزئیات یا اخراج مشتری، روی نام او بزنید."
    else:
        text += "هنوز مشتری ثبت نشده است."
    keyboard = get_user_panel_customers_keyboard(relations)
    if edit:
        await target.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await target.answer(text, parse_mode="Markdown", reply_markup=keyboard)


@router.message(F.text == USER_PANEL_CUSTOMERS_TEXT)
async def show_user_panel_customers(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return
    await _edit_or_answer_customers_panel(message, user.id, edit=False)


@router.callback_query(UserPanelCustomerCallback.filter(F.action == "list"))
async def show_user_panel_customers_callback(callback: types.CallbackQuery, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    await _edit_or_answer_customers_panel(callback.message, user.id, edit=True)
    await callback.answer()


@router.callback_query(UserPanelCustomerCallback.filter(F.action == "detail"))
async def show_user_panel_customer_detail(
    callback: types.CallbackQuery,
    callback_data: UserPanelCustomerCallback,
    user: Optional[User],
):
    if not user:
        await callback.answer()
        return
    relation = await _load_user_panel_customer_relation(user.id, callback_data.relation_id)
    if not relation:
        await callback.answer("مشتری یافت نشد.", show_alert=True)
        return
    await callback.message.edit_text(
        _customer_relation_detail_text(relation),
        parse_mode="Markdown",
        reply_markup=get_customer_detail_keyboard(relation),
    )
    await callback.answer()


@router.callback_query(UserPanelCustomerCallback.filter(F.action == "ask_unlink"))
async def ask_unlink_user_panel_customer(
    callback: types.CallbackQuery,
    callback_data: UserPanelCustomerCallback,
    user: Optional[User],
):
    if not user:
        await callback.answer()
        return
    relation = await _load_user_panel_customer_relation(user.id, callback_data.relation_id)
    if not relation:
        await callback.answer("مشتری یافت نشد.", show_alert=True)
        return
    await callback.message.edit_text(
        f"آیا از اخراج/قطع ارتباط با **{_customer_relation_name(relation)}** مطمئن هستید؟",
        parse_mode="Markdown",
        reply_markup=get_customer_unlink_confirm_keyboard(relation),
    )
    await callback.answer()


@router.callback_query(UserPanelCustomerCallback.filter(F.action == "confirm_unlink"))
async def confirm_unlink_user_panel_customer(
    callback: types.CallbackQuery,
    callback_data: UserPanelCustomerCallback,
    user: Optional[User],
):
    if not user:
        await callback.answer()
        return
    from core.services.customer_relation_service import unlink_owner_customer_relation

    try:
        async with AsyncSessionLocal() as session:
            relation = await unlink_owner_customer_relation(
                session,
                owner_user_id=user.id,
                relation_id=callback_data.relation_id,
            )
            relation_name = _customer_relation_name(relation)
    except HTTPException as exc:
        await callback.answer(str(exc.detail), show_alert=True)
        return
    except Exception as exc:
        await callback.answer(f"خطا در اخراج مشتری: {exc}", show_alert=True)
        return

    await callback.answer(f"{relation_name} از پروژه اخراج شد.", show_alert=True)
    await _edit_or_answer_customers_panel(callback.message, user.id, edit=True)


@router.callback_query(UserPanelCustomerCallback.filter(F.action == "invite"))
async def user_panel_customer_invite_placeholder(callback: types.CallbackQuery, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    await callback.answer("دعوت مشتری از بات در مرحله بعد اضافه می‌شود.", show_alert=True)


@router.callback_query(UserPanelCustomerCallback.filter(F.action == "back"))
async def back_to_user_panel_from_customers(callback: types.CallbackQuery, user: Optional[User]):
    if not user:
        await callback.answer()
        return
    await callback.message.edit_text("👤 **پنل کاربر**\n\nاز دکمه‌های پایین پیام استفاده کنید.", parse_mode="Markdown")
    await callback.answer()


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
    if await _reject_settings_callback_if_not_authoritative(callback, "update"):
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

    if await handoff_navigation_button(message, state, user):
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

    if await _reject_settings_message_if_not_authoritative(message, "update"):
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
    if await _reject_settings_callback_if_not_authoritative(callback, "reset"):
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
    if await _reject_settings_callback_if_not_authoritative(callback, "reset"):
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
