"""WA-FI-receiver-only factory artifact for the reverse data plane.

This module never imports or instantiates the retired dual-role reverse
factory.  It collects the local FI receiver identity before reverse preflight
exists, then exposes only callback-scoped exact ``HeadObject``/``GetObject``
after the verified preflight and IR Witness term are rechecked.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from core import physical_arvan_s3_role_local_client_support as _client_support
from core import physical_arvan_s3_role_local_credential_reader as _credential_reader
from core import physical_arvan_s3_role_profiles as _profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as _preflight
from core import physical_wa_fi_postgres_failback_pull_runtime as _pull
from core.object_delta_role_matrix_rollover import VerifiedObjectDeltaRoleMatrixWitnessedTerm
from core.physical_arvan_s3_failback_route_commitment import (
    PhysicalArvanS3FailbackRouteCommitmentError,
    derive_physical_arvan_s3_failback_four_role_route_binding_sha256,
    derive_physical_arvan_s3_failback_route_scope_sha256,
    physical_arvan_s3_failback_exact_prefix,
)
from core.physical_arvan_s3_role_local_identity import (
    PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
    ArvanS3RoleLocalIdentityProjection,
)
from core.physical_arvan_s3_role_local_route_policy import (
    ArvanS3RoleLocalRoutePolicy,
    validate_physical_arvan_s3_role_local_route_policy,
)
from core.physical_wal_object_manifest import PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE


__all__ = (
    "ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_DEFAULT_ENABLED",
    "ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_SCHEMA",
    "FIXED_ARVAN_S3_FI_RECEIVER_ROLE_CREDENTIAL_FILE",
    "ArvanS3FiReceiverFailbackRoleFactoryError",
    "RootOwnedArvanS3FiReceiverFailbackRoleFactory",
    "RootOwnedArvanS3FiReceiverFailbackRoleFactoryConfig",
    "validate_root_owned_arvan_s3_fi_receiver_failback_role_factory_config",
)


ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_SCHEMA = (
    "gold-trade-physical-arvan-s3-fi-receiver-failback-role-factory-v1"
)
ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_DEFAULT_ENABLED = False
_CLIENT_CONSTRUCTION_MODE = "root-owned-fi-receiver-only-s3v4-client-v1"
_MAX_ADMISSION_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{1,1023}$", re.ASCII)
_VERSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,1023}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SENSITIVE_OR_URL_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
FIXED_ARVAN_S3_FI_RECEIVER_ROLE_CREDENTIAL_FILE = Path(
    "/etc/trading-bot/security/arvan-s3-fi-receiver-credentials.json"
)


class ArvanS3FiReceiverFailbackRoleFactoryError(ValueError):
    """Stable redacted refusal from the FI-receiver-only factory."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanS3FiReceiverFailbackRoleFactoryConfig:
    """Default-off policy for only the rebuilding FI receiver role."""

    schema: str = ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_SCHEMA
    route_policy: ArvanS3RoleLocalRoutePolicy | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    preflight_config: _preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    enabled: bool = ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_DEFAULT_ENABLED
    client_construction_mode: str = _CLIENT_CONSTRUCTION_MODE


@dataclass(frozen=True)
class _Facts:
    route_policy: ArvanS3RoleLocalRoutePolicy
    preflight_config: _preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig | None
    endpoint: str
    region: str
    bucket: str
    exact_prefix: str | None


