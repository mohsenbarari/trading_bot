"""Telegram channel offer rendering and terminal-state side effects."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

from core.config import settings
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.trade_service import get_available_trade_amounts
from models.offer import OfferStatus, OfferType

logger = logging.getLogger(__name__)

INVISIBLE_CHANNEL_PADDING = "\u2800" * 35
TELEGRAM_MESSAGE_NOT_MODIFIED = "message is not modified"


def _status_value(status: Any) -> str:
    return str(getattr(status, "value", status) or "").strip().lower()


def _offer_type_value(offer_type: Any) -> str:
    return str(getattr(offer_type, "value", offer_type) or "").strip().lower()


def _finite_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        return None
    return numeric_value


def infer_traded_quantity_from_offer(offer: Any) -> int:
    """Infer completed quantity from offer state when trade aggregate is unavailable."""
    quantity = _finite_int(getattr(offer, "quantity", None))
    remaining = _finite_int(getattr(offer, "remaining_quantity", None))
    if quantity is None or remaining is None:
        return 0
    return max(0, quantity - remaining)


def get_offer_channel_history_tag(offer: Any, traded_quantity: Optional[int] = None) -> Optional[str]:
    """Return the terminal Telegram tag. Pure expired offers intentionally have no tag."""
    status = _status_value(getattr(offer, "status", None))
    if status == OfferStatus.COMPLETED.value:
        return "معامله‌شده"

    if status != OfferStatus.EXPIRED.value:
        return None

    if getattr(offer, "expire_reason", None) != "time_limit":
        return None

    quantity = traded_quantity if traded_quantity is not None else infer_traded_quantity_from_offer(offer)
    if quantity and quantity > 0:
        return f"معامله‌شده · {quantity:,} عدد"
    return None


def build_offer_channel_message(offer: Any, *, history_tag: Optional[str] = None) -> str:
    """Build the canonical channel post text for active and terminal offer states."""
    offer_type = _offer_type_value(getattr(offer, "offer_type", None))
    trade_emoji = "🟢" if offer_type == OfferType.BUY.value else "🔴"
    trade_label = "خرید" if offer_type == OfferType.BUY.value else "فروش"
    commodity = getattr(offer, "commodity", None)
    commodity_name = getattr(commodity, "name", None) or "نامشخص"
    quantity = _finite_int(getattr(offer, "quantity", None)) or 0
    price = _finite_int(getattr(offer, "price", None)) or 0

    message = f"{trade_emoji}{trade_label} {commodity_name} {quantity} عدد {price:,}"
    notes = (getattr(offer, "notes", None) or "").strip()
    if notes:
        message += f"\nتوضیحات: {notes}"
    if history_tag:
        message += f"\n{history_tag}"
    return f"{message}\n{INVISIBLE_CHANNEL_PADDING}"


def build_offer_channel_reply_markup(offer: Any) -> Optional[dict[str, Any]]:
    """Build Telegram inline keyboard for active offers."""
    status = _status_value(getattr(offer, "status", None))
    if status and status != OfferStatus.ACTIVE.value:
        return None

    offer_id = _finite_int(getattr(offer, "id", None))
    quantity = _finite_int(getattr(offer, "quantity", None)) or 0
    raw_remaining = _finite_int(getattr(offer, "remaining_quantity", None))
    remaining = quantity if raw_remaining is None else raw_remaining
    if not offer_id or remaining <= 0:
        return None

    is_wholesale = bool(getattr(offer, "is_wholesale", True))
    raw_lot_sizes = getattr(offer, "lot_sizes", None)
    lot_sizes = sorted(raw_lot_sizes, reverse=True) if raw_lot_sizes else None

    if is_wholesale or not lot_sizes:
        amounts = [remaining]
    else:
        amounts = get_available_trade_amounts(
            quantity=quantity,
            remaining_quantity=remaining,
            is_wholesale=False,
            lot_sizes=lot_sizes,
        )

    seen: set[int] = set()
    buttons = []
    for amount in amounts:
        numeric_amount = _finite_int(amount)
        if numeric_amount is None or numeric_amount <= 0 or numeric_amount in seen:
            continue
        seen.add(numeric_amount)
        buttons.append({
            "text": f"{numeric_amount} عدد",
            "callback_data": f"channel_trade:{offer_id}:{numeric_amount}",
        })

    return {"inline_keyboard": [buttons]} if buttons else None


def _telegram_credentials_available() -> tuple[Optional[str], Any]:
    bot_token = settings.bot_token or os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    return bot_token, channel_id


def _telegram_status_ok(response: Any) -> bool:
    if getattr(response, "status_code", None) == 200:
        return True
    body = ""
    try:
        body = response.text
    except Exception:
        body = ""
    return TELEGRAM_MESSAGE_NOT_MODIFIED in body.lower()


async def apply_offer_channel_state(
    offer: Any,
    *,
    traded_quantity: Optional[int] = None,
    reason: str = "",
    timeout: float = 10,
) -> bool:
    """
    Apply the current offer state to its Telegram channel post.

    This function is intentionally foreign-only. Iran may render WebApp history,
    but must not call Telegram for channel post mutations.
    """
    if current_server() != SERVER_FOREIGN:
        return False

    channel_message_id = _finite_int(getattr(offer, "channel_message_id", None))
    if not channel_message_id:
        return False

    bot_token, channel_id = _telegram_credentials_available()
    if not bot_token or not channel_id:
        return False

    status = _status_value(getattr(offer, "status", None))
    history_tag = get_offer_channel_history_tag(offer, traded_quantity=traded_quantity)

    if history_tag:
        url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
        payload = {
            "chat_id": channel_id,
            "message_id": channel_message_id,
            "text": build_offer_channel_message(offer, history_tag=history_tag),
            "reply_markup": None,
        }
    elif status and status != OfferStatus.ACTIVE.value:
        url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
        payload = {
            "chat_id": channel_id,
            "message_id": channel_message_id,
        }
    else:
        url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
        payload = {
            "chat_id": channel_id,
            "message_id": channel_message_id,
        }
        reply_markup = build_offer_channel_reply_markup(offer)
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=timeout)
        return _telegram_status_ok(response)
    except Exception as exc:
        logger.debug(
            "Failed to apply Telegram channel offer state",
            extra={
                "event": "telegram.offer_channel_state_failed",
                "offer_id": getattr(offer, "id", None),
                "reason": reason,
                "error_class": type(exc).__name__,
            },
        )
        return False
