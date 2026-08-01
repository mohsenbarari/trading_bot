"""Durable-only admission bridge for a four-role live-IAM aggregate.

This is intentionally the only bridge in this layer that accepts raw aggregate
bytes.  It routes those bytes through the root-owned Witness ledger runtime,
which verifies the aggregate against the latest durable nonce state, before it
asks the pure preflight gate to mint a readiness bridge.  There is no function
that accepts an in-memory verified aggregate as admission input.

The returned receipt is opaque and nonserializable.  It carries the already
verified preflight gate together with the immutable durable ledger-head receipt
that authenticated the aggregate.  It is not an execution/promotion permit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re

from core import physical_arvan_s3_four_role_live_iam_evidence as _live_iam
from core import physical_arvan_s3_four_role_live_iam_preflight_gate as _gate
from core import physical_arvan_s3_four_role_live_iam_witness_ledger_runtime as _runtime
from core import physical_ir_to_fi_object_storage_failback_preflight as _failback


__all__ = (
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_SCHEMA",
    "PhysicalArvanS3FourRoleLiveIamDurableAdmissionError",
    "VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission",
    "admit_physical_arvan_s3_four_role_live_iam_durable_aggregate",
    "require_verified_physical_arvan_s3_four_role_live_iam_durable_admission",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-live-iam-durable-admission-v1"
)

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_CAPABILITY = object()


class PhysicalArvanS3FourRoleLiveIamDurableAdmissionError(ValueError):
    """A raw aggregate did not pass the required durable admission route."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
    """Opaque admission receipt containing a live gate and durable head proof."""

    schema: str
    gate: _gate.VerifiedPhysicalArvanS3FourRoleLiveIamPreflightGate
    durable_ledger_state: _runtime.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState
    aggregate_sha256: str
    durable_ledger_head_sha256: str
    durable_ledger_sequence: int
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleLiveIamDurableAdmissionError(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _durable_state(
    value: object,
    *,
    binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
) -> _runtime.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState:
    if (
        type(value) is not _runtime.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState
        or value.schema != _runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RUNTIME_SCHEMA
        or value.evidence_binding_sha256 != binding.evidence_binding_sha256
        or type(value.sequence) is not int
        or value.sequence < 1
        or type(value.logical_record_count) is not int
        or value.logical_record_count < 1
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_LEDGER_STATE_INVALID")
    _sha256(value.head_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_LEDGER_STATE_INVALID")
    _sha256(value.ledger_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_LEDGER_STATE_INVALID")
    return value


def _require_admission(
    value: object,
    *,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
    if (
        type(value) is not VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission
        or value._capability is not _CAPABILITY
        or value.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_SCHEMA
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_NOT_VERIFIED")
    try:
        verified_gate = _gate.require_verified_physical_arvan_s3_four_role_live_iam_preflight_gate(
            value.gate,
            live_iam_binding=live_iam_binding,
            failback_binding=failback_binding,
            observed_at=observed_at,
        )
    except _gate.PhysicalArvanS3FourRoleLiveIamPreflightGateError as exc:
        _fail(f"ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_GATE_{exc.code}")
    state = _durable_state(value.durable_ledger_state, binding=live_iam_binding)
    if (
        _sha256(value.aggregate_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_FACTS_INVALID")
        != verified_gate.aggregate_sha256
        or _sha256(value.durable_ledger_head_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_FACTS_INVALID")
        != state.head_sha256
        or type(value.durable_ledger_sequence) is not int
        or value.durable_ledger_sequence != state.sequence
        or _utc(value.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_FACTS_INVALID")
        != verified_gate.expires_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_FACTS_INVALID")
    return value


def admit_physical_arvan_s3_four_role_live_iam_durable_aggregate(
    *,
    runtime: _runtime.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime,
    aggregate: bytes,
    witness_public_key: bytes,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
    """Durably verify raw aggregate bytes, then mint the exact preflight gate.

    ``runtime`` is mandatory and is the only component that reads the durable
    nonce ledger.  Calling a pure aggregate verifier directly is deliberately
    not an alternative admission route in this module.
    """

    try:
        state, verified_aggregate = _runtime.verify_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate(
            runtime=runtime,
            aggregate=aggregate,
            witness_public_key=witness_public_key,
            observed_at=observed_at,
        )
    except _runtime.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError as exc:
        _fail(f"ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_LEDGER_{exc.code}")
    try:
        minted_gate = _gate.mint_physical_arvan_s3_four_role_live_iam_preflight_gate(
            aggregate=verified_aggregate,
            live_iam_binding=live_iam_binding,
            failback_binding=failback_binding,
            observed_at=observed_at,
        )
    except _gate.PhysicalArvanS3FourRoleLiveIamPreflightGateError as exc:
        _fail(f"ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_GATE_{exc.code}")
    checked_state = _durable_state(state, binding=live_iam_binding)
    if (
        minted_gate.aggregate_sha256 != verified_aggregate.raw_sha256
        or minted_gate.expires_at != verified_aggregate.expires_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_FACTS_INVALID")
    result = VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_DURABLE_ADMISSION_SCHEMA,
        gate=minted_gate,
        durable_ledger_state=checked_state,
        aggregate_sha256=verified_aggregate.raw_sha256,
        durable_ledger_head_sha256=checked_state.head_sha256,
        durable_ledger_sequence=checked_state.sequence,
        expires_at=verified_aggregate.expires_at,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return _require_admission(
        result,
        live_iam_binding=live_iam_binding,
        failback_binding=failback_binding,
        observed_at=observed_at,
    )


def require_verified_physical_arvan_s3_four_role_live_iam_durable_admission(
    value: object,
    *,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
    """Revalidate an opaque durable admission receipt for a readiness consumer."""

    return _require_admission(
        value,
        live_iam_binding=live_iam_binding,
        failback_binding=failback_binding,
        observed_at=observed_at,
    )
