"""Independent low-date Shadow candidate anchored to physical melted gold."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import math
import statistics
from typing import Any, Mapping

from core.market_intelligence.contracts import RateShadowPrediction


LOW_DATE_V2_VERSION = "LOW_DATE_PHYSICAL_V2_SHADOW_20260726"
COEFFICIENTS = {
    "بهار": 2.253,
    "نیم تاریخ پایین": 2.253 / 2.0,
    "ربع تاریخ پایین": 2.253 / 4.0,
}
LIVE_COIN_ANCHOR_SECONDS = 5 * 60


def _gated(
    primary: RateShadowPrediction,
    reason: str,
) -> RateShadowPrediction:
    return RateShadowPrediction(
        status="GATED_OFF",
        commodity=primary.commodity,
        settlement=primary.settlement,
        trade_form=primary.trade_form,
        center_project_price=None,
        lower_project_price=None,
        upper_project_price=None,
        confidence_label=None,
        method="LOW_DATE_PHYSICAL_V2_GATED_OFF",
        decision_reason=reason,
        anchor_kind=primary.anchor_kind,
        anchor_age_seconds=primary.anchor_age_seconds,
        bundle_version=LOW_DATE_V2_VERSION,
        feature_schema_version=primary.feature_schema_version,
        snapshot_version=primary.snapshot_version,
        evidence={},
    )


def _positive(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def evaluate_low_date_v2(
    primary: RateShadowPrediction,
    *,
    as_of_utc: datetime,
) -> RateShadowPrediction:
    del as_of_utc  # cutoff is already frozen in the evidence.
    coefficient = COEFFICIENTS.get(primary.commodity)
    if coefficient is None:
        return _gated(primary, "LOW_DATE_V2_COMMODITY_NOT_ELIGIBLE")
    if primary.trade_form != "PHYSICAL":
        return _gated(primary, "LOW_DATE_V2_REQUIRES_PHYSICAL_MARKET")
    if (
        primary.status == "ESTIMATED"
        and primary.anchor_kind
        and primary.anchor_age_seconds is not None
        and primary.anchor_age_seconds <= LIVE_COIN_ANCHOR_SECONDS
    ):
        return replace(
            primary,
            method="LOW_DATE_V2_SHARED_LIVE_COIN_ANCHOR",
            decision_reason="LOW_DATE_V2_LIVE_PATH_IDENTICAL",
            bundle_version=LOW_DATE_V2_VERSION,
            evidence={},
        )
    evidence = primary.evidence or {}
    reference = evidence.get("low_date_physical_reference") or {}
    if str(reference.get("status")) not in {"OBSERVED", "BRIDGED"}:
        return _gated(primary, "LOW_DATE_V2_PHYSICAL_MELTED_UNAVAILABLE")
    if (
        str(reference.get("trade_form")) != "PHYSICAL"
        or str(reference.get("price_unit")) != "IRT_PER_MESGHAL_750"
    ):
        return _gated(primary, "LOW_DATE_V2_REFERENCE_DIMENSION_INVALID")
    melted = _positive(reference.get("average_price_toman"))
    low_melted = _positive(reference.get("lower_price_toman")) or melted
    high_melted = _positive(reference.get("upper_price_toman")) or melted
    if melted is None or low_melted is None or high_melted is None:
        return _gated(primary, "LOW_DATE_V2_REFERENCE_PRICE_INVALID")

    history_bubbles = []
    for item in evidence.get("same_market_history") or ():
        if not isinstance(item, Mapping):
            continue
        try:
            bubble = float(item["bubble_ratio"])
            weight = float(item.get("source_weight") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(bubble) and math.isfinite(weight) and weight > 0:
            history_bubbles.extend([bubble] * max(1, min(10, round(weight * 5))))
    # Low-date coins are normally near intrinsic. History is settlement-local
    # but cannot override the physical relationship by more than three points.
    bubble = (
        max(-0.03, min(0.03, statistics.median(history_bubbles)))
        if history_bubbles
        else 0.0
    )
    center = int(round((melted * coefficient * (1.0 + bubble)) / 1000 / 50) * 50)
    uncertainty = max(
        0.006,
        min(0.03, float(reference.get("uncertainty_relative") or 0.0)),
    )
    lower = int(
        round(
            (low_melted * coefficient * (1.0 + bubble) * (1.0 - uncertainty))
            / 1000
            / 50
        )
        * 50
    )
    upper = int(
        round(
            (high_melted * coefficient * (1.0 + bubble) * (1.0 + uncertainty))
            / 1000
            / 50
        )
        * 50
    )
    lower = min(lower, center)
    upper = max(upper, center)
    return RateShadowPrediction(
        status="ESTIMATED",
        commodity=primary.commodity,
        settlement=primary.settlement,
        trade_form=primary.trade_form,
        center_project_price=center,
        lower_project_price=lower,
        upper_project_price=upper,
        confidence_label="MEDIUM" if history_bubbles else "LOW",
        method="LOW_DATE_V2_PHYSICAL_MELTED_PLUS_SETTLEMENT_HISTORY",
        decision_reason="LOW_DATE_V2_INDEPENDENT_CANDIDATE",
        anchor_kind=str(reference.get("selection") or "PHYSICAL_MELTED"),
        anchor_age_seconds=(
            int(float(reference["age_seconds"]))
            if reference.get("age_seconds") is not None
            else None
        ),
        bundle_version=LOW_DATE_V2_VERSION,
        feature_schema_version=primary.feature_schema_version,
        snapshot_version=primary.snapshot_version,
        evidence={
            "history_count": len(history_bubbles),
            "settlement_local_bubble_ratio": bubble,
            "physical_reference_status": reference.get("status"),
        },
    )
