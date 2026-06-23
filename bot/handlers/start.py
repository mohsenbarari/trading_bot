# trading_bot/bot/handlers/start.py
"""هندلرهای شروع و ثبت‌نام"""

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from typing import Optional
import logging

from core.db import AsyncSessionLocal
from core.config import settings
from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_link_token_service import (
    TelegramLinkTokenError,
    load_pending_telegram_link_token_user_for_update,
)
from core.services.accountant_relation_service import (
    get_pending_accountant_relation_by_invitation_token,
    is_accountant_invitation_token,
)
from core.services.customer_relation_service import (
    get_pending_customer_relation_by_invitation_token,
    is_customer_invitation_token,
)
from models.invitation import Invitation
from models.user import User
from bot.states import Registration
from bot.keyboards import get_persistent_menu_keyboard
from bot.handlers.link_account import (
    BOT_ACCOUNT_INACTIVE_REASON,
    bot_account_access_denial_reason,
    build_bot_account_access_denial_message,
    build_neutral_account_link_message,
    prompt_contact_for_account_link,
)
from bot.message_manager import (
    set_anchor, 
    delete_previous_anchor,
    DeleteDelay
)

logger = logging.getLogger(__name__)

router = Router()


def build_webapp_link_line() -> str | None:
    frontend_url = (getattr(settings, "frontend_url", "") or "").strip()
    if not frontend_url:
        return None
    return f"🌐 [ورود به وب اپ]({frontend_url})"


def build_register_link_line(token: str) -> str | None:
    frontend_url = (getattr(settings, "frontend_url", "") or "").strip()
    if not frontend_url:
        return None
    return f"🌐 [تکمیل ثبت‌نام در وب اپ]({frontend_url}/register?token={token})"


def build_accountant_register_link_line(token: str) -> str | None:
    frontend_url = (getattr(settings, "frontend_url", "") or "").strip()
    if not frontend_url:
        return None
    return f"🌐 [تکمیل ثبت‌نام حسابدار در وب اپ]({frontend_url}/register?token={token})"


def build_customer_register_link_line(token: str) -> str | None:
    frontend_url = (getattr(settings, "frontend_url", "") or "").strip()
    if not frontend_url:
        return None
    return f"🌐 [تکمیل ثبت‌نام مشتری در وب اپ]({frontend_url}/register?token={token})"


