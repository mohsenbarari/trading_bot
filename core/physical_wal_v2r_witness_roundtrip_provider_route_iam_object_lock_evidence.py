"""Pure V2R provider-route/IAM/Object-Lock evidence admission.

This is a deliberately narrow, default-off signed-evidence grammar for the
eight V2R reverse-carrier roles.  It verifies a fresh external assertion
*about* one role's provider route, exact IAM actions, versioning and Object
Lock posture after the existing local V2R mailbox admission has already
verified the role/site/prefix/credential boundary.

It does not open Object Storage, resolve an endpoint, read a credential,
render or install IAM, contact a provider, create a client, deliver a
mailbox object, or grant any operational authority.  The evidence is only a
future adapter input; a production implementation still needs independent
provider-side observation and a root-owned runtime boundary.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core import physical_wal_v2r_witness_roundtrip_contract as _v2r
from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as _mailbox


__all__ = (
    "PHYSICAL_WAL_V2R_PROVIDER_ROUTE_IAM_OBJECT_LOCK_EVIDENCE_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2R_PROVIDER_ROUTE_IAM_OBJECT_LOCK_EVIDENCE_SCHEMA",
    "PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig",
    "PhysicalWalV2rProviderRouteIamObjectLockEvidenceError",
    "VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence",
    "VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidenceMatrix",
    "require_verified_physical_wal_v2r_provider_route_iam_object_lock_evidence",
    "verify_physical_wal_v2r_provider_route_iam_object_lock_evidence",
    "verify_physical_wal_v2r_provider_route_iam_object_lock_evidence_matrix",
)


PHYSICAL_WAL_V2R_PROVIDER_ROUTE_IAM_OBJECT_LOCK_EVIDENCE_SCHEMA = (
    "gold-trade-physical-wal-v2r-provider-route-iam-object-lock-evidence-v1"
)
PHYSICAL_WAL_V2R_PROVIDER_ROUTE_IAM_OBJECT_LOCK_EVIDENCE_DEFAULT_ENABLED = False

_DOMAIN = (
    b"gold-trade-physical-wal-v2r-provider-route-iam-object-lock-evidence-v1\x00"
)
_PROVIDER_KIND = "arvan-s3-compatible-v1"
_OBJECT_LOCK_MODE = "COMPLIANCE"
_CAPABILITY = object()
_ZERO = "0" * 64
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_FIELDS = frozenset(
    {
        "schema",
        "version",
        "protocol_domain",
        "mailbox_prefix",
        "local_site",
        "local_role",
        "mailbox",
        "direction",
        "object_prefix",
        "least_privilege_actions",
        "provider_kind",
        "provider_endpoint_sha256",
        "provider_bucket_sha256",
        "provider_region_sha256",
        "allowed_provider_actions",
        "versioning_enabled",
        "object_lock_enabled",
        "object_lock_mode",
        "object_lock_minimum_retention_seconds",
        "role_credential_identity_sha256",
        "role_iam_policy_sha256",
        "provider_route_iam_attestation_sha256",
        "object_lock_retention_proof_sha256",
        "deployment_binding_sha256",
        "delivery_binding_sha256",
        "v2r_iam_catalog_sha256",
        "evidence_id",
        "evidence_nonce",
        "issued_at",
        "expires_at",
        "signature_base64",
    }
)
_PUBLISH_PROVIDER_ACTIONS = (
    "s3:PutObject:IfNoneMatch:ObjectLockCompliance",
    "s3:HeadObject:ExactVersion",
    "s3:GetObject:ExactVersion",
)
_CONSUME_PROVIDER_ACTIONS = (
    "s3:ListObjectVersions:FixedPrefix",
    "s3:HeadObject:ExactVersion",
    "s3:GetObject:ExactVersion",
)


class PhysicalWalV2rProviderRouteIamObjectLockEvidenceError(ValueError):
    """A V2R provider evidence input is absent, foreign, stale, or unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rProviderRouteIamObjectLockEvidenceError(code)


