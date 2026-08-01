"""Pure, default-off V2R reverse-carrier signed wire contract.

V2R is deliberately a *new* emergency/failback carrier.  Its only permitted
route is ``WA-IR -> Witness -> WA-FI -> Witness -> WA-IR``.  This module has
no transport, provider, database, filesystem, lease, election, promotion, or
writer implementation.  Object storage may eventually carry these opaque
records, but is deliberately represented only as non-authoritative evidence.

The normal V2 contract is not imported: domains, mailbox prefix, IAM policy
pin, schemas and all four signer roles are independent and must be disjoint.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


__all__ = (
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX",
    "PHYSICAL_WAL_V2R_WITNESS_REVERSE_EXPORT_SCHEMA",
    "PHYSICAL_WAL_V2R_WITNESS_FORWARD_ENVELOPE_SCHEMA",
    "PHYSICAL_WAL_V2R_WITNESS_FI_ACK_SCHEMA",
    "PHYSICAL_WAL_V2R_WITNESS_RETURN_ENVELOPE_SCHEMA",
    "PhysicalWalV2rWitnessRoundtripConfig",
    "PhysicalWalV2rWitnessRoundtripError",
    "PhysicalWalV2rWitnessRoundtripReplayGuard",
    "PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard",
    "VerifiedPhysicalWalV2rWitnessReverseExport",
    "VerifiedPhysicalWalV2rWitnessForwardEnvelope",
    "VerifiedPhysicalWalV2rWitnessFiAck",
    "VerifiedPhysicalWalV2rWitnessReturnEnvelope",
    "build_physical_wal_v2r_witness_reverse_export",
    "build_physical_wal_v2r_witness_forward_envelope",
    "build_physical_wal_v2r_witness_fi_ack",
    "build_physical_wal_v2r_witness_return_envelope",
    "verify_physical_wal_v2r_witness_reverse_export",
    "verify_physical_wal_v2r_witness_forward_envelope",
    "verify_physical_wal_v2r_witness_fi_ack",
    "verify_physical_wal_v2r_witness_return_envelope",
)


PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DEFAULT_ENABLED = False
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN = (
    "gold-trade-physical-wal-v2r-witness-reverse-carrier-v1"
)
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX = "physical-wal-v2r-reverse/"
PHYSICAL_WAL_V2R_WITNESS_REVERSE_EXPORT_SCHEMA = (
    "gold-trade-physical-wal-v2r-witness-reverse-export-v1"
)
PHYSICAL_WAL_V2R_WITNESS_FORWARD_ENVELOPE_SCHEMA = (
    "gold-trade-physical-wal-v2r-witness-forward-envelope-v1"
)
PHYSICAL_WAL_V2R_WITNESS_FI_ACK_SCHEMA = (
    "gold-trade-physical-wal-v2r-witness-fi-ack-v1"
)
PHYSICAL_WAL_V2R_WITNESS_RETURN_ENVELOPE_SCHEMA = (
    "gold-trade-physical-wal-v2r-witness-return-envelope-v1"
)

_VERSION = 1
_MAX_WIRE_BYTES = 128 * 1024
_MAX_EVIDENCE_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 30
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RELEASE_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$", re.ASCII
)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_CLUSTER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$", re.ASCII)

_CARRIER_MODE = "opaque-carrier-evidence-only"
_ACK_STATUS = "fi-recovery-evidence-observed"
_RETURN_STATUS = "exact-fi-ack-returned"

_HOPS = {
    "ir-to-witness": ("wa-ir", "witness", "wa-ir-v2r-exporter", "witness-v2r-reverse-ingress"),
    "witness-to-fi": ("witness", "wa-fi", "witness-v2r-reverse-egress", "wa-fi-v2r-recovery-inbox"),
    "fi-to-witness": ("wa-fi", "witness", "wa-fi-v2r-ack-outbox", "witness-v2r-ack-ingress"),
    "witness-to-ir": ("witness", "wa-ir", "witness-v2r-return-egress", "wa-ir-v2r-return-inbox"),
}

_COMMON_FIELDS = frozenset(
    {
        "schema", "version", "kind", "protocol_domain", "mailbox", "mailbox_prefix",
        "sender_site", "recipient_site", "sender_role", "recipient_role",
        "configuration_sha256", "cluster_id", "release_sha", "stream_generation_id",
        "route_commitment_sha256", "reverse_frontier_sha256", "recovery_frontier_sha256",
        "blob_frontier_sha256", "v2r_iam_policy_sha256", "correlation_id", "chain_nonce",
        "carrier_mode", "object_storage_election_authority",
        "object_storage_lease_authority", "object_storage_writer_authority",
        "issued_at", "expires_at", "signer", "signature_base64",
    }
)
_EXPORT_FIELDS = _COMMON_FIELDS | frozenset(
    {"reverse_export_id", "reverse_export_nonce", "reverse_payload_commitment_sha256"}
)
_FORWARD_FIELDS = _COMMON_FIELDS | frozenset(
    {"forward_id", "forward_nonce", "ir_reverse_export_base64", "ir_reverse_export_sha256", "prior_hop_sha256"}
)
_ACK_FIELDS = _COMMON_FIELDS | frozenset(
    {"fi_ack_id", "fi_ack_nonce", "witness_forward_base64", "witness_forward_sha256",
     "ir_reverse_export_sha256", "prior_hop_sha256", "ack_status", "acknowledged_reverse_frontier_sha256"}
)
_RETURN_FIELDS = _COMMON_FIELDS | frozenset(
    {"return_id", "return_nonce", "fi_ack_base64", "fi_ack_sha256",
     "witness_forward_sha256", "ir_reverse_export_sha256", "prior_hop_sha256", "return_status"}
)
_STAGES = {
    "export": (PHYSICAL_WAL_V2R_WITNESS_REVERSE_EXPORT_SCHEMA, "ir-to-witness", "ir-export", _EXPORT_FIELDS, "ir_export_public_key"),
    "forward": (PHYSICAL_WAL_V2R_WITNESS_FORWARD_ENVELOPE_SCHEMA, "witness-to-fi", "witness-forward", _FORWARD_FIELDS, "witness_forward_public_key"),
    "ack": (PHYSICAL_WAL_V2R_WITNESS_FI_ACK_SCHEMA, "fi-to-witness", "fi-ack", _ACK_FIELDS, "fi_ack_public_key"),
    "return": (PHYSICAL_WAL_V2R_WITNESS_RETURN_ENVELOPE_SCHEMA, "witness-to-ir", "witness-return", _RETURN_FIELDS, "witness_return_public_key"),
}


class PhysicalWalV2rWitnessRoundtripError(ValueError):
    """A V2R wire input is foreign, unsafe, stale, or non-canonical."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rWitnessRoundtripError(code)


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripConfig:
    """Pins for the isolated reverse carrier; disabled unless explicitly enabled.

    ``normal_v2_*`` pins are mandatory deny-pins.  An installer must obtain
    them from the normal-V2 deployment so accidental domain/key/prefix/IAM
    reuse fails closed.  They are comparison inputs only, never a bridge.
    """

    cluster_id: str = ""
    release_sha: str = ""
    stream_generation_id: str = ""
    route_commitment_sha256: str = ""
    reverse_frontier_sha256: str = ""
    recovery_frontier_sha256: str = ""
    blob_frontier_sha256: str = ""
    v2r_iam_policy_sha256: str = ""
    normal_v2_protocol_domain: str = ""
    normal_v2_mailbox_prefix: str = ""
    normal_v2_iam_policy_sha256: str = ""
    normal_v2_public_key_sha256s: tuple[str, ...] = ()
    ir_export_public_key: bytes = b""
    witness_forward_public_key: bytes = b""
    fi_ack_public_key: bytes = b""
    witness_return_public_key: bytes = b""
    v2r_mailbox_prefix: str = PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX
    enabled: bool = PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = 60
    maximum_future_skew_seconds: int = 5


