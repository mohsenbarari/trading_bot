# bot/handlers/default.py
import logging

from aiogram import F, Router, types
from typing import Optional
from models.user import User
from bot.utils.redis_helpers import is_deleted_telegram_user
from bot.telegram_callback_answer import answer_callback_query_via_runtime
from bot.telegram_pre_auth_interaction import answer_pre_auth_message_via_runtime

router = Router()
logger = logging.getLogger(__name__)

STALE_PANEL_BUTTON_MESSAGE = "این دکمه دیگر فعال نیست. پنل را دوباره باز کنید."


@router.callback_query(F.data == "noop")
async def acknowledge_noop_callback(callback: types.CallbackQuery):
    """Close Telegram's spinner for deliberately non-interactive labels."""

    await answer_callback_query_via_runtime(callback)


@router.callback_query()
async def handle_unmatched_callback(callback: types.CallbackQuery, user: Optional[User]):
    """Fail visibly for stale panel buttons instead of dropping the callback."""

    callback_prefix = str(callback.data or "").partition(":")[0].partition("_")[0]
    logger.info(
        "Rejected unmatched Telegram callback",
        extra={
            "event": "telegram.callback_unmatched",
            "callback_prefix": callback_prefix[:32] or None,
            "authenticated": user is not None,
        },
    )
    await answer_callback_query_via_runtime(
        callback,
        STALE_PANEL_BUTTON_MESSAGE,
        show_alert=True,
    )

@router.message()
async def handle_unauthorized_messages(message: types.Message, user: Optional[User]):
    """
    این handler به تمام پیام‌هایی که توسط سایر handlerها مدیریت نشده‌اند پاسخ می‌دهد.
    """
    if not user:
        if message.from_user and await is_deleted_telegram_user(message.from_user.id):
            return
        await answer_pre_auth_message_via_runtime(message,
            "⛔️ بات برای شما هنوز فعال نشده است. "
            "اگر حساب شما قبلاً در وب یا با خط فرمان ساخته شده، دستور /link را بزنید و شماره همان حساب را ارسال کنید. "
            "در غیر این صورت با لینک دعوت معتبر ثبت‌نام کنید."
        )
    # اگر کاربر `user` وجود داشته باشد (یعنی مجاز باشد)، به پیام او پاسخی نمی‌دهیم.
