"""Fail-closed P1 contract preflight for a future same-root atomic owner.

Phase 1 needs one PostgreSQL root transaction to flush the exact Gen2 strict
writer row and the exact signed V4 checkpoint together.  It also needs a
separate, restart-safe post-commit reconciler before any P1 completion can be
emitted.  Neither owner exists in this module.

This small boundary intentionally does *not* pretend that an opaque pending
Gen2 handoff and checkpoint admission share a transaction.  It revalidates
their exact process-local provenance and returns an opaque **unavailable**
contract preflight.  The result is useful only as a narrow integration target
for the future named atomic owner; it cannot execute SQL, open a transaction,
perform reconciliation, or authorize a phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Final
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint as _checkpoint
from core import physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_admission as _admission
from core import (
    physical_wal_v2_witness_roundtrip_strict_writer_bound_sqlalchemy_transaction
    as _gen2_transaction,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_STATUS",
    "PhysicalFullMatrixV4Phase1SameRootCoordinatorContractConfig",
    "PhysicalFullMatrixV4Phase1SameRootCoordinatorContractError",
    "UnavailablePhysicalFullMatrixV4Phase1SameRootCoordinatorPreflight",
    "preflight_physical_full_matrix_v4_phase1_same_root_coordinator_contract",
    "require_unavailable_physical_full_matrix_v4_phase1_same_root_coordinator_preflight",
)


PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-phase1-same-root-coordinator-contract-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_DEFAULT_ENABLED: Final = False
PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_STATUS: Final = (
    "unavailable-no-atomic-owner-or-post-commit-reconciler"
)

_CAPABILITY = object()
_PHASE = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0]
_BLOCKING_REASON: Final = "named-atomic-owner-and-restart-safe-reconciler-not-installed"


class PhysicalFullMatrixV4Phase1SameRootCoordinatorContractError(ValueError):
    """The non-operational P1 coordinator contract refused unsafe provenance."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase1SameRootCoordinatorContractError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase1SameRootCoordinatorContractConfig:
    """Default-off pin to the pending-only P1 admission boundary.

    ``atomic_owner_installed`` and ``post_commit_reconciler_installed`` must
    remain false.  Turning either flag true here is not installation and is
    rejected, so a caller cannot promote this planning seam by configuration.
    """

    same_root_admission_config: (
        _admission.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig
        | None
    ) = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_DEFAULT_ENABLED
    atomic_owner_installed: bool = False
    post_commit_reconciler_installed: bool = False
    same_root_transaction_established: bool = False
    gen2_commit_reconciled: bool = False
    phase_completion_evidenced: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True, eq=False, init=False)
