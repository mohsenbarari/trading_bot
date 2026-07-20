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
from typing import Any, Protocol

from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import (
    append_hash_chained_jsonl,
    sha256_secure_file,
    verify_hash_chained_jsonl,
    write_secure_atomic_bytes,
)
from core.three_site_full_matrix_campaign import (
    PHASES,
    PHASE_EVIDENCE_SCHEMA,
    PHASE_SCENARIOS,
    FullMatrixCampaignError,
    _relative_artifact,
    _utc,
    _validate_artifact_root,
    secure_json,
    verify_bound_artifacts,
    verify_campaign,
    verify_complete_matrix,
)


SHA256 = re.compile(r"^[0-9a-f]{64}$")
JOURNAL_SCHEMA = "three-site-staging-full-matrix-journal-v1"


class FullMatrixRunnerError(FullMatrixCampaignError):
    pass


@dataclass(frozen=True)
class CampaignIdentity:
    campaign_id: str
    campaign_hash: str
    release_sha: str
    activation_sha: str
    repetitions: int


class FullMatrixExecutionBackend(Protocol):
    async def preflight(self, identity: CampaignIdentity) -> dict[str, Any]: ...

    async def recover_interrupted(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
    ) -> dict[str, Any]: ...

    async def execute_scenario(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
    ) -> dict[str, Any]: ...

    async def cleanup_phase(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        iteration: int,
        failed: bool,
    ) -> dict[str, Any]: ...

    async def finalize(self, identity: CampaignIdentity) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identity_fields(identity: CampaignIdentity) -> dict[str, Any]:
    return {
        "campaign_id": identity.campaign_id,
        "campaign_hash": identity.campaign_hash,
        "release_sha": identity.release_sha,
        "activation_sha": identity.activation_sha,
    }


def _validate_identity_result(
    result: Any,
    *,
    identity: CampaignIdentity,
    fields: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result) != fields:
        raise FullMatrixRunnerError(f"Full Matrix {label} fields are invalid")
    if any(result.get(key) != value for key, value in _identity_fields(identity).items()):
        raise FullMatrixRunnerError(f"Full Matrix {label} identity differs")
    if result.get("status") != "passed" or result.get("production_touched") is not False:
        raise FullMatrixRunnerError(f"Full Matrix {label} did not pass safely")
    if SHA256.fullmatch(str(result.get("evidence_hash") or "")) is None:
        raise FullMatrixRunnerError(f"Full Matrix {label} evidence hash is invalid")
    return result


def _validate_preflight(result: Any, identity: CampaignIdentity) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "production_touched", "evidence_hash",
    }
    return _validate_identity_result(
        result, identity=identity, fields=fields, label="preflight"
    )


def _validate_recovery(
    result: Any,
    identity: CampaignIdentity,
    *,
    phase: str,
    scenario_id: str,
    iteration: int,
) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "phase", "scenario_id", "iteration",
        "residue_count", "production_touched", "evidence_hash",
    }
    value = _validate_identity_result(
        result, identity=identity, fields=fields, label="interrupted recovery"
    )
    if (
        value["phase"] != phase
        or value["scenario_id"] != scenario_id
        or value["iteration"] != iteration
        or type(value["residue_count"]) is not int
        or value["residue_count"] != 0
    ):
        raise FullMatrixRunnerError(
            "Full Matrix interrupted scenario was not returned to a clean state"
        )
    return value


def _validate_scenario(
    result: Any,
    identity: CampaignIdentity,
    *,
    phase: str,
    scenario_id: str,
    iteration: int,
    artifact_root: Path,
) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "phase", "scenario_id", "iteration",
        "assertion_count", "artifact_path", "artifact_sha256", "artifact_size",
        "production_touched", "evidence_hash",
    }
    value = _validate_identity_result(
        result, identity=identity, fields=fields, label="scenario"
    )
    relative = _relative_artifact(value["artifact_path"])
    if (
        value["phase"] != phase
        or value["scenario_id"] != scenario_id
        or value["iteration"] != iteration
        or type(value["assertion_count"]) is not int
        or value["assertion_count"] < 1
        or SHA256.fullmatch(str(value["artifact_sha256"])) is None
        or type(value["artifact_size"]) is not int
        or value["artifact_size"] <= 0
    ):
        raise FullMatrixRunnerError("Full Matrix scenario result is inconsistent")
    digest, size = sha256_secure_file(
        artifact_root / relative,
        label=f"Full Matrix {phase}/{scenario_id} artifact",
    )
    if digest != value["artifact_sha256"] or size != value["artifact_size"]:
        raise FullMatrixRunnerError("Full Matrix scenario artifact differs from result")
    if value["evidence_hash"] != digest:
        raise FullMatrixRunnerError(
            "Full Matrix scenario evidence hash is not its retained artifact hash"
        )
    return {**value, "artifact_path": relative}


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
        "activation_sha", "phase", "scenario_id", "iteration",
        "assertion_count", "artifact_path", "artifact_sha256", "artifact_size",
        "production_touched", "evidence_hash", "started_at", "finished_at",
    }:
        raise FullMatrixRunnerError("Full Matrix recorded scenario fields are invalid")
    base = {
        key: value
        for key, value in result.items()
        if key not in {"started_at", "finished_at"}
    }
    value = _validate_scenario(
        base,
        identity,
        phase=phase,
        scenario_id=scenario_id,
        iteration=iteration,
        artifact_root=artifact_root,
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
) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "phase", "iteration", "residue_count",
        "production_touched", "evidence_hash",
    }
    value = _validate_identity_result(
        result, identity=identity, fields=fields, label="phase cleanup"
    )
    if (
        value["phase"] != phase
        or value["iteration"] != iteration
        or type(value["residue_count"]) is not int
        or value["residue_count"] != 0
    ):
        raise FullMatrixRunnerError("Full Matrix phase cleanup left residue")
    return value


