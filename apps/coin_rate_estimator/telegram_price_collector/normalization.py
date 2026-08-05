from __future__ import annotations

from decimal import Decimal


MESGHAL_750_GRAMS = Decimal("4.3318")
IME_GOLD_BAR_FINENESS = Decimal("995")
STANDARD_GOLD_FINENESS = Decimal("750")
IME_GOLD_BAR_CERTIFICATE_GRAMS = Decimal("0.1")
IMAM_COIN_GRAMS = Decimal("8.133")
IMAM_COIN_FINENESS = Decimal("900")


def ime_gold_bar_irr_per_certificate_to_irt_per_mesghal_750(
    raw_price_irr: Decimal,
) -> Decimal:
    """Normalize one 0.1 g / 995 IME certificate quote to 4.3318 g / 750.

    IME quotes one certificate in IRR.  One certificate is 0.1 gram of 995
    fineness gold.  Dividing IRR by ten to get toman and dividing by 0.1 gram
    cancel numerically; the remaining conversion is fineness and mesghal size.
    """
    if raw_price_irr <= 0:
        raise ValueError("IME gold bar quote must be positive")
    return (
        raw_price_irr
        * STANDARD_GOLD_FINENESS
        / IME_GOLD_BAR_FINENESS
        * MESGHAL_750_GRAMS
    )


def ime_coin_irr_per_coin_to_irt_per_coin(raw_price_irr: Decimal) -> Decimal:
    if raw_price_irr <= 0:
        raise ValueError("IME coin quote must be positive")
    return raw_price_irr / Decimal("10")


def imam_intrinsic_coefficient() -> Decimal:
    """Project/domain coefficient for Imam intrinsic value."""
    return Decimal("2.253")


def imam_intrinsic_from_mesghal_750(price_irt_per_mesghal_750: Decimal) -> Decimal:
    if price_irt_per_mesghal_750 <= 0:
        raise ValueError("Standard mesghal quote must be positive")
    return price_irt_per_mesghal_750 * imam_intrinsic_coefficient()
