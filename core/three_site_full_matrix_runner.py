"""Crash-safe controller for one immutable three-site staging Full Matrix.

The controller deliberately owns ordering, resume rules, artifact verification,
cleanup, and final evidence generation.  A deployment backend may implement
the closed live operations, but it cannot add, remove, reorder, or mark a
campaign scenario as skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import time
from typing import Any, Callable, Protocol
from uuid import NAMESPACE_URL, uuid5

from core.canonical_json import canonical_json_bytes
from core.secure_file_io import (
    append_hash_chained_jsonl,
    sha256_secure_file,
    verify_hash_chained_jsonl,
    write_secure_atomic_bytes,
)
from core.three_site_full_matrix_campaign import (
    PHASE_EVIDENCE_SCHEMA,
    FullMatrixCampaignError,
    _relative_artifact,
    _utc,
    _validate_artifact_root,
    secure_json,
    verify_bound_artifacts,
    verify_campaign,
    verify_complete_matrix,
    verify_operation_evidence,
    verify_scenario_evidence,
    scenarios_for_execution_class,
)


SHA256 = re.compile(r"^[0-9a-f]{64}$")
JOURNAL_SCHEMA = "three-site-staging-full-matrix-journal-v1"


class FullMatrixRunnerError(FullMatrixCampaignError):
    pass


@dataclass(frozen=True)
class CampaignIdentity:
    campaign_id: str
    gate_group_id: str
    execution_class: str
    campaign_hash: str
    release_sha: str
    activation_sha: str
    repetitions: int


def _identity_catalog(identity: CampaignIdentity) -> dict[str, tuple[str, ...]]:
    return scenarios_for_execution_class(identity.execution_class)


class FullMatrixExecutionBackend(Protocol):
    async def preflight(
        self, identity: CampaignIdentity, *, operation_id: str
    ) -> dict[str, Any]: ...

    async def recover_interrupted(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
        attempt: int,
        operation_id: str,
    ) -> dict[str, Any]: ...

    async def execute_scenario(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
        attempt: int,
        operation_id: str,
    ) -> dict[str, Any]: ...

    async def cleanup_phase(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        iteration: int,
        failed: bool,
        operation_id: str,
    ) -> dict[str, Any]: ...

    async def finalize(
        self, identity: CampaignIdentity, *, operation_id: str
    ) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identity_fields(identity: CampaignIdentity) -> dict[str, Any]:
    return {
        "campaign_id": identity.campaign_id,
        "campaign_hash": identity.campaign_hash,
        "release_sha": identity.release_sha,
        "activation_sha": identity.activation_sha,
    }


def _operation_id(
    identity: CampaignIdentity,
    kind: str,
    *,
    phase: str = "",
    scenario_id: str = "",
    iteration: int = 0,
    failed: bool | None = None,
    attempt: int = 0,
) -> str:
    material = (
        f"{identity.campaign_hash}:{kind}:{iteration}:{phase}:{scenario_id}:"
        f"{'' if failed is None else str(failed).lower()}:{attempt}"
    )
    return str(uuid5(NAMESPACE_URL, material))


def _validate_identity_result(
    result: Any,
    *,
    identity: CampaignIdentity,
    fields: set[str],
    label: str,
    artifact_root: Path,
) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result) != fields:
        raise FullMatrixRunnerError(f"Full Matrix {label} fields are invalid")
    if any(result.get(key) != value for key, value in _identity_fields(identity).items()):
        raise FullMatrixRunnerError(f"Full Matrix {label} identity differs")
    if result.get("status") != "passed" or result.get("production_touched") is not False:
        raise FullMatrixRunnerError(f"Full Matrix {label} did not pass safely")
    if (
        SHA256.fullmatch(str(result.get("evidence_hash") or "")) is None
        or SHA256.fullmatch(str(result.get("artifact_sha256") or "")) is None
        or type(result.get("artifact_size")) is not int
        or result["artifact_size"] <= 0
    ):
        raise FullMatrixRunnerError(f"Full Matrix {label} evidence hash is invalid")
    relative = _relative_artifact(result.get("artifact_path"))
    digest, size = sha256_secure_file(
        artifact_root / relative,
        label=f"Full Matrix {label} retained artifact",
    )
    if (
        digest != result["artifact_sha256"]
        or size != result["artifact_size"]
        or result["evidence_hash"] != digest
    ):
        raise FullMatrixRunnerError(f"Full Matrix {label} retained artifact differs")
    return {**result, "artifact_path": relative}


def _verify_typed_operation_result(
    value: dict[str, Any],
    *,
    identity: CampaignIdentity,
    operation_kind: str,
    operation_id: str,
    operation_context: dict[str, Any],
    artifact_root: Path,
) -> None:
    evidence = secure_json(
        artifact_root / value["artifact_path"],
        label=f"Full Matrix {operation_kind} typed evidence",
    )
    verify_operation_evidence(
        evidence,
        campaign={
            "campaign_id": identity.campaign_id,
            "release_sha": identity.release_sha,
            "activation_sha": identity.activation_sha,
        },
        campaign_hash=identity.campaign_hash,
        operation_kind=operation_kind,
        operation_id=operation_id,
        operation_context=operation_context,
        artifact_root=artifact_root,
    )


def _validate_preflight(
    result: Any, identity: CampaignIdentity, *, operation_id: str, artifact_root: Path
) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "production_touched", "evidence_hash",
        "artifact_path", "artifact_sha256", "artifact_size", "operation_id",
    }
    value = _validate_identity_result(
        result, identity=identity, fields=fields, label="preflight",
        artifact_root=artifact_root,
    )
    if value["operation_id"] != operation_id:
        raise FullMatrixRunnerError("Full Matrix preflight operation ID differs")
    _verify_typed_operation_result(
        value, identity=identity, operation_kind="preflight",
        operation_id=operation_id, operation_context=_operation_context(),
        artifact_root=artifact_root,
    )
    return value


def _validate_recovery(
    result: Any,
    identity: CampaignIdentity,
    *,
    phase: str,
    scenario_id: str,
    iteration: int,
    attempt: int,
    operation_id: str,
    artifact_root: Path,
) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "phase", "scenario_id", "iteration", "attempt",
        "residue_count", "production_touched", "evidence_hash",
        "artifact_path", "artifact_sha256", "artifact_size", "operation_id",
    }
    value = _validate_identity_result(
        result, identity=identity, fields=fields, label="interrupted recovery",
        artifact_root=artifact_root,
    )
    if (
        value["phase"] != phase
        or value["scenario_id"] != scenario_id
        or value["iteration"] != iteration
        or value["attempt"] != attempt
        or value["operation_id"] != operation_id
        or type(value["residue_count"]) is not int
        or value["residue_count"] != 0
    ):
        raise FullMatrixRunnerError(
            "Full Matrix interrupted scenario was not returned to a clean state"
        )
    _verify_typed_operation_result(
        value, identity=identity, operation_kind="recovery",
        operation_id=operation_id,
        operation_context=_operation_context(
            phase=phase, scenario_id=scenario_id, iteration=iteration,
            attempt=attempt,
        ),
        artifact_root=artifact_root,
    )
    return value


def _validate_scenario(
    result: Any,
    identity: CampaignIdentity,
    *,
    phase: str,
    scenario_id: str,
    iteration: int,
    attempt: int,
    operation_id: str,
    artifact_root: Path,
    controller_duration_seconds: float,
) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "phase", "scenario_id", "iteration", "attempt",
        "assertion_count", "artifact_path", "artifact_sha256", "artifact_size",
        "production_touched", "evidence_hash", "operation_id",
    }
    value = _validate_identity_result(
        result, identity=identity, fields=fields, label="scenario",
        artifact_root=artifact_root,
    )
    relative = _relative_artifact(value["artifact_path"])
    if (
        value["phase"] != phase
        or value["scenario_id"] != scenario_id
        or value["iteration"] != iteration
        or value["attempt"] != attempt
        or value["operation_id"] != operation_id
        or type(value["assertion_count"]) is not int
        or value["assertion_count"] < 1
        or SHA256.fullmatch(str(value["artifact_sha256"])) is None
        or type(value["artifact_size"]) is not int
        or value["artifact_size"] <= 0
    ):
        raise FullMatrixRunnerError("Full Matrix scenario result is inconsistent")
    evidence = secure_json(
        artifact_root / relative,
        label=f"Full Matrix {phase}/{scenario_id} typed evidence",
    )
    verified = verify_scenario_evidence(
        evidence,
        campaign={
            "campaign_id": identity.campaign_id,
            "release_sha": identity.release_sha,
            "activation_sha": identity.activation_sha,
        },
        campaign_hash=identity.campaign_hash,
        phase=phase,
        scenario_id=scenario_id,
        iteration=iteration,
        attempt=attempt,
        operation_id=operation_id,
        artifact_root=artifact_root,
    )
    if verified["assertion_count"] != value["assertion_count"]:
        raise FullMatrixRunnerError(
            "Full Matrix scenario assertion summary differs from typed evidence"
        )
    if (
        scenario_id == "twenty_four_hour_endurance_no_growth"
        and controller_duration_seconds < 86400
    ):
        raise FullMatrixRunnerError(
            "Full Matrix endurance scenario completed before 24 monotonic hours"
        )
    if verified["duration_seconds"] > controller_duration_seconds + 2:
        raise FullMatrixRunnerError(
            "Full Matrix scenario duration exceeds controller monotonic observation"
        )
    return {
        **value,
        "artifact_path": relative,
        "duration_seconds": verified["duration_seconds"],
        "raw_artifacts": verified["raw_artifacts"],
    }


def _validate_recorded_scenario(
    result: Any,
    identity: CampaignIdentity,
    *,
    phase: str,
    scenario_id: str,
    iteration: int,
    artifact_root: Path,
) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result) != {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "phase", "scenario_id", "iteration", "attempt",
        "assertion_count", "artifact_path", "artifact_sha256", "artifact_size",
        "production_touched", "evidence_hash", "started_at", "finished_at",
        "duration_seconds", "raw_artifacts", "operation_id",
    }:
        raise FullMatrixRunnerError("Full Matrix recorded scenario fields are invalid")
    if type(result.get("attempt")) is not int or result["attempt"] < 1:
        raise FullMatrixRunnerError("Full Matrix recorded scenario attempt is invalid")
    base = {
        key: value
        for key, value in result.items()
        if key not in {"started_at", "finished_at", "duration_seconds", "raw_artifacts"}
    }
    value = _validate_scenario(
        base,
        identity,
        phase=phase,
        scenario_id=scenario_id,
        iteration=iteration,
        attempt=result["attempt"],
        operation_id=_operation_id(
            identity, "scenario", phase=phase,
            scenario_id=scenario_id, iteration=iteration,
            attempt=result["attempt"],
        ),
        artifact_root=artifact_root,
        controller_duration_seconds=float(result["duration_seconds"]),
    )
    try:
        started = datetime.fromisoformat(str(result["started_at"]).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(result["finished_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise FullMatrixRunnerError("Full Matrix recorded scenario time is invalid") from exc
    if started.tzinfo is None or finished.tzinfo is None or started > finished:
        raise FullMatrixRunnerError("Full Matrix recorded scenario time order is invalid")
    return {
        **value,
        "started_at": started.astimezone(timezone.utc).isoformat(),
        "finished_at": finished.astimezone(timezone.utc).isoformat(),
    }


def _validate_cleanup(
    result: Any,
    identity: CampaignIdentity,
    *,
    phase: str,
    iteration: int,
    failed: bool,
    operation_id: str,
    artifact_root: Path,
) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "phase", "iteration", "residue_count",
        "production_touched", "evidence_hash",
        "artifact_path", "artifact_sha256", "artifact_size", "operation_id",
    }
    value = _validate_identity_result(
        result, identity=identity, fields=fields, label="phase cleanup",
        artifact_root=artifact_root,
    )
    if (
        value["phase"] != phase
        or value["iteration"] != iteration
        or value["operation_id"] != operation_id
        or type(value["residue_count"]) is not int
        or value["residue_count"] != 0
    ):
        raise FullMatrixRunnerError("Full Matrix phase cleanup left residue")
    _verify_typed_operation_result(
        value, identity=identity, operation_kind="cleanup",
        operation_id=operation_id,
        operation_context=_operation_context(
            phase=phase, iteration=iteration, failed=failed
        ),
        artifact_root=artifact_root,
    )
    return value


def _validate_finalize(
    result: Any, identity: CampaignIdentity, *, operation_id: str, artifact_root: Path
) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "residue_count", "production_touched", "evidence_hash",
        "artifact_path", "artifact_sha256", "artifact_size", "operation_id",
    }
    value = _validate_identity_result(
        result, identity=identity, fields=fields, label="finalization",
        artifact_root=artifact_root,
    )
    if (
        value["operation_id"] != operation_id
        or type(value["residue_count"]) is not int
        or value["residue_count"] != 0
    ):
        raise FullMatrixRunnerError("Full Matrix finalization left residue")
    _verify_typed_operation_result(
        value, identity=identity, operation_kind="finalize",
        operation_id=operation_id, operation_context=_operation_context(),
        artifact_root=artifact_root,
    )
    return value


def _journal_event(
    journal: Path,
    identity: CampaignIdentity,
    *,
    event: str,
    **fields: Any,
) -> dict[str, Any]:
    return append_hash_chained_jsonl(
        journal,
        {
            "schema": JOURNAL_SCHEMA,
            "timestamp": _now(),
            "event": event,
            **_identity_fields(identity),
            **fields,
        },
    )


def _operation_context(
    *,
    phase: str = "",
    scenario_id: str = "",
    iteration: int = 0,
    failed: bool | None = None,
    attempt: int = 0,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "scenario_id": scenario_id,
        "iteration": iteration,
        "failed": failed,
        "attempt": attempt,
    }


def _recorded_operation(
    records: list[dict[str, Any]],
    *,
    operation_id: str,
    operation_kind: str,
    context: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None]:
    starts = [
        record for record in records
        if record.get("operation_id") == operation_id
        and record.get("event") in {"campaign_started", "operation_started"}
    ]
    passes = [
        record for record in records
        if record.get("operation_id") == operation_id
        and record.get("event") == "operation_passed"
    ]
    if len(starts) > 1 or len(passes) > 1:
        raise FullMatrixRunnerError("Full Matrix operation intent/completion is duplicated")
    for record in [*starts, *passes]:
        if (
            record.get("operation_kind") != operation_kind
            or record.get("operation_context") != context
        ):
            raise FullMatrixRunnerError("Full Matrix operation intent identity differs")
    if passes and not starts:
        raise FullMatrixRunnerError("Full Matrix operation completed without write-ahead intent")
    result = passes[0].get("result") if passes else None
    if passes and not isinstance(result, dict):
        raise FullMatrixRunnerError("Full Matrix operation completion lacks a typed result")
    return bool(starts), result


def _journal_state(
    journal: Path,
    identity: CampaignIdentity,
) -> tuple[list[dict[str, Any]], dict[tuple[int, str, str], dict[str, Any]]]:
    catalog = _identity_catalog(identity)
    phases = tuple(catalog)
    if not journal.exists():
        return [], {}
    records = verify_hash_chained_jsonl(journal, label="Full Matrix execution journal")
    allowed_events = {
        "campaign_started", "scenario_started", "scenario_recovered",
        "scenario_passed", "phase_passed", "campaign_finalized",
        "campaign_completed", "campaign_blocked", "operation_started",
        "operation_passed",
    }
    if (
        not records
        or records[0].get("event") != "campaign_started"
        or sum(record.get("event") == "campaign_started" for record in records) != 1
        or any(record.get("event") not in allowed_events for record in records)
    ):
        raise FullMatrixRunnerError("Full Matrix journal event sequence is invalid")
    completed: dict[tuple[int, str, str], dict[str, Any]] = {}
    attempts: dict[tuple[int, str, str], int] = {}
    open_attempts: dict[tuple[int, str, str], int] = {}
    operation_starts: dict[str, tuple[str, dict[str, Any]]] = {}
    operation_passes: set[str] = set()
    for record in records:
        if (
            record.get("schema") != JOURNAL_SCHEMA
            or any(record.get(key) != value for key, value in _identity_fields(identity).items())
        ):
            raise FullMatrixRunnerError("Full Matrix journal identity/schema differs")
        event = record.get("event")
        if event in {"campaign_started", "operation_started", "operation_passed"}:
            operation_id = str(record.get("operation_id") or "")
            operation_kind = str(record.get("operation_kind") or "")
            context = record.get("operation_context")
            if (
                not operation_id
                or not operation_kind
                or not isinstance(context, dict)
                or set(context) != {
                    "phase", "scenario_id", "iteration", "failed", "attempt"
                }
                or operation_kind not in {"preflight", "recovery", "cleanup", "finalize"}
                or not isinstance(context.get("phase"), str)
                or not isinstance(context.get("scenario_id"), str)
                or type(context.get("iteration")) is not int
                or type(context.get("attempt")) is not int
                or context.get("failed") not in {None, True, False}
                or (operation_kind in {"preflight", "finalize"} and context != {
                    "phase": "", "scenario_id": "", "iteration": 0,
                    "failed": None, "attempt": 0,
                })
                or (operation_kind == "cleanup" and (
                    context["phase"] not in catalog
                    or context["scenario_id"] != ""
                    or not 1 <= context["iteration"] <= identity.repetitions
                    or type(context["failed"]) is not bool
                    or context["attempt"] != 0
                ))
                or (operation_kind == "recovery" and (
                    context["phase"] not in catalog
                    or context["scenario_id"] not in catalog.get(
                        context["phase"], ()
                    )
                    or not 1 <= context["iteration"] <= identity.repetitions
                    or context["failed"] is not None
                    or context["attempt"] < 1
                ))
                or operation_id != _operation_id(
                    identity, operation_kind,
                    phase=context["phase"], scenario_id=context["scenario_id"],
                    iteration=context["iteration"], failed=context["failed"],
                    attempt=context["attempt"],
                )
            ):
                raise FullMatrixRunnerError("Full Matrix operation journal fields are invalid")
            if event in {"campaign_started", "operation_started"}:
                if operation_id in operation_starts:
                    raise FullMatrixRunnerError("Full Matrix operation intent is duplicated")
                if event == "campaign_started" and operation_kind != "preflight":
                    raise FullMatrixRunnerError("Full Matrix campaign start is not preflight intent")
                operation_starts[operation_id] = (operation_kind, context)
            else:
                if (
                    operation_id not in operation_starts
                    or operation_id in operation_passes
                    or operation_starts[operation_id] != (operation_kind, context)
                    or not isinstance(record.get("result"), dict)
                ):
                    raise FullMatrixRunnerError("Full Matrix operation completion order is invalid")
                operation_passes.add(operation_id)
        if event in {"scenario_started", "scenario_recovered", "scenario_passed"}:
            key = (record.get("iteration"), record.get("phase"), record.get("scenario_id"))
            if (
                type(key[0]) is not int
                or not 1 <= key[0] <= identity.repetitions
                or key[1] not in catalog
                or key[2] not in catalog[key[1]]
            ):
                raise FullMatrixRunnerError("Full Matrix journal scenario identity is invalid")
            attempt = record.get("attempt")
            if event == "scenario_started":
                expected_attempt = attempts.get(key, 0) + 1
                expected_operation_id = _operation_id(
                    identity, "scenario", phase=str(key[1]),
                    scenario_id=str(key[2]), iteration=int(key[0]),
                    attempt=expected_attempt,
                )
                if (
                    type(attempt) is not int
                    or attempt != expected_attempt
                    or key in open_attempts
                    or key in completed
                    or record.get("operation_id") != expected_operation_id
                ):
                    raise FullMatrixRunnerError("Full Matrix scenario intent ID is invalid")
                attempts[key] = attempt
                open_attempts[key] = attempt
            elif event == "scenario_recovered":
                if open_attempts.get(key) != attempt or key in completed:
                    raise FullMatrixRunnerError("Full Matrix journal recovery order is invalid")
                del open_attempts[key]
            else:
                result = record.get("result")
                expected_operation_id = _operation_id(
                    identity, "scenario", phase=str(key[1]),
                    scenario_id=str(key[2]), iteration=int(key[0]),
                    attempt=int(attempt),
                ) if type(attempt) is int else ""
                if (
                    open_attempts.get(key) != attempt
                    or key in completed or not isinstance(result, dict)
                    or record.get("operation_id") != expected_operation_id
                    or result.get("operation_id") != expected_operation_id
                    or result.get("attempt") != attempt
                ):
                    raise FullMatrixRunnerError("Full Matrix journal completion order is invalid")
                del open_attempts[key]
                completed[key] = result
    completed_events = sum(record.get("event") == "campaign_completed" for record in records)
    finalized_events = [record for record in records if record.get("event") == "campaign_finalized"]
    if len(finalized_events) > 1:
        raise FullMatrixRunnerError("Full Matrix journal repeats finalization")
    if finalized_events:
        expected = sum(len(catalog[phase]) for phase in phases) * identity.repetitions
        if len(completed) != expected:
            raise FullMatrixRunnerError("Full Matrix journal finalized before all scenarios")
        allowed_following = {"campaign_completed", "campaign_blocked"}
        finalized_index = records.index(finalized_events[0])
        if finalized_index != len(records) - 1 and (
            finalized_index != len(records) - 2
            or records[-1].get("event") not in allowed_following
        ):
            raise FullMatrixRunnerError("Full Matrix journal finalization order is invalid")
    if completed_events:
        expected = sum(len(catalog[phase]) for phase in phases) * identity.repetitions
        if completed_events != 1 or records[-1].get("event") != "campaign_completed" or len(completed) != expected:
            raise FullMatrixRunnerError("completed Full Matrix journal is incomplete")
    return records, completed


def _interrupted_key(
    records: list[dict[str, Any]],
    completed: dict[tuple[int, str, str], dict[str, Any]],
) -> tuple[int, str, str, int] | None:
    pending: dict[tuple[int, str, str], int] = {}
    for record in records:
        if record.get("event") not in {"scenario_started", "scenario_recovered"}:
            continue
        key = (record["iteration"], record["phase"], record["scenario_id"])
        if record["event"] == "scenario_started":
            pending[key] = record["attempt"]
        else:
            pending.pop(key, None)
    for key in completed:
        pending.pop(key, None)
    if len(pending) > 1:
        raise FullMatrixRunnerError("Full Matrix journal has multiple interrupted scenarios")
    if not pending:
        return None
    key, attempt = next(iter(pending.items()))
    return (*key, attempt)


def _completed_phase_hashes(
    records: list[dict[str, Any]],
    identity: CampaignIdentity,
) -> dict[tuple[int, str], str]:
    catalog = _identity_catalog(identity)
    phases = tuple(catalog)
    completed: dict[tuple[int, str], str] = {}
    passed_scenarios: set[tuple[int, str, str]] = set()
    observed_order: list[tuple[int, str]] = []
    for record in records:
        if record.get("event") == "scenario_passed":
            passed_scenarios.add(
                (record.get("iteration"), record.get("phase"), record.get("scenario_id"))
            )
            continue
        if record.get("event") != "phase_passed":
            continue
        key = (record.get("iteration"), record.get("phase"))
        evidence_hash = str(record.get("evidence_hash") or "")
        expected_scenarios = {
            (key[0], key[1], scenario_id)
            for scenario_id in catalog.get(str(key[1]), ())
        }
        if (
            type(key[0]) is not int
            or key[1] not in catalog
            or key in completed
            or SHA256.fullmatch(evidence_hash) is None
            or not expected_scenarios.issubset(passed_scenarios)
        ):
            raise FullMatrixRunnerError("Full Matrix journal phase completion is invalid")
        completed[key] = evidence_hash
        observed_order.append(key)
    expected_order = [
        (iteration, phase)
        for iteration in range(1, max((key[0] for key in completed), default=0) + 1)
        for phase in phases
    ]
    if observed_order != expected_order[: len(observed_order)]:
        raise FullMatrixRunnerError("Full Matrix journal phase order is invalid")
    return completed


def _write_exact(path: Path, payload: bytes, *, label: str) -> None:
    if path.exists():
        digest, size = sha256_secure_file(path, label=label)
        if digest != hashlib.sha256(payload).hexdigest() or size != len(payload):
            raise FullMatrixRunnerError(f"existing {label} differs on resume")
        return
    write_secure_atomic_bytes(path, payload, label=label, mode=0o600, max_size=8 * 1024 * 1024)


def _phase_documents(
    *,
    identity: CampaignIdentity,
    iteration: int,
    phase: str,
    results: list[dict[str, Any]],
    cleanup: dict[str, Any],
    artifact_root: Path,
) -> tuple[dict[str, Any], str]:
    bundle_name = f"{identity.campaign_id}-i{iteration:02d}-{phase}-artifacts.json"
    bundle = {
        "schema": "three-site-staging-full-matrix-phase-artifacts-v1",
        **_identity_fields(identity),
        "phase": phase,
        "iteration": iteration,
        "scenario_results": results,
        "cleanup_evidence_hash": cleanup["evidence_hash"],
        "production_touched": False,
    }
    bundle_bytes = canonical_json_bytes(bundle) + b"\n"
    _write_exact(
        artifact_root / bundle_name,
        bundle_bytes,
        label="Full Matrix phase artifact bundle",
    )
    bundle_hash = hashlib.sha256(bundle_bytes).hexdigest()
    started_at = min(str(result["started_at"]) for result in results)
    finished_at = max(str(result["finished_at"]) for result in results)
    evidence = {
        "schema": PHASE_EVIDENCE_SCHEMA,
        "status": "passed",
        **_identity_fields(identity),
        "phase": phase,
        "iteration": iteration,
        "started_at": started_at,
        "finished_at": finished_at,
        "scenario_results": [
            {
                "scenario_id": result["scenario_id"],
                "operation_id": result["operation_id"],
                "attempt": result["attempt"],
                "status": "passed",
                "assertion_count": result["assertion_count"],
                "evidence_hash": result["evidence_hash"],
                "duration_seconds": result["duration_seconds"],
                "artifact": {
                    "path": result["artifact_path"],
                    "sha256": result["artifact_sha256"],
                    "size": result["artifact_size"],
                },
            }
            for result in results
        ],
        "skip_count": 0,
        "production_touched": False,
        "artifacts": [
            {"path": bundle_name, "sha256": bundle_hash, "size": len(bundle_bytes)},
            *[
                {
                    "path": result["artifact_path"],
                    "sha256": result["artifact_sha256"],
                    "size": result["artifact_size"],
                }
                for result in results
            ],
            *[
                artifact
                for result in results
                for artifact in result["raw_artifacts"]
            ],
            {
                "path": cleanup["artifact_path"],
                "sha256": cleanup["artifact_sha256"],
                "size": cleanup["artifact_size"],
            },
        ],
        "cleanup_residue_count": 0 if phase == "cleanup_repeatability" else None,
    }
    evidence_name = f"{identity.campaign_id}-i{iteration:02d}-{phase}-evidence.json"
    evidence_bytes = canonical_json_bytes(evidence) + b"\n"
    _write_exact(
        artifact_root / evidence_name,
        evidence_bytes,
        label="Full Matrix phase evidence",
    )
    return evidence, hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()


def _load_all_phase_evidence(
    *,
    identity: CampaignIdentity,
    artifact_root: Path,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for iteration in range(1, identity.repetitions + 1):
        for phase in _identity_catalog(identity):
            name = f"{identity.campaign_id}-i{iteration:02d}-{phase}-evidence.json"
            result.append(
                secure_json(
                    artifact_root / name,
                    label="Full Matrix retained phase evidence",
                )
            )
    return result


async def _run_journaled_operation(
    *,
    journal: Path,
    identity: CampaignIdentity,
    records: list[dict[str, Any]],
    operation_kind: str,
    context: dict[str, Any],
    invoke,  # noqa: ANN001
    validate,  # noqa: ANN001
    campaign_start_intent: bool = False,
) -> dict[str, Any]:
    operation_id = _operation_id(
        identity,
        operation_kind,
        phase=str(context["phase"]),
        scenario_id=str(context["scenario_id"]),
        iteration=int(context["iteration"]),
        failed=context["failed"],
        attempt=int(context["attempt"]),
    )
    started, recorded_result = _recorded_operation(
        records,
        operation_id=operation_id,
        operation_kind=operation_kind,
        context=context,
    )
    if recorded_result is not None:
        return validate(recorded_result, operation_id)
    if not started:
        event = "campaign_started" if campaign_start_intent else "operation_started"
        appended = _journal_event(
            journal,
            identity,
            event=event,
            operation_id=operation_id,
            operation_kind=operation_kind,
            operation_context=context,
        )
        records.append(appended)
    value = validate(await invoke(operation_id), operation_id)
    appended = _journal_event(
        journal,
        identity,
        event="operation_passed",
        operation_id=operation_id,
        operation_kind=operation_kind,
        operation_context=context,
        result=value,
    )
    records.append(appended)
    return value


async def run_full_matrix_campaign(
    *,
    campaign: dict[str, Any],
    approver_policy: dict[str, Any],
    bound_artifacts: dict[str, Path],
    artifact_root: Path,
    journal: Path,
    backend: FullMatrixExecutionBackend,
    now: datetime | None = None,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Execute or resume the one fixed campaign and return its final report."""

    monotonic = monotonic or getattr(backend, "monotonic", time.monotonic)
    journal_exists = journal.exists() and journal.stat().st_size > 0
    approved = verify_campaign(
        campaign,
        approver_policy=approver_policy,
        now=now,
        allow_expired_for_safe_cleanup=journal_exists,
    )
    _validate_artifact_root(artifact_root)
    verify_bound_artifacts(campaign, bound_artifacts)
    identity = CampaignIdentity(
        campaign_id=approved["campaign_id"],
        gate_group_id=approved["gate_group_id"],
        execution_class=approved["execution_class"],
        campaign_hash=approved["campaign_hash"],
        release_sha=approved["release_sha"],
        activation_sha=approved["activation_sha"],
        repetitions=approved["repetitions"],
    )
    records, completed = _journal_state(journal, identity)
    completed_phases = _completed_phase_hashes(records, identity)
    if any(record.get("event") == "campaign_blocked" for record in records):
        raise FullMatrixRunnerError("blocked Full Matrix campaign requires a new campaign")
    if any(record.get("event") == "campaign_completed" for record in records):
        raise FullMatrixRunnerError(
            "completed Full Matrix campaign is immutable; use the evidence verifier"
        )
    preflight_context = _operation_context()
    await _run_journaled_operation(
        journal=journal,
        identity=identity,
        records=records,
        operation_kind="preflight",
        context=preflight_context,
        campaign_start_intent=not records,
        invoke=lambda operation_id: backend.preflight(
            identity, operation_id=operation_id
        ),
        validate=lambda result, operation_id: _validate_preflight(
            result, identity, operation_id=operation_id, artifact_root=artifact_root
        ),
    )
    records, completed = _journal_state(journal, identity)
    completed_phases = _completed_phase_hashes(records, identity)

    interrupted = _interrupted_key(records, completed)
    if interrupted is not None:
        iteration, phase, scenario_id, attempt = interrupted
        recovery_context = _operation_context(
            phase=phase, scenario_id=scenario_id, iteration=iteration,
            attempt=attempt,
        )
        recovery = await _run_journaled_operation(
            journal=journal,
            identity=identity,
            records=records,
            operation_kind="recovery",
            context=recovery_context,
            invoke=lambda operation_id: backend.recover_interrupted(
                identity, phase=phase, scenario_id=scenario_id,
                iteration=iteration, attempt=attempt, operation_id=operation_id,
            ),
            validate=lambda result, operation_id: _validate_recovery(
                result, identity, phase=phase, scenario_id=scenario_id,
                iteration=iteration, attempt=attempt, operation_id=operation_id,
                artifact_root=artifact_root,
            ),
        )
        _journal_event(
            journal,
            identity,
            event="scenario_recovered",
            phase=phase,
            scenario_id=scenario_id,
            iteration=iteration,
            attempt=attempt,
            operation_id=recovery["operation_id"],
            result=recovery,
        )

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if _utc(campaign["expires_at"], label="campaign expires_at") <= current:
        active = interrupted
        if active is None:
            for record in reversed(records):
                if (
                    record.get("phase") in _identity_catalog(identity)
                    and type(record.get("iteration")) is int
                ):
                    active = (
                        record["iteration"],
                        record["phase"],
                        str(record.get("scenario_id") or ""),
                        int(record.get("attempt") or 0),
                    )
                    break
        cleanup_status = "not_required"
        cleanup_error: Exception | None = None
        if active is not None:
            iteration, phase, _scenario_id, _attempt = active
            try:
                cleanup_context = _operation_context(
                    phase=phase, iteration=iteration, failed=True
                )
                await _run_journaled_operation(
                    journal=journal,
                    identity=identity,
                    records=records,
                    operation_kind="cleanup",
                    context=cleanup_context,
                    invoke=lambda operation_id: backend.cleanup_phase(
                        identity, phase=phase, iteration=iteration,
                        failed=True, operation_id=operation_id,
                    ),
                    validate=lambda result, operation_id: _validate_cleanup(
                        result, identity, phase=phase, iteration=iteration,
                        failed=True,
                        operation_id=operation_id, artifact_root=artifact_root,
                    ),
                )
                cleanup_status = "passed"
            except Exception as exc:
                cleanup_status = "failed"
                cleanup_error = exc
        _journal_event(
            journal,
            identity,
            event="campaign_blocked",
            phase=active[1] if active else "preflight",
            iteration=active[0] if active else 0,
            error_class="FullMatrixCampaignExpired",
            cleanup_status=cleanup_status,
            cleanup_error_class=(
                type(cleanup_error).__name__ if cleanup_error is not None else None
            ),
        )
        if cleanup_error is not None:
            raise FullMatrixRunnerError(
                "expired Full Matrix campaign could not prove safe cleanup"
            ) from cleanup_error
        raise FullMatrixRunnerError(
            "expired Full Matrix campaign performed cleanup only and is now blocked"
        )

    finalized = [record for record in records if record.get("event") == "campaign_finalized"]
    if finalized:
        phase_evidence = _load_all_phase_evidence(
            identity=identity,
            artifact_root=artifact_root,
        )
        report = verify_complete_matrix(
            campaign=campaign,
            approver_policy=approver_policy,
            bound_artifacts=bound_artifacts,
            phase_evidence=phase_evidence,
            artifact_root=artifact_root,
            execution_journal=journal,
            now=now,
        )
        _journal_event(
            journal,
            identity,
            event="campaign_completed",
            report_hash=report["report_hash"],
        )
        return report

    phase_evidence: list[dict[str, Any]] = []
    used_scenario_artifacts: set[str] = set()
    try:
        catalog = _identity_catalog(identity)
        for iteration in range(1, identity.repetitions + 1):
            for phase in catalog:
                results: list[dict[str, Any]] = []
                for scenario_id in catalog[phase]:
                    fresh = verify_campaign(
                        campaign,
                        approver_policy=approver_policy,
                        now=now,
                    )
                    if fresh["campaign_hash"] != identity.campaign_hash:
                        raise FullMatrixRunnerError(
                            "Full Matrix campaign identity changed during execution"
                        )
                    key = (iteration, phase, scenario_id)
                    if key in completed:
                        result = _validate_recorded_scenario(
                            completed[key],
                            identity,
                            phase=phase,
                            scenario_id=scenario_id,
                            iteration=iteration,
                            artifact_root=artifact_root,
                        )
                        if result["artifact_path"] in used_scenario_artifacts:
                            raise FullMatrixRunnerError(
                                "Full Matrix scenario artifact was reused"
                            )
                        used_scenario_artifacts.add(result["artifact_path"])
                        results.append(result)
                        continue
                    started_at = _now()
                    attempt = 1 + sum(
                        record.get("event") == "scenario_started"
                        and record.get("iteration") == iteration
                        and record.get("phase") == phase
                        and record.get("scenario_id") == scenario_id
                        for record in records
                    )
                    scenario_operation_id = _operation_id(
                        identity, "scenario", phase=phase,
                        scenario_id=scenario_id, iteration=iteration,
                        attempt=attempt,
                    )
                    _journal_event(
                        journal,
                        identity,
                        event="scenario_started",
                        phase=phase,
                        scenario_id=scenario_id,
                        iteration=iteration,
                        attempt=attempt,
                        operation_id=scenario_operation_id,
                    )
                    monotonic_started = monotonic()
                    result = _validate_scenario(
                        await backend.execute_scenario(
                            identity,
                            phase=phase,
                            scenario_id=scenario_id,
                            iteration=iteration,
                            attempt=attempt,
                            operation_id=scenario_operation_id,
                        ),
                        identity,
                        phase=phase,
                        scenario_id=scenario_id,
                        iteration=iteration,
                        attempt=attempt,
                        operation_id=scenario_operation_id,
                        artifact_root=artifact_root,
                        controller_duration_seconds=max(
                            0.0, monotonic() - monotonic_started
                        ),
                    )
                    result = {**result, "started_at": started_at, "finished_at": _now()}
                    if result["artifact_path"] in used_scenario_artifacts:
                        raise FullMatrixRunnerError(
                            "Full Matrix scenario artifact was reused"
                        )
                    used_scenario_artifacts.add(result["artifact_path"])
                    _journal_event(
                        journal,
                        identity,
                        event="scenario_passed",
                        phase=phase,
                        scenario_id=scenario_id,
                        iteration=iteration,
                        attempt=attempt,
                        operation_id=scenario_operation_id,
                        result=result,
                    )
                    completed[key] = result
                    results.append(result)
                phase_key = (iteration, phase)
                if phase_key in completed_phases:
                    name = f"{identity.campaign_id}-i{iteration:02d}-{phase}-evidence.json"
                    evidence = secure_json(
                        artifact_root / name,
                        label="Full Matrix retained phase evidence",
                    )
                    if (
                        hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
                        != completed_phases[phase_key]
                    ):
                        raise FullMatrixRunnerError(
                            "Full Matrix retained phase evidence differs on resume"
                        )
                    phase_evidence.append(evidence)
                    continue
                cleanup_context = _operation_context(
                    phase=phase, iteration=iteration, failed=False
                )
                cleanup = await _run_journaled_operation(
                    journal=journal,
                    identity=identity,
                    records=records,
                    operation_kind="cleanup",
                    context=cleanup_context,
                    invoke=lambda operation_id: backend.cleanup_phase(
                        identity, phase=phase, iteration=iteration,
                        failed=False, operation_id=operation_id,
                    ),
                    validate=lambda result, operation_id: _validate_cleanup(
                        result, identity, phase=phase, iteration=iteration,
                        failed=False,
                        operation_id=operation_id, artifact_root=artifact_root,
                    ),
                )
                evidence, evidence_hash = _phase_documents(
                    identity=identity,
                    iteration=iteration,
                    phase=phase,
                    results=results,
                    cleanup=cleanup,
                    artifact_root=artifact_root,
                )
                phase_evidence.append(evidence)
                _journal_event(
                    journal,
                    identity,
                    event="phase_passed",
                    phase=phase,
                    iteration=iteration,
                    evidence_hash=evidence_hash,
                    cleanup_evidence_hash=cleanup["evidence_hash"],
                    cleanup_result=cleanup,
                )
        fresh = verify_campaign(campaign, approver_policy=approver_policy, now=now)
        if fresh["campaign_hash"] != identity.campaign_hash:
            raise FullMatrixRunnerError(
                "Full Matrix campaign identity changed before finalization"
            )
        finalization_context = _operation_context()
        finalization = await _run_journaled_operation(
            journal=journal,
            identity=identity,
            records=records,
            operation_kind="finalize",
            context=finalization_context,
            invoke=lambda operation_id: backend.finalize(
                identity, operation_id=operation_id
            ),
            validate=lambda result, operation_id: _validate_finalize(
                result, identity, operation_id=operation_id,
                artifact_root=artifact_root,
            ),
        )
        _journal_event(
            journal,
            identity,
            event="campaign_finalized",
            finalization_evidence_hash=finalization["evidence_hash"],
            result=finalization,
        )
        report = verify_complete_matrix(
            campaign=campaign,
            approver_policy=approver_policy,
            bound_artifacts=bound_artifacts,
            phase_evidence=phase_evidence,
            artifact_root=artifact_root,
            execution_journal=journal,
            now=now,
        )
        _journal_event(
            journal,
            identity,
            event="campaign_completed",
            report_hash=report["report_hash"],
            finalization_evidence_hash=finalization["evidence_hash"],
        )
        return report
    except Exception as exc:
        active_iteration = iteration if "iteration" in locals() else 0
        active_phase = phase if "phase" in locals() else "preflight"
        cleanup_error: Exception | None = None
        try:
            if active_iteration > 0 and active_phase in _identity_catalog(identity):
                cleanup_context = _operation_context(
                    phase=active_phase, iteration=active_iteration, failed=True
                )
                await _run_journaled_operation(
                    journal=journal,
                    identity=identity,
                    records=records,
                    operation_kind="cleanup",
                    context=cleanup_context,
                    invoke=lambda operation_id: backend.cleanup_phase(
                        identity, phase=active_phase, iteration=active_iteration,
                        failed=True, operation_id=operation_id,
                    ),
                    validate=lambda result, operation_id: _validate_cleanup(
                        result, identity, phase=active_phase,
                        iteration=active_iteration, failed=True,
                        operation_id=operation_id,
                        artifact_root=artifact_root,
                    ),
                )
        except Exception as caught:  # cleanup failure must replace safe status
            cleanup_error = caught
        _journal_event(
            journal,
            identity,
            event="campaign_blocked",
            phase=active_phase,
            iteration=active_iteration,
            error_class=type(exc).__name__,
            cleanup_status="failed" if cleanup_error else "passed",
            cleanup_error_class=type(cleanup_error).__name__ if cleanup_error else None,
        )
        if cleanup_error is not None:
            raise FullMatrixRunnerError(
                "Full Matrix failed and safe cleanup could not be proven"
            ) from cleanup_error
        raise
