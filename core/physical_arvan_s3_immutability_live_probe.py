"""Root-only injected-S3 collector for Arvan immutability preflight evidence.

This is deliberately a local policy adapter, not an S3 implementation.  It
does not import an SDK, load a credential, resolve an endpoint, open a socket,
or execute a process.  A root-owned deployment must inject two separately
scoped S3 clients: one FI publisher client and one IR receiver client.

The adapter permits only one internally generated disposable key below the
campaign-bound preflight namespace.  It cannot accept a caller-selected URL,
endpoint, prefix, key, range, header, ACL, version, credential, or operation.
All errors are fixed codes so S3 exception text and secret-shaped data never
cross this boundary.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import secrets
from typing import Any

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
)
from core.physical_arvan_immutability_preflight import (
    ARVAN_ACL_POSTURE,
    ARVAN_DISPOSABLE_DELETE_DENIED,
    ARVAN_DISPOSABLE_EXACT_GET_SUCCEEDED,
    ARVAN_VERSIONING_STATUS,
    MAX_PHYSICAL_ARVAN_DISPOSABLE_CIPHERTEXT_BYTES,
    MAX_PHYSICAL_ARVAN_RETENTION_DAYS,
    MIN_PHYSICAL_ARVAN_RETENTION_DAYS,
    PhysicalArvanCredentialRestrictionObservation,
    PhysicalArvanDeniedOperationObservation,
    PhysicalArvanDisposableImmutabilityProbe,
    PhysicalArvanImmutabilityPreflightBinding,
    PhysicalArvanImmutabilityPreflightError,
    PhysicalArvanImmutabilityPreflightObservation,
    build_physical_arvan_immutability_preflight_observation,
)


__all__ = (
    "PHYSICAL_ARVAN_S3_IMMUTABILITY_LIVE_PROBE_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_IMMUTABILITY_LIVE_PROBE_SCHEMA",
    "InjectedS3AccessDenied",
    "PhysicalArvanS3ImmutabilityLiveProbe",
    "PhysicalArvanS3ImmutabilityLiveProbeConfig",
    "PhysicalArvanS3ImmutabilityLiveProbeError",
    "PhysicalArvanS3ImmutabilityScopedClient",
)


PHYSICAL_ARVAN_S3_IMMUTABILITY_LIVE_PROBE_SCHEMA = (
    "gold-trade-physical-arvan-s3-immutability-live-probe-v1"
)
PHYSICAL_ARVAN_S3_IMMUTABILITY_LIVE_PROBE_DEFAULT_ENABLED = False

_ROOT_PINNED_DISPOSABLE_PREFIX = "physical-preflight/"
_ROOT_PINNED_DISPOSABLE_NAMESPACE = "arvan-immutability/"
_ROOT_PINNED_OBJECT_SUFFIX_PREFIX = "probe-"
_ROOT_PINNED_OBJECT_SUFFIX_EXTENSION = ".age"
_ROOT_PINNED_PAYLOAD_PREFIX = b"gold-trade-disposable-immutability-probe-v1\n"
_ROOT_PINNED_RANDOM_BYTES = 384
_ROOT_PINNED_CONTENT_TYPE = "application/octet-stream"
_ROOT_PINNED_CACHE_CONTROL = "no-store"
_ROOT_PINNED_ACL = "private"
_ROOT_PINNED_CHECKSUM_ALGORITHM = "SHA256"
_ROOT_PINNED_CHECKSUM_MODE = "ENABLED"
_ROOT_PINNED_OBJECT_LOCK_MODE = "COMPLIANCE"
_ROOT_PINNED_MAX_LIST_KEYS = 2
_ROOT_PINNED_RANGE_PREFIX = "bytes=0-"

_ENDPOINT_RE = re.compile(
    r"^https://s3\.([a-z0-9][a-z0-9-]{0,62})\.arvanstorage\.ir$",
    re.ASCII,
)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_SAFE_RESPONSE_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$", re.ASCII)

_FI_ALLOWED_OPERATIONS = (
    "GetBucketAcl",
    "GetBucketVersioning",
    "GetObjectLockConfiguration",
    "PutObject:create-only",
    "ListObjectVersions:exact-key",
    "GetObjectRetention:exact-version",
    "GetObject:exact-version",
    "HeadObject:exact-version",
)
_IR_ALLOWED_OPERATIONS = ("GetObject:exact-version", "HeadObject:exact-version")
_FI_DENIED_OPERATIONS = ("DeleteObject", "DeleteObjectVersion", "PutObject:overwrite")
_IR_DENIED_OPERATIONS = (
    "DeleteObject",
    "DeleteObjectVersion",
    "ListBucket",
    "ListObjectVersions",
    "PutObject",
)


class PhysicalArvanS3ImmutabilityLiveProbeError(
    PhysicalArvanImmutabilityPreflightError
):
    """A redacted local policy or injected-client collection failure."""


class InjectedS3AccessDenied(Exception):
    """Optional non-secret denial marker an injected test/client may raise.

    Real SDK clients need not import this class.  The adapter also recognizes
    only a structured S3 ``response.Error.Code == 'AccessDenied'`` signal and
    never inspects exception text.
    """


@dataclass(frozen=True)
class PhysicalArvanS3ImmutabilityScopedClient:
    """One non-secret identity digest paired with one injected S3 client."""

    credential_identity_sha256: str
    client: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalArvanS3ImmutabilityLiveProbeConfig:
    """Default-off root-owned probe policy with no endpoint or credentials."""

    schema: str = PHYSICAL_ARVAN_S3_IMMUTABILITY_LIVE_PROBE_SCHEMA
    binding: PhysicalArvanImmutabilityPreflightBinding | None = field(
        default=None,
        repr=False,
    )
    fi_publisher: PhysicalArvanS3ImmutabilityScopedClient | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    ir_receiver: PhysicalArvanS3ImmutabilityScopedClient | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    enabled: bool = PHYSICAL_ARVAN_S3_IMMUTABILITY_LIVE_PROBE_DEFAULT_ENABLED


@dataclass(frozen=True)
class _BindingFacts:
    binding: PhysicalArvanImmutabilityPreflightBinding
    disposable_prefix: str


@dataclass(frozen=True)
class _ProbeFacts:
    binding: _BindingFacts
    fi_publisher: PhysicalArvanS3ImmutabilityScopedClient
    ir_receiver: PhysicalArvanS3ImmutabilityScopedClient


def _fail(code: str) -> None:
    raise PhysicalArvanS3ImmutabilityLiveProbeError(code)


def _sha256(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    try:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            _fail(code)
        return value.astimezone(timezone.utc)
    except PhysicalArvanS3ImmutabilityLiveProbeError:
        raise
    except Exception:
        _fail(code)


def _plain_mapping(value: object, *, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    try:
        return dict(value)
    except Exception:
        _fail(code)


def _binding_facts(value: object) -> _BindingFacts:
    """Independently pin the preflight binding before any client call."""

    if type(value) is not PhysicalArvanImmutabilityPreflightBinding:
        _fail("ARVAN_S3_IMMUTABILITY_BINDING_INVALID")
    if type(value.campaign_id) is not str or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None:
        _fail("ARVAN_S3_IMMUTABILITY_BINDING_INVALID")
    if type(value.release_sha) is not str or RELEASE_SHA_RE.fullmatch(value.release_sha) is None:
        _fail("ARVAN_S3_IMMUTABILITY_BINDING_INVALID")
    if value.source_site != "webapp_fi" or value.destination_site != "webapp_ir":
        _fail("ARVAN_S3_IMMUTABILITY_BINDING_INVALID")
    route_binding_sha256 = _sha256(
        value.route_binding_sha256,
        code="ARVAN_S3_IMMUTABILITY_BINDING_INVALID",
    )
    if type(value.endpoint) is not str or type(value.region) is not str:
        _fail("ARVAN_S3_IMMUTABILITY_BINDING_INVALID")
    endpoint_match = _ENDPOINT_RE.fullmatch(value.endpoint)
    if endpoint_match is None or endpoint_match.group(1) != value.region:
        _fail("ARVAN_S3_IMMUTABILITY_BINDING_INVALID")
    if type(value.bucket) is not str or _BUCKET_RE.fullmatch(value.bucket) is None:
        _fail("ARVAN_S3_IMMUTABILITY_BINDING_INVALID")
    if (
        type(value.minimum_retention_days) is not int
        or not MIN_PHYSICAL_ARVAN_RETENTION_DAYS
        <= value.minimum_retention_days
        <= MAX_PHYSICAL_ARVAN_RETENTION_DAYS
    ):
        _fail("ARVAN_S3_IMMUTABILITY_BINDING_INVALID")
    binding = PhysicalArvanImmutabilityPreflightBinding(
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        route_binding_sha256=route_binding_sha256,
        endpoint=value.endpoint,
        region=value.region,
        bucket=value.bucket,
        minimum_retention_days=value.minimum_retention_days,
    )
    return _BindingFacts(
        binding=binding,
        disposable_prefix=(
            _ROOT_PINNED_DISPOSABLE_PREFIX
            + binding.campaign_id
            + "/"
            + _ROOT_PINNED_DISPOSABLE_NAMESPACE
        ),
    )


def _scoped_client(
    value: object,
    *,
    code: str,
) -> PhysicalArvanS3ImmutabilityScopedClient:
    if type(value) is not PhysicalArvanS3ImmutabilityScopedClient:
        _fail(code)
    identity = _sha256(value.credential_identity_sha256, code=code)
    if value.client is None:
        _fail(code)
    return PhysicalArvanS3ImmutabilityScopedClient(
        credential_identity_sha256=identity,
        client=value.client,
    )


def _probe_facts(
    config: object,
    *,
    requested_binding: object,
) -> _ProbeFacts:
    if type(config) is not PhysicalArvanS3ImmutabilityLiveProbeConfig:
        _fail("ARVAN_S3_IMMUTABILITY_CONFIG_INVALID")
    if config.schema != PHYSICAL_ARVAN_S3_IMMUTABILITY_LIVE_PROBE_SCHEMA:
        _fail("ARVAN_S3_IMMUTABILITY_CONFIG_INVALID")
    if type(config.enabled) is not bool:
        _fail("ARVAN_S3_IMMUTABILITY_CONFIG_INVALID")
    if config.enabled is not True:
        _fail("ARVAN_S3_IMMUTABILITY_LIVE_PROBE_DISABLED")
    configured = _binding_facts(config.binding)
    requested = _binding_facts(requested_binding)
    if configured.binding != requested.binding:
        _fail("ARVAN_S3_IMMUTABILITY_BINDING_MISMATCH")
    fi_publisher = _scoped_client(
        config.fi_publisher,
        code="ARVAN_S3_IMMUTABILITY_FI_CLIENT_INVALID",
    )
    ir_receiver = _scoped_client(
        config.ir_receiver,
        code="ARVAN_S3_IMMUTABILITY_IR_CLIENT_INVALID",
    )
    if (
        fi_publisher.credential_identity_sha256
        == ir_receiver.credential_identity_sha256
        or fi_publisher.client is ir_receiver.client
    ):
        _fail("ARVAN_S3_IMMUTABILITY_CREDENTIALS_NOT_SEPARATE")
    return _ProbeFacts(
        binding=configured,
        fi_publisher=fi_publisher,
        ir_receiver=ir_receiver,
    )


def _response(
    client: object,
    *,
    operation: str,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Call one fixed injected operation, never exposing external failures."""

    try:
        method = getattr(client, operation, None)
    except Exception:
        _fail("ARVAN_S3_IMMUTABILITY_CLIENT_INVALID")
    if not callable(method):
        _fail("ARVAN_S3_IMMUTABILITY_CLIENT_INVALID")
    try:
        result = method(**dict(request))
    except Exception:
        _fail("ARVAN_S3_IMMUTABILITY_CLIENT_OPERATION_FAILED")
    return _plain_mapping(result, code="ARVAN_S3_IMMUTABILITY_RESPONSE_INVALID")


