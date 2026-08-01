"""V2-only, role-local raw-S3 adapters for the fixed Witness mailboxes.

No connection is made by this module at construction time.  A deployment must
first supply a fresh typed mailbox admission plus a separately signed
Object-Lock retention proof.  Only then, inside one role-named callback, the
adapter opens its fixed root-owned credential file and exposes a narrowly
typed raw-S3 operation to an injected scope.  The scope owns provider/end
point details outside this module; this module never imports an SDK or offers
a generic transport/client/list/read API.

The retention proof is intentionally not treated as proof generated here.  It
is a fail-closed deployment evidence input.  A real provider probe/attestation
must establish it before use; without that evidence no adapter opens a
credential file or invokes a raw-S3 callback.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol, TypeVar

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_delivery_contract as _delivery
from core import physical_wal_v2_witness_roundtrip_delivery_runtime as _runtime
from core import physical_wal_v2_witness_roundtrip_mailbox_admission as _admission


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_MAXIMUM_DELIVERY_BYTES",
    "DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_MAXIMUM_LIST_ENTRIES",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_SCHEMA",
    "PhysicalWalV2WitnessRoundtripFiToWitnessPublisherRawS3",
    "PhysicalWalV2WitnessRoundtripFiToWitnessPublisherS3Adapter",
    "PhysicalWalV2WitnessRoundtripFiToWitnessPublisherS3Scope",
    "PhysicalWalV2WitnessRoundtripFiAckIngressRawS3",
    "PhysicalWalV2WitnessRoundtripFiAckIngressS3Adapter",
    "PhysicalWalV2WitnessRoundtripFiAckIngressS3Scope",
    "PhysicalWalV2WitnessRoundtripIrToWitnessPublisherRawS3",
    "PhysicalWalV2WitnessRoundtripIrToWitnessPublisherS3Adapter",
    "PhysicalWalV2WitnessRoundtripIrToWitnessPublisherS3Scope",
    "PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead",
    "PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator",
    "PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead",
    "PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig",
    "PhysicalWalV2WitnessRoundtripS3MailboxAdapterError",
    "PhysicalWalV2WitnessRoundtripS3ObjectVersion",
    "PhysicalWalV2WitnessRoundtripS3RetentionProofConfig",
    "PhysicalWalV2WitnessRoundtripWitnessFiIngressRawS3",
    "PhysicalWalV2WitnessRoundtripWitnessFiIngressS3Adapter",
    "PhysicalWalV2WitnessRoundtripWitnessFiIngressS3Scope",
    "PhysicalWalV2WitnessRoundtripWitnessFiPublisherRawS3",
    "PhysicalWalV2WitnessRoundtripWitnessFiPublisherS3Adapter",
    "PhysicalWalV2WitnessRoundtripWitnessFiPublisherS3Scope",
    "PhysicalWalV2WitnessRoundtripWitnessIrEgressRawS3",
    "PhysicalWalV2WitnessRoundtripWitnessIrEgressS3Adapter",
    "PhysicalWalV2WitnessRoundtripWitnessIrEgressS3Scope",
    "PhysicalWalV2WitnessRoundtripWitnessIrIngressRawS3",
    "PhysicalWalV2WitnessRoundtripWitnessIrIngressS3Adapter",
    "PhysicalWalV2WitnessRoundtripWitnessIrIngressS3Scope",
    "PhysicalWalV2WitnessRoundtripIrStandbyIngressRawS3",
    "PhysicalWalV2WitnessRoundtripIrStandbyIngressS3Adapter",
    "PhysicalWalV2WitnessRoundtripIrStandbyIngressS3Scope",
    "VerifiedPhysicalWalV2WitnessRoundtripS3RetentionProof",
    "open_physical_wal_v2_witness_roundtrip_fi_to_witness_publisher_s3_adapter",
    "open_physical_wal_v2_witness_roundtrip_ir_to_witness_publisher_s3_adapter",
    "open_physical_wal_v2_witness_roundtrip_witness_fi_ingress_s3_adapter",
    "open_physical_wal_v2_witness_roundtrip_witness_to_fi_publisher_s3_adapter",
    "open_physical_wal_v2_witness_roundtrip_witness_to_ir_publisher_s3_adapter",
    "open_physical_wal_v2_witness_roundtrip_ir_standby_ingress_s3_adapter",
    "open_physical_wal_v2_witness_roundtrip_witness_ir_ingress_s3_adapter",
    "open_physical_wal_v2_witness_roundtrip_fi_ack_ingress_s3_adapter",
    "require_verified_physical_wal_v2_witness_roundtrip_s3_retention_proof",
    "verify_physical_wal_v2_witness_roundtrip_s3_retention_proof",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-s3-mailbox-adapter-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_MAXIMUM_DELIVERY_BYTES = 2 * 1024 * 1024
DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_MAXIMUM_LIST_ENTRIES = 128

_MAX_DELIVERY_BYTES = 2 * 1024 * 1024
_MAX_LIST_ENTRIES = 1024
_MAX_CREDENTIAL_BYTES = 32 * 1024
_CREDENTIAL_DIRECTORY = "physical-wal-v2-witness-roundtrip-s3-mailbox-credentials-v1"
_CREDENTIAL_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-s3-fixed-role-credential-v1"
_CREDENTIAL_VERSION = 1
_RETENTION_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-s3-retention-proof-v1"
_RETENTION_VERSION = 1
_RETENTION_DOMAIN = b"gold-trade-physical-wal-v2-witness-roundtrip-s3-retention-proof-v1\x00"
_RETENTION_MODE = "object-lock-compliance"
_CAPABILITY = object()
_CREDENTIAL_CAPABILITY = object()
_ZERO_SHA256 = "0" * 64

_FI_TO_WITNESS = "fi-to-witness"
_WITNESS_TO_IR = "witness-to-ir"
_IR_TO_WITNESS = "ir-to-witness"
_WITNESS_TO_FI = "witness-to-fi"
_PUBLISH = "publish"
_CONSUME = "consume"
_OBJECT_ROOT = "physical-wal-v2-witness-roundtrip-delivery-v1"

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,255}$", re.ASCII)
_ACCESS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,127}$", re.ASCII)
_EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)

_CREDENTIAL_FIELDS = frozenset(
    {"schema", "version", "local_role", "access_key_id", "secret_access_key"}
)
_RETENTION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "host_id",
        "local_role",
        "mailbox",
        "direction",
        "object_prefix",
        "policy_sha256",
        "deployment_binding_sha256",
        "delivery_binding_sha256",
        "host_role_assertion_sha256",
        "retention_mode",
        "minimum_retention_seconds",
        "evidence_id",
        "evidence_nonce",
        "issued_at",
        "expires_at",
        "signature_base64",
    }
)


class PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(RuntimeError):
    """A role-local raw-S3 object observation is unsafe or insufficiently proven."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripS3RetentionProofConfig:
    """Public verification inputs for signed Object-Lock deployment evidence."""

    admission_config: _admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig | None = field(
        default=None,
        repr=False,
    )
    mailbox_admission: _admission.VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission | None = field(
        default=None,
        repr=False,
    )
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DEFAULT_ENABLED


