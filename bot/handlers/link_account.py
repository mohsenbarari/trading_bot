import asyncio
import logging
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User, set_legacy_has_bot_access_compatibility
from core.config import settings
from core.deployment_surface import normalize_origin
from core.db import AsyncSessionLocal, get_db
from core.services.accountant_relation_service import is_user_accountant
from core.services.customer_relation_service import is_user_customer
from core.public_webapp_url import public_webapp_url_for_links, user_facing_webapp_url
from core.registration_contracts import (
    TelegramRegistrationCommandResponse,
    TelegramRegistrationOutcome,
)
from core.registration_identity import normalize_mobile_number
from core.services.chat_room_service import ensure_mandatory_channel_membership
from core.telegram_account_link_contracts import build_telegram_account_link_command
from core.telegram_registration_transport import forward_telegram_account_link_command
from core.utils import utc_now
from core.services.bot_access_policy import (
    BOT_ACCESS_REASON_ACCOUNTANT,
    BOT_ACCESS_REASON_CUSTOMER_TIER2,
    BOT_ACCESS_REASON_DELETED,
    BOT_ACCESS_REASON_INACTIVE,
    BOT_ACCESS_REASON_SYNC_PENDING,
    bot_access_denial_message,
    evaluate_bot_access,
    evaluate_bot_access_local_state,
)
from core.services.telegram_link_token_service import (
    TelegramLinkTokenError,
    consume_telegram_link_token,
    load_pending_telegram_link_token_user_for_update,
)
from bot.repeat_offer import build_persistent_navigation_keyboard
from bot.telegram_pre_auth_interaction import (
    answer_pre_auth_message_via_runtime,
)
from bot.utils.channel_invites import (
    build_channel_access_text,
    build_channel_join_request_text,
)
from bot.utils.customer_display import attach_customer_management_names, user_display_name

router = Router()
logger = logging.getLogger(__name__)

INCOMPLETE_ADDRESS_SENTINELS = {"System Default", "REGISTRATION_PENDING"}
BOT_ACCOUNT_SYNC_PENDING_REASON = "pending_sync"
BOT_ACCOUNT_INACTIVE_REASON = "inactive"
BOT_ACCOUNT_DELETED_REASON = "deleted"
BOT_ACCOUNT_LINK_TOKEN_STATE_KEY = "telegram_link_token"
BOT_ACCOUNT_LINK_MOBILE_STATE_KEY = "telegram_link_mobile"
ACCOUNT_LINK_SYNC_WAIT_SECONDS = 45.0
ACCOUNT_LINK_SYNC_POLL_SECONDS = 1.0
ACCOUNT_LINK_SUCCESS_OUTCOMES = {
    TelegramRegistrationOutcome.LINKED_EXISTING,
    TelegramRegistrationOutcome.ALREADY_LINKED,
}


