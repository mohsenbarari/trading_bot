"""Deterministic safety finalization shared by live and shadow books.

This module intentionally excludes learned residual calibration.  Applying the
main model's residual state to a challenger would leak information and make a
shadow comparison meaningless.  It contains only invariant-preserving rules
which every published estimate must satisfy.
"""

from __future__ import annotations

from typing import Any

from coin_estimator import (
    PRICE_MULTIPLIER,
    apply_low_date_family_band_separation,
    enforce_cash_tomorrow_term_structure,
)


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _ensure_tolerance_contains_point(rate: dict[str, Any]) -> bool:
    """Widen, never narrow, an existing band to include its published point.

    This individual rule only ever moves a boundary outward.  The low-date
    family rule applied alongside it is the one place that deliberately
    narrows — see ``finalize_deterministic_book``.
    """

    point = _finite_positive(rate.get("estimated_price_toman"))
    tolerance = rate.get("tolerance")
    if point is None or not isinstance(tolerance, dict):
        return False
    lower = _finite_positive(tolerance.get("lower_price_toman"))
    upper = _finite_positive(tolerance.get("upper_price_toman"))
    if lower is None or upper is None:
        return False
    changed = False
    if point < lower:
        tolerance["lower_price_toman"] = int(round(point))
        changed = True
    if point > upper:
        tolerance["upper_price_toman"] = int(round(point))
        changed = True
    if changed:
        lower_final = int(tolerance["lower_price_toman"])
        upper_final = int(tolerance["upper_price_toman"])
        tolerance["lower_project_price"] = int(round(lower_final / PRICE_MULTIPLIER))
        tolerance["upper_project_price"] = int(round(upper_final / PRICE_MULTIPLIER))
    return changed


def finalize_deterministic_book(estimate: dict[str, Any]) -> dict[str, Any]:
    """Apply non-learned book invariants and return an audit summary.

    The function mutates only the supplied in-memory estimate.  It does not
    touch databases and does not update residual state.

    Two of the three rules only ever widen a band.  ``low-date family
    separation`` is the deliberate exception: a low-date band that overlaps its
    non-low-date sibling is clamped back below it, which narrows that band on
    purpose.  Overlap would assert that بهار can be worth as much as امام,
    which is a stronger and more misleading claim than a tighter interval.  The
    narrowing is bounded by the sibling's own point estimate, never below it.
    """

    settlements = estimate.get("settlements")
    if not isinstance(settlements, dict):
        return {"term_structure_fixes": [], "low_date_rows": 0, "band_widened": 0}

    term_structure_fixes = enforce_cash_tomorrow_term_structure(settlements)
    low_date_rows = 0
    band_widened = 0
    for payload in settlements.values():
        if not isinstance(payload, dict):
            continue
        rates = payload.get("rates")
        if not isinstance(rates, list):
            continue
        finalized = apply_low_date_family_band_separation(rates)
        payload["rates"] = finalized
        low_date_rows += len(finalized)
        for rate in finalized:
            if isinstance(rate, dict) and _ensure_tolerance_contains_point(rate):
                band_widened += 1
    return {
        "term_structure_fixes": term_structure_fixes,
        "low_date_rows": low_date_rows,
        "band_widened": band_widened,
    }
