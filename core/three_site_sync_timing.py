"""Fail-closed timing evidence for the authoritative three-site staging Matrix.

The Full Matrix controller must not infer synchronization performance from a
scenario's wall-clock duration.  This module owns the route topology, timing
profiles and the semantic verifier for raw, per-correlation synchronization
measurements collected from Bot-FI, WebApp-FI and WebApp-IR.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
from typing import Any


SYNC_TIMING_SCHEMA = "three-site-staging-sync-timing-v1"
SYNC_TIMING_ASSERTION = "three_site_sync_timing"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,191}$")

ROUTE_HOPS: dict[str, tuple[tuple[str, str], ...]] = {
    "bot_fi_to_webapp_fi": (("bot_fi", "webapp_fi"),),
    "webapp_fi_to_bot_fi": (("webapp_fi", "bot_fi"),),
    "webapp_fi_to_webapp_ir": (("webapp_fi", "webapp_ir"),),
    "webapp_ir_to_webapp_fi": (("webapp_ir", "webapp_fi"),),
    "bot_fi_to_webapp_ir_via_webapp_fi": (
        ("bot_fi", "webapp_fi"),
        ("webapp_fi", "webapp_ir"),
    ),
    "webapp_ir_to_bot_fi_via_webapp_fi": (
        ("webapp_ir", "webapp_fi"),
        ("webapp_fi", "bot_fi"),
    ),
}

ALL_ROUTES = tuple(ROUTE_HOPS)
NORMAL_ROUTES = (
    "bot_fi_to_webapp_fi",
    "webapp_fi_to_bot_fi",
    "webapp_fi_to_webapp_ir",
    "bot_fi_to_webapp_ir_via_webapp_fi",
)
RECOVERY_ROUTES = (
    "webapp_fi_to_webapp_ir",
    "webapp_ir_to_webapp_fi",
    "bot_fi_to_webapp_ir_via_webapp_fi",
    "webapp_ir_to_bot_fi_via_webapp_fi",
)

# Per-sample ceilings remain generous safety bounds.  The 300-rps profile also
# enforces the owner-approved route percentile SLOs recorded in the roadmap.
SYNC_TIMING_POLICIES: dict[str, dict[str, Any]] = {
    "three_site_sync_timing_steady_state": {
        "state": "normal_fi_active",
        "routes": NORMAL_ROUTES,
        "minimum_samples_per_route": 20,
        "maximum_sample_seconds": 30.0,
        "minimum_observed_rps": 0.0,
        "required_surface_split": None,
        "route_percentile_limits": {},
        "backlog_required": False,
    },
    "three_hundred_rps_fifty_fifty": {
        "state": "normal_fi_active_under_load",
        "routes": NORMAL_ROUTES,
        "minimum_samples_per_route": 100,
        "maximum_sample_seconds": 60.0,
        "minimum_observed_rps": 300.0,
        "required_surface_split": {
            "bot_fraction": 0.5,
            "webapp_fraction": 0.5,
            "maximum_absolute_deviation": 0.01,
        },
        "route_percentile_limits": {
            "bot_fi_to_webapp_fi": {
                "p50_seconds": 0.080,
                "p95_seconds": 0.150,
                "p99_seconds": 0.300,
            },
            "webapp_fi_to_bot_fi": {
                "p50_seconds": 0.080,
                "p95_seconds": 0.150,
                "p99_seconds": 0.300,
            },
            "webapp_fi_to_webapp_ir": {
                "p50_seconds": 0.500,
                "p95_seconds": 0.750,
                "p99_seconds": 2.000,
            },
        },
        "backlog_required": False,
    },
    "reconnect_flap_and_bounded_catchup": {
        "state": "recovery_ir_routed",
        "routes": RECOVERY_ROUTES,
        "minimum_samples_per_route": 20,
        "maximum_sample_seconds": 900.0,
        "minimum_observed_rps": 0.0,
        "required_surface_split": None,
        "route_percentile_limits": {},
        "backlog_required": True,
    },
    "one_hour_backlog_with_live_traffic": {
        "state": "recovery_backlog_under_live_traffic",
        "routes": RECOVERY_ROUTES,
        "minimum_samples_per_route": 100,
        "maximum_sample_seconds": 3600.0,
        "minimum_observed_rps": 1.0,
        "required_surface_split": None,
        "route_percentile_limits": {},
        "backlog_required": True,
    },
}


class SyncTimingEvidenceError(RuntimeError):
    pass


def sync_timing_policy(scenario_id: str) -> dict[str, Any] | None:
    value = SYNC_TIMING_POLICIES.get(scenario_id)
    if value is None:
        return None
    return {
        "schema": SYNC_TIMING_SCHEMA,
        "scenario_id": scenario_id,
        "state": value["state"],
        "routes": list(value["routes"]),
        "minimum_samples_per_route": value["minimum_samples_per_route"],
        "maximum_sample_seconds": value["maximum_sample_seconds"],
        "minimum_observed_rps": value["minimum_observed_rps"],
        "required_surface_split": value["required_surface_split"],
        "route_percentile_limits": value["route_percentile_limits"],
        "backlog_required": value["backlog_required"],
    }


def _utc(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyncTimingEvidenceError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise SyncTimingEvidenceError(f"{label} has no timezone")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any, *, label: str, minimum: float = 0.0) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SyncTimingEvidenceError(f"{label} is not numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise SyncTimingEvidenceError(f"{label} is outside its valid range")
    return result


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return round(ordered[rank - 1], 6)


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "sample_count": len(values),
        "p50_seconds": _nearest_rank(values, 0.50),
        "p95_seconds": _nearest_rank(values, 0.95),
        "p99_seconds": _nearest_rank(values, 0.99),
        "max_seconds": round(max(values), 6),
    }


def summarize_sync_timing_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Recompute route and physical-hop percentiles from retained raw rows."""

    route_values: dict[str, list[float]] = {}
    hop_values: dict[str, dict[str, list[float]]] = {}
    for sample in samples:
        route = str(sample["route"])
        route_values.setdefault(route, []).append(
            float(sample["controller_observed_duration_seconds"])
        )
        for hop in sample["hops"]:
            hop_name = f"{hop['source_site']}_to_{hop['destination_site']}"
            points = {
                name: _utc(hop[name], label=name)
                for name in (
                    "origin_event_created_at", "delivery_enqueued_at", "first_attempt_at",
                    "destination_received_at", "destination_applied_at",
                    "source_acknowledged_at",
                )
            }
            metrics = hop_values.setdefault(
                hop_name,
                {
                    "delivery_enqueued_to_first_attempt": [],
                    "first_attempt_to_received": [],
                    "received_to_applied": [],
                    "applied_to_acknowledged": [],
                    "origin_event_created_to_applied": [],
                    "origin_event_created_to_acknowledged": [],
                },
            )
            pairs = {
                "delivery_enqueued_to_first_attempt": (
                    "delivery_enqueued_at", "first_attempt_at"
                ),
                "first_attempt_to_received": ("first_attempt_at", "destination_received_at"),
                "received_to_applied": ("destination_received_at", "destination_applied_at"),
                "applied_to_acknowledged": ("destination_applied_at", "source_acknowledged_at"),
                "origin_event_created_to_applied": (
                    "origin_event_created_at", "destination_applied_at"
                ),
                "origin_event_created_to_acknowledged": (
                    "origin_event_created_at", "source_acknowledged_at"
                ),
            }
            for metric, (start, finish) in pairs.items():
                metrics[metric].append(
                    round(max(0.0, (points[finish] - points[start]).total_seconds()), 6)
                )
    return {
        "routes": {
            route: _summary(values) for route, values in sorted(route_values.items())
        },
        "physical_hops": {
            hop: {
                "sample_count": len(next(iter(metrics.values()))),
                **{
                    metric: _summary(values)
                    for metric, values in sorted(metrics.items())
                },
            }
            for hop, metrics in sorted(hop_values.items())
        },
    }


