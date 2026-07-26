"""Versioned project-offer quality and review policy for Shadow evidence."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


QUALITY_POLICY_VERSION = "COIN_OFFER_QUALITY_V2_SHADOW_20260726"
REVIEW_ACTIONS = frozenset(
    {
        "ACCEPT_ORIGINAL",
        "ACCEPT_CORRECTION",
        "REJECT_LABEL",
        "KEEP_UNREVIEWED",
        "AMBIGUOUS",
    }
)


@dataclass(frozen=True, slots=True)
class QualityDecision:
    decision: str
    reason_codes: tuple[str, ...]
    realtime_weight: float
    training_weight: float
    review_required: bool
    context: Mapping[str, Any]


def _positive(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def evaluate_offer_quality(
    *,
    side: str,
    price_project: int,
    lowest_active_buy: int | None,
    highest_active_sell: int | None,
    regime_v2: Mapping[str, Any],
    structural_reference_project: int | None = None,
) -> QualityDecision:
    """Apply the user's outer-extreme rule at the immutable offer cutoff.

    The relevant comparisons are deliberately *lowest* buy and *highest*
    sell—not best bid/ask. Coin order flow cannot authorize an exception.
    """

    side = str(side).upper()
    price = _positive(price_project)
    if side not in {"BUY", "SELL"} or price is None:
        return QualityDecision(
            decision="EXCLUDE",
            reason_codes=("INVALID_NORMALIZED_OFFER",),
            realtime_weight=0.0,
            training_weight=0.0,
            review_required=True,
            context={"policy_version": QUALITY_POLICY_VERSION},
        )
    lowest_buy = _positive(lowest_active_buy)
    highest_sell = _positive(highest_active_sell)
    outer_low_sell = (
        side == "SELL" and lowest_buy is not None and price < lowest_buy
    )
    outer_high_buy = (
        side == "BUY" and highest_sell is not None and price > highest_sell
    )
    regime_label = str(regime_v2.get("label") or "UNKNOWN").upper()
    direction = float(regime_v2.get("direction_score") or 0.0)
    confidence = float(regime_v2.get("confidence") or 0.0)
    agreement = float(regime_v2.get("agreement_score") or 0.0)
    independent_down = (
        regime_label in {"DOWN", "SHOCK"}
        and direction <= -0.35
        and confidence >= 0.55
        and agreement >= 0.60
        and not bool(regime_v2.get("disagreement_flag"))
    )
    independent_up = (
        regime_label in {"UP", "SHOCK"}
        and direction >= 0.35
        and confidence >= 0.55
        and agreement >= 0.60
        and not bool(regime_v2.get("disagreement_flag"))
    )
    directional_exception = (
        outer_low_sell and independent_down
    ) or (outer_high_buy and independent_up)

    reason_codes = []
    if outer_low_sell:
        reason_codes.append("SELL_BELOW_LOWEST_ACTIVE_BUY")
    if outer_high_buy:
        reason_codes.append("BUY_ABOVE_HIGHEST_ACTIVE_SELL")
    if (outer_low_sell or outer_high_buy) and not directional_exception:
        reason_codes.append("NO_INDEPENDENT_DIRECTIONAL_CONFIRMATION")
        return QualityDecision(
            decision="EXCLUDE",
            reason_codes=tuple(reason_codes),
            realtime_weight=0.0,
            training_weight=0.0,
            review_required=False,
            context={
                "policy_version": QUALITY_POLICY_VERSION,
                "outer_extreme": True,
                "directional_exception": False,
                "regime_label": regime_label,
            },
        )

    reference = _positive(structural_reference_project)
    discontinuity = (
        abs(price / reference - 1.0) if reference is not None else None
    )
    # Conservative research quarantine only. It does not correct a price and
    # cannot promote a label.
    if discontinuity is not None and discontinuity >= 0.06:
        reason_codes.append("PRICE_DISCONTINUITY_AT_LEAST_6_PERCENT")
        return QualityDecision(
            decision="REVIEW_REQUIRED",
            reason_codes=tuple(reason_codes),
            realtime_weight=0.0,
            training_weight=0.0,
            review_required=True,
            context={
                "policy_version": QUALITY_POLICY_VERSION,
                "outer_extreme": outer_low_sell or outer_high_buy,
                "directional_exception": directional_exception,
                "discontinuity_ratio": round(discontinuity, 6),
                "regime_label": regime_label,
            },
        )

    if directional_exception:
        reason_codes.append("INDEPENDENT_UNDERLYING_DIRECTION_CONFIRMED")
    else:
        reason_codes.append("QUALITY_GATE_PASSED")
    return QualityDecision(
        decision="INCLUDE_SHADOW",
        reason_codes=tuple(reason_codes),
        realtime_weight=0.70 if directional_exception else 1.0,
        # Project data remains unreviewed and cannot become training evidence.
        training_weight=0.35 if directional_exception else 0.50,
        review_required=False,
        context={
            "policy_version": QUALITY_POLICY_VERSION,
            "outer_extreme": outer_low_sell or outer_high_buy,
            "directional_exception": directional_exception,
            "discontinuity_ratio": (
                round(discontinuity, 6)
                if discontinuity is not None
                else None
            ),
            "regime_label": regime_label,
        },
    )