@dataclass(frozen=True, init=False)
class VerifiedPhysicalWalV2WitnessRoundtripS3RetentionProof:
    """Typed proof that an external deployment authority attested Object-Lock semantics.

    It is not a provider call.  The adapter still requires each returned object
    receipt/head to carry this exact proof hash and a compliance retention time.
    """

    host_id: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    policy_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    host_role_assertion_sha256: str
    retention_mode: str
    minimum_retention_seconds: int
    evidence_id: str
    evidence_nonce: str
    issued_at: datetime
    expires_at: datetime
    proof_sha256: str
    canonical_proof: bytes = field(repr=False)
    _admission_sha256: str = field(repr=False)
    _capability: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        host_id: str,
        local_role: str,
        mailbox: str,
        direction: str,
        object_prefix: str,
        policy_sha256: str,
        deployment_binding_sha256: str,
        delivery_binding_sha256: str,
        host_role_assertion_sha256: str,
        retention_mode: str,
        minimum_retention_seconds: int,
        evidence_id: str,
        evidence_nonce: str,
        issued_at: datetime,
        expires_at: datetime,
        proof_sha256: str,
        canonical_proof: bytes,
        admission_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("host_id", host_id),
            ("local_role", local_role),
            ("mailbox", mailbox),
            ("direction", direction),
            ("object_prefix", object_prefix),
            ("policy_sha256", policy_sha256),
            ("deployment_binding_sha256", deployment_binding_sha256),
            ("delivery_binding_sha256", delivery_binding_sha256),
            ("host_role_assertion_sha256", host_role_assertion_sha256),
            ("retention_mode", retention_mode),
            ("minimum_retention_seconds", minimum_retention_seconds),
            ("evidence_id", evidence_id),
            ("evidence_nonce", evidence_nonce),
            ("issued_at", issued_at),
            ("expires_at", expires_at),
            ("proof_sha256", proof_sha256),
            ("canonical_proof", canonical_proof),
            ("_admission_sha256", admission_sha256),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig:
    """Default-off configuration for one separately opened fixed-role adapter."""

    admission_config: _admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig | None = field(
        default=None,
        repr=False,
    )
    mailbox_admission: _admission.VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission | None = field(
        default=None,
        repr=False,
    )
    delivery_config: _delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig | None = field(
        default=None,
        repr=False,
    )
    retention_proof: VerifiedPhysicalWalV2WitnessRoundtripS3RetentionProof | None = field(
        default=None,
        repr=False,
    )
    credential_root: Path | None = None
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DEFAULT_ENABLED
    maximum_delivery_bytes: int = DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_MAXIMUM_DELIVERY_BYTES
    maximum_list_entries: int = DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_MAXIMUM_LIST_ENTRIES


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripS3ObjectVersion:
    """Raw callback result of a conditional create-only object write."""

    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retained_until: datetime
    conditional_create_only: bool
    object_lock_compliance: bool
    retention_proof_sha256: str


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead:
    """Raw callback result of an exact immutable-version metadata readback."""

    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retained_until: datetime
    object_lock_compliance: bool
    retention_proof_sha256: str


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead:
    """Raw callback result of an exact immutable-version bounded body read."""

    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retained_until: datetime
    object_lock_compliance: bool
    retention_proof_sha256: str
    canonical_delivery: bytes = field(repr=False)


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator:
    """Raw callback locator already limited by a role-named fixed-prefix operation."""

    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retained_until: datetime
    object_lock_compliance: bool
    retention_proof_sha256: str


@dataclass(frozen=True, init=False)
class _FixedRoleCredentials:
    local_role: str
    access_key_id: str
    secret_access_key: str = field(repr=False)
    _capability: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        local_role: str,
        access_key_id: str,
        secret_access_key: str,
        capability: object,
    ) -> None:
        if capability is not _CREDENTIAL_CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_CONSTRUCTION_FORBIDDEN")
        object.__setattr__(self, "local_role", local_role)
        object.__setattr__(self, "access_key_id", access_key_id)
        object.__setattr__(self, "secret_access_key", secret_access_key)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Policy:
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    actions: tuple[str, ...]


@dataclass(frozen=True)
class _Config:
    admission_config: _admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig
    mailbox_admission: _admission.VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission
    delivery_config: _delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig
    retention_proof: VerifiedPhysicalWalV2WitnessRoundtripS3RetentionProof
    credential_root: Path
    policy: _Policy
    delivery_binding_sha256: str
    maximum_delivery_bytes: int
    maximum_list_entries: int


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(code) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_JSON_INVALID")


def _parse_canonical(value: object, *, maximum_bytes: int, code: str) -> tuple[dict[str, Any], bytes]:
    if type(value) is not bytes or not 1 <= len(value) <= maximum_bytes:
        _fail(code)
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(code) from exc
    if type(parsed) is not dict or _canonical(parsed, code=code) != value:
        _fail(code)
    return dict(parsed), value


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if result.microsecond != 0:
        _fail(code)
    return result


def _timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(code) from exc


def _host_now() -> datetime:
    try:
        return datetime.now(timezone.utc).replace(microsecond=0)
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CLOCK_INVALID"
        ) from exc


def _object_prefix(mailbox: str) -> str:
    if mailbox not in {_FI_TO_WITNESS, _WITNESS_TO_IR, _IR_TO_WITNESS, _WITNESS_TO_FI}:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_MAILBOX_INVALID")
    return _OBJECT_ROOT + "/" + mailbox + "/"


def _object_key(mailbox: str, digest: str) -> str:
    return _object_prefix(mailbox) + _sha256(
        digest, code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_OBJECT_KEY_INVALID"
    ) + ".json"


def _key_sha256(value: object, *, policy: _Policy, code: str) -> str:
    if type(value) is not str or not value.isascii() or not value.startswith(policy.object_prefix):
        _fail(code)
    suffix = value[len(policy.object_prefix) :]
    if not suffix.endswith(".json"):
        _fail(code)
    return _sha256(suffix[:-5], code=code)


def _version(value: object, *, code: str) -> str:
    if type(value) is not str or _VERSION_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _policy_for_role(local_role: str) -> _Policy:
    for item in _admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES:
        if item.local_role == local_role:
            return _Policy(
                local_role=item.local_role,
                mailbox=item.mailbox,
                direction=item.direction,
                object_prefix=item.object_prefix,
                actions=item.least_privilege_actions,
            )
    _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_ROLE_INVALID")


def _retention_unsigned(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result.pop("signature_base64", None)
    return result


def _b64_signature(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(code) from exc
    if len(result) != 64:
        _fail(code)
    return result


def _retention_context(
    value: object,
    *,
    now: datetime,
) -> tuple[
    _admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig,
    _admission.VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission,
    Ed25519PublicKey,
]:
    if type(value) is not PhysicalWalV2WitnessRoundtripS3RetentionProofConfig or value.enabled is not True:
        _fail("V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CONFIG_INVALID")
    if type(value.admission_config) is not _admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig:
        _fail("V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CONFIG_INVALID")
    try:
        admitted = _admission.require_verified_physical_wal_v2_witness_roundtrip_mailbox_admission(
            value.mailbox_admission,
            config=value.admission_config,
            now=now,
        )
    except _admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_ADMISSION_INVALID"
        ) from exc
    public = value.admission_config.host_role_authority_public_key
    if type(public) is not bytes or len(public) != 32:
        _fail("V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CONFIG_INVALID")
    try:
        authority = Ed25519PublicKey.from_public_bytes(public)
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CONFIG_INVALID"
        ) from exc
    return value.admission_config, admitted, authority


