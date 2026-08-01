"""Pure deployment-admission grammar for the eight V2 Witness mailboxes.

This module is intentionally not an Object-Storage implementation.  It
enumerates the only eight role-local capabilities that a later adapter may
receive, verifies a deployment-authority-signed host-role assertion, and
returns one non-forgeable typed mailbox admission.  It contains no provider,
network, endpoint, credential loader, client factory, or legacy role-profile
surface.

The policy is deliberately concrete.  There is no FI-to-IR mailbox: all four
directions terminate at Witness, and every role gets only the fixed object
prefix and least-privilege actions required by that one direction.
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

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_MAXIMUM_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_SCHEMA",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES",
    "PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig",
    "PhysicalWalV2WitnessRoundtripMailboxAdmissionError",
    "PhysicalWalV2WitnessRoundtripMailboxPolicy",
    "VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission",
    "VerifiedPhysicalWalV2WitnessRoundtripMailboxHostRoleAssertion",
    "admit_physical_wal_v2_witness_roundtrip_mailbox",
    "require_verified_physical_wal_v2_witness_roundtrip_mailbox_admission",
    "verify_physical_wal_v2_witness_roundtrip_mailbox_host_role_assertion",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-mailbox-admission-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_MAXIMUM_EVIDENCE_AGE_SECONDS = 300

_HOST_ASSERTION_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-host-role-assertion-v1"
_HOST_ASSERTION_VERSION = 1
_POLICY_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-mailbox-policy-v1"
_CONFIG_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-mailbox-admission-config-v1"
_HOST_ROLE_DOMAIN = b"gold-trade-physical-wal-v2-witness-roundtrip-host-role-assertion-v1\x00"
_CAPABILITY = object()

_FI_TO_WITNESS = "fi-to-witness"
_WITNESS_TO_IR = "witness-to-ir"
_IR_TO_WITNESS = "ir-to-witness"
_WITNESS_TO_FI = "witness-to-fi"
_PUBLISH = "publish"
_CONSUME = "consume"
_OBJECT_ROOT = "physical-wal-v2-witness-roundtrip-delivery-v1"

_CREATE_ONLY = "object:create-only-fixed-key"
_READ_OWN_EXACT = "object:read-own-exact-version-receipt"
_LIST_FIXED = "object:list-fixed-prefix"
_READ_EXACT = "object:read-exact-version"

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_HOST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,127}$", re.ASCII)
_ASSERTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_ZERO_SHA256 = "0" * 64

_ASSERTION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "host_id",
        "local_role",
        "mailbox",
        "direction",
        "object_prefix",
        "least_privilege_actions",
        "policy_sha256",
        "deployment_binding_sha256",
        "delivery_binding_sha256",
        "assertion_id",
        "assertion_nonce",
        "issued_at",
        "expires_at",
        "signature_base64",
    }
)


class PhysicalWalV2WitnessRoundtripMailboxAdmissionError(ValueError):
    """A role-local mailbox admission is disabled, stale, foreign, or forged."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripMailboxPolicy:
    """One exhaustive fixed role/mailbox capability, never an input policy."""

    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    least_privilege_actions: tuple[str, ...]


