"""Fail-closed Gen2 V2 strict-writer response bound to a V1 parent.

This is intentionally a new generation, not an extension of the historical
Gen1 V2 strict-writer response.  A Gen2 receipt is locally Ed25519-signed and
commits to all of the following non-secret facts in one canonical object:

* every V2 Witness-roundtrip pin from an *opaque* Gen1 prepared capability;
* a complete immutable V1 ``transaction_commit`` parent projection; and
* the canonical, independently signed V1--V2 bridge certificate and its
  deterministic parent-binding digest.

The only allowed source of V1 facts is a verified, opaque bridge-bound
capability.  Raw V1 receipts, caller-built parent mappings, Gen1 runtime
receipts, and parsed fallback values are never accepted.  The module is pure,
default-off, and has no database, network, filesystem, subprocess, asyncio,
or traffic side effect.  A future root-owned SQL adapter owns the short local
transaction and the shared global attestation-consumption registry; this
module merely supplies fail-closed prepare/bind/sign/finalize evidence.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
import uuid
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core import physical_operational_failover_v1_v2_writer_term_bridge as bridge
from core import physical_wal_v2_witness_roundtrip_strict_writer_response as legacy


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_MAXIMUM_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_COMMIT_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA",
    "BoundPreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseProjection",
    "PreparedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponse",
    "VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation",
    "bind_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response",
    "finalize_bound_physical_wal_v2_witness_roundtrip_strict_writer_response",
    "prepare_physical_wal_v2_witness_roundtrip_strict_writer_bound_response",
    "project_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation",
    "require_bound_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
    "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response",
    "require_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation",
    "sign_bound_physical_wal_v2_witness_roundtrip_strict_writer_runtime_receipt",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v2"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_COMMIT_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-commit-receipt-v2"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY = (
    "root-owned-atomic-local-response-attestation-and-v1-v2-bridge-binding-v2"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_DEFAULT_ENABLED = False

DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_MAXIMUM_EVIDENCE_AGE_SECONDS = 30
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_MAXIMUM_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_FUTURE_SKEW_SECONDS = 5
# The canonical bridge certificate can be 256 KiB before base64 JSON encoding;
# leave room for that signed object plus the complete parent/V2 projection.
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RECEIPT_BYTES = 512 * 1024

_VERSION = 2
_COMMIT_DOMAIN = (
    b"gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-commit-receipt-v2\x00"
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_LEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_STREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
_CLUSTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$", re.ASCII)
_RELEASE_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.ASCII)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_GEN1_COMMIT_RE = re.compile(r"^v2-witness-strict-writer-[0-9a-f]{64}$", re.ASCII)
_GEN2_COMMIT_RE = re.compile(r"^v2-witness-strict-writer-g2-[0-9a-f]{64}$", re.ASCII)
_SITES = frozenset({"webapp_fi", "webapp_ir"})
# Bridge certificates carry the exact legacy V2 strict-writer role-matrix
# value; no aliases or spelling translation is accepted at this boundary.
_ACTIVATION_MODES = frozenset({"normal_fi_writer", "promoted_ir_writer"})
_PREPARED_CAPABILITY = object()
_BOUND_CAPABILITY = object()
_OBSERVATION_CAPABILITY = object()


class PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError(ValueError):
    """A Gen2 bridge-bound strict-writer response is unsafe or inconsistent."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError(code)


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig:
    """Default-off configuration for the isolated Gen2 response contract.

    ``legacy_response_config`` is retained only to revalidate an opaque Gen1
    prepared capability.  It does not authorize Gen1 receipt acceptance.  The
    bridge configuration supplies all V1/V2 term and key-role pins.  The same
    local Ed25519 public key must be present in both subordinate configs; the
    private half is passed only to the local signing function.
    """

    legacy_response_config: legacy.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig | None = None
    bridge_config: bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig | None = None
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_MAXIMUM_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction:
    """Gen2 V2-only persistence pins minted from an opaque Gen1 prepare."""

    schema: str
    configuration_sha256: str
    v2_base_configuration_sha256: str
    atomic_commit_boundary: str
    commit_id: str
    v2_base_commit_id: str
    attestation_sha256: str
    ir_durable_assertion_sha256: str
    context_certificate_sha256: str
    context_sha256: str
    source_envelope_sha256: str
    source_request_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    witness_transition_id: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    issued_at: datetime


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction:
    """Complete Gen2 instruction that a future SQL adapter persists exactly."""

    schema: str
    configuration_sha256: str
    v2_base_configuration_sha256: str
    atomic_commit_boundary: str
    commit_id: str
    v2_base_commit_id: str
    attestation_sha256: str
    ir_durable_assertion_sha256: str
    context_certificate_sha256: str
    context_sha256: str
    source_envelope_sha256: str
    source_request_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    witness_transition_id: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    v1_parent_cluster_id: str
    v1_parent_local_site: str
    v1_parent_release_sha: str
    v1_parent_generation_id: str
    v1_writer_admission_commit_id: str
    v1_writer_admission_commit_sha256: str
    v1_writer_admission_receipt_sha256: str
    v1_parent_prior_revision: int
    v1_parent_next_revision: int
    v1_parent_fence_generation: int
    v1_parent_holder_site: str
    v1_parent_evidence_id: str
    v1_parent_revalidation_id: str
    v1_parent_writer_epoch: int
    v1_parent_writer_lease_id: str
    v1_parent_term_issued_at: datetime
    v1_parent_term_expires_at: datetime
    v1_parent_admitted_at: datetime
    v1_v2_writer_term_bridge_certificate_id: str
    v1_v2_writer_term_bridge_intent_sha256: str
    v1_v2_writer_term_bridge_certificate_sha256: str
    v1_v2_writer_term_bridge_parent_binding_sha256: str
    canonical_v1_v2_writer_term_bridge_certificate: bytes
    issued_at: datetime