def _canonical(value: object, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2rProviderRouteIamObjectLockEvidenceError(code) from exc


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(value: object, code: str) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None or value == _ZERO:
        _fail(code)
    return value


def _utc(value: object, code: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
        or value.microsecond
    ):
        _fail(code)
    return value


def _timestamp(value: object, code: str) -> datetime:
    if type(value) is not str or _TIME_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        _fail(code)


def _provider_actions(direction: str) -> tuple[str, ...]:
    if direction == "publish":
        return _PUBLISH_PROVIDER_ACTIONS
    if direction == "consume":
        return _CONSUME_PROVIDER_ACTIONS
    _fail("V2R_PROVIDER_EVIDENCE_ROLE_INVALID")


@dataclass(frozen=True)
class PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig:
    """Public pins for a single V2R role's signed provider evidence.

    The endpoint/bucket/region pins are hashes deliberately: this pure
    admission grammar does not accept a usable provider route or credential.
    The three normal-V2 pins are deny-pins only and never form a bridge to
    the normal carrier.
    """

    admission_config: (
        _mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig | None
    ) = field(default=None, repr=False, compare=False)
    provider_evidence_authority_public_key: bytes | None = field(
        default=None, repr=False
    )
    provider_endpoint_sha256: str = ""
    provider_bucket_sha256: str = ""
    provider_region_sha256: str = ""
    object_lock_minimum_retention_seconds: int = 0
    normal_v2_provider_evidence_authority_public_key_sha256: str = ""
    normal_v2_mailbox_prefix: str = ""
    normal_v2_iam_catalog_sha256: str = ""
    enabled: bool = PHYSICAL_WAL_V2R_PROVIDER_ROUTE_IAM_OBJECT_LOCK_EVIDENCE_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = 300


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence:
    """Opaque verified statement, never a provider client or authority."""

    local_site: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    least_privilege_actions: tuple[str, ...]
    allowed_provider_actions: tuple[str, ...]
    provider_endpoint_sha256: str
    provider_bucket_sha256: str
    provider_region_sha256: str
    object_lock_minimum_retention_seconds: int
    role_credential_identity_sha256: str
    role_iam_policy_sha256: str
    provider_route_iam_attestation_sha256: str
    object_lock_retention_proof_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    v2r_iam_catalog_sha256: str
    evidence_sha256: str
    expires_at: datetime
    is_operational: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_authorized: bool = False
    phase5_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _configuration_sha256: str = field(repr=False)
    _capability: object = field(repr=False, compare=False)

    def __init__(self, *, configuration_sha256: str, capability: object, **values: Any) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2R_PROVIDER_EVIDENCE_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_configuration_sha256", configuration_sha256)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_PROVIDER_EVIDENCE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidenceMatrix:
    """Complete V2R evidence topology check, still non-operational."""

    evidence_matrix_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    v2r_iam_catalog_sha256: str
    is_operational: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_authorized: bool = False
    phase5_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object = field(repr=False, compare=False)

    def __init__(self, *, capability: object, **values: Any) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2R_PROVIDER_EVIDENCE_MATRIX_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_PROVIDER_EVIDENCE_MATRIX_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Resolved:
    config: PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig
    admission: _mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission
    authority: Ed25519PublicKey
    configuration_sha256: str


@dataclass(frozen=True)
class _EvidenceFacts:
    configuration_sha256: str
    canonical_evidence: bytes
    public_values: tuple[tuple[str, object], ...]


_VERIFIED_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence, _EvidenceFacts
] = WeakKeyDictionary()