def _is_access_denied(error: Exception) -> bool:
    if isinstance(error, InjectedS3AccessDenied):
        return True
    try:
        response = getattr(error, "response", None)
    except Exception:
        return False
    if not isinstance(response, Mapping):
        return False
    try:
        error_value = dict(response).get("Error")
        if not isinstance(error_value, Mapping):
            return False
        return dict(error_value).get("Code") == "AccessDenied"
    except Exception:
        return False


def _expect_access_denied(
    client: object,
    *,
    operation: str,
    request: Mapping[str, Any],
) -> PhysicalArvanDeniedOperationObservation:
    try:
        method = getattr(client, operation, None)
    except Exception:
        _fail("ARVAN_S3_IMMUTABILITY_CLIENT_INVALID")
    if not callable(method):
        _fail("ARVAN_S3_IMMUTABILITY_CLIENT_INVALID")
    try:
        method(**dict(request))
    except Exception as error:
        if not _is_access_denied(error):
            _fail("ARVAN_S3_IMMUTABILITY_DENIED_OPERATION_UNPROVEN")
        return PhysicalArvanDeniedOperationObservation(
            operation="",
            outcome=ARVAN_DISPOSABLE_DELETE_DENIED,
        )
    _fail("ARVAN_S3_IMMUTABILITY_DENIED_OPERATION_ACCEPTED")


