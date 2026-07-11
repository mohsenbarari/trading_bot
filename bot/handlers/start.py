# trading_bot/bot/handlers/start.py
"""هندلرهای شروع و ثبت‌نام"""

from aiogram import Router, types, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from typing import Optional
from datetime import datetime, timezone
from dataclasses import dataclass
from uuid import UUID
import asyncio
import logging
import math
import re

from core.db import AsyncSessionLocal
from core.config import settings
from core.audit_logger import audit_log
from core.public_webapp_url import public_webapp_url_for_links
from core.registration_contracts import (
    REGISTRATION_ADDRESS_MIN_LENGTH,
    REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE,
    normalize_registration_mobile_number,
)
from core.services.bot_access_policy import (
    BOT_ACCESS_REASON_SYNC_PENDING,
    evaluate_bot_access,
    evaluate_invitation_bot_access,
)
from core.services.invitation_lifecycle_service import derive_invitation_state
from core.services.telegram_registration_intent_service import (
    TERMINAL_INTENT_STATUSES,
    TelegramRegistrationIntentError,
    create_or_reuse_ready_registration_intent,
    get_registration_intent_for_invitation,
    registration_activation_block_for_user,
)
from core.services.telegram_link_token_service import (
    TelegramLinkTokenError,
    load_pending_telegram_link_token_user_for_update,
)
from core.services.accountant_relation_service import (
    get_pending_accountant_relation_by_invitation_token,
    is_accountant_invitation_token,
)
from core.services.customer_relation_service import (
    get_customer_relation_by_invitation_token,
    get_pending_customer_relation_by_invitation_token,
    is_customer_invitation_token,
)
from core.utils import utc_now
from models.customer_relation import CustomerRelationStatus, CustomerTier
from models.invitation import Invitation, InvitationCompletionSurface, InvitationKind
from models.telegram_registration_intent import (
    TelegramRegistrationIntent,
    TelegramRegistrationIntentStatus,
)
from models.user import User
from bot.onboarding import (
    BOT_ONBOARDING_REQUIRED_STEP,
    CUSTOMER_TUTORIAL_ACK_CALLBACK,
    CUSTOMER_TUTORIAL_STEP,
    OFFER_TUTORIAL_ACK_CALLBACK,
    OFFER_TUTORIAL_STEP,
    build_onboarding_keyboard,
    onboarding_text_for_step,
)
from bot.states import Registration
from bot.keyboards import get_persistent_menu_keyboard
from bot.handlers.link_account import (
    BOT_ACCOUNT_INACTIVE_REASON,
    bot_account_access_denial_reason,
    build_bot_account_access_denial_message,
    build_linked_account_panel_message,
    build_neutral_account_link_message,
    prompt_contact_for_account_link,
)
from bot.utils.customer_display import attach_customer_management_names, user_display_name
from bot.utils.public_profile import (
    build_bot_public_profile_keyboard,
    build_bot_public_profile_text,
    load_bot_public_profile,
)
from bot.message_manager import (
    set_anchor, 
    delete_previous_anchor,
    DeleteDelay
)

logger = logging.getLogger(__name__)

router = Router()

LEGACY_RESPOND_PATH_DISABLED_MESSAGE = (
    "این مسیر قدیمی پاسخ به آفر دیگر فعال نیست. لطفاً از دکمه‌های خود آفر در کانال معاملات استفاده کنید."
)
TELEGRAM_LINK_TOKEN_SYNC_GRACE_SECONDS = 45.0
TELEGRAM_LINK_TOKEN_SYNC_POLL_SECONDS = 1.0
_TELEGRAM_LINK_TOKEN_RETRYABLE_REASONS = {"invalid", BOT_ACCESS_REASON_SYNC_PENDING}
_TELEGRAM_LINK_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{32,59}$")
REGISTRATION_CONFIRM_CALLBACK = "telegram_registration_confirm"
REGISTRATION_EDIT_ADDRESS_CALLBACK = "telegram_registration_edit_address"
REGISTRATION_HANDOFF_WAIT_SECONDS = 7.0
REGISTRATION_HANDOFF_POLL_SECONDS = 0.25
_REGISTRATION_STATE_TOKEN = "registration_invitation_token"
_REGISTRATION_STATE_MOBILE = "registration_mobile_number"
_REGISTRATION_STATE_EXPIRES_AT = "registration_invitation_expires_at"
_REGISTRATION_STATE_TELEGRAM_ID = "registration_telegram_id"
_REGISTRATION_STATE_CONTACT_VERIFIED_AT = "registration_contact_verified_at"
_REGISTRATION_STATE_ADDRESS = "registration_address"
_SUCCESS_INTENT_STATUSES = frozenset(
    {
        TelegramRegistrationIntentStatus.RECONCILED_CREATED,
        TelegramRegistrationIntentStatus.RECONCILED_LINKED_EXISTING,
        TelegramRegistrationIntentStatus.RECONCILED_ALREADY_LINKED,
    }
)


@dataclass(frozen=True, slots=True)
class RegistrationHandoffResolution:
    status: TelegramRegistrationIntentStatus
    user: User | None = None
    reason: str | None = None


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _utc_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_private_chat(message: types.Message | None) -> bool:
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    return str(getattr(chat_type, "value", chat_type) or "").lower() == "private"


