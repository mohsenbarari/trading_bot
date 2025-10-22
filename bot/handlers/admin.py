# bot/handlers/admin.py (هماهنگ شده با کیبورد دائمی)
import httpx
from typing import Optional
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from models.user import User
from core.enums import UserRole
from bot.states import InvitationCreation
from bot.keyboards import get_role_selection_keyboard

router = Router()

# اصلاح: این handler اکنون به پیام متنی گوش می‌دهد
@router.message(F.text == "➕ ساخت توکن دعوت")
async def start_invitation_creation(message: types.Message, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN:
        # این پیام نباید برای کاربران عادی نمایش داده شود چون دکمه را ندارند
        return
    
    await state.set_state(InvitationCreation.awaiting_account_name)
    await message.answer("لطفاً نام کاربری منحصر به فرد (account_name) کاربر جدید را وارد کنید:")

# ... بقیه کدهای این فایل بدون تغییر باقی می‌مانند ...
@router.message(InvitationCreation.awaiting_account_name)
async def process_account_name(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await state.clear()
        return
    await state.update_data(account_name=message.text)
    await state.set_state(InvitationCreation.awaiting_mobile_number)
    await message.answer("عالی! حالا لطفاً شماره موبایل کاربر جدید را وارد کنید (مثال: 09123456789):")

@router.message(InvitationCreation.awaiting_mobile_number)
async def process_mobile_number(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await state.clear()
        return
    await state.update_data(mobile_number=message.text)
    await state.set_state(InvitationCreation.awaiting_role)
    await message.answer("بسیار خب. حالا سطح دسترسی کاربر جدید را انتخاب کنید:", reply_markup=get_role_selection_keyboard())

@router.callback_query(F.data.startswith("set_role_"), InvitationCreation.awaiting_role)
async def process_role_and_create(callback_query: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user or user.role != UserRole.SUPER_ADMIN:
        await state.clear()
        return
    
    await callback_query.answer()
    
    role_name = callback_query.data.split("_")[-1]
    role = UserRole[role_name]
    
    user_data = await state.get_data()
    account_name = user_data.get("account_name")
    mobile_number = user_data.get("mobile_number")

    api_url = "http://app:8000/api/invitations/"
    payload = {
        "account_name": account_name,
        "mobile_number": mobile_number,
        "role": role.value
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(api_url, json=payload, timeout=30.0)

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
            error_detail = response.json().get("detail", "خطای نامشخص")
            await callback_query.message.edit_text(f"❌ خطا در ساخت دعوتنامه: {error_detail}")

    except httpx.RequestError as e:
        await callback_query.message.edit_text(f"خطای ارتباط با سرور: {e}")
    
    finally:
        await state.clear()