def _fail(code: str) -> None:
    raise ArvanS3FiReceiverFailbackRoleFactoryError(code)


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _facts(value: object, *, require_enabled: bool, require_preflight: bool) -> _Facts:
    if type(value) is not RootOwnedArvanS3FiReceiverFailbackRoleFactoryConfig:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CONFIG_INVALID")
    if (
        value.schema != ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_SCHEMA
        or type(value.enabled) is not bool
        or value.client_construction_mode != _CLIENT_CONSTRUCTION_MODE
        or type(value.route_policy) is not ArvanS3RoleLocalRoutePolicy
        or (
            value.preflight_config is not None
            and type(value.preflight_config)
            is not _preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig
        )
    ):
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_DISABLED")
    try:
        route_policy = validate_physical_arvan_s3_role_local_route_policy(
            value.route_policy,
            expected_source_site="webapp_ir",
            expected_destination_site="webapp_fi",
            expected_object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
            require_enabled=require_enabled,
        )
    except Exception:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CONFIG_INVALID")
    if route_policy.enabled is not value.enabled:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ENABLED_MISMATCH")
    policy = value.preflight_config
    if require_preflight and policy is None:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_PREFLIGHT_CONFIG_REQUIRED")
    prefix: str | None = None
    if policy is not None:
        if policy.enabled is not value.enabled:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ENABLED_MISMATCH")
        try:
            binding = _preflight.validate_physical_ir_to_fi_object_storage_failback_binding(policy.binding)
            reverse_scope = derive_physical_arvan_s3_failback_route_scope_sha256(
                campaign_id=binding.campaign_id,
                release_sha=binding.release_sha,
                endpoint=route_policy.endpoint,
                region=route_policy.region,
                bucket=route_policy.bucket,
            )
            route_binding = derive_physical_arvan_s3_failback_four_role_route_binding_sha256(
                campaign_id=binding.campaign_id,
                release_sha=binding.release_sha,
                normal_route_scope_sha256=binding.normal_route_scope_sha256,
                reverse_route_scope_sha256=reverse_scope,
                fi_publisher_identity_sha256=binding.fi_publisher_identity_sha256,
                ir_receiver_identity_sha256=binding.ir_receiver_identity_sha256,
                ir_publisher_identity_sha256=binding.ir_publisher_identity_sha256,
                fi_receiver_identity_sha256=binding.fi_receiver_identity_sha256,
            )
            prefix = physical_arvan_s3_failback_exact_prefix(
                campaign_id=binding.campaign_id,
                release_sha=binding.release_sha,
            )
        except (Exception, PhysicalArvanS3FailbackRouteCommitmentError):
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ROUTE_COMMITMENT_INVALID")
        if binding.reverse_route_scope_sha256 != reverse_scope or binding.route_binding_sha256 != route_binding:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ROUTE_COMMITMENT_MISMATCH")
    return _Facts(
        route_policy=route_policy,
        preflight_config=policy,
        endpoint=route_policy.endpoint,
        region=route_policy.region,
        bucket=route_policy.bucket,
        exact_prefix=prefix,
    )


def validate_root_owned_arvan_s3_fi_receiver_failback_role_factory_config(
    config: RootOwnedArvanS3FiReceiverFailbackRoleFactoryConfig,
) -> RootOwnedArvanS3FiReceiverFailbackRoleFactoryConfig:
    """Pure validation; a preflight is optional only for collection phase."""

    facts = _facts(config, require_enabled=False, require_preflight=False)
    return RootOwnedArvanS3FiReceiverFailbackRoleFactoryConfig(
        schema=ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_SCHEMA,
        route_policy=facts.route_policy,
        preflight_config=facts.preflight_config,
        enabled=config.enabled,
        client_construction_mode=_CLIENT_CONSTRUCTION_MODE,
    )


def _verified_preflight(
    value: object,
    *,
    facts: _Facts,
    now: datetime,
) -> _preflight.VerifiedPhysicalIrToFiObjectStorageFailbackPreflight:
    if facts.preflight_config is None:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_PREFLIGHT_CONFIG_REQUIRED")
    try:
        verified = _preflight.require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            value,
            config=facts.preflight_config,
            now=now,
        )
    except Exception:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_PREFLIGHT_INVALID")
    if verified.binding != facts.preflight_config.binding:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_PREFLIGHT_MISMATCH")
    return verified


def _load_fi_receiver_credential(
    facts: _Facts,
) -> tuple[
    _credential_reader.ArvanS3RoleLocalRouteFacts,
    _credential_reader.ArvanS3RoleLocalCredentialFacts,
]:
    """Open exactly the fixed FI-receiver file for this one reverse route."""

    try:
        return _credential_reader.load_root_owned_arvan_s3_role_local_credential(
            route_policy=facts.route_policy,
            expected_source_site="webapp_ir",
            expected_destination_site="webapp_fi",
            expected_object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
            expected_role=_profiles.ARVAN_S3_FI_RECEIVER_ROLE,
            expected_action_profile=_profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
            fixed_credential_file=FIXED_ARVAN_S3_FI_RECEIVER_ROLE_CREDENTIAL_FILE,
        )
    except Exception:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CREDENTIAL_ADMISSION_FAILED")


