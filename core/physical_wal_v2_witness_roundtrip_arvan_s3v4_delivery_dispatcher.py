"""Role-local, named-only V2 wiring from Arvan S3v4 scope to delivery runtime.

Every named opener accepts exactly one local role configuration.  It never
loads another host's profile, credential root, typed admission, retention
proof, or S3 scope.  Completeness of the eight-role matrix is instead carried
by a fresh signed portable FullBundleAttestation containing only public role
projections and hashes.  The selected local projection is cross-pinned to the
local admission, delivery binding, retention proof, and provider-route/IAM
attestation before a local concrete scope, mailbox adapter, and durable
delivery runtime are opened.

There is no role selector, route argument, fallback, generic dispatcher, or
provider client in this module.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_scope as _scope
from core import physical_wal_v2_witness_roundtrip_delivery_contract as _delivery
from core import physical_wal_v2_witness_roundtrip_delivery_runtime as _runtime
from core import physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as _mailbox


__all__ = (
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_SCHEMA",
    "PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig",
    "PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError",
    "PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressDispatcher",
    "PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherDispatcher",
    "PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig",
    "PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressDispatcher",
    "PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherDispatcher",
    "PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressDispatcher",
    "PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherDispatcher",
    "PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressDispatcher",
    "PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressDispatcher",
    "VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestation",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_ack_ingress_dispatcher",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_dispatcher",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_standby_ingress_dispatcher",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_to_witness_publisher_dispatcher",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_dispatcher",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_fi_publisher_dispatcher",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_ir_publisher_dispatcher",
    "open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_ir_ingress_dispatcher",
    "require_verified_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation",
    "verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-arvan-s3v4-delivery-dispatcher-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_DEFAULT_ENABLED = False
_FULL_BUNDLE_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-arvan-s3v4-full-bundle-attestation-v1"
_FULL_BUNDLE_VERSION = 1
_FULL_BUNDLE_DOMAIN = b"gold-trade-physical-wal-v2-witness-roundtrip-arvan-s3v4-full-bundle-attestation-v1\x00"
_CAPABILITY = object()
_ZERO_SHA256 = "0" * 64
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_SHA_RE = re.compile(SHA256_RE.pattern, re.ASCII)
_RELEASE_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_FULL_FIELDS = frozenset(
    {
        "schema",
        "version",
        "bundle_id",
        "bundle_nonce",
        "release_sha",
        "issued_at",
        "expires_at",
        "deployment_binding_sha256",
        "deployment_authority_public_key_sha256",
        "role_projections",
        "signature_base64",
    }
)
_PROJECTION_FIELDS = frozenset(
    {
        "host_id",
        "local_role",
        "mailbox",
        "direction",
        "object_prefix",
        "admission_sha256",
        "deployment_binding_sha256",
        "delivery_binding_sha256",
        "retention_proof_sha256",
        "provider_route_iam_attestation_sha256",
        "roundtrip_configuration_sha256",
    }
)
_ROLE_SPECS = (
    (
        "fi-writer-source-outbox",
        "fi-to-witness",
        "publish",
        "physical-wal-v2-witness-roundtrip-delivery-v1/fi-to-witness/",
    ),
    (
        "witness-fi-ingress",
        "fi-to-witness",
        "consume",
        "physical-wal-v2-witness-roundtrip-delivery-v1/fi-to-witness/",
    ),
    (
        "witness-ir-egress",
        "witness-to-ir",
        "publish",
        "physical-wal-v2-witness-roundtrip-delivery-v1/witness-to-ir/",
    ),
    (
        "ir-standby-ack-inbox",
        "witness-to-ir",
        "consume",
        "physical-wal-v2-witness-roundtrip-delivery-v1/witness-to-ir/",
    ),
    (
        "ir-durable-ack-outbox",
        "ir-to-witness",
        "publish",
        "physical-wal-v2-witness-roundtrip-delivery-v1/ir-to-witness/",
    ),
    (
        "witness-ir-ingress",
        "ir-to-witness",
        "consume",
        "physical-wal-v2-witness-roundtrip-delivery-v1/ir-to-witness/",
    ),
    (
        "witness-fi-egress",
        "witness-to-fi",
        "publish",
        "physical-wal-v2-witness-roundtrip-delivery-v1/witness-to-fi/",
    ),
    (
        "fi-writer-ack-inbox",
        "witness-to-fi",
        "consume",
        "physical-wal-v2-witness-roundtrip-delivery-v1/witness-to-fi/",
    ),
)
_ROLE_BY_NAME = {item[0]: item for item in _ROLE_SPECS}


class PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(RuntimeError):
    """A local role or its portable full-matrix evidence is unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig:
    """Local deployment signer and binding pin for portable topology evidence.

    The full-bundle signer is deliberately not an independent authority.  Its
    public key is derived from this *local* mailbox-admission deployment
    configuration, and the signed bundle must carry that same local deployment
    binding.  This config never contains another host's root, profile,
    credentials, or typed capability.
    """

    mailbox_adapter_config: _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig | None = field(
        default=None,
        repr=False,
    )
    expected_release_sha: str = ""
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = 300


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig:
    """Default-off configuration for exactly one local named dispatcher role."""

    runtime_config: _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeConfig | None = None
    scope_config: _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig | None = field(
        default=None,
        repr=False,
    )
    full_bundle_attestation_config: PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig | None = field(
        default=None,
        repr=False,
    )
    full_bundle_attestation: "VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestation | None" = field(
        default=None,
        repr=False,
    )
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_DEFAULT_ENABLED


