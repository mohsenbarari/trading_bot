# trading_bot/bot/handlers/admin.py
"""هندلرهای مدیریت دعوت‌نامه‌ها"""

from aiogram import Router, types, F, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from typing import Optional
import re
from models.user import User
from core.enums import UserRole
from core.invitation_creation_contracts import (
    InvitationRequesterIdentity,
    build_standard_invitation_idempotency_key,
)
from core.invitation_creation_forwarding import forward_standard_invitation_to_iran
from core.server_routing import SERVER_FOREIGN
from core.utils import normalize_account_name, normalize_persian_numerals
from bot.states import InvitationCreation
from bot.keyboards import (
    get_invitable_roles_for_admin,
    get_role_selection_keyboard, 
    get_commodity_fsm_cancel_keyboard,
    get_admin_panel_keyboard 
)
from bot.message_manager import (
    set_anchor, 
    delete_previous_anchor,
    DeleteDelay
)

router = Router()

_ACCOUNT_NAME_PART = r"a-zA-Z0-9_\u0600-\u06FF۰-۹٠-٩"
_ACCOUNT_NAME_PATTERN = re.compile(
    rf"^[{_ACCOUNT_NAME_PART}]+(?: [{_ACCOUNT_NAME_PART}]+)*$"
)


def _can_manage_invitations(user: Optional[User]) -> bool:
    return bool(user and user.role in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER))


def _deserialize_user_role(raw_role: object) -> Optional[UserRole]:
    if isinstance(raw_role, UserRole):
        return raw_role
    if isinstance(raw_role, str):
        try:
            return UserRole(raw_role)
        except ValueError:
            try:
                return UserRole[raw_role]
            except KeyError:
                return None
    return None

# --- ۱. تابع کمکی (اصلاح شد) ---
async def _return_to_admin_panel(
    message: types.Message | types.CallbackQuery,
    state: FSMContext,
    bot: Bot,
    user_role: Optional[UserRole] = None,
):
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

    if user_role is None:
        user_role = _deserialize_user_role(data.get("inviter_role"))
            
    # --- ارسال لنگر جدید پنل مدیریت ---
    return_msg = await bot.send_message(
        chat_id=chat_id,
        text="...بازگشت به پنل مدیریت",
        reply_markup=get_admin_panel_keyboard(user_role)
    )
    
    # --- ذخیره ID لنگر جدید ---
    await state.update_data(anchor_message_id=return_msg.message_id)