def _safe_key(value: object, *, exact_prefix: str) -> str:
    if (
        type(value) is not str
        or _OBJECT_KEY_RE.fullmatch(value) is None
        or not value.startswith(exact_prefix)
        or "//" in value
        or _SENSITIVE_OR_URL_RE.search(value) is not None
    ):
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_OBJECT_KEY_INVALID")
    parts = value.split("/")
    if len(parts) < 3 or any(
        not part or part in {".", ".."} or part.lower() in {"alias", "current", "head", "latest", "pointer"}
        for part in parts
    ):
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_OBJECT_KEY_INVALID")
    return value


def _safe_version(value: object) -> str:
    if type(value) is not str or _VERSION_ID_RE.fullmatch(value) is None or not value:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_VERSION_INVALID")
    if value.lower() in {"alias", "current", "head", "latest", "pointer", "null", "undefined"}:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_VERSION_INVALID")
    return value


class _ReceiverClient:
    """Private exact-version-only callback surface for the FI receiver."""

    __slots__ = ("_bucket", "_prefix", "_lease", "__raw")

    def __init__(self, *, raw: object, bucket: str, prefix: str, lease: _client_support.ScopedRoleLocalCallbackLease) -> None:
        object.__setattr__(self, "_ReceiverClient__raw", raw)
        self._bucket = bucket
        self._prefix = prefix
        self._lease = lease

    def __getattribute__(self, name: str) -> Any:
        if name in {"_raw", "_ReceiverClient__raw"}:
            raise AttributeError("private raw client is not exposed")
        return object.__getattribute__(self, name)

    def _read(self, *, operation: str, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]:
        self._lease.require_active()
        if Bucket != self._bucket:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_BUCKET_MISMATCH")
        _safe_key(Key, exact_prefix=self._prefix)
        _safe_version(VersionId)
        try:
            raw = object.__getattribute__(self, "_ReceiverClient__raw")
            method = getattr(raw, operation, None)
            if not callable(method):
                _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CLIENT_INVALID")
            result = method(Bucket=Bucket, Key=Key, VersionId=VersionId)
        except ArvanS3FiReceiverFailbackRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CLIENT_OPERATION_FAILED")
        if not isinstance(result, Mapping):
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CLIENT_RESPONSE_INVALID")
        return result

    def head_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]:
        return self._read(operation="head_object", Bucket=Bucket, Key=Key, VersionId=VersionId)

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]:
        return self._read(operation="get_object", Bucket=Bucket, Key=Key, VersionId=VersionId)


def _static_admission(
    value: object,
    *,
    facts: _Facts,
    now: datetime,
) -> _pull.PhysicalWaFiFailbackExactVersionReceiverAdmission:
    if type(value) is not _pull.PhysicalWaFiFailbackExactVersionReceiverAdmission:
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ADMISSION_REQUIRED")
    if getattr(value, "_capability", None) is not _pull._FAILBACK_RECEIVER_ADMISSION_CAPABILITY:  # type: ignore[attr-defined]
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ADMISSION_REQUIRED")
    issued = _utc(value.admitted_at)
    if issued > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS) or issued < now - timedelta(seconds=_MAX_ADMISSION_AGE_SECONDS):
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ADMISSION_INVALID")
    binding = facts.preflight_config.binding if facts.preflight_config is not None else None
    if (
        binding is None
        or value.campaign_id != binding.campaign_id
        or value.release_sha != binding.release_sha
        or value.route_binding_sha256 != binding.route_binding_sha256
        or value.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or value.ir_publisher_identity_sha256 != binding.ir_publisher_identity_sha256
        or value.fi_receiver_identity_sha256 != binding.fi_receiver_identity_sha256
        or type(value.writer_epoch) is not int
        or value.writer_epoch < 1
        or type(value.writer_lease_id) is not str
        or type(value.witness_transition_id) is not str
        or type(value.witnessed_term_proof_sha256) is not str
        or _HEX64_RE.fullmatch(value.witnessed_term_proof_sha256) is None
    ):
        _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ADMISSION_INVALID")
    return value


