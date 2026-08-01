"""Default-off FI-local foundation for a V4 Phase-1 Strict-ACK checkpoint.

The existing Gen2 strict-writer receipt and the V2 witnessed ACK chain have no
V4 effect-start identity.  They must therefore never be relabelled as a
Phase-1 completion merely because a later caller has a V4 request.  This
module defines the *local capture grammar* which a future, separately owned
root transaction coordinator may persist beside a newly-created Gen2 row.

This is deliberately only the first half of that future coordinator:

* a capture capability is minted only from the driver's private, post-journal
  effect-start request and is one-shot;
* the signed checkpoint accepts only a pending Gen2 transaction capability,
  never a pre-existing Gen2 observation or an external ACK projection; and
* the result is explicitly ``pending-external-commit`` and non-authorizing.

There is no database/session, callback, transport, Object Storage, Witness,
runner, phase execution, or phase-success surface here.  Raw public
``Pending...Commit`` preparation and row projection are deliberately disabled.
The separately owned live-root diagnostic proves that the missing DB causal
fence cannot currently be supplied by the Gen2 API; therefore it intentionally
does *not* invoke the private grammar below.  That private grammar remains
quarantined for signed-format verification only, not as a live path.  A later
typed SQL transaction participant must first establish an auditable
pending-row/session/root association, insert the canonical bytes in that same
root transaction, and reconcile only after a known successful commit.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Final
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_response as _bound
from core import (
    physical_wal_v2_witness_roundtrip_strict_writer_bound_sqlalchemy_transaction
    as _gen2_transaction,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError",
    "PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint",
    "begin_physical_full_matrix_v4_phase1_post_effect_strict_ack_capture",
    "canonical_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint",
    "require_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint",
)


PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-phase1-post-effect-strict-ack-checkpoint-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_DEFAULT_ENABLED: Final = (
    False
)
PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS: Final = (
    "prepared-post-effect-strict-ack-capture-pending-external-commit"
)

_VERSION: Final = 1
_SIGNATURE_ALGORITHM: Final = "ed25519"
_SIGNING_DOMAIN: Final = (
    b"gold-trade-physical-full-matrix-v4-phase1-post-effect-strict-ack-checkpoint-v1\x00"
)
_PHASE: Final = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0]
_FI_SITE: Final = "webapp_fi"
_IR_SITE: Final = "webapp_ir"
_FORBIDDEN: Final = "forbidden"
_MAX_CHECKPOINT_BYTES: Final = 512 * 1024
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_KEY_ID_RE: Final = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_LEASE_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_CAPTURE_ID_RE: Final = re.compile(
    r"^v4-p1-post-effect-capture-[0-9a-f]{32}$", re.ASCII
)
_TIMESTAMP_RE: Final = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)

_CAPTURE_CAPABILITY = object()
_PREPARED_CAPABILITY = object()
# Deliberately private quarantine capability.  The public raw prepare/project
# entry points below hard-fail, and no live module imports this value to reach
# the grammar until an audited DB causal fence exists.  It is not a durable
# permit or a phase-completion capability.
_SAME_ROOT_ENVELOPE_CAPABILITY = object()

_CHECKPOINT_FIELDS: Final = frozenset(
    {
        "schema",
        "version",
        "status",
        "checkpoint_id",
        "captured_at",
        "signer_site",
        "signer_key_id",
        "signature_algorithm",
        "v4_request",
        "effect_start",
        "capture",
        "strict_gen2",
        "strict_ack_post_effect_bound",
        "capture_handoff_verified",
        "checkpoint_durable",
        "phase_completion_evidenced",
        "writer_authorized",
        "promotion_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
        "direct_fi_to_ir_control",
        "direct_ir_to_fi_control",
        "signature_base64",
    }
)

_V4_REQUEST_FIELDS: Final = frozenset(
    {
        "run_id",
        "plan_sha256",
        "phase_name",
        "phase_sequence",
        "oracle",
        "transport_profile",
        "effect_key",
        "phase_request_sha256",
        "binding",
    }
)
_BINDING_FIELDS: Final = frozenset(_driver.PhysicalFullMatrixV4ExecutionBinding.__dataclass_fields__)
_EFFECT_START_FIELDS: Final = frozenset(
    {
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
_CAPTURE_FIELDS: Final = frozenset(
    {
        "capture_id",
        "capture_handoff_sha256",
        "capture_started_at",
    }
)
_STRICT_GEN2_FIELDS: Final = frozenset(
    {
        "observation_schema",
        "observation_sha256",
        "runtime_commit_receipt_sha256",
        "runtime_commit_pins_sha256",
        "instruction_schema",
        "configuration_sha256",
        "v2_base_configuration_sha256",
        "atomic_commit_boundary",
        "commit_id",
        "v2_base_commit_id",
        "local_commit_record_id",
        "local_response_id",
        "attestation_consumption_id",
        "committed_at",
        "runtime_commit_pins",
    }
)


class PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError(ValueError):
    """The default-off local capture grammar refused unsafe evidence."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig:
    """Explicit FI-local signer pin for a pending P1 checkpoint only.

    The private signer is intentionally not retained in configuration.  It is
    passed only to the narrow preparation call, so a future SQL participant
    can preload it before opening PostgreSQL rather than calling an HSM or a
    generic signer while the transaction is live.
    """

    fi_checkpoint_signer_public_key: bytes = b""
    fi_checkpoint_signer_key_id: str = ""
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_DEFAULT_ENABLED