def verify_physical_wal_v2_witness_roundtrip_s3_retention_proof(
    proof: bytes,
    *,
    config: PhysicalWalV2WitnessRoundtripS3RetentionProofConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripS3RetentionProof:
    """Verify signed external Object-Lock evidence bound to one typed admission."""

    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CLOCK_INVALID")
    _admission_config, admitted, authority = _retention_context(config, now=observed)
    item, raw = _parse_canonical(
        proof,
        maximum_bytes=128 * 1024,
        code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_INVALID",
    )
    item = _exact_mapping(
        item,
        fields=_RETENTION_FIELDS,
        code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_INVALID",
    )
    signature = _b64_signature(
        item["signature_base64"], code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_INVALID"
    )
    try:
        authority.verify(
            signature,
            _RETENTION_DOMAIN
            + _canonical(
                _retention_unsigned(item),
                code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_INVALID",
            ),
        )
    except InvalidSignature as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_SIGNATURE_INVALID"
        ) from exc
    if (
        item["schema"] != _RETENTION_SCHEMA
        or item["version"] != _RETENTION_VERSION
        or item["host_id"] != admitted.host_id
        or item["local_role"] != admitted.local_role
        or item["mailbox"] != admitted.mailbox
        or item["direction"] != admitted.direction
        or item["object_prefix"] != admitted.object_prefix
        or _sha256(item["policy_sha256"], code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_INVALID")
        != admitted.policy_sha256
        or _sha256(
            item["deployment_binding_sha256"], code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_INVALID"
        )
        != admitted.deployment_binding_sha256
        or _sha256(
            item["delivery_binding_sha256"], code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_INVALID"
        )
        != admitted.delivery_binding_sha256
        or _sha256(
            item["host_role_assertion_sha256"], code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_INVALID"
        )
        != admitted.host_role_assertion_sha256
        or item["retention_mode"] != _RETENTION_MODE
        or type(item["minimum_retention_seconds"]) is not int
        or not 1 <= item["minimum_retention_seconds"] <= 31_536_000
        or type(item["evidence_id"]) is not str
        or _EVIDENCE_ID_RE.fullmatch(item["evidence_id"]) is None
        or type(item["evidence_nonce"]) is not str
        or _NONCE_RE.fullmatch(item["evidence_nonce"]) is None
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CROSS_PIN_MISMATCH")
    issued_at = _parse_timestamp(
        item["issued_at"], code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_TIME_INVALID"
    )
    expires_at = _parse_timestamp(
        item["expires_at"], code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_TIME_INVALID"
    )
    if issued_at > observed or expires_at <= observed or expires_at <= issued_at:
        _fail("V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_STALE")
    return VerifiedPhysicalWalV2WitnessRoundtripS3RetentionProof(
        host_id=admitted.host_id,
        local_role=admitted.local_role,
        mailbox=admitted.mailbox,
        direction=admitted.direction,
        object_prefix=admitted.object_prefix,
        policy_sha256=admitted.policy_sha256,
        deployment_binding_sha256=admitted.deployment_binding_sha256,
        delivery_binding_sha256=admitted.delivery_binding_sha256,
        host_role_assertion_sha256=admitted.host_role_assertion_sha256,
        retention_mode=_RETENTION_MODE,
        minimum_retention_seconds=item["minimum_retention_seconds"],
        evidence_id=item["evidence_id"],
        evidence_nonce=item["evidence_nonce"],
        issued_at=issued_at,
        expires_at=expires_at,
        proof_sha256=_sha256_bytes(raw),
        canonical_proof=raw,
        admission_sha256=admitted.admission_sha256,
        capability=_CAPABILITY,
    )


def require_verified_physical_wal_v2_witness_roundtrip_s3_retention_proof(
    proof: object,
    *,
    config: PhysicalWalV2WitnessRoundtripS3RetentionProofConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripS3RetentionProof:
    """Require a fresh typed retention proof before credentials or raw-S3 scope."""

    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CLOCK_INVALID")
    _admission_config, admitted, _authority = _retention_context(config, now=observed)
    if (
        type(proof) is not VerifiedPhysicalWalV2WitnessRoundtripS3RetentionProof
        or proof._capability is not _CAPABILITY
        or proof._admission_sha256 != admitted.admission_sha256
        or proof.host_id != admitted.host_id
        or proof.local_role != admitted.local_role
        or proof.mailbox != admitted.mailbox
        or proof.direction != admitted.direction
        or proof.object_prefix != admitted.object_prefix
        or proof.policy_sha256 != admitted.policy_sha256
        or proof.deployment_binding_sha256 != admitted.deployment_binding_sha256
        or proof.delivery_binding_sha256 != admitted.delivery_binding_sha256
        or proof.host_role_assertion_sha256 != admitted.host_role_assertion_sha256
        or proof.retention_mode != _RETENTION_MODE
        or type(proof.minimum_retention_seconds) is not int
        or proof.minimum_retention_seconds < 1
        or proof.issued_at > observed
        or proof.expires_at <= observed
        or _sha256(proof.proof_sha256, code="V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CAPABILITY_INVALID")
        != proof.proof_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_RETENTION_PROOF_CAPABILITY_INVALID")
    return proof


def _safe_credential_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(
        part in {"", ".", ".."} for part in value.parts[1:]
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_ROOT_UNSAFE")
    return value


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_ROOT_REQUIRED")
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_ROOT_REQUIRED"
        ) from exc


def _directory_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_PLATFORM_UNSAFE")
    close_on_exec = os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec


def _check_directory(descriptor: int, *, final: bool, code: str) -> None:
    try:
        info = os.fstat(descriptor)
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(code) from exc
    mode = stat.S_IMODE(info.st_mode)
    sticky_parent = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or (final and mode != 0o700)
        or (not final and mode & 0o022 and not sticky_parent)
    ):
        _fail(code)


def _open_secure_root(root: Path) -> int:
    flags = _directory_flags()
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        parts = root.parts[1:]
        if not parts:
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_ROOT_UNSAFE")
        for index, part in enumerate(parts):
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            _check_directory(
                descriptor,
                final=index == len(parts) - 1,
                code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_ROOT_UNSAFE",
            )
        return descriptor
    except PhysicalWalV2WitnessRoundtripS3MailboxAdapterError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_ROOT_UNSAFE"
        ) from exc


def _open_child_directory(parent_fd: int, name: str) -> int:
    descriptor = -1
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
        _check_directory(
            descriptor,
            final=True,
            code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_DIRECTORY_UNSAFE",
        )
        return descriptor
    except PhysicalWalV2WitnessRoundtripS3MailboxAdapterError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_DIRECTORY_UNSAFE"
        ) from exc


def _read_fixed_file(parent_fd: int, name: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | (os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0),
            dir_fd=parent_fd,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 1 <= info.st_size <= _MAX_CREDENTIAL_BYTES
        ):
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_FILE_UNSAFE")
        chunks = bytearray()
        while len(chunks) < info.st_size:
            chunk = os.read(descriptor, info.st_size - len(chunks))
            if not chunk:
                _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_FILE_UNSAFE")
            chunks.extend(chunk)
        if os.read(descriptor, 1):
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_FILE_UNSAFE")
        return bytes(chunks)
    except PhysicalWalV2WitnessRoundtripS3MailboxAdapterError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_FILE_UNSAFE"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_fixed_credentials(config: _Config) -> _FixedRoleCredentials:
    _require_root()
    root_fd = -1
    directory_fd = -1
    try:
        root_fd = _open_secure_root(config.credential_root)
        directory_fd = _open_child_directory(root_fd, _CREDENTIAL_DIRECTORY)
        raw = _read_fixed_file(directory_fd, config.policy.local_role + ".json")
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)
        if root_fd >= 0:
            os.close(root_fd)
    item, _canonical_raw = _parse_canonical(
        raw,
        maximum_bytes=_MAX_CREDENTIAL_BYTES,
        code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_INVALID",
    )
    item = _exact_mapping(
        item,
        fields=_CREDENTIAL_FIELDS,
        code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_INVALID",
    )
    if (
        item["schema"] != _CREDENTIAL_SCHEMA
        or item["version"] != _CREDENTIAL_VERSION
        or item["local_role"] != config.policy.local_role
        or type(item["access_key_id"]) is not str
        or _ACCESS_RE.fullmatch(item["access_key_id"]) is None
        or type(item["secret_access_key"]) is not str
        or not item["secret_access_key"].isascii()
        or not 16 <= len(item["secret_access_key"]) <= 1024
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREDENTIAL_INVALID")
    return _FixedRoleCredentials(
        local_role=config.policy.local_role,
        access_key_id=item["access_key_id"],
        secret_access_key=item["secret_access_key"],
        capability=_CREDENTIAL_CAPABILITY,
    )


def _config(
    value: object,
    *,
    local_role: str,
    direction: str,
    now: datetime,
) -> _Config:
    if type(value) is not PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CONFIG_INVALID")
    if (
        value.enabled is not True
        or type(value.maximum_delivery_bytes) is not int
        or not 1 <= value.maximum_delivery_bytes <= _MAX_DELIVERY_BYTES
        or type(value.maximum_list_entries) is not int
        or not 1 <= value.maximum_list_entries <= _MAX_LIST_ENTRIES
        or type(value.admission_config) is not _admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig
        or type(value.delivery_config) is not _delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CONFIG_INVALID")
    policy = _policy_for_role(local_role)
    if policy.direction != direction:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_ROLE_INVALID")
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CLOCK_INVALID")
    try:
        admitted = _admission.require_verified_physical_wal_v2_witness_roundtrip_mailbox_admission(
            value.mailbox_admission,
            config=value.admission_config,
            now=observed,
        )
    except _admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_ADMISSION_INVALID"
        ) from exc
    if (
        admitted.local_role != policy.local_role
        or admitted.mailbox != policy.mailbox
        or admitted.direction != policy.direction
        or admitted.object_prefix != policy.object_prefix
        or admitted.least_privilege_actions != policy.actions
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_ADMISSION_CROSS_PIN_MISMATCH")
    try:
        delivery_facts = _delivery._config(value.delivery_config, mailbox=policy.mailbox)
    except (AttributeError, TypeError, ValueError, _delivery.PhysicalWalV2WitnessRoundtripDeliveryError) as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DELIVERY_POLICY_INVALID"
        ) from exc
    if delivery_facts.binding_sha256 != admitted.delivery_binding_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DELIVERY_BINDING_MISMATCH")
    retention_context = PhysicalWalV2WitnessRoundtripS3RetentionProofConfig(
        admission_config=value.admission_config,
        mailbox_admission=admitted,
        enabled=True,
    )
    try:
        retention = require_verified_physical_wal_v2_witness_roundtrip_s3_retention_proof(
            value.retention_proof,
            config=retention_context,
            now=observed,
        )
    except PhysicalWalV2WitnessRoundtripS3MailboxAdapterError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_RETENTION_PROOF_INVALID"
        ) from exc
    return _Config(
        admission_config=value.admission_config,
        mailbox_admission=admitted,
        delivery_config=value.delivery_config,
        retention_proof=retention,
        credential_root=_safe_credential_root(value.credential_root),
        policy=policy,
        delivery_binding_sha256=delivery_facts.binding_sha256,
        maximum_delivery_bytes=value.maximum_delivery_bytes,
        maximum_list_entries=value.maximum_list_entries,
    )