@router.message(CommandStart(deep_link=True))
async def handle_start_with_token(message: types.Message, command: CommandObject, state: FSMContext, user: Optional[User]):
    
    token = command.args

    if token and token.startswith("link_"):
        await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
        if user:
            anchor_msg = await message.answer(
                "✅ حساب شما قبلاً به تلگرام متصل شده است.",
                reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url),
            )
            set_anchor(message.chat.id, anchor_msg.message_id)
            return

        raw_link_token = token.replace("link_", "", 1).strip()
        async with AsyncSessionLocal() as session:
            try:
                await load_pending_telegram_link_token_user_for_update(session, raw_link_token)
            except TelegramLinkTokenError:
                anchor_msg = await message.answer(
                    "لینک اتصال آماده نیست یا منقضی شده است. از وب‌اپ دوباره وارد مسیر اتصال شوید.",
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                set_anchor(message.chat.id, anchor_msg.message_id)
                return

        anchor_msg = await prompt_contact_for_account_link(
            message,
            state,
            prompt_text=(
                "برای تکمیل اتصال، شماره موبایل همین حساب را از دکمه زیر ارسال کنید."
            ),
            link_token=raw_link_token,
        )
        set_anchor(message.chat.id, anchor_msg.message_id)
        return
    
    # --- بررسی لینک پروفایل عمومی ---
    if token and token.startswith("profile_"):
        try:
            await message.delete()
        except Exception:
            pass
        
        try:
            target_user_id = int(token.replace("profile_", ""))
            async with AsyncSessionLocal() as session:
                stmt = select(User).where(User.id == target_user_id)
                target_user = (await session.execute(stmt)).scalar_one_or_none()
                
                if target_user and not target_user.is_deleted:
                    profile_text = (
                        f"👤 پروفایل عمومی\n\n"
                        f"🔸 نام کاربری: {target_user.account_name}\n"
                        f"📞 شماره تماس: {target_user.mobile_number}\n"
                        f"📍 آدرس: {target_user.address or 'ثبت نشده'}"
                    )
                    await delete_previous_anchor(message.bot, message.chat.id, delay=0)
                    
                    # دکمه تاریخچه معاملات (فقط برای کاربران لاگین شده)
                    if user:
                        from bot.callbacks import TradeHistoryCallback
                        profile_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="📊 تاریخچه معاملات", callback_data=TradeHistoryCallback(target_user_id=target_user_id).pack())]
                        ])
                    else:
                        profile_keyboard = None
                    
                    anchor_msg = await message.answer(
                        profile_text,
                        reply_markup=profile_keyboard
                    )
                    if user:
                        set_anchor(message.chat.id, anchor_msg.message_id)
                else:
                    await message.answer("❌ کاربر یافت نشد.")
        except (ValueError, Exception):
            await message.answer("❌ لینک نامعتبر است.")
        return
    
    # --- بررسی لینک پاسخ به لفظ ---
    if token and token.startswith("respond_"):
        try:
            await message.delete()
        except Exception:
            pass
        
        if not user:
            await message.answer("❌ برای انجام معامله ابتدا باید ثبت‌نام کنید.")
            return
        
        try:
            from models.offer import Offer, OfferStatus
            from models.trade import Trade, TradeType, TradeStatus
            from sqlalchemy.orm import joinedload
            
            offer_id = int(token.replace("respond_", ""))
            
            async with AsyncSessionLocal() as session:
                stmt = select(Offer).options(
                    joinedload(Offer.user),
                    joinedload(Offer.commodity)
                ).where(Offer.id == offer_id)
                offer = (await session.execute(stmt)).scalar_one_or_none()
                
                if not offer:
                    await message.answer("❌ این لفظ یافت نشد یا منقضی شده است.")
                    return
                
                if offer.status != OfferStatus.ACTIVE:
                    await message.answer("❌ این لفظ دیگر فعال نیست.")
                    return
                
                if offer.user_id == user.id:
                    await message.answer("❌ شما نمی‌توانید به لفظ خودتان پاسخ دهید.")
                    return
                
                # نمایش اطلاعات لفظ و تایید معامله
                offer_type_fa = "خرید" if offer.offer_type.value == "buy" else "فروش"
                respond_type_fa = "فروش" if offer.offer_type.value == "buy" else "خرید"
                
                confirm_text = (
                    f"🤝 **تایید معامله**\n\n"
                    f"📝 لفظ: {offer_type_fa} {offer.commodity.name}\n"
                    f"👤 لفظ‌دهنده: {offer.user.account_name}\n"
                    f"📦 تعداد: {offer.quantity} عدد\n"
                    f"💰 قیمت: {offer.price:,}\n\n"
                    f"شما در حال {respond_type_fa} هستید.\n"
                    f"آیا این معامله را تایید می‌کنید?"
                )
                
                confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ تایید معامله", callback_data=f"confirm_trade_{offer_id}"),
                        InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_respond")
                    ]
                ])
                
                await message.answer(
                    confirm_text,
                    parse_mode="Markdown",
                    reply_markup=confirm_keyboard
                )
                
        except (ValueError, Exception) as e:
            logger.error(f"Error responding to offer: {e}")
            await message.answer("❌ خطا در پردازش درخواست.")
        return
    
    # --- حذف پیام و لنگر برای سایر حالات ---
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    # --- کاربر قبلاً ثبت‌نام کرده ---
    if user:
        anchor_msg = await message.answer(
            "شما قبلاً ثبت‌نام کرده‌اید. برای دسترسی به پنل از دکمه زیر استفاده کنید.",
            reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
        )
        set_anchor(message.chat.id, anchor_msg.message_id)
        return
        
    # --- لینک دعوت ---
    async with AsyncSessionLocal() as session:
        inv_stmt = select(Invitation).where(Invitation.token == token)
        invitation = (await session.execute(inv_stmt)).scalar_one_or_none()
        if not invitation or invitation.is_used:
            bot_response = await message.answer("لینک دعوت شما نامعتبر یا منقضی شده است.", reply_markup=types.ReplyKeyboardRemove())
            return

        if is_accountant_invitation_token(token):
            relation = await get_pending_accountant_relation_by_invitation_token(session, token)
            if not relation:
                await message.answer("لینک دعوت شما نامعتبر یا منقضی شده است.", reply_markup=types.ReplyKeyboardRemove())
                return

            accountant_lines = [
                "✅ دعوت‌نامه حسابدار معتبر است.",
                "ثبت‌نام حسابدار فقط از طریق وب‌اپ انجام می‌شود و این حساب به ربات تلگرام دسترسی نخواهد داشت.",
            ]
            register_line = build_accountant_register_link_line(token)
            if register_line:
                accountant_lines.append(register_line)
            await message.answer(
                "\n\n".join(accountant_lines),
                reply_markup=types.ReplyKeyboardRemove(),
                parse_mode="Markdown",
            )
            return

        if is_customer_invitation_token(token):
            relation = await get_pending_customer_relation_by_invitation_token(session, token)
            if not relation:
                await message.answer("لینک دعوت شما نامعتبر یا منقضی شده است.", reply_markup=types.ReplyKeyboardRemove())
                return

            customer_lines = [
                "✅ دعوت‌نامه مشتری معتبر است.",
                "ثبت‌نام مشتری فقط از طریق وب‌اپ انجام می‌شود و این حساب به ربات تلگرام دسترسی نخواهد داشت.",
            ]
            register_line = build_customer_register_link_line(token)
            if register_line:
                customer_lines.append(register_line)
            await message.answer(
                "\n\n".join(customer_lines),
                reply_markup=types.ReplyKeyboardRemove(),
                parse_mode="Markdown",
            )
            return
            
        register_lines = [
            "✅ لینک دعوت معتبر است.",
            "ثبت‌نام از طریق وب‌اپ انجام می‌شود. پس از تکمیل ثبت‌نام، در صورت مجاز بودن می‌توانید اتصال تلگرام را از داخل وب‌اپ فعال کنید.",
        ]
        register_line = build_register_link_line(token)
        if register_line:
            register_lines.append(register_line)
        anchor_msg = await message.answer(
            "\n\n".join(register_lines),
            reply_markup=types.ReplyKeyboardRemove(),
            parse_mode="Markdown",
        )
        set_anchor(message.chat.id, anchor_msg.message_id)


