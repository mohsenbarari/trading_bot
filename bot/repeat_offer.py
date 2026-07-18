"""Telegram shortcut for the latest repeatable offer."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import time

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.keyboards import (
    get_admin_panel_keyboard,
    get_persistent_menu_keyboard,
    get_user_panel_keyboard,
    get_users_management_keyboard,
)
from core.db import AsyncSessionLocal
from core.offer_settlement import build_offer_draft_text
from core.server_routing import SERVER_FOREIGN
from core.services.bot_access_policy import evaluate_bot_access
from core.services.offer_republish_service import (
    REPEATABLE_EXPIRE_REASONS,
    list_repeatable_offers,
    offer_remaining_lot_sizes,
    offer_remaining_quantity,
)
from models.offer import Offer, OfferStatus
from models.user import User


logger = logging.getLogger(__name__)

BOT_REPEAT_OFFER_BUTTON_PREFIX = "🔁 "
BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH = 64
BOT_REPEAT_OFFER_REFRESH_DEBOUNCE_SECONDS = 2.0

_repeat_offer_refresh_sent_at: dict[int, float] = {}


@dataclass(frozen=True)
class BotRepeatOfferCandidate:
    source_offer_id: int
    source_offer_public_id: str
    draft_text: str
    button_text: str


def is_bot_repeat_offer_button_text(text: object) -> bool:
    return str(text or "").strip().startswith(BOT_REPEAT_OFFER_BUTTON_PREFIX)


def _button_text(draft_text: str) -> str:
    text = f"{BOT_REPEAT_OFFER_BUTTON_PREFIX}{draft_text}".strip()
    if len(text) <= BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH:
        return text
    return f"{text[: BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH - 3].rstrip()}..."


def bot_repeat_offer_candidate(offer: Offer | object) -> BotRepeatOfferCandidate | None:
    public_id = str(getattr(offer, "offer_public_id", "") or "").strip()
    commodity = getattr(offer, "commodity", None)
    commodity_name = str(getattr(commodity, "name", "") or "").strip()
    quantity = offer_remaining_quantity(offer)
    is_wholesale = bool(getattr(offer, "is_wholesale", True))
    try:
        lot_sizes = offer_remaining_lot_sizes(offer)
    except (TypeError, ValueError):
        return None

    if not public_id or not commodity_name or quantity <= 0:
        return None
    if not is_wholesale and (not lot_sizes or sum(lot_sizes) != quantity):
        return None

    common = {
        "offer_type": getattr(offer, "offer_type", None),
        "settlement_type": getattr(offer, "settlement_type", None),
        "commodity_name": commodity_name,
        "quantity": quantity,
        "price": getattr(offer, "price", 0),
        "is_wholesale": is_wholesale,
        "lot_sizes": lot_sizes,
    }
    try:
        draft_text = build_offer_draft_text(
            **common,
            notes=getattr(offer, "notes", None),
        )
        source_offer_id = int(getattr(offer, "id"))
    except (TypeError, ValueError):
        return None

    return BotRepeatOfferCandidate(
        source_offer_id=source_offer_id,
        source_offer_public_id=public_id,
        draft_text=draft_text,
        button_text=_button_text(draft_text),
    )


async def load_latest_bot_repeat_offer_candidate(
    db: AsyncSession,
    *,
    owner_user_id: int,
) -> BotRepeatOfferCandidate | None:
    offers = await list_repeatable_offers(
        db,
        owner_user_id=owner_user_id,
        limit=1,
        since_hours=1,
        options=(selectinload(Offer.commodity),),
        replacement_home_server=SERVER_FOREIGN,
    )
    if not offers:
        return None
    return bot_repeat_offer_candidate(offers[0])


async def resolve_bot_repeat_offer_button_candidate(
    db: AsyncSession,
    *,
    owner_user_id: int,
    button_text: str,
) -> tuple[BotRepeatOfferCandidate | None, bool, str | None]:
    """Accept only the currently displayed latest-offer text."""
    candidate = await load_latest_bot_repeat_offer_candidate(
        db,
        owner_user_id=owner_user_id,
    )
    if candidate is None:
        return None, True, "no_repeatable_offer"

    normalized_button = str(button_text or "").strip()
    if normalized_button != candidate.button_text:
        return None, True, "stale_button"
    return candidate, False, None


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


async def refresh_repeat_offer_menu_for_expired_offer(
    bot: object,
    offer_id: int,
) -> bool:
    """Push a current reply keyboard after an expiry changes the latest offer."""
    try:
        async with AsyncSessionLocal() as session:
            offer = await session.get(Offer, int(offer_id))
            if offer is None or _enum_value(getattr(offer, "status", None)) != OfferStatus.EXPIRED.value:
                return False
            if str(getattr(offer, "expire_reason", "") or "") not in REPEATABLE_EXPIRE_REASONS:
                return False
            if (
                _enum_value(getattr(offer, "home_server", None)) == SERVER_FOREIGN
                and _enum_value(getattr(offer, "expire_source_surface", None)) == "telegram_bot"
            ):
                # The bot's manual-expiry handler already sends the committed keyboard.
                return False

            owner_user_id = getattr(offer, "user_id", None)
            if owner_user_id is None:
                return False
            user = await session.get(User, int(owner_user_id))
            telegram_id = getattr(user, "telegram_id", None) if user is not None else None
            if telegram_id is None:
                return False
            access = await evaluate_bot_access(session, user)
            if not access.allowed:
                return False
            candidate = await load_latest_bot_repeat_offer_candidate(
                session,
                owner_user_id=int(owner_user_id),
            )

        now = time.monotonic()
        previous_sent_at = _repeat_offer_refresh_sent_at.get(int(owner_user_id))
        if (
            previous_sent_at is not None
            and now - previous_sent_at < BOT_REPEAT_OFFER_REFRESH_DEBOUNCE_SECONDS
        ):
            return False

        from core.config import settings
        from core.public_webapp_url import user_facing_webapp_url

        keyboard = prepend_repeat_offer_button(
            get_persistent_menu_keyboard(
                getattr(user, "role", None),
                user_facing_webapp_url(settings_obj=settings),
            ),
            candidate,
        )
        await bot.send_message(
            chat_id=int(telegram_id),
            text="منو با آخرین وضعیت به‌روزرسانی شد.",
            reply_markup=keyboard,
        )
        _repeat_offer_refresh_sent_at[int(owner_user_id)] = now
        return True
    except Exception:
        logger.warning(
            "Failed to refresh repeat-offer keyboard after offer expiry",
            exc_info=True,
            extra={
                "event": "telegram.repeat_offer.expiry_refresh_failed",
                "offer_id": offer_id,
            },
        )
        return False


def prepend_repeat_offer_button(
    keyboard: ReplyKeyboardMarkup,
    candidate: BotRepeatOfferCandidate | None,
) -> ReplyKeyboardMarkup:
    if candidate is None:
        return keyboard
    return keyboard.model_copy(
        update={
            "keyboard": [
                [KeyboardButton(text=candidate.button_text)],
                *keyboard.keyboard,
            ]
        }
    )


async def decorate_navigation_keyboard(
    keyboard: ReplyKeyboardMarkup,
    user: object | None,
) -> ReplyKeyboardMarkup:
    user_id = getattr(user, "id", None)
    if user_id is None:
        return keyboard
    try:
        async with AsyncSessionLocal() as session:
            access = await evaluate_bot_access(session, user)
            if not access.allowed:
                return keyboard
            candidate = await load_latest_bot_repeat_offer_candidate(
                session,
                owner_user_id=int(user_id),
            )
    except Exception:
        logger.warning(
            "Failed to build latest repeatable offer keyboard row",
            exc_info=True,
            extra={
                "event": "telegram.repeat_offer.keyboard_lookup_failed",
                "user_id": user_id,
            },
        )
        return keyboard
    return prepend_repeat_offer_button(keyboard, candidate)


async def build_persistent_navigation_keyboard(
    user: object,
    mini_app_url: str | None,
) -> ReplyKeyboardMarkup:
    return await decorate_navigation_keyboard(
        get_persistent_menu_keyboard(getattr(user, "role", None), mini_app_url),
        user,
    )


async def build_user_panel_navigation_keyboard(
    user: object,
    *,
    standard_actions: bool = False,
    show_support: bool = False,
) -> ReplyKeyboardMarkup:
    return await decorate_navigation_keyboard(
        get_user_panel_keyboard(
            getattr(user, "role", None),
            standard_actions=standard_actions,
            show_support=show_support,
        ),
        user,
    )


async def build_admin_panel_navigation_keyboard(user: object) -> ReplyKeyboardMarkup:
    return await decorate_navigation_keyboard(
        get_admin_panel_keyboard(getattr(user, "role", None)),
        user,
    )


async def build_users_management_navigation_keyboard(user: object) -> ReplyKeyboardMarkup:
    return await decorate_navigation_keyboard(get_users_management_keyboard(), user)
