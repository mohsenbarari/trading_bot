# bot/handlers/admin.py (نسخه نهایی با پاکسازی پیام لغو)
import httpx
import logging
import asyncio 
import re
from typing import Optional
from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest
from models.user import User
from core.enums import UserRole
from bot.states import InvitationCreation
from bot.keyboards import get_role_selection_keyboard, get_commodity_fsm_cancel_keyboard
from core.config import settings

router = Router()
logger = logging.getLogger(__name__)

# === توابع کمکی (بدون تغییر) ===
PERSIAN_TO_ENGLISH_MAP = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', # ارقام عربی
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9', # ارقام عربی
}
MOBILE_REGEX = r"^09[0-9]{9}$"

def normalize_mobile(mobile: str) -> str:
    if not mobile: return ""
    return mobile.translate(str.maketrans(PERSIAN_TO_ENGLISH_MAP))

def get_auth_headers() -> dict:
    if not settings.dev_api_key: return {"X-Dev-Key": "NOT_SET"} 
    return {"X-Dev-Key": settings.dev_api_key}

async def safe_delete_message(bot: Bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id, message_id)
    except (TelegramBadRequest, Exception) as e:
        logger.warning(f"Could not delete message {message_id} in chat {chat_id}: {e}")

# === هندلر لغو (اصلاح شد) ===
@router.callback_query(F.data == "comm_fsm_cancel", StateFilter(InvitationCreation))
@router.message(F.text == "لغو", StateFilter(InvitationCreation))
async def handle_cancel_invitation_fsm(event: types.Message | types.CallbackQuery, state: FSMContext, user: Optional[User]):
    """فرایند ساخت دعوت‌نامه را لغو و پیام‌ها را پاک می‌کند."""
    if not user: return
    
    data = await state.get_data()
    await state.clear()
    
    target_message = event.message if isinstance(event, types.CallbackQuery) else event
    
    # حذف پیام پرامپت (که دکمه لغو داشت)
    if isinstance(event, types.CallbackQuery):
        await event.answer("لغو شد")
        await safe_delete_message(event.bot, event.message.chat.id, event.message.message_id)
    elif isinstance(event, types.Message):
        await event.delete() # حذف پیام "لغو" کاربر
        prompt_message_id = data.get("prompt_message_id")
        if prompt_message_id:
            await safe_delete_message(event.bot, event.chat.id, prompt_message_id)

    # --- شروع اصلاح: حذف پیام لغو پس از 30 ثانیه ---
    # ۱. پیام لغو را ارسال و ذخیره کن
    cancel_msg = await target_message.answer("عملیات ساخت دعوت‌نامه لغو شد.")
    
    # ۲. 30 ثانیه صبر کن
    await asyncio.sleep(30)
    
    # ۳. پیام لغو را حذف کن
    await safe_delete_message(target_message.bot, cancel_msg.chat.id, cancel_msg.message_id)
    # --- پایان اصلاح ---

# === FSM (بدون تغییر) ===
@router.message(F.text == "➕ ارسال لینک دعوت")
async def start_invitation_creation(message: types.Message, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN:
        return
    await message.delete() 
    await state.set_state(InvitationCreation.awaiting_account_name)
    prompt_msg = await message.answer("لطفاً نام کاربری منحصر به فرد (account_name) کاربر جدید را وارد کنید:", reply_markup=get_commodity_fsm_cancel_keyboard())
    await state.update_data(prompt_message_id=prompt_msg.message_id)

@router.message(InvitationCreation.awaiting_account_name)
async def process_account_name(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await state.clear(); return
    data = await state.get_data()
    prompt_message_id = data.get("prompt_message_id")
    await message.delete()
    if prompt_message_id:
        await safe_delete_message(message.bot, message.chat.id, prompt_message_id)
    await state.update_data(account_name=message.text)
    await state.set_state(InvitationCreation.awaiting_mobile_number)
    prompt_msg = await message.answer("عالی! حالا لطفاً شماره موبایل کاربر جدید را وارد کنید (مثال: 09123456789):", reply_markup=get_commodity_fsm_cancel_keyboard())
    await state.update_data(prompt_message_id=prompt_msg.message_id)

@router.message(InvitationCreation.awaiting_mobile_number)
async def process_mobile_number(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await state.clear(); return

    data = await state.get_data()
    prompt_message_id = data.get("prompt_message_id")

    await message.delete()
    if prompt_message_id:
        await safe_delete_message(message.bot, message.chat.id, prompt_message_id)
    
    normalized_mobile = normalize_mobile(message.text)
    
    if not re.match(MOBILE_REGEX, normalized_mobile):
        prompt_msg = await message.answer(
            f"❌ شماره موبایل **'{message.text}'** نامعتبر است.\n\n"
            "لطفاً یک شماره ۱۱ رقمی صحیح (فارسی یا انگلیسی) که با 09 شروع می‌شود وارد کنید:",
            reply_markup=get_commodity_fsm_cancel_keyboard(),
            parse_mode="Markdown"
        )
        await state.set_state(InvitationCreation.awaiting_mobile_number)
        await state.update_data(prompt_message_id=prompt_msg.message_id)
        return

    await state.update_data(mobile_number=normalized_mobile)
    await state.set_state(InvitationCreation.awaiting_role)
    
    await message.answer("بسیار خب. حالا سطح دسترسی کاربر جدید را انتخاب کنید:", reply_markup=get_role_selection_keyboard())

@router.callback_query(F.data.startswith("set_role_"), InvitationCreation.awaiting_role)
async def process_role_and_create(callback_query: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await state.clear(); return
    
    await callback_query.answer("در حال بررسی اطلاعات...")
    
    role_name = callback_query.data.split("_")[-1]
    role = UserRole[role_name]
    
    user_data = await state.get_data()
    account_name = user_data.get("account_name")
    mobile_number = user_data.get("mobile_number")

    api_url = "http://app:8000/api/invitations/"
    payload = {"account_name": account_name, "mobile_number": mobile_number, "role": role.value}
    headers = get_auth_headers()
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(api_url, json=payload, timeout=30.0, headers=headers)

        if response.status_code == 201:
            invitation_data = response.json()
            token = invitation_data.get("token")
            bot_username = (await callback_query.bot.get_me()).username
            invitation_link = f"https://t.me/{bot_username}?start={token}"
            
            await callback_query.message.edit_text(
                f"✅ دعوتنامه با موفقیت برای **{role.value}** ساخته شد!\n\n"
                f"**نام کاربری:** `{account_name}`\n"
                f"**شماره موبایل:** `{mobile_number}`\n\n"
                f"لینک دعوت:\n`{invitation_link}`",
                parse_mode="Markdown"
            )
        else:
            try:
                error_detail = response.json().get("detail", "خطای نامشخص")
            except Exception:
                error_detail = response.text
            
            error_msg_text = f"❌ {error_detail}"
            error_msg = await callback_query.message.answer(error_msg_text, parse_mode="Markdown")
            await safe_delete_message(callback_query.bot, callback_query.message.chat.id, callback_query.message.message_id)
            
            await asyncio.sleep(30)
            await safe_delete_message(callback_query.bot, error_msg.chat.id, error_msg.message_id)

    except httpx.RequestError as e:
        error_msg = await callback_query.message.answer(f"❌ خطای ارتباط با سرور: {e}")
        await safe_delete_message(callback_query.bot, callback_query.message.chat.id, callback_query.message.message_id)
        await asyncio.sleep(30)
        await safe_delete_message(callback_query.bot, error_msg.chat.id, error_msg.message_id)
    
    finally:
        await state.clear()