async def _reject_non_private_registration(
    event: types.Message | types.CallbackQuery,
    state: FSMContext,
) -> bool:
    is_callback = isinstance(event, types.CallbackQuery) or (
        getattr(event, "message", None) is not None
        and getattr(event, "chat", None) is None
    )
    message = event.message if is_callback else event
    if _is_private_chat(message):
        return False
    await _clear_registration_owned_fsm(state)
    text = "برای حفظ امنیت اطلاعات، ثبت‌نام را فقط در گفت‌وگوی خصوصی بات انجام دهید."
    if is_callback:
        await event.answer(text, show_alert=True)
    elif getattr(event, "chat", None) is not None:
        await event.answer(text, reply_markup=types.ReplyKeyboardRemove())
    return True


def _customer_relation_allows_direct_registration(
    invitation: Invitation,
    relation: object | None,
) -> bool:
    if relation is None:
        return False
    if getattr(relation, "invitation_token", invitation.token) != invitation.token:
        return False
    if _enum_value(getattr(relation, "customer_tier", None)) != CustomerTier.TIER_1.value:
        return False
    if getattr(relation, "deleted_at", None) is not None:
        return False
    relation_status = _enum_value(getattr(relation, "status", None))
    invitation_state = _enum_value(derive_invitation_state(invitation))
    if invitation_state == "pending":
        relation_expiry = _utc_datetime(getattr(relation, "expires_at", None))
        return (
            relation_status == CustomerRelationStatus.PENDING.value
            and getattr(relation, "customer_user_id", None) is None
            and relation_expiry is not None
            and relation_expiry > utc_now()
        )
    return (
        invitation_state == "completed"
        and _enum_value(getattr(invitation, "completed_via", None))
        == InvitationCompletionSurface.WEB.value
        and relation_status == CustomerRelationStatus.ACTIVE.value
        and getattr(relation, "customer_user_id", None) is not None
        and getattr(invitation, "registered_user_id", None) is not None
        and int(getattr(relation, "customer_user_id"))
        == int(getattr(invitation, "registered_user_id"))
    )


def _direct_registration_enabled() -> bool:
    return bool(getattr(settings, "telegram_direct_registration_enabled", False))


def _direct_registration_runtime_ready() -> bool:
    from core.registration_feature_policy import direct_registration_runtime_ready

    return direct_registration_runtime_ready(settings)


async def _bound_registration_fsm_ttl(
    state: FSMContext,
    *,
    expires_at: datetime,
) -> None:
    expiry = _utc_datetime(expires_at)
    if expiry is None:
        raise RuntimeError("registration_fsm_expiry_invalid")
    ttl_seconds = math.ceil((expiry - utc_now()).total_seconds())
    if ttl_seconds <= 0:
        raise RuntimeError("registration_fsm_expired")

    storage = getattr(state, "storage", None)
    storage_key = getattr(state, "key", None)
    redis = getattr(storage, "redis", None)
    key_builder = getattr(storage, "key_builder", None)
    if redis is None or key_builder is None or storage_key is None:
        if isinstance(state, FSMContext):
            raise RuntimeError("registration_fsm_storage_unsupported")
        return
    state_key = key_builder.build(storage_key, "state")
    data_key = key_builder.build(storage_key, "data")
    state_expired, data_expired = await asyncio.gather(
        redis.expire(state_key, ttl_seconds),
        redis.expire(data_key, ttl_seconds),
    )
    if not state_expired or not data_expired:
        raise RuntimeError("registration_fsm_ttl_not_applied")


async def _write_registration_fsm(
    state: FSMContext,
    *,
    data: dict[str, object],
    next_state: object,
    expires_at: datetime,
) -> None:
    expiry = _utc_datetime(expires_at)
    if expiry is None:
        raise RuntimeError("registration_fsm_expiry_invalid")
    ttl_seconds = math.ceil((expiry - utc_now()).total_seconds())
    if ttl_seconds <= 0:
        raise RuntimeError("registration_fsm_expired")

    storage = getattr(state, "storage", None)
    storage_key = getattr(state, "key", None)
    redis = getattr(storage, "redis", None)
    key_builder = getattr(storage, "key_builder", None)
    json_dumps = getattr(storage, "json_dumps", None)
    if redis is not None and key_builder is not None and storage_key is not None and callable(json_dumps):
        state_key = key_builder.build(storage_key, "state")
        data_key = key_builder.build(storage_key, "data")
        state_value = getattr(next_state, "state", next_state)
        pipeline = redis.pipeline(transaction=True)
        pipeline.set(data_key, json_dumps(data), ex=ttl_seconds)
        pipeline.set(state_key, state_value, ex=ttl_seconds)
        results = await pipeline.execute()
        if len(results) != 2 or not all(results):
            raise RuntimeError("registration_fsm_atomic_write_failed")
        return
    if isinstance(state, FSMContext):
        raise RuntimeError("registration_fsm_storage_unsupported")

    await state.update_data(**data)
    await state.set_state(next_state)
    await _bound_registration_fsm_ttl(state, expires_at=expiry)


async def _read_registration_fsm(state: FSMContext) -> dict[str, object] | None:
    try:
        return await state.get_data()
    except Exception as exc:
        logger.warning(
            "Direct Telegram registration state read failed",
            extra={
                "event": "telegram_registration.fsm_read_failed",
                "error_type": type(exc).__name__,
            },
        )
        return None


async def _clear_registration_owned_fsm(state: FSMContext) -> None:
    """Clear only this flow's stale transient state; never cancel another bot workflow."""

    get_state = getattr(state, "get_state", None)
    if not callable(get_state):
        return
    try:
        current_state = await get_state()
    except Exception as exc:
        logger.warning(
            "Direct Telegram registration state probe failed",
            extra={
                "event": "telegram_registration.fsm_state_probe_failed",
                "error_type": type(exc).__name__,
            },
        )
        return
    registration_states = {
        Registration.awaiting_contact.state,
        Registration.awaiting_address.state,
        Registration.awaiting_confirmation.state,
    }
    if current_state in registration_states:
        try:
            await state.clear()
        except Exception as exc:
            logger.warning(
                "Could not clear stale direct Telegram registration state",
                extra={
                    "event": "telegram_registration.fsm_clear_failed",
                    "error_type": type(exc).__name__,
                },
            )


