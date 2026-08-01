"""Fail-closed, evidence-only grammar for V4 phase-4/phase-7 successors.

The V4 execution driver deliberately requires a fresh successor binding at
phase 4 (FI -> IR) and phase 7 (IR -> FI), but it does not implement the
root-owned promotion/restore operation which would create that binding.  This
module is the narrow portable evidence contract for such a future owner.  It
does not promote PostgreSQL, start a writer, change traffic, query Witness,
open a socket, invoke a process, or make a remote call.

Three independently signed canonical records are required: target executor,
independent observer, and a Witness durable anti-replay admission.  They
cross-pin the exact V4 journaled effect-start correlation and immutable
Witness anchor, predecessor binding, strictly newer successor binding, and a
fresh signed *readiness evidence projection*.  The projection is explicitly
not a reconstructed Gen2 capability or a readiness permit; a future V4
resolver must independently obtain its own current owner capability.

This is deliberately *not* a cross-phase continuation proof.  A V4 phase has
an effect-start record and, only after the root-owned effect is finished, a
separate completion record/anchor.  This grammar is bound to the former and
therefore cannot claim that the phase completed, that the next V4 phase
started, or that a future phase may use its start anchor as a predecessor.
The future cross-phase bridge must instead carry a typed predecessor
completion receipt and completion anchor, then prove that the next
effect-start anchor has that completion anchor as its immediate previous
head.  Start-to-start adjacency is intentionally not representable here.

Legacy V1 operational-failover/self-hashed lease records and the old generic
promotion Protocol are intentionally not imported, adapted, or accepted.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from uuid import UUID
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    canonical_json_bytes,
)
from core.physical_full_matrix_execution_driver_v4 import (
    PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA,
    PHYSICAL_FULL_MATRIX_V4_PHASES,
    PhysicalFullMatrixV4EffectStart,
    PhysicalFullMatrixV4ExecutionBinding,
    PhysicalFullMatrixV4ExecutionDriverError,
    PhysicalFullMatrixV4ExecutionPhase,
    derive_physical_full_matrix_v4_effect_start_identity_sha256,
)
from core.physical_full_matrix_v2_gen2_witnessed_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS,
)


__all__ = (
    "DEFAULT_V4_WITNESS_SUCCESSOR_TRANSITION_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_READINESS_EVIDENCE_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_READINESS_EVIDENCE_STATUS",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_ANTI_REPLAY_NAMESPACE",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_EXECUTOR_RECEIPT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_OBSERVER_RECEIPT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_STATUS",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_WITNESS_ADMISSION_SCHEMA",
    "PhysicalFullMatrixV4SuccessorTransitionAnchorPin",
    "PhysicalFullMatrixV4SuccessorTransitionEffectStartPin",
    "PhysicalFullMatrixV4SuccessorTransitionEvidencePins",
    "PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin",
    "PhysicalFullMatrixV4SuccessorTransitionReplayPolicy",
    "PhysicalFullMatrixV4SuccessorTransitionVerificationConfig",
    "PhysicalFullMatrixV4WitnessSuccessorTransitionError",
    "VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition",
    "derive_physical_full_matrix_v4_witness_successor_transition_replay_key_sha256",
    "require_verified_physical_full_matrix_v4_witness_successor_transition",
    "verify_physical_full_matrix_v4_witness_successor_transition",
)


PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-successor-transition-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_EXECUTOR_RECEIPT_SCHEMA = (
    PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_SCHEMA
    + "/target-root-executor-receipt-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_OBSERVER_RECEIPT_SCHEMA = (
    PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_SCHEMA
    + "/independent-observer-receipt-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_WITNESS_ADMISSION_SCHEMA = (
    PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_SCHEMA
    + "/witness-durable-anti-replay-admission-v1"
)
PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_READINESS_EVIDENCE_SCHEMA = (
    PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_SCHEMA
    + "/successor-readiness-evidence-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_STATUS = (
    "successor-transition-evidence-only"
)
PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_READINESS_EVIDENCE_STATUS = (
    "fresh-successor-readiness-evidence-only"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_ANTI_REPLAY_NAMESPACE = (
    "physical-full-matrix-v4-witness-successor-transition"
)

DEFAULT_V4_WITNESS_SUCCESSOR_TRANSITION_MAX_EVIDENCE_AGE_SECONDS = 90
_MAX_EVIDENCE_AGE_SECONDS = 300
_MAX_TRANSITION_WINDOW_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_MAX_WIRE_BYTES = 160 * 1024
_ZERO_SHA256 = "0" * 64
_VERSION = 1
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)

_PHASES = {
    phase.name: phase
    for phase in PHYSICAL_FULL_MATRIX_V4_PHASES
    if phase.name
    in {"witness-promote-ir-v2", "witness-restore-fi-writer-v2"}
}
_TRANSITION_DIRECTIONS = {
    "witness-promote-ir-v2": (("webapp_fi", "webapp_ir"), ("webapp_ir", "webapp_fi")),
    "witness-restore-fi-writer-v2": (("webapp_ir", "webapp_fi"), ("webapp_fi", "webapp_ir")),
}
_EXECUTOR_DOMAIN = (
    PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_EXECUTOR_RECEIPT_SCHEMA
    + "\x00"
).encode("ascii")
_OBSERVER_DOMAIN = (
    PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_OBSERVER_RECEIPT_SCHEMA
    + "\x00"
).encode("ascii")
_WITNESS_DOMAIN = (
    PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_WITNESS_ADMISSION_SCHEMA
    + "\x00"
).encode("ascii")
_EXECUTOR_KIND = "target-root-successor-transition-executor-evidence"
_OBSERVER_KIND = "independent-successor-transition-observer-evidence"
_WITNESS_KIND = "witness-durable-successor-transition-admission"
_EXECUTOR_ROLE = "target-root-successor-transition-executor"
_OBSERVER_ROLE = "independent-successor-transition-observer"
_WITNESS_ROLE = "witness-durable-successor-transition-ledger"
_ANTI_REPLAY_MODE = "witness-durable-single-use-successor-transition-v1"

_TRANSITION_BINDING_FIELDS = frozenset(
    {
        "schema",
        "version",
        "status",
        "transition_id",
        "transition_nonce",
        "requested_at",
        "expires_at",
        "effect_start",
        "effect_start_anchor",
        "predecessor_binding",
        "successor_binding",
        "successor_readiness",
        "evidence_pins",
    }
)
_SIGNED_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "signer_role",
        "transition_binding",
        "transition_binding_sha256",
        "signature_base64",
    }
)
_WITNESS_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "signer_role",
        "transition_id",
        "transition_nonce",
        "transition_binding_sha256",
        "replay_key_sha256",
        "predecessor_writer_epoch",
        "predecessor_writer_lease_id",
        "successor_writer_holder_site",
        "successor_writer_epoch",
        "successor_writer_lease_id",
        "successor_witness_transition_id",
        "successor_witnessed_term_proof_sha256",
        "successor_readiness_binding_sha256",
        "anti_replay_namespace",
        "anti_replay_mode",
        "witness_ledger_scope_sha256",
        "admission_id",
        "admission_nonce",
        "committed_at",
        "expires_at",
        "witness_ledger_sequence",
        "witness_ledger_entry_sha256",
        "witness_ledger_previous_head_sha256",
        "signature_base64",
    }
)
_EFFECT_START_FIELDS = frozenset(
    {
        "run_id",
        "plan_sha256",
        "phase",
        "effect_key",
        "phase_request_sha256",
        "binding",
        "claim_id",
        "journaled_effect_start_identity_sha256",
    }
)
_ANCHOR_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "plan_sha256",
        "phase",
        "effect_key",
        "phase_request_sha256",
        "binding",
        "claim_id",
        "journaled_effect_start_identity_sha256",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "anchor_previous_sequence",
        "anchor_previous_head_sha256",
        "anchor_sequence",
        "anchor_head_sha256",
        "anchor_commitment_sha256",
        "anchor_attestation_sha256",
        "anchor_local_previous_record_sha256",
        "anchor_local_event_sha256",
        "anchor_occurred_at",
    }
)
_PHASE_FIELDS = frozenset(
    {"sequence", "name", "oracle", "destructive", "transport_profile"}
)
_BINDING_FIELDS = frozenset(
    {
        "campaign_id",
        "release_sha",
        "readiness_binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "source_site",
        "destination_site",
        "roundtrip_attestation_sha256",
        "roundtrip_configuration_sha256",
        "witness_transition_id",
        "witness_sequence",
    }
)
_READINESS_FIELDS = frozenset(
    {
        "schema",
        "status",
        "gen2_readiness_schema",
        "gen2_readiness_status",
        "campaign_id",
        "release_sha",
        "successor_readiness_binding_sha256",
        "gen2_observed_slots",
        "gen2_reason_codes",
        "readiness_evidence_sha256",
        "observed_at",
        "expires_at",
    }
)
_EVIDENCE_PIN_FIELDS = frozenset(
    {
        "executor_installation_attestation_sha256",
        "executor_scope_policy_sha256",
        "executor_transition_evidence_sha256",
        "observer_installation_attestation_sha256",
        "observer_scope_policy_sha256",
        "observer_transition_evidence_sha256",
        "successor_installation_attestation_sha256",
    }
)
_CAPABILITY = object()


class PhysicalFullMatrixV4WitnessSuccessorTransitionError(ValueError):
    """One typed refusal from the V4 phase-4/phase-7 evidence boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4WitnessSuccessorTransitionError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4SuccessorTransitionEffectStartPin:
    """Portable projection of one phase-4/phase-7 journaled start only."""

    run_id: UUID
    plan_sha256: str
    phase: PhysicalFullMatrixV4ExecutionPhase
    effect_key: str
    phase_request_sha256: str
    binding: PhysicalFullMatrixV4ExecutionBinding
    claim_id: str
    journaled_effect_start_identity_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4SuccessorTransitionAnchorPin:
    """Exact public immutable Witness-anchor projection for that start."""

    schema: str
    run_id: UUID
    plan_sha256: str
    phase: PhysicalFullMatrixV4ExecutionPhase
    effect_key: str
    phase_request_sha256: str
    binding: PhysicalFullMatrixV4ExecutionBinding
    claim_id: str
    journaled_effect_start_identity_sha256: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    anchor_previous_sequence: int
    anchor_previous_head_sha256: str
    anchor_sequence: int
    anchor_head_sha256: str
    anchor_commitment_sha256: str
    anchor_attestation_sha256: str
    anchor_local_previous_record_sha256: str
    anchor_local_event_sha256: str
    anchor_occurred_at: datetime


