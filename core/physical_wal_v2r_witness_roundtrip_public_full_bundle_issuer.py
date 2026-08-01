"""Pure claims-only issuer for one V2R public eight-role bundle.

This is the producer counterpart to
``physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission``.  It
does not prove Object-Lock, IAM, a provider route, a credential, or a host.
Those hashes are already-public projections of eight fresh, locally verified
V2R host-role assertions.  The issuer only places those projections into the
fixed V2R public-bundle wire schema and asks a root-owned injected V2R bundle
signer for a detached signature.

No caller may supply a role projection, role map, raw credential identity,
provider claim, retention proof, path, endpoint, client, runtime, or normal
V2/recovery object.  The exact admissions and matrix are recovered only from
one verified V2R Phase-5 profile set.  The result is still public,
non-operational evidence; it cannot install a service or authorize Phase 5,
writer, promotion, traffic, or Full Matrix execution.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Protocol
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core import physical_wal_v2r_witness_roundtrip_contract as _v2r
from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as _mailbox
from core import physical_wal_v2r_witness_roundtrip_control_mailbox_profile as _profile
from core import physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission as _bundle


__all__ = (
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_FULL_BUNDLE_ISSUER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_FULL_BUNDLE_ISSUER_SCHEMA",
    "PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest",
    "PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError",
    "PhysicalWalV2rWitnessRoundtripPublicFullBundleSigner",
    "PhysicalWalV2rWitnessRoundtripPublicFullBundleSigningConfig",
    "PreparedPhysicalWalV2rWitnessRoundtripPublicFullBundle",
    "finalize_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle",
    "prepare_physical_wal_v2r_witness_roundtrip_public_full_bundle",
    "require_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle",
)


PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_FULL_BUNDLE_ISSUER_SCHEMA = (
    "gold-trade-physical-wal-v2r-public-full-bundle-issuer-v1"
)
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_FULL_BUNDLE_ISSUER_DEFAULT_ENABLED = (
    False
)

_STATUS = "v2r-public-full-bundle-claims-only-prepared"
_MAXIMUM_EVIDENCE_AGE_SECONDS = 300
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_CAPABILITY = object()


class PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError(ValueError):
    """A V2R public-bundle issuance request failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError(code)


class PhysicalWalV2rWitnessRoundtripPublicFullBundleSigner(Protocol):
    """One V2R-only injected signer; no key accessor is exposed."""

    def sign_physical_wal_v2r_witness_roundtrip_public_full_bundle(
        self,
        *,
        signing_payload: bytes,
    ) -> bytes: ...


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripPublicFullBundleSigningConfig:
    """Public root pin and normal-V2 deny pins for one V2R bundle signer.

    These are comparison pins only.  They are not a normal-V2 import, signer,
    credential, provider fact, or compatibility bridge.
    """

    bundle_authority_public_key: bytes | None = field(default=None, repr=False)
    normal_v2_bundle_authority_public_key_sha256: str = ""
    normal_v2_mailbox_prefix: str = ""
    normal_v2_iam_catalog_sha256: str = ""
    enabled: bool = (
        PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_FULL_BUNDLE_ISSUER_DEFAULT_ENABLED
    )
    maximum_evidence_age_seconds: int = _MAXIMUM_EVIDENCE_AGE_SECONDS


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest:
    """Only fresh public bundle identifiers and lifetime; never role data."""

    bundle_id: str = ""
    bundle_nonce: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    enabled: bool = (
        PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_FULL_BUNDLE_ISSUER_DEFAULT_ENABLED
    )


@dataclass(frozen=True, eq=False, init=False)
class PreparedPhysicalWalV2rWitnessRoundtripPublicFullBundle:
    """Opaque same-process prepared payload, never a provider attestation."""

    schema: str
    status: str
    prepared_sha256: str
    unsigned_bundle_sha256: str
    profile_set_sha256: str
    role_matrix_sha256: str
    release_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    v2r_iam_catalog_sha256: str
    bundle_id: str
    bundle_nonce: str
    expires_at: datetime
    provider_facts_verified: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_authorized: bool = False
    phase5_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(self, *, capability: object, **values: Any) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2R_PUBLIC_FULL_BUNDLE_PREPARED_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_PUBLIC_FULL_BUNDLE_PREPARED_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V2R_PUBLIC_FULL_BUNDLE_PREPARED_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V2R_PUBLIC_FULL_BUNDLE_PREPARED_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _ResolvedSigningConfig:
    config: PhysicalWalV2rWitnessRoundtripPublicFullBundleSigningConfig
    authority: Ed25519PublicKey
    authority_sha256: str
    configuration_sha256: str