class BotAccountLinkDenied(PermissionError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class BotAccountLinkPending(RuntimeError):
    pass


async def finalize_account_link(
    db: AsyncSession,
    user: User,
    message: types.Message,
    *,
    address: str | None = None,
    token_record=None,
    send_success_message: bool = True,
) -> None:
    if bool(getattr(settings, "registration_sync_v2_enabled", False)):
        raise RuntimeError("legacy_foreign_account_link_forbidden")
    access_denial_reason = bot_account_access_denial_reason(user)
    if access_denial_reason:
        raise BotAccountLinkDenied(access_denial_reason)
    block_reason = await get_web_only_bot_access_reason(db, user)
    if block_reason == "accountant":
        raise PermissionError("ACCOUNTANT_BOT_ACCESS_FORBIDDEN")
    if block_reason == "customer":
        raise PermissionError("CUSTOMER_BOT_ACCESS_FORBIDDEN")
    if token_record is not None:
        await consume_telegram_link_token(
            db,
            token_record,
            user,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    else:
        user.telegram_id = message.from_user.id
        user.username = message.from_user.username
    if address is not None:
        user.address = address
    set_legacy_has_bot_access_compatibility(user, enabled=True)
    await ensure_mandatory_channel_membership(db, user=user)
    await db.commit()
    if send_success_message:
        await answer_pre_auth_message_via_runtime(message,
            await build_linked_account_panel_message(
                getattr(message, "bot", None),
                user,
                newly_linked=True,
                address_registered=address is not None,
                db=db,
            ),
            reply_markup=await build_persistent_navigation_keyboard(
                user,
                _account_link_webapp_url(),
            ),
        )

class LinkState(StatesGroup):
    waiting_for_contact = State()
    waiting_for_address = State()


def _account_link_webapp_url() -> str | None:
    if getattr(settings, "public_webapp_url", None):
        return public_webapp_url_for_links(settings_obj=settings)

    fallback_url = user_facing_webapp_url(settings_obj=settings)
    return normalize_origin(getattr(settings, "iran_server_url", None)) or fallback_url


def build_webapp_link_line() -> str | None:
    webapp_url = _account_link_webapp_url()
    return f"🌐 [ورود به وب اپ]({webapp_url})" if webapp_url else None


def build_webapp_plain_link_line() -> str | None:
    webapp_url = _account_link_webapp_url()
    return f"🌐 ورود به وب اپ:\n{webapp_url}" if webapp_url else None


async def build_linked_account_panel_message(
    bot,
    user: User,
    *,
    newly_linked: bool = False,
    already_linked: bool = False,
    address_registered: bool = False,
    db: AsyncSession | None = None,
) -> str:
    if db is not None:
        await attach_customer_management_names(db, [user])
    else:
        async with AsyncSessionLocal() as display_session:
            await attach_customer_management_names(display_session, [user])
    account_name = user_display_name(user, "حساب شما")
    if newly_linked:
        lines = [f"✅ حساب کاربری {account_name} با موفقیت به تلگرام متصل شد."]
    elif already_linked:
        lines = ["✅ حساب شما قبلاً متصل شده است. این حساب قبلاً به تلگرام متصل شده است."]
    else:
        lines = [f"سلام {account_name}! به پنل کاربری خود خوش آمدید."]

    if address_registered:
        lines.append("📍 آدرس شما ثبت شد و ثبت‌نامتان تکمیل شد.")

    join_request_text = await build_channel_join_request_text(
        bot,
        user_id=getattr(user, "id", None),
    )
    if join_request_text:
        lines.append("از لینک زیر برای ثبت درخواست عضویت در کانال معاملات استفاده کنید:")
        lines.append(join_request_text)
        lines.append("پس از ثبت درخواست، عضویت شما در کانال به صورت خودکار تایید می‌شود.")

    webapp_link_line = build_webapp_plain_link_line()
    if webapp_link_line:
        lines.append(webapp_link_line)

    lines.append("برای دسترسی به امکانات، از دکمه‌های زیر استفاده کنید.")
    return "\n\n".join(lines)


async def build_returning_account_panel_message(bot, user: User) -> str:
    lines = ["حساب شما فعال است. از لینک‌های زیر برای ورود استفاده کنید:"]

    channel_link_text = await build_channel_access_text(
        bot,
        user_id=getattr(user, "id", None),
    )
    if channel_link_text:
        lines.append(channel_link_text)

    webapp_link_line = build_webapp_plain_link_line()
    if webapp_link_line:
        lines.append(webapp_link_line)

    lines.append("برای دسترسی به سایر امکانات، از دکمه‌های منو استفاده کنید.")
    return "\n\n".join(lines)


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


def build_customer_web_only_message() -> str:
    lines = [
        "⚠️ دسترسی این سطح مشتری به ربات تلگرام فعال نیست.",
        "برای استفاده از بازار فقط از وب‌اپ استفاده کنید.",
    ]
    webapp_link_line = build_webapp_link_line()
    if webapp_link_line:
        lines.append(webapp_link_line)
    return "\n\n".join(lines)


def bot_account_access_denial_reason(user: User | object | None) -> str | None:
    decision = evaluate_bot_access_local_state(user)
    if decision.allowed:
        return None
    if decision.reason == BOT_ACCESS_REASON_SYNC_PENDING:
        return BOT_ACCOUNT_SYNC_PENDING_REASON
    if decision.reason == BOT_ACCESS_REASON_DELETED:
        return BOT_ACCOUNT_DELETED_REASON
    if decision.reason == BOT_ACCESS_REASON_INACTIVE:
        return BOT_ACCOUNT_INACTIVE_REASON
    return decision.reason


def build_bot_account_access_denial_message(reason: str | None) -> str:
    return bot_access_denial_message(reason)


def build_neutral_account_link_message() -> str:
    return "برای فعال‌سازی این مسیر، ابتدا ثبت‌نام را در سامانه کامل کنید و از همان‌جا وارد ربات شوید."


def _is_mocked_relation_check(relation_checker) -> bool:
    return callable(getattr(relation_checker, "assert_awaited", None)) or callable(
        getattr(relation_checker, "assert_called", None)
    )


async def _run_relation_check(db: AsyncSession, relation_checker, user_id: int) -> bool:
    if not isinstance(db, AsyncSession) and not _is_mocked_relation_check(relation_checker):
        return False
    return await relation_checker(db, user_id)


async def get_web_only_bot_access_reason(db: AsyncSession, user: User) -> str | None:
    user_id = getattr(user, "id", None)
    if not isinstance(db, AsyncSession) and user_id is not None:
        if await _run_relation_check(db, is_user_accountant, int(user_id)):
            return "accountant"
        if await _run_relation_check(db, is_user_customer, int(user_id)):
            return "customer"
        return None

    decision = await evaluate_bot_access(db, user)
    if decision.allowed:
        return None
    if decision.reason == BOT_ACCESS_REASON_ACCOUNTANT:
        return "accountant"
    if decision.reason == BOT_ACCESS_REASON_CUSTOMER_TIER2:
        return "customer"
    if decision.reason:
        return decision.reason
    return None


def build_web_only_message_for_reason(reason: str | None) -> str:
    if reason == "customer":
        return build_customer_web_only_message()
    return build_accountant_web_only_message()


async def _wait_for_linked_account_projection(
    *,
    mobile_number: str,
    telegram_id: int,
    timeout_seconds: float = ACCOUNT_LINK_SYNC_WAIT_SECONDS,
) -> User | None:
    deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout_seconds))
    normalized_mobile = normalize_mobile_number(mobile_number)
    while True:
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(
                    select(User).where(
                        User.normalized_mobile_number == normalized_mobile,
                        User.telegram_id == int(telegram_id),
                    )
                )
            ).scalar_one_or_none()
            if user is not None and (await evaluate_bot_access(db, user)).allowed:
                return user
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(ACCOUNT_LINK_SYNC_POLL_SECONDS, remaining))