class UnavailablePhysicalFullMatrixV4Phase1SameRootCoordinatorPreflight:
    """Opaque proof of exact inputs and an explicit non-operational block.

    This is deliberately not a coordinator capability.  It carries the exact
    pair identity a future implementation must bind inside one root
    transaction, while stating that the required owner and reconciliation path
    are absent.  It is never durable, serializable, or phase-success evidence.
    """

    schema: str
    status: str
    blocking_reason: str
    contract_preflight_sha256: str
    admission_sha256: str
    checkpoint_sha256: str
    checkpoint_id: str
    run_id: object
    plan_sha256: str
    phase_name: str
    phase_sequence: int
    effect_key: str
    phase_request_sha256: str
    strict_commit_id: str
    strict_runtime_commit_receipt_sha256: str
    strict_local_commit_record_id: str
    strict_local_response_id: str
    atomic_owner_required: bool = True
    atomic_owner_installed: bool = False
    post_commit_reconciliation_required: bool = True
    post_commit_reconciler_installed: bool = False
    same_root_transaction_established: bool = False
    gen2_commit_reconciled: bool = False
    phase_completion_evidenced: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        contract_preflight_sha256: str,
        admission: _admission.PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_PREFLIGHT_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("schema", PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_SCHEMA),
            ("status", PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_STATUS),
            ("blocking_reason", _BLOCKING_REASON),
            ("contract_preflight_sha256", contract_preflight_sha256),
            ("admission_sha256", admission.admission_sha256),
            ("checkpoint_sha256", admission.checkpoint_sha256),
            ("checkpoint_id", admission.checkpoint_id),
            ("run_id", admission.run_id),
            ("plan_sha256", admission.plan_sha256),
            ("phase_name", admission.phase_name),
            ("phase_sequence", admission.phase_sequence),
            ("effect_key", admission.effect_key),
            ("phase_request_sha256", admission.phase_request_sha256),
            ("strict_commit_id", admission.strict_commit_id),
            ("strict_runtime_commit_receipt_sha256", admission.strict_runtime_commit_receipt_sha256),
            ("strict_local_commit_record_id", admission.strict_local_commit_record_id),
            ("strict_local_response_id", admission.strict_local_response_id),
            ("atomic_owner_required", True),
            ("atomic_owner_installed", False),
            ("post_commit_reconciliation_required", True),
            ("post_commit_reconciler_installed", False),
            ("same_root_transaction_established", False),
            ("gen2_commit_reconciled", False),
            ("phase_completion_evidenced", False),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("full_matrix_executed", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_PREFLIGHT_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_PREFLIGHT_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_PREFLIGHT_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV4Phase1SameRootCoordinatorContractConfig
    request: _driver.PhysicalFullMatrixV4ExecutionRequest
    checkpoint: _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint
    pending: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit
    admission: _admission.PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission


_STATES: WeakKeyDictionary[UnavailablePhysicalFullMatrixV4Phase1SameRootCoordinatorPreflight, _State] = WeakKeyDictionary()


def _config(value: object) -> PhysicalFullMatrixV4Phase1SameRootCoordinatorContractConfig:
    if (
        type(value) is not PhysicalFullMatrixV4Phase1SameRootCoordinatorContractConfig
        or value.enabled is not True
        or type(value.same_root_admission_config)
        is not _admission.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig
        or value.atomic_owner_installed is not False
        or value.post_commit_reconciler_installed is not False
        or value.same_root_transaction_established is not False
        or value.gen2_commit_reconciled is not False
        or value.phase_completion_evidenced is not False
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_CONFIG_INVALID")
    return value


def _verify_inputs(
    *,
    config: object,
    request: object,
    checkpoint: object,
    pending_gen2_commit: object,
    admission: object,
) -> tuple[
    PhysicalFullMatrixV4Phase1SameRootCoordinatorContractConfig,
    _driver.PhysicalFullMatrixV4ExecutionRequest,
    _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint,
    _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    _admission.PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission,
]:
    checked_config = _config(config)
    if type(request) is not _driver.PhysicalFullMatrixV4ExecutionRequest or request.phase != _PHASE:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_REQUEST_INVALID")
    if (
        type(checkpoint) is not _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint
        or type(pending_gen2_commit)
        is not _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit
        or pending_gen2_commit.outcome != "pending_external_commit"
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_PENDING_PAIR_REQUIRED")
    try:
        verified = _admission.require_pending_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_admission(
            admission,
            config=checked_config.same_root_admission_config,
            request=request,
            checkpoint=checkpoint,
            pending_gen2_commit=pending_gen2_commit,
        )
    except _admission.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionError as exc:
        raise PhysicalFullMatrixV4Phase1SameRootCoordinatorContractError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_ADMISSION_REQUIRED"
        ) from exc
    return checked_config, request, checkpoint, pending_gen2_commit, verified


def _preflight_sha(
    admission: _admission.PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_SCHEMA,
                "status": PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_STATUS,
                "blocking_reason": _BLOCKING_REASON,
                "admission_sha256": admission.admission_sha256,
                "checkpoint_sha256": admission.checkpoint_sha256,
                "checkpoint_id": admission.checkpoint_id,
                "run_id": str(admission.run_id),
                "plan_sha256": admission.plan_sha256,
                "phase_name": admission.phase_name,
                "phase_sequence": admission.phase_sequence,
                "effect_key": admission.effect_key,
                "phase_request_sha256": admission.phase_request_sha256,
                "strict_commit_id": admission.strict_commit_id,
                "strict_runtime_commit_receipt_sha256": admission.strict_runtime_commit_receipt_sha256,
                "strict_local_commit_record_id": admission.strict_local_commit_record_id,
                "strict_local_response_id": admission.strict_local_response_id,
                "atomic_owner_required": True,
                "atomic_owner_installed": False,
                "post_commit_reconciliation_required": True,
                "post_commit_reconciler_installed": False,
                "same_root_transaction_established": False,
                "gen2_commit_reconciled": False,
                "phase_completion_evidenced": False,
            }
        )
    ).hexdigest()


