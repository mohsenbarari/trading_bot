"""Pure V2R Phase-5 eight-role mailbox profile evidence.

This module intentionally sits *after* the signed local V2R mailbox-admission
grammar and *before* any future provider, IAM, credential, Object-Storage, or
delivery implementation.  It turns one already verified role-local admission
into an opaque, default-off profile projection and can verify the exact
eight-role profile set required by the reverse Phase-5 carrier.

It neither opens a credential nor a client, renders an IAM policy, contacts a
provider, reads a host, reserves replay state, transfers a mailbox record, or
creates writer/promotion/traffic/Phase-5 authority.  Recovery-data and normal
V2 identities are named only as literal deny-pins: they are never imported,
loaded, adapted, or treated as a compatibility source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_wal_v2r_witness_roundtrip_contract as _v2r
from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as _admission


__all__ = (
    "PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SCHEMA",
    "PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_SCHEMA",
    "PHYSICAL_WAL_V2R_PHASE5_TRANSPORT_PROFILE",
    "PhysicalWalV2rPhase5ControlMailboxLegacyDenyPin",
    "PhysicalWalV2rPhase5ControlMailboxProfileConfig",
    "PhysicalWalV2rPhase5ControlMailboxProfileError",
    "VerifiedPhysicalWalV2rPhase5ControlMailboxProfile",
    "VerifiedPhysicalWalV2rPhase5ControlMailboxProfileSet",
    "build_physical_wal_v2r_phase5_control_mailbox_profile",
    "require_verified_physical_wal_v2r_phase5_control_mailbox_profile",
    "require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set_admissions_and_matrix",
    "require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set",
    "verify_physical_wal_v2r_phase5_control_mailbox_profile_set",
)


PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SCHEMA = (
    "gold-trade-physical-wal-v2r-phase5-control-mailbox-profile-v1"
)
PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_SCHEMA = (
    "gold-trade-physical-wal-v2r-phase5-control-mailbox-profile-set-v1"
)
PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_DEFAULT_ENABLED = False
PHYSICAL_WAL_V2R_PHASE5_TRANSPORT_PROFILE = (
    "ir-v2r-witness-roundtrip-strict-ack-v1"
)

_STATUS = "v2r-phase5-control-mailbox-profile-evidence-only"
_SET_STATUS = "v2r-phase5-control-mailbox-profile-set-evidence-only"
_CONFIG_SCHEMA = "gold-trade-physical-wal-v2r-phase5-control-mailbox-profile-config-v1"
_DENY_PINS_SCHEMA = "gold-trade-physical-wal-v2r-phase5-legacy-credential-deny-pins-v1"
_ZERO_SHA256 = "0" * 64
_CAPABILITY = object()

# These names are literal deny-pins.  Do not import a recovery-data or normal
# V2 role module here: doing so would make a later profile layer an accidental
# compatibility/configuration bridge between control planes.
_LEGACY_DENY_ROLE_KEYS = (
    ("recovery-data", "fi-publisher"),
    ("recovery-data", "ir-receiver"),
    ("recovery-data", "ir-publisher"),
    ("recovery-data", "fi-receiver"),
    ("normal-v2", "fi-writer-source-outbox"),
    ("normal-v2", "witness-fi-ingress"),
    ("normal-v2", "witness-ir-egress"),
    ("normal-v2", "ir-standby-ack-inbox"),
    ("normal-v2", "ir-durable-ack-outbox"),
    ("normal-v2", "witness-ir-ingress"),
    ("normal-v2", "witness-fi-egress"),
    ("normal-v2", "fi-writer-ack-inbox"),
)

_PUBLISH_ACTIONS = (
    "object:create-only-fixed-key",
    "object:read-own-exact-version-receipt",
)
_CONSUME_ACTIONS = (
    "object:list-fixed-prefix",
    "object:read-exact-version",
)
_EXPECTED_ROLE_MATRIX = (
    (
        "wa-ir",
        "wa-ir-v2r-exporter",
        "ir-to-witness",
        "publish",
        "physical-wal-v2r-reverse/ir-to-witness/",
        _PUBLISH_ACTIONS,
    ),
    (
        "witness",
        "witness-v2r-reverse-ingress",
        "ir-to-witness",
        "consume",
        "physical-wal-v2r-reverse/ir-to-witness/",
        _CONSUME_ACTIONS,
    ),
    (
        "witness",
        "witness-v2r-reverse-egress",
        "witness-to-fi",
        "publish",
        "physical-wal-v2r-reverse/witness-to-fi/",
        _PUBLISH_ACTIONS,
    ),
    (
        "wa-fi",
        "wa-fi-v2r-recovery-inbox",
        "witness-to-fi",
        "consume",
        "physical-wal-v2r-reverse/witness-to-fi/",
        _CONSUME_ACTIONS,
    ),
    (
        "wa-fi",
        "wa-fi-v2r-ack-outbox",
        "fi-to-witness",
        "publish",
        "physical-wal-v2r-reverse/fi-to-witness/",
        _PUBLISH_ACTIONS,
    ),
    (
        "witness",
        "witness-v2r-ack-ingress",
        "fi-to-witness",
        "consume",
        "physical-wal-v2r-reverse/fi-to-witness/",
        _CONSUME_ACTIONS,
    ),
    (
        "witness",
        "witness-v2r-return-egress",
        "witness-to-ir",
        "publish",
        "physical-wal-v2r-reverse/witness-to-ir/",
        _PUBLISH_ACTIONS,
    ),
    (
        "wa-ir",
        "wa-ir-v2r-return-inbox",
        "witness-to-ir",
        "consume",
        "physical-wal-v2r-reverse/witness-to-ir/",
        _CONSUME_ACTIONS,
    ),
)
_EXPECTED_BY_ROLE = {item[1]: item for item in _EXPECTED_ROLE_MATRIX}


class PhysicalWalV2rPhase5ControlMailboxProfileError(ValueError):
    """A pure V2R Phase-5 profile input is absent, foreign, or stale."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rPhase5ControlMailboxProfileError(code)


