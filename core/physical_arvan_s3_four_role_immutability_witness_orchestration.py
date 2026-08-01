"""Signed Witness-mediated orchestration for the four role-local S3 probes.

The old injected live-probe runtime is useful for unit tests, but it cannot be
used as the physical four-host execution path: its four callbacks would put
the FI and IR roles in one process.  This module supplies the deliberately
small, transport-free wire grammar for the physical path instead.

Only a Witness can issue a bounded request.  WA-FI and WA-IR can only verify
such a request, run *their one local collector*, and return a signed semantic
receipt.  The Witness verifies that receipt before it issues the next one.
There is intentionally no FI-to-IR/IR-to-FI endpoint, address, client,
socket, subprocess, credential, or provider operation in this module.

The production delivery mechanism is an out-of-process, Witness-mediated
inbox/outbox concern.  It must carry these opaque bytes without changing the
grammar; a local role must never accept a peer-supplied request merely because
it was delivered over a trusted transport.
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

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_arvan_s3_four_role_immutability_live_probe_runtime as _probe
from core import physical_arvan_s3_four_role_immutability_preflight as _immutability
from core import physical_arvan_s3_four_role_live_iam_durable_admission_bridge as _admission
from core import physical_arvan_s3_four_role_live_iam_evidence as _live_iam
from core import physical_arvan_s3_role_profiles as _profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as _failback


__all__ = (
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA",
    "PhysicalArvanS3FourRoleImmutabilityWitnessApproval",
    "PhysicalArvanS3FourRoleImmutabilityWitnessBinding",
    "PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError",
    "VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt",
    "VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval",
    "build_physical_arvan_s3_four_role_immutability_witness_binding",
    "build_physical_arvan_s3_four_role_immutability_witness_mediated_preflight_observation",
    "issue_physical_arvan_s3_four_role_immutability_initial_witness_approval",
    "issue_physical_arvan_s3_four_role_immutability_next_witness_approval",
    "seal_physical_arvan_s3_four_role_immutability_role_receipt",
    "verify_physical_arvan_s3_four_role_immutability_role_receipt",
    "verify_physical_arvan_s3_four_role_immutability_witness_approval",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-immutability-witness-orchestration-v1"
)

_APPROVAL_KIND = "witness-approved-role-local-request"
_RECEIPT_KIND = "role-local-collector-receipt"
_MAX_WIRE_BYTES = 128 * 1024
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)

_ROLE_ORDER = (
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE,
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE,
)
_NEXT_ROLE = dict(zip(_ROLE_ORDER, _ROLE_ORDER[1:]))
_PUBLISHER_ROLES = frozenset(
    {
        _profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
        _profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
    }
)
_RECEIVER_ROLES = frozenset(
    {
        _profiles.ARVAN_S3_IR_RECEIVER_ROLE,
        _profiles.ARVAN_S3_FI_RECEIVER_ROLE,
    }
)
_ROLE_IDENTITY_FIELD = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: "fi_publisher_identity_sha256",
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: "ir_receiver_identity_sha256",
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: "ir_publisher_identity_sha256",
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: "fi_receiver_identity_sha256",
}
_ROLE_SIGNER_FIELD = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: "fi_publisher_signer_public_key",
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: "ir_receiver_signer_public_key",
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: "ir_publisher_signer_public_key",
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: "fi_receiver_signer_public_key",
}

_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_BUCKET_FIELDS = frozenset({"acl_posture", "versioning_status", "retention_mode", "retention_days"})
_VERSION_FIELDS = frozenset(
    {
        "probe_nonce_sha256",
        "object_key",
        "object_version_id",
        "content_sha256",
        "content_bytes",
        "retention_until",
        "exact_head_version_id",
        "exact_get_version_id",
        "exact_get_content_sha256",
        "exact_get_content_bytes",
    }
)
_PUBLISHER_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "direction",
        "role",
        "identity_sha256",
        "campaign_id",
        "release_sha",
        "endpoint",
        "region",
        "bucket",
        "object_storage_namespace",
        "probe_nonce_sha256",
        "object_key",
        "observed_at",
        "minimum_retention_days",
        "retention_not_before",
    }
)
_RECEIVER_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "direction",
        "role",
        "identity_sha256",
        "campaign_id",
        "release_sha",
        "endpoint",
        "region",
        "bucket",
        "object_storage_namespace",
        "immutable_version",
        "observed_at",
        "retention_not_before",
    }
)
_PUBLISHER_READBACK_FIELDS = frozenset(
    {
        "schema",
        "direction",
        "role",
        "identity_sha256",
        "probe_nonce_sha256",
        "object_key",
        "object_version_id",
        "content_sha256",
        "content_bytes",
        "retention_until",
        "create_only_outcome",
        "overwrite_outcome",
        "object_removal_outcome",
        "version_removal_outcome",
        "bucket_readback",
    }
)
_RECEIVER_READBACK_FIELDS = frozenset(
    {
        "schema",
        "direction",
        "role",
        "identity_sha256",
        "probe_nonce_sha256",
        "object_key",
        "object_version_id",
        "exact_head_version_id",
        "exact_get_version_id",
        "exact_get_content_sha256",
        "exact_get_content_bytes",
        "put_outcome",
        "object_removal_outcome",
        "version_removal_outcome",
        "bucket_enumeration_outcome",
        "version_enumeration_outcome",
    }
)
_APPROVAL_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "operation_nonce_sha256",
        "stage",
        "issued_at",
        "expires_at",
        "orchestration_binding_sha256",
        "admission_aggregate_sha256",
        "admission_durable_ledger_head_sha256",
        "prior_receipt_sha256",
        "normal_publisher_receipt_sha256",
        "shared_bucket_readback",
        "retention_floor_publisher_issued_at",
        "request",
        "witness_signer",
        "witness_signature",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "operation_nonce_sha256",
        "stage",
        "observed_at",
        "expires_at",
        "orchestration_binding_sha256",
        "admission_aggregate_sha256",
        "admission_durable_ledger_head_sha256",
        "approval_sha256",
        "prior_receipt_sha256",
        "normal_publisher_receipt_sha256",
        "shared_bucket_readback",
        "retention_floor_publisher_issued_at",
        "request_sha256",
        "readback",
        "role_signer",
        "role_signature",
    }
)

_CAPABILITY_APPROVAL = object()
_CAPABILITY_RECEIPT = object()


class PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError(ValueError):
    """A signed cross-host immutable-probe transition is unsafe or stale."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityWitnessBinding:
    """Non-secret pins for a Witness-mediated four-role run."""

    schema: str
    preflight_binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding
    witness_public_key: bytes = field(repr=False)
    orchestration_binding_sha256: str


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityWitnessApproval:
    """Decoded, non-authorizing view of a Witness request envelope."""

    operation_nonce_sha256: str
    stage: str
    issued_at: datetime
    expires_at: datetime
    admission_aggregate_sha256: str
    admission_durable_ledger_head_sha256: str
    prior_receipt_sha256: str | None
    normal_publisher_receipt_sha256: str | None
    shared_bucket_readback: _probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback | None
    retention_floor_publisher_issued_at: datetime
    request: (
        _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
        | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest
    )
    raw_sha256: str
    witness_key_id: str


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval:
    """Opaque capability minted only after a fresh pinned Witness signature."""

    approval: PhysicalArvanS3FourRoleImmutabilityWitnessApproval
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt:
    """Opaque signed outcome from exactly one local machine role."""

    operation_nonce_sha256: str
    stage: str
    observed_at: datetime
    expires_at: datetime
    orchestration_binding_sha256: str
    admission_aggregate_sha256: str
    admission_durable_ledger_head_sha256: str
    approval_sha256: str
    prior_receipt_sha256: str | None
    normal_publisher_receipt_sha256: str | None
    shared_bucket_readback: _probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback | None
    retention_floor_publisher_issued_at: datetime
    request: (
        _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
        | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest
    )
    readback: (
        _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback
        | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback
    )
    raw_sha256: str
    role_key_id: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError(code)


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


