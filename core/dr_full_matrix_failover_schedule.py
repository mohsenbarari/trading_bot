"""Closed failover/failback delegation schedule for one Full Matrix campaign.

The long-lived staging approval session is never treated as a generic
orchestration capability.  Before a campaign is approved, this module derives
every permitted Writer transition from the immutable Matrix catalog.  Runtime
code may then request a short-lived, action-bound Witness relay receipt only
for the exact next entry in this schedule.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable
from uuid import NAMESPACE_URL, UUID, uuid5

from core.canonical_json import canonical_json_bytes
from core.runtime_sites import SITE_WEBAPP_FI, SITE_WEBAPP_IR
from core.three_site_execution_safety import EXECUTION_CLASSES
from core.three_site_full_matrix_campaign import (
    PHASE_SCENARIOS,
    scenario_catalog_sha256,
    scenarios_for_execution_class,
)


SCHEDULE_SCHEMA = "three-site-staging-full-matrix-failover-schedule-v1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
TRANSITION_ACTIONS = ("promote_ir", "failback_fi")

# Every listed scenario deliberately enters IR-active state and returns to the
# normal FI-active state.  A crash/retry reuses the same operation id; it does
# not allocate another schedule entry or Writer epoch.
FAILOVER_TRANSITION_SCENARIOS = frozenset(
    {
        *PHASE_SCENARIOS["partitions_failover"],
        *PHASE_SCENARIOS["recovery_failback"],
        "session_failover_contract",
    }
)


class FullMatrixFailoverScheduleError(RuntimeError):
    pass


def _operation_id(
    *,
    campaign_id: str,
    execution_class: str,
    release_sha: str,
    iteration: int,
    scenario_id: str,
    transition_index: int,
    action: str,
) -> str:
    material = json.dumps(
        {
            "action": action,
            "campaign_id": campaign_id,
            "execution_class": execution_class,
            "iteration": iteration,
            "release_sha": release_sha,
            "scenario_id": scenario_id,
            "transition_index": transition_index,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return str(uuid5(NAMESPACE_URL, f"three-site-full-matrix-failover:{material}"))


def _operation_nonce(operation_id: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"three-site-full-matrix-failover-nonce:{operation_id}",
        )
    )


def _pair(action: str) -> tuple[str, str]:
    if action == "promote_ir":
        return SITE_WEBAPP_FI, SITE_WEBAPP_IR
    if action == "failback_fi":
        return SITE_WEBAPP_IR, SITE_WEBAPP_FI
    raise FullMatrixFailoverScheduleError("failover schedule action is invalid")


def build_schedule(
    *,
    campaign_id: str,
    gate_group_id: str,
    execution_class: str,
    release_sha: str,
    repetitions: int = 2,
    transition_scenarios: Iterable[str] | None = None,
) -> dict[str, Any]:
    try:
        campaign = str(UUID(campaign_id))
        group = str(UUID(gate_group_id))
    except ValueError as exc:
        raise FullMatrixFailoverScheduleError(
            "failover schedule campaign/group identity is invalid"
        ) from exc
    if (
        execution_class not in EXECUTION_CLASSES
        or SHA40.fullmatch(release_sha) is None
        or type(repetitions) is not int
        or repetitions != 2
    ):
        raise FullMatrixFailoverScheduleError(
            "failover schedule release/class/repetitions are invalid"
        )
    catalog = scenarios_for_execution_class(execution_class)
    ordered_catalog = [
        scenario
        for scenarios in catalog.values()
        for scenario in scenarios
    ]
    requested = (
        FAILOVER_TRANSITION_SCENARIOS
        if transition_scenarios is None
        else frozenset(str(value) for value in transition_scenarios)
    )
    if not requested.issubset(FAILOVER_TRANSITION_SCENARIOS):
        raise FullMatrixFailoverScheduleError(
            "failover schedule contains a non-transition scenario"
        )
    selected = [
        scenario
        for scenario in ordered_catalog
        if scenario in requested
    ]
    if requested.intersection(ordered_catalog) != set(selected):
        raise FullMatrixFailoverScheduleError(
            "failover schedule scenario selection is inconsistent"
        )

    entries: list[dict[str, Any]] = []
    sequence = 0
    for iteration in range(1, repetitions + 1):
        for scenario_id in selected:
            for transition_index, action in enumerate(TRANSITION_ACTIONS, start=1):
                sequence += 1
                source, target = _pair(action)
                entries.append(
                    {
                        "sequence": sequence,
                        "iteration": iteration,
                        "scenario_id": scenario_id,
                        "transition_index": transition_index,
                        "action": action,
                        "source_site": source,
                        "target_site": target,
                        "operation_id": _operation_id(
                            campaign_id=campaign,
                            execution_class=execution_class,
                            release_sha=release_sha,
                            iteration=iteration,
                            scenario_id=scenario_id,
                            transition_index=transition_index,
                            action=action,
                        ),
                    }
                )
                entries[-1]["operation_nonce"] = _operation_nonce(
                    entries[-1]["operation_id"]
                )
    payload = {
        "schema": SCHEDULE_SCHEMA,
        "campaign_id": campaign,
        "gate_group_id": group,
        "execution_class": execution_class,
        "release_sha": release_sha,
        "repetitions": repetitions,
        "catalog_sha256": scenario_catalog_sha256(execution_class),
        "entries": entries,
    }
    validate_schedule(
        payload,
        campaign_id=campaign,
        gate_group_id=group,
        execution_class=execution_class,
        release_sha=release_sha,
        repetitions=repetitions,
    )
    return payload


def validate_schedule(
    value: Any,
    *,
    campaign_id: str,
    gate_group_id: str,
    execution_class: str,
    release_sha: str,
    repetitions: int,
) -> dict[str, Any]:
    fields = {
        "schema",
        "campaign_id",
        "gate_group_id",
        "execution_class",
        "release_sha",
        "repetitions",
        "catalog_sha256",
        "entries",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != SCHEDULE_SCHEMA
        or value.get("campaign_id") != campaign_id
        or value.get("gate_group_id") != gate_group_id
        or value.get("execution_class") != execution_class
        or value.get("release_sha") != release_sha
        or value.get("repetitions") != repetitions
        or value.get("catalog_sha256")
        != scenario_catalog_sha256(execution_class)
    ):
        raise FullMatrixFailoverScheduleError(
            "failover schedule identity/schema is invalid"
        )
    catalog = {
        scenario
        for scenarios in scenarios_for_execution_class(execution_class).values()
        for scenario in scenarios
    }
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise FullMatrixFailoverScheduleError("failover schedule entries are invalid")
    seen_operations: set[str] = set()
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    entry_fields = {
        "sequence",
        "iteration",
        "scenario_id",
        "transition_index",
        "action",
        "source_site",
        "target_site",
        "operation_id",
        "operation_nonce",
    }
    for expected_sequence, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != entry_fields:
            raise FullMatrixFailoverScheduleError(
                "failover schedule entry fields are invalid"
            )
        iteration = entry.get("iteration")
        scenario_id = str(entry.get("scenario_id") or "")
        action = str(entry.get("action") or "")
        transition_index = entry.get("transition_index")
        source, target = _pair(action)
        expected_operation = _operation_id(
            campaign_id=campaign_id,
            execution_class=execution_class,
            release_sha=release_sha,
            iteration=iteration,
            scenario_id=scenario_id,
            transition_index=transition_index,
            action=action,
        ) if type(iteration) is int and type(transition_index) is int else ""
        if (
            entry.get("sequence") != expected_sequence
            or type(iteration) is not int
            or not 1 <= iteration <= repetitions
            or scenario_id not in catalog
            or scenario_id not in FAILOVER_TRANSITION_SCENARIOS
            or type(transition_index) is not int
            or transition_index not in {1, 2}
            or entry.get("source_site") != source
            or entry.get("target_site") != target
            or entry.get("operation_id") != expected_operation
            or entry.get("operation_nonce") != _operation_nonce(expected_operation)
            or entry.get("operation_nonce") == expected_operation
            or expected_operation in seen_operations
        ):
            raise FullMatrixFailoverScheduleError(
                "failover schedule entry identity/order is invalid"
            )
        seen_operations.add(expected_operation)
        grouped.setdefault((iteration, scenario_id), []).append(entry)
    for group in grouped.values():
        if [
            (entry["transition_index"], entry["action"])
            for entry in group
        ] != [(1, "promote_ir"), (2, "failback_fi")]:
            raise FullMatrixFailoverScheduleError(
                "failover schedule does not return a scenario to FI-active state"
            )
    return {
        "schema": SCHEDULE_SCHEMA,
        "entry_count": len(entries),
        "transition_scenario_count": len(grouped),
        "sha256": hashlib.sha256(canonical_json_bytes(value)).hexdigest(),
    }


def scheduled_entry(
    schedule: dict[str, Any],
    *,
    operation_id: str,
    scenario_id: str,
    iteration: int,
    action: str,
) -> dict[str, Any]:
    matches = [
        entry
        for entry in schedule.get("entries", [])
        if entry.get("operation_id") == operation_id
        and entry.get("scenario_id") == scenario_id
        and entry.get("iteration") == iteration
        and entry.get("action") == action
    ]
    if len(matches) != 1:
        raise FullMatrixFailoverScheduleError(
            "failover operation is absent or ambiguous in the campaign schedule"
        )
    return dict(matches[0])


def verify_scheduled_plan(
    schedule: dict[str, Any],
    *,
    plan: Any,
    scenario_id: str,
    iteration: int,
) -> dict[str, Any]:
    """Prove a parsed 15-minute failover plan is one pre-authorized entry."""

    entry = scheduled_entry(
        schedule,
        operation_id=str(plan.operation_id),
        scenario_id=scenario_id,
        iteration=iteration,
        action=str(plan.action),
    )
    if (
        str(plan.operation_nonce) != entry["operation_nonce"]
        or str(plan.release_sha) != schedule["release_sha"]
        or str(plan.source_site) != entry["source_site"]
        or str(plan.target_site) != entry["target_site"]
    ):
        raise FullMatrixFailoverScheduleError(
            "failover plan differs from its campaign schedule entry"
        )
    return {
        "sequence": entry["sequence"],
        "operation_id": entry["operation_id"],
        "operation_nonce": entry["operation_nonce"],
        "scenario_id": entry["scenario_id"],
        "iteration": entry["iteration"],
        "transition_index": entry["transition_index"],
        "action": entry["action"],
        "source_site": entry["source_site"],
        "target_site": entry["target_site"],
    }
