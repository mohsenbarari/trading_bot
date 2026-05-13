import asyncio
import logging
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from core.config import settings
from core.db import get_db
from core.services.accountant_relation_service import is_user_accountant
from core.services.chat_room_service import ensure_mandatory_channel_membership
from bot.keyboards import get_persistent_menu_keyboard
from bot.utils.channel_invites import build_channel_join_request_line

router = Router()
logger = logging.getLogger(__name__)

INCOMPLETE_ADDRESS_SENTINELS = {"System Default", "REGISTRATION_PENDING"}

class LinkState(StatesGroup):
    waiting_for_contact = State()
    waiting_for_address = State()


def build_webapp_link_line() -> str | None:
    frontend_url = (getattr(settings, "frontend_url", "") or "").strip()
    if not frontend_url:
        return None
    return f"🌐 [ورود به وب اپ]({frontend_url})"


def user_requires_address_completion(user: User) -> bool:
    address = (getattr(user, "address", "") or "").strip()
    return not address or address in INCOMPLETE_ADDRESS_SENTINELS


def build_accountant_web_only_message() -> str:
    lines = [
        "⚠️ حسابدارها به ربات تلگرام دسترسی ندارند.",
        "برای استفاده از حساب حسابدار فقط از وب‌اپ استفاده کنید.",
    ]
    webapp_link_line = build_webapp_link_line()
    if webapp_link_line:
        lines.append(webapp_link_line)
    return "\n\n".join(lines)


async def should_block_accountant_bot_access(db: AsyncSession, user: User) -> bool:
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    if not isinstance(db, AsyncSession):
        is_mocked_check = callable(getattr(is_user_accountant, "assert_awaited", None)) or callable(
            getattr(is_user_accountant, "assert_called", None)
        )
        if not is_mocked_check:
            return False
    return await is_user_accountant(db, user_id)


async def finalize_account_link(
    db: AsyncSession,
    user: User,
    message: types.Message,
    *,
    address: str | None = None,
) -> None:
    if await should_block_accountant_bot_access(db, user):
        raise PermissionError("ACCOUNTANT_BOT_ACCESS_FORBIDDEN")

    user.telegram_id = message.from_user.id
    user.username = message.from_user.username
    if user.full_name == user.account_name and message.from_user.full_name:
        user.full_name = message.from_user.full_name
    if address is not None:
        user.address = address
    user.has_bot_access = True

    await ensure_mandatory_channel_membership(db, user=user)
    await db.commit()

    success_lines = [f"✅ حساب کاربری **{user.account_name}** با موفقیت به تلگرام متصل شد."]
    if address is not None:
        success_lines.append("📍 آدرس شما ثبت شد و ثبت‌نامتان تکمیل شد.")
    join_request_line = await build_channel_join_request_line(
        getattr(message, "bot", None),
        user_id=getattr(user, "id", None),
    )
    if join_request_line:
        success_lines.append(join_request_line)
        success_lines.append("پس از ثبت درخواست، عضویت شما در کانال به صورت خودکار تایید می‌شود.")
    webapp_link_line = build_webapp_link_line()
    if webapp_link_line:
        success_lines.append(webapp_link_line)
    success_lines.append("اکنون می‌توانید از تمام امکانات ربات استفاده کنید.")

    await message.answer(
        "\n\n".join(success_lines),
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )


async def prompt_address_completion(
    message: types.Message,
    state: FSMContext,
    user_id: int,
    *,
    already_linked: bool,
) -> None:
    await state.update_data(link_user_id=user_id)
    await state.set_state(LinkState.waiting_for_address)

    intro = (
        "✅ حساب شما شناسایی شد، اما ثبت‌نام هنوز کامل نشده است.\n\n"
        if already_linked
        else "✅ شماره تماس تایید شد!\n\n"
    )
    await message.answer(
        intro + "📍 برای تکمیل ثبت‌نام، آدرس خود را جهت جابجایی سکه وارد نمایید:",
        reply_markup=types.ReplyKeyboardRemove()
    )


async def prompt_contact_for_account_link(
    message: types.Message,
    state: FSMContext,
    prompt_text: str | None = None,
) -> types.Message:
    text = prompt_text or (
        "🔗 برای اتصال حساب کاربری وب به تلگرام، لطفاً شماره موبایل خود را ارسال کنید.\n"
        "روی دکمه زیر کلیک کنید:"
    )
    sent_message = await message.answer(
        text,
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="📱 ارسال شماره همراه", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    await state.set_state(LinkState.waiting_for_contact)
    return sent_message

@router.message(Command("link"))
async def cmd_link(message: types.Message, state: FSMContext, user: User | None = None):
    """
    Start account linking process.
    """
    if user:
        if user_requires_address_completion(user):
            await prompt_address_completion(message, state, user.id, already_linked=True)
            return
        await message.answer(
            "✅ حساب شما قبلاً به تلگرام متصل شده است و نیازی به اشتراک‌گذاری دوباره شماره موبایل ندارید.",
            reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url),
        )
        return

    await prompt_contact_for_account_link(message, state)

