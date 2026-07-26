"""Gated Hybrid-v2 numerical Shadow candidate.

The candidate is deliberately pure: it consumes only one immutable feature
evidence object and the current validated range.  Missing short-state history
causes an explicit abstention instead of silently reusing the primary price.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence

from core.market_intelligence.contracts import RateShadowPrediction


HYBRID_V2_POLICY_VERSION = "HYBRID_V2_RESEARCH_20260725"
ENABLED_MARKETS = frozenset({"امام/CASH/PHYSICAL"})
LIVE_ANCHOR_SECONDS = 5 * 60
K_DIRECTIONAL = 3
K_RANGE = 5
HALF_LIFE_HOURS = 24.0
ALPHA_SHORT = 0.80
ALPHA_LONG = 0.80
BASIS_WEIGHT = 0.25
MINIMUM_BASIS_PAIRS = 5


def _parse_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _weighted_median(
    values: Sequence[float],
    weights: Sequence[float],
) -> float:
    if not values or len(values) != len(weights):
        raise ValueError("weighted median requires equal non-empty inputs")
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(max(0.0, weight) for _, weight in ordered)
    if total <= 0:
        raise ValueError("weighted median requires positive weight")
    threshold = total / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += max(0.0, weight)
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


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
        method="HYBRID_V2_GATED_OFF",
        decision_reason=reason,
        anchor_kind=primary.anchor_kind,
        anchor_age_seconds=primary.anchor_age_seconds,
        bundle_version=HYBRID_V2_POLICY_VERSION,
        feature_schema_version=primary.feature_schema_version,
        snapshot_version=primary.snapshot_version,
        evidence={},
    )


def _directional(evidence: Mapping[str, Any]) -> bool:
    regime = evidence.get("market_regime") or {}
    direction = float(regime.get("direction_score") or 0.0)
    confidence = float(regime.get("confidence") or 0.0)
    volatility = float(regime.get("volatility_percent") or 0.0)
    return abs(direction) * confidence >= 0.25 or volatility >= 0.30


def evaluate_hybrid_v2(
    primary: RateShadowPrediction,
    *,
    as_of_utc: datetime,
) -> RateShadowPrediction:
    """Evaluate the frozen research policy without future or target data."""

    market = (
        f"{primary.commodity}/{primary.settlement}/{primary.trade_form}"
    )
    if market not in ENABLED_MARKETS:
        return _gated(primary, "HYBRID_V2_MARKET_NOT_VALIDATED")
    if (
        primary.status != "ESTIMATED"
        or primary.center_project_price is None
        or primary.lower_project_price is None
        or primary.upper_project_price is None
    ):
        return _gated(primary, "HYBRID_V2_PRIMARY_RANGE_UNAVAILABLE")
    if (
        primary.anchor_age_seconds is not None
        and primary.anchor_age_seconds <= LIVE_ANCHOR_SECONDS
        and primary.anchor_kind
    ):
        return RateShadowPrediction(
            status="ESTIMATED",
            commodity=primary.commodity,
            settlement=primary.settlement,
            trade_form=primary.trade_form,
            center_project_price=primary.center_project_price,
            lower_project_price=primary.lower_project_price,
            upper_project_price=primary.upper_project_price,
            confidence_label=primary.confidence_label,
            method="HYBRID_V2_SHARED_LIVE_ANCHOR",
            decision_reason="HYBRID_V2_LIVE_PATH_IDENTICAL",
            anchor_kind=primary.anchor_kind,
            anchor_age_seconds=primary.anchor_age_seconds,
            bundle_version=HYBRID_V2_POLICY_VERSION,
            feature_schema_version=primary.feature_schema_version,
            snapshot_version=primary.snapshot_version,
            evidence={},
        )

    evidence = primary.evidence or {}
    intrinsic_toman = (evidence.get("rate") or {}).get("intrinsic_toman")
    history = evidence.get("same_market_history")
    if not isinstance(history, list) or not history:
        return _gated(
            primary,
            "HYBRID_V2_REQUIRED_SAME_MARKET_HISTORY_MISSING",
        )
    try:
        intrinsic_toman = float(intrinsic_toman)
    except (TypeError, ValueError):
        return _gated(primary, "HYBRID_V2_INTRINSIC_VALUE_MISSING")
    if not math.isfinite(intrinsic_toman) or intrinsic_toman <= 0:
        return _gated(primary, "HYBRID_V2_INTRINSIC_VALUE_INVALID")

    count = K_DIRECTIONAL if _directional(evidence) else K_RANGE
    usable = []
    as_of = as_of_utc.astimezone(timezone.utc)
    for item in history:
        if not isinstance(item, Mapping):
            continue
        try:
            observed_at = _parse_time(item["observed_at_utc"])
            age_seconds = (as_of - observed_at).total_seconds()
            bubble_ratio = float(item["bubble_ratio"])
            source_weight = float(item.get("source_weight") or 1.0)
        except (KeyError, TypeError, ValueError):
            continue
        if (
            age_seconds <= 0
            or not math.isfinite(bubble_ratio)
            or not math.isfinite(source_weight)
            or source_weight <= 0
        ):
            continue
        usable.append(
            (
                observed_at,
                bubble_ratio,
                source_weight
                * 0.5
                ** (
                    age_seconds
                    / 3600.0
                    / HALF_LIFE_HOURS
                ),
            )
        )
    usable.sort(key=lambda item: item[0])
    usable = usable[-count:]
    if not usable:
        return _gated(
            primary,
            "HYBRID_V2_STRICTLY_PRIOR_HISTORY_UNAVAILABLE",
        )
    dynamic_bubble = _weighted_median(
        [item[1] for item in usable],
        [item[2] for item in usable],
    )
    dynamic_project = intrinsic_toman * (1.0 + dynamic_bubble) / 1000.0
    structural_project = float(primary.center_project_price)
    anchor_age = primary.anchor_age_seconds
    alpha = (
        ALPHA_SHORT
        if anchor_age is not None and anchor_age <= 60 * 60
        else ALPHA_LONG
    )
    components = [
        ("structural", structural_project, 1.0 - alpha),
        ("short_state", dynamic_project, alpha),
    ]
    basis = evidence.get("settlement_basis")
    if (
        isinstance(basis, Mapping)
        and int(basis.get("pair_count") or 0) >= MINIMUM_BASIS_PAIRS
    ):
        try:
            basis_project = float(basis["price_project"])
        except (KeyError, TypeError, ValueError):
            basis_project = 0.0
        if math.isfinite(basis_project) and basis_project > 0:
            components = [
                (name, value, weight * (1.0 - BASIS_WEIGHT))
                for name, value, weight in components
            ]
            components.append(
                ("settlement_basis", basis_project, BASIS_WEIGHT)
            )
    total_weight = sum(weight for _, _, weight in components)
    center = int(
        round(
            sum(value * weight for _, value, weight in components)
            / total_weight
            / 50
        )
        * 50
    )
    primary_lower_distance = (
        primary.center_project_price - primary.lower_project_price
    )
    primary_upper_distance = (
        primary.upper_project_price - primary.center_project_price
    )
    direction = float(
        (evidence.get("market_regime") or {}).get("direction_score") or 0.0
    ) * float(
        (evidence.get("market_regime") or {}).get("confidence") or 0.0
    )
    candidate_lower = center - int(
        primary_lower_distance * (1.0 + 0.5 * max(0.0, -direction))
    )
    candidate_upper = center + int(
        primary_upper_distance * (1.0 + 0.5 * max(0.0, direction))
    )
    # Candidate-union safety: Shadow may widen but never narrow the primary.
    lower = min(primary.lower_project_price, candidate_lower)
    upper = max(primary.upper_project_price, candidate_upper)
    return RateShadowPrediction(
        status="ESTIMATED",
        commodity=primary.commodity,
        settlement=primary.settlement,
        trade_form=primary.trade_form,
        center_project_price=center,
        lower_project_price=lower,
        upper_project_price=upper,
        confidence_label=primary.confidence_label,
        method="HYBRID_V2_GATED_EXPERT_BLEND",
        decision_reason="HYBRID_V2_VALIDATED_MARKET_CANDIDATE",
        anchor_kind=primary.anchor_kind,
        anchor_age_seconds=primary.anchor_age_seconds,
        bundle_version=HYBRID_V2_POLICY_VERSION,
        feature_schema_version=primary.feature_schema_version,
        snapshot_version=primary.snapshot_version,
        evidence={
            "component_weights": {
                name: weight / total_weight
                for name, _, weight in components
            },
            "dynamic_history_count": len(usable),
            "directional_state": _directional(evidence),
        },
    )
