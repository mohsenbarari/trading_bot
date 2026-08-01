"""Fail-closed admission for the future P1 same-root transaction coordinator.

The Phase-1 checkpoint grammar can prove an *in-memory pending* Gen2 row and
the exact post-effect V4 correlation.  It cannot prove that the checkpoint row
and that Gen2 row were committed by one PostgreSQL root transaction, nor can it
reconcile an unknown commit outcome.  This module deliberately preserves that
boundary.

It supplies a narrow, process-local handoff for the future named coordinator:
the handoff revalidates the exact prepared checkpoint and its exact pending
Gen2 capability, projects an immutable correlation digest, and states that
same-root persistence and post-commit reconciliation remain required.  There
is no session, SQL, engine, callback, transport, storage, phase adapter, or
success result here.  In particular, an admission is not evidence that a
transaction exists or committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Final
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint as _checkpoint
from core import (
    physical_wal_v2_witness_roundtrip_strict_writer_bound_sqlalchemy_transaction
    as _gen2_transaction,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_STATUS",
    "PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionError",
    "admit_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_transaction",
    "require_pending_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_admission",
)


PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-phase1-post-effect-strict-ack-same-root-admission-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_DEFAULT_ENABLED: Final = False
PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_STATUS: Final = (
    "pending-external-same-root-transaction-and-reconciliation"
)

_CAPABILITY = object()
_PHASE = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0]


class PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionError(ValueError):
    """The pending-only P1 coordinator handoff refused unsafe input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig:
    """Default-off policy pinning only the existing pending-checkpoint owner."""

    checkpoint_config: _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_DEFAULT_ENABLED
    checkpoint_durable: bool = False
    same_root_transaction_established: bool = False
    gen2_commit_reconciled: bool = False
    phase_completion_evidenced: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True, eq=False, init=False)
class PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission:
    """Opaque, non-durable P1 handoff for a future same-root coordinator.

    Its public fields intentionally say that no same-root transaction has been
    established and no unknown outcome has been reconciled.  It cannot be
    serialized, copied, converted into a Phase-1 result, or reused after a
    restart.  A future SQL implementation must introduce a separate committed
    evidence verifier; it must not toggle these fields.
    """

    schema: str
    status: str
    admission_sha256: str
    checkpoint_sha256: str
    checkpoint_id: str
    run_id: object
    plan_sha256: str
    phase_name: str
    phase_sequence: int
    effect_key: str
    phase_request_sha256: str
    claim_id: str
    journaled_effect_start_identity_sha256: str
    strict_commit_id: str
    strict_runtime_commit_receipt_sha256: str
    strict_local_commit_record_id: str
    strict_local_response_id: str
    checkpoint_durable: bool = False
    same_root_transaction_established: bool = False
    gen2_commit_reconciled: bool = False
    reconciliation_required: bool = True
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
        admission_sha256: str,
        checkpoint: _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint,
        request: _driver.PhysicalFullMatrixV4ExecutionRequest,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("schema", PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_SCHEMA),
            ("status", PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_STATUS),
            ("admission_sha256", admission_sha256),
            ("checkpoint_sha256", checkpoint.checkpoint_sha256),
            ("checkpoint_id", checkpoint.checkpoint_id),
            ("run_id", request.run_id),
            ("plan_sha256", request.plan_sha256),
            ("phase_name", request.phase.name),
            ("phase_sequence", request.phase.sequence),
            ("effect_key", request.effect_key),
            ("phase_request_sha256", request.phase_request_sha256),
            ("claim_id", checkpoint.claim_id),
            ("journaled_effect_start_identity_sha256", checkpoint.journaled_effect_start_identity_sha256),
            ("strict_commit_id", checkpoint.strict_commit_id),
            ("strict_runtime_commit_receipt_sha256", checkpoint.strict_runtime_commit_receipt_sha256),
            ("strict_local_commit_record_id", checkpoint.strict_local_commit_record_id),
            ("strict_local_response_id", checkpoint.strict_local_response_id),
            ("checkpoint_durable", False),
            ("same_root_transaction_established", False),
            ("gen2_commit_reconciled", False),
            ("reconciliation_required", True),
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
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig
    request: _driver.PhysicalFullMatrixV4ExecutionRequest
    checkpoint: _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint
    pending: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit


_STATES: WeakKeyDictionary[PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission, _State] = WeakKeyDictionary()


