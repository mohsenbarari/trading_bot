"""Redacted parity evidence for inputs actually consumed by two estimators."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import hmac
import json
import math
from typing import Any, Mapping, Sequence


CONTRACT = "market_consumed_input_timeline/1.0"
SAMPLE_CONTRACT = "market_consumed_input_sample/1.0"
_RELATION_ORDER = (
    "BOTH_MISSING",
    "EXACT",
    "WITHIN_1_BPS",
    "WITHIN_5_BPS",
    "WITHIN_25_BPS",
    "WITHIN_100_BPS",
    "OUTSIDE_100_BPS",
    "PRESENCE_MISMATCH",
    "INVALID",
)


class ConsumedInputParityError(RuntimeError):
    """A content-free consumed-input evidence failure."""


def utc(value: str | datetime, *, field: str) -> datetime:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except ValueError as exc:
        raise ConsumedInputParityError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConsumedInputParityError(f"{field}_timezone_required")
    return parsed.astimezone(timezone.utc)


def stamp(value: str | datetime) -> str:
    return utc(value, field="timestamp").isoformat().replace("+00:00", "Z")


def decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConsumedInputParityError("economic_number_invalid") from exc
    if not parsed.is_finite():
        raise ConsumedInputParityError("economic_number_invalid")
    return parsed


def relation(left: object | None, right: object | None) -> str:
    left_number = decimal(left)
    right_number = decimal(right)
    if left_number is None and right_number is None:
        return "BOTH_MISSING"
    if left_number is None or right_number is None:
        return "PRESENCE_MISMATCH"
    if left_number == right_number:
        return "EXACT"
    denominator = max(abs(left_number), abs(right_number))
    if denominator == 0:
        return "INVALID"
    basis_points = abs(left_number - right_number) * Decimal("10000") / denominator
    for limit, code in (
        (Decimal("1"), "WITHIN_1_BPS"),
        (Decimal("5"), "WITHIN_5_BPS"),
        (Decimal("25"), "WITHIN_25_BPS"),
        (Decimal("100"), "WITHIN_100_BPS"),
    ):
        if basis_points <= limit:
            return code
    return "OUTSIDE_100_BPS"


def _optional_stamp(value: object | None) -> str | None:
    return stamp(str(value)) if value else None


def _time_skew(left: object | None, right: object | None) -> float | None:
    if not left or not right:
        return None
    return round(
        abs(
            (
                utc(str(left), field="left_event_time")
                - utc(str(right), field="right_event_time")
            ).total_seconds()
        ),
        3,
    )


def estimator_inputs_as_signals(
    inputs: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    signals: dict[str, dict[str, Any]] = {}
    for item in inputs:
        component = str(item.get("component") or "").strip().upper()
        if not component or component in signals:
            raise ConsumedInputParityError("estimator_input_component_invalid")
        signals[component] = {
            "status": item.get("freshness"),
            "price_unit": item.get("unit"),
            "latest_price": item.get("point_value"),
            "mean_price": item.get("mean_value"),
            "observation_count": item.get("sample_count"),
            "last_event_utc": item.get("occurred_at_utc"),
            "method": item.get("selection_method"),
            "source_codes": item.get("source_codes") or (),
        }
    return signals


def _signal(value: Mapping[str, Any] | None) -> dict[str, Any]:
    item = value or {}
    return {
        "status": str(item.get("status") or "MISSING").upper(),
        "unit": str(item.get("price_unit") or item.get("unit") or "UNKNOWN_UNIT"),
        "point": item.get("latest_price", item.get("point_value")),
        "mean": item.get("mean_price", item.get("mean_value")),
        "sample_count": int(item.get("observation_count", item.get("sample_count", 0)) or 0),
        "last_event_utc": _optional_stamp(
            item.get("last_event_utc", item.get("occurred_at_utc"))
        ),
        "method": str(item.get("method") or item.get("selection_method") or "NO_DATA"),
        "source_codes": tuple(str(code) for code in item.get("source_codes") or ()),
    }


def compare_signal_sets(
    reference: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for component in sorted(set(reference) | set(candidate)):
        left = _signal(reference.get(component))
        right = _signal(candidate.get(component))
        output.append(
            {
                "component": component,
                "status_equal": left["status"] == right["status"],
                "reference_status": left["status"],
                "candidate_status": right["status"],
                "unit_equal": left["unit"] == right["unit"],
                "reference_unit": left["unit"],
                "candidate_unit": right["unit"],
                "point_relation": relation(left["point"], right["point"]),
                "mean_relation": relation(left["mean"], right["mean"]),
                "reference_sample_count": left["sample_count"],
                "candidate_sample_count": right["sample_count"],
                "event_time_skew_seconds": _time_skew(
                    left["last_event_utc"], right["last_event_utc"]
                ),
                "method_equal": left["method"] == right["method"],
                "source_codes_equal": left["source_codes"] == right["source_codes"],
            }
        )
    return output


def _market_rates(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    rates = snapshot.get("rates") or {}
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for item in rates.get("items") or ():
        key = (
            str(item.get("commodity_code") or "").upper(),
            str(item.get("settlement_term") or "").upper(),
        )
        if not all(key) or key in output:
            raise ConsumedInputParityError("market_rate_identity_invalid")
        output[key] = {
            "status": str(item.get("status") or "NO_DATA").upper(),
            "point": item.get("estimated_project_price"),
            "lower": item.get("lower_project_price"),
            "upper": item.get("upper_project_price"),
            "method": str(item.get("method") or "NO_DATA"),
        }
    return output


def _estimator_rates(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for item in snapshot.get("rates") or ():
        instrument = str(item.get("instrument") or "").upper()
        commodity = instrument.removeprefix("COIN_")
        key = (commodity, str(item.get("settlement") or "").upper())
        if not all(key) or key in output:
            raise ConsumedInputParityError("estimator_rate_identity_invalid")
        output[key] = {
            "status": "ESTIMATED",
            "point": item.get("value"),
            "lower": item.get("lower_bound"),
            "upper": item.get("upper_bound"),
            "method": str(item.get("method") or "UNKNOWN_METHOD"),
        }
    return output


def compare_rate_sets(
    reference_snapshot: Mapping[str, Any],
    candidate_snapshot: Mapping[str, Any],
    *,
    candidate_is_estimator_snapshot: bool,
) -> list[dict[str, Any]]:
    reference = _market_rates(reference_snapshot)
    candidate = (
        _estimator_rates(candidate_snapshot)
        if candidate_is_estimator_snapshot
        else _market_rates(candidate_snapshot)
    )
    output: list[dict[str, Any]] = []
    for key in sorted(set(reference) | set(candidate)):
        left = reference.get(key) or {
            "status": "MISSING",
            "point": None,
            "lower": None,
            "upper": None,
            "method": "NO_DATA",
        }
        right = candidate.get(key) or {
            "status": "MISSING",
            "point": None,
            "lower": None,
            "upper": None,
            "method": "NO_DATA",
        }
        output.append(
            {
                "instrument": "COIN_" + key[0],
                "settlement": key[1],
                "status_equal": left["status"] == right["status"],
                "reference_status": left["status"],
                "candidate_status": right["status"],
                "point_relation": relation(left["point"], right["point"]),
                "lower_relation": relation(left["lower"], right["lower"]),
                "upper_relation": relation(left["upper"], right["upper"]),
                "method_equal": left["method"] == right["method"],
            }
        )
    return output


def hmac_reference(key: bytes, *, namespace: bytes, document: Mapping[str, Any]) -> str:
    if len(key) < 32:
        raise ConsumedInputParityError("timeline_identity_key_too_short")
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hmac.new(key, namespace + b"\0" + encoded, sha256).hexdigest()


def transition_trace(
    snapshot: Mapping[str, Any],
    *,
    identity_key: bytes,
    baseline: bool,
) -> list[dict[str, Any]]:
    generated = utc(str(snapshot.get("generated_at_utc")), field="candidate_generated_at")
    output: list[dict[str, Any]] = []
    for item in snapshot.get("inputs") or ():
        if str(item.get("freshness") or "").upper() == "MISSING":
            continue
        source_identity = {
            "component": item.get("component"),
            "source_event_key": item.get("source_event_key"),
            "source_fact_id": item.get("source_fact_id"),
            "fact_revision": item.get("fact_revision"),
        }
        times = {
            name: utc(str(item.get(name)), field=name)
            for name in (
                "occurred_at_utc",
                "available_at_utc",
                "parsed_at_utc",
                "transferred_at_utc",
            )
        }
        if not (
            times["occurred_at_utc"]
            <= times["available_at_utc"]
            <= times["parsed_at_utc"]
            <= times["transferred_at_utc"]
            <= generated
        ):
            raise ConsumedInputParityError("timeline_trace_time_order_invalid")
        output.append(
            {
                "component": str(item.get("component") or "").upper(),
                "source_ref": hmac_reference(
                    identity_key,
                    namespace=b"consumed-input-source-v1",
                    document=source_identity,
                ),
                "baseline_at_window_start": bool(baseline),
                "occurred_to_snapshot_seconds": round(
                    (generated - times["occurred_at_utc"]).total_seconds(), 3
                ),
                "available_to_snapshot_seconds": round(
                    (generated - times["available_at_utc"]).total_seconds(), 3
                ),
                "parsed_to_snapshot_seconds": round(
                    (generated - times["parsed_at_utc"]).total_seconds(), 3
                ),
                "transferred_to_snapshot_seconds": round(
                    (generated - times["transferred_at_utc"]).total_seconds(), 3
                ),
            }
        )
    return output


def percentile(values: Sequence[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    return ordered[max(0, math.ceil(len(ordered) * ratio) - 1)]


def summarize_relations(
    samples: Sequence[Mapping[str, Any]], *, field: str
) -> dict[str, dict[str, int]]:
    output: dict[str, Counter[str]] = {}
    for sample in samples:
        for comparison in sample.get(field) or ():
            component = str(
                comparison.get("component") or comparison.get("instrument") or "UNKNOWN"
            )
            relation_code = str(comparison.get("point_relation") or "INVALID")
            if relation_code not in _RELATION_ORDER:
                raise ConsumedInputParityError("timeline_relation_invalid")
            output.setdefault(component, Counter())[relation_code] += 1
    return {
        component: {code: counts.get(code, 0) for code in _RELATION_ORDER}
        for component, counts in sorted(output.items())
    }


def assert_redacted(document: Mapping[str, Any]) -> None:
    forbidden_keys = {
        "raw_text",
        "text",
        "message_id",
        "sender",
        "telegram_id",
        "event_id",
        "price",
        "quantity",
        "point_value",
        "mean_value",
        "value",
        "lower_bound",
        "upper_bound",
    }

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower() in forbidden_keys:
                    raise ConsumedInputParityError("timeline_economic_or_private_field_present")
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, str) and any(
            token in value.lower() for token in ("https://", "t.me/", "+98")
        ):
            raise ConsumedInputParityError("timeline_private_string_present")

    walk(document)


def build_report(
    *,
    started_at_utc: datetime,
    completed_at_utc: datetime,
    samples: Sequence[Mapping[str, Any]],
    candidate_snapshot_versions: Sequence[int],
    transitions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not samples:
        raise ConsumedInputParityError("timeline_samples_required")
    if completed_at_utc <= started_at_utc:
        raise ConsumedInputParityError("timeline_window_invalid")
    versions = [int(value) for value in candidate_snapshot_versions]
    gaps = sum(
        max(0, following - prior - 1)
        for prior, following in zip(versions, versions[1:])
    )
    non_baseline_transitions = [
        item for item in transitions if not item.get("baseline_at_window_start")
    ]
    transfer_latencies = [
        float(item["transferred_to_snapshot_seconds"])
        for item in non_baseline_transitions
    ]
    pair_skews = [float(item["pair_skew_seconds"]) for item in samples]
    exact_summary = summarize_relations(samples, field="exact_as_of_signals")
    scheduled_summary = summarize_relations(samples, field="scheduled_signals")
    exact_rate_summary = summarize_relations(samples, field="exact_as_of_rates")
    report = {
        "contract": CONTRACT,
        "evidence_mode": "LIVE_CONSUMED_INPUT_TIMELINE",
        "started_at_utc": stamp(started_at_utc),
        "completed_at_utc": stamp(completed_at_utc),
        "reference_sample_count": len(samples),
        "candidate_snapshot_count": len(versions),
        "candidate_snapshot_version_gap_count": gaps,
        "snapshot_timeline_complete": bool(versions) and gaps == 0,
        "pair_skew_p95_seconds": percentile(pair_skews, 0.95),
        "new_source_transition_count": len(non_baseline_transitions),
        "new_source_transfer_to_snapshot_p95_seconds": percentile(
            transfer_latencies, 0.95
        ),
        "exact_as_of_point_relations": exact_summary,
        "scheduled_point_relations": scheduled_summary,
        "exact_as_of_rate_relations": exact_rate_summary,
        "samples": list(samples),
        "source_transitions": list(transitions),
        "full_market_session": False,
        "cutover_performed": False,
        "promotion_recommendation": "HOLD_FULL_OPEN_MARKET_SESSION_REQUIRED",
    }
    assert_redacted(report)
    return report


__all__ = [
    "CONTRACT",
    "SAMPLE_CONTRACT",
    "ConsumedInputParityError",
    "assert_redacted",
    "build_report",
    "compare_rate_sets",
    "compare_signal_sets",
    "estimator_inputs_as_signals",
    "hmac_reference",
    "relation",
    "stamp",
    "transition_trace",
    "utc",
]
