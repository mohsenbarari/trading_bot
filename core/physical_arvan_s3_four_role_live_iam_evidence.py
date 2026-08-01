"""Pure, Witness-routed live-IAM evidence for the four Arvan S3 roles.

This module deliberately models *evidence*, not an Object-Storage client.  A
role-local runner may use its one local machine identity to perform a small,
disposable probe, but it must return a signed observation to the Witness.  The
Witness is the only component that creates a receiver-facing forward envelope;
there is no FI-to-IR or IR-to-FI control/data channel in this grammar.

The contract is intentionally useful before the provider adapter exists:

* every provider outcome is represented by an exact, fixed allow/deny matrix;
* every probe object has a deterministic, campaign/release/nonce-scoped key
  and an exact version/hash/byte selector;
* all four local signing keys and redacted machine-identity hashes are pinned;
* a Witness nonce is opened and committed through an immutable, serializable
  state transition.  A persistence adapter can store that state later without
  changing the safety grammar.

No function in this file opens credentials, reads files, imports an S3 SDK,
performs network I/O, starts a subprocess, or selects a provider endpoint.
The opaque route and four-role binding hashes are pins supplied by the
separate local-policy binder; this module never treats an arbitrary provider
hash as proof of IAM permission.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core import physical_arvan_s3_role_profiles as _profiles
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
)


__all__ = (
    "MAX_PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_TTL",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_LEDGER_SCHEMA",
    "PhysicalArvanS3FourRoleLiveIamEvidenceBinding",
    "PhysicalArvanS3FourRoleLiveIamEvidenceError",
    "PhysicalArvanS3FourRoleLiveIamNonceLedger",
    "PhysicalArvanS3FourRoleLiveIamNonceRecord",
    "PhysicalArvanS3LiveIamProbeLocator",
    "VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit",
    "VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation",
    "VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation",
    "VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate",
    "VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward",
    "build_physical_arvan_s3_four_role_live_iam_evidence_binding",
    "derive_physical_arvan_s3_four_role_live_iam_probe_object_key",
    "expire_physical_arvan_s3_four_role_live_iam_nonce",
    "issue_physical_arvan_s3_four_role_live_iam_nonce_permit",
    "make_physical_arvan_s3_four_role_live_iam_nonce_ledger",
    "make_physical_arvan_s3_live_iam_probe_locator",
    "parse_physical_arvan_s3_four_role_live_iam_nonce_ledger",
    "require_verified_physical_arvan_s3_four_role_live_iam_witness_aggregate",
    "seal_physical_arvan_s3_four_role_live_iam_publisher_observation",
    "seal_physical_arvan_s3_four_role_live_iam_receiver_observation",
    "seal_physical_arvan_s3_four_role_live_iam_witness_aggregate",
    "seal_physical_arvan_s3_four_role_live_iam_witness_forward",
    "serialize_physical_arvan_s3_four_role_live_iam_nonce_ledger",
    "verify_physical_arvan_s3_four_role_live_iam_nonce_permit",
    "verify_physical_arvan_s3_four_role_live_iam_publisher_observation",
    "verify_physical_arvan_s3_four_role_live_iam_receiver_observation",
    "verify_physical_arvan_s3_four_role_live_iam_witness_aggregate",
    "verify_physical_arvan_s3_four_role_live_iam_witness_forward",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-live-iam-evidence-v1"
)
PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_LEDGER_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-live-iam-nonce-ledger-v1"
)
MAX_PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_TTL = timedelta(minutes=5)

_PERMIT_KIND = "witness-nonce-permit"
_PUBLISHER_KIND = "publisher-observation"
_FORWARD_KIND = "witness-forward"
_RECEIVER_KIND = "receiver-observation"
_AGGREGATE_KIND = "witness-aggregate"
_LEDGER_OPEN = "open"
_LEDGER_COMMITTED = "committed"
_LEDGER_EXPIRED = "expired"
_MAX_WIRE_BYTES = 128 * 1024
_MAX_PROBE_OBJECT_BYTES = 8 * 1024 * 1024
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_NONCE_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)

_DIRECTION_NORMAL = "normal-fi-publisher-to-ir-receiver"
_DIRECTION_REVERSE = "reverse-ir-publisher-to-fi-receiver"
_DIRECTION_BY_PUBLISHER = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: _DIRECTION_NORMAL,
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: _DIRECTION_REVERSE,
}
_DIRECTION_BY_RECEIVER = {
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: _DIRECTION_NORMAL,
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: _DIRECTION_REVERSE,
}
_PUBLISHER_BY_DIRECTION = {
    _DIRECTION_NORMAL: _profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
    _DIRECTION_REVERSE: _profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
}
_RECEIVER_BY_DIRECTION = {
    _DIRECTION_NORMAL: _profiles.ARVAN_S3_IR_RECEIVER_ROLE,
    _DIRECTION_REVERSE: _profiles.ARVAN_S3_FI_RECEIVER_ROLE,
}
_NAMESPACE_BY_DIRECTION = {
    _DIRECTION_NORMAL: PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    _DIRECTION_REVERSE: PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
}
_ROLE_ORDER = (
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE,
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE,
)
_ROLE_SITE = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: "webapp_fi",
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: "webapp_ir",
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: "webapp_ir",
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: "webapp_fi",
}
_ROLE_PROFILE = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: _profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: _profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: _profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: _profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
}
_ROLE_ALLOWED = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: _profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS,
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: _profiles.ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS,
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: _profiles.ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS,
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: _profiles.ARVAN_S3_FI_RECEIVER_EXPECTED_ACTIONS,
}
_COMMON_DENIED = (
    "DeleteObject",
    "DeleteObjectVersion",
    "PutBucketPolicy",
    "PutBucketVersioning",
    "PutObject:overwrite",
    "PutObjectAcl",
    "PutObjectRetention",
)
_ROLE_DENIED = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: _COMMON_DENIED,
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: (
        "GetBucketAcl",
        "GetBucketVersioning",
        "GetObjectLockConfiguration",
        "ListObjectVersions:exact-key",
        "PutObject:create-only",
        *_COMMON_DENIED,
    ),
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: (
        "GetObjectLockConfiguration",
        "GetObjectRetention:exact-version",
        *_COMMON_DENIED,
    ),
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: (
        "GetBucketAcl",
        "GetBucketVersioning",
        "GetObjectLockConfiguration",
        "ListObjectVersions:exact-key",
        "PutObject:create-only",
        *_COMMON_DENIED,
    ),
}

_PERMIT_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "nonce",
        "issued_at",
        "expires_at",
        "evidence_binding_sha256",
        "normal_route_scope_sha256",
        "reverse_route_scope_sha256",
        "four_role_binding_sha256",
        "witness_signer",
        "witness_signature",
    }
)
_PUBLISHER_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "nonce",
        "observed_at",
        "expires_at",
        "evidence_binding_sha256",
        "direction",
        "route_scope_sha256",
        "role",
        "identity_sha256",
        "action_profile",
        "allowed_operation_outcomes",
        "denied_operation_outcomes",
        "probe_locator",
        "role_signer",
        "role_signature",
    }
)
_FORWARD_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "nonce",
        "forwarded_at",
        "expires_at",
        "evidence_binding_sha256",
        "direction",
        "route_scope_sha256",
        "publisher_role",
        "receiver_role",
        "publisher_observation_sha256",
        "probe_locator",
        "witness_signer",
        "witness_signature",
    }
)
_RECEIVER_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "nonce",
        "observed_at",
        "expires_at",
        "evidence_binding_sha256",
        "direction",
        "route_scope_sha256",
        "role",
        "identity_sha256",
        "action_profile",
        "publisher_observation_sha256",
        "witness_forward_sha256",
        "allowed_operation_outcomes",
        "denied_operation_outcomes",
        "probe_locator",
        "role_signer",
        "role_signature",
    }
)
_AGGREGATE_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "nonce",
        "issued_at",
        "expires_at",
        "committed_at",
        "evidence_binding_sha256",
        "normal_route_scope_sha256",
        "reverse_route_scope_sha256",
        "four_role_binding_sha256",
        "prior_ledger_sha256",
        "nonce_commitment_sha256",
        "role_matrix",
        "normal_direction",
        "reverse_direction",
        "witness_signer",
        "witness_signature",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_LOCATOR_FIELDS = frozenset(
    {"object_key", "object_version_id", "content_sha256", "content_bytes"}
)
_OUTCOME_FIELDS = frozenset({"operation", "outcome"})
_ROLE_MATRIX_FIELDS = frozenset(
    {
        "identity_sha256",
        "action_profile",
        "allowed_operation_outcomes",
        "denied_operation_outcomes",
        "signer",
    }
)
_DIRECTION_EVIDENCE_FIELDS = frozenset(
    {
        "publisher_role",
        "receiver_role",
        "publisher_observation_sha256",
        "witness_forward_sha256",
        "receiver_observation_sha256",
        "probe_locator",
    }
)
_LEDGER_FIELDS = frozenset({"schema", "evidence_binding_sha256", "records"})
_LEDGER_RECORD_FIELDS = frozenset(
    {
        "nonce",
        "issued_at",
        "expires_at",
        "status",
        "prior_ledger_sha256",
        "commit_prior_ledger_sha256",
        "nonce_commitment_sha256",
        "aggregate_sha256",
        "committed_at",
        "retired_at",
    }
)
_VERIFIED_PERMIT_CAPABILITY = object()
_VERIFIED_PUBLISHER_CAPABILITY = object()
_VERIFIED_FORWARD_CAPABILITY = object()
_VERIFIED_RECEIVER_CAPABILITY = object()
_VERIFIED_AGGREGATE_CAPABILITY = object()


class PhysicalArvanS3FourRoleLiveIamEvidenceError(ValueError):
    """A fixed-code rejection in the pure four-role IAM evidence grammar."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleLiveIamEvidenceBinding:
    """Pinned public inputs for one campaign's live IAM evidence run.

    The action profiles and allow/deny matrices are derived by the constructor
    function from canonical role vocabulary.  They are not provider claims and
    callers cannot substitute a provider-returned opaque permission hash.
    """

    schema: str
    campaign_id: str
    release_sha: str
    normal_route_scope_sha256: str
    reverse_route_scope_sha256: str
    four_role_binding_sha256: str
    evidence_binding_sha256: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    ir_publisher_identity_sha256: str
    fi_receiver_identity_sha256: str
    fi_publisher_signer_public_key: bytes
    ir_receiver_signer_public_key: bytes
    ir_publisher_signer_public_key: bytes
    fi_receiver_signer_public_key: bytes


