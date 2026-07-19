"""Crash-resumable saga binding Writer, readiness, routing, and audit steps."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any, Protocol
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.dr_event_protocol import canonical_json_bytes
from core.runtime_sites import SITE_WEBAPP_FI, SITE_WEBAPP_IR
from core.secure_file_io import append_hash_chained_jsonl, verify_hash_chained_jsonl


ORCHESTRATION_SCHEMA = "three-site-failover-operation-v1"
STEPS = (
    "classification_verified",
    "source_fenced",
    "target_ready",
    "target_term_acquired",
    "source_connections_drained",
    "route_switched",
    "public_route_verified",
    "completed",
)
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class DrOrchestrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class FailoverPlan:
    operation_id: str
    action: str
    source_site: str
    target_site: str
    expected_epoch: int
    target_epoch: int
    release_sha: str
    domain: str
    record: str
    expected_current_ip: str
    target_ip: str
    classification: dict[str, Any]
    readiness_hash: str
    command_manifest_hash: str
    approvals: tuple[dict[str, str], ...]
    plan_hash: str


class OrchestrationAdapter(Protocol):
    async def classification_verified(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def source_fenced(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def target_ready(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def target_term_acquired(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def source_connections_drained(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def route_switched(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def public_route_verified(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def rollback(
        self,
        plan: FailoverPlan,
        *,
        failed_step: str,
        completed_steps: tuple[str, ...],
    ) -> dict[str, Any]: ...


def parse_plan(payload: Any) -> FailoverPlan:
    expected = {
        "schema", "operation_id", "action", "source_site", "target_site",
        "expected_epoch", "target_epoch", "release_sha", "domain", "record",
        "expected_current_ip", "target_ip", "classification", "readiness_hash",
        "command_manifest_hash", "approvals",
    }
    if not isinstance(payload, dict) or set(payload) != expected or payload["schema"] != ORCHESTRATION_SCHEMA:
        raise DrOrchestrationError("orchestration plan fields/schema are invalid")
    try:
        operation_id = str(UUID(str(payload["operation_id"])))
    except ValueError as exc:
        raise DrOrchestrationError("operation_id must be a UUID") from exc
    action = str(payload["action"])
    source = str(payload["source_site"])
    target = str(payload["target_site"])
    if action not in {"promote_ir", "failback_fi"}:
        raise DrOrchestrationError("orchestration action is invalid")
    expected_pair = (
        (SITE_WEBAPP_FI, SITE_WEBAPP_IR)
        if action == "promote_ir"
        else (SITE_WEBAPP_IR, SITE_WEBAPP_FI)
    )
    if (source, target) != expected_pair:
        raise DrOrchestrationError("source/target sites do not match the action")
    expected_epoch = int(payload["expected_epoch"])
    target_epoch = int(payload["target_epoch"])
    if expected_epoch < 1 or target_epoch != expected_epoch + 1:
        raise DrOrchestrationError("target Writer epoch must advance exactly once")
    release_sha = str(payload["release_sha"]).lower()
    if not SHA_RE.fullmatch(release_sha):
        raise DrOrchestrationError("release SHA is malformed")
    if payload["domain"] != "gold-trading.ir":
        raise DrOrchestrationError("source orchestrator remains locked to the failover-test domain")
    classification = payload["classification"]
    if not isinstance(classification, dict) or set(classification) != {
        "mode", "confidence", "consecutive_rounds", "evidence_hash"
    }:
        raise DrOrchestrationError("classification evidence is invalid")
    required_mode = "isolated" if action == "promote_ir" else "online"
    if (
        classification["mode"] != required_mode
        or classification["confidence"] != "high"
        or int(classification["consecutive_rounds"]) < 3
        or not re.fullmatch(r"[0-9a-f]{64}", str(classification["evidence_hash"]))
    ):
        raise DrOrchestrationError("classification lacks stable multi-vantage evidence")
    readiness_hash = str(payload["readiness_hash"])
    if not re.fullmatch(r"[0-9a-f]{64}", readiness_hash):
        raise DrOrchestrationError("readiness evidence hash is malformed")
    command_manifest_hash = str(payload["command_manifest_hash"])
    if not re.fullmatch(r"[0-9a-f]{64}", command_manifest_hash):
        raise DrOrchestrationError("command manifest hash is malformed")
    approvals = payload["approvals"]
    if not isinstance(approvals, list) or len(approvals) != 2:
        raise DrOrchestrationError("exactly two approvals are required")
    unsigned = {key: value for key, value in payload.items() if key != "approvals"}
    plan_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    return FailoverPlan(
        operation_id=operation_id,
        action=action,
        source_site=source,
        target_site=target,
        expected_epoch=expected_epoch,
        target_epoch=target_epoch,
        release_sha=release_sha,
        domain=str(payload["domain"]),
        record=str(payload["record"]),
        expected_current_ip=str(payload["expected_current_ip"]),
        target_ip=str(payload["target_ip"]),
        classification=dict(classification),
        readiness_hash=readiness_hash,
        command_manifest_hash=command_manifest_hash,
        approvals=tuple(dict(item) for item in approvals),
        plan_hash=plan_hash,
    )


def verify_two_person_approvals(plan: FailoverPlan, public_keys: dict[str, str]) -> None:
    operators: set[str] = set()
    key_ids: set[str] = set()
    message = plan.plan_hash.encode("ascii")
    for approval in plan.approvals:
        if set(approval) != {"operator", "key_id", "signature"}:
            raise DrOrchestrationError("approval fields are invalid")
        operator = str(approval["operator"]).strip()
        key_id = str(approval["key_id"]).strip()
        if not operator or operator in operators or key_id in key_ids or key_id not in public_keys:
            raise DrOrchestrationError("approvals are not from two distinct authorized operators")
        try:
            public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_keys[key_id], validate=True))
            public_key.verify(base64.b64decode(approval["signature"], validate=True), message)
        except (ValueError, InvalidSignature) as exc:
            raise DrOrchestrationError("approval signature is invalid") from exc
        operators.add(operator)
        key_ids.add(key_id)


def _validate_step_result(step: str, result: Any, plan: FailoverPlan) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise DrOrchestrationError(f"orchestration step {step} did not return status=ok")
    if result.get("operation_id") != plan.operation_id:
        raise DrOrchestrationError(f"orchestration step {step} is not bound to the operation")
    evidence_hash = str(result.get("evidence_hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_hash):
        raise DrOrchestrationError(f"orchestration step {step} lacks an evidence hash")
    if step == "source_fenced" and (
        result.get("source_site") != plan.source_site or result.get("fenced") is not True
    ):
        raise DrOrchestrationError("source fencing evidence does not match the approved source")
    if step == "target_ready" and result.get("target_site") != plan.target_site:
        raise DrOrchestrationError("target readiness evidence does not match the approved target")
    if step == "target_term_acquired":
        if result.get("holder_site") != plan.target_site or int(result.get("writer_epoch") or 0) != plan.target_epoch:
            raise DrOrchestrationError("Witness term result does not match target site/epoch")
    if step == "source_connections_drained" and (
        result.get("source_site") != plan.source_site
        or type(result.get("active_connections")) is not int
        or result["active_connections"] != 0
    ):
        raise DrOrchestrationError("source connection-drain evidence is incomplete")
    if step in {"route_switched", "public_route_verified"}:
        if (
            result.get("origin_ip") != plan.target_ip
            or result.get("domain") != plan.domain
            or result.get("record") != plan.record
        ):
            raise DrOrchestrationError("route result does not match target origin")
    return result


def _validate_rollback_result(result: Any, plan: FailoverPlan) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise DrOrchestrationError("orchestration rollback did not return status=ok")
    if result.get("operation_id") != plan.operation_id:
        raise DrOrchestrationError("orchestration rollback is not bound to the operation")
    evidence_hash = str(result.get("evidence_hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_hash):
        raise DrOrchestrationError("orchestration rollback lacks an evidence hash")
    if (
        result.get("source_site") != plan.source_site
        or result.get("target_site") != plan.target_site
        or result.get("target_fenced") is not True
        or type(result.get("target_active_connections")) is not int
        or result["target_active_connections"] != 0
    ):
        raise DrOrchestrationError("orchestration rollback lacks target fence/drain evidence")
    rollback_state = result.get("rollback_state")
    if rollback_state == "source_restored":
        if (
            result.get("holder_site") != plan.source_site
            or type(result.get("writer_epoch")) is not int
            or result["writer_epoch"] not in {plan.expected_epoch, plan.target_epoch + 1}
            or result.get("origin_ip") != plan.expected_current_ip
            or result.get("domain") != plan.domain
            or result.get("record") != plan.record
        ):
            raise DrOrchestrationError("source-restored rollback evidence is inconsistent")
    elif rollback_state == "safe_fenced":
        if (
            result.get("holder_site") is not None
            or result.get("source_fenced") is not True
            or result.get("origin_ip") not in {plan.expected_current_ip, plan.target_ip}
            or result.get("domain") != plan.domain
            or result.get("record") != plan.record
        ):
            raise DrOrchestrationError("safe-fenced rollback evidence is inconsistent")
    else:
        raise DrOrchestrationError("orchestration rollback state is invalid")
    return result


def _append_operation_record(
    journal_path: Path,
    plan: FailoverPlan,
    *,
    event: str,
    **fields: Any,
) -> None:
    append_hash_chained_jsonl(
        journal_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "operation_id": plan.operation_id,
            "plan_hash": plan.plan_hash,
            **fields,
        },
    )


async def _rollback_after_failure(
    plan: FailoverPlan,
    *,
    adapter: OrchestrationAdapter,
    journal_path: Path,
    failed_step: str,
    completed_steps: tuple[str, ...],
) -> dict[str, Any]:
    _append_operation_record(
        journal_path,
        plan,
        event="dr.orchestration.rollback_started",
        failed_step=failed_step,
        completed_steps=list(completed_steps),
    )
    try:
        result = _validate_rollback_result(
            await adapter.rollback(
                plan,
                failed_step=failed_step,
                completed_steps=completed_steps,
            ),
            plan,
        )
    except Exception as exc:
        _append_operation_record(
            journal_path,
            plan,
            event="dr.orchestration.rollback_failed",
            failed_step=failed_step,
            error_class=type(exc).__name__,
        )
        raise
    _append_operation_record(
        journal_path,
        plan,
        event="dr.orchestration.rollback_completed",
        failed_step=failed_step,
        rollback_state=result["rollback_state"],
        evidence_hash=result["evidence_hash"],
    )
    return result


async def run_orchestration(
    plan: FailoverPlan,
    *,
    adapter: OrchestrationAdapter,
    journal_path: Path,
) -> dict[str, Any]:
    records = verify_hash_chained_jsonl(journal_path, label="failover journal") if journal_path.exists() else []
    operation_records = [record for record in records if record.get("operation_id") == plan.operation_id]
    if any(record.get("plan_hash") != plan.plan_hash for record in operation_records):
        raise DrOrchestrationError("journal operation_id was reused with a different plan")
    completed = {
        str(record.get("step"))
        for record in operation_records
        if record.get("event") == "dr.orchestration.step_completed"
    }
    if any(record.get("event") == "dr.orchestration.rollback_completed" for record in operation_records):
        return {"status": "rolled_back", "operation_id": plan.operation_id, "plan_hash": plan.plan_hash}
    failed_records = [
        record
        for record in operation_records
        if record.get("event") == "dr.orchestration.step_failed"
    ]
    rollback_incomplete = any(
        record.get("event") in {
            "dr.orchestration.rollback_started",
            "dr.orchestration.rollback_failed",
        }
        for record in operation_records
    )
    if failed_records or rollback_incomplete:
        failed_step = str(failed_records[-1].get("step") if failed_records else "unknown")
        completed_steps = tuple(step for step in STEPS[:-1] if step in completed)
        if failed_step != "classification_verified" or completed_steps:
            result = await _rollback_after_failure(
                plan,
                adapter=adapter,
                journal_path=journal_path,
                failed_step=failed_step,
                completed_steps=completed_steps,
            )
            return {
                "status": "rolled_back",
                "operation_id": plan.operation_id,
                "plan_hash": plan.plan_hash,
                "rollback_state": result["rollback_state"],
            }
    for step in STEPS[:-1]:
        if step in completed:
            continue
        method = getattr(adapter, step)
        try:
            result = _validate_step_result(step, await method(plan), plan)
        except Exception as exc:
            _append_operation_record(
                journal_path,
                plan,
                event="dr.orchestration.step_failed",
                step=step,
                error_class=type(exc).__name__,
            )
            if step != "classification_verified" or completed:
                completed_steps = tuple(item for item in STEPS[:-1] if item in completed)
                await _rollback_after_failure(
                    plan,
                    adapter=adapter,
                    journal_path=journal_path,
                    failed_step=step,
                    completed_steps=completed_steps,
                )
            raise
        _append_operation_record(
            journal_path,
            plan,
            event="dr.orchestration.step_completed",
            step=step,
            evidence_hash=result["evidence_hash"],
        )
        completed.add(step)
    if "completed" not in completed:
        _append_operation_record(
            journal_path,
            plan,
            event="dr.orchestration.step_completed",
            step="completed",
            evidence_hash=hashlib.sha256(
                canonical_json_bytes({"operation_id": plan.operation_id, "plan_hash": plan.plan_hash})
            ).hexdigest(),
        )
    return {"status": "completed", "operation_id": plan.operation_id, "plan_hash": plan.plan_hash}
