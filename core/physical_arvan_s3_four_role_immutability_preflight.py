"""Pure four-role Object-Storage immutability preflight contract.

This contract is purpose-built for the physical three-site Full Matrix.  It
accepts only a fresh, opaque durable live-IAM admission plus the exact public
four-role bindings.  It has no credential, Object-Storage client, network,
filesystem, subprocess, or collection implementation; a later root-owned
runner may construct the bounded observations it validates here.

Both immutable paths are required in one observation:

* FI publisher -> private versioned storage -> IR receiver; and
* IR publisher -> private versioned storage -> FI receiver.

The verifier requires exact immutable object versions, private ACL/versioning
and retention facts, publisher create-only plus overwrite/delete denials, and
receiver exact head/get plus put/delete/list denials for each direction.  The
output remains non-authorizing readiness evidence only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
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
from core import physical_arvan_s3_four_role_live_iam_durable_admission_bridge as _admission
from core import physical_arvan_s3_four_role_live_iam_evidence as _live_iam
from core import physical_ir_to_fi_object_storage_failback_preflight as _failback
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
)


__all__ = (
    "DEFAULT_PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_SCHEMA",
    "PhysicalArvanS3FourRoleImmutableVersionObservation",
    "PhysicalArvanS3FourRoleImmutabilityDirectionObservation",
    "PhysicalArvanS3FourRoleImmutabilityPreflightBinding",
    "PhysicalArvanS3FourRoleImmutabilityPreflightConfig",
    "PhysicalArvanS3FourRoleImmutabilityPreflightError",
    "PhysicalArvanS3FourRoleImmutabilityPreflightObservation",
    "PhysicalArvanS3FourRoleImmutabilityPreflightProjection",
    "VerifiedPhysicalArvanS3FourRoleImmutabilityPreflight",
    "build_physical_arvan_s3_four_role_immutability_preflight_observation",
    "derive_physical_arvan_s3_four_role_immutability_probe_object_key",
    "project_verified_physical_arvan_s3_four_role_immutability_preflight",
    "require_verified_physical_arvan_s3_four_role_immutability_preflight",
    "verify_physical_arvan_s3_four_role_immutability_preflight",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-immutability-preflight-v1"
)
PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS = 120
_MAX_EVIDENCE_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_MIN_RETENTION_DAYS = 7
_MAX_RETENTION_DAYS = 3650
_MAX_OBJECT_BYTES = 8 * 1024 * 1024
_ENDPOINT_RE = re.compile(r"^https://s3\.([a-z0-9][a-z0-9-]{0,62})\.arvanstorage\.ir$", re.ASCII)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)

_NORMAL_DIRECTION = "fi-publisher-to-ir-receiver"
_REVERSE_DIRECTION = "ir-publisher-to-fi-receiver"
_DIRECTION_FACTS = {
    _NORMAL_DIRECTION: {
        "publisher_role": "fi-publisher",
        "receiver_role": "ir-receiver",
        "namespace": PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    },
    _REVERSE_DIRECTION: {
        "publisher_role": "ir-publisher",
        "receiver_role": "fi-receiver",
        "namespace": PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    },
}
_PRIVATE_ACL = "private-canonical-owner-only-v1"
_VERSIONING = "Enabled"
_RETENTION_MODE = "s3-object-lock-compliance-v1"
_CREATE_ONLY = "create-only-succeeded"
_DENIED = "access-denied"
_EXACT_HEAD = "exact-version-head-succeeded"
_EXACT_GET = "exact-version-get-succeeded"
_CAPABILITY = object()


class PhysicalArvanS3FourRoleImmutabilityPreflightError(ValueError):
    """A bounded two-direction immutable-storage proof is incomplete or stale."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityPreflightBinding:
    """Exact non-secret storage and four-role route pins for one campaign."""

    campaign_id: str
    release_sha: str
    endpoint: str
    region: str
    bucket: str
    bucket_access_posture: str
    normal_object_storage_namespace: str
    reverse_object_storage_namespace: str
    minimum_retention_days: int
    normal_route_scope_sha256: str
    reverse_route_scope_sha256: str
    four_role_route_binding_sha256: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    ir_publisher_identity_sha256: str
    fi_receiver_identity_sha256: str


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutableVersionObservation:
    """One exact immutable Object version and exact-version readback facts."""

    probe_nonce_sha256: str
    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retention_until: datetime
    exact_head_version_id: str
    exact_get_version_id: str
    exact_get_content_sha256: str
    exact_get_content_bytes: int


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityDirectionObservation:
    """One publisher/receiver direction, with all attempted denial outcomes."""

    direction: str
    publisher_role: str
    receiver_role: str
    object_storage_namespace: str
    publisher_identity_sha256: str
    receiver_identity_sha256: str
    acl_posture: str
    versioning_status: str
    retention_mode: str
    retention_policy_evidence_sha256: str
    retention_days: int
    immutable_version: PhysicalArvanS3FourRoleImmutableVersionObservation
    publisher_create_only_outcome: str
    publisher_overwrite_outcome: str
    publisher_delete_object_outcome: str
    publisher_delete_version_outcome: str
    receiver_exact_head_outcome: str
    receiver_exact_get_outcome: str
    receiver_put_outcome: str
    receiver_delete_object_outcome: str
    receiver_delete_version_outcome: str
    receiver_list_bucket_outcome: str
    receiver_list_versions_outcome: str


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityPreflightObservation:
    """Raw bounded evidence; direct construction has no verification authority."""

    schema: str
    status: str
    campaign_id: str
    release_sha: str
    endpoint: str
    region: str
    bucket: str
    bucket_access_posture: str
    normal_route_scope_sha256: str
    reverse_route_scope_sha256: str
    four_role_route_binding_sha256: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    ir_publisher_identity_sha256: str
    fi_receiver_identity_sha256: str
    admission_aggregate_sha256: str
    admission_durable_ledger_head_sha256: str
    normal_direction: PhysicalArvanS3FourRoleImmutabilityDirectionObservation
    reverse_direction: PhysicalArvanS3FourRoleImmutabilityDirectionObservation
    observed_at: datetime
    evidence_sha256: str


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityPreflightConfig:
    """Default-off verifier policy; it holds no SDK client or credential material."""

    binding: PhysicalArvanS3FourRoleImmutabilityPreflightBinding | None = None
    enabled: bool = PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleImmutabilityPreflight:
    """Opaque verified two-direction immutable-storage evidence."""

    observation: PhysicalArvanS3FourRoleImmutabilityPreflightObservation
    binding: PhysicalArvanS3FourRoleImmutabilityPreflightBinding
    admission_aggregate_sha256: str
    admission_durable_ledger_head_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityPreflightProjection:
    """Non-authorizing compact projection for a later readiness aggregator."""

    schema: str
    campaign_id: str
    release_sha: str
    bucket: str
    normal_route_scope_sha256: str
    reverse_route_scope_sha256: str
    four_role_route_binding_sha256: str
    minimum_retention_days: int
    admission_aggregate_sha256: str
    admission_durable_ledger_head_sha256: str
    observed_at: datetime
    evidence_sha256: str


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleImmutabilityPreflightError(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _utc(value: object, *, code: str, whole_seconds: bool = False) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if whole_seconds and result.microsecond != 0:
        _fail(code)
    return result


def _canonical_utc(value: object, *, code: str) -> datetime:
    """Accept only a whole-second ``datetime.timezone.utc`` timestamp.

    Object Lock's retained-until instant is security-relevant.  Merely
    accepting an offset-equivalent local time would permit multiple wire
    representations for the same proof, so the contract requires the
    canonical UTC object itself.
    """

    if type(value) is not datetime or value.tzinfo is not timezone.utc or value.microsecond != 0:
        _fail(code)
    return value


def _timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code, whole_seconds=True).strftime("%Y-%m-%dT%H:%M:%SZ")