def _utc(value: object, *, code: str, whole_seconds: bool = True) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if whole_seconds and result.microsecond != 0:
        _fail(code)
    return result


def _timestamp(value: object, *, code: str) -> str:
    return _utc(value, code=code).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail(code)


def _require_fresh(*, now: object, issued_at: datetime, expires_at: datetime, code: str) -> datetime:
    observed = _utc(now, code=code, whole_seconds=False)
    if expires_at <= issued_at or observed < issued_at or observed >= expires_at:
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

        result = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError):
        _fail(code)
    return _require_public_key(result, code=code)


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
    public_key = _require_public_key(
        _decode_base64(item["public_key_base64"], expected_bytes=32, code=code), code=code
    )
    if item["key_id"] != _key_id(public_key):
        _fail(code)
    return public_key, item["key_id"]


def _signature_record(value: bytes) -> dict[str, str]:
    if type(value) is not bytes or len(value) != 64:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_SIGNATURE_INVALID")
    return {"algorithm": "ed25519", "signature_base64": base64.b64encode(value).decode("ascii")}


def _parse_signature(value: object, *, code: str) -> bytes:
    item = _exact_mapping(value, fields=_SIGNATURE_FIELDS, code=code)
    if item["algorithm"] != "ed25519":
        _fail(code)
    return _decode_base64(item["signature_base64"], expected_bytes=64, code=code)