def _config(value: object) -> PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig:
    if (
        type(value) is not PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig
        or value.enabled is not True
        or type(value.checkpoint_config)
        is not _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig
        or value.checkpoint_config.enabled is not True
        or value.checkpoint_durable is not False
        or value.same_root_transaction_established is not False
        or value.gen2_commit_reconciled is not False
        or value.phase_completion_evidenced is not False
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_CONFIG_INVALID")
    return value


def _verify_inputs(
    *,
    config: object,
    request: object,
    checkpoint: object,
    pending_gen2_commit: object,
) -> tuple[
    PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig,
    _driver.PhysicalFullMatrixV4ExecutionRequest,
    _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint,
    _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
]:
    checked_config = _config(config)
    if type(request) is not _driver.PhysicalFullMatrixV4ExecutionRequest or request.phase != _PHASE:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_REQUEST_INVALID")
    try:
        verified_checkpoint = _checkpoint.require_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint(
            checkpoint,
            config=checked_config.checkpoint_config,
            request=request,
            pending_gen2_commit=pending_gen2_commit,
        )
    except _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_CHECKPOINT_REQUIRED"
        ) from exc
    if (
        type(pending_gen2_commit)
        is not _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit
        or pending_gen2_commit.outcome != "pending_external_commit"
        or verified_checkpoint.phase_sequence != _PHASE.sequence
        or verified_checkpoint.run_id != request.run_id
        or verified_checkpoint.plan_sha256 != request.plan_sha256
        or verified_checkpoint.effect_key != request.effect_key
        or verified_checkpoint.phase_request_sha256 != request.phase_request_sha256
        or verified_checkpoint.checkpoint_durable is not False
        or verified_checkpoint.phase_completion_evidenced is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_CORRELATION_MISMATCH")
    return checked_config, request, verified_checkpoint, pending_gen2_commit


def _admission_sha(
    *,
    checkpoint: _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_SCHEMA,
                "status": PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_STATUS,
                "checkpoint_sha256": checkpoint.checkpoint_sha256,
                "checkpoint_id": checkpoint.checkpoint_id,
                "run_id": str(request.run_id),
                "plan_sha256": request.plan_sha256,
                "phase_name": request.phase.name,
                "phase_sequence": request.phase.sequence,
                "effect_key": request.effect_key,
                "phase_request_sha256": request.phase_request_sha256,
                "claim_id": checkpoint.claim_id,
                "journaled_effect_start_identity_sha256": checkpoint.journaled_effect_start_identity_sha256,
                "strict_commit_id": checkpoint.strict_commit_id,
                "strict_runtime_commit_receipt_sha256": checkpoint.strict_runtime_commit_receipt_sha256,
                "strict_local_commit_record_id": checkpoint.strict_local_commit_record_id,
                "strict_local_response_id": checkpoint.strict_local_response_id,
                "checkpoint_durable": False,
                "same_root_transaction_established": False,
                "gen2_commit_reconciled": False,
                "reconciliation_required": True,
                "phase_completion_evidenced": False,
            }
        )
    ).hexdigest()


def admit_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_transaction(
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    checkpoint: _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
) -> PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission:
    """Refuse the retired evidence-only admission as a live P1 path.

    A raw checkpoint and raw Gen2 pending handoff do not carry a verifiable
    ``AsyncSession``/root-transaction identity.  Minting an opaque admission
    from that pair would make a local in-memory association look like a
    same-root fact.  Until a reviewed DB causal fence exists, this legacy
    evidence-only module is deliberately non-candidate and cannot release an
    admission for any caller.
    """

    del config, request, checkpoint, pending_gen2_commit
    _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_LIVE_ROOT_ENVELOPE_REQUIRED")


def require_pending_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_admission(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    checkpoint: _checkpoint.PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
) -> PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission:
    """Revalidate the opaque pending-only handoff; never reconcile or commit."""

    checked_config, checked_request, verified_checkpoint, checked_pending = _verify_inputs(
        config=config,
        request=request,
        checkpoint=checkpoint,
        pending_gen2_commit=pending_gen2_commit,
    )
    if (
        type(value) is not PendingPhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmission
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if (
        state is None
        or state.config != checked_config
        or state.request is not checked_request
        or state.checkpoint is not verified_checkpoint
        or state.pending is not checked_pending
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_CAPABILITY_REQUIRED")
    expected = (
        PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_SCHEMA,
        PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_STATUS,
        _admission_sha(checkpoint=verified_checkpoint, request=checked_request),
        verified_checkpoint.checkpoint_sha256,
        verified_checkpoint.checkpoint_id,
        checked_request.run_id,
        checked_request.plan_sha256,
        _PHASE.name,
        _PHASE.sequence,
        checked_request.effect_key,
        checked_request.phase_request_sha256,
        verified_checkpoint.claim_id,
        verified_checkpoint.journaled_effect_start_identity_sha256,
        verified_checkpoint.strict_commit_id,
        verified_checkpoint.strict_runtime_commit_receipt_sha256,
        verified_checkpoint.strict_local_commit_record_id,
        verified_checkpoint.strict_local_response_id,
        False, False, False, True, False, False, False, False, False, False,
    )
    actual = (
        value.schema, value.status, value.admission_sha256,
        value.checkpoint_sha256, value.checkpoint_id, value.run_id,
        value.plan_sha256, value.phase_name, value.phase_sequence,
        value.effect_key, value.phase_request_sha256, value.claim_id,
        value.journaled_effect_start_identity_sha256, value.strict_commit_id,
        value.strict_runtime_commit_receipt_sha256, value.strict_local_commit_record_id,
        value.strict_local_response_id, value.checkpoint_durable,
        value.same_root_transaction_established, value.gen2_commit_reconciled,
        value.reconciliation_required, value.phase_completion_evidenced,
        value.writer_authorized, value.promotion_authorized, value.execution_authorized,
        value.full_matrix_authorized, value.full_matrix_executed,
    )
    if actual != expected:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ADMISSION_TAMPERED")
    return value