def _prefix(mailbox: str) -> str:
    return _OBJECT_ROOT + "/" + mailbox + "/"


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES = (
    PhysicalWalV2WitnessRoundtripMailboxPolicy(
        "fi-writer-source-outbox",
        _FI_TO_WITNESS,
        _PUBLISH,
        _prefix(_FI_TO_WITNESS),
        (_CREATE_ONLY, _READ_OWN_EXACT),
    ),
    PhysicalWalV2WitnessRoundtripMailboxPolicy(
        "witness-fi-ingress",
        _FI_TO_WITNESS,
        _CONSUME,
        _prefix(_FI_TO_WITNESS),
        (_LIST_FIXED, _READ_EXACT),
    ),
    PhysicalWalV2WitnessRoundtripMailboxPolicy(
        "witness-ir-egress",
        _WITNESS_TO_IR,
        _PUBLISH,
        _prefix(_WITNESS_TO_IR),
        (_CREATE_ONLY, _READ_OWN_EXACT),
    ),
    PhysicalWalV2WitnessRoundtripMailboxPolicy(
        "ir-standby-ack-inbox",
        _WITNESS_TO_IR,
        _CONSUME,
        _prefix(_WITNESS_TO_IR),
        (_LIST_FIXED, _READ_EXACT),
    ),
    PhysicalWalV2WitnessRoundtripMailboxPolicy(
        "ir-durable-ack-outbox",
        _IR_TO_WITNESS,
        _PUBLISH,
        _prefix(_IR_TO_WITNESS),
        (_CREATE_ONLY, _READ_OWN_EXACT),
    ),
    PhysicalWalV2WitnessRoundtripMailboxPolicy(
        "witness-ir-ingress",
        _IR_TO_WITNESS,
        _CONSUME,
        _prefix(_IR_TO_WITNESS),
        (_LIST_FIXED, _READ_EXACT),
    ),
    PhysicalWalV2WitnessRoundtripMailboxPolicy(
        "witness-fi-egress",
        _WITNESS_TO_FI,
        _PUBLISH,
        _prefix(_WITNESS_TO_FI),
        (_CREATE_ONLY, _READ_OWN_EXACT),
    ),
    PhysicalWalV2WitnessRoundtripMailboxPolicy(
        "fi-writer-ack-inbox",
        _WITNESS_TO_FI,
        _CONSUME,
        _prefix(_WITNESS_TO_FI),
        (_LIST_FIXED, _READ_EXACT),
    ),
)
_POLICIES_BY_ROLE = {policy.local_role: policy for policy in PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES}


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig:
    """Default-off deployment policy for one host's sole local mailbox role.

    ``host_role_authority_public_key`` is a public deployment-attestation key,
    not an Object-Storage credential.  The matching private key remains
    outside this module and outside future mailbox adapters.
    """

    host_id: str = ""
    local_role: str = ""
    deployment_binding_sha256: str = ""
    delivery_binding_sha256: str = ""
    host_role_authority_public_key: bytes | None = field(default=None, repr=False)
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_MAXIMUM_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True, init=False)
class VerifiedPhysicalWalV2WitnessRoundtripMailboxHostRoleAssertion:
    """A non-forgeable validated deployment role assertion."""

    host_id: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    least_privilege_actions: tuple[str, ...]
    policy_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    assertion_id: str
    assertion_nonce: str
    issued_at: datetime
    expires_at: datetime
    assertion_sha256: str
    canonical_assertion: bytes = field(repr=False)
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
        least_privilege_actions: tuple[str, ...],
        policy_sha256: str,
        deployment_binding_sha256: str,
        delivery_binding_sha256: str,
        assertion_id: str,
        assertion_nonce: str,
        issued_at: datetime,
        expires_at: datetime,
        assertion_sha256: str,
        canonical_assertion: bytes,
        configuration_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_MAILBOX_HOST_ROLE_ASSERTION_CONSTRUCTION_FORBIDDEN")
        object.__setattr__(self, "host_id", host_id)
        object.__setattr__(self, "local_role", local_role)
        object.__setattr__(self, "mailbox", mailbox)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "object_prefix", object_prefix)
        object.__setattr__(self, "least_privilege_actions", least_privilege_actions)
        object.__setattr__(self, "policy_sha256", policy_sha256)
        object.__setattr__(self, "deployment_binding_sha256", deployment_binding_sha256)
        object.__setattr__(self, "delivery_binding_sha256", delivery_binding_sha256)
        object.__setattr__(self, "assertion_id", assertion_id)
        object.__setattr__(self, "assertion_nonce", assertion_nonce)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "assertion_sha256", assertion_sha256)
        object.__setattr__(self, "canonical_assertion", canonical_assertion)
        object.__setattr__(self, "_configuration_sha256", configuration_sha256)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_MAILBOX_HOST_ROLE_ASSERTION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, init=False)
class VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission:
    """The sole typed capability a future fixed-mailbox adapter may accept."""

    schema: str
    host_id: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    least_privilege_actions: tuple[str, ...]
    policy_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    host_role_assertion_sha256: str
    issued_at: datetime
    expires_at: datetime
    admission_sha256: str
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
        least_privilege_actions: tuple[str, ...],
        policy_sha256: str,
        deployment_binding_sha256: str,
        delivery_binding_sha256: str,
        host_role_assertion_sha256: str,
        issued_at: datetime,
        expires_at: datetime,
        configuration_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CONSTRUCTION_FORBIDDEN")
        mapping = {
            "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_SCHEMA,
            "host_id": host_id,
            "local_role": local_role,
            "mailbox": mailbox,
            "direction": direction,
            "object_prefix": object_prefix,
            "least_privilege_actions": list(least_privilege_actions),
            "policy_sha256": policy_sha256,
            "deployment_binding_sha256": deployment_binding_sha256,
            "delivery_binding_sha256": delivery_binding_sha256,
            "host_role_assertion_sha256": host_role_assertion_sha256,
            "issued_at": _render_timestamp(issued_at),
            "expires_at": _render_timestamp(expires_at),
            "configuration_sha256": configuration_sha256,
        }
        object.__setattr__(self, "schema", PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_SCHEMA)
        object.__setattr__(self, "host_id", host_id)
        object.__setattr__(self, "local_role", local_role)
        object.__setattr__(self, "mailbox", mailbox)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "object_prefix", object_prefix)
        object.__setattr__(self, "least_privilege_actions", least_privilege_actions)
        object.__setattr__(self, "policy_sha256", policy_sha256)
        object.__setattr__(self, "deployment_binding_sha256", deployment_binding_sha256)
        object.__setattr__(self, "delivery_binding_sha256", delivery_binding_sha256)
        object.__setattr__(self, "host_role_assertion_sha256", host_role_assertion_sha256)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "admission_sha256", _sha256_bytes(_canonical(mapping, code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_INVALID")))
        object.__setattr__(self, "_configuration_sha256", configuration_sha256)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Config:
    host_id: str
    local_role: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    authority_public_key: Ed25519PublicKey
    policy: PhysicalWalV2WitnessRoundtripMailboxPolicy
    policy_sha256: str
    configuration_sha256: str
    maximum_evidence_age_seconds: int


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripMailboxAdmissionError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripMailboxAdmissionError(code) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_JSON_INVALID")


def _parse_canonical(value: object, *, code: str) -> tuple[dict[str, Any], bytes]:
    if type(value) is not bytes or not 1 <= len(value) <= 128 * 1024:
        _fail(code)
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripMailboxAdmissionError(code) from exc
    if type(parsed) is not dict or _canonical(parsed, code=code) != value:
        _fail(code)
    return dict(parsed), value


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if result.microsecond != 0:
        _fail(code)
    return result


def _render_timestamp(value: datetime) -> str:
    return _utc(value, code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_TIME_INVALID").strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripMailboxAdmissionError(code) from exc


def _b64(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        raw = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalV2WitnessRoundtripMailboxAdmissionError(code) from exc
    if len(raw) != 64:
        _fail(code)
    return raw


def _policy_mapping(policy: PhysicalWalV2WitnessRoundtripMailboxPolicy) -> dict[str, object]:
    return {
        "schema": _POLICY_SCHEMA,
        "local_role": policy.local_role,
        "mailbox": policy.mailbox,
        "direction": policy.direction,
        "object_prefix": policy.object_prefix,
        "least_privilege_actions": list(policy.least_privilege_actions),
    }


def _policy_sha256(policy: PhysicalWalV2WitnessRoundtripMailboxPolicy) -> str:
    return _sha256_bytes(_canonical(_policy_mapping(policy), code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_POLICY_INVALID"))


def _config(value: object) -> _Config:
    if type(value) is not PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig:
        _fail("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CONFIG_INVALID")
    config = value
    if (
        config.enabled is not True
        or type(config.host_id) is not str
        or _HOST_ID_RE.fullmatch(config.host_id) is None
        or type(config.maximum_evidence_age_seconds) is not int
        or not 1 <= config.maximum_evidence_age_seconds <= 3_600
    ):
        _fail("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CONFIG_INVALID")
    try:
        policy = _POLICIES_BY_ROLE[config.local_role]
    except (KeyError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripMailboxAdmissionError(
            "V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_ROLE_INVALID"
        ) from exc
    deployment_binding = _sha256(
        config.deployment_binding_sha256,
        code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CONFIG_INVALID",
    )
    delivery_binding = _sha256(
        config.delivery_binding_sha256,
        code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CONFIG_INVALID",
    )
    if type(config.host_role_authority_public_key) is not bytes or len(config.host_role_authority_public_key) != 32:
        _fail("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CONFIG_INVALID")
    try:
        authority = Ed25519PublicKey.from_public_bytes(config.host_role_authority_public_key)
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripMailboxAdmissionError(
            "V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CONFIG_INVALID"
        ) from exc
    policy_sha = _policy_sha256(policy)
    configuration = {
        "schema": _CONFIG_SCHEMA,
        "host_id": config.host_id,
        "local_role": policy.local_role,
        "deployment_binding_sha256": deployment_binding,
        "delivery_binding_sha256": delivery_binding,
        "host_role_authority_public_key_base64": base64.b64encode(
            config.host_role_authority_public_key
        ).decode("ascii"),
        "policy_sha256": policy_sha,
        "maximum_evidence_age_seconds": config.maximum_evidence_age_seconds,
    }
    return _Config(
        host_id=config.host_id,
        local_role=policy.local_role,
        deployment_binding_sha256=deployment_binding,
        delivery_binding_sha256=delivery_binding,
        authority_public_key=authority,
        policy=policy,
        policy_sha256=policy_sha,
        configuration_sha256=_sha256_bytes(
            _canonical(configuration, code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CONFIG_INVALID")
        ),
        maximum_evidence_age_seconds=config.maximum_evidence_age_seconds,
    )


def _assertion_unsigned(item: dict[str, Any]) -> dict[str, Any]:
    unsigned = dict(item)
    unsigned.pop("signature_base64", None)
    return unsigned


def _verify_signature(*, raw: bytes, signature: bytes, config: _Config) -> None:
    try:
        config.authority_public_key.verify(signature, _HOST_ROLE_DOMAIN + raw)
    except InvalidSignature as exc:
        raise PhysicalWalV2WitnessRoundtripMailboxAdmissionError(
            "V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_SIGNATURE_INVALID"
        ) from exc


def _verify_host_role_assertion(
    assertion: bytes,
    *,
    config: _Config,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripMailboxHostRoleAssertion:
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CLOCK_INVALID")
    item, raw = _parse_canonical(
        assertion,
        code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_INVALID",
    )
    item = _exact_mapping(
        item,
        fields=_ASSERTION_FIELDS,
        code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_INVALID",
    )
    signature = _b64(
        item["signature_base64"],
        code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_INVALID",
    )
    unsigned = _assertion_unsigned(item)
    unsigned_raw = _canonical(
        unsigned,
        code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_INVALID",
    )
    _verify_signature(raw=unsigned_raw, signature=signature, config=config)
    policy = config.policy
    if (
        item["schema"] != _HOST_ASSERTION_SCHEMA
        or item["version"] != _HOST_ASSERTION_VERSION
        or item["host_id"] != config.host_id
        or item["local_role"] != policy.local_role
        or item["mailbox"] != policy.mailbox
        or item["direction"] != policy.direction
        or item["object_prefix"] != policy.object_prefix
        or type(item["least_privilege_actions"]) is not list
        or tuple(item["least_privilege_actions"]) != policy.least_privilege_actions
        or _sha256(
            item["policy_sha256"],
            code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_INVALID",
        )
        != config.policy_sha256
        or _sha256(
            item["deployment_binding_sha256"],
            code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_INVALID",
        )
        != config.deployment_binding_sha256
        or _sha256(
            item["delivery_binding_sha256"],
            code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_INVALID",
        )
        != config.delivery_binding_sha256
        or type(item["assertion_id"]) is not str
        or _ASSERTION_ID_RE.fullmatch(item["assertion_id"]) is None
        or type(item["assertion_nonce"]) is not str
        or _NONCE_RE.fullmatch(item["assertion_nonce"]) is None
    ):
        _fail("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_CROSS_PIN_MISMATCH")
    issued_at = _parse_timestamp(
        item["issued_at"], code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_TIME_INVALID"
    )
    expires_at = _parse_timestamp(
        item["expires_at"], code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_TIME_INVALID"
    )
    if (
        issued_at > observed
        or expires_at <= observed
        or expires_at <= issued_at
        or (expires_at - issued_at).total_seconds() > config.maximum_evidence_age_seconds
    ):
        _fail("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_HOST_ROLE_ASSERTION_STALE")
    return VerifiedPhysicalWalV2WitnessRoundtripMailboxHostRoleAssertion(
        host_id=config.host_id,
        local_role=policy.local_role,
        mailbox=policy.mailbox,
        direction=policy.direction,
        object_prefix=policy.object_prefix,
        least_privilege_actions=policy.least_privilege_actions,
        policy_sha256=config.policy_sha256,
        deployment_binding_sha256=config.deployment_binding_sha256,
        delivery_binding_sha256=config.delivery_binding_sha256,
        assertion_id=item["assertion_id"],
        assertion_nonce=item["assertion_nonce"],
        issued_at=issued_at,
        expires_at=expires_at,
        assertion_sha256=_sha256_bytes(raw),
        canonical_assertion=raw,
        configuration_sha256=config.configuration_sha256,
        capability=_CAPABILITY,
    )


def verify_physical_wal_v2_witness_roundtrip_mailbox_host_role_assertion(
    assertion: bytes,
    *,
    config: PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripMailboxHostRoleAssertion:
    """Verify one signed host/local-role assertion under one default-off config."""

    return _verify_host_role_assertion(assertion, config=_config(config), now=now)


def admit_physical_wal_v2_witness_roundtrip_mailbox(
    *,
    config: PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig,
    host_role_assertion: bytes,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission:
    """Produce the only typed admission a future fixed Object-Storage adapter may use."""

    resolved = _config(config)
    assertion = _verify_host_role_assertion(host_role_assertion, config=resolved, now=now)
    return VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission(
        host_id=assertion.host_id,
        local_role=assertion.local_role,
        mailbox=assertion.mailbox,
        direction=assertion.direction,
        object_prefix=assertion.object_prefix,
        least_privilege_actions=assertion.least_privilege_actions,
        policy_sha256=assertion.policy_sha256,
        deployment_binding_sha256=assertion.deployment_binding_sha256,
        delivery_binding_sha256=assertion.delivery_binding_sha256,
        host_role_assertion_sha256=assertion.assertion_sha256,
        issued_at=assertion.issued_at,
        expires_at=assertion.expires_at,
        configuration_sha256=resolved.configuration_sha256,
        capability=_CAPABILITY,
    )


def require_verified_physical_wal_v2_witness_roundtrip_mailbox_admission(
    admission: object,
    *,
    config: PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission:
    """Revalidate a typed admission before a future role-local adapter uses it."""

    resolved = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CLOCK_INVALID")
    if (
        type(admission) is not VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission
        or admission._capability is not _CAPABILITY
        or admission.schema != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_SCHEMA
        or admission._configuration_sha256 != resolved.configuration_sha256
        or admission.host_id != resolved.host_id
        or admission.local_role != resolved.policy.local_role
        or admission.mailbox != resolved.policy.mailbox
        or admission.direction != resolved.policy.direction
        or admission.object_prefix != resolved.policy.object_prefix
        or admission.least_privilege_actions != resolved.policy.least_privilege_actions
        or admission.policy_sha256 != resolved.policy_sha256
        or admission.deployment_binding_sha256 != resolved.deployment_binding_sha256
        or admission.delivery_binding_sha256 != resolved.delivery_binding_sha256
        or _sha256(
            admission.host_role_assertion_sha256,
            code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CAPABILITY_INVALID",
        )
        != admission.host_role_assertion_sha256
        or admission.expires_at <= observed
        or admission.issued_at > observed
    ):
        _fail("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CAPABILITY_INVALID")
    expected_mapping = {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_SCHEMA,
        "host_id": admission.host_id,
        "local_role": admission.local_role,
        "mailbox": admission.mailbox,
        "direction": admission.direction,
        "object_prefix": admission.object_prefix,
        "least_privilege_actions": list(admission.least_privilege_actions),
        "policy_sha256": admission.policy_sha256,
        "deployment_binding_sha256": admission.deployment_binding_sha256,
        "delivery_binding_sha256": admission.delivery_binding_sha256,
        "host_role_assertion_sha256": admission.host_role_assertion_sha256,
        "issued_at": _render_timestamp(admission.issued_at),
        "expires_at": _render_timestamp(admission.expires_at),
        "configuration_sha256": admission._configuration_sha256,
    }
    if admission.admission_sha256 != _sha256_bytes(
        _canonical(expected_mapping, code="V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CAPABILITY_INVALID")
    ):
        _fail("V2_WITNESS_ROUNDTRIP_MAILBOX_ADMISSION_CAPABILITY_INVALID")
    return admission
