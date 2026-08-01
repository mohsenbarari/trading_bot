"""Fail-closed Arvan client construction for preflight and FI recovery handoff.

This module is the only credential-to-client bridge for the two independently
admitted FI-publisher and WA-IR-receiver machine users.  It has no import-time
SDK, credential-file, network, Object-Storage, SSH, Docker, or shell action.
The public surface never returns a credential path, access/secret key, raw
client, or a serializable client wrapper.

An explicit root-only collection call validates the route/binding, opens both
fixed files through the private separated-credential admission helper, creates
two separate path-style S3 clients inside this wrapper, and passes them
directly to the existing injected immutability live probe.  The clients and
key material remain local temporary implementation state and are never
returned to a caller.

The same root wrapper also has one narrower FI-only recovery-handoff path.
That path admits a *fresh, already verified* paired preflight, reopens only
the fixed FI publisher credential, compares its public identity to the
preflight's FI/IR identity pins, and invokes one injected local operation with
a private, bucket-scoped, create-only recovery client.  It neither opens the
IR secret nor exposes a raw client, endpoint selector, generic S3 session, or
direct FI-to-IR channel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import importlib
import os
from typing import Any

from core import physical_arvan_s3_separated_credential_loader as _credentials
from core import physical_arvan_immutability_preflight as _preflight
from core.physical_arvan_immutability_preflight import (
    PhysicalArvanImmutabilityPreflightBinding,
    PhysicalArvanImmutabilityPreflightObservation,
)
from core.physical_arvan_s3_immutability_live_probe import (
    PhysicalArvanS3ImmutabilityLiveProbe,
    PhysicalArvanS3ImmutabilityLiveProbeConfig,
    PhysicalArvanS3ImmutabilityLiveProbeError,
    PhysicalArvanS3ImmutabilityScopedClient,
)


__all__ = (
    "ARVAN_S3_SEPARATED_CLIENT_FACTORY_DEFAULT_ENABLED",
    "ARVAN_S3_SEPARATED_LEGACY_PAIRED_FACTORY_STATUS",
    "ARVAN_S3_SEPARATED_CLIENT_FACTORY_SCHEMA",
    "ArvanS3FiPublisherRecoveryHandoffAdmission",
    "ArvanS3NormalLocalIdentityProjection",
    "ArvanS3SeparatedClientFactoryCredentialProjection",
    "ArvanS3SeparatedClientFactoryError",
    "RootOwnedArvanS3SeparatedClientFactory",
    "RootOwnedArvanS3SeparatedClientFactoryConfig",
    "validate_root_owned_arvan_s3_separated_client_factory_config",
)


ARVAN_S3_SEPARATED_CLIENT_FACTORY_SCHEMA = (
    "gold-trade-physical-arvan-s3-separated-client-factory-v1"
)
ARVAN_S3_SEPARATED_CLIENT_FACTORY_DEFAULT_ENABLED = False
ARVAN_S3_SEPARATED_LEGACY_PAIRED_FACTORY_STATUS = "tombstoned-no-production-route-v1"

_CLIENT_CONSTRUCTION_MODE = "root-owned-two-machine-user-s3v4-clients-v1"
_CONNECT_TIMEOUT_SECONDS = 5
_READ_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 2
_FI_PUBLISHER_RECOVERY_HANDOFF_ADMISSION_SCHEMA = (
    "gold-trade-physical-arvan-s3-fi-publisher-recovery-handoff-admission-v1"
)
_FI_PUBLISHER_RECOVERY_HANDOFF_CAPABILITY = object()
_PHYSICAL_RECOVERY_OBJECT_PREFIX = "physical-wal/"


class ArvanS3SeparatedClientFactoryError(ValueError):
    """Fixed-code failure that never includes keys, paths, clients, or SDK text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArvanS3FiPublisherRecoveryHandoffAdmission:
    """Opaque fresh FI recovery-publisher admission, never an authority.

    The retained verified preflight and its binding are deliberately hidden;
    they contain only non-secret route facts but must not become a caller
    controlled endpoint/bucket selector.  The public fields are limited to
    lineage, freshness, and the two public machine-user fingerprints.  This
    value cannot be serialized or constructed into a trusted capability.
    """

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
        raise TypeError("ARVAN_S3_FI_PUBLISHER_RECOVERY_HANDOFF_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class RootOwnedArvanS3SeparatedClientFactoryConfig:
    """Default-off non-secret policy around the separate credential admission."""

    schema: str = ARVAN_S3_SEPARATED_CLIENT_FACTORY_SCHEMA
    credential_loader_config: _credentials.RootOwnedArvanS3SeparatedCredentialLoaderConfig | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    enabled: bool = ARVAN_S3_SEPARATED_CLIENT_FACTORY_DEFAULT_ENABLED
    client_construction_mode: str = _CLIENT_CONSTRUCTION_MODE


@dataclass(frozen=True)
class ArvanS3SeparatedClientFactoryCredentialProjection:
    """Only public FI/IR role, action, profile, and fingerprint facts."""

    schema: str
    fi_publisher_role: str
    fi_publisher_identity_sha256: str
    fi_publisher_action_profile: str
    fi_publisher_allowed_operations: tuple[str, ...]
    ir_receiver_role: str
    ir_receiver_identity_sha256: str
    ir_receiver_action_profile: str
    ir_receiver_allowed_operations: tuple[str, ...]


@dataclass(frozen=True)
class ArvanS3NormalLocalIdentityProjection:
    """One role-only normal-route identity fact for four-role collection.

    This is intentionally distinct from the historical paired
    ``credential_projection`` API: each method that creates this value opens
    only its corresponding fixed local credential.  A Witness/controller may
    aggregate redacted projections later, but this value alone is not a
    signed receipt, provider proof, or execution permit.
    """

    schema: str
    role: str
    identity_sha256: str
    action_profile: str
    source_site: str
    destination_site: str
    object_storage_namespace: str
    allowed_operations: tuple[str, ...]


@dataclass(frozen=True)
class _FactoryFacts:
    credential_loader_config: _credentials.RootOwnedArvanS3SeparatedCredentialLoaderConfig
    endpoint: str
    region: str
    bucket: str


def _fail(code: str) -> None:
    raise ArvanS3SeparatedClientFactoryError(code)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_ROOT_REQUIRED")


def _config_facts(
    config: object,
    *,
    require_enabled: bool,
) -> _FactoryFacts:
    if type(config) is not RootOwnedArvanS3SeparatedClientFactoryConfig:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    if config.schema != ARVAN_S3_SEPARATED_CLIENT_FACTORY_SCHEMA:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    if type(config.enabled) is not bool:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    if require_enabled and config.enabled is not True:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_DISABLED")
    if config.client_construction_mode != _CLIENT_CONSTRUCTION_MODE:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_MODE_INVALID")
    if type(config.credential_loader_config) is not _credentials.RootOwnedArvanS3SeparatedCredentialLoaderConfig:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    try:
        normalized = _credentials.validate_root_owned_arvan_s3_separated_credential_loader_config(
            config.credential_loader_config
        )
    except Exception:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    if normalized.enabled is not config.enabled:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_ENABLED_MISMATCH")
    return _FactoryFacts(
        credential_loader_config=normalized,
        endpoint=normalized.endpoint,
        region=normalized.region,
        bucket=normalized.bucket,
    )


def validate_root_owned_arvan_s3_separated_client_factory_config(
    config: RootOwnedArvanS3SeparatedClientFactoryConfig,
) -> RootOwnedArvanS3SeparatedClientFactoryConfig:
    """Pure validation that does not load a file, SDK, or client."""

    facts = _config_facts(config, require_enabled=False)
    return RootOwnedArvanS3SeparatedClientFactoryConfig(
        schema=ARVAN_S3_SEPARATED_CLIENT_FACTORY_SCHEMA,
        credential_loader_config=facts.credential_loader_config,
        enabled=config.enabled,
        client_construction_mode=_CLIENT_CONSTRUCTION_MODE,
    )


def _load_boto_sdk() -> tuple[object, object]:
    """Lazy SDK import after all root, route, and credential checks pass."""

    try:
        boto3_module = importlib.import_module("boto3")
        botocore_config_module = importlib.import_module("botocore.config")
    except Exception:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_SDK_UNAVAILABLE")
    return boto3_module, botocore_config_module


class _BucketScopedProbeClient:
    """Private explicit S3 method surface required by the live probe only."""

    def __init__(self, *, raw_client: object, bucket: str) -> None:
        self._raw_client = raw_client
        self._bucket = bucket

    def _call(self, method_name: str, request: Mapping[str, Any]) -> Any:
        if type(request) is not dict or type(request.get("Bucket")) is not str:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_BUCKET_REQUEST_INVALID")
        if request["Bucket"] != self._bucket:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_BUCKET_MISMATCH")
        try:
            method = getattr(self._raw_client, method_name, None)
        except Exception:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLIENT_INVALID")
        if not callable(method):
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLIENT_INVALID")
        # Preserve provider AccessDenied errors for the existing live probe's
        # denial oracle.  Other operation failures are redacted by that probe.
        return method(**dict(request))

    def get_bucket_versioning(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_versioning", {"Bucket": Bucket})

    def get_bucket_acl(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_acl", {"Bucket": Bucket})

    def get_object_lock_configuration(self, *, Bucket: str) -> Any:
        return self._call("get_object_lock_configuration", {"Bucket": Bucket})

    def list_object_versions(self, **request: Any) -> Any:
        return self._call("list_object_versions", dict(request))

    def list_objects_v2(self, **request: Any) -> Any:
        return self._call("list_objects_v2", dict(request))

    def put_object(self, **request: Any) -> Any:
        return self._call("put_object", dict(request))

    def get_object_retention(self, **request: Any) -> Any:
        return self._call("get_object_retention", dict(request))

    def head_object(self, **request: Any) -> Any:
        return self._call("head_object", dict(request))

    def get_object(self, **request: Any) -> Any:
        return self._call("get_object", dict(request))

    def delete_object(self, **request: Any) -> Any:
        return self._call("delete_object", dict(request))


class _BucketScopedFiPublisherRecoveryClient:
    """Private minimal S3 surface for one FI recovery-material publication.

    Unlike the paired preflight wrapper, this type deliberately has no
    delete, retention, object-lock, broad-list, or generic attribute-forward
    method.  It is passed only to a synchronously invoked trusted local
    handoff operation and is never returned by the root factory.
    """

    __slots__ = ("_bucket", "_raw_client")

    def __init__(self, *, raw_client: object, bucket: str) -> None:
        self._raw_client = raw_client
        self._bucket = bucket

    def _call(self, method_name: str, request: Mapping[str, Any]) -> Any:
        if type(request) is not dict or type(request.get("Bucket")) is not str:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_BUCKET_REQUEST_INVALID")
        if request["Bucket"] != self._bucket:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_BUCKET_MISMATCH")
        try:
            method = getattr(self._raw_client, method_name, None)
        except Exception:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLIENT_INVALID")
        if not callable(method):
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLIENT_INVALID")
        return method(**dict(request))

    @staticmethod
    def _recovery_key(value: object) -> str:
        if (
            type(value) is not str
            or not value.startswith(_PHYSICAL_RECOVERY_OBJECT_PREFIX)
            or len(value) > 512
            or "\x00" in value
        ):
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_KEY_INVALID")
        components = value.split("/")
        if (
            len(components) < 3
            or any(
                not component
                or component in {".", ".."}
                or component.lower() in {"alias", "current", "head", "latest", "pointer"}
                for component in components
            )
        ):
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_KEY_INVALID")
        return value

    def get_bucket_versioning(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_versioning", {"Bucket": Bucket})

    def get_bucket_acl(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_acl", {"Bucket": Bucket})

    def list_object_versions(self, **request: Any) -> Any:
        value = dict(request)
        allowed = {"Bucket", "Prefix", "KeyMarker", "VersionIdMarker"}
        if not {"Bucket", "Prefix"}.issubset(value) or set(value) - allowed:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_LIST_REQUEST_INVALID")
        self._recovery_key(value.get("Prefix"))
        for name in ("KeyMarker", "VersionIdMarker"):
            if name in value and (type(value[name]) is not str or not value[name]):
                _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_LIST_REQUEST_INVALID")
        return self._call("list_object_versions", value)

    def put_object(self, **request: Any) -> Any:
        value = dict(request)
        expected = {
            "Bucket",
            "Key",
            "Body",
            "ContentLength",
            "Metadata",
            "ContentType",
            "IfNoneMatch",
        }
        if set(value) != expected:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_PUT_REQUEST_INVALID")
        self._recovery_key(value["Key"])
        if (
            value["IfNoneMatch"] != "*"
            or type(value["ContentLength"]) is not int
            or value["ContentLength"] < 1
            or value["ContentType"] != "application/octet-stream"
            or not isinstance(value["Metadata"], Mapping)
            or not callable(getattr(value["Body"], "read", None))
        ):
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_PUT_REQUEST_INVALID")
        return self._call("put_object", value)

    def head_object(self, **request: Any) -> Any:
        value = dict(request)
        if set(value) != {"Bucket", "Key", "VersionId"}:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_READ_REQUEST_INVALID")
        self._recovery_key(value["Key"])
        if type(value["VersionId"]) is not str or not value["VersionId"]:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_READ_REQUEST_INVALID")
        return self._call("head_object", value)

    def get_object(self, **request: Any) -> Any:
        value = dict(request)
        if set(value) != {"Bucket", "Key", "VersionId"}:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_READ_REQUEST_INVALID")
        self._recovery_key(value["Key"])
        if type(value["VersionId"]) is not str or not value["VersionId"]:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_READ_REQUEST_INVALID")
        return self._call("get_object", value)


@dataclass(frozen=True)
class _FiPublisherRecoveryRoute:
    """Factory-owned S3 route facts passed only inside the synchronous call."""

    region: str
    bucket: str


def _create_raw_client(
    *,
    boto3_module: object,
    botocore_config_module: object,
    facts: _FactoryFacts,
    credentials: _credentials._CredentialFacts,
) -> object:
    """Build exactly one S3v4 client from transient internal credential facts."""

    try:
        session_namespace = getattr(boto3_module, "session")
        session_type = getattr(session_namespace, "Session")
        config_type = getattr(botocore_config_module, "Config")
        if not callable(session_type) or not callable(config_type):
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_SDK_UNAVAILABLE")
        client_config = config_type(
            signature_version="s3v4",
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            read_timeout=_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
            s3={"addressing_style": "path"},
            proxies={},
        )
        session = session_type(
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            region_name=facts.region,
        )
        raw_client = session.client(
            "s3",
            endpoint_url=facts.endpoint,
            region_name=facts.region,
            use_ssl=True,
            verify=True,
            config=client_config,
        )
    except ArvanS3SeparatedClientFactoryError:
        raise
    except Exception:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLIENT_CREATE_FAILED")
    if raw_client is None:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLIENT_CREATE_FAILED")
    return raw_client


def _binding_facts(
    binding: object,
    *,
    facts: _FactoryFacts,
) -> PhysicalArvanImmutabilityPreflightBinding:
    """Validate the exact FI-to-IR probe binding before credentials or SDK."""

    try:
        normalized = _live_probe_binding(binding)
    except Exception:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_BINDING_INVALID")
    if (
        normalized.endpoint != facts.endpoint
        or normalized.region != facts.region
        or normalized.bucket != facts.bucket
        or normalized.source_site != "webapp_fi"
        or normalized.destination_site != "webapp_ir"
    ):
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_BINDING_MISMATCH")
    return normalized


def _live_probe_binding(
    binding: object,
) -> PhysicalArvanImmutabilityPreflightBinding:
    """Keep the live-probe binding grammar as the single canonical parser."""

    # This private parser performs only local type/route/endpoint validation;
    # it opens no client and has no provider operation.
    from core import physical_arvan_s3_immutability_live_probe as live_probe

    return live_probe._binding_facts(binding).binding


def _validated_observed_at(value: object) -> datetime:
    try:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLOCK_INVALID")
        return value.astimezone(timezone.utc)
    except ArvanS3SeparatedClientFactoryError:
        raise
    except Exception:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLOCK_INVALID")


def _preflight_identity_facts(
    value: _preflight.VerifiedPhysicalArvanImmutabilityPreflight,
) -> tuple[str, str]:
    """Extract only the already-verified distinct FI/IR identity pins."""

    try:
        restrictions = value.observation.credential_restrictions
        by_role = {item.role: item for item in restrictions}
        fi = by_role["fi-publisher"].credential_identity_sha256
        ir = by_role["ir-receiver"].credential_identity_sha256
    except Exception:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_PREFLIGHT_INVALID")
    if (
        type(fi) is not str
        or type(ir) is not str
        or len(fi) != 64
        or len(ir) != 64
        or fi == "0" * 64
        or ir == "0" * 64
        or fi == ir
    ):
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_PREFLIGHT_INVALID")
    return fi, ir


def _verified_recovery_preflight(
    value: object,
    *,
    facts: _FactoryFacts,
    now: datetime,
) -> tuple[
    _preflight.VerifiedPhysicalArvanImmutabilityPreflight,
    _preflight.PhysicalArvanImmutabilityPreflightBinding,
    str,
    str,
]:
    """Recheck opaque paired preflight without opening either credential."""

    if type(value) is not _preflight.VerifiedPhysicalArvanImmutabilityPreflight:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_PREFLIGHT_INVALID")
    try:
        binding = value.binding
        verified = _preflight.require_verified_physical_arvan_immutability_preflight(
            value,
            binding=binding,
            now=now,
        )
    except Exception:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_PREFLIGHT_INVALID")
    if (
        type(binding) is not _preflight.PhysicalArvanImmutabilityPreflightBinding
        or verified is not value
        or binding.source_site != "webapp_fi"
        or binding.destination_site != "webapp_ir"
        or binding.endpoint != facts.endpoint
        or binding.region != facts.region
        or binding.bucket != facts.bucket
    ):
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_PREFLIGHT_MISMATCH")
    fi_identity, ir_identity = _preflight_identity_facts(verified)
    return verified, binding, fi_identity, ir_identity


def _admission_facts(
    value: object,
    *,
    facts: _FactoryFacts,
    now: datetime,
) -> ArvanS3FiPublisherRecoveryHandoffAdmission:
    """Recheck factory-minted admission and paired-preflight freshness."""

    if (
        type(value) is not ArvanS3FiPublisherRecoveryHandoffAdmission
        or value._capability is not _FI_PUBLISHER_RECOVERY_HANDOFF_CAPABILITY
        or value.schema != _FI_PUBLISHER_RECOVERY_HANDOFF_ADMISSION_SCHEMA
    ):
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_ADMISSION_REQUIRED")
    try:
        verified = _preflight.require_verified_physical_arvan_immutability_preflight(
            value._preflight,
            binding=value._binding,
            now=now,
        )
    except Exception:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_ADMISSION_INVALID")
    if verified is not value._preflight:
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_ADMISSION_INVALID")
    if (
        value._binding.source_site != "webapp_fi"
        or value._binding.destination_site != "webapp_ir"
        or value._binding.endpoint != facts.endpoint
        or value._binding.region != facts.region
        or value._binding.bucket != facts.bucket
    ):
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_ADMISSION_MISMATCH")
    fi_identity, ir_identity = _preflight_identity_facts(verified)
    observed_at = verified.observation.observed_at
    if (
        value.campaign_id != value._binding.campaign_id
        or value.release_sha != value._binding.release_sha
        or value.route_binding_sha256 != value._binding.route_binding_sha256
        or value.observed_at != observed_at
        or value.fi_publisher_identity_sha256 != fi_identity
        or value.ir_receiver_identity_sha256 != ir_identity
    ):
        _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_ADMISSION_TAMPERED")
    return value


class RootOwnedArvanS3SeparatedClientFactory:
    """Legacy paired compatibility wrapper, not a Full-Matrix factory.

    It is retained only for the older two-client immutability probe.  New
    production handoffs use explicit one-role artifacts and the four-role
    binder rejects this class's projections.
    """

    def __init__(self, config: RootOwnedArvanS3SeparatedClientFactoryConfig) -> None:
        # Construction is deliberately inert.  In particular, it retains no
        # credentials, file descriptors, SDK object, raw client, or probe.
        self._config = validate_root_owned_arvan_s3_separated_client_factory_config(config)

    def fi_publisher_identity_projection(self) -> ArvanS3NormalLocalIdentityProjection:
        """Open only FI's normal immutable publisher credential locally."""

        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        try:
            _route, credential = _credentials._load_root_owned_fi_publisher_credential_facts(
                facts.credential_loader_config
            )
        except _credentials.ArvanS3SeparatedCredentialLoaderError:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_FI_CREDENTIAL_ADMISSION_FAILED")
        except Exception:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_FI_CREDENTIAL_ADMISSION_FAILED")
        return ArvanS3NormalLocalIdentityProjection(
            schema=ARVAN_S3_SEPARATED_CLIENT_FACTORY_SCHEMA,
            role="fi-publisher",
            identity_sha256=credential.identity_sha256,
            action_profile="fi-publisher-immutable-create-only-v1",
            source_site="webapp_fi",
            destination_site="webapp_ir",
            object_storage_namespace="physical-wal",
            allowed_operations=_credentials.ARVAN_S3_FI_PUBLISHER_EXPECTED_PROBE_ACTIONS,
        )

    def ir_receiver_identity_projection(self) -> ArvanS3NormalLocalIdentityProjection:
        """Open only WA-IR's normal exact-reader credential locally."""

        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        try:
            _route, credential = _credentials._load_root_owned_ir_receiver_credential_facts(
                facts.credential_loader_config
            )
        except _credentials.ArvanS3SeparatedCredentialLoaderError:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_IR_CREDENTIAL_ADMISSION_FAILED")
        except Exception:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_IR_CREDENTIAL_ADMISSION_FAILED")
        return ArvanS3NormalLocalIdentityProjection(
            schema=ARVAN_S3_SEPARATED_CLIENT_FACTORY_SCHEMA,
            role="ir-receiver",
            identity_sha256=credential.identity_sha256,
            action_profile="ir-receiver-exact-readonly-v1",
            source_site="webapp_fi",
            destination_site="webapp_ir",
            object_storage_namespace="physical-wal",
            allowed_operations=_credentials.ARVAN_S3_IR_RECEIVER_EXPECTED_PROBE_ACTIONS,
        )

    def credential_projection(self) -> ArvanS3SeparatedClientFactoryCredentialProjection:
        """Legacy paired projection; not accepted by the four-role binder."""

        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        try:
            pair = _credentials.load_root_owned_arvan_s3_separated_credential_pair(
                facts.credential_loader_config
            )
        except _credentials.ArvanS3SeparatedCredentialLoaderError:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CREDENTIAL_ADMISSION_FAILED")
        projection = _credentials.project_root_owned_arvan_s3_immutability_probe_credentials(
            pair,
            config=facts.credential_loader_config,
        )
        return ArvanS3SeparatedClientFactoryCredentialProjection(
            schema=ARVAN_S3_SEPARATED_CLIENT_FACTORY_SCHEMA,
            fi_publisher_role=projection.fi_publisher_role,
            fi_publisher_identity_sha256=projection.fi_publisher_identity_sha256,
            fi_publisher_action_profile=pair.fi_publisher_action_profile,
            fi_publisher_allowed_operations=projection.fi_publisher_allowed_operations,
            ir_receiver_role=projection.ir_receiver_role,
            ir_receiver_identity_sha256=projection.ir_receiver_identity_sha256,
            ir_receiver_action_profile=pair.ir_receiver_action_profile,
            ir_receiver_allowed_operations=projection.ir_receiver_allowed_operations,
        )

    def admit_fi_publisher_recovery_handoff(
        self,
        *,
        preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight,
        now: datetime,
    ) -> ArvanS3FiPublisherRecoveryHandoffAdmission:
        """Admit a fresh paired preflight for one FI-only recovery handoff.

        This performs no credential, SDK, client, Object-Storage, or network
        action.  It establishes the non-secret identity pins that let the
        subsequent handoff reopen *only* the FI publisher file while still
        refusing an identity that differs from the recently proven pair.
        """

        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        observed = _validated_observed_at(now)
        verified, binding, fi_identity, ir_identity = _verified_recovery_preflight(
            preflight,
            facts=facts,
            now=observed,
        )
        result = ArvanS3FiPublisherRecoveryHandoffAdmission(
            schema=_FI_PUBLISHER_RECOVERY_HANDOFF_ADMISSION_SCHEMA,
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            route_binding_sha256=binding.route_binding_sha256,
            observed_at=verified.observation.observed_at,
            fi_publisher_identity_sha256=fi_identity,
            ir_receiver_identity_sha256=ir_identity,
            _preflight=verified,
            _binding=binding,
        )
        object.__setattr__(result, "_capability", _FI_PUBLISHER_RECOVERY_HANDOFF_CAPABILITY)
        return result

    def require_fi_publisher_recovery_handoff_admission(
        self,
        admission: object,
        *,
        now: datetime,
    ) -> ArvanS3FiPublisherRecoveryHandoffAdmission:
        """Recheck one in-memory admission without reopening credentials."""

        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        return _admission_facts(admission, facts=facts, now=_validated_observed_at(now))

    def execute_fi_publisher_recovery_handoff(
        self,
        *,
        admission: object,
        now: datetime,
        operation: Callable[[_BucketScopedFiPublisherRecoveryClient, _FiPublisherRecoveryRoute], object]
        | None,
    ) -> object:
        """Run one trusted local operation with a transient FI-only client.

        ``operation`` is intentionally an explicit in-process root adapter,
        not a generic client factory.  It receives only the minimal create-
        only/recovery-readback method set and a factory-owned route value.  A
        caller cannot inject an endpoint, bucket, raw client, or credential;
        the client is never returned and the IR credential is never opened.
        """

        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        observed = _validated_observed_at(now)
        checked = _admission_facts(admission, facts=facts, now=observed)
        if operation is None or not callable(operation):
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_OPERATION_INVALID")
        try:
            route_facts, credential = _credentials._load_root_owned_fi_publisher_credential_facts(
                facts.credential_loader_config
            )
        except _credentials.ArvanS3SeparatedCredentialLoaderError:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_CREDENTIAL_ADMISSION_FAILED")
        except Exception:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_CREDENTIAL_ADMISSION_FAILED")
        if (
            type(route_facts) is not _credentials._ConfigFacts
            or type(credential) is not _credentials._CredentialFacts
            or route_facts.endpoint != facts.endpoint
            or route_facts.region != facts.region
            or route_facts.bucket != facts.bucket
            or credential.identity_sha256 != checked.fi_publisher_identity_sha256
            or credential.identity_sha256 == checked.ir_receiver_identity_sha256
        ):
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_CREDENTIAL_MISMATCH")
        boto3_module, botocore_config_module = _load_boto_sdk()
        raw_client = _create_raw_client(
            boto3_module=boto3_module,
            botocore_config_module=botocore_config_module,
            facts=facts,
            credentials=credential,
        )
        client = _BucketScopedFiPublisherRecoveryClient(
            raw_client=raw_client,
            bucket=facts.bucket,
        )
        route = _FiPublisherRecoveryRoute(region=facts.region, bucket=facts.bucket)
        try:
            result = operation(client, route)
        except ArvanS3SeparatedClientFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_OPERATION_FAILED")
        if result is client or result is route:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_RECOVERY_OPERATION_RESULT_INVALID")
        return result

    def collect_immutability_preflight(
        self,
        *,
        binding: PhysicalArvanImmutabilityPreflightBinding,
        observed_at: datetime,
    ) -> PhysicalArvanImmutabilityPreflightObservation:
        """Run the existing injected live probe with two transient scoped clients.

        This is the only path that can create SDK clients.  It validates root,
        enabled policy, clock, and exact binding before opening either file or
        loading an SDK.  It never returns either client or the key material.
        """

        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        normalized_binding = _binding_facts(binding, facts=facts)
        observed = _validated_observed_at(observed_at)
        try:
            loaded = _credentials._load_root_owned_separated_credential_facts(
                facts.credential_loader_config
            )
        except _credentials.ArvanS3SeparatedCredentialLoaderError:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CREDENTIAL_ADMISSION_FAILED")
        boto3_module, botocore_config_module = _load_boto_sdk()
        fi_raw = _create_raw_client(
            boto3_module=boto3_module,
            botocore_config_module=botocore_config_module,
            facts=facts,
            credentials=loaded.fi_publisher,
        )
        ir_raw = _create_raw_client(
            boto3_module=boto3_module,
            botocore_config_module=botocore_config_module,
            facts=facts,
            credentials=loaded.ir_receiver,
        )
        if fi_raw is ir_raw:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLIENTS_NOT_SEPARATE")
        fi_client = _BucketScopedProbeClient(raw_client=fi_raw, bucket=facts.bucket)
        ir_client = _BucketScopedProbeClient(raw_client=ir_raw, bucket=facts.bucket)
        probe = PhysicalArvanS3ImmutabilityLiveProbe(
            PhysicalArvanS3ImmutabilityLiveProbeConfig(
                binding=normalized_binding,
                enabled=True,
                fi_publisher=PhysicalArvanS3ImmutabilityScopedClient(
                    credential_identity_sha256=loaded.fi_publisher.identity_sha256,
                    client=fi_client,
                ),
                ir_receiver=PhysicalArvanS3ImmutabilityScopedClient(
                    credential_identity_sha256=loaded.ir_receiver.identity_sha256,
                    client=ir_client,
                ),
            )
        )
        try:
            return probe.collect(binding=normalized_binding, observed_at=observed)
        except PhysicalArvanS3ImmutabilityLiveProbeError:
            raise
        except Exception:
            _fail("ARVAN_S3_SEPARATED_CLIENT_FACTORY_PROBE_FAILED")
