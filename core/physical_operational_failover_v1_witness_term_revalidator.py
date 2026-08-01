"""Fail-closed local bridge from a Witness ledger term to writer admission.

This module is deliberately a contract, not a runtime transport.  It does
not open network connections, call a provider, read a database, change traffic, start a
writer, or make a promotion decision.  A future root-owned deployment must
provide all three narrow dependencies below:

* an authenticated *role-local* fetcher for a Witness response;
* a durable replay/reservation guard shared across restarts; and
* a trusted local clock.

The response is independently signed in a domain and with a public key that
are separate from the V1 promotion-grant signer.  It is bound to one exact
canonical current Witness-ledger snapshot and to the exact writer-admission
revalidation request.  The only result exposed to writer admission is its
narrow term-evidence projection.  It deliberately contains no promotion,
writer, database, or traffic authority flag.

The durable guard is reserved *before* fetching and consumes the attestation
only after cryptographic and ledger-head validation.  A process-local replay
set would make restart/replay safety ambiguous, so this module has none.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from threading import RLock
from typing import Any, Protocol
from weakref import ReferenceType, ref

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.append_only_sync_delta_batch import LEASE_ID_RE
from core import physical_operational_failover_v1 as wire
from core import physical_operational_failover_v1_witness_ledger as ledger
from core import physical_operational_failover_v1_writer_admission as admission


__all__ = (
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_SCHEMA",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA",
    "BoundPhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenance",
    "PhysicalOperationalFailoverV1AuthenticatedWitnessCurrentTermFetcher",
    "PhysicalOperationalFailoverV1DurableWitnessTermReplayGuard",
    "PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption",
    "PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumptionReceipt",
    "PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse",
    "PhysicalOperationalFailoverV1WitnessCurrentTermAttestationInput",
    "PhysicalOperationalFailoverV1WitnessCurrentTermClock",
    "PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation",
    "PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest",
    "PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator",
    "PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig",
    "PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError",
    "PhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenanceProjection",
    "PhysicalOperationalFailoverV1WitnessCurrentTermTermEvidence",
    "VerifiedPhysicalOperationalFailoverV1WitnessCurrentTermAttestation",
    "consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance",
    "consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance_for_writer_admission",
    "physical_operational_failover_v1_witness_current_term_revalidator_configuration_sha256",
    "sign_physical_operational_failover_v1_witness_current_term_attestation",
    "verify_physical_operational_failover_v1_witness_current_term_attestation",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-witness-current-term-attestation-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-witness-current-term-admission-provenance-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_DEFAULT_ENABLED = False

_VERSION = 1
_MAX_WIRE_BYTES = 64 * 1024
_ZERO_SHA256 = "0" * 64
_WEBAPP_SITES = frozenset({"webapp_fi", "webapp_ir"})
_ACTIVE_PHASES = frozenset({"fi-active", "ir-active"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RELEASE_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.ASCII)
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)

_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "issuer_site",
        "signer_key_id",
        "configuration_sha256",
        "attestation_id",
        "attestation_nonce",
        "issued_at",
        "expires_at",
        "cluster_id",
        "holder_site",
        "release_sha",
        "generation_id",
        "runtime_instance_id",
        "revalidation_id",
        "reservation_id",
        "request_sha256",
        "ledger_schema",
        "ledger_version",
        "ledger_head_sha256",
        "ledger_entry_sha256",
        "ledger_previous_head_sha256",
        "ledger_state_sha256",
        "ledger_phase",
        "active_term",
        "active_term_sha256",
        "signature_base64",
    }
)

_DOMAIN = (PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA + "\x00").encode(
    "ascii"
)
_PROVENANCE_CAPABILITY = object()


class PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
    admission.PhysicalOperationalFailoverV1WriterAdmissionError
):
    """A current-term attestation cannot safely satisfy writer admission."""


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(code)


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig:
    """Default-off, one-site policy for a role-local revalidation runtime.

    ``witness_promotion_signer_public_key`` is intentionally mandatory even
    though this module never verifies a promotion grant.  It lets the policy
    prove that the current-term attestation key is a separate key role rather
    than a domain-only re-use of the promotion signer.
    """

    schema: str = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA
    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_DEFAULT_ENABLED
    binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding | None = None
    runtime_instance_id: str | None = None
    witness_current_term_signer_public_key: bytes = b""
    witness_promotion_signer_public_key: bytes = b""
    witness_current_term_signer_key_id: str | None = None
    durable_guard_id: str | None = None
    expected_ledger_schema: str = ledger.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SCHEMA
    safety_margin_seconds: int = 5
    maximum_attestation_age_seconds: int = 30
    maximum_attestation_duration_seconds: int = 90
    maximum_reservation_duration_seconds: int = 90


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessCurrentTermAttestationInput:
    """Unsigned, Witness-produced view of one exact active ledger snapshot."""

    attestation_id: str = ""
    attestation_nonce: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    cluster_id: str = ""
    holder_site: str = ""
    release_sha: str = ""
    generation_id: str = ""
    runtime_instance_id: str = ""
    revalidation_id: str = ""
    reservation_id: str = ""
    request_sha256: str = ""
    ledger_schema: str = ledger.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SCHEMA
    ledger_version: int = 0
    ledger_head_sha256: str = ""
    ledger_entry_sha256: str = ""
    ledger_previous_head_sha256: str = ""
    ledger_state_sha256: str = ""
    ledger_phase: str = ""
    active_term: wire.PhysicalOperationalFailoverV1Term | None = None
    active_term_sha256: str = ""


@dataclass(frozen=True)
class VerifiedPhysicalOperationalFailoverV1WitnessCurrentTermAttestation:
    """Verified evidence only; it intentionally grants no operational authority."""

    attestation_id: str
    attestation_nonce: str
    attestation_sha256: str
    canonical_attestation: bytes
    cluster_id: str
    holder_site: str
    release_sha: str
    generation_id: str
    runtime_instance_id: str
    revalidation_id: str
    reservation_id: str
    request_sha256: str
    issued_at: datetime
    expires_at: datetime
    ledger_version: int
    ledger_head_sha256: str
    ledger_entry_sha256: str
    ledger_previous_head_sha256: str
    ledger_state_sha256: str
    ledger_phase: str
    active_term: wire.PhysicalOperationalFailoverV1Term
    active_term_sha256: str


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest:
    """Exact durable reservation requested before any role-local fetch."""

    schema: str
    configuration_sha256: str
    durable_guard_id: str
    binding_sha256: str
    runtime_instance_id: str
    revalidation_id: str
    request_sha256: str
    requested_at: datetime


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
    """Durable reservation receipt returned by the injected guard.

    ``minimum_ledger_version`` and ``previous_ledger_head_sha256`` make an
    accepted older fork/rollback rejectable after a restart.  Revalidation of
    an unchanged live term remains possible when both values match exactly.
    """

    schema: str
    configuration_sha256: str
    durable_guard_id: str
    reservation_id: str
    binding_sha256: str
    runtime_instance_id: str
    revalidation_id: str
    request_sha256: str
    requested_at: datetime
    reserved_at: datetime
    expires_at: datetime
    minimum_ledger_version: int
    previous_ledger_head_sha256: str | None


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption:
    """One exact attestation whose replay must be durably consumed."""

    schema: str
    configuration_sha256: str
    durable_guard_id: str
    reservation_id: str
    revalidation_id: str
    request_sha256: str
    attestation_id: str
    attestation_nonce: str
    attestation_sha256: str
    ledger_version: int
    ledger_head_sha256: str
    consumed_at: datetime


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumptionReceipt:
    """Exact durable-consumption receipt; no local cache substitutes for it."""

    schema: str
    configuration_sha256: str
    durable_guard_id: str
    reservation_id: str
    revalidation_id: str
    request_sha256: str
    attestation_id: str
    attestation_nonce: str
    attestation_sha256: str
    ledger_version: int
    ledger_head_sha256: str
    consumed_at: datetime
    receipt_id: str


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse:
    """Authenticated role-local response, still verified independently here."""

    canonical_attestation: bytes
    ledger_snapshot: ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessCurrentTermTermEvidence:
    """The intentionally narrow projection required by writer admission."""

    cluster_id: str
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    release_sha: str
    generation_id: str
    evidence_id: str
    revalidation_id: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, eq=False, init=False)
class BoundPhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenance:
    """One-shot opaque proof that one V1 admission has a verified V1 term.

    This handle deliberately exposes no raw current-term attestation, nonce,
    reservation, state, or writer-admission object.  Its owner keeps only a
    digest-only verified projection in a private identity registry.  It is
    valid only in this process and cannot be serialized: after a restart a
    fresh V1 current-term attestation is required before a Gen2 bridge can be
    prepared again.
    """

    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(self, *, capability: object) -> None:
        if capability is not _PROVENANCE_CAPABILITY:
            raise TypeError(
                "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_CONSTRUCTION_FORBIDDEN"
            )
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_SERIALIZATION_FORBIDDEN"
        )


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenanceProjection:
    """Non-authorizing V1 pins released once to the Gen2 bridge owner.

    This is intentionally a scalar, audit-safe projection.  It contains no
    canonical attestation bytes, nonce, local state object, writer-admission
    capability, database handle, or authority bit.  A bridge must receive it
    only by consuming :class:`BoundPhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenance`.
    """

    schema: str
    revalidator_configuration_sha256: str
    cluster_id: str
    local_site: str
    release_sha: str
    generation_id: str
    attestation_sha256: str
    attestation_id: str
    revalidation_id: str
    reservation_id: str
    request_sha256: str
    ledger_schema: str
    ledger_version: int
    ledger_head_sha256: str
    ledger_entry_sha256: str
    ledger_previous_head_sha256: str
    ledger_state_sha256: str
    ledger_phase: str
    active_term_sha256: str
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    attestation_issued_at: datetime
    attestation_expires_at: datetime
    term_issued_at: datetime
    term_expires_at: datetime
    operation_kind: str
    prior_revision: int
    next_revision: int
    fence_generation: int
    operation_opened_at: datetime
    admitted_at: datetime


class PhysicalOperationalFailoverV1WitnessCurrentTermClock(Protocol):
    """Trusted local time source supplied by the future root-owned runtime."""

    def now_utc(self) -> datetime: ...


class PhysicalOperationalFailoverV1AuthenticatedWitnessCurrentTermFetcher(Protocol):
    """Authenticated role-local fetch seam; no direct FI<->IR path is implied."""

    def fetch_current_term_attestation(
        self,
        *,
        request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
        reservation: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
    ) -> PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse: ...


class PhysicalOperationalFailoverV1DurableWitnessTermReplayGuard(Protocol):
    """Durable cross-restart replay/reservation boundary supplied externally."""

    def reserve_revalidation(
        self,
        *,
        request: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest,
    ) -> PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation: ...

    def consume_attestation(
        self,
        *,
        reservation: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
        consumption: PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption,
    ) -> PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumptionReceipt: ...


@dataclass(frozen=True)
class _ConfigFacts:
    binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding
    runtime_instance_id: str
    attestation_public_key: Ed25519PublicKey
    attestation_public_key_raw: bytes
    promotion_public_key_raw: bytes
    signer_key_id: str
    durable_guard_id: str
    expected_ledger_schema: str
    safety_margin_seconds: int
    maximum_attestation_age_seconds: int
    maximum_attestation_duration_seconds: int
    maximum_reservation_duration_seconds: int
    binding_sha256: str
    configuration_sha256: str


@dataclass(frozen=True)
class _RequestFacts:
    request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest
    binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding
    runtime_instance_id: str
    revalidation_id: str
    minimum_writer_epoch: int
    previous_writer_lease_id: str | None
    previous_evidence_id: str | None
    previous_revalidation_id: str | None
    clock_floor: datetime | None
    request_sha256: str


@dataclass(frozen=True)
class _SnapshotFacts:
    snapshot: ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot
    active_term: wire.PhysicalOperationalFailoverV1Term
    active_term_sha256: str
    phase: str


@dataclass(frozen=True)
class _VerifiedCurrentTermProvenance:
    """Digest-only facts retained after a verified current-term attestation.

    ``canonical_attestation`` and the attestation nonce intentionally do not
    appear here.  The durable replay guard has already consumed that exact
    attestation before this object is created; this registry only preserves
    the minimum cross-pins needed to bind a later opaque V1 admission.
    """

    facts: _ConfigFacts
    owner_token: object
    attestation_sha256: str
    attestation_id: str
    revalidation_id: str
    reservation_id: str
    request_sha256: str
    ledger_schema: str
    ledger_version: int
    ledger_head_sha256: str
    ledger_entry_sha256: str
    ledger_previous_head_sha256: str
    ledger_state_sha256: str
    ledger_phase: str
    active_term_sha256: str
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    attestation_issued_at: datetime
    attestation_expires_at: datetime
    term_issued_at: datetime
    term_expires_at: datetime


@dataclass(frozen=True)
class _BoundCurrentTermAdmissionProvenance:
    provenance: _VerifiedCurrentTermProvenance
    writer_admission_reference: ReferenceType[
        admission.PhysicalOperationalFailoverV1WriterAdmission
    ]
    cluster_id: str
    local_site: str
    release_sha: str
    generation_id: str
    operation_kind: str
    prior_revision: int
    next_revision: int
    fence_generation: int
    operation_opened_at: datetime
    admitted_at: datetime


@dataclass(frozen=True)
class _IdentitySlot:
    reference: ReferenceType[object]
    value: object


_PROVENANCE_REGISTRY_LOCK = RLock()
_EVIDENCE_PROVENANCE: dict[int, _IdentitySlot] = {}
_STATE_PROVENANCE: dict[int, _IdentitySlot] = {}
_BOUND_PROVENANCE: dict[int, _IdentitySlot] = {}


def _identity_store(
    registry: dict[int, _IdentitySlot],
    *,
    target: object,
    value: object,
    code: str,
) -> None:
    """Store a value under exact object identity, never dataclass equality."""

    key = id(target)

    def _discard(reference: ReferenceType[object]) -> None:
        with _PROVENANCE_REGISTRY_LOCK:
            current = registry.get(key)
            if current is not None and current.reference is reference:
                del registry[key]

    try:
        reference = ref(target, _discard)
    except TypeError:
        _fail(code)
    with _PROVENANCE_REGISTRY_LOCK:
        current = registry.get(key)
        if current is not None and current.reference() is target:
            _fail(code)
        registry[key] = _IdentitySlot(reference=reference, value=value)


def _identity_take(
    registry: dict[int, _IdentitySlot],
    *,
    target: object,
    code: str,
) -> object:
    """Consume one exact identity-bound registry entry fail-closed."""

    key = id(target)
    with _PROVENANCE_REGISTRY_LOCK:
        slot = registry.get(key)
        if slot is None or slot.reference() is not target:
            _fail(code)
        del registry[key]
        return slot.value


def _identity_peek(
    registry: dict[int, _IdentitySlot],
    *,
    target: object,
    code: str,
) -> object:
    """Read one identity-bound entry without releasing it to a caller."""

    key = id(target)
    with _PROVENANCE_REGISTRY_LOCK:
        slot = registry.get(key)
        if slot is None or slot.reference() is not target:
            _fail(code)
        return slot.value


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(code) from exc


def _utc(value: object, *, code: str, require_second_precision: bool = True) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        result = value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)
    if require_second_precision and result.microsecond:
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


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _writer_lease_id(value: object, *, code: str) -> str:
    """Validate a V1/V2 writer lease without widening generic identifiers."""

    if type(value) is not str or LEASE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha(value: object, *, code: str, allow_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    if not allow_zero and value == _ZERO_SHA256:
        _fail(code)
    return value


def _release_sha(value: object, *, code: str) -> str:
    if type(value) is not str or _RELEASE_SHA_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _public_key(value: object, *, code: str) -> tuple[Ed25519PublicKey, bytes]:
    if not isinstance(value, bytes) or len(value) != 32:
        _fail(code)
    try:
        return Ed25519PublicKey.from_public_bytes(value), value
    except ValueError:
        _fail(code)


def _binding_mapping(
    value: object,
    *,
    code: str,
) -> tuple[admission.PhysicalOperationalFailoverV1WriterAdmissionBinding, dict[str, str]]:
    if type(value) is not admission.PhysicalOperationalFailoverV1WriterAdmissionBinding:
        _fail(code)
    if value.local_site not in _WEBAPP_SITES:
        _fail(code)
    normalized = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
        cluster_id=_identifier(value.cluster_id, code=code),
        local_site=value.local_site,
        release_sha=_release_sha(value.release_sha, code=code),
        generation_id=_identifier(value.generation_id, code=code),
    )
    return normalized, {
        "cluster_id": normalized.cluster_id,
        "local_site": normalized.local_site,
        "release_sha": normalized.release_sha,
        "generation_id": normalized.generation_id,
    }


def _term_mapping(
    value: object,
    *,
    code: str,
) -> tuple[wire.PhysicalOperationalFailoverV1Term, dict[str, object]]:
    if type(value) is not wire.PhysicalOperationalFailoverV1Term:
        _fail(code)
    if value.holder_site not in _WEBAPP_SITES:
        _fail(code)
    if type(value.writer_epoch) is not int or isinstance(value.writer_epoch, bool) or value.writer_epoch < 1:
        _fail(code)
    issued_at = _utc(value.issued_at, code=code)
    expires_at = _utc(value.expires_at, code=code)
    if expires_at <= issued_at or expires_at - issued_at > timedelta(seconds=300):
        _fail(code)
    term = wire.PhysicalOperationalFailoverV1Term(
        holder_site=value.holder_site,
        writer_epoch=value.writer_epoch,
        writer_lease_id=_writer_lease_id(value.writer_lease_id, code=code),
        witness_transition_id=_identifier(value.witness_transition_id, code=code),
        witnessed_term_proof_sha256=_sha(value.witnessed_term_proof_sha256, code=code),
        issued_at=issued_at,
        expires_at=expires_at,
    )
    return term, {
        "holder_site": term.holder_site,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": term.witnessed_term_proof_sha256,
        "issued_at": _render_time(term.issued_at, code=code),
        "expires_at": _render_time(term.expires_at, code=code),
    }


def _term_sha256(value: object, *, code: str) -> str:
    term, _mapping = _term_mapping(value, code=code)
    try:
        actual = ledger._term_sha256(term, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_TERM_INVALID")
    except ledger.PhysicalOperationalFailoverV1WitnessLedgerError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(code) from exc
    return _sha(actual, code=code)


def _config_mapping(facts: _ConfigFacts) -> dict[str, object]:
    return {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA,
        "binding": {
            "cluster_id": facts.binding.cluster_id,
            "local_site": facts.binding.local_site,
            "release_sha": facts.binding.release_sha,
            "generation_id": facts.binding.generation_id,
        },
        "runtime_instance_id": facts.runtime_instance_id,
        "witness_current_term_signer_public_key_sha256": hashlib.sha256(
            facts.attestation_public_key_raw
        ).hexdigest(),
        "witness_promotion_signer_public_key_sha256": hashlib.sha256(
            facts.promotion_public_key_raw
        ).hexdigest(),
        "witness_current_term_signer_key_id": facts.signer_key_id,
        "durable_guard_id": facts.durable_guard_id,
        "expected_ledger_schema": facts.expected_ledger_schema,
        "safety_margin_seconds": facts.safety_margin_seconds,
        "maximum_attestation_age_seconds": facts.maximum_attestation_age_seconds,
        "maximum_attestation_duration_seconds": facts.maximum_attestation_duration_seconds,
        "maximum_reservation_duration_seconds": facts.maximum_reservation_duration_seconds,
    }


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA
        or value.enabled is not True
        or value.expected_ledger_schema
        != ledger.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SCHEMA
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID")
    binding, binding_mapping = _binding_mapping(
        value.binding,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID",
    )
    runtime_instance_id = _identifier(
        value.runtime_instance_id,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID",
    )
    signer, signer_raw = _public_key(
        value.witness_current_term_signer_public_key,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID",
    )
    _promotion, promotion_raw = _public_key(
        value.witness_promotion_signer_public_key,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID",
    )
    if signer_raw == promotion_raw:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_KEY_ROLE_COLLISION")
    signer_key_id = _identifier(
        value.witness_current_term_signer_key_id,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID",
    )
    durable_guard_id = _identifier(
        value.durable_guard_id,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID",
    )
    limits = (
        value.safety_margin_seconds,
        value.maximum_attestation_age_seconds,
        value.maximum_attestation_duration_seconds,
        value.maximum_reservation_duration_seconds,
    )
    if (
        any(type(item) is not int for item in limits)
        or not 1 <= value.safety_margin_seconds <= 60
        or not 1 <= value.maximum_attestation_age_seconds <= 300
        or not 2 <= value.maximum_attestation_duration_seconds <= 300
        or not 2 <= value.maximum_reservation_duration_seconds <= 300
        or value.safety_margin_seconds >= value.maximum_attestation_duration_seconds
        or value.safety_margin_seconds >= value.maximum_reservation_duration_seconds
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID")
    provisional = _ConfigFacts(
        binding=binding,
        runtime_instance_id=runtime_instance_id,
        attestation_public_key=signer,
        attestation_public_key_raw=signer_raw,
        promotion_public_key_raw=promotion_raw,
        signer_key_id=signer_key_id,
        durable_guard_id=durable_guard_id,
        expected_ledger_schema=value.expected_ledger_schema,
        safety_margin_seconds=value.safety_margin_seconds,
        maximum_attestation_age_seconds=value.maximum_attestation_age_seconds,
        maximum_attestation_duration_seconds=value.maximum_attestation_duration_seconds,
        maximum_reservation_duration_seconds=value.maximum_reservation_duration_seconds,
        binding_sha256=hashlib.sha256(_canonical(binding_mapping, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID")).hexdigest(),
        configuration_sha256="",
    )
    configuration_sha256 = hashlib.sha256(
        _canonical(_config_mapping(provisional), code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CONFIG_INVALID")
    ).hexdigest()
    return _ConfigFacts(
        binding=provisional.binding,
        runtime_instance_id=provisional.runtime_instance_id,
        attestation_public_key=provisional.attestation_public_key,
        attestation_public_key_raw=provisional.attestation_public_key_raw,
        promotion_public_key_raw=provisional.promotion_public_key_raw,
        signer_key_id=provisional.signer_key_id,
        durable_guard_id=provisional.durable_guard_id,
        expected_ledger_schema=provisional.expected_ledger_schema,
        safety_margin_seconds=provisional.safety_margin_seconds,
        maximum_attestation_age_seconds=provisional.maximum_attestation_age_seconds,
        maximum_attestation_duration_seconds=provisional.maximum_attestation_duration_seconds,
        maximum_reservation_duration_seconds=provisional.maximum_reservation_duration_seconds,
        binding_sha256=provisional.binding_sha256,
        configuration_sha256=configuration_sha256,
    )


def physical_operational_failover_v1_witness_current_term_revalidator_configuration_sha256(
    *,
    config: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig,
) -> str:
    """Return the exact enabled V1 current-term verifier configuration pin.

    A future Gen2 bridge pins this value rather than accepting a caller-made
    V1 provenance dataclass.  Parsing through ``_config`` keeps disabled,
    mismatched, and key-role-colliding policies fail-closed.
    """

    return _config(config).configuration_sha256


def _request_facts(
    value: object,
    *,
    facts: _ConfigFacts,
) -> _RequestFacts:
    if type(value) is not admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_REQUEST_INVALID")
    binding, binding_mapping = _binding_mapping(
        value.binding,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_REQUEST_INVALID",
    )
    runtime_instance_id = _identifier(
        value.runtime_instance_id,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_REQUEST_INVALID",
    )
    revalidation_id = _identifier(
        value.revalidation_id,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_REQUEST_INVALID",
    )
    if type(value.minimum_writer_epoch) is not int or value.minimum_writer_epoch < 0:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_REQUEST_INVALID")
    prior: dict[str, str | None] = {}
    for name, item in (
        ("previous_writer_lease_id", value.previous_writer_lease_id),
        ("previous_evidence_id", value.previous_evidence_id),
        ("previous_revalidation_id", value.previous_revalidation_id),
    ):
        if item is not None:
            validator = (
                _writer_lease_id
                if name == "previous_writer_lease_id"
                else _identifier
            )
            prior[name] = validator(
                item,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_REQUEST_INVALID",
            )
        else:
            prior[name] = None
    floor = None
    if value.clock_floor is not None:
        floor = _utc(
            value.clock_floor,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_REQUEST_INVALID",
            require_second_precision=False,
        )
    if binding != facts.binding or runtime_instance_id != facts.runtime_instance_id:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_REQUEST_CONFIG_MISMATCH")
    request_mapping = {
        "binding": binding_mapping,
        "runtime_instance_id": runtime_instance_id,
        "revalidation_id": revalidation_id,
        "minimum_writer_epoch": value.minimum_writer_epoch,
        "previous_writer_lease_id": prior["previous_writer_lease_id"],
        "previous_evidence_id": prior["previous_evidence_id"],
        "previous_revalidation_id": prior["previous_revalidation_id"],
        "clock_floor": None
        if floor is None
        else floor.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return _RequestFacts(
        request=value,
        binding=binding,
        runtime_instance_id=runtime_instance_id,
        revalidation_id=revalidation_id,
        minimum_writer_epoch=value.minimum_writer_epoch,
        previous_writer_lease_id=prior["previous_writer_lease_id"],
        previous_evidence_id=prior["previous_evidence_id"],
        previous_revalidation_id=prior["previous_revalidation_id"],
        clock_floor=floor,
        request_sha256=hashlib.sha256(
            _canonical(request_mapping, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_REQUEST_INVALID")
        ).hexdigest(),
    )


def _trusted_now(clock: object, *, floor: datetime | None) -> datetime:
    callback = getattr(clock, "now_utc", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CLOCK_MISSING")
    try:
        result = _utc(
            callback(),
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CLOCK_INVALID",
        )
    except PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CLOCK_FAILED"
        ) from exc
    if floor is not None and result < floor:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CLOCK_REGRESSION")
    return result


def _reservation_request(
    *,
    facts: _ConfigFacts,
    request: _RequestFacts,
    now: datetime,
) -> PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest:
    return PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest(
        schema=PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA,
        configuration_sha256=facts.configuration_sha256,
        durable_guard_id=facts.durable_guard_id,
        binding_sha256=facts.binding_sha256,
        runtime_instance_id=request.runtime_instance_id,
        revalidation_id=request.revalidation_id,
        request_sha256=request.request_sha256,
        requested_at=now,
    )


def _reservation(
    value: object,
    *,
    request: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest,
    facts: _ConfigFacts,
    now: datetime,
) -> PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
    if type(value) is not PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    expected = (
        ("schema", value.schema, request.schema),
        ("configuration_sha256", value.configuration_sha256, request.configuration_sha256),
        ("durable_guard_id", value.durable_guard_id, request.durable_guard_id),
        ("binding_sha256", value.binding_sha256, request.binding_sha256),
        ("runtime_instance_id", value.runtime_instance_id, request.runtime_instance_id),
        ("revalidation_id", value.revalidation_id, request.revalidation_id),
        ("request_sha256", value.request_sha256, request.request_sha256),
        ("requested_at", value.requested_at, request.requested_at),
    )
    if any(actual != wanted for _name, actual, wanted in expected):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_MISMATCH")
    _identifier(value.reservation_id, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    _sha(value.configuration_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    _sha(value.binding_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    reserved_at = _utc(value.reserved_at, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    expires_at = _utc(value.expires_at, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    if (
        reserved_at > now
        or expires_at <= now + timedelta(seconds=facts.safety_margin_seconds)
        or expires_at <= reserved_at
        or expires_at - reserved_at
        > timedelta(seconds=facts.maximum_reservation_duration_seconds)
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    if type(value.minimum_ledger_version) is not int or value.minimum_ledger_version < 0:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    if value.previous_ledger_head_sha256 is None:
        if value.minimum_ledger_version != 0:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    else:
        _sha(value.previous_ledger_head_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
        if value.minimum_ledger_version < 1:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    return value


def _snapshot(value: object, *, now: datetime) -> _SnapshotFacts:
    try:
        snapshot = ledger._snapshot(value)
    except ledger.PhysicalOperationalFailoverV1WitnessLedgerError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_LEDGER_SNAPSHOT_INVALID"
        ) from exc
    state = snapshot.state
    if state.phase not in _ACTIVE_PHASES or state.active_term is None:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_LEDGER_NOT_ACTIVE")
    if state.clock_floor > now:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CLOCK_REGRESSION")
    active_term, _mapping = _term_mapping(
        state.active_term,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_LEDGER_SNAPSHOT_INVALID",
    )
    active_term_sha256 = _term_sha256(
        active_term,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_LEDGER_SNAPSHOT_INVALID",
    )
    if state.active_term_sha256 != active_term_sha256:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_LEDGER_SNAPSHOT_INVALID")
    return _SnapshotFacts(
        snapshot=snapshot,
        active_term=active_term,
        active_term_sha256=active_term_sha256,
        phase=state.phase,
    )


def _attestation_mapping(
    value: PhysicalOperationalFailoverV1WitnessCurrentTermAttestationInput,
    *,
    facts: _ConfigFacts,
    code: str,
) -> dict[str, object]:
    if type(value) is not PhysicalOperationalFailoverV1WitnessCurrentTermAttestationInput:
        _fail(code)
    issued_at = _utc(value.issued_at, code=code)
    expires_at = _utc(value.expires_at, code=code)
    if expires_at <= issued_at or expires_at - issued_at > timedelta(
        seconds=facts.maximum_attestation_duration_seconds
    ):
        _fail(code)
    term, term_mapping = _term_mapping(value.active_term, code=code)
    active_term_sha256 = _term_sha256(term, code=code)
    if (
        value.ledger_schema != facts.expected_ledger_schema
        or value.holder_site != term.holder_site
        or value.ledger_phase not in _ACTIVE_PHASES
        or type(value.ledger_version) is not int
        or value.ledger_version < 1
        or value.ledger_head_sha256 != value.ledger_entry_sha256
        or value.active_term_sha256 != active_term_sha256
    ):
        _fail(code)
    if (
        value.cluster_id != facts.binding.cluster_id
        or value.holder_site != facts.binding.local_site
        or value.release_sha != facts.binding.release_sha
        or value.generation_id != facts.binding.generation_id
        or value.runtime_instance_id != facts.runtime_instance_id
    ):
        _fail(code)
    previous_head = _sha(value.ledger_previous_head_sha256, code=code, allow_zero=True)
    return {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA,
        "version": _VERSION,
        "issuer_site": "witness",
        "signer_key_id": facts.signer_key_id,
        "configuration_sha256": facts.configuration_sha256,
        "attestation_id": _identifier(value.attestation_id, code=code),
        "attestation_nonce": _nonce(value.attestation_nonce, code=code),
        "issued_at": _render_time(issued_at, code=code),
        "expires_at": _render_time(expires_at, code=code),
        "cluster_id": _identifier(value.cluster_id, code=code),
        "holder_site": term.holder_site,
        "release_sha": _release_sha(value.release_sha, code=code),
        "generation_id": _identifier(value.generation_id, code=code),
        "runtime_instance_id": _identifier(value.runtime_instance_id, code=code),
        "revalidation_id": _identifier(value.revalidation_id, code=code),
        "reservation_id": _identifier(value.reservation_id, code=code),
        "request_sha256": _sha(value.request_sha256, code=code),
        "ledger_schema": facts.expected_ledger_schema,
        "ledger_version": value.ledger_version,
        "ledger_head_sha256": _sha(value.ledger_head_sha256, code=code),
        "ledger_entry_sha256": _sha(value.ledger_entry_sha256, code=code),
        "ledger_previous_head_sha256": previous_head,
        "ledger_state_sha256": _sha(value.ledger_state_sha256, code=code),
        "ledger_phase": value.ledger_phase,
        "active_term": term_mapping,
        "active_term_sha256": active_term_sha256,
    }


def _decode_wire(value: object) -> tuple[dict[str, object], bytes]:
    if not isinstance(value, bytes) or not value or len(value) > _MAX_WIRE_BYTES:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID")

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID")
            result[key] = item
        return result

    try:
        decoded = json.loads(value.decode("ascii"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID"
        ) from exc
    if type(decoded) is not dict or set(decoded) != _ATTESTATION_FIELDS:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID")
    canonical = _canonical(decoded, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID")
    if canonical != value:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_NONCANONICAL")
    return decoded, canonical


def _attestation_from_wire(
    value: object,
    *,
    facts: _ConfigFacts,
) -> tuple[dict[str, object], bytes, bytes]:
    decoded, canonical = _decode_wire(value)
    if (
        decoded["schema"] != PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA
        or decoded["version"] != _VERSION
        or decoded["issuer_site"] != "witness"
        or decoded["signer_key_id"] != facts.signer_key_id
        or decoded["configuration_sha256"] != facts.configuration_sha256
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_CONFIG_MISMATCH")
    signature = decoded["signature_base64"]
    if type(signature) is not str:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID")
    try:
        signature_raw = base64.b64decode(signature.encode("ascii"), validate=True)
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID"
        ) from exc
    if len(signature_raw) != 64:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID")
    unsigned = dict(decoded)
    del unsigned["signature_base64"]
    signed = _canonical(unsigned, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID")
    try:
        facts.attestation_public_key.verify(signature_raw, _DOMAIN + signed)
    except InvalidSignature as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_SIGNATURE_INVALID"
        ) from exc
    return decoded, canonical, signed


def _wire_term(value: object, *, code: str) -> wire.PhysicalOperationalFailoverV1Term:
    if type(value) is not dict:
        _fail(code)
    expected = {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
        "issued_at",
        "expires_at",
    }
    if set(value) != expected:
        _fail(code)
    return _term_mapping(
        wire.PhysicalOperationalFailoverV1Term(
            holder_site=value["holder_site"],
            writer_epoch=value["writer_epoch"],
            writer_lease_id=value["writer_lease_id"],
            witness_transition_id=value["witness_transition_id"],
            witnessed_term_proof_sha256=value["witnessed_term_proof_sha256"],
            issued_at=_parse_time(value["issued_at"], code=code),
            expires_at=_parse_time(value["expires_at"], code=code),
        ),
        code=code,
    )[0]


def _verify_attestation(
    value: object,
    *,
    facts: _ConfigFacts,
    request: _RequestFacts,
    reservation: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
    snapshot: _SnapshotFacts,
    now: datetime,
) -> VerifiedPhysicalOperationalFailoverV1WitnessCurrentTermAttestation:
    decoded, canonical, _signed = _attestation_from_wire(value, facts=facts)
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID"
    issued_at = _parse_time(decoded["issued_at"], code=code)
    expires_at = _parse_time(decoded["expires_at"], code=code)
    if (
        expires_at <= issued_at
        or expires_at - issued_at
        > timedelta(seconds=facts.maximum_attestation_duration_seconds)
        or issued_at > now
        or now - issued_at > timedelta(seconds=facts.maximum_attestation_age_seconds)
        or expires_at <= now + timedelta(seconds=facts.safety_margin_seconds)
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_STALE")
    term = _wire_term(decoded["active_term"], code=code)
    active_term_sha256 = _term_sha256(term, code=code)
    fields = {
        "attestation_id": _identifier(decoded["attestation_id"], code=code),
        "attestation_nonce": _nonce(decoded["attestation_nonce"], code=code),
        "cluster_id": _identifier(decoded["cluster_id"], code=code),
        "holder_site": decoded["holder_site"],
        "release_sha": _release_sha(decoded["release_sha"], code=code),
        "generation_id": _identifier(decoded["generation_id"], code=code),
        "runtime_instance_id": _identifier(decoded["runtime_instance_id"], code=code),
        "revalidation_id": _identifier(decoded["revalidation_id"], code=code),
        "reservation_id": _identifier(decoded["reservation_id"], code=code),
        "request_sha256": _sha(decoded["request_sha256"], code=code),
        "ledger_schema": decoded["ledger_schema"],
        "ledger_version": decoded["ledger_version"],
        "ledger_head_sha256": _sha(decoded["ledger_head_sha256"], code=code),
        "ledger_entry_sha256": _sha(decoded["ledger_entry_sha256"], code=code),
        "ledger_previous_head_sha256": _sha(
            decoded["ledger_previous_head_sha256"], code=code, allow_zero=True
        ),
        "ledger_state_sha256": _sha(decoded["ledger_state_sha256"], code=code),
        "ledger_phase": decoded["ledger_phase"],
        "active_term_sha256": _sha(decoded["active_term_sha256"], code=code),
    }
    if type(fields["ledger_version"]) is not int or fields["ledger_version"] < 1:
        _fail(code)
    if fields["holder_site"] not in _WEBAPP_SITES or fields["ledger_phase"] not in _ACTIVE_PHASES:
        _fail(code)
    if (
        fields["cluster_id"] != facts.binding.cluster_id
        or fields["holder_site"] != facts.binding.local_site
        or fields["release_sha"] != facts.binding.release_sha
        or fields["generation_id"] != facts.binding.generation_id
        or fields["runtime_instance_id"] != request.runtime_instance_id
        or fields["revalidation_id"] != request.revalidation_id
        or fields["reservation_id"] != reservation.reservation_id
        or fields["request_sha256"] != request.request_sha256
        or fields["ledger_schema"] != facts.expected_ledger_schema
        or fields["active_term_sha256"] != active_term_sha256
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_BINDING_MISMATCH")
    if (
        fields["ledger_version"] != snapshot.snapshot.version
        or fields["ledger_head_sha256"] != snapshot.snapshot.head_sha256
        or fields["ledger_entry_sha256"] != snapshot.snapshot.entry.entry_sha256
        or fields["ledger_previous_head_sha256"]
        != snapshot.snapshot.entry.previous_head_sha256
        or fields["ledger_state_sha256"] != snapshot.snapshot.entry.state_sha256
        or fields["ledger_phase"] != snapshot.phase
        or term != snapshot.active_term
        or active_term_sha256 != snapshot.active_term_sha256
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_LEDGER_HEAD_MISMATCH")
    if term.writer_epoch < request.minimum_writer_epoch:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_TERM_EPOCH_STALE")
    if (
        term.issued_at > now
        or term.expires_at <= now + timedelta(seconds=facts.safety_margin_seconds)
        or issued_at < term.issued_at
        or expires_at > term.expires_at
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_TERM_STALE")
    if fields["ledger_version"] < reservation.minimum_ledger_version:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_LEDGER_HEAD_STALE")
    if (
        fields["ledger_version"] == reservation.minimum_ledger_version
        and reservation.previous_ledger_head_sha256 is not None
        and fields["ledger_head_sha256"] != reservation.previous_ledger_head_sha256
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_LEDGER_HEAD_ROLLBACK")
    return VerifiedPhysicalOperationalFailoverV1WitnessCurrentTermAttestation(
        attestation_id=fields["attestation_id"],
        attestation_nonce=fields["attestation_nonce"],
        attestation_sha256=hashlib.sha256(canonical).hexdigest(),
        canonical_attestation=canonical,
        cluster_id=fields["cluster_id"],
        holder_site=fields["holder_site"],
        release_sha=fields["release_sha"],
        generation_id=fields["generation_id"],
        runtime_instance_id=fields["runtime_instance_id"],
        revalidation_id=fields["revalidation_id"],
        reservation_id=fields["reservation_id"],
        request_sha256=fields["request_sha256"],
        issued_at=issued_at,
        expires_at=expires_at,
        ledger_version=fields["ledger_version"],
        ledger_head_sha256=fields["ledger_head_sha256"],
        ledger_entry_sha256=fields["ledger_entry_sha256"],
        ledger_previous_head_sha256=fields["ledger_previous_head_sha256"],
        ledger_state_sha256=fields["ledger_state_sha256"],
        ledger_phase=fields["ledger_phase"],
        active_term=term,
        active_term_sha256=active_term_sha256,
    )


def _consumption(
    *,
    facts: _ConfigFacts,
    request: _RequestFacts,
    reservation: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
    attestation: VerifiedPhysicalOperationalFailoverV1WitnessCurrentTermAttestation,
    now: datetime,
) -> PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption:
    return PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption(
        schema=PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA,
        configuration_sha256=facts.configuration_sha256,
        durable_guard_id=facts.durable_guard_id,
        reservation_id=reservation.reservation_id,
        revalidation_id=request.revalidation_id,
        request_sha256=request.request_sha256,
        attestation_id=attestation.attestation_id,
        attestation_nonce=attestation.attestation_nonce,
        attestation_sha256=attestation.attestation_sha256,
        ledger_version=attestation.ledger_version,
        ledger_head_sha256=attestation.ledger_head_sha256,
        consumed_at=now,
    )


def _consumption_receipt(
    value: object,
    *,
    consumption: PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption,
) -> PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumptionReceipt:
    if type(value) is not PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumptionReceipt:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_CONSUMPTION_INVALID")
    for name in (
        "schema",
        "configuration_sha256",
        "durable_guard_id",
        "reservation_id",
        "revalidation_id",
        "request_sha256",
        "attestation_id",
        "attestation_nonce",
        "attestation_sha256",
        "ledger_version",
        "ledger_head_sha256",
        "consumed_at",
    ):
        if getattr(value, name) != getattr(consumption, name):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_CONSUMPTION_MISMATCH")
    _identifier(value.receipt_id, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_CONSUMPTION_INVALID")
    return value


def _provenance_from_attestation(
    *,
    facts: _ConfigFacts,
    owner_token: object,
    attestation: VerifiedPhysicalOperationalFailoverV1WitnessCurrentTermAttestation,
) -> _VerifiedCurrentTermProvenance:
    """Drop raw signed bytes/nonce after durable consumption succeeds."""

    term = attestation.active_term
    return _VerifiedCurrentTermProvenance(
        facts=facts,
        owner_token=owner_token,
        attestation_sha256=attestation.attestation_sha256,
        attestation_id=attestation.attestation_id,
        revalidation_id=attestation.revalidation_id,
        reservation_id=attestation.reservation_id,
        request_sha256=attestation.request_sha256,
        ledger_schema=facts.expected_ledger_schema,
        ledger_version=attestation.ledger_version,
        ledger_head_sha256=attestation.ledger_head_sha256,
        ledger_entry_sha256=attestation.ledger_entry_sha256,
        ledger_previous_head_sha256=attestation.ledger_previous_head_sha256,
        ledger_state_sha256=attestation.ledger_state_sha256,
        ledger_phase=attestation.ledger_phase,
        active_term_sha256=attestation.active_term_sha256,
        holder_site=term.holder_site,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witness_transition_id=term.witness_transition_id,
        witnessed_term_proof_sha256=term.witnessed_term_proof_sha256,
        attestation_issued_at=attestation.issued_at,
        attestation_expires_at=attestation.expires_at,
        term_issued_at=term.issued_at,
        term_expires_at=term.expires_at,
    )


def _require_provenance_fresh(
    provenance: _VerifiedCurrentTermProvenance,
    *,
    now: datetime,
    code: str,
) -> None:
    facts = provenance.facts
    if (
        now < provenance.attestation_issued_at
        or now - provenance.attestation_issued_at
        > timedelta(seconds=facts.maximum_attestation_age_seconds)
        or provenance.attestation_expires_at
        <= now + timedelta(seconds=facts.safety_margin_seconds)
        or provenance.term_issued_at > now
        or provenance.term_expires_at <= now + timedelta(seconds=facts.safety_margin_seconds)
    ):
        _fail(code)


def _writer_config_for_provenance(
    value: object,
    *,
    facts: _ConfigFacts,
    code: str,
) -> tuple[
    admission.PhysicalOperationalFailoverV1WriterAdmissionBinding,
    str,
    int,
    int,
    int,
]:
    """Require the V1 admission policy which produced the local state."""

    try:
        parsed = admission._config(value)
    except admission.PhysicalOperationalFailoverV1WriterAdmissionError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(code) from exc
    if parsed is None:
        _fail(code)
    binding, runtime_instance_id, margin, maximum_duration, maximum_age = parsed
    if (
        binding != facts.binding
        or runtime_instance_id != facts.runtime_instance_id
        or margin < facts.safety_margin_seconds
    ):
        _fail(code)
    return binding, runtime_instance_id, margin, maximum_duration, maximum_age


def _validated_revalidation_transition_for_provenance(
    *,
    transition: object,
    provenance: _VerifiedCurrentTermProvenance,
    writer_admission_config: object,
    observed_at: object,
) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
    """Validate the exact V1 transition before retaining its state identity."""

    code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_REVALIDATION_BINDING_INVALID"
    _writer_config_for_provenance(
        writer_admission_config,
        facts=provenance.facts,
        code=code,
    )
    observed = _utc(observed_at, code=code, require_second_precision=False)
    if (
        type(transition)
        is not admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition
        or transition._capability is not admission._STATE_TRANSITION_CAPABILITY
        or transition.kind != "witness_revalidation"
    ):
        _fail(code)
    try:
        candidate = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=transition.prior_state,
            transition=transition,
        )
    except admission.PhysicalOperationalFailoverV1WriterAdmissionError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(code) from exc
    if candidate is not transition.next_state:
        _fail(code)
    prior = transition.prior_state
    next_state = transition.next_state
    term = next_state.active_term
    if (
        type(prior) is not admission.PhysicalOperationalFailoverV1WriterAdmissionState
        or type(next_state) is not admission.PhysicalOperationalFailoverV1WriterAdmissionState
        or type(term) is not admission.PhysicalOperationalFailoverV1WriterTermSnapshot
        or next_state.binding != provenance.facts.binding
        or next_state.revalidated_runtime_instance_id != provenance.facts.runtime_instance_id
        or next_state.revision != prior.revision + 1
        or next_state.fence_generation != prior.fence_generation
        or next_state.fenced is not False
        or next_state.requires_fresh_witness_revalidation is not False
        or next_state.clock_floor != observed
        or (
            term.holder_site,
            term.writer_epoch,
            term.writer_lease_id,
            term.evidence_id,
            term.revalidation_id,
            term.issued_at,
            term.expires_at,
        )
        != (
            provenance.holder_site,
            provenance.writer_epoch,
            provenance.writer_lease_id,
            provenance.attestation_id,
            provenance.revalidation_id,
            provenance.attestation_issued_at,
            provenance.attestation_expires_at,
        )
    ):
        _fail(code)
    return next_state


def _validated_writer_admission_for_provenance(
    *,
    value: object,
    provenance: _VerifiedCurrentTermProvenance,
    writer_admission_config: object,
    now: datetime,
) -> tuple[
    admission.PhysicalOperationalFailoverV1WriterAdmission,
    admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    datetime,
    datetime,
]:
    """Require an exact opaque V1 transaction admission for one bound state."""

    code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_WRITER_ADMISSION_INVALID"
    _writer_config_for_provenance(
        writer_admission_config,
        facts=provenance.facts,
        code=code,
    )
    if (
        type(value) is not admission.PhysicalOperationalFailoverV1WriterAdmission
        or value._capability is not admission._ADMISSION_CAPABILITY
    ):
        _fail(code)
    transition = value.state_transition
    operation = value.operation
    if (
        type(transition)
        is not admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition
        or transition._capability is not admission._STATE_TRANSITION_CAPABILITY
        or transition.kind != "writer_admission"
        or type(operation) is not admission.PhysicalOperationalFailoverV1WriterOperation
        or operation._capability is not admission._OPERATION_CAPABILITY
        or operation.operation_kind
        != admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT
    ):
        _fail(code)
    try:
        candidate = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=transition.prior_state,
            transition=transition,
        )
    except admission.PhysicalOperationalFailoverV1WriterAdmissionError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(code) from exc
    prior = transition.prior_state
    next_state = transition.next_state
    term = prior.active_term
    admitted_at = _utc(value.admitted_at, code=code, require_second_precision=False)
    opened_at = _utc(operation.opened_at, code=code, require_second_precision=False)
    _writer_lease_id(operation.writer_lease_id, code=code)
    if (
        candidate is not next_state
        or type(prior) is not admission.PhysicalOperationalFailoverV1WriterAdmissionState
        or type(next_state) is not admission.PhysicalOperationalFailoverV1WriterAdmissionState
        or type(term) is not admission.PhysicalOperationalFailoverV1WriterTermSnapshot
        or type(value.term) is not admission.PhysicalOperationalFailoverV1WriterTermSnapshot
        or value.term is not term
        or prior.binding != provenance.facts.binding
        or prior.revalidated_runtime_instance_id != provenance.facts.runtime_instance_id
        or prior.fenced is not False
        or prior.requires_fresh_witness_revalidation is not False
        or next_state.revision != prior.revision + 1
        or next_state.fence_generation != prior.fence_generation
        or next_state.active_term is not term
        or next_state.fenced is not False
        or next_state.requires_fresh_witness_revalidation is not False
        or next_state.clock_floor != admitted_at
        or type(operation.runtime_instance_id) is not str
        or operation.runtime_instance_id != provenance.facts.runtime_instance_id
        or type(operation.opened_state_revision) is not int
        or isinstance(operation.opened_state_revision, bool)
        or operation.opened_state_revision < 0
        or operation.opened_state_revision > prior.revision
        or type(operation.fence_generation) is not int
        or isinstance(operation.fence_generation, bool)
        or operation.fence_generation != prior.fence_generation
        or type(operation.evidence_id) is not str
        or operation.evidence_id != provenance.attestation_id
        or type(operation.writer_epoch) is not int
        or isinstance(operation.writer_epoch, bool)
        or operation.writer_epoch != provenance.writer_epoch
        or type(operation.writer_lease_id) is not str
        or operation.writer_lease_id != provenance.writer_lease_id
        or opened_at > admitted_at
        or admitted_at > now
        or (
            prior.clock_floor is not None
            and admitted_at < _utc(
                prior.clock_floor,
                code=code,
                require_second_precision=False,
            )
        )
        or (
            term.holder_site,
            term.writer_epoch,
            term.writer_lease_id,
            term.evidence_id,
            term.revalidation_id,
            term.issued_at,
            term.expires_at,
        )
        != (
            provenance.holder_site,
            provenance.writer_epoch,
            provenance.writer_lease_id,
            provenance.attestation_id,
            provenance.revalidation_id,
            provenance.attestation_issued_at,
            provenance.attestation_expires_at,
        )
        or admitted_at < provenance.attestation_issued_at
        or admitted_at < provenance.term_issued_at
        or admitted_at >= provenance.attestation_expires_at
        or admitted_at >= provenance.term_expires_at
    ):
        _fail(code)
    return value, prior, opened_at, admitted_at


def _consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance(
    *,
    value: object,
    config: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig,
    now: datetime,
    required_writer_admission: object | None,
) -> PhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenanceProjection:
    """Consume a one-shot opaque V1 provenance handle for a Gen2 bridge.

    The caller must provide a trusted root-owned clock value.  Consumption
    burns the handle before returning the scalar projection, so a failed or
    interrupted bridge attempt cannot reuse the same V1 admission/attestation
    pairing.  A restart likewise loses the process-local handle and requires
    a fresh signed attestation, while the existing durable replay guard keeps
    the original attestation consumed across restarts.
    """

    code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_BOUND_HANDLE_INVALID"
    facts = _config(config)
    observed = _utc(now, code=code, require_second_precision=False)
    if (
        type(value)
        is not BoundPhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenance
        or value._capability is not _PROVENANCE_CAPABILITY
    ):
        _fail(code)
    bound = _identity_take(_BOUND_PROVENANCE, target=value, code=code)
    if type(bound) is not _BoundCurrentTermAdmissionProvenance:
        _fail(code)
    if required_writer_admission is not None:
        if (
            type(required_writer_admission)
            is not admission.PhysicalOperationalFailoverV1WriterAdmission
            or required_writer_admission._capability is not admission._ADMISSION_CAPABILITY
            or bound.writer_admission_reference() is not required_writer_admission
        ):
            # The handle has already been removed from the identity registry.
            # A forged/equal-but-foreign admission must not get a retry path
            # that could pair this verified term with a different V1 row.
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_WRITER_ADMISSION_MISMATCH")
    provenance = bound.provenance
    if provenance.facts.configuration_sha256 != facts.configuration_sha256:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_CONFIG_MISMATCH")
    _require_provenance_fresh(
        provenance,
        now=observed,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_STALE",
    )
    if bound.admitted_at > observed:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_CLOCK_REGRESSION")
    return PhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenanceProjection(
        schema=PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_SCHEMA,
        revalidator_configuration_sha256=provenance.facts.configuration_sha256,
        cluster_id=bound.cluster_id,
        local_site=bound.local_site,
        release_sha=bound.release_sha,
        generation_id=bound.generation_id,
        attestation_sha256=provenance.attestation_sha256,
        attestation_id=provenance.attestation_id,
        revalidation_id=provenance.revalidation_id,
        reservation_id=provenance.reservation_id,
        request_sha256=provenance.request_sha256,
        ledger_schema=provenance.ledger_schema,
        ledger_version=provenance.ledger_version,
        ledger_head_sha256=provenance.ledger_head_sha256,
        ledger_entry_sha256=provenance.ledger_entry_sha256,
        ledger_previous_head_sha256=provenance.ledger_previous_head_sha256,
        ledger_state_sha256=provenance.ledger_state_sha256,
        ledger_phase=provenance.ledger_phase,
        active_term_sha256=provenance.active_term_sha256,
        holder_site=provenance.holder_site,
        writer_epoch=provenance.writer_epoch,
        writer_lease_id=provenance.writer_lease_id,
        witness_transition_id=provenance.witness_transition_id,
        witnessed_term_proof_sha256=provenance.witnessed_term_proof_sha256,
        attestation_issued_at=provenance.attestation_issued_at,
        attestation_expires_at=provenance.attestation_expires_at,
        term_issued_at=provenance.term_issued_at,
        term_expires_at=provenance.term_expires_at,
        operation_kind=bound.operation_kind,
        prior_revision=bound.prior_revision,
        next_revision=bound.next_revision,
        fence_generation=bound.fence_generation,
        operation_opened_at=bound.operation_opened_at,
        admitted_at=bound.admitted_at,
    )


def consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance(
    *,
    value: object,
    config: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig,
    now: datetime,
) -> PhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenanceProjection:
    """Consume a V1 provenance handle without releasing its V1 object link.

    This compatibility projection is intentionally useful only to an owner
    which has no need to prove the exact V1 admission object at consumption.
    The V1/V2 bridge runtime must instead use the stricter sibling below.
    """

    return _consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance(
        value=value,
        config=config,
        now=now,
        required_writer_admission=None,
    )


def consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance_for_writer_admission(
    *,
    value: object,
    writer_admission: object,
    config: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig,
    now: datetime,
) -> PhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenanceProjection:
    """Consume V1 provenance only with the exact opaque V1 admission.

    This is the bridge-only handoff.  It rejects—and burns on—an equal-looking
    or reconstructed admission, rather than trusting public scalar fields.
    """

    return _consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance(
        value=value,
        config=config,
        now=now,
        required_writer_admission=writer_admission,
    )


def sign_physical_operational_failover_v1_witness_current_term_attestation(
    *,
    value: PhysicalOperationalFailoverV1WitnessCurrentTermAttestationInput,
    config: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig,
    private_key: Ed25519PrivateKey,
) -> bytes:
    """Canonical-sign one current-term attestation using its dedicated key role."""

    facts = _config(config)
    if not isinstance(private_key, Ed25519PrivateKey):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_PRIVATE_KEY_INVALID")
    try:
        public = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    except (TypeError, ValueError) as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_PRIVATE_KEY_INVALID"
        ) from exc
    if public != facts.attestation_public_key_raw:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_PRIVATE_KEY_ROLE_MISMATCH")
    unsigned = _attestation_mapping(
        value,
        facts=facts,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INPUT_INVALID",
    )
    signature = private_key.sign(
        _DOMAIN
        + _canonical(
            unsigned,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INPUT_INVALID",
        )
    )
    encoded = dict(unsigned)
    encoded["signature_base64"] = base64.b64encode(signature).decode("ascii")
    return _canonical(
        encoded,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INPUT_INVALID",
    )


def verify_physical_operational_failover_v1_witness_current_term_attestation(
    *,
    value: bytes,
    config: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig,
    request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
    reservation: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
    ledger_snapshot: ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot,
    now: datetime,
) -> VerifiedPhysicalOperationalFailoverV1WitnessCurrentTermAttestation:
    """Verify a signed response against one request, reservation, and snapshot."""

    facts = _config(config)
    request_facts = _request_facts(request, facts=facts)
    observed_now = _utc(now, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CLOCK_INVALID")
    if request_facts.clock_floor is not None and observed_now < request_facts.clock_floor:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CLOCK_REGRESSION")
    if type(value) is not bytes:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_ATTESTATION_INVALID")
    if type(reservation) is not PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID")
    reservation_requested_at = _utc(
        reservation.requested_at,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_INVALID",
    )
    reservation_request = _reservation_request(
        facts=facts,
        request=request_facts,
        now=reservation_requested_at,
    )
    normalized_reservation = _reservation(
        reservation,
        request=reservation_request,
        facts=facts,
        now=observed_now,
    )
    snapshot = _snapshot(ledger_snapshot, now=observed_now)
    return _verify_attestation(
        value,
        facts=facts,
        request=request_facts,
        reservation=normalized_reservation,
        snapshot=snapshot,
        now=observed_now,
    )


class PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator:
    """Narrow implementation of ``PhysicalOperationalFailoverV1WitnessTermRevalidator``.

    It returns the same narrow structural term-evidence object used by normal
    V1 admission.  When a caller explicitly passes this instance as the V1
    ``current_term_provenance_binder``, it can additionally retain an exact
    in-process identity link from that evidence object to the resulting V1
    state and then to one opaque transaction admission.  The link never puts
    raw attestation bytes into V1 state and disappears on restart.
    """

    def __init__(
        self,
        *,
        config: PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig,
        fetcher: PhysicalOperationalFailoverV1AuthenticatedWitnessCurrentTermFetcher,
        durable_guard: PhysicalOperationalFailoverV1DurableWitnessTermReplayGuard,
        clock: PhysicalOperationalFailoverV1WitnessCurrentTermClock,
    ) -> None:
        self._facts = _config(config)
        if not callable(getattr(fetcher, "fetch_current_term_attestation", None)):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_FETCHER_MISSING")
        if not callable(getattr(durable_guard, "reserve_revalidation", None)) or not callable(
            getattr(durable_guard, "consume_attestation", None)
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_MISSING")
        if not callable(getattr(clock, "now_utc", None)):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_CLOCK_MISSING")
        self._fetcher = fetcher
        self._durable_guard = durable_guard
        self._clock = clock
        self._provenance_owner_token = object()

    def bind_revalidated_current_term_provenance(
        self,
        *,
        evidence: admission.PhysicalOperationalFailoverV1WriterTermEvidence,
        state_transition: admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition,
        writer_admission_config: admission.PhysicalOperationalFailoverV1WriterAdmissionConfig,
        observed_at: datetime,
    ) -> None:
        """Bind one exact returned evidence object to one V1 state identity.

        This method is the explicit optional callback used by
        ``revalidate_physical_operational_failover_v1_writer_admission``.  It
        is intentionally local-only and has no durable or remote operation:
        the attestation was already cryptographically verified and durably
        consumed before the narrow evidence object was returned.
        """

        code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_EVIDENCE_CAPABILITY_REQUIRED"
        provenance = _identity_take(_EVIDENCE_PROVENANCE, target=evidence, code=code)
        if type(provenance) is not _VerifiedCurrentTermProvenance:
            _fail(code)
        if provenance.owner_token is not self._provenance_owner_token:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_OWNER_MISMATCH")
        if provenance.facts.configuration_sha256 != self._facts.configuration_sha256:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_CONFIG_MISMATCH")
        next_state = _validated_revalidation_transition_for_provenance(
            transition=state_transition,
            provenance=provenance,
            writer_admission_config=writer_admission_config,
            observed_at=observed_at,
        )
        floor = _utc(
            observed_at,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_REVALIDATION_BINDING_INVALID",
            require_second_precision=False,
        )
        trusted_now = _trusted_now(self._clock, floor=floor)
        _require_provenance_fresh(
            provenance,
            now=trusted_now,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_STALE",
        )
        _identity_store(
            _STATE_PROVENANCE,
            target=next_state,
            value=provenance,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_STATE_ALREADY_BOUND",
        )

    def bind_current_term_provenance_to_writer_admission(
        self,
        *,
        writer_admission: admission.PhysicalOperationalFailoverV1WriterAdmission,
        writer_admission_config: admission.PhysicalOperationalFailoverV1WriterAdmissionConfig,
    ) -> BoundPhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenance:
        """Mint one opaque V1-provenance handle for one exact V1 admission.

        The input admission must reference the *same in-memory state object*
        bound by :meth:`bind_revalidated_current_term_provenance`; matching
        scalar values or a restored state are insufficient.  The state entry
        is consumed before returning a handle, so both duplicate bridge work
        and crash/restart retry fail closed and require a fresh attestation.
        """

        code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_STATE_CAPABILITY_REQUIRED"
        if (
            type(writer_admission)
            is not admission.PhysicalOperationalFailoverV1WriterAdmission
            or writer_admission._capability is not admission._ADMISSION_CAPABILITY
            or type(writer_admission.state_transition)
            is not admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition
        ):
            _fail(code)
        provenance = _identity_take(
            _STATE_PROVENANCE,
            target=writer_admission.state_transition.prior_state,
            code=code,
        )
        if type(provenance) is not _VerifiedCurrentTermProvenance:
            _fail(code)
        if provenance.owner_token is not self._provenance_owner_token:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_OWNER_MISMATCH")
        if provenance.facts.configuration_sha256 != self._facts.configuration_sha256:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_CONFIG_MISMATCH")
        floor = writer_admission.state_transition.prior_state.clock_floor
        trusted_now = _trusted_now(self._clock, floor=floor)
        _require_provenance_fresh(
            provenance,
            now=trusted_now,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_STALE",
        )
        _checked, prior, opened_at, admitted_at = _validated_writer_admission_for_provenance(
            value=writer_admission,
            provenance=provenance,
            writer_admission_config=writer_admission_config,
            now=trusted_now,
        )
        if prior is not writer_admission.state_transition.prior_state:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_WRITER_ADMISSION_INVALID")
        result = BoundPhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenance(
            capability=_PROVENANCE_CAPABILITY
        )
        try:
            writer_admission_reference = ref(writer_admission)
        except TypeError:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_WRITER_ADMISSION_INVALID")
        _identity_store(
            _BOUND_PROVENANCE,
            target=result,
            value=_BoundCurrentTermAdmissionProvenance(
                provenance=provenance,
                writer_admission_reference=writer_admission_reference,
                cluster_id=provenance.facts.binding.cluster_id,
                local_site=provenance.facts.binding.local_site,
                release_sha=provenance.facts.binding.release_sha,
                generation_id=provenance.facts.binding.generation_id,
                operation_kind=writer_admission.operation.operation_kind,
                prior_revision=prior.revision,
                next_revision=writer_admission.state_transition.next_state.revision,
                fence_generation=prior.fence_generation,
                operation_opened_at=opened_at,
                admitted_at=admitted_at,
            ),
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_BOUND_HANDLE_INVALID",
        )
        return result

    def revalidate_writer_term(
        self,
        *,
        request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
    ) -> PhysicalOperationalFailoverV1WitnessCurrentTermTermEvidence:
        """Reserve, authenticate, verify, durably consume, then project evidence."""

        request_facts = _request_facts(request, facts=self._facts)
        before_fetch = _trusted_now(self._clock, floor=request_facts.clock_floor)
        reservation_request = _reservation_request(
            facts=self._facts,
            request=request_facts,
            now=before_fetch,
        )
        try:
            raw_reservation = self._durable_guard.reserve_revalidation(
                request=reservation_request
            )
        except PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError:
            raise
        except Exception as exc:
            raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
                "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_RESERVATION_FAILED"
            ) from exc
        reservation = _reservation(
            raw_reservation,
            request=reservation_request,
            facts=self._facts,
            now=before_fetch,
        )
        try:
            response = self._fetcher.fetch_current_term_attestation(
                request=request_facts.request,
                reservation=reservation,
            )
        except PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError:
            raise
        except Exception as exc:
            raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
                "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_FETCH_FAILED"
            ) from exc
        if type(response) is not PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_FETCH_RESPONSE_INVALID")
        after_fetch = _trusted_now(self._clock, floor=before_fetch)
        snapshot = _snapshot(response.ledger_snapshot, now=after_fetch)
        attestation = _verify_attestation(
            response.canonical_attestation,
            facts=self._facts,
            request=request_facts,
            reservation=reservation,
            snapshot=snapshot,
            now=after_fetch,
        )
        consumption = _consumption(
            facts=self._facts,
            request=request_facts,
            reservation=reservation,
            attestation=attestation,
            now=after_fetch,
        )
        try:
            raw_receipt = self._durable_guard.consume_attestation(
                reservation=reservation,
                consumption=consumption,
            )
        except PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError:
            raise
        except Exception as exc:
            raise PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError(
                "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_GUARD_CONSUMPTION_FAILED"
            ) from exc
        _consumption_receipt(raw_receipt, consumption=consumption)
        evidence = PhysicalOperationalFailoverV1WitnessCurrentTermTermEvidence(
            cluster_id=attestation.cluster_id,
            holder_site=attestation.holder_site,
            writer_epoch=attestation.active_term.writer_epoch,
            writer_lease_id=attestation.active_term.writer_lease_id,
            release_sha=attestation.release_sha,
            generation_id=attestation.generation_id,
            evidence_id=attestation.attestation_id,
            revalidation_id=attestation.revalidation_id,
            issued_at=attestation.issued_at,
            expires_at=attestation.expires_at,
        )
        _identity_store(
            _EVIDENCE_PROVENANCE,
            target=evidence,
            value=_provenance_from_attestation(
                facts=self._facts,
                owner_token=self._provenance_owner_token,
                attestation=attestation,
            ),
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_EVIDENCE_REGISTRATION_FAILED",
        )
        return evidence
