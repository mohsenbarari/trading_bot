"""V2-only, role-local Arvan S3v4 scopes for the Witness mailbox adapters.

This is a concrete provider bridge, but it intentionally performs no remote
I/O while this module is imported or while a scope is opened.  A root-owned
profile fixes one HTTPS endpoint, bucket, region, path-style S3v4 signing,
role, mailbox, and all typed admission/retention pins.  The SDK is loaded and
the client is created only inside a named callback after the V2 adapter has
opened its root-owned fixed-role credential file.

There is no exported client, broad transport method, paired-role factory, or
source-to-standby bypass.  Each of the eight public scope classes has one
named callback only.  A raw handle is deactivated in ``finally`` as soon as
that callback returns, so retaining it cannot provide a live client escape.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol, TypeVar
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as _mailbox


__all__ = (
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_SCHEMA",
    "PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressScope",
    "PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherScope",
    "PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressScope",
    "PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherScope",
    "PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig",
    "PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig",
    "PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError",
    "PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressScope",
    "PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherScope",
    "PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressScope",
    "PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressScope",
    "VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_ack_ingress_scope",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_scope",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_standby_ingress_scope",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_to_witness_publisher_scope",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_scope",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_fi_publisher_scope",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_ir_publisher_scope",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_ir_ingress_scope",
    "require_verified_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation",
    "verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-arvan-s3v4-scope-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_DEFAULT_ENABLED = False

_PROFILE_DIRECTORY = "physical-wal-v2-witness-roundtrip-arvan-s3v4-profiles-v1"
_PROFILE_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-arvan-s3v4-profile-v1"
_PROFILE_VERSION = 1
_ROUTE_IAM_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-arvan-s3v4-provider-route-iam-attestation-v1"
_ROUTE_IAM_VERSION = 1
_ROUTE_IAM_DOMAIN = b"gold-trade-physical-wal-v2-witness-roundtrip-arvan-s3v4-provider-route-iam-attestation-v1\x00"
_OBJECT_LOCK_MODE = "COMPLIANCE"
_ADDRESSING_STYLE = "path"
_MAX_PROFILE_BYTES = 32 * 1024
_MAX_SDK_LIST_ENTRIES = 1000
_ZERO_SHA256 = "0" * 64
_CAPABILITY = object()

_FI_TO_WITNESS = "fi-to-witness"
_WITNESS_TO_IR = "witness-to-ir"
_IR_TO_WITNESS = "ir-to-witness"
_WITNESS_TO_FI = "witness-to-fi"
_PUBLISH = "publish"
_CONSUME = "consume"

_SHA_RE = re.compile(SHA256_RE.pattern, re.ASCII)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$", re.ASCII)
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,255}$", re.ASCII)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_ATTESTATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_PROFILE_FIELDS = frozenset(
    {
        "schema",
        "version",
        "host_id",
        "local_role",
        "mailbox",
        "direction",
        "object_prefix",
        "endpoint_url",
        "bucket",
        "region_name",
        "addressing_style",
        "admission_sha256",
        "delivery_binding_sha256",
        "retention_proof_sha256",
        "provider_route_iam_attestation_sha256",
    }
)
_ROUTE_IAM_FIELDS = frozenset(
    {
        "schema",
        "version",
        "host_id",
        "local_role",
        "mailbox",
        "direction",
        "object_prefix",
        "endpoint_url",
        "bucket",
        "region_name",
        "addressing_style",
        "allowed_s3_actions",
        "admission_sha256",
        "deployment_binding_sha256",
        "delivery_binding_sha256",
        "retention_proof_sha256",
        "attestation_id",
        "attestation_nonce",
        "issued_at",
        "expires_at",
        "signature_base64",
    }
)


class PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(RuntimeError):
    """The local S3v4 scope is insufficiently pinned or a raw response is unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig:
    """Default-off, root-bound policy for one role-local concrete S3v4 scope."""

    mailbox_adapter_config: _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig | None = None
    provider_route_iam_attestation_config: "PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig | None" = field(
        default=None,
        repr=False,
    )
    provider_route_iam_attestation: "VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation | None" = field(
        default=None,
        repr=False,
    )
    root: Path | None = None
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig:
    """Public authority pin for a signed route, bucket, and least-privilege claim."""

    mailbox_adapter_config: _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig | None = field(
        default=None,
        repr=False,
    )
    provider_route_iam_authority_public_key: bytes | None = field(default=None, repr=False)
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = 300