@dataclass(frozen=True)
class _RoleProjection:
    host_id: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    admission_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    retention_proof_sha256: str
    provider_route_iam_attestation_sha256: str
    roundtrip_configuration_sha256: str


@dataclass(frozen=True, init=False)
class VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestation:
    """Fresh portable projection of all eight roles; it contains no secret or path."""

    bundle_id: str
    bundle_nonce: str
    release_sha: str
    issued_at: datetime
    expires_at: datetime
    deployment_binding_sha256: str
    deployment_authority_public_key_sha256: str
    projections: tuple[_RoleProjection, ...] = field(repr=False)
    attestation_sha256: str
    canonical_attestation: bytes = field(repr=False)
    _configuration_sha256: str = field(repr=False)
    _capability: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        bundle_id: str,
        bundle_nonce: str,
        release_sha: str,
        issued_at: datetime,
        expires_at: datetime,
        deployment_binding_sha256: str,
        deployment_authority_public_key_sha256: str,
        projections: tuple[_RoleProjection, ...],
        attestation_sha256: str,
        canonical_attestation: bytes,
        configuration_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("bundle_id", bundle_id),
            ("bundle_nonce", bundle_nonce),
            ("release_sha", release_sha),
            ("issued_at", issued_at),
            ("expires_at", expires_at),
            ("deployment_binding_sha256", deployment_binding_sha256),
            ("deployment_authority_public_key_sha256", deployment_authority_public_key_sha256),
            ("projections", projections),
            ("attestation_sha256", attestation_sha256),
            ("canonical_attestation", canonical_attestation),
            ("_configuration_sha256", configuration_sha256),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ResolvedLocal:
    local_role: str
    runtime_config: _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeConfig
    scope_config: _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(code) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _release(value: object, *, code: str) -> str:
    if type(value) is not str or _RELEASE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_INVALID")


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if result.microsecond != 0:
        _fail(code)
    return result


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(code) from exc


