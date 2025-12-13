# trading_bot/bot/handlers/admin.py
"""هندلرهای مدیریت دعوت‌نامه‌ها"""

from aiogram import Router, types, F, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from typing import Optional
import re
from fastapi import HTTPException, BackgroundTasks
from core.db import AsyncSessionLocal
from models.user import User
from core.enums import UserRole
from core.config import settings
from core.utils import normalize_account_name, normalize_persian_numerals
from bot.states import InvitationCreation
from bot.keyboards import (
    get_role_selection_keyboard, 
    get_commodity_fsm_cancel_keyboard,
    get_admin_panel_keyboard 
)
from bot.message_manager import (
    set_anchor, 
    schedule_message_delete, 
    schedule_delete,
    delete_previous_anchor,
    DeleteDelay
)
from api.routers.invitations import create_invitation
from schemas import InvitationCreate

router = Router()

# --- ۱. تابع کمکی (اصلاح شد) ---
async def _return_to_admin_panel(message: types.Message | types.CallbackQuery, state: FSMContext, bot: Bot):
    """
    لنگر قبلی را حذف می‌کند و لنگر پنل مدیریت را ارسال می‌کند.
    """
    
    # --- تشخیص chat_id بر اساس نوع ورودی ---
    if isinstance(message, types.Message):
        chat_id = message.chat.id
    elif isinstance(message, types.CallbackQuery):
        chat_id = message.message.chat.id
    else:
        # اگر نوع ورودی ناشناخته بود، کاری انجام نده
        return
        
    # --- حذف لنگر قبلی (مثلاً منوی اصلی) ---
    data = await state.get_data()
    last_anchor_id = data.get("anchor_message_id")
    if last_anchor_id:
        try:
            await bot.delete_message(chat_id, last_anchor_id)
        except Exception:
            pass
            
    # --- ارسال لنگر جدید پنل مدیریت ---
    return_msg = await bot.send_message(
        chat_id=chat_id,
        text="...بازگشت به پنل مدیریت",
        reply_markup=get_admin_panel_keyboard()
    )
    
    # --- ذخیره ID لنگر جدید ---
    await state.update_data(anchor_message_id=return_msg.message_id)