@dataclass(frozen=True, eq=False, init=False)
class PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture:
    """One in-process, one-shot handoff made after an exact V4 effect start.

    It is correlation only.  It is not a writer/promotion/execution permit and
    cannot be serialized or reconstructed after a process restart.
    """

    schema: str
    capture_id: str
    capture_handoff_sha256: str
    run_id: UUID
    plan_sha256: str
    phase_sequence: int
    effect_key: str
    phase_request_sha256: str
    claim_id: str
    journaled_effect_start_identity_sha256: str
    anchor_sequence: int
    anchor_head_sha256: str
    capture_started_at: datetime
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        capture_id: str,
        capture_handoff_sha256: str,
        facts: "_RequestFacts",
        capture_started_at: datetime,
        capability: object,
    ) -> None:
        if capability is not _CAPTURE_CAPABILITY:
            raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CAPTURE_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("schema", PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA),
            ("capture_id", capture_id),
            ("capture_handoff_sha256", capture_handoff_sha256),
            ("run_id", facts.run_id),
            ("plan_sha256", facts.plan_sha256),
            ("phase_sequence", facts.phase_sequence),
            ("effect_key", facts.effect_key),
            ("phase_request_sha256", facts.phase_request_sha256),
            ("claim_id", facts.claim_id),
            ("journaled_effect_start_identity_sha256", facts.journaled_effect_start_identity_sha256),
            ("anchor_sequence", facts.anchor_sequence),
            ("anchor_head_sha256", facts.anchor_head_sha256),
            ("capture_started_at", capture_started_at),
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
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CAPTURE_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CAPTURE_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CAPTURE_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False, init=False)
class PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint:
    """Signed local capture intent; it remains non-durable and non-authorizing.

    A future transaction participant may persist ``canonical_checkpoint`` only
    with the referenced pending Gen2 row.  This object intentionally cannot
    become a V4 phase result by itself.
    """

    schema: str
    status: str
    checkpoint_sha256: str
    canonical_checkpoint: bytes = field(repr=False)
    checkpoint_id: str
    signer_key_id: str
    capture_id: str
    capture_handoff_sha256: str
    run_id: UUID
    plan_sha256: str
    phase_sequence: int
    effect_key: str
    phase_request_sha256: str
    claim_id: str
    journaled_effect_start_identity_sha256: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    anchor_sequence: int
    anchor_head_sha256: str
    anchor_commitment_sha256: str
    anchor_attestation_sha256: str
    strict_observation_sha256: str
    strict_runtime_commit_receipt_sha256: str
    strict_commit_id: str
    strict_v2_base_commit_id: str
    strict_local_commit_record_id: str
    strict_local_response_id: str
    strict_attestation_consumption_id: str
    strict_committed_at: datetime
    checkpoint_durable: bool = False
    strict_ack_post_effect_bound: bool = True
    capture_handoff_verified: bool = True
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
        checkpoint_sha256: str,
        canonical_checkpoint: bytes,
        checkpoint_id: str,
        signer_key_id: str,
        capture: PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture,
        facts: "_RequestFacts",
        strict: "_StrictFacts",
        capability: object,
    ) -> None:
        if capability is not _PREPARED_CAPABILITY:
            raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("schema", PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA),
            ("status", PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS),
            ("checkpoint_sha256", checkpoint_sha256),
            ("canonical_checkpoint", canonical_checkpoint),
            ("checkpoint_id", checkpoint_id),
            ("signer_key_id", signer_key_id),
            ("capture_id", capture.capture_id),
            ("capture_handoff_sha256", capture.capture_handoff_sha256),
            ("run_id", facts.run_id),
            ("plan_sha256", facts.plan_sha256),
            ("phase_sequence", facts.phase_sequence),
            ("effect_key", facts.effect_key),
            ("phase_request_sha256", facts.phase_request_sha256),
            ("claim_id", facts.claim_id),
            ("journaled_effect_start_identity_sha256", facts.journaled_effect_start_identity_sha256),
            ("journal_binding_sha256", facts.journal_binding_sha256),
            ("baseline_plan_binding_sha256", facts.baseline_plan_binding_sha256),
            ("anchor_sequence", facts.anchor_sequence),
            ("anchor_head_sha256", facts.anchor_head_sha256),
            ("anchor_commitment_sha256", facts.anchor_commitment_sha256),
            ("anchor_attestation_sha256", facts.anchor_attestation_sha256),
            ("strict_observation_sha256", strict.observation_sha256),
            ("strict_runtime_commit_receipt_sha256", strict.runtime_commit_receipt_sha256),
            ("strict_commit_id", strict.commit_id),
            ("strict_v2_base_commit_id", strict.v2_base_commit_id),
            ("strict_local_commit_record_id", strict.local_commit_record_id),
            ("strict_local_response_id", strict.local_response_id),
            ("strict_attestation_consumption_id", strict.attestation_consumption_id),
            ("strict_committed_at", strict.committed_at),
            ("checkpoint_durable", False),
            ("strict_ack_post_effect_bound", True),
            ("capture_handoff_verified", True),
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
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _ConfigFacts:
    signer_public_key: bytes
    signer_key_id: str


@dataclass(frozen=True)
class _RequestFacts:
    run_id: UUID
    plan_sha256: str
    phase_name: str
    phase_sequence: int
    oracle: str
    transport_profile: str
    effect_key: str
    phase_request_sha256: str
    binding: dict[str, object]
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


@dataclass
class _CaptureState:
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig
    request: _driver.PhysicalFullMatrixV4ExecutionRequest
    facts: _RequestFacts
    started_at: datetime
    consumed: bool = False
    # Reserved for a future audited coordinator.  No live module is allowed to
    # claim this marker today: Gen2's public pending handoff has no verifiable
    # root/session identity.
    same_root_envelope_claimed: bool = False


@dataclass(frozen=True)
class _StrictFacts:
    observation_schema: str
    observation_sha256: str
    runtime_commit_receipt_sha256: str
    runtime_commit_pins_sha256: str
    instruction_schema: str
    configuration_sha256: str
    v2_base_configuration_sha256: str
    atomic_commit_boundary: str
    commit_id: str
    v2_base_commit_id: str
    local_commit_record_id: str
    local_response_id: str
    attestation_consumption_id: str
    committed_at: datetime
    runtime_commit_pins: dict[str, object]


