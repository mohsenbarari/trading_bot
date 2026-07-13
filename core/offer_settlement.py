"""Shared settlement normalization and Persian presentation labels."""
from __future__ import annotations

from typing import Any

from core.enums import SettlementType


def settlement_type_value(value: Any, *, default: SettlementType = SettlementType.CASH) -> str:
    raw_value = getattr(value, "value", value)
    normalized = str(raw_value or "").strip().lower()
    if normalized == SettlementType.TOMORROW.value:
        return SettlementType.TOMORROW.value
    if normalized == SettlementType.CASH.value:
        return SettlementType.CASH.value
    return default.value


def normalize_settlement_type(value: Any) -> SettlementType:
    return SettlementType(settlement_type_value(value))


def offer_settlement_label(value: Any) -> str:
    if settlement_type_value(value) == SettlementType.TOMORROW.value:
        return "فردا ➡️"
    return "نقد حاضر ☀️"


def trade_settlement_label(value: Any) -> str:
    if settlement_type_value(value) == SettlementType.TOMORROW.value:
        return "فردایی"
    return "نقد حاضر"


def build_offer_summary_text(
    *,
    offer_type: Any,
    settlement_type: Any,
    commodity_name: Any,
    quantity: Any,
    price: Any,
) -> str:
    raw_offer_type = getattr(offer_type, "value", offer_type)
    normalized_offer_type = str(raw_offer_type or "buy").strip().lower()
    trade_emoji = "🟢" if normalized_offer_type == "buy" else "🔴"
    trade_label = "خرید" if normalized_offer_type == "buy" else "فروش"
    normalized_commodity_name = str(commodity_name or "نامشخص").strip() or "نامشخص"
    try:
        formatted_price = f"{int(price or 0):,}"
    except (TypeError, ValueError):
        formatted_price = str(price or 0)
    return (
        f"{trade_emoji}{trade_label} {normalized_commodity_name} {quantity} عدد "
        f"{offer_settlement_label(settlement_type)} {formatted_price}"
    )
