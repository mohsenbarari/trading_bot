"""Fail-closed evidence contract for Full-Matrix V4 phase-2 FI retirement.

This is deliberately a *verification grammar*, not a fencer.  It never
stops a service, revokes a database credential, changes a firewall, talks to
Witness, contacts Object Storage, opens a socket, invokes a process, or starts
the next phase.  In particular, it must not turn the historical FI
``self-fence`` receipt (which is only a correlation pin) into authority.

The concrete phase-2 owner must produce three independently signed canonical
receipts after it has done the real work:

* a root-owned FI executor evidence receipt;
* an independently keyed observer evidence receipt; and
* a Witness durable anti-replay admission receipt.

All three repeat one exact V4 effect-start identity and the exact *former* FI
term.  The Witness replay key is derived from that identity and predecessor
term -- not from a caller-selected nonce -- so changing a receipt ID cannot
turn the same predecessor/effect into a second retirement operation.

Verification here is evidence-only.  A positive result is never a writer,
promotion, traffic, external-effect, execution, or Full-Matrix permit.  A
future root-owned P2 runtime still has to implement and attest the actual
server-side database/credential/service fence and durable Witness ledger.
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


__all__ = (
    "DEFAULT_RETIRED_FI_PREDECESSOR_FENCE_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_ANTI_REPLAY_NAMESPACE",
    "PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_EXECUTOR_RECEIPT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_OBSERVER_RECEIPT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_STATUS",
    "PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_WITNESS_ADMISSION_SCHEMA",
    "PhysicalFullMatrixV4EffectStartAnchorPin",
    "PhysicalFullMatrixV4EffectStartPin",
    "RetiredFiPredecessorFenceAntiReplayPolicy",
    "RetiredFiPredecessorFenceError",
    "RetiredFiPredecessorFenceEvidencePins",
    "RetiredFiPredecessorFenceTermPin",
    "RetiredFiPredecessorFenceVerificationConfig",
    "VerifiedRetiredFiPredecessorFence",
    "derive_retired_fi_predecessor_fence_replay_key_sha256",
    "require_verified_retired_fi_predecessor_fence",
    "verify_retired_fi_predecessor_fence",
)


PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-retired-fi-predecessor-fence-v1"
)
PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_EXECUTOR_RECEIPT_SCHEMA = (
    PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_SCHEMA
    + "/fi-root-executor-receipt-v1"
)
PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_OBSERVER_RECEIPT_SCHEMA = (
    PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_SCHEMA
    + "/independent-observer-receipt-v1"
)
PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_WITNESS_ADMISSION_SCHEMA = (
    PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_SCHEMA
    + "/witness-durable-anti-replay-admission-v1"
)
PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_STATUS = (
    "fi-predecessor-retired-evidence-only"
)
PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_ANTI_REPLAY_NAMESPACE = (
    "physical-full-matrix-v4-fi-predecessor-retirement"
)

DEFAULT_RETIRED_FI_PREDECESSOR_FENCE_MAX_EVIDENCE_AGE_SECONDS = 90
_MAX_EVIDENCE_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_MAX_RETIREMENT_WINDOW_SECONDS = 300
_MAX_WIRE_BYTES = 128 * 1024
_VERSION = 1
_ZERO_SHA256 = "0" * 64
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)

_EXECUTOR_DOMAIN = (
    PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_EXECUTOR_RECEIPT_SCHEMA
    + "\x00"
).encode("ascii")
_OBSERVER_DOMAIN = (
    PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_OBSERVER_RECEIPT_SCHEMA
    + "\x00"
).encode("ascii")
_WITNESS_DOMAIN = (
    PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_WITNESS_ADMISSION_SCHEMA
    + "\x00"
).encode("ascii")

_PHASE_TWO = PHYSICAL_FULL_MATRIX_V4_PHASES[1]
_RETIRED_FI_MODE = "server-side-fi-writer-retired-v1"
_EXECUTOR_KIND = "fi-root-fence-executor-evidence"
_OBSERVER_KIND = "fi-independent-fence-observer-evidence"
_WITNESS_KIND = "witness-durable-anti-replay-admission"
_EXECUTOR_ROLE = "fi-root-fence-executor"
_OBSERVER_ROLE = "fi-independent-fence-observer"
_WITNESS_ROLE = "witness-durable-anti-replay-ledger"
_ANTI_REPLAY_MODE = "witness-durable-single-use-admission-v1"

_FENCE_BINDING_FIELDS = frozenset(
    {
        "schema",
        "version",
        "status",
        "retirement_mode",
        "fence_id",
        "fence_nonce",
        "retired_at",
        "expires_at",
        "effect_start",
        "effect_start_anchor",
        "predecessor_term",
        "evidence_pins",
    }
)
_EXECUTOR_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "signer_role",
        "fence_binding",
        "fence_binding_sha256",
        "signature_base64",
    }
)
_OBSERVER_RECEIPT_FIELDS = _EXECUTOR_RECEIPT_FIELDS
_WITNESS_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "signer_role",
        "fence_id",
        "fence_nonce",
        "fence_binding_sha256",
        "replay_key_sha256",
        "anti_replay_namespace",
        "anti_replay_mode",
        "witness_ledger_scope_sha256",
        "admission_id",
        "admission_nonce",
        "admitted_at",
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
_EFFECT_START_ANCHOR_FIELDS = frozenset(
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
_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
    }
)
_EVIDENCE_PIN_FIELDS = frozenset(
    {
        "executor_installation_attestation_sha256",
        "executor_scope_policy_sha256",
        "executor_fence_evidence_sha256",
        "observer_installation_attestation_sha256",
        "observer_scope_policy_sha256",
        "observer_fence_evidence_sha256",
    }
)
_CAPABILITY = object()


class RetiredFiPredecessorFenceError(ValueError):
    """The P2 retirement evidence is absent, stale, noncanonical, or unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise RetiredFiPredecessorFenceError(code)


