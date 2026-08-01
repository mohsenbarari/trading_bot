"""FI-publisher-only Arvan S3 factory artifact for the normal data plane.

This module is self-contained with respect to client construction: it never
imports or instantiates the retired dual-role factory.  Its enabled paths
validate one normal route, verify a fresh normal preflight, and open only the
fixed FI publisher credential for one callback-scoped create-only client.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from core import physical_arvan_immutability_preflight as _preflight
from core import physical_arvan_s3_role_profiles as _profiles
from core import physical_arvan_s3_role_local_client_support as _client_support
from core import physical_arvan_s3_role_local_credential_reader as _credential_reader
from core.physical_arvan_s3_role_local_route_policy import (
    ArvanS3RoleLocalRoutePolicy,
    validate_physical_arvan_s3_role_local_route_policy,
)
from core.physical_arvan_s3_role_local_identity import (
    PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
    ArvanS3RoleLocalIdentityProjection,
)


__all__ = (
    "ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_DEFAULT_ENABLED",
    "ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_SCHEMA",
    "FIXED_ARVAN_S3_FI_PUBLISHER_ROLE_CREDENTIAL_FILE",
    "ArvanS3FiPublisherRoleFactoryError",
    "ArvanS3FiPublisherRoleHandoffAdmission",
    "RootOwnedArvanS3FiPublisherRoleFactory",
    "RootOwnedArvanS3FiPublisherRoleFactoryConfig",
    "load_root_owned_arvan_s3_fi_publisher_role_credential_facts",
    "validate_root_owned_arvan_s3_fi_publisher_role_factory_config",
)


ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_SCHEMA = (
    "gold-trade-physical-arvan-s3-fi-publisher-role-factory-v1"
)
ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_DEFAULT_ENABLED = False
_CLIENT_CONSTRUCTION_MODE = "root-owned-fi-publisher-only-s3v4-client-v1"
_HANDOFF_ADMISSION_SCHEMA = "gold-trade-physical-arvan-s3-fi-publisher-role-handoff-admission-v1"
_HANDOFF_CAPABILITY = object()
_RECOVERY_PREFIX = "physical-wal/"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
FIXED_ARVAN_S3_FI_PUBLISHER_ROLE_CREDENTIAL_FILE = Path(
    "/etc/trading-bot/security/arvan-s3-fi-publisher-credentials.json"
)


class ArvanS3FiPublisherRoleFactoryError(ValueError):
    """Stable redacted failure from the FI-only factory surface."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanS3FiPublisherRoleFactoryConfig:
    """Default-off non-secret policy for the FI publisher role only."""

    schema: str = ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_SCHEMA
    route_policy: ArvanS3RoleLocalRoutePolicy | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    enabled: bool = ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_DEFAULT_ENABLED
    client_construction_mode: str = _CLIENT_CONSTRUCTION_MODE


@dataclass(frozen=True)
class ArvanS3FiPublisherRoleHandoffAdmission:
    """Opaque FI-only preflight admission, never a release authority."""

    schema: str
    campaign_id: str
    release_sha: str
    route_binding_sha256: str
    observed_at: datetime
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    _preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight = field(
        repr=False,
        compare=False,
    )
    _binding: _preflight.PhysicalArvanImmutabilityPreflightBinding = field(
        repr=False,
        compare=False,
    )
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FI_PUBLISHER_ROLE_HANDOFF_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Facts:
    route_policy: ArvanS3RoleLocalRoutePolicy
    endpoint: str
    region: str
    bucket: str


@dataclass(frozen=True)
class _Route:
    region: str
    bucket: str


def _fail(code: str) -> None:
    raise ArvanS3FiPublisherRoleFactoryError(code)


def _facts(value: object, *, require_enabled: bool) -> _Facts:
    if type(value) is not RootOwnedArvanS3FiPublisherRoleFactoryConfig:
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_CONFIG_INVALID")
    if (
        value.schema != ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_SCHEMA
        or type(value.enabled) is not bool
        or value.client_construction_mode != _CLIENT_CONSTRUCTION_MODE
        or type(value.route_policy) is not ArvanS3RoleLocalRoutePolicy
    ):
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_DISABLED")
    try:
        route_policy = validate_physical_arvan_s3_role_local_route_policy(
            value.route_policy,
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_object_storage_namespace="physical-wal",
            require_enabled=require_enabled,
        )
    except Exception:
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_CONFIG_INVALID")
    if route_policy.enabled is not value.enabled:
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_ENABLED_MISMATCH")
    return _Facts(
        route_policy=route_policy,
        endpoint=route_policy.endpoint,
        region=route_policy.region,
        bucket=route_policy.bucket,
    )


