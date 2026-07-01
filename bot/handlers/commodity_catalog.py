"""Read-only commodity catalog for Telegram bot users."""

from __future__ import annotations

from math import ceil
from typing import Sequence

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.callbacks import CommodityCatalogPageCallback
from bot.message_manager import DeleteDelay, delete_previous_anchor, set_anchor
from core.db import AsyncSessionLocal
from core.services.user_account_status_service import is_user_global_web_locked
from models.commodity import Commodity
from models.user import User


router = Router()

COMMODITY_CATALOG_TEXT = "📦 لیست کالاها"
COMMODITY_CATALOG_PAGE_SIZE = 10
_MAX_ALIAS_TEXT_LENGTH = 900


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _stable_text_key(value: object) -> str:
    return _normalize_text(value).casefold()


def _commodity_display_name(commodity: object) -> str:
    return _normalize_text(getattr(commodity, "name", None)) or "نامشخص"


def _commodity_sort_key(commodity: object) -> tuple[str, int]:
    try:
        commodity_id = int(getattr(commodity, "id", 0) or 0)
    except (TypeError, ValueError):
        commodity_id = 0
    return _stable_text_key(_commodity_display_name(commodity)), commodity_id


def _alias_display_names(commodity: object) -> list[str]:
    commodity_name = _commodity_display_name(commodity)
    seen = {_stable_text_key(commodity_name)}
    alias_names: list[str] = []
    for alias in getattr(commodity, "aliases", None) or []:
        alias_name = _normalize_text(getattr(alias, "alias", None))
        alias_key = _stable_text_key(alias_name)
        if not alias_name or alias_key in seen:
            continue
        seen.add(alias_key)
        alias_names.append(alias_name)
    return sorted(alias_names, key=_stable_text_key)


def _bounded_alias_text(alias_names: Sequence[str]) -> str:
    text = "، ".join(alias_names)
    if len(text) <= _MAX_ALIAS_TEXT_LENGTH:
        return text
    return text[: _MAX_ALIAS_TEXT_LENGTH - 1].rstrip(" ،") + "…"


def _normalize_page(page: object, total_pages: int) -> int:
    try:
        normalized = int(page)
    except (TypeError, ValueError):
        normalized = 1
    if total_pages < 1:
        return 1
    return min(max(normalized, 1), total_pages)


def _catalog_page_items(commodities: Sequence[object], page: int, page_size: int) -> list[object]:
    offset = (page - 1) * page_size
    return list(commodities[offset : offset + page_size])


def build_commodity_catalog_text(
    commodities: Sequence[object],
    *,
    page: int,
    page_size: int = COMMODITY_CATALOG_PAGE_SIZE,
) -> str:
    sorted_commodities = sorted(commodities, key=_commodity_sort_key)
    total_count = len(sorted_commodities)
    total_pages = max(1, ceil(total_count / page_size))
    normalized_page = _normalize_page(page, total_pages)

    if not sorted_commodities:
        return "📦 لیست کالاها\n\nهنوز کالایی ثبت نشده است."

    lines = [
        "📦 لیست کالاها و نام‌های مستعار",
        "",
        f"تعداد کالاها: {total_count}",
        f"صفحه {normalized_page}/{total_pages}",
        "",
    ]
    start_index = (normalized_page - 1) * page_size + 1
    for index, commodity in enumerate(
        _catalog_page_items(sorted_commodities, normalized_page, page_size),
        start=start_index,
    ):
        lines.append(f"{index}. {_commodity_display_name(commodity)}")
        alias_names = _alias_display_names(commodity)
        if alias_names:
            lines.append(f"   نام‌های قابل استفاده: {_bounded_alias_text(alias_names)}")
        else:
            lines.append("   نام مستعار ثبت نشده است.")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_commodity_catalog_keyboard(
    *,
    page: int,
    total_count: int,
    page_size: int = COMMODITY_CATALOG_PAGE_SIZE,
) -> InlineKeyboardMarkup | None:
    total_pages = max(1, ceil(total_count / page_size))
    if total_pages <= 1:
        return None
    normalized_page = _normalize_page(page, total_pages)
    previous_button = InlineKeyboardButton(text="⏺", callback_data="noop")
    next_button = InlineKeyboardButton(text="⏺", callback_data="noop")
    if normalized_page > 1:
        previous_button = InlineKeyboardButton(
            text="➡️ قبلی",
            callback_data=CommodityCatalogPageCallback(page=normalized_page - 1).pack(),
        )
    if normalized_page < total_pages:
        next_button = InlineKeyboardButton(
            text="بعدی ⬅️",
            callback_data=CommodityCatalogPageCallback(page=normalized_page + 1).pack(),
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                previous_button,
                InlineKeyboardButton(text=f"{normalized_page}/{total_pages}", callback_data="noop"),
                next_button,
            ]
        ]
    )


async def load_commodity_catalog() -> list[Commodity]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Commodity)
            .options(selectinload(Commodity.aliases))
            .order_by(Commodity.name.asc(), Commodity.id.asc())
        )
        return sorted(result.scalars().all(), key=_commodity_sort_key)


async def _render_catalog(page: int) -> tuple[str, InlineKeyboardMarkup | None]:
    commodities = await load_commodity_catalog()
    total_pages = max(1, ceil(len(commodities) / COMMODITY_CATALOG_PAGE_SIZE))
    normalized_page = _normalize_page(page, total_pages)
    return (
        build_commodity_catalog_text(commodities, page=normalized_page),
        build_commodity_catalog_keyboard(page=normalized_page, total_count=len(commodities)),
    )


def _bot_user_can_view_catalog(user: User | object | None) -> bool:
    return user is not None and not getattr(user, "is_deleted", False) and not is_user_global_web_locked(user)


@router.message(F.text == COMMODITY_CATALOG_TEXT)
async def show_commodity_catalog(message: types.Message, user: User | None):
    if not _bot_user_can_view_catalog(user):
        return

    await delete_previous_anchor(message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value)
    text, keyboard = await _render_catalog(1)
    anchor_msg = await message.answer(text, reply_markup=keyboard)
    set_anchor(message.chat.id, anchor_msg.message_id)


@router.callback_query(CommodityCatalogPageCallback.filter())
async def paginate_commodity_catalog(
    callback: types.CallbackQuery,
    callback_data: CommodityCatalogPageCallback,
    user: User | None,
):
    if not _bot_user_can_view_catalog(user):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    text, keyboard = await _render_catalog(callback_data.page)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest:
        pass
    await callback.answer()
