"""Privacy-minimized, human-gated metrics for the inference shadow rollout.

The report intentionally evaluates product decisions and accepted selections,
not market prices or user identities.  It can therefore be generated on a
staging database without exporting offer text, notes, Telegram identifiers,
offer identifiers, or submitted prices.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


COIN_INFERENCE_ROLLOUT_METRICS_VERSION = "coin-inference-rollout-metrics-v1"
_TEHRAN = ZoneInfo("Asia/Tehran")
_DECISION_STATUSES = ("AUTO_SELECT", "CONFIRM", "ABSTAIN")


def _value(record: object, field_name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(field_name, default)
    return getattr(record, field_name, default)


def _utc(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, str):
        try:
            candidate = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if candidate.tzinfo is None:
        # PostgreSQL application records are UTC.  Treat an unexpected naive
        # value as UTC for reporting, never as Tehran local time.
        candidate = candidate.replace(tzinfo=timezone.utc)
    return candidate.astimezone(timezone.utc)


def _snapshot_age_bucket(*, created_at: datetime | None, snapshot_at: datetime | None) -> str:
    if snapshot_at is None:
        return "NO_SNAPSHOT"
    if created_at is None:
        return "UNKNOWN"
    seconds = (created_at - snapshot_at).total_seconds()
    if seconds < 0:
        return "FUTURE_SNAPSHOT"
    if seconds <= 30:
        return "AGE_0_30S"
    if seconds <= 120:
        return "AGE_31_120S"
    if seconds <= 300:
        return "AGE_121_300S"
    return "AGE_OVER_300S"


def _tehran_hour(value: datetime | None) -> str:
    return "UNKNOWN" if value is None else f"{value.astimezone(_TEHRAN).hour:02d}"


def _clean_status(value: object) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in _DECISION_STATUSES else "INVALID"


def _safe_choice_code(value: object) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized and len(normalized) <= 32 else "UNKNOWN"


def _zero_statuses(counter: Counter[str]) -> dict[str, int]:
    return {status: int(counter.get(status, 0)) for status in _DECISION_STATUSES}


def _counter_items(counter: Counter[str]) -> list[dict[str, object]]:
    return [
        {"key": key, "count": int(count)}
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_coin_inference_rollout_metrics(
    decisions: Iterable[object],
    outcomes: Iterable[object],
    *,
    generated_at_utc: datetime | None = None,
) -> dict[str, object]:
    """Aggregate append-only decision/outcome rows into a safe P7 report.

    A result is deliberately descriptive only.  In particular,
    ``auto_promotion_allowed`` is always false: owner-approved thresholds and
    frozen Snapshot/source dimensions are later release gates, not an implicit
    consequence of a favourable aggregate.
    """

    generated_at = (generated_at_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    decision_by_key: dict[str, dict[str, object]] = {}
    invalid_decisions = 0
    decision_statuses: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    cells: Counter[tuple[str, str, str, str, str, str, str]] = Counter()

    for row in decisions:
        decision_key = str(_value(row, "decision_key", "")).strip().lower()
        if len(decision_key) != 64 or decision_key in decision_by_key:
            invalid_decisions += 1
            continue
        created_at = _utc(_value(row, "created_at"))
        snapshot_at = _utc(_value(row, "snapshot_generated_at_utc"))
        status = _clean_status(_value(row, "decision_status"))
        source_surface = str(_value(row, "source_surface", "UNKNOWN")).strip().upper() or "UNKNOWN"
        settlement_term = str(_value(row, "settlement_term", "UNKNOWN")).strip().upper() or "UNKNOWN"
        reason = str(_value(row, "reason_code", "")).strip().upper()
        record = {
            "status": status,
            "source_surface": source_surface,
            "settlement_term": settlement_term,
            "selected_commodity_code": _safe_choice_code(_value(row, "selected_commodity_code")),
            "created_at": created_at,
            "snapshot_at": snapshot_at,
            "dominant_underlying_source": str(
                _value(row, "dominant_underlying_source", "UNKNOWN") or "UNKNOWN"
            ).strip().upper() or "UNKNOWN",
            "market_regime": str(_value(row, "market_regime", "UNKNOWN") or "UNKNOWN").strip().upper() or "UNKNOWN",
        }
        decision_by_key[decision_key] = record
        decision_statuses[status] += 1
        if reason:
            reason_counts[reason] += 1
        cells[
            (
                source_surface,
                settlement_term,
                status,
                _tehran_hour(created_at),
                _snapshot_age_bucket(created_at=created_at, snapshot_at=snapshot_at),
                str(record["dominant_underlying_source"]),
                str(record["market_regime"]),
            )
        ] += 1

    outcome_count = 0
    orphan_outcomes = 0
    surface_mismatches = 0
    multiple_outcomes = 0
    outcomes_per_decision: Counter[str] = Counter()
    accepted_choices: Counter[str] = Counter()
    accepted_by_surface: Counter[str] = Counter()
    auto_choice_match = 0
    auto_choice_mismatch = 0
    confirm_choice_count = 0

    seen_outcome_keys: set[str] = set()
    for row in outcomes:
        outcome_key = str(_value(row, "outcome_key", "")).strip().lower()
        if len(outcome_key) != 64 or outcome_key in seen_outcome_keys:
            continue
        seen_outcome_keys.add(outcome_key)
        outcome_count += 1
        decision_key = str(_value(row, "decision_key", "")).strip().lower()
        decision = decision_by_key.get(decision_key)
        if decision is None:
            orphan_outcomes += 1
            continue
        source_surface = str(_value(row, "source_surface", "UNKNOWN")).strip().upper() or "UNKNOWN"
        if source_surface != decision["source_surface"]:
            surface_mismatches += 1
            continue
        outcomes_per_decision[decision_key] += 1
        choice_code = _safe_choice_code(_value(row, "selected_commodity_code"))
        accepted_choices[choice_code] += 1
        accepted_by_surface[source_surface] += 1
        if decision["status"] == "AUTO_SELECT":
            if choice_code == decision["selected_commodity_code"]:
                auto_choice_match += 1
            else:
                auto_choice_mismatch += 1
        elif decision["status"] == "CONFIRM":
            confirm_choice_count += 1

    multiple_outcomes = sum(count > 1 for count in outcomes_per_decision.values())
    selectable_decisions = sum(
        count for status, count in decision_statuses.items() if status in {"AUTO_SELECT", "CONFIRM"}
    )
    accepted_decision_count = len(outcomes_per_decision)
    coverage_percent = (
        round(100.0 * accepted_decision_count / selectable_decisions, 2)
        if selectable_decisions
        else None
    )

    cell_rows = [
        {
            "source_surface": source,
            "settlement_term": settlement,
            "decision_status": status,
            "tehran_hour": hour,
            "snapshot_age_bucket": age,
            "dominant_underlying_source": underlying_source,
            "market_regime": market_regime,
            "decision_count": int(count),
        }
        for (source, settlement, status, hour, age, underlying_source, market_regime), count in sorted(cells.items())
    ]
    report_status = "READY" if decision_by_key else "NO_DECISIONS"
    return {
        "version": COIN_INFERENCE_ROLLOUT_METRICS_VERSION,
        "generated_at_utc": generated_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": report_status,
        "privacy": {
            "contains_raw_offer_text": False,
            "contains_user_identity": False,
            "contains_telegram_identity": False,
            "contains_offer_identity": False,
            "contains_submitted_price": False,
        },
        "decision_counts": _zero_statuses(decision_statuses),
        "selectable_decision_count": selectable_decisions,
        "accepted_selection_count": accepted_decision_count,
        "accepted_selection_coverage_percent": coverage_percent,
        "accepted_selection_by_commodity": _counter_items(accepted_choices),
        "accepted_selection_by_surface": _counter_items(accepted_by_surface),
        "auto_choice_revalidation": {
            "matching_accepted_choices": auto_choice_match,
            "mismatching_accepted_choices": auto_choice_mismatch,
        },
        "confirm_choice_count": confirm_choice_count,
        "reason_counts": _counter_items(reason_counts),
        "cells": cell_rows,
        "data_quality": {
            "invalid_or_duplicate_decision_rows": invalid_decisions,
            "orphan_outcomes": orphan_outcomes,
            "surface_mismatched_outcomes": surface_mismatches,
            "decisions_with_multiple_accepted_choices": multiple_outcomes,
        },
        "promotion_guard": {
            "auto_promotion_allowed": False,
            "reason": "HUMAN_APPROVAL_AND_CELL_THRESHOLDS_REQUIRED",
            "missing_dimensions": [
                "operator_correction_outcome",
            ],
        },
    }


__all__ = [
    "COIN_INFERENCE_ROLLOUT_METRICS_VERSION",
    "build_coin_inference_rollout_metrics",
]