@dataclass(frozen=True)
class _IssuanceFacts:
    config: _ResolvedSigningConfig
    request: PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest
    profile_set: _profile.VerifiedPhysicalWalV2rPhase5ControlMailboxProfileSet
    admissions: tuple[
        _mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission, ...
    ]
    matrix: _mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxRoleMatrix
    unsigned_body: dict[str, object]
    canonical_unsigned: bytes
    prepared_values: dict[str, object]


_PREPARED_STATES: WeakKeyDictionary[
    PreparedPhysicalWalV2rWitnessRoundtripPublicFullBundle, _IssuanceFacts
] = WeakKeyDictionary()


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError(code) from exc


def _hash(value: object, *, code: str) -> str:
    return hashlib.sha256(_canonical(value, code=code)).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or _SHA256_RE.fullmatch(value) is None
        or value == _ZERO_SHA256
    ):
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError(code) from exc


def _render_time(value: object, *, code: str) -> str:
    checked = _utc(value, code=code)
    if checked.microsecond:
        _fail(code)
    return checked.isoformat().replace("+00:00", "Z")


def _resolved_signing_config(
    value: object,
) -> _ResolvedSigningConfig:
    if type(value) is not PhysicalWalV2rWitnessRoundtripPublicFullBundleSigningConfig:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_CONFIG_INVALID")
    config = value
    if (
        config.enabled is not True
        or type(config.bundle_authority_public_key) is not bytes
        or len(config.bundle_authority_public_key) != 32
        or type(config.normal_v2_mailbox_prefix) is not str
        or not config.normal_v2_mailbox_prefix
        or type(config.maximum_evidence_age_seconds) is not int
        or not 1 <= config.maximum_evidence_age_seconds <= _MAXIMUM_EVIDENCE_AGE_SECONDS
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_CONFIG_INVALID")
    normal_key = _sha256(
        config.normal_v2_bundle_authority_public_key_sha256,
        code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_CONFIG_INVALID",
    )
    normal_iam = _sha256(
        config.normal_v2_iam_catalog_sha256,
        code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_CONFIG_INVALID",
    )
    if (
        config.normal_v2_mailbox_prefix
        == _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX
        or config.normal_v2_mailbox_prefix.startswith(
            _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX
        )
        or _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX.startswith(
            config.normal_v2_mailbox_prefix
        )
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_NORMAL_V2_PREFIX_REUSED")
    try:
        authority = Ed25519PublicKey.from_public_bytes(config.bundle_authority_public_key)
    except ValueError as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError(
            "V2R_PUBLIC_FULL_BUNDLE_ISSUER_CONFIG_INVALID"
        ) from exc
    authority_sha256 = hashlib.sha256(config.bundle_authority_public_key).hexdigest()
    if authority_sha256 == normal_key:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_NORMAL_V2_SIGNER_REUSED")
    configuration_sha256 = _hash(
        {
            "schema": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_FULL_BUNDLE_ISSUER_SCHEMA,
            "bundle_authority_public_key_sha256": authority_sha256,
            "normal_v2_bundle_authority_public_key_sha256": normal_key,
            "normal_v2_mailbox_prefix": config.normal_v2_mailbox_prefix,
            "normal_v2_iam_catalog_sha256": normal_iam,
            "maximum_evidence_age_seconds": config.maximum_evidence_age_seconds,
        },
        code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_CONFIG_INVALID",
    )
    return _ResolvedSigningConfig(
        config=config,
        authority=authority,
        authority_sha256=authority_sha256,
        configuration_sha256=configuration_sha256,
    )


def _request(
    value: object,
    *,
    maximum_evidence_age_seconds: int,
    now: datetime,
) -> PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest:
    if (
        type(value) is not PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest
        or value.enabled is not True
        or type(value.bundle_id) is not str
        or _ID_RE.fullmatch(value.bundle_id) is None
        or type(value.bundle_nonce) is not str
        or _NONCE_RE.fullmatch(value.bundle_nonce) is None
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_REQUEST_INVALID")
    issued = _utc(value.issued_at, code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_TIME_INVALID")
    expires = _utc(value.expires_at, code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_TIME_INVALID")
    observed = _utc(now, code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_CLOCK_INVALID")
    if (
        issued.microsecond
        or expires.microsecond
        or issued > observed
        or expires <= observed
        or expires <= issued
        or (expires - issued).total_seconds() > maximum_evidence_age_seconds
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_STALE")
    return value


def _role_projection(
    value: _mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission,
) -> dict[str, object]:
    """Project exactly the V2R public role shape accepted by the parser."""

    return {
        "local_site": value.local_site,
        "local_role": value.local_role,
        "mailbox": value.mailbox,
        "direction": value.direction,
        "object_prefix": value.object_prefix,
        "least_privilege_actions": list(value.least_privilege_actions),
        "role_credential_identity_sha256": value.role_credential_identity_sha256,
        "role_iam_policy_sha256": value.role_iam_policy_sha256,
        "provider_route_iam_attestation_sha256": (
            value.provider_route_iam_attestation_sha256
        ),
        "object_lock_retention_proof_sha256": value.object_lock_retention_proof_sha256,
        "assertion_sha256": value.assertion_sha256,
    }


def _profile_set_facts(
    *,
    value: object,
    now: datetime,
) -> tuple[
    _profile.VerifiedPhysicalWalV2rPhase5ControlMailboxProfileSet,
    tuple[_mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission, ...],
    _mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxRoleMatrix,
]:
    try:
        profile_set = (
            _profile.require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set(
                profile_set=value,
                now=now,
            )
        )
        admissions, matrix = (
            _profile.require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set_admissions_and_matrix(
                profile_set=profile_set,
                now=now,
            )
        )
    except _profile.PhysicalWalV2rPhase5ControlMailboxProfileError as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError(
            "V2R_PUBLIC_FULL_BUNDLE_ISSUER_PROFILE_SET_INVALID"
        ) from exc
    if (
        profile_set.phase5_transport_profile
        != _profile.PHYSICAL_WAL_V2R_PHASE5_TRANSPORT_PROFILE
        or profile_set.writer_authorized is not False
        or profile_set.promotion_authorized is not False
        or profile_set.traffic_authorized is not False
        or profile_set.phase5_authorized is not False
        or profile_set.execution_authorized is not False
        or profile_set.full_matrix_authorized is not False
        or profile_set.full_matrix_executed is not False
        or len(admissions) != 8
        or len({item.role_credential_identity_sha256 for item in admissions}) != 8
        or matrix.deployment_binding_sha256 != profile_set.deployment_binding_sha256
        or matrix.delivery_binding_sha256 != profile_set.delivery_binding_sha256
        or matrix.v2r_iam_catalog_sha256 != profile_set.v2r_iam_catalog_sha256
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_PROFILE_SET_INVALID")
    return profile_set, admissions, matrix


def _facts(
    *,
    config: object,
    request: object,
    profile_set: object,
    now: datetime,
) -> _IssuanceFacts:
    resolved = _resolved_signing_config(config)
    checked_request = _request(
        request,
        maximum_evidence_age_seconds=resolved.config.maximum_evidence_age_seconds,
        now=now,
    )
    checked_set, admissions, matrix = _profile_set_facts(value=profile_set, now=now)
    if checked_set.v2r_iam_catalog_sha256 == resolved.config.normal_v2_iam_catalog_sha256:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_NORMAL_V2_IAM_REUSED")
    expected_roles = tuple(
        policy.local_role
        for policy in _mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES
    )
    if tuple(item.local_role for item in admissions) != expected_roles:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_ROLE_SUBSTITUTION")
    unsigned_body: dict[str, object] = {
        "schema": _bundle._BUNDLE_SCHEMA,
        "version": 1,
        "protocol_domain": _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN,
        "mailbox_prefix": _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX,
        "bundle_id": checked_request.bundle_id,
        "bundle_nonce": checked_request.bundle_nonce,
        "release_sha256": checked_set.release_sha256,
        "deployment_binding_sha256": checked_set.deployment_binding_sha256,
        "delivery_binding_sha256": checked_set.delivery_binding_sha256,
        "v2r_iam_catalog_sha256": checked_set.v2r_iam_catalog_sha256,
        "role_matrix_sha256": matrix.role_matrix_sha256,
        "roles": [_role_projection(item) for item in admissions],
        "issued_at": _render_time(
            checked_request.issued_at,
            code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_TIME_INVALID",
        ),
        "expires_at": _render_time(
            checked_request.expires_at,
            code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_TIME_INVALID",
        ),
    }
    canonical_unsigned = _canonical(
        unsigned_body,
        code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_CANONICAL_INVALID",
    )
    unsigned_bundle_sha256 = hashlib.sha256(canonical_unsigned).hexdigest()
    prepared_values: dict[str, object] = {
        "schema": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_FULL_BUNDLE_ISSUER_SCHEMA,
        "status": _STATUS,
        "prepared_sha256": _hash(
            {
                "schema": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_FULL_BUNDLE_ISSUER_SCHEMA,
                "status": _STATUS,
                "issuer_configuration_sha256": resolved.configuration_sha256,
                "unsigned_bundle_sha256": unsigned_bundle_sha256,
                "profile_set_sha256": checked_set.profile_set_sha256,
                "role_matrix_sha256": matrix.role_matrix_sha256,
            },
            code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_CANONICAL_INVALID",
        ),
        "unsigned_bundle_sha256": unsigned_bundle_sha256,
        "profile_set_sha256": checked_set.profile_set_sha256,
        "role_matrix_sha256": matrix.role_matrix_sha256,
        "release_sha256": checked_set.release_sha256,
        "deployment_binding_sha256": checked_set.deployment_binding_sha256,
        "delivery_binding_sha256": checked_set.delivery_binding_sha256,
        "v2r_iam_catalog_sha256": checked_set.v2r_iam_catalog_sha256,
        "bundle_id": checked_request.bundle_id,
        "bundle_nonce": checked_request.bundle_nonce,
        "expires_at": _utc(
            checked_request.expires_at,
            code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_TIME_INVALID",
        ),
        "provider_facts_verified": False,
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_authorized": False,
        "phase5_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    return _IssuanceFacts(
        config=resolved,
        request=checked_request,
        profile_set=checked_set,
        admissions=admissions,
        matrix=matrix,
        unsigned_body=unsigned_body,
        canonical_unsigned=canonical_unsigned,
        prepared_values=prepared_values,
    )


def prepare_physical_wal_v2r_witness_roundtrip_public_full_bundle(
    *,
    config: PhysicalWalV2rWitnessRoundtripPublicFullBundleSigningConfig,
    request: PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest,
    profile_set: object,
    now: datetime,
) -> PreparedPhysicalWalV2rWitnessRoundtripPublicFullBundle:
    """Prepare one exact V2R public bundle without calling a signer or provider."""

    facts = _facts(config=config, request=request, profile_set=profile_set, now=now)
    result = PreparedPhysicalWalV2rWitnessRoundtripPublicFullBundle(
        capability=_CAPABILITY,
        **facts.prepared_values,
    )
    _PREPARED_STATES[result] = facts
    return result


def require_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
    *,
    prepared: object,
    now: datetime,
) -> PreparedPhysicalWalV2rWitnessRoundtripPublicFullBundle:
    """Recheck a same-process prepared V2R bundle before signing it."""

    if (
        type(prepared) is not PreparedPhysicalWalV2rWitnessRoundtripPublicFullBundle
        or prepared._capability is not _CAPABILITY
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_PREPARED_CAPABILITY_INVALID")
    facts = _PREPARED_STATES.get(prepared)
    if facts is None:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_PREPARED_CAPABILITY_INVALID")
    refreshed = _facts(
        config=facts.config.config,
        request=facts.request,
        profile_set=facts.profile_set,
        now=now,
    )
    if (
        refreshed.canonical_unsigned != facts.canonical_unsigned
        or refreshed.prepared_values != facts.prepared_values
    ):
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_PREPARED_TAMPERED")
    for name, value in refreshed.prepared_values.items():
        if getattr(prepared, name) != value:
            _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_PREPARED_TAMPERED")
    return prepared


def finalize_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
    *,
    prepared: object,
    signer: PhysicalWalV2rWitnessRoundtripPublicFullBundleSigner,
    now: datetime,
) -> bytes:
    """Sign the exact V2R public bundle; no provider or credential is opened."""

    checked = require_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
        prepared=prepared,
        now=now,
    )
    facts = _PREPARED_STATES.get(checked)
    if facts is None:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_PREPARED_CAPABILITY_INVALID")
    callback = getattr(
        signer,
        "sign_physical_wal_v2r_witness_roundtrip_public_full_bundle",
        None,
    )
    if not callable(callback):
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_SIGNER_INVALID")
    try:
        signature = callback(signing_payload=_bundle._BUNDLE_DOMAIN + facts.canonical_unsigned)
    except Exception as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError(
            "V2R_PUBLIC_FULL_BUNDLE_ISSUER_SIGNER_FAILED"
        ) from exc
    if type(signature) is not bytes or len(signature) != 64:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_SIGNATURE_INVALID")
    try:
        facts.config.authority.verify(
            signature,
            _bundle._BUNDLE_DOMAIN + facts.canonical_unsigned,
        )
    except InvalidSignature:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_SIGNATURE_INVALID")
    result = _canonical(
        {
            **facts.unsigned_body,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
        code="V2R_PUBLIC_FULL_BUNDLE_ISSUER_CANONICAL_INVALID",
    )
    # Keep this exact structural guard local as well as covering it with the
    # downstream admission test: no extra status/authority field may leak into
    # the public wire schema.
    try:
        parsed = json.loads(result.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError(
            "V2R_PUBLIC_FULL_BUNDLE_ISSUER_CANONICAL_INVALID"
        ) from exc
    if type(parsed) is not dict or set(parsed) != _bundle._BUNDLE_FIELDS:
        _fail("V2R_PUBLIC_FULL_BUNDLE_ISSUER_SCHEMA_INVALID")
    return result
