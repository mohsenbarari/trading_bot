"""Gated non-linear challenger for strictly-prior melted-market relations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from statistics import median
from typing import Iterable, Sequence


MELTED_CHALLENGER_VERSION = "MELTED_RELATIONSHIP_CATBOOST_V1_SHADOW_20260803"
PURGE_WINDOW = timedelta(minutes=15)
MINIMUM_FIT_ROWS = 1_500
MINIMUM_VALIDATION_ROWS = 350
MINIMUM_TEST_ROWS = 350
MINIMUM_DISTINCT_FIT_DAYS = 7
MINIMUM_TARGET_ROWS = 300


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("melted_challenger_timestamp_timezone_required")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MeltedRelationshipRow:
    available_at_utc: datetime
    realized_at_utc: datetime
    target_market: str
    target_return_bps: float
    features: dict[str, float]

    def __post_init__(self) -> None:
        available = _utc(self.available_at_utc)
        realized = _utc(self.realized_at_utc)
        if realized <= available:
            raise ValueError("melted_challenger_target_must_be_future")
        if not self.target_market or not math.isfinite(self.target_return_bps):
            raise ValueError("melted_challenger_target_invalid")
        if not self.features:
            raise ValueError("melted_challenger_features_missing")
        object.__setattr__(self, "available_at_utc", available)
        object.__setattr__(self, "realized_at_utc", realized)


@dataclass(frozen=True)
class MeltedChronologicalSplit:
    fit: tuple[MeltedRelationshipRow, ...]
    validation: tuple[MeltedRelationshipRow, ...]
    test: tuple[MeltedRelationshipRow, ...]
    validation_start_utc: datetime
    test_start_utc: datetime
    purged_rows: int


def chronological_split(rows: Sequence[MeltedRelationshipRow]) -> MeltedChronologicalSplit:
    ordered = tuple(sorted(rows, key=lambda row: row.available_at_utc))
    if len(ordered) < 5:
        raise ValueError("melted_challenger_rows_insufficient_for_split")
    validation_start = ordered[int(len(ordered) * 0.60)].available_at_utc
    test_start = ordered[int(len(ordered) * 0.80)].available_at_utc
    fit: list[MeltedRelationshipRow] = []
    validation: list[MeltedRelationshipRow] = []
    test: list[MeltedRelationshipRow] = []
    purged = 0
    for row in ordered:
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
    return MeltedChronologicalSplit(
        tuple(fit), tuple(validation), tuple(test), validation_start, test_start, purged
    )


def readiness(split: MeltedChronologicalSplit) -> dict[str, object]:
    days = {row.available_at_utc.date().isoformat() for row in split.fit}
    target_counts: dict[str, int] = {}
    for row in split.fit:
        target_counts[row.target_market] = target_counts.get(row.target_market, 0) + 1
    reasons = []
    if len(split.fit) < MINIMUM_FIT_ROWS:
        reasons.append("INSUFFICIENT_FIT_ROWS")
    if len(split.validation) < MINIMUM_VALIDATION_ROWS:
        reasons.append("INSUFFICIENT_VALIDATION_ROWS")
    if len(split.test) < MINIMUM_TEST_ROWS:
        reasons.append("INSUFFICIENT_TEST_ROWS")
    if len(days) < MINIMUM_DISTINCT_FIT_DAYS:
        reasons.append("INSUFFICIENT_DISTINCT_FIT_DAYS")
    eligible = {key: value for key, value in target_counts.items() if value >= MINIMUM_TARGET_ROWS}
    if not eligible:
        reasons.append("NO_TARGET_MARKET_WITH_SUFFICIENT_FIT_ROWS")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "fit_rows": len(split.fit),
        "validation_rows": len(split.validation),
        "test_rows": len(split.test),
        "purged_rows": split.purged_rows,
        "distinct_fit_days": len(days),
        "fit_target_counts": target_counts,
        "eligible_fit_targets": eligible,
    }


def median_baseline(
    fit: Iterable[MeltedRelationshipRow], test: Iterable[MeltedRelationshipRow]
) -> dict[str, float | int | None]:
    values: dict[str, list[float]] = {}
    overall: list[float] = []
    for row in fit:
        values.setdefault(row.target_market, []).append(row.target_return_bps)
        overall.append(row.target_return_bps)
    if not overall:
        return {"sample_count": 0, "mae_bps": None, "median_absolute_error_bps": None}
    overall_median = median(overall)
    errors = [
        abs(row.target_return_bps - median(values.get(row.target_market, overall)))
        for row in test
    ]
    return {
        "sample_count": len(errors),
        "mae_bps": round(sum(errors) / len(errors), 6) if errors else None,
        "median_absolute_error_bps": round(median(errors), 6) if errors else None,
        "overall_fit_median_bps": round(overall_median, 6),
    }