def _signature(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(code) from exc
    if len(result) != 64:
        _fail(code)
    return result


def _attestation_context(
    value: object,
    *,
    now: datetime,
) -> tuple[Ed25519PublicKey, str, int, _mailbox._Config, str, str]:
    """Derive the only permitted bundle signer from this local deployment.

    A caller cannot introduce a second topology-signing key here.  The local
    typed mailbox admission is freshly checked first; its deployment authority
    and deployment binding become the verifier's exact pins.
    """

    if (
        type(value) is not PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig
        or value.enabled is not True
        or type(value.mailbox_adapter_config)
        is not _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig
        or type(value.maximum_evidence_age_seconds) is not int
        or not 1 <= value.maximum_evidence_age_seconds <= 86_400
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CONFIG_INVALID")
    try:
        local_role = value.mailbox_adapter_config.admission_config.local_role
        expected = _ROLE_BY_NAME[local_role]
        mailbox_facts = _mailbox._config(
            value.mailbox_adapter_config,
            local_role=expected[0],
            direction=expected[2],
            now=now,
        )
    except (
        AttributeError,
        KeyError,
        _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
    ) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_LOCAL_ADMISSION_INVALID"
        ) from exc
    authority_public_key = mailbox_facts.admission_config.host_role_authority_public_key
    if type(authority_public_key) is not bytes or len(authority_public_key) != 32:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CONFIG_INVALID")
    try:
        authority = Ed25519PublicKey.from_public_bytes(authority_public_key)
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CONFIG_INVALID"
        ) from exc
    deployment_binding_sha256 = _sha256(
        mailbox_facts.mailbox_admission.deployment_binding_sha256,
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_LOCAL_ADMISSION_INVALID",
    )
    expected_release_sha = _release(
        value.expected_release_sha,
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CONFIG_INVALID",
    )
    try:
        local_delivery = _delivery._config(
            value.mailbox_adapter_config.delivery_config,
            mailbox=mailbox_facts.policy.mailbox,
        )
        local_release_sha = _release(
            local_delivery.binding.release_sha,
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_LOCAL_ADMISSION_INVALID",
        )
    except _delivery.PhysicalWalV2WitnessRoundtripDeliveryError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_LOCAL_ADMISSION_INVALID"
        ) from exc
    if expected_release_sha != local_release_sha:
        _fail(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_LOCAL_RELEASE_MISMATCH"
        )
    authority_sha256 = _sha256_bytes(authority_public_key)
    configuration_sha256 = _sha256_bytes(
        _canonical(
            {
                "local_role": mailbox_facts.policy.local_role,
                "mailbox": mailbox_facts.policy.mailbox,
                "direction": mailbox_facts.policy.direction,
                "admission_sha256": mailbox_facts.mailbox_admission.admission_sha256,
                "deployment_binding_sha256": deployment_binding_sha256,
                "delivery_binding_sha256": mailbox_facts.delivery_binding_sha256,
                "deployment_authority_public_key_sha256": authority_sha256,
                "expected_release_sha": expected_release_sha,
                "maximum_evidence_age_seconds": value.maximum_evidence_age_seconds,
            },
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CONFIG_INVALID",
        )
    )
    return (
        authority,
        configuration_sha256,
        value.maximum_evidence_age_seconds,
        mailbox_facts,
        authority_sha256,
        expected_release_sha,
    )