def _clock_evidence(value: Any, *, observed_at: datetime) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != {
        "basis", "maximum_absolute_offset_ms", "sites"
    } or value.get("basis") != "utc-ntp-plus-controller-monotonic":
        raise SyncTimingEvidenceError("sync timing clock evidence is invalid")
    maximum = _finite_number(
        value["maximum_absolute_offset_ms"],
        label="maximum clock offset",
    )
    if maximum > 1000.0:
        raise SyncTimingEvidenceError("sync timing clock offset exceeds one second")
    sites = value["sites"]
    if not isinstance(sites, dict) or set(sites) != {
        "bot_fi", "webapp_fi", "webapp_ir"
    }:
        raise SyncTimingEvidenceError("sync timing lacks all site clocks")
    offsets: dict[str, float] = {}
    for site, item in sites.items():
        if not isinstance(item, dict) or set(item) != {
            "synchronized", "offset_ms", "observed_at",
            "measurement_source", "measurement_raw_sha256", "measurement_raw",
        } or item["synchronized"] is not True:
            raise SyncTimingEvidenceError("sync timing site clock is not synchronized")
        if (
            item["measurement_source"] not in {
                "chronyc_tracking_csv", "ntpq_system_variables"
            }
            or SHA256.fullmatch(str(item["measurement_raw_sha256"])) is None
            or not isinstance(item["measurement_raw"], str)
            or not item["measurement_raw"]
            or len(item["measurement_raw"].encode("utf-8")) > 64 * 1024
            or hashlib.sha256(
                item["measurement_raw"].encode("utf-8")
            ).hexdigest() != item["measurement_raw_sha256"]
        ):
            raise SyncTimingEvidenceError("sync timing clock measurement identity is invalid")
        offset = _finite_number(
            abs(_finite_number(item["offset_ms"], label=f"{site} clock offset", minimum=-math.inf)),
            label=f"{site} absolute clock offset",
        )
        if offset > maximum:
            raise SyncTimingEvidenceError("sync timing site offset exceeds declared maximum")
        captured = _utc(item["observed_at"], label=f"{site} clock observed_at")
        if abs((observed_at - captured).total_seconds()) > 300:
            raise SyncTimingEvidenceError("sync timing clock evidence is stale")
        offsets[site] = offset
    return offsets


