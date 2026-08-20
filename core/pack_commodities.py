"""Shared product contract for sealed 100-coin packs.

Pack commodities intentionally have no independent estimator rate.  Their
type is inferred from the corresponding base-coin rate, while the authoritative
offer shape is always one indivisible pack of exactly 100 coins.
"""

from __future__ import annotations

from collections.abc import Sequence


PACK_QUANTITY = 100
PACK_FULL_COMMODITY_NAME = "پک تمام"
PACK_HALF_COMMODITY_NAME = "پک نیم"
PACK_QUARTER_COMMODITY_NAME = "پک ربع"
PACK_COMMODITY_NAMES = frozenset(
    {
        PACK_FULL_COMMODITY_NAME,
        PACK_HALF_COMMODITY_NAME,
        PACK_QUARTER_COMMODITY_NAME,
    }
)

PACK_BASE_RATE_CODE_TO_COMMODITY_CODE = {
    "IMAM": "PACK_FULL",
    "HALF_BAHAR": "PACK_HALF",
    "QUARTER_BAHAR": "PACK_QUARTER",
}
PACK_COMMODITY_CODE_TO_BASE_RATE_CODE = {
    pack_code: base_code
    for base_code, pack_code in PACK_BASE_RATE_CODE_TO_COMMODITY_CODE.items()
}
PACK_COMMODITY_NAME_BY_CODE = {
    "PACK_FULL": PACK_FULL_COMMODITY_NAME,
    "PACK_HALF": PACK_HALF_COMMODITY_NAME,
    "PACK_QUARTER": PACK_QUARTER_COMMODITY_NAME,
}

PACK_OFFER_SHAPE_ERROR = "آفر پک فقط یکجا و با تعداد ۱۰۰ ثبت می‌شود."


def is_pack_commodity_name(value: object) -> bool:
    return str(value or "").strip() in PACK_COMMODITY_NAMES


def validate_pack_offer_shape(
    *,
    commodity_name: object,
    quantity: object,
    is_wholesale: object,
    lot_sizes: Sequence[object] | None,
) -> tuple[bool, str]:
    """Validate only pack commodities; ordinary commodities pass unchanged."""

    if not is_pack_commodity_name(commodity_name):
        return True, ""
    try:
        normalized_quantity = int(quantity)
    except (TypeError, ValueError):
        return False, PACK_OFFER_SHAPE_ERROR
    if (
        normalized_quantity != PACK_QUANTITY
        or is_wholesale is not True
        or bool(lot_sizes)
    ):
        return False, PACK_OFFER_SHAPE_ERROR
    return True, ""


__all__ = [
    "PACK_BASE_RATE_CODE_TO_COMMODITY_CODE",
    "PACK_COMMODITY_CODE_TO_BASE_RATE_CODE",
    "PACK_COMMODITY_NAME_BY_CODE",
    "PACK_COMMODITY_NAMES",
    "PACK_FULL_COMMODITY_NAME",
    "PACK_HALF_COMMODITY_NAME",
    "PACK_OFFER_SHAPE_ERROR",
    "PACK_QUANTITY",
    "PACK_QUARTER_COMMODITY_NAME",
    "is_pack_commodity_name",
    "validate_pack_offer_shape",
]
