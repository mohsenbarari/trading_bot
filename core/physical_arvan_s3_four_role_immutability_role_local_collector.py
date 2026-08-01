"""Root-owned, one-role-at-a-time S3 collector for four-role immutability.

The four-role runtime deliberately has no provider operation surface.  This
module is its separately auditable execution boundary: one instance has one
fixed role, one fixed root-owned non-secret route configuration, and opens
only that role's fixed credential file when an already-bounded runtime request
is received.  It never receives a second role's credential, a generic client,
or a direct WA-FI <-> WA-IR route.

``collect`` is default-off and root-only.  It validates the runtime's exact
campaign/release/key/version selector before it opens a credential or imports
the SDK.  The only network-capable code path is then the narrowly fixed S3
operation sequence needed to produce a semantic readback.  No raw response,
client, credential, endpoint URL, owner id, or exception text is returned.

This is intentionally a *proof* collector, not a harmless health check.  A
successful publisher proof creates one immutable probe version and deliberately
attempts a second unconditioned put plus two deletes which must be denied.  A
receiver proof performs exact-version get/head and deliberately attempts put,
delete, and list operations which must be denied.  A posture-only read-only
inspection cannot substitute for those capability proofs.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
)
from core import physical_arvan_s3_four_role_immutability_live_probe_runtime as _runtime
from core import physical_arvan_s3_four_role_immutability_preflight as _immutability
from core import physical_arvan_s3_role_local_client_support as _client_support
from core import physical_arvan_s3_role_local_credential_reader as _credential_reader
from core import physical_arvan_s3_role_profiles as _profiles
from core.physical_arvan_s3_role_local_identity import (
    PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
    ArvanS3RoleLocalIdentityProjection,
)
from core.physical_arvan_s3_role_local_route_policy import (
    ArvanS3RoleLocalRoutePolicy,
    validate_physical_arvan_s3_role_local_route_policy,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
)


__all__ = (
    "FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_BY_ROLE",
    "FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_CREDENTIAL_FILE_BY_ROLE",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_SCHEMA",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_ROLE_LOCAL_COLLECTOR_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_ROLE_LOCAL_COLLECTOR_SCHEMA",
    "PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError",
    "RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector",
    "RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig",
    "load_root_owned_physical_arvan_s3_four_role_immutability_collector_config",
    "validate_root_owned_physical_arvan_s3_four_role_immutability_collector_config",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_ROLE_LOCAL_COLLECTOR_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-immutability-role-local-collector-v1"
)
PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-immutability-role-local-collector-config-v1"
)
PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_ROLE_LOCAL_COLLECTOR_DEFAULT_ENABLED = False

# The collector configuration is deliberately separate from a credential
# file.  Both are fixed paths, root-owned, non-symlink regular files under a
# root-only directory.  A deployment cannot point a collector at an arbitrary
# config or key file through a CLI argument or environment variable.
FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_BY_ROLE = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-four-role-immutability-fi-publisher.json"
    ),
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-four-role-immutability-ir-receiver.json"
    ),
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-four-role-immutability-ir-publisher.json"
    ),
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-four-role-immutability-fi-receiver.json"
    ),
}
FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_CREDENTIAL_FILE_BY_ROLE = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-fi-publisher-credentials.json"
    ),
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-ir-receiver-credentials.json"
    ),
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-ir-publisher-credentials.json"
    ),
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-fi-receiver-credentials.json"
    ),
}

_NORMAL_DIRECTION = "fi-publisher-to-ir-receiver"
_REVERSE_DIRECTION = "ir-publisher-to-fi-receiver"
_PRIVATE_ACL_POSTURE = "private-canonical-owner-only-v1"
_VERSIONING_STATUS = "Enabled"
_SEMANTIC_RETENTION_MODE = "s3-object-lock-compliance-v1"
_PROVIDER_RETENTION_MODE = "COMPLIANCE"
_CREATE_ONLY_OUTCOME = "create-only-succeeded"
_DENIED_OUTCOME = "access-denied"
_MIN_RETENTION_DAYS = 7
_MAX_RETENTION_DAYS = 3650
_MAX_OBJECT_BYTES = 8 * 1024 * 1024
_MAX_CONFIG_BYTES = 16 * 1024
_MAX_RESPONSE_ID_BYTES = 256
_PAYLOAD_RANDOM_BYTES = 384
_PAYLOAD_PREFIX = b"gold-trade-four-role-immutability-probe-v1\n"
_CONTENT_TYPE = "application/octet-stream"
_CACHE_CONTROL = "no-store"
_ACL = "private"
_CHECKSUM_ALGORITHM = "SHA256"
_CHECKSUM_MODE = "ENABLED"
_MAX_LIST_KEYS = 2


class PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError(ValueError):
    """Stable redacted failure from the S3 execution boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig:
    """One default-off, role-local, non-secret collector configuration."""

    schema: str = PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_ROLE_LOCAL_COLLECTOR_SCHEMA
    role: str = ""
    route_policy: ArvanS3RoleLocalRoutePolicy | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    retention_days: int = 0
    enabled: bool = PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_ROLE_LOCAL_COLLECTOR_DEFAULT_ENABLED


