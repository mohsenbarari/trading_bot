import logging
from aiogram import Router, F, types, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import Optional
from datetime import datetime

from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.commodity import Commodity
from bot.states import Trade
from core.config import settings
from core.enums import UserRole
from core.db import AsyncSessionLocal
from core.offer_expiry_forwarding import forward_offer_expiry_to_home_server
from core.offer_source import OfferSourceSurface
from core.services.offer_creation_service import OfferCreationCommand, create_authoritative_offer
from core.server_routing import current_server, is_remote_home
from core.services.offer_expiry_service import (
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    expire_offer_authoritatively,
    expire_offers_authoritatively,
)
from core.services.trade_service import (
    validate_lot_sizes,
    validate_quantity,
    validate_price,
    get_available_trade_amounts,
)
from core.services.telegram_offer_channel_service import apply_offer_channel_state
from core.services.telegram_offer_publication_service import publish_offer_to_telegram_channel_once
from core.utils import to_jalali_str, check_user_limits, increment_user_counter, utc_now_naive
from bot.handlers.trade_utils import (
    get_trade_type_keyboard,
    get_lot_type_keyboard,
    get_commodities_keyboard,
    get_quantity_keyboard,
    get_confirm_keyboard
)
from bot.callbacks import (
    TradeTypeCallback,
    CommodityCallback,
    PageCallback,
    QuantityCallback,
    LotTypeCallback,
    TradeActionCallback,
    AcceptLotsCallback,
    SkipNotesCallback,
    TextOfferActionCallback,
    TextOfferActionCallback,
    ACTION_NOOP
)


logger = logging.getLogger(__name__)

router = Router()

BOT_MARKET_CLOSED_MESSAGE = (
    "بعلت بسته بودن بازار درخواست شما ثبت نشد\n"
    "لطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید."
)


async def _expire_offer_after_publication_failure(session, offer: Offer, user_id: int) -> None:
    await expire_offer_authoritatively(
        session,
        offer,
        OfferExpiryCommand(
            reason=OfferExpiryReason.TELEGRAM_SEND_FAILED,
            source_surface=OfferExpirySourceSurface.TELEGRAM_BOT,
            source_server=current_server(),
            expired_by_user_id=user_id,
            expired_by_actor_user_id=user_id,
        ),
        require_authority=False,
    )


async def _bot_market_is_open() -> bool:
    from core.services.market_transition_service import evaluate_current_market_schedule

    async with AsyncSessionLocal() as session:
        evaluation = await evaluate_current_market_schedule(session)
    return bool(getattr(evaluation, "is_open", False))


def _get_price_warning_keyboard(confirm_callback_data: str, cancel_callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚠️ با وجود هشدار منتشر کن", callback_data=confirm_callback_data),
                InlineKeyboardButton(text="❌ انصراف", callback_data=cancel_callback_data),
            ]
        ]
    )


@router.message(F.text == "📈 معامله")
async def handle_trade_button(message: types.Message, state: FSMContext, user: Optional[User]):
    """راهنمای ثبت لفظ متنی به جای فرآیند مرحله‌ای"""
    if not user:
        return

    if user.role == UserRole.WATCH:
        await message.answer("⛔️ شما دسترسی به بخش معاملات را ندارید.")
        return

    if user.trading_restricted_until:
        now = datetime.utcnow()
        if user.trading_restricted_until > now:
            remaining = user.trading_restricted_until - now
            total_seconds = int(remaining.total_seconds())
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str(user.trading_restricted_until, "%Y/%m/%d - %H:%M")
            await message.answer(
                f"⛔️ **حساب شما مسدود است**\n\n"
                f"📅 تاریخ رفع مسدودیت: {expiry_jalali}\n"
                f"⏳ زمان باقی‌مانده: {countdown}\n\n"
                f"تا رفع مسدودیت امکان انتشار لفظ در کانال را ندارید.",
                parse_mode="Markdown",
            )
            return

    if not await _bot_market_is_open():
        await message.answer(BOT_MARKET_CLOSED_MESSAGE)
        return

    await state.clear()
    await message.answer(
        "📝 ثبت لفظ دکمه‌ای غیرفعال شده است.\n\n"
        "لطفاً لفظ را به صورت متن در همین چت ارسال کنید.\n"
        "نمونه‌ها:\n"
        "خ امام 30تا 75800\n"
        "ف ربع بهار 20 عدد 765000: فقط نقدی",
    )


@router.callback_query(TradeTypeCallback.filter())
async def handle_trade_type_selection(
    callback: types.CallbackQuery,
    state: FSMContext,
    user: Optional[User],
    callback_data: TradeTypeCallback,
):
    if not user:
        return

    trade_type = callback_data.type
    trade_type_fa = "🟢 خرید" if trade_type == "buy" else "🔴 فروش"
    await state.update_data(trade_type=trade_type, trade_type_fa=trade_type_fa)
    keyboard = await get_commodities_keyboard(trade_type)
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n\n"
        f"کالای مورد نظر را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(PageCallback.filter())
