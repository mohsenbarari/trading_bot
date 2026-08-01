"""Pure signed-wire contract for the V4 Witness anchor.

This module is deliberately narrower than a journal or an execution driver.
It only canonicalizes, signs, parses, and verifies non-secret V4 Witness
anchor messages.  It never opens files, makes a network call, starts a
process, reads a provider credential, or treats a verified message as an
execution/promotion permit.

The contract has two independent, explicit campaign pins:

* ``journal_binding_sha256`` is the canonical campaign/journal binding; and
* ``baseline_plan_binding_sha256`` commits to the canonical non-secret
  baseline plan facts required by a later, separate plan-rehydration gate.

Raw plan bytes and opaque readiness capabilities are intentionally absent.
An adapter may map its journal records to these wire values later, but this
module imports neither the journal nor an execution-driver generation.
"""

from __future__ import annotations

import base64
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_ATTESTATION_LIFETIME_SECONDS",
    "DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_COMMITMENT_AGE_SECONDS",
    "DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_FUTURE_SKEW_SECONDS",
    "DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_REQUEST_LIFETIME_SECONDS",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_REQUEST_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_BASELINE_PLAN_BINDING_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_COMMITMENT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNATURE_ALGORITHM",
    "PhysicalFullMatrixV4WitnessAnchorCommitment",
    "PhysicalFullMatrixV4WitnessAnchorControllerAppendRequest",
    "PhysicalFullMatrixV4WitnessAnchorGenesis",
    "PhysicalFullMatrixV4WitnessAnchorImmutableHead",
    "PhysicalFullMatrixV4WitnessAnchorPolicyIdentity",
    "PhysicalFullMatrixV4WitnessAnchorReadObservation",
    "PhysicalFullMatrixV4WitnessAnchorTransportEnvelope",
    "PhysicalFullMatrixV4WitnessAnchorVerificationPolicy",
    "PhysicalFullMatrixV4WitnessAnchorImmutableHeadSigningPayload",
    "PhysicalFullMatrixV4WitnessAnchorReadObservationSigningPayload",
    "PhysicalFullMatrixV4WitnessAnchorWireError",
    "VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest",
    "VerifiedPhysicalFullMatrixV4WitnessAnchorHead",
    "VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead",
    "VerifiedPhysicalFullMatrixV4WitnessAnchorReadObservation",
    "VerifiedPhysicalFullMatrixV4WitnessAnchorTransportEnvelope",
    "build_physical_full_matrix_v4_witness_anchor_controller_append_request",
    "build_physical_full_matrix_v4_witness_anchor_commitment",
    "build_physical_full_matrix_v4_witness_anchor_genesis",
    "build_physical_full_matrix_v4_witness_anchor_verification_policy",
    "build_physical_full_matrix_v4_witness_anchor_immutable_head",
    "build_physical_full_matrix_v4_witness_anchor_read_observation",
    "build_physical_full_matrix_v4_witness_anchor_transport_envelope",
    "canonical_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_facts",
    "canonical_physical_full_matrix_v4_witness_anchor_commitment_bytes",
    "canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes",
    "derive_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_sha256",
    "derive_physical_full_matrix_v4_witness_anchor_commitment_sha256",
    "ed25519_physical_full_matrix_v4_witness_anchor_key_id",
    "parse_physical_full_matrix_v4_witness_anchor_controller_append_request",
    "parse_physical_full_matrix_v4_witness_anchor_genesis",
    "parse_physical_full_matrix_v4_witness_anchor_immutable_head",
    "parse_physical_full_matrix_v4_witness_anchor_read_observation",
    "parse_physical_full_matrix_v4_witness_anchor_transport_envelope",
    "physical_full_matrix_v4_witness_anchor_phase_name",
    "verified_physical_full_matrix_v4_witness_anchor_genesis_head",
    "finalize_physical_full_matrix_v4_witness_anchor_immutable_head",
    "finalize_physical_full_matrix_v4_witness_anchor_read_observation",
    "prepare_physical_full_matrix_v4_witness_anchor_immutable_head",
    "prepare_physical_full_matrix_v4_witness_anchor_read_observation",
    "verify_physical_full_matrix_v4_witness_anchor_genesis",
    "verify_physical_full_matrix_v4_witness_anchor_immutable_head",
    "verify_physical_full_matrix_v4_witness_anchor_read_observation",
    "verify_physical_full_matrix_v4_witness_anchor_transport_envelope",
    "verify_physical_full_matrix_v4_witness_anchor_controller_append_request",
)


PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_BASELINE_PLAN_BINDING_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-baseline-plan-binding-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_COMMITMENT_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-commitment-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-genesis-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_REQUEST_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-controller-append-request-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-head-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-immutable-head-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-adapter-identity-v2"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-read-observation-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-transport-envelope-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNATURE_ALGORITHM = "ed25519"

DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_REQUEST_LIFETIME_SECONDS = 120
DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_ATTESTATION_LIFETIME_SECONDS = 120
DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_COMMITMENT_AGE_SECONDS = 300
DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_FUTURE_SKEW_SECONDS = 5

_MAX_REQUEST_LIFETIME_SECONDS = 600
_MAX_ATTESTATION_LIFETIME_SECONDS = 600
_MAX_COMMITMENT_AGE_SECONDS = 3_600
_MAX_FUTURE_SKEW_SECONDS = 30
_MAX_WIRE_BYTES = 64 * 1024
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_CLAIM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$", re.ASCII)
_REPLAY_ID_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)
_EVENT_EFFECT_STARTED = "effect-started"
_EVENT_COMPLETED = "completed"
_EVENTS = frozenset({_EVENT_EFFECT_STARTED, _EVENT_COMPLETED})
_CONTROLLER_PURPOSE = "physical-full-matrix-v4-witness-anchor-controller-append-v1"
_WITNESS_PURPOSE = "physical-full-matrix-v4-witness-anchor-head-attestation-v1"
_GENESIS_PURPOSE = "physical-full-matrix-v4-witness-anchor-genesis-attestation-v1"
_IMMUTABLE_HEAD_PURPOSE = "physical-full-matrix-v4-witness-anchor-immutable-append-v1"
_READ_OBSERVATION_PURPOSE = "physical-full-matrix-v4-witness-anchor-read-observation-v1"
_TRANSPORT_ENVELOPE_PURPOSE = "physical-full-matrix-v4-witness-anchor-transport-envelope-v1"

