"""Pure four-role compatibility binding for the reverse Object-Storage preflight.

This is the deliberately narrow bridge between four *one-role* identity
projections, two non-secret directed route policies, and the redacted reverse
preflight.  It converts neither hashes nor profile strings into provider/IAM
evidence: a later Witness-signed live receipt collector remains responsible
for provider observations.  Its only job is to make a preflight refuse unless
all four separately projected machine identities use the exact canonical
profiles and match one deterministic route commitment.

There is no legacy paired-loader alias path.  In particular, this module must
never import, accept, or derive a route from the retired normal/reverse paired
credential-loader configuration types: those configurations disclose a
second role's secret boundary and are not valid enabled-preflight input.  A
former ``fi-publisher-immutable-preflight-v1`` credential/configuration must
be reprovisioned under ``fi-publisher-immutable-create-only-v1`` before this
binding can be created.  The module does no I/O, credential loading, SDK
construction, provider call, network, subprocess, or root check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re

from core import physical_arvan_s3_role_profiles as _profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as _preflight
from core.physical_arvan_s3_failback_route_commitment import (
    PhysicalArvanS3FailbackRouteCommitmentError,
    derive_physical_arvan_s3_failback_four_role_route_binding_sha256,
    derive_physical_arvan_s3_failback_route_scope_sha256,
)
from core.physical_arvan_s3_role_local_identity import (
    PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
    ArvanS3RoleLocalIdentityProjection,
)
from core.physical_arvan_s3_role_local_route_policy import (
    ArvanS3RoleLocalRoutePolicy,
    ArvanS3RoleLocalRoutePolicyError,
    validate_physical_arvan_s3_role_local_route_policy,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
)


__all__ = (
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_SCHEMA",
    "PhysicalArvanS3FourRolePreflightBindingError",
    "VerifiedPhysicalArvanS3FourRolePreflightBinding",
    "bind_physical_arvan_s3_four_role_preflight",
    "derive_physical_ir_to_fi_object_storage_failback_binding",
    "require_verified_physical_arvan_s3_four_role_preflight_binding",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-preflight-binding-v1"
)

_ROUTE_COMMITMENT_SCHEMA = "gold-trade-physical-arvan-s3-four-role-route-commitment-v1"
_NORMAL_PREFIX = PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE + "/"
_REVERSE_PREFIX = PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE + "/"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_CAPABILITY = object()


class PhysicalArvanS3FourRolePreflightBindingError(ValueError):
    """A four-role compatibility/projection binding is not exact."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRolePreflightBinding:
    """Opaque local compatibility gate, explicitly not provider evidence."""

    schema: str
    binding: _preflight.PhysicalIrToFiObjectStorageFailbackBinding
    projection_commitment_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRolePreflightBindingError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError):
        _fail(code)


def _digest(value: object, *, code: str) -> str:
    return hashlib.sha256(_canonical(value, code=code)).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _normal_route_policy(value: object) -> ArvanS3RoleLocalRoutePolicy:
    """Accept only the one normal non-secret route policy.

    A legacy paired loader configuration is a different exact type and is
    therefore rejected before any route field is read.
    """

    if type(value) is not ArvanS3RoleLocalRoutePolicy:
        _fail("ARVAN_S3_FOUR_ROLE_NORMAL_ROUTE_POLICY_INVALID")
    if value.enabled is not True:
        _fail("ARVAN_S3_FOUR_ROLE_NORMAL_ROUTE_POLICY_DISABLED")
    try:
        return validate_physical_arvan_s3_role_local_route_policy(
            value,
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
            require_enabled=True,
        )
    except ArvanS3RoleLocalRoutePolicyError:
        _fail("ARVAN_S3_FOUR_ROLE_NORMAL_ROUTE_POLICY_INVALID")


