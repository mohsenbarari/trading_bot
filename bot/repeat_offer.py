"""Telegram shortcut for the latest repeatable offer."""
from __future__ import annotations

from dataclasses import dataclass
import logging

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
    list_repeatable_offers,
    offer_remaining_lot_sizes,
    offer_remaining_quantity,
)
from models.offer import Offer


logger = logging.getLogger(__name__)

BOT_REPEAT_OFFER_BUTTON_PREFIX = "🔁 "
BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH = 64
BOT_REPEAT_OFFER_RESOLUTION_LIMIT = 50


@dataclass(frozen=True)
class BotRepeatOfferCandidate:
    source_offer_id: int
    source_offer_public_id: str
    draft_text: str
    button_text: str
    legacy_button_text: str | None = None


def is_bot_repeat_offer_button_text(text: object) -> bool:
    return str(text or "").strip().startswith(BOT_REPEAT_OFFER_BUTTON_PREFIX)


def _button_text(draft_text: str, *, source_offer_id: int | None = None) -> str:
    suffix = f"  #{source_offer_id}" if source_offer_id is not None else ""
    text = f"{BOT_REPEAT_OFFER_BUTTON_PREFIX}{draft_text}".strip()
    available_length = BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH - len(suffix)
    if len(text) > available_length:
        text = f"{text[: available_length - 3].rstrip()}..."
    text = f"{text}{suffix}"
    if len(text) <= BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH:
        return text
    return text[:BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH]


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
        compact_draft = build_offer_draft_text(**common, notes=None)
        source_offer_id = int(getattr(offer, "id"))
    except (TypeError, ValueError):
        return None

    return BotRepeatOfferCandidate(
        source_offer_id=source_offer_id,
        source_offer_public_id=public_id,
        draft_text=draft_text,
        button_text=_button_text(draft_text, source_offer_id=source_offer_id),
        legacy_button_text=_button_text(compact_draft),
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
    """Resolve the button the user actually saw and report menu staleness."""
    offers = await list_repeatable_offers(
        db,
        owner_user_id=owner_user_id,
        limit=BOT_REPEAT_OFFER_RESOLUTION_LIMIT,
        since_hours=1,
        options=(selectinload(Offer.commodity),),
        replacement_home_server=SERVER_FOREIGN,
    )
    if not offers:
        return None, True, "no_repeatable_offer"

    normalized_button = str(button_text or "").strip()
    candidates = [
        candidate
        for offer in offers
        if (candidate := bot_repeat_offer_candidate(offer)) is not None
    ]
    latest = candidates[0] if candidates else None

    for candidate in candidates:
        if normalized_button != candidate.button_text:
            continue
        needs_refresh = (
            latest is None
            or candidate.source_offer_public_id != latest.source_offer_public_id
        )
        return candidate, needs_refresh, None

    legacy_matches = [
        candidate
        for candidate in candidates
        if candidate.legacy_button_text == normalized_button
    ]
    if len(legacy_matches) == 1:
        return legacy_matches[0], True, "legacy_button"
    if len(legacy_matches) > 1:
        return None, True, "ambiguous_legacy_button"

    return None, True, "button_not_found"


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