def validate_root_owned_arvan_s3_fi_publisher_role_factory_config(
    config: RootOwnedArvanS3FiPublisherRoleFactoryConfig,
) -> RootOwnedArvanS3FiPublisherRoleFactoryConfig:
    """Pure validation; no credential, SDK, provider, or client action."""

    facts = _facts(config, require_enabled=False)
    return RootOwnedArvanS3FiPublisherRoleFactoryConfig(
        schema=ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_SCHEMA,
        route_policy=facts.route_policy,
        enabled=config.enabled,
        client_construction_mode=_CLIENT_CONSTRUCTION_MODE,
    )


def _verified_preflight(
    value: object,
    *,
    facts: _Facts,
    now: datetime,
) -> tuple[
    _preflight.VerifiedPhysicalArvanImmutabilityPreflight,
    _preflight.PhysicalArvanImmutabilityPreflightBinding,
    str,
    str,
]:
    if type(value) is not _preflight.VerifiedPhysicalArvanImmutabilityPreflight:
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_PREFLIGHT_INVALID")
    binding = value.binding
    try:
        verified = _preflight.require_verified_physical_arvan_immutability_preflight(
            value,
            binding=binding,
            now=now,
        )
    except Exception:
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_PREFLIGHT_INVALID")
    if (
        verified is not value
        or type(binding) is not _preflight.PhysicalArvanImmutabilityPreflightBinding
        or binding.endpoint != facts.endpoint
        or binding.region != facts.region
        or binding.bucket != facts.bucket
        or binding.source_site != "webapp_fi"
        or binding.destination_site != "webapp_ir"
    ):
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_PREFLIGHT_MISMATCH")
    try:
        by_role = {item.role: item for item in verified.observation.credential_restrictions}
        fi_identity = by_role[_profiles.ARVAN_S3_FI_PUBLISHER_ROLE].credential_identity_sha256
        ir_identity = by_role[_profiles.ARVAN_S3_IR_RECEIVER_ROLE].credential_identity_sha256
    except Exception:
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_PREFLIGHT_INVALID")
    if (
        type(fi_identity) is not str
        or type(ir_identity) is not str
        or _HEX64_RE.fullmatch(fi_identity) is None
        or _HEX64_RE.fullmatch(ir_identity) is None
        or fi_identity == "0" * 64
        or ir_identity == "0" * 64
        or fi_identity == ir_identity
    ):
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_PREFLIGHT_INVALID")
    return verified, binding, fi_identity, ir_identity


def _admission(
    value: object,
    *,
    facts: _Facts,
    now: datetime,
) -> ArvanS3FiPublisherRoleHandoffAdmission:
    if (
        type(value) is not ArvanS3FiPublisherRoleHandoffAdmission
        or value._capability is not _HANDOFF_CAPABILITY
        or value.schema != _HANDOFF_ADMISSION_SCHEMA
    ):
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_ADMISSION_REQUIRED")
    verified, binding, fi_identity, ir_identity = _verified_preflight(
        value._preflight,
        facts=facts,
        now=now,
    )
    if (
        value._binding != binding
        or value.campaign_id != binding.campaign_id
        or value.release_sha != binding.release_sha
        or value.route_binding_sha256 != binding.route_binding_sha256
        or value.observed_at != verified.observation.observed_at
        or value.fi_publisher_identity_sha256 != fi_identity
        or value.ir_receiver_identity_sha256 != ir_identity
    ):
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_ADMISSION_INVALID")
    return value


def _load_fi_publisher_credential(
    facts: _Facts,
) -> tuple[
    _credential_reader.ArvanS3RoleLocalRouteFacts,
    _credential_reader.ArvanS3RoleLocalCredentialFacts,
]:
    try:
        return _credential_reader.load_root_owned_arvan_s3_role_local_credential(
            route_policy=facts.route_policy,
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_object_storage_namespace="physical-wal",
            expected_role=_profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
            expected_action_profile=_profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
            fixed_credential_file=FIXED_ARVAN_S3_FI_PUBLISHER_ROLE_CREDENTIAL_FILE,
        )
    except Exception:
        _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_CREDENTIAL_ADMISSION_FAILED")


