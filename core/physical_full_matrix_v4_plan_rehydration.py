"""Fail-closed rehydration of a V4 Full-Matrix plan after process restart.

The V4 execution plan is intentionally process-local and nonserializable.  A
restart therefore must not deserialize a plan or infer authority from a raw
receipt.  This narrow bridge may mint a new process-local plan only from a
nonserializable campaign-continuity projection minted by the Witness-anchored
V4 journal.  It rebuilds the deterministic non-authorizing plan from the
static config and requires both its plan SHA and its full baseline binding to
match that projection.  It does not itself certify that the projection is the
latest anchor head; every actual driver call re-reads the concrete journal and
requires fresh continuity before an adapter callback.

This module performs no filesystem, network, provider, host, database,
Docker, promotion, writer, or phase operation.  Rehydrating a plan is not a
permission to execute it: the V4 driver will still obtain fresh readiness,
recheck continuity, and use its durable pre-effect journal transition before
each phase.
"""

from __future__ import annotations

import hashlib

from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_full_matrix_v4_receipt_journal as _journal


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_SCHEMA",
    "PhysicalFullMatrixV4PlanRehydrationError",
    "rehydrate_physical_full_matrix_v4_execution_plan",
)


PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-plan-rehydration-v1"
)


class PhysicalFullMatrixV4PlanRehydrationError(ValueError):
    """A restart attempted to recreate V4 plan provenance unsafely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4PlanRehydrationError(code)


def rehydrate_physical_full_matrix_v4_execution_plan(
    *,
    config: _driver.PhysicalFullMatrixV4ExecutionConfig,
    continuity: _journal.VerifiedPhysicalFullMatrixV4CampaignContinuity,
) -> _driver.PhysicalFullMatrixV4ExecutionPlan:
    """Mint one new process-local, non-authorizing V4 plan after restart.

    ``continuity`` must be a process-local projection minted by the root-owned
    journal after it compared fixed local state with an external Witness head.
    This function deliberately does not accept raw receipts, canonical plan
    bytes, a caller-supplied plan SHA, or a generic "resume" boolean.  It also
    deliberately does not call an adapter or treat an older projection as a
    resume permit: the V4 driver re-verifies current continuity immediately
    before it may call an adapter.
    """

    try:
        verified = _journal.require_verified_physical_full_matrix_v4_campaign_continuity(
            continuity
        )
    except _journal.PhysicalFullMatrixV4ReceiptJournalError as exc:
        raise PhysicalFullMatrixV4PlanRehydrationError(
            "PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_CONTINUITY_INVALID"
        ) from exc

    try:
        binding, run_id, maximum_age = _driver._static_config(
            config,
            require_enabled=True,
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4PlanRehydrationError(
            "PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_CONFIG_INVALID"
        ) from exc

    if (
        verified.run_id != run_id
        or type(verified.completed_sequence) is not int
        or not 0 <= verified.completed_sequence <= len(_driver.PHYSICAL_FULL_MATRIX_V4_PHASES)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_CONTINUITY_MISMATCH")
    try:
        _driver._snapshot_binding(verified.active_binding, direction=None)
    except Exception as exc:
        raise PhysicalFullMatrixV4PlanRehydrationError(
            "PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_CONTINUITY_MISMATCH"
        ) from exc

    provisional = _driver._PlanSnapshot(
        canonical_plan=b"",
        plan_sha256="",
        run_id=run_id,
        binding=binding,
        phases=_driver._phase_snapshots(),
        maximum_oracle_age_seconds=maximum_age,
    )
    canonical = _driver._canonical_plan(provisional)
    plan_sha256 = hashlib.sha256(canonical).hexdigest()
    if plan_sha256 != verified.plan_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_PLAN_MISMATCH")
    try:
        baseline_plan_binding_sha256 = (
            _journal.derive_physical_full_matrix_v4_receipt_journal_baseline_plan_binding_sha256(
                run_id=run_id,
                plan_sha256=plan_sha256,
                initial_active_binding=_driver._binding_from_snapshot(binding),
            )
        )
    except _journal.PhysicalFullMatrixV4ReceiptJournalError as exc:
        raise PhysicalFullMatrixV4PlanRehydrationError(
            "PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_BASELINE_INVALID"
        ) from exc
    if baseline_plan_binding_sha256 != verified.baseline_plan_binding_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_BASELINE_MISMATCH")

    snapshot = _driver._PlanSnapshot(
        canonical_plan=canonical,
        plan_sha256=plan_sha256,
        run_id=run_id,
        binding=binding,
        phases=provisional.phases,
        maximum_oracle_age_seconds=maximum_age,
    )
    result = _driver.PhysicalFullMatrixV4ExecutionPlan(
        canonical_plan=snapshot.canonical_plan,
        plan_sha256=snapshot.plan_sha256,
        run_id=snapshot.run_id,
        binding=_driver._binding_from_snapshot(snapshot.binding),
        phases=tuple(_driver._phase_from_snapshot(phase) for phase in snapshot.phases),
        maximum_oracle_age_seconds=snapshot.maximum_oracle_age_seconds,
    )
    # The V4 driver owns this opaque capability.  It is deliberately set only
    # after a verified continuity projection matched both deterministic pins.
    object.__setattr__(result, "_capability", _driver._PLAN_CAPABILITY)
    _driver._PLAN_STATES[result] = _driver._PlanState(snapshot=snapshot)
    try:
        return _driver.require_physical_full_matrix_v4_execution_plan(result)
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4PlanRehydrationError(
            "PHYSICAL_FULL_MATRIX_V4_PLAN_REHYDRATION_RESULT_INVALID"
        ) from exc