def _resolve(
    value: object,
    *,
    admission: object,
    now: datetime,
) -> _Resolved:
    if type(value) is not PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig:
        _fail("V2R_PROVIDER_EVIDENCE_CONFIG_INVALID")
    config = value
    if (
        config.enabled is not True
        or type(config.admission_config)
        is not _mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig
        or type(config.provider_evidence_authority_public_key) is not bytes
        or len(config.provider_evidence_authority_public_key) != 32
        or type(config.maximum_evidence_age_seconds) is not int
        or not 1 <= config.maximum_evidence_age_seconds <= 3600
        or type(config.object_lock_minimum_retention_seconds) is not int
        or not 86_400 <= config.object_lock_minimum_retention_seconds <= 31_536_000
        or type(config.normal_v2_mailbox_prefix) is not str
        or not config.normal_v2_mailbox_prefix
    ):
        _fail("V2R_PROVIDER_EVIDENCE_CONFIG_INVALID")
    for name in (
        "provider_endpoint_sha256",
        "provider_bucket_sha256",
        "provider_region_sha256",
        "normal_v2_provider_evidence_authority_public_key_sha256",
        "normal_v2_iam_catalog_sha256",
    ):
        _sha(getattr(config, name), "V2R_PROVIDER_EVIDENCE_CONFIG_INVALID")
    if (
        config.normal_v2_mailbox_prefix
        == _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX
        or config.normal_v2_mailbox_prefix.startswith(
            _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX
        )
        or _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX.startswith(
            config.normal_v2_mailbox_prefix
        )
        or config.normal_v2_iam_catalog_sha256
        == config.admission_config.v2r_iam_catalog_sha256
    ):
        _fail("V2R_PROVIDER_EVIDENCE_NORMAL_V2_REUSED")
    try:
        authority = Ed25519PublicKey.from_public_bytes(
            config.provider_evidence_authority_public_key
        )
    except ValueError:
        _fail("V2R_PROVIDER_EVIDENCE_CONFIG_INVALID")
    authority_sha = _hash(config.provider_evidence_authority_public_key)
    if authority_sha == config.normal_v2_provider_evidence_authority_public_key_sha256:
        _fail("V2R_PROVIDER_EVIDENCE_NORMAL_V2_SIGNER_REUSED")
    try:
        verified_admission = (
            _mailbox.require_verified_physical_wal_v2r_witness_roundtrip_control_mailbox_admission(
                admission=admission,
                config=config.admission_config,
                now=now,
            )
        )
    except _mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError as exc:
        raise PhysicalWalV2rProviderRouteIamObjectLockEvidenceError(
            "V2R_PROVIDER_EVIDENCE_ADMISSION_INVALID:" + exc.code
        ) from exc
    digest = _hash(
        _canonical(
            {
                "schema": PHYSICAL_WAL_V2R_PROVIDER_ROUTE_IAM_OBJECT_LOCK_EVIDENCE_SCHEMA,
                "authority_public_key_base64": base64.b64encode(
                    config.provider_evidence_authority_public_key
                ).decode("ascii"),
                "provider_endpoint_sha256": config.provider_endpoint_sha256,
                "provider_bucket_sha256": config.provider_bucket_sha256,
                "provider_region_sha256": config.provider_region_sha256,
                "object_lock_minimum_retention_seconds": config.object_lock_minimum_retention_seconds,
                "normal_v2_provider_evidence_authority_public_key_sha256": config.normal_v2_provider_evidence_authority_public_key_sha256,
                "normal_v2_mailbox_prefix": config.normal_v2_mailbox_prefix,
                "normal_v2_iam_catalog_sha256": config.normal_v2_iam_catalog_sha256,
                "maximum_evidence_age_seconds": config.maximum_evidence_age_seconds,
                "admission_assertion_sha256": verified_admission.assertion_sha256,
                "local_site": verified_admission.local_site,
                "local_role": verified_admission.local_role,
                "mailbox": verified_admission.mailbox,
                "direction": verified_admission.direction,
                "object_prefix": verified_admission.object_prefix,
                "least_privilege_actions": list(verified_admission.least_privilege_actions),
                "role_credential_identity_sha256": verified_admission.role_credential_identity_sha256,
                "role_iam_policy_sha256": verified_admission.role_iam_policy_sha256,
                "provider_route_iam_attestation_sha256": verified_admission.provider_route_iam_attestation_sha256,
                "object_lock_retention_proof_sha256": verified_admission.object_lock_retention_proof_sha256,
                "deployment_binding_sha256": verified_admission.deployment_binding_sha256,
                "delivery_binding_sha256": verified_admission.delivery_binding_sha256,
                "v2r_iam_catalog_sha256": config.admission_config.v2r_iam_catalog_sha256,
            },
            "V2R_PROVIDER_EVIDENCE_CONFIG_INVALID",
        )
    )
    return _Resolved(config, verified_admission, authority, digest)