async def handle_commodity_page(
    callback: types.CallbackQuery,
    state: FSMContext,
    user: Optional[User],
    callback_data: PageCallback,
):
    if not user:
        return

    trade_type = callback_data.trade_type
    trade_type_fa = "🟢 خرید" if trade_type == "buy" else "🔴 فروش"
    keyboard = await get_commodities_keyboard(trade_type, page=callback_data.page)
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {trade_type_fa}\n\n"
        f"کالای مورد نظر را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(CommodityCallback.filter())
async def handle_commodity_selection(
    callback: types.CallbackQuery,
    state: FSMContext,
    user: Optional[User],
    callback_data: CommodityCallback,
):
    if not user:
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Commodity).where(Commodity.id == callback_data.id))
        commodity = result.scalar_one_or_none()

    if not commodity:
        await callback.answer("❌ کالا یافت نشد!", show_alert=True)
        return

    from core.trading_settings import get_trading_settings_async

    ts = await get_trading_settings_async()

    data = await state.get_data()
    await state.update_data(commodity_id=commodity.id, commodity_name=commodity.name)
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {data.get('trade_type_fa', '🟢 خرید')}\n"
        f"کالا: {commodity.name}\n\n"
        f"تعداد را انتخاب کنید یا عدد دلخواه را وارد کنید:",
        parse_mode="Markdown",
        reply_markup=get_quantity_keyboard(
            min_quantity=ts.offer_min_quantity,
            max_quantity=ts.offer_max_quantity,
        ),
    )
    await state.set_state(Trade.awaiting_quantity)
    await callback.answer()


@router.callback_query(Trade.awaiting_quantity, QuantityCallback.filter())
async def handle_quick_quantity(
    callback: types.CallbackQuery,
    state: FSMContext,
    user: Optional[User],
    callback_data: QuantityCallback,
):
    if not user:
        return

    if callback_data.value == "manual":
        await callback.message.answer("✏️ لطفاً تعداد مورد نظر را به عدد وارد کنید:")
        await callback.answer()
        return

    from core.trading_settings import get_trading_settings_async

    quantity = int(callback_data.value)
    ts = await get_trading_settings_async()
    if quantity < ts.offer_min_quantity or quantity > ts.offer_max_quantity:
        await callback.answer(
            f"❌ تعداد مجاز باید بین {ts.offer_min_quantity} تا {ts.offer_max_quantity} باشد.",
            show_alert=True,
        )
        return

    data = await state.get_data()
    await state.update_data(quantity=quantity)
    await callback.message.edit_text(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {data.get('trade_type_fa', '🟢 خرید')}\n"
        f"کالا: {data.get('commodity_name', 'نامشخص')}\n"
        f"تعداد: {quantity}\n\n"
        f"📦 نحوه معامله را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=get_lot_type_keyboard(),
    )
    await state.set_state(Trade.awaiting_lot_type)
    await callback.answer()


@router.message(Trade.awaiting_quantity)
async def handle_manual_quantity(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return

    if await _handoff_stale_wizard_state_to_text_offer(message, state, user):
        return

    from core.trading_settings import get_trading_settings

    try:
        quantity = int((message.text or "").strip())
        if quantity <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح مثبت وارد کنید.")
        return

    ts = get_trading_settings()
    if quantity < ts.offer_min_quantity:
        await message.answer(f"❌ حداقل تعداد باید {ts.offer_min_quantity} باشد.")
        return
    if quantity > ts.offer_max_quantity:
        await message.answer(f"❌ حداکثر تعداد می‌تواند {ts.offer_max_quantity} باشد.")
        return

    data = await state.get_data()
    await state.update_data(quantity=quantity)
    await message.answer(
        f"📈 **ثبت لفظ جدید**\n\n"
        f"نوع معامله: {data.get('trade_type_fa', '🟢 خرید')}\n"
        f"کالا: {data.get('commodity_name', 'نامشخص')}\n"
        f"تعداد: {quantity}\n\n"
        f"📦 نحوه معامله را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=get_lot_type_keyboard(),
    )
    await state.set_state(Trade.awaiting_lot_type)


@router.callback_query(Trade.awaiting_lot_type, LotTypeCallback.filter(F.type == "wholesale"))
async def handle_lot_wholesale(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return

    await state.update_data(is_wholesale=True, lot_sizes=None)
    await callback.message.edit_text(
        "💰 قیمت را وارد کنید (5 یا 6 رقم):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
            ]
        ),
    )
    await state.set_state(Trade.awaiting_price)
    await callback.answer()


