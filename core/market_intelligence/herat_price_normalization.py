"""Causal normalization for abbreviated USD/Herat prices.

The public feed occasionally drops leading digits from a quote.  A parser
cannot safely repair that notation in isolation: both the abbreviated and the
literal number can be valid at different market regimes.  This module therefore
uses only a bounded, strictly-prior same-book range supplied by the ingest
layer.  It never applies a fixed additive correction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Iterable


HERAT_TEMPORAL_RANGE_VERSION = "herat-temporal-range-v1"
HERAT_LOOKBACK_SECONDS = 15 * 60
HERAT_MINIMUM_REFERENCE_COUNT = 3
HERAT_MAXIMUM_REFERENCE_COUNT = 32
_CANONICAL_LOW = Decimal("50000")
_CANONICAL_HIGH = Decimal("500000")
_ENVELOPE_RELATIVE_PADDING = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class HeratPriceNormalization:
    price: Decimal
    adjusted: bool
    reference_count: int
    reference_low: Decimal | None
    reference_high: Decimal | None
    method: str


def _price(value: Decimal | int | float | str) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        return None
    if not _CANONICAL_LOW <= parsed <= _CANONICAL_HIGH:
        return None
    return parsed


def _robust_range(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    ordered = sorted(values)
    # With five or more points, discard only the extreme tails.  This prevents
    # one previously malformed row from defining the next decision's range.
    if len(ordered) >= 5:
        trim = max(1, len(ordered) // 10)
        ordered = ordered[trim:-trim]
    center = Decimal(str(median(ordered)))
    return min(ordered), max(ordered), center


def _notation_candidates(raw: Decimal, center: Decimal) -> set[Decimal]:
    digits = len(str(abs(int(raw))))
    modulus = Decimal(10) ** digits
    bucket = int(center // modulus)
    return {
        raw + Decimal(candidate_bucket) * modulus
        for candidate_bucket in range(max(0, bucket - 2), bucket + 3)
        if _CANONICAL_LOW
        <= raw + Decimal(candidate_bucket) * modulus
        <= _CANONICAL_HIGH
    }


def normalize_herat_price(
    raw_price: Decimal | int | float | str,
    *,
    strictly_prior_same_book_prices: Iterable[Decimal | int | float | str],
) -> HeratPriceNormalization:
    """Repair a clipped quote only when the prior temporal range is decisive."""

    try:
        original = Decimal(str(raw_price))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("herat_price_invalid") from exc
    raw = _price(original)
    if raw is None:
        return HeratPriceNormalization(
            price=original,
            adjusted=False,
            reference_count=0,
            reference_low=None,
            reference_high=None,
            method="INVALID_OR_NONCANONICAL_PRICE_UNCHANGED",
        )
    references = [
        parsed
        for parsed in (_price(value) for value in strictly_prior_same_book_prices)
        if parsed is not None
    ][-HERAT_MAXIMUM_REFERENCE_COUNT:]
    if len(references) < HERAT_MINIMUM_REFERENCE_COUNT:
        return HeratPriceNormalization(
            raw, False, len(references), None, None, "INSUFFICIENT_PRIOR_RANGE"
        )

    low, high, center = _robust_range(references)
    observed_span = high - low
    padding = max(observed_span, abs(center) * _ENVELOPE_RELATIVE_PADDING)
    envelope_low = low - padding
    envelope_high = high + padding
    if envelope_low <= raw <= envelope_high:
        return HeratPriceNormalization(
            raw, False, len(references), low, high, "RAW_WITHIN_PRIOR_RANGE"
        )

    candidates = sorted(
        (
            candidate
            for candidate in _notation_candidates(raw, center)
            if envelope_low <= candidate <= envelope_high
        ),
        key=lambda candidate: (abs(candidate - center), candidate),
    )
    if not candidates:
        return HeratPriceNormalization(
            raw, False, len(references), low, high, "NO_CANDIDATE_IN_PRIOR_RANGE"
        )
    if len(candidates) > 1 and abs(candidates[0] - center) == abs(candidates[1] - center):
        return HeratPriceNormalization(
            raw, False, len(references), low, high, "AMBIGUOUS_PRIOR_RANGE_CANDIDATES"
        )
    winner = candidates[0]
    return HeratPriceNormalization(
        winner,
        winner != raw,
        len(references),
        low,
        high,
        "RECONSTRUCTED_FROM_STRICTLY_PRIOR_RANGE" if winner != raw else "RAW_WITHIN_PRIOR_RANGE",
    )