class PhysicalWalV2rWitnessRoundtripReplayGuard:
    """Trusted receiver-local replay-guard interface.

    A production adapter must supply a root-owned durable, atomic subclass.
    This contract never treats a guard as election, lease, or writer
    authority; it only forces a caller to provide one before accepting a
    received record.
    """

    def consume(self, *, stage: str, correlation_id: str) -> None:
        raise NotImplementedError


class PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(
    PhysicalWalV2rWitnessRoundtripReplayGuard
):
    """Process-local replay guard for one receiving endpoint.

    It deliberately is not durable and must be replaced by a root-owned,
    atomic local replay store before an adapter is ever installed.  It grants
    no writer, lease, election, promotion, or storage authority.
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def consume(self, *, stage: str, correlation_id: str) -> None:
        item = (stage, correlation_id)
        if item in self._seen:
            _fail("V2R_REPLAY_DETECTED")
        self._seen.add(item)


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2rWitnessReverseExport:
    canonical_reverse_export: bytes = field(repr=False)
    reverse_export_sha256: str
    correlation_id: str
    chain_nonce: str
    reverse_payload_commitment_sha256: str

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_VERIFIED_CAPABILITY_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2rWitnessForwardEnvelope:
    canonical_forward_envelope: bytes = field(repr=False)
    forward_envelope_sha256: str
    ir_reverse_export_sha256: str
    correlation_id: str
    chain_nonce: str

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_VERIFIED_CAPABILITY_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2rWitnessFiAck:
    canonical_fi_ack: bytes = field(repr=False)
    fi_ack_sha256: str
    witness_forward_sha256: str
    ir_reverse_export_sha256: str
    correlation_id: str
    chain_nonce: str

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_VERIFIED_CAPABILITY_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2rWitnessReturnEnvelope:
    canonical_return_envelope: bytes = field(repr=False)
    return_envelope_sha256: str
    fi_ack_sha256: str
    witness_forward_sha256: str
    ir_reverse_export_sha256: str
    correlation_id: str
    chain_nonce: str

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_VERIFIED_CAPABILITY_SERIALIZATION_FORBIDDEN")


_VERIFIED: WeakKeyDictionary[object, object] = WeakKeyDictionary()


def _mark_verified(value: object) -> object:
    _VERIFIED[value] = object()
    return value


def _require_verified(value: object, expected: type[Any], code: str) -> Any:
    if not isinstance(value, expected) or _VERIFIED.get(value) is None:
        _fail(code)
    return value


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError):
        _fail("V2R_CANONICAL_ENCODING_INVALID")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_text(value: object, code: str, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value or (pattern is not None and pattern.fullmatch(value) is None):
        _fail(code)
    return value


def _require_sha256(value: object, code: str) -> str:
    return _require_text(value, code, _SHA256_RE)


def _parse_time(value: object, code: str) -> datetime:
    text = _require_text(value, code, _TIMESTAMP_RE)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        _fail(code)
    if parsed.tzinfo != timezone.utc:
        _fail(code)
    return parsed


def _time_text(value: datetime, code: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        _fail(code)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _b64_encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64_decode(value: object, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError, binascii.Error):
        _fail(code)
    if not result or len(result) > _MAX_WIRE_BYTES:
        _fail(code)
    return result


def _decode_canonical_mapping(value: object, code: str) -> dict[str, Any]:
    raw = _b64_decode(value, code)
    try:
        decoded = json.loads(raw.decode("ascii"), object_pairs_hook=_no_duplicate_object)
    except (UnicodeDecodeError, json.JSONDecodeError, PhysicalWalV2rWitnessRoundtripError):
        _fail(code)
    if type(decoded) is not dict or _canonical_bytes(decoded) != raw:
        _fail(code)
    return decoded


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2R_DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def _public_key_bytes(value: object, code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + _sha256(public_key)


@dataclass(frozen=True)
class _ValidatedConfig:
    config: PhysicalWalV2rWitnessRoundtripConfig
    configuration_sha256: str
    keys: dict[str, bytes]


def _validated_config(config: object) -> _ValidatedConfig:
    if not isinstance(config, PhysicalWalV2rWitnessRoundtripConfig):
        _fail("V2R_CONFIG_REQUIRED")
    if config.enabled is not True:
        _fail("V2R_DEFAULT_DISABLED")
    _require_text(config.cluster_id, "V2R_CLUSTER_INVALID", _CLUSTER_RE)
    _require_text(config.release_sha, "V2R_RELEASE_INVALID", _RELEASE_RE)
    _require_text(config.stream_generation_id, "V2R_GENERATION_INVALID", _ID_RE)
    for field_name in ("route_commitment_sha256", "reverse_frontier_sha256", "recovery_frontier_sha256", "blob_frontier_sha256", "v2r_iam_policy_sha256", "normal_v2_iam_policy_sha256"):
        _require_sha256(getattr(config, field_name), "V2R_" + field_name.upper() + "_INVALID")
    _require_text(config.normal_v2_protocol_domain, "V2R_NORMAL_DOMAIN_REQUIRED")
    _require_text(config.normal_v2_mailbox_prefix, "V2R_NORMAL_PREFIX_REQUIRED")
    if config.normal_v2_protocol_domain == PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN:
        _fail("V2R_NORMAL_DOMAIN_REUSED")
    if config.v2r_mailbox_prefix != PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX:
        _fail("V2R_MAILBOX_PREFIX_INVALID")
    if (
        config.normal_v2_mailbox_prefix == config.v2r_mailbox_prefix
        or config.normal_v2_mailbox_prefix.startswith(config.v2r_mailbox_prefix)
        or config.v2r_mailbox_prefix.startswith(config.normal_v2_mailbox_prefix)
    ):
        _fail("V2R_NORMAL_PREFIX_REUSED")
    if config.normal_v2_iam_policy_sha256 == config.v2r_iam_policy_sha256:
        _fail("V2R_NORMAL_IAM_PIN_REUSED")
    if type(config.normal_v2_public_key_sha256s) is not tuple or not config.normal_v2_public_key_sha256s:
        _fail("V2R_NORMAL_KEY_DENY_PINS_REQUIRED")
    normal_key_hashes = tuple(_require_sha256(item, "V2R_NORMAL_KEY_DENY_PIN_INVALID") for item in config.normal_v2_public_key_sha256s)
    if len(set(normal_key_hashes)) != len(normal_key_hashes):
        _fail("V2R_NORMAL_KEY_DENY_PIN_DUPLICATE")
    keys = {name: _public_key_bytes(getattr(config, name), "V2R_" + name.upper() + "_INVALID") for name in ("ir_export_public_key", "witness_forward_public_key", "fi_ack_public_key", "witness_return_public_key")}
    if len(set(keys.values())) != len(keys):
        _fail("V2R_SIGNER_KEY_REUSE_FORBIDDEN")
    if set(_sha256(value) for value in keys.values()) & set(normal_key_hashes):
        _fail("V2R_NORMAL_KEY_REUSE_FORBIDDEN")
    if type(config.maximum_evidence_age_seconds) is not int or not 1 <= config.maximum_evidence_age_seconds <= _MAX_EVIDENCE_AGE_SECONDS:
        _fail("V2R_MAXIMUM_EVIDENCE_AGE_INVALID")
    if type(config.maximum_future_skew_seconds) is not int or not 0 <= config.maximum_future_skew_seconds <= _MAX_FUTURE_SKEW_SECONDS:
        _fail("V2R_MAXIMUM_FUTURE_SKEW_INVALID")
    identity = {
        "protocol_domain": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN,
        "mailbox_prefix": config.v2r_mailbox_prefix,
        "cluster_id": config.cluster_id, "release_sha": config.release_sha,
        "stream_generation_id": config.stream_generation_id,
        "route_commitment_sha256": config.route_commitment_sha256,
        "reverse_frontier_sha256": config.reverse_frontier_sha256,
        "recovery_frontier_sha256": config.recovery_frontier_sha256,
        "blob_frontier_sha256": config.blob_frontier_sha256,
        "v2r_iam_policy_sha256": config.v2r_iam_policy_sha256,
        "normal_v2_protocol_domain": config.normal_v2_protocol_domain,
        "normal_v2_mailbox_prefix": config.normal_v2_mailbox_prefix,
        "normal_v2_iam_policy_sha256": config.normal_v2_iam_policy_sha256,
        "normal_v2_public_key_sha256s": list(normal_key_hashes),
        "v2r_signer_key_ids": {name: _key_id(key) for name, key in sorted(keys.items())},
    }
    return _ValidatedConfig(config=config, configuration_sha256=_sha256(_canonical_bytes(identity)), keys=keys)


def _common(config: _ValidatedConfig, stage: str, *, correlation_id: str, chain_nonce: str, expires_at: datetime, now: datetime) -> dict[str, Any]:
    _require_text(correlation_id, "V2R_CORRELATION_INVALID", _ID_RE)
    _require_text(chain_nonce, "V2R_CHAIN_NONCE_INVALID", _NONCE_RE)
    schema, mailbox, kind, _fields, _key = _STAGES[stage]
    sender_site, recipient_site, sender_role, recipient_role = _HOPS[mailbox]
    return {
        "schema": schema, "version": _VERSION, "kind": kind,
        "protocol_domain": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN,
        "mailbox": mailbox, "mailbox_prefix": config.config.v2r_mailbox_prefix,
        "sender_site": sender_site, "recipient_site": recipient_site,
        "sender_role": sender_role, "recipient_role": recipient_role,
        "configuration_sha256": config.configuration_sha256,
        "cluster_id": config.config.cluster_id, "release_sha": config.config.release_sha,
        "stream_generation_id": config.config.stream_generation_id,
        "route_commitment_sha256": config.config.route_commitment_sha256,
        "reverse_frontier_sha256": config.config.reverse_frontier_sha256,
        "recovery_frontier_sha256": config.config.recovery_frontier_sha256,
        "blob_frontier_sha256": config.config.blob_frontier_sha256,
        "v2r_iam_policy_sha256": config.config.v2r_iam_policy_sha256,
        "correlation_id": correlation_id, "chain_nonce": chain_nonce,
        "carrier_mode": _CARRIER_MODE,
        "object_storage_election_authority": False,
        "object_storage_lease_authority": False,
        "object_storage_writer_authority": False,
        "issued_at": _time_text(now, "V2R_ISSUED_AT_INVALID"),
        "expires_at": _time_text(expires_at, "V2R_EXPIRES_AT_INVALID"),
    }


def _signature_payload(stage: str, unsigned: Mapping[str, Any]) -> bytes:
    return (
        PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN.encode("ascii")
        + b"\x00" + _STAGES[stage][2].encode("ascii") + b"\x00" + _canonical_bytes(dict(unsigned))
    )


def _sign(stage: str, record: dict[str, Any], signer: object, expected_public_key: bytes) -> dict[str, Any]:
    if not isinstance(signer, Ed25519PrivateKey):
        _fail("V2R_SIGNER_PRIVATE_KEY_REQUIRED")
    actual_public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if actual_public != expected_public_key:
        _fail("V2R_SIGNER_KEY_MISMATCH")
    record["signer"] = {"algorithm": "ed25519", "key_id": _key_id(actual_public)}
    signature = signer.sign(_signature_payload(stage, record))
    record["signature_base64"] = _b64_encode(signature)
    return record


def _prepare_build(config: object, expires_at: datetime, now: datetime) -> _ValidatedConfig:
    validated = _validated_config(config)
    issued = _time_text(now, "V2R_NOW_INVALID")
    del issued
    expiry = _time_text(expires_at, "V2R_EXPIRES_AT_INVALID")
    del expiry
    if expires_at <= now or expires_at - now > timedelta(seconds=validated.config.maximum_evidence_age_seconds):
        _fail("V2R_EVIDENCE_LIFETIME_INVALID")
    return validated


def build_physical_wal_v2r_witness_reverse_export(*, config: PhysicalWalV2rWitnessRoundtripConfig, correlation_id: str, chain_nonce: str, reverse_export_id: str, reverse_export_nonce: str, reverse_payload_commitment_sha256: str, expires_at: datetime, ir_export_signer: Ed25519PrivateKey, now: datetime) -> dict[str, Any]:
    """Build only the first, fixed WA-IR -> Witness evidence record."""
    checked = _prepare_build(config, expires_at, now)
    _require_text(reverse_export_id, "V2R_EXPORT_ID_INVALID", _ID_RE)
    _require_text(reverse_export_nonce, "V2R_EXPORT_NONCE_INVALID", _NONCE_RE)
    _require_sha256(reverse_payload_commitment_sha256, "V2R_REVERSE_PAYLOAD_COMMITMENT_INVALID")
    record = _common(checked, "export", correlation_id=correlation_id, chain_nonce=chain_nonce, expires_at=expires_at, now=now)
    record.update({"reverse_export_id": reverse_export_id, "reverse_export_nonce": reverse_export_nonce, "reverse_payload_commitment_sha256": reverse_payload_commitment_sha256})
    return _sign("export", record, ir_export_signer, checked.keys["ir_export_public_key"])


def build_physical_wal_v2r_witness_forward_envelope(*, config: PhysicalWalV2rWitnessRoundtripConfig, ir_reverse_export: VerifiedPhysicalWalV2rWitnessReverseExport, forward_id: str, forward_nonce: str, expires_at: datetime, witness_forward_signer: Ed25519PrivateKey, now: datetime) -> dict[str, Any]:
    """Build only Witness -> WA-FI around an already verified exact export."""
    checked = _prepare_build(config, expires_at, now)
    export = _require_verified(ir_reverse_export, VerifiedPhysicalWalV2rWitnessReverseExport, "V2R_VERIFIED_EXPORT_REQUIRED")
    _require_text(forward_id, "V2R_FORWARD_ID_INVALID", _ID_RE)
    _require_text(forward_nonce, "V2R_FORWARD_NONCE_INVALID", _NONCE_RE)
    record = _common(checked, "forward", correlation_id=export.correlation_id, chain_nonce=export.chain_nonce, expires_at=expires_at, now=now)
    record.update({"forward_id": forward_id, "forward_nonce": forward_nonce, "ir_reverse_export_base64": _b64_encode(export.canonical_reverse_export), "ir_reverse_export_sha256": export.reverse_export_sha256, "prior_hop_sha256": export.reverse_export_sha256})
    return _sign("forward", record, witness_forward_signer, checked.keys["witness_forward_public_key"])


def build_physical_wal_v2r_witness_fi_ack(*, config: PhysicalWalV2rWitnessRoundtripConfig, witness_forward: VerifiedPhysicalWalV2rWitnessForwardEnvelope, fi_ack_id: str, fi_ack_nonce: str, expires_at: datetime, fi_ack_signer: Ed25519PrivateKey, now: datetime) -> dict[str, Any]:
    """Build only WA-FI -> Witness acknowledgement of the exact forward hop."""
    checked = _prepare_build(config, expires_at, now)
    forward = _require_verified(witness_forward, VerifiedPhysicalWalV2rWitnessForwardEnvelope, "V2R_VERIFIED_FORWARD_REQUIRED")
    _require_text(fi_ack_id, "V2R_FI_ACK_ID_INVALID", _ID_RE)
    _require_text(fi_ack_nonce, "V2R_FI_ACK_NONCE_INVALID", _NONCE_RE)
    record = _common(checked, "ack", correlation_id=forward.correlation_id, chain_nonce=forward.chain_nonce, expires_at=expires_at, now=now)
    record.update({"fi_ack_id": fi_ack_id, "fi_ack_nonce": fi_ack_nonce, "witness_forward_base64": _b64_encode(forward.canonical_forward_envelope), "witness_forward_sha256": forward.forward_envelope_sha256, "ir_reverse_export_sha256": forward.ir_reverse_export_sha256, "prior_hop_sha256": forward.forward_envelope_sha256, "ack_status": _ACK_STATUS, "acknowledged_reverse_frontier_sha256": checked.config.reverse_frontier_sha256})
    return _sign("ack", record, fi_ack_signer, checked.keys["fi_ack_public_key"])


def build_physical_wal_v2r_witness_return_envelope(*, config: PhysicalWalV2rWitnessRoundtripConfig, fi_ack: VerifiedPhysicalWalV2rWitnessFiAck, return_id: str, return_nonce: str, expires_at: datetime, witness_return_signer: Ed25519PrivateKey, now: datetime) -> dict[str, Any]:
    """Build only final Witness -> WA-IR return of the exact FI acknowledgement."""
    checked = _prepare_build(config, expires_at, now)
    ack = _require_verified(fi_ack, VerifiedPhysicalWalV2rWitnessFiAck, "V2R_VERIFIED_FI_ACK_REQUIRED")
    _require_text(return_id, "V2R_RETURN_ID_INVALID", _ID_RE)
    _require_text(return_nonce, "V2R_RETURN_NONCE_INVALID", _NONCE_RE)
    record = _common(checked, "return", correlation_id=ack.correlation_id, chain_nonce=ack.chain_nonce, expires_at=expires_at, now=now)
    record.update({"return_id": return_id, "return_nonce": return_nonce, "fi_ack_base64": _b64_encode(ack.canonical_fi_ack), "fi_ack_sha256": ack.fi_ack_sha256, "witness_forward_sha256": ack.witness_forward_sha256, "ir_reverse_export_sha256": ack.ir_reverse_export_sha256, "prior_hop_sha256": ack.fi_ack_sha256, "return_status": _RETURN_STATUS})
    return _sign("return", record, witness_return_signer, checked.keys["witness_return_public_key"])


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return dict(value)


def _verify_common(stage: str, record: Mapping[str, Any], config: _ValidatedConfig, now: datetime) -> tuple[dict[str, Any], bytes, str]:
    value = _mapping(record, "V2R_RECORD_MAPPING_REQUIRED")
    schema, mailbox, kind, fields, key_name = _STAGES[stage]
    if frozenset(value) != fields:
        _fail("V2R_FIELD_SET_INVALID")
    if value["schema"] != schema or value["version"] != _VERSION or value["kind"] != kind:
        _fail("V2R_SCHEMA_INVALID")
    if value["protocol_domain"] != PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN:
        _fail("V2R_PROTOCOL_DOMAIN_MISMATCH")
    if value["mailbox_prefix"] != config.config.v2r_mailbox_prefix:
        _fail("V2R_MAILBOX_PREFIX_MISMATCH")
    if value["mailbox"] != mailbox:
        _fail("V2R_DIRECT_OR_FOREIGN_ROUTE_REJECTED")
    expected_route = _HOPS[mailbox]
    if tuple(value[name] for name in ("sender_site", "recipient_site", "sender_role", "recipient_role")) != expected_route:
        _fail("V2R_ROLE_ROUTE_INVALID")
    if value["release_sha"] != config.config.release_sha:
        _fail("V2R_RELEASE_MISMATCH")
    if value["stream_generation_id"] != config.config.stream_generation_id:
        _fail("V2R_GENERATION_MISMATCH")
    for field_name in ("route_commitment_sha256", "reverse_frontier_sha256", "recovery_frontier_sha256", "blob_frontier_sha256", "v2r_iam_policy_sha256"):
        if value[field_name] != getattr(config.config, field_name):
            _fail("V2R_" + field_name.upper() + "_MISMATCH")
    if value["cluster_id"] != config.config.cluster_id or value["configuration_sha256"] != config.configuration_sha256:
        _fail("V2R_CONFIGURATION_MISMATCH")
    _require_text(value["correlation_id"], "V2R_CORRELATION_INVALID", _ID_RE)
    _require_text(value["chain_nonce"], "V2R_CHAIN_NONCE_INVALID", _NONCE_RE)
    if value["carrier_mode"] != _CARRIER_MODE or any(value[name] is not False for name in ("object_storage_election_authority", "object_storage_lease_authority", "object_storage_writer_authority")):
        _fail("V2R_OBJECT_STORAGE_AUTHORITY_FORBIDDEN")
    issued_at = _parse_time(value["issued_at"], "V2R_ISSUED_AT_INVALID")
    expires_at = _parse_time(value["expires_at"], "V2R_EXPIRES_AT_INVALID")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        _fail("V2R_NOW_INVALID")
    if expires_at <= issued_at or expires_at - issued_at > timedelta(seconds=config.config.maximum_evidence_age_seconds):
        _fail("V2R_EVIDENCE_LIFETIME_INVALID")
    if issued_at > now + timedelta(seconds=config.config.maximum_future_skew_seconds) or expires_at < now or now - issued_at > timedelta(seconds=config.config.maximum_evidence_age_seconds):
        _fail("V2R_EVIDENCE_STALE_OR_FUTURE")
    signer = _mapping(value["signer"], "V2R_SIGNER_INVALID")
    if frozenset(signer) != frozenset({"algorithm", "key_id"}) or signer.get("algorithm") != "ed25519" or signer.get("key_id") != _key_id(config.keys[key_name]):
        _fail("V2R_SIGNER_IDENTITY_MISMATCH")
    signature = _b64_decode(value["signature_base64"], "V2R_SIGNATURE_ENCODING_INVALID")
    unsigned = dict(value)
    del unsigned["signature_base64"]
    try:
        Ed25519PublicKey.from_public_bytes(config.keys[key_name]).verify(signature, _signature_payload(stage, unsigned))
    except (InvalidSignature, ValueError):
        _fail("V2R_SIGNATURE_INVALID")
    canonical = _canonical_bytes(value)
    if len(canonical) > _MAX_WIRE_BYTES:
        _fail("V2R_WIRE_TOO_LARGE")
    return value, canonical, _sha256(canonical)


def _consume(guard: object, stage: str, correlation_id: str) -> None:
    if not isinstance(guard, PhysicalWalV2rWitnessRoundtripReplayGuard):
        _fail("V2R_REPLAY_GUARD_REQUIRED")
    try:
        guard.consume(stage=stage, correlation_id=correlation_id)
    except NotImplementedError:
        _fail("V2R_REPLAY_GUARD_UNIMPLEMENTED")


def _require_local_receiver(stage: str, local_site: object, local_role: object) -> None:
    """Bind a public verifier call to the one recipient of that mailbox.

    Nested historical records are checked for their fixed route separately;
    only a top-level receive operation may claim a local endpoint.  This keeps
    a future generic carrier from accepting a valid V2R record on the wrong
    host/role by merely reusing the shared verification configuration.
    """
    _schema, mailbox, _kind, _fields, _key = _STAGES[stage]
    _sender_site, expected_site, _sender_role, expected_role = _HOPS[mailbox]
    if local_site != expected_site or local_role != expected_role:
        _fail("V2R_LOCAL_RECIPIENT_MISMATCH")


def verify_physical_wal_v2r_witness_reverse_export(reverse_export: Mapping[str, Any], *, config: PhysicalWalV2rWitnessRoundtripConfig, now: datetime, replay_guard: PhysicalWalV2rWitnessRoundtripReplayGuard, local_site: str, local_role: str) -> VerifiedPhysicalWalV2rWitnessReverseExport:
    checked = _validated_config(config)
    _require_local_receiver("export", local_site, local_role)
    value, canonical, digest = _verify_common("export", reverse_export, checked, now)
    _require_text(value["reverse_export_id"], "V2R_EXPORT_ID_INVALID", _ID_RE)
    _require_text(value["reverse_export_nonce"], "V2R_EXPORT_NONCE_INVALID", _NONCE_RE)
    commitment = _require_sha256(value["reverse_payload_commitment_sha256"], "V2R_REVERSE_PAYLOAD_COMMITMENT_INVALID")
    _consume(replay_guard, "export", value["correlation_id"])
    return _mark_verified(VerifiedPhysicalWalV2rWitnessReverseExport(canonical, digest, value["correlation_id"], value["chain_nonce"], commitment))  # type: ignore[return-value]


def _verify_export_nested(value: Mapping[str, Any], checked: _ValidatedConfig, now: datetime) -> VerifiedPhysicalWalV2rWitnessReverseExport:
    record, canonical, digest = _verify_common("export", value, checked, now)
    _require_text(record["reverse_export_id"], "V2R_EXPORT_ID_INVALID", _ID_RE)
    _require_text(record["reverse_export_nonce"], "V2R_EXPORT_NONCE_INVALID", _NONCE_RE)
    return _mark_verified(VerifiedPhysicalWalV2rWitnessReverseExport(canonical, digest, record["correlation_id"], record["chain_nonce"], _require_sha256(record["reverse_payload_commitment_sha256"], "V2R_REVERSE_PAYLOAD_COMMITMENT_INVALID")))  # type: ignore[return-value]


def verify_physical_wal_v2r_witness_forward_envelope(forward_envelope: Mapping[str, Any], *, config: PhysicalWalV2rWitnessRoundtripConfig, now: datetime, replay_guard: PhysicalWalV2rWitnessRoundtripReplayGuard, local_site: str, local_role: str) -> VerifiedPhysicalWalV2rWitnessForwardEnvelope:
    checked = _validated_config(config)
    _require_local_receiver("forward", local_site, local_role)
    result = _verify_forward(forward_envelope, checked, now)
    _consume(replay_guard, "forward", result.correlation_id)
    return result


def _verify_forward(forward_envelope: Mapping[str, Any], checked: _ValidatedConfig, now: datetime) -> VerifiedPhysicalWalV2rWitnessForwardEnvelope:
    value, canonical, digest = _verify_common("forward", forward_envelope, checked, now)
    export_raw = _decode_canonical_mapping(value["ir_reverse_export_base64"], "V2R_NESTED_EXPORT_INVALID")
    export = _verify_export_nested(export_raw, checked, now)
    if value["ir_reverse_export_sha256"] != export.reverse_export_sha256 or value["prior_hop_sha256"] != export.reverse_export_sha256:
        _fail("V2R_EXACT_ACK_CHAIN_INVALID")
    if value["correlation_id"] != export.correlation_id or value["chain_nonce"] != export.chain_nonce:
        _fail("V2R_CORRELATION_CHAIN_MISMATCH")
    _require_text(value["forward_id"], "V2R_FORWARD_ID_INVALID", _ID_RE)
    _require_text(value["forward_nonce"], "V2R_FORWARD_NONCE_INVALID", _NONCE_RE)
    return _mark_verified(VerifiedPhysicalWalV2rWitnessForwardEnvelope(canonical, digest, export.reverse_export_sha256, export.correlation_id, export.chain_nonce))  # type: ignore[return-value]


def verify_physical_wal_v2r_witness_fi_ack(fi_ack: Mapping[str, Any], *, config: PhysicalWalV2rWitnessRoundtripConfig, now: datetime, replay_guard: PhysicalWalV2rWitnessRoundtripReplayGuard, local_site: str, local_role: str) -> VerifiedPhysicalWalV2rWitnessFiAck:
    checked = _validated_config(config)
    _require_local_receiver("ack", local_site, local_role)
    result = _verify_ack(fi_ack, checked, now)
    _consume(replay_guard, "ack", result.correlation_id)
    return result


def _verify_ack(fi_ack: Mapping[str, Any], checked: _ValidatedConfig, now: datetime) -> VerifiedPhysicalWalV2rWitnessFiAck:
    value, canonical, digest = _verify_common("ack", fi_ack, checked, now)
    forward_raw = _decode_canonical_mapping(value["witness_forward_base64"], "V2R_NESTED_FORWARD_INVALID")
    forward = _verify_forward(forward_raw, checked, now)
    if value["witness_forward_sha256"] != forward.forward_envelope_sha256 or value["ir_reverse_export_sha256"] != forward.ir_reverse_export_sha256 or value["prior_hop_sha256"] != forward.forward_envelope_sha256:
        _fail("V2R_EXACT_ACK_CHAIN_INVALID")
    if value["correlation_id"] != forward.correlation_id or value["chain_nonce"] != forward.chain_nonce or value["ack_status"] != _ACK_STATUS or value["acknowledged_reverse_frontier_sha256"] != checked.config.reverse_frontier_sha256:
        _fail("V2R_CORRELATION_CHAIN_MISMATCH")
    _require_text(value["fi_ack_id"], "V2R_FI_ACK_ID_INVALID", _ID_RE)
    _require_text(value["fi_ack_nonce"], "V2R_FI_ACK_NONCE_INVALID", _NONCE_RE)
    return _mark_verified(VerifiedPhysicalWalV2rWitnessFiAck(canonical, digest, forward.forward_envelope_sha256, forward.ir_reverse_export_sha256, forward.correlation_id, forward.chain_nonce))  # type: ignore[return-value]


def verify_physical_wal_v2r_witness_return_envelope(return_envelope: Mapping[str, Any], *, config: PhysicalWalV2rWitnessRoundtripConfig, now: datetime, replay_guard: PhysicalWalV2rWitnessRoundtripReplayGuard, local_site: str, local_role: str) -> VerifiedPhysicalWalV2rWitnessReturnEnvelope:
    """Verify the complete exact four-hop chain at the WA-IR return endpoint."""
    checked = _validated_config(config)
    _require_local_receiver("return", local_site, local_role)
    value, canonical, digest = _verify_common("return", return_envelope, checked, now)
    ack_raw = _decode_canonical_mapping(value["fi_ack_base64"], "V2R_NESTED_FI_ACK_INVALID")
    ack = _verify_ack(ack_raw, checked, now)
    if value["fi_ack_sha256"] != ack.fi_ack_sha256 or value["witness_forward_sha256"] != ack.witness_forward_sha256 or value["ir_reverse_export_sha256"] != ack.ir_reverse_export_sha256 or value["prior_hop_sha256"] != ack.fi_ack_sha256:
        _fail("V2R_EXACT_ACK_CHAIN_INVALID")
    if value["correlation_id"] != ack.correlation_id or value["chain_nonce"] != ack.chain_nonce or value["return_status"] != _RETURN_STATUS:
        _fail("V2R_CORRELATION_CHAIN_MISMATCH")
    _require_text(value["return_id"], "V2R_RETURN_ID_INVALID", _ID_RE)
    _require_text(value["return_nonce"], "V2R_RETURN_NONCE_INVALID", _NONCE_RE)
    _consume(replay_guard, "return", value["correlation_id"])
    return _mark_verified(VerifiedPhysicalWalV2rWitnessReturnEnvelope(canonical, digest, ack.fi_ack_sha256, ack.witness_forward_sha256, ack.ir_reverse_export_sha256, ack.correlation_id, ack.chain_nonce))  # type: ignore[return-value]