@router.message(CommandStart(deep_link=False))
async def handle_start_without_token(message: types.Message, state: FSMContext, user: Optional[User]):
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    if user:
        logger.warning(f"DEBUG: Building keyboard with URL: '{settings.frontend_url}'")
        
        anchor_msg = await message.answer(
            f"سلام {user.full_name}! به پنل کاربری خود خوش آمدید. برای دسترسی به امکانات از دکمه زیر استفاده کنید.",
            reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url)
        )
        set_anchor(message.chat.id, anchor_msg.message_id)
    else:
        anchor_msg = await message.answer(
            build_neutral_account_link_message(),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        set_anchor(message.chat.id, anchor_msg.message_id)


@router.message(Registration.awaiting_contact, F.contact)
async def handle_contact(message: types.Message, state: FSMContext):
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    state_data = await state.get_data()
    token = state_data.get("token")
    await state.clear()

    register_lines = [
        "ثبت‌نام از طریق وب‌اپ انجام می‌شود.",
        "بعد از تکمیل ثبت‌نام، اتصال تلگرام را از داخل وب‌اپ فعال کنید.",
    ]
    if token:
        register_line = build_register_link_line(token)
        if register_line:
            register_lines.append(register_line)
    anchor_msg = await message.answer(
        "\n\n".join(register_lines),
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown",
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


@router.message(Registration.awaiting_address)
async def handle_address(message: types.Message, state: FSMContext):
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    state_data = await state.get_data()
    token = state_data.get("token")
    await state.clear()
    register_lines = [
        "این مسیر ثبت‌نام در ربات فعال نیست.",
        "برای تکمیل ثبت‌نام از وب‌اپ استفاده کنید.",
    ]
    if token:
        register_line = build_register_link_line(token)
        if register_line:
            register_lines.append(register_line)
    anchor_msg = await message.answer(
        "\n\n".join(register_lines),
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown",
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


@router.chat_join_request()
async def handle_channel_join_request(join_request: types.ChatJoinRequest):
    if not settings.channel_id or join_request.chat.id != settings.channel_id:
        return

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(
            User.telegram_id == join_request.from_user.id,
            User.is_deleted == False,
        )
        user = (await session.execute(stmt)).scalar_one_or_none()

        denial_reason = bot_account_access_denial_reason(user)
        if user:
            decision = await evaluate_bot_access(session, user)
            if not decision.allowed:
                denial_reason = decision.reason or BOT_ACCOUNT_INACTIVE_REASON

    if denial_reason:
        await join_request.bot.decline_chat_join_request(
            chat_id=join_request.chat.id,
            user_id=join_request.from_user.id,
        )
        decline_text = build_bot_account_access_denial_message(denial_reason)
        try:
            await join_request.bot.send_message(
                chat_id=join_request.user_chat_id,
                text=decline_text,
            )
        except Exception:
            logger.exception("Failed to notify declined channel join request user")
        return

    await join_request.bot.approve_chat_join_request(
        chat_id=join_request.chat.id,
        user_id=join_request.from_user.id,
    )
    try:
        await join_request.bot.send_message(
            chat_id=join_request.user_chat_id,
            text="✅ درخواست عضویت شما به صورت خودکار تایید شد. اکنون می‌توانید از کانال معاملات استفاده کنید.",
        )
    except Exception:
        logger.exception("Failed to notify approved channel join request user")


# --- تایید معامله ---
@router.callback_query(F.data.startswith("confirm_trade_"))
async def handle_confirm_trade(callback: types.CallbackQuery, user: Optional[User]):
    if not user:
        await callback.answer("❌ ابتدا ثبت‌نام کنید.", show_alert=True)
        return

    local_denial_reason = bot_account_access_denial_reason(user)
    if local_denial_reason:
        await callback.answer(build_bot_account_access_denial_message(local_denial_reason), show_alert=True)
        return
    
    from models.offer import Offer, OfferStatus
    from models.trade import Trade, TradeType, TradeStatus
    from sqlalchemy.orm import joinedload
    
    offer_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
        decision = await evaluate_bot_access(session, user)
        if not decision.allowed:
            await callback.answer(build_bot_account_access_denial_message(decision.reason), show_alert=True)
            return

        stmt = select(Offer).options(
            joinedload(Offer.user),
            joinedload(Offer.commodity)
        ).where(Offer.id == offer_id)
        offer = (await session.execute(stmt)).scalar_one_or_none()
        
        if not offer:
            await callback.message.edit_text("❌ لفظ یافت نشد.")
            await callback.answer()
            return
        
        if offer.status != OfferStatus.ACTIVE:
            await callback.message.edit_text("❌ این لفظ دیگر فعال نیست.")
            await callback.answer()
            return
        
        if offer.user_id == user.id:
            await callback.message.edit_text("❌ شما نمی‌توانید به لفظ خودتان پاسخ دهید.")
            await callback.answer()
            return
        
        # نوع معامله از دید پاسخ‌دهنده
        trade_type = TradeType.SELL if offer.offer_type.value == "buy" else TradeType.BUY
        
        # ایجاد معامله جدید
        new_trade = Trade(
            offer_id=offer.id,
            offer_user_id=offer.user_id,
            responder_user_id=user.id,
            commodity_id=offer.commodity_id,
            trade_type=trade_type,
            quantity=offer.quantity,
            price=offer.price,
            status=TradeStatus.COMPLETED
        )
        session.add(new_trade)
        
        # بروزرسانی وضعیت لفظ
        offer.status = OfferStatus.COMPLETED
        
        await session.commit()
        
        # پیام تایید
        offer_type_fa = "خرید" if offer.offer_type.value == "buy" else "فروش"
        respond_type_fa = "فروش" if offer.offer_type.value == "buy" else "خرید"
        
        success_text = (
            f"✅ **معامله با موفقیت ثبت شد!**\n\n"
            f"📦 کالا: {offer.commodity.name}\n"
            f"🔢 تعداد: {offer.quantity} عدد\n"
            f"💰 قیمت: {offer.price:,}\n\n"
            f"👤 لفظ‌دهنده ({offer_type_fa}): {offer.user.account_name}\n"
            f"👤 پاسخ‌دهنده ({respond_type_fa}): {user.account_name}\n\n"
            f"📞 برای هماهنگی با طرف معامله تماس بگیرید."
        )
        
        await callback.message.edit_text(success_text, parse_mode="Markdown")
        await callback.answer("✅ معامله ثبت شد!")


# --- انصراف از پاسخ ---
@router.callback_query(F.data == "cancel_respond")
async def handle_cancel_respond(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ انصراف از معامله.")
    await callback.answer()
