"""Default-off injected runtime for four-role immutable-storage evidence.

This module is intentionally *not* an S3 client and it is not a provider
probe implementation.  It owns no credential, SDK, endpoint resolver,
socket, subprocess, file path, generic client, or paired role object.  A
root-owned, separately audited, role-local collector may be injected for each
of the four machine identities.  The collector receives one bounded request
and returns a small semantic readback; this runtime only validates those
readbacks and turns them into the pure four-role immutability observation.

The boundary is deliberately narrow:

* FI publisher supplies the shared private/versioning/Object-Lock posture and
  the normal create-only version selector.
* IR receiver supplies only the exact normal-version readback and its denied
  capability outcomes.
* IR publisher supplies only the reverse create-only version selector.
* FI receiver supplies only the exact reverse-version readback and its denied
  capability outcomes.

There is no generic ``client`` field and this module never invokes provider
operation names itself.  In particular, it cannot issue a broad key query,
mutation, removal, or direct WA-FI <-> WA-IR request.  The runtime is inert
until an explicit enabled configuration is collected by root, and rejects an
invalid durable live-IAM admission *before* invoking any injected collector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import os
import secrets

from core.append_only_sync_delta_batch import SHA256_RE, VERSION_ID_RE, canonical_json_bytes
from core import physical_arvan_s3_four_role_immutability_preflight as _immutability
from core import physical_arvan_s3_four_role_live_iam_durable_admission_bridge as _admission
from core import physical_arvan_s3_four_role_live_iam_evidence as _live_iam
from core import physical_arvan_s3_role_profiles as _profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as _failback


__all__ = (
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA",
    "PhysicalArvanS3FourRoleImmutabilityBucketReadback",
    "PhysicalArvanS3FourRoleImmutabilityLiveProbeConfig",
    "PhysicalArvanS3FourRoleImmutabilityLiveProbeError",
    "PhysicalArvanS3FourRoleImmutabilityLiveProbeRuntime",
    "PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest",
    "PhysicalArvanS3FourRoleImmutabilityPublisherReadback",
    "PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest",
    "PhysicalArvanS3FourRoleImmutabilityReceiverReadback",
    "PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-immutability-live-probe-v1"
)
PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_DEFAULT_ENABLED = False
# Physical roles execute sequentially across independently reachable hosts.
# The Object-Lock floor is therefore not merely ``publisher time + days``:
# it has a small, fixed maximum transit allowance so a later exact-version
# receiver and the final Witness evidence can still prove the required number
# of whole retention days.  A larger caller-controlled retention extension is
# deliberately not accepted by the collector/runtime grammar.
PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS = 300

_NORMAL_DIRECTION = "fi-publisher-to-ir-receiver"
_REVERSE_DIRECTION = "ir-publisher-to-fi-receiver"
_PRIVATE_ACL = "private-canonical-owner-only-v1"
_VERSIONING = "Enabled"
_RETENTION_MODE = "s3-object-lock-compliance-v1"
_CREATE_ONLY = "create-only-succeeded"
_DENIED = "access-denied"
_EXACT_HEAD = "exact-version-head-succeeded"
_EXACT_GET = "exact-version-get-succeeded"
_MIN_RETENTION_DAYS = 7
_MAX_RETENTION_DAYS = 3650
_MAX_OBJECT_BYTES = 8 * 1024 * 1024

_ROLE_FACTS = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: {
        "direction": _NORMAL_DIRECTION,
        "binding_identity": "fi_publisher_identity_sha256",
        "profile": _profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    },
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: {
        "direction": _NORMAL_DIRECTION,
        "binding_identity": "ir_receiver_identity_sha256",
        "profile": _profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
    },
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: {
        "direction": _REVERSE_DIRECTION,
        "binding_identity": "ir_publisher_identity_sha256",
        "profile": _profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    },
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: {
        "direction": _REVERSE_DIRECTION,
        "binding_identity": "fi_receiver_identity_sha256",
        "profile": _profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
    },
}


class PhysicalArvanS3FourRoleImmutabilityLiveProbeError(ValueError):
    """A redacted local-policy or injected-readback failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityBucketReadback:
    """Semantic private/versioned/Object-Lock facts from the FI-local role.

    It deliberately contains no raw provider response, owner identifier,
    request ID, credential material, or SDK object.  The later role-local
    implementation is responsible for deriving these fixed semantic values
    from exact provider readback.
    """

    acl_posture: str
    versioning_status: str
    retention_mode: str
    retention_days: int


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest:
    """One bounded local request for either create-only publisher role."""

    schema: str
    direction: str
    role: str
    identity_sha256: str
    campaign_id: str
    release_sha: str
    endpoint: str
    region: str
    bucket: str
    object_storage_namespace: str
    probe_nonce_sha256: str
    object_key: str
    observed_at: datetime
    minimum_retention_days: int
    retention_not_before: datetime


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest:
    """One bounded exact-version request for either readonly receiver role."""

    schema: str
    direction: str
    role: str
    identity_sha256: str
    campaign_id: str
    release_sha: str
    endpoint: str
    region: str
    bucket: str
    object_storage_namespace: str
    immutable_version: _immutability.PhysicalArvanS3FourRoleImmutableVersionObservation
    observed_at: datetime
    retention_not_before: datetime


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityPublisherReadback:
    """Non-secret semantic facts returned by one publisher-local collector."""

    schema: str
    direction: str
    role: str
    identity_sha256: str
    probe_nonce_sha256: str
    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retention_until: datetime
    create_only_outcome: str
    overwrite_outcome: str
    object_removal_outcome: str
    version_removal_outcome: str
    bucket_readback: PhysicalArvanS3FourRoleImmutabilityBucketReadback | None


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityReceiverReadback:
    """Non-secret exact-version facts returned by one receiver-local collector."""

    schema: str
    direction: str
    role: str
    identity_sha256: str
    probe_nonce_sha256: str
    object_key: str
    object_version_id: str
    exact_head_version_id: str
    exact_get_version_id: str
    exact_get_content_sha256: str
    exact_get_content_bytes: int
    put_outcome: str
    object_removal_outcome: str
    version_removal_outcome: str
    bucket_enumeration_outcome: str
    version_enumeration_outcome: str


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter:
    """One explicit callback, pinned to one machine role and identity.

    ``readback_adapter`` is intentionally the only injected execution seam.
    It is not a client and this runtime never retains or exposes its return
    value except after exact dataclass validation.
    """

    role: str
    identity_sha256: str
    action_profile: str
    readback_adapter: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityLiveProbeConfig:
    """Inert root-only runtime configuration with four explicit local seams."""

    schema: str = PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding | None = field(
        default=None,
        repr=False,
    )
    fi_publisher_adapter: PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    ir_receiver_adapter: PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    ir_publisher_adapter: PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    fi_receiver_adapter: PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    enabled: bool = PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_DEFAULT_ENABLED


