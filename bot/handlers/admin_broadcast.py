"""Telegram admin broadcast bot flow."""
from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.states import AdminBroadcast
from bot.telegram_callback_answer import answer_callback_query_via_runtime
from bot.telegram_interaction_message import (
    answer_incoming_message_via_runtime,
    edit_callback_message_via_runtime,
)
from core.db import AsyncSessionLocal
from core.enums import UserRole
from core.services.telegram_admin_broadcast_service import (
    SUPPORTED_TELEGRAM_ADMIN_BROADCAST_GROUPS,
    TELEGRAM_ADMIN_BROADCAST_GROUP_MANAGERS,
    TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY,
    TELEGRAM_ADMIN_BROADCAST_GROUP_TIER1_CUSTOMERS,
    TELEGRAM_BROADCAST_SELECTED_RECIPIENT_CAP,
    TELEGRAM_BROADCAST_TEXT_MAX_LENGTH,
    TelegramAdminBroadcastRecipient,
    TelegramAdminBroadcastValidationError,
    create_telegram_admin_broadcast,
    resolve_telegram_admin_broadcast_recipients,
    search_telegram_admin_broadcast_recipients,
    validate_telegram_admin_broadcast_content,
)
from models.telegram_admin_broadcast import TelegramAdminBroadcastAudienceType
from models.user import User


router = Router()

ADMIN_BROADCAST_BUTTON_TEXT = "📣 ارسال پیام همگانی بات"
CALLBACK_PREFIX = "tgb"

GROUP_LABELS = {
    TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY: "کاربران عادی",
    TELEGRAM_ADMIN_BROADCAST_GROUP_MANAGERS: "مدیران",
    TELEGRAM_ADMIN_BROADCAST_GROUP_TIER1_CUSTOMERS: "مشتریان سطح1",
}


def _role_value(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _is_superadmin(user: User | object | None) -> bool:
    return bool(user and _role_value(getattr(user, "role", None)) == UserRole.SUPER_ADMIN.value)


async def _reject_if_not_superadmin_callback(callback: types.CallbackQuery, user: User | object | None) -> bool:
    if _is_superadmin(user):
        return False
    await answer_callback_query_via_runtime(
        callback,
        "عدم دسترسی",
        show_alert=True,
    )
    return True


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ارسال برای همه کاربران بات", callback_data=f"{CALLBACK_PREFIX}:all")],
            [InlineKeyboardButton(text="ارسال برای گروه‌ها", callback_data=f"{CALLBACK_PREFIX}:groups")],
            [InlineKeyboardButton(text="ارسال برای کاربران خاص", callback_data=f"{CALLBACK_PREFIX}:selected")],
            [InlineKeyboardButton(text="لغو", callback_data=f"{CALLBACK_PREFIX}:cancel")],
        ]
    )