@dataclass(frozen=True)
class _RoleSpec:
    role: str
    direction: str
    source_site: str
    destination_site: str
    namespace: str
    action_profile: str
    allowed_operations: tuple[str, ...]
    publisher: bool
    object_lock_posture_reader: bool


_ROLE_SPECS = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: _RoleSpec(
        role=_profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
        direction=_NORMAL_DIRECTION,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        action_profile=_profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
        allowed_operations=_profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS,
        publisher=True,
        object_lock_posture_reader=True,
    ),
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: _RoleSpec(
        role=_profiles.ARVAN_S3_IR_RECEIVER_ROLE,
        direction=_NORMAL_DIRECTION,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        action_profile=_profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
        allowed_operations=_profiles.ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS,
        publisher=False,
        object_lock_posture_reader=False,
    ),
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: _RoleSpec(
        role=_profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
        direction=_REVERSE_DIRECTION,
        source_site="webapp_ir",
        destination_site="webapp_fi",
        namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        action_profile=_profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
        allowed_operations=_profiles.ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS,
        publisher=True,
        object_lock_posture_reader=False,
    ),
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: _RoleSpec(
        role=_profiles.ARVAN_S3_FI_RECEIVER_ROLE,
        direction=_REVERSE_DIRECTION,
        source_site="webapp_ir",
        destination_site="webapp_fi",
        namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        action_profile=_profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
        allowed_operations=_profiles.ARVAN_S3_FI_RECEIVER_EXPECTED_ACTIONS,
        publisher=False,
        object_lock_posture_reader=False,
    ),
}


@dataclass(frozen=True)
class _CollectorFacts:
    spec: _RoleSpec
    route_policy: ArvanS3RoleLocalRoutePolicy
    retention_days: int


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError(code)