async def _claim_registration_handoff_message(
    state: FSMContext,
    *,
    intent_id: UUID,
) -> bool:
    storage = getattr(state, "storage", None)
    redis = getattr(storage, "redis", None)
    if redis is None:
        return not isinstance(state, FSMContext)
    try:
        return bool(
            await redis.set(
                f"telegram-registration:handoff:{intent_id}",
                "1",
                ex=120,
                nx=True,
            )
        )
    except Exception as exc:
        logger.warning(
            "Direct Telegram registration handoff claim failed",
            extra={
                "event": "telegram_registration.handoff_claim_failed",
                "error_type": type(exc).__name__,
            },
        )
        return False


def _registration_rejection_message(reason: str | None) -> str:
    code = str(reason or "").strip().lower()
    if code in {"invitation_expired", "expired"}:
        return "مهلت ثبت‌نام پایان یافته است. لطفاً دعوت‌نامه جدید دریافت کنید."
    if code == "invitation_revoked":
        return "این دعوت‌نامه دیگر معتبر نیست."
    if code == "contact_not_owned":
        return "شماره تماس باید مستقیماً از حساب تلگرام خودتان ارسال شود."
    if code == "contact_mobile_mismatch":
        return "شماره ارسال‌شده با شماره ثبت‌شده در دعوت‌نامه مطابقت ندارد."
    if code == "identity_conflict":
        return "امکان تکمیل ثبت‌نام با این دعوت‌نامه وجود ندارد. لطفاً با دعوت‌کننده تماس بگیرید."
    return "امکان تکمیل ثبت‌نام با این دعوت‌نامه وجود ندارد. لطفاً دعوت‌نامه جدید دریافت کنید."


def _registration_pending_message() -> str:
    return (
        "⏳ درخواست ثبت‌نام شما ذخیره شد، اما ثبت‌نام هنوز نهایی نشده است.\n\n"
        "پس از برقراری ارتباط و تکمیل همگام‌سازی، همین لینک دعوت یا دستور /start را دوباره باز کنید."
    )


async def _load_registration_handoff_resolution(
    *,
    intent_id: UUID,
    telegram_id: int,
) -> RegistrationHandoffResolution | None:
    async with AsyncSessionLocal() as session:
        intent = await session.get(TelegramRegistrationIntent, intent_id)
        if intent is None:
            return None
        status = TelegramRegistrationIntentStatus(_enum_value(intent.status))
        if status in _SUCCESS_INTENT_STATUSES:
            if intent.authoritative_user_id is None:
                return None
            projected_user_id = getattr(intent, "projected_user_id", None)
            if projected_user_id is None:
                return None
            user = await session.get(User, int(projected_user_id))
            if user is None or int(getattr(user, "telegram_id", 0) or 0) != int(telegram_id):
                return None
            if not (await evaluate_bot_access(session, user)).allowed:
                return None
            return RegistrationHandoffResolution(status=status, user=user)
        if status in TERMINAL_INTENT_STATUSES:
            return RegistrationHandoffResolution(
                status=status,
                reason=getattr(intent, "last_error_code", None),
            )
        return RegistrationHandoffResolution(status=status)


async def _wait_for_registration_handoff(
    *,
    intent_id: UUID,
    telegram_id: int,
    timeout_seconds: float = REGISTRATION_HANDOFF_WAIT_SECONDS,
) -> RegistrationHandoffResolution | None:
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout_seconds)
    while True:
        resolution = await _load_registration_handoff_resolution(
            intent_id=intent_id,
            telegram_id=telegram_id,
        )
        if resolution is not None and (
            resolution.user is not None
            or resolution.status in TERMINAL_INTENT_STATUSES
        ):
            return resolution
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return resolution
        await asyncio.sleep(min(REGISTRATION_HANDOFF_POLL_SECONDS, remaining))