def _private_canonical_owner_acl(response: Mapping[str, Any]) -> None:
    owner = response.get("Owner")
    grants = response.get("Grants")
    if not isinstance(owner, Mapping) or not isinstance(grants, list) or len(grants) != 1:
        _fail("ARVAN_S3_IMMUTABILITY_ACL_UNPROVEN")
    owner_value = _plain_mapping(owner, code="ARVAN_S3_IMMUTABILITY_ACL_UNPROVEN")
    owner_id = owner_value.get("ID")
    if type(owner_id) is not str or _SAFE_RESPONSE_ID_RE.fullmatch(owner_id) is None:
        _fail("ARVAN_S3_IMMUTABILITY_ACL_UNPROVEN")
    grant = grants[0]
    if not isinstance(grant, Mapping):
        _fail("ARVAN_S3_IMMUTABILITY_ACL_UNPROVEN")
    grant_value = _plain_mapping(grant, code="ARVAN_S3_IMMUTABILITY_ACL_UNPROVEN")
    grantee = grant_value.get("Grantee")
    if not isinstance(grantee, Mapping):
        _fail("ARVAN_S3_IMMUTABILITY_ACL_UNPROVEN")
    grantee_value = _plain_mapping(
        grantee,
        code="ARVAN_S3_IMMUTABILITY_ACL_UNPROVEN",
    )
    if (
        grantee_value.get("Type") != "CanonicalUser"
        or grantee_value.get("ID") != owner_id
        or grant_value.get("Permission") != "FULL_CONTROL"
    ):
        _fail("ARVAN_S3_IMMUTABILITY_ACL_UNPROVEN")