@dataclass(frozen=True)
class PhysicalWalV2rPhase5ControlMailboxLegacyDenyPin:
    """One labeled legacy identity that must never become a V2R role."""

    plane: str
    role: str
    credential_identity_sha256: str


@dataclass(frozen=True)
class PhysicalWalV2rPhase5ControlMailboxProfileConfig:
    """Default-off profile policy for exactly one already admitted V2R role.

    ``expected_host_role_assertion_sha256`` is a composition pin for the
    signed host-role assertion consumed by the prior admission grammar.  It
    is a hash, not a host credential or a provider attestation verifier.
    """

    schema: str = PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SCHEMA
    admission_config: (
        _admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig
        | None
    ) = field(default=None, repr=False, compare=False)
    expected_host_role_assertion_sha256: str = ""
    phase5_profile_binding_sha256: str = ""
    phase5_transport_profile: str = PHYSICAL_WAL_V2R_PHASE5_TRANSPORT_PROFILE
    legacy_credential_deny_pins: tuple[
        PhysicalWalV2rPhase5ControlMailboxLegacyDenyPin, ...
    ] = ()
    enabled: bool = PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_DEFAULT_ENABLED


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalWalV2rPhase5ControlMailboxProfile:
    """Opaque profile correlation, never a credential or Phase-5 permit."""

    schema: str
    status: str
    profile_sha256: str
    phase5_profile_binding_sha256: str
    phase5_transport_profile: str
    host_id: str
    local_site: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    least_privilege_actions: tuple[str, ...]
    release_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    v2r_iam_catalog_sha256: str
    role_credential_identity_sha256: str
    role_iam_policy_sha256: str
    provider_route_iam_attestation_sha256: str
    object_lock_retention_proof_sha256: str
    host_role_assertion_sha256: str
    legacy_credential_deny_pins_sha256: str
    expires_at: datetime
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
            raise TypeError("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalWalV2rPhase5ControlMailboxProfileSet:
    """Complete topology evidence for the eight future V2R mailbox roles."""

    schema: str
    status: str
    profile_set_sha256: str
    phase5_profile_binding_sha256: str
    phase5_transport_profile: str
    release_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    v2r_iam_catalog_sha256: str
    legacy_credential_deny_pins_sha256: str
    role_profile_sha256s: tuple[str, ...]
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
            raise TypeError("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _LegacyDenyFacts:
    pins: tuple[PhysicalWalV2rPhase5ControlMailboxLegacyDenyPin, ...]
    pin_sha256: str
    identities: tuple[str, ...]


@dataclass(frozen=True)
class _ProfileFacts:
    config: PhysicalWalV2rPhase5ControlMailboxProfileConfig
    admission: _admission.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission
    legacy: _LegacyDenyFacts
    public_values: dict[str, object]


@dataclass(frozen=True)
class _ProfileSetFacts:
    profiles: tuple[VerifiedPhysicalWalV2rPhase5ControlMailboxProfile, ...]
    public_values: dict[str, object]


_PROFILE_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalV2rPhase5ControlMailboxProfile, _ProfileFacts
] = WeakKeyDictionary()
_PROFILE_SET_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalV2rPhase5ControlMailboxProfileSet, _ProfileSetFacts
] = WeakKeyDictionary()


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2rPhase5ControlMailboxProfileError(code) from exc


