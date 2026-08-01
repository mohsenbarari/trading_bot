"""Strictly-prior Bayesian residual calibration for Shadow coin ranges.

The structural estimator remains the source of the primary range.  This
candidate only estimates the *residual* of prior reviewed confirmed trades
against their own recorded primary prediction.  It is deliberately small,
fully explainable, and can abstain; it must never mutate a primary artifact or
become an online self-training loop.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping

from core.market_intelligence.contracts import RateShadowPrediction


ONLINE_RESIDUAL_V1_VERSION = "ONLINE_BAYESIAN_RESIDUAL_V1_SHADOW_20260801"
MINIMUM_EFFECTIVE_SAMPLES = 3.0
PRIOR_EFFECTIVE_SAMPLES = 8.0
HALF_LIFE_HOURS = 18.0
MAX_ABSOLUTE_RESIDUAL = 0.12
MAX_CENTER_SHIFT = 0.035
MINIMUM_UNCERTAINTY = 0.0025
MAXIMUM_UNCERTAINTY = 0.0300


def _parse_utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc)


def _finite_positive(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _gated(primary: RateShadowPrediction, reason: str) -> RateShadowPrediction:
    return RateShadowPrediction(
        status="GATED_OFF",
        commodity=primary.commodity,
        settlement=primary.settlement,
        trade_form=primary.trade_form,
        center_project_price=None,
        lower_project_price=None,
        upper_project_price=None,
        confidence_label=None,
        method="ONLINE_BAYESIAN_RESIDUAL_V1_GATED_OFF",
        decision_reason=reason,
        anchor_kind=primary.anchor_kind,
        anchor_age_seconds=primary.anchor_age_seconds,
        bundle_version=ONLINE_RESIDUAL_V1_VERSION,
        feature_schema_version=primary.feature_schema_version,
        snapshot_version=primary.snapshot_version,
        evidence={},
    )


def _round_project(value: float) -> int:
    return int(round(value / 50.0) * 50)


def evaluate_online_residual_v1(
    primary: RateShadowPrediction,
    *,
    as_of_utc: datetime,
) -> RateShadowPrediction:
    """Return a range-union residual candidate or an explicit abstention.

    Only a reviewed/trusted, training-eligible confirmed trade can update this
    state.  The caller's feature store already excludes target, same-timestamp,
    and future observations; this function repeats the strict-prior check as a
    defense in depth.
    """

    if (
        primary.status != "ESTIMATED"
        or primary.center_project_price is None
        or primary.lower_project_price is None
        or primary.upper_project_price is None
        or primary.trade_form != "PHYSICAL"
    ):
        return _gated(primary, "ONLINE_RESIDUAL_PRIMARY_RANGE_UNAVAILABLE")
    try:
        cutoff = as_of_utc.astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return _gated(primary, "ONLINE_RESIDUAL_INVALID_CUTOFF")

    history = (primary.evidence or {}).get("same_market_history")
    if not isinstance(history, list):
        return _gated(primary, "ONLINE_RESIDUAL_HISTORY_UNAVAILABLE")

    weighted: list[tuple[float, float]] = []
    for item in history:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("source_kind") or "") != "CONFIRMED_TRADE":
            continue
        if str(item.get("label_status") or "").upper() not in {
            "REVIEWED",
            "TRUSTED",
        }:
            continue
        if not bool(item.get("training_eligible", False)):
            continue
        try:
            observed_at = _parse_utc(item["observed_at_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        age_seconds = (cutoff - observed_at).total_seconds()
        if age_seconds <= 0:
            continue
        actual = _finite_positive(item.get("price_project"))
        baseline = _finite_positive(item.get("baseline_project_price"))
        source_weight = _finite_positive(item.get("source_weight"))
        if actual is None or baseline is None or source_weight is None:
            continue
        residual = actual / baseline - 1.0
        if not math.isfinite(residual) or abs(residual) > MAX_ABSOLUTE_RESIDUAL:
            continue
        decay = 0.5 ** (age_seconds / 3600.0 / HALF_LIFE_HOURS)
        weight = source_weight * decay
        if math.isfinite(weight) and weight > 0:
            weighted.append((residual, weight))

    effective_samples = sum(weight for _, weight in weighted)
    if effective_samples < MINIMUM_EFFECTIVE_SAMPLES:
        return _gated(primary, "ONLINE_RESIDUAL_INSUFFICIENT_REVIEWED_TRADES")

    posterior_mean = sum(residual * weight for residual, weight in weighted) / (
        PRIOR_EFFECTIVE_SAMPLES + effective_samples
    )
    posterior_mean = max(-MAX_CENTER_SHIFT, min(MAX_CENTER_SHIFT, posterior_mean))
    weighted_variance = sum(
        weight * (residual - posterior_mean) ** 2
        for residual, weight in weighted
    ) / effective_samples
    posterior_std = math.sqrt(max(0.0, weighted_variance))
    uncertainty = max(
        MINIMUM_UNCERTAINTY,
        min(
            MAXIMUM_UNCERTAINTY,
            posterior_std + MINIMUM_UNCERTAINTY / math.sqrt(effective_samples),
        ),
    )

    center = _round_project(float(primary.center_project_price) * (1.0 + posterior_mean))
    candidate_lower = _round_project(center * (1.0 - uncertainty))
    candidate_upper = _round_project(center * (1.0 + uncertainty))
    # The candidate cannot make the production-style range narrower.  This is
    # critical while the state is sparse and residuals may shift regime.
    lower = min(int(primary.lower_project_price), candidate_lower)
    upper = max(int(primary.upper_project_price), candidate_upper)
    return RateShadowPrediction(
        status="ESTIMATED",
        commodity=primary.commodity,
        settlement=primary.settlement,
        trade_form=primary.trade_form,
        center_project_price=center,
        lower_project_price=lower,
        upper_project_price=upper,
        confidence_label=primary.confidence_label,
        method="ONLINE_BAYESIAN_RESIDUAL_V1_STRICTLY_PRIOR",
        decision_reason="ONLINE_RESIDUAL_REVIEWED_TRADE_CALIBRATION",
        anchor_kind=primary.anchor_kind,
        anchor_age_seconds=primary.anchor_age_seconds,
        bundle_version=ONLINE_RESIDUAL_V1_VERSION,
        feature_schema_version=primary.feature_schema_version,
        snapshot_version=primary.snapshot_version,
        evidence={
            "effective_reviewed_trade_weight": round(effective_samples, 6),
            "reviewed_trade_count": len(weighted),
            "posterior_residual_ratio": round(posterior_mean, 8),
            "posterior_uncertainty_ratio": round(uncertainty, 8),
            "prior_effective_samples": PRIOR_EFFECTIVE_SAMPLES,
            "half_life_hours": HALF_LIFE_HOURS,
        },
    )