def _parse(
    value: object,
    *,
    resolved: _Resolved,
    now: datetime,
) -> VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence:
    if type(value) is not bytes or not 1 <= len(value) <= 128 * 1024:
        _fail("V2R_PROVIDER_EVIDENCE_INVALID")
    try:
        item = json.loads(value.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("V2R_PROVIDER_EVIDENCE_INVALID")
    if (
        type(item) is not dict
        or set(item) != _FIELDS
        or _canonical(item, "V2R_PROVIDER_EVIDENCE_INVALID") != value
    ):
        _fail("V2R_PROVIDER_EVIDENCE_INVALID")
    unsigned = dict(item)
    signature_text = unsigned.pop("signature_base64")
    if type(signature_text) is not str:
        _fail("V2R_PROVIDER_EVIDENCE_INVALID")
    try:
        signature = base64.b64decode(signature_text.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail("V2R_PROVIDER_EVIDENCE_INVALID")
    if len(signature) != 64:
        _fail("V2R_PROVIDER_EVIDENCE_INVALID")
    try:
        resolved.authority.verify(
            signature,
            _DOMAIN + _canonical(unsigned, "V2R_PROVIDER_EVIDENCE_INVALID"),
        )
    except InvalidSignature:
        _fail("V2R_PROVIDER_EVIDENCE_SIGNATURE_INVALID")
    admission, config = resolved.admission, resolved.config
    exact = {
        "schema": PHYSICAL_WAL_V2R_PROVIDER_ROUTE_IAM_OBJECT_LOCK_EVIDENCE_SCHEMA,
        "version": 1,
        "protocol_domain": _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN,
        "mailbox_prefix": _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX,
        "local_site": admission.local_site,
        "local_role": admission.local_role,
        "mailbox": admission.mailbox,
        "direction": admission.direction,
        "object_prefix": admission.object_prefix,
        "least_privilege_actions": list(admission.least_privilege_actions),
        "provider_kind": _PROVIDER_KIND,
        "provider_endpoint_sha256": config.provider_endpoint_sha256,
        "provider_bucket_sha256": config.provider_bucket_sha256,
        "provider_region_sha256": config.provider_region_sha256,
        "allowed_provider_actions": list(_provider_actions(admission.direction)),
        "versioning_enabled": True,
        "object_lock_enabled": True,
        "object_lock_mode": _OBJECT_LOCK_MODE,
        "object_lock_minimum_retention_seconds": config.object_lock_minimum_retention_seconds,
        "role_credential_identity_sha256": admission.role_credential_identity_sha256,
        "role_iam_policy_sha256": admission.role_iam_policy_sha256,
        "provider_route_iam_attestation_sha256": admission.provider_route_iam_attestation_sha256,
        "object_lock_retention_proof_sha256": admission.object_lock_retention_proof_sha256,
        "deployment_binding_sha256": admission.deployment_binding_sha256,
        "delivery_binding_sha256": admission.delivery_binding_sha256,
        "v2r_iam_catalog_sha256": config.admission_config.v2r_iam_catalog_sha256,
    }
    if (
        any(item[name] != expected for name, expected in exact.items())
        or type(item["evidence_id"]) is not str
        or _ID_RE.fullmatch(item["evidence_id"]) is None
        or type(item["evidence_nonce"]) is not str
        or _NONCE_RE.fullmatch(item["evidence_nonce"]) is None
    ):
        _fail("V2R_PROVIDER_EVIDENCE_CROSS_PIN_MISMATCH")
    issued = _timestamp(item["issued_at"], "V2R_PROVIDER_EVIDENCE_TIME_INVALID")
    expires = _timestamp(item["expires_at"], "V2R_PROVIDER_EVIDENCE_TIME_INVALID")
    if (
        issued > now
        or expires <= now
        or expires <= issued
        or expires > admission.expires_at
        or (expires - issued).total_seconds() > config.maximum_evidence_age_seconds
    ):
        _fail("V2R_PROVIDER_EVIDENCE_STALE")
    public_values = (
        ("local_site", admission.local_site),
        ("local_role", admission.local_role),
        ("mailbox", admission.mailbox),
        ("direction", admission.direction),
        ("object_prefix", admission.object_prefix),
        ("least_privilege_actions", admission.least_privilege_actions),
        ("allowed_provider_actions", _provider_actions(admission.direction)),
        ("provider_endpoint_sha256", config.provider_endpoint_sha256),
        ("provider_bucket_sha256", config.provider_bucket_sha256),
        ("provider_region_sha256", config.provider_region_sha256),
        ("object_lock_minimum_retention_seconds", config.object_lock_minimum_retention_seconds),
        ("role_credential_identity_sha256", admission.role_credential_identity_sha256),
        ("role_iam_policy_sha256", admission.role_iam_policy_sha256),
        ("provider_route_iam_attestation_sha256", admission.provider_route_iam_attestation_sha256),
        ("object_lock_retention_proof_sha256", admission.object_lock_retention_proof_sha256),
        ("deployment_binding_sha256", admission.deployment_binding_sha256),
        ("delivery_binding_sha256", admission.delivery_binding_sha256),
        ("v2r_iam_catalog_sha256", config.admission_config.v2r_iam_catalog_sha256),
        ("evidence_sha256", _hash(value)),
        ("expires_at", expires),
        ("is_operational", False),
        ("writer_authorized", False),
        ("promotion_authorized", False),
        ("traffic_authorized", False),
        ("phase5_authorized", False),
        ("execution_authorized", False),
        ("full_matrix_authorized", False),
        ("full_matrix_executed", False),
    )
    result = VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence(
        configuration_sha256=resolved.configuration_sha256,
        capability=_CAPABILITY,
        **dict(public_values),
    )
    _VERIFIED_STATES[result] = _EvidenceFacts(
        configuration_sha256=resolved.configuration_sha256,
        canonical_evidence=value,
        public_values=public_values,
    )
    return result


def verify_physical_wal_v2r_provider_route_iam_object_lock_evidence(
    *,
    config: PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig,
    admission: object,
    provider_evidence: bytes,
    now: datetime,
) -> VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence:
    """Verify one role's fresh, exact, signed V2R provider evidence only."""

    observed = _utc(now, "V2R_PROVIDER_EVIDENCE_CLOCK_INVALID")
    return _parse(provider_evidence, resolved=_resolve(config, admission=admission, now=observed), now=observed)


def require_verified_physical_wal_v2r_provider_route_iam_object_lock_evidence(
    *,
    evidence: object,
    config: PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig,
    admission: object,
    now: datetime,
) -> VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence:
    """Recheck one opaque evidence capability against fresh role admission."""

    observed = _utc(now, "V2R_PROVIDER_EVIDENCE_CLOCK_INVALID")
    resolved = _resolve(config, admission=admission, now=observed)
    if (
        type(evidence) is not VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence
        or evidence._capability is not _CAPABILITY
    ):
        _fail("V2R_PROVIDER_EVIDENCE_CAPABILITY_INVALID")
    facts = _VERIFIED_STATES.get(evidence)
    if facts is None or facts.configuration_sha256 != resolved.configuration_sha256:
        _fail("V2R_PROVIDER_EVIDENCE_CAPABILITY_INVALID")
    if tuple((name, getattr(evidence, name)) for name, _value in facts.public_values) != facts.public_values:
        _fail("V2R_PROVIDER_EVIDENCE_CAPABILITY_INVALID")
    try:
        reverified = _parse(facts.canonical_evidence, resolved=resolved, now=observed)
    except PhysicalWalV2rProviderRouteIamObjectLockEvidenceError as exc:
        raise PhysicalWalV2rProviderRouteIamObjectLockEvidenceError(
            "V2R_PROVIDER_EVIDENCE_CAPABILITY_INVALID"
        ) from exc
    if reverified.evidence_sha256 != evidence.evidence_sha256:
        _fail("V2R_PROVIDER_EVIDENCE_CAPABILITY_INVALID")
    return reverified


def verify_physical_wal_v2r_provider_route_iam_object_lock_evidence_matrix(
    *,
    evidences: tuple[object, ...],
    configs: tuple[PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig, ...],
    admissions: tuple[object, ...],
    admission_configs: tuple[
        _mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig, ...
    ],
    now: datetime,
) -> VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidenceMatrix:
    """Require all eight exact V2R role attestations without provider action."""

    observed = _utc(now, "V2R_PROVIDER_EVIDENCE_CLOCK_INVALID")
    if (
        type(evidences) is not tuple
        or type(configs) is not tuple
        or type(admissions) is not tuple
        or type(admission_configs) is not tuple
        or len(evidences) != 8
        or len(configs) != 8
        or len(admissions) != 8
        or len(admission_configs) != 8
    ):
        _fail("V2R_PROVIDER_EVIDENCE_MATRIX_INCOMPLETE")
    try:
        admission_matrix = (
            _mailbox.verify_physical_wal_v2r_witness_roundtrip_control_mailbox_role_matrix(
                admissions=admissions,
                configs=admission_configs,
                now=observed,
            )
        )
    except _mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError as exc:
        raise PhysicalWalV2rProviderRouteIamObjectLockEvidenceError(
            "V2R_PROVIDER_EVIDENCE_MATRIX_ADMISSION_INVALID:" + exc.code
        ) from exc
    if any(
        type(config) is not PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig
        or type(admission_config)
        is not _mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig
        or config.admission_config is not admission_config
        for config, admission_config in zip(configs, admission_configs, strict=True)
    ):
        _fail("V2R_PROVIDER_EVIDENCE_MATRIX_ADMISSION_CONFIG_SUBSTITUTION")
    verified = tuple(
        require_verified_physical_wal_v2r_provider_route_iam_object_lock_evidence(
            evidence=evidence,
            config=config,
            admission=admission,
            now=observed,
        )
        for evidence, config, admission in zip(evidences, configs, admissions, strict=True)
    )
    expected = tuple(policy.local_role for policy in _mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES)
    if tuple(item.local_role for item in verified) != expected:
        _fail("V2R_PROVIDER_EVIDENCE_MATRIX_ROLE_SUBSTITUTION")
    digest = _hash(
        _canonical(
            {
                "schema": "gold-trade-physical-wal-v2r-provider-route-iam-object-lock-evidence-matrix-v1",
                "admission_role_matrix_sha256": admission_matrix.role_matrix_sha256,
                "roles": [
                    {
                        "local_site": item.local_site,
                        "local_role": item.local_role,
                        "mailbox": item.mailbox,
                        "direction": item.direction,
                        "object_prefix": item.object_prefix,
                        "least_privilege_actions": list(item.least_privilege_actions),
                        "allowed_provider_actions": list(item.allowed_provider_actions),
                        "provider_endpoint_sha256": item.provider_endpoint_sha256,
                        "provider_bucket_sha256": item.provider_bucket_sha256,
                        "provider_region_sha256": item.provider_region_sha256,
                        "role_credential_identity_sha256": item.role_credential_identity_sha256,
                        "role_iam_policy_sha256": item.role_iam_policy_sha256,
                        "provider_route_iam_attestation_sha256": item.provider_route_iam_attestation_sha256,
                        "object_lock_retention_proof_sha256": item.object_lock_retention_proof_sha256,
                        "evidence_sha256": item.evidence_sha256,
                    }
                    for item in verified
                ],
                "deployment_binding_sha256": admission_matrix.deployment_binding_sha256,
                "delivery_binding_sha256": admission_matrix.delivery_binding_sha256,
                "v2r_iam_catalog_sha256": admission_matrix.v2r_iam_catalog_sha256,
            },
            "V2R_PROVIDER_EVIDENCE_MATRIX_INVALID",
        )
    )
    return VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidenceMatrix(
        capability=_CAPABILITY,
        evidence_matrix_sha256=digest,
        deployment_binding_sha256=admission_matrix.deployment_binding_sha256,
        delivery_binding_sha256=admission_matrix.delivery_binding_sha256,
        v2r_iam_catalog_sha256=admission_matrix.v2r_iam_catalog_sha256,
        is_operational=False,
        writer_authorized=False,
        promotion_authorized=False,
        traffic_authorized=False,
        phase5_authorized=False,
        execution_authorized=False,
        full_matrix_authorized=False,
        full_matrix_executed=False,
    )
