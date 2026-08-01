"""Concrete root-only client seam for the reverse Object-Storage route.

This module is deliberately separate from the normal FI-publisher/IR-receiver
factory.  It implements only the two reverse roles required after a Witness
promotion: WA-IR's create-only publisher and WA-FI's exact-version receiver.
Neither normal credential file, normal factory, direct FI/IR connection, nor
mutable object selector is accepted or imported.

Construction is inert.  A root-only enabled call first validates a fresh
four-identity reverse preflight and a Witness-bound opaque admission, then
opens *only the local role's* fixed credential, creates one path-style S3v4
client, and passes a narrowly scoped wrapper to one synchronous operation.
It never returns a credential, raw client, endpoint selector, or generic
client factory.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import importlib
import os
import re
import threading
from typing import Any

from core import physical_arvan_s3_failback_separated_credential_loader as _credentials
from core import physical_ir_to_fi_object_storage_failback_preflight as _preflight
from core import physical_wa_fi_postgres_failback_pull_runtime as _fi_pull
from core import physical_wa_ir_postgres_failback_handoff_runtime as _ir_handoff
from core.object_delta_role_matrix_rollover import VerifiedObjectDeltaRoleMatrixWitnessedTerm
from core.physical_arvan_s3_failback_route_commitment import (
    PhysicalArvanS3FailbackRouteCommitmentError,
    derive_physical_arvan_s3_failback_four_role_route_binding_sha256,
    derive_physical_arvan_s3_failback_route_scope_sha256,
    physical_arvan_s3_failback_exact_prefix,
)
from core.physical_wal_object_manifest import PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE


__all__ = (
    "ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_DEFAULT_ENABLED",
    "ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SCHEMA",
    "ArvanS3FailbackLocalIdentityProjection",
    "ArvanS3FailbackSeparatedClientFactoryError",
    "RootOwnedArvanS3FailbackSeparatedClientFactory",
    "RootOwnedArvanS3FailbackSeparatedClientFactoryConfig",
    "validate_root_owned_arvan_s3_failback_separated_client_factory_config",
)


ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SCHEMA = (
    "gold-trade-physical-arvan-s3-failback-separated-client-factory-v1"
)
ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_DEFAULT_ENABLED = False

_CLIENT_CONSTRUCTION_MODE = "root-owned-reverse-machine-user-s3v4-clients-v1"
_CONNECT_TIMEOUT_SECONDS = 5
_READ_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 2
_MAX_ADMISSION_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{1,1023}$", re.ASCII)
_VERSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,1023}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SENSITIVE_OR_URL_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)


class ArvanS3FailbackSeparatedClientFactoryError(ValueError):
    """Fixed redacted factory failure; no keys, paths, SDK text, or URLs."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanS3FailbackSeparatedClientFactoryConfig:
    """Default-off root policy for one fixed IR→Object Storage→FI route."""

    schema: str = ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SCHEMA
    credential_loader_config: _credentials.RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig | None = field(
        default=None, repr=False, compare=False
    )
    preflight_config: _preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig | None = field(
        default=None, repr=False, compare=False
    )
    enabled: bool = ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_DEFAULT_ENABLED
    client_construction_mode: str = _CLIENT_CONSTRUCTION_MODE


@dataclass(frozen=True)
class ArvanS3FailbackLocalIdentityProjection:
    """A local public fingerprint suitable for the four-role preflight collector."""

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
    credential_loader_config: _credentials.RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig
    preflight_config: _preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig
    endpoint: str
    region: str
    bucket: str
    exact_prefix: str


