"""Pure safety decisions shared by failover fault injection and its oracle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ConnectivityVoteDecision:
    mode: str
    promote_ir: bool


def connectivity_vote_decision(
    *,
    domestic_fi_reachable: Iterable[bool],
    domestic_ir_reachable: Iterable[bool],
    domestic_witness_reachable: Iterable[bool],
    global_fi_reachable: bool,
    consecutive_rounds: int,
) -> ConnectivityVoteDecision:
    fi = tuple(domestic_fi_reachable)
    ir = tuple(domestic_ir_reachable)
    witness = tuple(domestic_witness_reachable)
    if (
        len(fi) < 2
        or len(fi) != len(ir)
        or len(fi) != len(witness)
        or consecutive_rounds < 3
    ):
        return ConnectivityVoteDecision("ambiguous", False)
    online = sum(fi) >= 2
    isolated = (
        sum(not value for value in fi) >= 2
        and sum(ir) >= 2
        and sum(witness) >= 2
        and global_fi_reachable
    )
    if online and not isolated:
        return ConnectivityVoteDecision("online", False)
    if isolated and not online:
        return ConnectivityVoteDecision("isolated", True)
    return ConnectivityVoteDecision("ambiguous", False)


def transition_reservation_decision(
    *,
    active_operation_id: str | None,
    requested_operation_id: str,
    requested_plan_hash: str,
    active_plan_hash: str | None,
) -> str:
    if not requested_operation_id or len(requested_plan_hash) != 64:
        return "reject"
    if active_operation_id is None:
        return "reserve"
    if (
        active_operation_id == requested_operation_id
        and active_plan_hash == requested_plan_hash
    ):
        return "resume"
    return "reject"


def route_verification_decision(
    *,
    expected_origin_ip: str,
    observed_pop_origins: Iterable[str],
    tls_valid: bool,
    health_cacheable: bool,
) -> str:
    observed = tuple(observed_pop_origins)
    if (
        not tls_valid
        or health_cacheable
        or not observed
        or any(value != expected_origin_ip for value in observed)
    ):
        return "safe_unavailable"
    return "verified"


def transition_mutation_gate(*, writer_control_state: str) -> str:
    return (
        "allow"
        if writer_control_state == "active"
        else "reject_transition_in_progress"
    )


def provider_mutation_recovery(
    *,
    before_ip: str,
    target_ip: str,
    put_response_observed: bool,
    readback_ip: str,
) -> str:
    del put_response_observed
    if readback_ip == target_ip:
        return "completed_without_replay"
    if readback_ip == before_ip:
        return "retry_same_idempotent_mutation"
    return "block_ambiguous_provider_state"


def partition_delivery_decision(
    *,
    writer_epoch: int,
    work_epoch: int,
    destination_applied: bool,
    source_acknowledged: bool,
) -> str:
    if work_epoch != writer_epoch:
        return "fence_stale_work"
    if source_acknowledged and not destination_applied:
        return "quarantine_invalid_ack"
    if not destination_applied:
        return "durable_pending"
    return "retire_after_apply"
