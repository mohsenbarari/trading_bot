# bot/middlewares/auth.py (نسخه نهایی و اصلاح شده)
from typing import Callable, Dict, Any, Awaitable, Optional
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery, Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from bot.onboarding import (
    BOT_ONBOARDING_BLOCK_MESSAGE,
    CUSTOMER_TUTORIAL_ACK_CALLBACK,
    OFFER_TUTORIAL_ACK_CALLBACK,
    build_onboarding_keyboard,
    is_allowed_onboarding_callback,
    onboarding_text_for_step,
    pending_onboarding_step,
    user_requires_bot_onboarding,
)
from core.services.user_account_status_service import is_user_global_web_locked
from core.config import settings
from core.services.telegram_registration_intent_service import (
    registration_activation_block_for_user,
)
from core.registration_feature_policy import direct_registration_runtime_ready
from core.services.bot_access_policy import bot_access_denial_message, evaluate_bot_access
from core.services.telegram_username_observation_service import (
    refresh_observed_telegram_username,
)
from models.user import User
from bot.telegram_callback_answer import answer_callback_query_via_runtime
from bot.telegram_interaction_message import answer_incoming_message_via_runtime


def _get_event_and_from_user(event: TelegramObject):
    """برگرداندن (event واقعی برای پاسخ، from_user). وقتی روی dp.update ثبت شده، event از نوع Update است."""
    if isinstance(event, Update):
        inner = event.message or event.callback_query or event.edited_message
        if inner and hasattr(inner, "from_user"):
            return inner, inner.from_user
        return None, None
    if isinstance(event, (Message, CallbackQuery)):
        return event, event.from_user
    return None, None


_HANDLER_USER_ATTRS = (
    "id",
    "telegram_id",
    "sync_version",
    "role",
    "account_status",
    "is_deleted",
    "has_bot_access",
    "trading_restricted_until",
    "limitations_expire_at",
    "max_daily_trades",
    "trades_count",
    "max_active_commodities",
    "commodities_traded_count",
    "max_daily_requests",
    "channel_messages_count",
    "username",
    "full_name",
    "account_name",
    "offer_overtime_minutes",
    "messenger_blocked_at",
    "messenger_grace_expires_at",
)


def _detach_user_for_handler(session: AsyncSession, user: Optional[User]) -> Optional[User]:
    """Close the auth session before handlers run without leaving lazy loads pending.

    Handlers (and sync helpers like get_trading_settings) must not run while this
    middleware still holds an AsyncSession open; that combination trips
    MissingGreenlet under concurrent load. Eager-read common columns, then
    expunge so scalar access stays safe after the session closes.
    """
    if user is None:
        return None
    for attr_name in _HANDLER_USER_ATTRS:
        getattr(user, attr_name, None)
    # Only detach real SQLAlchemy sessions (unit tests pass MagicMock pools).
    if isinstance(session, AsyncSession):
        try:
            session.expunge(user)
        except Exception:
            pass
    return user


class AuthMiddleware(BaseMiddleware):
    """
    این میدل‌ور، session دیتابیس را به handler ها تزریق کرده و کاربر را احراز هویت می‌کند.
    """
    def __init__(self, session_pool: async_sessionmaker[AsyncSession]):
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        inner_event, user_telegram_obj = _get_event_and_from_user(event)
        
        if not user_telegram_obj:
            return await handler(event, data)

        should_call_handler = False
        async with self.session_pool() as session:
            # کاربر را از دیتابیس خودمان پیدا می‌کنیم (فقط کاربران فعال)
            user = (await session.execute(
                select(User).where(User.telegram_id == user_telegram_obj.id, User.is_deleted == False)
            )).scalar_one_or_none()

            observed_username = getattr(user_telegram_obj, "username", None)
            if user is not None and (observed_username is None or isinstance(observed_username, str)):
                user = await refresh_observed_telegram_username(
                    session,
                    user=user,
                    telegram_id=user_telegram_obj.id,
                    observed_username=observed_username,
                )
            
            # آبجکت کاربر دیتابیس (که ممکن است None باشد) را به data اضافه می‌کنیم
            # تا در تمام handler ها در دسترس باشد.
            data["user"] = user

            if user and direct_registration_runtime_ready(settings):
                activation_block = await registration_activation_block_for_user(
                    session,
                    user=user,
                )
                if activation_block is not None:
                    pending_message = (
                        "⏳ ثبت‌نام شما هنوز نهایی نشده است. "
                        "پس از تکمیل همگام‌سازی دوباره تلاش کنید."
                    )
                    if isinstance(inner_event, Message):
                        await answer_incoming_message_via_runtime(
                            inner_event,
                            user,
                            pending_message,
                        )
                    elif isinstance(inner_event, CallbackQuery):
                        await answer_callback_query_via_runtime(
                            inner_event,
                            pending_message,
                            show_alert=True,
                        )
                    return
                access_decision = await evaluate_bot_access(session, user)
                if not access_decision.allowed:
                    denial_message = bot_access_denial_message(access_decision.reason)
                    if isinstance(inner_event, Message):
                        await answer_incoming_message_via_runtime(
                            inner_event,
                            user,
                            denial_message,
                        )
                    elif isinstance(inner_event, CallbackQuery):
                        await answer_callback_query_via_runtime(
                            inner_event,
                            denial_message,
                            show_alert=True,
                        )
                    return
            
            # بررسی قفل سراسری حساب بعد از پایان مهلت غیرفعال‌سازی
            if user and is_user_global_web_locked(user):
                restricted_message = (
                    "⛔ دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است.\n\n"
                    "پس از فعال‌سازی مجدد حساب، دسترسی شما باز می‌شود."
                )
                if inner_event is not None:
                    if isinstance(inner_event, Message):
                        await answer_incoming_message_via_runtime(
                            inner_event,
                            user,
                            restricted_message,
                        )
                    elif isinstance(inner_event, CallbackQuery):
                        await answer_callback_query_via_runtime(
                            inner_event,
                            restricted_message,
                            show_alert=True,
                        )
                return

            if user and user_requires_bot_onboarding(user):
                if isinstance(inner_event, CallbackQuery):
                    if is_allowed_onboarding_callback(user, getattr(inner_event, "data", None)):
                        should_call_handler = True
                    else:
                        await answer_callback_query_via_runtime(
                            inner_event,
                            BOT_ONBOARDING_BLOCK_MESSAGE,
                            show_alert=True,
                        )
                        return
                elif isinstance(inner_event, Message):
                    pending_step = pending_onboarding_step(user)
                    await answer_incoming_message_via_runtime(
                        inner_event,
                        user,
                        onboarding_text_for_step(pending_step or 1),
                        reply_markup=build_onboarding_keyboard(pending_step or 1),
                    )
                    return
                else:
                    return
            else:
                should_call_handler = True

            if should_call_handler:
                data["user"] = _detach_user_for_handler(session, user)

        if should_call_handler:
            return await handler(event, data)
        return None