@dataclass(frozen=True)
class RetiredFiPredecessorFenceTermPin:
    """The exact former FI term; this is deliberately not a live-term proof."""

    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4EffectStartPin:
    """Portable field projection of one V4 phase-2 journaled effect start.

    The original driver authority remains process-local.  This value repeats
    only the exact public correlation fields which the real executor and
    Witness must independently sign into their own evidence grammars.
    """

    run_id: UUID
    plan_sha256: str
    phase: PhysicalFullMatrixV4ExecutionPhase
    effect_key: str
    phase_request_sha256: str
    binding: PhysicalFullMatrixV4ExecutionBinding
    claim_id: str
    journaled_effect_start_identity_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4EffectStartAnchorPin:
    """Exact immutable Witness-anchor pins for one portable V4 start proof.

    The source driver's opaque anchor-proof capability stays process-local.
    This typed projection gives a cross-host P2 evidence grammar every
    immutable Witness/journal pin it needs without treating the projection as
    an execution or Witness authority.
    """

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
class RetiredFiPredecessorFenceEvidencePins:
    """Hashes of independently produced executor and observer evidence.

    These hashes bind installation attestation, covered enforcement policy,
    and the post-fence evidence projection.  They are not interpreted as a
    substitute for the real root-owned enforcement runtime.
    """

    executor_installation_attestation_sha256: str
    executor_scope_policy_sha256: str
    executor_fence_evidence_sha256: str
    observer_installation_attestation_sha256: str
    observer_scope_policy_sha256: str
    observer_fence_evidence_sha256: str


@dataclass(frozen=True)
class RetiredFiPredecessorFenceAntiReplayPolicy:
    """Pinned identity of the real Witness ledger which admits P2 once."""

    anti_replay_namespace: str
    witness_ledger_scope_sha256: str


@dataclass(frozen=True)
class RetiredFiPredecessorFenceVerificationConfig:
    """Default-off verification policy for one exact phase-2 effect.

    All three public keys must be distinct.  The configured Witness key must
    belong to a durable single-use admission service in a future deployment;
    this pure module only verifies its signed projection.
    """

    expected_effect_start: PhysicalFullMatrixV4EffectStartPin | None = None
    expected_effect_start_anchor: PhysicalFullMatrixV4EffectStartAnchorPin | None = None
    expected_predecessor_term: RetiredFiPredecessorFenceTermPin | None = None
    expected_evidence_pins: RetiredFiPredecessorFenceEvidencePins | None = None
    expected_anti_replay_policy: RetiredFiPredecessorFenceAntiReplayPolicy | None = None
    executor_signer_public_key: bytes = b""
    observer_signer_public_key: bytes = b""
    witness_anti_replay_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_RETIRED_FI_PREDECESSOR_FENCE_MAX_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True, eq=False, init=False)