def _groups_keyboard(selected_groups: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for group in (
        TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY,
        TELEGRAM_ADMIN_BROADCAST_GROUP_MANAGERS,
        TELEGRAM_ADMIN_BROADCAST_GROUP_TIER1_CUSTOMERS,
    ):
        marker = "✅" if group in selected_groups else "◻️"
        rows.append([InlineKeyboardButton(text=f"{marker} {GROUP_LABELS[group]}", callback_data=f"{CALLBACK_PREFIX}:gt:{group}")])
    rows.append([InlineKeyboardButton(text="ادامه و نوشتن پیام", callback_data=f"{CALLBACK_PREFIX}:groups_done")])
    rows.append([InlineKeyboardButton(text="لغو", callback_data=f"{CALLBACK_PREFIX}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _search_results_keyboard(
    recipients: tuple[TelegramAdminBroadcastRecipient, ...],
    *,
    selected_ids: set[int],
) -> InlineKeyboardMarkup:
    rows = []
    for recipient in recipients:
        marker = "✅" if recipient.user_id in selected_ids else "◻️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker} {recipient.display_name}",
                    callback_data=f"{CALLBACK_PREFIX}:tu:{recipient.user_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="جستجوی جدید", callback_data=f"{CALLBACK_PREFIX}:search_again")])
    rows.append([InlineKeyboardButton(text="ادامه و نوشتن پیام", callback_data=f"{CALLBACK_PREFIX}:selected_done")])
    rows.append([InlineKeyboardButton(text="لغو", callback_data=f"{CALLBACK_PREFIX}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ تایید و قرار دادن در صف ارسال", callback_data=f"{CALLBACK_PREFIX}:confirm")],
            [InlineKeyboardButton(text="لغو", callback_data=f"{CALLBACK_PREFIX}:cancel")],
        ]
    )


def _truncate_preview(content: str, *, max_length: int = 1200) -> str:
    if len(content) <= max_length:
        return content
    return f"{content[:max_length]}\n..."


async def _estimate_recipient_count(user: User, state_data: dict) -> int:
    audience_type = state_data.get("audience_type")
    target_groups = state_data.get("target_groups") or []
    selected_user_ids = state_data.get("selected_user_ids") or []
    async with AsyncSessionLocal() as db:
        recipients = await resolve_telegram_admin_broadcast_recipients(
            db,
            audience_type=audience_type,
            target_groups=target_groups,
            selected_user_ids=selected_user_ids,
            sender_user_id=user.id if audience_type in {TelegramAdminBroadcastAudienceType.ALL.value, TelegramAdminBroadcastAudienceType.GROUP.value} else None,
        )
        return len(recipients)


async def _ask_for_message_text(
    callback: types.CallbackQuery,
    state: FSMContext,
    user: User,
) -> None:
    await state.set_state(AdminBroadcast.awaiting_message_text)
    await edit_callback_message_via_runtime(
        callback,
        user,
        "متن پیام را وارد کنید.\n"
        f"حداکثر طول مجاز: {TELEGRAM_BROADCAST_TEXT_MAX_LENGTH} کاراکتر\n"
        "پیام به صورت متن ساده ارسال می‌شود.",
        source_key="admin-broadcast-ask-text",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="لغو", callback_data=f"{CALLBACK_PREFIX}:cancel")]]
        ),
    )


@router.message(F.text == ADMIN_BROADCAST_BUTTON_TEXT)
async def start_telegram_admin_broadcast(message: types.Message, state: FSMContext, user: User | None = None):
    if not _is_superadmin(user):
        return
    await state.clear()
    await answer_incoming_message_via_runtime(
        message,
        user,
        "📣 ارسال پیام از طریق بات تلگرام\n\n"
        "گیرندگان فقط کاربران فعال، متصل به بات و مجاز به استفاده از بات هستند.",
        source_key="admin-broadcast-menu",
        reply_markup=_main_menu_keyboard(),
    )


@router.callback_query(F.data == f"{CALLBACK_PREFIX}:cancel")
async def cancel_telegram_admin_broadcast(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    await state.clear()
    await edit_callback_message_via_runtime(
        callback,
        user,
        "فرایند ارسال پیام لغو شد.",
        source_key="admin-broadcast-cancel",
    )
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data == f"{CALLBACK_PREFIX}:all")
async def choose_all_recipients(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    await state.update_data(
        audience_type=TelegramAdminBroadcastAudienceType.ALL.value,
        target_groups=[],
        selected_user_ids=[],
    )
    await _ask_for_message_text(callback, state, user)
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data == f"{CALLBACK_PREFIX}:groups")
async def choose_group_recipients(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    await state.update_data(
        audience_type=TelegramAdminBroadcastAudienceType.GROUP.value,
        target_groups=[],
        selected_user_ids=[],
    )
    await edit_callback_message_via_runtime(
        callback,
        user,
        "گروه‌های دریافت‌کننده را انتخاب کنید:",
        source_key="admin-broadcast-groups",
        reply_markup=_groups_keyboard(set()),
    )
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:gt:"))
async def toggle_group_recipient(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    group = str(callback.data or "").split(":", 2)[2]
    if group not in SUPPORTED_TELEGRAM_ADMIN_BROADCAST_GROUPS:
        await answer_callback_query_via_runtime(
            callback,
            "گروه نامعتبر است.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    selected_groups = set(data.get("target_groups") or [])
    if group in selected_groups:
        selected_groups.remove(group)
    else:
        selected_groups.add(group)
    await state.update_data(
        audience_type=TelegramAdminBroadcastAudienceType.GROUP.value,
        target_groups=sorted(selected_groups),
    )
    await callback.message.edit_reply_markup(reply_markup=_groups_keyboard(selected_groups))
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data == f"{CALLBACK_PREFIX}:groups_done")
async def finish_group_selection(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    data = await state.get_data()
    if not data.get("target_groups"):
        await answer_callback_query_via_runtime(
            callback,
            "حداقل یک گروه انتخاب کنید.",
            show_alert=True,
        )
        return
    await _ask_for_message_text(callback, state, user)
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data == f"{CALLBACK_PREFIX}:selected")
async def choose_selected_recipients(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    await state.set_state(AdminBroadcast.awaiting_search_query)
    await state.update_data(
        audience_type=TelegramAdminBroadcastAudienceType.SELECTED.value,
        target_groups=[],
        selected_user_ids=[],
    )
    await edit_callback_message_via_runtime(
        callback,
        user,
        "نام، نام کاربری، شماره موبایل یا username کاربر را برای جستجو وارد کنید.",
        source_key="admin-broadcast-selected",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="لغو", callback_data=f"{CALLBACK_PREFIX}:cancel")]]
        ),
    )
    await answer_callback_query_via_runtime(callback)