def _fresh_gate(config: _Config, *, now: datetime) -> None:
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CLOCK_INVALID")
    try:
        admitted = _admission.require_verified_physical_wal_v2_witness_roundtrip_mailbox_admission(
            config.mailbox_admission,
            config=config.admission_config,
            now=observed,
        )
    except _admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_ADMISSION_INVALID"
        ) from exc
    if (
        admitted.local_role != config.policy.local_role
        or admitted.mailbox != config.policy.mailbox
        or admitted.direction != config.policy.direction
        or admitted.object_prefix != config.policy.object_prefix
        or admitted.least_privilege_actions != config.policy.actions
        or admitted.delivery_binding_sha256 != config.delivery_binding_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DELIVERY_BINDING_MISMATCH")
    try:
        delivery_facts = _delivery._config(
            config.delivery_config,
            mailbox=config.policy.mailbox,
        )
    except (AttributeError, TypeError, ValueError, _delivery.PhysicalWalV2WitnessRoundtripDeliveryError) as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DELIVERY_POLICY_INVALID"
        ) from exc
    if delivery_facts.binding_sha256 != config.delivery_binding_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DELIVERY_BINDING_MISMATCH")
    proof_context = PhysicalWalV2WitnessRoundtripS3RetentionProofConfig(
        admission_config=config.admission_config,
        mailbox_admission=admitted,
        enabled=True,
    )
    try:
        proof = require_verified_physical_wal_v2_witness_roundtrip_s3_retention_proof(
            config.retention_proof,
            config=proof_context,
            now=observed,
        )
    except PhysicalWalV2WitnessRoundtripS3MailboxAdapterError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_RETENTION_PROOF_INVALID"
        ) from exc
    if (
        proof.local_role != config.policy.local_role
        or proof.mailbox != config.policy.mailbox
        or proof.direction != config.policy.direction
        or proof.object_prefix != config.policy.object_prefix
        or proof.delivery_binding_sha256 != config.delivery_binding_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_RETENTION_PROOF_INVALID")


def _retention_floor(config: _Config, *, now: datetime) -> datetime:
    """Return the currently required compliance-retention floor.

    A caller supplied delivery expiry is necessary but not sufficient: the
    signed Object-Lock evidence also states the minimum period every raw
    object observation must still retain.  Recompute this at each gate so a
    delayed callback cannot convert an old proof into a weaker receipt.
    """

    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CLOCK_INVALID")
    return observed + timedelta(seconds=config.retention_proof.minimum_retention_seconds)


def _verify_delivery(
    value: object, *, config: _Config, now: datetime
) -> _delivery.VerifiedPhysicalWalV2WitnessRoundtripDelivery:
    if type(value) is not bytes or not 1 <= len(value) <= config.maximum_delivery_bytes:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DELIVERY_INVALID")
    if config.policy.mailbox == _FI_TO_WITNESS:
        verifier = _delivery.verify_physical_wal_v2_witness_fi_to_witness_delivery
    elif config.policy.mailbox == _WITNESS_TO_IR:
        verifier = _delivery.verify_physical_wal_v2_witness_witness_to_ir_delivery
    elif config.policy.mailbox == _IR_TO_WITNESS:
        verifier = _delivery.verify_physical_wal_v2_witness_ir_to_witness_delivery
    else:
        verifier = _delivery.verify_physical_wal_v2_witness_witness_to_fi_delivery
    try:
        verified = verifier(value, config=config.delivery_config, now=now)
    except _delivery.PhysicalWalV2WitnessRoundtripDeliveryError as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DELIVERY_INVALID"
        ) from exc
    if verified.canonical_delivery != value or verified.mailbox != config.policy.mailbox:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_DELIVERY_INVALID")
    return verified


def _validate_metadata(
    *,
    object_key: object,
    object_version_id: object,
    content_sha256: object,
    content_bytes: object,
    retained_until: object,
    object_lock_compliance: object,
    retention_proof_sha256: object,
    config: _Config,
    expected_key: str | None,
    expected_digest: str | None,
    minimum_retention: datetime,
    code: str,
) -> tuple[str, str, str, int, datetime]:
    if expected_key is None:
        key_digest = _key_sha256(object_key, policy=config.policy, code=code)
    else:
        if object_key != expected_key:
            _fail(code)
        key_digest = _key_sha256(object_key, policy=config.policy, code=code)
    digest = _sha256(content_sha256, code=code)
    if (
        digest != key_digest
        or (expected_digest is not None and digest != expected_digest)
        or type(content_bytes) is not int
        or not 1 <= content_bytes <= config.maximum_delivery_bytes
        or object_lock_compliance is not True
        or _sha256(retention_proof_sha256, code=code) != config.retention_proof.proof_sha256
    ):
        _fail(code)
    retained = _utc(retained_until, code=code)
    if retained < minimum_retention:
        _fail(code)
    return object_key, _version(object_version_id, code=code), digest, content_bytes, retained


def _validate_version(
    value: object,
    *,
    config: _Config,
    expected_key: str,
    expected_digest: str,
    expected_bytes: int,
    minimum_retention: datetime,
) -> tuple[str, datetime]:
    if type(value) is not PhysicalWalV2WitnessRoundtripS3ObjectVersion:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREATE_RECEIPT_INVALID")
    item = value
    key, version, _digest, size, retained = _validate_metadata(
        object_key=item.object_key,
        object_version_id=item.object_version_id,
        content_sha256=item.content_sha256,
        content_bytes=item.content_bytes,
        retained_until=item.retained_until,
        object_lock_compliance=item.object_lock_compliance,
        retention_proof_sha256=item.retention_proof_sha256,
        config=config,
        expected_key=expected_key,
        expected_digest=expected_digest,
        minimum_retention=minimum_retention,
        code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREATE_RECEIPT_INVALID",
    )
    if key != expected_key or size != expected_bytes or item.conditional_create_only is not True:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREATE_RECEIPT_INVALID")
    return version, retained


def _validate_head(
    value: object,
    *,
    config: _Config,
    expected_key: str,
    expected_version: str,
    expected_digest: str,
    expected_bytes: int,
    minimum_retention: datetime,
) -> datetime:
    if type(value) is not PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_HEAD_INVALID")
    item = value
    key, version, _digest, size, retained = _validate_metadata(
        object_key=item.object_key,
        object_version_id=item.object_version_id,
        content_sha256=item.content_sha256,
        content_bytes=item.content_bytes,
        retained_until=item.retained_until,
        object_lock_compliance=item.object_lock_compliance,
        retention_proof_sha256=item.retention_proof_sha256,
        config=config,
        expected_key=expected_key,
        expected_digest=expected_digest,
        minimum_retention=minimum_retention,
        code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_HEAD_INVALID",
    )
    if key != expected_key or version != expected_version or size != expected_bytes:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_HEAD_INVALID")
    return retained


