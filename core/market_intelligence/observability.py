"""Low-cardinality metrics and privacy-bounded logs for Shadow decisions."""

from __future__ import annotations

import logging

from core.market_intelligence.contracts import (
    SUPPORTED_SHADOW_OUTCOMES,
    ShadowObservation,
)
from core.metrics import registry


logger = logging.getLogger(__name__)

COMMODITY_METRIC_KEYS = {
    "امام": "imam",
    "بهار": "bahar_low_date",
    "نیم بهار": "half_bahar",
    "نیم تاریخ پایین": "half_low_date",
    "ربع بهار": "quarter_bahar",
    "ربع تاریخ پایین": "quarter_low_date",
    "یک گرمی": "one_gram",
}


def _commodity_metric_key(value: str | None) -> str:
    if value is None:
        return "none"
    return COMMODITY_METRIC_KEYS.get(value, "other")


def record_shadow_observation(observation: ShadowObservation) -> None:
    status = (
        observation.status
        if observation.status in SUPPORTED_SHADOW_OUTCOMES
        else "RUNTIME_ERROR"
    )
    if observation.agrees_with_current is True:
        comparison = "agree"
    elif observation.agrees_with_current is False:
        comparison = "disagree"
    else:
        comparison = "unavailable"
    current_commodity = _commodity_metric_key(
        observation.current_commodity
    )
    inferred_commodity = _commodity_metric_key(
        observation.inferred_commodity
    )
    registry.counter(
        "trading_bot_coin_intelligence_shadow_observations_total",
        "Implicit-commodity offer observations by bounded Shadow outcome.",
        status=status,
        settlement=observation.settlement,
        comparison=comparison,
        current_commodity=current_commodity,
        inferred_commodity=inferred_commodity,
    )
    logger.info(
        "Coin commodity inference observed in Shadow mode",
        extra={
            "event": "coin_intelligence.shadow_observed",
            "shadow_status": status,
            "settlement": observation.settlement,
            "comparison": comparison,
            "current_commodity": current_commodity,
            "inferred_commodity": inferred_commodity,
            "requires_user_confirmation": (
                observation.requires_user_confirmation
            ),
            "bundle_version": observation.bundle_version,
        },
    )
