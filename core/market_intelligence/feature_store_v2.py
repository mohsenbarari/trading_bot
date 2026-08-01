"""Strictly-prior feature history read from the local Shadow ledger."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import statistics
from typing import Any, Mapping, Sequence

from sqlalchemy import select

from core.db import AsyncSessionLocal
from models.coin_intelligence_shadow import (
    CoinIntelligenceShadowFeatureSnapshot,
    CoinIntelligenceShadowOutcome,
    CoinIntelligenceShadowPrediction,
    CoinIntelligenceShadowQualityDecision,
    CoinIntelligenceShadowRun,
)


HISTORY_POLICY_VERSION = "COIN_STRICTLY_PRIOR_HISTORY_V2_20260726"
MAX_QUERY_ROWS = 160
MAX_PAIR_GAP_SECONDS = 15 * 60
MINIMUM_BASIS_PAIRS = 5


def live_offer_age_weight(age_seconds: float) -> float:
    """Five-minute live influence: 1 -> 1/3, then no realtime authority."""

    age = max(0.0, float(age_seconds))
    if age >= 5 * 60:
        return 0.0
    return 1.0 - (2.0 / 3.0) * (age / (5 * 60))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("history cutoff requires timezone")
    return value.astimezone(timezone.utc)


def _finite_positive(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _observation_from_row(row: Mapping[str, Any], cutoff: datetime) -> dict | None:
    observed_at = row.get("run_as_of_utc")
    if not isinstance(observed_at, datetime):
        return None
    observed_at = _utc(observed_at)
    if observed_at >= cutoff:
        return None
    quality_weight = _finite_positive(row.get("quality_realtime_weight"))
    if (
        row.get("quality_realtime_weight") is not None
        and quality_weight is None
    ):
        return None
    training_quality_weight = _finite_positive(
        row.get("quality_training_weight")
    )
    training_quality_allowed = (
        row.get("quality_training_weight") is None
        or training_quality_weight is not None
    )

    outcome_at = row.get("outcome_occurred_at_utc")
    outcome_price = _finite_positive(row.get("outcome_price"))
    baseline_project_price = _finite_positive(
        row.get("primary_center_project_price")
    )
    outcome_is_prior = (
        isinstance(outcome_at, datetime)
        and _utc(outcome_at) < cutoff
        and outcome_price is not None
    )
    if outcome_is_prior:
        observed_at = _utc(outcome_at)
        label = str(row.get("label_status") or "UNREVIEWED").upper()
        base_weight = 1.50 if label in {"REVIEWED", "TRUSTED"} else 1.25
        price = outcome_price
        source_kind = "CONFIRMED_TRADE"
    else:
        price = _finite_positive(row.get("offer_price"))
        if price is None:
            return None
        label = "UNREVIEWED"
        age_weight = live_offer_age_weight(
            (cutoff - observed_at).total_seconds()
        )
        if age_weight <= 0:
            # The immutable row remains available to reviewed offline
            # training, but is absent from the live feature snapshot.
            return None
        base_weight = age_weight
        source_kind = "UNREVIEWED_OFFER"

    intrinsic = _finite_positive(row.get("intrinsic_toman"))
    if intrinsic is None:
        return None
    bubble_ratio = (price * 1000.0 / intrinsic) - 1.0
    if not math.isfinite(bubble_ratio):
        return None
    return {
        "observed_at_utc": observed_at,
        "settlement": str(row.get("settlement") or "").upper(),
        "price_project": price,
        # This is the strictly-prior primary prediction recorded with the
        # observation.  It lets a residual calibrator learn only prediction
        # error, never a target price directly.
        "baseline_project_price": baseline_project_price,
        "bubble_ratio": bubble_ratio,
        "source_weight": base_weight * (quality_weight or 1.0),
        "source_kind": source_kind,
        "label_status": label,
        "training_eligible": bool(row.get("training_eligible", False))
        and label in {"REVIEWED", "TRUSTED"}
        and training_quality_allowed,
        "previous_regime_label": row.get("regime_label"),
    }


def derive_feature_context_v2(
    rows: Sequence[Mapping[str, Any]],
    *,
    cutoff_utc: datetime,
    target_settlement: str,
) -> dict[str, Any]:
    """Build history and paired basis without predictions-as-labels."""

    cutoff = _utc(cutoff_utc)
    observations = [
        value
        for row in rows
        if (value := _observation_from_row(row, cutoff)) is not None
    ]
    observations.sort(key=lambda item: item["observed_at_utc"])
    settlement = str(target_settlement).upper()
    same_market = [
        {
            **item,
            "observed_at_utc": item["observed_at_utc"].isoformat(),
        }
        for item in observations
        if item["settlement"] == settlement
    ][-20:]
    previous_regime = next(
        (
            str(item["previous_regime_label"])
            for item in reversed(observations)
            if item["settlement"] == settlement
            and item.get("previous_regime_label")
        ),
        None,
    )

    cash = [item for item in observations if item["settlement"] == "CASH"]
    tomorrow = [
        item for item in observations if item["settlement"] == "TOMORROW"
    ]
    ratios = []
    used_tomorrow: set[int] = set()
    for cash_item in cash:
        candidates = [
            (index, item)
            for index, item in enumerate(tomorrow)
            if index not in used_tomorrow
            and abs(
                (
                    item["observed_at_utc"]
                    - cash_item["observed_at_utc"]
                ).total_seconds()
            )
            <= MAX_PAIR_GAP_SECONDS
        ]
        if not candidates:
            continue
        nearest_index, nearest = min(
            candidates,
            key=lambda pair: abs(
                (
                    pair[1]["observed_at_utc"]
                    - cash_item["observed_at_utc"]
                ).total_seconds()
            ),
        )
        used_tomorrow.add(nearest_index)
        ratios.append(
            float(cash_item["price_project"])
            / float(nearest["price_project"])
        )
    if len(ratios) < MINIMUM_BASIS_PAIRS or not cash or not tomorrow:
        basis = {
            "status": "NO_DATA",
            "reason": "INSUFFICIENT_STRICTLY_PRIOR_PAIRED_OBSERVATIONS",
            "pair_count": len(ratios),
            "policy_version": HISTORY_POLICY_VERSION,
        }
    else:
        cash_to_tomorrow = statistics.median(ratios)
        counterpart = tomorrow[-1] if settlement == "CASH" else cash[-1]
        price = (
            counterpart["price_project"] * cash_to_tomorrow
            if settlement == "CASH"
            else counterpart["price_project"] / cash_to_tomorrow
        )
        basis = {
            "status": "OBSERVED",
            "as_of_utc": counterpart["observed_at_utc"].isoformat(),
            "pair_count": len(ratios),
            "price_project": int(round(price / 50.0) * 50),
            "cash_to_tomorrow_ratio": cash_to_tomorrow,
            "counterpart_settlement": counterpart["settlement"],
            "policy_version": HISTORY_POLICY_VERSION,
        }
    return {
        "same_market_history": same_market,
        "settlement_basis": basis,
        "previous_regime_label": previous_regime,
        "policy_version": HISTORY_POLICY_VERSION,
    }


async def load_feature_context_v2(
    *,
    commodity_id: int,
    settlement: str,
    trade_form: str,
    cutoff_utc: datetime,
    session_factory=AsyncSessionLocal,
) -> dict[str, Any]:
    """Read compact evidence whose prediction/outcome times precede cutoff."""

    cutoff = _utc(cutoff_utc)
    async with session_factory() as session:
        statement = (
            select(
                CoinIntelligenceShadowRun.as_of_utc,
                CoinIntelligenceShadowRun.training_eligible,
                CoinIntelligenceShadowPrediction.settlement,
                CoinIntelligenceShadowPrediction.center_project_price.label(
                    "primary_center_project_price"
                ),
                CoinIntelligenceShadowPrediction.diagnostics,
                CoinIntelligenceShadowFeatureSnapshot.features,
                CoinIntelligenceShadowOutcome.actual_project_price,
                CoinIntelligenceShadowOutcome.occurred_at_utc,
                CoinIntelligenceShadowOutcome.label_status,
                CoinIntelligenceShadowQualityDecision.realtime_weight,
                CoinIntelligenceShadowQualityDecision.training_weight,
            )
            .join(
                CoinIntelligenceShadowPrediction,
                CoinIntelligenceShadowPrediction.run_id
                == CoinIntelligenceShadowRun.id,
            )
            .join(
                CoinIntelligenceShadowFeatureSnapshot,
                CoinIntelligenceShadowFeatureSnapshot.run_id
                == CoinIntelligenceShadowRun.id,
            )
            .outerjoin(
                CoinIntelligenceShadowOutcome,
                CoinIntelligenceShadowOutcome.prediction_id
                == CoinIntelligenceShadowPrediction.id,
            )
            .outerjoin(
                CoinIntelligenceShadowQualityDecision,
                CoinIntelligenceShadowQualityDecision.run_id
                == CoinIntelligenceShadowRun.id,
            )
            .where(
                CoinIntelligenceShadowRun.as_of_utc < cutoff,
                CoinIntelligenceShadowPrediction.model_role
                == "PRIMARY_SHADOW",
                CoinIntelligenceShadowPrediction.commodity_id
                == int(commodity_id),
                CoinIntelligenceShadowPrediction.trade_form
                == str(trade_form).upper(),
                CoinIntelligenceShadowPrediction.settlement.in_(
                    ("CASH", "TOMORROW")
                ),
            )
            .order_by(CoinIntelligenceShadowRun.as_of_utc.desc())
            .limit(MAX_QUERY_ROWS)
        )
        result = (await session.execute(statement)).all()
    rows = []
    for value in result:
        diagnostics = value.diagnostics or {}
        features = value.features or {}
        rows.append(
            {
                "run_as_of_utc": value.as_of_utc,
                "training_eligible": value.training_eligible,
                "settlement": value.settlement,
                "primary_center_project_price": value.primary_center_project_price,
                "offer_price": diagnostics.get(
                    "observed_offer_price_project"
                ),
                "intrinsic_toman": (features.get("rate") or {}).get(
                    "intrinsic_toman"
                ),
                "regime_label": (
                    features.get("market_regime_v2")
                    or features.get("market_regime")
                    or {}
                ).get("label"),
                "outcome_price": value.actual_project_price,
                "outcome_occurred_at_utc": value.occurred_at_utc,
                "label_status": value.label_status,
                "quality_realtime_weight": value.realtime_weight,
                "quality_training_weight": value.training_weight,
            }
        )
    return derive_feature_context_v2(
        rows,
        cutoff_utc=cutoff,
        target_settlement=settlement,
    )