def _validate_read(
    value: object,
    *,
    config: _Config,
    expected_key: str,
    expected_version: str,
    expected_digest: str,
    expected_bytes: int,
    minimum_retention: datetime,
    expected_delivery: bytes | None,
) -> tuple[bytes, datetime]:
    if type(value) is not PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_READBACK_INVALID")
    item = value
    key, version, digest, size, retained = _validate_metadata(
        object_key=item.object_key,
        object_version_id=item.object_version_id,
        content_sha256=item.content_sha256,
        content_bytes=item.content_bytes,
        retained_until=item.retained_until,
        object_lock_compliance=item.object_lock_compliance,
        retention_proof_sha256=item.retention_proof_sha256,
        config=config,
        expected_key=expected_key,
        expected_digest=expected_digest,
        minimum_retention=minimum_retention,
        code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_READBACK_INVALID",
    )
    if (
        key != expected_key
        or version != expected_version
        or size != expected_bytes
        or type(item.canonical_delivery) is not bytes
        or len(item.canonical_delivery) != expected_bytes
        or _sha256_bytes(item.canonical_delivery) != digest
        or (expected_delivery is not None and item.canonical_delivery != expected_delivery)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_READBACK_INVALID")
    return item.canonical_delivery, retained


_T = TypeVar("_T")


class PhysicalWalV2WitnessRoundtripFiToWitnessPublisherRawS3(Protocol):
    def put_fi_to_witness_create_only(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ObjectVersion: ...

    def head_fi_to_witness_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead: ...

    def get_fi_to_witness_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead: ...


class PhysicalWalV2WitnessRoundtripWitnessIrEgressRawS3(Protocol):
    def put_witness_to_ir_create_only(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ObjectVersion: ...

    def head_witness_to_ir_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead: ...

    def get_witness_to_ir_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead: ...


class PhysicalWalV2WitnessRoundtripIrToWitnessPublisherRawS3(Protocol):
    def put_ir_to_witness_create_only(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ObjectVersion: ...

    def head_ir_to_witness_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead: ...

    def get_ir_to_witness_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead: ...


class PhysicalWalV2WitnessRoundtripWitnessFiPublisherRawS3(Protocol):
    def put_witness_to_fi_create_only(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ObjectVersion: ...

    def head_witness_to_fi_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead: ...

    def get_witness_to_fi_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead: ...


class PhysicalWalV2WitnessRoundtripWitnessFiIngressRawS3(Protocol):
    def list_fi_to_witness_immutable_locators(self) -> tuple[PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]: ...

    def head_fi_to_witness_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead: ...

    def get_fi_to_witness_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead: ...


class PhysicalWalV2WitnessRoundtripIrStandbyIngressRawS3(Protocol):
    def list_witness_to_ir_immutable_locators(self) -> tuple[PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]: ...

    def head_witness_to_ir_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead: ...

    def get_witness_to_ir_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead: ...


class PhysicalWalV2WitnessRoundtripWitnessIrIngressRawS3(Protocol):
    def list_ir_to_witness_immutable_locators(self) -> tuple[PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]: ...

    def head_ir_to_witness_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead: ...

    def get_ir_to_witness_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead: ...


class PhysicalWalV2WitnessRoundtripFiAckIngressRawS3(Protocol):
    def list_witness_to_fi_immutable_locators(self) -> tuple[PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]: ...

    def head_witness_to_fi_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead: ...

    def get_witness_to_fi_exact(self, **kwargs: object) -> PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead: ...


class PhysicalWalV2WitnessRoundtripFiToWitnessPublisherS3Scope(Protocol):
    def with_fi_to_witness_publisher_s3(self, *, credentials: _FixedRoleCredentials, operation: Callable[[PhysicalWalV2WitnessRoundtripFiToWitnessPublisherRawS3], _T]) -> _T: ...


class PhysicalWalV2WitnessRoundtripWitnessIrEgressS3Scope(Protocol):
    def with_witness_to_ir_egress_s3(self, *, credentials: _FixedRoleCredentials, operation: Callable[[PhysicalWalV2WitnessRoundtripWitnessIrEgressRawS3], _T]) -> _T: ...


class PhysicalWalV2WitnessRoundtripIrToWitnessPublisherS3Scope(Protocol):
    def with_ir_to_witness_publisher_s3(self, *, credentials: _FixedRoleCredentials, operation: Callable[[PhysicalWalV2WitnessRoundtripIrToWitnessPublisherRawS3], _T]) -> _T: ...


class PhysicalWalV2WitnessRoundtripWitnessFiPublisherS3Scope(Protocol):
    def with_witness_to_fi_publisher_s3(self, *, credentials: _FixedRoleCredentials, operation: Callable[[PhysicalWalV2WitnessRoundtripWitnessFiPublisherRawS3], _T]) -> _T: ...


class PhysicalWalV2WitnessRoundtripWitnessFiIngressS3Scope(Protocol):
    def with_fi_to_witness_ingress_s3(self, *, credentials: _FixedRoleCredentials, operation: Callable[[PhysicalWalV2WitnessRoundtripWitnessFiIngressRawS3], _T]) -> _T: ...


class PhysicalWalV2WitnessRoundtripIrStandbyIngressS3Scope(Protocol):
    def with_witness_to_ir_ingress_s3(self, *, credentials: _FixedRoleCredentials, operation: Callable[[PhysicalWalV2WitnessRoundtripIrStandbyIngressRawS3], _T]) -> _T: ...


class PhysicalWalV2WitnessRoundtripWitnessIrIngressS3Scope(Protocol):
    def with_ir_to_witness_ingress_s3(self, *, credentials: _FixedRoleCredentials, operation: Callable[[PhysicalWalV2WitnessRoundtripWitnessIrIngressRawS3], _T]) -> _T: ...


class PhysicalWalV2WitnessRoundtripFiAckIngressS3Scope(Protocol):
    def with_witness_to_fi_ingress_s3(self, *, credentials: _FixedRoleCredentials, operation: Callable[[PhysicalWalV2WitnessRoundtripFiAckIngressRawS3], _T]) -> _T: ...


class _PublisherAdapter:
    __slots__ = ("_config", "_scope", "_capability")

    def __init__(self, config: _Config, scope: object, capability: object) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CONSTRUCTION_FORBIDDEN")
        self._config = config
        self._scope = scope
        self._capability = capability

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_SERIALIZATION_FORBIDDEN")


class _ScannerAdapter:
    __slots__ = ("_config", "_scope", "_cache", "_capability")

    def __init__(self, config: _Config, scope: object, capability: object) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CONSTRUCTION_FORBIDDEN")
        self._config = config
        self._scope = scope
        self._cache: dict[tuple[str, str], _runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator] = {}
        self._capability = capability

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_SERIALIZATION_FORBIDDEN")


def _publisher_create(
    adapter: _PublisherAdapter,
    *,
    canonical_delivery: bytes,
    object_key: str,
    content_sha256: str,
    content_bytes: int,
    retained_until: datetime,
    invoke: Callable[[_FixedRoleCredentials, Callable[[Any], _runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt]], _runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt],
    work: Callable[
        [Any, str, bytes, str, int, datetime, str, int],
        tuple[
            PhysicalWalV2WitnessRoundtripS3ObjectVersion,
            PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
            PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
        ],
    ],
) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt:
    config = adapter._config
    now = _host_now()
    _fresh_gate(config, now=now)
    verified = _verify_delivery(canonical_delivery, config=config, now=now)
    expected_key = _object_key(config.policy.mailbox, verified.delivery_sha256)
    if (
        object_key != expected_key
        or _sha256(content_sha256, code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_PUBLISH_INPUT_INVALID")
        != verified.delivery_sha256
        or type(content_bytes) is not int
        or content_bytes != len(canonical_delivery)
        or _utc(retained_until, code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_PUBLISH_INPUT_INVALID")
        < verified.expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_PUBLISH_INPUT_INVALID")
    requested_retention = max(
        verified.expires_at,
        _retention_floor(config, now=now),
    )
    credentials = _load_fixed_credentials(config)

    def operation(raw_s3: Any) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt:
        try:
            version, head, readback = work(
                raw_s3,
                expected_key,
                canonical_delivery,
                verified.delivery_sha256,
                len(canonical_delivery),
                requested_retention,
                config.retention_proof.proof_sha256,
                config.maximum_delivery_bytes,
            )
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
                "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CREATE_FAILED"
            ) from exc
        version_id, version_retention = _validate_version(
            version,
            config=config,
            expected_key=expected_key,
            expected_digest=verified.delivery_sha256,
            expected_bytes=len(canonical_delivery),
            minimum_retention=requested_retention,
        )
        head_retention = _validate_head(
            head,
            config=config,
            expected_key=expected_key,
            expected_version=version_id,
            expected_digest=verified.delivery_sha256,
            expected_bytes=len(canonical_delivery),
            minimum_retention=requested_retention,
        )
        _raw, read_retention = _validate_read(
            readback,
            config=config,
            expected_key=expected_key,
            expected_version=version_id,
            expected_digest=verified.delivery_sha256,
            expected_bytes=len(canonical_delivery),
            minimum_retention=requested_retention,
            expected_delivery=canonical_delivery,
        )
        if version_retention != head_retention or head_retention != read_retention:
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_RETENTION_READBACK_MISMATCH")
        completed_now = _host_now()
        _fresh_gate(config, now=completed_now)
        final = _verify_delivery(canonical_delivery, config=config, now=completed_now)
        final_retention_floor = max(
            final.expires_at,
            _retention_floor(config, now=completed_now),
        )
        if (
            final.delivery_sha256 != verified.delivery_sha256
            or read_retention < final_retention_floor
        ):
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_POST_CALLBACK_CHANGED")
        return _runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt(
            object_key=expected_key,
            object_version_id=version_id,
            content_sha256=verified.delivery_sha256,
            content_bytes=len(canonical_delivery),
            retained_until=read_retention,
            create_only=True,
            immutable=True,
        )

    try:
        return invoke(credentials, operation)
    except PhysicalWalV2WitnessRoundtripS3MailboxAdapterError:
        raise
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_SCOPE_FAILED"
        ) from exc


def _validate_raw_locator(
    value: object,
    *,
    config: _Config,
    now: datetime,
) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator:
    if type(value) is not PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_LOCATOR_INVALID")
    item = value
    key, version, digest, size, retained = _validate_metadata(
        object_key=item.object_key,
        object_version_id=item.object_version_id,
        content_sha256=item.content_sha256,
        content_bytes=item.content_bytes,
        retained_until=item.retained_until,
        object_lock_compliance=item.object_lock_compliance,
        retention_proof_sha256=item.retention_proof_sha256,
        config=config,
        expected_key=None,
        expected_digest=None,
        minimum_retention=_retention_floor(config, now=now),
        code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_LOCATOR_INVALID",
    )
    return _runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator(
        object_key=key,
        object_version_id=version,
        content_sha256=digest,
        content_bytes=size,
        retained_until=retained,
        immutable=True,
    )


def _scanner_list(
    adapter: _ScannerAdapter,
    *,
    invoke: Callable[[_FixedRoleCredentials, Callable[[Any], tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]]], tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]],
    list_work: Callable[[Any], object],
) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]:
    config = adapter._config
    now = _host_now()
    _fresh_gate(config, now=now)
    credentials = _load_fixed_credentials(config)

    def operation(raw_s3: Any) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]:
        try:
            raw_locators = list_work(raw_s3)
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
                "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_LIST_FAILED"
            ) from exc
        if type(raw_locators) is not tuple or len(raw_locators) > config.maximum_list_entries:
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_LIST_INVALID")
        completed_now = _host_now()
        _fresh_gate(config, now=completed_now)
        result: list[_runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator] = []
        seen: dict[str, _runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator] = {}
        for raw_locator in raw_locators:
            locator = _validate_raw_locator(raw_locator, config=config, now=completed_now)
            previous = seen.get(locator.object_key)
            if previous is not None:
                if previous != locator:
                    _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_LOCATOR_FORK")
                continue
            seen[locator.object_key] = locator
            result.append(locator)
        adapter._cache = {(item.object_key, item.object_version_id): item for item in result}
        return tuple(sorted(result, key=lambda item: (item.object_key, item.object_version_id)))

    try:
        return invoke(credentials, operation)
    except PhysicalWalV2WitnessRoundtripS3MailboxAdapterError:
        raise
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_SCOPE_FAILED"
        ) from exc