@router.message(AdminBroadcast.awaiting_search_query)
async def process_selected_recipient_search(message: types.Message, state: FSMContext, user: User | None = None):
    if not _is_superadmin(user):
        return
    query = (message.text or "").strip()
    data = await state.get_data()
    selected_ids = set(int(user_id) for user_id in data.get("selected_user_ids") or [])
    async with AsyncSessionLocal() as db:
        recipients = await search_telegram_admin_broadcast_recipients(db, query=query, limit=10)
    await state.update_data(last_search_query=query)
    if recipients:
        text = f"نتایج جستجو برای «{query}»\nانتخاب‌شده‌ها: {len(selected_ids)}"
    else:
        text = f"برای «{query}» کاربر مجاز و متصل به بات پیدا نشد.\nانتخاب‌شده‌ها: {len(selected_ids)}"
    await answer_incoming_message_via_runtime(
        message,
        user,
        text,
        source_key="admin-broadcast-search-results",
        reply_markup=_search_results_keyboard(
            recipients,
            selected_ids=selected_ids,
        ),
    )


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:tu:"))
async def toggle_selected_user(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    try:
        target_user_id = int(str(callback.data or "").split(":", 2)[2])
    except (TypeError, ValueError):
        await answer_callback_query_via_runtime(
            callback,
            "کاربر نامعتبر است.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    selected_ids = set(int(user_id) for user_id in data.get("selected_user_ids") or [])
    if target_user_id in selected_ids:
        selected_ids.remove(target_user_id)
    else:
        if len(selected_ids) >= TELEGRAM_BROADCAST_SELECTED_RECIPIENT_CAP:
            await answer_callback_query_via_runtime(
                callback,
                "تعداد کاربران انتخابی از سقف مجاز بیشتر است.",
                show_alert=True,
            )
            return
        selected_ids.add(target_user_id)
    await state.update_data(selected_user_ids=sorted(selected_ids))

    query = str(data.get("last_search_query") or "").strip()
    recipients: tuple[TelegramAdminBroadcastRecipient, ...] = ()
    if query:
        async with AsyncSessionLocal() as db:
            recipients = await search_telegram_admin_broadcast_recipients(db, query=query, limit=10)
    await edit_callback_message_via_runtime(
        callback,
        user,
        f"انتخاب‌شده‌ها: {len(selected_ids)}",
        source_key="admin-broadcast-selected-toggle",
        reply_markup=_search_results_keyboard(recipients, selected_ids=selected_ids),
    )
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data == f"{CALLBACK_PREFIX}:search_again")
async def search_again(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    await state.set_state(AdminBroadcast.awaiting_search_query)
    data = await state.get_data()
    await edit_callback_message_via_runtime(
        callback,
        user,
        f"جستجوی جدید را وارد کنید.\nانتخاب‌شده‌ها: {len(data.get('selected_user_ids') or [])}",
        source_key="admin-broadcast-search-again",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="لغو", callback_data=f"{CALLBACK_PREFIX}:cancel")]]
        ),
    )
    await answer_callback_query_via_runtime(callback)