def _versioning_enabled(response: Mapping[str, Any]) -> None:
    if response.get("Status") != ARVAN_VERSIONING_STATUS:
        _fail("ARVAN_S3_IMMUTABILITY_VERSIONING_UNPROVEN")


def _default_compliance_retention(
    response: Mapping[str, Any],
    *,
    minimum_days: int,
) -> int:
    configuration = response.get("ObjectLockConfiguration")
    if not isinstance(configuration, Mapping):
        _fail("ARVAN_S3_IMMUTABILITY_OBJECT_LOCK_UNPROVEN")
    configuration_value = _plain_mapping(
        configuration,
        code="ARVAN_S3_IMMUTABILITY_OBJECT_LOCK_UNPROVEN",
    )
    if configuration_value.get("ObjectLockEnabled") != "Enabled":
        _fail("ARVAN_S3_IMMUTABILITY_OBJECT_LOCK_UNPROVEN")
    rule = configuration_value.get("Rule")
    if not isinstance(rule, Mapping):
        _fail("ARVAN_S3_IMMUTABILITY_OBJECT_LOCK_UNPROVEN")
    rule_value = _plain_mapping(rule, code="ARVAN_S3_IMMUTABILITY_OBJECT_LOCK_UNPROVEN")
    retention = rule_value.get("DefaultRetention")
    if not isinstance(retention, Mapping):
        _fail("ARVAN_S3_IMMUTABILITY_OBJECT_LOCK_UNPROVEN")
    retention_value = _plain_mapping(
        retention,
        code="ARVAN_S3_IMMUTABILITY_OBJECT_LOCK_UNPROVEN",
    )
    days = retention_value.get("Days")
    if (
        retention_value.get("Mode") != _ROOT_PINNED_OBJECT_LOCK_MODE
        or type(days) is not int
        or days < minimum_days
        or days > MAX_PHYSICAL_ARVAN_RETENTION_DAYS
        or "Years" in retention_value
    ):
        _fail("ARVAN_S3_IMMUTABILITY_OBJECT_LOCK_UNPROVEN")
    return days