def _scanner_read(
    adapter: _ScannerAdapter,
    *,
    object_key: str,
    object_version_id: str,
    invoke: Callable[[_FixedRoleCredentials, Callable[[Any], _runtime.PhysicalWalV2WitnessRoundtripDeliveryContent]], _runtime.PhysicalWalV2WitnessRoundtripDeliveryContent],
    read_work: Callable[
        [Any, str, str, int],
        tuple[
            PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
            PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
        ],
    ],
) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryContent:
    config = adapter._config
    now = _host_now()
    _fresh_gate(config, now=now)
    expected = adapter._cache.get((object_key, object_version_id))
    if expected is None:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_UNLISTED_EXACT_READ")
    if _key_sha256(object_key, policy=config.policy, code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_OBJECT_KEY_INVALID") != expected.content_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_OBJECT_KEY_INVALID")
    credentials = _load_fixed_credentials(config)
    required_retention = max(
        expected.retained_until,
        _retention_floor(config, now=now),
    )

    def operation(raw_s3: Any) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryContent:
        try:
            head, read = read_work(
                raw_s3,
                object_key,
                object_version_id,
                config.maximum_delivery_bytes,
            )
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
                "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_EXACT_READ_FAILED"
            ) from exc
        head_retention = _validate_head(
            head,
            config=config,
            expected_key=object_key,
            expected_version=object_version_id,
            expected_digest=expected.content_sha256,
            expected_bytes=expected.content_bytes,
            minimum_retention=required_retention,
        )
        raw, read_retention = _validate_read(
            read,
            config=config,
            expected_key=object_key,
            expected_version=object_version_id,
            expected_digest=expected.content_sha256,
            expected_bytes=expected.content_bytes,
            minimum_retention=required_retention,
            expected_delivery=None,
        )
        if head_retention != read_retention or read_retention != expected.retained_until:
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_RETENTION_READBACK_MISMATCH")
        completed_now = _host_now()
        _fresh_gate(config, now=completed_now)
        verified = _verify_delivery(raw, config=config, now=completed_now)
        final_retention_floor = max(
            expected.retained_until,
            _retention_floor(config, now=completed_now),
        )
        if (
            verified.delivery_sha256 != expected.content_sha256
            or read_retention < final_retention_floor
        ):
            _fail("V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CONTENT_SUBSTITUTION")
        return _runtime.PhysicalWalV2WitnessRoundtripDeliveryContent(
            object_key=object_key,
            object_version_id=object_version_id,
            content_sha256=expected.content_sha256,
            content_bytes=expected.content_bytes,
            retained_until=expected.retained_until,
            immutable=True,
            canonical_delivery=raw,
        )

    try:
        return invoke(credentials, operation)
    except PhysicalWalV2WitnessRoundtripS3MailboxAdapterError:
        raise
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripS3MailboxAdapterError(
            "V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_SCOPE_FAILED"
        ) from exc


# These fixed work functions are deliberately separate rather than selected by
# a string or a route argument.  A role-local adapter therefore cannot turn a
# typed raw-S3 handle into an arbitrary object operation.
def _work_put_fi_to_witness(
    raw_s3: PhysicalWalV2WitnessRoundtripFiToWitnessPublisherRawS3,
    object_key: str,
    canonical_delivery: bytes,
    content_sha256: str,
    content_bytes: int,
    retained_until: datetime,
    retention_proof_sha256: str,
    maximum_bytes: int,
) -> tuple[
    PhysicalWalV2WitnessRoundtripS3ObjectVersion,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
]:
    version = raw_s3.put_fi_to_witness_create_only(
        object_key=object_key,
        canonical_delivery=canonical_delivery,
        content_sha256=content_sha256,
        content_bytes=content_bytes,
        retained_until=retained_until,
        retention_proof_sha256=retention_proof_sha256,
    )
    head = raw_s3.head_fi_to_witness_exact(
        object_key=object_key,
        object_version_id=version.object_version_id,
    )
    readback = raw_s3.get_fi_to_witness_exact(
        object_key=object_key,
        object_version_id=version.object_version_id,
        maximum_bytes=maximum_bytes,
    )
    return version, head, readback


def _work_put_witness_to_ir(
    raw_s3: PhysicalWalV2WitnessRoundtripWitnessIrEgressRawS3,
    object_key: str,
    canonical_delivery: bytes,
    content_sha256: str,
    content_bytes: int,
    retained_until: datetime,
    retention_proof_sha256: str,
    maximum_bytes: int,
) -> tuple[
    PhysicalWalV2WitnessRoundtripS3ObjectVersion,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
]:
    version = raw_s3.put_witness_to_ir_create_only(
        object_key=object_key,
        canonical_delivery=canonical_delivery,
        content_sha256=content_sha256,
        content_bytes=content_bytes,
        retained_until=retained_until,
        retention_proof_sha256=retention_proof_sha256,
    )
    head = raw_s3.head_witness_to_ir_exact(
        object_key=object_key,
        object_version_id=version.object_version_id,
    )
    readback = raw_s3.get_witness_to_ir_exact(
        object_key=object_key,
        object_version_id=version.object_version_id,
        maximum_bytes=maximum_bytes,
    )
    return version, head, readback


