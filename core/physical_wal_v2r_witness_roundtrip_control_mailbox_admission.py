"""Fresh, default-off admission boundary for the V2R Phase-5 control plane.

This is intentionally only a cryptographic *admission* grammar.  It does not
open Object Storage, read a credential, render a provider policy, or deliver a
mailbox record.  In particular, a verified result is not election, lease,
writer, promotion, traffic, Phase-5, or Full-Matrix authority.

V2R cannot reuse normal-V2 or recovery-data roles.  Each of its eight exact
roles is bound to one site, one route child-prefix, and one least-privilege
action tuple.  A future root-owned adapter must require the typed admission
below before it touches its role-local credential or provider client.
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

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core import physical_wal_v2r_witness_roundtrip_contract as v2r


__all__ = (
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_ADMISSION_SCHEMA",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES",
    "PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig",
    "PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError",
    "PhysicalWalV2rWitnessRoundtripControlMailboxPolicy",
    "VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission",
    "VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxRoleMatrix",
    "admit_physical_wal_v2r_witness_roundtrip_control_mailbox",
    "require_verified_physical_wal_v2r_witness_roundtrip_control_mailbox_admission",
    "verify_physical_wal_v2r_witness_roundtrip_control_mailbox_role_matrix",
)


PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_ADMISSION_SCHEMA = (
    "gold-trade-physical-wal-v2r-control-mailbox-admission-v1"
)
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_ADMISSION_DEFAULT_ENABLED = False
_ASSERTION_SCHEMA = "gold-trade-physical-wal-v2r-control-mailbox-host-role-assertion-v1"
_POLICY_SCHEMA = "gold-trade-physical-wal-v2r-control-mailbox-policy-v1"
_CONFIG_SCHEMA = "gold-trade-physical-wal-v2r-control-mailbox-config-v1"
_DOMAIN = b"gold-trade-physical-wal-v2r-control-mailbox-host-role-assertion-v1\x00"
_CAPABILITY = object()
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,127}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_ZERO = "0" * 64

_CREATE_ONLY = ("object:create-only-fixed-key", "object:read-own-exact-version-receipt")
_CONSUME = ("object:list-fixed-prefix", "object:read-exact-version")
_ROOT = "physical-wal-v2r-reverse"

# These vocabulary deny-pins intentionally live here rather than importing a
# normal/recovery module.  An import would create an accidental configuration
# or credential bridge between planes.
_NON_V2R_ROLES = frozenset({
    "fi-publisher", "ir-receiver", "ir-publisher", "fi-receiver",
    "fi-writer-source-outbox", "witness-fi-ingress", "witness-ir-egress",
    "ir-standby-ack-inbox", "ir-durable-ack-outbox", "witness-ir-ingress",
    "witness-fi-egress", "fi-writer-ack-inbox",
})


class PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError(code)


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripControlMailboxPolicy:
    local_site: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    least_privilege_actions: tuple[str, ...]


def _prefix(mailbox: str) -> str:
    return _ROOT + "/" + mailbox + "/"


# Exact, exhaustive V2R matrix.  It is deliberately not configurable.
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES = (
    PhysicalWalV2rWitnessRoundtripControlMailboxPolicy("wa-ir", "wa-ir-v2r-exporter", "ir-to-witness", "publish", _prefix("ir-to-witness"), _CREATE_ONLY),
    PhysicalWalV2rWitnessRoundtripControlMailboxPolicy("witness", "witness-v2r-reverse-ingress", "ir-to-witness", "consume", _prefix("ir-to-witness"), _CONSUME),
    PhysicalWalV2rWitnessRoundtripControlMailboxPolicy("witness", "witness-v2r-reverse-egress", "witness-to-fi", "publish", _prefix("witness-to-fi"), _CREATE_ONLY),
    PhysicalWalV2rWitnessRoundtripControlMailboxPolicy("wa-fi", "wa-fi-v2r-recovery-inbox", "witness-to-fi", "consume", _prefix("witness-to-fi"), _CONSUME),
    PhysicalWalV2rWitnessRoundtripControlMailboxPolicy("wa-fi", "wa-fi-v2r-ack-outbox", "fi-to-witness", "publish", _prefix("fi-to-witness"), _CREATE_ONLY),
    PhysicalWalV2rWitnessRoundtripControlMailboxPolicy("witness", "witness-v2r-ack-ingress", "fi-to-witness", "consume", _prefix("fi-to-witness"), _CONSUME),
    PhysicalWalV2rWitnessRoundtripControlMailboxPolicy("witness", "witness-v2r-return-egress", "witness-to-ir", "publish", _prefix("witness-to-ir"), _CREATE_ONLY),
    PhysicalWalV2rWitnessRoundtripControlMailboxPolicy("wa-ir", "wa-ir-v2r-return-inbox", "witness-to-ir", "consume", _prefix("witness-to-ir"), _CONSUME),
)
_POLICY_BY_ROLE = {item.local_role: item for item in PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES}


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig:
    """Root-supplied, per-role pins; no credential material is accepted."""

    host_id: str = ""
    local_site: str = ""
    local_role: str = ""
    release_sha256: str = ""
    deployment_binding_sha256: str = ""
    delivery_binding_sha256: str = ""
    v2r_iam_catalog_sha256: str = ""
    role_credential_identity_sha256: str = ""
    non_v2r_credential_identity_sha256s: tuple[str, ...] = ()
    host_role_authority_public_key: bytes | None = field(default=None, repr=False)
    enabled: bool = PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_ADMISSION_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = 300


@dataclass(frozen=True, init=False)
class VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission:
    """Non-serializable role-local admission for a future fixed adapter."""

    local_site: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    least_privilege_actions: tuple[str, ...]
    role_credential_identity_sha256: str
    role_iam_policy_sha256: str
    provider_route_iam_attestation_sha256: str
    object_lock_retention_proof_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    assertion_sha256: str
    expires_at: datetime
    _configuration_sha256: str = field(repr=False)
    _capability: object = field(repr=False, compare=False)

    def __init__(self, *, configuration_sha256: str, capability: object, **values: Any) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2R_CONTROL_MAILBOX_ADMISSION_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_configuration_sha256", configuration_sha256)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_CONTROL_MAILBOX_ADMISSION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, init=False)
class VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxRoleMatrix:
    """A complete eight-role topology check, not a deployment authorization."""

    role_matrix_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    v2r_iam_catalog_sha256: str
    _capability: object = field(repr=False, compare=False)

    def __init__(self, *, role_matrix_sha256: str, deployment_binding_sha256: str, delivery_binding_sha256: str, v2r_iam_catalog_sha256: str, capability: object) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2R_CONTROL_MAILBOX_ROLE_MATRIX_CONSTRUCTION_FORBIDDEN")
        object.__setattr__(self, "role_matrix_sha256", role_matrix_sha256)
        object.__setattr__(self, "deployment_binding_sha256", deployment_binding_sha256)
        object.__setattr__(self, "delivery_binding_sha256", delivery_binding_sha256)
        object.__setattr__(self, "v2r_iam_catalog_sha256", v2r_iam_catalog_sha256)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_CONTROL_MAILBOX_ROLE_MATRIX_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ResolvedConfig:
    config: PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig
    policy: PhysicalWalV2rWitnessRoundtripControlMailboxPolicy
    authority: Ed25519PublicKey
    configuration_sha256: str


def _canonical(value: object, code: str) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError(code) from exc


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(value: object, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None or value == _ZERO:
        _fail(code)
    return value


def _policy_sha(policy: PhysicalWalV2rWitnessRoundtripControlMailboxPolicy) -> str:
    return _hash(_canonical({"schema": _POLICY_SCHEMA, "protocol_domain": v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN, "mailbox_prefix": v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX, "local_site": policy.local_site, "local_role": policy.local_role, "mailbox": policy.mailbox, "direction": policy.direction, "object_prefix": policy.object_prefix, "least_privilege_actions": list(policy.least_privilege_actions)}, "V2R_CONTROL_MAILBOX_POLICY_INVALID"))


def _timestamp(value: object, code: str) -> datetime:
    if type(value) is not str or _TIME_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail(code)


def _utc(value: object, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value) or value.microsecond:
        _fail(code)
    return value


def _resolve(value: object) -> _ResolvedConfig:
    if type(value) is not PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig:
        _fail("V2R_CONTROL_MAILBOX_CONFIG_INVALID")
    config = value
    try:
        policy = _POLICY_BY_ROLE[config.local_role]
    except (KeyError, TypeError):
        _fail("V2R_CONTROL_MAILBOX_ROLE_INVALID")
    if (config.enabled is not True or config.local_site != policy.local_site or type(config.host_id) is not str or _HOST_RE.fullmatch(config.host_id) is None or type(config.maximum_evidence_age_seconds) is not int or not 1 <= config.maximum_evidence_age_seconds <= 3600):
        _fail("V2R_CONTROL_MAILBOX_CONFIG_INVALID")
    for field in ("release_sha256", "deployment_binding_sha256", "delivery_binding_sha256", "v2r_iam_catalog_sha256", "role_credential_identity_sha256"):
        _sha(getattr(config, field), "V2R_CONTROL_MAILBOX_CONFIG_INVALID")
    denied = config.non_v2r_credential_identity_sha256s
    # Four recovery-data and eight normal-V2 identities must be supplied as
    # explicit deny-pins.  A shorter list could silently omit a colocated
    # legacy credential, so it is not a meaningful non-alias assertion.
    if type(denied) is not tuple or len(denied) != 12 or len(set(denied)) != len(denied) or any(_sha(item, "V2R_CONTROL_MAILBOX_CONFIG_INVALID") != item for item in denied) or config.role_credential_identity_sha256 in denied:
        _fail("V2R_CONTROL_MAILBOX_CREDENTIAL_NON_ALIAS_REQUIRED")
    if type(config.host_role_authority_public_key) is not bytes or len(config.host_role_authority_public_key) != 32:
        _fail("V2R_CONTROL_MAILBOX_CONFIG_INVALID")
    try:
        authority = Ed25519PublicKey.from_public_bytes(config.host_role_authority_public_key)
    except ValueError:
        _fail("V2R_CONTROL_MAILBOX_CONFIG_INVALID")
    digest = _hash(_canonical({"schema": _CONFIG_SCHEMA, "host_id": config.host_id, "local_site": config.local_site, "local_role": config.local_role, "release_sha256": config.release_sha256, "deployment_binding_sha256": config.deployment_binding_sha256, "delivery_binding_sha256": config.delivery_binding_sha256, "v2r_iam_catalog_sha256": config.v2r_iam_catalog_sha256, "role_credential_identity_sha256": config.role_credential_identity_sha256, "non_v2r_credential_identity_sha256s": list(config.non_v2r_credential_identity_sha256s), "authority_public_key_base64": base64.b64encode(config.host_role_authority_public_key).decode("ascii"), "policy_sha256": _policy_sha(policy), "maximum_evidence_age_seconds": config.maximum_evidence_age_seconds}, "V2R_CONTROL_MAILBOX_CONFIG_INVALID"))
    return _ResolvedConfig(config, policy, authority, digest)


_FIELDS = frozenset({"schema", "version", "protocol_domain", "mailbox_prefix", "host_id", "local_site", "local_role", "mailbox", "direction", "object_prefix", "least_privilege_actions", "policy_sha256", "release_sha256", "deployment_binding_sha256", "delivery_binding_sha256", "v2r_iam_catalog_sha256", "role_credential_identity_sha256", "role_iam_policy_sha256", "provider_route_iam_attestation_sha256", "object_lock_retention_proof_sha256", "assertion_id", "assertion_nonce", "issued_at", "expires_at", "signature_base64"})


def _parse_assertion(value: object, resolved: _ResolvedConfig, now: datetime) -> VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission:
    if type(value) is not bytes or not 1 <= len(value) <= 128 * 1024:
        _fail("V2R_CONTROL_MAILBOX_ASSERTION_INVALID")
    try:
        item = json.loads(value.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("V2R_CONTROL_MAILBOX_ASSERTION_INVALID")
    if type(item) is not dict or set(item) != _FIELDS or _canonical(item, "V2R_CONTROL_MAILBOX_ASSERTION_INVALID") != value:
        _fail("V2R_CONTROL_MAILBOX_ASSERTION_INVALID")
    raw = dict(item)
    signature_text = raw.pop("signature_base64")
    if type(signature_text) is not str:
        _fail("V2R_CONTROL_MAILBOX_ASSERTION_INVALID")
    try:
        signature = base64.b64decode(signature_text.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail("V2R_CONTROL_MAILBOX_ASSERTION_INVALID")
    if len(signature) != 64:
        _fail("V2R_CONTROL_MAILBOX_ASSERTION_INVALID")
    try:
        resolved.authority.verify(signature, _DOMAIN + _canonical(raw, "V2R_CONTROL_MAILBOX_ASSERTION_INVALID"))
    except InvalidSignature:
        _fail("V2R_CONTROL_MAILBOX_ASSERTION_SIGNATURE_INVALID")
    policy, config = resolved.policy, resolved.config
    exact = {"schema": _ASSERTION_SCHEMA, "version": 1, "protocol_domain": v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN, "mailbox_prefix": v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX, "host_id": config.host_id, "local_site": policy.local_site, "local_role": policy.local_role, "mailbox": policy.mailbox, "direction": policy.direction, "object_prefix": policy.object_prefix, "least_privilege_actions": list(policy.least_privilege_actions), "policy_sha256": _policy_sha(policy), "release_sha256": config.release_sha256, "deployment_binding_sha256": config.deployment_binding_sha256, "delivery_binding_sha256": config.delivery_binding_sha256, "v2r_iam_catalog_sha256": config.v2r_iam_catalog_sha256, "role_credential_identity_sha256": config.role_credential_identity_sha256}
    if any(item[name] != expected for name, expected in exact.items()) or type(item["assertion_id"]) is not str or _ID_RE.fullmatch(item["assertion_id"]) is None or type(item["assertion_nonce"]) is not str or _NONCE_RE.fullmatch(item["assertion_nonce"]) is None:
        _fail("V2R_CONTROL_MAILBOX_ASSERTION_CROSS_PIN_MISMATCH")
    for name in ("role_iam_policy_sha256", "provider_route_iam_attestation_sha256", "object_lock_retention_proof_sha256"):
        _sha(item[name], "V2R_CONTROL_MAILBOX_ASSERTION_INVALID")
    issued, expires, observed = _timestamp(item["issued_at"], "V2R_CONTROL_MAILBOX_ASSERTION_TIME_INVALID"), _timestamp(item["expires_at"], "V2R_CONTROL_MAILBOX_ASSERTION_TIME_INVALID"), _utc(now, "V2R_CONTROL_MAILBOX_CLOCK_INVALID")
    if issued > observed or expires <= observed or expires <= issued or (expires - issued).total_seconds() > config.maximum_evidence_age_seconds:
        _fail("V2R_CONTROL_MAILBOX_ASSERTION_STALE")
    return VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission(configuration_sha256=resolved.configuration_sha256, capability=_CAPABILITY, local_site=policy.local_site, local_role=policy.local_role, mailbox=policy.mailbox, direction=policy.direction, object_prefix=policy.object_prefix, least_privilege_actions=policy.least_privilege_actions, role_credential_identity_sha256=config.role_credential_identity_sha256, role_iam_policy_sha256=item["role_iam_policy_sha256"], provider_route_iam_attestation_sha256=item["provider_route_iam_attestation_sha256"], object_lock_retention_proof_sha256=item["object_lock_retention_proof_sha256"], deployment_binding_sha256=config.deployment_binding_sha256, delivery_binding_sha256=config.delivery_binding_sha256, assertion_sha256=_hash(value), expires_at=expires)


def admit_physical_wal_v2r_witness_roundtrip_control_mailbox(*, config: PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig, host_role_assertion: bytes, now: datetime) -> VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission:
    """Verify exactly one fresh V2R role assertion; no provider action occurs."""
    return _parse_assertion(host_role_assertion, _resolve(config), now)


def require_verified_physical_wal_v2r_witness_roundtrip_control_mailbox_admission(*, admission: object, config: PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig, now: datetime) -> VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission:
    resolved, observed = _resolve(config), _utc(now, "V2R_CONTROL_MAILBOX_CLOCK_INVALID")
    if type(admission) is not VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission or admission._capability is not _CAPABILITY or admission._configuration_sha256 != resolved.configuration_sha256 or admission.local_role != resolved.policy.local_role or admission.local_site != resolved.policy.local_site or admission.mailbox != resolved.policy.mailbox or admission.direction != resolved.policy.direction or admission.object_prefix != resolved.policy.object_prefix or admission.least_privilege_actions != resolved.policy.least_privilege_actions or admission.role_credential_identity_sha256 != resolved.config.role_credential_identity_sha256 or admission.expires_at <= observed:
        _fail("V2R_CONTROL_MAILBOX_ADMISSION_CAPABILITY_INVALID")
    return admission


def verify_physical_wal_v2r_witness_roundtrip_control_mailbox_role_matrix(*, admissions: tuple[object, ...], configs: tuple[PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig, ...], now: datetime) -> VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxRoleMatrix:
    """Require all eight fresh role admissions before a future deployment seam.

    This validates topology and identity separation only.  It neither starts a
    service nor returns an Object-Storage client/credential.
    """
    if type(admissions) is not tuple or type(configs) is not tuple or len(admissions) != 8 or len(configs) != 8:
        _fail("V2R_CONTROL_MAILBOX_ROLE_MATRIX_INCOMPLETE")
    verified = tuple(require_verified_physical_wal_v2r_witness_roundtrip_control_mailbox_admission(admission=item, config=config, now=now) for item, config in zip(admissions, configs, strict=True))
    expected_roles = tuple(item.local_role for item in PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES)
    if tuple(item.local_role for item in verified) != expected_roles or any(item.local_role in _NON_V2R_ROLES for item in verified):
        _fail("V2R_CONTROL_MAILBOX_ROLE_MATRIX_ROLE_SUBSTITUTION")
    identities = tuple(item.role_credential_identity_sha256 for item in verified)
    if len(set(identities)) != 8:
        _fail("V2R_CONTROL_MAILBOX_ROLE_MATRIX_CREDENTIAL_ALIAS")
    deployment = {item.deployment_binding_sha256 for item in verified}
    delivery = {item.delivery_binding_sha256 for item in verified}
    catalogs = {_resolve(config).config.v2r_iam_catalog_sha256 for config in configs}
    if len(deployment) != 1 or len(delivery) != 1 or len(catalogs) != 1:
        _fail("V2R_CONTROL_MAILBOX_ROLE_MATRIX_BINDING_MISMATCH")
    digest = _hash(_canonical({"schema": "gold-trade-physical-wal-v2r-control-mailbox-role-matrix-v1", "roles": [{"local_site": item.local_site, "local_role": item.local_role, "mailbox": item.mailbox, "direction": item.direction, "object_prefix": item.object_prefix, "credential_identity_sha256": item.role_credential_identity_sha256, "role_iam_policy_sha256": item.role_iam_policy_sha256, "provider_route_iam_attestation_sha256": item.provider_route_iam_attestation_sha256, "object_lock_retention_proof_sha256": item.object_lock_retention_proof_sha256, "assertion_sha256": item.assertion_sha256} for item in verified], "deployment_binding_sha256": next(iter(deployment)), "delivery_binding_sha256": next(iter(delivery)), "v2r_iam_catalog_sha256": next(iter(catalogs))}, "V2R_CONTROL_MAILBOX_ROLE_MATRIX_INVALID"))
    return VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxRoleMatrix(role_matrix_sha256=digest, deployment_binding_sha256=next(iter(deployment)), delivery_binding_sha256=next(iter(delivery)), v2r_iam_catalog_sha256=next(iter(catalogs)), capability=_CAPABILITY)
