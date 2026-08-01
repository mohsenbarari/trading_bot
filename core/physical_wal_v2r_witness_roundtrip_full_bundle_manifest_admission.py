"""Fail-closed public V2R full-bundle and site-manifest admission.

This module is deliberately a verification boundary only.  It never reads a
credential, renders a provider policy, opens Object Storage, starts a service,
or creates a deployment.  The signed bundle contains public hashes and the
eight exact V2R role projections; a verified result is *not* election, lease,
writer, promotion, traffic, Phase-5 success, or Full-Matrix authority.
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

from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as mailbox
from core import physical_wal_v2r_witness_roundtrip_contract as v2r


__all__ = (
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_FULL_BUNDLE_MANIFEST_ADMISSION_DEFAULT_ENABLED",
    "PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig",
    "PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError",
    "PhysicalWalV2rWitnessRoundtripSiteManifestAdmissionConfig",
    "VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle",
    "VerifiedPhysicalWalV2rWitnessRoundtripSiteManifestAdmission",
    "admit_physical_wal_v2r_witness_roundtrip_public_full_bundle",
    "admit_physical_wal_v2r_witness_roundtrip_site_manifest",
    "require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle",
    "require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle_site_manifest_slice",
)


PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_FULL_BUNDLE_MANIFEST_ADMISSION_DEFAULT_ENABLED = False
_BUNDLE_SCHEMA = "gold-trade-physical-wal-v2r-public-full-bundle-v1"
_MANIFEST_SCHEMA = "gold-trade-physical-wal-v2r-public-site-manifest-v1"
_BUNDLE_DOMAIN = b"gold-trade-physical-wal-v2r-public-full-bundle-v1\x00"
_CAPABILITY = object()
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_ZERO = "0" * 64
_SITES = {"wa-fi", "wa-ir", "witness"}
_BUNDLE_FIELDS = frozenset({"schema", "version", "protocol_domain", "mailbox_prefix", "bundle_id", "bundle_nonce", "release_sha256", "deployment_binding_sha256", "delivery_binding_sha256", "v2r_iam_catalog_sha256", "role_matrix_sha256", "roles", "issued_at", "expires_at", "signature_base64"})
_MANIFEST_FIELDS = frozenset({"schema", "version", "site", "release_sha256", "deployment_binding_sha256", "delivery_binding_sha256", "v2r_iam_catalog_sha256", "full_bundle_sha256", "roles"})
_ROLE_FIELDS = frozenset({"local_site", "local_role", "mailbox", "direction", "object_prefix", "least_privilege_actions", "role_credential_identity_sha256", "role_iam_policy_sha256", "provider_route_iam_attestation_sha256", "object_lock_retention_proof_sha256", "assertion_sha256"})


class PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError(code)


def _canonical(value: object, code: str) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError(code) from exc


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(value: object, code: str) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None or value == _ZERO:
        _fail(code)
    return value


def _utc(value: object, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value) or value.microsecond:
        _fail(code)
    return value


def _timestamp(value: object, code: str) -> datetime:
    if type(value) is not str or _TIME_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail(code)


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig:
    """Public pins for one fresh eight-role V2R bundle; disabled by default."""

    release_sha256: str = ""
    deployment_binding_sha256: str = ""
    delivery_binding_sha256: str = ""
    v2r_iam_catalog_sha256: str = ""
    bundle_authority_public_key: bytes | None = field(default=None, repr=False)
    enabled: bool = PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_FULL_BUNDLE_MANIFEST_ADMISSION_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = 300


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripSiteManifestAdmissionConfig:
    """Pins a public per-site manifest to one verified public bundle."""

    expected_site: str = ""
    expected_manifest_sha256: str = ""
    enabled: bool = PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_FULL_BUNDLE_MANIFEST_ADMISSION_DEFAULT_ENABLED


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle:
    bundle_id: str
    release_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    v2r_iam_catalog_sha256: str
    role_matrix_sha256: str
    full_bundle_sha256: str
    expires_at: datetime
    is_operational: bool
    authorizes_phase5: bool
    authorizes_full_matrix: bool
    _configuration_sha256: str = field(repr=False)
    _role_digest: str = field(repr=False)
    _roles_canonical: bytes = field(repr=False)
    _capability: object = field(repr=False, compare=False)

    def __init__(self, *, configuration_sha256: str, role_digest: str, roles_canonical: bytes, capability: object, **values: Any) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2R_PUBLIC_FULL_BUNDLE_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_configuration_sha256", configuration_sha256)
        object.__setattr__(self, "_role_digest", role_digest)
        object.__setattr__(self, "_roles_canonical", roles_canonical)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_PUBLIC_FULL_BUNDLE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, init=False)
class VerifiedPhysicalWalV2rWitnessRoundtripSiteManifestAdmission:
    site: str
    manifest_sha256: str
    full_bundle_sha256: str
    is_operational: bool
    authorizes_phase5: bool
    authorizes_full_matrix: bool
    _capability: object = field(repr=False, compare=False)

    def __init__(self, *, capability: object, **values: Any) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2R_SITE_MANIFEST_ADMISSION_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_SITE_MANIFEST_ADMISSION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ResolvedBundleConfig:
    config: PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig
    authority: Ed25519PublicKey
    digest: str


@dataclass(frozen=True)
class _VerifiedPublicFullBundleFacts:
    """Private same-process seal for a bundle admitted from canonical bytes."""

    configuration_sha256: str
    role_digest: str
    roles_canonical: bytes
    public_values: tuple[tuple[str, object], ...]


_VERIFIED_PUBLIC_FULL_BUNDLE_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle,
    _VerifiedPublicFullBundleFacts,
] = WeakKeyDictionary()


def _resolve_bundle_config(value: object) -> _ResolvedBundleConfig:
    if type(value) is not PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig:
        _fail("V2R_PUBLIC_FULL_BUNDLE_CONFIG_INVALID")
    config = value
    if config.enabled is not True or type(config.maximum_evidence_age_seconds) is not int or not 1 <= config.maximum_evidence_age_seconds <= 3600:
        _fail("V2R_PUBLIC_FULL_BUNDLE_CONFIG_INVALID")
    for field_name in ("release_sha256", "deployment_binding_sha256", "delivery_binding_sha256", "v2r_iam_catalog_sha256"):
        _sha(getattr(config, field_name), "V2R_PUBLIC_FULL_BUNDLE_CONFIG_INVALID")
    if type(config.bundle_authority_public_key) is not bytes or len(config.bundle_authority_public_key) != 32:
        _fail("V2R_PUBLIC_FULL_BUNDLE_CONFIG_INVALID")
    try:
        authority = Ed25519PublicKey.from_public_bytes(config.bundle_authority_public_key)
    except ValueError:
        _fail("V2R_PUBLIC_FULL_BUNDLE_CONFIG_INVALID")
    digest = _hash(_canonical({"schema": _BUNDLE_SCHEMA, "release_sha256": config.release_sha256, "deployment_binding_sha256": config.deployment_binding_sha256, "delivery_binding_sha256": config.delivery_binding_sha256, "v2r_iam_catalog_sha256": config.v2r_iam_catalog_sha256, "bundle_authority_public_key_base64": base64.b64encode(config.bundle_authority_public_key).decode("ascii"), "maximum_evidence_age_seconds": config.maximum_evidence_age_seconds}, "V2R_PUBLIC_FULL_BUNDLE_CONFIG_INVALID"))
    return _ResolvedBundleConfig(config, authority, digest)


def _role_projection(grant: mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission) -> dict[str, object]:
    return {"local_site": grant.local_site, "local_role": grant.local_role, "mailbox": grant.mailbox, "direction": grant.direction, "object_prefix": grant.object_prefix, "least_privilege_actions": list(grant.least_privilege_actions), "role_credential_identity_sha256": grant.role_credential_identity_sha256, "role_iam_policy_sha256": grant.role_iam_policy_sha256, "provider_route_iam_attestation_sha256": grant.provider_route_iam_attestation_sha256, "object_lock_retention_proof_sha256": grant.object_lock_retention_proof_sha256, "assertion_sha256": grant.assertion_sha256}


def _checked_live_roles(*, admissions: object, configs: object, now: datetime) -> tuple[tuple[mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission, ...], mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxRoleMatrix]:
    if type(admissions) is not tuple or type(configs) is not tuple or len(admissions) != 8 or len(configs) != 8:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ROLE_MATRIX_INCOMPLETE")
    try:
        matrix = mailbox.verify_physical_wal_v2r_witness_roundtrip_control_mailbox_role_matrix(admissions=admissions, configs=configs, now=now)
        verified = tuple(mailbox.require_verified_physical_wal_v2r_witness_roundtrip_control_mailbox_admission(admission=item, config=config, now=now) for item, config in zip(admissions, configs, strict=True))
    except mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError as exc:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ROLE_MATRIX_INVALID:" + exc.code)
    return verified, matrix


def _parse_bundle(value: object, *, resolved: _ResolvedBundleConfig, roles: tuple[mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission, ...], matrix: mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxRoleMatrix, now: datetime) -> VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle:
    if type(value) is not bytes or not 1 <= len(value) <= 128 * 1024:
        _fail("V2R_PUBLIC_FULL_BUNDLE_INVALID")
    try:
        item = json.loads(value.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("V2R_PUBLIC_FULL_BUNDLE_INVALID")
    if type(item) is not dict or set(item) != _BUNDLE_FIELDS or _canonical(item, "V2R_PUBLIC_FULL_BUNDLE_INVALID") != value:
        _fail("V2R_PUBLIC_FULL_BUNDLE_INVALID")
    raw = dict(item)
    signature_text = raw.pop("signature_base64")
    if type(signature_text) is not str:
        _fail("V2R_PUBLIC_FULL_BUNDLE_INVALID")
    try:
        signature = base64.b64decode(signature_text.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail("V2R_PUBLIC_FULL_BUNDLE_INVALID")
    if len(signature) != 64:
        _fail("V2R_PUBLIC_FULL_BUNDLE_INVALID")
    try:
        resolved.authority.verify(signature, _BUNDLE_DOMAIN + _canonical(raw, "V2R_PUBLIC_FULL_BUNDLE_INVALID"))
    except InvalidSignature:
        _fail("V2R_PUBLIC_FULL_BUNDLE_SIGNATURE_INVALID")
    exact = {"schema": _BUNDLE_SCHEMA, "version": 1, "protocol_domain": v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN, "mailbox_prefix": v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX, "release_sha256": resolved.config.release_sha256, "deployment_binding_sha256": resolved.config.deployment_binding_sha256, "delivery_binding_sha256": resolved.config.delivery_binding_sha256, "v2r_iam_catalog_sha256": resolved.config.v2r_iam_catalog_sha256, "role_matrix_sha256": matrix.role_matrix_sha256}
    if any(item[name] != expected for name, expected in exact.items()) or type(item["bundle_id"]) is not str or _ID_RE.fullmatch(item["bundle_id"]) is None or type(item["bundle_nonce"]) is not str or _NONCE_RE.fullmatch(item["bundle_nonce"]) is None:
        _fail("V2R_PUBLIC_FULL_BUNDLE_CROSS_PIN_MISMATCH")
    if type(item["roles"]) is not list or len(item["roles"]) != 8 or any(type(role) is not dict or set(role) != _ROLE_FIELDS for role in item["roles"]):
        _fail("V2R_PUBLIC_FULL_BUNDLE_ROLE_PROJECTIONS_INVALID")
    expected_roles = [_role_projection(role) for role in roles]
    if item["roles"] != expected_roles:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ROLE_SUBSTITUTION")
    identities = [role["role_credential_identity_sha256"] for role in item["roles"]]
    if len(set(identities)) != 8 or any(_sha(value, "V2R_PUBLIC_FULL_BUNDLE_ROLE_PROJECTIONS_INVALID") != value for role in item["roles"] for value in (role["role_credential_identity_sha256"], role["role_iam_policy_sha256"], role["provider_route_iam_attestation_sha256"], role["object_lock_retention_proof_sha256"], role["assertion_sha256"])):
        _fail("V2R_PUBLIC_FULL_BUNDLE_IDENTITY_ALIAS")
    issued, expires, observed = _timestamp(item["issued_at"], "V2R_PUBLIC_FULL_BUNDLE_TIME_INVALID"), _timestamp(item["expires_at"], "V2R_PUBLIC_FULL_BUNDLE_TIME_INVALID"), _utc(now, "V2R_PUBLIC_FULL_BUNDLE_CLOCK_INVALID")
    if issued > observed or expires <= observed or expires <= issued or (expires - issued).total_seconds() > resolved.config.maximum_evidence_age_seconds:
        _fail("V2R_PUBLIC_FULL_BUNDLE_STALE")
    canonical_roles = _canonical(item["roles"], "V2R_PUBLIC_FULL_BUNDLE_INVALID")
    role_digest = _hash(canonical_roles)
    public_values = (
        ("bundle_id", item["bundle_id"]),
        ("release_sha256", item["release_sha256"]),
        ("deployment_binding_sha256", item["deployment_binding_sha256"]),
        ("delivery_binding_sha256", item["delivery_binding_sha256"]),
        ("v2r_iam_catalog_sha256", item["v2r_iam_catalog_sha256"]),
        ("role_matrix_sha256", item["role_matrix_sha256"]),
        ("full_bundle_sha256", _hash(value)),
        ("expires_at", expires),
        ("is_operational", False),
        ("authorizes_phase5", False),
        ("authorizes_full_matrix", False),
    )
    result = VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle(
        configuration_sha256=resolved.digest,
        role_digest=role_digest,
        roles_canonical=canonical_roles,
        capability=_CAPABILITY,
        **dict(public_values),
    )
    _VERIFIED_PUBLIC_FULL_BUNDLE_STATES[result] = _VerifiedPublicFullBundleFacts(
        configuration_sha256=resolved.digest,
        role_digest=role_digest,
        roles_canonical=canonical_roles,
        public_values=public_values,
    )
    return result


def admit_physical_wal_v2r_witness_roundtrip_public_full_bundle(*, full_bundle: bytes, config: PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig, role_admissions: tuple[object, ...], role_configs: tuple[mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig, ...], now: datetime) -> VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle:
    """Admit one fresh signed public bundle against eight fresh local grants."""
    resolved = _resolve_bundle_config(config)
    roles, matrix = _checked_live_roles(admissions=role_admissions, configs=role_configs, now=now)
    return _parse_bundle(full_bundle, resolved=resolved, roles=roles, matrix=matrix, now=now)


def _require_live_public_full_bundle(
    *,
    full_bundle: object,
    now: datetime,
    resolved: _ResolvedBundleConfig | None,
) -> VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle:
    """Recheck an admitted same-process bundle against its private seal."""

    observed = _utc(now, "V2R_PUBLIC_FULL_BUNDLE_CLOCK_INVALID")
    if (
        type(full_bundle) is not VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle
        or full_bundle._capability is not _CAPABILITY
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_CAPABILITY_INVALID")
    facts = _VERIFIED_PUBLIC_FULL_BUNDLE_STATES.get(full_bundle)
    if facts is None:
        _fail("V2R_PUBLIC_FULL_BUNDLE_CAPABILITY_INVALID")
    if (
        full_bundle._configuration_sha256 != facts.configuration_sha256
        or full_bundle._role_digest != facts.role_digest
        or full_bundle._roles_canonical != facts.roles_canonical
        or any(getattr(full_bundle, name) != value for name, value in facts.public_values)
        or full_bundle.expires_at <= observed
        or full_bundle.is_operational is not False
        or full_bundle.authorizes_phase5 is not False
        or full_bundle.authorizes_full_matrix is not False
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_CAPABILITY_INVALID")
    if resolved is not None and (
        facts.configuration_sha256 != resolved.digest
        or full_bundle.release_sha256 != resolved.config.release_sha256
        or full_bundle.deployment_binding_sha256
        != resolved.config.deployment_binding_sha256
        or full_bundle.delivery_binding_sha256
        != resolved.config.delivery_binding_sha256
        or full_bundle.v2r_iam_catalog_sha256 != resolved.config.v2r_iam_catalog_sha256
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_CAPABILITY_INVALID")
    return full_bundle


def require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle(
    *,
    full_bundle: object,
    config: PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle:
    """Require a fresh public bundle against its original admission config."""

    return _require_live_public_full_bundle(
        full_bundle=full_bundle,
        now=now,
        resolved=_resolve_bundle_config(config),
    )


def require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle_site_manifest_slice(
    *,
    full_bundle: object,
    site: str,
    now: datetime,
) -> tuple[
    VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle,
    tuple[dict[str, object], ...],
]:
    """Derive one exact public 2/2/4 site slice from an admitted bundle.

    This intentionally accepts no bundle admission config or raw role input.
    It is for a pure V2R public-manifest renderer only: the private
    same-process seal proves that the opaque bundle was already admitted and
    keeps an in-memory role/prefix/identity rewrite from becoming renderable.
    The returned role dictionaries are freshly decoded public projections;
    they are not credentials, IAM grants, provider facts, or runtime input.
    """

    if type(site) is not str or site not in _SITES:
        _fail("V2R_PUBLIC_FULL_BUNDLE_SITE_MANIFEST_SLICE_SITE_INVALID")
    bundle = _require_live_public_full_bundle(
        full_bundle=full_bundle,
        now=now,
        resolved=None,
    )
    facts = _VERIFIED_PUBLIC_FULL_BUNDLE_STATES.get(bundle)
    if facts is None:
        _fail("V2R_PUBLIC_FULL_BUNDLE_CAPABILITY_INVALID")
    try:
        roles = json.loads(facts.roles_canonical.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("V2R_PUBLIC_FULL_BUNDLE_CAPABILITY_INVALID")
    if (
        type(roles) is not list
        or len(roles) != 8
        or any(type(role) is not dict or set(role) != _ROLE_FIELDS for role in roles)
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_CAPABILITY_INVALID")
    selected = [role for role in roles if role["local_site"] == site]
    policies = tuple(
        policy
        for policy in mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES
        if policy.local_site == site
    )
    expected = [
        (
            policy.local_site,
            policy.local_role,
            policy.mailbox,
            policy.direction,
            policy.object_prefix,
            list(policy.least_privilege_actions),
        )
        for policy in policies
    ]
    actual = [
        (
            role["local_site"],
            role["local_role"],
            role["mailbox"],
            role["direction"],
            role["object_prefix"],
            role["least_privilege_actions"],
        )
        for role in selected
    ]
    if (
        len(selected) != len(policies)
        or actual != expected
        or len({role["role_credential_identity_sha256"] for role in selected})
        != len(selected)
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_SITE_MANIFEST_SLICE_ROLE_SUBSTITUTION")
    for role in selected:
        for field_name in (
            "role_credential_identity_sha256",
            "role_iam_policy_sha256",
            "provider_route_iam_attestation_sha256",
            "object_lock_retention_proof_sha256",
            "assertion_sha256",
        ):
            _sha(
                role[field_name],
                "V2R_PUBLIC_FULL_BUNDLE_SITE_MANIFEST_SLICE_ROLE_SUBSTITUTION",
            )
    return bundle, tuple(
        {
            **role,
            "least_privilege_actions": list(role["least_privilege_actions"]),
        }
        for role in selected
    )


def admit_physical_wal_v2r_witness_roundtrip_site_manifest(*, manifest: bytes, config: PhysicalWalV2rWitnessRoundtripSiteManifestAdmissionConfig, full_bundle: object, full_bundle_config: PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig, now: datetime) -> VerifiedPhysicalWalV2rWitnessRoundtripSiteManifestAdmission:
    """Check a canonical public site projection; it cannot start a service."""
    if type(config) is not PhysicalWalV2rWitnessRoundtripSiteManifestAdmissionConfig or config.enabled is not True or config.expected_site not in _SITES or _sha(config.expected_manifest_sha256, "V2R_SITE_MANIFEST_CONFIG_INVALID") != config.expected_manifest_sha256:
        _fail("V2R_SITE_MANIFEST_CONFIG_INVALID")
    bundle = require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle(full_bundle=full_bundle, config=full_bundle_config, now=now)
    if type(manifest) is not bytes or _hash(manifest) != config.expected_manifest_sha256:
        _fail("V2R_SITE_MANIFEST_HASH_MISMATCH")
    try:
        item = json.loads(manifest.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("V2R_SITE_MANIFEST_INVALID")
    if type(item) is not dict or set(item) != _MANIFEST_FIELDS or _canonical(item, "V2R_SITE_MANIFEST_INVALID") != manifest:
        _fail("V2R_SITE_MANIFEST_INVALID")
    exact = {"schema": _MANIFEST_SCHEMA, "version": 1, "site": config.expected_site, "release_sha256": bundle.release_sha256, "deployment_binding_sha256": bundle.deployment_binding_sha256, "delivery_binding_sha256": bundle.delivery_binding_sha256, "v2r_iam_catalog_sha256": bundle.v2r_iam_catalog_sha256, "full_bundle_sha256": bundle.full_bundle_sha256}
    if any(item[name] != expected for name, expected in exact.items()) or type(item["roles"]) is not list:
        _fail("V2R_SITE_MANIFEST_CROSS_PIN_MISMATCH")
    # The role objects in a manifest are intentionally public projections, but
    # must be exactly the site's slice of the signed bundle (including every
    # identity/IAM/retention/assertion hash), not merely a matching topology.
    policies = tuple(policy for policy in mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES if policy.local_site == config.expected_site)
    if len(item["roles"]) != len(policies) or any(type(role) is not dict or set(role) != _ROLE_FIELDS for role in item["roles"]):
        _fail("V2R_SITE_MANIFEST_ROLE_SUBSTITUTION")
    expected_roles = [(policy.local_site, policy.local_role, policy.mailbox, policy.direction, policy.object_prefix, list(policy.least_privilege_actions)) for policy in policies]
    actual_roles = [(role["local_site"], role["local_role"], role["mailbox"], role["direction"], role["object_prefix"], role["least_privilege_actions"]) for role in item["roles"]]
    try:
        signed_roles = json.loads(bundle._roles_canonical.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("V2R_SITE_MANIFEST_CAPABILITY_INVALID")
    expected_signed_slice = [role for role in signed_roles if role["local_site"] == config.expected_site]
    if actual_roles != expected_roles or item["roles"] != expected_signed_slice or len({role["role_credential_identity_sha256"] for role in item["roles"]}) != len(item["roles"]):
        _fail("V2R_SITE_MANIFEST_ROLE_SUBSTITUTION")
    for role in item["roles"]:
        for field_name in ("role_credential_identity_sha256", "role_iam_policy_sha256", "provider_route_iam_attestation_sha256", "object_lock_retention_proof_sha256", "assertion_sha256"):
            _sha(role[field_name], "V2R_SITE_MANIFEST_ROLE_PROJECTION_INVALID")
    return VerifiedPhysicalWalV2rWitnessRoundtripSiteManifestAdmission(capability=_CAPABILITY, site=config.expected_site, manifest_sha256=_hash(manifest), full_bundle_sha256=bundle.full_bundle_sha256, is_operational=False, authorizes_phase5=False, authorizes_full_matrix=False)