def _validate_finalize(result: Any, identity: CampaignIdentity) -> dict[str, Any]:
    fields = {
        "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "residue_count", "production_touched", "evidence_hash",
    }
    value = _validate_identity_result(
        result, identity=identity, fields=fields, label="finalization"
    )
    if type(value["residue_count"]) is not int or value["residue_count"] != 0:
        raise FullMatrixRunnerError("Full Matrix finalization left residue")
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


def _journal_state(
    journal: Path,
    identity: CampaignIdentity,
) -> tuple[list[dict[str, Any]], dict[tuple[int, str, str], dict[str, Any]]]:
    if not journal.exists():
        return [], {}
    records = verify_hash_chained_jsonl(journal, label="Full Matrix execution journal")
    allowed_events = {
        "campaign_started", "scenario_started", "scenario_recovered",
        "scenario_passed", "phase_passed", "campaign_finalized",
        "campaign_completed", "campaign_blocked",
    }
    if (
        not records
        or records[0].get("event") != "campaign_started"
        or sum(record.get("event") == "campaign_started" for record in records) != 1
        or any(record.get("event") not in allowed_events for record in records)
    ):
        raise FullMatrixRunnerError("Full Matrix journal event sequence is invalid")
    completed: dict[tuple[int, str, str], dict[str, Any]] = {}
    started: set[tuple[int, str, str]] = set()
    recovered: set[tuple[int, str, str]] = set()
    for record in records:
        if (
            record.get("schema") != JOURNAL_SCHEMA
            or any(record.get(key) != value for key, value in _identity_fields(identity).items())
        ):
            raise FullMatrixRunnerError("Full Matrix journal identity/schema differs")
        event = record.get("event")
        if event in {"scenario_started", "scenario_recovered", "scenario_passed"}:
            key = (record.get("iteration"), record.get("phase"), record.get("scenario_id"))
            if (
                type(key[0]) is not int
                or not 1 <= key[0] <= identity.repetitions
                or key[1] not in PHASE_SCENARIOS
                or key[2] not in PHASE_SCENARIOS[key[1]]
            ):
                raise FullMatrixRunnerError("Full Matrix journal scenario identity is invalid")
            if event == "scenario_started":
                if key in started and key not in recovered:
                    raise FullMatrixRunnerError("Full Matrix journal repeats an unclosed scenario")
                started.add(key)
                recovered.discard(key)
            elif event == "scenario_recovered":
                if key not in started or key in completed:
                    raise FullMatrixRunnerError("Full Matrix journal recovery order is invalid")
                recovered.add(key)
            else:
                result = record.get("result")
                if key not in started or key in completed or not isinstance(result, dict):
                    raise FullMatrixRunnerError("Full Matrix journal completion order is invalid")
                completed[key] = result
    completed_events = sum(record.get("event") == "campaign_completed" for record in records)
    finalized_events = [record for record in records if record.get("event") == "campaign_finalized"]
    if len(finalized_events) > 1:
        raise FullMatrixRunnerError("Full Matrix journal repeats finalization")
    if finalized_events:
        expected = sum(len(PHASE_SCENARIOS[phase]) for phase in PHASES) * identity.repetitions
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
        expected = sum(len(PHASE_SCENARIOS[phase]) for phase in PHASES) * identity.repetitions
        if completed_events != 1 or records[-1].get("event") != "campaign_completed" or len(completed) != expected:
            raise FullMatrixRunnerError("completed Full Matrix journal is incomplete")
    return records, completed


def _interrupted_key(
    records: list[dict[str, Any]],
    completed: dict[tuple[int, str, str], dict[str, Any]],
) -> tuple[int, str, str] | None:
    pending: set[tuple[int, str, str]] = set()
    for record in records:
        if record.get("event") not in {"scenario_started", "scenario_recovered"}:
            continue
        key = (record["iteration"], record["phase"], record["scenario_id"])
        if record["event"] == "scenario_started":
            pending.add(key)
        else:
            pending.discard(key)
    pending.difference_update(completed)
    if len(pending) > 1:
        raise FullMatrixRunnerError("Full Matrix journal has multiple interrupted scenarios")
    return next(iter(pending)) if pending else None


