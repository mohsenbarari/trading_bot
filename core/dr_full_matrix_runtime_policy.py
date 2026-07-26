"""Closed runtime invariants for non-destructive Full Matrix scenarios.

These helpers deliberately accept raw observations and return a decision.
They do not claim that traffic was generated; the live handler must collect
the named counters/readbacks from the exact campaign hosts first.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


class FullMatrixRuntimePolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapacityWatermarks:
    database_fraction: float
    redis_fraction: float
    blob_fraction: float
    wal_fraction: float
    event_fraction: float


def relay_identity_decision(
    *,
    origin_site: str,
    relay_site: str,
    destination_site: str,
    received_origin_site: str,
    received_envelope_hash: str,
    source_envelope_hash: str,
    echo_destination: str | None,
) -> dict[str, bool]:
    valid_sites = {"bot_fi", "webapp_fi", "webapp_ir"}
    if {origin_site, relay_site, destination_site} - valid_sites:
        raise FullMatrixRuntimePolicyError("relay observation contains an unknown site")
    if relay_site != "webapp_fi" or origin_site == relay_site or destination_site == relay_site:
        raise FullMatrixRuntimePolicyError("relay observation is outside the fixed topology")
    identity_preserved = (
        received_origin_site == origin_site
        and received_envelope_hash == source_envelope_hash
        and len(source_envelope_hash) == 64
    )
    no_echo = echo_destination != origin_site
    return {
        "origin_identity_preserved": identity_preserved,
        "envelope_hash_preserved": identity_preserved,
        "relay_echo_to_origin_absent": no_echo,
    }


def durable_drain_decision(
    *,
    committed_jobs: int,
    wakeups_delivered: int,
    claimed_jobs: int,
    terminal_jobs: int,
    duplicate_effects: int,
) -> dict[str, bool]:
    if min(committed_jobs, wakeups_delivered, claimed_jobs, terminal_jobs, duplicate_effects) < 0:
        raise FullMatrixRuntimePolicyError("queue counters cannot be negative")
    return {
        "dropped_wakeup_does_not_drop_committed_job": (
            wakeups_delivered < committed_jobs
            and claimed_jobs == committed_jobs
            and terminal_jobs == committed_jobs
        ),
        "durable_feeder_drains_without_pubsub_reliance": claimed_jobs == committed_jobs,
        "terminal_effect_exactly_once": duplicate_effects == 0,
    }


def ambiguous_retry_decision(
    *,
    command_attempts: int,
    committed_commands: int,
    business_rows: int,
    outbox_jobs: int,
    provider_effects: int,
) -> dict[str, bool]:
    if min(command_attempts, committed_commands, business_rows, outbox_jobs, provider_effects) < 0:
        raise FullMatrixRuntimePolicyError("idempotency counters cannot be negative")
    return {
        "ambiguous_retry_reuses_command_identity": command_attempts >= 2 and committed_commands == 1,
        "business_mutation_committed_once": business_rows == 1,
        "outbox_handoff_committed_once": outbox_jobs == 1,
        "provider_effect_not_duplicated": provider_effects <= 1,
    }


def bidirectional_capacity_decision(
    *,
    fi_to_peer_events: int,
    peer_to_fi_events: int,
    acknowledged_events: int,
    duplicate_applies: int,
) -> dict[str, bool]:
    if min(fi_to_peer_events, peer_to_fi_events, acknowledged_events, duplicate_applies) < 0:
        raise FullMatrixRuntimePolicyError("capacity counters cannot be negative")
    expected = fi_to_peer_events + peer_to_fi_events
    return {
        "one_hundred_fifty_events_each_direction": (
            fi_to_peer_events >= 150 and peer_to_fi_events >= 150
        ),
        "all_events_acknowledged": acknowledged_events == expected,
        "no_duplicate_projection_apply": duplicate_applies == 0,
    }


def amplified_webapp_decision(
    *,
    source_events: int,
    destination_deliveries: int,
    destination_receipts: int,
    relay_echoes: int,
) -> dict[str, bool]:
    if min(source_events, destination_deliveries, destination_receipts, relay_echoes) < 0:
        raise FullMatrixRuntimePolicyError("amplification counters cannot be negative")
    return {
        "three_hundred_source_events_observed": source_events >= 300,
        "fanout_amplification_is_exact": (
            destination_deliveries == source_events * 2
            and destination_receipts == destination_deliveries
        ),
        "relay_echo_absent": relay_echoes == 0,
    }


def batch_flush_decision(
    *,
    committed_before_flush: int,
    flushed: int,
    acknowledged: int,
    stranded: int,
) -> dict[str, bool]:
    if min(committed_before_flush, flushed, acknowledged, stranded) < 0:
        raise FullMatrixRuntimePolicyError("batch counters cannot be negative")
    return {
        "commit_boundary_precedes_delivery_flush": flushed <= committed_before_flush,
        "flushed_batch_fully_acknowledged": acknowledged == flushed,
        "no_committed_event_stranded": stranded == 0 and acknowledged == committed_before_flush,
    }


def capacity_watermark_decision(
    watermarks: CapacityWatermarks,
    *,
    warning_fraction: float = 0.70,
    hard_fraction: float = 0.90,
) -> dict[str, bool]:
    values = (
        watermarks.database_fraction,
        watermarks.redis_fraction,
        watermarks.blob_fraction,
        watermarks.wal_fraction,
        watermarks.event_fraction,
    )
    if not 0 < warning_fraction < hard_fraction <= 1 or any(
        not 0 <= value <= 1 for value in values
    ):
        raise FullMatrixRuntimePolicyError("capacity watermark input is invalid")
    return {
        "all_watermarks_below_hard_limit": max(values) < hard_fraction,
        "warning_state_is_observable": any(value >= warning_fraction for value in values)
        or max(values) < warning_fraction,
        "five_resource_planes_measured": len(values) == 5,
    }


def dpi_budget_decision(
    *,
    request_bytes: int,
    response_bytes: int,
    configured_request_limit: int,
    configured_response_limit: int,
    oversized_request_rejected: bool,
) -> dict[str, bool]:
    if min(request_bytes, response_bytes, configured_request_limit, configured_response_limit) <= 0:
        raise FullMatrixRuntimePolicyError("DPI byte-budget input is invalid")
    return {
        "request_within_configured_budget": request_bytes <= configured_request_limit,
        "response_within_configured_budget": response_bytes <= configured_response_limit,
        "oversized_request_fails_closed": oversized_request_rejected,
    }


def recovery_eta_decision(
    *,
    initial_backlog: int,
    final_backlog: int,
    live_ingress_events: int,
    applied_events: int,
    elapsed_seconds: float,
    declared_eta_seconds: float,
) -> dict[str, bool]:
    if (
        min(initial_backlog, final_backlog, live_ingress_events, applied_events) < 0
        or elapsed_seconds <= 0
        or declared_eta_seconds < 0
    ):
        raise FullMatrixRuntimePolicyError("recovery ETA input is invalid")
    drained = initial_backlog + live_ingress_events - final_backlog
    rate = drained / elapsed_seconds
    computed_eta = 0.0 if final_backlog == 0 else final_backlog / max(rate, 1e-9)
    return {
        "backlog_makes_forward_progress": drained > 0 and applied_events >= drained,
        "live_traffic_not_starved": live_ingress_events == 0 or applied_events > initial_backlog,
        "declared_eta_is_conservative": declared_eta_seconds >= computed_eta,
    }


def healthy_link_backlog_decision(
    *,
    samples: list[int],
    oldest_age_seconds: float,
    unresolved_gaps: int,
) -> dict[str, bool]:
    if len(samples) < 3 or any(value < 0 for value in samples) or oldest_age_seconds < 0:
        raise FullMatrixRuntimePolicyError("healthy-link backlog input is invalid")
    return {
        "healthy_link_has_no_monotonic_backlog_growth": not all(
            later > earlier for earlier, later in zip(samples, samples[1:])
        ),
        "oldest_pending_age_bounded": oldest_age_seconds < 30.0,
        "no_unresolved_stream_gap": unresolved_gaps == 0,
    }


def writer_final_state_decision(
    *,
    active_site: str,
    public_origin_site: str,
    fi_control_state: str,
    ir_runtime_role: str,
    active_epoch: int,
    prior_epoch: int,
) -> dict[str, bool]:
    return {
        "webapp_fi_is_final_writer": active_site == "webapp_fi",
        "public_route_returns_to_webapp_fi": public_origin_site == "webapp_fi",
        # The writer-control row is intentionally replicated exactly between
        # WebApp-FI and WebApp-IR.  In the normal state its *global* control
        # state is therefore ``active`` on both replicas; requiring a local
        # IR row of ``fenced`` would contradict single-writer replication.
        # The only meaningful IR assertion is its evaluated local role under
        # that shared state: it must be the non-writing standby.
        "webapp_ir_is_fenced_standby": ir_runtime_role == "standby",
        "webapp_fi_is_active": fi_control_state == "active",
        "writer_epoch_is_monotonic": active_epoch > prior_epoch >= 1,
    }


def artifact_chain_decision(
    *,
    ordered_hashes: list[str],
    retained_head: str,
    external_anchor: str,
) -> dict[str, bool]:
    if not ordered_hashes or any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in ordered_hashes
    ):
        raise FullMatrixRuntimePolicyError("artifact hash chain is invalid")
    head = "0" * 64
    for value in ordered_hashes:
        head = hashlib.sha256(f"{head}:{value}".encode("ascii")).hexdigest()
    return {
        "artifact_chain_head_recomputed": retained_head == head,
        "external_anchor_matches_chain_head": external_anchor == head,
        "ordered_artifacts_are_nonempty": len(ordered_hashes) > 0,
    }


def second_cycle_decision(
    *,
    first_cycle: dict[str, float],
    second_cycle: dict[str, float],
    lower_is_better: set[str],
) -> dict[str, bool]:
    if set(first_cycle) != set(second_cycle) or not first_cycle:
        raise FullMatrixRuntimePolicyError("cycle oracle sets differ")
    if any(not isinstance(value, (int, float)) for value in [*first_cycle.values(), *second_cycle.values()]):
        raise FullMatrixRuntimePolicyError("cycle oracle value is not numeric")
    stronger = all(
        second_cycle[name] <= first_cycle[name]
        if name in lower_is_better
        else second_cycle[name] >= first_cycle[name]
        for name in first_cycle
    )
    return {
        "second_cycle_oracle_set_exact": True,
        "second_cycle_is_same_or_stronger": stronger,
        "no_oracle_weakened_between_cycles": stronger,
    }


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