@router.callback_query(Trade.awaiting_lot_type, LotTypeCallback.filter(F.type == "retail"))
async def handle_lot_split(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return

    data = await state.get_data()
    quantity = data.get("quantity", 1)
    await state.update_data(is_wholesale=False)
    await callback.message.edit_text(
        f"🔢 ترکیب بخش‌ها را با فاصله وارد کنید:\n"
        f"(مثال: 10 15 25)\n\n"
        f"⚠️ جمع باید برابر {quantity} باشد\n"
        f"⚠️ هر بخش حداقل 5 عدد\n"
        f"⚠️ حداکثر 3 بخش",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
            ]
        ),
    )
    await state.set_state(Trade.awaiting_lot_sizes)
    await callback.answer()


@router.message(Trade.awaiting_lot_sizes)
async def handle_lot_sizes_input(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return

    if await _handoff_stale_wizard_state_to_text_offer(message, state, user):
        return

    data = await state.get_data()
    quantity = data.get("quantity", 1)
    try:
        lot_sizes = [int(part.strip()) for part in (message.text or "").strip().split()]
        if not lot_sizes:
            raise ValueError()
    except ValueError:
        await message.answer("❌ لطفاً اعداد را با فاصله وارد کنید (مثال: 10 15 25)")
        return

    is_valid, error_msg, suggested = validate_lot_sizes(quantity, lot_sizes)
    if not is_valid:
        keyboard = None
        if suggested:
            suggested_str = "_".join(str(item) for item in suggested)
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ قبول: {' '.join(str(item) for item in suggested)}",
                            callback_data=AcceptLotsCallback(lots=suggested_str).pack(),
                        )
                    ]
                ]
            )
        await message.answer(error_msg, reply_markup=keyboard)
        return

    await state.update_data(lot_sizes=sorted(lot_sizes, reverse=True))
    await message.answer(
        "💰 قیمت را وارد کنید (5 یا 6 رقم):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
            ]
        ),
    )
    await state.set_state(Trade.awaiting_price)


@router.callback_query(AcceptLotsCallback.filter())
async def handle_accept_suggested_lots(
    callback: types.CallbackQuery,
    state: FSMContext,
    user: Optional[User],
    callback_data: AcceptLotsCallback,
):
    if not user:
        await callback.answer()
        return

    lot_sizes = [int(item) for item in callback_data.lots.split("_")]
    await state.update_data(lot_sizes=lot_sizes)
    await callback.message.edit_text(
        "💰 قیمت را وارد کنید (5 یا 6 رقم):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ انصراف", callback_data=TradeActionCallback(action="cancel").pack())]
            ]
        ),
    )
    await state.set_state(Trade.awaiting_price)
    await callback.answer()


@router.message(Trade.awaiting_price)
async def handle_price_input(message: types.Message, state: FSMContext, user: Optional[User], bot: Bot):
    if not user:
        return

    if await _handoff_stale_wizard_state_to_text_offer(message, state, user, bot):
        return

    price_text = (message.text or "").strip()
    is_valid, price_error = validate_price(price_text)
    if not is_valid:
        await message.answer(price_error.replace("price", "قیمت"))
        return

    await state.update_data(price=int(price_text))
    await message.answer(
        "📝 **توضیحات یا شرایط (اختیاری)**\n\n"
        "اگر شرایط یا توضیحات خاصی دارید وارد کنید.\n"
        "مثال: فقط نقدی، حداقل 10 عدد، ...\n\n"
        "_حداکثر 200 کاراکتر_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏭️ بدون توضیحات", callback_data=SkipNotesCallback(target="notes").pack())]
            ]
        ),
    )
    await state.set_state(Trade.awaiting_notes)