def _reverse_route_policy(value: object) -> ArvanS3RoleLocalRoutePolicy:
    """Accept only the one reverse non-secret route policy.

    A legacy paired loader configuration is a different exact type and is
    therefore rejected before any route field is read.
    """

    if type(value) is not ArvanS3RoleLocalRoutePolicy:
        _fail("ARVAN_S3_FOUR_ROLE_REVERSE_ROUTE_POLICY_INVALID")
    if value.enabled is not True:
        _fail("ARVAN_S3_FOUR_ROLE_REVERSE_ROUTE_POLICY_DISABLED")
    try:
        return validate_physical_arvan_s3_role_local_route_policy(
            value,
            expected_source_site="webapp_ir",
            expected_destination_site="webapp_fi",
            expected_object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
            require_enabled=True,
        )
    except ArvanS3RoleLocalRoutePolicyError:
        _fail("ARVAN_S3_FOUR_ROLE_REVERSE_ROUTE_POLICY_INVALID")


def _normal_scope(
    value: ArvanS3RoleLocalRoutePolicy,
    *,
    campaign_id: str,
    release_sha: str,
) -> str:
    return _digest(
        {
            "schema": _ROUTE_COMMITMENT_SCHEMA,
            "route": "normal-fi-publisher-to-ir-receiver",
            "campaign_id": campaign_id,
            "release_sha": release_sha,
            "endpoint": value.endpoint,
            "region": value.region,
            "bucket": value.bucket,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "object_storage_namespace": PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
            "exact_prefix": _NORMAL_PREFIX + campaign_id + "/" + release_sha + "/",
            "profiles": [
                {
                    "role": _profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
                    "action_profile": _profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
                    "allowed_operations": list(_profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS),
                },
                {
                    "role": _profiles.ARVAN_S3_IR_RECEIVER_ROLE,
                    "action_profile": _profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
                    "allowed_operations": list(_profiles.ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS),
                },
            ],
        },
        code="ARVAN_S3_FOUR_ROLE_ROUTE_COMMITMENT_INVALID",
    )


def _reverse_scope(
    value: ArvanS3RoleLocalRoutePolicy,
    *,
    campaign_id: str,
    release_sha: str,
) -> str:
    try:
        return derive_physical_arvan_s3_failback_route_scope_sha256(
            campaign_id=campaign_id,
            release_sha=release_sha,
            endpoint=value.endpoint,
            region=value.region,
            bucket=value.bucket,
        )
    except PhysicalArvanS3FailbackRouteCommitmentError:
        _fail("ARVAN_S3_FOUR_ROLE_ROUTE_COMMITMENT_INVALID")


def _route_binding(
    *,
    campaign_id: object,
    release_sha: object,
    normal_scope: str,
    reverse_scope: str,
    fi_publisher_identity_sha256: object,
    ir_receiver_identity_sha256: object,
    ir_publisher_identity_sha256: object,
    fi_receiver_identity_sha256: object,
) -> str:
    try:
        return derive_physical_arvan_s3_failback_four_role_route_binding_sha256(
            campaign_id=campaign_id,
            release_sha=release_sha,
            normal_route_scope_sha256=normal_scope,
            reverse_route_scope_sha256=reverse_scope,
            fi_publisher_identity_sha256=fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=fi_receiver_identity_sha256,
        )
    except PhysicalArvanS3FailbackRouteCommitmentError:
        _fail("ARVAN_S3_FOUR_ROLE_ROUTE_COMMITMENT_INVALID")


