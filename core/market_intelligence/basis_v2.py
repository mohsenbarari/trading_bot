"""Strictly-prior cash/tomorrow basis candidate for premium coins."""

from __future__ import annotations

from datetime import datetime
import math

from core.market_intelligence.contracts import RateShadowPrediction


BASIS_V2_VERSION = "CASH_TOMORROW_BASIS_V2_SHADOW_20260726"
PREMIUM_COINS = frozenset(
    {"امام", "نیم بهار", "ربع بهار", "یک گرمی"}
)
MINIMUM_PAIRS = 5
LIVE_ANCHOR_SECONDS = 5 * 60


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
        method="CASH_TOMORROW_BASIS_V2_GATED_OFF",
        decision_reason=reason,
        anchor_kind=primary.anchor_kind,
        anchor_age_seconds=primary.anchor_age_seconds,
        bundle_version=BASIS_V2_VERSION,
        feature_schema_version=primary.feature_schema_version,
        snapshot_version=primary.snapshot_version,
        evidence={},
    )


def evaluate_basis_v2(
    primary: RateShadowPrediction,
    *,
    as_of_utc: datetime,
) -> RateShadowPrediction:
    del as_of_utc
    if primary.commodity not in PREMIUM_COINS:
        return _gated(primary, "BASIS_V2_COMMODITY_NOT_ELIGIBLE")
    if primary.trade_form != "PHYSICAL":
        return _gated(primary, "BASIS_V2_REQUIRES_PHYSICAL_MARKET")
    if (
        primary.anchor_kind
        and primary.anchor_age_seconds is not None
        and primary.anchor_age_seconds <= LIVE_ANCHOR_SECONDS
    ):
        return _gated(primary, "BASIS_V2_FRESH_SAME_MARKET_ANCHOR_EXISTS")
    basis = (primary.evidence or {}).get("settlement_basis") or {}
    try:
        price = float(basis.get("price_project"))
        pair_count = int(basis.get("pair_count") or 0)
    except (TypeError, ValueError):
        price = 0.0
        pair_count = 0
    if (
        str(basis.get("status")) != "OBSERVED"
        or pair_count < MINIMUM_PAIRS
        or not math.isfinite(price)
        or price <= 0
    ):
        return _gated(primary, "BASIS_V2_STRICTLY_PRIOR_PAIRS_UNAVAILABLE")
    center = int(round(price / 50) * 50)
    if (
        primary.center_project_price is not None
        and primary.lower_project_price is not None
        and primary.upper_project_price is not None
    ):
        half_width = max(
            250,
            int(
                (
                    primary.upper_project_price
                    - primary.lower_project_price
                )
                / 2
            ),
        )
    else:
        half_width = max(250, int(center * 0.012))
    return RateShadowPrediction(
        status="ESTIMATED",
        commodity=primary.commodity,
        settlement=primary.settlement,
        trade_form=primary.trade_form,
        center_project_price=center,
        lower_project_price=max(50, center - half_width),
        upper_project_price=center + half_width,
        confidence_label="LOW",
        method="CASH_TOMORROW_BASIS_V2_STRICTLY_PRIOR",
        decision_reason="BASIS_V2_INDEPENDENT_CANDIDATE",
        anchor_kind="STRICTLY_PRIOR_SETTLEMENT_BASIS",
        anchor_age_seconds=None,
        bundle_version=BASIS_V2_VERSION,
        feature_schema_version=primary.feature_schema_version,
        snapshot_version=primary.snapshot_version,
        evidence={
            "pair_count": pair_count,
            "counterpart_settlement": basis.get(
                "counterpart_settlement"
            ),
        },
    )