@dataclass(frozen=True, init=False)
class VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation:
    """Non-forgeable fresh claim for one exact provider route and IAM action set."""

    host_id: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    endpoint_url: str
    bucket: str
    region_name: str
    allowed_s3_actions: tuple[str, ...]
    admission_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    retention_proof_sha256: str
    attestation_id: str
    attestation_nonce: str
    issued_at: datetime
    expires_at: datetime
    attestation_sha256: str
    canonical_attestation: bytes = field(repr=False)
    _configuration_sha256: str = field(repr=False)
    _capability: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        host_id: str,
        local_role: str,
        mailbox: str,
        direction: str,
        object_prefix: str,
        endpoint_url: str,
        bucket: str,
        region_name: str,
        allowed_s3_actions: tuple[str, ...],
        admission_sha256: str,
        deployment_binding_sha256: str,
        delivery_binding_sha256: str,
        retention_proof_sha256: str,
        attestation_id: str,
        attestation_nonce: str,
        issued_at: datetime,
        expires_at: datetime,
        attestation_sha256: str,
        canonical_attestation: bytes,
        configuration_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("host_id", host_id),
            ("local_role", local_role),
            ("mailbox", mailbox),
            ("direction", direction),
            ("object_prefix", object_prefix),
            ("endpoint_url", endpoint_url),
            ("bucket", bucket),
            ("region_name", region_name),
            ("allowed_s3_actions", allowed_s3_actions),
            ("admission_sha256", admission_sha256),
            ("deployment_binding_sha256", deployment_binding_sha256),
            ("delivery_binding_sha256", delivery_binding_sha256),
            ("retention_proof_sha256", retention_proof_sha256),
            ("attestation_id", attestation_id),
            ("attestation_nonce", attestation_nonce),
            ("issued_at", issued_at),
            ("expires_at", expires_at),
            ("attestation_sha256", attestation_sha256),
            ("canonical_attestation", canonical_attestation),
            ("_configuration_sha256", configuration_sha256),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Profile:
    host_id: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    endpoint_url: str
    bucket: str
    region_name: str
    admission_sha256: str
    delivery_binding_sha256: str
    retention_proof_sha256: str
    provider_route_iam_attestation_sha256: str


@dataclass(frozen=True)
class _Config:
    mailbox_config: _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig
    mailbox_facts: _mailbox._Config
    root: Path
    profile: _Profile
    provider_route_iam_attestation_config: PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig
    provider_route_iam_attestation: VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation


def _host_now() -> datetime:
    try:
        return datetime.now(timezone.utc).replace(microsecond=0)
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CLOCK_INVALID"
        ) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(code) from exc