@dataclass(frozen=True, eq=False, init=False)
class PreparedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponse:
    """Opaque Gen2 base prepare capability; it carries no V1 parent."""

    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction,
        capability: object,
    ) -> None:
        if capability is not _PREPARED_CAPABILITY:
            raise TypeError("V2_WITNESS_STRICT_WRITER_BOUND_PREPARED_CONSTRUCTION_FORBIDDEN")
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_STRICT_WRITER_BOUND_PREPARED_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False, init=False)
class BoundPreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse:
    """Opaque Gen2 capability after a verified bridge binds the real V1 parent."""

    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
        capability: object,
    ) -> None:
        if capability is not _BOUND_CAPABILITY:
            raise TypeError("V2_WITNESS_STRICT_WRITER_BOUND_BINDING_CONSTRUCTION_FORBIDDEN")
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_STRICT_WRITER_BOUND_BINDING_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation:
    """Opaque post-commit Gen2 result; no Gen1 observation is interchangeable."""

    schema: str
    observation_sha256: str
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction
    runtime_commit_receipt_sha256: str
    local_commit_record_id: str
    local_response_id: str
    attestation_consumption_id: str
    committed_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        schema: str,
        observation_sha256: str,
        instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
        runtime_commit_receipt_sha256: str,
        local_commit_record_id: str,
        local_response_id: str,
        attestation_consumption_id: str,
        committed_at: datetime,
        capability: object,
    ) -> None:
        if capability is not _OBSERVATION_CAPABILITY:
            raise TypeError("V2_WITNESS_STRICT_WRITER_BOUND_OBSERVATION_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("schema", schema),
            ("observation_sha256", observation_sha256),
            ("instruction", instruction),
            ("runtime_commit_receipt_sha256", runtime_commit_receipt_sha256),
            ("local_commit_record_id", local_commit_record_id),
            ("local_response_id", local_response_id),
            ("attestation_consumption_id", attestation_consumption_id),
            ("committed_at", committed_at),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_STRICT_WRITER_BOUND_OBSERVATION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseProjection:
    """Non-authorizing projection after a fresh opaque observation recheck."""

    schema: str
    observation_sha256: str
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction
    runtime_commit_receipt_sha256: str
    local_commit_record_id: str
    local_response_id: str
    attestation_consumption_id: str
    committed_at: datetime


@dataclass(frozen=True)
class _ConfigFacts:
    legacy_response_config: legacy.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig
    bridge_config: bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig
    local_commit_signer_public_key: bytes
    expected_v2_base_configuration_sha256: str
    maximum_evidence_age_seconds: int
    configuration_sha256: str


@dataclass(frozen=True)
class _PreparedState:
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig
    legacy_prepared: legacy.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction


@dataclass(frozen=True)
class _BoundState:
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig
    prepared: PreparedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponse
    bridge_bound: bridge.BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction


@dataclass(frozen=True)
class _ReceiptFacts:
    canonical_receipt: bytes
    receipt_sha256: str
    local_commit_record_id: str
    local_response_id: str
    attestation_consumption_id: str
    committed_at: datetime


@dataclass(frozen=True)
class _ObservationState:
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig
    bound: BoundPreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse
    canonical_runtime_receipt: bytes


_PREPARED_STATES: WeakKeyDictionary[
    PreparedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponse, _PreparedState
] = WeakKeyDictionary()
_BOUND_STATES: WeakKeyDictionary[
    BoundPreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse, _BoundState
] = WeakKeyDictionary()
_OBSERVATION_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation,
    _ObservationState,
] = WeakKeyDictionary()


def _trusted_now() -> datetime:
    """Read the root-owned local clock; caller clocks never extend evidence."""

    return datetime.now(timezone.utc)


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        result = value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)
    if result.microsecond:
        _fail(code)
    return result