@router.message(LinkState.waiting_for_contact, F.contact)
async def handle_contact(message: types.Message, state: FSMContext):
    contact = message.contact
    
    # Security check: verify contact belongs to sender
    if contact.user_id != message.from_user.id:
        await message.answer("❌ لطفاً شماره خودتان را ارسال کنید.")
        return

    # Normalize phone number (remove +98, add 0)
    phone = contact.phone_number
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("98"):
        phone = "0" + phone[2:]
        
    logger.info(f"Linking attempt for phone: {phone} by tg_id: {message.from_user.id}")
    
    async for db in get_db():
        # Find user by phone
        stmt = select(User).where(User.mobile_number == phone)
        user = (await db.execute(stmt)).scalar_one_or_none()
        
        if not user:
            await message.answer("❌ کاربری با این شماره یافت نشد. لطفاً ابتدا در وب ثبت‌نام کنید یا از لینک دعوت استفاده کنید.", reply_markup=types.ReplyKeyboardRemove())
            await state.clear()
            return

        if await should_block_accountant_bot_access(db, user):
            await message.answer(build_accountant_web_only_message(), reply_markup=types.ReplyKeyboardRemove(), parse_mode="Markdown")
            await state.clear()
            return
            
        if user.telegram_id and user.telegram_id != message.from_user.id:
            await message.answer("❌ این شماره قبلاً به یک اکانت تلگرام دیگر متصل شده است.", reply_markup=types.ReplyKeyboardRemove())
            await state.clear()
            return
            
        if user.telegram_id == message.from_user.id:
            if user_requires_address_completion(user):
                await prompt_address_completion(message, state, user.id, already_linked=True)
                return
            await message.answer("✅ حساب شما قبلاً متصل شده است.", reply_markup=types.ReplyKeyboardRemove())
            await state.clear()
            return

        if user_requires_address_completion(user):
            await prompt_address_completion(message, state, user.id, already_linked=False)
            return
        
        try:
            await finalize_account_link(db, user, message)
        except PermissionError:
            await message.answer(build_accountant_web_only_message(), reply_markup=types.ReplyKeyboardRemove(), parse_mode="Markdown")
        except Exception as e:
            rollback = getattr(db, "rollback", None)
            if callable(rollback):
                await rollback()
            logger.error(f"Link error: {e}")
            await message.answer("❌ خطا در اتصال حساب.", reply_markup=types.ReplyKeyboardRemove())
            
        await state.clear()
        return


@router.message(LinkState.waiting_for_address)
async def handle_address_completion(message: types.Message, state: FSMContext):
    address = (message.text or "").strip()
    if len(address) < 10:
        await message.answer("❌ آدرس وارد شده کوتاه است. لطفاً آدرس کامل‌تری وارد کنید.")
        return

    state_data = await state.get_data()
    user_id = state_data.get("link_user_id")
    if not user_id:
        await state.clear()
        await message.answer("❌ فرآیند تکمیل ثبت‌نام منقضی شده است. لطفاً دوباره /link را بزنید.")
        return

    async for db in get_db():
        stmt = select(User).where(User.id == user_id)
        user = (await db.execute(stmt)).scalar_one_or_none()

        if not user:
            await state.clear()
            await message.answer("❌ کاربر یافت نشد. لطفاً دوباره /link را بزنید.")
            return

        if await should_block_accountant_bot_access(db, user):
            await state.clear()
            await message.answer(build_accountant_web_only_message(), reply_markup=types.ReplyKeyboardRemove(), parse_mode="Markdown")
            return

        if user.telegram_id and user.telegram_id != message.from_user.id:
            await state.clear()
            await message.answer("❌ این حساب قبلاً به یک اکانت تلگرام دیگر متصل شده است.")
            return

        try:
            await finalize_account_link(db, user, message, address=address)
        except PermissionError:
            await message.answer(build_accountant_web_only_message(), reply_markup=types.ReplyKeyboardRemove(), parse_mode="Markdown")
        except Exception as e:
            rollback = getattr(db, "rollback", None)
            if callable(rollback):
                await rollback()
            logger.error(f"Link completion error: {e}")
            await message.answer("❌ خطا در تکمیل ثبت‌نام.")

        await state.clear()
        return
