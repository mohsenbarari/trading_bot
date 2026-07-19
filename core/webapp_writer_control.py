"""Durable local WebApp writer state and explicit operator transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.runtime_identity import RuntimeIdentity, WEBAPP_SITES
from core.writer_witness_contract import (
    ValidatedWitnessLeaseProof,
    witness_timing_configuration_is_safe,
)
from core.writer_lease_clock import lease_clock_reasons
from models.webapp_writer_state import WebappWriterState, WebappWriterTransition


CONTROL_ACTIVE = "active"
CONTROL_FENCED = "fenced"
CONTROL_HANDOFF = "handoff"

ACTION_FENCE = "fence"
ACTION_ACTIVATE = "activate"
ACTION_APPROVE = "approve"
ACTION_LEASE_REFRESH = "lease_refresh"

REQUIRED_READY_EVIDENCE_FLAGS = (
    "schema_compatible",
    "release_compatible",
    "database_ready",
    "storage_ready",
    "sync_checkpoint_ready",
    "no_critical_conflicts",
    "background_jobs_ready",
    "fencing_acknowledged",
)


class WriterControlError(RuntimeError):
    """Raised when a control-plane transition is stale, incomplete, or unsafe."""


@dataclass(frozen=True)
class WriterStateSnapshot:
    active_site: str | None
    writer_epoch: int
    control_state: str
    transition_id: str
    readiness_evidence_hash: str | None
    readiness_evidence_id: str | None
    readiness_approved_by: str | None
    readiness_approved_at: datetime | None
    readiness_expires_at: datetime | None
    witness_lease_id: str | None = None
    witness_lease_expires_at: datetime | None = None
    witness_proof_hash: str | None = None
    witness_transition_id: str | None = None
    witness_lease_issued_at: datetime | None = None
    witness_local_boot_id: str | None = None
    witness_local_boottime_deadline: float | None = None
    witness_observed_wall_at: datetime | None = None
    witness_observed_boottime: float | None = None
    witness_clock_offset_ms: int | None = None

    def local_runtime_role(self, physical_site: str) -> str:
        if self.control_state == CONTROL_ACTIVE:
            return "active" if self.active_site == physical_site else "standby"
        if self.control_state == CONTROL_HANDOFF:
            return "recovering"
        return "fenced"


@dataclass(frozen=True)
class ValidatedReadinessEvidence:
    evidence_id: str
    target_site: str
    writer_epoch: int
    generated_at: datetime
    expires_at: datetime
    content_hash: str
    canonical_payload: dict[str, Any]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def writer_state_snapshot(state: WebappWriterState) -> WriterStateSnapshot:
    return WriterStateSnapshot(
        active_site=state.active_site,
        writer_epoch=int(state.writer_epoch),
        control_state=state.control_state,
        transition_id=state.transition_id,
        readiness_evidence_hash=state.readiness_evidence_hash,
        readiness_evidence_id=state.readiness_evidence_id,
        readiness_approved_by=state.readiness_approved_by,
        readiness_approved_at=state.readiness_approved_at,
        readiness_expires_at=state.readiness_expires_at,
        witness_lease_id=getattr(state, "witness_lease_id", None),
        witness_lease_expires_at=getattr(state, "witness_lease_expires_at", None),
        witness_proof_hash=getattr(state, "witness_proof_hash", None),
        witness_transition_id=getattr(state, "witness_transition_id", None),
        witness_lease_issued_at=getattr(state, "witness_lease_issued_at", None),
        witness_local_boot_id=getattr(state, "witness_local_boot_id", None),
        witness_local_boottime_deadline=getattr(state, "witness_local_boottime_deadline", None),
        witness_observed_wall_at=getattr(state, "witness_observed_wall_at", None),
        witness_observed_boottime=getattr(state, "witness_observed_boottime", None),
        witness_clock_offset_ms=getattr(state, "witness_clock_offset_ms", None),
    )


async def load_writer_state(
    session: AsyncSession,
    *,
    for_update: bool = False,
) -> WebappWriterState:
    statement = select(WebappWriterState).where(WebappWriterState.authority == "webapp")
    if for_update:
        statement = statement.with_for_update()
    state = (await session.execute(statement)).scalar_one_or_none()
    if state is None:
        raise WriterControlError("webapp_writer_state row is missing; writer must fail closed")
    return state


async def load_writer_snapshot(session: AsyncSession) -> WriterStateSnapshot:
    return writer_state_snapshot(await load_writer_state(session))


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise WriterControlError(f"readiness evidence requires {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WriterControlError(f"readiness evidence {field} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise WriterControlError(f"readiness evidence {field} must include a timezone")
    return _utc(parsed)


def validate_readiness_evidence(
    payload: dict[str, Any],
    *,
    target_site: str,
    writer_epoch: int,
    now: datetime | None = None,
    max_age_seconds: int | None = None,
) -> ValidatedReadinessEvidence:
    if target_site not in WEBAPP_SITES:
        raise WriterControlError(f"unsupported target_site={target_site!r}")
    if not isinstance(payload, dict):
        raise WriterControlError("readiness evidence must be a JSON object")
    evidence_id = str(payload.get("evidence_id") or "").strip()
    if not evidence_id or len(evidence_id) > 64:
        raise WriterControlError("readiness evidence_id is required and must be <= 64 characters")
    if payload.get("target_site") != target_site:
        raise WriterControlError("readiness evidence target_site does not match the requested site")
    try:
        evidence_epoch = int(payload.get("writer_epoch"))
    except (TypeError, ValueError) as exc:
        raise WriterControlError("readiness evidence writer_epoch must be an integer") from exc
    if evidence_epoch != int(writer_epoch):
        raise WriterControlError("readiness evidence writer_epoch does not match the requested epoch")
    failed_flags = [name for name in REQUIRED_READY_EVIDENCE_FLAGS if payload.get(name) is not True]
    if failed_flags:
        raise WriterControlError(
            "readiness evidence has false/missing gates: " + ",".join(sorted(failed_flags))
        )
    generated_at = _parse_timestamp(payload.get("generated_at"), "generated_at")
    expires_at = _parse_timestamp(payload.get("expires_at"), "expires_at")
    current = _utc(now or datetime.now(timezone.utc))
    allowed_age = max(
        1,
        int(
            max_age_seconds
            if max_age_seconds is not None
            else settings.origin_readiness_max_evidence_age_seconds
        ),
    )
    if generated_at > current:
        raise WriterControlError("readiness evidence was generated in the future")
    if (current - generated_at).total_seconds() > allowed_age:
        raise WriterControlError("readiness evidence is older than the configured maximum age")
    if expires_at <= current:
        raise WriterControlError("readiness evidence is expired")
    if (expires_at - generated_at).total_seconds() > allowed_age:
        raise WriterControlError("readiness evidence lifetime exceeds the configured maximum")
    canonical = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return ValidatedReadinessEvidence(
        evidence_id=evidence_id,
        target_site=target_site,
        writer_epoch=evidence_epoch,
        generated_at=generated_at,
        expires_at=expires_at,
        content_hash=content_hash,
        canonical_payload=canonical,
    )


def snapshot_is_local_active(
    identity: RuntimeIdentity,
    snapshot: WriterStateSnapshot,
    *,
    now: datetime | None = None,
    require_readiness_evidence: bool = False,
    require_witness_lease: bool = False,
    witness_safety_margin_seconds: int | None = None,
    current_boot_id: str | None = None,
    current_boottime: float | None = None,
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if not identity.is_webapp_site or not identity.is_webapp_authority:
        reasons.append("not_webapp_site")
    if snapshot.control_state != CONTROL_ACTIVE:
        reasons.append("writer_control_not_active")
    if snapshot.active_site != identity.physical_site:
        reasons.append("writer_active_site_mismatch")
    if snapshot.writer_epoch < 1:
        reasons.append("writer_epoch_invalid")
    if require_readiness_evidence:
        if not snapshot.readiness_evidence_hash or not snapshot.readiness_evidence_id:
            reasons.append("readiness_evidence_missing")
        if snapshot.readiness_approved_at is None or snapshot.readiness_expires_at is None:
            reasons.append("readiness_approval_missing")
        elif _utc(snapshot.readiness_expires_at) <= _utc(now or datetime.now(timezone.utc)):
            reasons.append("readiness_evidence_expired")
    if require_witness_lease:
        if not witness_timing_configuration_is_safe(
            lease_duration_seconds=settings.writer_witness_lease_duration_seconds,
            renew_interval_seconds=settings.writer_witness_renew_interval_seconds,
            safety_margin_seconds=settings.writer_witness_safety_margin_seconds,
            max_clock_skew_seconds=settings.writer_witness_max_clock_skew_seconds,
        ):
            reasons.append("writer_witness_timing_invalid")
        if (
            not snapshot.witness_lease_id
            or not snapshot.witness_proof_hash
            or not snapshot.witness_transition_id
        ):
            reasons.append("writer_witness_proof_missing")
        if snapshot.witness_lease_expires_at is None or snapshot.witness_lease_issued_at is None:
            reasons.append("writer_witness_expiry_missing")
        reasons.extend(
            lease_clock_reasons(
                stored_boot_id=snapshot.witness_local_boot_id,
                stored_boottime_deadline=snapshot.witness_local_boottime_deadline,
                current_boot=current_boot_id,
                current_boottime=current_boottime,
                boot_id_file=settings.writer_witness_boot_id_file,
            )
        )
    return not reasons, tuple(reasons)


def _clear_readiness(state: WebappWriterState) -> None:
    state.readiness_evidence_hash = None
    state.readiness_evidence_id = None
    state.readiness_approved_by = None
    state.readiness_approved_at = None
    state.readiness_expires_at = None


def _clear_witness_lease(state: WebappWriterState) -> None:
    state.witness_lease_id = None
    state.witness_lease_expires_at = None
    state.witness_proof_hash = None
    state.witness_transition_id = None
    state.witness_lease_issued_at = None
    state.witness_local_boot_id = None
    state.witness_local_boottime_deadline = None
    state.witness_observed_wall_at = None
    state.witness_observed_boottime = None
    state.witness_clock_offset_ms = None


def _store_witness_lease(
    state: WebappWriterState,
    proof: ValidatedWitnessLeaseProof,
) -> None:
    if proof.clock_evidence is None:
        raise WriterControlError("validated witness proof lacks host monotonic timing evidence")
    state.witness_lease_id = proof.lease_id
    state.witness_lease_issued_at = proof.issued_at
    state.witness_lease_expires_at = proof.expires_at
    state.witness_proof_hash = proof.proof_hash
    state.witness_transition_id = proof.witness_transition_id
    state.witness_local_boot_id = proof.clock_evidence.boot_id
    state.witness_local_boottime_deadline = proof.clock_evidence.boottime_deadline
    state.witness_observed_wall_at = proof.clock_evidence.observed_wall_at
    state.witness_observed_boottime = proof.clock_evidence.observed_boottime
    state.witness_clock_offset_ms = proof.clock_evidence.witness_issue_offset_ms


async def transition_writer_state(
    session: AsyncSession,
    *,
    action: str,
    identity: RuntimeIdentity,
    expected_epoch: int,
    expected_active_site: str | None,
    operator: str,
    reason: str,
    evidence: ValidatedReadinessEvidence | None = None,
    witness_proof: ValidatedWitnessLeaseProof | None = None,
    now: datetime | None = None,
) -> WriterStateSnapshot:
    if not identity.is_webapp_site:
        raise WriterControlError("writer transitions are valid only on a WebApp physical site")
    if evidence is not None and action not in {ACTION_ACTIVATE, ACTION_APPROVE}:
        raise WriterControlError("readiness evidence is not valid for this writer action")
    if witness_proof is not None and action not in {ACTION_ACTIVATE, ACTION_LEASE_REFRESH}:
        raise WriterControlError("witness lease proof is not valid for this writer action")
    operator = operator.strip()
    reason = reason.strip()
    if not operator or not reason:
        raise WriterControlError("operator and reason are mandatory")
    state = await load_writer_state(session, for_update=True)
    previous_site = state.active_site
    previous_epoch = int(state.writer_epoch)
    if previous_epoch != int(expected_epoch):
        raise WriterControlError(
            f"stale writer epoch: current={previous_epoch} expected={expected_epoch}"
        )
    if previous_site != expected_active_site:
        raise WriterControlError(
            f"stale active site: current={previous_site!r} expected={expected_active_site!r}"
        )

    audit_transition_id = str(uuid4())
    transition_id = audit_transition_id
    new_site: str | None
    new_epoch = previous_epoch
    evidence_hash: str | None = None
    witness_hash = witness_proof.proof_hash if witness_proof is not None else None
    approved_at = _utc(now or datetime.now(timezone.utc))

    if action == ACTION_FENCE:
        if state.control_state == CONTROL_FENCED and state.active_site is None:
            raise WriterControlError("writer is already fenced")
        state.control_state = CONTROL_FENCED
        state.active_site = None
        _clear_readiness(state)
        _clear_witness_lease(state)
        new_site = None
    elif action == ACTION_ACTIVATE:
        if state.control_state != CONTROL_FENCED or state.active_site is not None:
            raise WriterControlError("activation requires a locally fenced writer state")
        new_epoch = witness_proof.writer_epoch if witness_proof is not None else previous_epoch + 1
        if new_epoch <= previous_epoch:
            raise WriterControlError("activation witness epoch must advance the local writer epoch")
        if settings.writer_witness_required and witness_proof is None:
            raise WriterControlError("activation requires a validated witness lease proof")
        if witness_proof is not None and witness_proof.holder_site != identity.physical_site:
            raise WriterControlError("activation witness proof does not belong to the local site")
        if evidence is None:
            raise WriterControlError("activation requires validated readiness evidence")
        if evidence.target_site != identity.physical_site or evidence.writer_epoch != new_epoch:
            raise WriterControlError("activation evidence does not match local site/new epoch")
        state.control_state = CONTROL_ACTIVE
        state.active_site = identity.physical_site
        state.writer_epoch = new_epoch
        state.readiness_evidence_hash = evidence.content_hash
        state.readiness_evidence_id = evidence.evidence_id
        state.readiness_approved_by = operator
        state.readiness_approved_at = approved_at
        state.readiness_expires_at = evidence.expires_at
        if witness_proof is not None:
            _store_witness_lease(state, witness_proof)
        evidence_hash = evidence.content_hash
        new_site = identity.physical_site
    elif action == ACTION_LEASE_REFRESH:
        if state.control_state != CONTROL_ACTIVE or state.active_site != identity.physical_site:
            raise WriterControlError("lease refresh requires the local site to be active")
        if witness_proof is None:
            raise WriterControlError("lease refresh requires a validated witness lease proof")
        if (
            witness_proof.holder_site != identity.physical_site
            or witness_proof.writer_epoch != previous_epoch
        ):
            raise WriterControlError("witness lease proof does not match the local active term")
        if state.witness_lease_id and state.witness_lease_id != witness_proof.lease_id:
            raise WriterControlError("witness lease id changed inside an active writer term")
        if (
            state.witness_lease_expires_at is not None
            and _utc(witness_proof.expires_at) <= _utc(state.witness_lease_expires_at)
        ):
            raise WriterControlError("witness lease refresh must advance the local expiry")
        _store_witness_lease(state, witness_proof)
        new_site = identity.physical_site
        # Lease renewal must not invalidate in-flight transactions. Keep the
        # writer transition id stable and use a separate audit row id.
        transition_id = state.transition_id
    elif action == ACTION_APPROVE:
        if state.control_state != CONTROL_ACTIVE or state.active_site != identity.physical_site:
            raise WriterControlError("readiness approval requires the local site to be active")
        if evidence is None:
            raise WriterControlError("readiness approval requires validated evidence")
        if evidence.target_site != identity.physical_site or evidence.writer_epoch != previous_epoch:
            raise WriterControlError("readiness evidence does not match local active site/epoch")
        state.readiness_evidence_hash = evidence.content_hash
        state.readiness_evidence_id = evidence.evidence_id
        state.readiness_approved_by = operator
        state.readiness_approved_at = approved_at
        state.readiness_expires_at = evidence.expires_at
        evidence_hash = evidence.content_hash
        new_site = identity.physical_site
    else:
        raise WriterControlError(f"unsupported writer transition action={action!r}")

    state.transition_id = transition_id
    state.updated_by = operator
    state.reason = reason
    session.add(
        WebappWriterTransition(
            transition_id=audit_transition_id,
            authority="webapp",
            action=action,
            previous_active_site=previous_site,
            new_active_site=new_site,
            previous_epoch=previous_epoch,
            new_epoch=new_epoch,
            operator=operator,
            reason=reason,
            evidence_hash=evidence_hash,
            witness_proof_hash=witness_hash,
        )
    )
    await session.flush()
    return writer_state_snapshot(state)
