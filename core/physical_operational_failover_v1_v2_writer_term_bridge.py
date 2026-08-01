"""Pure, pre-transaction V1/V2 writer-term bridge intent contract.

This module deliberately solves only the narrow timing problem between the
existing V1 writer-admission boundary and the V2 strict-writer boundary:

* the bridge *intent certificate* is signed before a PostgreSQL transaction;
* a later local transaction may persist the V1 admission parent; and
* a pure parent-binding step proves that the persisted parent projection is
  exactly the one that was pre-certified.

The eventual V2 local commit receipt must embed the canonical certificate,
its hash, the V1 parent identifiers, and ``parent_binding_sha256`` under its
own local-commit signature.  The certificate intentionally does **not**
claim that a database row exists; its parent fields are unavailable until the
transaction.  Conversely, a raw parent projection never becomes authority:
the returned bound value is process-local and non-serializable.

No function opens a database, performs network or filesystem I/O, starts a
worker, contacts a Witness, calls asyncio, or changes traffic.  A future
integration must feed this contract only projections produced from opaque,
freshly revalidated V1 and V2 capabilities.  This module is default-off and
does not make raw V1/V2 evidence authoritative by itself.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.append_only_sync_delta_batch import LEASE_ID_RE


__all__ = (
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SCHEMA",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_PARENT_BINDING_SCHEMA",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_SCHEMA",
    "BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeConfig",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeError",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeIntent",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeBoundProjection",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeBoundIntentProjection",
    "VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate",
    "bind_physical_operational_failover_v1_v2_writer_term_bridge_parent",
    "issue_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate",
    "project_bound_physical_operational_failover_v1_v2_writer_term_bridge_intent",
    "require_bound_physical_operational_failover_v1_v2_writer_term_bridge_intent",
    "verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-v2-writer-term-bridge-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-v2-writer-term-bridge-intent-certificate-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_PARENT_BINDING_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-v2-writer-term-bridge-parent-binding-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_DEFAULT_ENABLED = False

_VERSION = 1
_MAX_CERTIFICATE_BYTES = 64 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_STREAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
_CLUSTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$", re.ASCII)
_RELEASE_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.ASCII)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_SITES = frozenset({"webapp_fi", "webapp_ir"})
# These are the exact canonical values emitted by the legacy V2 strict-writer
# runtime from its active object-delta role matrix.  Do not accept aliases or
# silently translate spellings at the certificate boundary.
_ACTIVATION_MODES = frozenset({"normal_fi_writer", "promoted_ir_writer"})
_DOMAIN = (
    PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SCHEMA
    + "\x00"
).encode("ascii")
_VERIFIED_CAPABILITY = object()
_BOUND_CAPABILITY = object()


class PhysicalOperationalFailoverV1V2WriterTermBridgeError(ValueError):
    """A V1/V2 pre-issued bridge intent is unsafe or inconsistent."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1V2WriterTermBridgeError(code)


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1V2WriterTermBridgeConfig:
    """Default-off policy and public key-role pins for one local bridge."""

    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_DEFAULT_ENABLED
    cluster_id: str | None = None
    local_site: str | None = None
    release_sha: str | None = None
    generation_id: str | None = None
    expected_v1_revalidator_configuration_sha256: str | None = None
    expected_v2_strict_writer_configuration_sha256: str | None = None
    expected_v2_context_sha256: str | None = None
    expected_v2_activation_mode: str | None = None
    expected_v2_stream_generation_id: str | None = None
    bridge_signer_public_key: bytes = b""
    bridge_signer_key_id: str | None = None
    v1_current_term_signer_public_key: bytes = b""
    v1_promotion_signer_public_key: bytes = b""
    v2_witness_public_key: bytes = b""
    v2_fi_outbox_public_key: bytes = b""
    v2_ir_recovery_exporter_public_key: bytes = b""
    v2_ir_durable_assertion_public_key: bytes = b""
    v2_remote_source_public_key: bytes = b""
    v2_remote_destination_public_key: bytes = b""
    v2_local_commit_signer_public_key: bytes = b""
    safety_margin_seconds: int = 5
    maximum_certificate_age_seconds: int = 30


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance:
    """Non-authorizing projection of an already verified V1 term attestation."""

    attestation_sha256: str
    attestation_id: str
    revalidation_id: str
    configuration_sha256: str
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
class PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission:
    """Public semantic projection of the exact pre-persist V1 admission."""

    cluster_id: str
    local_site: str
    release_sha: str
    generation_id: str
    operation_kind: str
    prior_revision: int
    next_revision: int
    fence_generation: int
    evidence_id: str
    revalidation_id: str
    writer_epoch: int
    writer_lease_id: str
    opened_at: datetime
    admitted_at: datetime
    term_evidence_issued_at: datetime
    term_evidence_expires_at: datetime


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction:
    """Public stable V2 strict-prepared instruction projection."""

    strict_schema: str
    configuration_sha256: str
    atomic_commit_boundary: str
    commit_id: str
    attestation_sha256: str
    context_sha256: str
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
    attestation_issued_at: datetime
    attestation_expires_at: datetime
    term_issued_at: datetime
    term_expires_at: datetime


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1V2WriterTermBridgeIntent:
    """Public, non-authorizing inputs which must originate from opaque callers."""

    v1_admission: PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission
    v1_current_term: PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance
    v2_instruction: PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt:
    """Public projection of the just-persisted V1 parent receipt.

    It is deliberately not a proof that a database commit occurred.  The
    future SQL adapter must derive it from its own opaque successful adapter
    receipt while the database FK/trigger validates parent existence.
    """

    commit_id: str
    commit_sha256: str
    receipt_sha256: str
    cluster_id: str
    local_site: str
    release_sha: str
    generation_id: str
    prior_revision: int
    next_revision: int
    fence_generation: int
    writer_epoch: int
    writer_lease_id: str
    evidence_id: str
    revalidation_id: str
    admitted_at: datetime


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate:
    """Verified, process-local pre-transaction certificate capability."""

    certificate_id: str
    certificate_sha256: str
    intent_sha256: str
    canonical_certificate: bytes
    issued_at: datetime
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(self, *, certificate_id: str, certificate_sha256: str, intent_sha256: str, canonical_certificate: bytes, issued_at: datetime, expires_at: datetime, capability: object) -> None:
        if capability is not _VERIFIED_CAPABILITY:
            raise TypeError("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_CONSTRUCTION_FORBIDDEN")
        object.__setattr__(self, "certificate_id", certificate_id)
        object.__setattr__(self, "certificate_sha256", certificate_sha256)
        object.__setattr__(self, "intent_sha256", intent_sha256)
        object.__setattr__(self, "canonical_certificate", canonical_certificate)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False, init=False)
class BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent:
    """Opaque capability after one exact V1 parent projection is bound."""

    certificate_id: str
    certificate_sha256: str
    intent_sha256: str
    parent_binding_sha256: str
    parent_commit_id: str
    parent_commit_sha256: str
    parent_receipt_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(self, *, certificate_id: str, certificate_sha256: str, intent_sha256: str, parent_binding_sha256: str, parent_commit_id: str, parent_commit_sha256: str, parent_receipt_sha256: str, capability: object) -> None:
        if capability is not _BOUND_CAPABILITY:
            raise TypeError("V1_V2_WRITER_TERM_BRIDGE_BOUND_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("certificate_id", certificate_id),
            ("certificate_sha256", certificate_sha256),
            ("intent_sha256", intent_sha256),
            ("parent_binding_sha256", parent_binding_sha256),
            ("parent_commit_id", parent_commit_id),
            ("parent_commit_sha256", parent_commit_sha256),
            ("parent_receipt_sha256", parent_receipt_sha256),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V1_V2_WRITER_TERM_BRIDGE_BOUND_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1V2WriterTermBridgeBoundProjection:
    """Non-authorizing pins a V2 receipt v2 must embed and sign."""

    certificate_id: str
    certificate_sha256: str
    intent_sha256: str
    parent_binding_sha256: str
    parent_commit_id: str
    parent_commit_sha256: str
    parent_receipt_sha256: str
    canonical_certificate: bytes


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1V2WriterTermBridgeBoundIntentProjection:
    """Non-authorizing, verified intent detail for a bound bridge capability.

    The compact :class:`PhysicalOperationalFailoverV1V2WriterTermBridgeBoundProjection`
    is enough for durable storage, but a Gen2 V2 receipt verifier must also
    cross-pin the *already verified* V2 instruction and complete V1 intent to
    its own opaque prepared capability.  This additive projection is the only
    supported way to obtain those details from a bound bridge.  It does not
    accept raw V1 evidence, decode caller-supplied certificate bytes, or turn
    its public fields into authority; callers must retain and revalidate the
    opaque bound capability for every use.
    """

    certificate_id: str
    certificate_sha256: str
    intent_sha256: str
    parent_binding_sha256: str
    parent_commit_id: str
    parent_commit_sha256: str
    parent_receipt_sha256: str
    canonical_certificate: bytes
    v1_admission: PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission
    v1_current_term: PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance
    v2_instruction: PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction
    parent: PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt


@dataclass(frozen=True)
class _Facts:
    config_sha256: str
    bridge_signer: Ed25519PublicKey
    bridge_signer_raw: bytes
    bridge_signer_key_id: str
    cluster_id: str
    local_site: str
    release_sha: str
    generation_id: str
    v1_config_sha256: str
    v2_config_sha256: str
    v2_context_sha256: str
    activation_mode: str
    stream_generation_id: str
    safety_margin_seconds: int
    maximum_certificate_age_seconds: int


@dataclass(frozen=True)
class _CertificateState:
    config: PhysicalOperationalFailoverV1V2WriterTermBridgeConfig
    canonical_certificate: bytes
    intent: PhysicalOperationalFailoverV1V2WriterTermBridgeIntent


@dataclass(frozen=True)
class _BoundState:
    certificate: VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate
    parent: PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt
    parent_binding_sha256: str


_CERTIFICATE_STATES: WeakKeyDictionary[
    VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate, _CertificateState
] = WeakKeyDictionary()
_BOUND_STATES: WeakKeyDictionary[BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent, _BoundState] = WeakKeyDictionary()


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalOperationalFailoverV1V2WriterTermBridgeError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")
        result[key] = value
    return result


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


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _writer_lease_id(value: object, *, code: str) -> str:
    """Accept exactly the established V1/V2 lease grammar, not generic IDs.

    V1/V2 can emit a canonical lease such as ``writer-lease-73`` (15
    characters).  It is valid under the shared lease contract even though it
    is one character shorter than this bridge's generic audit-identifier
    minimum.  Keep lease validation separate so accepting that legitimate
    legacy value never broadens the grammar for certificate IDs or other
    bridge identifiers.
    """

    if type(value) is not str or LEASE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None or (not permit_zero and value == "0" * 64):
        _fail(code)
    return value


def _public_key(value: object, *, code: str) -> tuple[Ed25519PublicKey, bytes]:
    if type(value) is not bytes or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        return Ed25519PublicKey.from_public_bytes(value), value
    except ValueError:
        _fail(code)


def _facts(value: object) -> _Facts:
    if type(value) is not PhysicalOperationalFailoverV1V2WriterTermBridgeConfig or value.enabled is not True:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CONFIG_DISABLED_OR_INVALID")
    if (
        type(value.cluster_id) is not str or _CLUSTER_RE.fullmatch(value.cluster_id) is None
        or value.local_site not in _SITES
        or type(value.release_sha) is not str or _RELEASE_RE.fullmatch(value.release_sha) is None
        or type(value.generation_id) is not str or _ID_RE.fullmatch(value.generation_id) is None
        or type(value.expected_v2_activation_mode) is not str or value.expected_v2_activation_mode not in _ACTIVATION_MODES
        or type(value.expected_v2_stream_generation_id) is not str or _STREAM_RE.fullmatch(value.expected_v2_stream_generation_id) is None
        or type(value.safety_margin_seconds) is not int or not 1 <= value.safety_margin_seconds <= 60
        or type(value.maximum_certificate_age_seconds) is not int or not 2 <= value.maximum_certificate_age_seconds <= 300
        or value.safety_margin_seconds >= value.maximum_certificate_age_seconds
    ):
        _fail("V1_V2_WRITER_TERM_BRIDGE_CONFIG_INVALID")
    v1_config = _sha(value.expected_v1_revalidator_configuration_sha256, code="V1_V2_WRITER_TERM_BRIDGE_CONFIG_INVALID")
    v2_config = _sha(value.expected_v2_strict_writer_configuration_sha256, code="V1_V2_WRITER_TERM_BRIDGE_CONFIG_INVALID")
    context = _sha(value.expected_v2_context_sha256, code="V1_V2_WRITER_TERM_BRIDGE_CONFIG_INVALID")
    signer, signer_raw = _public_key(value.bridge_signer_public_key, code="V1_V2_WRITER_TERM_BRIDGE_CONFIG_INVALID")
    key_id = _identifier(value.bridge_signer_key_id, code="V1_V2_WRITER_TERM_BRIDGE_CONFIG_INVALID")
    keys: list[bytes] = [signer_raw]
    for item in (
        value.v1_current_term_signer_public_key,
        value.v1_promotion_signer_public_key,
        value.v2_witness_public_key,
        value.v2_fi_outbox_public_key,
        value.v2_ir_recovery_exporter_public_key,
        value.v2_ir_durable_assertion_public_key,
        value.v2_remote_source_public_key,
        value.v2_remote_destination_public_key,
        value.v2_local_commit_signer_public_key,
    ):
        _checked, raw = _public_key(item, code="V1_V2_WRITER_TERM_BRIDGE_CONFIG_INVALID")
        keys.append(raw)
    if len(set(keys)) != len(keys):
        _fail("V1_V2_WRITER_TERM_BRIDGE_KEY_ROLE_COLLISION")
    payload = {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_SCHEMA,
        "cluster_id": value.cluster_id,
        "local_site": value.local_site,
        "release_sha": value.release_sha,
        "generation_id": value.generation_id,
        "v1_revalidator_configuration_sha256": v1_config,
        "v2_strict_writer_configuration_sha256": v2_config,
        "v2_context_sha256": context,
        "v2_activation_mode": value.expected_v2_activation_mode,
        "v2_stream_generation_id": value.expected_v2_stream_generation_id,
        "bridge_signer_key_id": key_id,
        "public_key_sha256": [hashlib.sha256(key).hexdigest() for key in keys],
        "safety_margin_seconds": value.safety_margin_seconds,
        "maximum_certificate_age_seconds": value.maximum_certificate_age_seconds,
    }
    return _Facts(
        config_sha256=hashlib.sha256(_canonical(payload, code="V1_V2_WRITER_TERM_BRIDGE_CONFIG_INVALID")).hexdigest(),
        bridge_signer=signer,
        bridge_signer_raw=signer_raw,
        bridge_signer_key_id=key_id,
        cluster_id=value.cluster_id,
        local_site=value.local_site,
        release_sha=value.release_sha,
        generation_id=value.generation_id,
        v1_config_sha256=v1_config,
        v2_config_sha256=v2_config,
        v2_context_sha256=context,
        activation_mode=value.expected_v2_activation_mode,
        stream_generation_id=value.expected_v2_stream_generation_id,
        safety_margin_seconds=value.safety_margin_seconds,
        maximum_certificate_age_seconds=value.maximum_certificate_age_seconds,
    )


def _term_window(issued: object, expires: object, *, now: datetime, margin: int, code: str) -> tuple[datetime, datetime]:
    start = _utc(issued, code=code)
    end = _utc(expires, code=code)
    if start > now or end <= start or end <= now + timedelta(seconds=margin):
        _fail(code)
    return start, end


def _intent_mapping(value: object, *, facts: _Facts, now: datetime) -> dict[str, object]:
    if type(value) is not PhysicalOperationalFailoverV1V2WriterTermBridgeIntent:
        _fail("V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    a = value.v1_admission
    p = value.v1_current_term
    v = value.v2_instruction
    if (
        type(a) is not PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission
        or type(p) is not PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance
        or type(v) is not PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction
        or (a.cluster_id, a.local_site, a.release_sha, a.generation_id) != (facts.cluster_id, facts.local_site, facts.release_sha, facts.generation_id)
        or a.operation_kind != "transaction_commit"
        or type(a.prior_revision) is not int or a.prior_revision < 0
        or type(a.next_revision) is not int or a.next_revision != a.prior_revision + 1
        or type(a.fence_generation) is not int or a.fence_generation < 0
        or type(a.writer_epoch) is not int or a.writer_epoch < 1
        or p.configuration_sha256 != facts.v1_config_sha256
        or v.configuration_sha256 != facts.v2_config_sha256
        or v.context_sha256 != facts.v2_context_sha256
        or v.writer_holder_site != facts.local_site
        or v.activation_mode != facts.activation_mode
        or v.activation_stream_generation_id != facts.stream_generation_id
    ):
        _fail("V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    for item in (
        a.evidence_id, a.revalidation_id,
        p.attestation_id, p.revalidation_id, p.reservation_id, p.witness_transition_id,
        v.strict_schema, v.atomic_commit_boundary, v.commit_id, v.witness_transition_id,
    ):
        _identifier(item, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    for item in (a.writer_lease_id, p.writer_lease_id, v.writer_lease_id):
        _writer_lease_id(item, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    if p.ledger_phase not in {"fi-active", "ir-active"} or (p.holder_site not in _SITES) or (v.writer_holder_site not in _SITES):
        _fail("V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    for item in (
        p.attestation_sha256, p.request_sha256, p.ledger_head_sha256, p.ledger_entry_sha256,
        p.ledger_state_sha256, p.active_term_sha256, p.witnessed_term_proof_sha256,
        v.attestation_sha256, v.context_sha256, v.witnessed_term_proof_sha256,
        v.activation_route_artifact_sha256, v.activation_source_cutover_attestation_sha256,
        v.activation_receiver_permit_sha256,
    ):
        _sha(item, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    _sha(p.ledger_previous_head_sha256, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID", permit_zero=True)
    if type(p.ledger_schema) is not str or not p.ledger_schema or type(p.ledger_version) is not int or p.ledger_version < 1:
        _fail("V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    if type(p.writer_epoch) is not int or p.writer_epoch < 1 or type(v.writer_epoch) is not int or v.writer_epoch < 1:
        _fail("V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    evidence_start, evidence_end = _term_window(a.term_evidence_issued_at, a.term_evidence_expires_at, now=now, margin=facts.safety_margin_seconds, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_STALE")
    attestation_start, attestation_end = _term_window(p.attestation_issued_at, p.attestation_expires_at, now=now, margin=facts.safety_margin_seconds, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_STALE")
    term_start, term_end = _term_window(p.term_issued_at, p.term_expires_at, now=now, margin=facts.safety_margin_seconds, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_STALE")
    v2_attestation_start, v2_attestation_end = _term_window(v.attestation_issued_at, v.attestation_expires_at, now=now, margin=facts.safety_margin_seconds, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_STALE")
    v2_term_start, v2_term_end = _term_window(v.term_issued_at, v.term_expires_at, now=now, margin=facts.safety_margin_seconds, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_STALE")
    admitted_at = _utc(a.admitted_at, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    opened_at = _utc(a.opened_at, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")
    if (
        admitted_at < opened_at or admitted_at < evidence_start or evidence_start != attestation_start
        or evidence_end != attestation_end or (p.holder_site, p.writer_epoch, p.writer_lease_id, p.witness_transition_id, p.witnessed_term_proof_sha256, term_start, term_end)
        != (v.writer_holder_site, v.writer_epoch, v.writer_lease_id, v.witness_transition_id, v.witnessed_term_proof_sha256, v2_term_start, v2_term_end)
        or (a.evidence_id, a.revalidation_id, a.writer_epoch, a.writer_lease_id)
        != (p.attestation_id, p.revalidation_id, p.writer_epoch, p.writer_lease_id)
        or p.holder_site != facts.local_site
    ):
        _fail("V1_V2_WRITER_TERM_BRIDGE_TERM_CROSS_PIN_MISMATCH")
    return {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_SCHEMA,
        "v1_admission": {
            "cluster_id": a.cluster_id, "local_site": a.local_site, "release_sha": a.release_sha, "generation_id": a.generation_id,
            "operation_kind": a.operation_kind, "prior_revision": a.prior_revision, "next_revision": a.next_revision,
            "fence_generation": a.fence_generation, "evidence_id": a.evidence_id, "revalidation_id": a.revalidation_id,
            "writer_epoch": a.writer_epoch, "writer_lease_id": a.writer_lease_id,
            "opened_at": _render_time(opened_at, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
            "admitted_at": _render_time(admitted_at, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
            "term_evidence_issued_at": _render_time(evidence_start, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
            "term_evidence_expires_at": _render_time(evidence_end, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
        },
        "v1_current_term": {
            "attestation_sha256": p.attestation_sha256, "attestation_id": p.attestation_id, "revalidation_id": p.revalidation_id,
            "configuration_sha256": p.configuration_sha256, "reservation_id": p.reservation_id, "request_sha256": p.request_sha256,
            "ledger_schema": p.ledger_schema, "ledger_version": p.ledger_version, "ledger_head_sha256": p.ledger_head_sha256,
            "ledger_entry_sha256": p.ledger_entry_sha256, "ledger_previous_head_sha256": p.ledger_previous_head_sha256,
            "ledger_state_sha256": p.ledger_state_sha256, "ledger_phase": p.ledger_phase, "active_term_sha256": p.active_term_sha256,
            "holder_site": p.holder_site, "writer_epoch": p.writer_epoch, "writer_lease_id": p.writer_lease_id,
            "witness_transition_id": p.witness_transition_id, "witnessed_term_proof_sha256": p.witnessed_term_proof_sha256,
            "attestation_issued_at": _render_time(attestation_start, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
            "attestation_expires_at": _render_time(attestation_end, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
            "term_issued_at": _render_time(term_start, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
            "term_expires_at": _render_time(term_end, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
        },
        "v2_instruction": {
            "strict_schema": v.strict_schema, "configuration_sha256": v.configuration_sha256,
            "atomic_commit_boundary": v.atomic_commit_boundary, "commit_id": v.commit_id,
            "attestation_sha256": v.attestation_sha256, "context_sha256": v.context_sha256,
            "writer_holder_site": v.writer_holder_site, "writer_epoch": v.writer_epoch, "writer_lease_id": v.writer_lease_id,
            "witnessed_term_proof_sha256": v.witnessed_term_proof_sha256, "witness_transition_id": v.witness_transition_id,
            "activation_mode": v.activation_mode, "activation_stream_generation_id": v.activation_stream_generation_id,
            "activation_route_artifact_sha256": v.activation_route_artifact_sha256,
            "activation_source_cutover_attestation_sha256": v.activation_source_cutover_attestation_sha256,
            "activation_receiver_permit_sha256": v.activation_receiver_permit_sha256,
            "attestation_issued_at": _render_time(v2_attestation_start, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
            "attestation_expires_at": _render_time(v2_attestation_end, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
            "term_issued_at": _render_time(v2_term_start, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
            "term_expires_at": _render_time(v2_term_end, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID"),
        },
    }


def _certificate_unsigned(*, facts: _Facts, intent_mapping: dict[str, object], issued_at: datetime, expires_at: datetime) -> dict[str, object]:
    intent_sha = hashlib.sha256(_canonical(intent_mapping, code="V1_V2_WRITER_TERM_BRIDGE_INTENT_INVALID")).hexdigest()
    return {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SCHEMA,
        "version": _VERSION,
        "kind": "preissued-v1-v2-writer-term-intent",
        "bridge_configuration_sha256": facts.config_sha256,
        "signer_key_id": facts.bridge_signer_key_id,
        "certificate_id": "v1-v2-writer-term-bridge-cert-" + intent_sha,
        "intent_sha256": intent_sha,
        "issued_at": _render_time(issued_at, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID"),
        "expires_at": _render_time(expires_at, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID"),
        "intent": intent_mapping,
    }


def _private_key(value: object, *, facts: _Facts) -> Ed25519PrivateKey:
    if not isinstance(value, Ed25519PrivateKey):
        _fail("V1_V2_WRITER_TERM_BRIDGE_SIGNER_PRIVATE_KEY_INVALID")
    try:
        raw = value.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    except (TypeError, ValueError):
        _fail("V1_V2_WRITER_TERM_BRIDGE_SIGNER_PRIVATE_KEY_INVALID")
    if raw != facts.bridge_signer_raw:
        _fail("V1_V2_WRITER_TERM_BRIDGE_SIGNER_KEY_MISMATCH")
    return value


def issue_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(*, config: PhysicalOperationalFailoverV1V2WriterTermBridgeConfig, intent: PhysicalOperationalFailoverV1V2WriterTermBridgeIntent, private_key: Ed25519PrivateKey, now: datetime, expires_at: datetime) -> bytes:
    """Sign a bridge intent before opening any database transaction."""

    facts = _facts(config)
    observed = _utc(now, code="V1_V2_WRITER_TERM_BRIDGE_CLOCK_INVALID")
    mapping = _intent_mapping(intent, facts=facts, now=observed)
    expiry = _utc(expires_at, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")
    windows = (
        intent.v1_admission.term_evidence_expires_at,
        intent.v1_current_term.term_expires_at,
        intent.v2_instruction.attestation_expires_at,
        intent.v2_instruction.term_expires_at,
    )
    if expiry <= observed + timedelta(seconds=facts.safety_margin_seconds) or expiry - observed > timedelta(seconds=facts.maximum_certificate_age_seconds) or any(expiry > _utc(item, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID") for item in windows):
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_WINDOW_UNSAFE")
    signer = _private_key(private_key, facts=facts)
    unsigned = _certificate_unsigned(facts=facts, intent_mapping=mapping, issued_at=observed, expires_at=expiry)
    signature = signer.sign(_DOMAIN + _canonical(unsigned, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID"))
    encoded = dict(unsigned)
    encoded["signature_base64"] = base64.b64encode(signature).decode("ascii")
    return _canonical(encoded, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")


def _decode_certificate(value: object, *, facts: _Facts, now: datetime) -> tuple[dict[str, object], bytes, PhysicalOperationalFailoverV1V2WriterTermBridgeIntent]:
    if type(value) is not bytes or not 1 <= len(value) <= _MAX_CERTIFICATE_BYTES:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")
    try:
        item = json.loads(value.decode("ascii", "strict"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")
    expected_fields = {"schema", "version", "kind", "bridge_configuration_sha256", "signer_key_id", "certificate_id", "intent_sha256", "issued_at", "expires_at", "intent", "signature_base64"}
    if type(item) is not dict or set(item) != expected_fields or _canonical(item, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID") != value:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")
    if item["schema"] != PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SCHEMA or item["version"] != _VERSION or item["kind"] != "preissued-v1-v2-writer-term-intent" or item["bridge_configuration_sha256"] != facts.config_sha256 or item["signer_key_id"] != facts.bridge_signer_key_id:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_CONFIG_MISMATCH")
    _identifier(item["certificate_id"], code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")
    if type(item["signature_base64"]) is not str:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SIGNATURE_INVALID")
    try:
        signature = base64.b64decode(item["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SIGNATURE_INVALID")
    if len(signature) != 64:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SIGNATURE_INVALID")
    unsigned = {key: part for key, part in item.items() if key != "signature_base64"}
    try:
        facts.bridge_signer.verify(signature, _DOMAIN + _canonical(unsigned, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID"))
    except (InvalidSignature, ValueError, TypeError):
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_SIGNATURE_INVALID")
    issued = _parse_time(item["issued_at"], code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")
    expires = _parse_time(item["expires_at"], code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")
    if issued > now or expires <= issued or now - issued > timedelta(seconds=facts.maximum_certificate_age_seconds) or expires <= now + timedelta(seconds=facts.safety_margin_seconds):
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_STALE")
    if expires - issued > timedelta(seconds=facts.maximum_certificate_age_seconds):
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_WINDOW_UNSAFE")
    # Reconstructing public dataclasses would create a second parser with a
    # broad raw-evidence surface.  Instead validate the canonical mapping
    # against the supplied policy and retain a private, non-authorizing token.
    intent_mapping = item["intent"]
    if type(intent_mapping) is not dict or item["intent_sha256"] != hashlib.sha256(_canonical(intent_mapping, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")).hexdigest() or item["certificate_id"] != "v1-v2-writer-term-bridge-cert-" + item["intent_sha256"]:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INTENT_MISMATCH")
    # The certificate parser keeps the exact public map; a future V1/V2
    # adapter supplies the independently opaque inputs before it issues one.
    # For now, validate all cross-pins through a structural rehydration helper.
    intent = _intent_from_mapping(intent_mapping, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID")
    canonical = _intent_mapping(intent, facts=facts, now=now)
    if canonical != intent_mapping:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INTENT_MISMATCH")
    underlying_expiries = (
        intent.v1_admission.term_evidence_expires_at,
        intent.v1_current_term.attestation_expires_at,
        intent.v1_current_term.term_expires_at,
        intent.v2_instruction.attestation_expires_at,
        intent.v2_instruction.term_expires_at,
    )
    if any(expires > _utc(value, code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID") for value in underlying_expiries):
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_WINDOW_UNSAFE")
    return item, value, intent


def _intent_from_mapping(value: object, *, code: str) -> PhysicalOperationalFailoverV1V2WriterTermBridgeIntent:
    if type(value) is not dict or set(value) != {"schema", "v1_admission", "v1_current_term", "v2_instruction"} or value["schema"] != PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_SCHEMA:
        _fail(code)
    a, p, v = value["v1_admission"], value["v1_current_term"], value["v2_instruction"]
    if type(a) is not dict or type(p) is not dict or type(v) is not dict:
        _fail(code)
    try:
        return PhysicalOperationalFailoverV1V2WriterTermBridgeIntent(
            v1_admission=PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission(
                cluster_id=a["cluster_id"], local_site=a["local_site"], release_sha=a["release_sha"], generation_id=a["generation_id"], operation_kind=a["operation_kind"], prior_revision=a["prior_revision"], next_revision=a["next_revision"], fence_generation=a["fence_generation"], evidence_id=a["evidence_id"], revalidation_id=a["revalidation_id"], writer_epoch=a["writer_epoch"], writer_lease_id=a["writer_lease_id"], opened_at=_parse_time(a["opened_at"], code=code), admitted_at=_parse_time(a["admitted_at"], code=code), term_evidence_issued_at=_parse_time(a["term_evidence_issued_at"], code=code), term_evidence_expires_at=_parse_time(a["term_evidence_expires_at"], code=code),
            ),
            v1_current_term=PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance(
                attestation_sha256=p["attestation_sha256"], attestation_id=p["attestation_id"], revalidation_id=p["revalidation_id"], configuration_sha256=p["configuration_sha256"], reservation_id=p["reservation_id"], request_sha256=p["request_sha256"], ledger_schema=p["ledger_schema"], ledger_version=p["ledger_version"], ledger_head_sha256=p["ledger_head_sha256"], ledger_entry_sha256=p["ledger_entry_sha256"], ledger_previous_head_sha256=p["ledger_previous_head_sha256"], ledger_state_sha256=p["ledger_state_sha256"], ledger_phase=p["ledger_phase"], active_term_sha256=p["active_term_sha256"], holder_site=p["holder_site"], writer_epoch=p["writer_epoch"], writer_lease_id=p["writer_lease_id"], witness_transition_id=p["witness_transition_id"], witnessed_term_proof_sha256=p["witnessed_term_proof_sha256"], attestation_issued_at=_parse_time(p["attestation_issued_at"], code=code), attestation_expires_at=_parse_time(p["attestation_expires_at"], code=code), term_issued_at=_parse_time(p["term_issued_at"], code=code), term_expires_at=_parse_time(p["term_expires_at"], code=code),
            ),
            v2_instruction=PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction(
                strict_schema=v["strict_schema"], configuration_sha256=v["configuration_sha256"], atomic_commit_boundary=v["atomic_commit_boundary"], commit_id=v["commit_id"], attestation_sha256=v["attestation_sha256"], context_sha256=v["context_sha256"], writer_holder_site=v["writer_holder_site"], writer_epoch=v["writer_epoch"], writer_lease_id=v["writer_lease_id"], witnessed_term_proof_sha256=v["witnessed_term_proof_sha256"], witness_transition_id=v["witness_transition_id"], activation_mode=v["activation_mode"], activation_stream_generation_id=v["activation_stream_generation_id"], activation_route_artifact_sha256=v["activation_route_artifact_sha256"], activation_source_cutover_attestation_sha256=v["activation_source_cutover_attestation_sha256"], activation_receiver_permit_sha256=v["activation_receiver_permit_sha256"], attestation_issued_at=_parse_time(v["attestation_issued_at"], code=code), attestation_expires_at=_parse_time(v["attestation_expires_at"], code=code), term_issued_at=_parse_time(v["term_issued_at"], code=code), term_expires_at=_parse_time(v["term_expires_at"], code=code),
            ),
        )
    except (KeyError, TypeError):
        _fail(code)


def verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(*, value: bytes, config: PhysicalOperationalFailoverV1V2WriterTermBridgeConfig, now: datetime) -> VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate:
    """Verify a pre-issued certificate and mint a non-serializable handle."""

    facts = _facts(config)
    observed = _utc(now, code="V1_V2_WRITER_TERM_BRIDGE_CLOCK_INVALID")
    item, canonical, intent = _decode_certificate(value, facts=facts, now=observed)
    result = VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate(
        certificate_id=item["certificate_id"], certificate_sha256=hashlib.sha256(canonical).hexdigest(), intent_sha256=item["intent_sha256"], canonical_certificate=canonical, issued_at=_parse_time(item["issued_at"], code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID"), expires_at=_parse_time(item["expires_at"], code="V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_INVALID"), capability=_VERIFIED_CAPABILITY,
    )
    _CERTIFICATE_STATES[result] = _CertificateState(config=config, canonical_certificate=canonical, intent=intent)
    return result


def _certificate_state(value: object, *, config: PhysicalOperationalFailoverV1V2WriterTermBridgeConfig, now: datetime) -> tuple[VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate, _CertificateState, _Facts]:
    if type(value) is not VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate or value._capability is not _VERIFIED_CAPABILITY:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_CAPABILITY_REQUIRED")
    state = _CERTIFICATE_STATES.get(value)
    if state is None:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_CAPABILITY_REQUIRED")
    facts = _facts(config)
    saved = _facts(state.config)
    if facts.config_sha256 != saved.config_sha256:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_CONFIG_MISMATCH")
    item, canonical, intent = _decode_certificate(state.canonical_certificate, facts=facts, now=now)
    if canonical != state.canonical_certificate or intent != state.intent or value.certificate_id != item["certificate_id"] or value.certificate_sha256 != hashlib.sha256(canonical).hexdigest() or value.intent_sha256 != item["intent_sha256"] or value.canonical_certificate != canonical:
        _fail("V1_V2_WRITER_TERM_BRIDGE_CERTIFICATE_TAMPERED")
    return value, state, facts


def _parent(value: object, *, intent: PhysicalOperationalFailoverV1V2WriterTermBridgeIntent) -> PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt:
    if type(value) is not PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt:
        _fail("V1_V2_WRITER_TERM_BRIDGE_PARENT_INVALID")
    for item in (
        value.commit_id,
        value.cluster_id,
        value.generation_id,
        value.evidence_id,
        value.revalidation_id,
    ):
        _identifier(item, code="V1_V2_WRITER_TERM_BRIDGE_PARENT_INVALID")
    _writer_lease_id(
        value.writer_lease_id,
        code="V1_V2_WRITER_TERM_BRIDGE_PARENT_INVALID",
    )
    for item in (value.commit_sha256, value.receipt_sha256):
        _sha(item, code="V1_V2_WRITER_TERM_BRIDGE_PARENT_INVALID")
    if (
        (value.cluster_id, value.local_site, value.release_sha, value.generation_id, value.prior_revision, value.next_revision, value.fence_generation, value.writer_epoch, value.writer_lease_id, value.evidence_id, value.revalidation_id, _utc(value.admitted_at, code="V1_V2_WRITER_TERM_BRIDGE_PARENT_INVALID"))
        != (intent.v1_admission.cluster_id, intent.v1_admission.local_site, intent.v1_admission.release_sha, intent.v1_admission.generation_id, intent.v1_admission.prior_revision, intent.v1_admission.next_revision, intent.v1_admission.fence_generation, intent.v1_admission.writer_epoch, intent.v1_admission.writer_lease_id, intent.v1_admission.evidence_id, intent.v1_admission.revalidation_id, _utc(intent.v1_admission.admitted_at, code="V1_V2_WRITER_TERM_BRIDGE_PARENT_INVALID"))
    ):
        _fail("V1_V2_WRITER_TERM_BRIDGE_PARENT_INTENT_MISMATCH")
    return value


def _parent_binding_sha(*, certificate_sha256: str, intent_sha256: str, intent: PhysicalOperationalFailoverV1V2WriterTermBridgeIntent, parent: PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt) -> str:
    return hashlib.sha256(_canonical({
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_PARENT_BINDING_SCHEMA,
        "certificate_sha256": certificate_sha256, "intent_sha256": intent_sha256,
        "v2_commit_id": intent.v2_instruction.commit_id,
        "parent_commit_id": parent.commit_id, "parent_commit_sha256": parent.commit_sha256, "parent_receipt_sha256": parent.receipt_sha256,
    }, code="V1_V2_WRITER_TERM_BRIDGE_PARENT_INVALID")).hexdigest()


def bind_physical_operational_failover_v1_v2_writer_term_bridge_parent(*, certificate: VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate, parent: PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt, config: PhysicalOperationalFailoverV1V2WriterTermBridgeConfig, now: datetime) -> BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent:
    """Bind the already-persisted V1 parent without signer, I/O, or async work."""

    observed = _utc(now, code="V1_V2_WRITER_TERM_BRIDGE_CLOCK_INVALID")
    verified, state, _facts_value = _certificate_state(certificate, config=config, now=observed)
    checked_parent = _parent(parent, intent=state.intent)
    digest = _parent_binding_sha(certificate_sha256=verified.certificate_sha256, intent_sha256=verified.intent_sha256, intent=state.intent, parent=checked_parent)
    result = BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent(
        certificate_id=verified.certificate_id, certificate_sha256=verified.certificate_sha256, intent_sha256=verified.intent_sha256, parent_binding_sha256=digest, parent_commit_id=checked_parent.commit_id, parent_commit_sha256=checked_parent.commit_sha256, parent_receipt_sha256=checked_parent.receipt_sha256, capability=_BOUND_CAPABILITY,
    )
    _BOUND_STATES[result] = _BoundState(certificate=verified, parent=checked_parent, parent_binding_sha256=digest)
    return result


def require_bound_physical_operational_failover_v1_v2_writer_term_bridge_intent(*, value: object, config: PhysicalOperationalFailoverV1V2WriterTermBridgeConfig, now: datetime) -> PhysicalOperationalFailoverV1V2WriterTermBridgeBoundProjection:
    """Revalidate an opaque binding and expose only receipt-embedding pins."""

    if type(value) is not BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent or value._capability is not _BOUND_CAPABILITY:
        _fail("V1_V2_WRITER_TERM_BRIDGE_BOUND_CAPABILITY_REQUIRED")
    state = _BOUND_STATES.get(value)
    if state is None:
        _fail("V1_V2_WRITER_TERM_BRIDGE_BOUND_CAPABILITY_REQUIRED")
    observed = _utc(now, code="V1_V2_WRITER_TERM_BRIDGE_CLOCK_INVALID")
    certificate, certificate_state, _facts_value = _certificate_state(state.certificate, config=config, now=observed)
    parent = _parent(state.parent, intent=certificate_state.intent)
    expected = _parent_binding_sha(certificate_sha256=certificate.certificate_sha256, intent_sha256=certificate.intent_sha256, intent=certificate_state.intent, parent=parent)
    if (value.certificate_id, value.certificate_sha256, value.intent_sha256, value.parent_binding_sha256, value.parent_commit_id, value.parent_commit_sha256, value.parent_receipt_sha256) != (certificate.certificate_id, certificate.certificate_sha256, certificate.intent_sha256, expected, parent.commit_id, parent.commit_sha256, parent.receipt_sha256):
        _fail("V1_V2_WRITER_TERM_BRIDGE_BOUND_TAMPERED")
    return PhysicalOperationalFailoverV1V2WriterTermBridgeBoundProjection(
        certificate_id=certificate.certificate_id, certificate_sha256=certificate.certificate_sha256, intent_sha256=certificate.intent_sha256, parent_binding_sha256=expected, parent_commit_id=parent.commit_id, parent_commit_sha256=parent.commit_sha256, parent_receipt_sha256=parent.receipt_sha256, canonical_certificate=certificate_state.canonical_certificate,
    )


def project_bound_physical_operational_failover_v1_v2_writer_term_bridge_intent(
    *,
    value: object,
    config: PhysicalOperationalFailoverV1V2WriterTermBridgeConfig,
    now: datetime,
) -> PhysicalOperationalFailoverV1V2WriterTermBridgeBoundIntentProjection:
    """Expose verified intent pins for one freshly revalidated bound handle.

    A Gen2 response needs to compare the certificate's V2 instruction against
    a separately opaque V2 prepared capability.  Re-parsing its canonical
    bytes in that response would introduce a second raw-evidence parser and
    could accidentally make unbound V1 data authoritative.  Keep that parser
    and capability ownership here instead: first perform the normal complete
    bound revalidation, then return only the exact intent retained by this
    module's private state.
    """

    compact = require_bound_physical_operational_failover_v1_v2_writer_term_bridge_intent(
        value=value,
        config=config,
        now=now,
    )
    # ``require_bound`` above has already verified both the opaque capability
    # and the private state.  Fetching it again only projects data from that
    # same state; the explicit consistency comparison below prevents a
    # process-local mutation from becoming a silent alternate authority.
    state = _BOUND_STATES.get(value)
    if state is None:
        _fail("V1_V2_WRITER_TERM_BRIDGE_BOUND_CAPABILITY_REQUIRED")
    certificate, certificate_state, _facts_value = _certificate_state(
        state.certificate,
        config=config,
        now=now,
    )
    parent = _parent(state.parent, intent=certificate_state.intent)
    expected = _parent_binding_sha(
        certificate_sha256=certificate.certificate_sha256,
        intent_sha256=certificate.intent_sha256,
        intent=certificate_state.intent,
        parent=parent,
    )
    if (
        compact.certificate_id,
        compact.certificate_sha256,
        compact.intent_sha256,
        compact.parent_binding_sha256,
        compact.parent_commit_id,
        compact.parent_commit_sha256,
        compact.parent_receipt_sha256,
        compact.canonical_certificate,
    ) != (
        certificate.certificate_id,
        certificate.certificate_sha256,
        certificate.intent_sha256,
        expected,
        parent.commit_id,
        parent.commit_sha256,
        parent.receipt_sha256,
        certificate_state.canonical_certificate,
    ):
        _fail("V1_V2_WRITER_TERM_BRIDGE_BOUND_TAMPERED")
    return PhysicalOperationalFailoverV1V2WriterTermBridgeBoundIntentProjection(
        certificate_id=compact.certificate_id,
        certificate_sha256=compact.certificate_sha256,
        intent_sha256=compact.intent_sha256,
        parent_binding_sha256=compact.parent_binding_sha256,
        parent_commit_id=compact.parent_commit_id,
        parent_commit_sha256=compact.parent_commit_sha256,
        parent_receipt_sha256=compact.parent_receipt_sha256,
        canonical_certificate=compact.canonical_certificate,
        v1_admission=certificate_state.intent.v1_admission,
        v1_current_term=certificate_state.intent.v1_current_term,
        v2_instruction=certificate_state.intent.v2_instruction,
        parent=parent,
    )
