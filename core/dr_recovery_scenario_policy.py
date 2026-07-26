"""Pure recovery/failback gates used by the live Matrix and runtime code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecoveryWriterDecision:
    active_site: str
    allow_failback: bool


def recovery_writer_decision(
    *,
    connectivity_online: bool,
    convergence_complete: bool,
    failback_plan_approved: bool,
) -> RecoveryWriterDecision:
    allow = (
        connectivity_online
        and convergence_complete
        and failback_plan_approved
    )
    return RecoveryWriterDecision(
        active_site="webapp_fi" if allow else "webapp_ir",
        allow_failback=allow,
    )


def final_write_barrier_decision(
    *,
    source_admission_fenced: bool,
    source_application_connections: int,
    final_tail_applied: bool,
    post_fence_authoritative_arrivals: int,
) -> str:
    if (
        source_admission_fenced
        and source_application_connections == 0
        and final_tail_applied
        and post_fence_authoritative_arrivals == 0
    ):
        return "barrier_complete"
    return "block_failback"


def connection_drain_decision(
    *,
    old_http_connections: int,
    old_websocket_connections: int,
    old_epoch_sessions: int,
) -> str:
    if min(
        old_http_connections,
        old_websocket_connections,
        old_epoch_sessions,
    ) < 0:
        return "invalid"
    if (
        old_http_connections
        or old_websocket_connections
        or old_epoch_sessions
    ):
        return "draining"
    return "drained"


def database_blob_reconcile_decision(
    *,
    database_committed: bool,
    blob_staged: bool,
    blob_hash_verified: bool,
) -> str:
    if database_committed and blob_hash_verified:
        return "publish_or_keep_blob"
    if database_committed:
        return "block_until_verified_blob"
    if blob_staged:
        return "discard_uncommitted_blob"
    return "no_change"
