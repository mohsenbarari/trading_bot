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
from models.invitation import Invitation
from models.user import User
from bot.states import Registration
from bot.keyboards import get_share_contact_keyboard, get_persistent_menu_keyboard
from bot.handlers.link_account import prompt_contact_for_account_link
from bot.message_manager import (
    set_anchor, 
    delete_previous_anchor,
    DeleteDelay
)

logger = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart(deep_link=True))
async def handle_start_with_token(message: types.Message, command: CommandObject, state: FSMContext, user: Optional[User]):
    
    token = command.args
    
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
            
        await state.update_data(token=token, mobile_number=invitation.mobile_number)
        await state.set_state(Registration.awaiting_contact)
        
        anchor_msg = await message.answer(
            "✅ لینک دعوت شما معتبر است. لطفاً برای تکمیل ثبت‌نام، شماره تماس خود را به اشتراک بگذارید.",
            reply_markup=get_share_contact_keyboard()
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
        anchor_msg = await prompt_contact_for_account_link(
            message,
            state,
            prompt_text=(
                "سلام! اگر حساب شما قبلاً در وب یا با خط فرمان ساخته شده، "
                "برای فعال شدن ربات نیازی به لینک دعوت جدید ندارید. "
                "شماره همراه همان حساب را ارسال کنید تا تلگرام شما متصل شود."
            ),
        )
        set_anchor(message.chat.id, anchor_msg.message_id)


@router.message(Registration.awaiting_contact, F.contact)
async def handle_contact(message: types.Message, state: FSMContext):
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    shared_contact = message.contact
    user_phone_number = shared_contact.phone_number
    
    if not user_phone_number.startswith('+'):
        user_phone_number = '+' + user_phone_number

    state_data = await state.get_data()
    expected_phone_number = state_data.get("mobile_number")
    token = state_data.get("token")

    if not user_phone_number.endswith(expected_phone_number[-10:]) or shared_contact.user_id != message.from_user.id:
        await state.clear()
        bot_response = await message.answer(
            "❌ شماره تماس شما با شماره ثبت شده برای این لینک دعوت مطابقت ندارد. ثبت‌نام انجام نشد.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return

    # ذخیره اطلاعات و رفتن به مرحله آدرس
    await state.update_data(phone_verified=True)
    await state.set_state(Registration.awaiting_address)
    
    anchor_msg = await message.answer(
        "✅ شماره تماس تایید شد!\n\n"
        "📍 آدرس خود را جهت جابجایی سکه وارد نمایید:",
        reply_markup=types.ReplyKeyboardRemove()
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


@router.message(Registration.awaiting_address)
async def handle_address(message: types.Message, state: FSMContext):
    
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    
    address = message.text.strip()
    
    if len(address) < 10:
        bot_response = await message.answer(
            "❌ آدرس وارد شده کوتاه است. لطفاً آدرس کامل‌تری وارد کنید."
        )
        return
    
    state_data = await state.get_data()
    token = state_data.get("token")
    
    await state.clear()

    async with AsyncSessionLocal() as session:
        inv_stmt = select(Invitation).where(Invitation.token == token)
        invitation: Optional[Invitation] = (await session.execute(inv_stmt)).scalar_one_or_none()

        if not invitation or invitation.is_used:
            bot_response = await message.answer("خطا! لینک دعوت شما دیگر معتبر نیست.", reply_markup=types.ReplyKeyboardRemove())
            return

        new_user = User(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            account_name=invitation.account_name,
            mobile_number=invitation.mobile_number,
            address=address,
            role=invitation.role,
            has_bot_access=True
        )

        invitation.is_used = True
        session.add(new_user)
        await session.commit()
        
        # ساخت پیام خوش‌آمدگویی با لینک کانال
        welcome_text = (
            f"✅ خوش آمدید، {message.from_user.full_name}!\n"
            f"ثبت‌نام شما با موفقیت انجام شد.\n\n"
            f"از لینک زیر جهت عضویت در کانال معاملات استفاده کنید:\n"
            
        )
        
        if settings.channel_invite_link:
            welcome_text += f"🔗 [عضویت در کانال معاملات]({settings.channel_invite_link})\n\n"
        
        welcome_text += "برای دسترسی به امکانات، از دکمه‌های زیر استفاده کنید."
        
        anchor_msg = await message.answer(
            welcome_text,
            parse_mode="Markdown",
            reply_markup=get_persistent_menu_keyboard(invitation.role, settings.frontend_url)
        )
        set_anchor(message.chat.id, anchor_msg.message_id)


# --- تایید معامله ---
@router.callback_query(F.data.startswith("confirm_trade_"))
async def handle_confirm_trade(callback: types.CallbackQuery, user: Optional[User]):
    if not user:
        await callback.answer("❌ ابتدا ثبت‌نام کنید.", show_alert=True)
        return
    
    from models.offer import Offer, OfferStatus
    from models.trade import Trade, TradeType, TradeStatus
    from sqlalchemy.orm import joinedload
    
    offer_id = int(callback.data.split("_")[-1])
    
    async with AsyncSessionLocal() as session:
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