def _require_local_route_commitments(
    *,
    credentials: _credentials.RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig,
    policy: _preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig,
) -> None:
    """Bind a local endpoint/bucket policy to the portable four-role proof.

    A reverse binding stores only public digests so it can travel through the
    Witness without exposing deployment topology.  Both FI and IR factories
    recompute the reverse scope from their own fixed endpoint/region/bucket
    before a secret is opened.  Therefore a same-text fake hash cannot make
    one side publish to one bucket while the other reads a different bucket.
    """

    binding = policy.binding
    if binding is None:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    try:
        reverse_scope = derive_physical_arvan_s3_failback_route_scope_sha256(
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            endpoint=credentials.endpoint,
            region=credentials.region,
            bucket=credentials.bucket,
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
    except PhysicalArvanS3FailbackRouteCommitmentError:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_ROUTE_COMMITMENT_INVALID")
    if (
        binding.reverse_route_scope_sha256 != reverse_scope
        or binding.route_binding_sha256 != route_binding
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_ROUTE_COMMITMENT_MISMATCH")


def _fail(code: str) -> None:
    raise ArvanS3FailbackSeparatedClientFactoryError(code)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_ROOT_REQUIRED")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _preflight_config(value: object, *, enabled: bool) -> _preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig:
    if type(value) is not _preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    if value.enabled is not enabled:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_ENABLED_MISMATCH")
    try:
        # The preflight module remains the single grammar authority for the
        # four public identity pins and the reverse namespace.
        _preflight._binding(value.binding)  # type: ignore[attr-defined]
    except Exception:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    return value


def _config_facts(
    config: object,
    *,
    require_enabled: bool,
) -> _FactoryFacts:
    if type(config) is not RootOwnedArvanS3FailbackSeparatedClientFactoryConfig:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    if config.schema != ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SCHEMA:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    if type(config.enabled) is not bool:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    if require_enabled and config.enabled is not True:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_DISABLED")
    if config.client_construction_mode != _CLIENT_CONSTRUCTION_MODE:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_MODE_INVALID")
    if type(config.credential_loader_config) is not _credentials.RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    try:
        credentials = _credentials.validate_root_owned_arvan_s3_failback_separated_credential_loader_config(
            config.credential_loader_config
        )
    except Exception:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CONFIG_INVALID")
    if credentials.enabled is not config.enabled:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_ENABLED_MISMATCH")
    policy = _preflight_config(config.preflight_config, enabled=config.enabled)
    _require_local_route_commitments(credentials=credentials, policy=policy)
    return _FactoryFacts(
        credential_loader_config=credentials,
        preflight_config=policy,
        endpoint=credentials.endpoint,
        region=credentials.region,
        bucket=credentials.bucket,
        exact_prefix=physical_arvan_s3_failback_exact_prefix(
            campaign_id=policy.binding.campaign_id,  # type: ignore[union-attr]
            release_sha=policy.binding.release_sha,  # type: ignore[union-attr]
        ),
    )


def validate_root_owned_arvan_s3_failback_separated_client_factory_config(
    config: RootOwnedArvanS3FailbackSeparatedClientFactoryConfig,
) -> RootOwnedArvanS3FailbackSeparatedClientFactoryConfig:
    """Validate public policy only; no credential, SDK, or network action."""

    facts = _config_facts(config, require_enabled=False)
    return RootOwnedArvanS3FailbackSeparatedClientFactoryConfig(
        schema=ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SCHEMA,
        credential_loader_config=facts.credential_loader_config,
        preflight_config=facts.preflight_config,
        enabled=config.enabled,
        client_construction_mode=_CLIENT_CONSTRUCTION_MODE,
    )


def _verified_preflight(
    value: object,
    *,
    facts: _FactoryFacts,
    now: datetime,
) -> _preflight.VerifiedPhysicalIrToFiObjectStorageFailbackPreflight:
    try:
        verified = _preflight.require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            value,
            config=facts.preflight_config,
            now=now,
        )
    except Exception:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_PREFLIGHT_INVALID_OR_STALE")
    binding = facts.preflight_config.binding
    if (
        verified.binding != binding
        or binding is None
        or verified.binding.source_site != "webapp_ir"
        or verified.binding.destination_site != "webapp_fi"
        or verified.binding.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_PREFLIGHT_MISMATCH")
    return verified


def _safe_key(value: object, *, exact_prefix: str) -> str:
    if (
        type(value) is not str
        or _OBJECT_KEY_RE.fullmatch(value) is None
        or not value.startswith(exact_prefix)
        or "//" in value
        or _SENSITIVE_OR_URL_RE.search(value) is not None
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_OBJECT_KEY_INVALID")
    components = value.split("/")
    if (
        len(components) < 3
        or any(
            not part
            or part in {".", ".."}
            or part.lower() in {"alias", "current", "head", "latest", "pointer"}
            for part in components
        )
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_OBJECT_KEY_INVALID")
    return value


def _safe_version(value: object) -> str:
    if type(value) is not str or _VERSION_ID_RE.fullmatch(value) is None or not value:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_VERSION_INVALID")
    if value.lower() in {"alias", "current", "head", "latest", "pointer", "null", "undefined"}:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_VERSION_INVALID")
    return value


def _load_boto_sdk() -> tuple[object, object]:
    try:
        boto3_module = importlib.import_module("boto3")
        botocore_config_module = importlib.import_module("botocore.config")
    except Exception:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SDK_UNAVAILABLE")
    return boto3_module, botocore_config_module


def _create_raw_client(
    *,
    boto3_module: object,
    botocore_config_module: object,
    facts: _FactoryFacts,
    credentials: _credentials._CredentialFacts,
) -> object:
    try:
        session_namespace = getattr(boto3_module, "session")
        session_type = getattr(session_namespace, "Session")
        config_type = getattr(botocore_config_module, "Config")
        if not callable(session_type) or not callable(config_type):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SDK_UNAVAILABLE")
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
        raw = session.client(
            "s3",
            endpoint_url=facts.endpoint,
            region_name=facts.region,
            use_ssl=True,
            verify=True,
            config=client_config,
        )
    except ArvanS3FailbackSeparatedClientFactoryError:
        raise
    except Exception:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLIENT_CREATE_FAILED")
    if raw is None:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLIENT_CREATE_FAILED")
    return raw


class _ScopedCallbackLease:
    """One-thread, one-callback revocation token for private client proxies."""

    __slots__ = ("_active", "_thread_id")

    def __init__(self) -> None:
        self._active = True
        self._thread_id = threading.get_ident()

    def require_active(self) -> None:
        if not self._active or threading.get_ident() != self._thread_id:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CALLBACK_REVOKED")

    def revoke(self) -> None:
        self._active = False


def _result_leaks_private_callback_value(value: object, *, blocked: tuple[object, ...]) -> bool:
    """Reject direct or shallow-container escape of a revocable local proxy."""

    pending: list[tuple[object, int]] = [(value, 0)]
    seen: set[int] = set()
    while pending:
        item, depth = pending.pop()
        if any(item is forbidden for forbidden in blocked):
            return True
        if depth >= 8 or id(item) in seen:
            continue
        seen.add(id(item))
        if type(item) in {tuple, list, set, frozenset}:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            pending.extend((child, depth + 1) for pair in item.items() for child in pair)
    return False


class _FailbackPublisherClient:
    """Private bucket/prefix-scoped surface for the IR create-only uploader."""

    __slots__ = ("_bucket", "_exact_prefix", "_lease", "__raw")

    def __init__(
        self,
        *,
        raw: object,
        bucket: str,
        exact_prefix: str,
        lease: _ScopedCallbackLease,
    ) -> None:
        object.__setattr__(self, "_FailbackPublisherClient__raw", raw)
        self._bucket = bucket
        self._exact_prefix = exact_prefix
        self._lease = lease

    def __getattribute__(self, name: str) -> Any:
        if name in {"_raw", "_FailbackPublisherClient__raw"}:
            raise AttributeError("private raw client is not exposed")
        return object.__getattribute__(self, name)

    def _call(self, name: str, request: Mapping[str, Any]) -> Any:
        self._lease.require_active()
        if type(request) is not dict or request.get("Bucket") != self._bucket:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_BUCKET_MISMATCH")
        try:
            raw = object.__getattribute__(self, "_FailbackPublisherClient__raw")
            method = getattr(raw, name, None)
            if not callable(method):
                _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLIENT_INVALID")
            return method(**dict(request))
        except ArvanS3FailbackSeparatedClientFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLIENT_OPERATION_FAILED")

    def get_bucket_versioning(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_versioning", {"Bucket": Bucket})

    def get_bucket_acl(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_acl", {"Bucket": Bucket})

    def list_object_versions(self, **request: Any) -> Any:
        value = dict(request)
        if not {"Bucket", "Prefix"}.issubset(value) or set(value) - {
            "Bucket", "Prefix", "KeyMarker", "VersionIdMarker"
        }:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_LIST_REQUEST_INVALID")
        _safe_key(value["Prefix"], exact_prefix=self._exact_prefix)
        for field in ("KeyMarker", "VersionIdMarker"):
            if field in value and (type(value[field]) is not str or not value[field]):
                _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_LIST_REQUEST_INVALID")
        if "KeyMarker" in value:
            _safe_key(value["KeyMarker"], exact_prefix=self._exact_prefix)
        return self._call("list_object_versions", value)

    def put_object(self, **request: Any) -> Any:
        value = dict(request)
        expected = {"Bucket", "Key", "Body", "ContentLength", "Metadata", "ContentType", "IfNoneMatch"}
        if set(value) != expected:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_PUT_REQUEST_INVALID")
        _safe_key(value["Key"], exact_prefix=self._exact_prefix)
        if (
            value["IfNoneMatch"] != "*"
            or type(value["ContentLength"]) is not int
            or value["ContentLength"] < 1
            or value["ContentType"] != "application/octet-stream"
            or not isinstance(value["Metadata"], Mapping)
            or not callable(getattr(value["Body"], "read", None))
        ):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_PUT_REQUEST_INVALID")
        return self._call("put_object", value)

    def head_object(self, **request: Any) -> Any:
        value = dict(request)
        if set(value) != {"Bucket", "Key", "VersionId"}:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_READ_REQUEST_INVALID")
        _safe_key(value["Key"], exact_prefix=self._exact_prefix)
        _safe_version(value["VersionId"])
        return self._call("head_object", value)

    def get_object(self, **request: Any) -> Any:
        value = dict(request)
        if set(value) != {"Bucket", "Key", "VersionId"}:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_READ_REQUEST_INVALID")
        _safe_key(value["Key"], exact_prefix=self._exact_prefix)
        _safe_version(value["VersionId"])
        return self._call("get_object", value)


class _FailbackReceiverClient:
    """Private exact-version-only surface for the rebuilding FI receiver."""

    __slots__ = ("_bucket", "_exact_prefix", "_lease", "__raw")

    def __init__(
        self,
        *,
        raw: object,
        bucket: str,
        exact_prefix: str,
        lease: _ScopedCallbackLease,
    ) -> None:
        object.__setattr__(self, "_FailbackReceiverClient__raw", raw)
        self._bucket = bucket
        self._exact_prefix = exact_prefix
        self._lease = lease

    def __getattribute__(self, name: str) -> Any:
        if name in {"_raw", "_FailbackReceiverClient__raw"}:
            raise AttributeError("private raw client is not exposed")
        return object.__getattribute__(self, name)

    def _exact_read(
        self,
        *,
        operation: str,
        Bucket: str,
        Key: str,
        VersionId: str,
    ) -> Mapping[str, Any]:
        self._lease.require_active()
        if Bucket != self._bucket:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_BUCKET_MISMATCH")
        _safe_key(Key, exact_prefix=self._exact_prefix)
        _safe_version(VersionId)
        try:
            raw = object.__getattribute__(self, "_FailbackReceiverClient__raw")
            method = getattr(raw, operation, None)
            if not callable(method):
                _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLIENT_INVALID")
            result = method(Bucket=Bucket, Key=Key, VersionId=VersionId)
        except ArvanS3FailbackSeparatedClientFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLIENT_OPERATION_FAILED")
        if not isinstance(result, Mapping):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLIENT_RESPONSE_INVALID")
        return result

    def head_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]:
        """Expose only one exact-version HEAD beneath the committed prefix."""

        return self._exact_read(
            operation="head_object",
            Bucket=Bucket,
            Key=Key,
            VersionId=VersionId,
        )

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]:
        """Expose only one exact-version GET beneath the committed prefix."""

        return self._exact_read(
            operation="get_object",
            Bucket=Bucket,
            Key=Key,
            VersionId=VersionId,
        )


def _static_ir_admission(
    value: object,
    *,
    facts: _FactoryFacts,
    now: datetime,
) -> _ir_handoff.PhysicalWaIrFailbackObjectStoragePublisherAdmission:
    if type(value) is not _ir_handoff.PhysicalWaIrFailbackObjectStoragePublisherAdmission:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_ADMISSION_REQUIRED")
    if getattr(value, "_capability", None) is not _ir_handoff._PUBLISHER_ADMISSION_CAPABILITY:  # type: ignore[attr-defined]
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_ADMISSION_REQUIRED")
    admitted = _utc(value.admitted_at, code="ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_ADMISSION_INVALID")
    if admitted > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS) or admitted < now - timedelta(
        seconds=_MAX_ADMISSION_AGE_SECONDS
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_ADMISSION_INVALID")
    binding = facts.preflight_config.binding
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
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_ADMISSION_INVALID")
    return value


def _static_fi_admission(
    value: object,
    *,
    facts: _FactoryFacts,
    now: datetime,
) -> _fi_pull.PhysicalWaFiFailbackExactVersionReceiverAdmission:
    if type(value) is not _fi_pull.PhysicalWaFiFailbackExactVersionReceiverAdmission:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_ADMISSION_REQUIRED")
    if getattr(value, "_capability", None) is not _fi_pull._FAILBACK_RECEIVER_ADMISSION_CAPABILITY:  # type: ignore[attr-defined]
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_ADMISSION_REQUIRED")
    admitted = _utc(value.admitted_at, code="ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_ADMISSION_INVALID")
    if admitted > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS) or admitted < now - timedelta(
        seconds=_MAX_ADMISSION_AGE_SECONDS
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_ADMISSION_INVALID")
    binding = facts.preflight_config.binding
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
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_ADMISSION_INVALID")
    return value


class RootOwnedArvanS3FailbackSeparatedClientFactory:
    """One inert class implementing the two role-specific reverse protocols.

    In deployment, each host calls only its own role method.  The other
    credential path is never opened as a side effect of admission, execution,
    or identity projection.
    """

    def __init__(self, config: RootOwnedArvanS3FailbackSeparatedClientFactoryConfig) -> None:
        self._config = validate_root_owned_arvan_s3_failback_separated_client_factory_config(config)

    def ir_publisher_identity_projection(self) -> ArvanS3FailbackLocalIdentityProjection:
        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        try:
            _route, credential = _credentials._load_root_owned_ir_publisher_credential_facts(
                facts.credential_loader_config
            )
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_CREDENTIAL_ADMISSION_FAILED")
        binding = facts.preflight_config.binding
        if binding is None or credential.identity_sha256 != binding.ir_publisher_identity_sha256:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_CREDENTIAL_MISMATCH")
        return ArvanS3FailbackLocalIdentityProjection(
            schema=ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SCHEMA,
            role="ir-publisher",
            identity_sha256=credential.identity_sha256,
            action_profile="ir-publisher-immutable-create-only-v1",
            source_site="webapp_ir",
            destination_site="webapp_fi",
            object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
            allowed_operations=_credentials.ARVAN_S3_IR_PUBLISHER_EXPECTED_PROBE_ACTIONS,
        )

    def fi_receiver_identity_projection(self) -> ArvanS3FailbackLocalIdentityProjection:
        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        try:
            _route, credential = _credentials._load_root_owned_fi_receiver_credential_facts(
                facts.credential_loader_config
            )
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_CREDENTIAL_ADMISSION_FAILED")
        binding = facts.preflight_config.binding
        if binding is None or credential.identity_sha256 != binding.fi_receiver_identity_sha256:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_CREDENTIAL_MISMATCH")
        return ArvanS3FailbackLocalIdentityProjection(
            schema=ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SCHEMA,
            role="fi-receiver",
            identity_sha256=credential.identity_sha256,
            action_profile="fi-receiver-exact-readonly-v1",
            source_site="webapp_ir",
            destination_site="webapp_fi",
            object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
            allowed_operations=_credentials.ARVAN_S3_FI_RECEIVER_EXPECTED_PROBE_ACTIONS,
        )

    def admit_ir_publisher_failback_handoff(
        self,
        *,
        preflight: _preflight.VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> _ir_handoff.PhysicalWaIrFailbackObjectStoragePublisherAdmission:
        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        observed = _utc(now, code="ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLOCK_INVALID")
        checked = _verified_preflight(preflight, facts=facts, now=observed)
        try:
            result = _ir_handoff.build_physical_wa_ir_failback_object_storage_publisher_admission(
                preflight=checked,
                preflight_config=facts.preflight_config,
                current_witnessed_term=current_witnessed_term,
                now=observed,
            )
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_ADMISSION_FAILED")
        return _static_ir_admission(result, facts=facts, now=observed)

    def require_ir_publisher_failback_handoff_admission(
        self,
        admission: _ir_handoff.PhysicalWaIrFailbackObjectStoragePublisherAdmission,
        *,
        preflight: _preflight.VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> _ir_handoff.PhysicalWaIrFailbackObjectStoragePublisherAdmission:
        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        observed = _utc(now, code="ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLOCK_INVALID")
        checked = _verified_preflight(preflight, facts=facts, now=observed)
        try:
            result = _ir_handoff.require_physical_wa_ir_failback_object_storage_publisher_admission(
                admission,
                preflight=checked,
                preflight_config=facts.preflight_config,
                current_witnessed_term=current_witnessed_term,
                now=observed,
            )
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_ADMISSION_FAILED")
        return _static_ir_admission(result, facts=facts, now=observed)

    def execute_ir_publisher_failback_handoff(
        self,
        *,
        admission: _ir_handoff.PhysicalWaIrFailbackObjectStoragePublisherAdmission,
        now: datetime,
        operation: Callable[[object, _ir_handoff.PhysicalWaIrFailbackObjectStoragePublisherRoute], object],
    ) -> object:
        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        observed = _utc(now, code="ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLOCK_INVALID")
        checked = _static_ir_admission(admission, facts=facts, now=observed)
        if not callable(operation):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_OPERATION_INVALID")
        try:
            route_facts, credential = _credentials._load_root_owned_ir_publisher_credential_facts(
                facts.credential_loader_config
            )
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_CREDENTIAL_ADMISSION_FAILED")
        if (
            route_facts.endpoint != facts.endpoint
            or route_facts.region != facts.region
            or route_facts.bucket != facts.bucket
            or credential.identity_sha256 != checked.ir_publisher_identity_sha256
            or credential.identity_sha256 == checked.fi_receiver_identity_sha256
        ):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_IR_CREDENTIAL_MISMATCH")
        boto3_module, botocore_config_module = _load_boto_sdk()
        raw = _create_raw_client(
            boto3_module=boto3_module,
            botocore_config_module=botocore_config_module,
            facts=facts,
            credentials=credential,
        )
        lease = _ScopedCallbackLease()
        client = _FailbackPublisherClient(
            raw=raw,
            bucket=facts.bucket,
            exact_prefix=facts.exact_prefix,
            lease=lease,
        )
        route = _ir_handoff.PhysicalWaIrFailbackObjectStoragePublisherRoute(
            bucket=facts.bucket,
            region=facts.region,
        )
        active = True
        called = False
        caller_thread = threading.get_ident()
        result: object | None = None
        try:
            if not active or called or threading.get_ident() != caller_thread:
                _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CALLBACK_INVALID")
            called = True
            result = operation(client, route)
        except ArvanS3FailbackSeparatedClientFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_OPERATION_FAILED")
        finally:
            active = False
            lease.revoke()
        if not called or _result_leaks_private_callback_value(
            result,
            blocked=(client, route, raw),
        ):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CALLBACK_INVALID")
        return result

    def admit_fi_receiver_failback_exact_pull(
        self,
        *,
        preflight: _preflight.VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> _fi_pull.PhysicalWaFiFailbackExactVersionReceiverAdmission:
        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        observed = _utc(now, code="ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLOCK_INVALID")
        checked = _verified_preflight(preflight, facts=facts, now=observed)
        try:
            result = _fi_pull.build_physical_wa_fi_failback_exact_version_receiver_admission(
                preflight=checked,
                preflight_config=facts.preflight_config,
                current_witnessed_term=current_witnessed_term,
                now=observed,
            )
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_ADMISSION_FAILED")
        return _static_fi_admission(result, facts=facts, now=observed)

    def require_fi_receiver_failback_exact_pull_admission(
        self,
        admission: _fi_pull.PhysicalWaFiFailbackExactVersionReceiverAdmission,
        *,
        preflight: _preflight.VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
        current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
        now: datetime,
    ) -> _fi_pull.PhysicalWaFiFailbackExactVersionReceiverAdmission:
        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        observed = _utc(now, code="ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLOCK_INVALID")
        checked = _verified_preflight(preflight, facts=facts, now=observed)
        try:
            result = _fi_pull.require_physical_wa_fi_failback_exact_version_receiver_admission(
                admission,
                preflight=checked,
                preflight_config=facts.preflight_config,
                current_witnessed_term=current_witnessed_term,
                now=observed,
            )
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_ADMISSION_FAILED")
        return _static_fi_admission(result, facts=facts, now=observed)

    def execute_fi_receiver_failback_exact_pull(
        self,
        *,
        admission: _fi_pull.PhysicalWaFiFailbackExactVersionReceiverAdmission,
        now: datetime,
        operation: Callable[[object, _fi_pull.PhysicalWaFiFailbackExactVersionReceiverRoute], object],
    ) -> object:
        facts = _config_facts(self._config, require_enabled=True)
        _require_root()
        observed = _utc(now, code="ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CLOCK_INVALID")
        checked = _static_fi_admission(admission, facts=facts, now=observed)
        if not callable(operation):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_OPERATION_INVALID")
        try:
            route_facts, credential = _credentials._load_root_owned_fi_receiver_credential_facts(
                facts.credential_loader_config
            )
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_CREDENTIAL_ADMISSION_FAILED")
        if (
            route_facts.endpoint != facts.endpoint
            or route_facts.region != facts.region
            or route_facts.bucket != facts.bucket
            or credential.identity_sha256 != checked.fi_receiver_identity_sha256
            or credential.identity_sha256 == checked.ir_publisher_identity_sha256
        ):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_FI_CREDENTIAL_MISMATCH")
        boto3_module, botocore_config_module = _load_boto_sdk()
        raw = _create_raw_client(
            boto3_module=boto3_module,
            botocore_config_module=botocore_config_module,
            facts=facts,
            credentials=credential,
        )
        lease = _ScopedCallbackLease()
        client = _FailbackReceiverClient(
            raw=raw,
            bucket=facts.bucket,
            exact_prefix=facts.exact_prefix,
            lease=lease,
        )
        route = _fi_pull.PhysicalWaFiFailbackExactVersionReceiverRoute(
            endpoint=facts.endpoint,
            region=facts.region,
            bucket=facts.bucket,
        )
        active = True
        called = False
        caller_thread = threading.get_ident()
        result: object | None = None
        try:
            if not active or called or threading.get_ident() != caller_thread:
                _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CALLBACK_INVALID")
            called = True
            result = operation(client, route)
        except ArvanS3FailbackSeparatedClientFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_OPERATION_FAILED")
        finally:
            active = False
            lease.revoke()
        if not called or _result_leaks_private_callback_value(
            result,
            blocked=(client, route, raw),
        ):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_CALLBACK_INVALID")
        return result
