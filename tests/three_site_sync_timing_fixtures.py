"""Deterministic valid timing artifacts used by Full Matrix controller tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
from typing import Any

from core.three_site_sync_timing import (
    ROUTE_HOPS,
    SYNC_TIMING_SCHEMA,
    summarize_sync_timing_samples,
    sync_timing_policy,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_sync_timing_artifact(
    scenario_id: str,
    *,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    policy = sync_timing_policy(scenario_id)
    if policy is None:
        raise ValueError(f"scenario has no timing policy: {scenario_id}")
    captured = captured_at or datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    samples: list[dict[str, Any]] = []
    durations: dict[str, list[float]] = {route: [] for route in policy["routes"]}
    for route_index, route in enumerate(policy["routes"]):
        for sample_index in range(policy["minimum_samples_per_route"]):
            cursor = captured + timedelta(
                seconds=(route_index * policy["minimum_samples_per_route"] + sample_index) * 0.2
            )
            first = cursor
            event_id = f"evt:{scenario_id}:{route_index}:{sample_index}"
            envelope_hash = _hash(f"envelope:{event_id}")
            hops = []
            for hop_index, (source, destination) in enumerate(ROUTE_HOPS[route]):
                sender_ready = cursor
                attempted = sender_ready + timedelta(milliseconds=10)
                received = sender_ready + timedelta(milliseconds=30)
                applied = sender_ready + timedelta(milliseconds=50)
                acknowledged = sender_ready + timedelta(milliseconds=60)
                hops.append(
                    {
                        "source_site": source,
                        "destination_site": destination,
                        "source_event_id": event_id,
                        "source_envelope_hash": envelope_hash,
                        "destination_receipt_hash": _hash(
                            f"receipt:{event_id}:{destination}"
                        ),
                        "origin_event_created_at": first.isoformat(),
                        "delivery_enqueued_at": sender_ready.isoformat(),
                        "first_attempt_at": attempted.isoformat(),
                        "destination_received_at": received.isoformat(),
                        "destination_applied_at": applied.isoformat(),
                        "source_acknowledged_at": acknowledged.isoformat(),
                        "attempt_count": 1,
                        "payload_bytes": 512,
                    }
                )
                cursor = applied + timedelta(milliseconds=10)
            duration = round((applied - first).total_seconds(), 6)
            durations[route].append(duration)
            samples.append(
                {
                    "sample_id": f"sample:{scenario_id}:{route_index}:{sample_index}",
                    "correlation_id": f"corr:{scenario_id}:{route_index}:{sample_index}",
                    "route": route,
                    "state": policy["state"],
                    "hops": hops,
                    "controller_observed_duration_seconds": duration,
                    "status": "applied_and_acknowledged",
                }
            )

    required_backlog = bool(policy["backlog_required"])
    backlog_samples = []
    peak = 0
    drain_seconds = 0.0
    applied_events = 0
    if required_backlog:
        peak = max(1, policy["minimum_samples_per_route"])
        drain_seconds = 5.0
        applied_events = peak + 1
        backlog_samples = [
            {
                "observed_at": captured.isoformat(),
                "pending_events": peak,
                "oldest_age_seconds": 60.0,
                "ingress_events_per_second": 1.0,
                "apply_events_per_second": 0.0,
            },
            {
                "observed_at": (captured + timedelta(seconds=5)).isoformat(),
                "pending_events": 0,
                "oldest_age_seconds": 0.0,
                "ingress_events_per_second": 1.0,
                "apply_events_per_second": float(peak) / 5.0,
            },
        ]

    minimum_rps = float(policy["minimum_observed_rps"])
    observed_rps = minimum_rps
    target_rps = minimum_rps
    window_seconds = 2.0 if minimum_rps > 0 else 0.0
    request_count = math.ceil(observed_rps * window_seconds)
    bot_request_count = (request_count + 1) // 2
    webapp_request_count = request_count - bot_request_count
    return {
        "schema": SYNC_TIMING_SCHEMA,
        "scenario_id": scenario_id,
        "state": policy["state"],
        "captured_at": captured.isoformat(),
        "clock": {
            "basis": "utc-ntp-plus-controller-monotonic",
            "maximum_absolute_offset_ms": 10.0,
            "sites": {
                site: {
                    "synchronized": True,
                    "offset_ms": 0.0,
                    "observed_at": captured.isoformat(),
                    "measurement_source": "chronyc_tracking_csv",
                    "measurement_raw_sha256": _hash(f"clock:{site}"),
                    "measurement_raw": f"clock:{site}",
                }
                for site in ("bot_fi", "webapp_fi", "webapp_ir")
            },
        },
        "load": {
            "target_requests_per_second": target_rps,
            "observed_requests_per_second": observed_rps,
            "window_seconds": window_seconds,
            "request_count": request_count,
            "bot_request_count": bot_request_count,
            "webapp_request_count": webapp_request_count,
        },
        "samples": samples,
        "backlog": {
            "required": required_backlog,
            "peak_pending_events": peak,
            "initial_oldest_age_seconds": 60.0 if required_backlog else 0.0,
            "drained_to_zero": True,
            "drain_duration_seconds": drain_seconds,
            "live_ingress_events": max(1, request_count) if required_backlog else 0,
            "applied_events": applied_events,
            "samples": backlog_samples,
        },
        "summary": summarize_sync_timing_samples(samples),
    }