def _work_put_ir_to_witness(
    raw_s3: PhysicalWalV2WitnessRoundtripIrToWitnessPublisherRawS3,
    object_key: str,
    canonical_delivery: bytes,
    content_sha256: str,
    content_bytes: int,
    retained_until: datetime,
    retention_proof_sha256: str,
    maximum_bytes: int,
) -> tuple[
    PhysicalWalV2WitnessRoundtripS3ObjectVersion,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
]:
    version = raw_s3.put_ir_to_witness_create_only(
        object_key=object_key,
        canonical_delivery=canonical_delivery,
        content_sha256=content_sha256,
        content_bytes=content_bytes,
        retained_until=retained_until,
        retention_proof_sha256=retention_proof_sha256,
    )
    head = raw_s3.head_ir_to_witness_exact(
        object_key=object_key,
        object_version_id=version.object_version_id,
    )
    readback = raw_s3.get_ir_to_witness_exact(
        object_key=object_key,
        object_version_id=version.object_version_id,
        maximum_bytes=maximum_bytes,
    )
    return version, head, readback


def _work_put_witness_to_fi(
    raw_s3: PhysicalWalV2WitnessRoundtripWitnessFiPublisherRawS3,
    object_key: str,
    canonical_delivery: bytes,
    content_sha256: str,
    content_bytes: int,
    retained_until: datetime,
    retention_proof_sha256: str,
    maximum_bytes: int,
) -> tuple[
    PhysicalWalV2WitnessRoundtripS3ObjectVersion,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
]:
    version = raw_s3.put_witness_to_fi_create_only(
        object_key=object_key,
        canonical_delivery=canonical_delivery,
        content_sha256=content_sha256,
        content_bytes=content_bytes,
        retained_until=retained_until,
        retention_proof_sha256=retention_proof_sha256,
    )
    head = raw_s3.head_witness_to_fi_exact(
        object_key=object_key,
        object_version_id=version.object_version_id,
    )
    readback = raw_s3.get_witness_to_fi_exact(
        object_key=object_key,
        object_version_id=version.object_version_id,
        maximum_bytes=maximum_bytes,
    )
    return version, head, readback


def _work_list_fi_to_witness(
    raw_s3: PhysicalWalV2WitnessRoundtripWitnessFiIngressRawS3,
) -> tuple[PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]:
    return raw_s3.list_fi_to_witness_immutable_locators()


def _work_list_witness_to_ir(
    raw_s3: PhysicalWalV2WitnessRoundtripIrStandbyIngressRawS3,
) -> tuple[PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]:
    return raw_s3.list_witness_to_ir_immutable_locators()


def _work_list_ir_to_witness(
    raw_s3: PhysicalWalV2WitnessRoundtripWitnessIrIngressRawS3,
) -> tuple[PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]:
    return raw_s3.list_ir_to_witness_immutable_locators()


def _work_list_witness_to_fi(
    raw_s3: PhysicalWalV2WitnessRoundtripFiAckIngressRawS3,
) -> tuple[PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]:
    return raw_s3.list_witness_to_fi_immutable_locators()


def _work_read_fi_to_witness(
    raw_s3: PhysicalWalV2WitnessRoundtripWitnessFiIngressRawS3,
    object_key: str,
    object_version_id: str,
    maximum_bytes: int,
) -> tuple[
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
]:
    return (
        raw_s3.head_fi_to_witness_exact(
            object_key=object_key,
            object_version_id=object_version_id,
        ),
        raw_s3.get_fi_to_witness_exact(
            object_key=object_key,
            object_version_id=object_version_id,
            maximum_bytes=maximum_bytes,
        ),
    )


def _work_read_witness_to_ir(
    raw_s3: PhysicalWalV2WitnessRoundtripIrStandbyIngressRawS3,
    object_key: str,
    object_version_id: str,
    maximum_bytes: int,
) -> tuple[
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
]:
    return (
        raw_s3.head_witness_to_ir_exact(
            object_key=object_key,
            object_version_id=object_version_id,
        ),
        raw_s3.get_witness_to_ir_exact(
            object_key=object_key,
            object_version_id=object_version_id,
            maximum_bytes=maximum_bytes,
        ),
    )


def _work_read_ir_to_witness(
    raw_s3: PhysicalWalV2WitnessRoundtripWitnessIrIngressRawS3,
    object_key: str,
    object_version_id: str,
    maximum_bytes: int,
) -> tuple[
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
]:
    return (
        raw_s3.head_ir_to_witness_exact(
            object_key=object_key,
            object_version_id=object_version_id,
        ),
        raw_s3.get_ir_to_witness_exact(
            object_key=object_key,
            object_version_id=object_version_id,
            maximum_bytes=maximum_bytes,
        ),
    )


def _work_read_witness_to_fi(
    raw_s3: PhysicalWalV2WitnessRoundtripFiAckIngressRawS3,
    object_key: str,
    object_version_id: str,
    maximum_bytes: int,
) -> tuple[
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead,
    PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead,
]:
    return (
        raw_s3.head_witness_to_fi_exact(
            object_key=object_key,
            object_version_id=object_version_id,
        ),
        raw_s3.get_witness_to_fi_exact(
            object_key=object_key,
            object_version_id=object_version_id,
            maximum_bytes=maximum_bytes,
        ),
    )