def preflight_physical_full_matrix_v4_phase1_same_root_coordinator_contract(
    *,
    config: PhysicalFullMatrixV4Phase1SameRootCoordinatorContractConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    checkpoint: _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    admission: _admission.PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission,
) -> UnavailablePhysicalFullMatrixV4Phase1SameRootCoordinatorPreflight:
    """Prove the exact pending pair, then explicitly report no owner exists.

    A future implementation must use a new reviewed, root-owned SQL adapter.
    It must not mutate this result, nor treat a preflight as evidence that an
    outer commit occurred.
    """

    checked_config, checked_request, checked_checkpoint, checked_pending, checked_admission = _verify_inputs(
        config=config,
        request=request,
        checkpoint=checkpoint,
        pending_gen2_commit=pending_gen2_commit,
        admission=admission,
    )
    result = UnavailablePhysicalFullMatrixV4Phase1SameRootCoordinatorPreflight(
        contract_preflight_sha256=_preflight_sha(checked_admission),
        admission=checked_admission,
        capability=_CAPABILITY,
    )
    _STATES[result] = _State(
        config=checked_config,
        request=checked_request,
        checkpoint=checked_checkpoint,
        pending=checked_pending,
        admission=checked_admission,
    )
    return require_unavailable_physical_full_matrix_v4_phase1_same_root_coordinator_preflight(
        result,
        config=checked_config,
        request=checked_request,
        checkpoint=checked_checkpoint,
        pending_gen2_commit=checked_pending,
        admission=checked_admission,
    )


def require_unavailable_physical_full_matrix_v4_phase1_same_root_coordinator_preflight(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1SameRootCoordinatorContractConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    checkpoint: _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    admission: _admission.PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission,
) -> UnavailablePhysicalFullMatrixV4Phase1SameRootCoordinatorPreflight:
    """Revalidate only the unavailable contract; never execute or reconcile."""

    checked_config, checked_request, checked_checkpoint, checked_pending, checked_admission = _verify_inputs(
        config=config,
        request=request,
        checkpoint=checkpoint,
        pending_gen2_commit=pending_gen2_commit,
        admission=admission,
    )
    if (
        type(value) is not UnavailablePhysicalFullMatrixV4Phase1SameRootCoordinatorPreflight
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if (
        state is None
        or state.config != checked_config
        or state.request is not checked_request
        or state.checkpoint is not checked_checkpoint
        or state.pending is not checked_pending
        or state.admission is not checked_admission
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_CAPABILITY_REQUIRED")
    expected = (
        PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_SCHEMA,
        PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_STATUS,
        _BLOCKING_REASON,
        _preflight_sha(checked_admission),
        checked_admission.admission_sha256,
        checked_admission.checkpoint_sha256,
        checked_admission.checkpoint_id,
        checked_admission.run_id,
        checked_admission.plan_sha256,
        _PHASE.name,
        _PHASE.sequence,
        checked_admission.effect_key,
        checked_admission.phase_request_sha256,
        checked_admission.strict_commit_id,
        checked_admission.strict_runtime_commit_receipt_sha256,
        checked_admission.strict_local_commit_record_id,
        checked_admission.strict_local_response_id,
        True, False, True, False, False, False, False, False, False, False, False, False,
    )
    actual = (
        value.schema, value.status, value.blocking_reason, value.contract_preflight_sha256,
        value.admission_sha256, value.checkpoint_sha256, value.checkpoint_id, value.run_id,
        value.plan_sha256, value.phase_name, value.phase_sequence, value.effect_key,
        value.phase_request_sha256, value.strict_commit_id,
        value.strict_runtime_commit_receipt_sha256, value.strict_local_commit_record_id,
        value.strict_local_response_id, value.atomic_owner_required, value.atomic_owner_installed,
        value.post_commit_reconciliation_required, value.post_commit_reconciler_installed,
        value.same_root_transaction_established, value.gen2_commit_reconciled,
        value.phase_completion_evidenced, value.writer_authorized, value.promotion_authorized,
        value.execution_authorized, value.full_matrix_authorized, value.full_matrix_executed,
    )
    if actual != expected:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_SAME_ROOT_COORDINATOR_CONTRACT_TAMPERED")
    return value