@router.callback_query(F.data == f"{CALLBACK_PREFIX}:selected_done")
async def finish_selected_recipients(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    data = await state.get_data()
    if not data.get("selected_user_ids"):
        await answer_callback_query_via_runtime(
            callback,
            "حداقل یک کاربر انتخاب کنید.",
            show_alert=True,
        )
        return
    await _ask_for_message_text(callback, state, user)
    await answer_callback_query_via_runtime(callback)


@router.message(AdminBroadcast.awaiting_message_text)
async def process_broadcast_message_text(message: types.Message, state: FSMContext, user: User | None = None):
    if not _is_superadmin(user):
        return
    try:
        content = validate_telegram_admin_broadcast_content(message.text or "")
    except TelegramAdminBroadcastValidationError as exc:
        if str(exc) == "content_too_long":
            await answer_incoming_message_via_runtime(
                message,
                user,
                f"متن پیام نباید بیشتر از {TELEGRAM_BROADCAST_TEXT_MAX_LENGTH} کاراکتر باشد.",
                source_key="admin-broadcast-content-too-long",
            )
        else:
            await answer_incoming_message_via_runtime(
                message,
                user,
                "متن پیام نمی‌تواند خالی باشد.",
                source_key="admin-broadcast-content-empty",
            )
        return

    data = await state.get_data()
    await state.update_data(content=content)
    try:
        recipient_count = await _estimate_recipient_count(user, {**data, "content": content})
    except TelegramAdminBroadcastValidationError:
        await answer_incoming_message_via_runtime(
            message,
            user,
            "گیرندگان پیام معتبر نیستند. لطفاً فرایند را از ابتدا شروع کنید.",
            source_key="admin-broadcast-recipients-invalid",
        )
        await state.clear()
        return

    await state.set_state(AdminBroadcast.awaiting_confirmation)
    await answer_incoming_message_via_runtime(
        message,
        user,
        "پیش‌نمایش پیام:\n\n"
        f"{_truncate_preview(content)}\n\n"
        f"تعداد گیرندگان واجد شرایط در این لحظه: {recipient_count}\n"
        "برای قرار گرفتن پیام در صف ارسال تایید کنید.",
        source_key="admin-broadcast-preview",
        reply_markup=_confirmation_keyboard(),
    )


@router.callback_query(AdminBroadcast.awaiting_confirmation, F.data == f"{CALLBACK_PREFIX}:confirm")
async def confirm_telegram_admin_broadcast(callback: types.CallbackQuery, state: FSMContext, user: User | None = None):
    if await _reject_if_not_superadmin_callback(callback, user):
        return
    data = await state.get_data()
    content = data.get("content")
    audience_type = data.get("audience_type")
    target_groups = data.get("target_groups") or []
    selected_user_ids = data.get("selected_user_ids") or []

    try:
        async with AsyncSessionLocal() as db:
            result = await create_telegram_admin_broadcast(
                db,
                actor=user,
                content=content,
                audience_type=audience_type,
                target_groups=target_groups,
                selected_user_ids=selected_user_ids,
            )
            broadcast_id = int(result.broadcast.id)
            receipt_count = result.receipt_count
            await db.commit()
    except TelegramAdminBroadcastValidationError as exc:
        await answer_callback_query_via_runtime(
            callback,
            "درخواست معتبر نیست.",
            show_alert=True,
        )
        await edit_callback_message_via_runtime(
            callback,
            user,
            f"خطا در ایجاد پیام: {str(exc)}",
            source_key="admin-broadcast-confirm-error",
        )
        await state.clear()
        return

    await state.clear()
    if receipt_count == 0:
        await edit_callback_message_via_runtime(
            callback,
            user,
            f"گیرنده واجد شرایطی پیدا نشد.\nشناسه ثبت: {broadcast_id}\nپیامی برای ارسال در صف قرار نگرفت.",
            source_key="admin-broadcast-confirm-empty",
        )
    else:
        await edit_callback_message_via_runtime(
            callback,
            user,
            f"✅ پیام در صف ارسال بات قرار گرفت.\nشناسه: {broadcast_id}\nتعداد گیرندگان: {receipt_count}",
            source_key="admin-broadcast-confirm-queued",
        )
    await answer_callback_query_via_runtime(callback)
