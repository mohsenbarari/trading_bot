"""Fail-closed Arvan Object-Storage immutability preflight contract.

Physical PostgreSQL recovery material needs more than bucket versioning.  A
privileged Object-Storage credential could otherwise create delete markers or
replace the object history that a standby must recover.  This module provides
the narrow local evidence boundary for the disposable-bucket/provider test
required before a physical Full-Matrix campaign can be considered.

The module has no S3 SDK, credential, network, subprocess, Docker, SSH, or
filesystem import-time action.  A separately installed root-owned probe may
collect the observations later through injected, least-privileged clients;
this boundary only validates the resulting bounded evidence and makes it
opaque to the campaign readiness oracle.  It never authorizes a write,
promotion, route change, or Full-Matrix execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Any, Protocol

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
)


__all__ = (
    "DEFAULT_PHYSICAL_ARVAN_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_ARVAN_IMMUTABILITY_PREFLIGHT_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_IMMUTABILITY_PREFLIGHT_SCHEMA",
    "PhysicalArvanCredentialRestrictionObservation",
    "PhysicalArvanDeniedOperationObservation",
    "PhysicalArvanDisposableImmutabilityProbe",
    "PhysicalArvanImmutabilityPreflightBinding",
    "PhysicalArvanImmutabilityPreflightConfig",
    "PhysicalArvanImmutabilityPreflightError",
    "PhysicalArvanImmutabilityPreflightObservation",
    "PhysicalArvanImmutabilityPreflightProbe",
    "PhysicalArvanImmutabilityPreflightProjection",
    "VerifiedPhysicalArvanImmutabilityPreflight",
    "build_physical_arvan_immutability_preflight_observation",
    "canonical_physical_arvan_immutability_preflight_evidence_bytes",
    "collect_physical_arvan_immutability_preflight",
    "project_verified_physical_arvan_immutability_preflight",
    "require_verified_physical_arvan_immutability_preflight",
    "verify_physical_arvan_immutability_preflight",
)


PHYSICAL_ARVAN_IMMUTABILITY_PREFLIGHT_SCHEMA = (
    "gold-trade-physical-arvan-immutability-preflight-v1"
)
PHYSICAL_ARVAN_IMMUTABILITY_PREFLIGHT_DEFAULT_ENABLED = False

ARVAN_PROVIDER = "arvan-object-storage"
ARVAN_ACL_POSTURE = "private-canonical-owner-only-v1"
ARVAN_VERSIONING_STATUS = "Enabled"
ARVAN_RETENTION_MODES = frozenset(
    {
        "s3-object-lock-compliance-v1",
        "provider-verified-immutable-retention-v1",
    }
)
ARVAN_DISPOSABLE_DELETE_DENIED = "access-denied"
ARVAN_DISPOSABLE_EXACT_GET_SUCCEEDED = "exact-version-get-succeeded"

DEFAULT_PHYSICAL_ARVAN_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_ARVAN_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS = 900
MAX_PHYSICAL_ARVAN_IMMUTABILITY_FUTURE_SKEW_SECONDS = 5
MIN_PHYSICAL_ARVAN_RETENTION_DAYS = 7
MAX_PHYSICAL_ARVAN_RETENTION_DAYS = 3650
MAX_PHYSICAL_ARVAN_DISPOSABLE_CIPHERTEXT_BYTES = 1024 * 1024

_ENDPOINT_RE = re.compile(
    r"^https://s3\.([a-z0-9][a-z0-9-]{0,62})\.arvanstorage\.ir$",
    re.ASCII,
)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{3,127}$", re.ASCII)
_DISPOSABLE_OBJECT_SUFFIX_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}\.age$", re.ASCII
)

_ROLE_ORDER = ("fi-publisher", "ir-receiver", "witness-controller")
_EXPECTED_ALLOWED_OPERATIONS: Mapping[str, tuple[str, ...]] = {
    "fi-publisher": (
        "GetBucketAcl",
        "GetBucketVersioning",
        "GetObjectLockConfiguration",
        "PutObject:create-only",
        "ListObjectVersions:exact-key",
        "GetObjectRetention:exact-version",
        "GetObject:exact-version",
        "HeadObject:exact-version",
    ),
    "ir-receiver": ("GetObject:exact-version", "HeadObject:exact-version"),
    "witness-controller": (),
}
_EXPECTED_DENIED_OPERATIONS: Mapping[str, tuple[str, ...]] = {
    "fi-publisher": ("DeleteObject", "DeleteObjectVersion", "PutObject:overwrite"),
    "ir-receiver": (
        "DeleteObject",
        "DeleteObjectVersion",
        "ListBucket",
        "ListObjectVersions",
        "PutObject",
    ),
    "witness-controller": (),
}

_VERIFIED_CAPABILITY = object()


class PhysicalArvanImmutabilityPreflightError(ValueError):
    """The supplied immutable-retention evidence is unsafe or incomplete."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalArvanImmutabilityPreflightBinding:
    """Non-secret campaign and Object-Storage pins for one FI-to-IR route."""

    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    route_binding_sha256: str
    endpoint: str
    region: str
    bucket: str
    minimum_retention_days: int