@dataclass(frozen=True)
class _RuntimeFacts:
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding
    adapters: dict[str, PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter]


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleImmutabilityLiveProbeError(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _exact_text(value: object, expected: str, *, code: str) -> str:
    if type(value) is not str or value != expected:
        _fail(code)
    return value


def _whole_second_utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc or value.microsecond != 0:
        _fail(code)
    return value


def _binding(value: object) -> _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding:
    try:
        return _immutability._binding(value)
    except _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_BINDING_INVALID")


def _adapter(
    value: object,
    *,
    role: str,
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
) -> PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_ADAPTER_INVALID")
    facts = _ROLE_FACTS[role]
    identity = _sha256(
        value.identity_sha256,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_ADAPTER_INVALID",
    )
    if (
        type(value.role) is not str
        or value.role != role
        or identity != getattr(binding, facts["binding_identity"])
        or type(value.action_profile) is not str
        or value.action_profile != facts["profile"]
        or not callable(value.readback_adapter)
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_ADAPTER_INVALID")
    try:
        _profiles.require_canonical_arvan_s3_role_profile(
            role=value.role,
            action_profile=value.action_profile,
        )
    except _profiles.ArvanS3RoleProfileError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_ADAPTER_INVALID")
    return value


def _runtime_facts(config: object) -> _RuntimeFacts:
    if type(config) is not PhysicalArvanS3FourRoleImmutabilityLiveProbeConfig:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_CONFIG_INVALID")
    if (
        config.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA
        or type(config.enabled) is not bool
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_CONFIG_INVALID")
    if config.enabled is not True:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_DISABLED")
    binding = _binding(config.binding)
    configured = {
        _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: config.fi_publisher_adapter,
        _profiles.ARVAN_S3_IR_RECEIVER_ROLE: config.ir_receiver_adapter,
        _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: config.ir_publisher_adapter,
        _profiles.ARVAN_S3_FI_RECEIVER_ROLE: config.fi_receiver_adapter,
    }
    adapters = {
        role: _adapter(value, role=role, binding=binding)
        for role, value in configured.items()
    }
    adapter_objects = tuple(adapters.values())
    adapter_callbacks = tuple(item.readback_adapter for item in adapter_objects)
    if len({id(item) for item in adapter_objects}) != len(adapter_objects) or len(
        {id(item) for item in adapter_callbacks}
    ) != len(adapter_callbacks):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_ROLE_COLLISION")
    return _RuntimeFacts(binding=binding, adapters=adapters)


def _require_admission(
    *,
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    admission: object,
    live_iam_binding: object,
    failback_binding: object,
    observed_at: datetime,
) -> _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
    """Reject mismatched durable IAM evidence before external callbacks run."""

    try:
        checked_live = _live_iam._require_binding(live_iam_binding)
        checked_failback = _failback.validate_physical_ir_to_fi_object_storage_failback_binding(
            failback_binding
        )
        checked_admission = (
            _admission.require_verified_physical_arvan_s3_four_role_live_iam_durable_admission(
                admission,
                live_iam_binding=checked_live,
                failback_binding=checked_failback,
                observed_at=observed_at,
            )
        )
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_ADMISSION_INVALID")
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
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_BINDING_ADMISSION_MISMATCH")
    return checked_admission


def _fresh_nonce_commitment() -> str:
    """Create one non-caller-controlled selector commitment after admission.

    The raw entropy never leaves this function.  The Object-Storage key and
    final evidence receive only its SHA-256 commitment, so callers cannot
    replay a selected key by supplying a convenient nonce value.
    """

    try:
        entropy = secrets.token_bytes(32)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_NONCE_GENERATION_FAILED")
    if type(entropy) is not bytes or len(entropy) != 32:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_NONCE_GENERATION_FAILED")
    return hashlib.sha256(entropy).hexdigest()


def _bucket_readback(
    value: object,
    *,
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
) -> PhysicalArvanS3FourRoleImmutabilityBucketReadback:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityBucketReadback:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_BUCKET_READBACK_INVALID")
    if (
        type(value.acl_posture) is not str
        or value.acl_posture != _PRIVATE_ACL
        or type(value.versioning_status) is not str
        or value.versioning_status != _VERSIONING
        or type(value.retention_mode) is not str
        or value.retention_mode != _RETENTION_MODE
        or type(value.retention_days) is not int
        or not max(_MIN_RETENTION_DAYS, binding.minimum_retention_days)
        <= value.retention_days
        <= _MAX_RETENTION_DAYS
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_BUCKET_READBACK_INVALID")
    return value


def _retention_policy_evidence_sha256(
    *,
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    bucket_readback: PhysicalArvanS3FourRoleImmutabilityBucketReadback,
) -> str:
    """Pin the only non-secret shared-bucket posture representation."""

    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
                "endpoint": binding.endpoint,
                "region": binding.region,
                "bucket": binding.bucket,
                "acl_posture": bucket_readback.acl_posture,
                "versioning_status": bucket_readback.versioning_status,
                "retention_mode": bucket_readback.retention_mode,
                "retention_days": bucket_readback.retention_days,
            }
        )
    ).hexdigest()


def _publisher_request(
    *,
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    role: str,
    identity_sha256: str,
    nonce: str,
    observed_at: datetime,
    retention_not_before: datetime | None = None,
) -> PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest:
    facts = _ROLE_FACTS[role]
    direction = facts["direction"]
    namespace = (
        binding.normal_object_storage_namespace
        if direction == _NORMAL_DIRECTION
        else binding.reverse_object_storage_namespace
    )
    observed = _whole_second_utc(
        observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_REQUEST_INVALID"
    )
    floor = (
        observed
        + timedelta(
            days=binding.minimum_retention_days,
            seconds=PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS,
        )
        if retention_not_before is None
        else _whole_second_utc(
            retention_not_before,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_REQUEST_INVALID",
        )
    )
    minimum_floor = observed + timedelta(days=binding.minimum_retention_days)
    maximum_floor = minimum_floor + timedelta(
        seconds=PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS
    )
    if floor < minimum_floor or floor > maximum_floor:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RETENTION_FLOOR_INVALID")
    return PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
        direction=direction,
        role=role,
        identity_sha256=identity_sha256,
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        endpoint=binding.endpoint,
        region=binding.region,
        bucket=binding.bucket,
        object_storage_namespace=namespace,
        probe_nonce_sha256=nonce,
        object_key=_immutability.derive_physical_arvan_s3_four_role_immutability_probe_object_key(
            binding=binding,
            direction=direction,
            probe_nonce_sha256=nonce,
        ),
        observed_at=observed,
        minimum_retention_days=binding.minimum_retention_days,
        retention_not_before=floor,
    )


