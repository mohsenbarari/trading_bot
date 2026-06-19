"""Telegram trade callback identity helpers.

Keep this module free of aiogram imports so API/channel services can build the
same callback payloads as the bot handlers.
"""
from __future__ import annotations

from typing import Any


CHANNEL_TRADE_LEGACY_CALLBACK_PREFIX = "channel_trade"
CHANNEL_TRADE_PUBLIC_CALLBACK_PREFIX = "ct2"


def _positive_int(value: Any) -> int | None:
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        return None
    return numeric_value if numeric_value > 0 else None


def _normalized_public_id(value: Any) -> str | None:
    if value is None:
        return None
    public_id = str(value).strip()
    return public_id or None


def build_channel_trade_callback_data(
    *,
    offer_id: Any = None,
    offer_public_id: Any = None,
    amount: Any,
) -> str:
    """Build a Telegram callback payload for offer trade actions.

    New callbacks prefer the stable offer public identity. The integer offer id
    fallback is kept only for legacy/compatibility paths.
    """
    numeric_amount = _positive_int(amount)
    if numeric_amount is None:
        raise ValueError("channel trade callback amount must be a positive integer")

    public_id = _normalized_public_id(offer_public_id)
    if public_id:
        return f"{CHANNEL_TRADE_PUBLIC_CALLBACK_PREFIX}:{public_id}:{numeric_amount}"

    numeric_offer_id = _positive_int(offer_id)
    if numeric_offer_id is None:
        raise ValueError("channel trade callback requires offer_public_id or a positive offer_id")
    return f"{CHANNEL_TRADE_LEGACY_CALLBACK_PREFIX}:{numeric_offer_id}:{numeric_amount}"