def _version_id(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or value == "null"
        or VERSION_ID_RE.fullmatch(value) is None
    ):
        _fail(code)
    return value


def _payload() -> bytes:
    try:
        random_bytes = secrets.token_bytes(_ROOT_PINNED_RANDOM_BYTES)
    except Exception:
        _fail("ARVAN_S3_IMMUTABILITY_PAYLOAD_GENERATION_FAILED")
    if type(random_bytes) is not bytes or len(random_bytes) != _ROOT_PINNED_RANDOM_BYTES:
        _fail("ARVAN_S3_IMMUTABILITY_PAYLOAD_GENERATION_FAILED")
    payload = _ROOT_PINNED_PAYLOAD_PREFIX + random_bytes
    if not 1 <= len(payload) <= MAX_PHYSICAL_ARVAN_DISPOSABLE_CIPHERTEXT_BYTES:
        _fail("ARVAN_S3_IMMUTABILITY_PAYLOAD_GENERATION_FAILED")
    return payload


def _disposable_key(*, prefix: str, observed_at: datetime) -> str:
    try:
        nonce = secrets.token_hex(16)
    except Exception:
        _fail("ARVAN_S3_IMMUTABILITY_KEY_GENERATION_FAILED")
    if type(nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", nonce, re.ASCII) is None:
        _fail("ARVAN_S3_IMMUTABILITY_KEY_GENERATION_FAILED")
    timestamp = observed_at.strftime("%Y%m%dT%H%M%SZ")
    key = (
        prefix
        + _ROOT_PINNED_OBJECT_SUFFIX_PREFIX
        + timestamp
        + "-"
        + nonce
        + _ROOT_PINNED_OBJECT_SUFFIX_EXTENSION
    )
    if (
        OBJECT_KEY_RE.fullmatch(key) is None
        or not key.startswith(prefix)
        or "/" in key.removeprefix(prefix)
    ):
        _fail("ARVAN_S3_IMMUTABILITY_KEY_GENERATION_FAILED")
    return key


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
        "ContentType": _ROOT_PINNED_CONTENT_TYPE,
        "CacheControl": _ROOT_PINNED_CACHE_CONTROL,
        "ACL": _ROOT_PINNED_ACL,
        "ChecksumAlgorithm": _ROOT_PINNED_CHECKSUM_ALGORITHM,
        "ChecksumSHA256": checksum_b64,
        "ObjectLockMode": _ROOT_PINNED_OBJECT_LOCK_MODE,
        "ObjectLockRetainUntilDate": retention_until,
    }
    if create_only:
        request["IfNoneMatch"] = "*"
    return request


def _validate_exact_metadata(
    response: Mapping[str, Any],
    *,
    version_id: str,
    payload_bytes: int,
    checksum_b64: str,
    retention_until: datetime,
) -> None:
    if (
        response.get("VersionId") != version_id
        or type(response.get("ContentLength")) is not int
        or response.get("ContentLength") != payload_bytes
        or response.get("ContentType") != _ROOT_PINNED_CONTENT_TYPE
        or response.get("CacheControl") != _ROOT_PINNED_CACHE_CONTROL
        or response.get("ChecksumSHA256") != checksum_b64
        or response.get("ObjectLockMode") != _ROOT_PINNED_OBJECT_LOCK_MODE
    ):
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID")
    retained_until = _utc(
        response.get("ObjectLockRetainUntilDate"),
        code="ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID",
    )
    if retained_until != retention_until:
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID")


