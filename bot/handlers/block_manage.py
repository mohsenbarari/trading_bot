# bot/handlers/block_manage.py
"""
هندلرهای مدیریت بلاک کاربران در بات
"""
import logging
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional
from sqlalchemy import select

from models.user import User
from core.db import AsyncSessionLocal
from core.services.block_service import (
    get_block_status,
    get_blocked_users,
    block_user,
    unblock_user,
    search_users_for_block
)

logger = logging.getLogger(__name__)

router = Router()


# ===== States =====
class BlockStates(StatesGroup):
    searching = State()  # در حال جستجوی کاربر


# ===== Callbacks =====
from aiogram.filters.callback_data import CallbackData

class BlockMenuCallback(CallbackData, prefix="block_menu"):
    action: str  # list, search, back

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
        text="🔙 بازگشت",
        callback_data=BlockMenuCallback(action="back").pack()
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


# ===== Handlers =====

@router.callback_query(BlockMenuCallback.filter(F.action == "main"))
async def show_block_menu(callback: types.CallbackQuery, user: Optional[User]):
    """نمایش منوی اصلی بلاک"""
    if not user:
        await callback.answer()
        return
    
    async with AsyncSessionLocal() as session:
        status = await get_block_status(session, user.id)
    
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
        status_text += "❌ قابلیت مسدود کردن برای شما غیرفعال است.\n"
    
    await callback.message.edit_text(
        status_text,
        parse_mode="Markdown",
        reply_markup=get_block_menu_keyboard(status)
    )
    await callback.answer()


@router.callback_query(BlockMenuCallback.filter(F.action == "list"))
async def show_blocked_list(callback: types.CallbackQuery, user: Optional[User]):
    """نمایش لیست کاربران مسدود"""
    if not user:
        await callback.answer()
        return
    
    async with AsyncSessionLocal() as session:
        blocked = await get_blocked_users(session, user.id)
    
    if not blocked:
        await callback.answer("لیست خالی است", show_alert=True)
        return
    
    text = (
        f"📋 **کاربران مسدود شده**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"برای رفع مسدودیت روی نام کلیک کنید:\n"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_blocked_list_keyboard(blocked)
    )
    await callback.answer()


@router.callback_query(BlockMenuCallback.filter(F.action == "search"))
async def start_search(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    """شروع جستجوی کاربر"""
    if not user:
        await callback.answer()
        return
    
    await state.set_state(BlockStates.searching)
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="❌ انصراف",
            callback_data=BlockMenuCallback(action="back").pack()
        )]
    ])
    
    await callback.message.edit_text(
        "🔍 **جستجوی کاربر**\n\n"
        "شماره موبایل یا نام کاربری را وارد کنید:\n"
        "(حداقل 2 کاراکتر)",
        parse_mode="Markdown",
        reply_markup=cancel_kb
    )
    await callback.answer()


@router.message(BlockStates.searching)
async def handle_search_query(message: types.Message, state: FSMContext, user: Optional[User]):
    """پردازش جستجوی کاربر"""
    if not user:
        return
    
    query = message.text.strip()
    
    if len(query) < 2:
        await message.answer("❌ حداقل 2 کاراکتر وارد کنید.")
        return
    
    async with AsyncSessionLocal() as session:
        users = await search_users_for_block(session, query, user.id, limit=10)
    
    if not users:
        await message.answer(
            "❌ کاربری یافت نشد.\n"
            "دوباره جستجو کنید:",
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
    
    await message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=get_search_results_keyboard(users)
    )


@router.callback_query(BlockUserCallback.filter(F.action == "block"))
async def handle_block_user(callback: types.CallbackQuery, callback_data: BlockUserCallback, user: Optional[User]):
    """مسدود کردن کاربر"""
    if not user:
        await callback.answer()
        return
    
    target_user_id = callback_data.user_id
    
    async with AsyncSessionLocal() as session:
        success, message = await block_user(session, user.id, target_user_id)
    
    await callback.answer(message, show_alert=True)
    
    if success:
        # بازگشت به منوی اصلی
        async with AsyncSessionLocal() as session:
            status = await get_block_status(session, user.id)
        
        status_text = (
            f"🚫 **مدیریت کاربران مسدود**\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ قابلیت مسدود کردن: فعال\n"
            f"📊 مسدود شده: {status['current_blocked']} از {status['max_blocked']}\n"
            f"💡 باقی‌مانده: {status['remaining']}\n"
        )
        
        await callback.message.edit_text(
            status_text,
            parse_mode="Markdown",
            reply_markup=get_block_menu_keyboard(status)
        )


@router.callback_query(BlockUserCallback.filter(F.action == "unblock"))
async def handle_unblock_user(callback: types.CallbackQuery, callback_data: BlockUserCallback, user: Optional[User]):
    """رفع مسدودیت کاربر"""
    if not user:
        await callback.answer()
        return
    
    target_user_id = callback_data.user_id
    
    async with AsyncSessionLocal() as session:
        success, message = await unblock_user(session, user.id, target_user_id)
    
    await callback.answer(message, show_alert=True)
    
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
            await callback.message.edit_text(
                text,
                parse_mode="Markdown",
                reply_markup=get_blocked_list_keyboard(blocked)
            )
        else:
            # لیست خالی شد، برگرد به منو
            async with AsyncSessionLocal() as session:
                status = await get_block_status(session, user.id)
            
            await callback.message.edit_text(
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
        await callback.answer()
        return
    
    await state.clear()
    
    async with AsyncSessionLocal() as session:
        status = await get_block_status(session, user.id)
    
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
        status_text += "❌ قابلیت مسدود کردن برای شما غیرفعال است.\n"
    
    await callback.message.edit_text(
        status_text,
        parse_mode="Markdown",
        reply_markup=get_block_menu_keyboard(status)
    )
    await callback.answer()