# --- شروع FSM ---
@router.message(F.text == "➕ ارسال لینک دعوت")
async def start_invitation_creation(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
        
    schedule_message_delete(message)
    
    await state.set_state(InvitationCreation.awaiting_account_name)
    prompt_msg = await message.answer(
        "لطفاً **نام کاربری (Account Name)** را وارد کنید.\n"
        "(حروف و اعداد فارسی و انگلیسی مجاز است، حداقل ۳ کاراکتر)",
        reply_markup=get_commodity_fsm_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await state.update_data(last_prompt_message_id=prompt_msg.message_id)

@router.callback_query(F.data == "create_invitation_inline")
async def start_invitation_creation_inline(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await callback.answer("شما مجاز به این کار نیستید.", show_alert=True)
        return
    
    await state.set_state(InvitationCreation.awaiting_account_name)
    await callback.message.edit_text(
        "لطفاً **نام کاربری (Account Name)** را وارد کنید.\n"
        "(حروف و اعداد فارسی و انگلیسی مجاز است، حداقل ۳ کارکتر)",
        reply_markup=get_commodity_fsm_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await state.update_data(last_prompt_message_id=callback.message.message_id)
    await callback.answer()

# --- دریافت نام کاربری ---
@router.message(InvitationCreation.awaiting_account_name)
async def process_invitation_account_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    last_prompt_id = data.get("last_prompt_message_id")
    
    schedule_message_delete(message) # حذف فوری
    if last_prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, last_prompt_id)
        except Exception:
            pass

    account_name_raw = message.text.strip()
    
    if not re.match(r"^[a-zA-Z0-9_\u0600-\u06FF۰-۹٠-٩]{3,32}$", account_name_raw):
        error_msg = await message.answer(
            "❌ **نام کاربری نامعتبر است.**\n"
            "لطفاً فقط از حروف و اعداد (فارسی یا انگلیسی) و آندرلاین استفاده کنید (حداقل ۳ کاراکتر).",
            reply_markup=get_commodity_fsm_cancel_keyboard(),
            parse_mode="Markdown"
        )
        await state.update_data(last_prompt_message_id=error_msg.message_id)
        return

    normalized_name = normalize_account_name(account_name_raw)
    await state.update_data(account_name=normalized_name)
    await state.set_state(InvitationCreation.awaiting_mobile_number)
    
    prompt_msg = await message.answer(
        f"✅ نام کاربری `{normalized_name}` ثبت شد.\n"
        "حالا **شماره موبایل** کاربر را وارد کنید (مثال: 09123456789):",
        reply_markup=get_commodity_fsm_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await state.update_data(last_prompt_message_id=prompt_msg.message_id)

# --- دریافت شماره موبایل ---
@router.message(InvitationCreation.awaiting_mobile_number)
async def process_invitation_mobile(message: types.Message, state: FSMContext):
    data = await state.get_data()
    last_prompt_id = data.get("last_prompt_message_id")
    
    schedule_message_delete(message) # حذف فوری
    if last_prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, last_prompt_id)
        except Exception:
            pass

    mobile_number_raw = message.text.strip()
    
    if not re.match(r"^[0۰٠][9۹٩][0-9۰-۹٠-٩]{9}$", mobile_number_raw):
        error_msg = await message.answer(
            "❌ **شماره موبایل نامعتبر است.**\n"
            "لطفاً شماره را با فرمت 09123456789 (فارسی یا انگلیسی) وارد کنید.",
            reply_markup=get_commodity_fsm_cancel_keyboard(),
            parse_mode="Markdown"
        )
        await state.update_data(last_prompt_message_id=error_msg.message_id)
        return
    
    normalized_mobile = normalize_persian_numerals(mobile_number_raw)
    await state.update_data(mobile_number=normalized_mobile)
    await state.set_state(InvitationCreation.awaiting_role)
    
    prompt_msg = await message.answer(
        f"✅ شماره موبایل `{normalized_mobile}` ثبت شد.\n"
        "لطفاً **نقش (سطح دسترسی)** کاربر را انتخاب کنید:",
        reply_markup=get_role_selection_keyboard(),
        parse_mode="Markdown"
    )
    await state.update_data(last_prompt_message_id=prompt_msg.message_id)

# --- دریافت نقش و ایجاد دعوت‌نامه ---
@router.callback_query(InvitationCreation.awaiting_role)
async def process_invitation_role(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await callback.answer("عدم دسترسی", show_alert=True)
        return

    data = await state.get_data()
    last_prompt_id = data.get("last_prompt_message_id")
    
    if last_prompt_id:
        try:
            await callback.message.edit_text("⏳ در حال ساخت لینک...")
            await callback.message.delete()
        except Exception:
            pass

    try:
        role_name = callback.data.split("set_role_")[1]
        role = UserRole[role_name]
    except (IndexError, KeyError):
        await callback.answer("نقش انتخاب شده نامعتبر است.", show_alert=True)
        return

    account_name = data.get("account_name")
    mobile_number = data.get("mobile_number")

    await state.clear()

    if not account_name or not mobile_number:
        error_msg = await callback.message.answer("خطایی رخ داد، اطلاعات ناقص است. لطفاً دوباره تلاش کنید.")
        schedule_message_delete(error_msg)
        await _return_to_admin_panel(callback, state, bot)
        return

    invitation_data = InvitationCreate(
        account_name=account_name,
        mobile_number=mobile_number,
        role=role
    )

    async with AsyncSessionLocal() as db:
        try:
            code = await create_invitation(
                invitation=invitation_data,
                current_user=user,
                db=db,
                background_tasks=BackgroundTasks()
            )
            
            bot_user = await bot.get_me()
            bot_username = bot_user.username
            invite_link = f"https://t.me/{bot_username}?start={code.token}" 

            invite_msg = await callback.message.answer(
                f"✅ لینک دعوت با موفقیت برای نقش **{role.value}** ایجاد شد:\n\n"
                f"**نام کاربری:** `{account_name}`\n"
                f"**موبایل:** `{mobile_number}`\n\n"
                f"لینک مستقیم:\n`{invite_link}`",
                parse_mode="Markdown",
                reply_markup=None 
            )
            # لینک دعوت بعد از 3 روز حذف شود
            schedule_message_delete(invite_msg, DeleteDelay.INVITATION)

        except HTTPException as e:
            if e.detail.startswith("EXISTING_ACTIVE_LINK::"):
                try:
                    _, acc_name, token = e.detail.split("::")
                    bot_user = await bot.get_me()
                    bot_username = bot_user.username
                    invite_link = f"https://t.me/{bot_username}?start={token}"
                    
                    await callback.message.answer(
                        f"⚠️ **لینک قبلی هنوز فعال است**\n\n"
                        f"کاربر **{acc_name}** قبلاً دعوت شده اما هنوز ثبت‌نام نکرده است.\n"
                        f"لینک دعوت فعال برای ایشان مجدداً ارسال می‌شود:\n\n"
                        f"`{invite_link}`",
                        parse_mode="Markdown"
                    )
                except Exception:
                    error_msg = await callback.message.answer(f"❌ خطای سیستمی: {str(e)}", parse_mode=None)
                    schedule_message_delete(error_msg)
            
            else:
                error_msg = await callback.message.answer(
                    f"❌ **خطا در ایجاد دعوت‌نامه:**\n\n{e.detail.replace('**', '')}",
                    parse_mode="Markdown"
                )
                schedule_message_delete(error_msg)
            
        except Exception as e:
            error_msg = await callback.message.answer(
                f"❌ خطای سیستمی: {str(e)}",
                parse_mode=None
            )
            schedule_message_delete(error_msg)
            
    await _return_to_admin_panel(callback, state, bot)
    await callback.answer()

# --- هندلر لغو عملیات ---
@router.callback_query(F.data == "comm_fsm_cancel", StateFilter(InvitationCreation))
async def cancel_invitation_creation(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    last_prompt_id = data.get("last_prompt_message_id")

    await state.clear()
    
    if last_prompt_id:
        try:
            await callback.message.delete()
        except Exception:
            pass

    cancel_msg = await callback.message.answer("عملیات لغو شد.")
    schedule_message_delete(cancel_msg)
    
    await _return_to_admin_panel(callback, state, bot)
    await callback.answer("لغو شد")