def _validate_retention_readback(
    response: Mapping[str, Any],
    *,
    retention_not_before: datetime,
) -> datetime:
    retention = response.get("Retention")
    if not isinstance(retention, Mapping):
        _fail("ARVAN_S3_IMMUTABILITY_RETENTION_READBACK_INVALID")
    retention_value = _plain_mapping(
        retention,
        code="ARVAN_S3_IMMUTABILITY_RETENTION_READBACK_INVALID",
    )
    if retention_value.get("Mode") != _ROOT_PINNED_OBJECT_LOCK_MODE:
        _fail("ARVAN_S3_IMMUTABILITY_RETENTION_READBACK_INVALID")
    retained_until = _utc(
        retention_value.get("RetainUntilDate"),
        code="ARVAN_S3_IMMUTABILITY_RETENTION_READBACK_INVALID",
    )
    if retained_until < retention_not_before:
        _fail("ARVAN_S3_IMMUTABILITY_RETENTION_READBACK_INVALID")
    return retained_until


def _validate_exact_list_readback(
    response: Mapping[str, Any],
    *,
    key: str,
    version_id: str,
    payload_bytes: int,
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
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_LIST_READBACK_INVALID")
    item = versions[0]
    if not isinstance(item, Mapping):
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_LIST_READBACK_INVALID")
    item_value = _plain_mapping(
        item,
        code="ARVAN_S3_IMMUTABILITY_EXACT_LIST_READBACK_INVALID",
    )
    if (
        item_value.get("Key") != key
        or item_value.get("VersionId") != version_id
        or item_value.get("IsLatest") is not True
        or type(item_value.get("Size")) is not int
        or item_value.get("Size") != payload_bytes
    ):
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_LIST_READBACK_INVALID")


def _validate_exact_payload_readback(
    response: Mapping[str, Any],
    *,
    payload: bytes,
    version_id: str,
    checksum_b64: str,
    retention_until: datetime,
) -> None:
    _validate_exact_metadata(
        response,
        version_id=version_id,
        payload_bytes=len(payload),
        checksum_b64=checksum_b64,
        retention_until=retention_until,
    )
    expected_content_range = "bytes 0-" + str(len(payload) - 1) + "/" + str(len(payload))
    if (
        response.get("ContentRange") != expected_content_range
        or response.get("AcceptRanges") != "bytes"
    ):
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID")
    body = response.get("Body")
    try:
        reader = getattr(body, "read", None)
    except Exception:
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID")
    if not callable(reader):
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID")
    try:
        received = reader(len(payload) + 1)
        trailing = reader(1)
    except Exception:
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID")
    if type(received) is not bytes or type(trailing) is not bytes:
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID")
    if (
        trailing
        or received != payload
        or hashlib.sha256(received).hexdigest()
        != hashlib.sha256(payload).hexdigest()
    ):
        _fail("ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID")


def _read_exact_version(
    client: object,
    *,
    bucket: str,
    key: str,
    version_id: str,
    payload: bytes,
    checksum_b64: str,
    retention_until: datetime,
) -> None:
    response = _response(
        client,
        operation="get_object",
        request={
            "Bucket": bucket,
            "Key": key,
            "VersionId": version_id,
            "ChecksumMode": _ROOT_PINNED_CHECKSUM_MODE,
            "Range": _ROOT_PINNED_RANGE_PREFIX + str(len(payload) - 1),
        },
    )
    _validate_exact_payload_readback(
        response,
        payload=payload,
        version_id=version_id,
        checksum_b64=checksum_b64,
        retention_until=retention_until,
    )
    head = _response(
        client,
        operation="head_object",
        request={
            "Bucket": bucket,
            "Key": key,
            "VersionId": version_id,
            "ChecksumMode": _ROOT_PINNED_CHECKSUM_MODE,
        },
    )
    _validate_exact_metadata(
        head,
        version_id=version_id,
        payload_bytes=len(payload),
        checksum_b64=checksum_b64,
        retention_until=retention_until,
    )


def _retention_evidence_sha256(
    *,
    retention_days: int,
    retained_until: datetime,
) -> str:
    evidence = {
        "schema": PHYSICAL_ARVAN_S3_IMMUTABILITY_LIVE_PROBE_SCHEMA,
        "versioning_status": ARVAN_VERSIONING_STATUS,
        "acl_posture": ARVAN_ACL_POSTURE,
        "object_lock_enabled": "Enabled",
        "retention_mode": _ROOT_PINNED_OBJECT_LOCK_MODE,
        "retention_days": retention_days,
        "retained_until": retained_until.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    try:
        raw = json.dumps(
            evidence,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail("ARVAN_S3_IMMUTABILITY_RETENTION_EVIDENCE_INVALID")
    return hashlib.sha256(raw).hexdigest()


def _denied_observation(
    *,
    client: object,
    operation_name: str,
    s3_operation: str,
    request: Mapping[str, Any],
) -> PhysicalArvanDeniedOperationObservation:
    result = _expect_access_denied(
        client,
        operation=s3_operation,
        request=request,
    )
    return PhysicalArvanDeniedOperationObservation(
        operation=operation_name,
        outcome=result.outcome,
    )


class PhysicalArvanS3ImmutabilityLiveProbe:
    """Default-off, root-only S3 implementation of the preflight probe protocol."""

    def __init__(
        self,
        config: PhysicalArvanS3ImmutabilityLiveProbeConfig = (
            PhysicalArvanS3ImmutabilityLiveProbeConfig()
        ),
    ) -> None:
        # Keep construction inert: configuration and client inspection happen
        # only in collect(), after root/default-off checks.
        self._config = config

    def collect(
        self,
        *,
        binding: PhysicalArvanImmutabilityPreflightBinding,
        observed_at: datetime,
    ) -> PhysicalArvanImmutabilityPreflightObservation:
        """Collect one strictly disposable immutable-retention observation."""

        if os.geteuid() != 0:
            _fail("ARVAN_S3_IMMUTABILITY_REQUIRES_ROOT")
        facts = _probe_facts(self._config, requested_binding=binding)
        observed = _utc(
            observed_at,
            code="ARVAN_S3_IMMUTABILITY_OBSERVATION_CLOCK_INVALID",
        )
        bucket = facts.binding.binding.bucket
        fi_client = facts.fi_publisher.client
        ir_client = facts.ir_receiver.client

        acl = _response(
            fi_client,
            operation="get_bucket_acl",
            request={"Bucket": bucket},
        )
        _private_canonical_owner_acl(acl)
        versioning = _response(
            fi_client,
            operation="get_bucket_versioning",
            request={"Bucket": bucket},
        )
        _versioning_enabled(versioning)
        object_lock = _response(
            fi_client,
            operation="get_object_lock_configuration",
            request={"Bucket": bucket},
        )
        retention_days = _default_compliance_retention(
            object_lock,
            minimum_days=facts.binding.binding.minimum_retention_days,
        )

        payload = _payload()
        ciphertext_sha256, checksum_b64 = _checksum(payload)
        key = _disposable_key(prefix=facts.binding.disposable_prefix, observed_at=observed)
        retention_not_before = observed + timedelta(days=retention_days)
        create_response = _response(
            fi_client,
            operation="put_object",
            request=_put_request(
                bucket=bucket,
                key=key,
                payload=payload,
                checksum_b64=checksum_b64,
                retention_until=retention_not_before,
                create_only=True,
            ),
        )
        version_id = _version_id(
            create_response.get("VersionId"),
            code="ARVAN_S3_IMMUTABILITY_CREATE_ONLY_UNPROVEN",
        )

        exact_versions = _response(
            fi_client,
            operation="list_object_versions",
            request={
                "Bucket": bucket,
                "Prefix": key,
                "MaxKeys": _ROOT_PINNED_MAX_LIST_KEYS,
            },
        )
        _validate_exact_list_readback(
            exact_versions,
            key=key,
            version_id=version_id,
            payload_bytes=len(payload),
        )
        retention_response = _response(
            fi_client,
            operation="get_object_retention",
            request={"Bucket": bucket, "Key": key, "VersionId": version_id},
        )
        retained_until = _validate_retention_readback(
            retention_response,
            retention_not_before=retention_not_before,
        )
        _read_exact_version(
            fi_client,
            bucket=bucket,
            key=key,
            version_id=version_id,
            payload=payload,
            checksum_b64=checksum_b64,
            retention_until=retained_until,
        )

        fi_denied = (
            _denied_observation(
                client=fi_client,
                operation_name=_FI_DENIED_OPERATIONS[0],
                s3_operation="delete_object",
                request={"Bucket": bucket, "Key": key},
            ),
            _denied_observation(
                client=fi_client,
                operation_name=_FI_DENIED_OPERATIONS[1],
                s3_operation="delete_object",
                request={"Bucket": bucket, "Key": key, "VersionId": version_id},
            ),
            _denied_observation(
                client=fi_client,
                operation_name=_FI_DENIED_OPERATIONS[2],
                s3_operation="put_object",
                request=_put_request(
                    bucket=bucket,
                    key=key,
                    payload=payload + b"!",
                    checksum_b64=_checksum(payload + b"!")[1],
                    retention_until=retention_not_before,
                    create_only=False,
                ),
            ),
        )

        ir_denied = (
            _denied_observation(
                client=ir_client,
                operation_name=_IR_DENIED_OPERATIONS[0],
                s3_operation="delete_object",
                request={"Bucket": bucket, "Key": key},
            ),
            _denied_observation(
                client=ir_client,
                operation_name=_IR_DENIED_OPERATIONS[1],
                s3_operation="delete_object",
                request={"Bucket": bucket, "Key": key, "VersionId": version_id},
            ),
            _denied_observation(
                client=ir_client,
                operation_name=_IR_DENIED_OPERATIONS[2],
                s3_operation="list_objects_v2",
                request={"Bucket": bucket, "Prefix": key, "MaxKeys": 1},
            ),
            _denied_observation(
                client=ir_client,
                operation_name=_IR_DENIED_OPERATIONS[3],
                s3_operation="list_object_versions",
                request={"Bucket": bucket, "Prefix": key, "MaxKeys": 1},
            ),
            _denied_observation(
                client=ir_client,
                operation_name=_IR_DENIED_OPERATIONS[4],
                s3_operation="put_object",
                request=_put_request(
                    bucket=bucket,
                    key=key,
                    payload=payload + b"?",
                    checksum_b64=_checksum(payload + b"?")[1],
                    retention_until=retention_not_before,
                    create_only=False,
                ),
            ),
        )
        _read_exact_version(
            ir_client,
            bucket=bucket,
            key=key,
            version_id=version_id,
            payload=payload,
            checksum_b64=checksum_b64,
            retention_until=retained_until,
        )

        credential_restrictions = (
            PhysicalArvanCredentialRestrictionObservation(
                role="fi-publisher",
                credential_posture="scoped-credential-probed",
                credential_identity_sha256=facts.fi_publisher.credential_identity_sha256,
                allowed_operations=_FI_ALLOWED_OPERATIONS,
                denied_operations=fi_denied,
            ),
            PhysicalArvanCredentialRestrictionObservation(
                role="ir-receiver",
                credential_posture="scoped-credential-probed",
                credential_identity_sha256=facts.ir_receiver.credential_identity_sha256,
                allowed_operations=_IR_ALLOWED_OPERATIONS,
                denied_operations=ir_denied,
            ),
            PhysicalArvanCredentialRestrictionObservation(
                role="witness-controller",
                credential_posture="no-object-storage-credential-issued",
                credential_identity_sha256=None,
                allowed_operations=(),
                denied_operations=(),
            ),
        )
        disposable_probe = PhysicalArvanDisposableImmutabilityProbe(
            object_key=key,
            version_id=version_id,
            ciphertext_sha256=ciphertext_sha256,
            ciphertext_bytes=len(payload),
            delete_version_outcome=ARVAN_DISPOSABLE_DELETE_DENIED,
            delete_marker_outcome=ARVAN_DISPOSABLE_DELETE_DENIED,
            exact_version_get_outcome=ARVAN_DISPOSABLE_EXACT_GET_SUCCEEDED,
            retrieved_version_id=version_id,
            retrieved_ciphertext_sha256=ciphertext_sha256,
            retrieved_ciphertext_bytes=len(payload),
        )
        return build_physical_arvan_immutability_preflight_observation(
            binding=facts.binding.binding,
            versioning_status=ARVAN_VERSIONING_STATUS,
            acl_posture=ARVAN_ACL_POSTURE,
            retention_mode="s3-object-lock-compliance-v1",
            retention_policy_evidence_sha256=_retention_evidence_sha256(
                retention_days=retention_days,
                retained_until=retained_until,
            ),
            retention_days=retention_days,
            credential_restrictions=credential_restrictions,
            disposable_probe=disposable_probe,
            observed_at=observed,
        )