def _parse_attestation(value: object) -> tuple[dict[str, Any], bytes]:
    if type(value) is not bytes or not 1 <= len(value) <= 256 * 1024:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_INVALID")
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_INVALID"
        ) from exc
    if (
        type(parsed) is not dict
        or set(parsed) != _FULL_FIELDS
        or _canonical(parsed, code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_INVALID")
        != value
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_INVALID")
    return dict(parsed), value


def _projection(value: object, *, expected: tuple[str, str, str, str]) -> _RoleProjection:
    if type(value) is not dict or set(value) != _PROJECTION_FIELDS:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_PROJECTION_INVALID")
    local_role, mailbox, direction, prefix = expected
    fields = dict(value)
    if (
        type(fields["host_id"]) is not str
        or not fields["host_id"].isascii()
        or not 1 <= len(fields["host_id"]) <= 127
        or fields["local_role"] != local_role
        or fields["mailbox"] != mailbox
        or fields["direction"] != direction
        or fields["object_prefix"] != prefix
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_PROJECTION_INVALID")
    return _RoleProjection(
        host_id=fields["host_id"],
        local_role=local_role,
        mailbox=mailbox,
        direction=direction,
        object_prefix=prefix,
        admission_sha256=_sha256(
            fields["admission_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_PROJECTION_INVALID",
        ),
        deployment_binding_sha256=_sha256(
            fields["deployment_binding_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_PROJECTION_INVALID",
        ),
        delivery_binding_sha256=_sha256(
            fields["delivery_binding_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_PROJECTION_INVALID",
        ),
        retention_proof_sha256=_sha256(
            fields["retention_proof_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_PROJECTION_INVALID",
        ),
        provider_route_iam_attestation_sha256=_sha256(
            fields["provider_route_iam_attestation_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_PROJECTION_INVALID",
        ),
        roundtrip_configuration_sha256=_sha256(
            fields["roundtrip_configuration_sha256"],
            code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_PROJECTION_INVALID",
        ),
    )


def verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
    attestation: bytes,
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestation:
    """Verify portable fresh proof that all eight public role projections exist."""

    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CLOCK_INVALID")
    (
        authority,
        configuration_sha256,
        maximum_age,
        mailbox_facts,
        authority_sha256,
        expected_release_sha,
    ) = _attestation_context(config, now=observed)
    item, raw = _parse_attestation(attestation)
    signature = _signature(
        item["signature_base64"],
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_INVALID",
    )
    unsigned = dict(item)
    unsigned.pop("signature_base64", None)
    try:
        authority.verify(
            signature,
            _FULL_BUNDLE_DOMAIN
            + _canonical(
                unsigned,
                code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_INVALID",
            ),
        )
    except InvalidSignature as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_SIGNATURE_INVALID"
        ) from exc
    if (
        item["schema"] != _FULL_BUNDLE_SCHEMA
        or item["version"] != _FULL_BUNDLE_VERSION
        or type(item["bundle_id"]) is not str
        or _ID_RE.fullmatch(item["bundle_id"]) is None
        or type(item["bundle_nonce"]) is not str
        or _NONCE_RE.fullmatch(item["bundle_nonce"]) is None
        or type(item["role_projections"]) is not list
        or len(item["role_projections"]) != len(_ROLE_SPECS)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH")
    projections = tuple(
        _projection(value, expected=expected)
        for value, expected in zip(item["role_projections"], _ROLE_SPECS, strict=True)
    )
    if (
        len({projection.local_role for projection in projections}) != len(_ROLE_SPECS)
        or len({projection.delivery_binding_sha256 for projection in projections}) != 1
        or len({projection.roundtrip_configuration_sha256 for projection in projections}) != 1
        or len({projection.deployment_binding_sha256 for projection in projections}) != 1
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH")
    deployment_binding_sha256 = _sha256(
        item["deployment_binding_sha256"],
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH",
    )
    deployment_authority_public_key_sha256 = _sha256(
        item["deployment_authority_public_key_sha256"],
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH",
    )
    release_sha = _release(
        item["release_sha"],
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH",
    )
    if (
        deployment_binding_sha256 != mailbox_facts.mailbox_admission.deployment_binding_sha256
        or deployment_authority_public_key_sha256 != authority_sha256
        or release_sha != expected_release_sha
        or any(
            projection.deployment_binding_sha256 != deployment_binding_sha256
            for projection in projections
        )
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH")
    issued_at = _parse_timestamp(
        item["issued_at"],
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_TIME_INVALID",
    )
    expires_at = _parse_timestamp(
        item["expires_at"],
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_TIME_INVALID",
    )
    if (
        issued_at > observed
        or expires_at <= observed
        or expires_at <= issued_at
        or (observed - issued_at).total_seconds() > maximum_age
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_STALE")
    return VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestation(
        bundle_id=item["bundle_id"],
        bundle_nonce=item["bundle_nonce"],
        release_sha=release_sha,
        issued_at=issued_at,
        expires_at=expires_at,
        deployment_binding_sha256=deployment_binding_sha256,
        deployment_authority_public_key_sha256=deployment_authority_public_key_sha256,
        projections=projections,
        attestation_sha256=_sha256_bytes(raw),
        canonical_attestation=raw,
        configuration_sha256=configuration_sha256,
        capability=_CAPABILITY,
    )


def require_verified_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
    attestation: object,
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestation:
    """Reverify portable full-matrix evidence before opening the local role."""

    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CLOCK_INVALID")
    (
        _authority,
        configuration_sha256,
        _maximum_age,
        mailbox_facts,
        authority_sha256,
        expected_release_sha,
    ) = _attestation_context(config, now=observed)
    if (
        type(attestation) is not VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestation
        or attestation._capability is not _CAPABILITY
        or attestation._configuration_sha256 != configuration_sha256
        or type(attestation.canonical_attestation) is not bytes
        or _sha256_bytes(attestation.canonical_attestation) != attestation.attestation_sha256
        or attestation.deployment_binding_sha256
        != mailbox_facts.mailbox_admission.deployment_binding_sha256
        or attestation.deployment_authority_public_key_sha256 != authority_sha256
        or attestation.release_sha != expected_release_sha
        or attestation.issued_at > observed
        or attestation.expires_at <= observed
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CAPABILITY_INVALID")
    try:
        reverified = verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
            attestation.canonical_attestation,
            config=config,
            now=observed,
        )
    except PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CAPABILITY_INVALID"
        ) from exc
    if (
        reverified.attestation_sha256 != attestation.attestation_sha256
        or reverified.canonical_attestation != attestation.canonical_attestation
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_FULL_BUNDLE_ATTESTATION_CAPABILITY_INVALID")
    return reverified


def _host_now() -> datetime:
    try:
        return datetime.now(timezone.utc).replace(microsecond=0)
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_CLOCK_INVALID"
        ) from exc


def _local(
    value: object,
    *,
    local_role: str,
    now: datetime,
) -> _ResolvedLocal:
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig
        or value.enabled is not True
        or type(value.runtime_config) is not _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeConfig
        or type(value.scope_config) is not _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig
        or type(value.full_bundle_attestation_config)
        is not PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig
        or type(value.scope_config.mailbox_adapter_config)
        is not _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_CONFIG_INVALID")
    try:
        expected = _ROLE_BY_NAME[local_role]
    except KeyError as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_ROLE_INVALID"
        ) from exc
    _role_name, mailbox, direction, prefix = expected
    if value.runtime_config.delivery_config is not value.scope_config.mailbox_adapter_config.delivery_config:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_DELIVERY_CONFIG_IDENTITY_MISMATCH")
    if (
        value.full_bundle_attestation_config.mailbox_adapter_config
        is not value.scope_config.mailbox_adapter_config
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_FULL_BUNDLE_LOCAL_CONFIG_IDENTITY_MISMATCH")
    try:
        runtime_facts = _runtime._config(value.runtime_config)
        delivery_facts = _delivery._config(value.runtime_config.delivery_config, mailbox=mailbox)
        mailbox_facts = _mailbox._config(
            value.scope_config.mailbox_adapter_config,
            local_role=local_role,
            direction=direction,
            now=now,
        )
        scope_facts = _scope._config(
            value.scope_config,
            local_role=local_role,
            direction=direction,
            now=now,
        )
        bundle = require_verified_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
            value.full_bundle_attestation,
            config=value.full_bundle_attestation_config,
            now=now,
        )
    except (
        _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
        _delivery.PhysicalWalV2WitnessRoundtripDeliveryError,
        _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
        _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError,
        PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
    ) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_LOCAL_ADMISSION_INVALID"
        ) from exc
    projection = next(
        (item for item in bundle.projections if item.local_role == local_role),
        None,
    )
    if projection is None:
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_FULL_BUNDLE_ROLE_MISSING")
    if (
        runtime_facts.local_role != local_role
        or runtime_facts.policy.mailbox != mailbox
        or runtime_facts.policy.direction != direction
        or mailbox_facts.policy.local_role != local_role
        or mailbox_facts.policy.mailbox != mailbox
        or mailbox_facts.policy.direction != direction
        or mailbox_facts.policy.object_prefix != prefix
        or scope_facts.mailbox_facts.policy.local_role != local_role
        or scope_facts.mailbox_facts.policy.mailbox != mailbox
        or scope_facts.mailbox_facts.policy.direction != direction
        or mailbox_facts.delivery_binding_sha256 != delivery_facts.binding_sha256
        or scope_facts.mailbox_facts.delivery_binding_sha256 != delivery_facts.binding_sha256
        or projection.host_id != mailbox_facts.mailbox_admission.host_id
        or projection.mailbox != mailbox
        or projection.direction != direction
        or projection.object_prefix != prefix
        or projection.admission_sha256 != mailbox_facts.mailbox_admission.admission_sha256
        or projection.deployment_binding_sha256
        != mailbox_facts.mailbox_admission.deployment_binding_sha256
        or projection.delivery_binding_sha256 != delivery_facts.binding_sha256
        or projection.retention_proof_sha256 != mailbox_facts.retention_proof.proof_sha256
        or projection.provider_route_iam_attestation_sha256
        != scope_facts.provider_route_iam_attestation.attestation_sha256
        or projection.roundtrip_configuration_sha256
        != delivery_facts.binding.roundtrip_configuration_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_LOCAL_CROSS_PIN_MISMATCH")
    return _ResolvedLocal(
        local_role=local_role,
        runtime_config=value.runtime_config,
        scope_config=value.scope_config,
    )


class _DispatcherBase:
    __slots__ = ("_runtime", "_adapter", "_local_config", "_local_role", "_capability")

    def __init__(
        self,
        *,
        runtime: _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntime,
        adapter: object,
        local_config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
        local_role: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_CONSTRUCTION_FORBIDDEN")
        self._runtime = runtime
        self._adapter = adapter
        self._local_config = local_config
        self._local_role = local_role
        self._capability = capability

    def _fresh_local_bundle_gate(self) -> None:
        """Recheck topology freshness before a runtime or S3 callback can run."""

        observed = _host_now()
        try:
            _local(self._local_config, local_role=self._local_role, now=observed)
        except PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError as exc:
            raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
                "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_OPERATION_FRESHNESS_INVALID"
            ) from exc

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_SERIALIZATION_FORBIDDEN")


class PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherDispatcher(_DispatcherBase):
    def publish_fi_to_witness_delivery(
        self, delivery: bytes
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
        self._fresh_local_bundle_gate()
        return _runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
            self._runtime,
            delivery,
            publisher=self._adapter,
        )


class PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressDispatcher(_DispatcherBase):
    def consume_fi_to_witness_delivery(
        self,
    ) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult, ...]:
        self._fresh_local_bundle_gate()
        return _runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
            self._runtime,
            scanner=self._adapter,
        )


class PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressDispatcher(_DispatcherBase):
    def publish_witness_to_ir_delivery(
        self, delivery: bytes
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
        self._fresh_local_bundle_gate()
        return _runtime.publish_physical_wal_v2_witness_witness_to_ir_delivery(
            self._runtime,
            delivery,
            publisher=self._adapter,
        )


class PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressDispatcher(_DispatcherBase):
    def consume_witness_to_ir_delivery(
        self,
    ) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult, ...]:
        self._fresh_local_bundle_gate()
        return _runtime.consume_physical_wal_v2_witness_witness_to_ir_delivery(
            self._runtime,
            scanner=self._adapter,
        )


class PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherDispatcher(_DispatcherBase):
    def publish_ir_to_witness_delivery(
        self, delivery: bytes
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
        self._fresh_local_bundle_gate()
        return _runtime.publish_physical_wal_v2_witness_ir_to_witness_delivery(
            self._runtime,
            delivery,
            publisher=self._adapter,
        )


class PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressDispatcher(_DispatcherBase):
    def consume_ir_to_witness_delivery(
        self,
    ) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult, ...]:
        self._fresh_local_bundle_gate()
        return _runtime.consume_physical_wal_v2_witness_ir_to_witness_delivery(
            self._runtime,
            scanner=self._adapter,
        )


class PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherDispatcher(_DispatcherBase):
    def publish_witness_to_fi_delivery(
        self, delivery: bytes
    ) -> _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
        self._fresh_local_bundle_gate()
        return _runtime.publish_physical_wal_v2_witness_witness_to_fi_delivery(
            self._runtime,
            delivery,
            publisher=self._adapter,
        )


class PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressDispatcher(_DispatcherBase):
    def consume_witness_to_fi_delivery(
        self,
    ) -> tuple[_runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult, ...]:
        self._fresh_local_bundle_gate()
        return _runtime.consume_physical_wal_v2_witness_witness_to_fi_delivery(
            self._runtime,
            scanner=self._adapter,
        )


def _open_local(
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
    local_role: str,
    scope_open: Callable[..., object],
    adapter_open: Callable[..., object],
    dispatcher_type: type[_DispatcherBase],
    now: datetime | None,
) -> _DispatcherBase:
    observed = _host_now() if now is None else _utc(
        now,
        code="V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_CLOCK_INVALID",
    )
    local = _local(config, local_role=local_role, now=observed)
    try:
        scoped = scope_open(config=local.scope_config, now=observed)
        adapter = adapter_open(
            config=local.scope_config.mailbox_adapter_config,
            scope=scoped,
            now=observed,
        )
        runtime = _runtime.open_physical_wal_v2_witness_roundtrip_delivery_runtime(
            config=local.runtime_config,
        )
    except (
        _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError,
        _mailbox.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
        _runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
    ) as exc:
        raise PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError(
            "V2_WITNESS_ROUNDTRIP_ARVAN_S3V4_DELIVERY_DISPATCHER_OPEN_FAILED"
        ) from exc
    return dispatcher_type(
        runtime=runtime,
        adapter=adapter,
        local_config=config,
        local_role=local.local_role,
        capability=_CAPABILITY,
    )


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_dispatcher(
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherDispatcher:
    return _open_local(
        config=config,
        local_role="fi-writer-source-outbox",
        scope_open=_scope.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_scope,
        adapter_open=_mailbox.open_physical_wal_v2_witness_roundtrip_fi_to_witness_publisher_s3_adapter,
        dispatcher_type=PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherDispatcher,
        now=now,
    )  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_dispatcher(
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressDispatcher:
    return _open_local(
        config=config,
        local_role="witness-fi-ingress",
        scope_open=_scope.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_scope,
        adapter_open=_mailbox.open_physical_wal_v2_witness_roundtrip_witness_fi_ingress_s3_adapter,
        dispatcher_type=PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressDispatcher,
        now=now,
    )  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_ir_publisher_dispatcher(
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressDispatcher:
    return _open_local(
        config=config,
        local_role="witness-ir-egress",
        scope_open=_scope.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_ir_publisher_scope,
        adapter_open=_mailbox.open_physical_wal_v2_witness_roundtrip_witness_to_ir_publisher_s3_adapter,
        dispatcher_type=PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressDispatcher,
        now=now,
    )  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_standby_ingress_dispatcher(
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressDispatcher:
    return _open_local(
        config=config,
        local_role="ir-standby-ack-inbox",
        scope_open=_scope.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_standby_ingress_scope,
        adapter_open=_mailbox.open_physical_wal_v2_witness_roundtrip_ir_standby_ingress_s3_adapter,
        dispatcher_type=PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressDispatcher,
        now=now,
    )  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_to_witness_publisher_dispatcher(
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherDispatcher:
    return _open_local(
        config=config,
        local_role="ir-durable-ack-outbox",
        scope_open=_scope.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_to_witness_publisher_scope,
        adapter_open=_mailbox.open_physical_wal_v2_witness_roundtrip_ir_to_witness_publisher_s3_adapter,
        dispatcher_type=PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherDispatcher,
        now=now,
    )  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_ir_ingress_dispatcher(
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressDispatcher:
    return _open_local(
        config=config,
        local_role="witness-ir-ingress",
        scope_open=_scope.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_ir_ingress_scope,
        adapter_open=_mailbox.open_physical_wal_v2_witness_roundtrip_witness_ir_ingress_s3_adapter,
        dispatcher_type=PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressDispatcher,
        now=now,
    )  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_fi_publisher_dispatcher(
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherDispatcher:
    return _open_local(
        config=config,
        local_role="witness-fi-egress",
        scope_open=_scope.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_fi_publisher_scope,
        adapter_open=_mailbox.open_physical_wal_v2_witness_roundtrip_witness_to_fi_publisher_s3_adapter,
        dispatcher_type=PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherDispatcher,
        now=now,
    )  # type: ignore[return-value]


def open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_ack_ingress_dispatcher(
    *,
    config: PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressDispatcher:
    return _open_local(
        config=config,
        local_role="fi-writer-ack-inbox",
        scope_open=_scope.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_ack_ingress_scope,
        adapter_open=_mailbox.open_physical_wal_v2_witness_roundtrip_fi_ack_ingress_s3_adapter,
        dispatcher_type=PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressDispatcher,
        now=now,
    )  # type: ignore[return-value]
