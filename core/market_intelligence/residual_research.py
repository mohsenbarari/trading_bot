"""Shared, leakage-resistant dataset helpers for offline residual research.

This module intentionally has no CatBoost or PySR dependency.  It defines the
compact exported row contract and time split used by optional research tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping


RESIDUAL_RESEARCH_SCHEMA = "COIN_RESIDUAL_RESEARCH_V1_20260801"
ALLOWED_LABELS = frozenset({"REVIEWED", "TRUSTED"})


@dataclass(frozen=True, slots=True)
class ResidualResearchRow:
    occurred_at_utc: datetime
    commodity: str
    settlement: str
    trade_form: str
    baseline_project_price: int
    actual_project_price: int
    training_weight: float
    features: Mapping[str, Any]

    @property
    def residual_ratio(self) -> float:
        return self.actual_project_price / self.baseline_project_price - 1.0


def parse_utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_timezone_required")
    return parsed.astimezone(timezone.utc)


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def normalize_rows(rows: Iterable[Mapping[str, Any]]) -> list[ResidualResearchRow]:
    """Validate compact reviewed labels and reject malformed/extreme targets."""

    output = []
    for value in rows:
        if not isinstance(value, Mapping):
            continue
        if str(value.get("label_status") or "").upper() not in ALLOWED_LABELS:
            continue
        if not bool(value.get("training_eligible", False)):
            continue
        baseline = _positive_int(value.get("baseline_project_price"))
        actual = _positive_int(value.get("actual_project_price"))
        weight = _positive_float(value.get("training_weight"))
        features = value.get("features")
        if (
            baseline is None
            or actual is None
            or weight is None
            or not isinstance(features, Mapping)
        ):
            continue
        residual = actual / baseline - 1.0
        if not math.isfinite(residual) or abs(residual) > 0.12:
            continue
        try:
            occurred_at = parse_utc(value["occurred_at_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        commodity = str(value.get("commodity") or "").strip()
        settlement = str(value.get("settlement") or "").upper()
        trade_form = str(value.get("trade_form") or "").upper()
        if not commodity or settlement not in {"CASH", "TOMORROW"}:
            continue
        if trade_form != "PHYSICAL":
            continue
        output.append(
            ResidualResearchRow(
                occurred_at_utc=occurred_at,
                commodity=commodity,
                settlement=settlement,
                trade_form=trade_form,
                baseline_project_price=baseline,
                actual_project_price=actual,
                training_weight=weight,
                features=dict(features),
            )
        )
    return sorted(output, key=lambda row: row.occurred_at_utc)


def chronological_split(
    rows: list[ResidualResearchRow],
) -> tuple[list[ResidualResearchRow], list[ResidualResearchRow], list[ResidualResearchRow]]:
    """Split 60/20/20 without allowing a timestamp to cross folds."""

    if len(rows) < 15:
        raise ValueError("insufficient_rows_for_chronological_split")
    buckets: list[list[ResidualResearchRow]] = []
    for row in sorted(rows, key=lambda item: item.occurred_at_utc):
        if not buckets or buckets[-1][0].occurred_at_utc != row.occurred_at_utc:
            buckets.append([row])
        else:
            buckets[-1].append(row)
    if len(buckets) < 3:
        raise ValueError("insufficient_distinct_timestamps")
    total = len(rows)
    split_one = total * 0.60
    split_two = total * 0.80
    partitions: list[list[ResidualResearchRow]] = [[], [], []]
    seen = 0
    for bucket in buckets:
        midpoint = seen + len(bucket) / 2.0
        target = 0 if midpoint <= split_one else 1 if midpoint <= split_two else 2
        partitions[target].extend(bucket)
        seen += len(bucket)
    if not all(partitions):
        raise ValueError("chronological_split_empty_partition")
    return tuple(partitions)  # type: ignore[return-value]


def feature_vector(row: ResidualResearchRow) -> dict[str, float | str]:
    """Whitelist only cutoff-known compact features; no outcome-derived data."""

    evidence = row.features
    tehran = evidence.get("tehran_time") or {}
    regime = evidence.get("market_regime_v2") or {}
    sources = evidence.get("sources") or {}
    rate = evidence.get("rate") or {}

    def numeric(container: Mapping[str, Any], key: str) -> float:
        value = container.get(key)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return parsed if math.isfinite(parsed) else 0.0

    return {
        "commodity": row.commodity,
        "settlement": row.settlement,
        "trade_form": row.trade_form,
        "minute_of_day": numeric(tehran, "minute_of_day"),
        "weekday_iso": numeric(tehran, "weekday_iso"),
        "candidate_banking_window": float(bool(tehran.get("candidate_banking_window"))),
        "direction_score": numeric(regime, "direction_score"),
        "volatility_percent": numeric(regime, "volatility_percent"),
        "confidence": numeric(regime, "confidence"),
        "agreement_score": numeric(regime, "agreement_score"),
        "cross_source_disagreement": float(bool(regime.get("disagreement_flag"))),
        "intrinsic_toman_log": math.log(max(1.0, numeric(rate, "intrinsic_toman"))),
        "melted_observed": float(
            str((sources.get("melted_gold") or {}).get("status") or "") == "OBSERVED"
        ),
        "usd_observed": float(
            str((sources.get("usd") or {}).get("status") or "") == "OBSERVED"
        ),
        "usdt_observed": float(
            str((sources.get("usdt") or {}).get("status") or "") == "OBSERVED"
        ),
        "ime_observed": float(
            str((sources.get("ime") or {}).get("status") or "") == "OBSERVED"
        ),
    }
