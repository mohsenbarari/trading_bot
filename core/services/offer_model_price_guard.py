"""Deterministic offer-price outlier guard backed by the atomic coin Snapshot.

This guard is intentionally independent from the condition-language shadow
model.  It compares the already-resolved commodity against the exact published
rate interval for the offer settlement term and fails open whenever that
evidence is unavailable, malformed, stale, or unsupported.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.metrics import registry as metrics_registry
from core.market_intelligence.coin_inference import CANONICAL_COMMODITY_NAMES
from core.market_intelligence.market_contracts import normalize_utc
from core.market_intelligence.market_snapshot import (
    AtomicMarketSnapshotProvider,
    MarketSnapshotUnavailable,
)
from core.offer_settlement import normalize_settlement_type
from core.services.market_transition_service import evaluate_current_market_schedule
from models.commodity import Commodity


logger = logging.getLogger(__name__)

OFFER_MODEL_PRICE_GUARD_VERSION = "offer-model-price-guard-v2"
OFFER_MODEL_PRICE_GUARD_OPENING_WINDOW_SECONDS = 15 * 60
OFFER_MODEL_PRICE_GUARD_DEFAULT_MAXIMUM_SNAPSHOT_AGE_SECONDS = 120
# A refreshed artifact must never make old underlying market data eligible for
# a hard rejection.  This limit is evaluated at decision time by adding the
# elapsed Snapshot age to the age frozen into the rate item.
OFFER_MODEL_PRICE_GUARD_MAXIMUM_UNDERLYING_AGE_SECONDS = 120
# Older estimates may remain useful for preview, but cannot be authoritative
# enough to reject an offer beyond the established two-hour anchor horizon.
OFFER_MODEL_PRICE_GUARD_MAXIMUM_ANCHOR_AGE_SECONDS = 2 * 60 * 60
OFFER_MODEL_PRICE_GUARD_REJECTION_CONFIDENCES = frozenset({"HIGH", "MEDIUM"})

# Basis points keep all boundary arithmetic integer-only and reproducible.
OFFER_MODEL_PRICE_TOLERANCE_BPS_BY_CODE: Mapping[str, int] = {
    "IMAM": 50,
    "BAHAR": 50,
    "HALF_BAHAR": 100,
    "HALF_LOW_DATE": 100,
    "QUARTER_BAHAR": 150,
    "QUARTER_LOW_DATE": 150,
    "ONE_GRAM": 300,
}

_COMMODITY_CODE_BY_CANONICAL_NAME = {
    name: code for code, name in CANONICAL_COMMODITY_NAMES.items()
}

SELL_PRICE_OUTLIER_MESSAGE = "قیمت فروش شما بالاست؛ قیمت بهتری در بازار وجود دارد."
BUY_PRICE_OUTLIER_MESSAGE = "قیمت خرید شما پایین است؛ قیمت بهتری در بازار وجود دارد."


@dataclass(frozen=True, slots=True)
class OfferModelPriceGuardDecision:
    status: str
    reason: str
    message: str | None = None
    commodity_code: str | None = None
    settlement_term: str | None = None
    lower_project_price: int | None = None
    upper_project_price: int | None = None
    boundary_price: int | None = None
    base_tolerance_bps: int | None = None
    effective_tolerance_bps: int | None = None
    opening_window_applied: bool = False
    snapshot_generated_at_utc: str | None = None

    @property
    def allowed(self) -> bool:
        return self.status != "REJECTED"


def _abstain(reason: str, **values: Any) -> OfferModelPriceGuardDecision:
    return OfferModelPriceGuardDecision(status="ABSTAINED", reason=reason, **values)


def _observe(decision: OfferModelPriceGuardDecision) -> OfferModelPriceGuardDecision:
    """Expose every enabled guard outcome with bounded, non-personal labels."""

    metrics_registry.counter(
        "trading_bot_offer_model_price_guard_decisions_total",
        "Offer model-price guard outcomes, including fail-open abstentions.",
        status=decision.status,
        reason=decision.reason,
        commodity=decision.commodity_code or "unknown",
        settlement=decision.settlement_term or "unknown",
    )
    return decision


def _utc(value: datetime | str, *, field_name: str) -> datetime:
    normalized = normalize_utc(value, field_name=field_name)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _opening_window_applies(
    *,
    now_utc: datetime,
    market_opened_at: datetime | None,
) -> bool:
    if market_opened_at is None or market_opened_at.tzinfo is None:
        return False
    opened_utc = market_opened_at.astimezone(timezone.utc)
    elapsed = (now_utc - opened_utc).total_seconds()
    return 0 <= elapsed < OFFER_MODEL_PRICE_GUARD_OPENING_WINDOW_SECONDS


def _find_rate_item(
    snapshot: Mapping[str, Any],
    *,
    commodity_code: str,
    settlement_term: str,
) -> Mapping[str, Any] | None:
    rates = snapshot.get("rates")
    if not isinstance(rates, Mapping):
        return None
    for item in rates.get("items") or ():
        if not isinstance(item, Mapping):
            continue
        if (
            item.get("status") == "ESTIMATED"
            and str(item.get("commodity_code") or "") == commodity_code
            and str(item.get("settlement_term") or "").upper() == settlement_term
        ):
            return item
    return None


def evaluate_offer_model_price_snapshot(
    snapshot: Mapping[str, Any],
    *,
    commodity_name: str,
    settlement_type: object,
    offer_type: str,
    proposed_price: int,
    now_utc: datetime,
    market_opened_at: datetime | None,
    maximum_snapshot_age_seconds: int = OFFER_MODEL_PRICE_GUARD_DEFAULT_MAXIMUM_SNAPSHOT_AGE_SECONDS,
    maximum_anchor_age_seconds: int = OFFER_MODEL_PRICE_GUARD_MAXIMUM_ANCHOR_AGE_SECONDS,
    maximum_underlying_age_seconds: int = OFFER_MODEL_PRICE_GUARD_MAXIMUM_UNDERLYING_AGE_SECONDS,
) -> OfferModelPriceGuardDecision:
    """Evaluate one offer against the exact interval in one validated Snapshot."""

    commodity_code = _COMMODITY_CODE_BY_CANONICAL_NAME.get(str(commodity_name or "").strip())
    if commodity_code not in OFFER_MODEL_PRICE_TOLERANCE_BPS_BY_CODE:
        return _abstain("COMMODITY_UNSUPPORTED")

    normalized_offer_type = str(offer_type or "").strip().lower()
    if normalized_offer_type not in {"buy", "sell"}:
        return _abstain("OFFER_TYPE_UNSUPPORTED", commodity_code=commodity_code)
    try:
        normalized_price = int(proposed_price)
    except (TypeError, ValueError):
        return _abstain("PRICE_INVALID", commodity_code=commodity_code)
    if normalized_price <= 0:
        return _abstain("PRICE_INVALID", commodity_code=commodity_code)

    if now_utc.tzinfo is None:
        return _abstain("NOW_UTC_REQUIRED", commodity_code=commodity_code)
    normalized_now = now_utc.astimezone(timezone.utc)
    try:
        generated_at = _utc(
            str(snapshot.get("generated_at_utc") or ""),
            field_name="offer_model_price_guard_snapshot_generated_at_utc",
        )
    except (TypeError, ValueError):
        return _abstain("SNAPSHOT_TIME_INVALID", commodity_code=commodity_code)
    maximum_age = max(1, int(maximum_snapshot_age_seconds))
    snapshot_age = (normalized_now - generated_at).total_seconds()
    if snapshot_age < 0 or snapshot_age > maximum_age:
        return _abstain(
            "SNAPSHOT_STALE_OR_FUTURE",
            commodity_code=commodity_code,
            snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
        )

    settlement_term = (
        "TOMORROW"
        if normalize_settlement_type(settlement_type).value == "tomorrow"
        else "CASH"
    )
    rate_item = _find_rate_item(
        snapshot,
        commodity_code=commodity_code,
        settlement_term=settlement_term,
    )
    if rate_item is None:
        return _abstain(
            "MODEL_RANGE_UNAVAILABLE",
            commodity_code=commodity_code,
            settlement_term=settlement_term,
            snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
        )

    confidence = str(rate_item.get("confidence") or "").strip().upper()
    if confidence not in OFFER_MODEL_PRICE_GUARD_REJECTION_CONFIDENCES:
        return _abstain(
            "MODEL_EVIDENCE_CONFIDENCE_UNSAFE",
            commodity_code=commodity_code,
            settlement_term=settlement_term,
            snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
        )

    underlying_age_raw = rate_item.get("underlying_age_seconds")
    if (
        isinstance(underlying_age_raw, bool)
        or not isinstance(underlying_age_raw, (int, float))
    ):
        return _abstain(
            "MODEL_UNDERLYING_AGE_UNAVAILABLE",
            commodity_code=commodity_code,
            settlement_term=settlement_term,
            snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
        )
    underlying_age = float(underlying_age_raw)
    if not math.isfinite(underlying_age) or underlying_age < 0:
        return _abstain(
            "MODEL_UNDERLYING_AGE_INVALID",
            commodity_code=commodity_code,
            settlement_term=settlement_term,
            snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
        )
    maximum_underlying_age = max(1, int(maximum_underlying_age_seconds))
    if underlying_age + max(0.0, snapshot_age) > maximum_underlying_age:
        return _abstain(
            "MODEL_UNDERLYING_STALE",
            commodity_code=commodity_code,
            settlement_term=settlement_term,
            snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
        )

    anchor_age_raw = rate_item.get("anchor_age_seconds")
    if anchor_age_raw is None and confidence == "MEDIUM":
        anchor_age = None
    elif isinstance(anchor_age_raw, bool) or not isinstance(anchor_age_raw, (int, float)):
        return _abstain(
            "MODEL_ANCHOR_AGE_UNAVAILABLE",
            commodity_code=commodity_code,
            settlement_term=settlement_term,
            snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
        )
    else:
        anchor_age = float(anchor_age_raw)
    if anchor_age is not None and (not math.isfinite(anchor_age) or anchor_age < 0):
        return _abstain(
            "MODEL_ANCHOR_AGE_INVALID",
            commodity_code=commodity_code,
            settlement_term=settlement_term,
            snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
        )
    maximum_anchor_age = max(1, int(maximum_anchor_age_seconds))
    # anchor_age_seconds is frozen when the Snapshot is generated.  Include
    # the artifact's own elapsed age so the rejection decision uses age now.
    effective_anchor_age = (
        anchor_age + max(0.0, snapshot_age)
        if anchor_age is not None
        else None
    )
    if effective_anchor_age is not None and effective_anchor_age > maximum_anchor_age:
        return _abstain(
            "MODEL_ANCHOR_STALE",
            commodity_code=commodity_code,
            settlement_term=settlement_term,
            snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
        )

    lower = rate_item.get("lower_project_price")
    upper = rate_item.get("upper_project_price")
    if (
        not isinstance(lower, int)
        or isinstance(lower, bool)
        or not isinstance(upper, int)
        or isinstance(upper, bool)
        or lower <= 0
        or upper < lower
    ):
        return _abstain(
            "MODEL_RANGE_INVALID",
            commodity_code=commodity_code,
            settlement_term=settlement_term,
        )

    base_tolerance_bps = OFFER_MODEL_PRICE_TOLERANCE_BPS_BY_CODE[commodity_code]
    opening_window = _opening_window_applies(
        now_utc=normalized_now,
        market_opened_at=market_opened_at,
    )
    effective_tolerance_bps = base_tolerance_bps * (2 if opening_window else 1)

    if normalized_offer_type == "sell":
        boundary = upper * (10_000 + effective_tolerance_bps) // 10_000
        rejected = normalized_price > boundary
        message = SELL_PRICE_OUTLIER_MESSAGE if rejected else None
    else:
        # Ceiling division: prices below the exact percentage boundary reject.
        numerator = lower * (10_000 - effective_tolerance_bps)
        boundary = (numerator + 9_999) // 10_000
        rejected = normalized_price < boundary
        message = BUY_PRICE_OUTLIER_MESSAGE if rejected else None

    return OfferModelPriceGuardDecision(
        status="REJECTED" if rejected else "ALLOWED",
        reason="PRICE_OUTSIDE_MODEL_RANGE" if rejected else "PRICE_WITHIN_MODEL_RANGE",
        message=message,
        commodity_code=commodity_code,
        settlement_term=settlement_term,
        lower_project_price=lower,
        upper_project_price=upper,
        boundary_price=boundary,
        base_tolerance_bps=base_tolerance_bps,
        effective_tolerance_bps=effective_tolerance_bps,
        opening_window_applied=opening_window,
        snapshot_generated_at_utc=str(snapshot.get("generated_at_utc") or "") or None,
    )


async def evaluate_offer_model_price_guard(
    db: AsyncSession,
    *,
    commodity_id: int,
    settlement_type: object,
    offer_type: str,
    proposed_price: int,
    market_evaluation: object | None = None,
    now_utc: datetime | None = None,
) -> OfferModelPriceGuardDecision:
    """Load the current catalog/Snapshot and fail open unless both are safe."""

    if not bool(getattr(settings, "offer_model_price_guard_enabled", False)):
        return _abstain("FEATURE_DISABLED")
    snapshot_path = str(
        getattr(settings, "coin_intelligence_inference_snapshot_path", "") or ""
    ).strip()
    if not snapshot_path:
        return _observe(_abstain("SNAPSHOT_PATH_UNCONFIGURED"))

    commodity = await db.get(Commodity, int(commodity_id))
    if commodity is None:
        return _observe(_abstain("COMMODITY_NOT_FOUND"))
    try:
        snapshot = AtomicMarketSnapshotProvider(snapshot_path).load()
    except MarketSnapshotUnavailable:
        return _observe(_abstain("SNAPSHOT_UNAVAILABLE"))

    current_time = now_utc or datetime.now(timezone.utc)
    evaluation = market_evaluation
    if evaluation is None:
        try:
            evaluation = await evaluate_current_market_schedule(
                db,
                current_time=current_time,
            )
        except Exception:
            logger.warning(
                "Offer model-price guard could not resolve the market schedule",
                exc_info=True,
            )
            return _observe(_abstain("MARKET_SCHEDULE_UNAVAILABLE"))
    maximum_age = int(
        getattr(
            settings,
            "offer_model_price_guard_max_snapshot_age_seconds",
            OFFER_MODEL_PRICE_GUARD_DEFAULT_MAXIMUM_SNAPSHOT_AGE_SECONDS,
        )
    )
    market_opened_at = None
    if bool(getattr(evaluation, "is_open", False)):
        market_opened_at = getattr(evaluation, "current_transition_at", None)

    return _observe(
        evaluate_offer_model_price_snapshot(
            snapshot,
            commodity_name=str(getattr(commodity, "name", "") or ""),
            settlement_type=settlement_type,
            offer_type=offer_type,
            proposed_price=proposed_price,
            now_utc=current_time,
            market_opened_at=market_opened_at,
            maximum_snapshot_age_seconds=maximum_age,
        )
    )


__all__ = [
    "BUY_PRICE_OUTLIER_MESSAGE",
    "OFFER_MODEL_PRICE_GUARD_DEFAULT_MAXIMUM_SNAPSHOT_AGE_SECONDS",
    "OFFER_MODEL_PRICE_GUARD_MAXIMUM_ANCHOR_AGE_SECONDS",
    "OFFER_MODEL_PRICE_GUARD_MAXIMUM_UNDERLYING_AGE_SECONDS",
    "OFFER_MODEL_PRICE_GUARD_OPENING_WINDOW_SECONDS",
    "OFFER_MODEL_PRICE_GUARD_REJECTION_CONFIDENCES",
    "OFFER_MODEL_PRICE_GUARD_VERSION",
    "OFFER_MODEL_PRICE_TOLERANCE_BPS_BY_CODE",
    "OfferModelPriceGuardDecision",
    "SELL_PRICE_OUTLIER_MESSAGE",
    "evaluate_offer_model_price_guard",
    "evaluate_offer_model_price_snapshot",
]
