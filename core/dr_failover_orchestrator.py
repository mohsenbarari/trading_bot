"""Crash-resumable saga binding Writer, readiness, routing, and audit steps."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
from pathlib import Path
import re
import secrets
from typing import Any, Protocol
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.dr_event_protocol import canonical_json_bytes
from core.runtime_sites import SITE_WEBAPP_FI, SITE_WEBAPP_IR
from core.secure_file_io import append_hash_chained_jsonl, read_secure_text, verify_hash_chained_jsonl


ORCHESTRATION_SCHEMA = "three-site-failover-operation-v1"
STEPS = (
    "classification_verified",
    "source_fenced",
    "source_connections_drained",
    "target_ready",
    "target_term_acquired",
    "route_switched",
    "public_route_verified",
    "completed",
)
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
MAX_PLAN_LIFETIME = timedelta(minutes=15)
MAX_PLAN_FUTURE_SKEW = timedelta(seconds=30)
PINNED_APPROVER_POLICY_PATH = Path("/etc/trading-bot/security/dr-failover-approvers.json")
APPROVER_POLICY_SCHEMA = "three-site-failover-approver-policy-v1"


class DrOrchestrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class FailoverPlan:
    operation_id: str
    operation_nonce: str
    generated_at: datetime
    expires_at: datetime
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
    rpo_policy: dict[str, Any]
    readiness_hash: str
    command_manifest_hash: str
    approver_policy_hash: str
    approvals: tuple[dict[str, str], ...]
    plan_hash: str


class OrchestrationAdapter(Protocol):
    async def classification_verified(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def source_fenced(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def target_ready(
        self,
        plan: FailoverPlan,
        *,
        source_tail_boundary: dict[str, Any],
    ) -> dict[str, Any]: ...
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


@dataclass(frozen=True)
class ApprovedSigner:
    operator: str
    key_id: str
    custody_domain: str
    public_key: bytes


@dataclass(frozen=True)
class ApproverPolicy:
    policy_id: str
    release_sha: str
    signers: dict[str, ApprovedSigner]
    policy_hash: str


class OperationLedger(Protocol):
    async def reserve(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def finalize(self, plan: FailoverPlan, *, outcome: str, evidence_hash: str) -> dict[str, Any]: ...


def parse_plan(payload: Any) -> FailoverPlan:
    expected = {
        "schema", "operation_id", "operation_nonce", "generated_at", "expires_at",
        "action", "source_site", "target_site",
        "expected_epoch", "target_epoch", "release_sha", "domain", "record",
        "expected_current_ip", "target_ip", "classification", "rpo_policy", "readiness_hash",
        "command_manifest_hash", "approver_policy_hash", "approvals",
    }
    if not isinstance(payload, dict) or set(payload) != expected or payload["schema"] != ORCHESTRATION_SCHEMA:
        raise DrOrchestrationError("orchestration plan fields/schema are invalid")
    try:
        operation_id = str(UUID(str(payload["operation_id"])))
        operation_nonce = str(UUID(str(payload["operation_nonce"])))
    except ValueError as exc:
        raise DrOrchestrationError("operation_id and operation_nonce must be UUIDs") from exc
    if operation_nonce == operation_id:
        raise DrOrchestrationError("operation_nonce must be independent from operation_id")
    generated_at = _parse_plan_time(payload["generated_at"], "generated_at")
    expires_at = _parse_plan_time(payload["expires_at"], "expires_at")
    if expires_at <= generated_at or expires_at - generated_at > MAX_PLAN_LIFETIME:
        raise DrOrchestrationError("orchestration plan lifetime is invalid")
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
    if type(payload["expected_epoch"]) is not int or type(payload["target_epoch"]) is not int:
        raise DrOrchestrationError("Writer epochs must be exact integers")
    expected_epoch = payload["expected_epoch"]
    target_epoch = payload["target_epoch"]
    if expected_epoch < 1 or target_epoch != expected_epoch + 1:
        raise DrOrchestrationError("target Writer epoch must advance exactly once")
    release_sha = str(payload["release_sha"]).lower()
    if not SHA_RE.fullmatch(release_sha):
        raise DrOrchestrationError("release SHA is malformed")
    if payload["domain"] != "gold-trading.ir" or payload["record"] != "app":
        raise DrOrchestrationError("source orchestrator remains locked to the failover-test domain")
    try:
        current_ip = str(ipaddress.ip_address(str(payload["expected_current_ip"])))
        target_ip = str(ipaddress.ip_address(str(payload["target_ip"])))
    except ValueError as exc:
        raise DrOrchestrationError("orchestration origin IP is invalid") from exc
    if current_ip == target_ip:
        raise DrOrchestrationError("source and target origin IPs must be distinct")
    classification = payload["classification"]
    if not isinstance(classification, dict) or set(classification) != {
        "mode", "confidence", "consecutive_rounds", "evidence_hash",
        "campaign_id", "policy_hash",
    }:
        raise DrOrchestrationError("classification evidence is invalid")
    required_mode = "isolated" if action == "promote_ir" else "online"
    if (
        classification["mode"] != required_mode
        or classification["confidence"] != "high"
        or type(classification["consecutive_rounds"]) is not int
        or classification["consecutive_rounds"] < 3
        or not re.fullmatch(r"[0-9a-f]{64}", str(classification["evidence_hash"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(classification["policy_hash"]))
    ):
        raise DrOrchestrationError("classification lacks stable multi-vantage evidence")
    try:
        UUID(str(classification["campaign_id"]))
    except ValueError as exc:
        raise DrOrchestrationError("classification campaign id is invalid") from exc
    rpo_policy = payload["rpo_policy"]
    if not isinstance(rpo_policy, dict) or set(rpo_policy) != {
        "mode", "max_unreplicated_events", "approval_reason", "approval_ticket"
    }:
        raise DrOrchestrationError("RPO policy fields are invalid")
    rpo_mode = str(rpo_policy["mode"])
    max_unreplicated = rpo_policy["max_unreplicated_events"]
    reason = rpo_policy["approval_reason"]
    ticket = rpo_policy["approval_ticket"]
    if (
        rpo_mode != "zero_loss"
        or type(max_unreplicated) is not int
        or max_unreplicated < 0
        or max_unreplicated > 1_000_000
    ):
        raise DrOrchestrationError("RPO policy is invalid")
    if max_unreplicated != 0 or reason is not None or ticket is not None:
        raise DrOrchestrationError("zero-loss RPO policy cannot authorize residual loss")
    readiness_hash = str(payload["readiness_hash"])
    if not re.fullmatch(r"[0-9a-f]{64}", readiness_hash):
        raise DrOrchestrationError("readiness evidence hash is malformed")
    command_manifest_hash = str(payload["command_manifest_hash"])
    if not re.fullmatch(r"[0-9a-f]{64}", command_manifest_hash):
        raise DrOrchestrationError("command manifest hash is malformed")
    approver_policy_hash = str(payload["approver_policy_hash"])
    if not re.fullmatch(r"[0-9a-f]{64}", approver_policy_hash):
        raise DrOrchestrationError("approver policy hash is malformed")
    approvals = payload["approvals"]
    if not isinstance(approvals, list) or len(approvals) != 2:
        raise DrOrchestrationError("exactly two approvals are required")
    unsigned = {key: value for key, value in payload.items() if key != "approvals"}
    plan_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    return FailoverPlan(
        operation_id=operation_id,
        operation_nonce=operation_nonce,
        generated_at=generated_at,
        expires_at=expires_at,
        action=action,
        source_site=source,
        target_site=target,
        expected_epoch=expected_epoch,
        target_epoch=target_epoch,
        release_sha=release_sha,
        domain=str(payload["domain"]),
        record=str(payload["record"]),
        expected_current_ip=current_ip,
        target_ip=target_ip,
        classification=dict(classification),
        rpo_policy=dict(rpo_policy),
        readiness_hash=readiness_hash,
        command_manifest_hash=command_manifest_hash,
        approver_policy_hash=approver_policy_hash,
        approvals=tuple(dict(item) for item in approvals),
        plan_hash=plan_hash,
    )


def _parse_plan_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DrOrchestrationError(f"orchestration plan {field} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DrOrchestrationError(f"orchestration plan {field} is invalid") from exc
    if parsed.tzinfo is None:
        raise DrOrchestrationError(f"orchestration plan {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_plan_freshness(plan: FailoverPlan, *, now: datetime | None = None) -> None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if plan.generated_at > current + MAX_PLAN_FUTURE_SKEW:
        raise DrOrchestrationError("orchestration plan was generated in the future")
    if current >= plan.expires_at:
        raise DrOrchestrationError("orchestration plan has expired")


def load_approver_policy(path: Path = PINNED_APPROVER_POLICY_PATH) -> ApproverPolicy:
    try:
        payload = __import__("json").loads(
            read_secure_text(path, label="failover approver policy", max_size=64 * 1024)
        )
    except Exception as exc:
        raise DrOrchestrationError("failover approver policy is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "policy_id", "release_sha", "minimum_approvals", "signers"
    } or payload["schema"] != APPROVER_POLICY_SCHEMA or payload["minimum_approvals"] != 2:
        raise DrOrchestrationError("failover approver policy fields/schema are invalid")
    try:
        policy_id = str(UUID(str(payload["policy_id"])))
    except ValueError as exc:
        raise DrOrchestrationError("failover approver policy id is invalid") from exc
    release_sha = str(payload["release_sha"]).lower()
    if not SHA_RE.fullmatch(release_sha) or not isinstance(payload["signers"], list):
        raise DrOrchestrationError("failover approver policy release/signers are invalid")
    signers: dict[str, ApprovedSigner] = {}
    operators: set[str] = set()
    custody_domains: set[str] = set()
    public_keys: set[bytes] = set()
    for raw in payload["signers"]:
        if not isinstance(raw, dict) or set(raw) != {
            "operator", "key_id", "custody_domain", "public_key"
        }:
            raise DrOrchestrationError("failover signer policy fields are invalid")
        operator = str(raw["operator"])
        key_id = str(raw["key_id"])
        custody = str(raw["custody_domain"])
        try:
            public_key = base64.b64decode(str(raw["public_key"]), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise DrOrchestrationError("failover signer public key is invalid") from exc
        if (
            not operator or operator in operators or not key_id or key_id in signers
            or not custody or custody in custody_domains or len(public_key) != 32
            or public_key in public_keys
        ):
            raise DrOrchestrationError("failover signer identities/custody are not independent")
        signers[key_id] = ApprovedSigner(operator, key_id, custody, public_key)
        operators.add(operator)
        custody_domains.add(custody)
        public_keys.add(public_key)
    if len(signers) < 2:
        raise DrOrchestrationError("failover approver policy needs two independent signers")
    policy_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ApproverPolicy(policy_id, release_sha, signers, policy_hash)


def verify_two_person_approvals(plan: FailoverPlan, policy: ApproverPolicy) -> None:
    if (
        policy.policy_hash != plan.approver_policy_hash
        or policy.release_sha != plan.release_sha
    ):
        raise DrOrchestrationError("approver policy is not pinned to the approved plan/release")
    operators: set[str] = set()
    key_ids: set[str] = set()
    custody_domains: set[str] = set()
    message = plan.plan_hash.encode("ascii")
    for approval in plan.approvals:
        if set(approval) != {"operator", "key_id", "signature"}:
            raise DrOrchestrationError("approval fields are invalid")
        operator = str(approval["operator"]).strip()
        key_id = str(approval["key_id"]).strip()
        signer = policy.signers.get(key_id)
        if (
            signer is None or operator != signer.operator or operator in operators
            or key_id in key_ids or signer.custody_domain in custody_domains
        ):
            raise DrOrchestrationError("approvals are not from two distinct authorized operators")
        try:
            public_key = Ed25519PublicKey.from_public_bytes(signer.public_key)
            public_key.verify(base64.b64decode(approval["signature"], validate=True), message)
        except (ValueError, binascii.Error, InvalidSignature) as exc:
            raise DrOrchestrationError("approval signature is invalid") from exc
        operators.add(operator)
        key_ids.add(key_id)
        custody_domains.add(signer.custody_domain)


def _validate_source_tail_boundary(
    value: Any,
    *,
    plan: FailoverPlan,
) -> dict[str, Any]:
    fields = {
        "mode",
        "origin_site",
        "target_site",
        "producer_epoch",
        "final_sequence",
        "final_transaction_hash",
        "estimated_unreplicated_events",
        "boundary_hash",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise DrOrchestrationError("source tail boundary fields are invalid")
    unsigned = {key: value[key] for key in fields - {"boundary_hash"}}
    expected_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    if (
        value["origin_site"] != plan.source_site
        or value["target_site"] != plan.target_site
        or type(value["producer_epoch"]) is not int
        or value["producer_epoch"] != plan.expected_epoch
        or type(value["final_sequence"]) is not int
        or value["final_sequence"] < 0
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["final_transaction_hash"]))
        or (
            value["final_sequence"] == 0
            and value["final_transaction_hash"] != "0" * 64
        )
        or (
            value["final_sequence"] > 0
            and value["final_transaction_hash"] == "0" * 64
        )
        or type(value["estimated_unreplicated_events"]) is not int
        or value["estimated_unreplicated_events"] < 0
        or not secrets.compare_digest(str(value["boundary_hash"]), expected_hash)
    ):
        raise DrOrchestrationError("source tail boundary is inconsistent")
    mode = str(value["mode"])
    estimated = int(value["estimated_unreplicated_events"])
    if mode == "proven":
        if estimated != 0:
            raise DrOrchestrationError("proven source tail cannot report residual loss")
    else:
        raise DrOrchestrationError("source tail boundary mode is invalid")
    return dict(value)


def _validate_step_result(
    step: str,
    result: Any,
    plan: FailoverPlan,
    *,
    source_tail_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise DrOrchestrationError(f"orchestration step {step} did not return status=ok")
    if result.get("operation_id") != plan.operation_id:
        raise DrOrchestrationError(f"orchestration step {step} is not bound to the operation")
    evidence_hash = str(result.get("evidence_hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_hash):
        raise DrOrchestrationError(f"orchestration step {step} lacks an evidence hash")
    if step == "classification_verified" and any(
        result.get(key) != plan.classification[key]
        for key in (
            "mode", "confidence", "consecutive_rounds", "evidence_hash",
            "campaign_id", "policy_hash",
        )
    ):
        raise DrOrchestrationError(
            "classification step is not bound to fresh signed approved evidence"
        )
    if step == "source_fenced":
        if (
            result.get("source_site") != plan.source_site
            or result.get("fenced") is not True
            or result.get("active_connections") != 0
            or result.get("boundary_captured_after_drain") is not True
        ):
            raise DrOrchestrationError("source fencing evidence does not match the approved source")
        _validate_source_tail_boundary(result.get("source_tail_boundary"), plan=plan)
    if step == "target_ready":
        if result.get("target_site") != plan.target_site or source_tail_boundary is None:
            raise DrOrchestrationError("target readiness evidence does not match the approved target")
        boundary = _validate_source_tail_boundary(source_tail_boundary, plan=plan)
        if result.get("source_tail_boundary_hash") != boundary["boundary_hash"]:
            raise DrOrchestrationError("target readiness is not bound to the fenced source tail")
        applied_through = result.get("target_applied_through_boundary")
        applied_sequence = result.get("target_applied_sequence")
        if type(applied_through) is not bool or type(applied_sequence) is not int:
            raise DrOrchestrationError("target applied-boundary evidence is malformed")
        if boundary["mode"] == "proven" and (
            applied_through is not True
            or applied_sequence < int(boundary["final_sequence"])
        ):
            raise DrOrchestrationError("target has not applied the proven source tail")
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


def _validate_rollback_result(
    result: Any,
    plan: FailoverPlan,
    *,
    failed_step: str,
    completed_steps: tuple[str, ...],
) -> dict[str, Any]:
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
        or result.get("source_fenced") is not True
        or type(result.get("source_active_connections")) is not int
        or result["source_active_connections"] != 0
    ):
        raise DrOrchestrationError("orchestration rollback lacks target fence/drain evidence")
    rollback_state = result.get("rollback_state")
    witness_receipt_hash = str(result.get("witness_receipt_hash") or "")
    witness_request_id = str(result.get("witness_request_id") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", witness_receipt_hash) or not witness_request_id:
        raise DrOrchestrationError("orchestration rollback lacks a durable Witness receipt")
    if rollback_state == "safe_fenced":
        if (
            result.get("witness_lease_live") is not False
            or result.get("holder_site") not in {None, plan.source_site, plan.target_site}
            or result.get("origin_ip") not in {plan.expected_current_ip, plan.target_ip}
            or result.get("domain") != plan.domain
            or result.get("record") != plan.record
        ):
            raise DrOrchestrationError("safe-fenced rollback evidence is inconsistent")
    else:
        raise DrOrchestrationError(
            "orchestration rollback must remain safe-fenced; restoration requires a new signed plan"
        )
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


def _validate_ledger_result(
    result: Any,
    plan: FailoverPlan,
    *,
    allowed_statuses: set[str],
) -> dict[str, Any]:
    required = {
        "status", "operation_id", "operation_nonce", "plan_hash",
        "ledger_receipt_hash", "ledger_receipt_id",
    }
    if not isinstance(result, dict) or set(result) != required:
        raise DrOrchestrationError("independent operation ledger receipt fields are invalid")
    if (
        result["status"] not in allowed_statuses
        or result["operation_id"] != plan.operation_id
        or result["operation_nonce"] != plan.operation_nonce
        or result["plan_hash"] != plan.plan_hash
        or not re.fullmatch(r"[0-9a-f]{64}", str(result["ledger_receipt_hash"]))
        or not str(result["ledger_receipt_id"])
    ):
        raise DrOrchestrationError("independent operation ledger receipt is inconsistent")
    return result


async def _finalize_ledger(
    ledger: OperationLedger,
    plan: FailoverPlan,
    *,
    outcome: str,
    evidence_hash: str,
) -> dict[str, Any]:
    result = _validate_ledger_result(
        await ledger.finalize(plan, outcome=outcome, evidence_hash=evidence_hash),
        plan,
        allowed_statuses={"completed", "rolled_back"},
    )
    if result["status"] != outcome:
        raise DrOrchestrationError("independent operation ledger finalized the wrong outcome")
    return result


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
            failed_step=failed_step,
            completed_steps=completed_steps,
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


async def _finish_expired_operation(
    plan: FailoverPlan,
    *,
    adapter: OrchestrationAdapter,
    ledger: OperationLedger,
    journal_path: Path,
    completed: set[str],
    started: set[str] | None = None,
) -> dict[str, Any]:
    """Stop an expired saga without permitting another forward mutation."""
    completed_steps = tuple(step for step in STEPS[:-1] if step in completed)
    ambiguous_started = set(started or ()) - set(completed)
    mutating_steps = (
        set(completed_steps) | ambiguous_started
    ) - {"classification_verified"}
    if mutating_steps:
        result = await _rollback_after_failure(
            plan,
            adapter=adapter,
            journal_path=journal_path,
            failed_step=(
                "ambiguous:" + sorted(ambiguous_started)[-1]
                if ambiguous_started
                else "expired_plan"
            ),
            completed_steps=completed_steps,
        )
        evidence_hash = str(result["evidence_hash"])
        rollback_state = result["rollback_state"]
    else:
        evidence_hash = hashlib.sha256(
            canonical_json_bytes(
                {
                    "operation_id": plan.operation_id,
                    "plan_hash": plan.plan_hash,
                    "outcome": "expired_without_mutation",
                }
            )
        ).hexdigest()
        rollback_state = "not_started"
        _append_operation_record(
            journal_path,
            plan,
            event="dr.orchestration.expired_without_mutation",
            evidence_hash=evidence_hash,
        )
    await _finalize_ledger(
        ledger,
        plan,
        outcome="rolled_back",
        evidence_hash=evidence_hash,
    )
    return {
        "status": "rolled_back",
        "operation_id": plan.operation_id,
        "plan_hash": plan.plan_hash,
        "rollback_state": rollback_state,
        "reason": "plan_expired",
    }


async def run_orchestration(
    plan: FailoverPlan,
    *,
    adapter: OrchestrationAdapter,
    ledger: OperationLedger,
    journal_path: Path,
) -> dict[str, Any]:
    records = verify_hash_chained_jsonl(journal_path, label="failover journal") if journal_path.exists() else []
    operation_records = [record for record in records if record.get("operation_id") == plan.operation_id]
    freshness_error: DrOrchestrationError | None = None
    try:
        validate_plan_freshness(plan)
    except DrOrchestrationError as exc:
        if not operation_records:
            raise
        freshness_error = exc
    if any(record.get("plan_hash") != plan.plan_hash for record in operation_records):
        raise DrOrchestrationError("journal operation_id was reused with a different plan")
    reservation = _validate_ledger_result(
        await ledger.reserve(plan),
        plan,
        allowed_statuses={"reserved", "existing", "expired"},
    )
    if reservation["status"] == "expired" and freshness_error is None:
        freshness_error = DrOrchestrationError(
            "independent operation ledger reports an expired plan"
        )
    local_reservations = [
        record for record in operation_records
        if record.get("event") == "dr.orchestration.operation_reserved"
    ]
    if reservation["status"] == "existing" and not local_reservations:
        raise DrOrchestrationError(
            "operation is already consumed by another controller; independent recovery is required"
        )
    if local_reservations and any(
        record.get("ledger_receipt_hash") != reservation["ledger_receipt_hash"]
        or record.get("ledger_receipt_id") != reservation["ledger_receipt_id"]
        for record in local_reservations
    ):
        raise DrOrchestrationError("local journal conflicts with the independent operation ledger")
    if not local_reservations:
        _append_operation_record(
            journal_path,
            plan,
            event="dr.orchestration.operation_reserved",
            ledger_receipt_hash=reservation["ledger_receipt_hash"],
            ledger_receipt_id=reservation["ledger_receipt_id"],
        )
    completed = {
        str(record.get("step"))
        for record in operation_records
        if record.get("event") == "dr.orchestration.step_completed"
    }
    started = {
        str(record.get("step"))
        for record in operation_records
        if record.get("event") == "dr.orchestration.step_started"
    }
    ambiguous_started = {
        step
        for step in started - completed
        if not any(
            record.get("event") == "dr.orchestration.step_failed"
            and record.get("step") == step
            for record in operation_records
        )
    }
    source_tail_boundary: dict[str, Any] | None = None
    source_boundary_records = [
        record
        for record in operation_records
        if record.get("event") == "dr.orchestration.step_completed"
        and record.get("step") == "source_fenced"
    ]
    if source_boundary_records:
        source_tail_boundary = _validate_source_tail_boundary(
            source_boundary_records[-1].get("source_tail_boundary"),
            plan=plan,
        )
    completion_records = [
        record
        for record in operation_records
        if record.get("event") == "dr.orchestration.step_completed"
        and record.get("step") == "completed"
    ]
    if completion_records:
        await _finalize_ledger(
            ledger,
            plan,
            outcome="completed",
            evidence_hash=str(completion_records[-1]["evidence_hash"]),
        )
        return {
            "status": "completed",
            "operation_id": plan.operation_id,
            "plan_hash": plan.plan_hash,
        }
    if any(record.get("event") == "dr.orchestration.rollback_completed" for record in operation_records):
        rollback_record = next(
            record for record in reversed(operation_records)
            if record.get("event") == "dr.orchestration.rollback_completed"
        )
        await _finalize_ledger(
            ledger,
            plan,
            outcome="rolled_back",
            evidence_hash=str(rollback_record["evidence_hash"]),
        )
        return {"status": "rolled_back", "operation_id": plan.operation_id, "plan_hash": plan.plan_hash}
    if freshness_error is not None:
        return await _finish_expired_operation(
            plan,
            adapter=adapter,
            ledger=ledger,
            journal_path=journal_path,
            completed=completed,
            started=started,
        )
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
    if ambiguous_started:
        # A durable intent without a terminal step record means the remote
        # mutation may have committed while its response was lost.  Never
        # replay forward or call it "not started"; converge to the typed safe
        # rollback contract first.
        failed_step = "ambiguous:" + sorted(
            ambiguous_started,
            key=lambda item: STEPS.index(item),
        )[-1]
        completed_steps = tuple(step for step in STEPS[:-1] if step in completed)
        result = await _rollback_after_failure(
            plan,
            adapter=adapter,
            journal_path=journal_path,
            failed_step=failed_step,
            completed_steps=completed_steps,
        )
        await _finalize_ledger(
            ledger,
            plan,
            outcome="rolled_back",
            evidence_hash=result["evidence_hash"],
        )
        return {
            "status": "rolled_back",
            "operation_id": plan.operation_id,
            "plan_hash": plan.plan_hash,
            "rollback_state": result["rollback_state"],
            "reason": "ambiguous_remote_step_recovered",
        }
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
            await _finalize_ledger(
                ledger,
                plan,
                outcome="rolled_back",
                evidence_hash=result["evidence_hash"],
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
        try:
            validate_plan_freshness(plan)
        except DrOrchestrationError:
            return await _finish_expired_operation(
                plan,
                adapter=adapter,
                ledger=ledger,
                journal_path=journal_path,
                completed=completed,
                started=started,
            )
        method = getattr(adapter, step)
        _append_operation_record(
            journal_path,
            plan,
            event="dr.orchestration.step_started",
            step=step,
        )
        started.add(step)
        try:
            raw_result = (
                await method(plan, source_tail_boundary=source_tail_boundary)
                if step == "target_ready"
                else await method(plan)
            )
            result = _validate_step_result(
                step,
                raw_result,
                plan,
                source_tail_boundary=source_tail_boundary,
            )
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
                rollback = await _rollback_after_failure(
                    plan,
                    adapter=adapter,
                    journal_path=journal_path,
                    failed_step=step,
                    completed_steps=completed_steps,
                )
                await _finalize_ledger(
                    ledger,
                    plan,
                    outcome="rolled_back",
                    evidence_hash=rollback["evidence_hash"],
                )
            raise
        record_fields: dict[str, Any] = {}
        if step == "source_fenced":
            source_tail_boundary = _validate_source_tail_boundary(
                result.get("source_tail_boundary"),
                plan=plan,
            )
            record_fields["source_tail_boundary"] = source_tail_boundary
        if step == "target_ready":
            record_fields.update(
                source_tail_boundary_hash=result["source_tail_boundary_hash"],
                target_applied_sequence=result["target_applied_sequence"],
                target_applied_through_boundary=result[
                    "target_applied_through_boundary"
                ],
            )
        _append_operation_record(
            journal_path,
            plan,
            event="dr.orchestration.step_completed",
            step=step,
            evidence_hash=result["evidence_hash"],
            **record_fields,
        )
        completed.add(step)
    try:
        validate_plan_freshness(plan)
    except DrOrchestrationError:
        return await _finish_expired_operation(
            plan,
            adapter=adapter,
            ledger=ledger,
            journal_path=journal_path,
            completed=completed,
            started=started,
        )
    completion_hash = hashlib.sha256(
        canonical_json_bytes({"operation_id": plan.operation_id, "plan_hash": plan.plan_hash})
    ).hexdigest()
    if "completed" not in completed:
        _append_operation_record(
            journal_path,
            plan,
            event="dr.orchestration.step_completed",
            step="completed",
            evidence_hash=completion_hash,
        )
    await _finalize_ledger(
        ledger,
        plan,
        outcome="completed",
        evidence_hash=completion_hash,
    )
    return {"status": "completed", "operation_id": plan.operation_id, "plan_hash": plan.plan_hash}