def _hash(value: object, *, code: str) -> str:
    try:
        return hashlib.sha256(_canonical(value, code=code)).hexdigest()
    except PhysicalWalV2rPhase5ControlMailboxProfileError:
        raise


def _sha256(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        or value == _ZERO_SHA256
    ):
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _policy_for_role(
    role: object,
) -> tuple[str, str, str, str, str, tuple[str, ...]]:
    if type(role) is not str:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_ROLE_INVALID")
    expected = _EXPECTED_BY_ROLE.get(role)
    if expected is None:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_ROLE_INVALID")
    matched = tuple(
        policy
        for policy in _admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES
        if policy.local_role == role
    )
    if len(matched) != 1:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_POLICY_INVALID")
    policy = matched[0]
    actual = (
        policy.local_site,
        policy.local_role,
        policy.mailbox,
        policy.direction,
        policy.object_prefix,
        policy.least_privilege_actions,
    )
    if actual != expected:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_POLICY_INVALID")
    return expected


def _legacy_deny_pins(value: object) -> _LegacyDenyFacts:
    if type(value) is not tuple or len(value) != len(_LEGACY_DENY_ROLE_KEYS):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_LEGACY_DENY_PINS_INVALID")
    pins: list[PhysicalWalV2rPhase5ControlMailboxLegacyDenyPin] = []
    for expected, item in zip(_LEGACY_DENY_ROLE_KEYS, value, strict=True):
        if type(item) is not PhysicalWalV2rPhase5ControlMailboxLegacyDenyPin:
            _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_LEGACY_DENY_PINS_INVALID")
        if (item.plane, item.role) != expected:
            _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_LEGACY_DENY_PINS_INVALID")
        _sha256(
            item.credential_identity_sha256,
            code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_LEGACY_DENY_PINS_INVALID",
        )
        pins.append(item)
    identities = tuple(item.credential_identity_sha256 for item in pins)
    if len(set(identities)) != len(identities):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_LEGACY_DENY_PINS_INVALID")
    body = {
        "schema": _DENY_PINS_SCHEMA,
        "pins": [
            {
                "plane": item.plane,
                "role": item.role,
                "credential_identity_sha256": item.credential_identity_sha256,
            }
            for item in pins
        ],
    }
    return _LegacyDenyFacts(
        pins=tuple(pins),
        pin_sha256=_hash(
            body,
            code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_LEGACY_DENY_PINS_INVALID",
        ),
        identities=identities,
    )