def load_root_owned_arvan_s3_fi_publisher_role_credential_facts(
    route_policy: ArvanS3RoleLocalRoutePolicy,
) -> tuple[
    _credential_reader.ArvanS3RoleLocalRouteFacts,
    _credential_reader.ArvanS3RoleLocalCredentialFacts,
]:
    """Role-local credential handoff for FI-only reviewed normal publishers.

    This has no IR credential/path input and cannot construct a client or
    bypass the role-local route validation.  It exists for narrow reviewed
    normal publication runtimes that need the FI publisher identity before a
    callback admission object is relevant.
    """

    facts = _facts(
        RootOwnedArvanS3FiPublisherRoleFactoryConfig(
            route_policy=route_policy,
            enabled=True,
        ),
        require_enabled=True,
    )
    return _load_fi_publisher_credential(facts)


class _FiPublisherRecoveryClient:
    """Callback-only FI surface: immutable write plus exact readback."""

    __slots__ = ("_bucket", "__raw")

    def __init__(self, *, raw: object, bucket: str) -> None:
        object.__setattr__(self, "_FiPublisherRecoveryClient__raw", raw)
        self._bucket = bucket

    def __getattribute__(self, name: str) -> Any:
        if name in {"_raw", "_FiPublisherRecoveryClient__raw"}:
            raise AttributeError("private raw client is not exposed")
        return object.__getattribute__(self, name)

    def _call(self, method_name: str, request: Mapping[str, Any]) -> Any:
        if type(request) is not dict or request.get("Bucket") != self._bucket:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_BUCKET_MISMATCH")
        try:
            raw = object.__getattribute__(self, "_FiPublisherRecoveryClient__raw")
            method = getattr(raw, method_name, None)
            if not callable(method):
                _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_CLIENT_INVALID")
            return method(**dict(request))
        except ArvanS3FiPublisherRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_CLIENT_OPERATION_FAILED")

    @staticmethod
    def _recovery_key(value: object) -> str:
        if type(value) is not str or not value.startswith(_RECOVERY_PREFIX) or len(value) > 512 or "\x00" in value:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_RECOVERY_KEY_INVALID")
        components = value.split("/")
        if len(components) < 3 or any(
            not item
            or item in {".", ".."}
            or item.lower() in {"alias", "current", "head", "latest", "pointer"}
            for item in components
        ):
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_RECOVERY_KEY_INVALID")
        return value

    def get_bucket_versioning(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_versioning", {"Bucket": Bucket})

    def get_bucket_acl(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_acl", {"Bucket": Bucket})

    def list_object_versions(self, **request: Any) -> Any:
        value = dict(request)
        allowed = {"Bucket", "Prefix", "KeyMarker", "VersionIdMarker"}
        if not {"Bucket", "Prefix"}.issubset(value) or set(value) - allowed:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_LIST_REQUEST_INVALID")
        self._recovery_key(value["Prefix"])
        for field in ("KeyMarker", "VersionIdMarker"):
            if field in value and (type(value[field]) is not str or not value[field]):
                _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_LIST_REQUEST_INVALID")
        return self._call("list_object_versions", value)

    def put_object(self, **request: Any) -> Any:
        value = dict(request)
        expected = {"Bucket", "Key", "Body", "ContentLength", "Metadata", "ContentType", "IfNoneMatch"}
        if set(value) != expected:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_PUT_REQUEST_INVALID")
        self._recovery_key(value["Key"])
        if (
            value["IfNoneMatch"] != "*"
            or type(value["ContentLength"]) is not int
            or value["ContentLength"] < 1
            or value["ContentType"] != "application/octet-stream"
            or not isinstance(value["Metadata"], Mapping)
            or not callable(getattr(value["Body"], "read", None))
        ):
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_PUT_REQUEST_INVALID")
        return self._call("put_object", value)

    def head_object(self, **request: Any) -> Any:
        value = dict(request)
        if set(value) != {"Bucket", "Key", "VersionId"} or type(value["VersionId"]) is not str or not value["VersionId"]:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_READ_REQUEST_INVALID")
        self._recovery_key(value["Key"])
        return self._call("head_object", value)

    def get_object(self, **request: Any) -> Any:
        value = dict(request)
        if set(value) != {"Bucket", "Key", "VersionId"} or type(value["VersionId"]) is not str or not value["VersionId"]:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_READ_REQUEST_INVALID")
        self._recovery_key(value["Key"])
        return self._call("get_object", value)


class RootOwnedArvanS3FiPublisherRoleFactory:
    """One-role root factory; it has no receiver or two-client API."""

    __slots__ = ("_config",)

    def __init__(self, config: RootOwnedArvanS3FiPublisherRoleFactoryConfig) -> None:
        self._config = validate_root_owned_arvan_s3_fi_publisher_role_factory_config(config)

    def identity_projection(self) -> ArvanS3RoleLocalIdentityProjection:
        facts = _facts(self._config, require_enabled=True)
        try:
            _client_support.require_role_local_root()
            _route, credential = _load_fi_publisher_credential(facts)
        except Exception:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_CREDENTIAL_ADMISSION_FAILED")
        return ArvanS3RoleLocalIdentityProjection(
            schema=PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
            role=_profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
            identity_sha256=credential.identity_sha256,
            action_profile=_profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            object_storage_namespace="physical-wal",
            allowed_operations=_profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS,
        )

    def admit_fi_publisher_recovery_handoff(
        self,
        *,
        preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight,
        now: datetime,
    ) -> ArvanS3FiPublisherRoleHandoffAdmission:
        facts = _facts(self._config, require_enabled=True)
        try:
            _client_support.require_role_local_root()
            verified, binding, fi_identity, ir_identity = _verified_preflight(
                preflight,
                facts=facts,
                now=now,
            )
        except ArvanS3FiPublisherRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_PREFLIGHT_ADMISSION_FAILED")
        result = ArvanS3FiPublisherRoleHandoffAdmission(
            schema=_HANDOFF_ADMISSION_SCHEMA,
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            route_binding_sha256=binding.route_binding_sha256,
            observed_at=verified.observation.observed_at,
            fi_publisher_identity_sha256=fi_identity,
            ir_receiver_identity_sha256=ir_identity,
            _preflight=verified,
            _binding=binding,
        )
        object.__setattr__(result, "_capability", _HANDOFF_CAPABILITY)
        return result

    def require_fi_publisher_recovery_handoff_admission(
        self,
        admission: object,
        *,
        now: datetime,
    ) -> ArvanS3FiPublisherRoleHandoffAdmission:
        facts = _facts(self._config, require_enabled=True)
        try:
            _client_support.require_role_local_root()
            return _admission(admission, facts=facts, now=now)
        except ArvanS3FiPublisherRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_ADMISSION_INVALID")

    def execute_fi_publisher_recovery_handoff(
        self,
        *,
        admission: object,
        now: datetime,
        operation: Callable[[object, object], object] | None,
    ) -> object:
        facts = _facts(self._config, require_enabled=True)
        try:
            _client_support.require_role_local_root()
            checked = _admission(admission, facts=facts, now=now)
        except ArvanS3FiPublisherRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_ADMISSION_INVALID")
        if operation is None or not callable(operation):
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_OPERATION_INVALID")
        try:
            route_facts, credential = _load_fi_publisher_credential(facts)
        except Exception:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_CREDENTIAL_ADMISSION_FAILED")
        if (
            type(route_facts) is not _credential_reader.ArvanS3RoleLocalRouteFacts
            or type(credential) is not _credential_reader.ArvanS3RoleLocalCredentialFacts
            or route_facts.endpoint != facts.endpoint
            or route_facts.region != facts.region
            or route_facts.bucket != facts.bucket
            or credential.identity_sha256 != checked.fi_publisher_identity_sha256
            or credential.identity_sha256 == checked.ir_receiver_identity_sha256
        ):
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_CREDENTIAL_MISMATCH")
        try:
            boto3_module, botocore_config_module = _client_support.load_role_local_boto_sdk()
            raw = _client_support.create_role_local_raw_s3_client(
                boto3_module=boto3_module,
                botocore_config_module=botocore_config_module,
                endpoint=facts.endpoint,
                region=facts.region,
                access_key=credential.access_key,
                secret_key=credential.secret_key,
            )
            client = _FiPublisherRecoveryClient(raw=raw, bucket=facts.bucket)
            route = _Route(region=facts.region, bucket=facts.bucket)
            result = operation(client, route)
        except ArvanS3FiPublisherRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_OPERATION_FAILED")
        if result is client or result is route:
            _fail("ARVAN_S3_FI_PUBLISHER_ROLE_FACTORY_OPERATION_RESULT_INVALID")
        return result