@dataclass(frozen=True)
class _PreparedState:
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig
    capture: PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture
    request: _driver.PhysicalFullMatrixV4ExecutionRequest
    pending: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit
    facts: _RequestFacts
    strict: _StrictFacts


_CAPTURE_STATES: WeakKeyDictionary[PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture, _CaptureState] = WeakKeyDictionary()
_PREPARED_STATES: WeakKeyDictionary[PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint, _PreparedState] = WeakKeyDictionary()


def _sha(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    if value == "0" * 64 and not permit_zero:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _lease_identifier(value: object, *, code: str) -> str:
    """Validate the V4/Gen2 lease grammar without imposing an invented length."""

    if type(value) is not str or _LEASE_IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _capture_id(value: object, *, code: str) -> str:
    if type(value) is not str or _CAPTURE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)


def _render_timestamp(value: object, *, code: str) -> str:
    candidate = _utc(value, code=code)
    if candidate.microsecond:
        return candidate.strftime("%Y-%m-%dT%H:%M:%S.") + f"{candidate.microsecond:06d}".rstrip("0") + "Z"
    return candidate.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError:
        _fail(code)


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CONFIG_INVALID")
    if value.enabled is False:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_DISABLED")
    public = value.fi_checkpoint_signer_public_key
    if type(public) is not bytes or len(public) != 32 or public == b"\x00" * 32:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CONFIG_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(public)
    except ValueError:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CONFIG_INVALID")
    if (
        value.enabled is not True
        or type(value.fi_checkpoint_signer_key_id) is not str
        or _KEY_ID_RE.fullmatch(value.fi_checkpoint_signer_key_id) is None
        or value.fi_checkpoint_signer_key_id != _key_id(public)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CONFIG_INVALID")
    return _ConfigFacts(signer_public_key=public, signer_key_id=value.fi_checkpoint_signer_key_id)


def _binding(value: object) -> dict[str, object]:
    if type(value) is not _driver.PhysicalFullMatrixV4ExecutionBinding:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    body = dict(value.__dict__)
    if set(body) != _BINDING_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    for name in (
        "readiness_binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "witnessed_term_proof_sha256",
        "roundtrip_attestation_sha256",
        "roundtrip_configuration_sha256",
    ):
        _sha(body[name], code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    if (
        body["writer_holder_site"] != _FI_SITE
        or body["source_site"] != _FI_SITE
        or body["destination_site"] != _IR_SITE
        or type(body["campaign_id"]) is not str
        or type(body["release_sha"]) is not str
        or type(body["writer_epoch"]) is not int
        or body["writer_epoch"] < 1
        or type(body["witness_sequence"]) is not int
        or body["witness_sequence"] < 1
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    _lease_identifier(body["writer_lease_id"], code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    _identifier(body["witness_transition_id"], code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    return body


def _request_facts(request: object) -> _RequestFacts:
    if type(request) is not _driver.PhysicalFullMatrixV4ExecutionRequest:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_REQUIRED")
    try:
        authority = _driver.require_physical_full_matrix_v4_effect_start_authority(request=request)
        anchor = _driver.require_physical_full_matrix_v4_effect_start_anchor_proof(request=request)
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_POST_EFFECT_AUTHORITY_REQUIRED"
        ) from exc
    if (
        request.phase != _PHASE
        or authority.phase != _PHASE
        or anchor.phase != _PHASE
        or request.run_id != authority.run_id
        or request.run_id != anchor.run_id
        or request.plan_sha256 != authority.plan_sha256
        or request.plan_sha256 != anchor.plan_sha256
        or request.effect_key != authority.effect_key
        or request.effect_key != anchor.effect_key
        or request.phase_request_sha256 != authority.phase_request_sha256
        or request.phase_request_sha256 != anchor.phase_request_sha256
        or request.binding != authority.binding
        or request.binding != anchor.binding
        or authority.claim_id != anchor.claim_id
        or authority.journaled_effect_start_identity_sha256
        != anchor.journaled_effect_start_identity_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_POST_EFFECT_CORRELATION_MISMATCH")
    run_id = request.run_id
    if type(run_id) is not UUID:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    plan_sha = _sha(request.plan_sha256, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    effect_key = _sha(request.effect_key, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    request_sha = _sha(request.phase_request_sha256, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    binding = _binding(request.binding)
    claim_id = _identifier(authority.claim_id, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    hashes = {
        "journaled_effect_start_identity_sha256": authority.journaled_effect_start_identity_sha256,
        "journal_binding_sha256": anchor.journal_binding_sha256,
        "baseline_plan_binding_sha256": anchor.baseline_plan_binding_sha256,
        "anchor_head_sha256": anchor.anchor_head_sha256,
        "anchor_commitment_sha256": anchor.anchor_commitment_sha256,
        "anchor_attestation_sha256": anchor.anchor_attestation_sha256,
        "anchor_local_event_sha256": anchor.anchor_local_event_sha256,
    }
    checked = {
        name: _sha(value, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
        for name, value in hashes.items()
    }
    anchor_genesis_head = _sha(
        anchor.anchor_genesis_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID",
        permit_zero=True,
    )
    anchor_previous_head = _sha(
        anchor.anchor_previous_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID",
        permit_zero=True,
    )
    anchor_local_previous = _sha(
        anchor.anchor_local_previous_record_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID",
        permit_zero=True,
    )
    if (
        type(anchor.anchor_genesis_sequence) is not int
        or anchor.anchor_genesis_sequence < 0
        or type(anchor.anchor_previous_sequence) is not int
        or anchor.anchor_previous_sequence < 0
        or type(anchor.anchor_sequence) is not int
        or anchor.anchor_sequence != anchor.anchor_previous_sequence + 1
        or anchor.anchor_sequence < 1
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID")
    return _RequestFacts(
        run_id=run_id,
        plan_sha256=plan_sha,
        phase_name=_PHASE.name,
        phase_sequence=_PHASE.sequence,
        oracle=_PHASE.oracle,
        transport_profile=_PHASE.transport_profile,
        effect_key=effect_key,
        phase_request_sha256=request_sha,
        binding=binding,
        claim_id=claim_id,
        journaled_effect_start_identity_sha256=checked["journaled_effect_start_identity_sha256"],
        journal_binding_sha256=checked["journal_binding_sha256"],
        baseline_plan_binding_sha256=checked["baseline_plan_binding_sha256"],
        anchor_genesis_sequence=anchor.anchor_genesis_sequence,
        anchor_genesis_head_sha256=anchor_genesis_head,
        anchor_previous_sequence=anchor.anchor_previous_sequence,
        anchor_previous_head_sha256=anchor_previous_head,
        anchor_sequence=anchor.anchor_sequence,
        anchor_head_sha256=checked["anchor_head_sha256"],
        anchor_commitment_sha256=checked["anchor_commitment_sha256"],
        anchor_attestation_sha256=checked["anchor_attestation_sha256"],
        anchor_local_previous_record_sha256=anchor_local_previous,
        anchor_local_event_sha256=checked["anchor_local_event_sha256"],
        anchor_occurred_at=_utc(anchor.anchor_occurred_at, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_REQUEST_INVALID"),
    )


def _effect_start_body(facts: _RequestFacts) -> dict[str, object]:
    return {
        "claim_id": facts.claim_id,
        "journaled_effect_start_identity_sha256": facts.journaled_effect_start_identity_sha256,
        "journal_binding_sha256": facts.journal_binding_sha256,
        "baseline_plan_binding_sha256": facts.baseline_plan_binding_sha256,
        "anchor_genesis_sequence": facts.anchor_genesis_sequence,
        "anchor_genesis_head_sha256": facts.anchor_genesis_head_sha256,
        "anchor_previous_sequence": facts.anchor_previous_sequence,
        "anchor_previous_head_sha256": facts.anchor_previous_head_sha256,
        "anchor_sequence": facts.anchor_sequence,
        "anchor_head_sha256": facts.anchor_head_sha256,
        "anchor_commitment_sha256": facts.anchor_commitment_sha256,
        "anchor_attestation_sha256": facts.anchor_attestation_sha256,
        "anchor_local_previous_record_sha256": facts.anchor_local_previous_record_sha256,
        "anchor_local_event_sha256": facts.anchor_local_event_sha256,
        "anchor_occurred_at": _render_timestamp(
            facts.anchor_occurred_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_INTERNAL_INVALID",
        ),
    }


def _capture_body(
    *, capture_id: str, capture_handoff_sha256: str, started_at: datetime
) -> dict[str, object]:
    return {
        "capture_id": capture_id,
        "capture_handoff_sha256": capture_handoff_sha256,
        "capture_started_at": _render_timestamp(
            started_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_INTERNAL_INVALID",
        ),
    }


def _capture_handoff_sha(*, capture_id: str, facts: _RequestFacts, started_at: datetime) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA,
                "kind": "post-effect-strict-ack-capture-handoff",
                "capture_id": capture_id,
                "v4_request": {
                    "run_id": str(facts.run_id),
                    "plan_sha256": facts.plan_sha256,
                    "phase_sequence": facts.phase_sequence,
                    "effect_key": facts.effect_key,
                    "phase_request_sha256": facts.phase_request_sha256,
                },
                "effect_start": _effect_start_body(facts),
                "capture_started_at": _render_timestamp(
                    started_at,
                    code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_INTERNAL_INVALID",
                ),
            }
        )
    ).hexdigest()


def _capture_state(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
) -> _CaptureState:
    _config(config)
    if (
        type(value) is not PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture
        or value._capability is not _CAPTURE_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPTURE_REQUIRED")
    state = _CAPTURE_STATES.get(value)
    if state is None or state.config != config or state.request is not request:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPTURE_REQUIRED")
    facts = _request_facts(request)
    expected = (
        PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA,
        value.capture_id,
        value.capture_handoff_sha256,
        facts.run_id,
        facts.plan_sha256,
        facts.phase_sequence,
        facts.effect_key,
        facts.phase_request_sha256,
        facts.claim_id,
        facts.journaled_effect_start_identity_sha256,
        facts.anchor_sequence,
        facts.anchor_head_sha256,
        state.started_at,
        False,
        False,
        False,
        False,
        False,
    )
    actual = (
        value.schema,
        value.capture_id,
        value.capture_handoff_sha256,
        value.run_id,
        value.plan_sha256,
        value.phase_sequence,
        value.effect_key,
        value.phase_request_sha256,
        value.claim_id,
        value.journaled_effect_start_identity_sha256,
        value.anchor_sequence,
        value.anchor_head_sha256,
        value.capture_started_at,
        value.writer_authorized,
        value.promotion_authorized,
        value.execution_authorized,
        value.full_matrix_authorized,
        value.full_matrix_executed,
    )
    if actual != expected or state.facts != facts:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPTURE_TAMPERED")
    if value.capture_handoff_sha256 != _capture_handoff_sha(
        capture_id=_capture_id(value.capture_id, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPTURE_TAMPERED"),
        facts=facts,
        started_at=state.started_at,
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPTURE_TAMPERED")
    return state


def begin_physical_full_matrix_v4_phase1_post_effect_strict_ack_capture(
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    now: datetime,
) -> PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture:
    """Mint a one-shot post-start capture handoff without any I/O.

    The caller must subsequently create the strict ACK from this handoff.  The
    function intentionally has no parameter for an old ACK, chain, response,
    receipt, runner, or transport.
    """

    _config(config)
    facts = _request_facts(request)
    started_at = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CLOCK_INVALID")
    if started_at < facts.anchor_occurred_at:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPTURE_BEFORE_EFFECT_START")
    capture_id = "v4-p1-post-effect-capture-" + uuid4().hex
    handoff_sha = _capture_handoff_sha(capture_id=capture_id, facts=facts, started_at=started_at)
    result = PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture(
        capture_id=capture_id,
        capture_handoff_sha256=handoff_sha,
        facts=facts,
        capture_started_at=started_at,
        capability=_CAPTURE_CAPABILITY,
    )
    _CAPTURE_STATES[result] = _CaptureState(
        config=config,
        request=request,
        facts=facts,
        started_at=started_at,
    )
    _capture_state(result, config=config, request=request)
    return result


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID")


def _strict_from_pending(value: object) -> _StrictFacts:
    """Project one exact pending Gen2 response; observations are rejected.

    The exact class has no public constructor and originates only after the
    Gen2 adapter has flushed its row in the root transaction.  This module does
    not accept ``Verified...Observation`` because that type is released only
    after a known commit and could be historical external evidence.
    """

    if (
        type(value)
        is not _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit
        or value.outcome != "pending_external_commit"
        # A class-shaped object is not a flushed Gen2 transaction handoff.
        # Retain the Gen2 adapter's private pending capability and exact
        # bound-response handoff; otherwise a caller could manufacture a
        # syntactically valid receipt after a V4 start and relabel it as a
        # local pending commit.
        or value._capability is not _gen2_transaction._PENDING_CAPABILITY
        or value._bound_response is None
        or type(value.instruction)
        is not _bound.PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction
        or type(value.runtime_receipt) is not bytes
        or not 1 <= len(value.runtime_receipt) <= 256 * 1024
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_PENDING_GEN2_REQUIRED")
    instruction = value.instruction
    raw = value.runtime_receipt
    try:
        receipt = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID")
    if type(receipt) is not dict or raw != canonical_json_bytes(receipt):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID")
    signature = receipt.get("signature_base64")
    if type(signature) is not str:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID")
    try:
        decoded_signature = base64.b64decode(signature.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID")
    if len(decoded_signature) != 64:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID")
    try:
        local_commit = _identifier(
            receipt["local_commit_record_id"],
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID",
        )
        local_response = _identifier(
            receipt["local_response_id"],
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID",
        )
        consumption = _identifier(
            receipt["attestation_consumption_id"],
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID",
        )
        committed = _parse_timestamp(
            receipt["committed_at"],
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID",
        )
    except KeyError:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID")
    if consumption != "v2-witness-consume-g2-" + instruction.attestation_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID")
    actual_unsigned = {name: item for name, item in receipt.items() if name != "signature_base64"}
    try:
        expected_unsigned = _bound._runtime_unsigned(
            instruction,
            local_commit_record_id=local_commit,
            local_response_id=local_response,
            attestation_consumption_id=consumption,
            committed_at=committed,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_INVALID"
        ) from exc
    if actual_unsigned != expected_unsigned:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_GEN2_PENDING_MISMATCH")
    runtime_sha = hashlib.sha256(raw).hexdigest()
    observation_payload = {
        "schema": _bound.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
        "instruction": expected_unsigned,
        "runtime_commit_receipt_sha256": runtime_sha,
    }
    return _StrictFacts(
        observation_schema=_bound.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
        observation_sha256=hashlib.sha256(canonical_json_bytes(observation_payload)).hexdigest(),
        runtime_commit_receipt_sha256=runtime_sha,
        runtime_commit_pins_sha256=hashlib.sha256(canonical_json_bytes(expected_unsigned)).hexdigest(),
        instruction_schema=instruction.schema,
        configuration_sha256=instruction.configuration_sha256,
        v2_base_configuration_sha256=instruction.v2_base_configuration_sha256,
        atomic_commit_boundary=instruction.atomic_commit_boundary,
        commit_id=instruction.commit_id,
        v2_base_commit_id=instruction.v2_base_commit_id,
        local_commit_record_id=local_commit,
        local_response_id=local_response,
        attestation_consumption_id=consumption,
        committed_at=committed,
        runtime_commit_pins=expected_unsigned,
    )


def _strict_body(strict: _StrictFacts) -> dict[str, object]:
    return {
        "observation_schema": strict.observation_schema,
        "observation_sha256": strict.observation_sha256,
        "runtime_commit_receipt_sha256": strict.runtime_commit_receipt_sha256,
        "runtime_commit_pins_sha256": strict.runtime_commit_pins_sha256,
        "instruction_schema": strict.instruction_schema,
        "configuration_sha256": strict.configuration_sha256,
        "v2_base_configuration_sha256": strict.v2_base_configuration_sha256,
        "atomic_commit_boundary": strict.atomic_commit_boundary,
        "commit_id": strict.commit_id,
        "v2_base_commit_id": strict.v2_base_commit_id,
        "local_commit_record_id": strict.local_commit_record_id,
        "local_response_id": strict.local_response_id,
        "attestation_consumption_id": strict.attestation_consumption_id,
        "committed_at": _render_timestamp(
            strict.committed_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_INTERNAL_INVALID",
        ),
        "runtime_commit_pins": strict.runtime_commit_pins,
    }


def _unsigned_checkpoint_body(
    *,
    checkpoint_id: str,
    captured_at: datetime,
    config: _ConfigFacts,
    facts: _RequestFacts,
    capture: PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture,
    strict: _StrictFacts,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA,
        "version": _VERSION,
        "status": PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS,
        "checkpoint_id": checkpoint_id,
        "captured_at": _render_timestamp(
            captured_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CLOCK_INVALID",
        ),
        "signer_site": _FI_SITE,
        "signer_key_id": config.signer_key_id,
        "signature_algorithm": _SIGNATURE_ALGORITHM,
        "v4_request": {
            "run_id": str(facts.run_id),
            "plan_sha256": facts.plan_sha256,
            "phase_name": facts.phase_name,
            "phase_sequence": facts.phase_sequence,
            "oracle": facts.oracle,
            "transport_profile": facts.transport_profile,
            "effect_key": facts.effect_key,
            "phase_request_sha256": facts.phase_request_sha256,
            "binding": facts.binding,
        },
        "effect_start": _effect_start_body(facts),
        "capture": _capture_body(
            capture_id=capture.capture_id,
            capture_handoff_sha256=capture.capture_handoff_sha256,
            started_at=capture.capture_started_at,
        ),
        "strict_gen2": _strict_body(strict),
        "strict_ack_post_effect_bound": True,
        "capture_handoff_verified": True,
        "checkpoint_durable": False,
        "phase_completion_evidenced": False,
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
        "direct_fi_to_ir_control": _FORBIDDEN,
        "direct_ir_to_fi_control": _FORBIDDEN,
    }


def _private_key(value: object, *, facts: _ConfigFacts) -> Ed25519PrivateKey:
    if not isinstance(value, Ed25519PrivateKey):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SIGNER_INVALID")
    try:
        raw = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SIGNER_INVALID"
        ) from exc
    if raw != facts.signer_public_key:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SIGNER_MISMATCH")
    return value


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_NONCANONICAL")
        result[key] = value
    return result


def _parse_checkpoint(raw: object, *, config: _ConfigFacts) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_CHECKPOINT_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_BYTES_INVALID")
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_NONCANONICAL")
    if type(parsed) is not dict or raw != canonical_json_bytes(parsed) + b"\n":
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_NONCANONICAL")
    if set(parsed) != _CHECKPOINT_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_FIELDS_INVALID")
    if (
        parsed["schema"] != PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA
        or parsed["version"] != _VERSION
        or parsed["status"] != PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS
        or parsed["signer_site"] != _FI_SITE
        or parsed["signer_key_id"] != config.signer_key_id
        or parsed["signature_algorithm"] != _SIGNATURE_ALGORITHM
        or parsed["direct_fi_to_ir_control"] != _FORBIDDEN
        or parsed["direct_ir_to_fi_control"] != _FORBIDDEN
        or parsed["strict_ack_post_effect_bound"] is not True
        or parsed["capture_handoff_verified"] is not True
        or parsed["checkpoint_durable"] is not False
        or parsed["phase_completion_evidenced"] is not False
        or parsed["writer_authorized"] is not False
        or parsed["promotion_authorized"] is not False
        or parsed["execution_authorized"] is not False
        or parsed["full_matrix_authorized"] is not False
        or parsed["full_matrix_executed"] is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_FIELDS_INVALID")
    _identifier(parsed["checkpoint_id"], code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_FIELDS_INVALID")
    _parse_timestamp(parsed["captured_at"], code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_FIELDS_INVALID")
    signature_text = parsed["signature_base64"]
    if type(signature_text) is not str:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SIGNATURE_INVALID")
    try:
        signature = base64.b64decode(signature_text.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SIGNATURE_INVALID")
    if len(signature) != 64:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SIGNATURE_INVALID")
    unsigned = {name: value for name, value in parsed.items() if name != "signature_base64"}
    try:
        Ed25519PublicKey.from_public_bytes(config.signer_public_key).verify(
            signature,
            _SIGNING_DOMAIN + canonical_json_bytes(unsigned),
        )
    except (InvalidSignature, TypeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SIGNATURE_INVALID")
    return dict(parsed)


def _checkpoint_body_matches(
    *,
    parsed: dict[str, Any],
    facts: _RequestFacts,
    capture: PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture,
    strict: _StrictFacts,
) -> bool:
    request = parsed.get("v4_request")
    effect_start = parsed.get("effect_start")
    capture_body = parsed.get("capture")
    strict_body = parsed.get("strict_gen2")
    if (
        type(request) is not dict
        or type(effect_start) is not dict
        or type(capture_body) is not dict
        or type(strict_body) is not dict
        or set(request) != _V4_REQUEST_FIELDS
        or type(request.get("binding")) is not dict
        or set(request["binding"]) != _BINDING_FIELDS
        or set(effect_start) != _EFFECT_START_FIELDS
        or set(capture_body) != _CAPTURE_FIELDS
        or set(strict_body) != _STRICT_GEN2_FIELDS
    ):
        return False
    expected_request = {
        "run_id": str(facts.run_id),
        "plan_sha256": facts.plan_sha256,
        "phase_name": facts.phase_name,
        "phase_sequence": facts.phase_sequence,
        "oracle": facts.oracle,
        "transport_profile": facts.transport_profile,
        "effect_key": facts.effect_key,
        "phase_request_sha256": facts.phase_request_sha256,
        "binding": facts.binding,
    }
    return (
        request == expected_request
        and effect_start == _effect_start_body(facts)
        and capture_body
        == _capture_body(
            capture_id=capture.capture_id,
            capture_handoff_sha256=capture.capture_handoff_sha256,
            started_at=capture.capture_started_at,
        )
        and strict_body == _strict_body(strict)
    )


def _prepare_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_from_same_root_envelope(
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    capture: PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    fi_checkpoint_private_key: Ed25519PrivateKey,
    now: datetime,
    same_root_envelope_capability: object,
) -> PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint:
    """Quarantined signed-format grammar with no supported live caller.

    Keep this function private: accepting the raw Gen2 pending handoff from an
    arbitrary caller would discard the missing root-transaction provenance.
    The current live-root diagnostic intentionally does not call it, because a
    mutable in-memory association is not a database causal fence.
    """

    if same_root_envelope_capability is not _SAME_ROOT_ENVELOPE_CAPABILITY:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SAME_ROOT_ENVELOPE_REQUIRED")
    config_facts = _config(config)
    state = _capture_state(capture, config=config, request=request)
    if state.same_root_envelope_claimed is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SAME_ROOT_ENVELOPE_REQUIRED")
    if state.consumed:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPTURE_ALREADY_CONSUMED")
    facts = _request_facts(request)
    if facts != state.facts:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPTURE_TAMPERED")
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CLOCK_INVALID")
    if observed < state.started_at:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CLOCK_REGRESSION")
    strict = _strict_from_pending(pending_gen2_commit)
    signer = _private_key(fi_checkpoint_private_key, facts=config_facts)
    checkpoint_id = "v4-p1-post-effect-checkpoint-" + uuid4().hex
    unsigned = _unsigned_checkpoint_body(
        checkpoint_id=checkpoint_id,
        captured_at=observed,
        config=config_facts,
        facts=facts,
        capture=capture,
        strict=strict,
    )
    try:
        signature = signer.sign(_SIGNING_DOMAIN + canonical_json_bytes(unsigned))
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SIGNER_INVALID"
        ) from exc
    canonical = canonical_json_bytes(
        {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}
    ) + b"\n"
    parsed = _parse_checkpoint(canonical, config=config_facts)
    if not _checkpoint_body_matches(parsed=parsed, facts=facts, capture=capture, strict=strict):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_INTERNAL_INVALID")
    result = PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint(
        checkpoint_sha256=hashlib.sha256(canonical).hexdigest(),
        canonical_checkpoint=canonical,
        checkpoint_id=checkpoint_id,
        signer_key_id=config_facts.signer_key_id,
        capture=capture,
        facts=facts,
        strict=strict,
        capability=_PREPARED_CAPABILITY,
    )
    state.consumed = True
    _PREPARED_STATES[result] = _PreparedState(
        config=config,
        capture=capture,
        request=request,
        pending=pending_gen2_commit,
        facts=facts,
        strict=strict,
    )
    require_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint(
        result,
        config=config,
        request=request,
        pending_gen2_commit=pending_gen2_commit,
    )
    return result


def prepare_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint(
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    capture: PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    fi_checkpoint_private_key: Ed25519PrivateKey,
    now: datetime,
) -> PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint:
    """Reject the retired public raw-Pending preparation path.

    A raw pending Gen2 object carries no public proof of the ``AsyncSession``
    root transaction that flushed it.  The default-off P1 same-root envelope
    owns that association and is the only supported route to the private
    preparation grammar.  This compatibility-shaped entry point intentionally
    never inspects, signs, consumes, or projects its inputs.
    """

    del config, request, capture, pending_gen2_commit, fi_checkpoint_private_key, now
    _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SAME_ROOT_ENVELOPE_REQUIRED")


def require_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
) -> PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint:
    """Revalidate one in-memory, still-pending capture without granting success."""

    config_facts = _config(config)
    if (
        type(value)
        is not PreparedPhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint
        or value._capability is not _PREPARED_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPABILITY_REQUIRED")
    state = _PREPARED_STATES.get(value)
    if (
        state is None
        or state.config != config
        or state.request is not request
        or state.pending is not pending_gen2_commit
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPABILITY_REQUIRED")
    capture_state = _capture_state(state.capture, config=config, request=request)
    if capture_state.consumed is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPTURE_TAMPERED")
    facts = _request_facts(request)
    strict = _strict_from_pending(pending_gen2_commit)
    parsed = _parse_checkpoint(value.canonical_checkpoint, config=config_facts)
    if (
        not _checkpoint_body_matches(parsed=parsed, facts=facts, capture=state.capture, strict=strict)
        or hashlib.sha256(value.canonical_checkpoint).hexdigest() != value.checkpoint_sha256
        or value.schema != PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA
        or value.status != PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS
        or value.checkpoint_id != parsed["checkpoint_id"]
        or value.signer_key_id != config_facts.signer_key_id
        or value.capture_id != state.capture.capture_id
        or value.capture_handoff_sha256 != state.capture.capture_handoff_sha256
        or value.run_id != facts.run_id
        or value.plan_sha256 != facts.plan_sha256
        or value.phase_sequence != facts.phase_sequence
        or value.effect_key != facts.effect_key
        or value.phase_request_sha256 != facts.phase_request_sha256
        or value.claim_id != facts.claim_id
        or value.journaled_effect_start_identity_sha256
        != facts.journaled_effect_start_identity_sha256
        or value.journal_binding_sha256 != facts.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != facts.baseline_plan_binding_sha256
        or value.anchor_sequence != facts.anchor_sequence
        or value.anchor_head_sha256 != facts.anchor_head_sha256
        or value.anchor_commitment_sha256 != facts.anchor_commitment_sha256
        or value.anchor_attestation_sha256 != facts.anchor_attestation_sha256
        or value.strict_observation_sha256 != strict.observation_sha256
        or value.strict_runtime_commit_receipt_sha256 != strict.runtime_commit_receipt_sha256
        or value.strict_commit_id != strict.commit_id
        or value.strict_v2_base_commit_id != strict.v2_base_commit_id
        or value.strict_local_commit_record_id != strict.local_commit_record_id
        or value.strict_local_response_id != strict.local_response_id
        or value.strict_attestation_consumption_id != strict.attestation_consumption_id
        or value.strict_committed_at != strict.committed_at
        or value.checkpoint_durable is not False
        or value.strict_ack_post_effect_bound is not True
        or value.capture_handoff_verified is not True
        or value.phase_completion_evidenced is not False
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_TAMPERED")
    return value


def canonical_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
) -> bytes:
    """Return exact signed bytes only after the pending capability revalidates."""

    verified = require_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint(
        value,
        config=config,
        request=request,
        pending_gen2_commit=pending_gen2_commit,
    )
    return verified.canonical_checkpoint


def _project_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_row_values_from_same_root_envelope(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    same_root_envelope_capability: object,
) -> dict[str, object]:
    """Quarantined row grammar with no supported live caller.

    It does no persistence and does not turn a pre-commit result into a phase
    completion.  A later named SQL participant must first establish an audited
    database causal fence, then add the projection beside its already-flushed
    Gen2 row and separately establish the outer commit outcome.
    """

    if same_root_envelope_capability is not _SAME_ROOT_ENVELOPE_CAPABILITY:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SAME_ROOT_ENVELOPE_REQUIRED")
    verified = require_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint(
        value,
        config=config,
        request=request,
        pending_gen2_commit=pending_gen2_commit,
    )
    state = _PREPARED_STATES.get(verified)
    if state is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_CAPABILITY_REQUIRED")
    config_facts = _config(config)
    parsed = _parse_checkpoint(verified.canonical_checkpoint, config=config_facts)
    facts = state.facts
    strict = state.strict
    binding = facts.binding
    attestation = _sha(
        strict.runtime_commit_pins.get("attestation_sha256"),
        code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_INTERNAL_INVALID",
    )
    return {
        # Signed checkpoint envelope.
        "schema": verified.schema,
        "status": verified.status,
        "checkpoint_id": verified.checkpoint_id,
        "checkpoint_sha256": verified.checkpoint_sha256,
        "canonical_checkpoint": verified.canonical_checkpoint,
        "captured_at": _parse_timestamp(
            parsed["captured_at"],
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_INTERNAL_INVALID",
        ),
        "signer_site": _FI_SITE,
        "signer_key_id": verified.signer_key_id,
        # V4 request and complete execution binding.
        "run_id": facts.run_id,
        "plan_sha256": facts.plan_sha256,
        "phase_name": facts.phase_name,
        "phase_sequence": facts.phase_sequence,
        "phase_oracle": facts.oracle,
        "transport_profile": facts.transport_profile,
        "effect_key": facts.effect_key,
        "phase_request_sha256": facts.phase_request_sha256,
        "readiness_binding_sha256": binding["readiness_binding_sha256"],
        "route_commitment_sha256": binding["route_commitment_sha256"],
        "four_role_binding_sha256": binding["four_role_binding_sha256"],
        "writer_holder_site": binding["writer_holder_site"],
        "writer_epoch": binding["writer_epoch"],
        "writer_lease_id": binding["writer_lease_id"],
        "witnessed_term_proof_sha256": binding["witnessed_term_proof_sha256"],
        "source_site": binding["source_site"],
        "destination_site": binding["destination_site"],
        "roundtrip_attestation_sha256": binding["roundtrip_attestation_sha256"],
        "roundtrip_configuration_sha256": binding["roundtrip_configuration_sha256"],
        "witness_transition_id": binding["witness_transition_id"],
        "witness_sequence": binding["witness_sequence"],
        # Exact post-journal effect-start anchor.
        "claim_id": facts.claim_id,
        "journaled_effect_start_identity_sha256": facts.journaled_effect_start_identity_sha256,
        "journal_binding_sha256": facts.journal_binding_sha256,
        "baseline_plan_binding_sha256": facts.baseline_plan_binding_sha256,
        "anchor_genesis_sequence": facts.anchor_genesis_sequence,
        "anchor_genesis_head_sha256": facts.anchor_genesis_head_sha256,
        "anchor_previous_sequence": facts.anchor_previous_sequence,
        "anchor_previous_head_sha256": facts.anchor_previous_head_sha256,
        "anchor_sequence": facts.anchor_sequence,
        "anchor_head_sha256": facts.anchor_head_sha256,
        "anchor_commitment_sha256": facts.anchor_commitment_sha256,
        "anchor_attestation_sha256": facts.anchor_attestation_sha256,
        "anchor_local_previous_record_sha256": facts.anchor_local_previous_record_sha256,
        "anchor_local_event_sha256": facts.anchor_local_event_sha256,
        "anchor_occurred_at": facts.anchor_occurred_at,
        # One-shot local handoff.
        "capture_id": verified.capture_id,
        "capture_handoff_sha256": verified.capture_handoff_sha256,
        "capture_started_at": state.capture.capture_started_at,
        # Exact Gen2 pending-commit projection and raw signed receipt.
        "strict_observation_schema": strict.observation_schema,
        "strict_observation_sha256": strict.observation_sha256,
        "strict_runtime_commit_receipt_sha256": strict.runtime_commit_receipt_sha256,
        "strict_runtime_commit_pins_sha256": strict.runtime_commit_pins_sha256,
        "strict_instruction_schema": strict.instruction_schema,
        "strict_configuration_sha256": strict.configuration_sha256,
        "strict_v2_base_configuration_sha256": strict.v2_base_configuration_sha256,
        "strict_atomic_commit_boundary": strict.atomic_commit_boundary,
        "strict_gen2_commit_id": strict.commit_id,
        "strict_v2_base_commit_id": strict.v2_base_commit_id,
        "strict_attestation_sha256": attestation,
        "strict_local_commit_record_id": strict.local_commit_record_id,
        "strict_local_response_id": strict.local_response_id,
        "strict_attestation_consumption_id": strict.attestation_consumption_id,
        "strict_committed_at": strict.committed_at,
        "strict_canonical_runtime_commit_receipt": pending_gen2_commit.runtime_receipt,
        # Permanently non-authorizing capture semantics.
        "strict_ack_post_effect_bound": True,
        "capture_handoff_verified": True,
        "checkpoint_durable": False,
        "phase_completion_evidenced": False,
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
        "direct_fi_to_ir_control": _FORBIDDEN,
        "direct_ir_to_fi_control": _FORBIDDEN,
    }


def project_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_row_values(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    pending_gen2_commit: _gen2_transaction.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
) -> dict[str, object]:
    """Reject the retired public raw-Pending row-projection path.

    Projection is intentionally not a portable serializer: it is released only
    while the default-off same-root envelope can prove that the exact root
    transaction remains live.  This entry point is retained solely to produce
    a stable typed refusal for legacy callers.
    """

    del value, config, request, pending_gen2_commit
    _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SAME_ROOT_ENVELOPE_REQUIRED")
