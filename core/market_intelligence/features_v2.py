"""Immutable feature-snapshot-v2 assembly for one Shadow cutoff."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timezone
import math
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from core.market_intelligence.regime_v2 import evaluate_regime_v2


FEATURE_SNAPSHOT_V2_SCHEMA = "COIN_FEATURE_SNAPSHOT_V2_20260726"
TEHRAN = ZoneInfo("Asia/Tehran")
MAX_HISTORY_ITEMS = 20


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("feature_snapshot_cutoff_timezone_required")
    return value.astimezone(timezone.utc)


def _parse_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("feature history timestamp must have timezone")
    return parsed.astimezone(timezone.utc)


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _tehran_clock(cutoff: datetime) -> dict[str, Any]:
    local = cutoff.astimezone(TEHRAN)
    minute_of_day = local.hour * 60 + local.minute
    # This is an explicitly versioned model feature, not a statement about
    # bank/legal opening. Raw weekday and minute remain available.
    candidate_banking_window = (
        local.weekday() in {5, 6, 0, 1, 2}
        and time(8, 0) <= local.timetz().replace(tzinfo=None) < time(14, 0)
    )
    return {
        "timezone": "Asia/Tehran",
        "local_iso": local.isoformat(),
        "weekday_iso": local.isoweekday(),
        "minute_of_day": minute_of_day,
        "gregorian_date": local.date().isoformat(),
        "candidate_banking_window": candidate_banking_window,
        "banking_window_policy": "RESEARCH_SAT_TO_WED_0800_1400_V1",
    }


def _history(
    items: Sequence[Mapping[str, Any]],
    *,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    accepted = []
    for item in items:
        try:
            observed_at = _parse_time(item["observed_at_utc"])
            bubble_ratio = float(item["bubble_ratio"])
            source_weight = float(item.get("source_weight") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if (
            observed_at >= cutoff
            or not math.isfinite(bubble_ratio)
            or not math.isfinite(source_weight)
            or source_weight <= 0
        ):
            continue
        accepted.append(
            {
                "observed_at_utc": observed_at.isoformat(),
                "price_project": (
                    int(round(float(item["price_project"])))
                    if _finite(item.get("price_project"))
                    and float(item["price_project"]) > 0
                    else None
                ),
                "baseline_project_price": (
                    int(round(float(item["baseline_project_price"])))
                    if _finite(item.get("baseline_project_price"))
                    and float(item["baseline_project_price"]) > 0
                    else None
                ),
                "bubble_ratio": bubble_ratio,
                "source_weight": source_weight,
                "source_kind": str(
                    item.get("source_kind") or "UNREVIEWED_OFFER"
                )[:32],
                "label_status": str(
                    item.get("label_status") or "UNREVIEWED"
                )[:32],
                "training_eligible": bool(
                    item.get("training_eligible", False)
                ),
            }
        )
    accepted.sort(key=lambda item: item["observed_at_utc"])
    return accepted[-MAX_HISTORY_ITEMS:]


def build_feature_snapshot_v2(
    primary_evidence: Mapping[str, Any],
    *,
    as_of_utc: datetime,
    same_market_history: Sequence[Mapping[str, Any]] = (),
    settlement_basis: Mapping[str, Any] | None = None,
    quality: Mapping[str, Any] | None = None,
    previous_regime_label: str | None = None,
) -> dict[str, Any]:
    """Return a compact snapshot where future/same-time rows are impossible."""

    cutoff = _utc(as_of_utc)
    output = deepcopy(dict(primary_evidence))
    output["schema_version"] = FEATURE_SNAPSHOT_V2_SCHEMA
    output["as_of_utc"] = cutoff.isoformat()
    output["tehran_time"] = _tehran_clock(cutoff)
    output["same_market_history"] = _history(
        same_market_history,
        cutoff=cutoff,
    )
    basis_payload = dict(settlement_basis or {})
    basis_as_of = basis_payload.get("as_of_utc")
    if basis_as_of is not None:
        try:
            if _parse_time(basis_as_of) >= cutoff:
                basis_payload = {
                    "status": "NO_DATA",
                    "reason": "BASIS_NOT_STRICTLY_PRIOR",
                }
        except ValueError:
            basis_payload = {
                "status": "NO_DATA",
                "reason": "BASIS_TIMESTAMP_INVALID",
            }
    output["settlement_basis"] = basis_payload or {
        "status": "NO_DATA",
        "reason": "BASIS_UNAVAILABLE",
    }
    output["quality"] = dict(quality or {})
    output["market_regime_v2"] = evaluate_regime_v2(
        output,
        previous_label=previous_regime_label,
    )

    missing = []
    sources = output.get("sources") or {}
    if isinstance(sources, Mapping):
        missing.extend(
            str(name)
            for name, value in sources.items()
            if not isinstance(value, Mapping)
            or str(value.get("status") or "NO_DATA") != "OBSERVED"
        )
    if not output["same_market_history"]:
        missing.append("same_market_history")
    if str(output["settlement_basis"].get("status")) != "OBSERVED":
        missing.append("settlement_basis")
    rate = output.get("rate") or {}
    if not isinstance(rate, Mapping) or not _finite(
        rate.get("intrinsic_toman")
    ):
        missing.append("intrinsic_toman")
    output["missing_fields_v2"] = sorted(set(missing))
    output["strictly_prior_history_count"] = len(
        output["same_market_history"]
    )
    return output
