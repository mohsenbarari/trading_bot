"""Gated chronological evaluation for a coin-bubble relationship challenger.

This module never loads a production estimator or publishes a model.  Its only
job is to decide whether a numeric, aggregate-only research dataset has enough
strictly-prior labels for an offline challenger experiment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from statistics import median
from typing import Iterable, Sequence


CHALLENGER_VERSION = "COIN_BUBBLE_RELATIONSHIP_CATBOOST_V1_SHADOW_20260803"
PURGE_WINDOW = timedelta(minutes=15)
MINIMUM_FIT_ROWS = 250
MINIMUM_VALIDATION_ROWS = 60
MINIMUM_TEST_ROWS = 60
MINIMUM_DISTINCT_FIT_DAYS = 7
MINIMUM_MARKET_ROWS = 30


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("challenger_timestamp_timezone_required")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class CoinBubbleRow:
    available_at_utc: datetime
    realized_at_utc: datetime
    commodity: str
    settlement: str
    trade_form: str
    bubble_ratio: float
    features: dict[str, float]

    def __post_init__(self) -> None:
        available = _utc(self.available_at_utc)
        realized = _utc(self.realized_at_utc)
        if realized <= available:
            raise ValueError("challenger_label_must_be_strictly_future")
        if not self.commodity or not self.settlement or not self.trade_form:
            raise ValueError("challenger_market_dimension_invalid")
        if not math.isfinite(self.bubble_ratio):
            raise ValueError("challenger_label_invalid")
        if not self.features:
            raise ValueError("challenger_features_missing")
        object.__setattr__(self, "available_at_utc", available)
        object.__setattr__(self, "realized_at_utc", realized)

    @property
    def market_key(self) -> str:
        return f"{self.commodity}:{self.settlement}:{self.trade_form}"


@dataclass(frozen=True)
class ChronologicalSplit:
    fit: tuple[CoinBubbleRow, ...]
    validation: tuple[CoinBubbleRow, ...]
    test: tuple[CoinBubbleRow, ...]
    validation_start_utc: datetime
    test_start_utc: datetime
    purged_rows: int


def chronological_split(rows: Sequence[CoinBubbleRow]) -> ChronologicalSplit:
    """Split by availability, purging both sides of time boundaries."""

    ordered = tuple(sorted(rows, key=lambda row: row.available_at_utc))
    if len(ordered) < 5:
        raise ValueError("challenger_rows_insufficient_for_split")
    validation_start = ordered[int(len(ordered) * 0.60)].available_at_utc
    test_start = ordered[int(len(ordered) * 0.80)].available_at_utc
    fit: list[CoinBubbleRow] = []
    validation: list[CoinBubbleRow] = []
    test: list[CoinBubbleRow] = []
    purged = 0
    for row in ordered:
        # A row within the purge band can share short-lived order-flow context
        # with a neighbouring partition.  It belongs to neither partition.
        if (
            abs(row.available_at_utc - validation_start) <= PURGE_WINDOW
            or abs(row.available_at_utc - test_start) <= PURGE_WINDOW
        ):
            purged += 1
        elif row.available_at_utc < validation_start:
            fit.append(row)
        elif row.available_at_utc < test_start:
            validation.append(row)
        else:
            test.append(row)
    return ChronologicalSplit(
        fit=tuple(fit),
        validation=tuple(validation),
        test=tuple(test),
        validation_start_utc=validation_start,
        test_start_utc=test_start,
        purged_rows=purged,
    )


def readiness(split: ChronologicalSplit) -> dict[str, object]:
    fit_days = {row.available_at_utc.date().isoformat() for row in split.fit}
    market_counts: dict[str, int] = {}
    for row in split.fit:
        market_counts[row.market_key] = market_counts.get(row.market_key, 0) + 1
    reasons = []
    if len(split.fit) < MINIMUM_FIT_ROWS:
        reasons.append("INSUFFICIENT_FIT_ROWS")
    if len(split.validation) < MINIMUM_VALIDATION_ROWS:
        reasons.append("INSUFFICIENT_VALIDATION_ROWS")
    if len(split.test) < MINIMUM_TEST_ROWS:
        reasons.append("INSUFFICIENT_TEST_ROWS")
    if len(fit_days) < MINIMUM_DISTINCT_FIT_DAYS:
        reasons.append("INSUFFICIENT_DISTINCT_FIT_DAYS")
    eligible_markets = {
        key: count for key, count in market_counts.items() if count >= MINIMUM_MARKET_ROWS
    }
    if not eligible_markets:
        reasons.append("NO_COMMODITY_SETTLEMENT_WITH_SUFFICIENT_FIT_ROWS")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "fit_rows": len(split.fit),
        "validation_rows": len(split.validation),
        "test_rows": len(split.test),
        "purged_rows": split.purged_rows,
        "distinct_fit_days": len(fit_days),
        "fit_market_counts": market_counts,
        "eligible_fit_markets": eligible_markets,
    }


def median_baseline(
    fit_rows: Iterable[CoinBubbleRow], test_rows: Iterable[CoinBubbleRow]
) -> dict[str, float | int | None]:
    """Use only fitted market medians as a transparent challenger baseline."""

    values: dict[str, list[float]] = {}
    all_values: list[float] = []
    for row in fit_rows:
        values.setdefault(row.market_key, []).append(row.bubble_ratio)
        all_values.append(row.bubble_ratio)
    if not all_values:
        return {"sample_count": 0, "mae": None, "median_absolute_error": None}
    overall = median(all_values)
    errors = []
    for row in test_rows:
        prediction = median(values.get(row.market_key, all_values)) if row.market_key in values else overall
        errors.append(abs(row.bubble_ratio - prediction))
    return {
        "sample_count": len(errors),
        "mae": round(sum(errors) / len(errors), 8) if errors else None,
        "median_absolute_error": round(median(errors), 8) if errors else None,
    }