def _role_spec(value: object, *, code: str) -> _RoleSpec:
    if type(value) is not str:
        _fail(code)
    try:
        return _ROLE_SPECS[value]
    except KeyError:
        _fail(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _provider_utc(value: object, *, code: str) -> datetime:
    """Normalize a provider timestamp to the one semantic UTC representation."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if result.microsecond != 0:
        _fail(code)
    return result


def _request_utc(value: object, *, code: str) -> datetime:
    """Accept only the canonical timestamp grammar emitted by the runtime."""

    if type(value) is not datetime or value.tzinfo is not timezone.utc or value.microsecond != 0:
        _fail(code)
    return value


def _collector_facts(value: object, *, require_enabled: bool) -> _CollectorFacts:
    if type(value) is not RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_ROLE_LOCAL_COLLECTOR_SCHEMA
        or type(value.enabled) is not bool
        or type(value.route_policy) is not ArvanS3RoleLocalRoutePolicy
        or type(value.retention_days) is not int
        or not _MIN_RETENTION_DAYS <= value.retention_days <= _MAX_RETENTION_DAYS
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_INVALID")
    spec = _role_spec(
        value.role,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_INVALID",
    )
    if require_enabled and value.enabled is not True:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_DISABLED")
    try:
        route_policy = validate_physical_arvan_s3_role_local_route_policy(
            value.route_policy,
            expected_source_site=spec.source_site,
            expected_destination_site=spec.destination_site,
            expected_object_storage_namespace=spec.namespace,
            require_enabled=require_enabled,
        )
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_INVALID")
    if route_policy.enabled is not value.enabled:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ENABLED_MISMATCH")
    return _CollectorFacts(
        spec=spec,
        route_policy=route_policy,
        retention_days=value.retention_days,
    )


def validate_root_owned_physical_arvan_s3_four_role_immutability_collector_config(
    config: RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig,
) -> RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig:
    """Purely normalize one config; no file, SDK, credential, or provider I/O."""

    facts = _collector_facts(config, require_enabled=False)
    return RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_ROLE_LOCAL_COLLECTOR_SCHEMA,
        role=facts.spec.role,
        route_policy=facts.route_policy,
        retention_days=facts.retention_days,
        enabled=config.enabled,
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")


def _fixed_root_private_path(path: object) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
    parent = path.parent
    try:
        metadata = os.lstat(path)
        parent_metadata = os.lstat(parent)
        resolved = path.resolve(strict=True)
        parent_resolved = parent.resolve(strict=True)
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
    if (
        resolved != path
        or parent_resolved != parent
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or metadata.st_uid != 0
        or parent_metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        or metadata.st_size < 2
        or metadata.st_size > _MAX_CONFIG_BYTES
        or not hasattr(os, "O_NOFOLLOW")
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
    return resolved


def _read_fixed_root_private_file(path: object) -> bytes:
    fixed = _fixed_root_private_path(path)
    descriptor = -1
    try:
        before = os.lstat(fixed)
        descriptor = os.open(
            fixed,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size < 2
            or metadata.st_size > _MAX_CONFIG_BYTES
            or metadata.st_dev != before.st_dev
            or metadata.st_ino != before.st_ino
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 4096)
            if type(chunk) is not bytes:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_CONFIG_BYTES:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
            chunks.append(chunk)
        after = os.lstat(fixed)
        if (
            total != metadata.st_size
            or after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_uid != 0
            or after.st_nlink != 1
            or stat.S_ISLNK(after.st_mode)
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
        return b"".join(chunks)
    except PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError:
        raise
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")


def _parse_fixed_collector_config(
    raw: object,
    *,
    expected_role: str,
) -> RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig:
    if type(raw) is not bytes:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
    required = {"schema", "role", "endpoint", "region", "bucket", "retention_days", "enabled"}
    if type(value) is not dict or set(value) != required:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
    spec = _role_spec(
        expected_role,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID",
    )
    if (
        value["schema"] != PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_SCHEMA
        or value["role"] != spec.role
        or type(value["endpoint"]) is not str
        or type(value["region"]) is not str
        or type(value["bucket"]) is not str
        or type(value["retention_days"]) is not int
        or type(value["enabled"]) is not bool
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")
    try:
        return validate_root_owned_physical_arvan_s3_four_role_immutability_collector_config(
            RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig(
                role=spec.role,
                route_policy=ArvanS3RoleLocalRoutePolicy(
                    endpoint=value["endpoint"],
                    region=value["region"],
                    bucket=value["bucket"],
                    enabled=value["enabled"],
                    source_site=spec.source_site,
                    destination_site=spec.destination_site,
                    object_storage_namespace=spec.namespace,
                ),
                retention_days=value["retention_days"],
                enabled=value["enabled"],
            )
        )
    except PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError:
        raise
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID")


def load_root_owned_physical_arvan_s3_four_role_immutability_collector_config(
    *,
    role: str,
) -> RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig:
    """Load one fixed root-only config; no credential, SDK, or provider call."""

    spec = _role_spec(
        role,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_INVALID",
    )
    try:
        _client_support.require_role_local_root()
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ROOT_REQUIRED")
    try:
        path = FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_BY_ROLE[spec.role]
    except KeyError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_INVALID")
    return _parse_fixed_collector_config(
        _read_fixed_root_private_file(path),
        expected_role=spec.role,
    )


def _expected_object_key(
    *,
    facts: _CollectorFacts,
    campaign_id: str,
    release_sha: str,
    probe_nonce_sha256: str,
) -> str:
    return (
        f"{facts.spec.namespace}/{campaign_id}/{release_sha}/"
        f"four-role-immutability/{facts.spec.direction}/{probe_nonce_sha256}.age"
    )


def _request_common(
    request: object,
    *,
    facts: _CollectorFacts,
) -> tuple[str, str, str, datetime]:
    try:
        schema = request.schema  # type: ignore[attr-defined]
        direction = request.direction  # type: ignore[attr-defined]
        role = request.role  # type: ignore[attr-defined]
        identity = request.identity_sha256  # type: ignore[attr-defined]
        campaign_id = request.campaign_id  # type: ignore[attr-defined]
        release_sha = request.release_sha  # type: ignore[attr-defined]
        endpoint = request.endpoint  # type: ignore[attr-defined]
        region = request.region  # type: ignore[attr-defined]
        bucket = request.bucket  # type: ignore[attr-defined]
        namespace = request.object_storage_namespace  # type: ignore[attr-defined]
        observed_at = request.observed_at  # type: ignore[attr-defined]
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID")
    if (
        type(schema) is not str
        or schema != _runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA
        or type(direction) is not str
        or direction != facts.spec.direction
        or type(role) is not str
        or role != facts.spec.role
        or type(endpoint) is not str
        or endpoint != facts.route_policy.endpoint
        or type(region) is not str
        or region != facts.route_policy.region
        or type(bucket) is not str
        or bucket != facts.route_policy.bucket
        or type(namespace) is not str
        or namespace != facts.spec.namespace
        or type(campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(campaign_id) is None
        or type(release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(release_sha) is None
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID")
    return (
        campaign_id,
        release_sha,
        _sha256(
            identity,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID",
        ),
        _request_utc(
            observed_at,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID",
        ),
    )


def _publisher_request(
    value: object,
    *,
    facts: _CollectorFacts,
) -> _runtime.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest:
    if (
        type(value)
        is not _runtime.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
        or not facts.spec.publisher
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID")
    campaign_id, release_sha, _identity, observed_at = _request_common(value, facts=facts)
    nonce = _sha256(
        value.probe_nonce_sha256,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID",
    )
    if (
        type(value.object_key) is not str
        or OBJECT_KEY_RE.fullmatch(value.object_key) is None
        or ".." in value.object_key.split("/")
        or value.object_key
        != _expected_object_key(
            facts=facts,
            campaign_id=campaign_id,
            release_sha=release_sha,
            probe_nonce_sha256=nonce,
        )
        or type(value.minimum_retention_days) is not int
        or value.minimum_retention_days != facts.retention_days
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID")
    retention_floor = _request_utc(
        value.retention_not_before,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID",
    )
    minimum_floor = observed_at + timedelta(days=facts.retention_days)
    maximum_floor = minimum_floor + timedelta(
        seconds=_runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS
    )
    if retention_floor < minimum_floor or retention_floor > maximum_floor:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_RETENTION_FLOOR_INVALID")
    return value


def _receiver_request(
    value: object,
    *,
    facts: _CollectorFacts,
) -> _runtime.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest:
    if (
        type(value)
        is not _runtime.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest
        or facts.spec.publisher
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID")
    campaign_id, release_sha, _identity, observed_at = _request_common(value, facts=facts)
    version = value.immutable_version
    if type(version) is not _immutability.PhysicalArvanS3FourRoleImmutableVersionObservation:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID")
    try:
        nonce = _sha256(
            version.probe_nonce_sha256,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID",
        )
        content_sha256 = _sha256(
            version.content_sha256,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID",
        )
        retention_until = _request_utc(
            version.retention_until,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID",
        )
        retention_floor = _request_utc(
            value.retention_not_before,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID",
        )
    except AttributeError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID")
    expected_key = _expected_object_key(
        facts=facts,
        campaign_id=campaign_id,
        release_sha=release_sha,
        probe_nonce_sha256=nonce,
    )
    if (
        type(version.object_key) is not str
        or OBJECT_KEY_RE.fullmatch(version.object_key) is None
        or version.object_key != expected_key
        or type(version.object_version_id) is not str
        or VERSION_ID_RE.fullmatch(version.object_version_id) is None
        or version.object_version_id.lower() == "null"
        or type(version.content_bytes) is not int
        or not 1 <= version.content_bytes <= _MAX_OBJECT_BYTES
        or retention_floor < observed_at + timedelta(days=facts.retention_days)
        or retention_until < retention_floor
        or type(version.exact_head_version_id) is not str
        or version.exact_head_version_id != version.object_version_id
        or type(version.exact_get_version_id) is not str
        or version.exact_get_version_id != version.object_version_id
        or type(version.exact_get_content_sha256) is not str
        or version.exact_get_content_sha256 != content_sha256
        or type(version.exact_get_content_bytes) is not int
        or version.exact_get_content_bytes != version.content_bytes
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID")
    return value


def _load_role_credential(
    facts: _CollectorFacts,
) -> tuple[
    _credential_reader.ArvanS3RoleLocalRouteFacts,
    _credential_reader.ArvanS3RoleLocalCredentialFacts,
]:
    try:
        credential_file = FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_CREDENTIAL_FILE_BY_ROLE[
            facts.spec.role
        ]
        route, credential = _credential_reader.load_root_owned_arvan_s3_role_local_credential(
            route_policy=facts.route_policy,
            expected_source_site=facts.spec.source_site,
            expected_destination_site=facts.spec.destination_site,
            expected_object_storage_namespace=facts.spec.namespace,
            expected_role=facts.spec.role,
            expected_action_profile=facts.spec.action_profile,
            fixed_credential_file=credential_file,
        )
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CREDENTIAL_ADMISSION_FAILED")
    try:
        if (
            type(route) is not _credential_reader.ArvanS3RoleLocalRouteFacts
            or route.endpoint != facts.route_policy.endpoint
            or route.region != facts.route_policy.region
            or route.bucket != facts.route_policy.bucket
            or type(credential) is not _credential_reader.ArvanS3RoleLocalCredentialFacts
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CREDENTIAL_ADMISSION_FAILED")
        _sha256(
            credential.identity_sha256,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CREDENTIAL_ADMISSION_FAILED",
        )
    except PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError:
        raise
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CREDENTIAL_ADMISSION_FAILED")
    return route, credential


def _create_raw_client(
    *,
    facts: _CollectorFacts,
    credential: _credential_reader.ArvanS3RoleLocalCredentialFacts,
) -> object:
    try:
        boto3_module, botocore_config_module = _client_support.load_role_local_boto_sdk()
        client = _client_support.create_role_local_raw_s3_client(
            boto3_module=boto3_module,
            botocore_config_module=botocore_config_module,
            endpoint=facts.route_policy.endpoint,
            region=facts.route_policy.region,
            access_key=credential.access_key,
            secret_key=credential.secret_key,
        )
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_CREATE_FAILED")
    if client is None:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_CREATE_FAILED")
    return client


def _close_raw_client(client: object) -> None:
    """Do not retain an SDK transport after its semantic readback is built."""

    try:
        close = getattr(client, "close", None)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_CLOSE_FAILED")
    if close is not None and not callable(close):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_CLOSE_FAILED")
    if callable(close):
        try:
            close()
        except Exception:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_CLOSE_FAILED")


def _plain_mapping(value: object, *, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    try:
        return dict(value)
    except Exception:
        _fail(code)


def _response(
    client: object,
    *,
    operation: str,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        method = getattr(client, operation, None)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_INVALID")
    if not callable(method):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_INVALID")
    try:
        response = method(**dict(request))
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_OPERATION_FAILED")
    return _plain_mapping(
        response,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_RESPONSE_INVALID",
    )


def _is_access_denied(error: Exception) -> bool:
    """Accept only the structured S3 error code, never exception text."""

    try:
        response = getattr(error, "response", None)
    except Exception:
        return False
    if not isinstance(response, Mapping):
        return False
    try:
        details = response.get("Error")
    except Exception:
        return False
    if not isinstance(details, Mapping):
        return False
    try:
        return details.get("Code") == "AccessDenied"
    except Exception:
        return False


def _expect_access_denied(
    client: object,
    *,
    operation: str,
    request: Mapping[str, Any],
) -> None:
    try:
        method = getattr(client, operation, None)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_INVALID")
    if not callable(method):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CLIENT_INVALID")
    try:
        method(**dict(request))
    except Exception as error:
        if not _is_access_denied(error):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_DENIAL_UNPROVEN")
        return
    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_DENIED_OPERATION_ACCEPTED")


def _private_canonical_owner_acl(response: Mapping[str, Any]) -> None:
    owner = response.get("Owner")
    grants = response.get("Grants")
    if not isinstance(owner, Mapping) or not isinstance(grants, list) or len(grants) != 1:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ACL_UNPROVEN")
    owner_value = _plain_mapping(
        owner,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ACL_UNPROVEN",
    )
    owner_id = owner_value.get("ID")
    try:
        owner_is_safe = (
            type(owner_id) is str
            and bool(owner_id)
            and len(owner_id.encode("utf-8", "strict")) <= _MAX_RESPONSE_ID_BYTES
            and not any(
                ord(character) < 0x21 or ord(character) > 0x7E
                for character in owner_id
            )
        )
    except Exception:
        owner_is_safe = False
    if not owner_is_safe:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ACL_UNPROVEN")
    grant = grants[0]
    if not isinstance(grant, Mapping):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ACL_UNPROVEN")
    grant_value = _plain_mapping(
        grant,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ACL_UNPROVEN",
    )
    grantee = grant_value.get("Grantee")
    if not isinstance(grantee, Mapping):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ACL_UNPROVEN")
    grantee_value = _plain_mapping(
        grantee,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ACL_UNPROVEN",
    )
    if (
        grantee_value.get("Type") != "CanonicalUser"
        or grantee_value.get("ID") != owner_id
        or grant_value.get("Permission") != "FULL_CONTROL"
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ACL_UNPROVEN")


def _versioning_enabled(response: Mapping[str, Any]) -> None:
    if response.get("Status") != _VERSIONING_STATUS:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_VERSIONING_UNPROVEN")


def _default_compliance_retention(
    response: Mapping[str, Any],
    *,
    expected_days: int,
) -> int:
    configuration = response.get("ObjectLockConfiguration")
    if not isinstance(configuration, Mapping):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_OBJECT_LOCK_UNPROVEN")
    configuration_value = _plain_mapping(
        configuration,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_OBJECT_LOCK_UNPROVEN",
    )
    rule = configuration_value.get("Rule")
    if not isinstance(rule, Mapping):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_OBJECT_LOCK_UNPROVEN")
    rule_value = _plain_mapping(
        rule,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_OBJECT_LOCK_UNPROVEN",
    )
    retention = rule_value.get("DefaultRetention")
    if not isinstance(retention, Mapping):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_OBJECT_LOCK_UNPROVEN")
    retention_value = _plain_mapping(
        retention,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_OBJECT_LOCK_UNPROVEN",
    )
    days = retention_value.get("Days")
    if (
        configuration_value.get("ObjectLockEnabled") != "Enabled"
        or retention_value.get("Mode") != _PROVIDER_RETENTION_MODE
        or type(days) is not int
        or days != expected_days
        or "Years" in retention_value
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_OBJECT_LOCK_UNPROVEN")
    return days


def _payload() -> bytes:
    try:
        random_bytes = secrets.token_bytes(_PAYLOAD_RANDOM_BYTES)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_PAYLOAD_GENERATION_FAILED")
    if type(random_bytes) is not bytes or len(random_bytes) != _PAYLOAD_RANDOM_BYTES:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_PAYLOAD_GENERATION_FAILED")
    payload = _PAYLOAD_PREFIX + random_bytes
    if not 1 <= len(payload) <= _MAX_OBJECT_BYTES:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_PAYLOAD_GENERATION_FAILED")
    return payload


def _checksum(payload: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(payload)
    return digest.hexdigest(), base64.b64encode(digest.digest()).decode("ascii")


def _put_request(
    *,
    bucket: str,
    key: str,
    payload: bytes,
    checksum_b64: str,
    retention_until: datetime,
    create_only: bool,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": payload,
        "ContentLength": len(payload),
        "ContentType": _CONTENT_TYPE,
        "CacheControl": _CACHE_CONTROL,
        "ACL": _ACL,
        "ChecksumAlgorithm": _CHECKSUM_ALGORITHM,
        "ChecksumSHA256": checksum_b64,
        "ObjectLockMode": _PROVIDER_RETENTION_MODE,
        "ObjectLockRetainUntilDate": retention_until,
    }
    if create_only:
        request["IfNoneMatch"] = "*"
    return request


def _version_id(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or VERSION_ID_RE.fullmatch(value) is None
        or value.lower() == "null"
    ):
        _fail(code)
    return value


def _validate_exact_list_readback(
    response: Mapping[str, Any],
    *,
    key: str,
    version_id: str,
    content_bytes: int,
) -> None:
    versions = response.get("Versions")
    delete_markers = response.get("DeleteMarkers")
    if (
        response.get("IsTruncated") is not False
        or not isinstance(versions, list)
        or len(versions) != 1
        or not isinstance(delete_markers, list)
        or delete_markers != []
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_LIST_INVALID")
    version = versions[0]
    if not isinstance(version, Mapping):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_LIST_INVALID")
    item = _plain_mapping(
        version,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_LIST_INVALID",
    )
    if (
        item.get("Key") != key
        or item.get("VersionId") != version_id
        or item.get("IsLatest") is not True
        or type(item.get("Size")) is not int
        or item.get("Size") != content_bytes
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_LIST_INVALID")


def _validate_retention_readback(
    response: Mapping[str, Any],
    *,
    not_before: datetime,
) -> datetime:
    retention = response.get("Retention")
    if not isinstance(retention, Mapping):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_RETENTION_READBACK_INVALID")
    value = _plain_mapping(
        retention,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_RETENTION_READBACK_INVALID",
    )
    if value.get("Mode") != _PROVIDER_RETENTION_MODE:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_RETENTION_READBACK_INVALID")
    retained_until = _provider_utc(
        value.get("RetainUntilDate"),
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_RETENTION_READBACK_INVALID",
    )
    if retained_until < not_before:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_RETENTION_READBACK_INVALID")
    return retained_until


def _validate_exact_metadata(
    response: Mapping[str, Any],
    *,
    version_id: str,
    content_bytes: int,
    checksum_b64: str,
    retention_not_before: datetime,
) -> datetime:
    if (
        response.get("VersionId") != version_id
        or type(response.get("ContentLength")) is not int
        or response.get("ContentLength") != content_bytes
        or response.get("ContentType") != _CONTENT_TYPE
        or response.get("CacheControl") != _CACHE_CONTROL
        or response.get("ChecksumSHA256") != checksum_b64
        or response.get("ObjectLockMode") != _PROVIDER_RETENTION_MODE
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    retained_until = _provider_utc(
        response.get("ObjectLockRetainUntilDate"),
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID",
    )
    if retained_until < retention_not_before:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    return retained_until


def _validate_exact_payload_readback(
    response: Mapping[str, Any],
    *,
    payload: bytes,
    version_id: str,
    checksum_b64: str,
    retention_not_before: datetime,
) -> datetime:
    retained_until = _validate_exact_metadata(
        response,
        version_id=version_id,
        content_bytes=len(payload),
        checksum_b64=checksum_b64,
        retention_not_before=retention_not_before,
    )
    expected_range = f"bytes 0-{len(payload) - 1}/{len(payload)}"
    if response.get("ContentRange") != expected_range or response.get("AcceptRanges") != "bytes":
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    body = response.get("Body")
    try:
        reader = getattr(body, "read", None)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    if not callable(reader):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    try:
        received = reader(len(payload) + 1)
        trailing = reader(1)
        closer = getattr(body, "close", None)
        if closer is not None and not callable(closer):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
        if callable(closer):
            closer()
    except PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError:
        raise
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    if (
        type(received) is not bytes
        or type(trailing) is not bytes
        or trailing
        or received != payload
        or hashlib.sha256(received).hexdigest() != hashlib.sha256(payload).hexdigest()
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    return retained_until


def _read_exact_version(
    client: object,
    *,
    bucket: str,
    key: str,
    version_id: str,
    payload: bytes,
    checksum_b64: str,
    retention_not_before: datetime,
    expected_retention_until: datetime | None = None,
) -> datetime:
    get_response = _response(
        client,
        operation="get_object",
        request={
            "Bucket": bucket,
            "Key": key,
            "VersionId": version_id,
            "ChecksumMode": _CHECKSUM_MODE,
            "Range": f"bytes=0-{len(payload) - 1}",
        },
    )
    get_retention = _validate_exact_payload_readback(
        get_response,
        payload=payload,
        version_id=version_id,
        checksum_b64=checksum_b64,
        retention_not_before=retention_not_before,
    )
    head_response = _response(
        client,
        operation="head_object",
        request={
            "Bucket": bucket,
            "Key": key,
            "VersionId": version_id,
            "ChecksumMode": _CHECKSUM_MODE,
        },
    )
    head_retention = _validate_exact_metadata(
        head_response,
        version_id=version_id,
        content_bytes=len(payload),
        checksum_b64=checksum_b64,
        retention_not_before=retention_not_before,
    )
    if (
        head_retention != get_retention
        or (
            expected_retention_until is not None
            and get_retention != expected_retention_until
        )
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    return get_retention


def _publisher_denials(
    client: object,
    *,
    bucket: str,
    key: str,
    version_id: str,
    payload: bytes,
    retention_until: datetime,
) -> None:
    _expect_access_denied(
        client,
        operation="delete_object",
        request={"Bucket": bucket, "Key": key},
    )
    _expect_access_denied(
        client,
        operation="delete_object",
        request={"Bucket": bucket, "Key": key, "VersionId": version_id},
    )
    overwrite = payload + b"!"
    _expect_access_denied(
        client,
        operation="put_object",
        request=_put_request(
            bucket=bucket,
            key=key,
            payload=overwrite,
            checksum_b64=_checksum(overwrite)[1],
            retention_until=retention_until,
            create_only=False,
        ),
    )


def _receiver_denials(
    client: object,
    *,
    bucket: str,
    key: str,
    version_id: str,
    retention_until: datetime,
) -> None:
    # If a wrongly privileged receiver accepts this request, the resulting
    # diagnostic version is still Object-Lock protected rather than becoming
    # an unretained cleanup hazard.  A success still fails closed immediately.
    attempted_payload = b"gold-trade-four-role-receiver-write-must-deny-v1\n"
    _expect_access_denied(
        client,
        operation="put_object",
        request=_put_request(
            bucket=bucket,
            key=key,
            payload=attempted_payload,
            checksum_b64=_checksum(attempted_payload)[1],
            retention_until=retention_until,
            create_only=False,
        ),
    )
    _expect_access_denied(
        client,
        operation="delete_object",
        request={"Bucket": bucket, "Key": key},
    )
    _expect_access_denied(
        client,
        operation="delete_object",
        request={"Bucket": bucket, "Key": key, "VersionId": version_id},
    )
    _expect_access_denied(
        client,
        operation="list_objects_v2",
        request={"Bucket": bucket, "Prefix": key, "MaxKeys": 1},
    )
    _expect_access_denied(
        client,
        operation="list_object_versions",
        request={"Bucket": bucket, "Prefix": key, "MaxKeys": 1},
    )


def _collect_publisher(
    client: object,
    *,
    request: _runtime.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest,
    facts: _CollectorFacts,
) -> _runtime.PhysicalArvanS3FourRoleImmutabilityPublisherReadback:
    bucket = facts.route_policy.bucket
    acl = _response(client, operation="get_bucket_acl", request={"Bucket": bucket})
    _private_canonical_owner_acl(acl)
    versioning = _response(
        client,
        operation="get_bucket_versioning",
        request={"Bucket": bucket},
    )
    _versioning_enabled(versioning)
    bucket_readback: _runtime.PhysicalArvanS3FourRoleImmutabilityBucketReadback | None = None
    if facts.spec.object_lock_posture_reader:
        object_lock = _response(
            client,
            operation="get_object_lock_configuration",
            request={"Bucket": bucket},
        )
        default_retention_days = _default_compliance_retention(
            object_lock,
            expected_days=facts.retention_days,
        )
        bucket_readback = _runtime.PhysicalArvanS3FourRoleImmutabilityBucketReadback(
            acl_posture=_PRIVATE_ACL_POSTURE,
            versioning_status=_VERSIONING_STATUS,
            retention_mode=_SEMANTIC_RETENTION_MODE,
            retention_days=default_retention_days,
        )
    payload = _payload()
    content_sha256, checksum_b64 = _checksum(payload)
    retention_not_before = request.retention_not_before
    create_response = _response(
        client,
        operation="put_object",
        request=_put_request(
            bucket=bucket,
            key=request.object_key,
            payload=payload,
            checksum_b64=checksum_b64,
            retention_until=retention_not_before,
            create_only=True,
        ),
    )
    version_id = _version_id(
        create_response.get("VersionId"),
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CREATE_ONLY_UNPROVEN",
    )
    versions = _response(
        client,
        operation="list_object_versions",
        request={"Bucket": bucket, "Prefix": request.object_key, "MaxKeys": _MAX_LIST_KEYS},
    )
    _validate_exact_list_readback(
        versions,
        key=request.object_key,
        version_id=version_id,
        content_bytes=len(payload),
    )
    expected_retention: datetime | None = None
    if facts.spec.object_lock_posture_reader:
        retention_response = _response(
            client,
            operation="get_object_retention",
            request={"Bucket": bucket, "Key": request.object_key, "VersionId": version_id},
        )
        expected_retention = _validate_retention_readback(
            retention_response,
            not_before=retention_not_before,
        )
    retained_until = _read_exact_version(
        client,
        bucket=bucket,
        key=request.object_key,
        version_id=version_id,
        payload=payload,
        checksum_b64=checksum_b64,
        retention_not_before=retention_not_before,
        expected_retention_until=expected_retention,
    )
    _publisher_denials(
        client,
        bucket=bucket,
        key=request.object_key,
        version_id=version_id,
        payload=payload,
        retention_until=retained_until,
    )
    return _runtime.PhysicalArvanS3FourRoleImmutabilityPublisherReadback(
        schema=_runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
        direction=facts.spec.direction,
        role=facts.spec.role,
        identity_sha256=request.identity_sha256,
        probe_nonce_sha256=request.probe_nonce_sha256,
        object_key=request.object_key,
        object_version_id=version_id,
        content_sha256=content_sha256,
        content_bytes=len(payload),
        retention_until=retained_until,
        create_only_outcome=_CREATE_ONLY_OUTCOME,
        overwrite_outcome=_DENIED_OUTCOME,
        object_removal_outcome=_DENIED_OUTCOME,
        version_removal_outcome=_DENIED_OUTCOME,
        bucket_readback=bucket_readback,
    )


def _collect_receiver(
    client: object,
    *,
    request: _runtime.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest,
    facts: _CollectorFacts,
) -> _runtime.PhysicalArvanS3FourRoleImmutabilityReceiverReadback:
    version = request.immutable_version
    checksum_b64 = base64.b64encode(bytes.fromhex(version.content_sha256)).decode("ascii")
    get_response = _response(
        client,
        operation="get_object",
        request={
            "Bucket": facts.route_policy.bucket,
            "Key": version.object_key,
            "VersionId": version.object_version_id,
            "ChecksumMode": _CHECKSUM_MODE,
            "Range": f"bytes=0-{version.content_bytes - 1}",
        },
    )
    get_retention = _validate_receiver_get_response(
        get_response,
        version=version,
        checksum_b64=checksum_b64,
        retention_not_before=request.retention_not_before,
    )
    head_response = _response(
        client,
        operation="head_object",
        request={
            "Bucket": facts.route_policy.bucket,
            "Key": version.object_key,
            "VersionId": version.object_version_id,
            "ChecksumMode": _CHECKSUM_MODE,
        },
    )
    head_retention = _validate_exact_metadata(
        head_response,
        version_id=version.object_version_id,
        content_bytes=version.content_bytes,
        checksum_b64=checksum_b64,
        retention_not_before=request.retention_not_before,
    )
    if get_retention != head_retention or get_retention != version.retention_until:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    _receiver_denials(
        client,
        bucket=facts.route_policy.bucket,
        key=version.object_key,
        version_id=version.object_version_id,
        retention_until=version.retention_until,
    )
    return _runtime.PhysicalArvanS3FourRoleImmutabilityReceiverReadback(
        schema=_runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
        direction=facts.spec.direction,
        role=facts.spec.role,
        identity_sha256=request.identity_sha256,
        probe_nonce_sha256=version.probe_nonce_sha256,
        object_key=version.object_key,
        object_version_id=version.object_version_id,
        exact_head_version_id=version.object_version_id,
        exact_get_version_id=version.object_version_id,
        exact_get_content_sha256=version.content_sha256,
        exact_get_content_bytes=version.content_bytes,
        put_outcome=_DENIED_OUTCOME,
        object_removal_outcome=_DENIED_OUTCOME,
        version_removal_outcome=_DENIED_OUTCOME,
        bucket_enumeration_outcome=_DENIED_OUTCOME,
        version_enumeration_outcome=_DENIED_OUTCOME,
    )


def _validate_receiver_get_response(
    response: Mapping[str, Any],
    *,
    version: object,
    checksum_b64: str,
    retention_not_before: datetime,
) -> datetime:
    try:
        version_id = version.object_version_id
        content_bytes = version.content_bytes
        content_sha256 = version.content_sha256
    except AttributeError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    retained_until = _validate_exact_metadata(
        response,
        version_id=version_id,
        content_bytes=content_bytes,
        checksum_b64=checksum_b64,
        retention_not_before=retention_not_before,
    )
    expected_range = f"bytes 0-{content_bytes - 1}/{content_bytes}"
    if response.get("ContentRange") != expected_range or response.get("AcceptRanges") != "bytes":
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    body = response.get("Body")
    try:
        reader = getattr(body, "read", None)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    if not callable(reader):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    try:
        received = reader(content_bytes + 1)
        trailing = reader(1)
        closer = getattr(body, "close", None)
        if closer is not None and not callable(closer):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
        if callable(closer):
            closer()
    except PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError:
        raise
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    if (
        type(received) is not bytes
        or type(trailing) is not bytes
        or trailing
        or len(received) != content_bytes
        or hashlib.sha256(received).hexdigest() != content_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_EXACT_VERSION_INVALID")
    return retained_until


class RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector:
    """One-role semantic collector suitable for one injected runtime adapter."""

    __slots__ = ("_config",)

    def __init__(
        self,
        config: RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig,
    ) -> None:
        # Constructor stays inert: no credential read, SDK import, or provider
        # action occurs until the runtime invokes collect after durable IAM
        # admission and fresh nonce creation.
        self._config = validate_root_owned_physical_arvan_s3_four_role_immutability_collector_config(
            config
        )

    def identity_projection(self) -> ArvanS3RoleLocalIdentityProjection:
        """Return only this role's redacted identity fact; no provider I/O."""

        facts = _collector_facts(self._config, require_enabled=True)
        try:
            _client_support.require_role_local_root()
            _route, credential = _load_role_credential(facts)
        except PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError:
            raise
        except Exception:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CREDENTIAL_ADMISSION_FAILED")
        return ArvanS3RoleLocalIdentityProjection(
            schema=PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
            role=facts.spec.role,
            identity_sha256=credential.identity_sha256,
            action_profile=facts.spec.action_profile,
            source_site=facts.spec.source_site,
            destination_site=facts.spec.destination_site,
            object_storage_namespace=facts.spec.namespace,
            allowed_operations=facts.spec.allowed_operations,
        )

    def live_probe_adapter(
        self,
    ) -> _runtime.PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter:
        """Produce one runtime callback seam after the local identity is pinned."""

        projection = self.identity_projection()
        return _runtime.PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter(
            role=projection.role,
            identity_sha256=projection.identity_sha256,
            action_profile=projection.action_profile,
            readback_adapter=self.collect,
        )

    def collect(
        self,
        request: object,
    ) -> (
        _runtime.PhysicalArvanS3FourRoleImmutabilityPublisherReadback
        | _runtime.PhysicalArvanS3FourRoleImmutabilityReceiverReadback
    ):
        """Execute one bounded probe and return only its semantic readback."""

        try:
            _client_support.require_role_local_root()
        except Exception:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_ROOT_REQUIRED")
        facts = _collector_facts(self._config, require_enabled=True)
        if facts.spec.publisher:
            checked_request = _publisher_request(request, facts=facts)
            requested_identity = checked_request.identity_sha256
        else:
            checked_request = _receiver_request(request, facts=facts)
            requested_identity = checked_request.identity_sha256
        _route, credential = _load_role_credential(facts)
        if credential.identity_sha256 != requested_identity:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_IDENTITY_MISMATCH")
        client = _create_raw_client(facts=facts, credential=credential)
        try:
            if facts.spec.publisher:
                return _collect_publisher(client, request=checked_request, facts=facts)
            return _collect_receiver(client, request=checked_request, facts=facts)
        finally:
            _close_raw_client(client)