@dataclass(frozen=True)
class PhysicalArvanDeniedOperationObservation:
    """One deliberately attempted prohibited Object-Storage operation."""

    operation: str
    outcome: str


@dataclass(frozen=True)
class PhysicalArvanCredentialRestrictionObservation:
    """One separately scoped FI/IR/Witness Object-Storage credential posture."""

    role: str
    credential_posture: str
    credential_identity_sha256: str | None
    allowed_operations: tuple[str, ...]
    denied_operations: tuple[PhysicalArvanDeniedOperationObservation, ...]


@dataclass(frozen=True)
class PhysicalArvanDisposableImmutabilityProbe:
    """Exact-version survival evidence after denied delete attempts."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    delete_version_outcome: str
    delete_marker_outcome: str
    exact_version_get_outcome: str
    retrieved_version_id: str
    retrieved_ciphertext_sha256: str
    retrieved_ciphertext_bytes: int


@dataclass(frozen=True)
class PhysicalArvanImmutabilityPreflightObservation:
    """Bounded raw preflight result; direct construction creates no trust."""

    schema: str
    status: str
    provider: str
    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    route_binding_sha256: str
    endpoint: str
    region: str
    bucket: str
    versioning_status: str
    acl_posture: str
    retention_mode: str
    retention_policy_evidence_sha256: str
    retention_days: int
    credential_restrictions: tuple[PhysicalArvanCredentialRestrictionObservation, ...]
    disposable_probe: PhysicalArvanDisposableImmutabilityProbe
    observed_at: datetime
    evidence_sha256: str


@dataclass(frozen=True)
class PhysicalArvanImmutabilityPreflightConfig:
    """Default-off root-owned collection policy with no credential material."""

    binding: PhysicalArvanImmutabilityPreflightBinding | None = None
    enabled: bool = PHYSICAL_ARVAN_IMMUTABILITY_PREFLIGHT_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_ARVAN_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS
    )


class PhysicalArvanImmutabilityPreflightProbe(Protocol):
    """Injected future live probe; its implementation owns all S3 I/O."""

    def collect(
        self,
        *,
        binding: PhysicalArvanImmutabilityPreflightBinding,
        observed_at: datetime,
    ) -> PhysicalArvanImmutabilityPreflightObservation: ...


@dataclass(frozen=True)
class VerifiedPhysicalArvanImmutabilityPreflight:
    """Opaque verified preflight evidence, not an execution authority."""

    observation: PhysicalArvanImmutabilityPreflightObservation
    binding: PhysicalArvanImmutabilityPreflightBinding
    maximum_evidence_age_seconds: int
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalArvanImmutabilityPreflightProjection:
    """Non-authorizing campaign-readiness projection of verified evidence."""

    schema: str
    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    route_binding_sha256: str
    bucket: str
    retention_mode: str
    retention_days: int
    observed_at: datetime
    evidence_sha256: str


@dataclass(frozen=True)
class _BindingFacts:
    binding: PhysicalArvanImmutabilityPreflightBinding
    disposable_object_prefix: str


def _fail(code: str) -> None:
    raise PhysicalArvanImmutabilityPreflightError(code)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise PhysicalArvanImmutabilityPreflightError(
            "ARVAN_IMMUTABILITY_CANONICAL_EVIDENCE_INVALID"
        ) from exc


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _safe_id(value: object, *, code: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _maximum_age(value: object, *, code: str) -> int:
    if (
        type(value) is not int
        or not 1 <= value <= MAX_PHYSICAL_ARVAN_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS
    ):
        _fail(code)
    return value


def _normalise_binding(value: object) -> _BindingFacts:
    if type(value) is not PhysicalArvanImmutabilityPreflightBinding:
        _fail("ARVAN_IMMUTABILITY_BINDING_INVALID")
    if type(value.campaign_id) is not str or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None:
        _fail("ARVAN_IMMUTABILITY_BINDING_INVALID")
    if type(value.release_sha) is not str or RELEASE_SHA_RE.fullmatch(value.release_sha) is None:
        _fail("ARVAN_IMMUTABILITY_BINDING_INVALID")
    if value.source_site != "webapp_fi" or value.destination_site != "webapp_ir":
        _fail("ARVAN_IMMUTABILITY_BINDING_DIRECTION_INVALID")
    route_binding_sha256 = _sha256(
        value.route_binding_sha256, code="ARVAN_IMMUTABILITY_BINDING_INVALID"
    )
    if type(value.endpoint) is not str:
        _fail("ARVAN_IMMUTABILITY_BINDING_ENDPOINT_INVALID")
    endpoint_match = _ENDPOINT_RE.fullmatch(value.endpoint)
    if endpoint_match is None or type(value.region) is not str:
        _fail("ARVAN_IMMUTABILITY_BINDING_ENDPOINT_INVALID")
    if endpoint_match.group(1) != value.region:
        _fail("ARVAN_IMMUTABILITY_BINDING_ENDPOINT_INVALID")
    if type(value.bucket) is not str or _BUCKET_RE.fullmatch(value.bucket) is None:
        _fail("ARVAN_IMMUTABILITY_BINDING_BUCKET_INVALID")
    if (
        type(value.minimum_retention_days) is not int
        or not MIN_PHYSICAL_ARVAN_RETENTION_DAYS
        <= value.minimum_retention_days
        <= MAX_PHYSICAL_ARVAN_RETENTION_DAYS
    ):
        _fail("ARVAN_IMMUTABILITY_BINDING_RETENTION_INVALID")
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
        disposable_object_prefix=(
            f"physical-preflight/{binding.campaign_id}/arvan-immutability/"
        ),
    )


def _normalise_denied_operations(
    value: object,
    *,
    role: str,
) -> tuple[PhysicalArvanDeniedOperationObservation, ...]:
    expected = _EXPECTED_DENIED_OPERATIONS[role]
    if not isinstance(value, tuple) or len(value) != len(expected):
        _fail("ARVAN_IMMUTABILITY_CREDENTIAL_RESTRICTION_INVALID")
    output: list[PhysicalArvanDeniedOperationObservation] = []
    for item, expected_operation in zip(value, expected, strict=True):
        if type(item) is not PhysicalArvanDeniedOperationObservation:
            _fail("ARVAN_IMMUTABILITY_CREDENTIAL_RESTRICTION_INVALID")
        if item.operation != expected_operation or item.outcome != ARVAN_DISPOSABLE_DELETE_DENIED:
            _fail("ARVAN_IMMUTABILITY_CREDENTIAL_RESTRICTION_INVALID")
        output.append(
            PhysicalArvanDeniedOperationObservation(
                operation=expected_operation,
                outcome=ARVAN_DISPOSABLE_DELETE_DENIED,
            )
        )
    return tuple(output)


def _normalise_credential_restrictions(
    value: object,
) -> tuple[PhysicalArvanCredentialRestrictionObservation, ...]:
    if not isinstance(value, tuple) or len(value) != len(_ROLE_ORDER):
        _fail("ARVAN_IMMUTABILITY_CREDENTIAL_RESTRICTIONS_INVALID")
    result: list[PhysicalArvanCredentialRestrictionObservation] = []
    identity_hashes: set[str] = set()
    for item, role in zip(value, _ROLE_ORDER, strict=True):
        if type(item) is not PhysicalArvanCredentialRestrictionObservation or item.role != role:
            _fail("ARVAN_IMMUTABILITY_CREDENTIAL_RESTRICTIONS_INVALID")
        expected_allowed = _EXPECTED_ALLOWED_OPERATIONS[role]
        if not isinstance(item.allowed_operations, tuple) or item.allowed_operations != expected_allowed:
            _fail("ARVAN_IMMUTABILITY_CREDENTIAL_RESTRICTION_INVALID")
        denied = _normalise_denied_operations(item.denied_operations, role=role)
        if role == "witness-controller":
            if (
                item.credential_posture != "no-object-storage-credential-issued"
                or item.credential_identity_sha256 is not None
            ):
                _fail("ARVAN_IMMUTABILITY_WITNESS_CREDENTIAL_INVALID")
            identity = None
        else:
            if item.credential_posture != "scoped-credential-probed":
                _fail("ARVAN_IMMUTABILITY_CREDENTIAL_RESTRICTION_INVALID")
            identity = _sha256(
                item.credential_identity_sha256,
                code="ARVAN_IMMUTABILITY_CREDENTIAL_RESTRICTION_INVALID",
            )
            if identity in identity_hashes:
                _fail("ARVAN_IMMUTABILITY_CREDENTIALS_NOT_SEPARATE")
            identity_hashes.add(identity)
        result.append(
            PhysicalArvanCredentialRestrictionObservation(
                role=role,
                credential_posture=item.credential_posture,
                credential_identity_sha256=identity,
                allowed_operations=expected_allowed,
                denied_operations=denied,
            )
        )
    return tuple(result)


def _normalise_disposable_probe(
    value: object,
    *,
    facts: _BindingFacts,
) -> PhysicalArvanDisposableImmutabilityProbe:
    if type(value) is not PhysicalArvanDisposableImmutabilityProbe:
        _fail("ARVAN_IMMUTABILITY_DISPOSABLE_PROBE_INVALID")
    if type(value.object_key) is not str or OBJECT_KEY_RE.fullmatch(value.object_key) is None:
        _fail("ARVAN_IMMUTABILITY_DISPOSABLE_PROBE_INVALID")
    if not value.object_key.startswith(facts.disposable_object_prefix):
        _fail("ARVAN_IMMUTABILITY_DISPOSABLE_OBJECT_SCOPE_INVALID")
    suffix = value.object_key.removeprefix(facts.disposable_object_prefix)
    if "/" in suffix or _DISPOSABLE_OBJECT_SUFFIX_RE.fullmatch(suffix) is None:
        _fail("ARVAN_IMMUTABILITY_DISPOSABLE_OBJECT_SCOPE_INVALID")
    if type(value.version_id) is not str or VERSION_ID_RE.fullmatch(value.version_id) is None:
        _fail("ARVAN_IMMUTABILITY_DISPOSABLE_PROBE_INVALID")
    ciphertext_sha256 = _sha256(
        value.ciphertext_sha256, code="ARVAN_IMMUTABILITY_DISPOSABLE_PROBE_INVALID"
    )
    if (
        type(value.ciphertext_bytes) is not int
        or not 1 <= value.ciphertext_bytes <= MAX_PHYSICAL_ARVAN_DISPOSABLE_CIPHERTEXT_BYTES
    ):
        _fail("ARVAN_IMMUTABILITY_DISPOSABLE_PROBE_INVALID")
    if (
        value.delete_version_outcome != ARVAN_DISPOSABLE_DELETE_DENIED
        or value.delete_marker_outcome != ARVAN_DISPOSABLE_DELETE_DENIED
        or value.exact_version_get_outcome != ARVAN_DISPOSABLE_EXACT_GET_SUCCEEDED
        or value.retrieved_version_id != value.version_id
        or value.retrieved_ciphertext_sha256 != ciphertext_sha256
        or value.retrieved_ciphertext_bytes != value.ciphertext_bytes
    ):
        _fail("ARVAN_IMMUTABILITY_DELETE_SURVIVAL_UNPROVEN")
    return PhysicalArvanDisposableImmutabilityProbe(
        object_key=value.object_key,
        version_id=value.version_id,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=value.ciphertext_bytes,
        delete_version_outcome=ARVAN_DISPOSABLE_DELETE_DENIED,
        delete_marker_outcome=ARVAN_DISPOSABLE_DELETE_DENIED,
        exact_version_get_outcome=ARVAN_DISPOSABLE_EXACT_GET_SUCCEEDED,
        retrieved_version_id=value.version_id,
        retrieved_ciphertext_sha256=ciphertext_sha256,
        retrieved_ciphertext_bytes=value.ciphertext_bytes,
    )


def _observation_payload(
    value: PhysicalArvanImmutabilityPreflightObservation,
) -> dict[str, Any]:
    return {
        "schema": value.schema,
        "status": value.status,
        "provider": value.provider,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "route_binding_sha256": value.route_binding_sha256,
        "endpoint": value.endpoint,
        "region": value.region,
        "bucket": value.bucket,
        "versioning_status": value.versioning_status,
        "acl_posture": value.acl_posture,
        "retention_mode": value.retention_mode,
        "retention_policy_evidence_sha256": value.retention_policy_evidence_sha256,
        "retention_days": value.retention_days,
        "credential_restrictions": [
            {
                "role": item.role,
                "credential_posture": item.credential_posture,
                "credential_identity_sha256": item.credential_identity_sha256,
                "allowed_operations": list(item.allowed_operations),
                "denied_operations": [
                    {"operation": denial.operation, "outcome": denial.outcome}
                    for denial in item.denied_operations
                ],
            }
            for item in value.credential_restrictions
        ],
        "disposable_probe": {
            "object_key": value.disposable_probe.object_key,
            "version_id": value.disposable_probe.version_id,
            "ciphertext_sha256": value.disposable_probe.ciphertext_sha256,
            "ciphertext_bytes": value.disposable_probe.ciphertext_bytes,
            "delete_version_outcome": value.disposable_probe.delete_version_outcome,
            "delete_marker_outcome": value.disposable_probe.delete_marker_outcome,
            "exact_version_get_outcome": value.disposable_probe.exact_version_get_outcome,
            "retrieved_version_id": value.disposable_probe.retrieved_version_id,
            "retrieved_ciphertext_sha256": value.disposable_probe.retrieved_ciphertext_sha256,
            "retrieved_ciphertext_bytes": value.disposable_probe.retrieved_ciphertext_bytes,
        },
        "observed_at": value.observed_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def canonical_physical_arvan_immutability_preflight_evidence_bytes(
    value: PhysicalArvanImmutabilityPreflightObservation,
) -> bytes:
    """Return the canonical hashable evidence payload without the hash field."""

    if type(value) is not PhysicalArvanImmutabilityPreflightObservation:
        _fail("ARVAN_IMMUTABILITY_OBSERVATION_INVALID")
    return _canonical_json_bytes(_observation_payload(value))


def build_physical_arvan_immutability_preflight_observation(
    *,
    binding: PhysicalArvanImmutabilityPreflightBinding,
    versioning_status: str,
    acl_posture: str,
    retention_mode: str,
    retention_policy_evidence_sha256: str,
    retention_days: int,
    credential_restrictions: tuple[PhysicalArvanCredentialRestrictionObservation, ...],
    disposable_probe: PhysicalArvanDisposableImmutabilityProbe,
    observed_at: datetime,
) -> PhysicalArvanImmutabilityPreflightObservation:
    """Create a hash-bound raw observation; verification remains mandatory."""

    facts = _normalise_binding(binding)
    observed = _utc(observed_at, code="ARVAN_IMMUTABILITY_OBSERVATION_CLOCK_INVALID")
    provisional = PhysicalArvanImmutabilityPreflightObservation(
        schema=PHYSICAL_ARVAN_IMMUTABILITY_PREFLIGHT_SCHEMA,
        status="observed",
        provider=ARVAN_PROVIDER,
        campaign_id=facts.binding.campaign_id,
        release_sha=facts.binding.release_sha,
        source_site=facts.binding.source_site,
        destination_site=facts.binding.destination_site,
        route_binding_sha256=facts.binding.route_binding_sha256,
        endpoint=facts.binding.endpoint,
        region=facts.binding.region,
        bucket=facts.binding.bucket,
        versioning_status=versioning_status,
        acl_posture=acl_posture,
        retention_mode=retention_mode,
        retention_policy_evidence_sha256=retention_policy_evidence_sha256,
        retention_days=retention_days,
        credential_restrictions=credential_restrictions,
        disposable_probe=disposable_probe,
        observed_at=observed,
        evidence_sha256="",
    )
    # The evidence hash deliberately excludes itself, so calculate it before
    # running the full verifier.  The verifier below still rejects every
    # malformed caller field and the hash still does not create capability.
    evidence_sha256 = hashlib.sha256(
        canonical_physical_arvan_immutability_preflight_evidence_bytes(provisional)
    ).hexdigest()
    result = replace(provisional, evidence_sha256=evidence_sha256)
    _normalise_observation(result, facts=facts, now=observed, maximum_age_seconds=1)
    return result


def _normalise_observation(
    value: object,
    *,
    facts: _BindingFacts,
    now: datetime,
    maximum_age_seconds: int,
) -> PhysicalArvanImmutabilityPreflightObservation:
    if type(value) is not PhysicalArvanImmutabilityPreflightObservation:
        _fail("ARVAN_IMMUTABILITY_OBSERVATION_INVALID")
    expected = facts.binding
    if (
        value.schema != PHYSICAL_ARVAN_IMMUTABILITY_PREFLIGHT_SCHEMA
        or value.status != "observed"
        or value.provider != ARVAN_PROVIDER
        or value.campaign_id != expected.campaign_id
        or value.release_sha != expected.release_sha
        or value.source_site != expected.source_site
        or value.destination_site != expected.destination_site
        or value.route_binding_sha256 != expected.route_binding_sha256
        or value.endpoint != expected.endpoint
        or value.region != expected.region
        or value.bucket != expected.bucket
        or value.versioning_status != ARVAN_VERSIONING_STATUS
        or value.acl_posture != ARVAN_ACL_POSTURE
    ):
        _fail("ARVAN_IMMUTABILITY_OBSERVATION_BINDING_MISMATCH")
    if value.retention_mode not in ARVAN_RETENTION_MODES:
        _fail("ARVAN_IMMUTABILITY_RETENTION_MODE_INVALID")
    retention_evidence = _sha256(
        value.retention_policy_evidence_sha256,
        code="ARVAN_IMMUTABILITY_RETENTION_EVIDENCE_INVALID",
    )
    if (
        type(value.retention_days) is not int
        or value.retention_days < expected.minimum_retention_days
        or value.retention_days > MAX_PHYSICAL_ARVAN_RETENTION_DAYS
    ):
        _fail("ARVAN_IMMUTABILITY_RETENTION_DAYS_INVALID")
    restrictions = _normalise_credential_restrictions(value.credential_restrictions)
    disposable = _normalise_disposable_probe(value.disposable_probe, facts=facts)
    observed = _utc(value.observed_at, code="ARVAN_IMMUTABILITY_OBSERVATION_CLOCK_INVALID")
    if observed > now + timedelta(seconds=MAX_PHYSICAL_ARVAN_IMMUTABILITY_FUTURE_SKEW_SECONDS):
        _fail("ARVAN_IMMUTABILITY_OBSERVATION_FROM_FUTURE")
    if observed < now - timedelta(seconds=maximum_age_seconds):
        _fail("ARVAN_IMMUTABILITY_OBSERVATION_STALE")
    normalised = PhysicalArvanImmutabilityPreflightObservation(
        schema=PHYSICAL_ARVAN_IMMUTABILITY_PREFLIGHT_SCHEMA,
        status="observed",
        provider=ARVAN_PROVIDER,
        campaign_id=expected.campaign_id,
        release_sha=expected.release_sha,
        source_site=expected.source_site,
        destination_site=expected.destination_site,
        route_binding_sha256=expected.route_binding_sha256,
        endpoint=expected.endpoint,
        region=expected.region,
        bucket=expected.bucket,
        versioning_status=ARVAN_VERSIONING_STATUS,
        acl_posture=ARVAN_ACL_POSTURE,
        retention_mode=value.retention_mode,
        retention_policy_evidence_sha256=retention_evidence,
        retention_days=value.retention_days,
        credential_restrictions=restrictions,
        disposable_probe=disposable,
        observed_at=observed,
        evidence_sha256=_sha256(
            value.evidence_sha256, code="ARVAN_IMMUTABILITY_EVIDENCE_HASH_INVALID"
        ),
    )
    expected_hash = hashlib.sha256(
        canonical_physical_arvan_immutability_preflight_evidence_bytes(normalised)
    ).hexdigest()
    if normalised.evidence_sha256 != expected_hash:
        _fail("ARVAN_IMMUTABILITY_EVIDENCE_HASH_MISMATCH")
    return normalised


def verify_physical_arvan_immutability_preflight(
    observation: PhysicalArvanImmutabilityPreflightObservation,
    *,
    binding: PhysicalArvanImmutabilityPreflightBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_ARVAN_IMMUTABILITY_MAX_EVIDENCE_AGE_SECONDS
    ),
) -> VerifiedPhysicalArvanImmutabilityPreflight:
    """Verify one bounded disposable-bucket immutability observation."""

    facts = _normalise_binding(binding)
    observed_now = _utc(now, code="ARVAN_IMMUTABILITY_CLOCK_INVALID")
    maximum = _maximum_age(
        maximum_evidence_age_seconds, code="ARVAN_IMMUTABILITY_MAX_AGE_INVALID"
    )
    normalized = _normalise_observation(
        observation,
        facts=facts,
        now=observed_now,
        maximum_age_seconds=maximum,
    )
    result = VerifiedPhysicalArvanImmutabilityPreflight(
        observation=normalized,
        binding=facts.binding,
        maximum_evidence_age_seconds=maximum,
    )
    object.__setattr__(result, "_capability", _VERIFIED_CAPABILITY)
    return result


def require_verified_physical_arvan_immutability_preflight(
    value: object,
    *,
    binding: PhysicalArvanImmutabilityPreflightBinding,
    now: datetime,
    maximum_evidence_age_seconds: int | None = None,
) -> VerifiedPhysicalArvanImmutabilityPreflight:
    """Revalidate an opaque result against a current campaign binding."""

    if (
        type(value) is not VerifiedPhysicalArvanImmutabilityPreflight
        or value._capability is not _VERIFIED_CAPABILITY
    ):
        _fail("ARVAN_IMMUTABILITY_PRECHECK_NOT_VERIFIED")
    stored_maximum = _maximum_age(
        value.maximum_evidence_age_seconds, code="ARVAN_IMMUTABILITY_MAX_AGE_INVALID"
    )
    requested_maximum = (
        stored_maximum
        if maximum_evidence_age_seconds is None
        else _maximum_age(
            maximum_evidence_age_seconds, code="ARVAN_IMMUTABILITY_MAX_AGE_INVALID"
        )
    )
    # A downstream oracle may tighten freshness but never relax the bound
    # accepted by the root-owned collector.
    effective_maximum = min(stored_maximum, requested_maximum)
    fresh = verify_physical_arvan_immutability_preflight(
        value.observation,
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=effective_maximum,
    )
    if (
        fresh.observation != value.observation
        or fresh.binding != value.binding
    ):
        _fail("ARVAN_IMMUTABILITY_PRECHECK_TAMPERED")
    return value


def _normalise_config(
    value: object,
) -> tuple[PhysicalArvanImmutabilityPreflightBinding, int]:
    if type(value) is not PhysicalArvanImmutabilityPreflightConfig:
        _fail("ARVAN_IMMUTABILITY_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("ARVAN_IMMUTABILITY_PREFLIGHT_DISABLED")
    facts = _normalise_binding(value.binding)
    maximum = _maximum_age(
        value.maximum_evidence_age_seconds, code="ARVAN_IMMUTABILITY_MAX_AGE_INVALID"
    )
    return facts.binding, maximum


def collect_physical_arvan_immutability_preflight(
    *,
    config: PhysicalArvanImmutabilityPreflightConfig,
    probe: PhysicalArvanImmutabilityPreflightProbe,
    now: datetime,
) -> VerifiedPhysicalArvanImmutabilityPreflight:
    """Collect through an injected live probe only after default-off checks.

    The caller controls no credential or endpoint argument here.  The future
    probe is responsible for the actual disposable-bucket operations; this
    function simply makes their evidence fail closed and opaque.  It does not
    persist the result, so a future runtime must independently retain and
    recheck it immediately before a real campaign.
    """

    if os.geteuid() != 0:
        _fail("ARVAN_IMMUTABILITY_PREFLIGHT_REQUIRES_ROOT")
    binding, maximum = _normalise_config(config)
    observed_now = _utc(now, code="ARVAN_IMMUTABILITY_CLOCK_INVALID")
    collector = getattr(probe, "collect", None)
    if not callable(collector):
        _fail("ARVAN_IMMUTABILITY_PROBE_INVALID")
    try:
        observation = collector(binding=binding, observed_at=observed_now)
    except PhysicalArvanImmutabilityPreflightError:
        raise
    except Exception as exc:
        raise PhysicalArvanImmutabilityPreflightError(
            "ARVAN_IMMUTABILITY_PROBE_FAILED"
        ) from exc
    return verify_physical_arvan_immutability_preflight(
        observation,
        binding=binding,
        now=observed_now,
        maximum_evidence_age_seconds=maximum,
    )


def project_verified_physical_arvan_immutability_preflight(
    value: object,
    *,
    binding: PhysicalArvanImmutabilityPreflightBinding,
    now: datetime,
    maximum_evidence_age_seconds: int | None = None,
) -> PhysicalArvanImmutabilityPreflightProjection:
    """Return only non-authorizing facts for the Full-Matrix readiness oracle."""

    verified = require_verified_physical_arvan_immutability_preflight(
        value,
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )
    observation = verified.observation
    return PhysicalArvanImmutabilityPreflightProjection(
        schema=observation.schema,
        campaign_id=observation.campaign_id,
        release_sha=observation.release_sha,
        source_site=observation.source_site,
        destination_site=observation.destination_site,
        route_binding_sha256=observation.route_binding_sha256,
        bucket=observation.bucket,
        retention_mode=observation.retention_mode,
        retention_days=observation.retention_days,
        observed_at=observation.observed_at,
        evidence_sha256=observation.evidence_sha256,
    )