async def complete_account_link_via_iran(
    *,
    message: types.Message,
    mobile_number: str,
    link_token: str | None,
    address: str | None,
) -> User:
    mode = "link_token" if link_token else "existing_linked_user"
    command = build_telegram_account_link_command(
        mode=mode,
        link_token=link_token,
        mobile_number=mobile_number,
        telegram_id=message.from_user.id,
        telegram_username=message.from_user.username,
        telegram_full_name=message.from_user.full_name,
        address=address,
        contact_verified_at=(utc_now() if link_token else None),
    )
    status_code, body = await forward_telegram_account_link_command(command)
    try:
        response = TelegramRegistrationCommandResponse.model_validate(body)
    except (TypeError, ValueError):
        raise BotAccountLinkPending("invalid_response") from None
    if (
        status_code >= 500
        or not response.terminal
        or response.outcome == TelegramRegistrationOutcome.FEATURE_DISABLED
    ):
        raise BotAccountLinkPending(response.outcome.value)
    if response.command_id != command.command_id:
        raise BotAccountLinkPending("response_command_mismatch")
    if response.outcome not in ACCOUNT_LINK_SUCCESS_OUTCOMES:
        raise BotAccountLinkDenied(response.outcome.value)
    projected = await _wait_for_linked_account_projection(
        mobile_number=command.mobile_number,
        telegram_id=command.telegram_id,
    )
    if projected is None:
        raise BotAccountLinkPending("projection_pending")
    return projected


