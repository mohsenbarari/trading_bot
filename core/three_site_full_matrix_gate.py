"""Fail-closed aggregation contract for the two-part three-site Gate D."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.canonical_json import canonical_json_bytes
from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    EXECUTION_CLASSES,
    SHARED_HOST_SAFE,
)
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    SHA40,
    SHA256,
    _policy,
    scenarios_for_execution_class,
    scenario_catalog_sha256,
)


COMPONENT_REPORT_SCHEMA = "three-site-staging-full-matrix-report-v1"
AGGREGATE_SCHEMA = "three-site-staging-gate-d-aggregate-v1"
AGGREGATE_APPROVAL_SCHEMA = "three-site-staging-gate-d-aggregate-approval-v1"


class GateDAggregateError(RuntimeError):
    pass


def _utc(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateDAggregateError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise GateDAggregateError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def verify_component_report(report: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "status", "campaign_id", "gate_group_id", "execution_class",
        "campaign_hash", "release_sha", "activation_sha", "repetitions",
        "expires_at", "authoritative_controller_journal", "execution_journal",
        "bound_artifacts", "phase_results", "phase_evidence_count",
        "scenario_ids", "scenario_catalog_sha256", "scenario_execution_count",
        "skip_count", "cleanup_residue_count", "production_touched", "report_hash",
    }
    if (
        not isinstance(report, dict)
        or set(report) != fields
        or report.get("schema") != COMPONENT_REPORT_SCHEMA
        or report.get("status") != "passed"
        or report.get("authoritative_controller_journal") is not True
        or report.get("repetitions") != 2
        or report.get("skip_count") != 0
        or report.get("cleanup_residue_count") != 0
        or report.get("production_touched") is not False
    ):
        raise GateDAggregateError("Gate D component report is not authoritative and clean")
    try:
        campaign_id = str(UUID(str(report["campaign_id"])))
        gate_group_id = str(UUID(str(report["gate_group_id"])))
    except ValueError as exc:
        raise GateDAggregateError("Gate D component identity is invalid") from exc
    execution_class = str(report["execution_class"])
    if execution_class not in EXECUTION_CLASSES:
        raise GateDAggregateError("Gate D component execution class is invalid")
    release_sha = str(report["release_sha"])
    if (
        SHA40.fullmatch(release_sha) is None
        or report.get("activation_sha") != release_sha
        or SHA256.fullmatch(str(report.get("campaign_hash") or "")) is None
        or SHA256.fullmatch(str(report.get("report_hash") or "")) is None
    ):
        raise GateDAggregateError("Gate D component lineage/hash is invalid")
    _utc(report["expires_at"], label="Gate D component expires_at")
    body = {key: value for key, value in report.items() if key != "report_hash"}
    if hashlib.sha256(canonical_json_bytes(body)).hexdigest() != report["report_hash"]:
        raise GateDAggregateError("Gate D component report hash differs")

    catalog = scenarios_for_execution_class(execution_class)
    expected_ids = [scenario for phase in catalog.values() for scenario in phase]
    if (
        report.get("scenario_ids") != expected_ids
        or report.get("scenario_catalog_sha256")
        != scenario_catalog_sha256(execution_class)
        or report.get("scenario_execution_count") != 2 * len(expected_ids)
    ):
        raise GateDAggregateError("Gate D component scenario coverage is invalid")
    phases = report.get("phase_results")
    expected_phase_keys = [
        (phase, iteration)
        for iteration in (1, 2)
        for phase in catalog
    ]
    if not isinstance(phases, list) or len(phases) != len(expected_phase_keys):
        raise GateDAggregateError("Gate D component phase coverage is invalid")
    actual_phase_keys: list[tuple[str, int]] = []
    for phase in phases:
        if (
            not isinstance(phase, dict)
            or set(phase) != {
                "phase", "iteration", "evidence_hash", "artifact_count",
                "assertion_count",
            }
            or SHA256.fullmatch(str(phase.get("evidence_hash") or "")) is None
            or type(phase.get("artifact_count")) is not int
            or phase["artifact_count"] < 1
            or type(phase.get("assertion_count")) is not int
            or phase["assertion_count"] < 1
        ):
            raise GateDAggregateError("Gate D component phase result is invalid")
        actual_phase_keys.append((str(phase["phase"]), phase["iteration"]))
    if actual_phase_keys != expected_phase_keys or report.get("phase_evidence_count") != len(phases):
        raise GateDAggregateError("Gate D component phase order/count is invalid")

    artifacts = report.get("bound_artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != BOUND_ARTIFACTS:
        raise GateDAggregateError("Gate D component artifact bindings are incomplete")
    for binding in artifacts.values():
        if (
            not isinstance(binding, dict)
            or set(binding) != {"sha256", "size"}
            or SHA256.fullmatch(str(binding.get("sha256") or "")) is None
            or type(binding.get("size")) is not int
            or binding["size"] < 1
        ):
            raise GateDAggregateError("Gate D component artifact binding is invalid")
    journal = report.get("execution_journal")
    if (
        not isinstance(journal, dict)
        or set(journal) != {
            "schema", "head_before_completion", "finalization_evidence_hash",
            "scenario_completion_count", "phase_completion_count",
            "operation_artifacts", "operation_artifact_count",
        }
        or journal.get("schema")
        != "three-site-staging-full-matrix-journal-binding-v1"
        or journal.get("scenario_completion_count") != report["scenario_execution_count"]
        or journal.get("phase_completion_count") != report["phase_evidence_count"]
        or SHA256.fullmatch(str(journal.get("head_before_completion") or "")) is None
        or SHA256.fullmatch(str(journal.get("finalization_evidence_hash") or "")) is None
    ):
        raise GateDAggregateError("Gate D component journal binding is invalid")
    operation_artifacts = journal.get("operation_artifacts")
    if (
        not isinstance(operation_artifacts, list)
        or journal.get("operation_artifact_count") != len(operation_artifacts)
    ):
        raise GateDAggregateError("Gate D operation artifact summary is invalid")
    seen_paths: set[str] = set()
    for artifact in operation_artifacts:
        operation_label = str(artifact.get("operation") or "")
        operation_parts = operation_label.split(":")
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"path", "sha256", "size", "operation"}
            or not isinstance(artifact.get("path"), str)
            or not artifact["path"]
            or artifact["path"] in seen_paths
            or SHA256.fullmatch(str(artifact.get("sha256") or "")) is None
            or type(artifact.get("size")) is not int
            or artifact["size"] < 1
            or len(operation_parts) != 6
            or operation_parts[0]
            not in {"preflight", "recovery", "cleanup", "finalize"}
        ):
            raise GateDAggregateError("Gate D operation artifact record is invalid")
        seen_paths.add(artifact["path"])
    return {
        "campaign_id": campaign_id,
        "gate_group_id": gate_group_id,
        "execution_class": execution_class,
        "campaign_hash": report["campaign_hash"],
        "report_hash": report["report_hash"],
        "release_sha": release_sha,
        "scenario_catalog_sha256": report["scenario_catalog_sha256"],
        "scenario_ids": list(expected_ids),
        "scenario_execution_count": report["scenario_execution_count"],
    }


def verify_gate_d_aggregate(
    aggregate: dict[str, Any], *, approver_policy: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    fields = {
        "schema", "gate_group_id", "release_sha", "generated_at", "expires_at",
        "repetitions", "component_reports", "combined_scenario_count",
        "combined_scenario_execution_count", "skip_count", "cleanup_residue_count",
        "production_touched", "approver_policy_hash", "approvals",
    }
    if (
        not isinstance(aggregate, dict)
        or set(aggregate) != fields
        or aggregate.get("schema") != AGGREGATE_SCHEMA
        or aggregate.get("repetitions") != 2
        or aggregate.get("skip_count") != 0
        or aggregate.get("cleanup_residue_count") != 0
        or aggregate.get("production_touched") is not False
    ):
        raise GateDAggregateError("Gate D aggregate fields/status are invalid")
    try:
        gate_group_id = str(UUID(str(aggregate["gate_group_id"])))
    except ValueError as exc:
        raise GateDAggregateError("Gate D aggregate group is invalid") from exc
    release_sha = str(aggregate["release_sha"])
    if SHA40.fullmatch(release_sha) is None:
        raise GateDAggregateError("Gate D aggregate release SHA is invalid")
    components = aggregate.get("component_reports")
    if not isinstance(components, dict) or set(components) != EXECUTION_CLASSES:
        raise GateDAggregateError("Gate D requires both execution classes exactly once")
    seen_campaigns: set[str] = set()
    all_scenarios: list[str] = []
    execution_count = 0
    for execution_class, component in components.items():
        verified = verify_component_report(component)
        campaign_id = verified["campaign_id"]
        expected_ids = verified["scenario_ids"]
        if (
            campaign_id in seen_campaigns
            or verified["gate_group_id"] != gate_group_id
            or verified["execution_class"] != execution_class
            or verified["release_sha"] != release_sha
        ):
            raise GateDAggregateError("Gate D component report group/lineage is invalid")
        seen_campaigns.add(campaign_id)
        all_scenarios.extend(expected_ids)
        execution_count += verified["scenario_execution_count"]
    complete_catalog = [
        scenario
        for phase in scenarios_for_execution_class(SHARED_HOST_SAFE).values()
        for scenario in phase
    ]
    complete_catalog += [
        scenario
        for phase in scenarios_for_execution_class(DEDICATED_HOST_DESTRUCTIVE).values()
        for scenario in phase
    ]
    if (
        len(all_scenarios) != len(set(all_scenarios))
        or set(all_scenarios) != set(complete_catalog)
        or aggregate.get("combined_scenario_count") != len(complete_catalog)
        or aggregate.get("combined_scenario_execution_count") != execution_count
    ):
        raise GateDAggregateError("Gate D combined scenario coverage is incomplete/overlapping")
    generated = _utc(aggregate["generated_at"], label="Gate D generated_at")
    expires = _utc(aggregate["expires_at"], label="Gate D expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (
        generated > current + timedelta(minutes=2)
        or expires <= current
        or expires <= generated
        or expires - generated > timedelta(hours=24)
    ):
        raise GateDAggregateError("Gate D aggregate is expired or future-dated")
    try:
        signers, policy_hash = _policy(approver_policy, release_sha=release_sha)
    except Exception as exc:
        raise GateDAggregateError("Gate D approver policy is invalid") from exc
    if aggregate.get("approver_policy_hash") != policy_hash:
        raise GateDAggregateError("Gate D aggregate is not bound to approver policy")
    approvals = aggregate.get("approvals")
    if not isinstance(approvals, list) or len(approvals) != 2:
        raise GateDAggregateError("Gate D aggregate needs exactly two approvals")
    unsigned = {key: value for key, value in aggregate.items() if key != "approvals"}
    aggregate_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    used_operators: set[str] = set()
    used_domains: set[str] = set()
    for approval in approvals:
        if not isinstance(approval, dict) or set(approval) != {"operator", "key_id", "signature"}:
            raise GateDAggregateError("Gate D approval fields are invalid")
        signer = signers.get(str(approval["key_id"]))
        operator = str(approval["operator"])
        if (
            signer is None or operator != signer[0] or operator in used_operators
            or signer[1] in used_domains
        ):
            raise GateDAggregateError("Gate D approvals are not independent")
        try:
            signature = base64.b64decode(str(approval["signature"]), validate=True)
            Ed25519PublicKey.from_public_bytes(signer[2]).verify(
                signature, aggregate_hash.encode("ascii")
            )
        except (ValueError, binascii.Error, InvalidSignature) as exc:
            raise GateDAggregateError("Gate D approval signature is invalid") from exc
        used_operators.add(operator)
        used_domains.add(signer[1])
    return {
        "schema": "three-site-staging-gate-d-result-v1",
        "status": "passed",
        "gate_group_id": gate_group_id,
        "release_sha": release_sha,
        "aggregate_hash": aggregate_hash,
        "component_report_hashes": {
            name: components[name]["report_hash"] for name in sorted(components)
        },
        "combined_scenario_count": len(complete_catalog),
        "combined_scenario_execution_count": execution_count,
        "approved_by": sorted(used_operators),
    }