def _verify_hop(
    value: Any,
    *,
    expected: tuple[str, str],
    clock_tolerance_seconds: float,
) -> tuple[datetime, datetime, datetime, str, str]:
    fields = {
        "source_site", "destination_site", "source_event_id",
        "source_envelope_hash", "destination_receipt_hash",
        "origin_event_created_at", "delivery_enqueued_at", "first_attempt_at",
        "destination_received_at", "destination_applied_at",
        "source_acknowledged_at", "attempt_count", "payload_bytes",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise SyncTimingEvidenceError("sync timing hop fields are invalid")
    if (value["source_site"], value["destination_site"]) != expected:
        raise SyncTimingEvidenceError("sync timing hop does not match its route")
    if SAFE_ID.fullmatch(str(value["source_event_id"])) is None:
        raise SyncTimingEvidenceError("sync timing event identity is invalid")
    for name in ("source_envelope_hash", "destination_receipt_hash"):
        if SHA256.fullmatch(str(value[name])) is None:
            raise SyncTimingEvidenceError("sync timing event hash is invalid")
    if type(value["attempt_count"]) is not int or value["attempt_count"] < 1:
        raise SyncTimingEvidenceError("sync timing attempt count is invalid")
    if type(value["payload_bytes"]) is not int or value["payload_bytes"] <= 0:
        raise SyncTimingEvidenceError("sync timing payload size is invalid")
    points = [
        _utc(value[name], label=name)
        for name in (
            "origin_event_created_at", "delivery_enqueued_at", "first_attempt_at",
            "destination_received_at", "destination_applied_at",
            "source_acknowledged_at",
        )
    ]
    for earlier, later in zip(points, points[1:]):
        if (earlier - later).total_seconds() > clock_tolerance_seconds:
            raise SyncTimingEvidenceError("sync timing hop timestamps are out of order")
    return (
        points[0],
        points[1],
        points[-2],
        str(value["source_event_id"]),
        str(value["source_envelope_hash"]),
    )


def _verify_backlog(value: Any, *, required: bool) -> dict[str, Any]:
    fields = {
        "required", "peak_pending_events", "initial_oldest_age_seconds",
        "drained_to_zero", "drain_duration_seconds", "live_ingress_events",
        "applied_events", "samples",
    }
    if not isinstance(value, dict) or set(value) != fields or value["required"] is not required:
        raise SyncTimingEvidenceError("sync timing backlog evidence is invalid")
    if type(value["peak_pending_events"]) is not int or value["peak_pending_events"] < 0:
        raise SyncTimingEvidenceError("sync timing peak backlog is invalid")
    if type(value["live_ingress_events"]) is not int or value["live_ingress_events"] < 0:
        raise SyncTimingEvidenceError("sync timing live ingress count is invalid")
    if type(value["applied_events"]) is not int or value["applied_events"] < 0:
        raise SyncTimingEvidenceError("sync timing applied count is invalid")
    _finite_number(value["initial_oldest_age_seconds"], label="initial backlog age")
    drain = _finite_number(value["drain_duration_seconds"], label="backlog drain duration")
    samples = value["samples"]
    if not isinstance(samples, list) or (required and len(samples) < 2):
        raise SyncTimingEvidenceError("sync timing backlog samples are incomplete")
    previous: datetime | None = None
    normalized: list[dict[str, Any]] = []
    for item in samples:
        if not isinstance(item, dict) or set(item) != {
            "observed_at", "pending_events", "oldest_age_seconds",
            "ingress_events_per_second", "apply_events_per_second",
        }:
            raise SyncTimingEvidenceError("sync timing backlog sample is invalid")
        observed = _utc(item["observed_at"], label="backlog observed_at")
        if previous is not None and observed <= previous:
            raise SyncTimingEvidenceError("sync timing backlog samples are not ordered")
        previous = observed
        if type(item["pending_events"]) is not int or item["pending_events"] < 0:
            raise SyncTimingEvidenceError("sync timing pending backlog is invalid")
        normalized.append({
            "observed_at": observed.isoformat(),
            "pending_events": item["pending_events"],
            "oldest_age_seconds": _finite_number(
                item["oldest_age_seconds"], label="backlog oldest age"
            ),
            "ingress_events_per_second": _finite_number(
                item["ingress_events_per_second"], label="backlog ingress rate"
            ),
            "apply_events_per_second": _finite_number(
                item["apply_events_per_second"], label="backlog apply rate"
            ),
        })
    if required and (
        value["peak_pending_events"] <= 0
        or value["drained_to_zero"] is not True
        or not normalized
        or normalized[0]["pending_events"] <= 0
        or normalized[-1]["pending_events"] != 0
        or value["applied_events"] < value["peak_pending_events"]
        or drain <= 0
    ):
        raise SyncTimingEvidenceError("sync timing backlog was not proven to drain")
    if required and value["live_ingress_events"] <= 0:
        raise SyncTimingEvidenceError(
            "sync timing recovery did not prove live ingress while draining"
        )
    if not required and (
        value["drained_to_zero"] is not True
        or value["peak_pending_events"] != 0
        or drain != 0
    ):
        raise SyncTimingEvidenceError("steady-state timing unexpectedly declares backlog")
    return {"drain_duration_seconds": drain, "sample_count": len(normalized)}


def verify_sync_timing_evidence(
    artifact: dict[str, Any],
    *,
    scenario_id: str,
) -> dict[str, Any]:
    """Validate and recompute one raw timing artifact without trusting summaries."""

    policy = sync_timing_policy(scenario_id)
    if policy is None:
        raise SyncTimingEvidenceError("scenario has no synchronization timing policy")
    fields = {
        "schema", "scenario_id", "state", "captured_at", "clock",
        "load", "samples", "backlog", "summary",
    }
    if (
        not isinstance(artifact, dict)
        or set(artifact) != fields
        or artifact.get("schema") != SYNC_TIMING_SCHEMA
        or artifact.get("scenario_id") != scenario_id
        or artifact.get("state") != policy["state"]
    ):
        raise SyncTimingEvidenceError("sync timing artifact identity is invalid")
    captured_at = _utc(artifact["captured_at"], label="timing captured_at")
    offsets = _clock_evidence(artifact["clock"], observed_at=captured_at)
    clock_tolerance = (max(offsets.values()) * 2 / 1000.0) + 0.010

    load = artifact["load"]
    if not isinstance(load, dict) or set(load) != {
        "target_requests_per_second", "observed_requests_per_second",
        "window_seconds", "request_count", "bot_request_count",
        "webapp_request_count",
    }:
        raise SyncTimingEvidenceError("sync timing load evidence is invalid")
    target_rps = _finite_number(
        load["target_requests_per_second"], label="target request rate"
    )
    observed_rps = _finite_number(
        load["observed_requests_per_second"], label="observed request rate"
    )
    window_seconds = _finite_number(load["window_seconds"], label="load window")
    if (
        type(load["request_count"]) is not int
        or load["request_count"] < 0
        or type(load["bot_request_count"]) is not int
        or load["bot_request_count"] < 0
        or type(load["webapp_request_count"]) is not int
        or load["webapp_request_count"] < 0
        or load["bot_request_count"] + load["webapp_request_count"]
        != load["request_count"]
    ):
        raise SyncTimingEvidenceError("sync timing request count is invalid")
    if observed_rps < float(policy["minimum_observed_rps"]):
        raise SyncTimingEvidenceError("sync timing observed load is below policy")
    if policy["minimum_observed_rps"] > 0 and (
        target_rps < policy["minimum_observed_rps"]
        or window_seconds <= 0
        or abs((load["request_count"] / window_seconds) - observed_rps)
        > max(0.01, observed_rps * 0.05)
    ):
        raise SyncTimingEvidenceError("sync timing load cardinality is inconsistent")
    required_split = policy["required_surface_split"]
    if required_split is not None:
        if load["request_count"] <= 0:
            raise SyncTimingEvidenceError("sync timing 50/50 surface split is empty")
        bot_fraction = load["bot_request_count"] / load["request_count"]
        webapp_fraction = load["webapp_request_count"] / load["request_count"]
        maximum_deviation = float(required_split["maximum_absolute_deviation"])
        if (
            abs(bot_fraction - float(required_split["bot_fraction"]))
            > maximum_deviation
            or abs(webapp_fraction - float(required_split["webapp_fraction"]))
            > maximum_deviation
        ):
            raise SyncTimingEvidenceError("sync timing 50/50 surface split differs")

    samples = artifact["samples"]
    if not isinstance(samples, list):
        raise SyncTimingEvidenceError("sync timing samples are missing")
    durations: dict[str, list[float]] = {route: [] for route in policy["routes"]}
    sample_ids: set[str] = set()
    correlations: set[str] = set()
    event_ids: set[str] = set()
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {
            "sample_id", "correlation_id", "route", "state", "hops",
            "controller_observed_duration_seconds", "status",
        }:
            raise SyncTimingEvidenceError("sync timing sample fields are invalid")
        sample_id = str(sample["sample_id"])
        correlation_id = str(sample["correlation_id"])
        route = str(sample["route"])
        if (
            SAFE_ID.fullmatch(sample_id) is None
            or SAFE_ID.fullmatch(correlation_id) is None
            or sample_id in sample_ids
            or correlation_id in correlations
            or route not in durations
            or sample["state"] != policy["state"]
            or sample["status"] != "applied_and_acknowledged"
        ):
            raise SyncTimingEvidenceError("sync timing sample identity/status is invalid")
        hops = sample["hops"]
        expected_hops = ROUTE_HOPS[route]
        if not isinstance(hops, list) or len(hops) != len(expected_hops):
            raise SyncTimingEvidenceError("sync timing route hop count is invalid")
        first: datetime | None = None
        final: datetime | None = None
        previous_applied: datetime | None = None
        route_event_id: str | None = None
        route_envelope_hash: str | None = None
        for hop, expected_hop in zip(hops, expected_hops):
            created, delivery_enqueued, applied, event_id, envelope_hash = _verify_hop(
                hop,
                expected=expected_hop,
                clock_tolerance_seconds=clock_tolerance,
            )
            if route_event_id is None:
                if event_id in event_ids:
                    raise SyncTimingEvidenceError("sync timing event evidence was reused")
                event_ids.add(event_id)
                route_event_id = event_id
                route_envelope_hash = envelope_hash
            elif event_id != route_event_id or envelope_hash != route_envelope_hash:
                raise SyncTimingEvidenceError(
                    "sync timing relay changed event identity or envelope"
                )
            if previous_applied is not None and (
                previous_applied - delivery_enqueued
            ).total_seconds() > clock_tolerance:
                raise SyncTimingEvidenceError("sync timing relay was ready before prior apply")
            first = first or created
            final = applied
            previous_applied = applied
        assert first is not None and final is not None
        wall_duration = max(0.0, (final - first).total_seconds())
        observed_duration = _finite_number(
            sample["controller_observed_duration_seconds"],
            label="controller observed sync duration",
        )
        if (
            observed_duration > float(policy["maximum_sample_seconds"])
            or abs(observed_duration - wall_duration) > clock_tolerance + 2.0
        ):
            raise SyncTimingEvidenceError("sync timing duration exceeds policy or clocks")
        sample_ids.add(sample_id)
        correlations.add(correlation_id)
        durations[route].append(observed_duration)

    minimum = int(policy["minimum_samples_per_route"])
    if any(len(values) < minimum for values in durations.values()):
        raise SyncTimingEvidenceError("sync timing route sample coverage is incomplete")
    computed_summary = summarize_sync_timing_samples(samples)
    expected_route_summary = {
        route: _summary(values) for route, values in durations.items()
    }
    for route, limits in policy["route_percentile_limits"].items():
        observed = expected_route_summary[route]
        if any(
            float(observed[name]) >= float(limit)
            for name, limit in limits.items()
        ):
            raise SyncTimingEvidenceError(
                f"sync timing route percentile SLO failed: {route}"
            )
    if computed_summary["routes"] != expected_route_summary:
        raise SyncTimingEvidenceError("sync timing route summary computation differs")
    if artifact["summary"] != computed_summary:
        raise SyncTimingEvidenceError("sync timing summary differs from raw samples")
    backlog = _verify_backlog(
        artifact["backlog"], required=bool(policy["backlog_required"])
    )
    return {
        "schema": SYNC_TIMING_SCHEMA,
        "scenario_id": scenario_id,
        "state": policy["state"],
        "route_summary": computed_summary["routes"],
        "physical_hop_summary": computed_summary["physical_hops"],
        "sample_count": len(samples),
        "observed_requests_per_second": observed_rps,
        "backlog": backlog,
        "maximum_absolute_clock_offset_ms": max(offsets.values()),
    }