def _binding(value: object) -> PhysicalArvanS3FourRoleImmutabilityPreflightBinding:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityPreflightBinding:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID")
    if (
        type(value.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None
        or type(value.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(value.release_sha) is None
        or type(value.endpoint) is not str
        or type(value.region) is not str
        or type(value.bucket) is not str
        or _BUCKET_RE.fullmatch(value.bucket) is None
        or value.bucket_access_posture != "private"
        or value.normal_object_storage_namespace != PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE
        or value.reverse_object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or type(value.minimum_retention_days) is not int
        or not _MIN_RETENTION_DAYS <= value.minimum_retention_days <= _MAX_RETENTION_DAYS
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID")
    endpoint = _ENDPOINT_RE.fullmatch(value.endpoint)
    if endpoint is None or endpoint.group(1) != value.region:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID")
    normal_scope = _sha256(value.normal_route_scope_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID")
    reverse_scope = _sha256(value.reverse_route_scope_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID")
    if normal_scope == reverse_scope:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_SCOPE_COLLISION")
    _sha256(value.four_role_route_binding_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID")
    identities = (
        _sha256(value.fi_publisher_identity_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID"),
        _sha256(value.ir_receiver_identity_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID"),
        _sha256(value.ir_publisher_identity_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID"),
        _sha256(value.fi_receiver_identity_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_INVALID"),
    )
    if len(set(identities)) != 4:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_IDENTITIES_NOT_DISTINCT")
    return value


def _config(value: object, *, require_enabled: bool) -> tuple[PhysicalArvanS3FourRoleImmutabilityPreflightBinding, int]:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityPreflightConfig or type(value.enabled) is not bool:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_DISABLED")
    if (
        type(value.maximum_evidence_age_seconds) is not int
        or not 1 <= value.maximum_evidence_age_seconds <= _MAX_EVIDENCE_AGE_SECONDS
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_CONFIG_INVALID")
    return _binding(value.binding), value.maximum_evidence_age_seconds


def _inputs(
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
    try:
        checked_live = _live_iam._require_binding(live_iam_binding)
        checked_failback = _failback.validate_physical_ir_to_fi_object_storage_failback_binding(failback_binding)
        checked_admission = _admission.require_verified_physical_arvan_s3_four_role_live_iam_durable_admission(
            admission,
            live_iam_binding=checked_live,
            failback_binding=checked_failback,
            observed_at=observed_at,
        )
    except (
        _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError,
        _failback.PhysicalIrToFiObjectStorageFailbackPreflightError,
        _admission.PhysicalArvanS3FourRoleLiveIamDurableAdmissionError,
    ) as exc:
        _fail(f"ARVAN_S3_FOUR_ROLE_IMMUTABILITY_ADMISSION_INVALID_{type(exc).__name__}")
    if (
        binding.campaign_id != checked_live.campaign_id
        or binding.release_sha != checked_live.release_sha
        or binding.normal_route_scope_sha256 != checked_live.normal_route_scope_sha256
        or binding.reverse_route_scope_sha256 != checked_live.reverse_route_scope_sha256
        or binding.four_role_route_binding_sha256 != checked_live.four_role_binding_sha256
        or binding.fi_publisher_identity_sha256 != checked_live.fi_publisher_identity_sha256
        or binding.ir_receiver_identity_sha256 != checked_live.ir_receiver_identity_sha256
        or binding.ir_publisher_identity_sha256 != checked_live.ir_publisher_identity_sha256
        or binding.fi_receiver_identity_sha256 != checked_live.fi_receiver_identity_sha256
        or binding.campaign_id != checked_failback.campaign_id
        or binding.release_sha != checked_failback.release_sha
        or binding.normal_route_scope_sha256 != checked_failback.normal_route_scope_sha256
        or binding.reverse_route_scope_sha256 != checked_failback.reverse_route_scope_sha256
        or binding.four_role_route_binding_sha256 != checked_failback.route_binding_sha256
        or binding.fi_publisher_identity_sha256 != checked_failback.fi_publisher_identity_sha256
        or binding.ir_receiver_identity_sha256 != checked_failback.ir_receiver_identity_sha256
        or binding.ir_publisher_identity_sha256 != checked_failback.ir_publisher_identity_sha256
        or binding.fi_receiver_identity_sha256 != checked_failback.fi_receiver_identity_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_BINDING_ADMISSION_MISMATCH")
    return checked_admission


def derive_physical_arvan_s3_four_role_immutability_probe_object_key(
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    direction: str,
    probe_nonce_sha256: str,
) -> str:
    """Derive the only admissible disposable immutable key for one direction."""

    checked = _binding(binding)
    if direction not in _DIRECTION_FACTS:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_DIRECTION_INVALID")
    nonce = _sha256(probe_nonce_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_PROBE_SELECTOR_INVALID")
    namespace = _DIRECTION_FACTS[direction]["namespace"]
    return (
        f"{namespace}/{checked.campaign_id}/{checked.release_sha}/"
        f"four-role-immutability/{direction}/{nonce}.age"
    )


def _version(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    direction: str,
    observed_at: datetime,
    retention_days: int,
) -> PhysicalArvanS3FourRoleImmutableVersionObservation:
    if type(value) is not PhysicalArvanS3FourRoleImmutableVersionObservation:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_VERSION_INVALID")
    nonce = _sha256(value.probe_nonce_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_VERSION_INVALID")
    expected_key = derive_physical_arvan_s3_four_role_immutability_probe_object_key(
        binding=binding, direction=direction, probe_nonce_sha256=nonce
    )
    if (
        type(value.object_key) is not str
        or OBJECT_KEY_RE.fullmatch(value.object_key) is None
        or ".." in value.object_key.split("/")
        or value.object_key != expected_key
        or type(value.object_version_id) is not str
        or VERSION_ID_RE.fullmatch(value.object_version_id) is None
        or value.object_version_id.lower() == "null"
        or type(value.content_bytes) is not int
        or not 1 <= value.content_bytes <= _MAX_OBJECT_BYTES
        or type(value.exact_get_content_bytes) is not int
        or value.exact_get_content_bytes != value.content_bytes
        or value.exact_head_version_id != value.object_version_id
        or value.exact_get_version_id != value.object_version_id
        or value.exact_get_content_sha256 != value.content_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_VERSION_INVALID")
    if (
        type(value.exact_head_version_id) is not str
        or VERSION_ID_RE.fullmatch(value.exact_head_version_id) is None
        or type(value.exact_get_version_id) is not str
        or VERSION_ID_RE.fullmatch(value.exact_get_version_id) is None
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_VERSION_INVALID")
    _sha256(value.content_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_VERSION_INVALID")
    _sha256(value.exact_get_content_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_VERSION_INVALID")
    retention_until = _canonical_utc(
        value.retention_until,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_RETENTION_UNTIL_INVALID",
    )
    required_until = observed_at + timedelta(
        days=max(binding.minimum_retention_days, retention_days)
    )
    if retention_until < required_until:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_RETENTION_UNTIL_TOO_SHORT")
    return value


def _direction(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    direction: str,
    observed_at: datetime,
) -> PhysicalArvanS3FourRoleImmutabilityDirectionObservation:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityDirectionObservation or direction not in _DIRECTION_FACTS:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_DIRECTION_INVALID")
    expected = _DIRECTION_FACTS[direction]
    publisher_identity = (
        binding.fi_publisher_identity_sha256
        if expected["publisher_role"] == "fi-publisher"
        else binding.ir_publisher_identity_sha256
    )
    receiver_identity = (
        binding.ir_receiver_identity_sha256
        if expected["receiver_role"] == "ir-receiver"
        else binding.fi_receiver_identity_sha256
    )
    if (
        value.direction != direction
        or value.publisher_role != expected["publisher_role"]
        or value.receiver_role != expected["receiver_role"]
        or value.object_storage_namespace != expected["namespace"]
        or _sha256(value.publisher_identity_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_DIRECTION_INVALID") != publisher_identity
        or _sha256(value.receiver_identity_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_DIRECTION_INVALID") != receiver_identity
        or value.acl_posture != _PRIVATE_ACL
        or value.versioning_status != _VERSIONING
        or value.retention_mode != _RETENTION_MODE
        or _sha256(value.retention_policy_evidence_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_DIRECTION_INVALID") == "0" * 64
        or type(value.retention_days) is not int
        or value.retention_days < binding.minimum_retention_days
        or value.retention_days > _MAX_RETENTION_DAYS
        or value.publisher_create_only_outcome != _CREATE_ONLY
        or value.publisher_overwrite_outcome != _DENIED
        or value.publisher_delete_object_outcome != _DENIED
        or value.publisher_delete_version_outcome != _DENIED
        or value.receiver_exact_head_outcome != _EXACT_HEAD
        or value.receiver_exact_get_outcome != _EXACT_GET
        or value.receiver_put_outcome != _DENIED
        or value.receiver_delete_object_outcome != _DENIED
        or value.receiver_delete_version_outcome != _DENIED
        or value.receiver_list_bucket_outcome != _DENIED
        or value.receiver_list_versions_outcome != _DENIED
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_DIRECTION_INVALID")
    _version(
        value.immutable_version,
        binding=binding,
        direction=direction,
        observed_at=observed_at,
        retention_days=value.retention_days,
    )
    return value


def _require_shared_bucket_compatibility(
    normal: PhysicalArvanS3FourRoleImmutabilityDirectionObservation,
    reverse: PhysicalArvanS3FourRoleImmutabilityDirectionObservation,
) -> None:
    """Pin shared-bucket Object-Lock policy facts and unique probe selectors."""

    if (
        normal.acl_posture != reverse.acl_posture
        or normal.versioning_status != reverse.versioning_status
        or normal.retention_mode != reverse.retention_mode
        or normal.retention_policy_evidence_sha256 != reverse.retention_policy_evidence_sha256
        or normal.retention_days != reverse.retention_days
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_SHARED_BUCKET_POLICY_MISMATCH")
    normal_version = normal.immutable_version
    reverse_version = reverse.immutable_version
    if (
        normal_version.probe_nonce_sha256 == reverse_version.probe_nonce_sha256
        or normal_version.object_key == reverse_version.object_key
        or normal_version.object_version_id == reverse_version.object_version_id
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_DIRECTION_SELECTOR_COLLISION")


def _version_mapping(value: PhysicalArvanS3FourRoleImmutableVersionObservation) -> dict[str, Any]:
    return {
        "probe_nonce_sha256": value.probe_nonce_sha256,
        "object_key": value.object_key,
        "object_version_id": value.object_version_id,
        "content_sha256": value.content_sha256,
        "content_bytes": value.content_bytes,
        "retention_until": _timestamp(
            value.retention_until,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_RETENTION_UNTIL_INVALID",
        ),
        "exact_head_version_id": value.exact_head_version_id,
        "exact_get_version_id": value.exact_get_version_id,
        "exact_get_content_sha256": value.exact_get_content_sha256,
        "exact_get_content_bytes": value.exact_get_content_bytes,
    }


def _direction_mapping(value: PhysicalArvanS3FourRoleImmutabilityDirectionObservation) -> dict[str, Any]:
    return {
        "direction": value.direction,
        "publisher_role": value.publisher_role,
        "receiver_role": value.receiver_role,
        "object_storage_namespace": value.object_storage_namespace,
        "publisher_identity_sha256": value.publisher_identity_sha256,
        "receiver_identity_sha256": value.receiver_identity_sha256,
        "acl_posture": value.acl_posture,
        "versioning_status": value.versioning_status,
        "retention_mode": value.retention_mode,
        "retention_policy_evidence_sha256": value.retention_policy_evidence_sha256,
        "retention_days": value.retention_days,
        "immutable_version": _version_mapping(value.immutable_version),
        "publisher_create_only_outcome": value.publisher_create_only_outcome,
        "publisher_overwrite_outcome": value.publisher_overwrite_outcome,
        "publisher_delete_object_outcome": value.publisher_delete_object_outcome,
        "publisher_delete_version_outcome": value.publisher_delete_version_outcome,
        "receiver_exact_head_outcome": value.receiver_exact_head_outcome,
        "receiver_exact_get_outcome": value.receiver_exact_get_outcome,
        "receiver_put_outcome": value.receiver_put_outcome,
        "receiver_delete_object_outcome": value.receiver_delete_object_outcome,
        "receiver_delete_version_outcome": value.receiver_delete_version_outcome,
        "receiver_list_bucket_outcome": value.receiver_list_bucket_outcome,
        "receiver_list_versions_outcome": value.receiver_list_versions_outcome,
    }


def _observation_mapping(value: PhysicalArvanS3FourRoleImmutabilityPreflightObservation) -> dict[str, Any]:
    return {
        "schema": value.schema,
        "status": value.status,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "endpoint": value.endpoint,
        "region": value.region,
        "bucket": value.bucket,
        "bucket_access_posture": value.bucket_access_posture,
        "normal_route_scope_sha256": value.normal_route_scope_sha256,
        "reverse_route_scope_sha256": value.reverse_route_scope_sha256,
        "four_role_route_binding_sha256": value.four_role_route_binding_sha256,
        "fi_publisher_identity_sha256": value.fi_publisher_identity_sha256,
        "ir_receiver_identity_sha256": value.ir_receiver_identity_sha256,
        "ir_publisher_identity_sha256": value.ir_publisher_identity_sha256,
        "fi_receiver_identity_sha256": value.fi_receiver_identity_sha256,
        "admission_aggregate_sha256": value.admission_aggregate_sha256,
        "admission_durable_ledger_head_sha256": value.admission_durable_ledger_head_sha256,
        "normal_direction": _direction_mapping(value.normal_direction),
        "reverse_direction": _direction_mapping(value.reverse_direction),
        "observed_at": _timestamp(value.observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_OBSERVATION_INVALID"),
    }


def _evidence_sha256(value: PhysicalArvanS3FourRoleImmutabilityPreflightObservation) -> str:
    return hashlib.sha256(
        canonical_json_bytes(_observation_mapping(value))
    ).hexdigest()


def build_physical_arvan_s3_four_role_immutability_preflight_observation(
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    normal_direction: PhysicalArvanS3FourRoleImmutabilityDirectionObservation,
    reverse_direction: PhysicalArvanS3FourRoleImmutabilityDirectionObservation,
    observed_at: datetime,
) -> PhysicalArvanS3FourRoleImmutabilityPreflightObservation:
    """Build canonical bounded evidence after validating a fresh admission."""

    checked_binding = _binding(binding)
    observed = _utc(observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_OBSERVATION_INVALID", whole_seconds=True)
    checked_admission = _inputs(
        binding=checked_binding,
        admission=admission,
        live_iam_binding=live_iam_binding,
        failback_binding=failback_binding,
        observed_at=observed,
    )
    normal = _direction(
        normal_direction,
        binding=checked_binding,
        direction=_NORMAL_DIRECTION,
        observed_at=observed,
    )
    reverse = _direction(
        reverse_direction,
        binding=checked_binding,
        direction=_REVERSE_DIRECTION,
        observed_at=observed,
    )
    _require_shared_bucket_compatibility(normal, reverse)
    provisional = PhysicalArvanS3FourRoleImmutabilityPreflightObservation(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_SCHEMA,
        status="four-role-immutable-observed",
        campaign_id=checked_binding.campaign_id,
        release_sha=checked_binding.release_sha,
        endpoint=checked_binding.endpoint,
        region=checked_binding.region,
        bucket=checked_binding.bucket,
        bucket_access_posture=checked_binding.bucket_access_posture,
        normal_route_scope_sha256=checked_binding.normal_route_scope_sha256,
        reverse_route_scope_sha256=checked_binding.reverse_route_scope_sha256,
        four_role_route_binding_sha256=checked_binding.four_role_route_binding_sha256,
        fi_publisher_identity_sha256=checked_binding.fi_publisher_identity_sha256,
        ir_receiver_identity_sha256=checked_binding.ir_receiver_identity_sha256,
        ir_publisher_identity_sha256=checked_binding.ir_publisher_identity_sha256,
        fi_receiver_identity_sha256=checked_binding.fi_receiver_identity_sha256,
        admission_aggregate_sha256=checked_admission.aggregate_sha256,
        admission_durable_ledger_head_sha256=checked_admission.durable_ledger_head_sha256,
        normal_direction=normal,
        reverse_direction=reverse,
        observed_at=observed,
        evidence_sha256="1" * 64,
    )
    return PhysicalArvanS3FourRoleImmutabilityPreflightObservation(
        **{**provisional.__dict__, "evidence_sha256": _evidence_sha256(provisional)}
    )


def _observation(
    value: object,
    *,
    binding: PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    now: datetime,
    maximum_age_seconds: int,
) -> PhysicalArvanS3FourRoleImmutabilityPreflightObservation:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityPreflightObservation:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_OBSERVATION_INVALID")
    observed = _utc(value.observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_OBSERVATION_INVALID", whole_seconds=True)
    if observed > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS) or now - observed > timedelta(seconds=maximum_age_seconds):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_STALE")
    if (
        value.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_SCHEMA
        or value.status != "four-role-immutable-observed"
        or value.campaign_id != binding.campaign_id
        or value.release_sha != binding.release_sha
        or value.endpoint != binding.endpoint
        or value.region != binding.region
        or value.bucket != binding.bucket
        or value.bucket_access_posture != binding.bucket_access_posture
        or value.normal_route_scope_sha256 != binding.normal_route_scope_sha256
        or value.reverse_route_scope_sha256 != binding.reverse_route_scope_sha256
        or value.four_role_route_binding_sha256 != binding.four_role_route_binding_sha256
        or value.fi_publisher_identity_sha256 != binding.fi_publisher_identity_sha256
        or value.ir_receiver_identity_sha256 != binding.ir_receiver_identity_sha256
        or value.ir_publisher_identity_sha256 != binding.ir_publisher_identity_sha256
        or value.fi_receiver_identity_sha256 != binding.fi_receiver_identity_sha256
        or value.admission_aggregate_sha256 != admission.aggregate_sha256
        or value.admission_durable_ledger_head_sha256 != admission.durable_ledger_head_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_OBSERVATION_BINDING_MISMATCH")
    normal = _direction(
        value.normal_direction,
        binding=binding,
        direction=_NORMAL_DIRECTION,
        observed_at=observed,
    )
    reverse = _direction(
        value.reverse_direction,
        binding=binding,
        direction=_REVERSE_DIRECTION,
        observed_at=observed,
    )
    _require_shared_bucket_compatibility(normal, reverse)
    if _sha256(value.evidence_sha256, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_OBSERVATION_INVALID") != _evidence_sha256(value):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_EVIDENCE_MISMATCH")
    return value


def verify_physical_arvan_s3_four_role_immutability_preflight(
    observation: PhysicalArvanS3FourRoleImmutabilityPreflightObservation,
    *,
    config: PhysicalArvanS3FourRoleImmutabilityPreflightConfig,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleImmutabilityPreflight:
    """Verify fresh two-direction evidence and its durable live-IAM admission."""

    binding, maximum_age = _config(config, require_enabled=True)
    now = _utc(observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_TIME_INVALID")
    checked_admission = _inputs(
        binding=binding,
        admission=admission,
        live_iam_binding=live_iam_binding,
        failback_binding=failback_binding,
        observed_at=now,
    )
    checked_observation = _observation(
        observation,
        binding=binding,
        admission=checked_admission,
        now=now,
        maximum_age_seconds=maximum_age,
    )
    result = VerifiedPhysicalArvanS3FourRoleImmutabilityPreflight(
        observation=checked_observation,
        binding=binding,
        admission_aggregate_sha256=checked_admission.aggregate_sha256,
        admission_durable_ledger_head_sha256=checked_admission.durable_ledger_head_sha256,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def require_verified_physical_arvan_s3_four_role_immutability_preflight(
    value: object,
    *,
    config: PhysicalArvanS3FourRoleImmutabilityPreflightConfig,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleImmutabilityPreflight:
    """Revalidate opaque evidence for a readiness-only consumer."""

    if type(value) is not VerifiedPhysicalArvanS3FourRoleImmutabilityPreflight or value._capability is not _CAPABILITY:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_NOT_VERIFIED")
    checked = verify_physical_arvan_s3_four_role_immutability_preflight(
        value.observation,
        config=config,
        admission=admission,
        live_iam_binding=live_iam_binding,
        failback_binding=failback_binding,
        observed_at=observed_at,
    )
    if (
        checked.binding != value.binding
        or checked.admission_aggregate_sha256 != value.admission_aggregate_sha256
        or checked.admission_durable_ledger_head_sha256 != value.admission_durable_ledger_head_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_NOT_VERIFIED")
    return value


def project_verified_physical_arvan_s3_four_role_immutability_preflight(
    value: object,
    *,
    config: PhysicalArvanS3FourRoleImmutabilityPreflightConfig,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> PhysicalArvanS3FourRoleImmutabilityPreflightProjection:
    """Project verified evidence without granting an operational capability."""

    verified = require_verified_physical_arvan_s3_four_role_immutability_preflight(
        value,
        config=config,
        admission=admission,
        live_iam_binding=live_iam_binding,
        failback_binding=failback_binding,
        observed_at=observed_at,
    )
    observation = verified.observation
    binding = verified.binding
    return PhysicalArvanS3FourRoleImmutabilityPreflightProjection(
        schema="gold-trade-physical-arvan-s3-four-role-immutability-projection-v1",
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        bucket=binding.bucket,
        normal_route_scope_sha256=binding.normal_route_scope_sha256,
        reverse_route_scope_sha256=binding.reverse_route_scope_sha256,
        four_role_route_binding_sha256=binding.four_role_route_binding_sha256,
        minimum_retention_days=binding.minimum_retention_days,
        admission_aggregate_sha256=verified.admission_aggregate_sha256,
        admission_durable_ledger_head_sha256=verified.admission_durable_ledger_head_sha256,
        observed_at=observation.observed_at,
        evidence_sha256=observation.evidence_sha256,
    )