def _signature(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(code) from exc
    if len(result) != 64:
        _fail(code)
    return result


def _route_actions(*, direction: str) -> tuple[str, ...]:
    if direction == _PUBLISH:
        return (
            "s3:PutObject:IfNoneMatch:ObjectLockCompliance",
            "s3:HeadObject:ExactVersion",
            "s3:GetObject:ExactVersion",
        )
    if direction == _CONSUME:
        return (
            "s3:ListObjectVersions:FixedPrefix",
            "s3:HeadObject:ExactVersion",
            "s3:GetObject:ExactVersion",
        )
    _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_ROLE_INVALID")


def _route_context(
    value: object,
    *,
    local_role: str,
    direction: str,
    now: datetime,
) -> tuple[
    _mailbox._Config,
    Ed25519PublicKey,
    str,
]:
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig
        or value.enabled is not True
        or type(value.mailbox_adapter_config)
        is not _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig
        or type(value.provider_route_iam_authority_public_key) is not bytes
        or len(value.provider_route_iam_authority_public_key) != 32
        or type(value.maximum_evidence_age_seconds) is not int
        or not 1 <= value.maximum_evidence_age_seconds <= 86_400
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CONFIG_INVALID")
    try:
        facts = _mailbox._config(
            value.mailbox_adapter_config,
            local_role=local_role,
            direction=direction,
            now=now,
        )
        authority = Ed25519PublicKey.from_public_bytes(value.provider_route_iam_authority_public_key)
    except (ValueError, _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CONFIG_INVALID"
        ) from exc
    configuration = {
        "authority_public_key_base64": base64.b64encode(
            value.provider_route_iam_authority_public_key
        ).decode("ascii"),
        "maximum_evidence_age_seconds": value.maximum_evidence_age_seconds,
        "host_id": facts.mailbox_admission.host_id,
        "local_role": facts.policy.local_role,
        "mailbox": facts.policy.mailbox,
        "direction": facts.policy.direction,
        "object_prefix": facts.policy.object_prefix,
        "allowed_s3_actions": list(_route_actions(direction=facts.policy.direction)),
        "admission_sha256": facts.mailbox_admission.admission_sha256,
        "deployment_binding_sha256": facts.mailbox_admission.deployment_binding_sha256,
        "delivery_binding_sha256": facts.delivery_binding_sha256,
        "retention_proof_sha256": facts.retention_proof.proof_sha256,
    }
    return facts, authority, _sha256_bytes(
        _canonical(
            configuration,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CONFIG_INVALID",
        )
    )


def _parse_route_attestation(value: object) -> tuple[dict[str, Any], bytes]:
    if type(value) is not bytes or not 1 <= len(value) <= 128 * 1024:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID")
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID"
        ) from exc
    if (
        type(parsed) is not dict
        or set(parsed) != _ROUTE_IAM_FIELDS
        or _canonical(parsed, code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID")
        != value
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID")
    return dict(parsed), value


def _route_unsigned(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result.pop("signature_base64", None)
    return result


def verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation(
    attestation: bytes,
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig,
    local_role: str,
    direction: str,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation:
    """Verify one fresh signed provider route/IAM claim for one named local role."""

    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CLOCK_INVALID")
    facts, authority, configuration_sha256 = _route_context(
        config,
        local_role=local_role,
        direction=direction,
        now=observed,
    )
    item, raw = _parse_route_attestation(attestation)
    signature = _signature(
        item["signature_base64"],
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID",
    )
    try:
        authority.verify(
            signature,
            _ROUTE_IAM_DOMAIN
            + _canonical(
                _route_unsigned(item),
                code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID",
            ),
        )
    except InvalidSignature as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_SIGNATURE_INVALID"
        ) from exc
    required_actions = _route_actions(direction=facts.policy.direction)
    if (
        item["schema"] != _ROUTE_IAM_SCHEMA
        or item["version"] != _ROUTE_IAM_VERSION
        or type(item["host_id"]) is not str
        or item["host_id"] != facts.mailbox_admission.host_id
        or item["local_role"] != facts.policy.local_role
        or item["mailbox"] != facts.policy.mailbox
        or item["direction"] != facts.policy.direction
        or item["object_prefix"] != facts.policy.object_prefix
        or _endpoint(item["endpoint_url"]) != item["endpoint_url"]
        or type(item["bucket"]) is not str
        or _BUCKET_RE.fullmatch(item["bucket"]) is None
        or type(item["region_name"]) is not str
        or _REGION_RE.fullmatch(item["region_name"]) is None
        or item["addressing_style"] != _ADDRESSING_STYLE
        or type(item["allowed_s3_actions"]) is not list
        or tuple(item["allowed_s3_actions"]) != required_actions
        or any(type(action) is not str for action in item["allowed_s3_actions"])
        or _sha256(
            item["admission_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID",
        )
        != facts.mailbox_admission.admission_sha256
        or _sha256(
            item["deployment_binding_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID",
        )
        != facts.mailbox_admission.deployment_binding_sha256
        or _sha256(
            item["delivery_binding_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID",
        )
        != facts.delivery_binding_sha256
        or _sha256(
            item["retention_proof_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_INVALID",
        )
        != facts.retention_proof.proof_sha256
        or type(item["attestation_id"]) is not str
        or _ATTESTATION_ID_RE.fullmatch(item["attestation_id"]) is None
        or type(item["attestation_nonce"]) is not str
        or _NONCE_RE.fullmatch(item["attestation_nonce"]) is None
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CROSS_PIN_MISMATCH")
    issued_at = _parse_timestamp(
        item["issued_at"],
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_TIME_INVALID",
    )
    expires_at = _parse_timestamp(
        item["expires_at"],
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_TIME_INVALID",
    )
    if (
        issued_at > observed
        or expires_at <= observed
        or expires_at <= issued_at
        or (observed - issued_at).total_seconds() > config.maximum_evidence_age_seconds
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_STALE")
    return VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation(
        host_id=facts.mailbox_admission.host_id,
        local_role=facts.policy.local_role,
        mailbox=facts.policy.mailbox,
        direction=facts.policy.direction,
        object_prefix=facts.policy.object_prefix,
        endpoint_url=item["endpoint_url"],
        bucket=item["bucket"],
        region_name=item["region_name"],
        allowed_s3_actions=required_actions,
        admission_sha256=facts.mailbox_admission.admission_sha256,
        deployment_binding_sha256=facts.mailbox_admission.deployment_binding_sha256,
        delivery_binding_sha256=facts.delivery_binding_sha256,
        retention_proof_sha256=facts.retention_proof.proof_sha256,
        attestation_id=item["attestation_id"],
        attestation_nonce=item["attestation_nonce"],
        issued_at=issued_at,
        expires_at=expires_at,
        attestation_sha256=_sha256_bytes(raw),
        canonical_attestation=raw,
        configuration_sha256=configuration_sha256,
        capability=_CAPABILITY,
    )


def require_verified_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation(
    attestation: object,
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig,
    local_role: str,
    direction: str,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation:
    """Require the typed route/IAM claim again immediately before client creation."""

    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CLOCK_INVALID")
    facts, _authority, configuration_sha256 = _route_context(
        config,
        local_role=local_role,
        direction=direction,
        now=observed,
    )
    required_actions = _route_actions(direction=facts.policy.direction)
    if (
        type(attestation)
        is not VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation
        or attestation._capability is not _CAPABILITY
        or attestation._configuration_sha256 != configuration_sha256
        or attestation.host_id != facts.mailbox_admission.host_id
        or attestation.local_role != facts.policy.local_role
        or attestation.mailbox != facts.policy.mailbox
        or attestation.direction != facts.policy.direction
        or attestation.object_prefix != facts.policy.object_prefix
        or _endpoint(attestation.endpoint_url) != attestation.endpoint_url
        or _BUCKET_RE.fullmatch(attestation.bucket) is None
        or _REGION_RE.fullmatch(attestation.region_name) is None
        or attestation.allowed_s3_actions != required_actions
        or attestation.admission_sha256 != facts.mailbox_admission.admission_sha256
        or attestation.deployment_binding_sha256
        != facts.mailbox_admission.deployment_binding_sha256
        or attestation.delivery_binding_sha256 != facts.delivery_binding_sha256
        or attestation.retention_proof_sha256 != facts.retention_proof.proof_sha256
        or _sha256(
            attestation.attestation_sha256,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CAPABILITY_INVALID",
        )
        != attestation.attestation_sha256
        or type(attestation.canonical_attestation) is not bytes
        or _sha256_bytes(attestation.canonical_attestation) != attestation.attestation_sha256
        or attestation.issued_at > observed
        or attestation.expires_at <= observed
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CAPABILITY_INVALID")
    try:
        reverified = verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation(
            attestation.canonical_attestation,
            config=config,
            local_role=local_role,
            direction=direction,
            now=observed,
        )
    except PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CAPABILITY_INVALID"
        ) from exc
    if (
        reverified.attestation_sha256 != attestation.attestation_sha256
        or reverified.canonical_attestation != attestation.canonical_attestation
        or reverified.endpoint_url != attestation.endpoint_url
        or reverified.bucket != attestation.bucket
        or reverified.region_name != attestation.region_name
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_ROUTE_IAM_ATTESTATION_CAPABILITY_INVALID")
    return reverified


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID")


def _parse_profile(value: object) -> dict[str, Any]:
    if type(value) is not bytes or not 1 <= len(value) <= _MAX_PROFILE_BYTES:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID")
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID"
        ) from exc
    if type(parsed) is not dict or set(parsed) != _PROFILE_FIELDS or _canonical(parsed, code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID") != value:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID")
    return dict(parsed)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if result.microsecond != 0:
        _fail(code)
    return result


def _safe_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(
        part in {"", ".", ".."} for part in value.parts[1:]
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_ROOT_UNSAFE")
    return value


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_ROOT_REQUIRED")
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_ROOT_REQUIRED"
        ) from exc


def _directory_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PLATFORM_UNSAFE")
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | os.O_DIRECTORY
        | (os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0)
    )


def _check_directory(descriptor: int, *, final: bool, code: str) -> None:
    try:
        info = os.fstat(descriptor)
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(code) from exc
    mode = stat.S_IMODE(info.st_mode)
    sticky_parent = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or (final and mode != 0o700)
        or (not final and mode & 0o022 and not sticky_parent)
    ):
        _fail(code)


def _open_secure_root(root: Path) -> int:
    descriptor = -1
    try:
        descriptor = os.open("/", _directory_flags())
        parts = root.parts[1:]
        if not parts:
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_ROOT_UNSAFE")
        for index, part in enumerate(parts):
            next_descriptor = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            _check_directory(
                descriptor,
                final=index == len(parts) - 1,
                code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_ROOT_UNSAFE",
            )
        return descriptor
    except PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_ROOT_UNSAFE"
        ) from exc


def _open_profile_directory(root_fd: int) -> int:
    descriptor = -1
    try:
        descriptor = os.open(_PROFILE_DIRECTORY, _directory_flags(), dir_fd=root_fd)
        _check_directory(
            descriptor,
            final=True,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_DIRECTORY_UNSAFE",
        )
        return descriptor
    except PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_DIRECTORY_UNSAFE"
        ) from exc


def _read_profile_file(directory_fd: int, *, local_role: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            local_role + ".json",
            os.O_RDONLY | os.O_NOFOLLOW | (os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0),
            dir_fd=directory_fd,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 1 <= info.st_size <= _MAX_PROFILE_BYTES
        ):
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_FILE_UNSAFE")
        chunks = bytearray()
        while len(chunks) < info.st_size:
            chunk = os.read(descriptor, info.st_size - len(chunks))
            if not chunk:
                _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_FILE_UNSAFE")
            chunks.extend(chunk)
        if os.read(descriptor, 1):
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_FILE_UNSAFE")
        return bytes(chunks)
    except PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_FILE_UNSAFE"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _endpoint(value: object) -> str:
    if type(value) is not str or not value.isascii() or not 12 <= len(value) <= 255:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_ENDPOINT_INVALID")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_ENDPOINT_INVALID"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or not parsed.hostname.isascii()
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (port is not None and not 1 <= port <= 65535)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_ENDPOINT_INVALID")
    canonical = "https://" + parsed.netloc
    if value != canonical:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_ENDPOINT_INVALID")
    return canonical


def _profile(
    raw: bytes,
    *,
    facts: _mailbox._Config,
    route_iam: VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation,
) -> _Profile:
    item = _parse_profile(raw)
    if any(
        type(item[name]) is not str
        for name in (
            "host_id",
            "local_role",
            "mailbox",
            "direction",
            "object_prefix",
            "bucket",
            "region_name",
            "addressing_style",
        )
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID")
    profile = _Profile(
        host_id=item["host_id"],
        local_role=item["local_role"],
        mailbox=item["mailbox"],
        direction=item["direction"],
        object_prefix=item["object_prefix"],
        endpoint_url=_endpoint(item["endpoint_url"]),
        bucket=item["bucket"],
        region_name=item["region_name"],
        admission_sha256=_sha256(
            item["admission_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID",
        ),
        delivery_binding_sha256=_sha256(
            item["delivery_binding_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID",
        ),
        retention_proof_sha256=_sha256(
            item["retention_proof_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID",
        ),
        provider_route_iam_attestation_sha256=_sha256(
            item["provider_route_iam_attestation_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_INVALID",
        ),
    )
    if (
        item["schema"] != _PROFILE_SCHEMA
        or item["version"] != _PROFILE_VERSION
        or not isinstance(profile.host_id, str)
        or profile.host_id != facts.mailbox_admission.host_id
        or profile.local_role != facts.policy.local_role
        or profile.mailbox != facts.policy.mailbox
        or profile.direction != facts.policy.direction
        or profile.object_prefix != facts.policy.object_prefix
        or _BUCKET_RE.fullmatch(profile.bucket) is None
        or _REGION_RE.fullmatch(profile.region_name) is None
        or item["addressing_style"] != _ADDRESSING_STYLE
        or profile.admission_sha256 != facts.mailbox_admission.admission_sha256
        or profile.delivery_binding_sha256 != facts.delivery_binding_sha256
        or profile.retention_proof_sha256 != facts.retention_proof.proof_sha256
        or profile.provider_route_iam_attestation_sha256 != route_iam.attestation_sha256
        or profile.endpoint_url != route_iam.endpoint_url
        or profile.bucket != route_iam.bucket
        or profile.region_name != route_iam.region_name
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_CROSS_PIN_MISMATCH")
    return profile


def _load_profile(
    root: Path,
    *,
    facts: _mailbox._Config,
    route_iam: VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation,
) -> _Profile:
    _require_root()
    root_fd = -1
    directory_fd = -1
    try:
        root_fd = _open_secure_root(root)
        directory_fd = _open_profile_directory(root_fd)
        raw = _read_profile_file(directory_fd, local_role=facts.policy.local_role)
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)
        if root_fd >= 0:
            os.close(root_fd)
    return _profile(raw, facts=facts, route_iam=route_iam)


def _config(
    value: object,
    *,
    local_role: str,
    direction: str,
    now: datetime,
) -> _Config:
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig
        or value.enabled is not True
        or type(value.mailbox_adapter_config)
        is not _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig
        or type(value.provider_route_iam_attestation_config)
        is not PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CONFIG_INVALID")
    try:
        facts = _mailbox._config(
            value.mailbox_adapter_config,
            local_role=local_role,
            direction=direction,
            now=now,
        )
    except _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_MAILBOX_ADMISSION_INVALID"
        ) from exc
    if (
        value.provider_route_iam_attestation_config.mailbox_adapter_config
        is not value.mailbox_adapter_config
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CONFIG_INVALID")
    try:
        route_iam = require_verified_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation(
            value.provider_route_iam_attestation,
            config=value.provider_route_iam_attestation_config,
            local_role=local_role,
            direction=direction,
            now=now,
        )
    except PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_ROUTE_IAM_ATTESTATION_INVALID"
        ) from exc
    root = _safe_root(value.root)
    return _Config(
        mailbox_config=value.mailbox_adapter_config,
        mailbox_facts=facts,
        root=root,
        profile=_load_profile(root, facts=facts, route_iam=route_iam),
        provider_route_iam_attestation_config=value.provider_route_iam_attestation_config,
        provider_route_iam_attestation=route_iam,
    )


def _fresh(config: _Config, *, now: datetime) -> None:
    try:
        facts = _mailbox._config(
            config.mailbox_config,
            local_role=config.mailbox_facts.policy.local_role,
            direction=config.mailbox_facts.policy.direction,
            now=now,
        )
        _mailbox._fresh_gate(facts, now=now)
    except _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_MAILBOX_ADMISSION_INVALID"
        ) from exc
    try:
        route_iam = require_verified_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation(
            config.provider_route_iam_attestation,
            config=config.provider_route_iam_attestation_config,
            local_role=facts.policy.local_role,
            direction=facts.policy.direction,
            now=now,
        )
    except PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_ROUTE_IAM_ATTESTATION_INVALID"
        ) from exc
    profile = config.profile
    if (
        facts.mailbox_admission.admission_sha256 != profile.admission_sha256
        or facts.delivery_binding_sha256 != profile.delivery_binding_sha256
        or facts.retention_proof.proof_sha256 != profile.retention_proof_sha256
        or profile.provider_route_iam_attestation_sha256 != route_iam.attestation_sha256
        or profile.endpoint_url != route_iam.endpoint_url
        or profile.bucket != route_iam.bucket
        or profile.region_name != route_iam.region_name
        or facts.policy != config.mailbox_facts.policy
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_PROFILE_CROSS_PIN_MISMATCH")


def _credentials(value: object, *, local_role: str) -> _mailbox._FixedRoleCredentials:
    if (
        type(value) is not _mailbox._FixedRoleCredentials
        or value._capability is not _mailbox._CREDENTIAL_CAPABILITY
        or value.local_role != local_role
        or type(value.access_key_id) is not str
        or type(value.secret_access_key) is not str
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CREDENTIAL_INVALID")
    return value


def _load_s3v4_sdk() -> tuple[Any, Callable[..., Any]]:
    """Import provider SDK pieces only after credential admission in a callback."""

    try:
        boto3 = importlib.import_module("boto3")
        botocore_config = importlib.import_module("botocore.config")
        configuration = botocore_config.Config
    except (ImportError, AttributeError) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_SDK_UNAVAILABLE"
        ) from exc
    return boto3, configuration


def _new_s3v4_client(
    *,
    credentials: _mailbox._FixedRoleCredentials,
    profile: _Profile,
) -> Any:
    boto3, configuration = _load_s3v4_sdk()
    try:
        return boto3.client(
            "s3",
            endpoint_url=profile.endpoint_url,
            region_name=profile.region_name,
            aws_access_key_id=credentials.access_key_id,
            aws_secret_access_key=credentials.secret_access_key,
            config=configuration(
                signature_version="s3v4",
                s3={"addressing_style": _ADDRESSING_STYLE},
            ),
        )
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CLIENT_CREATE_FAILED"
        ) from exc


def _content_sha256(value: object, *, code: str) -> str:
    return _sha256(value, code=code)


def _version_id(value: object, *, code: str) -> str:
    if type(value) is not str or value == "null" or _VERSION_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _object_key(value: object, *, profile: _Profile, code: str) -> str:
    if type(value) is not str or not value.isascii() or not value.startswith(profile.object_prefix):
        _fail(code)
    suffix = value[len(profile.object_prefix) :]
    if not suffix.endswith(".json") or _SHA_RE.fullmatch(suffix[:-5]) is None:
        _fail(code)
    return value


@dataclass(frozen=True)
class _Observation:
    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retained_until: datetime
    retention_proof_sha256: str


class _RawBase:
    __slots__ = ("_client", "_profile", "_maximum_list_entries", "_active")

    def __init__(self, *, client: Any, profile: _Profile, maximum_list_entries: int) -> None:
        self._client = client
        self._profile = profile
        self._maximum_list_entries = maximum_list_entries
        self._active = True

    def _close(self) -> None:
        self._active = False

    def _require_active(self) -> None:
        if self._active is not True:
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_RAW_HANDLE_INACTIVE")

    def _head(self, *, object_key: str, object_version_id: str) -> _Observation:
        self._require_active()
        key = _object_key(
            object_key,
            profile=self._profile,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_HEAD_INVALID",
        )
        version = _version_id(
            object_version_id,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_HEAD_INVALID",
        )
        try:
            response = self._client.head_object(
                Bucket=self._profile.bucket,
                Key=key,
                VersionId=version,
            )
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
                "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_HEAD_FAILED"
            ) from exc
        return self._observation(
            response,
            object_key=key,
            object_version_id=version,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_HEAD_INVALID",
        )

    def _observation(
        self,
        response: object,
        *,
        object_key: str,
        object_version_id: str,
        code: str,
    ) -> _Observation:
        if type(response) is not dict:
            _fail(code)
        metadata = response.get("Metadata")
        if type(metadata) is not dict:
            _fail(code)
        content_sha = _content_sha256(metadata.get("content-sha256"), code=code)
        retention_proof_sha = _content_sha256(metadata.get("retention-proof-sha256"), code=code)
        content_bytes = response.get("ContentLength")
        if (
            type(content_bytes) is not int
            or content_bytes < 1
            or response.get("ObjectLockMode") != _OBJECT_LOCK_MODE
        ):
            _fail(code)
        retained = _utc(response.get("ObjectLockRetainUntilDate"), code=code)
        return _Observation(
            object_key=object_key,
            object_version_id=object_version_id,
            content_sha256=content_sha,
            content_bytes=content_bytes,
            retained_until=retained,
            retention_proof_sha256=retention_proof_sha,
        )

    def _put(
        self,
        *,
        object_key: str,
        canonical_delivery: bytes,
        content_sha256: str,
        content_bytes: int,
        retained_until: datetime,
        retention_proof_sha256: str,
    ) -> _mailbox.PhysicalWalV2WitnessRoundtripS3ObjectVersion:
        self._require_active()
        key = _object_key(
            object_key,
            profile=self._profile,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CREATE_INPUT_INVALID",
        )
        digest = _content_sha256(
            content_sha256,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CREATE_INPUT_INVALID",
        )
        proof_sha = _content_sha256(
            retention_proof_sha256,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CREATE_INPUT_INVALID",
        )
        retained = _utc(
            retained_until,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CREATE_INPUT_INVALID",
        )
        if (
            type(canonical_delivery) is not bytes
            or type(content_bytes) is not int
            or content_bytes != len(canonical_delivery)
            or hashlib.sha256(canonical_delivery).hexdigest() != digest
        ):
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CREATE_INPUT_INVALID")
        try:
            response = self._client.put_object(
                Bucket=self._profile.bucket,
                Key=key,
                Body=canonical_delivery,
                ContentLength=content_bytes,
                ContentType="application/json",
                IfNoneMatch="*",
                ChecksumAlgorithm="SHA256",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
                Metadata={
                    "content-sha256": digest,
                    "retention-proof-sha256": proof_sha,
                },
                ObjectLockMode=_OBJECT_LOCK_MODE,
                ObjectLockRetainUntilDate=retained,
            )
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
                "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CREATE_FAILED"
            ) from exc
        if type(response) is not dict:
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CREATE_RECEIPT_INVALID")
        version = _version_id(
            response.get("VersionId"),
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CREATE_RECEIPT_INVALID",
        )
        return _mailbox.PhysicalWalV2WitnessRoundtripS3ObjectVersion(
            object_key=key,
            object_version_id=version,
            content_sha256=digest,
            content_bytes=content_bytes,
            retained_until=retained,
            conditional_create_only=True,
            object_lock_compliance=True,
            retention_proof_sha256=proof_sha,
        )

    def _head_result(
        self,
        *,
        object_key: str,
        object_version_id: str,
    ) -> _mailbox.PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead:
        observed = self._head(object_key=object_key, object_version_id=object_version_id)
        return _mailbox.PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead(
            object_key=observed.object_key,
            object_version_id=observed.object_version_id,
            content_sha256=observed.content_sha256,
            content_bytes=observed.content_bytes,
            retained_until=observed.retained_until,
            object_lock_compliance=True,
            retention_proof_sha256=observed.retention_proof_sha256,
        )

    def _get_result(
        self,
        *,
        object_key: str,
        object_version_id: str,
        maximum_bytes: int,
    ) -> _mailbox.PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead:
        self._require_active()
        if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= 2 * 1024 * 1024:
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_GET_INPUT_INVALID")
        key = _object_key(
            object_key,
            profile=self._profile,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_GET_INPUT_INVALID",
        )
        version = _version_id(
            object_version_id,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_GET_INPUT_INVALID",
        )
        try:
            response = self._client.get_object(
                Bucket=self._profile.bucket,
                Key=key,
                VersionId=version,
                Range="bytes=0-" + str(maximum_bytes - 1),
            )
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
                "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_GET_FAILED"
            ) from exc
        observed = self._observation(
            response,
            object_key=key,
            object_version_id=version,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_GET_INVALID",
        )
        if observed.content_bytes > maximum_bytes:
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_GET_OVERSIZE")
        body = response.get("Body") if type(response) is dict else None
        if body is None:
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_GET_INVALID")
        try:
            raw = body.read(maximum_bytes + 1)
            trailing = body.read(1)
        except (AttributeError, TypeError, ValueError, OSError) as exc:
            raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
                "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_GET_FAILED"
            ) from exc
        finally:
            try:
                body.close()
            except Exception:
                pass
        if (
            type(raw) is not bytes
            or len(raw) != observed.content_bytes
            or len(raw) > maximum_bytes
            or trailing not in {b"", None}
            or hashlib.sha256(raw).hexdigest() != observed.content_sha256
        ):
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_EXACT_GET_INVALID")
        return _mailbox.PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead(
            object_key=observed.object_key,
            object_version_id=observed.object_version_id,
            content_sha256=observed.content_sha256,
            content_bytes=observed.content_bytes,
            retained_until=observed.retained_until,
            object_lock_compliance=True,
            retention_proof_sha256=observed.retention_proof_sha256,
            canonical_delivery=raw,
        )

    def _list_results(self) -> tuple[_mailbox.PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]:
        self._require_active()
        maximum = min(_MAX_SDK_LIST_ENTRIES, self._maximum_list_entries)
        try:
            response = self._client.list_object_versions(
                Bucket=self._profile.bucket,
                Prefix=self._profile.object_prefix,
                MaxKeys=maximum,
            )
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError(
                "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_LIST_FAILED"
            ) from exc
        if (
            type(response) is not dict
            or response.get("IsTruncated") is not False
            or response.get("DeleteMarkers", []) not in ([], None)
            or type(response.get("Versions", [])) is not list
            or len(response.get("Versions", [])) > maximum
        ):
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_LIST_INVALID")
        result: list[_mailbox.PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator] = []
        for item in response.get("Versions", []):
            if type(item) is not dict:
                _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_LIST_INVALID")
            key = _object_key(
                item.get("Key"),
                profile=self._profile,
                code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_LIST_INVALID",
            )
            version = _version_id(
                item.get("VersionId"),
                code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_LIST_INVALID",
            )
            observed = self._head(object_key=key, object_version_id=version)
            result.append(
                _mailbox.PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator(
                    object_key=observed.object_key,
                    object_version_id=observed.object_version_id,
                    content_sha256=observed.content_sha256,
                    content_bytes=observed.content_bytes,
                    retained_until=observed.retained_until,
                    object_lock_compliance=True,
                    retention_proof_sha256=observed.retention_proof_sha256,
                )
            )
        return tuple(result)


class _FiToWitnessPublisherRaw(_RawBase):
    def put_fi_to_witness_create_only(self, **kwargs: object):
        return self._put(**kwargs)

    def head_fi_to_witness_exact(self, **kwargs: object):
        return self._head_result(**kwargs)

    def get_fi_to_witness_exact(self, **kwargs: object):
        return self._get_result(**kwargs)


class _WitnessIrEgressRaw(_RawBase):
    def put_witness_to_ir_create_only(self, **kwargs: object):
        return self._put(**kwargs)

    def head_witness_to_ir_exact(self, **kwargs: object):
        return self._head_result(**kwargs)

    def get_witness_to_ir_exact(self, **kwargs: object):
        return self._get_result(**kwargs)


class _IrToWitnessPublisherRaw(_RawBase):
    def put_ir_to_witness_create_only(self, **kwargs: object):
        return self._put(**kwargs)

    def head_ir_to_witness_exact(self, **kwargs: object):
        return self._head_result(**kwargs)

    def get_ir_to_witness_exact(self, **kwargs: object):
        return self._get_result(**kwargs)


class _WitnessFiPublisherRaw(_RawBase):
    def put_witness_to_fi_create_only(self, **kwargs: object):
        return self._put(**kwargs)

    def head_witness_to_fi_exact(self, **kwargs: object):
        return self._head_result(**kwargs)

    def get_witness_to_fi_exact(self, **kwargs: object):
        return self._get_result(**kwargs)


class _WitnessFiIngressRaw(_RawBase):
    def list_fi_to_witness_immutable_locators(self):
        return self._list_results()

    def head_fi_to_witness_exact(self, **kwargs: object):
        return self._head_result(**kwargs)

    def get_fi_to_witness_exact(self, **kwargs: object):
        return self._get_result(**kwargs)


class _IrStandbyIngressRaw(_RawBase):
    def list_witness_to_ir_immutable_locators(self):
        return self._list_results()

    def head_witness_to_ir_exact(self, **kwargs: object):
        return self._head_result(**kwargs)

    def get_witness_to_ir_exact(self, **kwargs: object):
        return self._get_result(**kwargs)


class _WitnessIrIngressRaw(_RawBase):
    def list_ir_to_witness_immutable_locators(self):
        return self._list_results()

    def head_ir_to_witness_exact(self, **kwargs: object):
        return self._head_result(**kwargs)

    def get_ir_to_witness_exact(self, **kwargs: object):
        return self._get_result(**kwargs)


class _FiAckIngressRaw(_RawBase):
    def list_witness_to_fi_immutable_locators(self):
        return self._list_results()

    def head_witness_to_fi_exact(self, **kwargs: object):
        return self._head_result(**kwargs)

    def get_witness_to_fi_exact(self, **kwargs: object):
        return self._get_result(**kwargs)


_T = TypeVar("_T")


class _ScopeBase:
    __slots__ = ("_config", "_capability")

    def __init__(self, config: _Config, capability: object) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CONSTRUCTION_FORBIDDEN")
        self._config = config
        self._capability = capability

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_SERIALIZATION_FORBIDDEN")

    def _with(
        self,
        *,
        credentials: _mailbox._FixedRoleCredentials,
        operation: Callable[[Any], _T],
        raw_type: type[_RawBase],
    ) -> _T:
        if not callable(operation):
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CALLBACK_INVALID")
        _fresh(self._config, now=_host_now())
        fixed_credentials = _credentials(
            credentials,
            local_role=self._config.mailbox_facts.policy.local_role,
        )
        client = _new_s3v4_client(
            credentials=fixed_credentials,
            profile=self._config.profile,
        )
        raw = raw_type(
            client=client,
            profile=self._config.profile,
            maximum_list_entries=self._config.mailbox_facts.maximum_list_entries,
        )
        try:
            return operation(raw)
        finally:
            raw._close()


class PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherScope(_ScopeBase):
    def with_fi_to_witness_publisher_s3(self, *, credentials, operation):
        return self._with(credentials=credentials, operation=operation, raw_type=_FiToWitnessPublisherRaw)


class PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressScope(_ScopeBase):
    def with_witness_to_ir_egress_s3(self, *, credentials, operation):
        return self._with(credentials=credentials, operation=operation, raw_type=_WitnessIrEgressRaw)


class PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherScope(_ScopeBase):
    def with_ir_to_witness_publisher_s3(self, *, credentials, operation):
        return self._with(credentials=credentials, operation=operation, raw_type=_IrToWitnessPublisherRaw)


class PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherScope(_ScopeBase):
    def with_witness_to_fi_publisher_s3(self, *, credentials, operation):
        return self._with(credentials=credentials, operation=operation, raw_type=_WitnessFiPublisherRaw)


class PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressScope(_ScopeBase):
    def with_fi_to_witness_ingress_s3(self, *, credentials, operation):
        return self._with(credentials=credentials, operation=operation, raw_type=_WitnessFiIngressRaw)


class PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressScope(_ScopeBase):
    def with_witness_to_ir_ingress_s3(self, *, credentials, operation):
        return self._with(credentials=credentials, operation=operation, raw_type=_IrStandbyIngressRaw)


class PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressScope(_ScopeBase):
    def with_ir_to_witness_ingress_s3(self, *, credentials, operation):
        return self._with(credentials=credentials, operation=operation, raw_type=_WitnessIrIngressRaw)


class PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressScope(_ScopeBase):
    def with_witness_to_fi_ingress_s3(self, *, credentials, operation):
        return self._with(credentials=credentials, operation=operation, raw_type=_FiAckIngressRaw)


def _open(
    value: PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig,
    *,
    local_role: str,
    direction: str,
    scope_type: type[_ScopeBase],
    now: datetime | None,
) -> _ScopeBase:
    observed = _host_now() if now is None else _utc(
        now,
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_SCOPE_CLOCK_INVALID",
    )
    return scope_type(
        _config(value, local_role=local_role, direction=direction, now=observed),
        _CAPABILITY,
    )


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_scope(
    *, config: PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig, now: datetime | None = None
) -> PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherScope:
    return _open(config, local_role="fi-writer-source-outbox", direction=_PUBLISH, scope_type=PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherScope, now=now)  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_ir_publisher_scope(
    *, config: PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig, now: datetime | None = None
) -> PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressScope:
    return _open(config, local_role="witness-ir-egress", direction=_PUBLISH, scope_type=PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressScope, now=now)  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_to_witness_publisher_scope(
    *, config: PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig, now: datetime | None = None
) -> PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherScope:
    return _open(config, local_role="ir-durable-ack-outbox", direction=_PUBLISH, scope_type=PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherScope, now=now)  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_fi_publisher_scope(
    *, config: PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig, now: datetime | None = None
) -> PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherScope:
    return _open(config, local_role="witness-fi-egress", direction=_PUBLISH, scope_type=PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherScope, now=now)  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_scope(
    *, config: PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig, now: datetime | None = None
) -> PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressScope:
    return _open(config, local_role="witness-fi-ingress", direction=_CONSUME, scope_type=PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressScope, now=now)  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_standby_ingress_scope(
    *, config: PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig, now: datetime | None = None
) -> PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressScope:
    return _open(config, local_role="ir-standby-ack-inbox", direction=_CONSUME, scope_type=PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressScope, now=now)  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_ir_ingress_scope(
    *, config: PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig, now: datetime | None = None
) -> PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressScope:
    return _open(config, local_role="witness-ir-ingress", direction=_CONSUME, scope_type=PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressScope, now=now)  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_ack_ingress_scope(
    *, config: PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig, now: datetime | None = None
) -> PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressScope:
    return _open(config, local_role="fi-writer-ack-inbox", direction=_CONSUME, scope_type=PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressScope, now=now)  # type: ignore[return-value]