# This is intentionally copied as wire grammar, not imported from an execution
# driver.  The wire contract remains usable by a future isolated adapter.
_PHASES: dict[int, str] = {
    1: "normal-fi-writer-v2-witness-roundtrip-strict-ack-matrix",
    2: "fence-fi-writer-v2",
    3: "recover-ir-through-object-storage-v2",
    4: "witness-promote-ir-v2",
    5: "ir-writer-v2-witness-roundtrip-strict-ack-matrix",
    6: "rebuild-fi-through-object-storage-v2",
    7: "witness-restore-fi-writer-v2",
    8: "final-three-site-v2-convergence-oracle",
}
_BASELINE_BINDING_FIELDS = frozenset(
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


def physical_full_matrix_v4_witness_anchor_phase_name(sequence: int) -> str:
    """Return the exact V4 wire phase label for a validated sequence 1..8."""

    result = _PHASES.get(sequence)
    if result is None or type(sequence) is not int:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PHASE_INVALID")
    return result


class PhysicalFullMatrixV4WitnessAnchorWireError(ValueError):
    """The pure V4 Witness-anchor grammar rejected untrusted wire data."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4WitnessAnchorWireError(code)


def _legacy_one_layer_head_fenced() -> None:
    """Reject the unreleased expiring-head grammar after the V2 migration."""

    _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEGACY_ONE_LAYER_MIGRATION_REQUIRED")


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorGenesis:
    """Explicit immutable root used by every request and witnessed head."""

    schema: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    sequence: int
    head_sha256: str
    witness_key_id: str
    witness_attestation_sha256: str
    witness_signature: bytes


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorVerificationPolicy:
    """Pinned public keys and bounded-time rules; never an execution permit."""

    genesis: PhysicalFullMatrixV4WitnessAnchorGenesis
    controller_public_key: bytes
    witness_public_key: bytes
    maximum_request_lifetime_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_REQUEST_LIFETIME_SECONDS
    )
    maximum_attestation_lifetime_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_ATTESTATION_LIFETIME_SECONDS
    )
    maximum_commitment_age_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_COMMITMENT_AGE_SECONDS
    )
    maximum_future_skew_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_FUTURE_SKEW_SECONDS
    )


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorPolicyIdentity:
    """Exact non-secret campaign identity shared by adapter and root ledger.

    This is not an authorization token.  It prevents a narrow transport
    endpoint from silently crossing campaign/genesis pins while avoiding an
    adapter↔ledger import cycle.
    """

    schema: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    canonical_genesis_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorCommitment:
    """One semantic V4 journal transition, without a transport implementation."""

    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    event: str
    phase_sequence: int
    phase: str
    phase_request_sha256: str
    effect_key: str
    claim_id: str
    receipt_sha256: str | None
    previous_anchor_sequence: int
    previous_anchor_head_sha256: str
    local_previous_record_sha256: str
    local_event_sha256: str
    occurred_at: datetime


@dataclass(frozen=True)
class _Signature:
    key_id: str
    signature: bytes


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorControllerAppendRequest:
    """Parsed but unverified controller-signed append envelope."""

    canonical_bytes: bytes
    request_sha256: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    replay_id: str
    issued_at: datetime
    expires_at: datetime
    commitment_sha256: str
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment
    controller_signature: _Signature


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorWitnessHead:
    """Parsed but unverified Witness-signed immutable head/readback."""

    canonical_bytes: bytes
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    sequence: int
    previous_head_sha256: str
    head_sha256: str
    commitment_sha256: str
    controller_request_sha256: str
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment
    attestation_id: str
    attested_at: datetime
    expires_at: datetime
    witness_attestation_sha256: str
    witness_signature: _Signature


@dataclass(frozen=True, init=False)
class PhysicalFullMatrixV4WitnessAnchorWitnessHeadSigningPayload:
    """Exact unsigned Witness-head bytes handed to a root signer boundary."""

    canonical_signed_head: bytes

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNING_PAYLOAD_MINTED_ONLY")


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorImmutableHead:
    """Parsed but unverified permanent append-head evidence."""

    canonical_bytes: bytes
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    sequence: int
    previous_head_sha256: str
    head_sha256: str
    commitment_sha256: str
    controller_request_sha256: str
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment
    immutable_attestation_sha256: str
    witness_signature: _Signature


@dataclass(frozen=True, init=False)
class PhysicalFullMatrixV4WitnessAnchorImmutableHeadSigningPayload:
    """Immutable append-head bytes for the immutable-head signer domain only."""

    canonical_signed_immutable_head: bytes

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_PAYLOAD_MINTED_ONLY")


@dataclass(frozen=True, init=False)
class VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead:
    """Verifier-minted immutable provenance; never an execution capability."""

    canonical_immutable_head: bytes
    immutable_head_canonical_sha256: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    sequence: int
    previous_head_sha256: str
    head_sha256: str
    commitment_sha256: str
    controller_request_sha256: str
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment
    immutable_attestation_sha256: str
    verification_observed_at: datetime
    execution_authorized: bool
    promotion_authorized: bool
    full_matrix_executed: bool

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_VERIFIED_IMMUTABLE_HEAD_MINTED_ONLY")


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorReadObservation:
    """Parsed but unverified short-lived read proof for one immutable head."""

    canonical_bytes: bytes
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    immutable_head_canonical_sha256: str
    sequence: int
    previous_head_sha256: str
    head_sha256: str
    commitment_sha256: str
    controller_request_sha256: str
    immutable_attestation_sha256: str
    read_challenge: str
    observation_id: str
    observed_at: datetime
    expires_at: datetime
    observation_attestation_sha256: str
    witness_signature: _Signature


@dataclass(frozen=True, init=False)
class PhysicalFullMatrixV4WitnessAnchorReadObservationSigningPayload:
    """Read-observation bytes for a distinct short-lived signer domain."""

    canonical_signed_read_observation: bytes

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_PAYLOAD_MINTED_ONLY")


@dataclass(frozen=True, init=False)
class VerifiedPhysicalFullMatrixV4WitnessAnchorReadObservation:
    """Verifier-minted timely observation provenance only."""

    canonical_read_observation: bytes
    immutable_head_canonical_sha256: str
    sequence: int
    head_sha256: str
    read_challenge: str
    observation_id: str
    observed_at: datetime
    expires_at: datetime
    observation_attestation_sha256: str
    verification_observed_at: datetime
    execution_authorized: bool
    promotion_authorized: bool
    full_matrix_executed: bool

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_VERIFIED_OBSERVATION_MINTED_ONLY")


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorTransportEnvelope:
    """Parsed transport payload carrying immutable evidence plus fresh observation."""

    canonical_bytes: bytes
    canonical_immutable_head: bytes
    canonical_read_observation: bytes
    read_challenge: str


@dataclass(frozen=True, init=False)
class VerifiedPhysicalFullMatrixV4WitnessAnchorTransportEnvelope:
    """One verified stable anchor plus a fresh non-mutating read proof.

    ``anchor_head`` is the exact configured signed genesis for the initial
    read, or an immutable append head thereafter.  Keeping the genesis case
    explicit makes a reader unable to manufacture an all-zero non-genesis
    predecessor.
    """

    anchor_head: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    )
    read_observation: VerifiedPhysicalFullMatrixV4WitnessAnchorReadObservation
    read_challenge: str
    execution_authorized: bool
    promotion_authorized: bool
    full_matrix_executed: bool

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_VERIFIED_ENVELOPE_MINTED_ONLY")


@dataclass(frozen=True, init=False)
class VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
    """Verifier-minted provenance only; it carries no execution authority."""

    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    sequence: int
    previous_head_sha256: str | None
    head_sha256: str
    commitment_sha256: str
    controller_request_sha256: str | None
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment | None
    attestation_id: str | None
    attested_at: datetime | None
    expires_at: datetime | None
    witness_attestation_sha256: str | None
    canonical_head: bytes | None
    verification_observed_at: datetime
    execution_authorized: bool
    promotion_authorized: bool
    full_matrix_executed: bool

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_VERIFIED_HEAD_MINTED_ONLY")


@dataclass(frozen=True, init=False)
class VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest:
    """Verifier-minted request provenance only; it cannot authorize an effect."""

    canonical_request: bytes
    request_sha256: str
    replay_id: str
    issued_at: datetime
    expires_at: datetime
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    predecessor_sequence: int
    predecessor_head_sha256: str
    commitment_sha256: str
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment
    verified_at: datetime
    execution_authorized: bool
    promotion_authorized: bool
    full_matrix_executed: bool

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_VERIFIED_REQUEST_MINTED_ONLY")


@dataclass(frozen=True)
class _PolicyFacts:
    genesis: PhysicalFullMatrixV4WitnessAnchorGenesis
    controller_public_key: bytes
    witness_public_key: bytes
    controller_key_id: str
    witness_key_id: str
    maximum_request_lifetime_seconds: int
    maximum_attestation_lifetime_seconds: int
    maximum_commitment_age_seconds: int
    maximum_future_skew_seconds: int


def _canonical(value: object, *, code: str) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PhysicalFullMatrixV4WitnessAnchorWireError(code) from exc
    return encoded


def _strict_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _parse_canonical_object(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not bytes or not value or len(value) > _MAX_WIRE_BYTES:
        _fail(code)
    try:
        decoded = json.loads(
            value.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _item: _fail(code),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, PhysicalFullMatrixV4WitnessAnchorWireError):
        _fail(code)
    if type(decoded) is not dict or _canonical(decoded, code=code) != value:
        _fail(code)
    return decoded


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    if not permit_zero and value == _ZERO_SHA256:
        _fail(code)
    return value


def _positive_int(value: object, *, code: str, permit_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if permit_zero else 1) or value > (2**63 - 1):
        _fail(code)
    return value


def _uuid(value: object, *, code: str) -> UUID:
    if type(value) is UUID:
        result = value
    elif type(value) is str:
        try:
            result = UUID(value)
        except ValueError:
            _fail(code)
        if str(result) != value:
            _fail(code)
    else:
        _fail(code)
    if result.int == 0:
        _fail(code)
    return result


def _identifier(value: object, *, code: str, pattern: re.Pattern[str] = _CLAIM_ID_RE) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: object, *, code: str) -> str:
    observed = _utc(value, code=code)
    if observed.microsecond:
        return observed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return observed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail(code)
    result = parsed.astimezone(timezone.utc)
    if _render_timestamp(result, code=code) != value:
        _fail(code)
    return result


def _signature_from_mapping(value: object, *, code: str) -> _Signature:
    if type(value) is not dict or set(value) != {
        "algorithm",
        "key_id",
        "signature_base64",
    }:
        _fail(code)
    if value["algorithm"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNATURE_ALGORITHM:
        _fail(code)
    key_id = _identifier(value["key_id"], code=code, pattern=_KEY_ID_RE)
    signature_base64 = value["signature_base64"]
    if type(signature_base64) is not str:
        _fail(code)
    try:
        signature = base64.b64decode(signature_base64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail(code)
    if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != signature_base64:
        _fail(code)
    return _Signature(key_id=key_id, signature=signature)


def _signature_body(*, key_id: str, signature: bytes) -> dict[str, object]:
    _identifier(key_id, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNATURE_INVALID", pattern=_KEY_ID_RE)
    if type(signature) is not bytes or len(signature) != 64:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNATURE_INVALID")
    return {
        "algorithm": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNATURE_ALGORITHM,
        "key_id": key_id,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def ed25519_physical_full_matrix_v4_witness_anchor_key_id(public_key: bytes) -> str:
    """Return the canonical key pin for one exact raw 32-byte Ed25519 key."""

    raw = _public_key_bytes(public_key, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PUBLIC_KEY_INVALID")
    return "ed25519-sha256:" + hashlib.sha256(raw).hexdigest()


def _public_key_bytes(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _private_key_matches(
    value: object,
    *,
    expected_public_key: bytes,
    code: str,
) -> Ed25519PrivateKey:
    if not isinstance(value, Ed25519PrivateKey):
        _fail(code)
    actual = value.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    if actual != expected_public_key:
        _fail(code)
    return value


def _genesis(value: object, *, code: str) -> PhysicalFullMatrixV4WitnessAnchorGenesis:
    if type(value) is not PhysicalFullMatrixV4WitnessAnchorGenesis:
        _fail(code)
    if value.schema != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SCHEMA:
        _fail(code)
    if type(value.witness_signature) is not bytes or len(value.witness_signature) != 64:
        _fail(code)
    return PhysicalFullMatrixV4WitnessAnchorGenesis(
        schema=value.schema,
        journal_binding_sha256=_sha256(value.journal_binding_sha256, code=code),
        baseline_plan_binding_sha256=_sha256(value.baseline_plan_binding_sha256, code=code),
        run_id=_uuid(value.run_id, code=code),
        plan_sha256=_sha256(value.plan_sha256, code=code),
        sequence=_positive_int(value.sequence, code=code, permit_zero=True),
        head_sha256=_sha256(value.head_sha256, code=code, permit_zero=True),
        witness_key_id=_identifier(value.witness_key_id, code=code, pattern=_KEY_ID_RE),
        witness_attestation_sha256=_sha256(value.witness_attestation_sha256, code=code),
        witness_signature=value.witness_signature,
    )


def _policy_facts(value: object) -> _PolicyFacts:
    if type(value) is not PhysicalFullMatrixV4WitnessAnchorVerificationPolicy:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_INVALID")
    genesis = _genesis(value.genesis, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_INVALID")
    controller = _public_key_bytes(
        value.controller_public_key,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_INVALID",
    )
    witness = _public_key_bytes(
        value.witness_public_key,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_INVALID",
    )
    if controller == witness:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_INVALID")
    limits = (
        (value.maximum_request_lifetime_seconds, _MAX_REQUEST_LIFETIME_SECONDS),
        (value.maximum_attestation_lifetime_seconds, _MAX_ATTESTATION_LIFETIME_SECONDS),
        (value.maximum_commitment_age_seconds, _MAX_COMMITMENT_AGE_SECONDS),
        (value.maximum_future_skew_seconds, _MAX_FUTURE_SKEW_SECONDS),
    )
    if any(type(item) is not int or item < 1 or item > maximum for item, maximum in limits):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_INVALID")
    facts = _PolicyFacts(
        genesis=genesis,
        controller_public_key=controller,
        witness_public_key=witness,
        controller_key_id=ed25519_physical_full_matrix_v4_witness_anchor_key_id(controller),
        witness_key_id=ed25519_physical_full_matrix_v4_witness_anchor_key_id(witness),
        maximum_request_lifetime_seconds=value.maximum_request_lifetime_seconds,
        maximum_attestation_lifetime_seconds=value.maximum_attestation_lifetime_seconds,
        maximum_commitment_age_seconds=value.maximum_commitment_age_seconds,
        maximum_future_skew_seconds=value.maximum_future_skew_seconds,
    )
    _verify_genesis_signature(facts)
    return facts


def build_physical_full_matrix_v4_witness_anchor_verification_policy(
    *,
    genesis: PhysicalFullMatrixV4WitnessAnchorGenesis,
    controller_public_key: bytes,
    witness_public_key: bytes,
    maximum_request_lifetime_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_REQUEST_LIFETIME_SECONDS
    ),
    maximum_attestation_lifetime_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_ATTESTATION_LIFETIME_SECONDS
    ),
    maximum_commitment_age_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_COMMITMENT_AGE_SECONDS
    ),
    maximum_future_skew_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_MAX_FUTURE_SKEW_SECONDS
    ),
) -> PhysicalFullMatrixV4WitnessAnchorVerificationPolicy:
    """Build a checked pure verification policy from pinned public facts."""

    policy = PhysicalFullMatrixV4WitnessAnchorVerificationPolicy(
        genesis=genesis,
        controller_public_key=controller_public_key,
        witness_public_key=witness_public_key,
        maximum_request_lifetime_seconds=maximum_request_lifetime_seconds,
        maximum_attestation_lifetime_seconds=maximum_attestation_lifetime_seconds,
        maximum_commitment_age_seconds=maximum_commitment_age_seconds,
        maximum_future_skew_seconds=maximum_future_skew_seconds,
    )
    _policy_facts(policy)
    return policy


def _genesis_base_body(
    *,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    sequence: int,
    head_sha256: str,
    witness_key_id: str,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SCHEMA,
        "purpose": _GENESIS_PURPOSE,
        "journal_binding_sha256": _sha256(
            journal_binding_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
        ),
        "baseline_plan_binding_sha256": _sha256(
            baseline_plan_binding_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
        ),
        "run_id": str(_uuid(run_id, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")),
        "plan_sha256": _sha256(
            plan_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
        ),
        "sequence": _positive_int(
            sequence,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
            permit_zero=True,
        ),
        "head_sha256": _sha256(
            head_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
            permit_zero=True,
        ),
        "witness_key_id": _identifier(
            witness_key_id,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
            pattern=_KEY_ID_RE,
        ),
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }


def _genesis_signed_body(value: PhysicalFullMatrixV4WitnessAnchorGenesis) -> dict[str, object]:
    basic = _genesis(value, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    base = _genesis_base_body(
        journal_binding_sha256=basic.journal_binding_sha256,
        baseline_plan_binding_sha256=basic.baseline_plan_binding_sha256,
        run_id=basic.run_id,
        plan_sha256=basic.plan_sha256,
        sequence=basic.sequence,
        head_sha256=basic.head_sha256,
        witness_key_id=basic.witness_key_id,
    )
    expected = hashlib.sha256(
        _canonical(base, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    ).hexdigest()
    if basic.witness_attestation_sha256 != expected:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    return {**base, "witness_attestation_sha256": expected}


def _verify_genesis_signature(facts: _PolicyFacts) -> None:
    genesis = facts.genesis
    if genesis.witness_key_id != facts.witness_key_id:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SIGNER_MISMATCH")
    try:
        Ed25519PublicKey.from_public_bytes(facts.witness_public_key).verify(
            genesis.witness_signature,
            _canonical(
                _genesis_signed_body(genesis),
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
            ),
        )
    except (InvalidSignature, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SIGNATURE_INVALID")


def canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
    value: PhysicalFullMatrixV4WitnessAnchorGenesis,
) -> bytes:
    """Render the exact signed genesis wire material retained by policy."""

    basic = _genesis(value, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    return _canonical(
        {
            **_genesis_signed_body(basic),
            "witness_signature": _signature_body(
                key_id=basic.witness_key_id,
                signature=basic.witness_signature,
            ),
        },
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
    )


_GENESIS_BASE_FIELDS = frozenset(
    {
        "schema",
        "purpose",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "run_id",
        "plan_sha256",
        "sequence",
        "head_sha256",
        "witness_key_id",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)
_GENESIS_SIGNED_FIELDS = _GENESIS_BASE_FIELDS | {"witness_attestation_sha256"}
_GENESIS_FIELDS = _GENESIS_SIGNED_FIELDS | {"witness_signature"}


def parse_physical_full_matrix_v4_witness_anchor_genesis(
    value: object,
) -> PhysicalFullMatrixV4WitnessAnchorGenesis:
    """Parse one exact canonical signed genesis without accepting a policy yet."""

    raw = value if type(value) is bytes else None
    decoded = _parse_canonical_object(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_ENCODING_INVALID",
    )
    assert raw is not None
    if (
        set(decoded) != _GENESIS_FIELDS
        or decoded["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SCHEMA
        or decoded["purpose"] != _GENESIS_PURPOSE
        or decoded["execution_authorized"] is not False
        or decoded["promotion_authorized"] is not False
        or decoded["full_matrix_executed"] is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    signature = _signature_from_mapping(
        decoded["witness_signature"],
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
    )
    candidate = PhysicalFullMatrixV4WitnessAnchorGenesis(
        schema=decoded["schema"],  # type: ignore[arg-type]
        journal_binding_sha256=decoded["journal_binding_sha256"],  # type: ignore[arg-type]
        baseline_plan_binding_sha256=decoded["baseline_plan_binding_sha256"],  # type: ignore[arg-type]
        run_id=_uuid(
            decoded["run_id"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID",
        ),
        plan_sha256=decoded["plan_sha256"],  # type: ignore[arg-type]
        sequence=decoded["sequence"],  # type: ignore[arg-type]
        head_sha256=decoded["head_sha256"],  # type: ignore[arg-type]
        witness_key_id=signature.key_id,
        witness_attestation_sha256=decoded["witness_attestation_sha256"],  # type: ignore[arg-type]
        witness_signature=signature.signature,
    )
    basic = _genesis(candidate, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    if (
        canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(basic) != raw
        or decoded["witness_key_id"] != signature.key_id
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    return basic


def verify_physical_full_matrix_v4_witness_anchor_genesis(
    value: object,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
    """Accept only the exact configured signed genesis, never a lookalike root."""

    candidate = parse_physical_full_matrix_v4_witness_anchor_genesis(value)
    facts = _policy_facts(policy)
    if candidate != facts.genesis:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_POLICY_MISMATCH")
    return verified_physical_full_matrix_v4_witness_anchor_genesis_head(
        policy=policy,
        now=now,
    )


def build_physical_full_matrix_v4_witness_anchor_genesis(
    *,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    sequence: int,
    head_sha256: str,
    witness_private_key: Ed25519PrivateKey,
) -> PhysicalFullMatrixV4WitnessAnchorGenesis:
    """Create a signed, explicitly pinned genesis without any I/O."""

    if not isinstance(witness_private_key, Ed25519PrivateKey):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SIGNER_INVALID")
    raw_public = witness_private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = ed25519_physical_full_matrix_v4_witness_anchor_key_id(raw_public)
    base = _genesis_base_body(
        journal_binding_sha256=journal_binding_sha256,
        baseline_plan_binding_sha256=baseline_plan_binding_sha256,
        run_id=run_id,
        plan_sha256=plan_sha256,
        sequence=sequence,
        head_sha256=head_sha256,
        witness_key_id=key_id,
    )
    attestation = hashlib.sha256(
        _canonical(base, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    ).hexdigest()
    signed = {**base, "witness_attestation_sha256": attestation}
    signature = witness_private_key.sign(
        _canonical(signed, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    )
    genesis = PhysicalFullMatrixV4WitnessAnchorGenesis(
        schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SCHEMA,
        journal_binding_sha256=base["journal_binding_sha256"],  # type: ignore[arg-type]
        baseline_plan_binding_sha256=base["baseline_plan_binding_sha256"],  # type: ignore[arg-type]
        run_id=_uuid(base["run_id"], code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID"),
        plan_sha256=base["plan_sha256"],  # type: ignore[arg-type]
        sequence=base["sequence"],  # type: ignore[arg-type]
        head_sha256=base["head_sha256"],  # type: ignore[arg-type]
        witness_key_id=key_id,
        witness_attestation_sha256=attestation,
        witness_signature=signature,
    )
    _genesis(genesis, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_INVALID")
    return genesis


def _baseline_binding_body(value: object, *, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _BASELINE_BINDING_FIELDS:
        _fail(code)
    campaign_id = _identifier(value["campaign_id"], code=code, pattern=_CAMPAIGN_ID_RE)
    release_sha = value["release_sha"]
    if type(release_sha) is not str or _RELEASE_SHA_RE.fullmatch(release_sha) is None:
        _fail(code)
    sha_names = (
        "readiness_binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "witnessed_term_proof_sha256",
        "roundtrip_attestation_sha256",
        "roundtrip_configuration_sha256",
    )
    facts = {name: _sha256(value[name], code=code) for name in sha_names}
    holder = value["writer_holder_site"]
    source = value["source_site"]
    destination = value["destination_site"]
    sites = {"webapp_fi", "webapp_ir"}
    if holder not in sites or source not in sites or destination not in sites or source == destination:
        _fail(code)
    # This helper commits the *initial normal* plan.  A reverse/foreign term
    # must never be hashable as a rehydration baseline after phase four.
    if holder != source or holder != "webapp_fi" or source != "webapp_fi" or destination != "webapp_ir":
        _fail(code)
    return {
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        **facts,
        "writer_holder_site": holder,
        "writer_epoch": _positive_int(value["writer_epoch"], code=code),
        "writer_lease_id": _identifier(value["writer_lease_id"], code=code),
        "source_site": source,
        "destination_site": destination,
        "witness_transition_id": _identifier(value["witness_transition_id"], code=code),
        "witness_sequence": _positive_int(value["witness_sequence"], code=code),
    }


def canonical_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_facts(
    *,
    run_id: UUID,
    plan_sha256: str,
    initial_active_binding: Mapping[str, object],
) -> bytes:
    """Canonical non-secret plan facts used by later rehydration admission.

    This deliberately commits no raw V4 plan bytes and no opaque readiness
    object.  It is the one public canonicalization routine that a journal and
    a later rehydrator must share, rather than reconstructing a private hash.
    """

    return _canonical(
        {
            "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_BASELINE_PLAN_BINDING_SCHEMA,
            "run_id": str(_uuid(run_id, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_BASELINE_INVALID")),
            "plan_sha256": _sha256(
                plan_sha256,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_BASELINE_INVALID",
            ),
            "initial_active_binding": _baseline_binding_body(
                initial_active_binding,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_BASELINE_INVALID",
            ),
        },
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_BASELINE_INVALID",
    )


def derive_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_sha256(
    *,
    run_id: UUID,
    plan_sha256: str,
    initial_active_binding: Mapping[str, object],
) -> str:
    """Hash :func:`canonical_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_facts`."""

    return hashlib.sha256(
        canonical_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_facts(
            run_id=run_id,
            plan_sha256=plan_sha256,
            initial_active_binding=initial_active_binding,
        )
    ).hexdigest()


_COMMITMENT_FIELDS = frozenset(
    {
        "schema",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "run_id",
        "plan_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "event",
        "phase_sequence",
        "phase",
        "phase_request_sha256",
        "effect_key",
        "claim_id",
        "receipt_sha256",
        "previous_anchor_sequence",
        "previous_anchor_head_sha256",
        "local_previous_record_sha256",
        "local_event_sha256",
        "occurred_at",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)


def _commitment_body(
    value: object,
    *,
    code: str,
) -> dict[str, object]:
    if type(value) is not PhysicalFullMatrixV4WitnessAnchorCommitment:
        _fail(code)
    if value.event not in _EVENTS:
        _fail(code)
    phase_sequence = _positive_int(value.phase_sequence, code=code)
    if _PHASES.get(phase_sequence) != value.phase:
        _fail(code)
    receipt: str | None
    if value.event == _EVENT_EFFECT_STARTED:
        if value.receipt_sha256 is not None:
            _fail(code)
        receipt = None
    else:
        receipt = _sha256(value.receipt_sha256, code=code)
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_COMMITMENT_SCHEMA,
        "journal_binding_sha256": _sha256(value.journal_binding_sha256, code=code),
        "baseline_plan_binding_sha256": _sha256(
            value.baseline_plan_binding_sha256,
            code=code,
        ),
        "run_id": str(_uuid(value.run_id, code=code)),
        "plan_sha256": _sha256(value.plan_sha256, code=code),
        "anchor_genesis_sequence": _positive_int(
            value.anchor_genesis_sequence,
            code=code,
            permit_zero=True,
        ),
        "anchor_genesis_head_sha256": _sha256(
            value.anchor_genesis_head_sha256,
            code=code,
            permit_zero=True,
        ),
        "event": value.event,
        "phase_sequence": phase_sequence,
        "phase": value.phase,
        "phase_request_sha256": _sha256(value.phase_request_sha256, code=code),
        "effect_key": _sha256(value.effect_key, code=code),
        "claim_id": _identifier(value.claim_id, code=code),
        "receipt_sha256": receipt,
        "previous_anchor_sequence": _positive_int(
            value.previous_anchor_sequence,
            code=code,
            permit_zero=True,
        ),
        "previous_anchor_head_sha256": _sha256(
            value.previous_anchor_head_sha256,
            code=code,
            permit_zero=True,
        ),
        "local_previous_record_sha256": _sha256(
            value.local_previous_record_sha256,
            code=code,
            permit_zero=True,
        ),
        "local_event_sha256": _sha256(value.local_event_sha256, code=code),
        "occurred_at": _render_timestamp(value.occurred_at, code=code),
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }


def _commitment_from_mapping(
    value: object,
    *,
    code: str,
) -> PhysicalFullMatrixV4WitnessAnchorCommitment:
    if type(value) is not dict or set(value) != _COMMITMENT_FIELDS:
        _fail(code)
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_COMMITMENT_SCHEMA
        or value["execution_authorized"] is not False
        or value["promotion_authorized"] is not False
        or value["full_matrix_executed"] is not False
    ):
        _fail(code)
    result = PhysicalFullMatrixV4WitnessAnchorCommitment(
        journal_binding_sha256=value["journal_binding_sha256"],  # type: ignore[arg-type]
        baseline_plan_binding_sha256=value["baseline_plan_binding_sha256"],  # type: ignore[arg-type]
        run_id=_uuid(value["run_id"], code=code),
        plan_sha256=value["plan_sha256"],  # type: ignore[arg-type]
        anchor_genesis_sequence=value["anchor_genesis_sequence"],  # type: ignore[arg-type]
        anchor_genesis_head_sha256=value["anchor_genesis_head_sha256"],  # type: ignore[arg-type]
        event=value["event"],  # type: ignore[arg-type]
        phase_sequence=value["phase_sequence"],  # type: ignore[arg-type]
        phase=value["phase"],  # type: ignore[arg-type]
        phase_request_sha256=value["phase_request_sha256"],  # type: ignore[arg-type]
        effect_key=value["effect_key"],  # type: ignore[arg-type]
        claim_id=value["claim_id"],  # type: ignore[arg-type]
        receipt_sha256=value["receipt_sha256"],  # type: ignore[arg-type]
        previous_anchor_sequence=value["previous_anchor_sequence"],  # type: ignore[arg-type]
        previous_anchor_head_sha256=value["previous_anchor_head_sha256"],  # type: ignore[arg-type]
        local_previous_record_sha256=value["local_previous_record_sha256"],  # type: ignore[arg-type]
        local_event_sha256=value["local_event_sha256"],  # type: ignore[arg-type]
        occurred_at=_timestamp(value["occurred_at"], code=code),
    )
    if _commitment_body(result, code=code) != value:
        _fail(code)
    return result


def build_physical_full_matrix_v4_witness_anchor_commitment(
    *,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    anchor_genesis_sequence: int,
    anchor_genesis_head_sha256: str,
    event: str,
    phase_sequence: int,
    phase_request_sha256: str,
    effect_key: str,
    claim_id: str,
    receipt_sha256: str | None,
    previous_anchor_sequence: int,
    previous_anchor_head_sha256: str,
    local_previous_record_sha256: str,
    local_event_sha256: str,
    occurred_at: datetime,
) -> PhysicalFullMatrixV4WitnessAnchorCommitment:
    """Create the one exact wire commitment mapped by a future journal adapter.

    The phase name is deliberately derived from its V4 sequence rather than
    accepted from the caller, preventing a journal-to-wire phase-label split.
    """

    result = PhysicalFullMatrixV4WitnessAnchorCommitment(
        journal_binding_sha256=journal_binding_sha256,
        baseline_plan_binding_sha256=baseline_plan_binding_sha256,
        run_id=run_id,
        plan_sha256=plan_sha256,
        anchor_genesis_sequence=anchor_genesis_sequence,
        anchor_genesis_head_sha256=anchor_genesis_head_sha256,
        event=event,
        phase_sequence=phase_sequence,
        phase=physical_full_matrix_v4_witness_anchor_phase_name(phase_sequence),
        phase_request_sha256=phase_request_sha256,
        effect_key=effect_key,
        claim_id=claim_id,
        receipt_sha256=receipt_sha256,
        previous_anchor_sequence=previous_anchor_sequence,
        previous_anchor_head_sha256=previous_anchor_head_sha256,
        local_previous_record_sha256=local_previous_record_sha256,
        local_event_sha256=local_event_sha256,
        occurred_at=occurred_at,
    )
    _commitment_body(
        result,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_COMMITMENT_INVALID",
    )
    return result


def canonical_physical_full_matrix_v4_witness_anchor_commitment_bytes(
    value: PhysicalFullMatrixV4WitnessAnchorCommitment,
) -> bytes:
    """Return the sole canonical ASCII/newline commitment representation."""

    return _canonical(
        _commitment_body(value, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_COMMITMENT_INVALID"),
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_COMMITMENT_INVALID",
    )


def derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(
    value: PhysicalFullMatrixV4WitnessAnchorCommitment,
) -> str:
    """Hash the canonical commitment wire bytes, including its newline."""

    return hashlib.sha256(
        canonical_physical_full_matrix_v4_witness_anchor_commitment_bytes(value)
    ).hexdigest()


def _require_commitment_for_policy(
    value: PhysicalFullMatrixV4WitnessAnchorCommitment,
    *,
    facts: _PolicyFacts,
    code: str,
) -> PhysicalFullMatrixV4WitnessAnchorCommitment:
    _commitment_body(value, code=code)
    genesis = facts.genesis
    if (
        value.journal_binding_sha256 != genesis.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != genesis.baseline_plan_binding_sha256
        or value.run_id != genesis.run_id
        or value.plan_sha256 != genesis.plan_sha256
        or value.anchor_genesis_sequence != genesis.sequence
        or value.anchor_genesis_head_sha256 != genesis.head_sha256
        or value.previous_anchor_sequence < genesis.sequence
    ):
        _fail(code)
    if (
        value.previous_anchor_sequence == genesis.sequence
        and value.previous_anchor_head_sha256 != genesis.head_sha256
    ):
        _fail(code)
    return value


def _mint_verified_head(
    *,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    anchor_genesis_sequence: int,
    anchor_genesis_head_sha256: str,
    sequence: int,
    previous_head_sha256: str | None,
    head_sha256: str,
    commitment_sha256: str,
    controller_request_sha256: str | None,
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment | None,
    attestation_id: str | None,
    attested_at: datetime | None,
    expires_at: datetime | None,
    witness_attestation_sha256: str | None,
    canonical_head: bytes | None,
    verification_observed_at: datetime,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
    result = object.__new__(VerifiedPhysicalFullMatrixV4WitnessAnchorHead)
    for name, item in {
        "journal_binding_sha256": journal_binding_sha256,
        "baseline_plan_binding_sha256": baseline_plan_binding_sha256,
        "run_id": run_id,
        "plan_sha256": plan_sha256,
        "anchor_genesis_sequence": anchor_genesis_sequence,
        "anchor_genesis_head_sha256": anchor_genesis_head_sha256,
        "sequence": sequence,
        "previous_head_sha256": previous_head_sha256,
        "head_sha256": head_sha256,
        "commitment_sha256": commitment_sha256,
        "controller_request_sha256": controller_request_sha256,
        "commitment": commitment,
        "attestation_id": attestation_id,
        "attested_at": attested_at,
        "expires_at": expires_at,
        "witness_attestation_sha256": witness_attestation_sha256,
        "canonical_head": canonical_head,
        "verification_observed_at": verification_observed_at,
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }.items():
        object.__setattr__(result, name, item)
    return result


def _mint_verified_request(
    *,
    canonical_request: bytes,
    request_sha256: str,
    replay_id: str,
    issued_at: datetime,
    expires_at: datetime,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    anchor_genesis_sequence: int,
    anchor_genesis_head_sha256: str,
    predecessor_sequence: int,
    predecessor_head_sha256: str,
    commitment_sha256: str,
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment,
    verified_at: datetime,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest:
    result = object.__new__(VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest)
    for name, item in {
        "canonical_request": canonical_request,
        "request_sha256": request_sha256,
        "replay_id": replay_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "journal_binding_sha256": journal_binding_sha256,
        "baseline_plan_binding_sha256": baseline_plan_binding_sha256,
        "run_id": run_id,
        "plan_sha256": plan_sha256,
        "anchor_genesis_sequence": anchor_genesis_sequence,
        "anchor_genesis_head_sha256": anchor_genesis_head_sha256,
        "predecessor_sequence": predecessor_sequence,
        "predecessor_head_sha256": predecessor_head_sha256,
        "commitment_sha256": commitment_sha256,
        "commitment": commitment,
        "verified_at": verified_at,
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }.items():
        object.__setattr__(result, name, item)
    return result


def verified_physical_full_matrix_v4_witness_anchor_genesis_head(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
    """Verify the signed configured genesis and mint nonauthorizing provenance."""

    facts = _policy_facts(policy)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CLOCK_INVALID")
    genesis = facts.genesis
    return _mint_verified_head(
        journal_binding_sha256=genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=genesis.baseline_plan_binding_sha256,
        run_id=genesis.run_id,
        plan_sha256=genesis.plan_sha256,
        anchor_genesis_sequence=genesis.sequence,
        anchor_genesis_head_sha256=genesis.head_sha256,
        sequence=genesis.sequence,
        previous_head_sha256=None,
        head_sha256=genesis.head_sha256,
        commitment_sha256=_ZERO_SHA256,
        controller_request_sha256=None,
        commitment=None,
        attestation_id=None,
        attested_at=None,
        expires_at=None,
        witness_attestation_sha256=genesis.witness_attestation_sha256,
        canonical_head=canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(genesis),
        verification_observed_at=observed,
    )


def _require_verified_head_for_policy(
    value: object,
    *,
    facts: _PolicyFacts,
    now: datetime,
    code: str,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
    if type(value) is not VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
        _fail(code)
    genesis = facts.genesis
    if (
        value.journal_binding_sha256 != genesis.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != genesis.baseline_plan_binding_sha256
        or value.run_id != genesis.run_id
        or value.plan_sha256 != genesis.plan_sha256
        or value.anchor_genesis_sequence != genesis.sequence
        or value.anchor_genesis_head_sha256 != genesis.head_sha256
        or value.execution_authorized is not False
        or value.promotion_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail(code)
    if value.sequence == genesis.sequence:
        if (
            value.head_sha256 != genesis.head_sha256
            or value.previous_head_sha256 is not None
            or value.commitment_sha256 != _ZERO_SHA256
            or value.controller_request_sha256 is not None
            or value.commitment is not None
            or value.attestation_id is not None
            or value.attested_at is not None
            or value.expires_at is not None
            or value.witness_attestation_sha256 != genesis.witness_attestation_sha256
        ):
            _fail(code)
        return value
    if value.sequence <= genesis.sequence or value.commitment is None:
        _fail(code)
    if (
        value.previous_head_sha256 is None
        or value.controller_request_sha256 is None
        or value.attestation_id is None
        or value.attested_at is None
        or value.expires_at is None
        or value.witness_attestation_sha256 is None
        or value.expires_at < now
    ):
        _fail(code)
    _require_commitment_for_policy(value.commitment, facts=facts, code=code)
    if (
        value.sequence != value.commitment.previous_anchor_sequence + 1
        or value.previous_head_sha256 != value.commitment.previous_anchor_head_sha256
        or value.commitment_sha256
        != derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(value.commitment)
    ):
        _fail(code)
    return value


_REQUEST_UNSIGNED_FIELDS = frozenset(
    {
        "schema",
        "purpose",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "run_id",
        "plan_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "replay_id",
        "issued_at",
        "expires_at",
        "commitment_sha256",
        "commitment",
        "controller_key_id",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)
_REQUEST_FIELDS = _REQUEST_UNSIGNED_FIELDS | {"controller_signature"}


def _request_unsigned_body(
    *,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    anchor_genesis_sequence: int,
    anchor_genesis_head_sha256: str,
    replay_id: str,
    issued_at: datetime,
    expires_at: datetime,
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment,
    controller_key_id: str,
    code: str,
) -> dict[str, object]:
    commitment_body = _commitment_body(commitment, code=code)
    commitment_sha256 = hashlib.sha256(_canonical(commitment_body, code=code)).hexdigest()
    result = {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_REQUEST_SCHEMA,
        "purpose": _CONTROLLER_PURPOSE,
        "journal_binding_sha256": _sha256(journal_binding_sha256, code=code),
        "baseline_plan_binding_sha256": _sha256(baseline_plan_binding_sha256, code=code),
        "run_id": str(_uuid(run_id, code=code)),
        "plan_sha256": _sha256(plan_sha256, code=code),
        "anchor_genesis_sequence": _positive_int(
            anchor_genesis_sequence,
            code=code,
            permit_zero=True,
        ),
        "anchor_genesis_head_sha256": _sha256(
            anchor_genesis_head_sha256,
            code=code,
            permit_zero=True,
        ),
        "replay_id": _identifier(replay_id, code=code, pattern=_REPLAY_ID_RE),
        "issued_at": _render_timestamp(issued_at, code=code),
        "expires_at": _render_timestamp(expires_at, code=code),
        "commitment_sha256": commitment_sha256,
        "commitment": commitment_body,
        "controller_key_id": _identifier(controller_key_id, code=code, pattern=_KEY_ID_RE),
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }
    if (
        result["journal_binding_sha256"] != commitment_body["journal_binding_sha256"]
        or result["baseline_plan_binding_sha256"]
        != commitment_body["baseline_plan_binding_sha256"]
        or result["run_id"] != commitment_body["run_id"]
        or result["plan_sha256"] != commitment_body["plan_sha256"]
        or result["anchor_genesis_sequence"]
        != commitment_body["anchor_genesis_sequence"]
        or result["anchor_genesis_head_sha256"]
        != commitment_body["anchor_genesis_head_sha256"]
    ):
        _fail(code)
    return result


def _request_from_mapping(
    value: object,
    *,
    canonical_bytes: bytes,
    code: str,
) -> PhysicalFullMatrixV4WitnessAnchorControllerAppendRequest:
    if type(value) is not dict or set(value) != _REQUEST_FIELDS:
        _fail(code)
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_REQUEST_SCHEMA
        or value["purpose"] != _CONTROLLER_PURPOSE
        or value["execution_authorized"] is not False
        or value["promotion_authorized"] is not False
        or value["full_matrix_executed"] is not False
    ):
        _fail(code)
    commitment = _commitment_from_mapping(value["commitment"], code=code)
    result = PhysicalFullMatrixV4WitnessAnchorControllerAppendRequest(
        canonical_bytes=canonical_bytes,
        request_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        journal_binding_sha256=value["journal_binding_sha256"],  # type: ignore[arg-type]
        baseline_plan_binding_sha256=value["baseline_plan_binding_sha256"],  # type: ignore[arg-type]
        run_id=_uuid(value["run_id"], code=code),
        plan_sha256=value["plan_sha256"],  # type: ignore[arg-type]
        anchor_genesis_sequence=value["anchor_genesis_sequence"],  # type: ignore[arg-type]
        anchor_genesis_head_sha256=value["anchor_genesis_head_sha256"],  # type: ignore[arg-type]
        replay_id=value["replay_id"],  # type: ignore[arg-type]
        issued_at=_timestamp(value["issued_at"], code=code),
        expires_at=_timestamp(value["expires_at"], code=code),
        commitment_sha256=value["commitment_sha256"],  # type: ignore[arg-type]
        commitment=commitment,
        controller_signature=_signature_from_mapping(value["controller_signature"], code=code),
    )
    unsigned = _request_unsigned_body(
        journal_binding_sha256=result.journal_binding_sha256,
        baseline_plan_binding_sha256=result.baseline_plan_binding_sha256,
        run_id=result.run_id,
        plan_sha256=result.plan_sha256,
        anchor_genesis_sequence=result.anchor_genesis_sequence,
        anchor_genesis_head_sha256=result.anchor_genesis_head_sha256,
        replay_id=result.replay_id,
        issued_at=result.issued_at,
        expires_at=result.expires_at,
        commitment=result.commitment,
        controller_key_id=result.controller_signature.key_id,
        code=code,
    )
    if (
        _sha256(result.commitment_sha256, code=code)
        != derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(result.commitment)
        or {key: item for key, item in value.items() if key != "controller_signature"}
        != unsigned
    ):
        _fail(code)
    return result


def parse_physical_full_matrix_v4_witness_anchor_controller_append_request(
    value: object,
) -> PhysicalFullMatrixV4WitnessAnchorControllerAppendRequest:
    """Strictly parse one canonical controller request before trusting it."""

    raw = value if type(value) is bytes else None
    decoded = _parse_canonical_object(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_ENCODING_INVALID",
    )
    assert raw is not None
    return _request_from_mapping(
        decoded,
        canonical_bytes=raw,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_INVALID",
    )


def _validate_request_timing(
    request: PhysicalFullMatrixV4WitnessAnchorControllerAppendRequest,
    *,
    facts: _PolicyFacts,
    now: datetime,
    code: str,
) -> None:
    if (
        request.expires_at < request.issued_at
        or request.expires_at - request.issued_at
        > timedelta(seconds=facts.maximum_request_lifetime_seconds)
        or request.issued_at > now + timedelta(seconds=facts.maximum_future_skew_seconds)
        or request.expires_at < now
        or request.commitment.occurred_at
        > request.issued_at + timedelta(seconds=facts.maximum_future_skew_seconds)
        or request.commitment.occurred_at > request.expires_at
        or now - request.commitment.occurred_at
        > timedelta(seconds=facts.maximum_commitment_age_seconds)
    ):
        _fail(code)


def _replay_not_seen(
    value: str,
    *,
    seen: Collection[str],
    code: str,
) -> None:
    if isinstance(seen, (str, bytes)) or not isinstance(seen, Collection):
        _fail(code)
    if value in seen:
        _fail(code)


def build_physical_full_matrix_v4_witness_anchor_controller_append_request(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    predecessor: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment,
    replay_id: str,
    issued_at: datetime,
    expires_at: datetime,
    controller_private_key: Ed25519PrivateKey,
) -> bytes:
    """Build a canonical controller signature; this performs no append action."""

    facts = _policy_facts(policy)
    issued = _utc(issued_at, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_INVALID")
    expires = _utc(expires_at, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_INVALID")
    prior = _require_verified_anchor_predecessor_for_policy(
        predecessor,
        facts=facts,
        now=issued,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_INVALID",
    )
    _require_commitment_for_policy(
        commitment,
        facts=facts,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_INVALID",
    )
    if (
        commitment.previous_anchor_sequence != prior.sequence
        or commitment.previous_anchor_head_sha256 != prior.head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
    unsigned = _request_unsigned_body(
        journal_binding_sha256=facts.genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.genesis.baseline_plan_binding_sha256,
        run_id=facts.genesis.run_id,
        plan_sha256=facts.genesis.plan_sha256,
        anchor_genesis_sequence=facts.genesis.sequence,
        anchor_genesis_head_sha256=facts.genesis.head_sha256,
        replay_id=replay_id,
        issued_at=issued,
        expires_at=expires,
        commitment=commitment,
        controller_key_id=facts.controller_key_id,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_INVALID",
    )
    provisional = PhysicalFullMatrixV4WitnessAnchorControllerAppendRequest(
        canonical_bytes=b"placeholder\n",
        request_sha256="0" * 64,
        journal_binding_sha256=facts.genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.genesis.baseline_plan_binding_sha256,
        run_id=facts.genesis.run_id,
        plan_sha256=facts.genesis.plan_sha256,
        anchor_genesis_sequence=facts.genesis.sequence,
        anchor_genesis_head_sha256=facts.genesis.head_sha256,
        replay_id=replay_id,
        issued_at=issued,
        expires_at=expires,
        commitment_sha256=derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(commitment),
        commitment=commitment,
        controller_signature=_Signature(key_id=facts.controller_key_id, signature=b"0" * 64),
    )
    _validate_request_timing(
        provisional,
        facts=facts,
        now=issued,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_TIME_INVALID",
    )
    signer = _private_key_matches(
        controller_private_key,
        expected_public_key=facts.controller_public_key,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CONTROLLER_SIGNER_INVALID",
    )
    signature = signer.sign(
        _canonical(unsigned, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_INVALID")
    )
    return _canonical(
        {
            **unsigned,
            "controller_signature": _signature_body(
                key_id=facts.controller_key_id,
                signature=signature,
            ),
        },
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_INVALID",
    )


def verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
    value: object,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    predecessor: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    now: datetime,
    seen_replay_ids: Collection[str] = (),
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest:
    """Verify a bounded, unused controller request against an exact head."""

    request = parse_physical_full_matrix_v4_witness_anchor_controller_append_request(value)
    facts = _policy_facts(policy)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CLOCK_INVALID")
    # Preserve the legacy verifier's diagnostic for a stale V1 head while
    # still refusing to let that expiring type enter the V2 immutable path.
    if (
        type(predecessor) is VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        and predecessor.sequence != facts.genesis.sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
    prior = _require_verified_anchor_predecessor_for_policy(
        predecessor,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_INVALID",
    )
    _validate_request_timing(
        request,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_TIME_INVALID",
    )
    _replay_not_seen(
        request.replay_id,
        seen=seen_replay_ids,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_REPLAYED",
    )
    if request.controller_signature.key_id != facts.controller_key_id:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CONTROLLER_SIGNER_MISMATCH")
    _require_commitment_for_policy(
        request.commitment,
        facts=facts,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_BINDING_MISMATCH",
    )
    if (
        request.journal_binding_sha256 != facts.genesis.journal_binding_sha256
        or request.baseline_plan_binding_sha256
        != facts.genesis.baseline_plan_binding_sha256
        or request.run_id != facts.genesis.run_id
        or request.plan_sha256 != facts.genesis.plan_sha256
        or request.anchor_genesis_sequence != facts.genesis.sequence
        or request.anchor_genesis_head_sha256 != facts.genesis.head_sha256
        or request.commitment.previous_anchor_sequence != prior.sequence
        or request.commitment.previous_anchor_head_sha256 != prior.head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
    unsigned = _request_unsigned_body(
        journal_binding_sha256=request.journal_binding_sha256,
        baseline_plan_binding_sha256=request.baseline_plan_binding_sha256,
        run_id=request.run_id,
        plan_sha256=request.plan_sha256,
        anchor_genesis_sequence=request.anchor_genesis_sequence,
        anchor_genesis_head_sha256=request.anchor_genesis_head_sha256,
        replay_id=request.replay_id,
        issued_at=request.issued_at,
        expires_at=request.expires_at,
        commitment=request.commitment,
        controller_key_id=request.controller_signature.key_id,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_INVALID",
    )
    try:
        Ed25519PublicKey.from_public_bytes(facts.controller_public_key).verify(
            request.controller_signature.signature,
            _canonical(unsigned, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_REQUEST_INVALID"),
        )
    except (InvalidSignature, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CONTROLLER_SIGNATURE_INVALID")
    return _mint_verified_request(
        canonical_request=request.canonical_bytes,
        request_sha256=request.request_sha256,
        replay_id=request.replay_id,
        issued_at=request.issued_at,
        expires_at=request.expires_at,
        journal_binding_sha256=request.journal_binding_sha256,
        baseline_plan_binding_sha256=request.baseline_plan_binding_sha256,
        run_id=request.run_id,
        plan_sha256=request.plan_sha256,
        anchor_genesis_sequence=request.anchor_genesis_sequence,
        anchor_genesis_head_sha256=request.anchor_genesis_head_sha256,
        predecessor_sequence=prior.sequence,
        predecessor_head_sha256=prior.head_sha256,
        commitment_sha256=request.commitment_sha256,
        commitment=request.commitment,
        verified_at=observed,
    )


_HEAD_BASE_FIELDS = frozenset(
    {
        "schema",
        "purpose",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "run_id",
        "plan_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "sequence",
        "previous_head_sha256",
        "head_sha256",
        "commitment_sha256",
        "controller_request_sha256",
        "commitment",
        "attestation_id",
        "attested_at",
        "expires_at",
        "witness_key_id",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)
_HEAD_SIGNED_FIELDS = _HEAD_BASE_FIELDS | {"witness_attestation_sha256"}
_HEAD_FIELDS = _HEAD_SIGNED_FIELDS | {"witness_signature"}


def _derive_head_sha256(
    *,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    anchor_genesis_sequence: int,
    anchor_genesis_head_sha256: str,
    sequence: int,
    previous_head_sha256: str,
    commitment_sha256: str,
    controller_request_sha256: str,
    code: str,
) -> str:
    """Deterministically derive a non-genesis immutable head identifier."""

    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_SCHEMA,
                "purpose": "physical-full-matrix-v4-witness-anchor-chain-head-v1",
                "journal_binding_sha256": _sha256(journal_binding_sha256, code=code),
                "baseline_plan_binding_sha256": _sha256(
                    baseline_plan_binding_sha256,
                    code=code,
                ),
                "run_id": str(_uuid(run_id, code=code)),
                "plan_sha256": _sha256(plan_sha256, code=code),
                "anchor_genesis_sequence": _positive_int(
                    anchor_genesis_sequence,
                    code=code,
                    permit_zero=True,
                ),
                "anchor_genesis_head_sha256": _sha256(
                    anchor_genesis_head_sha256,
                    code=code,
                    permit_zero=True,
                ),
                "sequence": _positive_int(sequence, code=code),
                "previous_head_sha256": _sha256(
                    previous_head_sha256,
                    code=code,
                    permit_zero=True,
                ),
                "commitment_sha256": _sha256(commitment_sha256, code=code),
                "controller_request_sha256": _sha256(
                    controller_request_sha256,
                    code=code,
                ),
            },
            code=code,
        )
    ).hexdigest()


def _head_base_body(
    *,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    anchor_genesis_sequence: int,
    anchor_genesis_head_sha256: str,
    sequence: int,
    previous_head_sha256: str,
    head_sha256: str,
    controller_request_sha256: str,
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment,
    attestation_id: str,
    attested_at: datetime,
    expires_at: datetime,
    witness_key_id: str,
    code: str,
) -> dict[str, object]:
    commitment_body = _commitment_body(commitment, code=code)
    commitment_sha256 = hashlib.sha256(_canonical(commitment_body, code=code)).hexdigest()
    result = {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_SCHEMA,
        "purpose": _WITNESS_PURPOSE,
        "journal_binding_sha256": _sha256(journal_binding_sha256, code=code),
        "baseline_plan_binding_sha256": _sha256(baseline_plan_binding_sha256, code=code),
        "run_id": str(_uuid(run_id, code=code)),
        "plan_sha256": _sha256(plan_sha256, code=code),
        "anchor_genesis_sequence": _positive_int(
            anchor_genesis_sequence,
            code=code,
            permit_zero=True,
        ),
        "anchor_genesis_head_sha256": _sha256(
            anchor_genesis_head_sha256,
            code=code,
            permit_zero=True,
        ),
        "sequence": _positive_int(sequence, code=code),
        "previous_head_sha256": _sha256(
            previous_head_sha256,
            code=code,
            permit_zero=True,
        ),
        "head_sha256": _sha256(head_sha256, code=code),
        "commitment_sha256": commitment_sha256,
        "controller_request_sha256": _sha256(controller_request_sha256, code=code),
        "commitment": commitment_body,
        "attestation_id": _identifier(attestation_id, code=code, pattern=_REPLAY_ID_RE),
        "attested_at": _render_timestamp(attested_at, code=code),
        "expires_at": _render_timestamp(expires_at, code=code),
        "witness_key_id": _identifier(witness_key_id, code=code, pattern=_KEY_ID_RE),
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }
    if (
        result["journal_binding_sha256"] != commitment_body["journal_binding_sha256"]
        or result["baseline_plan_binding_sha256"]
        != commitment_body["baseline_plan_binding_sha256"]
        or result["run_id"] != commitment_body["run_id"]
        or result["plan_sha256"] != commitment_body["plan_sha256"]
        or result["anchor_genesis_sequence"]
        != commitment_body["anchor_genesis_sequence"]
        or result["anchor_genesis_head_sha256"]
        != commitment_body["anchor_genesis_head_sha256"]
        or result["sequence"] != commitment_body["previous_anchor_sequence"] + 1
        or result["previous_head_sha256"]
        != commitment_body["previous_anchor_head_sha256"]
    ):
        _fail(code)
    expected_head = _derive_head_sha256(
        journal_binding_sha256=result["journal_binding_sha256"],  # type: ignore[arg-type]
        baseline_plan_binding_sha256=result["baseline_plan_binding_sha256"],  # type: ignore[arg-type]
        run_id=_uuid(result["run_id"], code=code),
        plan_sha256=result["plan_sha256"],  # type: ignore[arg-type]
        anchor_genesis_sequence=result["anchor_genesis_sequence"],  # type: ignore[arg-type]
        anchor_genesis_head_sha256=result["anchor_genesis_head_sha256"],  # type: ignore[arg-type]
        sequence=result["sequence"],  # type: ignore[arg-type]
        previous_head_sha256=result["previous_head_sha256"],  # type: ignore[arg-type]
        commitment_sha256=commitment_sha256,
        controller_request_sha256=result["controller_request_sha256"],  # type: ignore[arg-type]
        code=code,
    )
    if result["head_sha256"] != expected_head:
        _fail(code)
    return result


def _head_from_mapping(
    value: object,
    *,
    canonical_bytes: bytes,
    code: str,
) -> PhysicalFullMatrixV4WitnessAnchorWitnessHead:
    if type(value) is not dict or set(value) != _HEAD_FIELDS:
        _fail(code)
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_SCHEMA
        or value["purpose"] != _WITNESS_PURPOSE
        or value["execution_authorized"] is not False
        or value["promotion_authorized"] is not False
        or value["full_matrix_executed"] is not False
    ):
        _fail(code)
    commitment = _commitment_from_mapping(value["commitment"], code=code)
    signature = _signature_from_mapping(value["witness_signature"], code=code)
    result = PhysicalFullMatrixV4WitnessAnchorWitnessHead(
        canonical_bytes=canonical_bytes,
        journal_binding_sha256=value["journal_binding_sha256"],  # type: ignore[arg-type]
        baseline_plan_binding_sha256=value["baseline_plan_binding_sha256"],  # type: ignore[arg-type]
        run_id=_uuid(value["run_id"], code=code),
        plan_sha256=value["plan_sha256"],  # type: ignore[arg-type]
        anchor_genesis_sequence=value["anchor_genesis_sequence"],  # type: ignore[arg-type]
        anchor_genesis_head_sha256=value["anchor_genesis_head_sha256"],  # type: ignore[arg-type]
        sequence=value["sequence"],  # type: ignore[arg-type]
        previous_head_sha256=value["previous_head_sha256"],  # type: ignore[arg-type]
        head_sha256=value["head_sha256"],  # type: ignore[arg-type]
        commitment_sha256=value["commitment_sha256"],  # type: ignore[arg-type]
        controller_request_sha256=value["controller_request_sha256"],  # type: ignore[arg-type]
        commitment=commitment,
        attestation_id=value["attestation_id"],  # type: ignore[arg-type]
        attested_at=_timestamp(value["attested_at"], code=code),
        expires_at=_timestamp(value["expires_at"], code=code),
        witness_attestation_sha256=value["witness_attestation_sha256"],  # type: ignore[arg-type]
        witness_signature=signature,
    )
    base = _head_base_body(
        journal_binding_sha256=result.journal_binding_sha256,
        baseline_plan_binding_sha256=result.baseline_plan_binding_sha256,
        run_id=result.run_id,
        plan_sha256=result.plan_sha256,
        anchor_genesis_sequence=result.anchor_genesis_sequence,
        anchor_genesis_head_sha256=result.anchor_genesis_head_sha256,
        sequence=result.sequence,
        previous_head_sha256=result.previous_head_sha256,
        head_sha256=result.head_sha256,
        controller_request_sha256=result.controller_request_sha256,
        commitment=result.commitment,
        attestation_id=result.attestation_id,
        attested_at=result.attested_at,
        expires_at=result.expires_at,
        witness_key_id=result.witness_signature.key_id,
        code=code,
    )
    expected_attestation = hashlib.sha256(_canonical(base, code=code)).hexdigest()
    if (
        _sha256(result.commitment_sha256, code=code)
        != derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(result.commitment)
        or _sha256(result.witness_attestation_sha256, code=code) != expected_attestation
        or {
            key: item
            for key, item in value.items()
            if key not in {"witness_attestation_sha256", "witness_signature"}
        }
        != base
        or {
            key: item for key, item in value.items() if key != "witness_signature"
        }
        != {**base, "witness_attestation_sha256": expected_attestation}
    ):
        _fail(code)
    return result


def parse_physical_full_matrix_v4_witness_anchor_witness_head(
    value: object,
) -> PhysicalFullMatrixV4WitnessAnchorWitnessHead:
    """Strictly parse a canonical immutable Witness-head/readback envelope."""

    _legacy_one_layer_head_fenced()

    raw = value if type(value) is bytes else None
    decoded = _parse_canonical_object(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_ENCODING_INVALID",
    )
    assert raw is not None
    return _head_from_mapping(
        decoded,
        canonical_bytes=raw,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID",
    )


def _validate_head_timing(
    value: PhysicalFullMatrixV4WitnessAnchorWitnessHead,
    *,
    facts: _PolicyFacts,
    now: datetime,
    code: str,
) -> None:
    if (
        value.expires_at <= value.attested_at
        or value.expires_at - value.attested_at
        > timedelta(seconds=facts.maximum_attestation_lifetime_seconds)
        or value.attested_at > now + timedelta(seconds=facts.maximum_future_skew_seconds)
        or value.expires_at < now
        or value.commitment.occurred_at
        > value.attested_at + timedelta(seconds=facts.maximum_future_skew_seconds)
    ):
        _fail(code)


def _require_verified_request_for_policy(
    value: object,
    *,
    facts: _PolicyFacts,
    now: datetime,
    code: str,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest:
    if type(value) is not VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest:
        _fail(code)
    genesis = facts.genesis
    if (
        value.journal_binding_sha256 != genesis.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != genesis.baseline_plan_binding_sha256
        or value.run_id != genesis.run_id
        or value.plan_sha256 != genesis.plan_sha256
        or value.anchor_genesis_sequence != genesis.sequence
        or value.anchor_genesis_head_sha256 != genesis.head_sha256
        or value.execution_authorized is not False
        or value.promotion_authorized is not False
        or value.full_matrix_executed is not False
        or value.expires_at < now
        or _sha256(value.request_sha256, code=code)
        != hashlib.sha256(value.canonical_request).hexdigest()
        or _identifier(value.replay_id, code=code, pattern=_REPLAY_ID_RE) != value.replay_id
    ):
        _fail(code)
    _require_commitment_for_policy(value.commitment, facts=facts, code=code)
    if (
        value.commitment_sha256
        != derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(value.commitment)
        or value.predecessor_sequence != value.commitment.previous_anchor_sequence
        or value.predecessor_head_sha256 != value.commitment.previous_anchor_head_sha256
    ):
        _fail(code)
    return value


def _prepare_witness_head_signed_bytes(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    predecessor: VerifiedPhysicalFullMatrixV4WitnessAnchorHead,
    append_request: VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest,
    attestation_id: str,
    attested_at: datetime,
    expires_at: datetime,
) -> bytes:
    """Construct exact unsigned Witness-head bytes after all semantic fences."""

    facts = _policy_facts(policy)
    attested = _utc(attested_at, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID")
    expires = _utc(expires_at, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID")
    prior = _require_verified_head_for_policy(
        predecessor,
        facts=facts,
        now=attested,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_INVALID",
    )
    verified_request = _require_verified_request_for_policy(
        append_request,
        facts=facts,
        now=attested,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_REQUEST_INVALID",
    )
    if (
        verified_request.predecessor_sequence != prior.sequence
        or verified_request.predecessor_head_sha256 != prior.head_sha256
        or attested > verified_request.expires_at
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
    sequence = prior.sequence + 1
    head_sha256 = _derive_head_sha256(
        journal_binding_sha256=facts.genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.genesis.baseline_plan_binding_sha256,
        run_id=facts.genesis.run_id,
        plan_sha256=facts.genesis.plan_sha256,
        anchor_genesis_sequence=facts.genesis.sequence,
        anchor_genesis_head_sha256=facts.genesis.head_sha256,
        sequence=sequence,
        previous_head_sha256=prior.head_sha256,
        commitment_sha256=verified_request.commitment_sha256,
        controller_request_sha256=verified_request.request_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID",
    )
    base = _head_base_body(
        journal_binding_sha256=facts.genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.genesis.baseline_plan_binding_sha256,
        run_id=facts.genesis.run_id,
        plan_sha256=facts.genesis.plan_sha256,
        anchor_genesis_sequence=facts.genesis.sequence,
        anchor_genesis_head_sha256=facts.genesis.head_sha256,
        sequence=sequence,
        previous_head_sha256=prior.head_sha256,
        head_sha256=head_sha256,
        controller_request_sha256=verified_request.request_sha256,
        commitment=verified_request.commitment,
        attestation_id=attestation_id,
        attested_at=attested,
        expires_at=expires,
        witness_key_id=facts.witness_key_id,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID",
    )
    provisional = PhysicalFullMatrixV4WitnessAnchorWitnessHead(
        canonical_bytes=b"placeholder\n",
        journal_binding_sha256=facts.genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.genesis.baseline_plan_binding_sha256,
        run_id=facts.genesis.run_id,
        plan_sha256=facts.genesis.plan_sha256,
        anchor_genesis_sequence=facts.genesis.sequence,
        anchor_genesis_head_sha256=facts.genesis.head_sha256,
        sequence=sequence,
        previous_head_sha256=prior.head_sha256,
        head_sha256=head_sha256,
        commitment_sha256=verified_request.commitment_sha256,
        controller_request_sha256=verified_request.request_sha256,
        commitment=verified_request.commitment,
        attestation_id=attestation_id,
        attested_at=attested,
        expires_at=expires,
        witness_attestation_sha256=hashlib.sha256(
            _canonical(base, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID")
        ).hexdigest(),
        witness_signature=_Signature(key_id=facts.witness_key_id, signature=b"0" * 64),
    )
    _validate_head_timing(
        provisional,
        facts=facts,
        now=attested,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_TIME_INVALID",
    )
    attestation_sha256 = provisional.witness_attestation_sha256
    signed = {**base, "witness_attestation_sha256": attestation_sha256}
    return _canonical(
        signed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID",
    )


def _mint_witness_head_signing_payload(
    canonical_signed_head: bytes,
) -> PhysicalFullMatrixV4WitnessAnchorWitnessHeadSigningPayload:
    result = object.__new__(PhysicalFullMatrixV4WitnessAnchorWitnessHeadSigningPayload)
    object.__setattr__(result, "canonical_signed_head", canonical_signed_head)
    return result


def prepare_physical_full_matrix_v4_witness_anchor_witness_head(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    predecessor: VerifiedPhysicalFullMatrixV4WitnessAnchorHead,
    append_request: VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest,
    attestation_id: str,
    attested_at: datetime,
    expires_at: datetime,
) -> PhysicalFullMatrixV4WitnessAnchorWitnessHeadSigningPayload:
    """Mint exact unsigned bytes for an injected root Witness signer.

    The output is not a head and carries no authority by itself.  It merely
    prevents a ledger from ever receiving the private Witness key in order to
    construct the canonical bytes it asks its root-owned signer to sign.
    """

    _legacy_one_layer_head_fenced()
    return _mint_witness_head_signing_payload(
        _prepare_witness_head_signed_bytes(
            policy=policy,
            predecessor=predecessor,
            append_request=append_request,
            attestation_id=attestation_id,
            attested_at=attested_at,
            expires_at=expires_at,
        )
    )


def finalize_physical_full_matrix_v4_witness_anchor_witness_head(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    signing_payload: PhysicalFullMatrixV4WitnessAnchorWitnessHeadSigningPayload,
    witness_signature: bytes,
    now: datetime,
) -> bytes:
    """Attach and immediately verify a root-signer result without I/O."""

    _legacy_one_layer_head_fenced()

    if type(signing_payload) is not PhysicalFullMatrixV4WitnessAnchorWitnessHeadSigningPayload:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNING_PAYLOAD_INVALID")
    if type(witness_signature) is not bytes or len(witness_signature) != 64:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_WITNESS_SIGNATURE_INVALID")
    facts = _policy_facts(policy)
    signed = _parse_canonical_object(
        signing_payload.canonical_signed_head,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNING_PAYLOAD_INVALID",
    )
    if set(signed) != _HEAD_SIGNED_FIELDS or signed.get("witness_key_id") != facts.witness_key_id:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_SIGNING_PAYLOAD_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(facts.witness_public_key).verify(
            witness_signature,
            signing_payload.canonical_signed_head,
        )
    except (InvalidSignature, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_WITNESS_SIGNATURE_INVALID")
    result = _canonical(
        {
            **signed,
            "witness_signature": _signature_body(
                key_id=facts.witness_key_id,
                signature=witness_signature,
            ),
        },
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID",
    )
    # Parse and timely-verify the newly composed envelope before it leaves
    # this pure boundary.  No predecessor is needed here: prepare() received
    # and checked it before the signing payload was minted.
    verify_physical_full_matrix_v4_witness_anchor_witness_head(
        result,
        policy=policy,
        now=now,
    )
    return result


def build_physical_full_matrix_v4_witness_anchor_witness_head(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    predecessor: VerifiedPhysicalFullMatrixV4WitnessAnchorHead,
    append_request: VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest,
    attestation_id: str,
    attested_at: datetime,
    expires_at: datetime,
    witness_private_key: Ed25519PrivateKey,
) -> bytes:
    """Convenience pure signer for tests; runtimes should use prepare/finalize."""

    _legacy_one_layer_head_fenced()

    facts = _policy_facts(policy)
    signer = _private_key_matches(
        witness_private_key,
        expected_public_key=facts.witness_public_key,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_WITNESS_SIGNER_INVALID",
    )
    payload = prepare_physical_full_matrix_v4_witness_anchor_witness_head(
        policy=policy,
        predecessor=predecessor,
        append_request=append_request,
        attestation_id=attestation_id,
        attested_at=attested_at,
        expires_at=expires_at,
    )
    return finalize_physical_full_matrix_v4_witness_anchor_witness_head(
        policy=policy,
        signing_payload=payload,
        witness_signature=signer.sign(payload.canonical_signed_head),
        now=attested_at,
    )


def verify_physical_full_matrix_v4_witness_anchor_witness_head(
    value: object,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    now: datetime,
    expected_predecessor: VerifiedPhysicalFullMatrixV4WitnessAnchorHead | None = None,
    seen_attestation_ids: Collection[str] = (),
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
    """Verify a timely Witness head; optionally bind it to one exact predecessor."""

    _legacy_one_layer_head_fenced()

    head = parse_physical_full_matrix_v4_witness_anchor_witness_head(value)
    facts = _policy_facts(policy)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CLOCK_INVALID")
    _validate_head_timing(
        head,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_TIME_INVALID",
    )
    _replay_not_seen(
        head.attestation_id,
        seen=seen_attestation_ids,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_ATTESTATION_REPLAYED",
    )
    if head.witness_signature.key_id != facts.witness_key_id:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_WITNESS_SIGNER_MISMATCH")
    _require_commitment_for_policy(
        head.commitment,
        facts=facts,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_BINDING_MISMATCH",
    )
    if (
        head.journal_binding_sha256 != facts.genesis.journal_binding_sha256
        or head.baseline_plan_binding_sha256
        != facts.genesis.baseline_plan_binding_sha256
        or head.run_id != facts.genesis.run_id
        or head.plan_sha256 != facts.genesis.plan_sha256
        or head.anchor_genesis_sequence != facts.genesis.sequence
        or head.anchor_genesis_head_sha256 != facts.genesis.head_sha256
        or head.sequence != head.commitment.previous_anchor_sequence + 1
        or head.previous_head_sha256 != head.commitment.previous_anchor_head_sha256
        or head.sequence <= facts.genesis.sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_BINDING_MISMATCH")
    if expected_predecessor is not None:
        prior = _require_verified_head_for_policy(
            expected_predecessor,
            facts=facts,
            now=observed,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_INVALID",
        )
        if (
            head.sequence != prior.sequence + 1
            or head.previous_head_sha256 != prior.head_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
    elif (
        head.commitment.previous_anchor_sequence == facts.genesis.sequence
        and head.previous_head_sha256 != facts.genesis.head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
    base = _head_base_body(
        journal_binding_sha256=head.journal_binding_sha256,
        baseline_plan_binding_sha256=head.baseline_plan_binding_sha256,
        run_id=head.run_id,
        plan_sha256=head.plan_sha256,
        anchor_genesis_sequence=head.anchor_genesis_sequence,
        anchor_genesis_head_sha256=head.anchor_genesis_head_sha256,
        sequence=head.sequence,
        previous_head_sha256=head.previous_head_sha256,
        head_sha256=head.head_sha256,
        controller_request_sha256=head.controller_request_sha256,
        commitment=head.commitment,
        attestation_id=head.attestation_id,
        attested_at=head.attested_at,
        expires_at=head.expires_at,
        witness_key_id=head.witness_signature.key_id,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID",
    )
    try:
        Ed25519PublicKey.from_public_bytes(facts.witness_public_key).verify(
            head.witness_signature.signature,
            _canonical(
                {**base, "witness_attestation_sha256": head.witness_attestation_sha256},
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_HEAD_INVALID",
            ),
        )
    except (InvalidSignature, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_WITNESS_SIGNATURE_INVALID")
    return _mint_verified_head(
        journal_binding_sha256=head.journal_binding_sha256,
        baseline_plan_binding_sha256=head.baseline_plan_binding_sha256,
        run_id=head.run_id,
        plan_sha256=head.plan_sha256,
        anchor_genesis_sequence=head.anchor_genesis_sequence,
        anchor_genesis_head_sha256=head.anchor_genesis_head_sha256,
        sequence=head.sequence,
        previous_head_sha256=head.previous_head_sha256,
        head_sha256=head.head_sha256,
        commitment_sha256=head.commitment_sha256,
        controller_request_sha256=head.controller_request_sha256,
        commitment=head.commitment,
        attestation_id=head.attestation_id,
        attested_at=head.attested_at,
        expires_at=head.expires_at,
        witness_attestation_sha256=head.witness_attestation_sha256,
        canonical_head=head.canonical_bytes,
        verification_observed_at=observed,
    )


def verify_physical_full_matrix_v4_witness_anchor_append_head(
    value: object,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    predecessor: VerifiedPhysicalFullMatrixV4WitnessAnchorHead,
    append_request: VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest,
    now: datetime,
    seen_attestation_ids: Collection[str] = (),
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
    """Verify that a Witness response is the exact append result for one request."""

    _legacy_one_layer_head_fenced()

    facts = _policy_facts(policy)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CLOCK_INVALID")
    verified_request = _require_verified_request_for_policy(
        append_request,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_REQUEST_INVALID",
    )
    head = verify_physical_full_matrix_v4_witness_anchor_witness_head(
        value,
        policy=policy,
        now=observed,
        expected_predecessor=predecessor,
        seen_attestation_ids=seen_attestation_ids,
    )
    if (
        head.controller_request_sha256 != verified_request.request_sha256
        or head.commitment_sha256 != verified_request.commitment_sha256
        or head.commitment != verified_request.commitment
        or head.sequence != verified_request.predecessor_sequence + 1
        or head.previous_head_sha256 != verified_request.predecessor_head_sha256
        or head.attested_at is None
        or head.attested_at > verified_request.expires_at
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_RESULT_MISMATCH")
    return head


# V2 stable-anchor protocol -------------------------------------------------
#
# The old ``WitnessHead`` above deliberately remains available for existing
# V1 callers/tests.  It is not reachable through any of the V2 transport
# functions below.  A V2 append record is immutable and never expires; a
# separate ReadObservation is the only short-lived object.


def _require_verified_anchor_predecessor_for_policy(
    value: object,
    *,
    facts: _PolicyFacts,
    now: datetime,
    code: str,
) -> (
    VerifiedPhysicalFullMatrixV4WitnessAnchorHead
    | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
):
    """Accept only configured genesis or a stable V2 immutable predecessor.

    ``VerifiedPhysical...Head`` is retained solely as the verifier-minted
    configured genesis object.  In particular, an old time-bounded head is
    never a V2 predecessor even if it has not expired yet.
    """

    if type(value) is VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
        genesis = _require_verified_head_for_policy(
            value,
            facts=facts,
            now=now,
            code=code,
        )
        if (
            genesis.sequence != facts.genesis.sequence
            or genesis.head_sha256 != facts.genesis.head_sha256
            or genesis.previous_head_sha256 is not None
            or genesis.commitment_sha256 != _ZERO_SHA256
            or genesis.controller_request_sha256 is not None
            or genesis.commitment is not None
            or genesis.canonical_head
            != canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
                facts.genesis
            )
        ):
            _fail(code)
        return genesis
    if type(value) is VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead:
        return _require_verified_immutable_head_for_policy(value, facts=facts, code=code)
    _fail(code)


_IMMUTABLE_HEAD_BASE_FIELDS = frozenset(
    {
        "schema",
        "purpose",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "run_id",
        "plan_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "sequence",
        "previous_head_sha256",
        "head_sha256",
        "commitment_sha256",
        "controller_request_sha256",
        "commitment",
        "witness_key_id",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)
_IMMUTABLE_HEAD_SIGNED_FIELDS = _IMMUTABLE_HEAD_BASE_FIELDS | {
    "immutable_attestation_sha256"
}
_IMMUTABLE_HEAD_FIELDS = _IMMUTABLE_HEAD_SIGNED_FIELDS | {"witness_signature"}


def _derive_immutable_head_sha256(
    *,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    anchor_genesis_sequence: int,
    anchor_genesis_head_sha256: str,
    sequence: int,
    previous_head_sha256: str,
    commitment_sha256: str,
    controller_request_sha256: str,
    code: str,
) -> str:
    """Derive a stable V2 append identifier in a domain distinct from V1."""

    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_SCHEMA,
                "purpose": "physical-full-matrix-v4-witness-anchor-immutable-chain-head-v1",
                "journal_binding_sha256": _sha256(journal_binding_sha256, code=code),
                "baseline_plan_binding_sha256": _sha256(
                    baseline_plan_binding_sha256,
                    code=code,
                ),
                "run_id": str(_uuid(run_id, code=code)),
                "plan_sha256": _sha256(plan_sha256, code=code),
                "anchor_genesis_sequence": _positive_int(
                    anchor_genesis_sequence,
                    code=code,
                    permit_zero=True,
                ),
                "anchor_genesis_head_sha256": _sha256(
                    anchor_genesis_head_sha256,
                    code=code,
                    permit_zero=True,
                ),
                "sequence": _positive_int(sequence, code=code),
                "previous_head_sha256": _sha256(
                    previous_head_sha256,
                    code=code,
                    permit_zero=True,
                ),
                "commitment_sha256": _sha256(commitment_sha256, code=code),
                "controller_request_sha256": _sha256(
                    controller_request_sha256,
                    code=code,
                ),
            },
            code=code,
        )
    ).hexdigest()


def _immutable_head_base_body(
    *,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    run_id: UUID,
    plan_sha256: str,
    anchor_genesis_sequence: int,
    anchor_genesis_head_sha256: str,
    sequence: int,
    previous_head_sha256: str,
    head_sha256: str,
    controller_request_sha256: str,
    commitment: PhysicalFullMatrixV4WitnessAnchorCommitment,
    witness_key_id: str,
    code: str,
) -> dict[str, object]:
    commitment_body = _commitment_body(commitment, code=code)
    commitment_sha256 = hashlib.sha256(_canonical(commitment_body, code=code)).hexdigest()
    result = {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_SCHEMA,
        "purpose": _IMMUTABLE_HEAD_PURPOSE,
        "journal_binding_sha256": _sha256(journal_binding_sha256, code=code),
        "baseline_plan_binding_sha256": _sha256(baseline_plan_binding_sha256, code=code),
        "run_id": str(_uuid(run_id, code=code)),
        "plan_sha256": _sha256(plan_sha256, code=code),
        "anchor_genesis_sequence": _positive_int(
            anchor_genesis_sequence,
            code=code,
            permit_zero=True,
        ),
        "anchor_genesis_head_sha256": _sha256(
            anchor_genesis_head_sha256,
            code=code,
            permit_zero=True,
        ),
        "sequence": _positive_int(sequence, code=code),
        "previous_head_sha256": _sha256(
            previous_head_sha256,
            code=code,
            permit_zero=True,
        ),
        "head_sha256": _sha256(head_sha256, code=code),
        "commitment_sha256": commitment_sha256,
        "controller_request_sha256": _sha256(controller_request_sha256, code=code),
        "commitment": commitment_body,
        "witness_key_id": _identifier(witness_key_id, code=code, pattern=_KEY_ID_RE),
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }
    if (
        result["journal_binding_sha256"] != commitment_body["journal_binding_sha256"]
        or result["baseline_plan_binding_sha256"]
        != commitment_body["baseline_plan_binding_sha256"]
        or result["run_id"] != commitment_body["run_id"]
        or result["plan_sha256"] != commitment_body["plan_sha256"]
        or result["anchor_genesis_sequence"]
        != commitment_body["anchor_genesis_sequence"]
        or result["anchor_genesis_head_sha256"]
        != commitment_body["anchor_genesis_head_sha256"]
        or result["sequence"] != commitment_body["previous_anchor_sequence"] + 1
        or result["previous_head_sha256"]
        != commitment_body["previous_anchor_head_sha256"]
    ):
        _fail(code)
    expected_head = _derive_immutable_head_sha256(
        journal_binding_sha256=result["journal_binding_sha256"],  # type: ignore[arg-type]
        baseline_plan_binding_sha256=result["baseline_plan_binding_sha256"],  # type: ignore[arg-type]
        run_id=_uuid(result["run_id"], code=code),
        plan_sha256=result["plan_sha256"],  # type: ignore[arg-type]
        anchor_genesis_sequence=result["anchor_genesis_sequence"],  # type: ignore[arg-type]
        anchor_genesis_head_sha256=result["anchor_genesis_head_sha256"],  # type: ignore[arg-type]
        sequence=result["sequence"],  # type: ignore[arg-type]
        previous_head_sha256=result["previous_head_sha256"],  # type: ignore[arg-type]
        commitment_sha256=commitment_sha256,
        controller_request_sha256=result["controller_request_sha256"],  # type: ignore[arg-type]
        code=code,
    )
    if result["head_sha256"] != expected_head:
        _fail(code)
    return result


def _immutable_head_from_mapping(
    value: object,
    *,
    canonical_bytes: bytes,
    code: str,
) -> PhysicalFullMatrixV4WitnessAnchorImmutableHead:
    if type(value) is not dict or set(value) != _IMMUTABLE_HEAD_FIELDS:
        _fail(code)
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_SCHEMA
        or value["purpose"] != _IMMUTABLE_HEAD_PURPOSE
        or value["execution_authorized"] is not False
        or value["promotion_authorized"] is not False
        or value["full_matrix_executed"] is not False
    ):
        _fail(code)
    commitment = _commitment_from_mapping(value["commitment"], code=code)
    signature = _signature_from_mapping(value["witness_signature"], code=code)
    result = PhysicalFullMatrixV4WitnessAnchorImmutableHead(
        canonical_bytes=canonical_bytes,
        journal_binding_sha256=value["journal_binding_sha256"],  # type: ignore[arg-type]
        baseline_plan_binding_sha256=value["baseline_plan_binding_sha256"],  # type: ignore[arg-type]
        run_id=_uuid(value["run_id"], code=code),
        plan_sha256=value["plan_sha256"],  # type: ignore[arg-type]
        anchor_genesis_sequence=value["anchor_genesis_sequence"],  # type: ignore[arg-type]
        anchor_genesis_head_sha256=value["anchor_genesis_head_sha256"],  # type: ignore[arg-type]
        sequence=value["sequence"],  # type: ignore[arg-type]
        previous_head_sha256=value["previous_head_sha256"],  # type: ignore[arg-type]
        head_sha256=value["head_sha256"],  # type: ignore[arg-type]
        commitment_sha256=value["commitment_sha256"],  # type: ignore[arg-type]
        controller_request_sha256=value["controller_request_sha256"],  # type: ignore[arg-type]
        commitment=commitment,
        immutable_attestation_sha256=value["immutable_attestation_sha256"],  # type: ignore[arg-type]
        witness_signature=signature,
    )
    base = _immutable_head_base_body(
        journal_binding_sha256=result.journal_binding_sha256,
        baseline_plan_binding_sha256=result.baseline_plan_binding_sha256,
        run_id=result.run_id,
        plan_sha256=result.plan_sha256,
        anchor_genesis_sequence=result.anchor_genesis_sequence,
        anchor_genesis_head_sha256=result.anchor_genesis_head_sha256,
        sequence=result.sequence,
        previous_head_sha256=result.previous_head_sha256,
        head_sha256=result.head_sha256,
        controller_request_sha256=result.controller_request_sha256,
        commitment=result.commitment,
        witness_key_id=result.witness_signature.key_id,
        code=code,
    )
    expected_attestation = hashlib.sha256(_canonical(base, code=code)).hexdigest()
    if (
        _sha256(result.commitment_sha256, code=code)
        != derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(result.commitment)
        or _sha256(result.immutable_attestation_sha256, code=code) != expected_attestation
        or {
            key: item
            for key, item in value.items()
            if key not in {"immutable_attestation_sha256", "witness_signature"}
        }
        != base
        or {key: item for key, item in value.items() if key != "witness_signature"}
        != {**base, "immutable_attestation_sha256": expected_attestation}
    ):
        _fail(code)
    return result


def parse_physical_full_matrix_v4_witness_anchor_immutable_head(
    value: object,
) -> PhysicalFullMatrixV4WitnessAnchorImmutableHead:
    """Strictly parse an immutable V2 append record; no legacy schema is accepted."""

    raw = value if type(value) is bytes else None
    decoded = _parse_canonical_object(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_ENCODING_INVALID",
    )
    assert raw is not None
    return _immutable_head_from_mapping(
        decoded,
        canonical_bytes=raw,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_INVALID",
    )


def _mint_verified_immutable_head(
    *,
    head: PhysicalFullMatrixV4WitnessAnchorImmutableHead,
    verification_observed_at: datetime,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead:
    result = object.__new__(VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead)
    for name, item in {
        "canonical_immutable_head": head.canonical_bytes,
        "immutable_head_canonical_sha256": hashlib.sha256(head.canonical_bytes).hexdigest(),
        "journal_binding_sha256": head.journal_binding_sha256,
        "baseline_plan_binding_sha256": head.baseline_plan_binding_sha256,
        "run_id": head.run_id,
        "plan_sha256": head.plan_sha256,
        "anchor_genesis_sequence": head.anchor_genesis_sequence,
        "anchor_genesis_head_sha256": head.anchor_genesis_head_sha256,
        "sequence": head.sequence,
        "previous_head_sha256": head.previous_head_sha256,
        "head_sha256": head.head_sha256,
        "commitment_sha256": head.commitment_sha256,
        "controller_request_sha256": head.controller_request_sha256,
        "commitment": head.commitment,
        "immutable_attestation_sha256": head.immutable_attestation_sha256,
        "verification_observed_at": verification_observed_at,
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }.items():
        object.__setattr__(result, name, item)
    return result


def _verify_immutable_head_signature(
    head: PhysicalFullMatrixV4WitnessAnchorImmutableHead,
    *,
    facts: _PolicyFacts,
    code: str,
) -> None:
    if head.witness_signature.key_id != facts.witness_key_id:
        _fail(code)
    base = _immutable_head_base_body(
        journal_binding_sha256=head.journal_binding_sha256,
        baseline_plan_binding_sha256=head.baseline_plan_binding_sha256,
        run_id=head.run_id,
        plan_sha256=head.plan_sha256,
        anchor_genesis_sequence=head.anchor_genesis_sequence,
        anchor_genesis_head_sha256=head.anchor_genesis_head_sha256,
        sequence=head.sequence,
        previous_head_sha256=head.previous_head_sha256,
        head_sha256=head.head_sha256,
        controller_request_sha256=head.controller_request_sha256,
        commitment=head.commitment,
        witness_key_id=head.witness_signature.key_id,
        code=code,
    )
    try:
        Ed25519PublicKey.from_public_bytes(facts.witness_public_key).verify(
            head.witness_signature.signature,
            _canonical(
                {**base, "immutable_attestation_sha256": head.immutable_attestation_sha256},
                code=code,
            ),
        )
    except (InvalidSignature, ValueError):
        _fail(code)


def _require_verified_immutable_head_for_policy(
    value: object,
    *,
    facts: _PolicyFacts,
    code: str,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead:
    if type(value) is not VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead:
        _fail(code)
    if (
        value.execution_authorized is not False
        or value.promotion_authorized is not False
        or value.full_matrix_executed is not False
        or value.journal_binding_sha256 != facts.genesis.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != facts.genesis.baseline_plan_binding_sha256
        or value.run_id != facts.genesis.run_id
        or value.plan_sha256 != facts.genesis.plan_sha256
        or value.anchor_genesis_sequence != facts.genesis.sequence
        or value.anchor_genesis_head_sha256 != facts.genesis.head_sha256
        or value.sequence <= facts.genesis.sequence
        or value.previous_head_sha256 == _ZERO_SHA256
        or value.commitment_sha256 == _ZERO_SHA256
        or value.controller_request_sha256 == _ZERO_SHA256
        or value.immutable_attestation_sha256 == _ZERO_SHA256
        or type(value.canonical_immutable_head) is not bytes
        or hashlib.sha256(value.canonical_immutable_head).hexdigest()
        != value.immutable_head_canonical_sha256
    ):
        _fail(code)
    parsed = parse_physical_full_matrix_v4_witness_anchor_immutable_head(
        value.canonical_immutable_head
    )
    if (
        parsed.journal_binding_sha256 != value.journal_binding_sha256
        or parsed.baseline_plan_binding_sha256 != value.baseline_plan_binding_sha256
        or parsed.run_id != value.run_id
        or parsed.plan_sha256 != value.plan_sha256
        or parsed.anchor_genesis_sequence != value.anchor_genesis_sequence
        or parsed.anchor_genesis_head_sha256 != value.anchor_genesis_head_sha256
        or parsed.sequence != value.sequence
        or parsed.previous_head_sha256 != value.previous_head_sha256
        or parsed.head_sha256 != value.head_sha256
        or parsed.commitment_sha256 != value.commitment_sha256
        or parsed.controller_request_sha256 != value.controller_request_sha256
        or parsed.commitment != value.commitment
        or parsed.immutable_attestation_sha256 != value.immutable_attestation_sha256
    ):
        _fail(code)
    _require_commitment_for_policy(parsed.commitment, facts=facts, code=code)
    if (
        parsed.sequence != parsed.commitment.previous_anchor_sequence + 1
        or parsed.previous_head_sha256 != parsed.commitment.previous_anchor_head_sha256
        or parsed.commitment_sha256
        != derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(parsed.commitment)
    ):
        _fail(code)
    _verify_immutable_head_signature(parsed, facts=facts, code=code)
    return value


def _mint_immutable_head_signing_payload(
    canonical_signed_immutable_head: bytes,
) -> PhysicalFullMatrixV4WitnessAnchorImmutableHeadSigningPayload:
    result = object.__new__(PhysicalFullMatrixV4WitnessAnchorImmutableHeadSigningPayload)
    object.__setattr__(result, "canonical_signed_immutable_head", canonical_signed_immutable_head)
    return result


def prepare_physical_full_matrix_v4_witness_anchor_immutable_head(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    predecessor: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    append_request: VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest,
    now: datetime,
) -> PhysicalFullMatrixV4WitnessAnchorImmutableHeadSigningPayload:
    """Prepare permanent append evidence for the immutable signer domain.

    The signer receives only the canonical signed bytes.  The short-lived
    observation timestamp is intentionally absent from this permanent record.
    """

    facts = _policy_facts(policy)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CLOCK_INVALID")
    prior = _require_verified_anchor_predecessor_for_policy(
        predecessor,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_INVALID",
    )
    request = _require_verified_request_for_policy(
        append_request,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_REQUEST_INVALID",
    )
    if (
        request.predecessor_sequence != prior.sequence
        or request.predecessor_head_sha256 != prior.head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
    sequence = prior.sequence + 1
    head_sha256 = _derive_immutable_head_sha256(
        journal_binding_sha256=facts.genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.genesis.baseline_plan_binding_sha256,
        run_id=facts.genesis.run_id,
        plan_sha256=facts.genesis.plan_sha256,
        anchor_genesis_sequence=facts.genesis.sequence,
        anchor_genesis_head_sha256=facts.genesis.head_sha256,
        sequence=sequence,
        previous_head_sha256=prior.head_sha256,
        commitment_sha256=request.commitment_sha256,
        controller_request_sha256=request.request_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_INVALID",
    )
    base = _immutable_head_base_body(
        journal_binding_sha256=facts.genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.genesis.baseline_plan_binding_sha256,
        run_id=facts.genesis.run_id,
        plan_sha256=facts.genesis.plan_sha256,
        anchor_genesis_sequence=facts.genesis.sequence,
        anchor_genesis_head_sha256=facts.genesis.head_sha256,
        sequence=sequence,
        previous_head_sha256=prior.head_sha256,
        head_sha256=head_sha256,
        controller_request_sha256=request.request_sha256,
        commitment=request.commitment,
        witness_key_id=facts.witness_key_id,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_INVALID",
    )
    attestation = hashlib.sha256(
        _canonical(base, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_INVALID")
    ).hexdigest()
    return _mint_immutable_head_signing_payload(
        _canonical(
            {**base, "immutable_attestation_sha256": attestation},
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_INVALID",
        )
    )


def finalize_physical_full_matrix_v4_witness_anchor_immutable_head(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    signing_payload: PhysicalFullMatrixV4WitnessAnchorImmutableHeadSigningPayload,
    witness_signature: bytes,
    now: datetime,
) -> bytes:
    """Attach a root-signer signature to already-validated immutable bytes."""

    if type(signing_payload) is not PhysicalFullMatrixV4WitnessAnchorImmutableHeadSigningPayload:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_PAYLOAD_INVALID")
    if type(witness_signature) is not bytes or len(witness_signature) != 64:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_SIGNATURE_INVALID")
    facts = _policy_facts(policy)
    signed = _parse_canonical_object(
        signing_payload.canonical_signed_immutable_head,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_PAYLOAD_INVALID",
    )
    if (
        set(signed) != _IMMUTABLE_HEAD_SIGNED_FIELDS
        or signed.get("schema") != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_SCHEMA
        or signed.get("purpose") != _IMMUTABLE_HEAD_PURPOSE
        or signed.get("witness_key_id") != facts.witness_key_id
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_PAYLOAD_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(facts.witness_public_key).verify(
            witness_signature,
            signing_payload.canonical_signed_immutable_head,
        )
    except (InvalidSignature, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_SIGNATURE_INVALID")
    result = _canonical(
        {
            **signed,
            "witness_signature": _signature_body(
                key_id=facts.witness_key_id,
                signature=witness_signature,
            ),
        },
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_INVALID",
    )
    # A sealed payload already passed predecessor/request checks in prepare().
    # This post-sign check guarantees that only the new schema can leave the
    # signing boundary; the exact-current facts are extracted from that sealed
    # payload solely to avoid imposing a TTL or an in-process predecessor on a
    # later immutable append.
    parsed = parse_physical_full_matrix_v4_witness_anchor_immutable_head(result)
    verify_physical_full_matrix_v4_witness_anchor_immutable_head(
        result,
        policy=policy,
        now=now,
        expected_current_sequence=parsed.sequence,
        expected_current_head_sha256=parsed.head_sha256,
    )
    return result


def build_physical_full_matrix_v4_witness_anchor_immutable_head(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    predecessor: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    append_request: VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest,
    now: datetime,
    witness_private_key: Ed25519PrivateKey,
) -> bytes:
    """Pure test helper; production supplies the signature through finalize."""

    facts = _policy_facts(policy)
    signer = _private_key_matches(
        witness_private_key,
        expected_public_key=facts.witness_public_key,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_WITNESS_SIGNER_INVALID",
    )
    payload = prepare_physical_full_matrix_v4_witness_anchor_immutable_head(
        policy=policy,
        predecessor=predecessor,
        append_request=append_request,
        now=now,
    )
    return finalize_physical_full_matrix_v4_witness_anchor_immutable_head(
        policy=policy,
        signing_payload=payload,
        witness_signature=signer.sign(payload.canonical_signed_immutable_head),
        now=now,
    )


def verify_physical_full_matrix_v4_witness_anchor_immutable_head(
    value: object,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    now: datetime,
    expected_predecessor: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
        | None
    ) = None,
    append_request: VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest | None = None,
    expected_current_sequence: int | None = None,
    expected_current_head_sha256: str | None = None,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead:
    """Verify stable V2 evidence without an expiry or arbitrary gap mode.

    A restart may have only durable local ``sequence/head`` facts rather than
    a verifier-minted predecessor object.  The two ``expected_current_*``
    values are therefore deliberately narrow: the returned head must be that
    exact current head or exactly one signed successor.  They are not a
    general unlinked-chain escape hatch.
    """

    head = parse_physical_full_matrix_v4_witness_anchor_immutable_head(value)
    facts = _policy_facts(policy)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CLOCK_INVALID")
    has_expected_current = (
        expected_current_sequence is not None
        or expected_current_head_sha256 is not None
    )
    if has_expected_current:
        if expected_predecessor is not None:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_EXPECTED_CURRENT_INVALID")
        expected_sequence = _positive_int(
            expected_current_sequence,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_EXPECTED_CURRENT_INVALID",
            permit_zero=True,
        )
        expected_head = _sha256(
            expected_current_head_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_EXPECTED_CURRENT_INVALID",
            permit_zero=True,
        )
    else:
        expected_sequence = None
        expected_head = None
    _require_commitment_for_policy(
        head.commitment,
        facts=facts,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_BINDING_MISMATCH",
    )
    if (
        head.journal_binding_sha256 != facts.genesis.journal_binding_sha256
        or head.baseline_plan_binding_sha256 != facts.genesis.baseline_plan_binding_sha256
        or head.run_id != facts.genesis.run_id
        or head.plan_sha256 != facts.genesis.plan_sha256
        or head.anchor_genesis_sequence != facts.genesis.sequence
        or head.anchor_genesis_head_sha256 != facts.genesis.head_sha256
        or head.sequence != head.commitment.previous_anchor_sequence + 1
        or head.previous_head_sha256 != head.commitment.previous_anchor_head_sha256
        or head.sequence <= facts.genesis.sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_BINDING_MISMATCH")
    if expected_predecessor is not None:
        prior = _require_verified_anchor_predecessor_for_policy(
            expected_predecessor,
            facts=facts,
            now=observed,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_INVALID",
        )
        if (
            head.sequence != prior.sequence + 1
            or head.previous_head_sha256 != prior.head_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
    elif has_expected_current:
        assert expected_sequence is not None and expected_head is not None
        if not (
            (
                head.sequence == expected_sequence
                and head.head_sha256 == expected_head
            )
            or (
                head.sequence == expected_sequence + 1
                and head.previous_head_sha256 == expected_head
            )
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_EXPECTED_CURRENT_MISMATCH")
    elif (
        head.sequence != facts.genesis.sequence + 1
        or head.previous_head_sha256 != facts.genesis.head_sha256
    ):
        # A standalone verifier may establish only the first non-genesis link.
        # Later links must be supplied with their exact verified predecessor.
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
    if append_request is not None:
        request = _require_verified_request_for_policy(
            append_request,
            facts=facts,
            now=observed,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_REQUEST_INVALID",
        )
        if (
            head.controller_request_sha256 != request.request_sha256
            or head.commitment_sha256 != request.commitment_sha256
            or head.commitment != request.commitment
            or head.sequence != request.predecessor_sequence + 1
            or head.previous_head_sha256 != request.predecessor_head_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_APPEND_RESULT_MISMATCH")
    _verify_immutable_head_signature(
        head,
        facts=facts,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_SIGNATURE_INVALID",
    )
    return _mint_verified_immutable_head(
        head=head,
        verification_observed_at=observed,
    )


@dataclass(frozen=True)
class _ObservationAnchor:
    canonical_anchor_head: bytes
    immutable_head_canonical_sha256: str
    sequence: int
    previous_head_sha256: str
    head_sha256: str
    commitment_sha256: str
    controller_request_sha256: str
    immutable_attestation_sha256: str
    is_genesis: bool


def _observation_anchor_for_policy(
    value: object,
    *,
    facts: _PolicyFacts,
    now: datetime,
    code: str,
) -> _ObservationAnchor:
    """Return the exact stable material a read observation is allowed to cite."""

    predecessor = _require_verified_anchor_predecessor_for_policy(
        value,
        facts=facts,
        now=now,
        code=code,
    )
    if type(predecessor) is VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
        canonical = canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
            facts.genesis
        )
        return _ObservationAnchor(
            canonical_anchor_head=canonical,
            immutable_head_canonical_sha256=hashlib.sha256(canonical).hexdigest(),
            sequence=facts.genesis.sequence,
            previous_head_sha256=_ZERO_SHA256,
            head_sha256=facts.genesis.head_sha256,
            commitment_sha256=_ZERO_SHA256,
            controller_request_sha256=_ZERO_SHA256,
            immutable_attestation_sha256=facts.genesis.witness_attestation_sha256,
            is_genesis=True,
        )
    return _ObservationAnchor(
        canonical_anchor_head=predecessor.canonical_immutable_head,
        immutable_head_canonical_sha256=predecessor.immutable_head_canonical_sha256,
        sequence=predecessor.sequence,
        previous_head_sha256=predecessor.previous_head_sha256,
        head_sha256=predecessor.head_sha256,
        commitment_sha256=predecessor.commitment_sha256,
        controller_request_sha256=predecessor.controller_request_sha256,
        immutable_attestation_sha256=predecessor.immutable_attestation_sha256,
        is_genesis=False,
    )


_READ_OBSERVATION_BASE_FIELDS = frozenset(
    {
        "schema",
        "purpose",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "run_id",
        "plan_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "immutable_head_canonical_sha256",
        "sequence",
        "previous_head_sha256",
        "head_sha256",
        "commitment_sha256",
        "controller_request_sha256",
        "immutable_attestation_sha256",
        "read_challenge",
        "observation_id",
        "observed_at",
        "expires_at",
        "witness_key_id",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)
_READ_OBSERVATION_SIGNED_FIELDS = _READ_OBSERVATION_BASE_FIELDS | {
    "observation_attestation_sha256"
}
_READ_OBSERVATION_FIELDS = _READ_OBSERVATION_SIGNED_FIELDS | {"witness_signature"}


def _read_observation_base_body(
    *,
    facts: _PolicyFacts,
    anchor: _ObservationAnchor,
    read_challenge: str,
    observation_id: str,
    observed_at: datetime,
    expires_at: datetime,
    witness_key_id: str,
    code: str,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_SCHEMA,
        "purpose": _READ_OBSERVATION_PURPOSE,
        "journal_binding_sha256": _sha256(
            facts.genesis.journal_binding_sha256,
            code=code,
        ),
        "baseline_plan_binding_sha256": _sha256(
            facts.genesis.baseline_plan_binding_sha256,
            code=code,
        ),
        "run_id": str(_uuid(facts.genesis.run_id, code=code)),
        "plan_sha256": _sha256(facts.genesis.plan_sha256, code=code),
        "anchor_genesis_sequence": _positive_int(
            facts.genesis.sequence,
            code=code,
            permit_zero=True,
        ),
        "anchor_genesis_head_sha256": _sha256(
            facts.genesis.head_sha256,
            code=code,
            permit_zero=True,
        ),
        "immutable_head_canonical_sha256": _sha256(
            anchor.immutable_head_canonical_sha256,
            code=code,
        ),
        "sequence": _positive_int(
            anchor.sequence,
            code=code,
            permit_zero=True,
        ),
        "previous_head_sha256": _sha256(
            anchor.previous_head_sha256,
            code=code,
            permit_zero=True,
        ),
        "head_sha256": _sha256(
            anchor.head_sha256,
            code=code,
            permit_zero=True,
        ),
        "commitment_sha256": _sha256(
            anchor.commitment_sha256,
            code=code,
            permit_zero=True,
        ),
        "controller_request_sha256": _sha256(
            anchor.controller_request_sha256,
            code=code,
            permit_zero=True,
        ),
        "immutable_attestation_sha256": _sha256(
            anchor.immutable_attestation_sha256,
            code=code,
        ),
        "read_challenge": _identifier(
            read_challenge,
            code=code,
            pattern=_REPLAY_ID_RE,
        ),
        "observation_id": _identifier(
            observation_id,
            code=code,
            pattern=_REPLAY_ID_RE,
        ),
        "observed_at": _render_timestamp(observed_at, code=code),
        "expires_at": _render_timestamp(expires_at, code=code),
        "witness_key_id": _identifier(witness_key_id, code=code, pattern=_KEY_ID_RE),
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }


def _read_observation_from_mapping(
    value: object,
    *,
    canonical_bytes: bytes,
    code: str,
) -> PhysicalFullMatrixV4WitnessAnchorReadObservation:
    if type(value) is not dict or set(value) != _READ_OBSERVATION_FIELDS:
        _fail(code)
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_SCHEMA
        or value["purpose"] != _READ_OBSERVATION_PURPOSE
        or value["execution_authorized"] is not False
        or value["promotion_authorized"] is not False
        or value["full_matrix_executed"] is not False
    ):
        _fail(code)
    signature = _signature_from_mapping(value["witness_signature"], code=code)
    result = PhysicalFullMatrixV4WitnessAnchorReadObservation(
        canonical_bytes=canonical_bytes,
        journal_binding_sha256=value["journal_binding_sha256"],  # type: ignore[arg-type]
        baseline_plan_binding_sha256=value["baseline_plan_binding_sha256"],  # type: ignore[arg-type]
        run_id=_uuid(value["run_id"], code=code),
        plan_sha256=value["plan_sha256"],  # type: ignore[arg-type]
        anchor_genesis_sequence=value["anchor_genesis_sequence"],  # type: ignore[arg-type]
        anchor_genesis_head_sha256=value["anchor_genesis_head_sha256"],  # type: ignore[arg-type]
        immutable_head_canonical_sha256=value["immutable_head_canonical_sha256"],  # type: ignore[arg-type]
        sequence=value["sequence"],  # type: ignore[arg-type]
        previous_head_sha256=value["previous_head_sha256"],  # type: ignore[arg-type]
        head_sha256=value["head_sha256"],  # type: ignore[arg-type]
        commitment_sha256=value["commitment_sha256"],  # type: ignore[arg-type]
        controller_request_sha256=value["controller_request_sha256"],  # type: ignore[arg-type]
        immutable_attestation_sha256=value["immutable_attestation_sha256"],  # type: ignore[arg-type]
        read_challenge=value["read_challenge"],  # type: ignore[arg-type]
        observation_id=value["observation_id"],  # type: ignore[arg-type]
        observed_at=_timestamp(value["observed_at"], code=code),
        expires_at=_timestamp(value["expires_at"], code=code),
        observation_attestation_sha256=value["observation_attestation_sha256"],  # type: ignore[arg-type]
        witness_signature=signature,
    )
    anchor = _ObservationAnchor(
        canonical_anchor_head=b"untrusted-observation-parser-anchor",
        immutable_head_canonical_sha256=result.immutable_head_canonical_sha256,
        sequence=result.sequence,
        previous_head_sha256=result.previous_head_sha256,
        head_sha256=result.head_sha256,
        commitment_sha256=result.commitment_sha256,
        controller_request_sha256=result.controller_request_sha256,
        immutable_attestation_sha256=result.immutable_attestation_sha256,
        is_genesis=False,
    )
    # The policy pins are reconstructed from the wire fields here only to
    # validate canonical grammar.  verify_read_observation() later binds all
    # of them to the configured policy and exact trusted anchor.
    pseudo_genesis = PhysicalFullMatrixV4WitnessAnchorGenesis(
        schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SCHEMA,
        journal_binding_sha256=result.journal_binding_sha256,
        baseline_plan_binding_sha256=result.baseline_plan_binding_sha256,
        run_id=result.run_id,
        plan_sha256=result.plan_sha256,
        sequence=result.anchor_genesis_sequence,
        head_sha256=result.anchor_genesis_head_sha256,
        witness_key_id=signature.key_id,
        witness_attestation_sha256=result.immutable_attestation_sha256,
        witness_signature=b"0" * 64,
    )
    # Avoid invoking the strict signed-genesis constructor for untrusted
    # grammar.  The helper only reads the four public campaign pins below.
    pseudo_facts = _PolicyFacts(
        genesis=pseudo_genesis,
        controller_public_key=b"0" * 32,
        witness_public_key=b"0" * 32,
        controller_key_id=signature.key_id,
        witness_key_id=signature.key_id,
        maximum_request_lifetime_seconds=1,
        maximum_attestation_lifetime_seconds=1,
        maximum_commitment_age_seconds=1,
        maximum_future_skew_seconds=1,
    )
    base = _read_observation_base_body(
        facts=pseudo_facts,
        anchor=anchor,
        read_challenge=result.read_challenge,
        observation_id=result.observation_id,
        observed_at=result.observed_at,
        expires_at=result.expires_at,
        witness_key_id=signature.key_id,
        code=code,
    )
    expected_attestation = hashlib.sha256(_canonical(base, code=code)).hexdigest()
    if (
        _sha256(result.observation_attestation_sha256, code=code) != expected_attestation
        or {key: item for key, item in value.items() if key not in {"observation_attestation_sha256", "witness_signature"}}
        != base
        or {key: item for key, item in value.items() if key != "witness_signature"}
        != {**base, "observation_attestation_sha256": expected_attestation}
    ):
        _fail(code)
    return result


def parse_physical_full_matrix_v4_witness_anchor_read_observation(
    value: object,
) -> PhysicalFullMatrixV4WitnessAnchorReadObservation:
    """Strictly parse a V2 read proof; it is not an append record."""

    raw = value if type(value) is bytes else None
    decoded = _parse_canonical_object(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_ENCODING_INVALID",
    )
    assert raw is not None
    return _read_observation_from_mapping(
        decoded,
        canonical_bytes=raw,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_INVALID",
    )


def _validate_read_observation_timing(
    observation: PhysicalFullMatrixV4WitnessAnchorReadObservation,
    *,
    facts: _PolicyFacts,
    now: datetime,
    code: str,
) -> None:
    if (
        observation.expires_at <= observation.observed_at
        or observation.expires_at - observation.observed_at
        > timedelta(seconds=facts.maximum_attestation_lifetime_seconds)
        or observation.observed_at
        > now + timedelta(seconds=facts.maximum_future_skew_seconds)
        or observation.expires_at < now
    ):
        _fail(code)


def _mint_verified_read_observation(
    *,
    observation: PhysicalFullMatrixV4WitnessAnchorReadObservation,
    verification_observed_at: datetime,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorReadObservation:
    result = object.__new__(VerifiedPhysicalFullMatrixV4WitnessAnchorReadObservation)
    for name, item in {
        "canonical_read_observation": observation.canonical_bytes,
        "immutable_head_canonical_sha256": observation.immutable_head_canonical_sha256,
        "sequence": observation.sequence,
        "head_sha256": observation.head_sha256,
        "read_challenge": observation.read_challenge,
        "observation_id": observation.observation_id,
        "observed_at": observation.observed_at,
        "expires_at": observation.expires_at,
        "observation_attestation_sha256": observation.observation_attestation_sha256,
        "verification_observed_at": verification_observed_at,
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }.items():
        object.__setattr__(result, name, item)
    return result


def _mint_read_observation_signing_payload(
    canonical_signed_read_observation: bytes,
) -> PhysicalFullMatrixV4WitnessAnchorReadObservationSigningPayload:
    result = object.__new__(PhysicalFullMatrixV4WitnessAnchorReadObservationSigningPayload)
    object.__setattr__(result, "canonical_signed_read_observation", canonical_signed_read_observation)
    return result


def prepare_physical_full_matrix_v4_witness_anchor_read_observation(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    anchor_head: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    read_challenge: str,
    observation_id: str,
    observed_at: datetime,
    expires_at: datetime,
) -> PhysicalFullMatrixV4WitnessAnchorReadObservationSigningPayload:
    """Prepare a fresh, challenge-bound observation without changing the anchor."""

    facts = _policy_facts(policy)
    observed = _utc(
        observed_at,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_INVALID",
    )
    expires = _utc(
        expires_at,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_INVALID",
    )
    anchor = _observation_anchor_for_policy(
        anchor_head,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_ANCHOR_INVALID",
    )
    base = _read_observation_base_body(
        facts=facts,
        anchor=anchor,
        read_challenge=read_challenge,
        observation_id=observation_id,
        observed_at=observed,
        expires_at=expires,
        witness_key_id=facts.witness_key_id,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_INVALID",
    )
    provisional = PhysicalFullMatrixV4WitnessAnchorReadObservation(
        canonical_bytes=b"placeholder\n",
        journal_binding_sha256=facts.genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.genesis.baseline_plan_binding_sha256,
        run_id=facts.genesis.run_id,
        plan_sha256=facts.genesis.plan_sha256,
        anchor_genesis_sequence=facts.genesis.sequence,
        anchor_genesis_head_sha256=facts.genesis.head_sha256,
        immutable_head_canonical_sha256=anchor.immutable_head_canonical_sha256,
        sequence=anchor.sequence,
        previous_head_sha256=anchor.previous_head_sha256,
        head_sha256=anchor.head_sha256,
        commitment_sha256=anchor.commitment_sha256,
        controller_request_sha256=anchor.controller_request_sha256,
        immutable_attestation_sha256=anchor.immutable_attestation_sha256,
        read_challenge=read_challenge,
        observation_id=observation_id,
        observed_at=observed,
        expires_at=expires,
        observation_attestation_sha256=hashlib.sha256(
            _canonical(
                base,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_INVALID",
            )
        ).hexdigest(),
        witness_signature=_Signature(key_id=facts.witness_key_id, signature=b"0" * 64),
    )
    _validate_read_observation_timing(
        provisional,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_TIME_INVALID",
    )
    return _mint_read_observation_signing_payload(
        _canonical(
            {
                **base,
                "observation_attestation_sha256": provisional.observation_attestation_sha256,
            },
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_INVALID",
        )
    )


def finalize_physical_full_matrix_v4_witness_anchor_read_observation(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    anchor_head: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    signing_payload: PhysicalFullMatrixV4WitnessAnchorReadObservationSigningPayload,
    witness_signature: bytes,
    now: datetime,
) -> bytes:
    """Attach a Witness signature to a challenge-bound fresh-read proof."""

    if type(signing_payload) is not PhysicalFullMatrixV4WitnessAnchorReadObservationSigningPayload:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_PAYLOAD_INVALID")
    if type(witness_signature) is not bytes or len(witness_signature) != 64:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_SIGNATURE_INVALID")
    facts = _policy_facts(policy)
    signed = _parse_canonical_object(
        signing_payload.canonical_signed_read_observation,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_PAYLOAD_INVALID",
    )
    if (
        set(signed) != _READ_OBSERVATION_SIGNED_FIELDS
        or signed.get("schema") != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_SCHEMA
        or signed.get("purpose") != _READ_OBSERVATION_PURPOSE
        or signed.get("witness_key_id") != facts.witness_key_id
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_PAYLOAD_INVALID")
    challenge = _identifier(
        signed.get("read_challenge"),
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_PAYLOAD_INVALID",
        pattern=_REPLAY_ID_RE,
    )
    try:
        Ed25519PublicKey.from_public_bytes(facts.witness_public_key).verify(
            witness_signature,
            signing_payload.canonical_signed_read_observation,
        )
    except (InvalidSignature, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_SIGNATURE_INVALID")
    result = _canonical(
        {
            **signed,
            "witness_signature": _signature_body(
                key_id=facts.witness_key_id,
                signature=witness_signature,
            ),
        },
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_INVALID",
    )
    verify_physical_full_matrix_v4_witness_anchor_read_observation(
        result,
        policy=policy,
        anchor_head=anchor_head,
        now=now,
        expected_read_challenge=challenge,
    )
    return result


def build_physical_full_matrix_v4_witness_anchor_read_observation(
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    anchor_head: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    read_challenge: str,
    observation_id: str,
    observed_at: datetime,
    expires_at: datetime,
    witness_private_key: Ed25519PrivateKey,
) -> bytes:
    """Pure test helper for a non-mutating, challenge-bound observation."""

    facts = _policy_facts(policy)
    signer = _private_key_matches(
        witness_private_key,
        expected_public_key=facts.witness_public_key,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_WITNESS_SIGNER_INVALID",
    )
    payload = prepare_physical_full_matrix_v4_witness_anchor_read_observation(
        policy=policy,
        anchor_head=anchor_head,
        read_challenge=read_challenge,
        observation_id=observation_id,
        observed_at=observed_at,
        expires_at=expires_at,
    )
    return finalize_physical_full_matrix_v4_witness_anchor_read_observation(
        policy=policy,
        anchor_head=anchor_head,
        signing_payload=payload,
        witness_signature=signer.sign(payload.canonical_signed_read_observation),
        now=observed_at,
    )


def verify_physical_full_matrix_v4_witness_anchor_read_observation(
    value: object,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    anchor_head: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    now: datetime,
    expected_read_challenge: str,
    seen_observation_ids: Collection[str] = (),
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorReadObservation:
    """Verify a fresh response against one exact stable anchor and challenge."""

    observation = parse_physical_full_matrix_v4_witness_anchor_read_observation(value)
    facts = _policy_facts(policy)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_CLOCK_INVALID")
    challenge = _identifier(
        expected_read_challenge,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_CHALLENGE_INVALID",
        pattern=_REPLAY_ID_RE,
    )
    _validate_read_observation_timing(
        observation,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_TIME_INVALID",
    )
    _replay_not_seen(
        observation.observation_id,
        seen=seen_observation_ids,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_REPLAYED",
    )
    anchor = _observation_anchor_for_policy(
        anchor_head,
        facts=facts,
        now=observed,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_ANCHOR_INVALID",
    )
    if (
        observation.witness_signature.key_id != facts.witness_key_id
        or observation.journal_binding_sha256 != facts.genesis.journal_binding_sha256
        or observation.baseline_plan_binding_sha256
        != facts.genesis.baseline_plan_binding_sha256
        or observation.run_id != facts.genesis.run_id
        or observation.plan_sha256 != facts.genesis.plan_sha256
        or observation.anchor_genesis_sequence != facts.genesis.sequence
        or observation.anchor_genesis_head_sha256 != facts.genesis.head_sha256
        or observation.immutable_head_canonical_sha256
        != anchor.immutable_head_canonical_sha256
        or observation.sequence != anchor.sequence
        or observation.previous_head_sha256 != anchor.previous_head_sha256
        or observation.head_sha256 != anchor.head_sha256
        or observation.commitment_sha256 != anchor.commitment_sha256
        or observation.controller_request_sha256 != anchor.controller_request_sha256
        or observation.immutable_attestation_sha256 != anchor.immutable_attestation_sha256
        or observation.read_challenge != challenge
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_BINDING_MISMATCH")
    if anchor.is_genesis:
        if (
            observation.previous_head_sha256 != _ZERO_SHA256
            or observation.commitment_sha256 != _ZERO_SHA256
            or observation.controller_request_sha256 != _ZERO_SHA256
            or observation.immutable_attestation_sha256
            != facts.genesis.witness_attestation_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_GENESIS_INVALID")
    elif (
        observation.previous_head_sha256 == _ZERO_SHA256
        or observation.commitment_sha256 == _ZERO_SHA256
        or observation.controller_request_sha256 == _ZERO_SHA256
        or observation.immutable_attestation_sha256 == _ZERO_SHA256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_GENESIS_INVALID")
    base = _read_observation_base_body(
        facts=facts,
        anchor=anchor,
        read_challenge=observation.read_challenge,
        observation_id=observation.observation_id,
        observed_at=observation.observed_at,
        expires_at=observation.expires_at,
        witness_key_id=observation.witness_signature.key_id,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_INVALID",
    )
    try:
        Ed25519PublicKey.from_public_bytes(facts.witness_public_key).verify(
            observation.witness_signature.signature,
            _canonical(
                {
                    **base,
                    "observation_attestation_sha256": observation.observation_attestation_sha256,
                },
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_OBSERVATION_INVALID",
            ),
        )
    except (InvalidSignature, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_OBSERVATION_SIGNATURE_INVALID")
    return _mint_verified_read_observation(
        observation=observation,
        verification_observed_at=observed,
    )


_TRANSPORT_ENVELOPE_FIELDS = frozenset(
    {
        "schema",
        "purpose",
        "canonical_anchor_head_base64",
        "canonical_read_observation_base64",
        "read_challenge",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)


def _strict_base64_bytes(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail(code)
    if (
        not result
        or len(result) > _MAX_WIRE_BYTES
        or base64.b64encode(result).decode("ascii") != value
    ):
        _fail(code)
    return result


def _transport_envelope_body(
    *,
    canonical_anchor_head: bytes,
    canonical_read_observation: bytes,
    read_challenge: str,
    code: str,
) -> dict[str, object]:
    if (
        type(canonical_anchor_head) is not bytes
        or not canonical_anchor_head
        or len(canonical_anchor_head) > _MAX_WIRE_BYTES
        or type(canonical_read_observation) is not bytes
        or not canonical_read_observation
        or len(canonical_read_observation) > _MAX_WIRE_BYTES
    ):
        _fail(code)
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_SCHEMA,
        "purpose": _TRANSPORT_ENVELOPE_PURPOSE,
        "canonical_anchor_head_base64": base64.b64encode(canonical_anchor_head).decode(
            "ascii"
        ),
        "canonical_read_observation_base64": base64.b64encode(
            canonical_read_observation
        ).decode("ascii"),
        "read_challenge": _identifier(
            read_challenge,
            code=code,
            pattern=_REPLAY_ID_RE,
        ),
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }


def build_physical_full_matrix_v4_witness_anchor_transport_envelope(
    *,
    canonical_anchor_head: bytes,
    canonical_read_observation: bytes,
    read_challenge: str,
) -> bytes:
    """Package exact V2 anchor + observation bytes; legacy heads are rejected."""

    try:
        parse_physical_full_matrix_v4_witness_anchor_genesis(canonical_anchor_head)
    except PhysicalFullMatrixV4WitnessAnchorWireError:
        parse_physical_full_matrix_v4_witness_anchor_immutable_head(canonical_anchor_head)
    observation = parse_physical_full_matrix_v4_witness_anchor_read_observation(
        canonical_read_observation
    )
    challenge = _identifier(
        read_challenge,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID",
        pattern=_REPLAY_ID_RE,
    )
    if observation.read_challenge != challenge:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_CHALLENGE_MISMATCH")
    return _canonical(
        _transport_envelope_body(
            canonical_anchor_head=canonical_anchor_head,
            canonical_read_observation=canonical_read_observation,
            read_challenge=challenge,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID",
        ),
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID",
    )


def parse_physical_full_matrix_v4_witness_anchor_transport_envelope(
    value: object,
) -> PhysicalFullMatrixV4WitnessAnchorTransportEnvelope:
    """Strictly parse only the V2 dual-layer transport envelope."""

    raw = value if type(value) is bytes else None
    decoded = _parse_canonical_object(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_ENCODING_INVALID",
    )
    assert raw is not None
    if (
        set(decoded) != _TRANSPORT_ENVELOPE_FIELDS
        or decoded["schema"]
        != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_SCHEMA
        or decoded["purpose"] != _TRANSPORT_ENVELOPE_PURPOSE
        or decoded["execution_authorized"] is not False
        or decoded["promotion_authorized"] is not False
        or decoded["full_matrix_executed"] is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID")
    canonical_anchor_head = _strict_base64_bytes(
        decoded["canonical_anchor_head_base64"],
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID",
    )
    canonical_read_observation = _strict_base64_bytes(
        decoded["canonical_read_observation_base64"],
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID",
    )
    challenge = _identifier(
        decoded["read_challenge"],
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID",
        pattern=_REPLAY_ID_RE,
    )
    body = _transport_envelope_body(
        canonical_anchor_head=canonical_anchor_head,
        canonical_read_observation=canonical_read_observation,
        read_challenge=challenge,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID",
    )
    if decoded != body:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID")
    # Reject a legacy head immediately, even before a caller can choose a
    # policy.  Genesis is the only non-V2 append object allowed here.
    try:
        parse_physical_full_matrix_v4_witness_anchor_genesis(canonical_anchor_head)
    except PhysicalFullMatrixV4WitnessAnchorWireError:
        parse_physical_full_matrix_v4_witness_anchor_immutable_head(canonical_anchor_head)
    observation = parse_physical_full_matrix_v4_witness_anchor_read_observation(
        canonical_read_observation
    )
    if observation.read_challenge != challenge:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_CHALLENGE_MISMATCH")
    return PhysicalFullMatrixV4WitnessAnchorTransportEnvelope(
        canonical_bytes=raw,
        canonical_immutable_head=canonical_anchor_head,
        canonical_read_observation=canonical_read_observation,
        read_challenge=challenge,
    )


def _mint_verified_transport_envelope(
    *,
    anchor_head: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    read_observation: VerifiedPhysicalFullMatrixV4WitnessAnchorReadObservation,
    read_challenge: str,
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorTransportEnvelope:
    result = object.__new__(VerifiedPhysicalFullMatrixV4WitnessAnchorTransportEnvelope)
    for name, item in {
        "anchor_head": anchor_head,
        "read_observation": read_observation,
        "read_challenge": read_challenge,
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }.items():
        object.__setattr__(result, name, item)
    return result


def verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
    value: object,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    now: datetime,
    expected_read_challenge: str,
    expected_predecessor: (
        VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
        | None
    ) = None,
    append_request: VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest | None = None,
    expected_current_sequence: int | None = None,
    expected_current_head_sha256: str | None = None,
    seen_observation_ids: Collection[str] = (),
) -> VerifiedPhysicalFullMatrixV4WitnessAnchorTransportEnvelope:
    """Verify both layers, binding the exact fresh challenge at the boundary."""

    envelope = parse_physical_full_matrix_v4_witness_anchor_transport_envelope(value)
    challenge = _identifier(
        expected_read_challenge,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_READ_CHALLENGE_INVALID",
        pattern=_REPLAY_ID_RE,
    )
    if envelope.read_challenge != challenge:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_CHALLENGE_MISMATCH")
    has_expected_current = (
        expected_current_sequence is not None
        or expected_current_head_sha256 is not None
    )
    if has_expected_current:
        if expected_predecessor is not None:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_EXPECTED_CURRENT_INVALID")
        expected_sequence = _positive_int(
            expected_current_sequence,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_EXPECTED_CURRENT_INVALID",
            permit_zero=True,
        )
        expected_head = _sha256(
            expected_current_head_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_EXPECTED_CURRENT_INVALID",
            permit_zero=True,
        )
    else:
        expected_sequence = None
        expected_head = None
    decoded_anchor = _parse_canonical_object(
        envelope.canonical_immutable_head,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ENVELOPE_INVALID",
    )
    if decoded_anchor.get("schema") == PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_GENESIS_SCHEMA:
        if expected_predecessor is not None or append_request is not None:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_PREDECESSOR_MISMATCH")
        anchor: (
            VerifiedPhysicalFullMatrixV4WitnessAnchorHead
            | VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
        ) = verify_physical_full_matrix_v4_witness_anchor_genesis(
            envelope.canonical_immutable_head,
            policy=policy,
            now=now,
        )
        if has_expected_current and (
            anchor.sequence != expected_sequence or anchor.head_sha256 != expected_head
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_EXPECTED_CURRENT_MISMATCH")
    elif (
        decoded_anchor.get("schema")
        == PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_IMMUTABLE_HEAD_SCHEMA
    ):
        anchor = verify_physical_full_matrix_v4_witness_anchor_immutable_head(
            envelope.canonical_immutable_head,
            policy=policy,
            now=now,
            expected_predecessor=expected_predecessor,
            append_request=append_request,
            expected_current_sequence=expected_current_sequence,
            expected_current_head_sha256=expected_current_head_sha256,
        )
    else:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_TRANSPORT_ANCHOR_SCHEMA_INVALID")
    observation = verify_physical_full_matrix_v4_witness_anchor_read_observation(
        envelope.canonical_read_observation,
        policy=policy,
        anchor_head=anchor,
        now=now,
        expected_read_challenge=challenge,
        seen_observation_ids=seen_observation_ids,
    )
    return _mint_verified_transport_envelope(
        anchor_head=anchor,
        read_observation=observation,
        read_challenge=challenge,
    )