def _completed_phase_hashes(
    records: list[dict[str, Any]],
) -> dict[tuple[int, str], str]:
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
            for scenario_id in PHASE_SCENARIOS.get(str(key[1]), ())
        }
        if (
            type(key[0]) is not int
            or key[1] not in PHASE_SCENARIOS
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
        for phase in PHASES
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
                "status": "passed",
                "assertion_count": result["assertion_count"],
                "evidence_hash": result["evidence_hash"],
            }
            for result in results
        ],
        "skip_count": 0,
        "production_touched": False,
        "artifacts": [
            {"path": bundle_name, "sha256": bundle_hash, "size": len(bundle_bytes)}
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
        for phase in PHASES:
            name = f"{identity.campaign_id}-i{iteration:02d}-{phase}-evidence.json"
            result.append(
                secure_json(
                    artifact_root / name,
                    label="Full Matrix retained phase evidence",
                )
            )
    return result


async def run_full_matrix_campaign(
    *,
    campaign: dict[str, Any],
    approver_policy: dict[str, Any],
    bound_artifacts: dict[str, Path],
    artifact_root: Path,
    journal: Path,
    backend: FullMatrixExecutionBackend,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute or resume the one fixed campaign and return its final report."""

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
        campaign_hash=approved["campaign_hash"],
        release_sha=approved["release_sha"],
        activation_sha=approved["activation_sha"],
        repetitions=approved["repetitions"],
    )
    records, completed = _journal_state(journal, identity)
    completed_phases = _completed_phase_hashes(records)
    if any(record.get("event") == "campaign_blocked" for record in records):
        raise FullMatrixRunnerError("blocked Full Matrix campaign requires a new campaign")
    if any(record.get("event") == "campaign_completed" for record in records):
        raise FullMatrixRunnerError(
            "completed Full Matrix campaign is immutable; use the evidence verifier"
        )
    if not records:
        preflight = _validate_preflight(await backend.preflight(identity), identity)
        _journal_event(journal, identity, event="campaign_started", result=preflight)
        records, completed = _journal_state(journal, identity)
        completed_phases = _completed_phase_hashes(records)

    interrupted = _interrupted_key(records, completed)
    if interrupted is not None:
        iteration, phase, scenario_id = interrupted
        recovery = _validate_recovery(
            await backend.recover_interrupted(
                identity,
                phase=phase,
                scenario_id=scenario_id,
                iteration=iteration,
            ),
            identity,
            phase=phase,
            scenario_id=scenario_id,
            iteration=iteration,
        )
        _journal_event(
            journal,
            identity,
            event="scenario_recovered",
            phase=phase,
            scenario_id=scenario_id,
            iteration=iteration,
            result=recovery,
        )

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if _utc(campaign["expires_at"], label="campaign expires_at") <= current:
        active = interrupted
        if active is None:
            for record in reversed(records):
                if (
                    record.get("phase") in PHASE_SCENARIOS
                    and type(record.get("iteration")) is int
                ):
                    active = (
                        record["iteration"],
                        record["phase"],
                        str(record.get("scenario_id") or ""),
                    )
                    break
        cleanup_status = "not_required"
        cleanup_error: Exception | None = None
        if active is not None:
            iteration, phase, _scenario_id = active
            try:
                _validate_cleanup(
                    await backend.cleanup_phase(
                        identity, phase=phase, iteration=iteration, failed=True
                    ),
                    identity,
                    phase=phase,
                    iteration=iteration,
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
        for iteration in range(1, identity.repetitions + 1):
            for phase in PHASES:
                results: list[dict[str, Any]] = []
                for scenario_id in PHASE_SCENARIOS[phase]:
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
                    _journal_event(
                        journal,
                        identity,
                        event="scenario_started",
                        phase=phase,
                        scenario_id=scenario_id,
                        iteration=iteration,
                    )
                    result = _validate_scenario(
                        await backend.execute_scenario(
                            identity,
                            phase=phase,
                            scenario_id=scenario_id,
                            iteration=iteration,
                        ),
                        identity,
                        phase=phase,
                        scenario_id=scenario_id,
                        iteration=iteration,
                        artifact_root=artifact_root,
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
                cleanup = _validate_cleanup(
                    await backend.cleanup_phase(
                        identity, phase=phase, iteration=iteration, failed=False
                    ),
                    identity,
                    phase=phase,
                    iteration=iteration,
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
                )
        fresh = verify_campaign(campaign, approver_policy=approver_policy, now=now)
        if fresh["campaign_hash"] != identity.campaign_hash:
            raise FullMatrixRunnerError(
                "Full Matrix campaign identity changed before finalization"
            )
        finalization = _validate_finalize(await backend.finalize(identity), identity)
        _journal_event(
            journal,
            identity,
            event="campaign_finalized",
            finalization_evidence_hash=finalization["evidence_hash"],
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
            if active_iteration > 0 and active_phase in PHASE_SCENARIOS:
                _validate_cleanup(
                    await backend.cleanup_phase(
                        identity,
                        phase=active_phase,
                        iteration=active_iteration,
                        failed=True,
                    ),
                    identity,
                    phase=active_phase,
                    iteration=active_iteration,
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