# --- شروع FSM ---
@router.message(F.text == "➕ ارسال لینک دعوت")
async def start_invitation_creation(message: types.Message, state: FSMContext, user: Optional[User]):
    if not _can_manage_invitations(user):
        return
        
    
    await state.set_state(InvitationCreation.awaiting_account_name)
    prompt_msg = await message.answer(
        "لطفاً **نام کاربری (Account Name)** را وارد کنید.\n"
        "(حروف، اعداد، آندرلاین و فاصله بین کلمات مجاز است، حداقل ۳ کاراکتر)",
        reply_markup=get_commodity_fsm_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await state.update_data(last_prompt_message_id=prompt_msg.message_id, inviter_role=user.role.value)

@router.callback_query(F.data == "create_invitation_inline")
async def start_invitation_creation_inline(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not _can_manage_invitations(user):
        await callback.answer("شما مجاز به این کار نیستید.", show_alert=True)
        return
    
    await state.set_state(InvitationCreation.awaiting_account_name)
    await callback.message.edit_text(
        "لطفاً **نام کاربری (Account Name)** را وارد کنید.\n"
        "(حروف، اعداد، آندرلاین و فاصله بین کلمات مجاز است، حداقل ۳ کاراکتر)",
        reply_markup=get_commodity_fsm_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await state.update_data(last_prompt_message_id=callback.message.message_id, inviter_role=user.role.value)
    await callback.answer()

# --- دریافت نام کاربری ---
@router.message(InvitationCreation.awaiting_account_name)
async def process_invitation_account_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    last_prompt_id = data.get("last_prompt_message_id")
    
    if last_prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, last_prompt_id)
        except Exception:
            pass

    account_name_raw = re.sub(r" {2,}", " ", message.text.strip())
    
    if not 3 <= len(account_name_raw) <= 32 or not _ACCOUNT_NAME_PATTERN.fullmatch(account_name_raw):
        error_msg = await message.answer(
            "❌ **نام کاربری نامعتبر است.**\n"
            "لطفاً فقط از حروف، اعداد، آندرلاین و فاصله بین کلمات استفاده کنید (۳ تا ۳۲ کاراکتر).",
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
    inviter_role = _deserialize_user_role(data.get("inviter_role"))
    
    prompt_msg = await message.answer(
        f"✅ شماره موبایل `{normalized_mobile}` ثبت شد.\n"
        "لطفاً **نقش (سطح دسترسی)** کاربر را انتخاب کنید:",
        reply_markup=get_role_selection_keyboard(get_invitable_roles_for_admin(inviter_role)),
        parse_mode="Markdown"
    )
    await state.update_data(last_prompt_message_id=prompt_msg.message_id)

# --- دریافت نقش و ایجاد دعوت‌نامه ---
@router.callback_query(InvitationCreation.awaiting_role)
async def process_invitation_role(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    if not _can_manage_invitations(user):
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

    if role not in get_invitable_roles_for_admin(user.role):
        await callback.answer("این نقش برای شما مجاز نیست.", show_alert=True)
        return

    account_name = data.get("account_name")
    mobile_number = data.get("mobile_number")

    await state.clear()

    if not account_name or not mobile_number:
        error_msg = await callback.message.answer("خطایی رخ داد، اطلاعات ناقص است. لطفاً دوباره تلاش کنید.")
        await _return_to_admin_panel(callback, state, bot, user.role)
        return

    requester_identity = InvitationRequesterIdentity(
        account_name=user.account_name,
        mobile_number=user.mobile_number,
        telegram_id=user.telegram_id,
    )
    payload = {
        "requester_identity": requester_identity.model_dump(mode="json"),
        "account_name": account_name,
        "mobile_number": mobile_number,
        "role": role.value,
        "source_server": SERVER_FOREIGN,
        "idempotency_key": build_standard_invitation_idempotency_key(
            requester_identity=requester_identity,
            account_name=account_name,
            mobile_number=mobile_number,
            role=role,
        ),
    }
    try:
        status_code, result = await forward_standard_invitation_to_iran(payload)
    except Exception:
        await callback.message.answer("❌ خطای سیستمی در ارتباط با سرور ایران.")
        await _return_to_admin_panel(callback, state, bot, user.role)
        await callback.answer()
        return
    if status_code >= 400 or not isinstance(result, dict):
        detail = result.get("detail") if isinstance(result, dict) else None
        await callback.message.answer(
            f"❌ **خطا در ایجاد دعوت‌نامه:**\n\n{str(detail or 'پاسخ نامعتبر از سرور ایران').replace('**', '')}",
            parse_mode="Markdown",
        )
    else:
        bot_link = result.get("bot_link") or result.get("link")
        web_link = result.get("web_link")
        if not bot_link or not web_link:
            await callback.message.answer("❌ پاسخ ساخت دعوت‌نامه ناقص است.")
        else:
            await callback.message.answer(
                f"✅ لینک دعوت برای نقش **{role.value}** آماده است:\n\n"
                f"**نام کاربری:** `{account_name}`\n"
                f"**موبایل:** `{mobile_number}`\n\n"
                f"لینک تلگرام:\n`{bot_link}`\n\n"
                f"لینک وب‌اپ:\n`{web_link}`",
                parse_mode="Markdown",
                reply_markup=None,
            )
            
    await _return_to_admin_panel(callback, state, bot, user.role)
    await callback.answer()

# --- هندلر لغو عملیات ---
@router.callback_query(F.data == "comm_fsm_cancel", StateFilter(InvitationCreation))
async def cancel_invitation_creation(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    last_prompt_id = data.get("last_prompt_message_id")
    inviter_role = _deserialize_user_role(data.get("inviter_role"))

    await state.clear()
    
    if last_prompt_id:
        try:
            await callback.message.delete()
        except Exception:
            pass

    cancel_msg = await callback.message.answer("عملیات لغو شد.")
    
    await _return_to_admin_panel(callback, state, bot, inviter_role)
    await callback.answer("لغو شد")