@router.callback_query(Trade.awaiting_notes, SkipNotesCallback.filter(F.target == "notes"))
async def handle_skip_notes(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return

    await state.update_data(notes=None)
    await show_trade_preview(callback.message, state, edit=True)
    await callback.answer()


@router.message(Trade.awaiting_notes)
async def handle_notes_input(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return

    if await _handoff_stale_wizard_state_to_text_offer(message, state, user):
        return

    notes = (message.text or "").strip()
    if len(notes) > 200:
        await message.answer("❌ توضیحات نباید بیش از 200 کاراکتر باشد.")
        return

    await state.update_data(notes=notes)
    await show_trade_preview(message, state, edit=False)


async def show_trade_preview(message_or_callback, state: FSMContext, edit: bool = False):
    """نمایش پیش‌نمایش لفظ قبل از تایید نهایی"""
    data = await state.get_data()
    trade_type = data.get("trade_type", "buy")
    commodity_name = data.get("commodity_name", "نامشخص")
    quantity = data.get("quantity", 1)
    price = data.get("price", 0)
    notes = data.get("notes")

    trade_emoji = "🟢" if trade_type == "buy" else "🔴"
    trade_label = "خرید" if trade_type == "buy" else "فروش"
    invisible_padding = "\u2800" * 35
    channel_text = f"{trade_emoji}{trade_label} {commodity_name} {quantity} عدد {price:,}"
    if notes:
        channel_text += f"\nتوضیحات: {notes}"
    channel_text += f"\n{invisible_padding}"

    preview = f"**لفظ شما:**\n\n{channel_text}\n\nآیا تایید می‌کنید?"
    if edit:
        await message_or_callback.edit_text(
            preview,
            parse_mode="Markdown",
            reply_markup=get_confirm_keyboard(),
        )
        return

    await message_or_callback.answer(
        preview,
        parse_mode="Markdown",
        reply_markup=get_confirm_keyboard(),
    )


async def _handle_trade_confirm_core(
    callback: types.CallbackQuery,
    state: FSMContext,
    user: Optional[User],
    bot: Bot,
    *,
    check_user_limits_fn,
    to_jalali_str_fn,
    increment_user_counter_fn,
    success_message_text: str,
    unexpected_error_prefix: str,
    warning_confirm_callback_data: str,
    cancel_callback_data: str,
    warning_acknowledged: bool = False,
) -> None:
    if not user:
        await callback.answer()
        return

    if not await _bot_market_is_open():
        await callback.message.edit_text(BOT_MARKET_CLOSED_MESSAGE)
        await state.clear()
        await callback.answer()
        return

    from core.trading_settings import get_trading_settings
    from core.services.trade_service import detect_offer_price_warning, validate_competitive_price

    ts = get_trading_settings()
    data = await state.get_data()
    quantity = data.get("quantity", 1)

    allowed, error_msg = check_user_limits_fn(user, "channel_message")
    if not allowed:
        if user.limitations_expire_at:
            remaining = user.limitations_expire_at - datetime.utcnow()
            total_seconds = max(0, int(remaining.total_seconds()))
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str_fn(user.limitations_expire_at, "%Y/%m/%d - %H:%M")
            error_msg += f"\n\n📅 رفع محدودیت: {expiry_jalali}\n⏳ زمان باقی‌مانده: {countdown}"
        await callback.message.edit_text(f"⚠️ **محدودیت**\n\n{error_msg}", parse_mode="Markdown")
        await state.clear()
        await callback.answer()
        return

    allowed, error_msg = check_user_limits_fn(user, "trade", quantity)
    if not allowed:
        if user.limitations_expire_at:
            remaining = user.limitations_expire_at - datetime.utcnow()
            total_seconds = max(0, int(remaining.total_seconds()))
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str_fn(user.limitations_expire_at, "%Y/%m/%d - %H:%M")
            error_msg += f"\n\n📅 رفع محدودیت: {expiry_jalali}\n⏳ زمان باقی‌مانده: {countdown}"
        await callback.message.edit_text(f"⚠️ **محدودیت**\n\n{error_msg}", parse_mode="Markdown")
        await state.clear()
        await callback.answer()
        return

    async with AsyncSessionLocal() as session:
        from sqlalchemy import func

        active_count = await session.scalar(
            select(func.count(Offer.id)).where(
                Offer.user_id == user.id,
                Offer.status == OfferStatus.ACTIVE,
            )
        )
        if active_count >= ts.max_active_offers:
            await callback.message.edit_text(
                f"❌ شما حداکثر {ts.max_active_offers} لفظ فعال دارید.\n"
                f"لطفاً ابتدا یکی از لفظ‌های قبلی را منقضی کنید.",
                parse_mode="Markdown",
            )
            await state.clear()
            await callback.answer()
            return

    trade_type = data.get("trade_type")
    commodity_id = data.get("commodity_id")
    commodity_name = data.get("commodity_name")
    price = data.get("price")
    is_wholesale = data.get("is_wholesale", True)
    lot_sizes = data.get("lot_sizes")
    notes = data.get("notes")

    price_warning = None
    async with AsyncSessionLocal() as session:
        is_valid_comp, err_comp = await validate_competitive_price(
            db=session,
            offer_type=trade_type,
            commodity_id=commodity_id,
            quantity=quantity,
            proposed_price=price,
            user_id=user.id,
        )
        if is_valid_comp:
            price_warning = await detect_offer_price_warning(
                db=session,
                offer_type=trade_type,
                commodity_id=commodity_id,
                quantity=quantity,
                proposed_price=price,
                user_id=user.id,
            )
    if not is_valid_comp:
        await callback.message.edit_text(err_comp, parse_mode="Markdown")
        await state.clear()
        await callback.answer()
        return

    if price_warning and not warning_acknowledged:
        await callback.message.edit_text(
            price_warning["message"],
            reply_markup=_get_price_warning_keyboard(
                confirm_callback_data=warning_confirm_callback_data,
                cancel_callback_data=cancel_callback_data,
            ),
        )
        await callback.answer()
        return

    if not settings.channel_id:
        await callback.message.edit_text("❌ کانال تنظیم نشده است.")
        await state.clear()
        await callback.answer()
        return

    trade_emoji = "🟢" if trade_type == "buy" else "🔴"
    trade_label = "خرید" if trade_type == "buy" else "فروش"
    invisible_padding = "\u2800" * 35
    channel_message = f"{trade_emoji}{trade_label} {commodity_name} {quantity} عدد {price:,}"
    if notes:
        channel_message += f"\nتوضیحات: {notes}"
    channel_message += f"\n{invisible_padding}"

    try:
        async with AsyncSessionLocal() as session:
            new_offer = await create_authoritative_offer(
                session,
                OfferCreationCommand(
                    source_surface=OfferSourceSurface.TELEGRAM_BOT,
                    owner_user_id=user.id,
                    actor_user_id=user.id,
                    offer_type=OfferType.BUY if trade_type == "buy" else OfferType.SELL,
                    commodity_id=commodity_id,
                    quantity=quantity,
                    price=price,
                    exclude_from_competitive_price=bool(price_warning),
                    price_warning_type=price_warning["warning_type"] if price_warning else None,
                    is_wholesale=is_wholesale,
                    lot_sizes=lot_sizes,
                    original_lot_sizes=lot_sizes,
                    notes=notes,
                    status=OfferStatus.ACTIVE,
                ),
            )
            offer_id = new_offer.id

        from bot.callbacks import ChannelTradeCallback, ExpireOfferCallback

        if is_wholesale or not lot_sizes:
            trade_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{quantity} عدد",
                            callback_data=ChannelTradeCallback(offer_id=offer_id, amount=quantity).pack(),
                        )
                    ]
                ]
            )
        else:
            all_amounts = get_available_trade_amounts(
                quantity=quantity,
                remaining_quantity=quantity,
                is_wholesale=False,
                lot_sizes=sorted(lot_sizes, reverse=True),
            )
            unique_amounts: list[int] = []
            seen_amounts: set[int] = set()
            for amount in all_amounts:
                if amount in seen_amounts:
                    continue
                seen_amounts.add(amount)
                unique_amounts.append(amount)
            trade_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{amount} عدد",
                            callback_data=ChannelTradeCallback(offer_id=offer_id, amount=amount).pack(),
                        )
                        for amount in unique_amounts
                    ]
                ]
            )

        async def send_created_offer_to_channel(_offer, _user):
            sent_msg = await bot.send_message(
                chat_id=settings.channel_id,
                text=channel_message,
                reply_markup=trade_keyboard,
            )
            return sent_msg.message_id

        async with AsyncSessionLocal() as session:
            offer = await session.get(Offer, offer_id)
            if not offer:
                raise RuntimeError("offer_not_found_for_channel_publication")
            try:
                publish_result = await publish_offer_to_telegram_channel_once(
                    session,
                    offer,
                    user,
                    send_offer_to_channel=send_created_offer_to_channel,
                    raise_send_errors=True,
                )
                await session.commit()
            except Exception:
                await session.commit()
                raise
            if not publish_result.message_id:
                raise RuntimeError(publish_result.error_code or "telegram_channel_publication_failed")

            db_user = await session.get(User, user.id)
            if db_user:
                await increment_user_counter_fn(session, db_user, "channel_message")

        from core.cache import incr_active_offer_count

        await incr_active_offer_count(user.id)

        try:
            from core.web_push import schedule_market_offer_web_push

            schedule_market_offer_web_push(offer_id)
        except Exception as push_error:
            logger.warning(f"Market offer Web Push schedule error: {push_error}")

        await callback.message.edit_text(success_message_text, parse_mode="Markdown")
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"**لفظ شما:**\n\n{channel_message}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="❌ منقضی کردن", callback_data=ExpireOfferCallback(offer_id=offer_id).pack())]
                ]
            ),
        )
    except TelegramBadRequest as exc:
        if "offer_id" in locals():
            try:
                async with AsyncSessionLocal() as session:
                    offer = await session.get(Offer, offer_id)
                    if offer:
                        await _expire_offer_after_publication_failure(session, offer, user.id)
            except Exception as rollback_error:
                logger.debug(f"Rollback failed after Telegram error: {rollback_error}")
        await callback.message.edit_text(f"❌ خطا در ارسال به کانال: {exc}")
    except Exception as exc:
        if "offer_id" in locals():
            try:
                async with AsyncSessionLocal() as session:
                    offer = await session.get(Offer, offer_id)
                    if offer:
                        await _expire_offer_after_publication_failure(session, offer, user.id)
            except Exception as rollback_error:
                logger.debug(f"Rollback failed after unexpected error: {rollback_error}")
        await callback.message.edit_text(f"{unexpected_error_prefix}: {exc}")

    await state.clear()
    await callback.answer()


