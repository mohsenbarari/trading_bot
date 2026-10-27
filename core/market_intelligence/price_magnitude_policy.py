"""Canonical price-unit policy: Market Store speaks toman (plus USD for XAU).

Product sources (public melted channels, coin groups, Herat, USDT, operator
entry) quote toman.  The store therefore stores toman.  True rial inputs are
converted once at the ingest boundary (÷10).  Silent unit mixing is forbidden.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping


RIAL_PER_TOMAN = Decimal("10")

# Rial-labeled units must not be stored as the product canonical unit.
FORBIDDEN_IRT_PRICE_UNITS = frozenset(
    {
        "IRT",
        "IRT_PER_MESGHAL",
        "IRT_PER_MESGHAL_750",
        "IRT_PER_GRAM_750",
        "IRT_PER_USD",
        "IRT_PER_USDT",
        "IRT_PER_COIN",
        "IRT_BUBBLE_PER_COIN",
    }
)

# True IRT mesghal after the previous rial-canonical era is typically >= 300M.
TRUE_IRT_MESGHAL_FLOOR = Decimal("200000000")
TRUE_IRT_COIN_FLOOR = Decimal("500000000")
TRUE_IRT_FX_FLOOR = Decimal("500000")

# Plausible absolute ranges for ELIGIBLE canonical toman store values.
CANONICAL_PRICE_RANGES_TOMAN: Mapping[str, tuple[Decimal, Decimal]] = {
    "TOMAN_PER_MESGHAL_750": (Decimal("30000000"), Decimal("200000000")),
    "TOMAN_PER_GRAM_750": (Decimal("1000000"), Decimal("20000000")),
    "TOMAN_PER_COIN": (Decimal("50000000"), Decimal("500000000")),
    "TOMAN_PER_USD": (Decimal("50000"), Decimal("500000")),
    "TOMAN_PER_USDT": (Decimal("50000"), Decimal("500000")),
    "USD_PER_TROY_OUNCE": (Decimal("1500"), Decimal("8000")),
    # Product coin book: 1 unit = 1,000 toman.
    "PROJECT_THOUSAND_TOMAN": (Decimal("5000"), Decimal("500000")),
}

PROJECT_TOMAN_FIELD_RANGE = (50_000, 400_000)
FULL_TOMAN_COIN_FIELD_RANGE = (50_000_000, 400_000_000)

IRT_TO_TOMAN_UNIT = {
    "IRT_PER_MESGHAL_750": "TOMAN_PER_MESGHAL_750",
    "IRT_PER_GRAM_750": "TOMAN_PER_GRAM_750",
    "IRT_PER_COIN": "TOMAN_PER_COIN",
    "IRT_PER_USD": "TOMAN_PER_USD",
    "IRT_PER_USDT": "TOMAN_PER_USDT",
}


class PriceUnitPolicyError(ValueError):
    """Raised when a price unit or magnitude is unsafe for storage or entry."""


def forbid_irt_unit(price_unit: str) -> str:
    normalized = str(price_unit or "").strip().upper()
    if not normalized:
        raise PriceUnitPolicyError("price_unit_required")
    if normalized in FORBIDDEN_IRT_PRICE_UNITS or (
        normalized.startswith("IRT_") and normalized != "IRT_BUBBLE_PER_COIN"
    ):
        raise PriceUnitPolicyError("irt_price_unit_forbidden_use_toman")
    if normalized.startswith("IRT"):
        raise PriceUnitPolicyError("irt_price_unit_forbidden_use_toman")
    return normalized


def convert_irt_amount_to_toman(amount: Decimal | int | float | str) -> Decimal:
    value = Decimal(str(amount))
    if value <= 0:
        raise PriceUnitPolicyError("price_must_be_positive")
    return value / RIAL_PER_TOMAN


def canonicalize_legacy_public_price(
    *,
    price: Decimal,
    price_unit: str,
    currency: str,
) -> tuple[Decimal, str, str, dict[str, object]]:
    """Convert legacy public units into canonical toman store units."""

    unit = str(price_unit or "").strip().upper()
    cur = str(currency or "").strip().upper()
    attrs: dict[str, object] = {}

    if unit == "TOMAN_PER_USD":
        return price, "TOMAN_PER_USD", "TOMAN", attrs
    if unit in {"TOMAN_PER_MESGHAL_750", "TOMAN_PER_MESGHAL"}:
        return price, "TOMAN_PER_MESGHAL_750", "TOMAN", attrs
    if unit == "TOMAN_PER_COIN":
        return price, "TOMAN_PER_COIN", "TOMAN", attrs
    if unit == "USD_PER_TROY_OUNCE":
        return price, unit, "USD", attrs
    if unit == "IRT_PER_MESGHAL_750":
        # Legacy often labeled toman as IRT.  Only true rial magnitudes ÷10.
        if price >= TRUE_IRT_MESGHAL_FLOOR:
            return (
                convert_irt_amount_to_toman(price),
                "TOMAN_PER_MESGHAL_750",
                "TOMAN",
                {
                    "legacy_unit_converted_from": "IRT_PER_MESGHAL_750",
                    "legacy_price_scale_fixed": True,
                },
            )
        return (
            price,
            "TOMAN_PER_MESGHAL_750",
            "TOMAN",
            {"legacy_unit_relabeled_from": "MISLABELED_TOMAN_AS_IRT_PER_MESGHAL_750"},
        )
    if unit == "IRT_PER_USD":
        if price >= TRUE_IRT_FX_FLOOR and cur != "TOMAN":
            return (
                convert_irt_amount_to_toman(price),
                "TOMAN_PER_USD",
                "TOMAN",
                {
                    "legacy_unit_converted_from": "IRT_PER_USD",
                    "legacy_price_scale_fixed": True,
                },
            )
        return (
            price,
            "TOMAN_PER_USD",
            "TOMAN",
            {"legacy_unit_relabeled_from": "MISLABELED_TOMAN_AS_IRT_PER_USD"},
        )
    raise PriceUnitPolicyError(f"legacy_price_unit_unsupported:{unit}")


def canonicalize_external_price(
    *,
    instrument: str,
    price: Decimal,
    price_unit: str,
) -> tuple[Decimal, str, dict[str, object]]:
    """Map external feed labels into canonical toman units."""

    unit = str(price_unit or "").strip().upper()
    instrument_code = str(instrument or "").strip().upper()
    attrs: dict[str, object] = {}

    target = {
        "IME_GOLD_BAR": "TOMAN_PER_MESGHAL_750",
        "IME_GOLD_COIN_IMAM": "TOMAN_PER_COIN",
        "USDT_IRT": "TOMAN_PER_USDT",
    }.get(instrument_code)
    if target is None:
        raise PriceUnitPolicyError(f"external_instrument_unsupported:{instrument_code}")

    # External normalized values in the legacy DB are toman-scale even when the
    # unit string says IRT.  True IRT leftovers are divided once.
    floor = {
        "TOMAN_PER_MESGHAL_750": TRUE_IRT_MESGHAL_FLOOR,
        "TOMAN_PER_COIN": TRUE_IRT_COIN_FLOOR,
        "TOMAN_PER_USDT": TRUE_IRT_FX_FLOOR,
    }[target]
    if unit.startswith("IRT") and price >= floor:
        return (
            convert_irt_amount_to_toman(price),
            target,
            {
                "external_unit_converted_from": unit,
                "legacy_price_scale_fixed": True,
            },
        )
    if unit.startswith("IRT") or unit == target or unit.startswith("TOMAN"):
        return price, target, {"external_unit_relabeled_from": unit}
    raise PriceUnitPolicyError(f"external_price_unit_unsupported:{unit}")


def assert_canonical_magnitude(*, price_unit: str, price: Decimal) -> None:
    """Reject out-of-range canonical prices; never silently clamp."""

    unit = forbid_irt_unit(price_unit)
    bounds = CANONICAL_PRICE_RANGES_TOMAN.get(unit)
    if bounds is None:
        return
    low, high = bounds
    if price < low or price > high:
        raise PriceUnitPolicyError(
            f"price_out_of_canonical_range:{unit}:{price}"
        )


def assert_project_toman_field(value: int | float, *, field: str) -> int:
    """Validate operator fields that store project units (1 = 1,000 toman)."""

    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PriceUnitPolicyError(f"{field}_invalid") from exc
    low, high = PROJECT_TOMAN_FIELD_RANGE
    if not low <= number <= high:
        if FULL_TOMAN_COIN_FIELD_RANGE[0] <= number <= FULL_TOMAN_COIN_FIELD_RANGE[1]:
            raise PriceUnitPolicyError(
                f"{field}_looks_like_full_toman_use_project_thousand_toman"
            )
        if number >= FULL_TOMAN_COIN_FIELD_RANGE[1] * 5:
            raise PriceUnitPolicyError(f"{field}_looks_like_rial_not_toman")
        raise PriceUnitPolicyError(f"{field}_out_of_allowed_toman_range")
    return number


def needs_irt_to_toman_migration(*, price_unit: str) -> bool:
    return str(price_unit or "").strip().upper() in IRT_TO_TOMAN_UNIT