@dataclass(frozen=True)
class PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin:
    """Signed successor-readiness projection, never a readiness capability."""

    schema: str
    status: str
    gen2_readiness_schema: str
    gen2_readiness_status: str
    campaign_id: str
    release_sha: str
    successor_readiness_binding_sha256: str
    gen2_observed_slots: tuple[str, ...]
    gen2_reason_codes: tuple[str, ...]
    readiness_evidence_sha256: str
    observed_at: datetime
    expires_at: datetime
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False


@dataclass(frozen=True)
class PhysicalFullMatrixV4SuccessorTransitionEvidencePins:
    """Hashes of separate target executor/observer installation evidence."""

    executor_installation_attestation_sha256: str
    executor_scope_policy_sha256: str
    executor_transition_evidence_sha256: str
    observer_installation_attestation_sha256: str
    observer_scope_policy_sha256: str
    observer_transition_evidence_sha256: str
    successor_installation_attestation_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4SuccessorTransitionReplayPolicy:
    """Pinned identity of the external durable Witness anti-replay ledger."""

    anti_replay_namespace: str
    witness_ledger_scope_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4SuccessorTransitionVerificationConfig:
    """Default-off policy for one exact phase-4 or phase-7 successor."""

    expected_effect_start: PhysicalFullMatrixV4SuccessorTransitionEffectStartPin | None = None
    expected_effect_start_anchor: PhysicalFullMatrixV4SuccessorTransitionAnchorPin | None = None
    expected_predecessor_binding: PhysicalFullMatrixV4ExecutionBinding | None = None
    expected_successor_binding: PhysicalFullMatrixV4ExecutionBinding | None = None
    expected_successor_readiness: (
        PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin | None
    ) = None
    expected_evidence_pins: PhysicalFullMatrixV4SuccessorTransitionEvidencePins | None = None
    expected_replay_policy: PhysicalFullMatrixV4SuccessorTransitionReplayPolicy | None = None
    executor_signer_public_key: bytes = b""
    observer_signer_public_key: bytes = b""
    witness_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_V4_WITNESS_SUCCESSOR_TRANSITION_MAX_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition:
    """Opaque signed successor evidence, never an operation or phase completion.

    ``phase_completion_evidenced`` and ``next_phase_start_authorized`` are
    permanent false sentinels rather than derived facts.  They make the
    start-only boundary explicit to a future integration: it must obtain a
    separately typed completion-anchor bridge before admitting a following
    phase.
    """

    canonical_executor_receipt: bytes
    canonical_observer_receipt: bytes
    canonical_witness_admission_receipt: bytes
    executor_receipt_sha256: str
    observer_receipt_sha256: str
    witness_admission_receipt_sha256: str
    effect_start: PhysicalFullMatrixV4SuccessorTransitionEffectStartPin
    effect_start_anchor: PhysicalFullMatrixV4SuccessorTransitionAnchorPin
    predecessor_binding: PhysicalFullMatrixV4ExecutionBinding
    successor_binding: PhysicalFullMatrixV4ExecutionBinding
    successor_readiness: PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin
    evidence_pins: PhysicalFullMatrixV4SuccessorTransitionEvidencePins
    replay_policy: PhysicalFullMatrixV4SuccessorTransitionReplayPolicy
    transition_id: str
    transition_nonce: str
    replay_key_sha256: str
    requested_at: datetime
    expires_at: datetime
    admission_id: str
    admission_nonce: str
    committed_at: datetime
    witness_ledger_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    phase_completion_evidenced: bool = False
    next_phase_start_authorized: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(self, *, capability: object, **values: object) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_CONSTRUCTION_FORBIDDEN")
        expected = (
            "canonical_executor_receipt",
            "canonical_observer_receipt",
            "canonical_witness_admission_receipt",
            "executor_receipt_sha256",
            "observer_receipt_sha256",
            "witness_admission_receipt_sha256",
            "effect_start",
            "effect_start_anchor",
            "predecessor_binding",
            "successor_binding",
            "successor_readiness",
            "evidence_pins",
            "replay_policy",
            "transition_id",
            "transition_nonce",
            "replay_key_sha256",
            "requested_at",
            "expires_at",
            "admission_id",
            "admission_nonce",
            "committed_at",
            "witness_ledger_sequence",
            "witness_ledger_entry_sha256",
            "witness_ledger_previous_head_sha256",
        )
        if set(values) != set(expected):
            raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_CONSTRUCTION_FORBIDDEN")
        for name in expected:
            object.__setattr__(self, name, values[name])
        for name in (
            "writer_authorized",
            "promotion_authorized",
            "traffic_switch_authorized",
            "execution_authorized",
            "full_matrix_authorized",
            "phase_completion_evidenced",
            "next_phase_start_authorized",
        ):
            object.__setattr__(self, name, False)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _ConfigFacts:
    effect_start: PhysicalFullMatrixV4SuccessorTransitionEffectStartPin
    anchor: PhysicalFullMatrixV4SuccessorTransitionAnchorPin
    predecessor: PhysicalFullMatrixV4ExecutionBinding
    successor: PhysicalFullMatrixV4ExecutionBinding
    readiness: PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin
    evidence_pins: PhysicalFullMatrixV4SuccessorTransitionEvidencePins
    replay_policy: PhysicalFullMatrixV4SuccessorTransitionReplayPolicy
    executor_key: bytes
    observer_key: bytes
    witness_key: bytes
    maximum_age_seconds: int