def derive_physical_ir_to_fi_object_storage_failback_binding(
    *,
    campaign_id: str,
    release_sha: str,
    fi_publisher_identity_sha256: str,
    ir_receiver_identity_sha256: str,
    ir_publisher_identity_sha256: str,
    fi_receiver_identity_sha256: str,
    normal_route_policy: ArvanS3RoleLocalRoutePolicy,
    reverse_route_policy: ArvanS3RoleLocalRoutePolicy,
) -> _preflight.PhysicalIrToFiObjectStorageFailbackBinding:
    """Derive the only accepted route hashes from exact local route policies.

    This derives a policy commitment; it does not inspect a file or establish
    that a machine user actually has the declared provider permissions.
    """

    normal = _normal_route_policy(normal_route_policy)
    reverse = _reverse_route_policy(reverse_route_policy)
    normal_scope = _normal_scope(normal, campaign_id=campaign_id, release_sha=release_sha)
    reverse_scope = _reverse_scope(reverse, campaign_id=campaign_id, release_sha=release_sha)
    candidate = _preflight.PhysicalIrToFiObjectStorageFailbackBinding(
        campaign_id=campaign_id,
        release_sha=release_sha,
        route_binding_sha256=_route_binding(
            campaign_id=campaign_id,
            release_sha=release_sha,
            normal_scope=normal_scope,
            reverse_scope=reverse_scope,
            fi_publisher_identity_sha256=fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=fi_receiver_identity_sha256,
        ),
        normal_route_scope_sha256=normal_scope,
        reverse_route_scope_sha256=reverse_scope,
        fi_publisher_identity_sha256=fi_publisher_identity_sha256,
        ir_receiver_identity_sha256=ir_receiver_identity_sha256,
        ir_publisher_identity_sha256=ir_publisher_identity_sha256,
        fi_receiver_identity_sha256=fi_receiver_identity_sha256,
    )
    try:
        return _preflight.validate_physical_ir_to_fi_object_storage_failback_binding(candidate)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_ROUTE_COMMITMENT_INVALID")


def _normal_projection(
    value: object,
    *,
    role: str,
    profile: str,
    operations: tuple[str, ...],
) -> str:
    # The legacy normal paired-factory projection has a different type and is
    # deliberately rejected here.  Full-Matrix preflight must receive the
    # dedicated one-role artifact output instead.
    if type(value) is not ArvanS3RoleLocalIdentityProjection:
        _fail("ARVAN_S3_FOUR_ROLE_NORMAL_PROJECTION_INVALID")
    if (
        value.schema != PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA
        or value.role != role
        or value.source_site != "webapp_fi"
        or value.destination_site != "webapp_ir"
        or value.object_storage_namespace != PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE
        or value.allowed_operations != operations
    ):
        _fail("ARVAN_S3_FOUR_ROLE_NORMAL_PROJECTION_INVALID")
    try:
        _profiles.require_canonical_arvan_s3_role_profile(
            role=value.role,
            action_profile=value.action_profile,
        )
    except _profiles.ArvanS3RoleProfileError:
        _fail("ARVAN_S3_FOUR_ROLE_NORMAL_PROFILE_INVALID")
    return _sha256(value.identity_sha256, code="ARVAN_S3_FOUR_ROLE_NORMAL_PROJECTION_INVALID")


def _reverse_projection(
    value: object,
    *,
    role: str,
    profile: str,
    operations: tuple[str, ...],
) -> str:
    # A dual-role reverse factory projection is likewise not a valid
    # Full-Matrix input.  Only the corresponding one-role facade may emit
    # this grammar.
    if type(value) is not ArvanS3RoleLocalIdentityProjection:
        _fail("ARVAN_S3_FOUR_ROLE_REVERSE_PROJECTION_INVALID")
    if (
        value.schema != PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA
        or value.role != role
        or value.source_site != "webapp_ir"
        or value.destination_site != "webapp_fi"
        or value.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or value.allowed_operations != operations
    ):
        _fail("ARVAN_S3_FOUR_ROLE_REVERSE_PROJECTION_INVALID")
    try:
        _profiles.require_canonical_arvan_s3_role_profile(role=value.role, action_profile=value.action_profile)
    except _profiles.ArvanS3RoleProfileError:
        _fail("ARVAN_S3_FOUR_ROLE_REVERSE_PROFILE_INVALID")
    return _sha256(value.identity_sha256, code="ARVAN_S3_FOUR_ROLE_REVERSE_PROJECTION_INVALID")