@dataclass(frozen=True)
class PhysicalArvanS3LiveIamProbeLocator:
    """Exact selector for one disposable live-IAM probe Object version."""

    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleLiveIamNonceRecord:
    """One durable logical nonce slot; persistence remains outside this module."""

    nonce: str
    issued_at: str
    expires_at: str
    status: str
    prior_ledger_sha256: str
    commit_prior_ledger_sha256: str | None = None
    nonce_commitment_sha256: str | None = None
    aggregate_sha256: str | None = None
    committed_at: str | None = None
    retired_at: str | None = None


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleLiveIamNonceLedger:
    """Serializable immutable Witness state with no filesystem behavior."""

    schema: str
    evidence_binding_sha256: str
    records: tuple[PhysicalArvanS3FourRoleLiveIamNonceRecord, ...]


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit:
    nonce: str
    issued_at: datetime
    expires_at: datetime
    evidence_binding_sha256: str
    witness_key_id: str
    raw_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation:
    nonce: str
    direction: str
    role: str
    route_scope_sha256: str
    observed_at: datetime
    expires_at: datetime
    locator: PhysicalArvanS3LiveIamProbeLocator
    raw_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward:
    nonce: str
    direction: str
    publisher_role: str
    receiver_role: str
    route_scope_sha256: str
    forwarded_at: datetime
    expires_at: datetime
    publisher_observation_sha256: str
    locator: PhysicalArvanS3LiveIamProbeLocator
    raw_sha256: str
    witness_key_id: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation:
    nonce: str
    direction: str
    role: str
    route_scope_sha256: str
    observed_at: datetime
    expires_at: datetime
    publisher_observation_sha256: str
    witness_forward_sha256: str
    locator: PhysicalArvanS3LiveIamProbeLocator
    raw_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate:
    nonce: str
    issued_at: datetime
    expires_at: datetime
    committed_at: datetime
    evidence_binding_sha256: str
    nonce_commitment_sha256: str
    raw_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleLiveIamEvidenceError(code)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _campaign(value: object) -> str:
    if type(value) is not str or CAMPAIGN_ID_RE.fullmatch(value) is None:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_CAMPAIGN_INVALID")
    return value


def _release(value: object) -> str:
    if type(value) is not str or RELEASE_SHA_RE.fullmatch(value) is None:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_RELEASE_INVALID")
    return value


def _utc(value: object, *, code: str, whole_seconds: bool) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    normalized = value.astimezone(timezone.utc)
    if whole_seconds and normalized.microsecond != 0:
        _fail(code)
    return normalized


def _format_timestamp(value: object, *, code: str) -> str:
    return _utc(value, code=code, whole_seconds=True).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail(code)


def _validate_window(*, issued_at: datetime, expires_at: datetime, code: str) -> None:
    if expires_at <= issued_at or expires_at - issued_at > MAX_PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_TTL:
        _fail(code)


def _require_now_in_window(*, now: object, issued_at: datetime, expires_at: datetime, code: str) -> datetime:
    observed = _utc(now, code=code, whole_seconds=False)
    if observed < issued_at or observed >= expires_at:
        _fail(code)
    return observed


def _decode_base64(value: object, *, expected_bytes: int, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(decoded) != expected_bytes:
        _fail(code)
    return decoded


def _require_public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError):
        _fail(code)
    return value


def _public_key_from_signer(value: object, *, code: str) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization

        public_key = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError):
        _fail(code)
    return _require_public_key(public_key, code=code)


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + _sha256_bytes(public_key)


def _signer_record(public_key: bytes) -> dict[str, str]:
    return {
        "algorithm": "ed25519",
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "key_id": _key_id(public_key),
    }


def _parse_signer(value: object, *, code: str) -> tuple[bytes, str]:
    item = _exact_mapping(value, fields=_SIGNER_FIELDS, code=code)
    if item["algorithm"] != "ed25519" or type(item["key_id"]) is not str or _KEY_ID_RE.fullmatch(item["key_id"]) is None:
        _fail(code)
    public_key = _decode_base64(item["public_key_base64"], expected_bytes=32, code=code)
    public_key = _require_public_key(public_key, code=code)
    if item["key_id"] != _key_id(public_key):
        _fail(code)
    return public_key, item["key_id"]