@dataclass(frozen=True)
class _TransitionFacts:
    binding_mapping: dict[str, Any]
    binding_sha256: str
    effect_start: PhysicalFullMatrixV4SuccessorTransitionEffectStartPin
    anchor: PhysicalFullMatrixV4SuccessorTransitionAnchorPin
    predecessor: PhysicalFullMatrixV4ExecutionBinding
    successor: PhysicalFullMatrixV4ExecutionBinding
    readiness: PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin
    evidence_pins: PhysicalFullMatrixV4SuccessorTransitionEvidencePins
    transition_id: str
    transition_nonce: str
    requested_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class _WitnessFacts:
    replay_key_sha256: str
    admission_id: str
    admission_nonce: str
    committed_at: datetime
    witness_ledger_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str


@dataclass(frozen=True)
class _VerifiedState:
    executor: bytes
    observer: bytes
    witness: bytes


_STATES: WeakKeyDictionary[
    VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition, _VerifiedState
] = WeakKeyDictionary()


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4WitnessSuccessorTransitionError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("V4_WITNESS_SUCCESSOR_TRANSITION_WIRE_INVALID")
        result[key] = value
    return result


def _parse(raw: object, *, fields: frozenset[str], code: str) -> tuple[dict[str, Any], bytes]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_WIRE_BYTES:
        _fail(code)
    try:
        mapping = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, PhysicalFullMatrixV4WitnessSuccessorTransitionError):
        _fail(code)
    if type(mapping) is not dict or set(mapping) != fields or _canonical(mapping, code=code) != raw:
        _fail(code)
    return mapping, raw