def _config(
    value: object,
) -> tuple[PhysicalWalV2rPhase5ControlMailboxProfileConfig, _LegacyDenyFacts]:
    if type(value) is not PhysicalWalV2rPhase5ControlMailboxProfileConfig:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CONFIG_INVALID")
    config = value
    if (
        config.schema != PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SCHEMA
        or config.enabled is not True
        or type(config.admission_config)
        is not _admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig
        or config.phase5_transport_profile
        != PHYSICAL_WAL_V2R_PHASE5_TRANSPORT_PROFILE
    ):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CONFIG_INVALID")
    _policy_for_role(config.admission_config.local_role)
    _sha256(
        config.expected_host_role_assertion_sha256,
        code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CONFIG_INVALID",
    )
    _sha256(
        config.phase5_profile_binding_sha256,
        code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CONFIG_INVALID",
    )
    legacy = _legacy_deny_pins(config.legacy_credential_deny_pins)
    admission_deny_pins = config.admission_config.non_v2r_credential_identity_sha256s
    if (
        type(admission_deny_pins) is not tuple
        or len(admission_deny_pins) != len(_LEGACY_DENY_ROLE_KEYS)
        or any(type(item) is not str for item in admission_deny_pins)
        or len(set(admission_deny_pins)) != len(admission_deny_pins)
        or set(legacy.identities) != set(admission_deny_pins)
    ):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_LEGACY_DENY_PINS_MISMATCH")
    return config, legacy