class PhysicalWalV2WitnessRoundtripFiToWitnessPublisherS3Adapter(_PublisherAdapter):
    """FI's one-way source-outbox publisher; it has no inbound or other-hop API."""

    def create_fi_to_witness_delivery(
        self,
        *,
        object_key: str,
        canonical_delivery: bytes,
        content_sha256: str,
        content_bytes: int,
        retained_until: datetime,
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt:
        return _publisher_create(
            self,
            canonical_delivery=canonical_delivery,
            object_key=object_key,
            content_sha256=content_sha256,
            content_bytes=content_bytes,
            retained_until=retained_until,
            invoke=lambda credentials, operation: self._scope.with_fi_to_witness_publisher_s3(
                credentials=credentials,
                operation=operation,
            ),
            work=_work_put_fi_to_witness,
        )


class PhysicalWalV2WitnessRoundtripWitnessIrEgressS3Adapter(_PublisherAdapter):
    """Witness's solely named publication operation for the standby mailbox."""

    def create_witness_to_ir_delivery(
        self,
        *,
        object_key: str,
        canonical_delivery: bytes,
        content_sha256: str,
        content_bytes: int,
        retained_until: datetime,
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt:
        return _publisher_create(
            self,
            canonical_delivery=canonical_delivery,
            object_key=object_key,
            content_sha256=content_sha256,
            content_bytes=content_bytes,
            retained_until=retained_until,
            invoke=lambda credentials, operation: self._scope.with_witness_to_ir_egress_s3(
                credentials=credentials,
                operation=operation,
            ),
            work=_work_put_witness_to_ir,
        )


class PhysicalWalV2WitnessRoundtripIrToWitnessPublisherS3Adapter(_PublisherAdapter):
    """IR's durable acknowledgement outbox, fixed to the Witness hop."""

    def create_ir_to_witness_delivery(
        self,
        *,
        object_key: str,
        canonical_delivery: bytes,
        content_sha256: str,
        content_bytes: int,
        retained_until: datetime,
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt:
        return _publisher_create(
            self,
            canonical_delivery=canonical_delivery,
            object_key=object_key,
            content_sha256=content_sha256,
            content_bytes=content_bytes,
            retained_until=retained_until,
            invoke=lambda credentials, operation: self._scope.with_ir_to_witness_publisher_s3(
                credentials=credentials,
                operation=operation,
            ),
            work=_work_put_ir_to_witness,
        )


class PhysicalWalV2WitnessRoundtripWitnessFiPublisherS3Adapter(_PublisherAdapter):
    """Witness's final acknowledgement publisher for the FI inbox."""

    def create_witness_to_fi_delivery(
        self,
        *,
        object_key: str,
        canonical_delivery: bytes,
        content_sha256: str,
        content_bytes: int,
        retained_until: datetime,
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt:
        return _publisher_create(
            self,
            canonical_delivery=canonical_delivery,
            object_key=object_key,
            content_sha256=content_sha256,
            content_bytes=content_bytes,
            retained_until=retained_until,
            invoke=lambda credentials, operation: self._scope.with_witness_to_fi_publisher_s3(
                credentials=credentials,
                operation=operation,
            ),
            work=_work_put_witness_to_fi,
        )


class PhysicalWalV2WitnessRoundtripWitnessFiIngressS3Adapter(_ScannerAdapter):
    """Witness's fixed-prefix scanner for FI source packets only."""

    def list_fi_to_witness_delivery_locators(
        self,
    ) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]:
        return _scanner_list(
            self,
            invoke=lambda credentials, operation: self._scope.with_fi_to_witness_ingress_s3(
                credentials=credentials,
                operation=operation,
            ),
            list_work=_work_list_fi_to_witness,
        )

    def read_fi_to_witness_delivery_exact(
        self,
        *,
        object_key: str,
        object_version_id: str,
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryContent:
        return _scanner_read(
            self,
            object_key=object_key,
            object_version_id=object_version_id,
            invoke=lambda credentials, operation: self._scope.with_fi_to_witness_ingress_s3(
                credentials=credentials,
                operation=operation,
            ),
            read_work=_work_read_fi_to_witness,
        )


class PhysicalWalV2WitnessRoundtripIrStandbyIngressS3Adapter(_ScannerAdapter):
    """IR standby's fixed-prefix scanner for Witness packets only."""

    def list_witness_to_ir_delivery_locators(
        self,
    ) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]:
        return _scanner_list(
            self,
            invoke=lambda credentials, operation: self._scope.with_witness_to_ir_ingress_s3(
                credentials=credentials,
                operation=operation,
            ),
            list_work=_work_list_witness_to_ir,
        )

    def read_witness_to_ir_delivery_exact(
        self,
        *,
        object_key: str,
        object_version_id: str,
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryContent:
        return _scanner_read(
            self,
            object_key=object_key,
            object_version_id=object_version_id,
            invoke=lambda credentials, operation: self._scope.with_witness_to_ir_ingress_s3(
                credentials=credentials,
                operation=operation,
            ),
            read_work=_work_read_witness_to_ir,
        )


class PhysicalWalV2WitnessRoundtripWitnessIrIngressS3Adapter(_ScannerAdapter):
    """Witness's fixed-prefix scanner for IR durable acknowledgements only."""

    def list_ir_to_witness_delivery_locators(
        self,
    ) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]:
        return _scanner_list(
            self,
            invoke=lambda credentials, operation: self._scope.with_ir_to_witness_ingress_s3(
                credentials=credentials,
                operation=operation,
            ),
            list_work=_work_list_ir_to_witness,
        )

    def read_ir_to_witness_delivery_exact(
        self,
        *,
        object_key: str,
        object_version_id: str,
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryContent:
        return _scanner_read(
            self,
            object_key=object_key,
            object_version_id=object_version_id,
            invoke=lambda credentials, operation: self._scope.with_ir_to_witness_ingress_s3(
                credentials=credentials,
                operation=operation,
            ),
            read_work=_work_read_ir_to_witness,
        )


class PhysicalWalV2WitnessRoundtripFiAckIngressS3Adapter(_ScannerAdapter):
    """FI's final-acknowledgement scanner, fixed to its own inbox prefix."""

    def list_witness_to_fi_delivery_locators(
        self,
    ) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]:
        return _scanner_list(
            self,
            invoke=lambda credentials, operation: self._scope.with_witness_to_fi_ingress_s3(
                credentials=credentials,
                operation=operation,
            ),
            list_work=_work_list_witness_to_fi,
        )

    def read_witness_to_fi_delivery_exact(
        self,
        *,
        object_key: str,
        object_version_id: str,
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryContent:
        return _scanner_read(
            self,
            object_key=object_key,
            object_version_id=object_version_id,
            invoke=lambda credentials, operation: self._scope.with_witness_to_fi_ingress_s3(
                credentials=credentials,
                operation=operation,
            ),
            read_work=_work_read_witness_to_fi,
        )


def _open_adapter_config(
    config: PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
    *,
    local_role: str,
    direction: str,
    now: datetime | None,
) -> _Config:
    observed = _host_now() if now is None else _utc(
        now,
        code="V2_WITNESS_ROUNDTRIP_S3_MAILBOX_ADAPTER_CLOCK_INVALID",
    )
    return _config(
        config,
        local_role=local_role,
        direction=direction,
        now=observed,
    )


def open_physical_wal_v2_witness_roundtrip_fi_to_witness_publisher_s3_adapter(
    *,
    config: PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
    scope: PhysicalWalV2WitnessRoundtripFiToWitnessPublisherS3Scope,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripFiToWitnessPublisherS3Adapter:
    return PhysicalWalV2WitnessRoundtripFiToWitnessPublisherS3Adapter(
        _open_adapter_config(
            config,
            local_role="fi-writer-source-outbox",
            direction=_PUBLISH,
            now=now,
        ),
        scope,
        _CAPABILITY,
    )


def open_physical_wal_v2_witness_roundtrip_witness_to_ir_publisher_s3_adapter(
    *,
    config: PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
    scope: PhysicalWalV2WitnessRoundtripWitnessIrEgressS3Scope,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripWitnessIrEgressS3Adapter:
    return PhysicalWalV2WitnessRoundtripWitnessIrEgressS3Adapter(
        _open_adapter_config(
            config,
            local_role="witness-ir-egress",
            direction=_PUBLISH,
            now=now,
        ),
        scope,
        _CAPABILITY,
    )


def open_physical_wal_v2_witness_roundtrip_ir_to_witness_publisher_s3_adapter(
    *,
    config: PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
    scope: PhysicalWalV2WitnessRoundtripIrToWitnessPublisherS3Scope,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripIrToWitnessPublisherS3Adapter:
    return PhysicalWalV2WitnessRoundtripIrToWitnessPublisherS3Adapter(
        _open_adapter_config(
            config,
            local_role="ir-durable-ack-outbox",
            direction=_PUBLISH,
            now=now,
        ),
        scope,
        _CAPABILITY,
    )


def open_physical_wal_v2_witness_roundtrip_witness_to_fi_publisher_s3_adapter(
    *,
    config: PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
    scope: PhysicalWalV2WitnessRoundtripWitnessFiPublisherS3Scope,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripWitnessFiPublisherS3Adapter:
    return PhysicalWalV2WitnessRoundtripWitnessFiPublisherS3Adapter(
        _open_adapter_config(
            config,
            local_role="witness-fi-egress",
            direction=_PUBLISH,
            now=now,
        ),
        scope,
        _CAPABILITY,
    )


def open_physical_wal_v2_witness_roundtrip_witness_fi_ingress_s3_adapter(
    *,
    config: PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
    scope: PhysicalWalV2WitnessRoundtripWitnessFiIngressS3Scope,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripWitnessFiIngressS3Adapter:
    return PhysicalWalV2WitnessRoundtripWitnessFiIngressS3Adapter(
        _open_adapter_config(
            config,
            local_role="witness-fi-ingress",
            direction=_CONSUME,
            now=now,
        ),
        scope,
        _CAPABILITY,
    )


def open_physical_wal_v2_witness_roundtrip_ir_standby_ingress_s3_adapter(
    *,
    config: PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
    scope: PhysicalWalV2WitnessRoundtripIrStandbyIngressS3Scope,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripIrStandbyIngressS3Adapter:
    return PhysicalWalV2WitnessRoundtripIrStandbyIngressS3Adapter(
        _open_adapter_config(
            config,
            local_role="ir-standby-ack-inbox",
            direction=_CONSUME,
            now=now,
        ),
        scope,
        _CAPABILITY,
    )


def open_physical_wal_v2_witness_roundtrip_witness_ir_ingress_s3_adapter(
    *,
    config: PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
    scope: PhysicalWalV2WitnessRoundtripWitnessIrIngressS3Scope,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripWitnessIrIngressS3Adapter:
    return PhysicalWalV2WitnessRoundtripWitnessIrIngressS3Adapter(
        _open_adapter_config(
            config,
            local_role="witness-ir-ingress",
            direction=_CONSUME,
            now=now,
        ),
        scope,
        _CAPABILITY,
    )


def open_physical_wal_v2_witness_roundtrip_fi_ack_ingress_s3_adapter(
    *,
    config: PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
    scope: PhysicalWalV2WitnessRoundtripFiAckIngressS3Scope,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripFiAckIngressS3Adapter:
    return PhysicalWalV2WitnessRoundtripFiAckIngressS3Adapter(
        _open_adapter_config(
            config,
            local_role="fi-writer-ack-inbox",
            direction=_CONSUME,
            now=now,
        ),
        scope,
        _CAPABILITY,
    )