def _exact(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _sha(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime) -> str:
    return _utc(value, code="V4_WITNESS_SUCCESSOR_TRANSITION_TIME_INVALID").isoformat().replace(
        "+00:00", "Z"
    )


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    normalized = _utc(parsed, code=code)
    if _render_timestamp(normalized) != value:
        _fail(code)
    return normalized


def _positive(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _verify_signature(
    *, signer: bytes, domain: bytes, unsigned: Mapping[str, Any], signature_base64: object, code: str
) -> None:
    if type(signature_base64) is not str:
        _fail(code)
    try:
        signature = base64.b64decode(signature_base64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(signature) != 64:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(signer).verify(
            signature, domain + _canonical(dict(unsigned), code=code)
        )
    except (InvalidSignature, ValueError, PhysicalFullMatrixV4WitnessSuccessorTransitionError):
        _fail(code)


def _phase_mapping(value: object, *, code: str) -> tuple[PhysicalFullMatrixV4ExecutionPhase, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4ExecutionPhase:
        _fail(code)
    expected = _PHASES.get(value.name)
    if (
        expected is None
        or value.sequence != expected.sequence
        or value.oracle != expected.oracle
        or value.destructive is not True
        or value.transport_profile != expected.transport_profile
    ):
        _fail(code)
    return value, {
        "sequence": value.sequence,
        "name": value.name,
        "oracle": value.oracle,
        "destructive": True,
        "transport_profile": value.transport_profile,
    }


def _binding_mapping(
    value: object, *, direction: tuple[str, str] | None, code: str
) -> tuple[PhysicalFullMatrixV4ExecutionBinding, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4ExecutionBinding:
        _fail(code)
    binding = value
    pair = (binding.source_site, binding.destination_site)
    if (
        type(binding.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(binding.campaign_id) is None
        or type(binding.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(binding.release_sha) is None
        or pair not in {("webapp_fi", "webapp_ir"), ("webapp_ir", "webapp_fi")}
        or (direction is not None and pair != direction)
        or binding.writer_holder_site != binding.source_site
        or type(binding.writer_epoch) is not int
        or binding.writer_epoch < 1
        or LEASE_ID_RE.fullmatch(binding.writer_lease_id) is None
        or _IDENTIFIER_RE.fullmatch(binding.witness_transition_id) is None
        or type(binding.witness_sequence) is not int
        or binding.witness_sequence < 1
    ):
        _fail(code)
    for item in (
        binding.readiness_binding_sha256,
        binding.route_commitment_sha256,
        binding.four_role_binding_sha256,
        binding.witnessed_term_proof_sha256,
        binding.roundtrip_attestation_sha256,
        binding.roundtrip_configuration_sha256,
    ):
        _sha(item, code=code)
    return binding, {
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "readiness_binding_sha256": binding.readiness_binding_sha256,
        "route_commitment_sha256": binding.route_commitment_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "writer_holder_site": binding.writer_holder_site,
        "writer_epoch": binding.writer_epoch,
        "writer_lease_id": binding.writer_lease_id,
        "witnessed_term_proof_sha256": binding.witnessed_term_proof_sha256,
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "roundtrip_attestation_sha256": binding.roundtrip_attestation_sha256,
        "roundtrip_configuration_sha256": binding.roundtrip_configuration_sha256,
        "witness_transition_id": binding.witness_transition_id,
        "witness_sequence": binding.witness_sequence,
    }


def _binding_from_mapping(
    value: object, *, direction: tuple[str, str] | None, code: str
) -> PhysicalFullMatrixV4ExecutionBinding:
    mapping = _exact(value, fields=_BINDING_FIELDS, code=code)
    try:
        binding = PhysicalFullMatrixV4ExecutionBinding(**mapping)
    except TypeError:
        _fail(code)
    checked, canonical = _binding_mapping(binding, direction=direction, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _phase_from_mapping(value: object, *, code: str) -> PhysicalFullMatrixV4ExecutionPhase:
    mapping = _exact(value, fields=_PHASE_FIELDS, code=code)
    try:
        phase = PhysicalFullMatrixV4ExecutionPhase(**mapping)
    except TypeError:
        _fail(code)
    checked, canonical = _phase_mapping(phase, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _effect_start_mapping(
    value: object, *, code: str
) -> tuple[PhysicalFullMatrixV4SuccessorTransitionEffectStartPin, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4SuccessorTransitionEffectStartPin:
        _fail(code)
    pin = value
    if type(pin.run_id) is not UUID or pin.run_id.int == 0:
        _fail(code)
    _sha(pin.plan_sha256, code=code)
    phase, phase_mapping = _phase_mapping(pin.phase, code=code)
    predecessor_direction, _successor_direction = _TRANSITION_DIRECTIONS[phase.name]
    binding, binding_mapping = _binding_mapping(pin.binding, direction=predecessor_direction, code=code)
    _sha(pin.effect_key, code=code)
    _sha(pin.phase_request_sha256, code=code)
    _identifier(pin.claim_id, code=code)
    identity = _sha(pin.journaled_effect_start_identity_sha256, code=code)
    try:
        derived = derive_physical_full_matrix_v4_effect_start_identity_sha256(
            PhysicalFullMatrixV4EffectStart(
                run_id=pin.run_id,
                plan_sha256=pin.plan_sha256,
                sequence=phase.sequence,
                phase_request_sha256=pin.phase_request_sha256,
                effect_key=pin.effect_key,
                claim_id=pin.claim_id,
            )
        )
    except (PhysicalFullMatrixV4ExecutionDriverError, TypeError, ValueError):
        _fail(code)
    if identity != derived:
        _fail(code)
    return pin, {
        "run_id": str(pin.run_id),
        "plan_sha256": pin.plan_sha256,
        "phase": phase_mapping,
        "effect_key": pin.effect_key,
        "phase_request_sha256": pin.phase_request_sha256,
        "binding": binding_mapping,
        "claim_id": pin.claim_id,
        "journaled_effect_start_identity_sha256": identity,
    }


def _effect_start_from_mapping(
    value: object, *, code: str
) -> PhysicalFullMatrixV4SuccessorTransitionEffectStartPin:
    mapping = _exact(value, fields=_EFFECT_START_FIELDS, code=code)
    try:
        run_id = UUID(mapping["run_id"])
        phase = _phase_from_mapping(mapping["phase"], code=code)
        direction = _TRANSITION_DIRECTIONS[phase.name][0]
        pin = PhysicalFullMatrixV4SuccessorTransitionEffectStartPin(
            run_id=run_id,
            plan_sha256=mapping["plan_sha256"],
            phase=phase,
            effect_key=mapping["effect_key"],
            phase_request_sha256=mapping["phase_request_sha256"],
            binding=_binding_from_mapping(mapping["binding"], direction=direction, code=code),
            claim_id=mapping["claim_id"],
            journaled_effect_start_identity_sha256=mapping[
                "journaled_effect_start_identity_sha256"
            ],
        )
    except (KeyError, TypeError):
        _fail(code)
    checked, canonical = _effect_start_mapping(pin, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _anchor_effect_start(
    value: PhysicalFullMatrixV4SuccessorTransitionAnchorPin, *, code: str
) -> PhysicalFullMatrixV4SuccessorTransitionEffectStartPin:
    if type(value) is not PhysicalFullMatrixV4SuccessorTransitionAnchorPin:
        _fail(code)
    try:
        return PhysicalFullMatrixV4SuccessorTransitionEffectStartPin(
            run_id=value.run_id,
            plan_sha256=value.plan_sha256,
            phase=value.phase,
            effect_key=value.effect_key,
            phase_request_sha256=value.phase_request_sha256,
            binding=value.binding,
            claim_id=value.claim_id,
            journaled_effect_start_identity_sha256=(
                value.journaled_effect_start_identity_sha256
            ),
        )
    except TypeError:
        _fail(code)


def _anchor_mapping(
    value: object, *, code: str
) -> tuple[PhysicalFullMatrixV4SuccessorTransitionAnchorPin, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4SuccessorTransitionAnchorPin:
        _fail(code)
    pin = value
    if pin.schema != PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA:
        _fail(code)
    _, effect_mapping = _effect_start_mapping(_anchor_effect_start(pin, code=code), code=code)
    for item in (
        pin.journal_binding_sha256,
        pin.baseline_plan_binding_sha256,
        pin.anchor_head_sha256,
        pin.anchor_commitment_sha256,
        pin.anchor_attestation_sha256,
        pin.anchor_local_event_sha256,
    ):
        _sha(item, code=code)
    genesis = _sha(pin.anchor_genesis_head_sha256, code=code, permit_zero=True)
    previous = _sha(pin.anchor_previous_head_sha256, code=code, permit_zero=True)
    local_previous = _sha(pin.anchor_local_previous_record_sha256, code=code, permit_zero=True)
    if (
        type(pin.anchor_genesis_sequence) is not int
        or pin.anchor_genesis_sequence < 0
        or type(pin.anchor_previous_sequence) is not int
        or pin.anchor_previous_sequence < pin.anchor_genesis_sequence
        or type(pin.anchor_sequence) is not int
        or pin.anchor_sequence != pin.anchor_previous_sequence + 1
        or (
            pin.anchor_previous_sequence == pin.anchor_genesis_sequence
            and previous != genesis
        )
    ):
        _fail(code)
    occurred = _utc(pin.anchor_occurred_at, code=code)
    return pin, {
        "schema": pin.schema,
        **effect_mapping,
        "journal_binding_sha256": pin.journal_binding_sha256,
        "baseline_plan_binding_sha256": pin.baseline_plan_binding_sha256,
        "anchor_genesis_sequence": pin.anchor_genesis_sequence,
        "anchor_genesis_head_sha256": genesis,
        "anchor_previous_sequence": pin.anchor_previous_sequence,
        "anchor_previous_head_sha256": previous,
        "anchor_sequence": pin.anchor_sequence,
        "anchor_head_sha256": pin.anchor_head_sha256,
        "anchor_commitment_sha256": pin.anchor_commitment_sha256,
        "anchor_attestation_sha256": pin.anchor_attestation_sha256,
        "anchor_local_previous_record_sha256": local_previous,
        "anchor_local_event_sha256": pin.anchor_local_event_sha256,
        "anchor_occurred_at": _render_timestamp(occurred),
    }


def _anchor_from_mapping(
    value: object, *, code: str
) -> PhysicalFullMatrixV4SuccessorTransitionAnchorPin:
    mapping = _exact(value, fields=_ANCHOR_FIELDS, code=code)
    try:
        run_id = UUID(mapping["run_id"])
        phase = _phase_from_mapping(mapping["phase"], code=code)
        direction = _TRANSITION_DIRECTIONS[phase.name][0]
        pin = PhysicalFullMatrixV4SuccessorTransitionAnchorPin(
            schema=mapping["schema"],
            run_id=run_id,
            plan_sha256=mapping["plan_sha256"],
            phase=phase,
            effect_key=mapping["effect_key"],
            phase_request_sha256=mapping["phase_request_sha256"],
            binding=_binding_from_mapping(mapping["binding"], direction=direction, code=code),
            claim_id=mapping["claim_id"],
            journaled_effect_start_identity_sha256=mapping[
                "journaled_effect_start_identity_sha256"
            ],
            journal_binding_sha256=mapping["journal_binding_sha256"],
            baseline_plan_binding_sha256=mapping["baseline_plan_binding_sha256"],
            anchor_genesis_sequence=mapping["anchor_genesis_sequence"],
            anchor_genesis_head_sha256=mapping["anchor_genesis_head_sha256"],
            anchor_previous_sequence=mapping["anchor_previous_sequence"],
            anchor_previous_head_sha256=mapping["anchor_previous_head_sha256"],
            anchor_sequence=mapping["anchor_sequence"],
            anchor_head_sha256=mapping["anchor_head_sha256"],
            anchor_commitment_sha256=mapping["anchor_commitment_sha256"],
            anchor_attestation_sha256=mapping["anchor_attestation_sha256"],
            anchor_local_previous_record_sha256=mapping[
                "anchor_local_previous_record_sha256"
            ],
            anchor_local_event_sha256=mapping["anchor_local_event_sha256"],
            anchor_occurred_at=_timestamp(mapping["anchor_occurred_at"], code=code),
        )
    except (KeyError, TypeError):
        _fail(code)
    checked, canonical = _anchor_mapping(pin, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _readiness_mapping(
    value: object, *, successor: PhysicalFullMatrixV4ExecutionBinding | None, now: datetime | None, maximum_age: int, code: str
) -> tuple[PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin:
        _fail(code)
    readiness = value
    if (
        readiness.schema != PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_READINESS_EVIDENCE_SCHEMA
        or readiness.status != PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_READINESS_EVIDENCE_STATUS
        or readiness.gen2_readiness_schema
        != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA
        or readiness.gen2_readiness_status
        != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        or readiness.gen2_observed_slots
        != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS
        or readiness.gen2_reason_codes != ()
        or readiness.writer_authorized is not False
        or readiness.promotion_authorized is not False
        or readiness.execution_authorized is not False
        or type(readiness.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(readiness.campaign_id) is None
        or type(readiness.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(readiness.release_sha) is None
    ):
        _fail(code)
    _sha(readiness.successor_readiness_binding_sha256, code=code)
    _sha(readiness.readiness_evidence_sha256, code=code)
    observed = _utc(readiness.observed_at, code=code)
    expires = _utc(readiness.expires_at, code=code)
    if expires <= observed or expires - observed > timedelta(seconds=_MAX_TRANSITION_WINDOW_SECONDS):
        _fail(code)
    if now is not None and (
        observed > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or now - observed > timedelta(seconds=maximum_age)
        or expires <= now
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_READINESS_STALE_OR_EXPIRED")
    if successor is not None and (
        readiness.campaign_id != successor.campaign_id
        or readiness.release_sha != successor.release_sha
        or readiness.successor_readiness_binding_sha256
        != successor.readiness_binding_sha256
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_READINESS_BINDING_MISMATCH")
    return readiness, {
        "schema": readiness.schema,
        "status": readiness.status,
        "gen2_readiness_schema": readiness.gen2_readiness_schema,
        "gen2_readiness_status": readiness.gen2_readiness_status,
        "campaign_id": readiness.campaign_id,
        "release_sha": readiness.release_sha,
        "successor_readiness_binding_sha256": readiness.successor_readiness_binding_sha256,
        "gen2_observed_slots": list(readiness.gen2_observed_slots),
        "gen2_reason_codes": list(readiness.gen2_reason_codes),
        "readiness_evidence_sha256": readiness.readiness_evidence_sha256,
        "observed_at": _render_timestamp(observed),
        "expires_at": _render_timestamp(expires),
    }


def _readiness_from_mapping(
    value: object, *, successor: PhysicalFullMatrixV4ExecutionBinding | None, now: datetime | None, maximum_age: int, code: str
) -> PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin:
    mapping = _exact(value, fields=_READINESS_FIELDS, code=code)
    slots = mapping["gen2_observed_slots"]
    reasons = mapping["gen2_reason_codes"]
    if type(slots) is not list or type(reasons) is not list or any(type(x) is not str for x in slots + reasons):
        _fail(code)
    try:
        readiness = PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin(
            schema=mapping["schema"],
            status=mapping["status"],
            gen2_readiness_schema=mapping["gen2_readiness_schema"],
            gen2_readiness_status=mapping["gen2_readiness_status"],
            campaign_id=mapping["campaign_id"],
            release_sha=mapping["release_sha"],
            successor_readiness_binding_sha256=mapping[
                "successor_readiness_binding_sha256"
            ],
            gen2_observed_slots=tuple(slots),
            gen2_reason_codes=tuple(reasons),
            readiness_evidence_sha256=mapping["readiness_evidence_sha256"],
            observed_at=_timestamp(mapping["observed_at"], code=code),
            expires_at=_timestamp(mapping["expires_at"], code=code),
        )
    except (KeyError, TypeError):
        _fail(code)
    checked, canonical = _readiness_mapping(
        readiness, successor=successor, now=now, maximum_age=maximum_age, code=code
    )
    if mapping != canonical:
        _fail(code)
    return checked


def _evidence_pins_mapping(
    value: object, *, code: str
) -> tuple[PhysicalFullMatrixV4SuccessorTransitionEvidencePins, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4SuccessorTransitionEvidencePins:
        _fail(code)
    pins = value
    values = tuple(pins.__dict__.values())
    if len(values) != 7:
        _fail(code)
    for item in values:
        _sha(item, code=code)
    if (
        pins.executor_installation_attestation_sha256
        == pins.observer_installation_attestation_sha256
        or pins.executor_transition_evidence_sha256
        == pins.observer_transition_evidence_sha256
    ):
        _fail(code)
    return pins, dict(pins.__dict__)


def _replay_policy(value: object, *, code: str) -> PhysicalFullMatrixV4SuccessorTransitionReplayPolicy:
    if type(value) is not PhysicalFullMatrixV4SuccessorTransitionReplayPolicy:
        _fail(code)
    if (
        value.anti_replay_namespace
        != PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_ANTI_REPLAY_NAMESPACE
    ):
        _fail(code)
    _sha(value.witness_ledger_scope_sha256, code=code)
    return value


def _successor_relation(
    predecessor: PhysicalFullMatrixV4ExecutionBinding,
    successor: PhysicalFullMatrixV4ExecutionBinding,
    *, phase: PhysicalFullMatrixV4ExecutionPhase, code: str
) -> None:
    predecessor_direction, successor_direction = _TRANSITION_DIRECTIONS[phase.name]
    _binding_mapping(predecessor, direction=predecessor_direction, code=code)
    _binding_mapping(successor, direction=successor_direction, code=code)
    if (
        successor.campaign_id != predecessor.campaign_id
        or successor.release_sha != predecessor.release_sha
        or successor.writer_epoch <= predecessor.writer_epoch
        or successor.writer_lease_id == predecessor.writer_lease_id
        or successor.witnessed_term_proof_sha256 == predecessor.witnessed_term_proof_sha256
        or successor.route_commitment_sha256 == predecessor.route_commitment_sha256
        or successor.four_role_binding_sha256 != predecessor.four_role_binding_sha256
        or successor.readiness_binding_sha256 == predecessor.readiness_binding_sha256
        or successor.roundtrip_attestation_sha256 == predecessor.roundtrip_attestation_sha256
        or successor.roundtrip_configuration_sha256
        != predecessor.roundtrip_configuration_sha256
        or successor.witness_transition_id == predecessor.witness_transition_id
        or successor.witness_sequence <= predecessor.witness_sequence
    ):
        _fail(code)


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalFullMatrixV4SuccessorTransitionVerificationConfig:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_DISABLED")
    effect_start, _ = _effect_start_mapping(
        value.expected_effect_start, code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID"
    )
    anchor, _ = _anchor_mapping(
        value.expected_effect_start_anchor, code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID"
    )
    if _anchor_effect_start(anchor, code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID") != effect_start:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_ANCHOR_EFFECT_MISMATCH")
    predecessor_direction, successor_direction = _TRANSITION_DIRECTIONS[effect_start.phase.name]
    predecessor, _ = _binding_mapping(
        value.expected_predecessor_binding,
        direction=predecessor_direction,
        code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID",
    )
    successor, _ = _binding_mapping(
        value.expected_successor_binding,
        direction=successor_direction,
        code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID",
    )
    if predecessor != effect_start.binding:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_PREDECESSOR_MISMATCH")
    _successor_relation(
        predecessor,
        successor,
        phase=effect_start.phase,
        code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_SUCCESSOR_INVALID",
    )
    maximum = _positive(
        value.maximum_evidence_age_seconds,
        maximum=_MAX_EVIDENCE_AGE_SECONDS,
        code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID",
    )
    readiness, _ = _readiness_mapping(
        value.expected_successor_readiness,
        successor=successor,
        now=None,
        maximum_age=maximum,
        code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID",
    )
    pins, _ = _evidence_pins_mapping(
        value.expected_evidence_pins, code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID"
    )
    policy = _replay_policy(
        value.expected_replay_policy, code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID"
    )
    executor = _public_key(value.executor_signer_public_key, code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID")
    observer = _public_key(value.observer_signer_public_key, code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID")
    witness = _public_key(value.witness_signer_public_key, code="V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_INVALID")
    if len({executor, observer, witness}) != 3:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_CONFIG_SIGNER_SEPARATION_REQUIRED")
    return _ConfigFacts(
        effect_start=effect_start,
        anchor=anchor,
        predecessor=predecessor,
        successor=successor,
        readiness=readiness,
        evidence_pins=pins,
        replay_policy=policy,
        executor_key=executor,
        observer_key=observer,
        witness_key=witness,
        maximum_age_seconds=maximum,
    )


def _transition_binding(
    value: object, *, facts: _ConfigFacts, now: datetime, code: str
) -> _TransitionFacts:
    mapping = _exact(value, fields=_TRANSITION_BINDING_FIELDS, code=code)
    if (
        mapping["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_SCHEMA
        or mapping["version"] != _VERSION
        or mapping["status"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_STATUS
    ):
        _fail(code)
    transition_id = _identifier(mapping["transition_id"], code=code)
    transition_nonce = _nonce(mapping["transition_nonce"], code=code)
    requested = _timestamp(mapping["requested_at"], code=code)
    expires = _timestamp(mapping["expires_at"], code=code)
    if (
        expires <= requested
        or expires - requested > timedelta(seconds=_MAX_TRANSITION_WINDOW_SECONDS)
        or requested > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or now - requested > timedelta(seconds=facts.maximum_age_seconds)
        or expires <= now
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_STALE_OR_EXPIRED")
    effect = _effect_start_from_mapping(mapping["effect_start"], code=code)
    anchor = _anchor_from_mapping(mapping["effect_start_anchor"], code=code)
    predecessor_direction, successor_direction = _TRANSITION_DIRECTIONS[effect.phase.name]
    predecessor = _binding_from_mapping(
        mapping["predecessor_binding"], direction=predecessor_direction, code=code
    )
    successor = _binding_from_mapping(
        mapping["successor_binding"], direction=successor_direction, code=code
    )
    readiness = _readiness_from_mapping(
        mapping["successor_readiness"],
        successor=successor,
        now=now,
        maximum_age=facts.maximum_age_seconds,
        code=code,
    )
    pins = _evidence_pins_from_mapping(mapping["evidence_pins"], code=code)
    if (
        effect != facts.effect_start
        or anchor != facts.anchor
        or _anchor_effect_start(anchor, code=code) != effect
        or predecessor != facts.predecessor
        or successor != facts.successor
        or readiness != facts.readiness
        or pins != facts.evidence_pins
        or predecessor != effect.binding
        or anchor.anchor_occurred_at > requested
        or requested - anchor.anchor_occurred_at
        > timedelta(seconds=facts.maximum_age_seconds)
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_EXPECTED_PINS_MISMATCH")
    _successor_relation(predecessor, successor, phase=effect.phase, code=code)
    canonical = _canonical(mapping, code=code)
    return _TransitionFacts(
        binding_mapping=mapping,
        binding_sha256=hashlib.sha256(canonical).hexdigest(),
        effect_start=effect,
        anchor=anchor,
        predecessor=predecessor,
        successor=successor,
        readiness=readiness,
        evidence_pins=pins,
        transition_id=transition_id,
        transition_nonce=transition_nonce,
        requested_at=requested,
        expires_at=expires,
    )


def _evidence_pins_from_mapping(
    value: object, *, code: str
) -> PhysicalFullMatrixV4SuccessorTransitionEvidencePins:
    mapping = _exact(value, fields=_EVIDENCE_PIN_FIELDS, code=code)
    try:
        pins = PhysicalFullMatrixV4SuccessorTransitionEvidencePins(**mapping)
    except TypeError:
        _fail(code)
    checked, canonical = _evidence_pins_mapping(pins, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _signed_transition_receipt(
    raw: object, *, schema: str, kind: str, role: str, signer: bytes, domain: bytes, facts: _ConfigFacts, now: datetime, code: str
) -> tuple[_TransitionFacts, bytes]:
    mapping, canonical = _parse(raw, fields=_SIGNED_RECEIPT_FIELDS, code=code)
    if (
        mapping["schema"] != schema
        or mapping["version"] != _VERSION
        or mapping["kind"] != kind
        or mapping["signer_role"] != role
    ):
        _fail(code)
    transition = _transition_binding(mapping["transition_binding"], facts=facts, now=now, code=code)
    if mapping["transition_binding_sha256"] != transition.binding_sha256:
        _fail(code)
    unsigned = dict(mapping)
    signature = unsigned.pop("signature_base64")
    _verify_signature(signer=signer, domain=domain, unsigned=unsigned, signature_base64=signature, code=code)
    return transition, canonical


def _same_transition(left: _TransitionFacts, right: _TransitionFacts) -> bool:
    return (
        left.binding_mapping == right.binding_mapping
        and left.binding_sha256 == right.binding_sha256
        and left.effect_start == right.effect_start
        and left.anchor == right.anchor
        and left.predecessor == right.predecessor
        and left.successor == right.successor
        and left.readiness == right.readiness
        and left.evidence_pins == right.evidence_pins
        and left.transition_id == right.transition_id
        and left.transition_nonce == right.transition_nonce
        and left.requested_at == right.requested_at
        and left.expires_at == right.expires_at
    )


def derive_physical_full_matrix_v4_witness_successor_transition_replay_key_sha256(
    *, effect_start: object, predecessor_binding: object
) -> str:
    """Derive a stable single-use identity excluding caller-selected IDs/nonces."""

    effect, effect_mapping = _effect_start_mapping(
        effect_start, code="V4_WITNESS_SUCCESSOR_TRANSITION_REPLAY_KEY_INVALID"
    )
    predecessor_direction, _ = _TRANSITION_DIRECTIONS[effect.phase.name]
    predecessor, predecessor_mapping = _binding_mapping(
        predecessor_binding,
        direction=predecessor_direction,
        code="V4_WITNESS_SUCCESSOR_TRANSITION_REPLAY_KEY_INVALID",
    )
    if predecessor != effect.binding:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_REPLAY_KEY_INVALID")
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_SCHEMA,
                "purpose": "witness-durable-single-use-v4-successor-transition-v1",
                "effect_start": effect_mapping,
                "predecessor_binding": predecessor_mapping,
            },
            code="V4_WITNESS_SUCCESSOR_TRANSITION_REPLAY_KEY_INVALID",
        )
    ).hexdigest()


def _witness_receipt(
    raw: object, *, transition: _TransitionFacts, facts: _ConfigFacts, now: datetime
) -> tuple[_WitnessFacts, bytes]:
    code = "V4_WITNESS_SUCCESSOR_TRANSITION_WITNESS_ADMISSION_INVALID"
    mapping, canonical = _parse(raw, fields=_WITNESS_RECEIPT_FIELDS, code=code)
    successor = transition.successor
    predecessor = transition.predecessor
    if (
        mapping["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_WITNESS_ADMISSION_SCHEMA
        or mapping["version"] != _VERSION
        or mapping["kind"] != _WITNESS_KIND
        or mapping["signer_role"] != _WITNESS_ROLE
        or mapping["transition_id"] != transition.transition_id
        or mapping["transition_nonce"] != transition.transition_nonce
        or mapping["transition_binding_sha256"] != transition.binding_sha256
        or mapping["predecessor_writer_epoch"] != predecessor.writer_epoch
        or mapping["predecessor_writer_lease_id"] != predecessor.writer_lease_id
        or mapping["successor_writer_holder_site"] != successor.writer_holder_site
        or mapping["successor_writer_epoch"] != successor.writer_epoch
        or mapping["successor_writer_lease_id"] != successor.writer_lease_id
        or mapping["successor_witness_transition_id"] != successor.witness_transition_id
        or mapping["successor_witnessed_term_proof_sha256"] != successor.witnessed_term_proof_sha256
        or mapping["successor_readiness_binding_sha256"] != successor.readiness_binding_sha256
        or mapping["anti_replay_namespace"] != facts.replay_policy.anti_replay_namespace
        or mapping["anti_replay_mode"] != _ANTI_REPLAY_MODE
        or mapping["witness_ledger_scope_sha256"] != facts.replay_policy.witness_ledger_scope_sha256
    ):
        _fail(code)
    replay_key = _sha(mapping["replay_key_sha256"], code=code)
    if replay_key != derive_physical_full_matrix_v4_witness_successor_transition_replay_key_sha256(
        effect_start=transition.effect_start, predecessor_binding=predecessor
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_REPLAY_KEY_MISMATCH")
    admission_id = _identifier(mapping["admission_id"], code=code)
    admission_nonce = _nonce(mapping["admission_nonce"], code=code)
    committed = _timestamp(mapping["committed_at"], code=code)
    expires = _timestamp(mapping["expires_at"], code=code)
    if (
        expires != transition.expires_at
        or committed < transition.requested_at
        # The durable Witness record is evidence of this observed successor
        # state, not a pre-credit for a readiness hash that has not yet been
        # observed.  A real owner may reserve a separate durable operation
        # gate before acting, but that reservation is intentionally outside
        # this post-transition evidence grammar.
        or committed < transition.readiness.observed_at
        or committed > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or now - committed > timedelta(seconds=facts.maximum_age_seconds)
        or expires <= now
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_WITNESS_ADMISSION_STALE_OR_ORDER_INVALID")
    sequence = _positive(mapping["witness_ledger_sequence"], maximum=2**63 - 1, code=code)
    entry = _sha(mapping["witness_ledger_entry_sha256"], code=code)
    previous = _sha(
        mapping["witness_ledger_previous_head_sha256"], code=code, permit_zero=sequence == 1
    )
    if sequence > 1 and previous == _ZERO_SHA256:
        _fail(code)
    unsigned = dict(mapping)
    signature = unsigned.pop("signature_base64")
    _verify_signature(signer=facts.witness_key, domain=_WITNESS_DOMAIN, unsigned=unsigned, signature_base64=signature, code=code)
    return _WitnessFacts(replay_key, admission_id, admission_nonce, committed, sequence, entry, previous), canonical


def verify_physical_full_matrix_v4_witness_successor_transition(
    *, executor_receipt: object, observer_receipt: object, witness_admission_receipt: object,
    config: PhysicalFullMatrixV4SuccessorTransitionVerificationConfig, now: datetime
) -> VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition:
    """Verify P4/P7 signed evidence only; this never performs a transition."""

    facts = _config(config)
    observed = _utc(now, code="V4_WITNESS_SUCCESSOR_TRANSITION_CLOCK_INVALID")
    executor, executor_raw = _signed_transition_receipt(
        executor_receipt,
        schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_EXECUTOR_RECEIPT_SCHEMA,
        kind=_EXECUTOR_KIND,
        role=_EXECUTOR_ROLE,
        signer=facts.executor_key,
        domain=_EXECUTOR_DOMAIN,
        facts=facts,
        now=observed,
        code="V4_WITNESS_SUCCESSOR_TRANSITION_EXECUTOR_RECEIPT_INVALID",
    )
    observer, observer_raw = _signed_transition_receipt(
        observer_receipt,
        schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_OBSERVER_RECEIPT_SCHEMA,
        kind=_OBSERVER_KIND,
        role=_OBSERVER_ROLE,
        signer=facts.observer_key,
        domain=_OBSERVER_DOMAIN,
        facts=facts,
        now=observed,
        code="V4_WITNESS_SUCCESSOR_TRANSITION_OBSERVER_RECEIPT_INVALID",
    )
    if not _same_transition(executor, observer):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_EXECUTOR_OBSERVER_MISMATCH")
    witness, witness_raw = _witness_receipt(
        witness_admission_receipt, transition=executor, facts=facts, now=observed
    )
    result = VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition(
        capability=_CAPABILITY,
        canonical_executor_receipt=executor_raw,
        canonical_observer_receipt=observer_raw,
        canonical_witness_admission_receipt=witness_raw,
        executor_receipt_sha256=hashlib.sha256(executor_raw).hexdigest(),
        observer_receipt_sha256=hashlib.sha256(observer_raw).hexdigest(),
        witness_admission_receipt_sha256=hashlib.sha256(witness_raw).hexdigest(),
        effect_start=executor.effect_start,
        effect_start_anchor=executor.anchor,
        predecessor_binding=executor.predecessor,
        successor_binding=executor.successor,
        successor_readiness=executor.readiness,
        evidence_pins=executor.evidence_pins,
        replay_policy=facts.replay_policy,
        transition_id=executor.transition_id,
        transition_nonce=executor.transition_nonce,
        replay_key_sha256=witness.replay_key_sha256,
        requested_at=executor.requested_at,
        expires_at=executor.expires_at,
        admission_id=witness.admission_id,
        admission_nonce=witness.admission_nonce,
        committed_at=witness.committed_at,
        witness_ledger_sequence=witness.witness_ledger_sequence,
        witness_ledger_entry_sha256=witness.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=witness.witness_ledger_previous_head_sha256,
    )
    _STATES[result] = _VerifiedState(executor_raw, observer_raw, witness_raw)
    return result


def _same_public(
    value: VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition,
    verified: VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition,
) -> bool:
    names = (
        "canonical_executor_receipt", "canonical_observer_receipt", "canonical_witness_admission_receipt",
        "executor_receipt_sha256", "observer_receipt_sha256", "witness_admission_receipt_sha256",
        "effect_start", "effect_start_anchor", "predecessor_binding", "successor_binding",
        "successor_readiness", "evidence_pins", "replay_policy", "transition_id", "transition_nonce",
        "replay_key_sha256", "requested_at", "expires_at", "admission_id", "admission_nonce",
        "committed_at", "witness_ledger_sequence", "witness_ledger_entry_sha256",
        "witness_ledger_previous_head_sha256",
    )
    return (
        all(getattr(value, name) == getattr(verified, name) for name in names)
        and value.writer_authorized is False
        and value.promotion_authorized is False
        and value.traffic_switch_authorized is False
        and value.execution_authorized is False
        and value.full_matrix_authorized is False
        and value.phase_completion_evidenced is False
        and value.next_phase_start_authorized is False
    )


def require_verified_physical_full_matrix_v4_witness_successor_transition(
    value: object, *, config: PhysicalFullMatrixV4SuccessorTransitionVerificationConfig, now: datetime
) -> VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition:
    """Reverify an opaque successor evidence capability and its exact pins."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition
        or value._capability is not _CAPABILITY
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_VERIFIED_EVIDENCE_REQUIRED")
    state = _STATES.get(value)
    if state is None:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_VERIFIED_EVIDENCE_REQUIRED")
    verified = verify_physical_full_matrix_v4_witness_successor_transition(
        executor_receipt=state.executor,
        observer_receipt=state.observer,
        witness_admission_receipt=state.witness,
        config=config,
        now=now,
    )
    if not _same_public(value, verified):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_VERIFIED_EVIDENCE_TAMPERED")
    return value