class VerifiedRetiredFiPredecessorFence:
    """Opaque, fresh P2 retirement evidence -- never an operational permit."""

    canonical_executor_receipt: bytes
    canonical_observer_receipt: bytes
    canonical_witness_admission_receipt: bytes
    executor_receipt_sha256: str
    observer_receipt_sha256: str
    witness_admission_receipt_sha256: str
    effect_start: PhysicalFullMatrixV4EffectStartPin
    effect_start_anchor: PhysicalFullMatrixV4EffectStartAnchorPin
    predecessor_term: RetiredFiPredecessorFenceTermPin
    evidence_pins: RetiredFiPredecessorFenceEvidencePins
    anti_replay_policy: RetiredFiPredecessorFenceAntiReplayPolicy
    fence_id: str
    fence_nonce: str
    replay_key_sha256: str
    retired_at: datetime
    expires_at: datetime
    admission_id: str
    admission_nonce: str
    admitted_at: datetime
    witness_ledger_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    external_effect_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        canonical_executor_receipt: bytes,
        canonical_observer_receipt: bytes,
        canonical_witness_admission_receipt: bytes,
        executor_receipt_sha256: str,
        observer_receipt_sha256: str,
        witness_admission_receipt_sha256: str,
        effect_start: PhysicalFullMatrixV4EffectStartPin,
        effect_start_anchor: PhysicalFullMatrixV4EffectStartAnchorPin,
        predecessor_term: RetiredFiPredecessorFenceTermPin,
        evidence_pins: RetiredFiPredecessorFenceEvidencePins,
        anti_replay_policy: RetiredFiPredecessorFenceAntiReplayPolicy,
        fence_id: str,
        fence_nonce: str,
        replay_key_sha256: str,
        retired_at: datetime,
        expires_at: datetime,
        admission_id: str,
        admission_nonce: str,
        admitted_at: datetime,
        witness_ledger_sequence: int,
        witness_ledger_entry_sha256: str,
        witness_ledger_previous_head_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("canonical_executor_receipt", canonical_executor_receipt),
            ("canonical_observer_receipt", canonical_observer_receipt),
            ("canonical_witness_admission_receipt", canonical_witness_admission_receipt),
            ("executor_receipt_sha256", executor_receipt_sha256),
            ("observer_receipt_sha256", observer_receipt_sha256),
            ("witness_admission_receipt_sha256", witness_admission_receipt_sha256),
            ("effect_start", effect_start),
            ("effect_start_anchor", effect_start_anchor),
            ("predecessor_term", predecessor_term),
            ("evidence_pins", evidence_pins),
            ("anti_replay_policy", anti_replay_policy),
            ("fence_id", fence_id),
            ("fence_nonce", fence_nonce),
            ("replay_key_sha256", replay_key_sha256),
            ("retired_at", retired_at),
            ("expires_at", expires_at),
            ("admission_id", admission_id),
            ("admission_nonce", admission_nonce),
            ("admitted_at", admitted_at),
            ("witness_ledger_sequence", witness_ledger_sequence),
            ("witness_ledger_entry_sha256", witness_ledger_entry_sha256),
            ("witness_ledger_previous_head_sha256", witness_ledger_previous_head_sha256),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("traffic_switch_authorized", False),
            ("external_effect_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _ConfigFacts:
    effect_start: PhysicalFullMatrixV4EffectStartPin
    effect_start_anchor: PhysicalFullMatrixV4EffectStartAnchorPin
    predecessor_term: RetiredFiPredecessorFenceTermPin
    evidence_pins: RetiredFiPredecessorFenceEvidencePins
    anti_replay_policy: RetiredFiPredecessorFenceAntiReplayPolicy
    executor_key: bytes
    observer_key: bytes
    witness_key: bytes
    maximum_age_seconds: int


@dataclass(frozen=True)
class _FenceFacts:
    binding_mapping: dict[str, Any]
    binding_sha256: str
    effect_start: PhysicalFullMatrixV4EffectStartPin
    effect_start_anchor: PhysicalFullMatrixV4EffectStartAnchorPin
    predecessor_term: RetiredFiPredecessorFenceTermPin
    evidence_pins: RetiredFiPredecessorFenceEvidencePins
    fence_id: str
    fence_nonce: str
    retired_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class _WitnessFacts:
    replay_key_sha256: str
    admission_id: str
    admission_nonce: str
    admitted_at: datetime
    witness_ledger_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str


@dataclass(frozen=True)
class _VerifiedState:
    executor_receipt: bytes
    observer_receipt: bytes
    witness_receipt: bytes


_VERIFIED_STATES: WeakKeyDictionary[VerifiedRetiredFiPredecessorFence, _VerifiedState] = (
    WeakKeyDictionary()
)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise RetiredFiPredecessorFenceError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("RETIRED_FI_PREDECESSOR_FENCE_WIRE_INVALID")
        result[key] = value
    return result


def _parse_canonical_mapping(
    raw: object,
    *,
    fields: frozenset[str],
    code: str,
) -> tuple[dict[str, Any], bytes]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_WIRE_BYTES:
        _fail(code)
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RetiredFiPredecessorFenceError):
        _fail(code)
    if type(parsed) is not dict or set(parsed) != fields:
        _fail(code)
    try:
        if _canonical(parsed, code=code) != raw:
            _fail(code)
    except RetiredFiPredecessorFenceError:
        _fail(code)
    return parsed, raw


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
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
    return _utc(value, code="RETIRED_FI_PREDECESSOR_FENCE_TIME_INVALID").isoformat().replace(
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
    *,
    signer_public_key: bytes,
    domain: bytes,
    unsigned: Mapping[str, Any],
    signature_base64: object,
    code: str,
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
        Ed25519PublicKey.from_public_bytes(signer_public_key).verify(
            signature, domain + _canonical(dict(unsigned), code=code)
        )
    except (InvalidSignature, ValueError, RetiredFiPredecessorFenceError):
        _fail(code)


def _phase_mapping(value: object, *, code: str) -> tuple[PhysicalFullMatrixV4ExecutionPhase, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4ExecutionPhase:
        _fail(code)
    phase = value
    if (
        phase.sequence != _PHASE_TWO.sequence
        or phase.name != _PHASE_TWO.name
        or phase.oracle != _PHASE_TWO.oracle
        or phase.destructive is not True
        or phase.transport_profile != _PHASE_TWO.transport_profile
    ):
        _fail(code)
    return phase, {
        "sequence": phase.sequence,
        "name": phase.name,
        "oracle": phase.oracle,
        "destructive": True,
        "transport_profile": phase.transport_profile,
    }


def _binding_mapping(
    value: object,
    *,
    code: str,
) -> tuple[PhysicalFullMatrixV4ExecutionBinding, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4ExecutionBinding:
        _fail(code)
    binding = value
    if (
        type(binding.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(binding.campaign_id) is None
        or type(binding.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(binding.release_sha) is None
        or binding.writer_holder_site != "webapp_fi"
        or binding.source_site != "webapp_fi"
        or binding.destination_site != "webapp_ir"
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
        _sha256(item, code=code)
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


def _term_mapping(
    value: object,
    *,
    code: str,
) -> tuple[RetiredFiPredecessorFenceTermPin, dict[str, Any]]:
    if type(value) is not RetiredFiPredecessorFenceTermPin:
        _fail(code)
    term = value
    if (
        term.holder_site != "webapp_fi"
        or type(term.writer_epoch) is not int
        or term.writer_epoch < 1
        or LEASE_ID_RE.fullmatch(term.writer_lease_id) is None
        or _IDENTIFIER_RE.fullmatch(term.witness_transition_id) is None
    ):
        _fail(code)
    _sha256(term.witnessed_term_proof_sha256, code=code)
    return term, {
        "holder_site": term.holder_site,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": term.witnessed_term_proof_sha256,
    }


def _evidence_pins_mapping(
    value: object,
    *,
    code: str,
) -> tuple[RetiredFiPredecessorFenceEvidencePins, dict[str, Any]]:
    if type(value) is not RetiredFiPredecessorFenceEvidencePins:
        _fail(code)
    pins = value
    for item in (
        pins.executor_installation_attestation_sha256,
        pins.executor_scope_policy_sha256,
        pins.executor_fence_evidence_sha256,
        pins.observer_installation_attestation_sha256,
        pins.observer_scope_policy_sha256,
        pins.observer_fence_evidence_sha256,
    ):
        _sha256(item, code=code)
    # A shared policy digest can be intentional, but independent installation
    # and post-action evidence must never collapse to the executor's hashes.
    if (
        pins.executor_installation_attestation_sha256
        == pins.observer_installation_attestation_sha256
        or pins.executor_fence_evidence_sha256 == pins.observer_fence_evidence_sha256
    ):
        _fail(code)
    return pins, {
        "executor_installation_attestation_sha256": pins.executor_installation_attestation_sha256,
        "executor_scope_policy_sha256": pins.executor_scope_policy_sha256,
        "executor_fence_evidence_sha256": pins.executor_fence_evidence_sha256,
        "observer_installation_attestation_sha256": pins.observer_installation_attestation_sha256,
        "observer_scope_policy_sha256": pins.observer_scope_policy_sha256,
        "observer_fence_evidence_sha256": pins.observer_fence_evidence_sha256,
    }


def _anti_replay_policy(
    value: object,
    *,
    code: str,
) -> RetiredFiPredecessorFenceAntiReplayPolicy:
    if type(value) is not RetiredFiPredecessorFenceAntiReplayPolicy:
        _fail(code)
    if (
        value.anti_replay_namespace
        != PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_ANTI_REPLAY_NAMESPACE
    ):
        _fail(code)
    _sha256(value.witness_ledger_scope_sha256, code=code)
    return value


def _effect_start_mapping(
    value: object,
    *,
    code: str,
) -> tuple[PhysicalFullMatrixV4EffectStartPin, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4EffectStartPin:
        _fail(code)
    pin = value
    if type(pin.run_id) is not UUID or pin.run_id.int == 0:
        _fail(code)
    _sha256(pin.plan_sha256, code=code)
    phase, phase_mapping = _phase_mapping(pin.phase, code=code)
    _sha256(pin.effect_key, code=code)
    _sha256(pin.phase_request_sha256, code=code)
    binding, binding_mapping = _binding_mapping(pin.binding, code=code)
    _identifier(pin.claim_id, code=code)
    identity = _sha256(pin.journaled_effect_start_identity_sha256, code=code)
    try:
        derived_identity = derive_physical_full_matrix_v4_effect_start_identity_sha256(
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
    if identity != derived_identity:
        _fail(code)
    return pin, {
        "run_id": str(pin.run_id),
        "plan_sha256": pin.plan_sha256,
        "phase": phase_mapping,
        "effect_key": pin.effect_key,
        "phase_request_sha256": pin.phase_request_sha256,
        "binding": binding_mapping,
        "claim_id": pin.claim_id,
        "journaled_effect_start_identity_sha256": pin.journaled_effect_start_identity_sha256,
    }


def _effect_start_anchor_mapping(
    value: object,
    *,
    code: str,
) -> tuple[PhysicalFullMatrixV4EffectStartAnchorPin, dict[str, Any]]:
    if type(value) is not PhysicalFullMatrixV4EffectStartAnchorPin:
        _fail(code)
    pin = value
    if pin.schema != PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA:
        _fail(code)
    embedded_effect_start = _anchor_effect_start(pin, code=code)
    _, embedded_effect_mapping = _effect_start_mapping(
        embedded_effect_start, code=code
    )
    for item in (
        pin.journal_binding_sha256,
        pin.baseline_plan_binding_sha256,
        pin.anchor_head_sha256,
        pin.anchor_commitment_sha256,
        pin.anchor_attestation_sha256,
        pin.anchor_local_event_sha256,
    ):
        _sha256(item, code=code)
    genesis_head = _sha256(pin.anchor_genesis_head_sha256, code=code, permit_zero=True)
    previous_head = _sha256(pin.anchor_previous_head_sha256, code=code, permit_zero=True)
    local_previous = _sha256(
        pin.anchor_local_previous_record_sha256, code=code, permit_zero=True
    )
    if (
        type(pin.anchor_genesis_sequence) is not int
        or pin.anchor_genesis_sequence < 0
        or type(pin.anchor_previous_sequence) is not int
        or pin.anchor_previous_sequence < pin.anchor_genesis_sequence
        or type(pin.anchor_sequence) is not int
        or pin.anchor_sequence != pin.anchor_previous_sequence + 1
        or (
            pin.anchor_previous_sequence == pin.anchor_genesis_sequence
            and previous_head != genesis_head
        )
    ):
        _fail(code)
    occurred_at = _utc(pin.anchor_occurred_at, code=code)
    return pin, {
        "schema": pin.schema,
        **embedded_effect_mapping,
        "journal_binding_sha256": pin.journal_binding_sha256,
        "baseline_plan_binding_sha256": pin.baseline_plan_binding_sha256,
        "anchor_genesis_sequence": pin.anchor_genesis_sequence,
        "anchor_genesis_head_sha256": genesis_head,
        "anchor_previous_sequence": pin.anchor_previous_sequence,
        "anchor_previous_head_sha256": previous_head,
        "anchor_sequence": pin.anchor_sequence,
        "anchor_head_sha256": pin.anchor_head_sha256,
        "anchor_commitment_sha256": pin.anchor_commitment_sha256,
        "anchor_attestation_sha256": pin.anchor_attestation_sha256,
        "anchor_local_previous_record_sha256": local_previous,
        "anchor_local_event_sha256": pin.anchor_local_event_sha256,
        "anchor_occurred_at": _render_timestamp(occurred_at),
    }


def _anchor_effect_start(
    value: PhysicalFullMatrixV4EffectStartAnchorPin,
    *,
    code: str,
) -> PhysicalFullMatrixV4EffectStartPin:
    if type(value) is not PhysicalFullMatrixV4EffectStartAnchorPin:
        _fail(code)
    try:
        return PhysicalFullMatrixV4EffectStartPin(
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


def _term_matches_effect_start(
    term: RetiredFiPredecessorFenceTermPin,
    effect_start: PhysicalFullMatrixV4EffectStartPin,
) -> bool:
    binding = effect_start.binding
    return (
        term.holder_site == binding.writer_holder_site
        and term.writer_epoch == binding.writer_epoch
        and term.writer_lease_id == binding.writer_lease_id
        and term.witness_transition_id == binding.witness_transition_id
        and term.witnessed_term_proof_sha256 == binding.witnessed_term_proof_sha256
    )


def _config(value: object) -> _ConfigFacts:
    if type(value) is not RetiredFiPredecessorFenceVerificationConfig:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_DISABLED")
    effect_start, _ = _effect_start_mapping(
        value.expected_effect_start,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    )
    effect_start_anchor, _ = _effect_start_anchor_mapping(
        value.expected_effect_start_anchor,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    )
    if _anchor_effect_start(
        effect_start_anchor,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    ) != effect_start:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_CONFIG_ANCHOR_EFFECT_MISMATCH")
    predecessor, _ = _term_mapping(
        value.expected_predecessor_term,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    )
    if not _term_matches_effect_start(predecessor, effect_start):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_CONFIG_PREDECESSOR_MISMATCH")
    evidence_pins, _ = _evidence_pins_mapping(
        value.expected_evidence_pins,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    )
    anti_replay_policy = _anti_replay_policy(
        value.expected_anti_replay_policy,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    )
    executor = _public_key(
        value.executor_signer_public_key,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    )
    observer = _public_key(
        value.observer_signer_public_key,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    )
    witness = _public_key(
        value.witness_anti_replay_signer_public_key,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    )
    if len({executor, observer, witness}) != 3:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_CONFIG_SIGNER_SEPARATION_REQUIRED")
    maximum = _positive(
        value.maximum_evidence_age_seconds,
        maximum=_MAX_EVIDENCE_AGE_SECONDS,
        code="RETIRED_FI_PREDECESSOR_FENCE_CONFIG_INVALID",
    )
    return _ConfigFacts(
        effect_start=effect_start,
        effect_start_anchor=effect_start_anchor,
        predecessor_term=predecessor,
        evidence_pins=evidence_pins,
        anti_replay_policy=anti_replay_policy,
        executor_key=executor,
        observer_key=observer,
        witness_key=witness,
        maximum_age_seconds=maximum,
    )


def _binding_from_mapping(
    value: object,
    *,
    code: str,
) -> PhysicalFullMatrixV4ExecutionBinding:
    mapping = _exact_mapping(value, fields=_BINDING_FIELDS, code=code)
    try:
        binding = PhysicalFullMatrixV4ExecutionBinding(
            campaign_id=mapping["campaign_id"],
            release_sha=mapping["release_sha"],
            readiness_binding_sha256=mapping["readiness_binding_sha256"],
            route_commitment_sha256=mapping["route_commitment_sha256"],
            four_role_binding_sha256=mapping["four_role_binding_sha256"],
            writer_holder_site=mapping["writer_holder_site"],
            writer_epoch=mapping["writer_epoch"],
            writer_lease_id=mapping["writer_lease_id"],
            witnessed_term_proof_sha256=mapping["witnessed_term_proof_sha256"],
            source_site=mapping["source_site"],
            destination_site=mapping["destination_site"],
            roundtrip_attestation_sha256=mapping["roundtrip_attestation_sha256"],
            roundtrip_configuration_sha256=mapping["roundtrip_configuration_sha256"],
            witness_transition_id=mapping["witness_transition_id"],
            witness_sequence=mapping["witness_sequence"],
        )
    except (KeyError, TypeError):
        _fail(code)
    checked, canonical = _binding_mapping(binding, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _phase_from_mapping(value: object, *, code: str) -> PhysicalFullMatrixV4ExecutionPhase:
    mapping = _exact_mapping(value, fields=_PHASE_FIELDS, code=code)
    try:
        phase = PhysicalFullMatrixV4ExecutionPhase(
            sequence=mapping["sequence"],
            name=mapping["name"],
            oracle=mapping["oracle"],
            destructive=mapping["destructive"],
            transport_profile=mapping["transport_profile"],
        )
    except (KeyError, TypeError):
        _fail(code)
    checked, canonical = _phase_mapping(phase, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _effect_start_from_mapping(value: object, *, code: str) -> PhysicalFullMatrixV4EffectStartPin:
    mapping = _exact_mapping(value, fields=_EFFECT_START_FIELDS, code=code)
    try:
        run_id = UUID(mapping["run_id"])
    except (AttributeError, TypeError, ValueError):
        _fail(code)
    try:
        pin = PhysicalFullMatrixV4EffectStartPin(
            run_id=run_id,
            plan_sha256=mapping["plan_sha256"],
            phase=_phase_from_mapping(mapping["phase"], code=code),
            effect_key=mapping["effect_key"],
            phase_request_sha256=mapping["phase_request_sha256"],
            binding=_binding_from_mapping(mapping["binding"], code=code),
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


def _effect_start_anchor_from_mapping(
    value: object,
    *,
    code: str,
) -> PhysicalFullMatrixV4EffectStartAnchorPin:
    mapping = _exact_mapping(value, fields=_EFFECT_START_ANCHOR_FIELDS, code=code)
    try:
        run_id = UUID(mapping["run_id"])
    except (AttributeError, TypeError, ValueError):
        _fail(code)
    try:
        pin = PhysicalFullMatrixV4EffectStartAnchorPin(
            schema=mapping["schema"],
            run_id=run_id,
            plan_sha256=mapping["plan_sha256"],
            phase=_phase_from_mapping(mapping["phase"], code=code),
            effect_key=mapping["effect_key"],
            phase_request_sha256=mapping["phase_request_sha256"],
            binding=_binding_from_mapping(mapping["binding"], code=code),
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
    checked, canonical = _effect_start_anchor_mapping(pin, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _term_from_mapping(value: object, *, code: str) -> RetiredFiPredecessorFenceTermPin:
    mapping = _exact_mapping(value, fields=_TERM_FIELDS, code=code)
    try:
        term = RetiredFiPredecessorFenceTermPin(
            holder_site=mapping["holder_site"],
            writer_epoch=mapping["writer_epoch"],
            writer_lease_id=mapping["writer_lease_id"],
            witness_transition_id=mapping["witness_transition_id"],
            witnessed_term_proof_sha256=mapping["witnessed_term_proof_sha256"],
        )
    except (KeyError, TypeError):
        _fail(code)
    checked, canonical = _term_mapping(term, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _evidence_pins_from_mapping(
    value: object,
    *,
    code: str,
) -> RetiredFiPredecessorFenceEvidencePins:
    mapping = _exact_mapping(value, fields=_EVIDENCE_PIN_FIELDS, code=code)
    try:
        pins = RetiredFiPredecessorFenceEvidencePins(
            executor_installation_attestation_sha256=mapping[
                "executor_installation_attestation_sha256"
            ],
            executor_scope_policy_sha256=mapping["executor_scope_policy_sha256"],
            executor_fence_evidence_sha256=mapping["executor_fence_evidence_sha256"],
            observer_installation_attestation_sha256=mapping[
                "observer_installation_attestation_sha256"
            ],
            observer_scope_policy_sha256=mapping["observer_scope_policy_sha256"],
            observer_fence_evidence_sha256=mapping["observer_fence_evidence_sha256"],
        )
    except (KeyError, TypeError):
        _fail(code)
    checked, canonical = _evidence_pins_mapping(pins, code=code)
    if mapping != canonical:
        _fail(code)
    return checked


def _fence_binding_from_mapping(
    value: object,
    *,
    facts: _ConfigFacts,
    now: datetime,
    code: str,
) -> _FenceFacts:
    mapping = _exact_mapping(value, fields=_FENCE_BINDING_FIELDS, code=code)
    if (
        mapping["schema"] != PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_SCHEMA
        or mapping["version"] != _VERSION
        or mapping["status"] != PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_STATUS
        or mapping["retirement_mode"] != _RETIRED_FI_MODE
    ):
        _fail(code)
    fence_id = _identifier(mapping["fence_id"], code=code)
    fence_nonce = _nonce(mapping["fence_nonce"], code=code)
    retired_at = _timestamp(mapping["retired_at"], code=code)
    expires_at = _timestamp(mapping["expires_at"], code=code)
    if (
        expires_at <= retired_at
        or expires_at - retired_at > timedelta(seconds=_MAX_RETIREMENT_WINDOW_SECONDS)
        or retired_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or now - retired_at > timedelta(seconds=facts.maximum_age_seconds)
        or expires_at <= now
    ):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_STALE_OR_EXPIRED")
    effect_start = _effect_start_from_mapping(mapping["effect_start"], code=code)
    effect_start_anchor = _effect_start_anchor_from_mapping(
        mapping["effect_start_anchor"], code=code
    )
    predecessor = _term_from_mapping(mapping["predecessor_term"], code=code)
    evidence_pins = _evidence_pins_from_mapping(mapping["evidence_pins"], code=code)
    if (
        effect_start != facts.effect_start
        or effect_start_anchor != facts.effect_start_anchor
        or _anchor_effect_start(effect_start_anchor, code=code) != effect_start
        or predecessor != facts.predecessor_term
        or evidence_pins != facts.evidence_pins
        or not _term_matches_effect_start(predecessor, effect_start)
        or effect_start_anchor.anchor_occurred_at > retired_at
        or retired_at - effect_start_anchor.anchor_occurred_at
        > timedelta(seconds=facts.maximum_age_seconds)
    ):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_EXPECTED_PINS_MISMATCH")
    canonical_binding = _canonical(mapping, code=code)
    return _FenceFacts(
        binding_mapping=mapping,
        binding_sha256=hashlib.sha256(canonical_binding).hexdigest(),
        effect_start=effect_start,
        effect_start_anchor=effect_start_anchor,
        predecessor_term=predecessor,
        evidence_pins=evidence_pins,
        fence_id=fence_id,
        fence_nonce=fence_nonce,
        retired_at=retired_at,
        expires_at=expires_at,
    )


def _signed_fence_receipt(
    raw: object,
    *,
    expected_schema: str,
    expected_kind: str,
    expected_role: str,
    signer_public_key: bytes,
    domain: bytes,
    facts: _ConfigFacts,
    now: datetime,
    code: str,
) -> tuple[_FenceFacts, bytes]:
    mapping, canonical = _parse_canonical_mapping(raw, fields=_EXECUTOR_RECEIPT_FIELDS, code=code)
    if (
        mapping["schema"] != expected_schema
        or mapping["version"] != _VERSION
        or mapping["kind"] != expected_kind
        or mapping["signer_role"] != expected_role
    ):
        _fail(code)
    fence = _fence_binding_from_mapping(
        mapping["fence_binding"], facts=facts, now=now, code=code
    )
    if mapping["fence_binding_sha256"] != fence.binding_sha256:
        _fail(code)
    unsigned = dict(mapping)
    signature = unsigned.pop("signature_base64")
    _verify_signature(
        signer_public_key=signer_public_key,
        domain=domain,
        unsigned=unsigned,
        signature_base64=signature,
        code=code,
    )
    return fence, canonical


def _fence_equal(left: _FenceFacts, right: _FenceFacts) -> bool:
    return (
        left.binding_mapping == right.binding_mapping
        and left.binding_sha256 == right.binding_sha256
        and left.effect_start == right.effect_start
        and left.effect_start_anchor == right.effect_start_anchor
        and left.predecessor_term == right.predecessor_term
        and left.evidence_pins == right.evidence_pins
        and left.fence_id == right.fence_id
        and left.fence_nonce == right.fence_nonce
        and left.retired_at == right.retired_at
        and left.expires_at == right.expires_at
    )


def derive_retired_fi_predecessor_fence_replay_key_sha256(
    *,
    effect_start: object,
    predecessor_term: object,
) -> str:
    """Derive the immutable P2 replay identity, never a reservation/action.

    Crucially, the result excludes receipt/admission IDs and nonces.  A real
    Witness ledger can therefore reject every duplicate attempt for this V4
    start and FI predecessor even when an attacker changes caller-selected
    receipt metadata.
    """

    effect, effect_mapping = _effect_start_mapping(
        effect_start,
        code="RETIRED_FI_PREDECESSOR_FENCE_REPLAY_KEY_INVALID",
    )
    term, term_mapping = _term_mapping(
        predecessor_term,
        code="RETIRED_FI_PREDECESSOR_FENCE_REPLAY_KEY_INVALID",
    )
    if not _term_matches_effect_start(term, effect):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_REPLAY_KEY_INVALID")
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_SCHEMA,
                "purpose": "witness-durable-single-use-fi-predecessor-retirement-v1",
                "effect_start": effect_mapping,
                "predecessor_term": term_mapping,
            },
            code="RETIRED_FI_PREDECESSOR_FENCE_REPLAY_KEY_INVALID",
        )
    ).hexdigest()


def _witness_receipt(
    raw: object,
    *,
    fence: _FenceFacts,
    facts: _ConfigFacts,
    now: datetime,
) -> tuple[_WitnessFacts, bytes]:
    code = "RETIRED_FI_PREDECESSOR_FENCE_WITNESS_ADMISSION_INVALID"
    mapping, canonical = _parse_canonical_mapping(raw, fields=_WITNESS_RECEIPT_FIELDS, code=code)
    if (
        mapping["schema"]
        != PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_WITNESS_ADMISSION_SCHEMA
        or mapping["version"] != _VERSION
        or mapping["kind"] != _WITNESS_KIND
        or mapping["signer_role"] != _WITNESS_ROLE
        or mapping["fence_id"] != fence.fence_id
        or mapping["fence_nonce"] != fence.fence_nonce
        or mapping["fence_binding_sha256"] != fence.binding_sha256
        or mapping["anti_replay_namespace"]
        != facts.anti_replay_policy.anti_replay_namespace
        or mapping["anti_replay_mode"] != _ANTI_REPLAY_MODE
        or mapping["witness_ledger_scope_sha256"]
        != facts.anti_replay_policy.witness_ledger_scope_sha256
    ):
        _fail(code)
    replay_key = _sha256(mapping["replay_key_sha256"], code=code)
    expected_replay_key = derive_retired_fi_predecessor_fence_replay_key_sha256(
        effect_start=fence.effect_start,
        predecessor_term=fence.predecessor_term,
    )
    if replay_key != expected_replay_key:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_REPLAY_KEY_MISMATCH")
    admission_id = _identifier(mapping["admission_id"], code=code)
    admission_nonce = _nonce(mapping["admission_nonce"], code=code)
    admitted_at = _timestamp(mapping["admitted_at"], code=code)
    expires_at = _timestamp(mapping["expires_at"], code=code)
    if (
        expires_at != fence.expires_at
        or admitted_at < fence.retired_at
        or admitted_at > expires_at
        or admitted_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or now - admitted_at > timedelta(seconds=facts.maximum_age_seconds)
        or expires_at <= now
    ):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_WITNESS_ADMISSION_STALE_OR_EXPIRED")
    sequence = _positive(
        mapping["witness_ledger_sequence"],
        maximum=2**63 - 1,
        code=code,
    )
    entry = _sha256(mapping["witness_ledger_entry_sha256"], code=code)
    previous = _sha256(
        mapping["witness_ledger_previous_head_sha256"],
        code=code,
        permit_zero=sequence == 1,
    )
    if sequence > 1 and previous == _ZERO_SHA256:
        _fail(code)
    unsigned = dict(mapping)
    signature = unsigned.pop("signature_base64")
    _verify_signature(
        signer_public_key=facts.witness_key,
        domain=_WITNESS_DOMAIN,
        unsigned=unsigned,
        signature_base64=signature,
        code=code,
    )
    return (
        _WitnessFacts(
            replay_key_sha256=replay_key,
            admission_id=admission_id,
            admission_nonce=admission_nonce,
            admitted_at=admitted_at,
            witness_ledger_sequence=sequence,
            witness_ledger_entry_sha256=entry,
            witness_ledger_previous_head_sha256=previous,
        ),
        canonical,
    )


def verify_retired_fi_predecessor_fence(
    *,
    executor_receipt: object,
    observer_receipt: object,
    witness_admission_receipt: object,
    config: RetiredFiPredecessorFenceVerificationConfig,
    now: datetime,
) -> VerifiedRetiredFiPredecessorFence:
    """Verify exact signed P2 evidence without performing the P2 fence.

    A success only says all independently signed records match one configured
    V4 phase-2 start and former FI term while their evidence window is fresh.
    It does not mean a Python process has observed, enforced, or can enforce
    the real server-side fence.
    """

    facts = _config(config)
    observed_now = _utc(now, code="RETIRED_FI_PREDECESSOR_FENCE_CLOCK_INVALID")
    executor, canonical_executor = _signed_fence_receipt(
        executor_receipt,
        expected_schema=PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_EXECUTOR_RECEIPT_SCHEMA,
        expected_kind=_EXECUTOR_KIND,
        expected_role=_EXECUTOR_ROLE,
        signer_public_key=facts.executor_key,
        domain=_EXECUTOR_DOMAIN,
        facts=facts,
        now=observed_now,
        code="RETIRED_FI_PREDECESSOR_FENCE_EXECUTOR_RECEIPT_INVALID",
    )
    observer, canonical_observer = _signed_fence_receipt(
        observer_receipt,
        expected_schema=PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_OBSERVER_RECEIPT_SCHEMA,
        expected_kind=_OBSERVER_KIND,
        expected_role=_OBSERVER_ROLE,
        signer_public_key=facts.observer_key,
        domain=_OBSERVER_DOMAIN,
        facts=facts,
        now=observed_now,
        code="RETIRED_FI_PREDECESSOR_FENCE_OBSERVER_RECEIPT_INVALID",
    )
    if not _fence_equal(executor, observer):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_EXECUTOR_OBSERVER_MISMATCH")
    witness, canonical_witness = _witness_receipt(
        witness_admission_receipt,
        fence=executor,
        facts=facts,
        now=observed_now,
    )
    result = VerifiedRetiredFiPredecessorFence(
        canonical_executor_receipt=canonical_executor,
        canonical_observer_receipt=canonical_observer,
        canonical_witness_admission_receipt=canonical_witness,
        executor_receipt_sha256=hashlib.sha256(canonical_executor).hexdigest(),
        observer_receipt_sha256=hashlib.sha256(canonical_observer).hexdigest(),
        witness_admission_receipt_sha256=hashlib.sha256(canonical_witness).hexdigest(),
        effect_start=executor.effect_start,
        effect_start_anchor=executor.effect_start_anchor,
        predecessor_term=executor.predecessor_term,
        evidence_pins=executor.evidence_pins,
        anti_replay_policy=facts.anti_replay_policy,
        fence_id=executor.fence_id,
        fence_nonce=executor.fence_nonce,
        replay_key_sha256=witness.replay_key_sha256,
        retired_at=executor.retired_at,
        expires_at=executor.expires_at,
        admission_id=witness.admission_id,
        admission_nonce=witness.admission_nonce,
        admitted_at=witness.admitted_at,
        witness_ledger_sequence=witness.witness_ledger_sequence,
        witness_ledger_entry_sha256=witness.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=witness.witness_ledger_previous_head_sha256,
        capability=_CAPABILITY,
    )
    _VERIFIED_STATES[result] = _VerifiedState(
        executor_receipt=canonical_executor,
        observer_receipt=canonical_observer,
        witness_receipt=canonical_witness,
    )
    return result


def _same_public_projection(
    value: VerifiedRetiredFiPredecessorFence,
    verified: VerifiedRetiredFiPredecessorFence,
) -> bool:
    return (
        value.canonical_executor_receipt == verified.canonical_executor_receipt
        and value.canonical_observer_receipt == verified.canonical_observer_receipt
        and value.canonical_witness_admission_receipt
        == verified.canonical_witness_admission_receipt
        and value.executor_receipt_sha256 == verified.executor_receipt_sha256
        and value.observer_receipt_sha256 == verified.observer_receipt_sha256
        and value.witness_admission_receipt_sha256
        == verified.witness_admission_receipt_sha256
        and value.effect_start == verified.effect_start
        and value.effect_start_anchor == verified.effect_start_anchor
        and value.predecessor_term == verified.predecessor_term
        and value.evidence_pins == verified.evidence_pins
        and value.anti_replay_policy == verified.anti_replay_policy
        and value.fence_id == verified.fence_id
        and value.fence_nonce == verified.fence_nonce
        and value.replay_key_sha256 == verified.replay_key_sha256
        and value.retired_at == verified.retired_at
        and value.expires_at == verified.expires_at
        and value.admission_id == verified.admission_id
        and value.admission_nonce == verified.admission_nonce
        and value.admitted_at == verified.admitted_at
        and value.witness_ledger_sequence == verified.witness_ledger_sequence
        and value.witness_ledger_entry_sha256 == verified.witness_ledger_entry_sha256
        and value.witness_ledger_previous_head_sha256
        == verified.witness_ledger_previous_head_sha256
        and value.writer_authorized is False
        and value.promotion_authorized is False
        and value.traffic_switch_authorized is False
        and value.external_effect_authorized is False
        and value.execution_authorized is False
        and value.full_matrix_authorized is False
    )


def require_verified_retired_fi_predecessor_fence(
    value: object,
    *,
    config: RetiredFiPredecessorFenceVerificationConfig,
    now: datetime,
) -> VerifiedRetiredFiPredecessorFence:
    """Revalidate an opaque P2 evidence capability against exact expected pins."""

    if (
        type(value) is not VerifiedRetiredFiPredecessorFence
        or value._capability is not _CAPABILITY
    ):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_VERIFIED_EVIDENCE_REQUIRED")
    state = _VERIFIED_STATES.get(value)
    if state is None:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_VERIFIED_EVIDENCE_REQUIRED")
    verified = verify_retired_fi_predecessor_fence(
        executor_receipt=state.executor_receipt,
        observer_receipt=state.observer_receipt,
        witness_admission_receipt=state.witness_receipt,
        config=config,
        now=now,
    )
    if not _same_public_projection(value, verified):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_VERIFIED_EVIDENCE_TAMPERED")
    return value
