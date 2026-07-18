# bot/handlers/block_manage.py
"""
هندلرهای مدیریت بلاک کاربران در بات
"""
import logging
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from typing import Optional
from sqlalchemy import select

from models.user import User
from core.db import AsyncSessionLocal
from core.services.block_service import (
    BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED,
    BLOCK_STATUS_REASON_CUSTOMER_DELEGATED,
    get_block_status,
    get_blocked_users,
    block_user,
    unblock_user,
    search_users_for_block
)
from bot.telegram_callback_answer import answer_callback_query_via_runtime
from bot.telegram_interaction_message import answer_incoming_message_via_runtime

logger = logging.getLogger(__name__)

router = Router()


async def safe_edit_text(message: types.Message, text: str, **kwargs):
    """ویرایش پیام با مدیریت خطای 'message is not modified'"""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ===== States =====
class BlockStates(StatesGroup):
    searching = State()  # در حال جستجوی کاربر


# ===== Callbacks =====
from aiogram.filters.callback_data import CallbackData

class BlockMenuCallback(CallbackData, prefix="block_menu"):
    action: str  # list, search, back, panel

class BlockUserCallback(CallbackData, prefix="block_user"):
    user_id: int
    action: str  # block, unblock


# ===== Keyboards =====
def get_block_menu_keyboard(status: dict) -> InlineKeyboardMarkup:
    """کیبورد منوی اصلی بلاک"""
    buttons = [
        [InlineKeyboardButton(
            text="📋 لیست کاربران مسدود",
            callback_data=BlockMenuCallback(action="list").pack()
        )]
    ]
    
    # فقط اگر قابلیت فعال و ظرفیت داشته باشد
    if status.get("can_block") and status.get("remaining", 0) > 0:
        buttons.append([InlineKeyboardButton(
            text="🔍 جستجو و مسدود کردن",
            callback_data=BlockMenuCallback(action="search").pack()
        )])
    
    buttons.append([InlineKeyboardButton(
        text="🔙 بازگشت به پنل کاربر",
        callback_data=BlockMenuCallback(action="panel").pack()
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_blocked_list_keyboard(blocked_users: list) -> InlineKeyboardMarkup:
    """کیبورد لیست کاربران مسدود با دکمه رفع بلاک"""
    buttons = []
    
    for user in blocked_users[:10]:  # حداکثر 10 تا
        buttons.append([
            InlineKeyboardButton(
                text=f"❌ {user['account_name']}",
                callback_data=BlockUserCallback(user_id=user['id'], action="unblock").pack()
            )
        ])
    
    buttons.append([InlineKeyboardButton(
        text="🔙 بازگشت",
        callback_data=BlockMenuCallback(action="back").pack()
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_search_results_keyboard(users: list) -> InlineKeyboardMarkup:
    """کیبورد نتایج جستجو"""
    buttons = []
    
    for user in users[:10]:
        if user['is_blocked']:
            text = f"✅ {user['account_name']} (مسدود)"
            action = "unblock"
        else:
            text = f"🚫 {user['account_name']}"
            action = "block"
        
        buttons.append([
            InlineKeyboardButton(
                text=text,
                callback_data=BlockUserCallback(user_id=user['id'], action=action).pack()
            )
        ])
    
    buttons.append([InlineKeyboardButton(
        text="🔙 بازگشت",
        callback_data=BlockMenuCallback(action="back").pack()
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_block_menu_text(status: dict) -> str:
    status_text = (
        f"🚫 **مدیریت کاربران مسدود**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if status.get("can_block"):
        status_text += (
            f"✅ قابلیت مسدود کردن: فعال\n"
            f"📊 مسدود شده: {status['current_blocked']} از {status['max_blocked']}\n"
            f"💡 باقی‌مانده: {status['remaining']}\n"
        )
    else:
        reason_message = status.get("reason_message") or "قابلیت مسدود کردن برای شما غیرفعال است."
        status_text += f"❌ {reason_message}\n"

    return status_text


def _delegated_block_management_message(status: dict) -> str | None:
    if status.get("reason_code") not in {
        BLOCK_STATUS_REASON_CUSTOMER_DELEGATED,
        BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED,
    }:
        return None
    return status.get("reason_message") or "مدیریت مسدودسازی برای این حساب مجاز نیست."


async def reject_delegated_block_management(callback: types.CallbackQuery, user: User) -> bool:
    async with AsyncSessionLocal() as session:
        status = await get_block_status(session, user.id)

    message = _delegated_block_management_message(status)
    if not message:
        return False

    await answer_callback_query_via_runtime(
        callback,
        message,
        show_alert=True,
    )
    return True


async def reject_delegated_block_management_message(message: types.Message, user: User) -> bool:
    async with AsyncSessionLocal() as session:
        status = await get_block_status(session, user.id)

    rejection_message = _delegated_block_management_message(status)
    if not rejection_message:
        return False

    await answer_incoming_message_via_runtime(
        message,
        user,
        f"❌ {rejection_message}",
        source_key="block-delegated-rejection",
    )
    return True


async def send_block_menu_message(message: types.Message, user: User) -> types.Message:
    async with AsyncSessionLocal() as session:
        status = await get_block_status(session, user.id)

    return await message.answer(
        build_block_menu_text(status),
        parse_mode="Markdown",
        reply_markup=get_block_menu_keyboard(status),
    )


# ===== Handlers =====

@router.callback_query(BlockMenuCallback.filter(F.action == "main"))
async def show_block_menu(callback: types.CallbackQuery, user: Optional[User]):
    """نمایش منوی اصلی بلاک"""
    if not user:
        await answer_callback_query_via_runtime(callback)
        return

    async with AsyncSessionLocal() as session:
        status = await get_block_status(session, user.id)

    await safe_edit_text(callback.message, 
        build_block_menu_text(status),
        parse_mode="Markdown",
        reply_markup=get_block_menu_keyboard(status)
    )
    await answer_callback_query_via_runtime(callback)


@router.callback_query(BlockMenuCallback.filter(F.action == "list"))
async def show_blocked_list(callback: types.CallbackQuery, user: Optional[User]):
    """نمایش لیست کاربران مسدود"""
    if not user:
        await answer_callback_query_via_runtime(callback)
        return

    if await reject_delegated_block_management(callback, user):
        return
    
    async with AsyncSessionLocal() as session:
        blocked = await get_blocked_users(session, user.id)
    
    if not blocked:
        await answer_callback_query_via_runtime(
            callback,
            "لیست خالی است",
            show_alert=True,
        )
        return
    
    text = (
        f"📋 **کاربران مسدود شده**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"برای رفع مسدودیت روی نام کلیک کنید:\n"
    )
    
    await safe_edit_text(callback.message, 
        text,
        parse_mode="Markdown",
        reply_markup=get_blocked_list_keyboard(blocked)
    )
    await answer_callback_query_via_runtime(callback)


@router.callback_query(BlockMenuCallback.filter(F.action == "search"))
async def start_search(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    """شروع جستجوی کاربر"""
    if not user:
        await answer_callback_query_via_runtime(callback)
        return

    if await reject_delegated_block_management(callback, user):
        return
    
    await state.set_state(BlockStates.searching)
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="❌ انصراف",
            callback_data=BlockMenuCallback(action="back").pack()
        )]
    ])
    
    await safe_edit_text(callback.message, 
        "🔍 **جستجوی کاربر**\n\n"
        "شماره موبایل یا نام کاربری را وارد کنید:\n"
        "(حداقل 2 کاراکتر)",
        parse_mode="Markdown",
        reply_markup=cancel_kb
    )
    await answer_callback_query_via_runtime(callback)


@router.message(BlockStates.searching)
async def handle_search_query(message: types.Message, state: FSMContext, user: Optional[User]):
    """پردازش جستجوی کاربر"""
    if not user:
        return
    
    query = message.text.strip()
    
    if len(query) < 2:
        await answer_incoming_message_via_runtime(
            message,
            user,
            "❌ حداقل 2 کاراکتر وارد کنید.",
            source_key="block-search-short",
        )
        return

    if await reject_delegated_block_management_message(message, user):
        await state.clear()
        return
    
    async with AsyncSessionLocal() as session:
        users = await search_users_for_block(session, query, user.id, limit=10)
    
    if not users:
        await answer_incoming_message_via_runtime(
            message,
            user,
            "❌ کاربری یافت نشد.\n"
            "دوباره جستجو کنید:",
            source_key="block-search-empty",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data=BlockMenuCallback(action="back").pack()
                )]
            ])
        )
        return
    
    await state.clear()
    
    text = (
        f"🔍 **نتایج جستجو**\n\n"
        f"🚫 = کلیک برای مسدود کردن\n"
        f"✅ = کلیک برای رفع مسدودیت\n"
    )
    
    await answer_incoming_message_via_runtime(
        message,
        user,
        text,
        source_key="block-search-results",
        parse_mode="Markdown",
        reply_markup=get_search_results_keyboard(users)
    )


@router.callback_query(BlockUserCallback.filter(F.action == "block"))
async def handle_block_user(callback: types.CallbackQuery, callback_data: BlockUserCallback, user: Optional[User]):
    """مسدود کردن کاربر"""
    if not user:
        await answer_callback_query_via_runtime(callback)
        return

    if await reject_delegated_block_management(callback, user):
        return
    
    target_user_id = callback_data.user_id
    
    async with AsyncSessionLocal() as session:
        success, message = await block_user(session, user.id, target_user_id)
    
    await answer_callback_query_via_runtime(
        callback,
        message,
        show_alert=True,
    )
    
    if success:
        # بازگشت به منوی اصلی
        async with AsyncSessionLocal() as session:
            status = await get_block_status(session, user.id)
        
        await safe_edit_text(callback.message, 
            build_block_menu_text(status),
            parse_mode="Markdown",
            reply_markup=get_block_menu_keyboard(status)
        )


@router.callback_query(BlockUserCallback.filter(F.action == "unblock"))
async def handle_unblock_user(callback: types.CallbackQuery, callback_data: BlockUserCallback, user: Optional[User]):
    """رفع مسدودیت کاربر"""
    if not user:
        await answer_callback_query_via_runtime(callback)
        return

    if await reject_delegated_block_management(callback, user):
        return
    
    target_user_id = callback_data.user_id
    
    async with AsyncSessionLocal() as session:
        success, message = await unblock_user(session, user.id, target_user_id)
    
    await answer_callback_query_via_runtime(
        callback,
        message,
        show_alert=True,
    )
    
    if success:
        # بازگشت به لیست
        async with AsyncSessionLocal() as session:
            blocked = await get_blocked_users(session, user.id)
        
        if blocked:
            text = (
                f"📋 **کاربران مسدود شده**\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"برای رفع مسدودیت روی نام کلیک کنید:\n"
            )
            await safe_edit_text(callback.message, 
                text,
                parse_mode="Markdown",
                reply_markup=get_blocked_list_keyboard(blocked)
            )
        else:
            # لیست خالی شد، برگرد به منو
            async with AsyncSessionLocal() as session:
                status = await get_block_status(session, user.id)
            
            await safe_edit_text(callback.message, 
                f"🚫 **مدیریت کاربران مسدود**\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"✅ لیست خالی است.\n"
                f"📊 ظرفیت: {status['max_blocked']}\n",
                parse_mode="Markdown",
                reply_markup=get_block_menu_keyboard(status)
            )


@router.callback_query(BlockMenuCallback.filter(F.action == "back"))
async def handle_back(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    """بازگشت به منوی اصلی بلاک"""
    if not user:
        await answer_callback_query_via_runtime(callback)
        return
    
    await state.clear()
    
    async with AsyncSessionLocal() as session:
        status = await get_block_status(session, user.id)
    
    await safe_edit_text(callback.message, 
        build_block_menu_text(status),
        parse_mode="Markdown",
        reply_markup=get_block_menu_keyboard(status)
    )
    await answer_callback_query_via_runtime(callback)


@router.callback_query(BlockMenuCallback.filter(F.action == "panel"))
async def back_to_user_panel_from_block_menu(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await answer_callback_query_via_runtime(callback)
        return

    await state.clear()
    await callback.message.edit_text("👤 **پنل کاربر**\n\nاز دکمه‌های پایین پیام استفاده کنید.", parse_mode="Markdown")
    await answer_callback_query_via_runtime(callback)