async def _send_registration_handoff(
    message: types.Message,
    resolution: RegistrationHandoffResolution | None,
) -> None:
    if resolution is None or resolution.user is None:
        if resolution is not None and resolution.status in {
            TelegramRegistrationIntentStatus.REJECTED,
            TelegramRegistrationIntentStatus.EXPIRED,
        }:
            await message.answer(
                _registration_rejection_message(resolution.reason or resolution.status.value),
                reply_markup=types.ReplyKeyboardRemove(),
            )
            return
        await message.answer(
            _registration_pending_message(),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    user = resolution.user
    anchor_msg = await message.answer(
        await build_linked_account_panel_message(
            getattr(message, "bot", None),
            user,
            newly_linked=(
                resolution.status
                == TelegramRegistrationIntentStatus.RECONCILED_LINKED_EXISTING
            ),
            already_linked=(
                resolution.status
                == TelegramRegistrationIntentStatus.RECONCILED_ALREADY_LINKED
            ),
            address_registered=(
                resolution.status
                == TelegramRegistrationIntentStatus.RECONCILED_CREATED
            ),
        ),
        reply_markup=get_persistent_menu_keyboard(
            user.role,
            public_webapp_url_for_links(),
        ),
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


async def _begin_direct_registration(
    message: types.Message,
    state: FSMContext,
    *,
    session,
    invitation: Invitation,
) -> None:
    telegram_id = int(message.from_user.id)
    existing_intent = await get_registration_intent_for_invitation(
        session,
        invitation_token=invitation.token,
        telegram_id=telegram_id,
    )
    if existing_intent is not None:
        await _clear_registration_owned_fsm(state)
        resolution = await _load_registration_handoff_resolution(
            intent_id=existing_intent.id,
            telegram_id=telegram_id,
        )
        await _send_registration_handoff(message, resolution)
        return

    invitation_state = _enum_value(derive_invitation_state(invitation))
    if invitation_state not in {"pending", "completed"}:
        await message.answer(
            _registration_rejection_message(
                "invitation_revoked"
                if getattr(invitation, "revoked_at", None) is not None
                else "invitation_expired"
            ),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    expires_at = _utc_datetime(invitation.expires_at)
    if expires_at is None or expires_at <= utc_now():
        await message.answer(
            _registration_rejection_message("invitation_expired"),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    await state.clear()
    try:
        normalized_mobile = normalize_registration_mobile_number(
            invitation.mobile_number
        )
    except ValueError:
        await message.answer(
            _registration_rejection_message("identity_conflict"),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return
    try:
        await _write_registration_fsm(
            state,
            data={
                _REGISTRATION_STATE_TOKEN: invitation.token,
                _REGISTRATION_STATE_MOBILE: normalized_mobile,
                _REGISTRATION_STATE_EXPIRES_AT: expires_at.isoformat(),
                _REGISTRATION_STATE_TELEGRAM_ID: telegram_id,
            },
            next_state=Registration.awaiting_contact,
            expires_at=expires_at,
        )
    except Exception:
        await state.clear()
        raise
    anchor_msg = await message.answer(
        "✅ لینک دعوت معتبر است.\n\nبرای تایید هویت، شماره موبایل همین حساب تلگرام را از دکمه زیر ارسال کنید.",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="📱 ارسال شماره همراه", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


def _looks_like_webapp_link_token(raw_token: str) -> bool:
    return bool(_TELEGRAM_LINK_TOKEN_SHAPE.fullmatch((raw_token or "").strip()))


async def _rollback_link_token_probe(session) -> None:
    rollback = getattr(session, "rollback", None)
    if not callable(rollback):
        return
    try:
        await rollback()
    except Exception as exc:
        logger.debug("Telegram link token retry rollback failed: %s", exc)


async def load_pending_telegram_link_token_user_with_sync_grace(session, raw_token: str):
    """Wait briefly for Iran-issued link tokens to arrive on the foreign bot DB."""
    should_wait_for_sync = _looks_like_webapp_link_token(raw_token)
    deadline = asyncio.get_running_loop().time() + TELEGRAM_LINK_TOKEN_SYNC_GRACE_SECONDS
    attempts = 0

    while True:
        attempts += 1
        try:
            result = await load_pending_telegram_link_token_user_for_update(session, raw_token)
            if attempts > 1:
                logger.info(
                    "Telegram link token became available after sync grace.",
                    extra={"attempts": attempts},
                )
            return result
        except TelegramLinkTokenError as exc:
            if exc.reason not in _TELEGRAM_LINK_TOKEN_RETRYABLE_REASONS or not should_wait_for_sync:
                raise

            now = asyncio.get_running_loop().time()
            if now >= deadline:
                logger.info(
                    "Telegram link token sync grace exhausted.",
                    extra={"attempts": attempts, "reason": exc.reason},
                )
                raise

            await _rollback_link_token_probe(session)
            await asyncio.sleep(min(TELEGRAM_LINK_TOKEN_SYNC_POLL_SECONDS, max(0.0, deadline - now)))


def build_webapp_link_line() -> str | None:
    return f"🌐 [ورود به وب اپ]({public_webapp_url_for_links()})"


def build_register_link_line(token: str) -> str | None:
    return f"🌐 [تکمیل ثبت‌نام در وب اپ]({public_webapp_url_for_links()}/register?token={token})"


def build_accountant_register_link_line(token: str) -> str | None:
    return f"🌐 [تکمیل ثبت‌نام حسابدار در وب اپ]({public_webapp_url_for_links()}/register?token={token})"


def build_customer_register_link_line(token: str) -> str | None:
    return f"🌐 [تکمیل ثبت‌نام مشتری در وب اپ]({public_webapp_url_for_links()}/register?token={token})"


@router.message(CommandStart(deep_link=True))
async def handle_start_with_token(message: types.Message, command: CommandObject, state: FSMContext, user: Optional[User]):
    
    token = command.args

    if token and token.startswith("link_"):
        await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
        if user:
            anchor_msg = await message.answer(
                await build_linked_account_panel_message(message.bot, user),
                reply_markup=get_persistent_menu_keyboard(user.role, settings.frontend_url),
            )
            set_anchor(message.chat.id, anchor_msg.message_id)
            return

        raw_link_token = token.replace("link_", "", 1).strip()
        async with AsyncSessionLocal() as session:
            try:
                await load_pending_telegram_link_token_user_with_sync_grace(session, raw_link_token)
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
                profile = await load_bot_public_profile(session, viewer=user, target_user_id=target_user_id)
                if profile is None:
                    await message.answer("❌ پروفایل در دسترس نیست.")
                    return

                await delete_previous_anchor(message.bot, message.chat.id, delay=0)
                anchor_msg = await message.answer(
                    build_bot_public_profile_text(profile),
                    reply_markup=build_bot_public_profile_keyboard(profile),
                )
                set_anchor(message.chat.id, anchor_msg.message_id)
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

        await message.answer(LEGACY_RESPOND_PATH_DISABLED_MESSAGE)
        return
    
    # --- حذف پیام و لنگر برای سایر حالات ---
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)

    if _direct_registration_runtime_ready() and await _reject_non_private_registration(message, state):
        return
    
    # --- کاربر قبلاً ثبت‌نام کرده ---
    if user:
        if _direct_registration_runtime_ready():
            async with AsyncSessionLocal() as session:
                activation_block = await registration_activation_block_for_user(
                    session,
                    user=user,
                )
                decision = await evaluate_bot_access(session, user)
            if activation_block is not None:
                await message.answer(
                    _registration_pending_message(),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                return
            if not decision.allowed:
                await message.answer(
                    build_bot_account_access_denial_message(decision.reason),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                return
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
        direct_runtime_ready = _direct_registration_runtime_ready()
        if (
            not invitation
            or (invitation.is_used and not direct_runtime_ready)
            or getattr(invitation, "revoked_at", None) is not None
            or str(getattr(getattr(invitation, "kind", None), "value", getattr(invitation, "kind", ""))) == "legacy_unknown"
        ):
            bot_response = await message.answer("لینک دعوت شما نامعتبر یا منقضی شده است.", reply_markup=types.ReplyKeyboardRemove())
            return

        audit_log(
            "invitation.opened",
            target_type="invitation",
            target_id=getattr(invitation, "id", None),
            extra={"surface": "telegram"},
        )

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
            relation = (
                await get_customer_relation_by_invitation_token(session, token)
                if direct_runtime_ready
                else await get_pending_customer_relation_by_invitation_token(session, token)
            )
            if not relation:
                await message.answer("لینک دعوت شما نامعتبر یا منقضی شده است.", reply_markup=types.ReplyKeyboardRemove())
                return

            if direct_runtime_ready:
                decision = evaluate_invitation_bot_access(
                    role=invitation.role,
                    invitation_kind=invitation.kind,
                    customer_tier=relation.customer_tier,
                )
                if decision.allowed and _customer_relation_allows_direct_registration(
                    invitation,
                    relation,
                ):
                    try:
                        await _begin_direct_registration(
                            message,
                            state,
                            session=session,
                            invitation=invitation,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Direct Telegram registration entry failed",
                            extra={
                                "event": "telegram_registration.entry_failed",
                                "error_type": type(exc).__name__,
                            },
                        )
                        await state.clear()
                        await message.answer(
                            "ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید.",
                            reply_markup=types.ReplyKeyboardRemove(),
                        )
                    return

            customer_lines = [
                "✅ دعوت‌نامه مشتری معتبر است.",
                (
                    "ثبت‌نام مشتری از طریق وب‌اپ انجام می‌شود. اگر سطح دسترسی حساب شما مجاز باشد، "
                    "بعد از تکمیل ثبت‌نام می‌توانید اتصال تلگرام را از داخل وب‌اپ فعال کنید."
                ),
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

        if direct_runtime_ready:
            decision = evaluate_invitation_bot_access(
                role=invitation.role,
                invitation_kind=invitation.kind,
                customer_tier=None,
            )
            if decision.allowed:
                try:
                    await _begin_direct_registration(
                        message,
                        state,
                        session=session,
                        invitation=invitation,
                    )
                except Exception as exc:
                    logger.warning(
                        "Direct Telegram registration entry failed",
                        extra={
                            "event": "telegram_registration.entry_failed",
                            "error_type": type(exc).__name__,
                        },
                    )
                    await state.clear()
                    await message.answer(
                        "ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید.",
                        reply_markup=types.ReplyKeyboardRemove(),
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
        if _direct_registration_runtime_ready():
            async with AsyncSessionLocal() as session:
                activation_block = await registration_activation_block_for_user(
                    session,
                    user=user,
                )
                decision = await evaluate_bot_access(session, user)
            if activation_block is not None:
                anchor_msg = await message.answer(
                    _registration_pending_message(),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                set_anchor(message.chat.id, anchor_msg.message_id)
                return
            if not decision.allowed:
                anchor_msg = await message.answer(
                    build_bot_account_access_denial_message(decision.reason),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                set_anchor(message.chat.id, anchor_msg.message_id)
                return
        logger.warning(f"DEBUG: Building keyboard with URL: '{settings.frontend_url}'")
        
        anchor_msg = await message.answer(
            await build_linked_account_panel_message(message.bot, user),
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
    if await _reject_non_private_registration(message, state):
        return
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    state_data = await _read_registration_fsm(state)
    if state_data is None:
        await message.answer("ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید.")
        return
    if not _direct_registration_runtime_ready():
        token = state_data.get("token") or state_data.get(_REGISTRATION_STATE_TOKEN)
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
        return

    expires_at = _utc_datetime(state_data.get(_REGISTRATION_STATE_EXPIRES_AT))
    expected_mobile = state_data.get(_REGISTRATION_STATE_MOBILE)
    expected_telegram_id = state_data.get(_REGISTRATION_STATE_TELEGRAM_ID)
    if (
        expires_at is None
        or expires_at <= utc_now()
        or not expected_mobile
        or int(expected_telegram_id or 0) != int(message.from_user.id)
    ):
        await state.clear()
        await message.answer(
            _registration_rejection_message("invitation_expired"),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    contact = message.contact
    if getattr(contact, "user_id", None) != message.from_user.id:
        await message.answer(_registration_rejection_message("contact_not_owned"))
        return
    try:
        contact_mobile = normalize_registration_mobile_number(
            getattr(contact, "phone_number", None)
        )
    except ValueError:
        await message.answer(_registration_rejection_message("contact_mobile_mismatch"))
        return
    if contact_mobile != expected_mobile:
        await message.answer(_registration_rejection_message("contact_mobile_mismatch"))
        return

    try:
        state_data[_REGISTRATION_STATE_CONTACT_VERIFIED_AT] = utc_now().isoformat()
        await _write_registration_fsm(
            state,
            data=state_data,
            next_state=Registration.awaiting_address,
            expires_at=expires_at,
        )
    except Exception:
        await state.clear()
        await message.answer(
            "ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return
    anchor_msg = await message.answer(
        "✅ شماره تماس تایید شد!\n\n📍 برای تکمیل ثبت‌نام، آدرس خود را جهت جابجایی سکه وارد نمایید:",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


@router.message(Registration.awaiting_contact)
async def handle_contact_non_contact(message: types.Message, state: FSMContext):
    if await _reject_non_private_registration(message, state):
        return
    if not _direct_registration_runtime_ready():
        await handle_contact(message, state)
        return
    await message.answer(
        "شماره تماس باید مستقیماً از حساب تلگرام خودتان ارسال شود. از دکمه «ارسال شماره همراه» استفاده کنید."
    )

@router.message(Registration.awaiting_address)
async def handle_address(message: types.Message, state: FSMContext):
    if await _reject_non_private_registration(message, state):
        return
    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    state_data = await _read_registration_fsm(state)
    if state_data is None:
        await message.answer("ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید.")
        return
    if not _direct_registration_runtime_ready():
        token = state_data.get("token") or state_data.get(_REGISTRATION_STATE_TOKEN)
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
        return

    expires_at = _utc_datetime(state_data.get(_REGISTRATION_STATE_EXPIRES_AT))
    if (
        expires_at is None
        or expires_at <= utc_now()
        or int(state_data.get(_REGISTRATION_STATE_TELEGRAM_ID) or 0)
        != int(message.from_user.id)
        or not state_data.get(_REGISTRATION_STATE_CONTACT_VERIFIED_AT)
    ):
        await state.clear()
        await message.answer(
            _registration_rejection_message("invitation_expired"),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    address = message.text
    if not isinstance(address, str) or len(address) < REGISTRATION_ADDRESS_MIN_LENGTH:
        await message.answer(REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE)
        return

    try:
        state_data[_REGISTRATION_STATE_ADDRESS] = address
        await _write_registration_fsm(
            state,
            data=state_data,
            next_state=Registration.awaiting_confirmation,
            expires_at=expires_at,
        )
    except Exception:
        await state.clear()
        await message.answer(
            "ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return
    anchor_msg = await message.answer(
        f"آدرس واردشده:\n{address}\n\nآیا اطلاعات را تایید می‌کنید؟",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="تایید و تکمیل ثبت‌نام",
                        callback_data=REGISTRATION_CONFIRM_CALLBACK,
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="اصلاح آدرس",
                        callback_data=REGISTRATION_EDIT_ADDRESS_CALLBACK,
                    )
                ],
            ]
        ),
    )
    set_anchor(message.chat.id, anchor_msg.message_id)


@router.callback_query(
    StateFilter(Registration.awaiting_confirmation),
    F.data == REGISTRATION_EDIT_ADDRESS_CALLBACK,
)
async def handle_registration_edit_address(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    if await _reject_non_private_registration(callback, state):
        return
    state_data = await _read_registration_fsm(state)
    if state_data is None:
        await callback.answer("ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست.", show_alert=True)
        return
    expires_at = _utc_datetime(state_data.get(_REGISTRATION_STATE_EXPIRES_AT))
    if (
        not _direct_registration_runtime_ready()
        or expires_at is None
        or expires_at <= utc_now()
        or int(state_data.get(_REGISTRATION_STATE_TELEGRAM_ID) or 0)
        != int(callback.from_user.id)
    ):
        await state.clear()
        await callback.answer("فرآیند ثبت‌نام منقضی شده است.", show_alert=True)
        return
    try:
        await _write_registration_fsm(
            state,
            data=state_data,
            next_state=Registration.awaiting_address,
            expires_at=expires_at,
        )
    except Exception:
        await state.clear()
        await callback.answer(
            "ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست.",
            show_alert=True,
        )
        if callback.message:
            await callback.message.answer(
                "ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید.",
                reply_markup=types.ReplyKeyboardRemove(),
            )
        return
    await callback.answer("آدرس را دوباره وارد کنید.")
    if callback.message:
        await callback.message.answer(
            "📍 آدرس کامل خود را جهت جابجایی سکه وارد نمایید:",
            reply_markup=types.ReplyKeyboardRemove(),
        )


@router.callback_query(
    StateFilter(Registration.awaiting_confirmation),
    F.data == REGISTRATION_CONFIRM_CALLBACK,
)
async def handle_registration_confirm(
    callback: types.CallbackQuery,
    state: FSMContext,
):
    if await _reject_non_private_registration(callback, state):
        return
    if callback.message is None:
        await callback.answer("امکان تکمیل ثبت‌نام وجود ندارد.", show_alert=True)
        return
    state_data = await _read_registration_fsm(state)
    if state_data is None:
        await callback.answer("ثبت‌نام مستقیم تلگرام موقتاً در دسترس نیست.", show_alert=True)
        return
    expires_at = _utc_datetime(state_data.get(_REGISTRATION_STATE_EXPIRES_AT))
    token = state_data.get(_REGISTRATION_STATE_TOKEN)
    mobile_number = state_data.get(_REGISTRATION_STATE_MOBILE)
    address = state_data.get(_REGISTRATION_STATE_ADDRESS)
    contact_verified_at = _utc_datetime(
        state_data.get(_REGISTRATION_STATE_CONTACT_VERIFIED_AT)
    )
    telegram_id = int(state_data.get(_REGISTRATION_STATE_TELEGRAM_ID) or 0)
    if (
        not _direct_registration_runtime_ready()
        or expires_at is None
        or expires_at <= utc_now()
        or not token
        or not mobile_number
        or not isinstance(address, str)
        or len(address) < REGISTRATION_ADDRESS_MIN_LENGTH
        or contact_verified_at is None
        or telegram_id != int(callback.from_user.id)
    ):
        await state.clear()
        await callback.answer("فرآیند ثبت‌نام منقضی شده است.", show_alert=True)
        await callback.message.answer(
            _registration_rejection_message("invitation_expired"),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    intent_id = None
    try:
        async with AsyncSessionLocal() as session:
            invitation = (
                await session.execute(
                    select(Invitation).where(Invitation.token == token)
                )
            ).scalar_one_or_none()
            if (
                invitation is None
                or getattr(invitation, "revoked_at", None) is not None
                or _enum_value(getattr(invitation, "kind", None)) == InvitationKind.LEGACY_UNKNOWN.value
            ):
                await state.clear()
                await callback.answer("دعوت‌نامه معتبر نیست.", show_alert=True)
                await callback.message.answer(
                    _registration_rejection_message(
                        "invitation_revoked" if invitation is not None else "legacy_state_ambiguous"
                    ),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                return

            current_expiry = _utc_datetime(invitation.expires_at)
            if current_expiry is None or current_expiry <= utc_now():
                await state.clear()
                await callback.answer("مهلت ثبت‌نام پایان یافته است.", show_alert=True)
                await callback.message.answer(
                    _registration_rejection_message("invitation_expired"),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                return
            try:
                current_mobile = normalize_registration_mobile_number(
                    invitation.mobile_number
                )
            except ValueError:
                current_mobile = None
            if current_mobile != mobile_number:
                await state.clear()
                await callback.answer("هویت دعوت‌نامه قابل تایید نیست.", show_alert=True)
                await callback.message.answer(
                    _registration_rejection_message("identity_conflict"),
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                return

            customer_relation = None
            if _enum_value(invitation.kind) == InvitationKind.CUSTOMER.value:
                customer_relation = await get_customer_relation_by_invitation_token(
                    session,
                    token,
                )
                if not _customer_relation_allows_direct_registration(
                    invitation,
                    customer_relation,
                ):
                    await state.clear()
                    await callback.answer("دعوت‌نامه مشتری معتبر نیست.", show_alert=True)
                    await callback.message.answer(
                        _registration_rejection_message("invitation_revoked"),
                        reply_markup=types.ReplyKeyboardRemove(),
                    )
                    return
            decision = evaluate_invitation_bot_access(
                role=invitation.role,
                invitation_kind=invitation.kind,
                customer_tier=(
                    getattr(customer_relation, "customer_tier", None)
                    if customer_relation is not None
                    else None
                ),
            )
            if not decision.allowed:
                await state.clear()
                await callback.answer("این دعوت‌نامه فقط برای وب‌اپ معتبر است.", show_alert=True)
                await callback.message.answer(
                    "این نوع حساب فقط از طریق وب‌اپ ثبت‌نام می‌شود.",
                    reply_markup=types.ReplyKeyboardRemove(),
                )
                return

            completed_at = utc_now()
            creation = await create_or_reuse_ready_registration_intent(
                session,
                invitation_token=token,
                mobile_number=mobile_number,
                telegram_id=telegram_id,
                telegram_username=callback.from_user.username,
                telegram_full_name=callback.from_user.full_name,
                address=address,
                contact_verified_at=contact_verified_at,
                completed_at=completed_at,
                invitation_expires_at_snapshot=current_expiry,
            )
            await session.commit()
            intent_id = creation.intent.id
    except TelegramRegistrationIntentError as exc:
        if exc.code == "changed_payload_replay":
            await state.clear()
            await callback.answer("درخواست ثبت‌نام قابل تکرار نیست.", show_alert=True)
            await callback.message.answer(
                _registration_rejection_message("identity_conflict"),
                reply_markup=types.ReplyKeyboardRemove(),
            )
            return
        logger.warning(
            "Direct Telegram registration intent rejected locally",
            extra={
                "event": "telegram_registration.intent_local_rejected",
                "reason": exc.code,
            },
        )
        await callback.answer("ثبت درخواست انجام نشد. دوباره تلاش کنید.", show_alert=True)
        return
    except Exception as exc:
        logger.warning(
            "Direct Telegram registration intent persistence failed",
            extra={
                "event": "telegram_registration.intent_persistence_failed",
                "error_type": type(exc).__name__,
            },
        )
        await callback.answer("ثبت درخواست انجام نشد. دوباره تلاش کنید.", show_alert=True)
        return

    try:
        await state.clear()
    except Exception as exc:
        logger.warning(
            "Durable Telegram registration intent committed but FSM clear failed",
            extra={
                "event": "telegram_registration.post_commit_clear_failed",
                "error_type": type(exc).__name__,
                "intent_id": str(intent_id),
            },
        )
    audit_log(
        "telegram_registration.intent_ready",
        target_type="telegram_registration_intent",
        target_id=str(intent_id),
        result="success",
    )
    await callback.answer("درخواست ثبت شد.")
    await callback.message.answer(
        "⏳ اطلاعات شما ثبت شد و در حال بررسی نهایی است.",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    if not await _claim_registration_handoff_message(state, intent_id=intent_id):
        return
    try:
        resolution = await _wait_for_registration_handoff(
            intent_id=intent_id,
            telegram_id=telegram_id,
        )
    except Exception as exc:
        logger.warning(
            "Direct Telegram registration handoff polling failed",
            extra={
                "event": "telegram_registration.handoff_poll_failed",
                "error_type": type(exc).__name__,
            },
        )
        resolution = None
    await _send_registration_handoff(callback.message, resolution)


@router.message(Registration.awaiting_confirmation)
async def handle_registration_confirmation_message(
    message: types.Message,
    state: FSMContext,
):
    if await _reject_non_private_registration(message, state):
        return
    await message.answer("برای ادامه از دکمه «تایید و تکمیل ثبت‌نام» یا «اصلاح آدرس» استفاده کنید.")


@router.chat_join_request()
async def handle_channel_join_request(join_request: types.ChatJoinRequest):
    if not settings.channel_id or join_request.chat.id != settings.channel_id:
        return

    pending_tutorial_step = None
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(
            User.telegram_id == join_request.from_user.id,
            User.is_deleted == False,
        )
        user = (await session.execute(stmt)).scalar_one_or_none()

        denial_reason = bot_account_access_denial_reason(user)
        if user:
            activation_block = None
            if _direct_registration_runtime_ready():
                activation_block = await registration_activation_block_for_user(
                    session,
                    user=user,
                )
            decision = await evaluate_bot_access(session, user)
            if activation_block is not None:
                denial_reason = BOT_ACCESS_REASON_SYNC_PENDING
            elif not decision.allowed:
                denial_reason = decision.reason or BOT_ACCOUNT_INACTIVE_REASON

        if not denial_reason and user:
            completed_step = int(getattr(user, "bot_onboarding_completed_step", 0) or 0)
            if completed_step < BOT_ONBOARDING_REQUIRED_STEP:
                required_step = int(getattr(user, "bot_onboarding_required_step", 0) or 0)
                user.bot_onboarding_required_step = max(required_step, BOT_ONBOARDING_REQUIRED_STEP)
                pending_tutorial_step = max(completed_step + 1, OFFER_TUTORIAL_STEP)
                await session.commit()

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
        if pending_tutorial_step:
            tutorial_text = onboarding_text_for_step(pending_tutorial_step)
            tutorial_markup = build_onboarding_keyboard(pending_tutorial_step)
        else:
            tutorial_text = "✅ درخواست عضویت شما به صورت خودکار تایید شد. اکنون می‌توانید از کانال معاملات استفاده کنید."
            tutorial_markup = None
        await join_request.bot.send_message(
            chat_id=join_request.user_chat_id,
            text=tutorial_text,
            reply_markup=tutorial_markup,
        )
    except Exception:
        logger.exception("Failed to notify approved channel join request user")


@router.callback_query(F.data == OFFER_TUTORIAL_ACK_CALLBACK)
async def handle_offer_tutorial_ack(callback: types.CallbackQuery, user: Optional[User]):
    await _handle_bot_onboarding_ack(callback, user, acknowledged_step=OFFER_TUTORIAL_STEP)


@router.callback_query(F.data == CUSTOMER_TUTORIAL_ACK_CALLBACK)
async def handle_customer_tutorial_ack(callback: types.CallbackQuery, user: Optional[User]):
    await _handle_bot_onboarding_ack(callback, user, acknowledged_step=CUSTOMER_TUTORIAL_STEP)


async def _handle_bot_onboarding_ack(callback: types.CallbackQuery, user: Optional[User], *, acknowledged_step: int):
    if not user:
        await callback.answer("ابتدا حساب تلگرام خود را به حساب کاربری متصل کنید.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(
            User.telegram_id == callback.from_user.id,
            User.is_deleted == False,
        )
        db_user = (await session.execute(stmt)).scalar_one_or_none()
        if not db_user:
            await callback.answer("حساب کاربری شما یافت نشد.", show_alert=True)
            return

        required_step = int(getattr(db_user, "bot_onboarding_required_step", 0) or 0)
        db_user.bot_onboarding_required_step = max(required_step, BOT_ONBOARDING_REQUIRED_STEP)
        db_user.bot_onboarding_completed_step = max(
            int(getattr(db_user, "bot_onboarding_completed_step", 0) or 0),
            acknowledged_step,
        )
        completed_step = int(getattr(db_user, "bot_onboarding_completed_step", 0) or 0)
        if completed_step >= BOT_ONBOARDING_REQUIRED_STEP:
            db_user.bot_onboarding_completed_at = datetime.now(timezone.utc)
        await session.commit()

    if acknowledged_step < BOT_ONBOARDING_REQUIRED_STEP:
        await callback.answer("مرحله بعد")
        if callback.message:
            try:
                await callback.message.edit_text(
                    onboarding_text_for_step(acknowledged_step + 1),
                    reply_markup=build_onboarding_keyboard(acknowledged_step + 1),
                )
            except Exception:
                logger.exception("Failed to update bot onboarding tutorial message")
        return

    await callback.answer("ثبت شد.")
    if callback.message:
        try:
            await callback.message.edit_text("✅ راهنما تایید شد. اکنون می‌توانید از امکانات بات استفاده کنید.")
        except Exception:
            logger.exception("Failed to update bot onboarding completion message")


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

    await callback.message.edit_text(LEGACY_RESPOND_PATH_DISABLED_MESSAGE)
    await callback.answer("این مسیر دیگر فعال نیست.", show_alert=True)


# --- انصراف از پاسخ ---
@router.callback_query(F.data == "cancel_respond")
async def handle_cancel_respond(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ انصراف از معامله.")
    await callback.answer()