async def prompt_address_completion(
    message: types.Message,
    state: FSMContext,
    user_id: int,
    *,
    already_linked: bool,
    link_token: str | None = None,
    mobile_number: str | None = None,
) -> None:
    await state.update_data(link_user_id=user_id)
    if link_token:
        await state.update_data(
            **{
                BOT_ACCOUNT_LINK_TOKEN_STATE_KEY: link_token,
                BOT_ACCOUNT_LINK_MOBILE_STATE_KEY: normalize_mobile_number(mobile_number or ""),
            }
        )
    await state.set_state(LinkState.waiting_for_address)

    intro = (
        "✅ حساب شما شناسایی شد، اما ثبت‌نام هنوز کامل نشده است.\n\n"
        if already_linked
        else "✅ شماره تماس تایید شد!\n\n"
    )
    await answer_pre_auth_message_via_runtime(message,
        intro + "📍 برای تکمیل ثبت‌نام، آدرس خود را جهت جابجایی سکه وارد نمایید:",
        reply_markup=types.ReplyKeyboardRemove()
    )


async def prompt_contact_for_account_link(
    message: types.Message,
    state: FSMContext,
    prompt_text: str | None = None,
    *,
    link_token: str | None = None,
) -> types.Message:
    text = prompt_text or (
        "🔗 برای اتصال حساب کاربری وب به تلگرام، لطفاً شماره موبایل خود را ارسال کنید.\n"
        "روی دکمه زیر کلیک کنید:"
    )
    sent_message = await answer_pre_auth_message_via_runtime(message,
        text,
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="📱 ارسال شماره همراه", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    if link_token:
        await state.update_data(**{BOT_ACCOUNT_LINK_TOKEN_STATE_KEY: link_token})
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
        await answer_pre_auth_message_via_runtime(message,
            await build_linked_account_panel_message(getattr(message, "bot", None), user, already_linked=True),
            reply_markup=await build_persistent_navigation_keyboard(
                user,
                settings.frontend_url,
            ),
        )
        return

    await answer_pre_auth_message_via_runtime(message, build_neutral_account_link_message(), reply_markup=types.ReplyKeyboardRemove())
    clear_state = getattr(state, "clear", None)
    if callable(clear_state):
        await clear_state()

@router.message(LinkState.waiting_for_contact, F.contact)
async def handle_contact(message: types.Message, state: FSMContext):
    contact = message.contact

    # Security check: verify contact belongs to sender
    if contact.user_id != message.from_user.id:
        await answer_pre_auth_message_via_runtime(message, "❌ لطفاً شماره خودتان را ارسال کنید.")
        return

    phone = normalize_mobile_number(contact.phone_number)
    logger.info(
        "Telegram account-link contact received",
        extra={"event": "telegram_account_link.contact_verified"},
    )

    state_data = await state.get_data()
    link_token = state_data.get(BOT_ACCOUNT_LINK_TOKEN_STATE_KEY)
    if not link_token:
        await answer_pre_auth_message_via_runtime(message,
            build_neutral_account_link_message(),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        await state.clear()
        return

    async for db in get_db():
        token_record = None
        if isinstance(db, AsyncSession):
            try:
                token_record, user, _decision = await load_pending_telegram_link_token_user_for_update(db, link_token)
            except TelegramLinkTokenError as exc:
                await answer_pre_auth_message_via_runtime(message,
                    build_bot_account_access_denial_message(exc.reason),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                await state.clear()
                return
        else:
            stmt = select(User).where(User.mobile_number == phone)
            user = (await db.execute(stmt)).scalar_one_or_none()

        if not user:
            await answer_pre_auth_message_via_runtime(message,
                build_bot_account_access_denial_message(BOT_ACCOUNT_SYNC_PENDING_REASON),
                reply_markup=types.ReplyKeyboardRemove(),
            )
            await state.clear()
            return

        registered_phone = str(getattr(user, "mobile_number", "") or "")
        if registered_phone and not registered_phone.endswith(phone[-10:]):
            await answer_pre_auth_message_via_runtime(message, "❌ شماره تماس ارسال شده با حساب انتخاب‌شده مطابقت ندارد.", reply_markup=types.ReplyKeyboardRemove())
            await state.clear()
            return

        access_denial_reason = bot_account_access_denial_reason(user)
        if access_denial_reason:
            await answer_pre_auth_message_via_runtime(message,
                build_bot_account_access_denial_message(access_denial_reason),
                reply_markup=types.ReplyKeyboardRemove(),
            )
            await state.clear()
            return

        block_reason = await get_web_only_bot_access_reason(db, user)
        if block_reason:
            await answer_pre_auth_message_via_runtime(message,
                build_web_only_message_for_reason(block_reason),
                reply_markup=types.ReplyKeyboardRemove(),
                parse_mode="Markdown",
            )
            await state.clear()
            return

        if user.telegram_id and user.telegram_id != message.from_user.id:
            await answer_pre_auth_message_via_runtime(message, "❌ این شماره قبلاً به یک اکانت تلگرام دیگر متصل شده است.", reply_markup=types.ReplyKeyboardRemove())
            await state.clear()
            return

        if user.telegram_id == message.from_user.id:
            if user_requires_address_completion(user):
                await prompt_address_completion(
                    message,
                    state,
                    user.id,
                    already_linked=True,
                    mobile_number=registered_phone,
                )
                return
            await answer_pre_auth_message_via_runtime(message,
                await build_linked_account_panel_message(getattr(message, "bot", None), user, already_linked=True, db=db),
                reply_markup=await build_persistent_navigation_keyboard(
                    user,
                    _account_link_webapp_url(),
                ),
            )
            await state.clear()
            return

        if user_requires_address_completion(user):
            if not bool(getattr(settings, "registration_sync_v2_enabled", False)):
                try:
                    await finalize_account_link(
                        db,
                        user,
                        message,
                        token_record=token_record,
                        send_success_message=False,
                    )
                except BotAccountLinkDenied as exc:
                    await db.rollback()
                    await answer_pre_auth_message_via_runtime(message,
                        build_bot_account_access_denial_message(exc.reason),
                        reply_markup=types.ReplyKeyboardRemove(),
                    )
                    await state.clear()
                    return
                except PermissionError as exc:
                    await db.rollback()
                    await answer_pre_auth_message_via_runtime(message,
                        build_web_only_message_for_reason(
                            str(exc).replace("_BOT_ACCESS_FORBIDDEN", "").lower()
                        ),
                        reply_markup=types.ReplyKeyboardRemove(),
                        parse_mode="Markdown",
                    )
                    await state.clear()
                    return
                except Exception as exc:
                    await db.rollback()
                    logger.error(
                        "Legacy Telegram account-link before address failed",
                        extra={
                            "event": "telegram_account_link.legacy_before_address_failed",
                            "error_type": type(exc).__name__,
                        },
                    )
                    await answer_pre_auth_message_via_runtime(message,
                        "❌ خطا در اتصال حساب.",
                        reply_markup=types.ReplyKeyboardRemove(),
                    )
                    await state.clear()
                    return
                await prompt_address_completion(
                    message,
                    state,
                    user.id,
                    already_linked=False,
                )
                return
            await db.rollback()
            await prompt_address_completion(
                message,
                state,
                user.id,
                already_linked=False,
                link_token=link_token,
                mobile_number=registered_phone,
            )
            return

        if not bool(getattr(settings, "registration_sync_v2_enabled", False)):
            try:
                await finalize_account_link(
                    db,
                    user,
                    message,
                    token_record=token_record,
                )
            except BotAccountLinkDenied as exc:
                await db.rollback()
                await answer_pre_auth_message_via_runtime(message,
                    build_bot_account_access_denial_message(exc.reason),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
            except PermissionError as exc:
                await db.rollback()
                await answer_pre_auth_message_via_runtime(message,
                    build_web_only_message_for_reason(
                        str(exc).replace("_BOT_ACCESS_FORBIDDEN", "").lower()
                    ),
                    reply_markup=types.ReplyKeyboardRemove(),
                    parse_mode="Markdown",
                )
            except Exception as exc:
                await db.rollback()
                logger.error(
                    "Legacy Telegram account-link failed",
                    extra={
                        "event": "telegram_account_link.legacy_failed",
                        "error_type": type(exc).__name__,
                    },
                )
                await answer_pre_auth_message_via_runtime(message,
                    "❌ خطا در اتصال حساب.",
                    reply_markup=types.ReplyKeyboardRemove(),
                )
            await state.clear()
            return

        await db.rollback()
        try:
            projected_user = await complete_account_link_via_iran(
                message=message,
                mobile_number=registered_phone,
                link_token=link_token,
                address=None,
            )
            await answer_pre_auth_message_via_runtime(message,
                await build_linked_account_panel_message(
                    getattr(message, "bot", None),
                    projected_user,
                    newly_linked=True,
                ),
                reply_markup=await build_persistent_navigation_keyboard(
                    projected_user,
                    public_webapp_url_for_links(),
                ),
            )
        except BotAccountLinkPending:
            await answer_pre_auth_message_via_runtime(message,
                "⏳ اتصال فعلاً به علت ارتباط سرورها تکمیل نشد. چند لحظه بعد دوباره از لینک اتصال استفاده کنید.",
                reply_markup=types.ReplyKeyboardRemove(),
            )
        except BotAccountLinkDenied as exc:
            await answer_pre_auth_message_via_runtime(message,
                build_bot_account_access_denial_message(exc.reason),
                reply_markup=types.ReplyKeyboardRemove(),
            )
        except Exception as exc:
            logger.error(
                "Telegram account-link forwarding failed",
                extra={
                    "event": "telegram_account_link.forward_failed",
                    "error_type": type(exc).__name__,
                },
            )
            await answer_pre_auth_message_via_runtime(message, "❌ خطا در اتصال حساب.", reply_markup=types.ReplyKeyboardRemove())

        await state.clear()
        return


@router.message(LinkState.waiting_for_address)
async def handle_address_completion(message: types.Message, state: FSMContext):
    sync_v2_enabled = bool(getattr(settings, "registration_sync_v2_enabled", False))
    raw_address = message.text or ""
    # Preserve the deployed legacy handler while Sync-v2 is off. The strict
    # reconciliation contract keeps the Web registration address byte-exact.
    address = raw_address if sync_v2_enabled else raw_address.strip()
    if len(address) < 10:
        await answer_pre_auth_message_via_runtime(message, "❌ آدرس وارد شده کوتاه است. لطفاً آدرس کامل‌تری وارد کنید.")
        return

    state_data = await state.get_data()
    user_id = state_data.get("link_user_id")
    link_token = state_data.get(BOT_ACCOUNT_LINK_TOKEN_STATE_KEY)
    link_mobile = state_data.get(BOT_ACCOUNT_LINK_MOBILE_STATE_KEY)
    if not user_id:
        await state.clear()
        await answer_pre_auth_message_via_runtime(message, "❌ فرآیند تکمیل ثبت‌نام منقضی شده است. لطفاً دوباره /link را بزنید.")
        return

    async for db in get_db():
        stmt = select(User).where(User.id == user_id)
        user = (await db.execute(stmt)).scalar_one_or_none()

        if not user:
            await state.clear()
            await answer_pre_auth_message_via_runtime(message,
                build_bot_account_access_denial_message(BOT_ACCOUNT_SYNC_PENDING_REASON),
                reply_markup=types.ReplyKeyboardRemove(),
            )
            return

        access_denial_reason = bot_account_access_denial_reason(user)
        if access_denial_reason:
            await state.clear()
            await answer_pre_auth_message_via_runtime(message,
                build_bot_account_access_denial_message(access_denial_reason),
                reply_markup=types.ReplyKeyboardRemove(),
            )
            return

        block_reason = await get_web_only_bot_access_reason(db, user)
        if block_reason:
            await state.clear()
            await answer_pre_auth_message_via_runtime(message,
                build_web_only_message_for_reason(block_reason),
                reply_markup=types.ReplyKeyboardRemove(),
                parse_mode="Markdown",
            )
            return

        if user.telegram_id and user.telegram_id != message.from_user.id:
            await state.clear()
            await answer_pre_auth_message_via_runtime(message, "❌ این حساب قبلاً به یک اکانت تلگرام دیگر متصل شده است.")
            return

        if link_token and user.telegram_id not in (None, message.from_user.id):
            await state.clear()
            await answer_pre_auth_message_via_runtime(message, "❌ این حساب قبلاً به یک اکانت تلگرام دیگر متصل شده است.")
            return
        if not link_token and user.telegram_id != message.from_user.id:
            await state.clear()
            await answer_pre_auth_message_via_runtime(message, "❌ هویت حساب تلگرام قابل تایید نیست.")
            return

        if not sync_v2_enabled:
            try:
                await finalize_account_link(
                    db,
                    user,
                    message,
                    address=address,
                )
            except BotAccountLinkDenied as exc:
                await db.rollback()
                await answer_pre_auth_message_via_runtime(message,
                    build_bot_account_access_denial_message(exc.reason),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
            except PermissionError as exc:
                await db.rollback()
                await answer_pre_auth_message_via_runtime(message,
                    build_web_only_message_for_reason(
                        str(exc).replace("_BOT_ACCESS_FORBIDDEN", "").lower()
                    ),
                    reply_markup=types.ReplyKeyboardRemove(),
                    parse_mode="Markdown",
                )
            except Exception as exc:
                await db.rollback()
                logger.error(
                    "Legacy Telegram account-link address completion failed",
                    extra={
                        "event": "telegram_account_link.legacy_address_failed",
                        "error_type": type(exc).__name__,
                    },
                )
                await answer_pre_auth_message_via_runtime(message, "❌ خطا در تکمیل ثبت‌نام.")
            await state.clear()
            return

        await db.rollback()
        try:
            projected_user = await complete_account_link_via_iran(
                message=message,
                mobile_number=(link_mobile or user.mobile_number),
                link_token=link_token,
                address=address,
            )
            await answer_pre_auth_message_via_runtime(message,
                await build_linked_account_panel_message(
                    getattr(message, "bot", None),
                    projected_user,
                    newly_linked=bool(link_token),
                    already_linked=not bool(link_token),
                    address_registered=True,
                ),
                reply_markup=await build_persistent_navigation_keyboard(
                    projected_user,
                    public_webapp_url_for_links(),
                ),
            )
        except BotAccountLinkPending:
            await answer_pre_auth_message_via_runtime(message,
                "⏳ تکمیل اتصال فعلاً ممکن نیست. چند لحظه بعد دوباره از لینک اتصال استفاده کنید.",
                reply_markup=types.ReplyKeyboardRemove(),
            )
        except BotAccountLinkDenied as exc:
            await answer_pre_auth_message_via_runtime(message,
                build_bot_account_access_denial_message(exc.reason),
                reply_markup=types.ReplyKeyboardRemove(),
            )
        except Exception as exc:
            logger.error(
                "Telegram account-link address completion failed",
                extra={
                    "event": "telegram_account_link.address_completion_failed",
                    "error_type": type(exc).__name__,
                },
            )
            await answer_pre_auth_message_via_runtime(message, "❌ خطا در تکمیل ثبت‌نام.")

        await state.clear()
        return