def _profile_body(
    *,
    config: PhysicalWalV2rPhase5ControlMailboxProfileConfig,
    admission: _admission.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission,
    legacy: _LegacyDenyFacts,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SCHEMA,
        "status": _STATUS,
        "protocol_domain": _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN,
        "mailbox_prefix": _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX,
        "phase5_profile_binding_sha256": config.phase5_profile_binding_sha256,
        "phase5_transport_profile": config.phase5_transport_profile,
        "host_id": config.admission_config.host_id,
        "local_site": admission.local_site,
        "local_role": admission.local_role,
        "mailbox": admission.mailbox,
        "direction": admission.direction,
        "object_prefix": admission.object_prefix,
        "least_privilege_actions": list(admission.least_privilege_actions),
        "release_sha256": config.admission_config.release_sha256,
        "deployment_binding_sha256": admission.deployment_binding_sha256,
        "delivery_binding_sha256": admission.delivery_binding_sha256,
        "v2r_iam_catalog_sha256": config.admission_config.v2r_iam_catalog_sha256,
        "role_credential_identity_sha256": admission.role_credential_identity_sha256,
        "role_iam_policy_sha256": admission.role_iam_policy_sha256,
        "provider_route_iam_attestation_sha256": (
            admission.provider_route_iam_attestation_sha256
        ),
        "object_lock_retention_proof_sha256": admission.object_lock_retention_proof_sha256,
        "host_role_assertion_sha256": admission.assertion_sha256,
        "legacy_credential_deny_pins_sha256": legacy.pin_sha256,
        "expires_at": admission.expires_at.isoformat(),
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_authorized": False,
        "phase5_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def _profile_public_values(
    *,
    config: PhysicalWalV2rPhase5ControlMailboxProfileConfig,
    admission: _admission.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission,
    legacy: _LegacyDenyFacts,
) -> dict[str, object]:
    body = _profile_body(config=config, admission=admission, legacy=legacy)
    return {
        "schema": PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SCHEMA,
        "status": _STATUS,
        "profile_sha256": _hash(body, code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CANONICAL_INVALID"),
        "phase5_profile_binding_sha256": config.phase5_profile_binding_sha256,
        "phase5_transport_profile": config.phase5_transport_profile,
        "host_id": config.admission_config.host_id,
        "local_site": admission.local_site,
        "local_role": admission.local_role,
        "mailbox": admission.mailbox,
        "direction": admission.direction,
        "object_prefix": admission.object_prefix,
        "least_privilege_actions": admission.least_privilege_actions,
        "release_sha256": config.admission_config.release_sha256,
        "deployment_binding_sha256": admission.deployment_binding_sha256,
        "delivery_binding_sha256": admission.delivery_binding_sha256,
        "v2r_iam_catalog_sha256": config.admission_config.v2r_iam_catalog_sha256,
        "role_credential_identity_sha256": admission.role_credential_identity_sha256,
        "role_iam_policy_sha256": admission.role_iam_policy_sha256,
        "provider_route_iam_attestation_sha256": admission.provider_route_iam_attestation_sha256,
        "object_lock_retention_proof_sha256": admission.object_lock_retention_proof_sha256,
        "host_role_assertion_sha256": admission.assertion_sha256,
        "legacy_credential_deny_pins_sha256": legacy.pin_sha256,
        "expires_at": admission.expires_at,
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_authorized": False,
        "phase5_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def _verified_admission(
    *,
    value: object,
    config: PhysicalWalV2rPhase5ControlMailboxProfileConfig,
    legacy: _LegacyDenyFacts,
    now: datetime,
) -> _admission.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission:
    try:
        admission = (
            _admission.require_verified_physical_wal_v2r_witness_roundtrip_control_mailbox_admission(
                admission=value,
                config=config.admission_config,
                now=now,
            )
        )
    except _admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError as exc:
        raise PhysicalWalV2rPhase5ControlMailboxProfileError(
            "V2R_PHASE5_CONTROL_MAILBOX_PROFILE_ADMISSION_INVALID"
        ) from exc
    expected = _policy_for_role(admission.local_role)
    if (
        (
            admission.local_site,
            admission.local_role,
            admission.mailbox,
            admission.direction,
            admission.object_prefix,
            admission.least_privilege_actions,
        )
        != expected
        or admission.assertion_sha256 != config.expected_host_role_assertion_sha256
        or admission.role_credential_identity_sha256 in legacy.identities
        or config.admission_config.role_credential_identity_sha256
        != admission.role_credential_identity_sha256
    ):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_ADMISSION_CROSS_PIN_MISMATCH")
    return admission


def build_physical_wal_v2r_phase5_control_mailbox_profile(
    *,
    config: PhysicalWalV2rPhase5ControlMailboxProfileConfig,
    admission: object,
    now: datetime,
) -> VerifiedPhysicalWalV2rPhase5ControlMailboxProfile:
    """Build one pure, non-authorizing V2R role profile from a live admission."""

    checked_config, legacy = _config(config)
    observed = _utc(now, code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CLOCK_INVALID")
    checked_admission = _verified_admission(
        value=admission,
        config=checked_config,
        legacy=legacy,
        now=observed,
    )
    public_values = _profile_public_values(
        config=checked_config,
        admission=checked_admission,
        legacy=legacy,
    )
    result = VerifiedPhysicalWalV2rPhase5ControlMailboxProfile(
        capability=_CAPABILITY,
        **public_values,
    )
    _PROFILE_STATES[result] = _ProfileFacts(
        config=checked_config,
        admission=checked_admission,
        legacy=legacy,
        public_values=public_values,
    )
    return result


def require_verified_physical_wal_v2r_phase5_control_mailbox_profile(
    *,
    profile: object,
    now: datetime,
) -> VerifiedPhysicalWalV2rPhase5ControlMailboxProfile:
    """Require a fresh same-process profile projection, never an action permit."""

    if (
        type(profile) is not VerifiedPhysicalWalV2rPhase5ControlMailboxProfile
        or profile._capability is not _CAPABILITY
    ):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CAPABILITY_INVALID")
    facts = _PROFILE_STATES.get(profile)
    if facts is None:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CAPABILITY_INVALID")
    observed = _utc(now, code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CLOCK_INVALID")
    checked_config, legacy = _config(facts.config)
    checked_admission = _verified_admission(
        value=facts.admission,
        config=checked_config,
        legacy=legacy,
        now=observed,
    )
    expected = _profile_public_values(
        config=checked_config,
        admission=checked_admission,
        legacy=legacy,
    )
    if expected != facts.public_values:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_TAMPERED")
    for name, value in expected.items():
        if getattr(profile, name) != value:
            _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_TAMPERED")
    return profile


def _profile_set_body(
    profiles: tuple[VerifiedPhysicalWalV2rPhase5ControlMailboxProfile, ...],
) -> dict[str, object]:
    first = profiles[0]
    return {
        "schema": PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_SCHEMA,
        "status": _SET_STATUS,
        "protocol_domain": _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN,
        "mailbox_prefix": _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX,
        "phase5_profile_binding_sha256": first.phase5_profile_binding_sha256,
        "phase5_transport_profile": first.phase5_transport_profile,
        "release_sha256": first.release_sha256,
        "deployment_binding_sha256": first.deployment_binding_sha256,
        "delivery_binding_sha256": first.delivery_binding_sha256,
        "v2r_iam_catalog_sha256": first.v2r_iam_catalog_sha256,
        "legacy_credential_deny_pins_sha256": first.legacy_credential_deny_pins_sha256,
        "roles": [
            {
                "host_id": profile.host_id,
                "local_site": profile.local_site,
                "local_role": profile.local_role,
                "mailbox": profile.mailbox,
                "direction": profile.direction,
                "object_prefix": profile.object_prefix,
                "least_privilege_actions": list(profile.least_privilege_actions),
                "profile_sha256": profile.profile_sha256,
                "host_role_assertion_sha256": profile.host_role_assertion_sha256,
                "credential_identity_sha256": profile.role_credential_identity_sha256,
                "role_iam_policy_sha256": profile.role_iam_policy_sha256,
                "provider_route_iam_attestation_sha256": (
                    profile.provider_route_iam_attestation_sha256
                ),
                "object_lock_retention_proof_sha256": (
                    profile.object_lock_retention_proof_sha256
                ),
            }
            for profile in profiles
        ],
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_authorized": False,
        "phase5_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def _profile_set_public_values(
    profiles: tuple[VerifiedPhysicalWalV2rPhase5ControlMailboxProfile, ...],
) -> dict[str, object]:
    first = profiles[0]
    return {
        "schema": PHYSICAL_WAL_V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_SCHEMA,
        "status": _SET_STATUS,
        "profile_set_sha256": _hash(
            _profile_set_body(profiles),
            code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_CANONICAL_INVALID",
        ),
        "phase5_profile_binding_sha256": first.phase5_profile_binding_sha256,
        "phase5_transport_profile": first.phase5_transport_profile,
        "release_sha256": first.release_sha256,
        "deployment_binding_sha256": first.deployment_binding_sha256,
        "delivery_binding_sha256": first.delivery_binding_sha256,
        "v2r_iam_catalog_sha256": first.v2r_iam_catalog_sha256,
        "legacy_credential_deny_pins_sha256": first.legacy_credential_deny_pins_sha256,
        "role_profile_sha256s": tuple(profile.profile_sha256 for profile in profiles),
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_authorized": False,
        "phase5_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def _verify_profile_set_shape(
    profiles: tuple[object, ...], *, now: datetime
) -> tuple[VerifiedPhysicalWalV2rPhase5ControlMailboxProfile, ...]:
    if type(profiles) is not tuple or len(profiles) != len(_EXPECTED_ROLE_MATRIX):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_INCOMPLETE")
    checked = tuple(
        require_verified_physical_wal_v2r_phase5_control_mailbox_profile(
            profile=item,
            now=now,
        )
        for item in profiles
    )
    actual = tuple(
        (
            profile.local_site,
            profile.local_role,
            profile.mailbox,
            profile.direction,
            profile.object_prefix,
            profile.least_privilege_actions,
        )
        for profile in checked
    )
    if actual != _EXPECTED_ROLE_MATRIX:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_ROLE_SUBSTITUTION")
    if (
        len({profile.role_credential_identity_sha256 for profile in checked})
        != len(checked)
        or len({profile.host_role_assertion_sha256 for profile in checked})
        != len(checked)
        or len({profile.profile_sha256 for profile in checked}) != len(checked)
    ):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_ALIAS")
    common = (
        "phase5_profile_binding_sha256",
        "phase5_transport_profile",
        "release_sha256",
        "deployment_binding_sha256",
        "delivery_binding_sha256",
        "v2r_iam_catalog_sha256",
        "legacy_credential_deny_pins_sha256",
    )
    if any(len({getattr(profile, name) for profile in checked}) != 1 for name in common):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_BINDING_MISMATCH")
    host_counts = {
        site: sum(profile.local_site == site for profile in checked)
        for site in ("wa-ir", "wa-fi", "witness")
    }
    if host_counts != {"wa-ir": 2, "wa-fi": 2, "witness": 4}:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_HOST_TOPOLOGY_INVALID")
    if any(
        profile.writer_authorized is not False
        or profile.promotion_authorized is not False
        or profile.traffic_authorized is not False
        or profile.phase5_authorized is not False
        or profile.execution_authorized is not False
        or profile.full_matrix_authorized is not False
        or profile.full_matrix_executed is not False
        for profile in checked
    ):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_AUTHORITY_INVALID")
    return checked


def verify_physical_wal_v2r_phase5_control_mailbox_profile_set(
    *,
    profiles: tuple[object, ...],
    now: datetime,
) -> VerifiedPhysicalWalV2rPhase5ControlMailboxProfileSet:
    """Verify exactly eight fresh profiles, without installing any mailbox."""

    observed = _utc(now, code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CLOCK_INVALID")
    checked = _verify_profile_set_shape(profiles, now=observed)
    values = _profile_set_public_values(checked)
    result = VerifiedPhysicalWalV2rPhase5ControlMailboxProfileSet(
        capability=_CAPABILITY,
        **values,
    )
    _PROFILE_SET_STATES[result] = _ProfileSetFacts(
        profiles=checked,
        public_values=values,
    )
    return result


def require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set(
    *,
    profile_set: object,
    now: datetime,
) -> VerifiedPhysicalWalV2rPhase5ControlMailboxProfileSet:
    """Require the fresh all-eight evidence set, never a deployment permit."""

    if (
        type(profile_set) is not VerifiedPhysicalWalV2rPhase5ControlMailboxProfileSet
        or profile_set._capability is not _CAPABILITY
    ):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_CAPABILITY_INVALID")
    facts = _PROFILE_SET_STATES.get(profile_set)
    if facts is None:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_CAPABILITY_INVALID")
    observed = _utc(now, code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CLOCK_INVALID")
    checked = _verify_profile_set_shape(facts.profiles, now=observed)
    expected = _profile_set_public_values(checked)
    if expected != facts.public_values:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_TAMPERED")
    for name, value in expected.items():
        if getattr(profile_set, name) != value:
            _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_TAMPERED")
    return profile_set


def require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set_admissions_and_matrix(
    *,
    profile_set: object,
    now: datetime,
) -> tuple[
    tuple[_admission.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission, ...],
    _admission.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxRoleMatrix,
]:
    """Return only the fresh opaque admissions bound into one profile set.

    A later pure V2R bundle issuer needs the exact already-admitted role
    projections and their matrix digest, but must not accept caller-supplied
    configs, role maps, credential identities, or provider claims.  This
    narrow accessor reuses the profile set's private, same-process facts only
    after re-verifying the complete set.  It exposes no config, credential,
    signer, host path, or provider client.
    """

    checked_set = require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set(
        profile_set=profile_set,
        now=now,
    )
    facts = _PROFILE_SET_STATES.get(checked_set)
    if facts is None:
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_CAPABILITY_INVALID")
    observed = _utc(now, code="V2R_PHASE5_CONTROL_MAILBOX_PROFILE_CLOCK_INVALID")
    profiles = _verify_profile_set_shape(facts.profiles, now=observed)
    admission_facts: list[_ProfileFacts] = []
    for profile in profiles:
        require_verified_physical_wal_v2r_phase5_control_mailbox_profile(
            profile=profile,
            now=observed,
        )
        profile_facts = _PROFILE_STATES.get(profile)
        if profile_facts is None:
            _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_CAPABILITY_INVALID")
        admission_facts.append(profile_facts)
    admissions = tuple(item.admission for item in admission_facts)
    configs = tuple(item.config.admission_config for item in admission_facts)
    if any(config is None for config in configs):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_CAPABILITY_INVALID")
    try:
        matrix = (
            _admission.verify_physical_wal_v2r_witness_roundtrip_control_mailbox_role_matrix(
                admissions=admissions,
                configs=configs,
                now=observed,
            )
        )
    except _admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError as exc:
        raise PhysicalWalV2rPhase5ControlMailboxProfileError(
            "V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_ADMISSION_MATRIX_INVALID"
        ) from exc
    if (
        matrix.deployment_binding_sha256 != checked_set.deployment_binding_sha256
        or matrix.delivery_binding_sha256 != checked_set.delivery_binding_sha256
        or matrix.v2r_iam_catalog_sha256 != checked_set.v2r_iam_catalog_sha256
    ):
        _fail("V2R_PHASE5_CONTROL_MAILBOX_PROFILE_SET_ADMISSION_MATRIX_INVALID")
    return admissions, matrix
