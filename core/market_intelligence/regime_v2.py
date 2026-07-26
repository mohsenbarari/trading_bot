"""Continuous, fail-closed market-regime vector for Shadow candidates."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


REGIME_V2_POLICY_VERSION = "COIN_REGIME_V2_SHADOW_20260726"


def _finite(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _bounded(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _component_vector(
    components: Sequence[Mapping[str, Any]],
) -> tuple[float, float, bool]:
    weighted = []
    for item in components:
        reliability = max(0.0, _finite(item.get("reliability")))
        direction = _bounded(
            _finite(item.get("direction_strength")),
            -1.0,
            1.0,
        )
        if reliability > 0:
            weighted.append((direction, reliability))
    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return 0.0, 0.0, False
    center = sum(value * weight for value, weight in weighted) / total
    dispersion = math.sqrt(
        sum(
            weight * (value - center) ** 2
            for value, weight in weighted
        )
        / total
    )
    agreement = _bounded(1.0 - dispersion, 0.0, 1.0)
    positive = sum(
        weight for value, weight in weighted if value >= 0.30
    )
    negative = sum(
        weight for value, weight in weighted if value <= -0.30
    )
    disagreement = positive / total >= 0.20 and negative / total >= 0.20
    return center, agreement, disagreement


def evaluate_regime_v2(
    evidence: Mapping[str, Any],
    *,
    previous_label: str | None = None,
) -> dict[str, Any]:
    """Build a continuous regime state without allowing coin flow to lead."""

    legacy = evidence.get("market_regime") or {}
    components = legacy.get("components") or ()
    if not isinstance(components, (list, tuple)):
        components = ()
    component_direction, agreement, disagreement = _component_vector(
        tuple(
            item for item in components if isinstance(item, Mapping)
        )
    )
    legacy_direction = _bounded(
        _finite(legacy.get("direction_score"), component_direction),
        -1.0,
        1.0,
    )
    legacy_confidence = _bounded(
        _finite(legacy.get("confidence")),
        0.0,
        1.0,
    )
    volatility_percent = max(
        0.0,
        _finite(legacy.get("volatility_percent")),
    )
    # A 0.30% robust ten-minute volatility is already a strong signal in
    # these markets.  Keep the raw percentage alongside the normalized score.
    volatility_score = _bounded(volatility_percent / 0.30, 0.0, 1.0)
    sources = evidence.get("sources") or {}
    observed_sources = 0
    source_samples = 0
    if isinstance(sources, Mapping):
        for value in sources.values():
            if not isinstance(value, Mapping):
                continue
            if str(value.get("status") or "") == "OBSERVED":
                observed_sources += 1
                source_samples += max(
                    0,
                    int(_finite(value.get("sample_count"))),
                )
    order_flow = evidence.get("order_flow") or {}
    flow_events = max(
        0,
        int(_finite(order_flow.get("event_count"))),
    )
    coverage = _bounded(observed_sources / 4.0, 0.0, 1.0)
    activity = _bounded((source_samples + flow_events) / 24.0, 0.0, 1.0)
    liquidity_score = 0.65 * coverage + 0.35 * activity
    direction_score = (
        0.80 * legacy_direction + 0.20 * component_direction
        if components
        else legacy_direction
    )
    confidence = _bounded(
        legacy_confidence
        * (0.55 + 0.45 * agreement)
        * (0.60 + 0.40 * coverage),
        0.0,
        1.0,
    )

    directional_strength = abs(direction_score) * confidence * agreement
    if volatility_score >= 0.75:
        label = "SHOCK"
        reason = "HIGH_REALIZED_VOLATILITY"
    elif (
        direction_score >= 0.0
        and directional_strength >= 0.22
        and not disagreement
    ):
        label = "UP"
        reason = "AGREED_POSITIVE_UNDERLYING_DIRECTION"
    elif (
        direction_score < 0.0
        and directional_strength >= 0.22
        and not disagreement
    ):
        label = "DOWN"
        reason = "AGREED_NEGATIVE_UNDERLYING_DIRECTION"
    else:
        label = "RANGE"
        reason = (
            "CROSS_SOURCE_DISAGREEMENT"
            if disagreement
            else "NO_CONFIRMED_DIRECTION"
        )

    previous = str(previous_label or "").upper()
    hysteresis_applied = False
    if previous == "UP" and label == "RANGE" and direction_score >= 0.12:
        label = "UP"
        reason = "HYSTERESIS_RETAINED_UP"
        hysteresis_applied = True
    elif (
        previous == "DOWN"
        and label == "RANGE"
        and direction_score <= -0.12
    ):
        label = "DOWN"
        reason = "HYSTERESIS_RETAINED_DOWN"
        hysteresis_applied = True
    elif (
        previous == "SHOCK"
        and label != "SHOCK"
        and volatility_score >= 0.45
    ):
        label = "SHOCK"
        reason = "HYSTERESIS_RETAINED_SHOCK"
        hysteresis_applied = True

    status = (
        "OBSERVED"
        if observed_sources > 0 and legacy_confidence > 0
        else "INSUFFICIENT"
    )
    return {
        "schema_version": REGIME_V2_POLICY_VERSION,
        "status": status,
        "label": label if status == "OBSERVED" else "UNKNOWN",
        "direction_score": _bounded(direction_score, -1.0, 1.0),
        "volatility_score": volatility_score,
        "volatility_percent": volatility_percent,
        "agreement_score": agreement,
        "liquidity_score": _bounded(liquidity_score, 0.0, 1.0),
        "confidence": confidence,
        "disagreement_flag": disagreement,
        "hysteresis_applied": hysteresis_applied,
        "previous_label": previous or None,
        "reason_codes": [reason],
        "coin_flow_role": "CONFIRM_TOLERANCE_ONLY",
    }