def _signature_record(signature: bytes) -> dict[str, str]:
    if type(signature) is not bytes or len(signature) != 64:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_SIGNATURE_INVALID")
    return {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")}


def _parse_signature(value: object, *, code: str) -> bytes:
    item = _exact_mapping(value, fields=_SIGNATURE_FIELDS, code=code)
    if item["algorithm"] != "ed25519":
        _fail(code)
    return _decode_base64(item["signature_base64"], expected_bytes=64, code=code)


def _signed_bytes(*, kind: str, unsigned: Mapping[str, Any]) -> bytes:
    return (
        b"gold-trade-physical-arvan-s3-four-role-live-iam-evidence-v1/"
        + kind.encode("ascii")
        + b"\x00"
        + _canonical(unsigned, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_CANONICAL_INVALID")
    )


def _seal(*, unsigned: dict[str, Any], signer: object, signer_field: str, signature_field: str, kind: str) -> bytes:
    public_key = _public_key_from_signer(signer, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_SIGNER_INVALID")
    unsigned[signer_field] = _signer_record(public_key)
    try:
        signature = signer.sign(_signed_bytes(kind=kind, unsigned=unsigned))
    except (AttributeError, TypeError, ValueError):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_SIGNER_INVALID")
    signed = {**unsigned, signature_field: _signature_record(signature)}
    return _canonical(signed, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_CANONICAL_INVALID")


def _verify_signature(
    *,
    sealed: dict[str, Any],
    signer_field: str,
    signature_field: str,
    expected_public_key: bytes,
    kind: str,
    code: str,
) -> str:
    actual_public_key, key_id = _parse_signer(sealed[signer_field], code=code)
    pinned = _require_public_key(expected_public_key, code=code)
    if actual_public_key != pinned:
        _fail(code)
    signature = _parse_signature(sealed[signature_field], code=code)
    unsigned = {key: value for key, value in sealed.items() if key != signature_field}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(pinned).verify(signature, _signed_bytes(kind=kind, unsigned=unsigned))
    except ImportError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_ED25519_UNAVAILABLE")
    except InvalidSignature:
        _fail(code)
    return key_id


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WIRE_INVALID")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WIRE_INVALID")


def _parse_wire(raw: object, *, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_WIRE_BYTES:
        _fail(code)
    try:
        text = raw.decode("ascii")
        value = json.loads(text, object_pairs_hook=_strict_object, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        _fail(code)
    if not isinstance(value, dict) or _canonical(value, code=code) != raw:
        _fail(code)
    return value


def _role_identity(binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding, role: str) -> str:
    return {
        _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: binding.fi_publisher_identity_sha256,
        _profiles.ARVAN_S3_IR_RECEIVER_ROLE: binding.ir_receiver_identity_sha256,
        _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: binding.ir_publisher_identity_sha256,
        _profiles.ARVAN_S3_FI_RECEIVER_ROLE: binding.fi_receiver_identity_sha256,
    }[role]


def _role_public_key(binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding, role: str) -> bytes:
    return {
        _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: binding.fi_publisher_signer_public_key,
        _profiles.ARVAN_S3_IR_RECEIVER_ROLE: binding.ir_receiver_signer_public_key,
        _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: binding.ir_publisher_signer_public_key,
        _profiles.ARVAN_S3_FI_RECEIVER_ROLE: binding.fi_receiver_signer_public_key,
    }[role]


def _binding_payload(binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "normal_route_scope_sha256": binding.normal_route_scope_sha256,
        "reverse_route_scope_sha256": binding.reverse_route_scope_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "roles": {
            role: {
                "site": _ROLE_SITE[role],
                "identity_sha256": _role_identity(binding, role),
                "action_profile": _ROLE_PROFILE[role],
                "allowed_operations": list(_ROLE_ALLOWED[role]),
                "denied_operations": list(_ROLE_DENIED[role]),
                "signer_key_id": _key_id(_role_public_key(binding, role)),
            }
            for role in _ROLE_ORDER
        },
    }


def _binding_digest(binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding) -> str:
    return _sha256_bytes(_canonical(_binding_payload(binding), code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"))


def _require_binding(value: object) -> PhysicalArvanS3FourRoleLiveIamEvidenceBinding:
    if type(value) is not PhysicalArvanS3FourRoleLiveIamEvidenceBinding:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID")
    binding = value
    if binding.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID")
    _campaign(binding.campaign_id)
    _release(binding.release_sha)
    _sha256(binding.normal_route_scope_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID")
    _sha256(binding.reverse_route_scope_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID")
    _sha256(binding.four_role_binding_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID")
    identities = tuple(_sha256(_role_identity(binding, role), code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID") for role in _ROLE_ORDER)
    if len(set(identities)) != len(identities):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_IDENTITIES_NOT_DISTINCT")
    public_keys = tuple(_require_public_key(_role_public_key(binding, role), code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID") for role in _ROLE_ORDER)
    if len(set(public_keys)) != len(public_keys):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_SIGNERS_NOT_DISTINCT")
    if _sha256(binding.evidence_binding_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID") != _binding_digest(binding):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID")
    return binding


def build_physical_arvan_s3_four_role_live_iam_evidence_binding(
    *,
    campaign_id: str,
    release_sha: str,
    normal_route_scope_sha256: str,
    reverse_route_scope_sha256: str,
    four_role_binding_sha256: str,
    fi_publisher_identity_sha256: str,
    ir_receiver_identity_sha256: str,
    ir_publisher_identity_sha256: str,
    fi_receiver_identity_sha256: str,
    fi_publisher_signer_public_key: bytes,
    ir_receiver_signer_public_key: bytes,
    ir_publisher_signer_public_key: bytes,
    fi_receiver_signer_public_key: bytes,
) -> PhysicalArvanS3FourRoleLiveIamEvidenceBinding:
    """Pin all four local identities/keys and the two separately-derived routes.

    This accepts no provider permission digest.  The fixed operation matrices
    are taken only from :mod:`physical_arvan_s3_role_profiles`.
    """

    provisional = PhysicalArvanS3FourRoleLiveIamEvidenceBinding(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA,
        campaign_id=_campaign(campaign_id),
        release_sha=_release(release_sha),
        normal_route_scope_sha256=_sha256(normal_route_scope_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        reverse_route_scope_sha256=_sha256(reverse_route_scope_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        four_role_binding_sha256=_sha256(four_role_binding_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        evidence_binding_sha256="1" * 64,
        fi_publisher_identity_sha256=_sha256(fi_publisher_identity_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        ir_receiver_identity_sha256=_sha256(ir_receiver_identity_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        ir_publisher_identity_sha256=_sha256(ir_publisher_identity_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        fi_receiver_identity_sha256=_sha256(fi_receiver_identity_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        fi_publisher_signer_public_key=_require_public_key(fi_publisher_signer_public_key, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        ir_receiver_signer_public_key=_require_public_key(ir_receiver_signer_public_key, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        ir_publisher_signer_public_key=_require_public_key(ir_publisher_signer_public_key, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
        fi_receiver_signer_public_key=_require_public_key(fi_receiver_signer_public_key, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_BINDING_INVALID"),
    )
    completed = PhysicalArvanS3FourRoleLiveIamEvidenceBinding(
        **{**provisional.__dict__, "evidence_binding_sha256": _binding_digest(provisional)}
    )
    return _require_binding(completed)


def _route_scope(binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding, direction: str) -> str:
    if direction == _DIRECTION_NORMAL:
        return binding.normal_route_scope_sha256
    if direction == _DIRECTION_REVERSE:
        return binding.reverse_route_scope_sha256
    _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_DIRECTION_INVALID")


def derive_physical_arvan_s3_four_role_live_iam_probe_object_key(
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce: str,
    publisher_role: str,
) -> str:
    """Return the one disposable key permitted for one role/nonce probe."""

    checked = _require_binding(binding)
    if publisher_role not in _DIRECTION_BY_PUBLISHER:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_ROLE_INVALID")
    direction = _DIRECTION_BY_PUBLISHER[publisher_role]
    return (
        f"{_NAMESPACE_BY_DIRECTION[direction]}/{checked.campaign_id}/{checked.release_sha}/"
        f"live-iam-probe/{_nonce(nonce, code='ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_INVALID')}/"
        f"{publisher_role}.probe"
    )


def _locator_to_wire(locator: PhysicalArvanS3LiveIamProbeLocator) -> dict[str, Any]:
    return {
        "object_key": locator.object_key,
        "object_version_id": locator.object_version_id,
        "content_sha256": locator.content_sha256,
        "content_bytes": locator.content_bytes,
    }


def _require_locator(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce: str,
    publisher_role: str,
    code: str,
) -> PhysicalArvanS3LiveIamProbeLocator:
    if type(value) is PhysicalArvanS3LiveIamProbeLocator:
        item = _locator_to_wire(value)
    else:
        item = _exact_mapping(value, fields=_LOCATOR_FIELDS, code=code)
    object_key = item["object_key"]
    version_id = item["object_version_id"]
    content_sha256 = item["content_sha256"]
    content_bytes = item["content_bytes"]
    if type(object_key) is not str or OBJECT_KEY_RE.fullmatch(object_key) is None or ".." in object_key.split("/"):
        _fail(code)
    if type(version_id) is not str or VERSION_ID_RE.fullmatch(version_id) is None or version_id.lower() == "null":
        _fail(code)
    if type(content_bytes) is not int or content_bytes < 1 or content_bytes > _MAX_PROBE_OBJECT_BYTES:
        _fail(code)
    checked_hash = _sha256(content_sha256, code=code)
    expected_key = derive_physical_arvan_s3_four_role_live_iam_probe_object_key(
        binding=binding, nonce=nonce, publisher_role=publisher_role
    )
    if object_key != expected_key:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PROBE_SELECTOR_INVALID")
    return PhysicalArvanS3LiveIamProbeLocator(
        object_key=object_key,
        object_version_id=version_id,
        content_sha256=checked_hash,
        content_bytes=content_bytes,
    )


def make_physical_arvan_s3_live_iam_probe_locator(
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce: str,
    publisher_role: str,
    object_version_id: str,
    content_sha256: str,
    content_bytes: int,
) -> PhysicalArvanS3LiveIamProbeLocator:
    """Build a typed locator only after deriving its deterministic key."""

    checked = _require_binding(binding)
    candidate = PhysicalArvanS3LiveIamProbeLocator(
        object_key=derive_physical_arvan_s3_four_role_live_iam_probe_object_key(
            binding=checked, nonce=nonce, publisher_role=publisher_role
        ),
        object_version_id=object_version_id,
        content_sha256=content_sha256,
        content_bytes=content_bytes,
    )
    return _require_locator(
        candidate,
        binding=checked,
        nonce=nonce,
        publisher_role=publisher_role,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PROBE_SELECTOR_INVALID",
    )


def _normalize_outcomes(
    value: object,
    *,
    expected_operations: tuple[str, ...],
    expected_outcome: str,
    code: str,
) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail(code)
    result: list[dict[str, str]] = []
    for item in value:
        parsed = _exact_mapping(item, fields=_OUTCOME_FIELDS, code=code)
        if type(parsed["operation"]) is not str or parsed["outcome"] != expected_outcome:
            _fail(code)
        result.append({"operation": parsed["operation"], "outcome": parsed["outcome"]})
    if tuple(item["operation"] for item in result) != expected_operations:
        _fail(code)
    return result


def _role_outcomes(
    *,
    role: str,
    allowed_operation_outcomes: object,
    denied_operation_outcomes: object,
    code: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if role not in _ROLE_ORDER:
        _fail(code)
    return (
        _normalize_outcomes(
            allowed_operation_outcomes,
            expected_operations=_ROLE_ALLOWED[role],
            expected_outcome="allowed",
            code=code,
        ),
        _normalize_outcomes(
            denied_operation_outcomes,
            expected_operations=_ROLE_DENIED[role],
            expected_outcome="denied",
            code=code,
        ),
    )


def _ledger_record_wire(record: PhysicalArvanS3FourRoleLiveIamNonceRecord) -> dict[str, Any]:
    return {
        "nonce": record.nonce,
        "issued_at": record.issued_at,
        "expires_at": record.expires_at,
        "status": record.status,
        "prior_ledger_sha256": record.prior_ledger_sha256,
        "commit_prior_ledger_sha256": record.commit_prior_ledger_sha256,
        "nonce_commitment_sha256": record.nonce_commitment_sha256,
        "aggregate_sha256": record.aggregate_sha256,
        "committed_at": record.committed_at,
        "retired_at": record.retired_at,
    }


def _ledger_wire(ledger: PhysicalArvanS3FourRoleLiveIamNonceLedger) -> dict[str, Any]:
    return {
        "schema": ledger.schema,
        "evidence_binding_sha256": ledger.evidence_binding_sha256,
        "records": [_ledger_record_wire(record) for record in ledger.records],
    }


def _ledger_sha256(ledger: PhysicalArvanS3FourRoleLiveIamNonceLedger) -> str:
    return _sha256_bytes(_canonical(_ledger_wire(ledger), code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID"))


def _require_ledger(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
) -> PhysicalArvanS3FourRoleLiveIamNonceLedger:
    if type(value) is not PhysicalArvanS3FourRoleLiveIamNonceLedger:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
    ledger = value
    if (
        ledger.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_LEDGER_SCHEMA
        or _sha256(ledger.evidence_binding_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        != binding.evidence_binding_sha256
        or type(ledger.records) is not tuple
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
    seen: set[str] = set()
    previous_digest = _sha256_bytes(
        _canonical(
            {
                "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_LEDGER_SCHEMA,
                "evidence_binding_sha256": binding.evidence_binding_sha256,
                "records": [],
            },
            code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID",
        )
    )
    for record in ledger.records:
        if type(record) is not PhysicalArvanS3FourRoleLiveIamNonceRecord:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        nonce = _nonce(record.nonce, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        if nonce in seen:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_REPLAY")
        seen.add(nonce)
        issued_at = _parse_timestamp(record.issued_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        expires_at = _parse_timestamp(record.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        _validate_window(issued_at=issued_at, expires_at=expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        if _sha256(record.prior_ledger_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID") != previous_digest:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_CHAIN_INVALID")
        if record.status == _LEDGER_OPEN:
            if any(
                item is not None
                for item in (
                    record.commit_prior_ledger_sha256,
                    record.nonce_commitment_sha256,
                    record.aggregate_sha256,
                    record.committed_at,
                    record.retired_at,
                )
            ):
                _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        elif record.status == _LEDGER_COMMITTED:
            _sha256(record.commit_prior_ledger_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
            commitment = _sha256(record.nonce_commitment_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
            _sha256(record.aggregate_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
            committed_at = _parse_timestamp(record.committed_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
            if (
                committed_at < issued_at
                or committed_at >= expires_at
                or commitment == "0" * 64
                or record.retired_at is not None
            ):
                _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        elif record.status == _LEDGER_EXPIRED:
            if any(
                item is not None
                for item in (
                    record.commit_prior_ledger_sha256,
                    record.nonce_commitment_sha256,
                    record.aggregate_sha256,
                    record.committed_at,
                )
            ):
                _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
            retired_at = _parse_timestamp(record.retired_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
            if retired_at < expires_at:
                _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        else:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        previous_digest = _sha256_bytes(
            _canonical(
                {
                    "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_LEDGER_SCHEMA,
                    "evidence_binding_sha256": binding.evidence_binding_sha256,
                    "records": [_ledger_record_wire(item) for item in ledger.records[: len(seen)]],
                },
                code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID",
            )
        )
    return ledger


def make_physical_arvan_s3_four_role_live_iam_nonce_ledger(
    *, binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding
) -> PhysicalArvanS3FourRoleLiveIamNonceLedger:
    """Create the empty serializable state for one exact evidence binding."""

    checked = _require_binding(binding)
    ledger = PhysicalArvanS3FourRoleLiveIamNonceLedger(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_LEDGER_SCHEMA,
        evidence_binding_sha256=checked.evidence_binding_sha256,
        records=(),
    )
    return _require_ledger(ledger, binding=checked)


def serialize_physical_arvan_s3_four_role_live_iam_nonce_ledger(
    ledger: PhysicalArvanS3FourRoleLiveIamNonceLedger,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
) -> bytes:
    """Canonicalize logical state for a later root-owned durable adapter."""

    checked = _require_binding(binding)
    return _canonical(_ledger_wire(_require_ledger(ledger, binding=checked)), code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")


def parse_physical_arvan_s3_four_role_live_iam_nonce_ledger(
    raw: bytes,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
) -> PhysicalArvanS3FourRoleLiveIamNonceLedger:
    """Parse canonical durable state; anti-rollback storage is an adapter duty."""

    checked = _require_binding(binding)
    item = _parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
    payload = _exact_mapping(item, fields=_LEDGER_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
    if payload["schema"] != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_LEDGER_SCHEMA:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
    if _sha256(payload["evidence_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID") != checked.evidence_binding_sha256:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
    if not isinstance(payload["records"], list):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
    records: list[PhysicalArvanS3FourRoleLiveIamNonceRecord] = []
    for raw_record in payload["records"]:
        record = _exact_mapping(raw_record, fields=_LEDGER_RECORD_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
        records.append(
            PhysicalArvanS3FourRoleLiveIamNonceRecord(
                nonce=record["nonce"],
                issued_at=record["issued_at"],
                expires_at=record["expires_at"],
                status=record["status"],
                prior_ledger_sha256=record["prior_ledger_sha256"],
                commit_prior_ledger_sha256=record["commit_prior_ledger_sha256"],
                nonce_commitment_sha256=record["nonce_commitment_sha256"],
                aggregate_sha256=record["aggregate_sha256"],
                committed_at=record["committed_at"],
                retired_at=record["retired_at"],
            )
        )
    return _require_ledger(
        PhysicalArvanS3FourRoleLiveIamNonceLedger(
            schema=payload["schema"],
            evidence_binding_sha256=payload["evidence_binding_sha256"],
            records=tuple(records),
        ),
        binding=checked,
    )


def _permit_unsigned(
    *, binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding, nonce: str, issued_at: str, expires_at: str
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA,
        "kind": _PERMIT_KIND,
        "nonce": nonce,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "evidence_binding_sha256": binding.evidence_binding_sha256,
        "normal_route_scope_sha256": binding.normal_route_scope_sha256,
        "reverse_route_scope_sha256": binding.reverse_route_scope_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
    }


def issue_physical_arvan_s3_four_role_live_iam_nonce_permit(
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    ledger: PhysicalArvanS3FourRoleLiveIamNonceLedger,
    nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> tuple[PhysicalArvanS3FourRoleLiveIamNonceLedger, bytes]:
    """Atomically model opening exactly one nonce and sealing its Witness permit."""

    checked = _require_binding(binding)
    current = _require_ledger(ledger, binding=checked)
    checked_nonce = _nonce(nonce, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_INVALID")
    if any(record.nonce == checked_nonce for record in current.records):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_REPLAY")
    # A committed record is updated in place so that the aggregate and the
    # durable one-use slot share one identity.  Do not create a later record
    # while an earlier slot is open: otherwise an in-place commit could alter
    # the prior-ledger digest pinned by that later record.  The Witness can
    # only run one short IAM probe campaign at a time, which is intentional.
    if any(record.status == _LEDGER_OPEN for record in current.records):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_ALREADY_OPEN")
    issued = _format_timestamp(issued_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_WINDOW_INVALID")
    expires = _format_timestamp(expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_WINDOW_INVALID")
    parsed_issued = _parse_timestamp(issued, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_WINDOW_INVALID")
    parsed_expires = _parse_timestamp(expires, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_WINDOW_INVALID")
    _validate_window(issued_at=parsed_issued, expires_at=parsed_expires, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_WINDOW_INVALID")
    prior_ledger_sha256 = _ledger_sha256(current)
    next_ledger = PhysicalArvanS3FourRoleLiveIamNonceLedger(
        schema=current.schema,
        evidence_binding_sha256=current.evidence_binding_sha256,
        records=(
            *current.records,
            PhysicalArvanS3FourRoleLiveIamNonceRecord(
                nonce=checked_nonce,
                issued_at=issued,
                expires_at=expires,
                status=_LEDGER_OPEN,
                prior_ledger_sha256=prior_ledger_sha256,
            ),
        ),
    )
    next_ledger = _require_ledger(next_ledger, binding=checked)
    raw = _seal(
        unsigned=_permit_unsigned(binding=checked, nonce=checked_nonce, issued_at=issued, expires_at=expires),
        signer=witness_signer,
        signer_field="witness_signer",
        signature_field="witness_signature",
        kind=_PERMIT_KIND,
    )
    return next_ledger, raw


def expire_physical_arvan_s3_four_role_live_iam_nonce(
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    ledger: PhysicalArvanS3FourRoleLiveIamNonceLedger,
    nonce: str,
    retired_at: datetime,
) -> PhysicalArvanS3FourRoleLiveIamNonceLedger:
    """Close an uncommitted nonce only after its permit has expired.

    This is a pure Witness-state transition, not an authority to delete the
    disposable Object.  An expired probe may leave an immutable orphan, but it
    can never be committed and the next nonce gets a distinct deterministic
    object key.  Since issuance permits only one open slot, the retired record
    is necessarily the last chain entry and changing its status cannot alter a
    later entry's prior-ledger commitment.
    """

    checked = _require_binding(binding)
    current = _require_ledger(ledger, binding=checked)
    checked_nonce = _nonce(nonce, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_INVALID")
    matches = [
        (index, record)
        for index, record in enumerate(current.records)
        if record.nonce == checked_nonce
    ]
    if len(matches) != 1 or matches[0][1].status != _LEDGER_OPEN:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_NOT_OPEN")
    index, record = matches[0]
    if index != len(current.records) - 1:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_CHAIN_INVALID")
    retired = _format_timestamp(retired_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_RETIRE_INVALID")
    if _parse_timestamp(retired, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_RETIRE_INVALID") < _parse_timestamp(
        record.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID"
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_RETIRE_EARLY")
    records = list(current.records)
    records[index] = PhysicalArvanS3FourRoleLiveIamNonceRecord(
        nonce=record.nonce,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        status=_LEDGER_EXPIRED,
        prior_ledger_sha256=record.prior_ledger_sha256,
        retired_at=retired,
    )
    return _require_ledger(
        PhysicalArvanS3FourRoleLiveIamNonceLedger(
            schema=current.schema,
            evidence_binding_sha256=current.evidence_binding_sha256,
            records=tuple(records),
        ),
        binding=checked,
    )


def _parse_permit(
    raw: object,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    witness_public_key: bytes,
    observed_at: object,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit:
    sealed = _exact_mapping(_parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID"), fields=_PERMIT_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID")
    if sealed["schema"] != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA or sealed["kind"] != _PERMIT_KIND:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID")
    nonce = _nonce(sealed["nonce"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID")
    issued_at = _parse_timestamp(sealed["issued_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID")
    expires_at = _parse_timestamp(sealed["expires_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID")
    _validate_window(issued_at=issued_at, expires_at=expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID")
    if (
        _sha256(sealed["evidence_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID") != binding.evidence_binding_sha256
        or _sha256(sealed["normal_route_scope_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID") != binding.normal_route_scope_sha256
        or _sha256(sealed["reverse_route_scope_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID") != binding.reverse_route_scope_sha256
        or _sha256(sealed["four_role_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID") != binding.four_role_binding_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROUTE_MISMATCH")
    key_id = _verify_signature(
        sealed=sealed,
        signer_field="witness_signer",
        signature_field="witness_signature",
        expected_public_key=witness_public_key,
        kind=_PERMIT_KIND,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_SIGNATURE_INVALID",
    )
    _require_now_in_window(now=observed_at, issued_at=issued_at, expires_at=expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_STALE")
    verified = VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit(
        nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        evidence_binding_sha256=binding.evidence_binding_sha256,
        witness_key_id=key_id,
        raw_sha256=_sha256_bytes(raw),
    )
    object.__setattr__(verified, "_capability", _VERIFIED_PERMIT_CAPABILITY)
    return verified


def verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
    raw: bytes,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    witness_public_key: bytes,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit:
    """Verify a fresh Witness nonce permit before a local role observes IAM."""

    return _parse_permit(
        raw,
        binding=_require_binding(binding),
        witness_public_key=witness_public_key,
        observed_at=observed_at,
    )


def _require_permit(
    value: object, *, binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding, observed_at: object
) -> VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit:
    if type(value) is not VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit or value._capability is not _VERIFIED_PERMIT_CAPABILITY:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_NOT_VERIFIED")
    if value.evidence_binding_sha256 != binding.evidence_binding_sha256:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROUTE_MISMATCH")
    _require_now_in_window(now=observed_at, issued_at=value.issued_at, expires_at=value.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_STALE")
    return value


def seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    publisher_role: str,
    observed_at: datetime,
    probe_locator: PhysicalArvanS3LiveIamProbeLocator,
    allowed_operation_outcomes: Sequence[Mapping[str, str]],
    denied_operation_outcomes: Sequence[Mapping[str, str]],
    role_signer: object,
) -> bytes:
    """Seal one role-local publisher observation; no receiver input exists."""

    checked = _require_binding(binding)
    permit = _require_permit(nonce_permit, binding=checked, observed_at=observed_at)
    if publisher_role not in _DIRECTION_BY_PUBLISHER:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_ROLE_INVALID")
    observed = _format_timestamp(observed_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_OBSERVED_AT_INVALID")
    locator = _require_locator(
        probe_locator,
        binding=checked,
        nonce=permit.nonce,
        publisher_role=publisher_role,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PROBE_SELECTOR_INVALID",
    )
    allowed, denied = _role_outcomes(
        role=publisher_role,
        allowed_operation_outcomes=allowed_operation_outcomes,
        denied_operation_outcomes=denied_operation_outcomes,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_OUTCOME_MATRIX_INVALID",
    )
    unsigned = {
        "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA,
        "kind": _PUBLISHER_KIND,
        "nonce": permit.nonce,
        "observed_at": observed,
        "expires_at": _format_timestamp(permit.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID"),
        "evidence_binding_sha256": checked.evidence_binding_sha256,
        "direction": _DIRECTION_BY_PUBLISHER[publisher_role],
        "route_scope_sha256": _route_scope(checked, _DIRECTION_BY_PUBLISHER[publisher_role]),
        "role": publisher_role,
        "identity_sha256": _role_identity(checked, publisher_role),
        "action_profile": _ROLE_PROFILE[publisher_role],
        "allowed_operation_outcomes": allowed,
        "denied_operation_outcomes": denied,
        "probe_locator": _locator_to_wire(locator),
    }
    raw = _seal(
        unsigned=unsigned,
        signer=role_signer,
        signer_field="role_signer",
        signature_field="role_signature",
        kind=_PUBLISHER_KIND,
    )
    # A local runner may only seal with its pinned local signing key.
    parsed = _parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID")
    _verify_signature(
        sealed=_exact_mapping(parsed, fields=_PUBLISHER_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID"),
        signer_field="role_signer",
        signature_field="role_signature",
        expected_public_key=_role_public_key(checked, publisher_role),
        kind=_PUBLISHER_KIND,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROLE_SIGNER_NOT_PINNED",
    )
    return raw


def _parse_publisher(
    raw: object,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    observed_at: object,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation:
    permit = _require_permit(nonce_permit, binding=binding, observed_at=observed_at)
    sealed = _exact_mapping(_parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID"), fields=_PUBLISHER_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID")
    if sealed["schema"] != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA or sealed["kind"] != _PUBLISHER_KIND:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID")
    role = sealed["role"]
    if role not in _DIRECTION_BY_PUBLISHER:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_ROLE_INVALID")
    direction = _DIRECTION_BY_PUBLISHER[role]
    if (
        _nonce(sealed["nonce"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID") != permit.nonce
        or _parse_timestamp(sealed["expires_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID") != permit.expires_at
        or _sha256(sealed["evidence_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID") != binding.evidence_binding_sha256
        or sealed["direction"] != direction
        or _sha256(sealed["route_scope_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID") != _route_scope(binding, direction)
        or _sha256(sealed["identity_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID") != _role_identity(binding, role)
        or sealed["action_profile"] != _ROLE_PROFILE[role]
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROUTE_MISMATCH")
    claim_observed = _parse_timestamp(sealed["observed_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_INVALID")
    if claim_observed < permit.issued_at or claim_observed >= permit.expires_at:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_STALE")
    _role_outcomes(
        role=role,
        allowed_operation_outcomes=sealed["allowed_operation_outcomes"],
        denied_operation_outcomes=sealed["denied_operation_outcomes"],
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_OUTCOME_MATRIX_INVALID",
    )
    locator = _require_locator(
        sealed["probe_locator"],
        binding=binding,
        nonce=permit.nonce,
        publisher_role=role,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PROBE_SELECTOR_INVALID",
    )
    _verify_signature(
        sealed=sealed,
        signer_field="role_signer",
        signature_field="role_signature",
        expected_public_key=_role_public_key(binding, role),
        kind=_PUBLISHER_KIND,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROLE_SIGNER_NOT_PINNED",
    )
    verified = VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation(
        nonce=permit.nonce,
        direction=direction,
        role=role,
        route_scope_sha256=_route_scope(binding, direction),
        observed_at=claim_observed,
        expires_at=permit.expires_at,
        locator=locator,
        raw_sha256=_sha256_bytes(raw),
    )
    object.__setattr__(verified, "_capability", _VERIFIED_PUBLISHER_CAPABILITY)
    return verified


def verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
    raw: bytes,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation:
    """Verify publisher evidence only after it was routed back to Witness."""

    return _parse_publisher(
        raw,
        binding=_require_binding(binding),
        nonce_permit=nonce_permit,
        observed_at=observed_at,
    )


def _require_publisher(value: object) -> VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation:
    if type(value) is not VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation or value._capability is not _VERIFIED_PUBLISHER_CAPABILITY:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PUBLISHER_NOT_VERIFIED")
    return value


def seal_physical_arvan_s3_four_role_live_iam_witness_forward(
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    publisher_observation: VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
    forwarded_at: datetime,
    witness_signer: object,
) -> bytes:
    """Create the only receiver-facing envelope; no site-peer address exists."""

    checked = _require_binding(binding)
    permit = _require_permit(nonce_permit, binding=checked, observed_at=forwarded_at)
    publisher = _require_publisher(publisher_observation)
    if publisher.nonce != permit.nonce or publisher.expires_at != permit.expires_at:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_BINDING_INVALID")
    receiver_role = _RECEIVER_BY_DIRECTION[publisher.direction]
    unsigned = {
        "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA,
        "kind": _FORWARD_KIND,
        "nonce": permit.nonce,
        "forwarded_at": _format_timestamp(forwarded_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID"),
        "expires_at": _format_timestamp(permit.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID"),
        "evidence_binding_sha256": checked.evidence_binding_sha256,
        "direction": publisher.direction,
        "route_scope_sha256": publisher.route_scope_sha256,
        "publisher_role": publisher.role,
        "receiver_role": receiver_role,
        "publisher_observation_sha256": publisher.raw_sha256,
        "probe_locator": _locator_to_wire(publisher.locator),
    }
    return _seal(
        unsigned=unsigned,
        signer=witness_signer,
        signer_field="witness_signer",
        signature_field="witness_signature",
        kind=_FORWARD_KIND,
    )


def _parse_forward(
    raw: object,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    witness_public_key: bytes,
    observed_at: object,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward:
    permit = _require_permit(nonce_permit, binding=binding, observed_at=observed_at)
    sealed = _exact_mapping(_parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID"), fields=_FORWARD_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID")
    if sealed["schema"] != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA or sealed["kind"] != _FORWARD_KIND:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID")
    direction = sealed["direction"]
    publisher_role = sealed["publisher_role"]
    receiver_role = sealed["receiver_role"]
    if (
        direction not in _PUBLISHER_BY_DIRECTION
        or publisher_role != _PUBLISHER_BY_DIRECTION[direction]
        or receiver_role != _RECEIVER_BY_DIRECTION[direction]
        or _nonce(sealed["nonce"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID") != permit.nonce
        or _parse_timestamp(sealed["expires_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID") != permit.expires_at
        or _sha256(sealed["evidence_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID") != binding.evidence_binding_sha256
        or _sha256(sealed["route_scope_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID") != _route_scope(binding, direction)
        or _sha256(sealed["publisher_observation_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID") == "0" * 64
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_BINDING_INVALID")
    forwarded_at = _parse_timestamp(sealed["forwarded_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_INVALID")
    if forwarded_at < permit.issued_at or forwarded_at >= permit.expires_at:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_STALE")
    locator = _require_locator(
        sealed["probe_locator"],
        binding=binding,
        nonce=permit.nonce,
        publisher_role=publisher_role,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PROBE_SELECTOR_INVALID",
    )
    key_id = _verify_signature(
        sealed=sealed,
        signer_field="witness_signer",
        signature_field="witness_signature",
        expected_public_key=witness_public_key,
        kind=_FORWARD_KIND,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_SIGNATURE_INVALID",
    )
    verified = VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward(
        nonce=permit.nonce,
        direction=direction,
        publisher_role=publisher_role,
        receiver_role=receiver_role,
        route_scope_sha256=_route_scope(binding, direction),
        forwarded_at=forwarded_at,
        expires_at=permit.expires_at,
        publisher_observation_sha256=sealed["publisher_observation_sha256"],
        locator=locator,
        raw_sha256=_sha256_bytes(raw),
        witness_key_id=key_id,
    )
    object.__setattr__(verified, "_capability", _VERIFIED_FORWARD_CAPABILITY)
    return verified


def verify_physical_arvan_s3_four_role_live_iam_witness_forward(
    raw: bytes,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    witness_public_key: bytes,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward:
    """Verify the Witness envelope before any receiver-side object request."""

    return _parse_forward(
        raw,
        binding=_require_binding(binding),
        nonce_permit=nonce_permit,
        witness_public_key=witness_public_key,
        observed_at=observed_at,
    )


def _require_forward(value: object) -> VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward:
    if type(value) is not VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward or value._capability is not _VERIFIED_FORWARD_CAPABILITY:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_FORWARD_NOT_VERIFIED")
    return value


def seal_physical_arvan_s3_four_role_live_iam_receiver_observation(
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    witness_forward: VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
    observed_at: datetime,
    allowed_operation_outcomes: Sequence[Mapping[str, str]],
    denied_operation_outcomes: Sequence[Mapping[str, str]],
    role_signer: object,
) -> bytes:
    """Seal receiver readback evidence only from a verified Witness forward."""

    checked = _require_binding(binding)
    permit = _require_permit(nonce_permit, binding=checked, observed_at=observed_at)
    forward = _require_forward(witness_forward)
    if forward.nonce != permit.nonce or forward.expires_at != permit.expires_at:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_BINDING_INVALID")
    role = forward.receiver_role
    allowed, denied = _role_outcomes(
        role=role,
        allowed_operation_outcomes=allowed_operation_outcomes,
        denied_operation_outcomes=denied_operation_outcomes,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_OUTCOME_MATRIX_INVALID",
    )
    unsigned = {
        "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA,
        "kind": _RECEIVER_KIND,
        "nonce": permit.nonce,
        "observed_at": _format_timestamp(observed_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_OBSERVED_AT_INVALID"),
        "expires_at": _format_timestamp(permit.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID"),
        "evidence_binding_sha256": checked.evidence_binding_sha256,
        "direction": forward.direction,
        "route_scope_sha256": forward.route_scope_sha256,
        "role": role,
        "identity_sha256": _role_identity(checked, role),
        "action_profile": _ROLE_PROFILE[role],
        "publisher_observation_sha256": forward.publisher_observation_sha256,
        "witness_forward_sha256": forward.raw_sha256,
        "allowed_operation_outcomes": allowed,
        "denied_operation_outcomes": denied,
        "probe_locator": _locator_to_wire(forward.locator),
    }
    raw = _seal(
        unsigned=unsigned,
        signer=role_signer,
        signer_field="role_signer",
        signature_field="role_signature",
        kind=_RECEIVER_KIND,
    )
    parsed = _parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID")
    _verify_signature(
        sealed=_exact_mapping(parsed, fields=_RECEIVER_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID"),
        signer_field="role_signer",
        signature_field="role_signature",
        expected_public_key=_role_public_key(checked, role),
        kind=_RECEIVER_KIND,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROLE_SIGNER_NOT_PINNED",
    )
    return raw


def _parse_receiver(
    raw: object,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    witness_forward: VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
    observed_at: object,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation:
    permit = _require_permit(nonce_permit, binding=binding, observed_at=observed_at)
    forward = _require_forward(witness_forward)
    sealed = _exact_mapping(_parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID"), fields=_RECEIVER_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID")
    if sealed["schema"] != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA or sealed["kind"] != _RECEIVER_KIND:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID")
    role = sealed["role"]
    if (
        role != forward.receiver_role
        or _nonce(sealed["nonce"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID") != permit.nonce
        or _parse_timestamp(sealed["expires_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID") != permit.expires_at
        or _sha256(sealed["evidence_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID") != binding.evidence_binding_sha256
        or sealed["direction"] != forward.direction
        or _sha256(sealed["route_scope_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID") != forward.route_scope_sha256
        or _sha256(sealed["identity_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID") != _role_identity(binding, role)
        or sealed["action_profile"] != _ROLE_PROFILE[role]
        or _sha256(sealed["publisher_observation_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID") != forward.publisher_observation_sha256
        or _sha256(sealed["witness_forward_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID") != forward.raw_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_BINDING_INVALID")
    claim_observed = _parse_timestamp(sealed["observed_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_INVALID")
    if claim_observed < permit.issued_at or claim_observed >= permit.expires_at:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_STALE")
    _role_outcomes(
        role=role,
        allowed_operation_outcomes=sealed["allowed_operation_outcomes"],
        denied_operation_outcomes=sealed["denied_operation_outcomes"],
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_OUTCOME_MATRIX_INVALID",
    )
    locator = _require_locator(
        sealed["probe_locator"],
        binding=binding,
        nonce=permit.nonce,
        publisher_role=forward.publisher_role,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PROBE_SELECTOR_INVALID",
    )
    if locator != forward.locator:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PROBE_SELECTOR_MISMATCH")
    _verify_signature(
        sealed=sealed,
        signer_field="role_signer",
        signature_field="role_signature",
        expected_public_key=_role_public_key(binding, role),
        kind=_RECEIVER_KIND,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROLE_SIGNER_NOT_PINNED",
    )
    verified = VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation(
        nonce=permit.nonce,
        direction=forward.direction,
        role=role,
        route_scope_sha256=forward.route_scope_sha256,
        observed_at=claim_observed,
        expires_at=permit.expires_at,
        publisher_observation_sha256=forward.publisher_observation_sha256,
        witness_forward_sha256=forward.raw_sha256,
        locator=locator,
        raw_sha256=_sha256_bytes(raw),
    )
    object.__setattr__(verified, "_capability", _VERIFIED_RECEIVER_CAPABILITY)
    return verified


def verify_physical_arvan_s3_four_role_live_iam_receiver_observation(
    raw: bytes,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    witness_forward: VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation:
    """Verify exact receiver head/get evidence bound to the Witness forward."""

    return _parse_receiver(
        raw,
        binding=_require_binding(binding),
        nonce_permit=nonce_permit,
        witness_forward=witness_forward,
        observed_at=observed_at,
    )


def _require_receiver(value: object) -> VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation:
    if type(value) is not VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation or value._capability is not _VERIFIED_RECEIVER_CAPABILITY:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_RECEIVER_NOT_VERIFIED")
    return value


def _direction_evidence_wire(
    publisher: VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
    forward: VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
    receiver: VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
) -> dict[str, Any]:
    return {
        "publisher_role": publisher.role,
        "receiver_role": receiver.role,
        "publisher_observation_sha256": publisher.raw_sha256,
        "witness_forward_sha256": forward.raw_sha256,
        "receiver_observation_sha256": receiver.raw_sha256,
        "probe_locator": _locator_to_wire(publisher.locator),
    }


def _require_direction_chain(
    *,
    direction: str,
    permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    publisher: object,
    forward: object,
    receiver: object,
) -> tuple[
    VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
    VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
    VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
]:
    checked_publisher = _require_publisher(publisher)
    checked_forward = _require_forward(forward)
    checked_receiver = _require_receiver(receiver)
    if (
        checked_publisher.nonce != permit.nonce
        or checked_forward.nonce != permit.nonce
        or checked_receiver.nonce != permit.nonce
        or checked_publisher.direction != direction
        or checked_forward.direction != direction
        or checked_receiver.direction != direction
        or checked_publisher.role != _PUBLISHER_BY_DIRECTION[direction]
        or checked_forward.publisher_role != checked_publisher.role
        or checked_forward.receiver_role != _RECEIVER_BY_DIRECTION[direction]
        or checked_receiver.role != checked_forward.receiver_role
        or checked_forward.publisher_observation_sha256 != checked_publisher.raw_sha256
        or checked_receiver.publisher_observation_sha256 != checked_publisher.raw_sha256
        or checked_receiver.witness_forward_sha256 != checked_forward.raw_sha256
        or checked_publisher.locator != checked_forward.locator
        or checked_publisher.locator != checked_receiver.locator
        or checked_publisher.expires_at != permit.expires_at
        or checked_forward.expires_at != permit.expires_at
        or checked_receiver.expires_at != permit.expires_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_DIRECTION_CHAIN_INVALID")
    return checked_publisher, checked_forward, checked_receiver


def _role_matrix(binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding) -> dict[str, Any]:
    return {
        role: {
            "identity_sha256": _role_identity(binding, role),
            "action_profile": _ROLE_PROFILE[role],
            "allowed_operation_outcomes": [
                {"operation": operation, "outcome": "allowed"} for operation in _ROLE_ALLOWED[role]
            ],
            "denied_operation_outcomes": [
                {"operation": operation, "outcome": "denied"} for operation in _ROLE_DENIED[role]
            ],
            "signer": _signer_record(_role_public_key(binding, role)),
        }
        for role in _ROLE_ORDER
    }


def _nonce_commitment(
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce: str,
    prior_ledger_sha256: str,
    committed_at: str,
    normal: Mapping[str, Any],
    reverse: Mapping[str, Any],
) -> str:
    return _sha256_bytes(
        _canonical(
            {
                "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA,
                "purpose": "nonce-commit-v1",
                "evidence_binding_sha256": binding.evidence_binding_sha256,
                "nonce": nonce,
                "prior_ledger_sha256": prior_ledger_sha256,
                "committed_at": committed_at,
                "normal_direction": normal,
                "reverse_direction": reverse,
            },
            code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_COMMIT_INVALID",
        )
    )


def seal_physical_arvan_s3_four_role_live_iam_witness_aggregate(
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    ledger: PhysicalArvanS3FourRoleLiveIamNonceLedger,
    nonce_permit: VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    normal_publisher_observation: VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
    normal_witness_forward: VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
    normal_receiver_observation: VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
    reverse_publisher_observation: VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
    reverse_witness_forward: VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
    reverse_receiver_observation: VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
    committed_at: datetime,
    witness_signer: object,
) -> tuple[PhysicalArvanS3FourRoleLiveIamNonceLedger, bytes]:
    """Commit exactly one fully verified two-direction evidence set at Witness."""

    checked = _require_binding(binding)
    current = _require_ledger(ledger, binding=checked)
    permit = _require_permit(nonce_permit, binding=checked, observed_at=committed_at)
    matching = [record for record in current.records if record.nonce == permit.nonce]
    if len(matching) != 1 or matching[0].status != _LEDGER_OPEN:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_NOT_OPEN")
    record = matching[0]
    if (
        _parse_timestamp(record.issued_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID") != permit.issued_at
        or _parse_timestamp(record.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID") != permit.expires_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_LEDGER_INVALID")
    normal = _direction_evidence_wire(
        *_require_direction_chain(
            direction=_DIRECTION_NORMAL,
            permit=permit,
            publisher=normal_publisher_observation,
            forward=normal_witness_forward,
            receiver=normal_receiver_observation,
        )
    )
    reverse = _direction_evidence_wire(
        *_require_direction_chain(
            direction=_DIRECTION_REVERSE,
            permit=permit,
            publisher=reverse_publisher_observation,
            forward=reverse_witness_forward,
            receiver=reverse_receiver_observation,
        )
    )
    committed = _format_timestamp(committed_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_COMMIT_INVALID")
    prior_ledger_sha256 = _ledger_sha256(current)
    nonce_commitment_sha256 = _nonce_commitment(
        binding=checked,
        nonce=permit.nonce,
        prior_ledger_sha256=prior_ledger_sha256,
        committed_at=committed,
        normal=normal,
        reverse=reverse,
    )
    unsigned = {
        "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA,
        "kind": _AGGREGATE_KIND,
        "nonce": permit.nonce,
        "issued_at": _format_timestamp(permit.issued_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID"),
        "expires_at": _format_timestamp(permit.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PERMIT_INVALID"),
        "committed_at": committed,
        "evidence_binding_sha256": checked.evidence_binding_sha256,
        "normal_route_scope_sha256": checked.normal_route_scope_sha256,
        "reverse_route_scope_sha256": checked.reverse_route_scope_sha256,
        "four_role_binding_sha256": checked.four_role_binding_sha256,
        "prior_ledger_sha256": prior_ledger_sha256,
        "nonce_commitment_sha256": nonce_commitment_sha256,
        "role_matrix": _role_matrix(checked),
        "normal_direction": normal,
        "reverse_direction": reverse,
    }
    raw = _seal(
        unsigned=unsigned,
        signer=witness_signer,
        signer_field="witness_signer",
        signature_field="witness_signature",
        kind=_AGGREGATE_KIND,
    )
    aggregate_sha256 = _sha256_bytes(raw)
    records = list(current.records)
    index = records.index(record)
    records[index] = PhysicalArvanS3FourRoleLiveIamNonceRecord(
        nonce=record.nonce,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        status=_LEDGER_COMMITTED,
        prior_ledger_sha256=record.prior_ledger_sha256,
        commit_prior_ledger_sha256=prior_ledger_sha256,
        nonce_commitment_sha256=nonce_commitment_sha256,
        aggregate_sha256=aggregate_sha256,
        committed_at=committed,
    )
    next_ledger = _require_ledger(
        PhysicalArvanS3FourRoleLiveIamNonceLedger(
            schema=current.schema,
            evidence_binding_sha256=current.evidence_binding_sha256,
            records=tuple(records),
        ),
        binding=checked,
    )
    return next_ledger, raw


def _validate_role_matrix(value: object, *, binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding) -> None:
    if not isinstance(value, Mapping) or set(value) != set(_ROLE_ORDER):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROLE_MATRIX_INVALID")
    expected = _role_matrix(binding)
    for role in _ROLE_ORDER:
        actual = _exact_mapping(value[role], fields=_ROLE_MATRIX_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROLE_MATRIX_INVALID")
        if actual != expected[role]:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROLE_MATRIX_INVALID")


def _parse_direction_evidence(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    nonce: str,
    direction: str,
) -> dict[str, Any]:
    item = _exact_mapping(value, fields=_DIRECTION_EVIDENCE_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_DIRECTION_CHAIN_INVALID")
    publisher_role = _PUBLISHER_BY_DIRECTION[direction]
    receiver_role = _RECEIVER_BY_DIRECTION[direction]
    if item["publisher_role"] != publisher_role or item["receiver_role"] != receiver_role:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_DIRECTION_CHAIN_INVALID")
    for field in ("publisher_observation_sha256", "witness_forward_sha256", "receiver_observation_sha256"):
        _sha256(item[field], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_DIRECTION_CHAIN_INVALID")
    _require_locator(
        item["probe_locator"],
        binding=binding,
        nonce=nonce,
        publisher_role=publisher_role,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_PROBE_SELECTOR_INVALID",
    )
    return item


def verify_physical_arvan_s3_four_role_live_iam_witness_aggregate(
    raw: bytes,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    ledger: PhysicalArvanS3FourRoleLiveIamNonceLedger,
    witness_public_key: bytes,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate:
    """Verify a committed aggregate only against the exact durable nonce state.

    The aggregate is rejected if the provided ledger does not contain the
    one-time committed record for the raw receipt.  A runtime must persist the
    returned state atomically; this pure verifier intentionally does not
    perform that filesystem/database work itself.
    """

    checked = _require_binding(binding)
    current = _require_ledger(ledger, binding=checked)
    sealed = _exact_mapping(_parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID"), fields=_AGGREGATE_FIELDS, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    if sealed["schema"] != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_EVIDENCE_SCHEMA or sealed["kind"] != _AGGREGATE_KIND:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    nonce = _nonce(sealed["nonce"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    issued_at = _parse_timestamp(sealed["issued_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    expires_at = _parse_timestamp(sealed["expires_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    committed_at = _parse_timestamp(sealed["committed_at"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    _validate_window(issued_at=issued_at, expires_at=expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    if committed_at < issued_at or committed_at >= expires_at:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    _require_now_in_window(now=observed_at, issued_at=issued_at, expires_at=expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_STALE")
    if (
        _sha256(sealed["evidence_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID") != checked.evidence_binding_sha256
        or _sha256(sealed["normal_route_scope_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID") != checked.normal_route_scope_sha256
        or _sha256(sealed["reverse_route_scope_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID") != checked.reverse_route_scope_sha256
        or _sha256(sealed["four_role_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID") != checked.four_role_binding_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_ROUTE_MISMATCH")
    _validate_role_matrix(sealed["role_matrix"], binding=checked)
    normal = _parse_direction_evidence(sealed["normal_direction"], binding=checked, nonce=nonce, direction=_DIRECTION_NORMAL)
    reverse = _parse_direction_evidence(sealed["reverse_direction"], binding=checked, nonce=nonce, direction=_DIRECTION_REVERSE)
    prior_ledger_sha256 = _sha256(sealed["prior_ledger_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    commitment = _sha256(sealed["nonce_commitment_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_INVALID")
    if commitment != _nonce_commitment(
        binding=checked,
        nonce=nonce,
        prior_ledger_sha256=prior_ledger_sha256,
        committed_at=sealed["committed_at"],
        normal=normal,
        reverse=reverse,
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_COMMIT_INVALID")
    _verify_signature(
        sealed=sealed,
        signer_field="witness_signer",
        signature_field="witness_signature",
        expected_public_key=witness_public_key,
        kind=_AGGREGATE_KIND,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_SIGNATURE_INVALID",
    )
    raw_sha256 = _sha256_bytes(raw)
    matching = [record for record in current.records if record.nonce == nonce]
    if (
        len(matching) != 1
        or matching[0].status != _LEDGER_COMMITTED
        or matching[0].issued_at != sealed["issued_at"]
        or matching[0].expires_at != sealed["expires_at"]
        or matching[0].commit_prior_ledger_sha256 != prior_ledger_sha256
        or matching[0].nonce_commitment_sha256 != commitment
        or matching[0].aggregate_sha256 != raw_sha256
        or matching[0].committed_at != sealed["committed_at"]
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_NONCE_COMMIT_MISSING")
    verified = VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate(
        nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        committed_at=committed_at,
        evidence_binding_sha256=checked.evidence_binding_sha256,
        nonce_commitment_sha256=commitment,
        raw_sha256=raw_sha256,
    )
    object.__setattr__(verified, "_capability", _VERIFIED_AGGREGATE_CAPABILITY)
    return verified


def require_verified_physical_arvan_s3_four_role_live_iam_witness_aggregate(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate:
    """Revalidate the opaque final result for a readiness-only consumer.

    A readiness integration must first call
    :func:`verify_physical_arvan_s3_four_role_live_iam_witness_aggregate`
    against its durable Witness nonce ledger.  It may then retain only this
    typed result.  Direct construction and ``dataclasses.replace`` do not
    carry the verifier capability and fail closed here.
    """

    checked = _require_binding(binding)
    if (
        type(value) is not VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate
        or value._capability is not _VERIFIED_AGGREGATE_CAPABILITY
        or value.evidence_binding_sha256 != checked.evidence_binding_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_NOT_VERIFIED")
    _require_now_in_window(
        now=observed_at,
        issued_at=value.issued_at,
        expires_at=value.expires_at,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_AGGREGATE_STALE",
    )
    return value