def _signed_bytes(*, kind: str, unsigned: Mapping[str, Any]) -> bytes:
    return (
        PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA.encode("ascii")
        + b"/"
        + kind.encode("ascii")
        + b"\x00"
        + _canonical(unsigned, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_CANONICAL_INVALID")
    )


def _seal(
    *,
    unsigned: dict[str, Any],
    signer: object,
    signer_field: str,
    signature_field: str,
    kind: str,
) -> bytes:
    public_key = _public_key_from_signer(
        signer, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_SIGNER_INVALID"
    )
    unsigned[signer_field] = _signer_record(public_key)
    try:
        signature = signer.sign(_signed_bytes(kind=kind, unsigned=unsigned))
    except (AttributeError, TypeError, ValueError):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_SIGNER_INVALID")
    return _canonical(
        {**unsigned, signature_field: _signature_record(signature)},
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_CANONICAL_INVALID",
    )


def _verify_signature(
    *,
    sealed: dict[str, Any],
    signer_field: str,
    signature_field: str,
    expected_public_key: bytes,
    kind: str,
    code: str,
) -> str:
    actual, key_id = _parse_signer(sealed[signer_field], code=code)
    expected = _require_public_key(expected_public_key, code=code)
    if actual != expected:
        _fail(code)
    signature = _parse_signature(sealed[signature_field], code=code)
    unsigned = {key: value for key, value in sealed.items() if key != signature_field}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(expected).verify(
            signature, _signed_bytes(kind=kind, unsigned=unsigned)
        )
    except ImportError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ED25519_UNAVAILABLE")
    except InvalidSignature:
        _fail(code)
    return key_id


def _require_signer_matches(*, signer: object, expected_public_key: bytes, code: str) -> None:
    if _public_key_from_signer(signer, code=code) != _require_public_key(expected_public_key, code=code):
        _fail(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_WIRE_INVALID")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    del value
    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_WIRE_INVALID")


def _parse_wire(raw: object, *, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_WIRE_BYTES:
        _fail(code)
    try:
        value = json.loads(
            raw.decode("ascii"), object_pairs_hook=_strict_object, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        _fail(code)
    if not isinstance(value, dict) or _canonical(value, code=code) != raw:
        _fail(code)
    return value


def _role_identity(
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding, role: str
) -> str:
    return getattr(binding.preflight_binding, _ROLE_IDENTITY_FIELD[role])


def _role_public_key(
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding, role: str
) -> bytes:
    return getattr(binding.live_iam_binding, _ROLE_SIGNER_FIELD[role])


def _binding_payload(
    *,
    preflight_binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    witness_public_key: bytes,
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA,
        "campaign_id": preflight_binding.campaign_id,
        "release_sha": preflight_binding.release_sha,
        "normal_route_scope_sha256": preflight_binding.normal_route_scope_sha256,
        "reverse_route_scope_sha256": preflight_binding.reverse_route_scope_sha256,
        "four_role_route_binding_sha256": preflight_binding.four_role_route_binding_sha256,
        "live_iam_evidence_binding_sha256": live_iam_binding.evidence_binding_sha256,
        "failback_route_binding_sha256": failback_binding.route_binding_sha256,
        "witness_key_id": _key_id(witness_public_key),
        "roles": {
            role: {
                "identity_sha256": getattr(preflight_binding, _ROLE_IDENTITY_FIELD[role]),
                "signer_key_id": _key_id(getattr(live_iam_binding, _ROLE_SIGNER_FIELD[role])),
            }
            for role in _ROLE_ORDER
        },
    }


def _bindings_match(
    *,
    preflight_binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
) -> None:
    pairs = (
        (preflight_binding.campaign_id, live_iam_binding.campaign_id),
        (preflight_binding.release_sha, live_iam_binding.release_sha),
        (preflight_binding.normal_route_scope_sha256, live_iam_binding.normal_route_scope_sha256),
        (preflight_binding.reverse_route_scope_sha256, live_iam_binding.reverse_route_scope_sha256),
        (preflight_binding.four_role_route_binding_sha256, live_iam_binding.four_role_binding_sha256),
        (preflight_binding.campaign_id, failback_binding.campaign_id),
        (preflight_binding.release_sha, failback_binding.release_sha),
        (preflight_binding.normal_route_scope_sha256, failback_binding.normal_route_scope_sha256),
        (preflight_binding.reverse_route_scope_sha256, failback_binding.reverse_route_scope_sha256),
        (preflight_binding.four_role_route_binding_sha256, failback_binding.route_binding_sha256),
    )
    if any(left != right for left, right in pairs):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_MISMATCH")
    for role in _ROLE_ORDER:
        identity = getattr(preflight_binding, _ROLE_IDENTITY_FIELD[role])
        if identity != getattr(live_iam_binding, _ROLE_IDENTITY_FIELD[role]) or identity != getattr(
            failback_binding, _ROLE_IDENTITY_FIELD[role]
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_MISMATCH")


def _binding(value: object) -> PhysicalArvanS3FourRoleImmutabilityWitnessBinding:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityWitnessBinding:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID")
    binding = value
    if binding.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID")
    try:
        preflight = _immutability._binding(binding.preflight_binding)
        live = _live_iam._require_binding(binding.live_iam_binding)
        failback = _failback.validate_physical_ir_to_fi_object_storage_failback_binding(
            binding.failback_binding
        )
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID")
    witness = _require_public_key(
        binding.witness_public_key, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID"
    )
    _bindings_match(
        preflight_binding=preflight, live_iam_binding=live, failback_binding=failback
    )
    expected = _sha256_bytes(
        _canonical(
            _binding_payload(
                preflight_binding=preflight,
                live_iam_binding=live,
                failback_binding=failback,
                witness_public_key=witness,
            ),
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID",
        )
    )
    if _sha256(
        binding.orchestration_binding_sha256,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID",
    ) != expected:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID")
    return binding


def build_physical_arvan_s3_four_role_immutability_witness_binding(
    *,
    preflight_binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    witness_public_key: bytes,
) -> PhysicalArvanS3FourRoleImmutabilityWitnessBinding:
    """Bind four local signers to one Witness and one admitted route."""

    try:
        preflight = _immutability._binding(preflight_binding)
        live = _live_iam._require_binding(live_iam_binding)
        failback = _failback.validate_physical_ir_to_fi_object_storage_failback_binding(
            failback_binding
        )
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID")
    witness = _require_public_key(
        witness_public_key, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID"
    )
    _bindings_match(preflight_binding=preflight, live_iam_binding=live, failback_binding=failback)
    digest = _sha256_bytes(
        _canonical(
            _binding_payload(
                preflight_binding=preflight,
                live_iam_binding=live,
                failback_binding=failback,
                witness_public_key=witness,
            ),
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_BINDING_INVALID",
        )
    )
    result = PhysicalArvanS3FourRoleImmutabilityWitnessBinding(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA,
        preflight_binding=preflight,
        live_iam_binding=live,
        failback_binding=failback,
        witness_public_key=witness,
        orchestration_binding_sha256=digest,
    )
    return _binding(result)


def _bucket_to_wire(
    value: _probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback,
) -> dict[str, Any]:
    return {
        "acl_posture": value.acl_posture,
        "versioning_status": value.versioning_status,
        "retention_mode": value.retention_mode,
        "retention_days": value.retention_days,
    }


def _bucket_from_wire(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    code: str,
) -> _probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback:
    item = _exact_mapping(value, fields=_BUCKET_FIELDS, code=code)
    candidate = _probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback(**item)
    try:
        return _probe._bucket_readback(candidate, binding=binding.preflight_binding)
    except _probe.PhysicalArvanS3FourRoleImmutabilityLiveProbeError:
        _fail(code)


def _version_to_wire(value: _immutability.PhysicalArvanS3FourRoleImmutableVersionObservation) -> dict[str, Any]:
    return {
        "probe_nonce_sha256": value.probe_nonce_sha256,
        "object_key": value.object_key,
        "object_version_id": value.object_version_id,
        "content_sha256": value.content_sha256,
        "content_bytes": value.content_bytes,
        "retention_until": _timestamp(
            value.retention_until, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_VERSION_INVALID"
        ),
        "exact_head_version_id": value.exact_head_version_id,
        "exact_get_version_id": value.exact_get_version_id,
        "exact_get_content_sha256": value.exact_get_content_sha256,
        "exact_get_content_bytes": value.exact_get_content_bytes,
    }


def _version_from_wire(value: object, *, code: str) -> _immutability.PhysicalArvanS3FourRoleImmutableVersionObservation:
    item = _exact_mapping(value, fields=_VERSION_FIELDS, code=code)
    return _immutability.PhysicalArvanS3FourRoleImmutableVersionObservation(
        probe_nonce_sha256=item["probe_nonce_sha256"],
        object_key=item["object_key"],
        object_version_id=item["object_version_id"],
        content_sha256=item["content_sha256"],
        content_bytes=item["content_bytes"],
        retention_until=_parse_timestamp(item["retention_until"], code=code),
        exact_head_version_id=item["exact_head_version_id"],
        exact_get_version_id=item["exact_get_version_id"],
        exact_get_content_sha256=item["exact_get_content_sha256"],
        exact_get_content_bytes=item["exact_get_content_bytes"],
    )


def _request_to_wire(
    value: (
        _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
        | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest
    ),
) -> dict[str, Any]:
    if type(value) is _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest:
        return {
            "schema": value.schema,
            "direction": value.direction,
            "role": value.role,
            "identity_sha256": value.identity_sha256,
            "campaign_id": value.campaign_id,
            "release_sha": value.release_sha,
            "endpoint": value.endpoint,
            "region": value.region,
            "bucket": value.bucket,
            "object_storage_namespace": value.object_storage_namespace,
            "probe_nonce_sha256": value.probe_nonce_sha256,
            "object_key": value.object_key,
            "observed_at": _timestamp(value.observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_REQUEST_INVALID"),
            "minimum_retention_days": value.minimum_retention_days,
            "retention_not_before": _timestamp(value.retention_not_before, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_REQUEST_INVALID"),
        }
    if type(value) is _probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest:
        return {
            "schema": value.schema,
            "direction": value.direction,
            "role": value.role,
            "identity_sha256": value.identity_sha256,
            "campaign_id": value.campaign_id,
            "release_sha": value.release_sha,
            "endpoint": value.endpoint,
            "region": value.region,
            "bucket": value.bucket,
            "object_storage_namespace": value.object_storage_namespace,
            "immutable_version": _version_to_wire(value.immutable_version),
            "observed_at": _timestamp(value.observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_REQUEST_INVALID"),
            "retention_not_before": _timestamp(value.retention_not_before, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_REQUEST_INVALID"),
        }
    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_REQUEST_INVALID")


def _request_from_wire(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    stage: str,
    shared_bucket_readback: _probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback | None,
    code: str,
) -> (
    _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
    | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest
):
    if stage in _PUBLISHER_ROLES:
        item = _exact_mapping(value, fields=_PUBLISHER_REQUEST_FIELDS, code=code)
        observed = _parse_timestamp(item["observed_at"], code=code)
        floor = _parse_timestamp(item["retention_not_before"], code=code)
        request = _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest(
            schema=item["schema"],
            direction=item["direction"],
            role=item["role"],
            identity_sha256=item["identity_sha256"],
            campaign_id=item["campaign_id"],
            release_sha=item["release_sha"],
            endpoint=item["endpoint"],
            region=item["region"],
            bucket=item["bucket"],
            object_storage_namespace=item["object_storage_namespace"],
            probe_nonce_sha256=item["probe_nonce_sha256"],
            object_key=item["object_key"],
            observed_at=observed,
            minimum_retention_days=item["minimum_retention_days"],
            retention_not_before=floor,
        )
        try:
            expected = _probe._publisher_request(
                binding=binding.preflight_binding,
                role=stage,
                identity_sha256=_role_identity(binding, stage),
                nonce=_sha256(request.probe_nonce_sha256, code=code),
                observed_at=observed,
                retention_not_before=floor,
            )
        except (KeyError, _probe.PhysicalArvanS3FourRoleImmutabilityLiveProbeError):
            _fail(code)
        if request != expected or (stage == _profiles.ARVAN_S3_IR_PUBLISHER_ROLE and shared_bucket_readback is None):
            _fail(code)
        return request
    item = _exact_mapping(value, fields=_RECEIVER_REQUEST_FIELDS, code=code)
    observed = _parse_timestamp(item["observed_at"], code=code)
    floor = _parse_timestamp(item["retention_not_before"], code=code)
    version = _version_from_wire(item["immutable_version"], code=code)
    request = _probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest(
        schema=item["schema"],
        direction=item["direction"],
        role=item["role"],
        identity_sha256=item["identity_sha256"],
        campaign_id=item["campaign_id"],
        release_sha=item["release_sha"],
        endpoint=item["endpoint"],
        region=item["region"],
        bucket=item["bucket"],
        object_storage_namespace=item["object_storage_namespace"],
        immutable_version=version,
        observed_at=observed,
        retention_not_before=floor,
    )
    if shared_bucket_readback is None:
        _fail(code)
    try:
        _immutability._version(
            version,
            binding=binding.preflight_binding,
            direction=request.direction,
            observed_at=floor - timedelta(days=shared_bucket_readback.retention_days),
            retention_days=shared_bucket_readback.retention_days,
        )
        expected = _probe._receiver_request(
            binding=binding.preflight_binding,
            role=stage,
            identity_sha256=_role_identity(binding, stage),
            immutable_version=version,
            observed_at=observed,
            retention_not_before=floor,
        )
    except (
        KeyError,
        _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightError,
        _probe.PhysicalArvanS3FourRoleImmutabilityLiveProbeError,
    ):
        _fail(code)
    if request != expected:
        _fail(code)
    return request


def _readback_to_wire(
    value: (
        _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback
        | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback
    ),
) -> dict[str, Any]:
    if type(value) is _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback:
        return {
            "schema": value.schema,
            "direction": value.direction,
            "role": value.role,
            "identity_sha256": value.identity_sha256,
            "probe_nonce_sha256": value.probe_nonce_sha256,
            "object_key": value.object_key,
            "object_version_id": value.object_version_id,
            "content_sha256": value.content_sha256,
            "content_bytes": value.content_bytes,
            "retention_until": _timestamp(value.retention_until, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_READBACK_INVALID"),
            "create_only_outcome": value.create_only_outcome,
            "overwrite_outcome": value.overwrite_outcome,
            "object_removal_outcome": value.object_removal_outcome,
            "version_removal_outcome": value.version_removal_outcome,
            "bucket_readback": None if value.bucket_readback is None else _bucket_to_wire(value.bucket_readback),
        }
    if type(value) is _probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback:
        return {
            "schema": value.schema,
            "direction": value.direction,
            "role": value.role,
            "identity_sha256": value.identity_sha256,
            "probe_nonce_sha256": value.probe_nonce_sha256,
            "object_key": value.object_key,
            "object_version_id": value.object_version_id,
            "exact_head_version_id": value.exact_head_version_id,
            "exact_get_version_id": value.exact_get_version_id,
            "exact_get_content_sha256": value.exact_get_content_sha256,
            "exact_get_content_bytes": value.exact_get_content_bytes,
            "put_outcome": value.put_outcome,
            "object_removal_outcome": value.object_removal_outcome,
            "version_removal_outcome": value.version_removal_outcome,
            "bucket_enumeration_outcome": value.bucket_enumeration_outcome,
            "version_enumeration_outcome": value.version_enumeration_outcome,
        }
    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_READBACK_INVALID")


def _readback_from_wire(
    value: object,
    *,
    request: (
        _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
        | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest
    ),
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    shared_bucket_readback: _probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback | None,
    code: str,
) -> (
    _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback
    | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback
):
    if type(request) is _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest:
        item = _exact_mapping(value, fields=_PUBLISHER_READBACK_FIELDS, code=code)
        bucket_value = item["bucket_readback"]
        if bucket_value is None:
            bucket = None
        else:
            bucket = _bucket_from_wire(bucket_value, binding=binding, code=code)
        readback = _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback(
            schema=item["schema"],
            direction=item["direction"],
            role=item["role"],
            identity_sha256=item["identity_sha256"],
            probe_nonce_sha256=item["probe_nonce_sha256"],
            object_key=item["object_key"],
            object_version_id=item["object_version_id"],
            content_sha256=item["content_sha256"],
            content_bytes=item["content_bytes"],
            retention_until=_parse_timestamp(item["retention_until"], code=code),
            create_only_outcome=item["create_only_outcome"],
            overwrite_outcome=item["overwrite_outcome"],
            object_removal_outcome=item["object_removal_outcome"],
            version_removal_outcome=item["version_removal_outcome"],
            bucket_readback=bucket,
        )
        require_bucket = request.role == _profiles.ARVAN_S3_FI_PUBLISHER_ROLE
        try:
            checked, derived_bucket = _probe._publisher_readback(
                readback,
                request=request,
                binding=binding.preflight_binding,
                require_bucket_readback=require_bucket,
                bucket_readback=shared_bucket_readback,
            )
        except _probe.PhysicalArvanS3FourRoleImmutabilityLiveProbeError:
            _fail(code)
        if require_bucket and derived_bucket != bucket:
            _fail(code)
        return checked
    item = _exact_mapping(value, fields=_RECEIVER_READBACK_FIELDS, code=code)
    readback = _probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback(
        schema=item["schema"],
        direction=item["direction"],
        role=item["role"],
        identity_sha256=item["identity_sha256"],
        probe_nonce_sha256=item["probe_nonce_sha256"],
        object_key=item["object_key"],
        object_version_id=item["object_version_id"],
        exact_head_version_id=item["exact_head_version_id"],
        exact_get_version_id=item["exact_get_version_id"],
        exact_get_content_sha256=item["exact_get_content_sha256"],
        exact_get_content_bytes=item["exact_get_content_bytes"],
        put_outcome=item["put_outcome"],
        object_removal_outcome=item["object_removal_outcome"],
        version_removal_outcome=item["version_removal_outcome"],
        bucket_enumeration_outcome=item["bucket_enumeration_outcome"],
        version_enumeration_outcome=item["version_enumeration_outcome"],
    )
    try:
        return _probe._receiver_readback(readback, request=request)
    except _probe.PhysicalArvanS3FourRoleImmutabilityLiveProbeError:
        _fail(code)


def _approval_unsigned(
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    operation_nonce_sha256: str,
    stage: str,
    issued_at: datetime,
    expires_at: datetime,
    admission_aggregate_sha256: str,
    admission_durable_ledger_head_sha256: str,
    prior_receipt_sha256: str | None,
    normal_publisher_receipt_sha256: str | None,
    shared_bucket_readback: _probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback | None,
    retention_floor_publisher_issued_at: datetime,
    request: (
        _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
        | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest
    ),
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA,
        "kind": _APPROVAL_KIND,
        "operation_nonce_sha256": operation_nonce_sha256,
        "stage": stage,
        "issued_at": _timestamp(issued_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID"),
        "expires_at": _timestamp(expires_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID"),
        "orchestration_binding_sha256": binding.orchestration_binding_sha256,
        "admission_aggregate_sha256": admission_aggregate_sha256,
        "admission_durable_ledger_head_sha256": admission_durable_ledger_head_sha256,
        "prior_receipt_sha256": prior_receipt_sha256,
        "normal_publisher_receipt_sha256": normal_publisher_receipt_sha256,
        "shared_bucket_readback": None
        if shared_bucket_readback is None
        else _bucket_to_wire(shared_bucket_readback),
        "retention_floor_publisher_issued_at": _timestamp(
            retention_floor_publisher_issued_at,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID",
        ),
        "request": _request_to_wire(request),
    }


def _require_admission(
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    admission: object,
    observed_at: datetime,
) -> _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
    try:
        return _admission.require_verified_physical_arvan_s3_four_role_live_iam_durable_admission(
            admission,
            live_iam_binding=binding.live_iam_binding,
            failback_binding=binding.failback_binding,
            observed_at=observed_at,
        )
    except _admission.PhysicalArvanS3FourRoleLiveIamDurableAdmissionError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ADMISSION_INVALID")


def _require_verified_approval(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval:
    if (
        type(value) is not VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval
        or value._capability is not _CAPABILITY_APPROVAL
        or value.binding != binding
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_NOT_VERIFIED")
    _require_fresh(
        now=observed_at,
        issued_at=value.approval.issued_at,
        expires_at=value.approval.expires_at,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_STALE",
    )
    return value


def _require_verified_receipt(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt:
    if (
        type(value) is not VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt
        or value._capability is not _CAPABILITY_RECEIPT
        or value.orchestration_binding_sha256 != binding.orchestration_binding_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_NOT_VERIFIED")
    _binding(binding)
    _require_fresh(
        now=observed_at,
        issued_at=value.observed_at,
        expires_at=value.expires_at,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_STALE",
    )
    return value


def issue_physical_arvan_s3_four_role_immutability_initial_witness_approval(
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    operation_nonce_sha256: str,
    normal_probe_nonce_sha256: str,
    issued_at: datetime,
    witness_signer: object,
) -> bytes:
    """Issue the only initial request: Witness -> WA-FI ``fi-publisher``.

    The caller supplies only commitments generated by its local root runner;
    it cannot choose a role, object key, endpoint, bucket, identity, or
    lifetime.  The lifetime is exactly the already verified durable-IAM gate.
    """

    checked = _binding(binding)
    issued = _utc(issued_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_TIME_INVALID")
    admitted = _require_admission(binding=checked, admission=admission, observed_at=issued)
    operation = _sha256(operation_nonce_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_OPERATION_INVALID")
    normal_nonce = _sha256(normal_probe_nonce_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_OPERATION_INVALID")
    if operation == normal_nonce:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_NONCE_COLLISION")
    role = _profiles.ARVAN_S3_FI_PUBLISHER_ROLE
    request = _probe._publisher_request(
        binding=checked.preflight_binding,
        role=role,
        identity_sha256=_role_identity(checked, role),
        nonce=normal_nonce,
        observed_at=issued,
    )
    _require_signer_matches(
        signer=witness_signer,
        expected_public_key=checked.witness_public_key,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_SIGNER_NOT_PINNED",
    )
    return _seal(
        unsigned=_approval_unsigned(
            binding=checked,
            operation_nonce_sha256=operation,
            stage=role,
            issued_at=issued,
            expires_at=admitted.expires_at,
            admission_aggregate_sha256=admitted.aggregate_sha256,
            admission_durable_ledger_head_sha256=admitted.durable_ledger_head_sha256,
            prior_receipt_sha256=None,
            normal_publisher_receipt_sha256=None,
            shared_bucket_readback=None,
            retention_floor_publisher_issued_at=issued,
            request=request,
        ),
        signer=witness_signer,
        signer_field="witness_signer",
        signature_field="witness_signature",
        kind=_APPROVAL_KIND,
    )


def _parse_approval(
    raw: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval:
    sealed = _exact_mapping(
        _parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID"),
        fields=_APPROVAL_FIELDS,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID",
    )
    if (
        sealed["schema"] != PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA
        or sealed["kind"] != _APPROVAL_KIND
        or sealed["stage"] not in _ROLE_ORDER
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
    operation = _sha256(sealed["operation_nonce_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
    issued = _parse_timestamp(sealed["issued_at"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
    expires = _parse_timestamp(sealed["expires_at"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
    _require_fresh(
        now=observed_at,
        issued_at=issued,
        expires_at=expires,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_STALE",
    )
    if (
        _sha256(sealed["orchestration_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
        != binding.orchestration_binding_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_BINDING_MISMATCH")
    aggregate = _sha256(sealed["admission_aggregate_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
    ledger = _sha256(sealed["admission_durable_ledger_head_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
    stage = sealed["stage"]
    prior = sealed["prior_receipt_sha256"]
    normal = sealed["normal_publisher_receipt_sha256"]
    if prior is not None:
        prior = _sha256(prior, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
    if normal is not None:
        normal = _sha256(normal, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
    bucket_value = sealed["shared_bucket_readback"]
    bucket = None if bucket_value is None else _bucket_from_wire(bucket_value, binding=binding, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID")
    if stage == _profiles.ARVAN_S3_FI_PUBLISHER_ROLE:
        if prior is not None or normal is not None or bucket is not None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_CHAIN_INVALID")
    else:
        if prior is None or normal is None or bucket is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_CHAIN_INVALID")
    floor_publisher_issued_at = _parse_timestamp(
        sealed["retention_floor_publisher_issued_at"],
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID",
    )
    if floor_publisher_issued_at > issued:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_CHAIN_INVALID")
    request = _request_from_wire(
        sealed["request"],
        binding=binding,
        stage=stage,
        shared_bucket_readback=bucket,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_INVALID",
    )
    # The floor and all selectors are issued at the Witness timestamp, never
    # re-anchored to an arbitrary receiver-local clock.
    if request.observed_at != issued:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_CHAIN_INVALID")
    expected_floor = floor_publisher_issued_at + timedelta(
        days=binding.preflight_binding.minimum_retention_days,
        seconds=_probe.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS,
    )
    if (
        request.retention_not_before != expected_floor
        or (
            stage in _PUBLISHER_ROLES
            and floor_publisher_issued_at != issued
        )
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_CHAIN_INVALID")
    key_id = _verify_signature(
        sealed=sealed,
        signer_field="witness_signer",
        signature_field="witness_signature",
        expected_public_key=binding.witness_public_key,
        kind=_APPROVAL_KIND,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_SIGNATURE_INVALID",
    )
    approval = PhysicalArvanS3FourRoleImmutabilityWitnessApproval(
        operation_nonce_sha256=operation,
        stage=stage,
        issued_at=issued,
        expires_at=expires,
        admission_aggregate_sha256=aggregate,
        admission_durable_ledger_head_sha256=ledger,
        prior_receipt_sha256=prior,
        normal_publisher_receipt_sha256=normal,
        shared_bucket_readback=bucket,
        retention_floor_publisher_issued_at=floor_publisher_issued_at,
        request=request,
        raw_sha256=_sha256_bytes(raw),
        witness_key_id=key_id,
    )
    result = VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval(
        approval=approval, binding=binding
    )
    object.__setattr__(result, "_capability", _CAPABILITY_APPROVAL)
    return result


def verify_physical_arvan_s3_four_role_immutability_witness_approval(
    raw: bytes,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval:
    """Verify a fresh Witness approval before touching the local collector."""

    return _parse_approval(raw, binding=_binding(binding), observed_at=_utc(observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_TIME_INVALID", whole_seconds=False))


def _receipt_unsigned(
    *,
    approval: PhysicalArvanS3FourRoleImmutabilityWitnessApproval,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    observed_at: datetime,
    readback: (
        _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback
        | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback
    ),
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA,
        "kind": _RECEIPT_KIND,
        "operation_nonce_sha256": approval.operation_nonce_sha256,
        "stage": approval.stage,
        "observed_at": _timestamp(observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID"),
        "expires_at": _timestamp(approval.expires_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID"),
        "orchestration_binding_sha256": binding.orchestration_binding_sha256,
        "admission_aggregate_sha256": approval.admission_aggregate_sha256,
        "admission_durable_ledger_head_sha256": approval.admission_durable_ledger_head_sha256,
        "approval_sha256": approval.raw_sha256,
        "prior_receipt_sha256": approval.prior_receipt_sha256,
        "normal_publisher_receipt_sha256": approval.normal_publisher_receipt_sha256,
        "shared_bucket_readback": None
        if approval.shared_bucket_readback is None
        else _bucket_to_wire(approval.shared_bucket_readback),
        "retention_floor_publisher_issued_at": _timestamp(
            approval.retention_floor_publisher_issued_at,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID",
        ),
        "request_sha256": _sha256_bytes(
            _canonical(_request_to_wire(approval.request), code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID")
        ),
        "readback": _readback_to_wire(readback),
    }


def seal_physical_arvan_s3_four_role_immutability_role_receipt(
    *,
    approval: VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    observed_at: datetime,
    local_readback: (
        _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback
        | _probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback
    ),
    role_signer: object,
) -> bytes:
    """Seal a semantic result after exactly one verified local request.

    This function is intentionally not a transport or a collector invoker.
    A root-owned host agent calls its one collector first, then supplies the
    result here.  A mismatch is rejected before the local signer can attest it.
    """

    checked = _binding(binding)
    observed = _utc(observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_TIME_INVALID")
    verified = _require_verified_approval(approval, binding=checked, observed_at=observed)
    approval_value = verified.approval
    readback = _readback_from_wire(
        _readback_to_wire(local_readback),
        request=approval_value.request,
        binding=checked,
        shared_bucket_readback=approval_value.shared_bucket_readback,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_READBACK_INVALID",
    )
    _require_signer_matches(
        signer=role_signer,
        expected_public_key=_role_public_key(checked, approval_value.stage),
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_SIGNER_NOT_PINNED",
    )
    return _seal(
        unsigned=_receipt_unsigned(
            approval=approval_value,
            binding=checked,
            observed_at=observed,
            readback=readback,
        ),
        signer=role_signer,
        signer_field="role_signer",
        signature_field="role_signature",
        kind=_RECEIPT_KIND,
    )


def _parse_receipt(
    raw: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    approval: VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt:
    verified_approval = _require_verified_approval(approval, binding=binding, observed_at=observed_at)
    approved = verified_approval.approval
    sealed = _exact_mapping(
        _parse_wire(raw, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID"),
        fields=_RECEIPT_FIELDS,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID",
    )
    if (
        sealed["schema"] != PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ORCHESTRATION_SCHEMA
        or sealed["kind"] != _RECEIPT_KIND
        or sealed["stage"] != approved.stage
        or _sha256(sealed["operation_nonce_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID") != approved.operation_nonce_sha256
        or _parse_timestamp(sealed["expires_at"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID") != approved.expires_at
        or _sha256(sealed["orchestration_binding_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID") != binding.orchestration_binding_sha256
        or _sha256(sealed["admission_aggregate_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID") != approved.admission_aggregate_sha256
        or _sha256(sealed["admission_durable_ledger_head_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID") != approved.admission_durable_ledger_head_sha256
        or _sha256(sealed["approval_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID") != approved.raw_sha256
        or _sha256(sealed["request_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID")
        != _sha256_bytes(_canonical(_request_to_wire(approved.request), code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID"))
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_BINDING_MISMATCH")
    observed = _parse_timestamp(sealed["observed_at"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID")
    if observed < approved.issued_at or observed >= approved.expires_at:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_STALE")
    for key, expected in (
        ("prior_receipt_sha256", approved.prior_receipt_sha256),
        ("normal_publisher_receipt_sha256", approved.normal_publisher_receipt_sha256),
    ):
        actual = sealed[key]
        if expected is None:
            if actual is not None:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_BINDING_MISMATCH")
        elif _sha256(actual, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID") != expected:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_BINDING_MISMATCH")
    shared = sealed["shared_bucket_readback"]
    if approved.shared_bucket_readback is None:
        if shared is not None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_BINDING_MISMATCH")
        bucket = None
    else:
        bucket = _bucket_from_wire(shared, binding=binding, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID")
        if bucket != approved.shared_bucket_readback:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_BINDING_MISMATCH")
    floor_publisher_issued_at = _parse_timestamp(
        sealed["retention_floor_publisher_issued_at"],
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_INVALID",
    )
    if floor_publisher_issued_at != approved.retention_floor_publisher_issued_at:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_BINDING_MISMATCH")
    readback = _readback_from_wire(
        sealed["readback"],
        request=approved.request,
        binding=binding,
        shared_bucket_readback=approved.shared_bucket_readback,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_READBACK_INVALID",
    )
    key_id = _verify_signature(
        sealed=sealed,
        signer_field="role_signer",
        signature_field="role_signature",
        expected_public_key=_role_public_key(binding, approved.stage),
        kind=_RECEIPT_KIND,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_RECEIPT_SIGNATURE_INVALID",
    )
    result = VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt(
        operation_nonce_sha256=approved.operation_nonce_sha256,
        stage=approved.stage,
        observed_at=observed,
        expires_at=approved.expires_at,
        orchestration_binding_sha256=binding.orchestration_binding_sha256,
        admission_aggregate_sha256=approved.admission_aggregate_sha256,
        admission_durable_ledger_head_sha256=approved.admission_durable_ledger_head_sha256,
        approval_sha256=approved.raw_sha256,
        prior_receipt_sha256=approved.prior_receipt_sha256,
        normal_publisher_receipt_sha256=approved.normal_publisher_receipt_sha256,
        shared_bucket_readback=bucket,
        retention_floor_publisher_issued_at=floor_publisher_issued_at,
        request=approved.request,
        readback=readback,
        raw_sha256=_sha256_bytes(raw),
        role_key_id=key_id,
    )
    object.__setattr__(result, "_capability", _CAPABILITY_RECEIPT)
    return result


def verify_physical_arvan_s3_four_role_immutability_role_receipt(
    raw: bytes,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    approval: VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt:
    """Verify a local receipt only in the exact Witness approval context."""

    return _parse_receipt(
        raw,
        binding=_binding(binding),
        approval=approval,
        observed_at=_utc(observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_TIME_INVALID", whole_seconds=False),
    )


def issue_physical_arvan_s3_four_role_immutability_next_witness_approval(
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    prior_receipt: VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt,
    issued_at: datetime,
    witness_signer: object,
    reverse_probe_nonce_sha256: str | None = None,
) -> bytes:
    """Advance exactly one receipt through the Witness-only state machine.

    ``fi-publisher -> ir-receiver -> ir-publisher -> fi-receiver`` is fixed.
    The caller cannot skip a receipt, select a remote peer, or change a
    selector.  A reverse publisher nonce is accepted only at the one stage
    where a new reverse immutable object must be created.
    """

    checked = _binding(binding)
    issued = _utc(issued_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_TIME_INVALID")
    prior = _require_verified_receipt(prior_receipt, binding=checked, observed_at=issued)
    if prior.stage not in _NEXT_ROLE:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_CHAIN_COMPLETE")
    next_role = _NEXT_ROLE[prior.stage]
    if prior.expires_at <= issued:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_APPROVAL_STALE")
    if prior.stage == _profiles.ARVAN_S3_FI_PUBLISHER_ROLE:
        if type(prior.readback) is not _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback or prior.readback.bucket_readback is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_CHAIN_INVALID")
        normal_publisher_sha = prior.raw_sha256
        bucket = prior.readback.bucket_readback
        floor_publisher_issued_at = prior.request.observed_at
        version = _probe._immutable_version(prior.readback)
        request = _probe._receiver_request(
            binding=checked.preflight_binding,
            role=next_role,
            identity_sha256=_role_identity(checked, next_role),
            immutable_version=version,
            observed_at=issued,
            retention_not_before=prior.request.retention_not_before,
        )
        if reverse_probe_nonce_sha256 is not None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_NONCE_UNEXPECTED")
    elif prior.stage == _profiles.ARVAN_S3_IR_RECEIVER_ROLE:
        if type(prior.readback) is not _probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback or prior.normal_publisher_receipt_sha256 is None or prior.shared_bucket_readback is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_CHAIN_INVALID")
        nonce = _sha256(reverse_probe_nonce_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_REVERSE_NONCE_REQUIRED")
        if nonce in {prior.operation_nonce_sha256, prior.readback.probe_nonce_sha256}:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_NONCE_COLLISION")
        normal_publisher_sha = prior.normal_publisher_receipt_sha256
        bucket = prior.shared_bucket_readback
        floor_publisher_issued_at = issued
        request = _probe._publisher_request(
            binding=checked.preflight_binding,
            role=next_role,
            identity_sha256=_role_identity(checked, next_role),
            nonce=nonce,
            observed_at=issued,
        )
    else:
        if type(prior.readback) is not _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback or prior.normal_publisher_receipt_sha256 is None or prior.shared_bucket_readback is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_CHAIN_INVALID")
        if reverse_probe_nonce_sha256 is not None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_NONCE_UNEXPECTED")
        normal_publisher_sha = prior.normal_publisher_receipt_sha256
        bucket = prior.shared_bucket_readback
        floor_publisher_issued_at = prior.request.observed_at
        request = _probe._receiver_request(
            binding=checked.preflight_binding,
            role=next_role,
            identity_sha256=_role_identity(checked, next_role),
            immutable_version=_probe._immutable_version(prior.readback),
            observed_at=issued,
            retention_not_before=prior.request.retention_not_before,
        )
    _require_signer_matches(
        signer=witness_signer,
        expected_public_key=checked.witness_public_key,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_SIGNER_NOT_PINNED",
    )
    return _seal(
        unsigned=_approval_unsigned(
            binding=checked,
            operation_nonce_sha256=prior.operation_nonce_sha256,
            stage=next_role,
            issued_at=issued,
            expires_at=prior.expires_at,
            admission_aggregate_sha256=prior.admission_aggregate_sha256,
            admission_durable_ledger_head_sha256=prior.admission_durable_ledger_head_sha256,
            prior_receipt_sha256=prior.raw_sha256,
            normal_publisher_receipt_sha256=normal_publisher_sha,
            shared_bucket_readback=bucket,
            retention_floor_publisher_issued_at=floor_publisher_issued_at,
            request=request,
        ),
        signer=witness_signer,
        signer_field="witness_signer",
        signature_field="witness_signature",
        kind=_APPROVAL_KIND,
    )


def _require_receipt_stage(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    expected_role: str,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt:
    receipt = _require_verified_receipt(value, binding=binding, observed_at=observed_at)
    if receipt.stage != expected_role:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_AGGREGATE_CHAIN_INVALID")
    return receipt


def _same_run(
    *,
    receipts: tuple[VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt, ...],
) -> None:
    first = receipts[0]
    if any(
        item.operation_nonce_sha256 != first.operation_nonce_sha256
        or item.expires_at != first.expires_at
        or item.admission_aggregate_sha256 != first.admission_aggregate_sha256
        or item.admission_durable_ledger_head_sha256
        != first.admission_durable_ledger_head_sha256
        for item in receipts[1:]
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_AGGREGATE_CHAIN_INVALID")


def _require_receiver_matches_publisher(
    *,
    publisher: VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt,
    receiver: VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt,
    normal_publisher_sha256: str,
    expected_prior_sha256: str,
    expected_bucket: _probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback,
) -> None:
    if (
        type(publisher.request)
        is not _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
        or type(publisher.readback)
        is not _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback
        or type(receiver.request)
        is not _probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest
        or type(receiver.readback)
        is not _probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback
        or receiver.prior_receipt_sha256 != expected_prior_sha256
        or receiver.normal_publisher_receipt_sha256 != normal_publisher_sha256
        or receiver.shared_bucket_readback != expected_bucket
        or receiver.retention_floor_publisher_issued_at != publisher.request.observed_at
        or receiver.request.retention_not_before != publisher.request.retention_not_before
        or receiver.request.immutable_version != _probe._immutable_version(publisher.readback)
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_AGGREGATE_CHAIN_INVALID")


def build_physical_arvan_s3_four_role_immutability_witness_mediated_preflight_observation(
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    fi_publisher_receipt: VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt,
    ir_receiver_receipt: VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt,
    ir_publisher_receipt: VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt,
    fi_receiver_receipt: VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt,
    observed_at: datetime,
) -> _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightObservation:
    """Build final evidence only from a complete verified Witness receipt chain.

    This is intentionally a Witness-side aggregation step, not a dispatcher:
    no role callback, peer delivery, provider client, or raw credential can be
    supplied.  The opaque durable IAM admission is revalidated at the final
    host-owned observation time, so a stale chain cannot become readiness
    evidence merely because its individual signatures remain parseable.
    """

    checked = _binding(binding)
    now = _utc(observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_TIME_INVALID")
    fi_publisher = _require_receipt_stage(
        fi_publisher_receipt,
        binding=checked,
        expected_role=_profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
        observed_at=now,
    )
    ir_receiver = _require_receipt_stage(
        ir_receiver_receipt,
        binding=checked,
        expected_role=_profiles.ARVAN_S3_IR_RECEIVER_ROLE,
        observed_at=now,
    )
    ir_publisher = _require_receipt_stage(
        ir_publisher_receipt,
        binding=checked,
        expected_role=_profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
        observed_at=now,
    )
    fi_receiver = _require_receipt_stage(
        fi_receiver_receipt,
        binding=checked,
        expected_role=_profiles.ARVAN_S3_FI_RECEIVER_ROLE,
        observed_at=now,
    )
    chain = (fi_publisher, ir_receiver, ir_publisher, fi_receiver)
    _same_run(receipts=chain)
    if (
        fi_publisher.prior_receipt_sha256 is not None
        or fi_publisher.normal_publisher_receipt_sha256 is not None
        or fi_publisher.shared_bucket_readback is not None
        or type(fi_publisher.request)
        is not _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
        or type(fi_publisher.readback)
        is not _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback
        or fi_publisher.readback.bucket_readback is None
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_AGGREGATE_CHAIN_INVALID")
    bucket = fi_publisher.readback.bucket_readback
    _require_receiver_matches_publisher(
        publisher=fi_publisher,
        receiver=ir_receiver,
        normal_publisher_sha256=fi_publisher.raw_sha256,
        expected_prior_sha256=fi_publisher.raw_sha256,
        expected_bucket=bucket,
    )
    if (
        type(ir_publisher.request)
        is not _probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
        or type(ir_publisher.readback)
        is not _probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback
        or ir_publisher.prior_receipt_sha256 != ir_receiver.raw_sha256
        or ir_publisher.normal_publisher_receipt_sha256 != fi_publisher.raw_sha256
        or ir_publisher.shared_bucket_readback != bucket
        or ir_publisher.retention_floor_publisher_issued_at
        != ir_publisher.request.observed_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_AGGREGATE_CHAIN_INVALID")
    _require_receiver_matches_publisher(
        publisher=ir_publisher,
        receiver=fi_receiver,
        normal_publisher_sha256=fi_publisher.raw_sha256,
        expected_prior_sha256=ir_publisher.raw_sha256,
        expected_bucket=bucket,
    )
    admitted = _require_admission(binding=checked, admission=admission, observed_at=now)
    if (
        admitted.aggregate_sha256 != fi_publisher.admission_aggregate_sha256
        or admitted.durable_ledger_head_sha256
        != fi_publisher.admission_durable_ledger_head_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_AGGREGATE_ADMISSION_MISMATCH")
    try:
        return _immutability.build_physical_arvan_s3_four_role_immutability_preflight_observation(
            binding=checked.preflight_binding,
            admission=admitted,
            live_iam_binding=checked.live_iam_binding,
            failback_binding=checked.failback_binding,
            normal_direction=_probe._direction_observation(
                direction="fi-publisher-to-ir-receiver",
                publisher=fi_publisher.readback,
                receiver=ir_receiver.readback,
                bucket_readback=bucket,
                binding=checked.preflight_binding,
            ),
            reverse_direction=_probe._direction_observation(
                direction="ir-publisher-to-fi-receiver",
                publisher=ir_publisher.readback,
                receiver=fi_receiver.readback,
                bucket_readback=bucket,
                binding=checked.preflight_binding,
            ),
            observed_at=now,
        )
    except _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_AGGREGATE_INVALID")
