"""Pure failover-saga expiry recovery classification."""

from __future__ import annotations

from collections.abc import Iterable


FORWARD_FAILOVER_STEPS = (
    "classification_verified",
    "source_fenced",
    "source_connections_drained",
    "target_ready",
    "target_term_acquired",
    "route_switched",
    "public_route_verified",
)


def expired_plan_recovery_decision(
    *,
    completed: Iterable[str],
    started: Iterable[str],
) -> dict[str, object]:
    """Permit no forward work after expiry and classify the safe recovery."""

    completed_set = set(completed)
    started_set = set(started)
    known = set(FORWARD_FAILOVER_STEPS)
    if not completed_set.issubset(known) or not started_set.issubset(known):
        return {
            "decision": "fail_closed_unknown_step",
            "completed_steps": (),
            "ambiguous_started_steps": (),
        }
    completed_steps = tuple(
        step for step in FORWARD_FAILOVER_STEPS if step in completed_set
    )
    ambiguous_started = tuple(
        step
        for step in FORWARD_FAILOVER_STEPS
        if step in started_set - completed_set
    )
    mutations = (
        (set(completed_steps) | set(ambiguous_started))
        - {"classification_verified"}
    )
    return {
        "decision": (
            "rollback_to_safe_fenced"
            if mutations
            else "expire_without_mutation"
        ),
        "completed_steps": completed_steps,
        "ambiguous_started_steps": ambiguous_started,
    }