def _call_adapter(
    adapter: PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter,
    request: object,
    *,
    code: str,
) -> object:
    """Call exactly one explicit local seam and redact every callback error."""

    try:
        return adapter.readback_adapter(request)
    except Exception:
        _fail(code)


def _publisher_readback(
    value: object,
    *,
    request: PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest,
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    require_bucket_readback: bool,
    bucket_readback: PhysicalArvanS3FourRoleImmutabilityBucketReadback | None = None,
) -> tuple[
    PhysicalArvanS3FourRoleImmutabilityPublisherReadback,
    PhysicalArvanS3FourRoleImmutabilityBucketReadback | None,
]:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityPublisherReadback:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID")
    _exact_text(
        value.schema,
        PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    _exact_text(
        value.direction,
        request.direction,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    _exact_text(
        value.role,
        request.role,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    _exact_text(
        value.object_key,
        request.object_key,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    _exact_text(
        value.create_only_outcome,
        _CREATE_ONLY,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    _exact_text(
        value.overwrite_outcome,
        _DENIED,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    _exact_text(
        value.object_removal_outcome,
        _DENIED,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    _exact_text(
        value.version_removal_outcome,
        _DENIED,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    if (
        _sha256(
            value.identity_sha256,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
        )
        != request.identity_sha256
        or _sha256(
            value.probe_nonce_sha256,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
        )
        != request.probe_nonce_sha256
        or type(value.object_version_id) is not str
        or VERSION_ID_RE.fullmatch(value.object_version_id) is None
        or value.object_version_id.lower() == "null"
        or type(value.content_bytes) is not int
        or not 1 <= value.content_bytes <= _MAX_OBJECT_BYTES
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID")
    _sha256(
        value.content_sha256,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    if require_bucket_readback:
        checked_bucket = _bucket_readback(value.bucket_readback, binding=binding)
    else:
        if value.bucket_readback is not None or bucket_readback is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID")
        checked_bucket = bucket_readback
    retention_until = _whole_second_utc(
        value.retention_until,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_PUBLISHER_READBACK_INVALID",
    )
    if retention_until < request.retention_not_before:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RETENTION_TOO_SHORT")
    return value, checked_bucket


def _immutable_version(
    publisher: PhysicalArvanS3FourRoleImmutabilityPublisherReadback,
) -> _immutability.PhysicalArvanS3FourRoleImmutableVersionObservation:
    return _immutability.PhysicalArvanS3FourRoleImmutableVersionObservation(
        probe_nonce_sha256=publisher.probe_nonce_sha256,
        object_key=publisher.object_key,
        object_version_id=publisher.object_version_id,
        content_sha256=publisher.content_sha256,
        content_bytes=publisher.content_bytes,
        retention_until=publisher.retention_until,
        exact_head_version_id=publisher.object_version_id,
        exact_get_version_id=publisher.object_version_id,
        exact_get_content_sha256=publisher.content_sha256,
        exact_get_content_bytes=publisher.content_bytes,
    )


def _receiver_request(
    *,
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    role: str,
    identity_sha256: str,
    immutable_version: _immutability.PhysicalArvanS3FourRoleImmutableVersionObservation,
    observed_at: datetime,
    retention_not_before: datetime,
) -> PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest:
    facts = _ROLE_FACTS[role]
    direction = facts["direction"]
    namespace = (
        binding.normal_object_storage_namespace
        if direction == _NORMAL_DIRECTION
        else binding.reverse_object_storage_namespace
    )
    observed = _whole_second_utc(
        observed_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_REQUEST_INVALID"
    )
    floor = _whole_second_utc(
        retention_not_before,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_REQUEST_INVALID",
    )
    # The receiver does not derive a new local retention floor.  It accepts
    # only the fixed Witness/publisher floor attached to the exact immutable
    # version, and still rejects a floor that cannot satisfy the binding.
    if (
        floor > immutable_version.retention_until
        or floor < observed + timedelta(days=binding.minimum_retention_days)
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RETENTION_FLOOR_INVALID")
    return PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
        direction=direction,
        role=role,
        identity_sha256=identity_sha256,
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        endpoint=binding.endpoint,
        region=binding.region,
        bucket=binding.bucket,
        object_storage_namespace=namespace,
        immutable_version=immutable_version,
        observed_at=observed,
        retention_not_before=floor,
    )


def _receiver_readback(
    value: object,
    *,
    request: PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest,
) -> PhysicalArvanS3FourRoleImmutabilityReceiverReadback:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityReceiverReadback:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID")
    version = request.immutable_version
    _exact_text(
        value.schema,
        PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.direction,
        request.direction,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.role,
        request.role,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.object_key,
        version.object_key,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.object_version_id,
        version.object_version_id,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.exact_head_version_id,
        version.object_version_id,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.exact_get_version_id,
        version.object_version_id,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.exact_get_content_sha256,
        version.content_sha256,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.put_outcome,
        _DENIED,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.object_removal_outcome,
        _DENIED,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.version_removal_outcome,
        _DENIED,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.bucket_enumeration_outcome,
        _DENIED,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    _exact_text(
        value.version_enumeration_outcome,
        _DENIED,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
    )
    if (
        _sha256(
            value.identity_sha256,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
        )
        != request.identity_sha256
        or _sha256(
            value.probe_nonce_sha256,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
        )
        != version.probe_nonce_sha256
        or value.exact_get_content_bytes != version.content_bytes
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID")
    return value


def _direction_observation(
    *,
    direction: str,
    publisher: PhysicalArvanS3FourRoleImmutabilityPublisherReadback,
    receiver: PhysicalArvanS3FourRoleImmutabilityReceiverReadback,
    bucket_readback: PhysicalArvanS3FourRoleImmutabilityBucketReadback,
    binding: _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
) -> _immutability.PhysicalArvanS3FourRoleImmutabilityDirectionObservation:
    if direction == _NORMAL_DIRECTION:
        publisher_role = _profiles.ARVAN_S3_FI_PUBLISHER_ROLE
        receiver_role = _profiles.ARVAN_S3_IR_RECEIVER_ROLE
        namespace = binding.normal_object_storage_namespace
    else:
        publisher_role = _profiles.ARVAN_S3_IR_PUBLISHER_ROLE
        receiver_role = _profiles.ARVAN_S3_FI_RECEIVER_ROLE
        namespace = binding.reverse_object_storage_namespace
    return _immutability.PhysicalArvanS3FourRoleImmutabilityDirectionObservation(
        direction=direction,
        publisher_role=publisher_role,
        receiver_role=receiver_role,
        object_storage_namespace=namespace,
        publisher_identity_sha256=publisher.identity_sha256,
        receiver_identity_sha256=receiver.identity_sha256,
        acl_posture=bucket_readback.acl_posture,
        versioning_status=bucket_readback.versioning_status,
        retention_mode=bucket_readback.retention_mode,
        retention_policy_evidence_sha256=_retention_policy_evidence_sha256(
            binding=binding,
            bucket_readback=bucket_readback,
        ),
        retention_days=bucket_readback.retention_days,
        immutable_version=_immutability.PhysicalArvanS3FourRoleImmutableVersionObservation(
            probe_nonce_sha256=publisher.probe_nonce_sha256,
            object_key=publisher.object_key,
            object_version_id=publisher.object_version_id,
            content_sha256=publisher.content_sha256,
            content_bytes=publisher.content_bytes,
            retention_until=publisher.retention_until,
            exact_head_version_id=receiver.exact_head_version_id,
            exact_get_version_id=receiver.exact_get_version_id,
            exact_get_content_sha256=receiver.exact_get_content_sha256,
            exact_get_content_bytes=receiver.exact_get_content_bytes,
        ),
        publisher_create_only_outcome=publisher.create_only_outcome,
        publisher_overwrite_outcome=publisher.overwrite_outcome,
        publisher_delete_object_outcome=publisher.object_removal_outcome,
        publisher_delete_version_outcome=publisher.version_removal_outcome,
        receiver_exact_head_outcome=_EXACT_HEAD,
        receiver_exact_get_outcome=_EXACT_GET,
        receiver_put_outcome=receiver.put_outcome,
        receiver_delete_object_outcome=receiver.object_removal_outcome,
        receiver_delete_version_outcome=receiver.version_removal_outcome,
        receiver_list_bucket_outcome=receiver.bucket_enumeration_outcome,
        receiver_list_versions_outcome=receiver.version_enumeration_outcome,
    )


class PhysicalArvanS3FourRoleImmutabilityLiveProbeRuntime:
    """Root-only bridge from four explicit local readbacks to pure evidence."""

    def __init__(
        self,
        config: PhysicalArvanS3FourRoleImmutabilityLiveProbeConfig = (
            PhysicalArvanS3FourRoleImmutabilityLiveProbeConfig()
        ),
    ) -> None:
        # Construction is intentionally inert.  In particular, adapters are
        # neither inspected nor called until collect() has passed root and
        # default-off admission.
        self._config = config

    def collect(
        self,
        *,
        admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
        live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
        failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
        observed_at: datetime,
    ) -> _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightObservation:
        """Validate four bounded role-local facts and build one observation.

        This function has no S3 operation surface.  The only callbacks are the
        four explicit ``readback_adapter`` values, and they are reached only
        after root, enabled-config, binding, time, nonce, and durable-IAM
        checks all pass.
        """

        try:
            if os.geteuid() != 0:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_REQUIRES_ROOT")
        except OSError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_REQUIRES_ROOT")
        facts = _runtime_facts(self._config)
        observed = _whole_second_utc(
            observed_at,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_TIME_INVALID",
        )
        checked_admission = _require_admission(
            binding=facts.binding,
            admission=admission,
            live_iam_binding=live_iam_binding,
            failback_binding=failback_binding,
            observed_at=observed,
        )
        normal_nonce = _fresh_nonce_commitment()
        reverse_nonce = _fresh_nonce_commitment()
        if normal_nonce == reverse_nonce:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_NONCE_COLLISION")

        normal_publisher_adapter = facts.adapters[_profiles.ARVAN_S3_FI_PUBLISHER_ROLE]
        normal_publisher_request = _publisher_request(
            binding=facts.binding,
            role=normal_publisher_adapter.role,
            identity_sha256=normal_publisher_adapter.identity_sha256,
            nonce=normal_nonce,
            observed_at=observed,
        )
        normal_publisher, bucket_readback = _publisher_readback(
            _call_adapter(
                normal_publisher_adapter,
                normal_publisher_request,
                code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_FI_PUBLISHER_ADAPTER_FAILED",
            ),
            request=normal_publisher_request,
            binding=facts.binding,
            require_bucket_readback=True,
        )
        if bucket_readback is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_BUCKET_READBACK_INVALID")
        normal_version = _immutable_version(normal_publisher)
        normal_receiver_adapter = facts.adapters[_profiles.ARVAN_S3_IR_RECEIVER_ROLE]
        normal_receiver_request = _receiver_request(
            binding=facts.binding,
            role=normal_receiver_adapter.role,
            identity_sha256=normal_receiver_adapter.identity_sha256,
            immutable_version=normal_version,
            observed_at=observed,
            retention_not_before=normal_publisher_request.retention_not_before,
        )
        normal_receiver = _receiver_readback(
            _call_adapter(
                normal_receiver_adapter,
                normal_receiver_request,
                code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_IR_RECEIVER_ADAPTER_FAILED",
            ),
            request=normal_receiver_request,
        )

        reverse_publisher_adapter = facts.adapters[_profiles.ARVAN_S3_IR_PUBLISHER_ROLE]
        reverse_publisher_request = _publisher_request(
            binding=facts.binding,
            role=reverse_publisher_adapter.role,
            identity_sha256=reverse_publisher_adapter.identity_sha256,
            nonce=reverse_nonce,
            observed_at=observed,
        )
        reverse_publisher, _ignored_bucket_readback = _publisher_readback(
            _call_adapter(
                reverse_publisher_adapter,
                reverse_publisher_request,
                code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_IR_PUBLISHER_ADAPTER_FAILED",
            ),
            request=reverse_publisher_request,
            binding=facts.binding,
            require_bucket_readback=False,
            bucket_readback=bucket_readback,
        )
        reverse_version = _immutable_version(reverse_publisher)
        reverse_receiver_adapter = facts.adapters[_profiles.ARVAN_S3_FI_RECEIVER_ROLE]
        reverse_receiver_request = _receiver_request(
            binding=facts.binding,
            role=reverse_receiver_adapter.role,
            identity_sha256=reverse_receiver_adapter.identity_sha256,
            immutable_version=reverse_version,
            observed_at=observed,
            retention_not_before=reverse_publisher_request.retention_not_before,
        )
        reverse_receiver = _receiver_readback(
            _call_adapter(
                reverse_receiver_adapter,
                reverse_receiver_request,
                code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_FI_RECEIVER_ADAPTER_FAILED",
            ),
            request=reverse_receiver_request,
        )
        try:
            return _immutability.build_physical_arvan_s3_four_role_immutability_preflight_observation(
                binding=facts.binding,
                admission=checked_admission,
                live_iam_binding=live_iam_binding,
                failback_binding=failback_binding,
                normal_direction=_direction_observation(
                    direction=_NORMAL_DIRECTION,
                    publisher=normal_publisher,
                    receiver=normal_receiver,
                    bucket_readback=bucket_readback,
                    binding=facts.binding,
                ),
                reverse_direction=_direction_observation(
                    direction=_REVERSE_DIRECTION,
                    publisher=reverse_publisher,
                    receiver=reverse_receiver,
                    bucket_readback=bucket_readback,
                    binding=facts.binding,
                ),
                observed_at=observed,
            )
        except _immutability.PhysicalArvanS3FourRoleImmutabilityPreflightError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_OBSERVATION_INVALID")