def _projection_commitment(
    *,
    binding: _preflight.PhysicalIrToFiObjectStorageFailbackBinding,
) -> str:
    return _digest(
        {
            "schema": PHYSICAL_ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_SCHEMA,
            "binding": {
                "campaign_id": binding.campaign_id,
                "release_sha": binding.release_sha,
                "route_binding_sha256": binding.route_binding_sha256,
                "normal_route_scope_sha256": binding.normal_route_scope_sha256,
                "reverse_route_scope_sha256": binding.reverse_route_scope_sha256,
                "fi_publisher_identity_sha256": binding.fi_publisher_identity_sha256,
                "ir_receiver_identity_sha256": binding.ir_receiver_identity_sha256,
                "ir_publisher_identity_sha256": binding.ir_publisher_identity_sha256,
                "fi_receiver_identity_sha256": binding.fi_receiver_identity_sha256,
            },
            "identity_profiles": [list(item) for item in _profiles.ARVAN_S3_FOUR_ROLE_IDENTITY_PROFILES],
        },
        code="ARVAN_S3_FOUR_ROLE_PROJECTION_COMMITMENT_INVALID",
    )


def bind_physical_arvan_s3_four_role_preflight(
    *,
    binding: _preflight.PhysicalIrToFiObjectStorageFailbackBinding,
    normal_route_policy: ArvanS3RoleLocalRoutePolicy,
    reverse_route_policy: ArvanS3RoleLocalRoutePolicy,
    fi_publisher_projection: object,
    ir_receiver_projection: object,
    ir_publisher_projection: object,
    fi_receiver_projection: object,
) -> VerifiedPhysicalArvanS3FourRolePreflightBinding:
    """Bind exact real-factory projections to one deterministically derived policy.

    The projections are public identity facts from the normal/reverse factory
    seams.  This function verifies their exact type, roles, canonical action
    profiles, and bounded operation surfaces, then requires the supplied
    preflight binding to equal the derivation from those facts and the two
    one-route, non-secret policies.  Retired paired loader configurations are
    rejected by exact type, rather than being adapted or aliased.  It creates
    no provider evidence or execution authority.
    """

    try:
        checked_binding = _preflight.validate_physical_ir_to_fi_object_storage_failback_binding(
            binding
        )
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_INVALID")
    fi_publisher = _normal_projection(
        fi_publisher_projection,
        role=_profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
        profile=_profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
        operations=_profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS,
    )
    ir_receiver = _normal_projection(
        ir_receiver_projection,
        role=_profiles.ARVAN_S3_IR_RECEIVER_ROLE,
        profile=_profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
        operations=_profiles.ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS,
    )
    ir_publisher = _reverse_projection(
        ir_publisher_projection,
        role=_profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
        profile=_profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
        operations=_profiles.ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS,
    )
    fi_receiver = _reverse_projection(
        fi_receiver_projection,
        role=_profiles.ARVAN_S3_FI_RECEIVER_ROLE,
        profile=_profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
        operations=_profiles.ARVAN_S3_FI_RECEIVER_EXPECTED_ACTIONS,
    )
    derived = derive_physical_ir_to_fi_object_storage_failback_binding(
        campaign_id=checked_binding.campaign_id,
        release_sha=checked_binding.release_sha,
        fi_publisher_identity_sha256=fi_publisher,
        ir_receiver_identity_sha256=ir_receiver,
        ir_publisher_identity_sha256=ir_publisher,
        fi_receiver_identity_sha256=fi_receiver,
        normal_route_policy=normal_route_policy,
        reverse_route_policy=reverse_route_policy,
    )
    if checked_binding != derived:
        _fail("ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_MISMATCH")
    result = VerifiedPhysicalArvanS3FourRolePreflightBinding(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_SCHEMA,
        binding=derived,
        projection_commitment_sha256=_projection_commitment(binding=derived),
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def require_verified_physical_arvan_s3_four_role_preflight_binding(
    value: object,
    *,
    binding: _preflight.PhysicalIrToFiObjectStorageFailbackBinding,
) -> VerifiedPhysicalArvanS3FourRolePreflightBinding:
    """Consume only this process's exact local compatibility binding."""

    try:
        checked_binding = _preflight.validate_physical_ir_to_fi_object_storage_failback_binding(
            binding
        )
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_INVALID")
    if (
        type(value) is not VerifiedPhysicalArvanS3FourRolePreflightBinding
        or value._capability is not _CAPABILITY
        or value.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_SCHEMA
        or value.binding != checked_binding
        or value.projection_commitment_sha256 != _projection_commitment(binding=checked_binding)
    ):
        _fail("ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_REQUIRED")
    return value