class RootOwnedArvanS3FiReceiverFailbackRoleFactory:
    """One-role reverse factory with no IR publisher capability surface."""

    __slots__ = ("_config",)

    def __init__(self, config: RootOwnedArvanS3FiReceiverFailbackRoleFactoryConfig) -> None:
        self._config = validate_root_owned_arvan_s3_fi_receiver_failback_role_factory_config(config)

    def identity_projection(self) -> ArvanS3RoleLocalIdentityProjection:
        facts = _facts(self._config, require_enabled=True, require_preflight=False)
        try:
            _client_support.require_role_local_root()
            _route, credential = _load_fi_receiver_credential(facts)
        except Exception:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CREDENTIAL_ADMISSION_FAILED")
        return ArvanS3RoleLocalIdentityProjection(
            schema=PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
            role=_profiles.ARVAN_S3_FI_RECEIVER_ROLE,
            identity_sha256=credential.identity_sha256,
            action_profile=_profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
            source_site="webapp_ir",
            destination_site="webapp_fi",
            object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
            allowed_operations=_profiles.ARVAN_S3_FI_RECEIVER_EXPECTED_ACTIONS,
        )

    def admit_fi_receiver_failback_exact_pull(
        self,
        *,
        preflight: _preflight.VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> object:
        facts = _facts(self._config, require_enabled=True, require_preflight=True)
        observed = _utc(now)
        try:
            _client_support.require_role_local_root()
            checked = _verified_preflight(preflight, facts=facts, now=observed)
            result = _pull.build_physical_wa_fi_failback_exact_version_receiver_admission(
                preflight=checked,
                preflight_config=facts.preflight_config,
                current_witnessed_term=current_witnessed_term,
                now=observed,
            )
            return _static_admission(result, facts=facts, now=observed)
        except ArvanS3FiReceiverFailbackRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ADMISSION_FAILED")

    def require_fi_receiver_failback_exact_pull_admission(
        self,
        admission: object,
        *,
        preflight: _preflight.VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> object:
        facts = _facts(self._config, require_enabled=True, require_preflight=True)
        observed = _utc(now)
        try:
            _client_support.require_role_local_root()
            checked = _verified_preflight(preflight, facts=facts, now=observed)
            result = _pull.require_physical_wa_fi_failback_exact_version_receiver_admission(
                admission,
                preflight=checked,
                preflight_config=facts.preflight_config,
                current_witnessed_term=current_witnessed_term,
                now=observed,
            )
            return _static_admission(result, facts=facts, now=observed)
        except ArvanS3FiReceiverFailbackRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ADMISSION_FAILED")

    def execute_fi_receiver_failback_exact_pull(
        self,
        *,
        admission: object,
        now: datetime,
        operation: Callable[[object, object], object],
    ) -> object:
        facts = _facts(self._config, require_enabled=True, require_preflight=True)
        observed = _utc(now)
        try:
            _client_support.require_role_local_root()
            checked = _static_admission(admission, facts=facts, now=observed)
        except ArvanS3FiReceiverFailbackRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_ADMISSION_INVALID")
        if not callable(operation) or facts.exact_prefix is None:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_OPERATION_INVALID")
        try:
            route_facts, credential = _load_fi_receiver_credential(facts)
        except Exception:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CREDENTIAL_ADMISSION_FAILED")
        if (
            type(route_facts) is not _credential_reader.ArvanS3RoleLocalRouteFacts
            or type(credential) is not _credential_reader.ArvanS3RoleLocalCredentialFacts
            or route_facts.endpoint != facts.endpoint
            or route_facts.region != facts.region
            or route_facts.bucket != facts.bucket
            or credential.identity_sha256 != checked.fi_receiver_identity_sha256
            or credential.identity_sha256 == checked.ir_publisher_identity_sha256
        ):
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CREDENTIAL_MISMATCH")
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
            lease = _client_support.ScopedRoleLocalCallbackLease()
            client = _ReceiverClient(raw=raw, bucket=facts.bucket, prefix=facts.exact_prefix, lease=lease)
            route = _pull.PhysicalWaFiFailbackExactVersionReceiverRoute(
                endpoint=facts.endpoint,
                region=facts.region,
                bucket=facts.bucket,
            )
            called = False
            result: object | None = None
            try:
                called = True
                result = operation(client, route)
            finally:
                lease.revoke()
        except ArvanS3FiReceiverFailbackRoleFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_OPERATION_FAILED")
        if not called or _client_support.result_leaks_role_local_callback_value(
            result,
            blocked=(client, route, raw),
        ):
            _fail("ARVAN_S3_FI_RECEIVER_FAILBACK_ROLE_FACTORY_CALLBACK_INVALID")
        return result