@router.callback_query(TradeActionCallback.filter(F.action == "confirm"))
async def handle_trade_confirm(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    await _handle_trade_confirm_core(
        callback,
        state,
        user,
        bot,
        check_user_limits_fn=check_user_limits,
        to_jalali_str_fn=to_jalali_str,
        increment_user_counter_fn=increment_user_counter,
        success_message_text="✅ لفظ شما با موفقیت در کانال ارسال شد!",
        unexpected_error_prefix="❌ لفظ ثبت نشد",
        warning_confirm_callback_data=TradeActionCallback(action="confirm_warning").pack(),
        cancel_callback_data=TradeActionCallback(action="cancel").pack(),
    )


@router.callback_query(TradeActionCallback.filter(F.action == "confirm_warning"))
async def handle_trade_warning_confirm(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    await _handle_trade_confirm_core(
        callback,
        state,
        user,
        bot,
        check_user_limits_fn=check_user_limits,
        to_jalali_str_fn=to_jalali_str,
        increment_user_counter_fn=increment_user_counter,
        success_message_text="✅ لفظ شما با موفقیت در کانال ارسال شد!",
        unexpected_error_prefix="❌ لفظ ثبت نشد",
        warning_confirm_callback_data=TradeActionCallback(action="confirm_warning").pack(),
        cancel_callback_data=TradeActionCallback(action="cancel").pack(),
        warning_acknowledged=True,
    )


@router.callback_query(TradeActionCallback.filter(F.action == "back_to_type"))
async def handle_back_to_type(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "📈 **ثبت لفظ جدید**\n\nنوع معامله را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=get_trade_type_keyboard(),
    )
    await callback.answer()


@router.callback_query(TradeActionCallback.filter(F.action == "cancel"))
async def handle_trade_cancel(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user:
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text("❌ فرآیند ثبت لفظ لغو شد.")
    await callback.answer()

# ============================================
# TEXT OFFER HANDLER
# ============================================

def _get_offer_suggestion(original_text: str, error_message: str) -> str:
    """پیشنهاد فرمت صحیح بر اساس نوع خطا"""
    import re
    
    # نمونه‌های صحیح
    examples = [
        "خ ربع 30تا 75800",
        "فروش نیم 50عدد 758000",
        "خرید 40تا 87000: فقط نقدی",
        "ف 30تا 75800 15 15"
    ]
    
    hint = "💡 **فرمت صحیح:**\n"
    hint += "`[خ/ف/خرید/فروش] [کالا] [تعداد]تا [قیمت]`\n\n"
    
    # پیشنهادات بر اساس نوع خطا
    if "تعداد" in error_message:
        hint += "📌 تعداد باید با `تا` یا `عدد` همراه باشد\n"
        hint += "مثال: `30تا` یا `30 عدد`\n"
    
    elif "قیمت" in error_message:
        if "چندین" in error_message:
            hint += "📌 فقط یک عدد 5 یا 6 رقمی (قیمت) مجاز است\n"
        else:
            hint += "📌 قیمت باید 5 یا 6 رقم باشد\n"
        hint += "مثال: `75800` یا `758000`\n"
    
    elif "خرید" in error_message or "فروش" in error_message:
        hint += "📌 فقط یک نشانگر معامله مجاز است\n"
        hint += "استفاده کنید از: `خ` یا `ف` یا `خرید` یا `فروش`\n"
    
    elif "بخش" in error_message or "جمع" in error_message:
        hint += "📌 برای خُرده‌فروشی:\n"
        hint += "- حداکثر 3 بخش\n"
        hint += "- هر بخش حداقل 5 عدد\n"
        hint += "- جمع بخش‌ها = تعداد کل\n"
        hint += "مثال: `خ 30تا 75800 15 15`\n"
    
    elif "کاراکتر" in error_message:
        hint += "📌 از علائم خاص استفاده نکنید\n"
        hint += "فقط: حروف، اعداد، فاصله، `-` `/` `,`\n"
    
    elif "حداقل" in error_message or "حداکثر" in error_message:
        from core.trading_settings import get_trading_settings
        ts = get_trading_settings()
        hint += f"📌 تعداد مجاز: {ts.offer_min_quantity} تا {ts.offer_max_quantity}\n"
    
    else:
        hint += "📌 نمونه‌های صحیح:\n"
        for ex in examples[:2]:
            hint += f"  `{ex}`\n"
    
    return hint

# فیلتر: پیام‌هایی که خ/ف/خرید/فروش دارند
def has_trade_indicator(text: str) -> bool:
    """چک می‌کند آیا متن حاوی نشانگر معامله است"""
    import re
    if not text:
        return False
    offer_part = text.split(':')[0]  # فقط قبل از توضیحات
    # خ یا ف مستقل یا خرید/فروش
    pattern = r'(?<![آ-ی])[خف](?![آ-ی])|خرید|فروش'
    return bool(re.search(pattern, offer_part))


async def _handoff_stale_wizard_state_to_text_offer(
    message: types.Message,
    state: FSMContext,
    user: Optional[User],
    bot: Optional[Bot] = None,
) -> bool:
    """اگر کاربر وسط FSM قدیمی یک لفظ متنی کامل فرستاد، همان را پردازش کن."""
    if not user:
        return False

    from bot.handlers.panel import handoff_navigation_button

    if await handoff_navigation_button(message, state, user):
        return True

    if not has_trade_indicator(message.text or ""):
        return False

    await state.clear()
    await handle_text_offer(message, state, user, bot)
    return True


@router.message(F.text.func(lambda text: text and text.strip() == "نشد"))
async def handle_cancel_all_offers_bot(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user:
        return
        
    async with AsyncSessionLocal() as session:
        query = select(Offer).where(
            Offer.user_id == user.id,
            Offer.status == OfferStatus.ACTIVE
        ).options(selectinload(Offer.commodity))
        result = await session.execute(query)
        offers = result.scalars().all()
        
        if not offers:
            await message.answer("شما هیچ لفظ فعالی ندارید.")
            return
            
        from api.routers.realtime import publish_event
        from core.cache import decr_active_offer_count

        local_offers = [offer for offer in offers if not is_remote_home(getattr(offer, "home_server", None))]
        remote_offers = [offer for offer in offers if is_remote_home(getattr(offer, "home_server", None))]
        local_result = await expire_offers_authoritatively(
            session,
            local_offers,
            OfferExpiryCommand(
                reason=OfferExpiryReason.BOT_CANCEL_ALL,
                source_surface=OfferExpirySourceSurface.TELEGRAM_BOT,
                source_server=current_server(),
                expired_by_user_id=user.id,
                expired_by_actor_user_id=user.id,
            ),
            commit=bool(local_offers),
        )

        remote_expired_count = 0
        for offer in remote_offers:
            status_code, _body = await forward_offer_expiry_to_home_server(
                offer.home_server,
                {
                    "offer_id": getattr(offer, "id", None),
                    "offer_public_id": getattr(offer, "offer_public_id", None),
                    "owner_user_id": user.id,
                    "actor_user_id": user.id,
                    "source_surface": OfferExpirySourceSurface.TELEGRAM_BOT.value,
                    "source_server": current_server(),
                    "expire_reason": OfferExpiryReason.BOT_CANCEL_ALL,
                },
            )
            if status_code < 400:
                remote_expired_count += 1

        for offer in local_result.expired_offers:
            await apply_offer_channel_state(offer, reason="bot_cancel_all", timeout=5)
            await publish_event("offer:expired", {"id": offer.id})
            await decr_active_offer_count(user.id)

        for _ in range(remote_expired_count):
            await decr_active_offer_count(user.id)
        
    expired_count = local_result.expired_count + remote_expired_count
    await message.answer(f"✅ تمام لفظ‌های فعال شما ({expired_count} لفظ) منقضی شدند.")


@router.message(F.text.func(has_trade_indicator))
async def handle_text_offer(message: types.Message, state: FSMContext, user: Optional[User], bot: Optional[Bot] = None):
    """پردازش لفظ متنی (خ/ف)"""
    if not user:
        return
    
    # بررسی نقش کاربر
    if user.role == UserRole.WATCH:
        await message.answer("⛔️ شما دسترسی به بخش معاملات را ندارید.")
        return
    
    # بررسی مسدودیت
    if user.trading_restricted_until:
        from datetime import datetime
        now = datetime.utcnow()
        if user.trading_restricted_until > now:
            # محاسبه زمان باقیمانده
            remaining = user.trading_restricted_until - now
            total_seconds = int(remaining.total_seconds())
            
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str(user.trading_restricted_until, "%Y/%m/%d - %H:%M")
            
            await message.answer(
                f"⛔️ **حساب شما مسدود است**\n\n"
                f"📅 تاریخ رفع مسدودیت: {expiry_jalali}\n"
                f"⏳ زمان باقی‌مانده: {countdown}\n\n"
                f"تا رفع مسدودیت امکان انتشار لفظ در کانال را ندارید.",
                parse_mode="Markdown"
            )
            return
    
    # اگر در state دیگری هستیم، پردازش نکن
    current_state = await state.get_state()
    if current_state is not None:
        return

    if not await _bot_market_is_open():
        await message.answer(BOT_MARKET_CLOSED_MESSAGE)
        return
    
    from bot.utils.offer_parser import parse_offer_text, ParsedOffer
    
    result, error = await parse_offer_text(message.text)
    
    # اگر لفظ نیست (خ/ف ندارد)، نادیده بگیر
    if result is None and error is None:
        return
    
    # اگر خطا دارد، پیام خطا با پیشنهاد بده
    if error:
        # ساخت پیام راهنما بر اساس نوع خطا
        suggestion = _get_offer_suggestion(message.text, error.message)
        error_msg = f"{error.message}\n\n{suggestion}"
        await message.answer(error_msg)
        return
    
    # بررسی تعداد لفظ‌های فعال
    from core.trading_settings import get_trading_settings
    ts = get_trading_settings()
    
    async with AsyncSessionLocal() as session:
        from sqlalchemy import func
        active_count = await session.scalar(
            select(func.count(Offer.id)).where(
                Offer.user_id == user.id,
                Offer.status == OfferStatus.ACTIVE
            )
        )
        if active_count >= ts.max_active_offers:
            await message.answer(
                f"❌ شما حداکثر {ts.max_active_offers} لفظ فعال دارید.\n"
                f"لطفاً ابتدا یکی از لفظ‌های قبلی را منقضی کنید."
            )
            return
    
    # ذخیره اطلاعات در state
    await state.update_data(
        trade_type=result.trade_type,
        commodity_id=result.commodity_id,
        commodity_name=result.commodity_name,
        quantity=result.quantity,
        price=result.price,
        is_wholesale=result.is_wholesale,
        lot_sizes=result.lot_sizes,
        notes=result.notes
    )
    
    # نمایش پیش‌نمایش
    trade_emoji = "🟢" if result.trade_type == "buy" else "🔴"
    trade_label = "خرید" if result.trade_type == "buy" else "فروش"
    invisible_padding = "\u2800" * 35
    
    channel_text = f"{trade_emoji}{trade_label} {result.commodity_name} {result.quantity} عدد {result.price:,}"
    if result.notes:
        channel_text += f"\nتوضیحات: {result.notes}"
    channel_text += f"\n{invisible_padding}"
    
    lot_info = "یکجا" if result.is_wholesale else f"خُرد {result.lot_sizes}"
    
    preview = (
        f"**پیش‌نمایش لفظ:**\n\n"
        f"{channel_text}\n\n"
        f"📦 نوع: {lot_info}\n\n"
        f"آیا تایید می‌کنید؟"
    )
    
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تایید و ارسال", callback_data=TextOfferActionCallback(action="confirm").pack()),
            InlineKeyboardButton(text="❌ انصراف", callback_data=TextOfferActionCallback(action="cancel").pack())
        ]
    ])
    
    await message.answer(preview, parse_mode="Markdown", reply_markup=confirm_kb)
    await state.set_state(Trade.awaiting_text_confirm)


@router.callback_query(Trade.awaiting_text_confirm, TextOfferActionCallback.filter(F.action == "confirm"))
async def handle_text_offer_confirm(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    """تایید و ارسال لفظ متنی به کانال (از لاجیک مشترک handle_trade_confirm استفاده می‌کند)"""
    from core.utils import check_user_limits as runtime_check_user_limits
    from core.utils import increment_user_counter as runtime_increment_user_counter
    from core.utils import to_jalali_str as runtime_to_jalali_str

    await _handle_trade_confirm_core(
        callback,
        state,
        user,
        bot,
        check_user_limits_fn=runtime_check_user_limits,
        to_jalali_str_fn=runtime_to_jalali_str,
        increment_user_counter_fn=runtime_increment_user_counter,
        success_message_text="✅ لفظ شما با موفقیت در کانال منتشر شد!",
        unexpected_error_prefix="❌ خطا در ارسال به کانال",
        warning_confirm_callback_data=TextOfferActionCallback(action="confirm_warning").pack(),
        cancel_callback_data=TextOfferActionCallback(action="cancel").pack(),
    )


@router.callback_query(Trade.awaiting_text_confirm, TextOfferActionCallback.filter(F.action == "confirm_warning"))
async def handle_text_offer_warning_confirm(callback: types.CallbackQuery, state: FSMContext, user: Optional[User], bot: Bot):
    from core.utils import check_user_limits as runtime_check_user_limits
    from core.utils import increment_user_counter as runtime_increment_user_counter
    from core.utils import to_jalali_str as runtime_to_jalali_str

    await _handle_trade_confirm_core(
        callback,
        state,
        user,
        bot,
        check_user_limits_fn=runtime_check_user_limits,
        to_jalali_str_fn=runtime_to_jalali_str,
        increment_user_counter_fn=runtime_increment_user_counter,
        success_message_text="✅ لفظ شما با موفقیت در کانال منتشر شد!",
        unexpected_error_prefix="❌ خطا در ارسال به کانال",
        warning_confirm_callback_data=TextOfferActionCallback(action="confirm_warning").pack(),
        cancel_callback_data=TextOfferActionCallback(action="cancel").pack(),
        warning_acknowledged=True,
    )


@router.callback_query(Trade.awaiting_text_confirm, TextOfferActionCallback.filter(F.action == "cancel"))
async def handle_text_offer_cancel(callback: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    """انصراف از لفظ متنی"""
    await callback.message.edit_text("❌ لفظ لغو شد.")
    await state.clear()
    await callback.answer()