def _render_time(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIME_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail(code)


def _sha(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None or (not permit_zero and value == "0" * 64):
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _uuid_identifier(value: object, *, code: str) -> str:
    if type(value) is not str:
        _fail(code)
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        _fail(code)
    if str(parsed) != value:
        _fail(code)
    return value


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID")
        result[key] = value
    return result


def _configuration_sha256(
    *,
    bridge_config: bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig,
    local_commit_signer_public_key: bytes,
    expected_v2_base_configuration_sha256: str,
    maximum_evidence_age_seconds: int,
) -> str:
    """Bind every local Gen2 policy/key pin that is not in the V2 base receipt."""

    try:
        key_names = (
            "bridge_signer_public_key",
            "v1_current_term_signer_public_key",
            "v1_promotion_signer_public_key",
            "v2_witness_public_key",
            "v2_fi_outbox_public_key",
            "v2_ir_recovery_exporter_public_key",
            "v2_ir_durable_assertion_public_key",
            "v2_remote_source_public_key",
            "v2_remote_destination_public_key",
            "v2_local_commit_signer_public_key",
        )
        payload = {
            "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
            "v2_base_configuration_sha256": expected_v2_base_configuration_sha256,
            "bridge_cluster_id": bridge_config.cluster_id,
            "bridge_local_site": bridge_config.local_site,
            "bridge_release_sha": bridge_config.release_sha,
            "bridge_generation_id": bridge_config.generation_id,
            "bridge_expected_v1_revalidator_configuration_sha256": bridge_config.expected_v1_revalidator_configuration_sha256,
            "bridge_expected_v2_context_sha256": bridge_config.expected_v2_context_sha256,
            "bridge_expected_v2_activation_mode": bridge_config.expected_v2_activation_mode,
            "bridge_expected_v2_stream_generation_id": bridge_config.expected_v2_stream_generation_id,
            "bridge_signer_key_id": bridge_config.bridge_signer_key_id,
            "bridge_key_sha256": {
                name: hashlib.sha256(getattr(bridge_config, name)).hexdigest()
                for name in key_names
            },
            "bridge_safety_margin_seconds": bridge_config.safety_margin_seconds,
            "bridge_maximum_certificate_age_seconds": bridge_config.maximum_certificate_age_seconds,
            "local_commit_signer_public_key_base64": base64.b64encode(local_commit_signer_public_key).decode("ascii"),
            "maximum_evidence_age_seconds": maximum_evidence_age_seconds,
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError(
            "V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_INVALID"
        ) from exc
    return hashlib.sha256(
        _canonical(payload, code="V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_INVALID")
    ).hexdigest()


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_REQUIRED")
    if value.enabled is not True:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_DISABLED")
    if type(value.legacy_response_config) is not legacy.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig or type(value.bridge_config) is not bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_INVALID")
    legacy_config = value.legacy_response_config
    bridge_config = value.bridge_config
    if legacy_config.enabled is not True or bridge_config.enabled is not True:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_INVALID")
    if (
        type(value.maximum_evidence_age_seconds) is not int
        or not 1 <= value.maximum_evidence_age_seconds <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_MAXIMUM_EVIDENCE_AGE_SECONDS
        or type(legacy_config.maximum_evidence_age_seconds) is not int
        or value.maximum_evidence_age_seconds > legacy_config.maximum_evidence_age_seconds
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_INVALID")
    local_signer = _public_key(
        legacy_config.local_commit_signer_public_key,
        code="V2_WITNESS_STRICT_WRITER_BOUND_LOCAL_COMMIT_SIGNER_INVALID",
    )
    expected_base = _sha(
        bridge_config.expected_v2_strict_writer_configuration_sha256,
        code="V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_INVALID",
    )
    key_names = (
        "bridge_signer_public_key",
        "v1_current_term_signer_public_key",
        "v1_promotion_signer_public_key",
        "v2_witness_public_key",
        "v2_fi_outbox_public_key",
        "v2_ir_recovery_exporter_public_key",
        "v2_ir_durable_assertion_public_key",
        "v2_remote_source_public_key",
        "v2_remote_destination_public_key",
        "v2_local_commit_signer_public_key",
    )
    keys = tuple(
        _public_key(getattr(bridge_config, name), code="V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_INVALID")
        for name in key_names
    )
    if (
        len(set(keys)) != len(keys)
        or bridge_config.v2_local_commit_signer_public_key != local_signer
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_CONFIG_ROLE_KEY_REUSE")
    return _ConfigFacts(
        legacy_response_config=legacy_config,
        bridge_config=bridge_config,
        local_commit_signer_public_key=local_signer,
        expected_v2_base_configuration_sha256=expected_base,
        maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
        configuration_sha256=_configuration_sha256(
            bridge_config=bridge_config,
            local_commit_signer_public_key=local_signer,
            expected_v2_base_configuration_sha256=expected_base,
            maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
        ),
    )


def _legacy_instruction(
    value: object,
    *,
    normalized: _ConfigFacts,
) -> legacy.PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction:
    """Take the sole allowed V2 Gen1 handoff: an opaque prepared capability."""

    if type(value) is not legacy.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BASE_PREPARED_CAPABILITY_REQUIRED")
    try:
        result = legacy.require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
            value,
            config=normalized.legacy_response_config,
        )
    except legacy.PhysicalWalV2WitnessRoundtripStrictWriterResponseError as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError(
            "V2_WITNESS_STRICT_WRITER_BOUND_BASE_PREPARED_INVALID"
        ) from exc
    if type(result) is not legacy.PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BASE_PREPARED_INVALID")
    return result


def _validated_legacy_instruction(
    value: object,
    *,
    normalized: _ConfigFacts,
) -> legacy.PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction:
    """Defensively validate the public projection returned by the legacy seam."""

    if type(value) is not legacy.PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BASE_INSTRUCTION_INVALID")
    if (
        value.schema != legacy.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA
        or value.atomic_commit_boundary
        != legacy.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY
        or value.configuration_sha256 != normalized.expected_v2_base_configuration_sha256
        or type(value.commit_id) is not str
        or _GEN1_COMMIT_RE.fullmatch(value.commit_id) is None
        or value.writer_holder_site not in _SITES
        or type(value.writer_epoch) is not int
        or value.writer_epoch < 1
        or type(value.witness_sequence) is not int
        or value.witness_sequence < 1
        or value.activation_mode not in _ACTIVATION_MODES
        or type(value.writer_lease_id) is not str
        or _LEASE_RE.fullmatch(value.writer_lease_id) is None
        or type(value.activation_stream_generation_id) is not str
        or _STREAM_RE.fullmatch(value.activation_stream_generation_id) is None
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BASE_INSTRUCTION_INVALID")
    for item in (
        value.ir_durable_assertion_sha256,
        value.context_certificate_sha256,
        value.context_sha256,
        value.source_envelope_sha256,
        value.source_request_sha256,
        value.destination_receipt_sha256,
        value.durable_ledger_entry_sha256,
        value.target_recovery_evidence_sha256,
        value.readback_attestation_sha256,
        value.stage_receipt_sha256,
        value.witness_ledger_entry_sha256,
        value.witness_ledger_binding_sha256,
        value.witnessed_term_proof_sha256,
        value.activation_route_artifact_sha256,
        value.activation_source_cutover_attestation_sha256,
        value.activation_receiver_permit_sha256,
    ):
        _sha(item, code="V2_WITNESS_STRICT_WRITER_BOUND_BASE_INSTRUCTION_INVALID")
    _sha(
        value.attestation_sha256,
        code="V2_WITNESS_STRICT_WRITER_BOUND_BASE_INSTRUCTION_INVALID",
    )
    _sha(
        value.witness_ledger_previous_head_sha256,
        code="V2_WITNESS_STRICT_WRITER_BOUND_BASE_INSTRUCTION_INVALID",
        permit_zero=True,
    )
    _identifier(
        value.witness_transition_id,
        code="V2_WITNESS_STRICT_WRITER_BOUND_BASE_INSTRUCTION_INVALID",
    )
    _utc(value.issued_at, code="V2_WITNESS_STRICT_WRITER_BOUND_BASE_INSTRUCTION_INVALID")
    return value


def _gen2_base_commit_id(
    *,
    normalized: _ConfigFacts,
    source: legacy.PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
) -> str:
    """Derive the Gen2 id before a V1 parent exists or a transaction opens."""

    payload = {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
        "configuration_sha256": normalized.configuration_sha256,
        "v2_base_configuration_sha256": source.configuration_sha256,
        "v2_base_commit_id": source.commit_id,
        "attestation_sha256": source.attestation_sha256,
        "ir_durable_assertion_sha256": source.ir_durable_assertion_sha256,
        "context_certificate_sha256": source.context_certificate_sha256,
        "context_sha256": source.context_sha256,
        "source_envelope_sha256": source.source_envelope_sha256,
        "source_request_sha256": source.source_request_sha256,
        "destination_receipt_sha256": source.destination_receipt_sha256,
        "durable_ledger_entry_sha256": source.durable_ledger_entry_sha256,
        "target_recovery_evidence_sha256": source.target_recovery_evidence_sha256,
        "readback_attestation_sha256": source.readback_attestation_sha256,
        "stage_receipt_sha256": source.stage_receipt_sha256,
        "witness_sequence": source.witness_sequence,
        "witness_ledger_entry_sha256": source.witness_ledger_entry_sha256,
        "witness_ledger_previous_head_sha256": source.witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": source.witness_ledger_binding_sha256,
        "writer_holder_site": source.writer_holder_site,
        "writer_epoch": source.writer_epoch,
        "writer_lease_id": source.writer_lease_id,
        "witnessed_term_proof_sha256": source.witnessed_term_proof_sha256,
        "witness_transition_id": source.witness_transition_id,
        "activation_mode": source.activation_mode,
        "activation_stream_generation_id": source.activation_stream_generation_id,
        "activation_route_artifact_sha256": source.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": source.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": source.activation_receiver_permit_sha256,
    }
    return "v2-witness-strict-writer-g2-" + hashlib.sha256(
        _canonical(payload, code="V2_WITNESS_STRICT_WRITER_BOUND_COMMIT_ID_INVALID")
    ).hexdigest()


def _base_instruction_from_legacy(
    *,
    source: legacy.PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
    normalized: _ConfigFacts,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction:
    source = _validated_legacy_instruction(source, normalized=normalized)
    return PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
        configuration_sha256=normalized.configuration_sha256,
        v2_base_configuration_sha256=source.configuration_sha256,
        atomic_commit_boundary=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY,
        commit_id=_gen2_base_commit_id(normalized=normalized, source=source),
        v2_base_commit_id=source.commit_id,
        attestation_sha256=source.attestation_sha256,
        ir_durable_assertion_sha256=source.ir_durable_assertion_sha256,
        context_certificate_sha256=source.context_certificate_sha256,
        context_sha256=source.context_sha256,
        source_envelope_sha256=source.source_envelope_sha256,
        source_request_sha256=source.source_request_sha256,
        destination_receipt_sha256=source.destination_receipt_sha256,
        durable_ledger_entry_sha256=source.durable_ledger_entry_sha256,
        target_recovery_evidence_sha256=source.target_recovery_evidence_sha256,
        readback_attestation_sha256=source.readback_attestation_sha256,
        stage_receipt_sha256=source.stage_receipt_sha256,
        witness_sequence=source.witness_sequence,
        witness_ledger_entry_sha256=source.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=source.witness_ledger_previous_head_sha256,
        witness_ledger_binding_sha256=source.witness_ledger_binding_sha256,
        writer_holder_site=source.writer_holder_site,
        writer_epoch=source.writer_epoch,
        writer_lease_id=source.writer_lease_id,
        witnessed_term_proof_sha256=source.witnessed_term_proof_sha256,
        witness_transition_id=source.witness_transition_id,
        activation_mode=source.activation_mode,
        activation_stream_generation_id=source.activation_stream_generation_id,
        activation_route_artifact_sha256=source.activation_route_artifact_sha256,
        activation_source_cutover_attestation_sha256=source.activation_source_cutover_attestation_sha256,
        activation_receiver_permit_sha256=source.activation_receiver_permit_sha256,
        issued_at=_utc(source.issued_at, code="V2_WITNESS_STRICT_WRITER_BOUND_BASE_INSTRUCTION_INVALID"),
    )


def _prepared_state(
    value: object,
) -> _PreparedState:
    if (
        type(value) is not PreparedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponse
        or value._capability is not _PREPARED_CAPABILITY
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_PREPARED_CAPABILITY_REQUIRED")
    state = _PREPARED_STATES.get(value)
    if state is None or value.instruction is not state.instruction:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_PREPARED_CAPABILITY_REQUIRED")
    return state


def _same_base(
    left: PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction,
    right: PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction,
) -> bool:
    return all(
        getattr(left, name) == getattr(right, name)
        for name in PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction.__dataclass_fields__
        if name != "issued_at"
    )


def _revalidated_base(
    state: _PreparedState,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
) -> tuple[_ConfigFacts, PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction]:
    normalized = _config(config)
    saved = _config(state.config)
    if normalized.configuration_sha256 != saved.configuration_sha256:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_PREPARED_CONFIG_MISMATCH")
    source = _legacy_instruction(state.legacy_prepared, normalized=normalized)
    rebuilt = _base_instruction_from_legacy(source=source, normalized=normalized)
    if not _same_base(state.instruction, rebuilt):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BASE_INPUT_CHANGED")
    return normalized, state.instruction


def prepare_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    v2_prepared: legacy.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse,
    now: datetime | None = None,
) -> PreparedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponse:
    """Freshly validate an opaque V2 base prepare before the local transaction.

    This deliberately has no V1 parent or receipt input.  A root-owned
    composition obtains the bridge certificate before PostgreSQL and passes
    only its later opaque bound result to :func:`bind_prepared...` after the
    V1 parent has been persisted in that same short transaction.
    """

    del now
    normalized = _config(config)
    source = _legacy_instruction(v2_prepared, normalized=normalized)
    instruction = _base_instruction_from_legacy(source=source, normalized=normalized)
    result = PreparedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponse(
        instruction=instruction,
        capability=_PREPARED_CAPABILITY,
    )
    _PREPARED_STATES[result] = _PreparedState(
        config=config,
        legacy_prepared=v2_prepared,
        instruction=instruction,
    )
    return result


def require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction:
    """Revalidate the opaque base prepare; raw Gen1 instructions never qualify."""

    del now
    _normalized, instruction = _revalidated_base(_prepared_state(value), config=config)
    return instruction


def _bridge_projection(
    value: object,
    *,
    normalized: _ConfigFacts,
    now: datetime,
) -> bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeBoundIntentProjection:
    """Require the bridge's opaque verified/bound capability, never raw V1 data."""

    if type(value) is not bridge.BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BRIDGE_CAPABILITY_REQUIRED")
    try:
        return bridge.project_bound_physical_operational_failover_v1_v2_writer_term_bridge_intent(
            value=value,
            config=normalized.bridge_config,
            now=now,
        )
    except bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeError as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError(
            "V2_WITNESS_STRICT_WRITER_BOUND_BRIDGE_INVALID"
        ) from exc


def _bridge_parent_binding(
    *,
    projection: bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeBoundIntentProjection,
) -> str:
    """Independently recompute the bridge's specified final local binding."""

    parent = projection.parent
    return hashlib.sha256(
        _canonical(
            {
                "schema": bridge.PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_PARENT_BINDING_SCHEMA,
                "certificate_sha256": projection.certificate_sha256,
                "intent_sha256": projection.intent_sha256,
                "v2_commit_id": projection.v2_instruction.commit_id,
                "parent_commit_id": parent.commit_id,
                "parent_commit_sha256": parent.commit_sha256,
                "parent_receipt_sha256": parent.receipt_sha256,
            },
            code="V2_WITNESS_STRICT_WRITER_BOUND_BRIDGE_INVALID",
        )
    ).hexdigest()


def _cross_pin_bridge_v2(
    *,
    base: PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction,
    projection: bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeBoundIntentProjection,
) -> None:
    """Ensure the signed bridge intent describes this exact opaque V2 prepare."""

    value = projection.v2_instruction
    if (
        value.strict_schema
        != legacy.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA
        or value.configuration_sha256 != base.v2_base_configuration_sha256
        or value.atomic_commit_boundary
        != legacy.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY
        or value.commit_id != base.v2_base_commit_id
        or value.attestation_sha256 != base.attestation_sha256
        or value.context_sha256 != base.context_sha256
        or value.writer_holder_site != base.writer_holder_site
        or value.writer_epoch != base.writer_epoch
        or value.writer_lease_id != base.writer_lease_id
        or value.witnessed_term_proof_sha256 != base.witnessed_term_proof_sha256
        or value.witness_transition_id != base.witness_transition_id
        or value.activation_mode != base.activation_mode
        or value.activation_stream_generation_id != base.activation_stream_generation_id
        or value.activation_route_artifact_sha256
        != base.activation_route_artifact_sha256
        or value.activation_source_cutover_attestation_sha256
        != base.activation_source_cutover_attestation_sha256
        or value.activation_receiver_permit_sha256
        != base.activation_receiver_permit_sha256
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BRIDGE_V2_CROSS_PIN_MISMATCH")


def _bound_instruction(
    *,
    base: PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction,
    projection: bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeBoundIntentProjection,
    now: datetime,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction:
    """Build a complete parent-bound instruction from verified opaque inputs."""

    _cross_pin_bridge_v2(base=base, projection=projection)
    parent = projection.parent
    admission = projection.v1_admission
    term = projection.v1_current_term
    certificate = projection.canonical_certificate
    if (
        hashlib.sha256(certificate).hexdigest() != projection.certificate_sha256
        or _bridge_parent_binding(projection=projection)
        != projection.parent_binding_sha256
        or parent.commit_id != projection.parent_commit_id
        or parent.commit_sha256 != projection.parent_commit_sha256
        or parent.receipt_sha256 != projection.parent_receipt_sha256
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BRIDGE_PARENT_BINDING_MISMATCH")
    if (
        type(admission.cluster_id) is not str
        or _CLUSTER_RE.fullmatch(admission.cluster_id) is None
        or admission.local_site not in _SITES
        or type(admission.release_sha) is not str
        or _RELEASE_RE.fullmatch(admission.release_sha) is None
        or type(admission.generation_id) is not str
        or _ID_RE.fullmatch(admission.generation_id) is None
        or admission.operation_kind != "transaction_commit"
        or type(admission.prior_revision) is not int
        or admission.prior_revision < 0
        or type(admission.next_revision) is not int
        or admission.next_revision != admission.prior_revision + 1
        or type(admission.fence_generation) is not int
        or admission.fence_generation < 0
        or term.holder_site not in _SITES
        or term.holder_site != admission.local_site
        or type(term.writer_epoch) is not int
        or term.writer_epoch < 1
        or type(term.writer_lease_id) is not str
        or _LEASE_RE.fullmatch(term.writer_lease_id) is None
        or term.writer_epoch != base.writer_epoch
        or term.writer_lease_id != base.writer_lease_id
        or parent.cluster_id != admission.cluster_id
        or parent.local_site != admission.local_site
        or parent.release_sha != admission.release_sha
        or parent.generation_id != admission.generation_id
        or parent.prior_revision != admission.prior_revision
        or parent.next_revision != admission.next_revision
        or parent.fence_generation != admission.fence_generation
        or parent.writer_epoch != term.writer_epoch
        or parent.writer_lease_id != term.writer_lease_id
        or parent.evidence_id != admission.evidence_id
        or parent.revalidation_id != admission.revalidation_id
        or parent.admitted_at != admission.admitted_at
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_CROSS_PIN_MISMATCH")
    _uuid_identifier(
        parent.commit_id,
        code="V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_INVALID",
    )
    for item in (
        parent.commit_sha256,
        parent.receipt_sha256,
        projection.intent_sha256,
        projection.certificate_sha256,
        projection.parent_binding_sha256,
    ):
        _sha(item, code="V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_INVALID")
    _identifier(
        projection.certificate_id,
        code="V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_INVALID",
    )
    _identifier(
        admission.evidence_id,
        code="V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_INVALID",
    )
    _identifier(
        admission.revalidation_id,
        code="V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_INVALID",
    )
    term_issued = _utc(
        admission.term_evidence_issued_at,
        code="V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_INVALID",
    )
    term_expires = _utc(
        admission.term_evidence_expires_at,
        code="V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_INVALID",
    )
    admitted = _utc(
        admission.admitted_at,
        code="V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_INVALID",
    )
    if term_expires <= term_issued or admitted < term_issued or admitted >= term_expires:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_INVALID")
    # The bridge itself checks its short validity window.  Rechecking here
    # makes the future transaction seam explicit and rejects any projection
    # whose parent term has already become unsuitable at this trusted clock.
    if term_expires <= now:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_V1_PARENT_STALE")
    return PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction(
        schema=base.schema,
        configuration_sha256=base.configuration_sha256,
        v2_base_configuration_sha256=base.v2_base_configuration_sha256,
        atomic_commit_boundary=base.atomic_commit_boundary,
        commit_id=base.commit_id,
        v2_base_commit_id=base.v2_base_commit_id,
        attestation_sha256=base.attestation_sha256,
        ir_durable_assertion_sha256=base.ir_durable_assertion_sha256,
        context_certificate_sha256=base.context_certificate_sha256,
        context_sha256=base.context_sha256,
        source_envelope_sha256=base.source_envelope_sha256,
        source_request_sha256=base.source_request_sha256,
        destination_receipt_sha256=base.destination_receipt_sha256,
        durable_ledger_entry_sha256=base.durable_ledger_entry_sha256,
        target_recovery_evidence_sha256=base.target_recovery_evidence_sha256,
        readback_attestation_sha256=base.readback_attestation_sha256,
        stage_receipt_sha256=base.stage_receipt_sha256,
        witness_sequence=base.witness_sequence,
        witness_ledger_entry_sha256=base.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=base.witness_ledger_previous_head_sha256,
        witness_ledger_binding_sha256=base.witness_ledger_binding_sha256,
        writer_holder_site=base.writer_holder_site,
        writer_epoch=base.writer_epoch,
        writer_lease_id=base.writer_lease_id,
        witnessed_term_proof_sha256=base.witnessed_term_proof_sha256,
        witness_transition_id=base.witness_transition_id,
        activation_mode=base.activation_mode,
        activation_stream_generation_id=base.activation_stream_generation_id,
        activation_route_artifact_sha256=base.activation_route_artifact_sha256,
        activation_source_cutover_attestation_sha256=base.activation_source_cutover_attestation_sha256,
        activation_receiver_permit_sha256=base.activation_receiver_permit_sha256,
        v1_parent_cluster_id=parent.cluster_id,
        v1_parent_local_site=parent.local_site,
        v1_parent_release_sha=parent.release_sha,
        v1_parent_generation_id=parent.generation_id,
        v1_writer_admission_commit_id=parent.commit_id,
        v1_writer_admission_commit_sha256=parent.commit_sha256,
        v1_writer_admission_receipt_sha256=parent.receipt_sha256,
        v1_parent_prior_revision=parent.prior_revision,
        v1_parent_next_revision=parent.next_revision,
        v1_parent_fence_generation=parent.fence_generation,
        v1_parent_holder_site=term.holder_site,
        v1_parent_evidence_id=parent.evidence_id,
        v1_parent_revalidation_id=parent.revalidation_id,
        v1_parent_writer_epoch=term.writer_epoch,
        v1_parent_writer_lease_id=term.writer_lease_id,
        v1_parent_term_issued_at=term_issued,
        v1_parent_term_expires_at=term_expires,
        v1_parent_admitted_at=admitted,
        v1_v2_writer_term_bridge_certificate_id=projection.certificate_id,
        v1_v2_writer_term_bridge_intent_sha256=projection.intent_sha256,
        v1_v2_writer_term_bridge_certificate_sha256=projection.certificate_sha256,
        v1_v2_writer_term_bridge_parent_binding_sha256=projection.parent_binding_sha256,
        canonical_v1_v2_writer_term_bridge_certificate=certificate,
        issued_at=now,
    )


def bind_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
    prepared: object,
    *,
    bridge_bound: bridge.BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    now: datetime | None = None,
) -> BoundPreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse:
    """Bind a post-persistence opaque bridge result to a fresh V2 prepare.

    The bridge result is the only V1-parent input accepted here.  It can only
    be minted by the bridge module after its verified certificate is bound to
    a parent receipt; this function performs no certificate issue, no remote
    call, and no raw parent parsing.
    """

    del now
    observed = _utc(
        _trusted_now(),
        code="V2_WITNESS_STRICT_WRITER_BOUND_CLOCK_INVALID",
    )
    state = _prepared_state(prepared)
    normalized, base = _revalidated_base(state, config=config)
    projection = _bridge_projection(
        bridge_bound,
        normalized=normalized,
        now=observed,
    )
    instruction = _bound_instruction(
        base=base,
        projection=projection,
        now=observed,
    )
    result = BoundPreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse(
        instruction=instruction,
        capability=_BOUND_CAPABILITY,
    )
    _BOUND_STATES[result] = _BoundState(
        config=config,
        prepared=prepared,
        bridge_bound=bridge_bound,
        instruction=instruction,
    )
    return result


def _bound_state(
    value: object,
) -> _BoundState:
    if (
        type(value) is not BoundPreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse
        or value._capability is not _BOUND_CAPABILITY
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BINDING_CAPABILITY_REQUIRED")
    state = _BOUND_STATES.get(value)
    if state is None or value.instruction is not state.instruction:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BINDING_CAPABILITY_REQUIRED")
    return state


def _same_bound(
    left: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
    right: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
) -> bool:
    return all(
        getattr(left, name) == getattr(right, name)
        for name in PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction.__dataclass_fields__
        if name != "issued_at"
    )


def _revalidated_bound(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    now: datetime,
) -> tuple[_ConfigFacts, PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction]:
    state = _bound_state(value)
    normalized, base = _revalidated_base(_prepared_state(state.prepared), config=config)
    saved = _config(state.config)
    if normalized.configuration_sha256 != saved.configuration_sha256:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_BINDING_CONFIG_MISMATCH")
    projection = _bridge_projection(
        state.bridge_bound,
        normalized=normalized,
        now=now,
    )
    rebuilt = _bound_instruction(base=base, projection=projection, now=now)
    if not _same_bound(state.instruction, rebuilt):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_INPUT_CHANGED")
    return normalized, state.instruction


def require_bound_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction:
    """Freshly revalidate V2 and bridge pins before a local adapter persists them."""

    # Check the opaque handle before consulting a clock so a forged object is
    # rejected at the intended capability boundary even on a malformed test
    # clock or a host clock incident.
    _bound_state(value)
    del now
    observed = _utc(
        _trusted_now(),
        code="V2_WITNESS_STRICT_WRITER_BOUND_CLOCK_INVALID",
    )
    _normalized, instruction = _revalidated_bound(value, config=config, now=observed)
    return instruction


def _attestation_consumption_id(
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
) -> str:
    """Gen2-only consumption identity; shared registry remains the authority."""

    return "v2-witness-consume-g2-" + instruction.attestation_sha256


def _runtime_unsigned(
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
    *,
    local_commit_record_id: str,
    local_response_id: str,
    attestation_consumption_id: str,
    committed_at: datetime,
) -> dict[str, object]:
    """Every durable Gen2 row/receipt pin, excluding only its local signature."""

    return {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_COMMIT_RECEIPT_SCHEMA,
        "version": _VERSION,
        "kind": "durable-local-writer-response-attestation-and-v1-v2-bridge-binding",
        "configuration_sha256": instruction.configuration_sha256,
        "v2_base_configuration_sha256": instruction.v2_base_configuration_sha256,
        "atomic_commit_boundary": instruction.atomic_commit_boundary,
        "commit_id": instruction.commit_id,
        "v2_base_commit_id": instruction.v2_base_commit_id,
        "attestation_sha256": instruction.attestation_sha256,
        "ir_durable_assertion_sha256": instruction.ir_durable_assertion_sha256,
        "context_certificate_sha256": instruction.context_certificate_sha256,
        "context_sha256": instruction.context_sha256,
        "source_envelope_sha256": instruction.source_envelope_sha256,
        "source_request_sha256": instruction.source_request_sha256,
        "destination_receipt_sha256": instruction.destination_receipt_sha256,
        "durable_ledger_entry_sha256": instruction.durable_ledger_entry_sha256,
        "target_recovery_evidence_sha256": instruction.target_recovery_evidence_sha256,
        "readback_attestation_sha256": instruction.readback_attestation_sha256,
        "stage_receipt_sha256": instruction.stage_receipt_sha256,
        "witness_sequence": instruction.witness_sequence,
        "witness_ledger_entry_sha256": instruction.witness_ledger_entry_sha256,
        "witness_ledger_previous_head_sha256": instruction.witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": instruction.witness_ledger_binding_sha256,
        "writer_holder_site": instruction.writer_holder_site,
        "writer_epoch": instruction.writer_epoch,
        "writer_lease_id": instruction.writer_lease_id,
        "witnessed_term_proof_sha256": instruction.witnessed_term_proof_sha256,
        "witness_transition_id": instruction.witness_transition_id,
        "activation_mode": instruction.activation_mode,
        "activation_stream_generation_id": instruction.activation_stream_generation_id,
        "activation_route_artifact_sha256": instruction.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": instruction.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": instruction.activation_receiver_permit_sha256,
        "v1_parent_cluster_id": instruction.v1_parent_cluster_id,
        "v1_parent_local_site": instruction.v1_parent_local_site,
        "v1_parent_release_sha": instruction.v1_parent_release_sha,
        "v1_parent_generation_id": instruction.v1_parent_generation_id,
        "v1_writer_admission_commit_id": instruction.v1_writer_admission_commit_id,
        "v1_writer_admission_commit_sha256": instruction.v1_writer_admission_commit_sha256,
        "v1_writer_admission_receipt_sha256": instruction.v1_writer_admission_receipt_sha256,
        "v1_parent_prior_revision": instruction.v1_parent_prior_revision,
        "v1_parent_next_revision": instruction.v1_parent_next_revision,
        "v1_parent_fence_generation": instruction.v1_parent_fence_generation,
        "v1_parent_holder_site": instruction.v1_parent_holder_site,
        "v1_parent_evidence_id": instruction.v1_parent_evidence_id,
        "v1_parent_revalidation_id": instruction.v1_parent_revalidation_id,
        "v1_parent_writer_epoch": instruction.v1_parent_writer_epoch,
        "v1_parent_writer_lease_id": instruction.v1_parent_writer_lease_id,
        "v1_parent_term_issued_at": _render_time(
            instruction.v1_parent_term_issued_at,
            code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
        ),
        "v1_parent_term_expires_at": _render_time(
            instruction.v1_parent_term_expires_at,
            code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
        ),
        "v1_parent_admitted_at": _render_time(
            instruction.v1_parent_admitted_at,
            code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
        ),
        "v1_v2_writer_term_bridge_certificate_id": instruction.v1_v2_writer_term_bridge_certificate_id,
        "v1_v2_writer_term_bridge_intent_sha256": instruction.v1_v2_writer_term_bridge_intent_sha256,
        "v1_v2_writer_term_bridge_certificate_sha256": instruction.v1_v2_writer_term_bridge_certificate_sha256,
        "v1_v2_writer_term_bridge_parent_binding_sha256": instruction.v1_v2_writer_term_bridge_parent_binding_sha256,
        "canonical_v1_v2_writer_term_bridge_certificate_base64": base64.b64encode(
            instruction.canonical_v1_v2_writer_term_bridge_certificate
        ).decode("ascii"),
        "local_commit_record_id": local_commit_record_id,
        "local_response_id": local_response_id,
        "attestation_consumption_id": attestation_consumption_id,
        "committed_at": _render_time(
            committed_at,
            code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
        ),
    }


def _private_key(value: object, *, normalized: _ConfigFacts) -> Ed25519PrivateKey:
    if not isinstance(value, Ed25519PrivateKey):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_LOCAL_COMMIT_PRIVATE_KEY_INVALID")
    try:
        raw = value.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    except (TypeError, ValueError):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_LOCAL_COMMIT_PRIVATE_KEY_INVALID")
    if raw != normalized.local_commit_signer_public_key:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_LOCAL_COMMIT_SIGNER_KEY_MISMATCH")
    return value


def sign_bound_physical_wal_v2_witness_roundtrip_strict_writer_runtime_receipt(
    bound: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    local_commit_private_key: Ed25519PrivateKey,
    local_commit_record_id: str,
    local_response_id: str,
    committed_at: datetime,
    now: datetime | None = None,
) -> bytes:
    """Create the Gen2 receipt between parent flush/bind and Gen2-row insert.

    The caller must invoke this only inside its short local transaction after
    it has flushed the V1 parent and obtained the opaque bridge bind, but
    *before* it inserts/flushes the Gen2 response row that contains this
    receipt and claims the shared attestation consumption.  The signature is
    a bounded local intent/attestation, not proof of durability; only the
    caller's later known transaction commit establishes that.  This pure
    function refuses all raw parent/bridge inputs and signs only a freshly
    revalidated opaque bound capability.
    """

    del now
    observed = _utc(
        _trusted_now(),
        code="V2_WITNESS_STRICT_WRITER_BOUND_CLOCK_INVALID",
    )
    normalized, instruction = _revalidated_bound(bound, config=config, now=observed)
    signer = _private_key(local_commit_private_key, normalized=normalized)
    commit_record = _identifier(
        local_commit_record_id,
        code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
    )
    response = _identifier(
        local_response_id,
        code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
    )
    consumption = _attestation_consumption_id(instruction)
    if len({commit_record, response, consumption}) != 3:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_IDENTITY_REUSED")
    committed = _utc(
        committed_at,
        code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
    )
    if committed > observed + timedelta(
        seconds=MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_FUTURE_SKEW_SECONDS
    ) or observed - committed > timedelta(seconds=normalized.maximum_evidence_age_seconds):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_TIME_INVALID")
    unsigned = _runtime_unsigned(
        instruction,
        local_commit_record_id=commit_record,
        local_response_id=response,
        attestation_consumption_id=consumption,
        committed_at=committed,
    )
    signature = signer.sign(
        _COMMIT_DOMAIN
        + _canonical(
            unsigned,
            code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
        )
    )
    encoded = dict(unsigned)
    encoded["signature_base64"] = base64.b64encode(signature).decode("ascii")
    return _canonical(
        encoded,
        code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
    )


def _runtime_receipt(
    value: object,
    *,
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
    normalized: _ConfigFacts,
    now: datetime,
) -> _ReceiptFacts:
    """Verify exact canonical Gen2 receipt bytes and all signed bindings."""

    if type(value) is not bytes or not 1 <= len(value) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RECEIPT_BYTES:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID")
    try:
        receipt = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID")
    if type(receipt) is not dict or _canonical(
        receipt,
        code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
    ) != value:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID")
    try:
        commit_record = _identifier(
            receipt["local_commit_record_id"],
            code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
        )
        response = _identifier(
            receipt["local_response_id"],
            code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
        )
        consumption = _identifier(
            receipt["attestation_consumption_id"],
            code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
        )
        committed = _parse_time(
            receipt["committed_at"],
            code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
        )
    except KeyError:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID")
    if len({commit_record, response, consumption}) != 3:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_IDENTITY_REUSED")
    if consumption != _attestation_consumption_id(instruction):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_CONSUMPTION_MISMATCH")
    expected = _runtime_unsigned(
        instruction,
        local_commit_record_id=commit_record,
        local_response_id=response,
        attestation_consumption_id=consumption,
        committed_at=committed,
    )
    unsigned = {key: item for key, item in receipt.items() if key != "signature_base64"}
    if unsigned != expected:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_BINDING_MISMATCH")
    if type(receipt.get("signature_base64")) is not str:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    try:
        signature = base64.b64decode(
            receipt["signature_base64"].encode("ascii", "strict"),
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    if len(signature) != 64:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(
            normalized.local_commit_signer_public_key
        ).verify(
            signature,
            _COMMIT_DOMAIN
            + _canonical(
                expected,
                code="V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_INVALID",
            ),
        )
    except (InvalidSignature, ValueError, TypeError):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    if committed > now + timedelta(
        seconds=MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_FUTURE_SKEW_SECONDS
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_FUTURE")
    if now - committed > timedelta(seconds=normalized.maximum_evidence_age_seconds):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_RUNTIME_RECEIPT_STALE")
    return _ReceiptFacts(
        canonical_receipt=value,
        receipt_sha256=hashlib.sha256(value).hexdigest(),
        local_commit_record_id=commit_record,
        local_response_id=response,
        attestation_consumption_id=consumption,
        committed_at=committed,
    )


def _observation_payload(
    *,
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
    receipt: _ReceiptFacts,
) -> dict[str, object]:
    """Hash a complete response projection without making it another authority."""

    return {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
        "instruction": _runtime_unsigned(
            instruction,
            local_commit_record_id=receipt.local_commit_record_id,
            local_response_id=receipt.local_response_id,
            attestation_consumption_id=receipt.attestation_consumption_id,
            committed_at=receipt.committed_at,
        ),
        "runtime_commit_receipt_sha256": receipt.receipt_sha256,
    }


def _observation_from(
    *,
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
    receipt: _ReceiptFacts,
) -> VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation:
    payload = _observation_payload(instruction=instruction, receipt=receipt)
    return VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
        observation_sha256=hashlib.sha256(
            _canonical(
                payload,
                code="V2_WITNESS_STRICT_WRITER_BOUND_OBSERVATION_INVALID",
            )
        ).hexdigest(),
        instruction=instruction,
        runtime_commit_receipt_sha256=receipt.receipt_sha256,
        local_commit_record_id=receipt.local_commit_record_id,
        local_response_id=receipt.local_response_id,
        attestation_consumption_id=receipt.attestation_consumption_id,
        committed_at=receipt.committed_at,
        capability=_OBSERVATION_CAPABILITY,
    )


def _validate_observation(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    now: datetime,
) -> tuple[
    VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
    _ReceiptFacts,
]:
    if (
        type(value)
        is not VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation
        or value._capability is not _OBSERVATION_CAPABILITY
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_OBSERVATION_CAPABILITY_REQUIRED")
    state = _OBSERVATION_STATES.get(value)
    if state is None:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_OBSERVATION_CAPABILITY_REQUIRED")
    normalized, instruction = _revalidated_bound(
        state.bound,
        config=config,
        now=now,
    )
    receipt = _runtime_receipt(
        state.canonical_runtime_receipt,
        instruction=instruction,
        normalized=normalized,
        now=now,
    )
    expected = _observation_from(instruction=instruction, receipt=receipt)
    if (
        value.schema,
        value.observation_sha256,
        value.instruction,
        value.runtime_commit_receipt_sha256,
        value.local_commit_record_id,
        value.local_response_id,
        value.attestation_consumption_id,
        value.committed_at,
    ) != (
        expected.schema,
        expected.observation_sha256,
        expected.instruction,
        expected.runtime_commit_receipt_sha256,
        expected.local_commit_record_id,
        expected.local_response_id,
        expected.attestation_consumption_id,
        expected.committed_at,
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_OBSERVATION_TAMPERED")
    return value, instruction, receipt


def finalize_bound_physical_wal_v2_witness_roundtrip_strict_writer_response(
    bound: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    runtime_receipt: bytes,
    now: datetime | None = None,
) -> VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation:
    """Release a Gen2 observation only after fresh base/bridge/receipt checks.

    The caller invokes this strictly after a known successful database commit.
    If it fails, the application response must remain withheld and an
    independent durable fence/reconciliation path must handle the committed
    state; no caller may claim a later rollback erased it.
    """

    del now
    observed = _utc(
        _trusted_now(),
        code="V2_WITNESS_STRICT_WRITER_BOUND_CLOCK_INVALID",
    )
    normalized, instruction = _revalidated_bound(bound, config=config, now=observed)
    receipt = _runtime_receipt(
        runtime_receipt,
        instruction=instruction,
        normalized=normalized,
        now=observed,
    )
    result = _observation_from(instruction=instruction, receipt=receipt)
    _OBSERVATION_STATES[result] = _ObservationState(
        config=config,
        bound=bound,
        canonical_runtime_receipt=receipt.canonical_receipt,
    )
    _validate_observation(result, config=config, now=observed)
    return result


def require_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    now: datetime | None = None,
) -> VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation:
    """Freshly revalidate an opaque Gen2 observation at the root clock."""

    del now
    observed = _utc(
        _trusted_now(),
        code="V2_WITNESS_STRICT_WRITER_BOUND_CLOCK_INVALID",
    )
    result, _instruction, _receipt = _validate_observation(
        value,
        config=config,
        now=observed,
    )
    return result


def project_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseProjection:
    """Return non-authorizing complete Gen2 pins after fresh revalidation."""

    verified = require_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation(
        value,
        config=config,
        now=now,
    )
    return PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseProjection(
        schema=verified.schema,
        observation_sha256=verified.observation_sha256,
        instruction=verified.instruction,
        runtime_commit_receipt_sha256=verified.runtime_commit_receipt_sha256,
        local_commit_record_id=verified.local_commit_record_id,
        local_response_id=verified.local_response_id,
        attestation_consumption_id=verified.attestation_consumption_id,
        committed_at=verified.committed_at,
    )
