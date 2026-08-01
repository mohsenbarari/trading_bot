"""Pure signed evidence grammar for the operational FI/IR failover runtime.

This module is intentionally only a wire-contract boundary.  It has no
filesystem, network, database, Object Storage, process, service, writer-start,
traffic, or promotion action.  In particular, a verified promotion grant is
*evidence* that a future root-owned Witness coordinator may consume; it is not
a capability to mutate data or start a writer.

The contract is separate from Full-Matrix V4 and from every V2/legacy transport
grammar.  A later operational runtime may carry these canonical bytes over
role-local mailboxes, but cannot obtain authority merely by parsing them here.

`fi_self_fence_receipt_sha256` is deliberately only a correlation pin.  Even a
validly signed receipt hash is not proof that FI has actually been fenced: a
future root-owned CAS/term ledger must bind the complete receipt to an
enforced fence transition before any mutation can be considered.  This module
does not implement that ledger or make such a decision.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.append_only_sync_delta_batch import LEASE_ID_RE


__all__ = (
    "DEFAULT_PHYSICAL_OPERATIONAL_FAILOVER_V1_MAXIMUM_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_SCHEMA",
    "PhysicalOperationalFailoverV1Error",
    "PhysicalOperationalFailoverV1FiSelfFenceReceiptInput",
    "PhysicalOperationalFailoverV1IrPromotionCompletionInput",
    "PhysicalOperationalFailoverV1IrPromotionRequestInput",
    "PhysicalOperationalFailoverV1Pins",
    "PhysicalOperationalFailoverV1Term",
    "PhysicalOperationalFailoverV1VerificationConfig",
    "PhysicalOperationalFailoverV1WitnessPromotionGrantInput",
    "VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt",
    "VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion",
    "VerifiedPhysicalOperationalFailoverV1IrPromotionRequest",
    "VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant",
    "require_verified_physical_operational_failover_v1_fi_self_fence_receipt",
    "require_verified_physical_operational_failover_v1_ir_promotion_completion",
    "require_verified_physical_operational_failover_v1_ir_promotion_request",
    "require_verified_physical_operational_failover_v1_witness_promotion_grant",
    "sign_physical_operational_failover_v1_fi_self_fence_receipt",
    "sign_physical_operational_failover_v1_ir_promotion_completion",
    "sign_physical_operational_failover_v1_ir_promotion_request",
    "sign_physical_operational_failover_v1_witness_promotion_grant",
    "verify_physical_operational_failover_v1_fi_self_fence_receipt",
    "verify_physical_operational_failover_v1_ir_promotion_completion",
    "verify_physical_operational_failover_v1_ir_promotion_request",
    "verify_physical_operational_failover_v1_witness_promotion_grant",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_SCHEMA = "gold-trade-physical-operational-failover-v1"
PHYSICAL_OPERATIONAL_FAILOVER_V1_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_OPERATIONAL_FAILOVER_V1_MAXIMUM_EVIDENCE_AGE_SECONDS = 60

_VERSION = 1
_MAX_EVIDENCE_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_MAX_WIRE_BYTES = 128 * 1024
_MAX_TERM_DURATION_SECONDS = 300
_ZERO_SHA256 = "0" * 64

_FI_SELF_FENCE_SCHEMA = PHYSICAL_OPERATIONAL_FAILOVER_V1_SCHEMA + "/fi-self-fence-receipt-v1"
_IR_PROMOTION_REQUEST_SCHEMA = PHYSICAL_OPERATIONAL_FAILOVER_V1_SCHEMA + "/ir-promotion-request-v1"
_WITNESS_PROMOTION_GRANT_SCHEMA = PHYSICAL_OPERATIONAL_FAILOVER_V1_SCHEMA + "/witness-promotion-grant-v1"
_IR_PROMOTION_COMPLETION_SCHEMA = PHYSICAL_OPERATIONAL_FAILOVER_V1_SCHEMA + "/ir-promotion-completion-v1"

_FI_SELF_FENCE_DOMAIN = (_FI_SELF_FENCE_SCHEMA + "\x00").encode("ascii")
_IR_PROMOTION_REQUEST_DOMAIN = (_IR_PROMOTION_REQUEST_SCHEMA + "\x00").encode("ascii")
_WITNESS_PROMOTION_GRANT_DOMAIN = (_WITNESS_PROMOTION_GRANT_SCHEMA + "\x00").encode("ascii")
_IR_PROMOTION_COMPLETION_DOMAIN = (_IR_PROMOTION_COMPLETION_SCHEMA + "\x00").encode("ascii")

_CLUSTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
_RELEASE_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_SITES = frozenset({"webapp_fi", "webapp_ir", "witness"})
_FENCE_REASONS = frozenset({"ack-unavailable", "term-expiring"})
_TERMINATION_REASONS = frozenset({"fi-self-fence-receipt", "predecessor-term-expired"})
_PROMOTED_IR_MODE = "promoted_ir_writer"
_COMPLETION_STATUS = "local-promotion-completed-evidence-only"
_CAPABILITY = object()

_PINS_FIELDS = frozenset(
    {
        "cluster_id",
        "release_sha",
        "stream_generation_id",
        "route_binding_sha256",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "recovery_frontier_wal_lsn",
        "blob_frontier_wal_lsn",
    }
)
_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
        "issued_at",
        "expires_at",
    }
)
_FI_SELF_FENCE_FIELDS = frozenset(
    {
        "schema",
        "version",
        "issuer_site",
        "receipt_id",
        "receipt_nonce",
        "issued_at",
        "expires_at",
        "replay_key_sha256",
        "pins",
        "predecessor_term",
        "predecessor_term_sha256",
        "fence_reason",
        "last_final_ack_sha256",
        "last_committed_frontier_wal_lsn",
        "signature_base64",
    }
)
_IR_PROMOTION_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "issuer_site",
        "request_id",
        "request_nonce",
        "issued_at",
        "expires_at",
        "replay_key_sha256",
        "pins",
        "predecessor_term",
        "predecessor_term_sha256",
        "predecessor_termination_reason",
        "fi_self_fence_receipt_sha256",
        "recovery_evidence_sha256",
        "p0_policy_bundle_sha256",
        "requested_activation_mode",
        "signature_base64",
    }
)
_WITNESS_PROMOTION_GRANT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "issuer_site",
        "granted_site",
        "grant_id",
        "grant_nonce",
        "issued_at",
        "expires_at",
        "replay_key_sha256",
        "pins",
        "request_sha256",
        "request_id",
        "request_nonce",
        "predecessor_term",
        "predecessor_term_sha256",
        "predecessor_termination_reason",
        "fi_self_fence_receipt_sha256",
        "successor_term",
        "successor_term_sha256",
        "activation_mode",
        "activation_route_artifact_sha256",
        "activation_receiver_permit_sha256",
        "witness_ledger_sequence",
        "witness_ledger_entry_sha256",
        "witness_ledger_previous_head_sha256",
        "signature_base64",
    }
)
_IR_PROMOTION_COMPLETION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "issuer_site",
        "completed_site",
        "completion_id",
        "completion_nonce",
        "issued_at",
        "expires_at",
        "replay_key_sha256",
        "pins",
        "predecessor_term",
        "predecessor_term_sha256",
        "predecessor_termination_reason",
        "fi_self_fence_receipt_sha256",
        "grant_sha256",
        "grant_id",
        "grant_nonce",
        "successor_term",
        "successor_term_sha256",
        "activation_mode",
        "activation_route_artifact_sha256",
        "activation_receiver_permit_sha256",
        "promotion_record_sha256",
        "recovery_evidence_sha256",
        "p0_execution_sha256",
        "traffic_fence_receipt_sha256",
        "completion_status",
        "signature_base64",
    }
)


class PhysicalOperationalFailoverV1Error(ValueError):
    """The supplied evidence is foreign, stale, noncanonical, or unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1Pins:
    """Release-bound continuity pins shared by every operational evidence item."""

    cluster_id: str = ""
    release_sha: str = ""
    stream_generation_id: str = ""
    route_binding_sha256: str = ""
    baseline_generation_id: str = ""
    baseline_manifest_sha256: str = ""
    recovery_frontier_wal_lsn: str = ""
    blob_frontier_wal_lsn: str = ""


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1Term:
    """One signed-term projection; this value itself grants no authority."""

    holder_site: str = ""
    writer_epoch: int = 0
    writer_lease_id: str = ""
    witness_transition_id: str = ""
    witnessed_term_proof_sha256: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1VerificationConfig:
    """Default-off public verification policy for one three-site cluster."""

    pins: PhysicalOperationalFailoverV1Pins | None = None
    fi_self_fence_signer_public_key: bytes = b""
    ir_promotion_request_signer_public_key: bytes = b""
    witness_term_signer_public_key: bytes = b""
    ir_promotion_completion_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_OPERATIONAL_FAILOVER_V1_MAXIMUM_EVIDENCE_AGE_SECONDS


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1FiSelfFenceReceiptInput:
    receipt_id: str = ""
    receipt_nonce: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    replay_key_sha256: str = ""
    pins: PhysicalOperationalFailoverV1Pins | None = None
    predecessor_term: PhysicalOperationalFailoverV1Term | None = None
    fence_reason: str = ""
    last_final_ack_sha256: str = ""
    last_committed_frontier_wal_lsn: str = ""


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1IrPromotionRequestInput:
    request_id: str = ""
    request_nonce: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    replay_key_sha256: str = ""
    pins: PhysicalOperationalFailoverV1Pins | None = None
    predecessor_term: PhysicalOperationalFailoverV1Term | None = None
    predecessor_termination_reason: str = ""
    fi_self_fence_receipt_sha256: str | None = None
    recovery_evidence_sha256: str = ""
    p0_policy_bundle_sha256: str = ""


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessPromotionGrantInput:
    grant_id: str = ""
    grant_nonce: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    replay_key_sha256: str = ""
    pins: PhysicalOperationalFailoverV1Pins | None = None
    request_sha256: str = ""
    request_id: str = ""
    request_nonce: str = ""
    predecessor_term: PhysicalOperationalFailoverV1Term | None = None
    predecessor_termination_reason: str = ""
    fi_self_fence_receipt_sha256: str | None = None
    successor_term: PhysicalOperationalFailoverV1Term | None = None
    activation_route_artifact_sha256: str = ""
    activation_receiver_permit_sha256: str = ""
    witness_ledger_sequence: int = 0
    witness_ledger_entry_sha256: str = ""
    witness_ledger_previous_head_sha256: str = ""


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1IrPromotionCompletionInput:
    completion_id: str = ""
    completion_nonce: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    replay_key_sha256: str = ""
    pins: PhysicalOperationalFailoverV1Pins | None = None
    predecessor_term: PhysicalOperationalFailoverV1Term | None = None
    predecessor_termination_reason: str = ""
    fi_self_fence_receipt_sha256: str | None = None
    grant_sha256: str = ""
    grant_id: str = ""
    grant_nonce: str = ""
    successor_term: PhysicalOperationalFailoverV1Term | None = None
    activation_route_artifact_sha256: str = ""
    activation_receiver_permit_sha256: str = ""
    promotion_record_sha256: str = ""
    recovery_evidence_sha256: str = ""
    p0_execution_sha256: str = ""
    traffic_fence_receipt_sha256: str = ""


@dataclass(frozen=True)
class VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt:
    receipt_id: str
    receipt_nonce: str
    issued_at: datetime
    expires_at: datetime
    replay_key_sha256: str
    pins: PhysicalOperationalFailoverV1Pins
    predecessor_term: PhysicalOperationalFailoverV1Term
    predecessor_term_sha256: str
    fence_reason: str
    last_final_ack_sha256: str
    last_committed_frontier_wal_lsn: str
    receipt_sha256: str
    canonical_receipt: bytes = field(repr=False)
    promotion_authorized: bool = field(default=False, init=False)
    writer_authorized: bool = field(default=False, init=False)
    traffic_authorized: bool = field(default=False, init=False)
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("OPERATIONAL_FAILOVER_V1_EVIDENCE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class VerifiedPhysicalOperationalFailoverV1IrPromotionRequest:
    request_id: str
    request_nonce: str
    issued_at: datetime
    expires_at: datetime
    replay_key_sha256: str
    pins: PhysicalOperationalFailoverV1Pins
    predecessor_term: PhysicalOperationalFailoverV1Term
    predecessor_term_sha256: str
    predecessor_termination_reason: str
    fi_self_fence_receipt_sha256: str | None
    recovery_evidence_sha256: str
    p0_policy_bundle_sha256: str
    request_sha256: str
    canonical_request: bytes = field(repr=False)
    promotion_authorized: bool = field(default=False, init=False)
    writer_authorized: bool = field(default=False, init=False)
    traffic_authorized: bool = field(default=False, init=False)
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("OPERATIONAL_FAILOVER_V1_EVIDENCE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant:
    grant_id: str
    grant_nonce: str
    issued_at: datetime
    expires_at: datetime
    replay_key_sha256: str
    pins: PhysicalOperationalFailoverV1Pins
    request_sha256: str
    request_id: str
    request_nonce: str
    predecessor_term: PhysicalOperationalFailoverV1Term
    predecessor_term_sha256: str
    predecessor_termination_reason: str
    fi_self_fence_receipt_sha256: str | None
    successor_term: PhysicalOperationalFailoverV1Term
    successor_term_sha256: str
    activation_route_artifact_sha256: str
    activation_receiver_permit_sha256: str
    witness_ledger_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    grant_sha256: str
    canonical_grant: bytes = field(repr=False)
    promotion_authorized: bool = field(default=False, init=False)
    writer_authorized: bool = field(default=False, init=False)
    traffic_authorized: bool = field(default=False, init=False)
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("OPERATIONAL_FAILOVER_V1_EVIDENCE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion:
    completion_id: str
    completion_nonce: str
    issued_at: datetime
    expires_at: datetime
    replay_key_sha256: str
    pins: PhysicalOperationalFailoverV1Pins
    predecessor_term: PhysicalOperationalFailoverV1Term
    predecessor_term_sha256: str
    predecessor_termination_reason: str
    fi_self_fence_receipt_sha256: str | None
    grant_sha256: str
    grant_id: str
    grant_nonce: str
    successor_term: PhysicalOperationalFailoverV1Term
    successor_term_sha256: str
    activation_route_artifact_sha256: str
    activation_receiver_permit_sha256: str
    promotion_record_sha256: str
    recovery_evidence_sha256: str
    p0_execution_sha256: str
    traffic_fence_receipt_sha256: str
    completion_sha256: str
    canonical_completion: bytes = field(repr=False)
    promotion_authorized: bool = field(default=False, init=False)
    writer_authorized: bool = field(default=False, init=False)
    traffic_authorized: bool = field(default=False, init=False)
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("OPERATIONAL_FAILOVER_V1_EVIDENCE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ConfigFacts:
    pins: PhysicalOperationalFailoverV1Pins
    fi_fence: Ed25519PublicKey
    ir_request: Ed25519PublicKey
    witness: Ed25519PublicKey
    ir_completion: Ed25519PublicKey
    maximum_evidence_age_seconds: int


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1Error(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalOperationalFailoverV1Error(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("OPERATIONAL_FAILOVER_V1_WIRE_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("OPERATIONAL_FAILOVER_V1_WIRE_INVALID")


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _parse_canonical(raw: object, *, fields: frozenset[str], code: str) -> tuple[dict[str, Any], bytes]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_WIRE_BYTES:
        _fail(code)
    try:
        parsed = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalOperationalFailoverV1Error:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PhysicalOperationalFailoverV1Error(code) from exc
    mapping = _exact_mapping(parsed, fields=fields, code=code)
    canonical = _canonical(mapping, code=code)
    if canonical != raw:
        _fail(code)
    return mapping, canonical


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None or (not permit_zero and value == _ZERO_SHA256):
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _writer_lease_id(value: object, *, code: str) -> str:
    """Validate V1 terms with the shared canonical writer-lease grammar."""

    if type(value) is not str or LEASE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> str:
    if type(value) is not str or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _lsn_value(value: str) -> int:
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) + int(low, 16)


def _datetime(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if result.microsecond != 0:
        _fail(code)
    return result


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhysicalOperationalFailoverV1Error(code) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        _fail(code)
    result = result.astimezone(timezone.utc)
    if result.microsecond != 0 or _render_timestamp(result) != value:
        _fail(code)
    return result


def _render_timestamp(value: datetime) -> str:
    return _datetime(value, code="OPERATIONAL_FAILOVER_V1_TIME_INVALID").strftime("%Y-%m-%dT%H:%M:%SZ")


def _evidence_window(
    issued_at: datetime,
    expires_at: datetime,
    *,
    now: datetime,
    maximum_age_seconds: int,
    code: str,
) -> None:
    if expires_at <= issued_at or expires_at - issued_at > timedelta(seconds=maximum_age_seconds):
        _fail(code)
    if issued_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        _fail(code)
    if now > expires_at or now - issued_at > timedelta(seconds=maximum_age_seconds):
        _fail(code)


def _pins_mapping(value: object, *, code: str) -> tuple[PhysicalOperationalFailoverV1Pins, dict[str, str]]:
    if type(value) is not PhysicalOperationalFailoverV1Pins:
        _fail(code)
    fields = {
        "cluster_id": value.cluster_id,
        "release_sha": value.release_sha,
        "stream_generation_id": value.stream_generation_id,
        "route_binding_sha256": value.route_binding_sha256,
        "baseline_generation_id": value.baseline_generation_id,
        "baseline_manifest_sha256": value.baseline_manifest_sha256,
        "recovery_frontier_wal_lsn": value.recovery_frontier_wal_lsn,
        "blob_frontier_wal_lsn": value.blob_frontier_wal_lsn,
    }
    if (
        type(fields["cluster_id"]) is not str
        or _CLUSTER_RE.fullmatch(fields["cluster_id"]) is None
        or type(fields["release_sha"]) is not str
        or _RELEASE_RE.fullmatch(fields["release_sha"]) is None
    ):
        _fail(code)
    for name in ("stream_generation_id", "baseline_generation_id"):
        if type(fields[name]) is not str or _ID_RE.fullmatch(fields[name]) is None:
            _fail(code)
    for name in ("route_binding_sha256", "baseline_manifest_sha256"):
        fields[name] = _sha(fields[name], code=code)
    fields["recovery_frontier_wal_lsn"] = _lsn(fields["recovery_frontier_wal_lsn"], code=code)
    fields["blob_frontier_wal_lsn"] = _lsn(fields["blob_frontier_wal_lsn"], code=code)
    if _lsn_value(fields["blob_frontier_wal_lsn"]) < _lsn_value(fields["recovery_frontier_wal_lsn"]):
        _fail(code)
    pins = PhysicalOperationalFailoverV1Pins(**fields)
    return pins, fields


def _pins_from_mapping(value: object, *, code: str) -> tuple[PhysicalOperationalFailoverV1Pins, dict[str, str]]:
    fields = _exact_mapping(value, fields=_PINS_FIELDS, code=code)
    return _pins_mapping(PhysicalOperationalFailoverV1Pins(**fields), code=code)


def _term_mapping(value: object, *, code: str) -> tuple[PhysicalOperationalFailoverV1Term, dict[str, object]]:
    if type(value) is not PhysicalOperationalFailoverV1Term:
        _fail(code)
    holder = value.holder_site
    if holder not in _SITES - {"witness"}:
        _fail(code)
    if type(value.writer_epoch) is not int or isinstance(value.writer_epoch, bool) or value.writer_epoch < 1:
        _fail(code)
    mapping: dict[str, object] = {
        "holder_site": holder,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": _writer_lease_id(value.writer_lease_id, code=code),
        "witness_transition_id": _identifier(value.witness_transition_id, code=code),
        "witnessed_term_proof_sha256": _sha(value.witnessed_term_proof_sha256, code=code),
        "issued_at": _render_timestamp(_datetime(value.issued_at, code=code)),
        "expires_at": _render_timestamp(_datetime(value.expires_at, code=code)),
    }
    issued_at = _timestamp(mapping["issued_at"], code=code)
    expires_at = _timestamp(mapping["expires_at"], code=code)
    if expires_at <= issued_at or expires_at - issued_at > timedelta(seconds=_MAX_TERM_DURATION_SECONDS):
        _fail(code)
    term = PhysicalOperationalFailoverV1Term(
        holder_site=holder,
        writer_epoch=value.writer_epoch,
        writer_lease_id=mapping["writer_lease_id"],  # type: ignore[arg-type]
        witness_transition_id=mapping["witness_transition_id"],  # type: ignore[arg-type]
        witnessed_term_proof_sha256=mapping["witnessed_term_proof_sha256"],  # type: ignore[arg-type]
        issued_at=issued_at,
        expires_at=expires_at,
    )
    return term, mapping


def _term_from_mapping(value: object, *, code: str) -> tuple[PhysicalOperationalFailoverV1Term, dict[str, object]]:
    fields = _exact_mapping(value, fields=_TERM_FIELDS, code=code)
    try:
        term = PhysicalOperationalFailoverV1Term(
            holder_site=fields["holder_site"],
            writer_epoch=fields["writer_epoch"],
            writer_lease_id=fields["writer_lease_id"],
            witness_transition_id=fields["witness_transition_id"],
            witnessed_term_proof_sha256=fields["witnessed_term_proof_sha256"],
            issued_at=_timestamp(fields["issued_at"], code=code),
            expires_at=_timestamp(fields["expires_at"], code=code),
        )
    except TypeError as exc:
        raise PhysicalOperationalFailoverV1Error(code) from exc
    return _term_mapping(term, code=code)


def _term_sha256(mapping: dict[str, object]) -> str:
    return _sha256_bytes(_canonical(mapping, code="OPERATIONAL_FAILOVER_V1_TERM_INVALID"))


def _public_key(value: object, *, code: str) -> Ed25519PublicKey:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        return Ed25519PublicKey.from_public_bytes(value)
    except ValueError as exc:
        raise PhysicalOperationalFailoverV1Error(code) from exc


def _public_bytes(value: Ed25519PublicKey) -> bytes:
    return value.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalOperationalFailoverV1VerificationConfig:
        _fail("OPERATIONAL_FAILOVER_V1_CONFIG_REQUIRED")
    if value.enabled is not True:
        _fail("OPERATIONAL_FAILOVER_V1_CONFIG_DISABLED")
    pins, _mapping = _pins_mapping(value.pins, code="OPERATIONAL_FAILOVER_V1_CONFIG_INVALID")
    if (
        type(value.maximum_evidence_age_seconds) is not int
        or isinstance(value.maximum_evidence_age_seconds, bool)
        or not 1 <= value.maximum_evidence_age_seconds <= _MAX_EVIDENCE_AGE_SECONDS
    ):
        _fail("OPERATIONAL_FAILOVER_V1_CONFIG_INVALID")
    fi_fence = _public_key(value.fi_self_fence_signer_public_key, code="OPERATIONAL_FAILOVER_V1_CONFIG_INVALID")
    ir_request = _public_key(value.ir_promotion_request_signer_public_key, code="OPERATIONAL_FAILOVER_V1_CONFIG_INVALID")
    witness = _public_key(value.witness_term_signer_public_key, code="OPERATIONAL_FAILOVER_V1_CONFIG_INVALID")
    ir_completion = _public_key(value.ir_promotion_completion_signer_public_key, code="OPERATIONAL_FAILOVER_V1_CONFIG_INVALID")
    if len({_public_bytes(item) for item in (fi_fence, ir_request, witness, ir_completion)}) != 4:
        _fail("OPERATIONAL_FAILOVER_V1_CONFIG_ROLE_KEY_REUSE")
    return _ConfigFacts(
        pins=pins,
        fi_fence=fi_fence,
        ir_request=ir_request,
        witness=witness,
        ir_completion=ir_completion,
        maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
    )


def _require_private_matches(private_key: object, public_key: Ed25519PublicKey, *, code: str) -> Ed25519PrivateKey:
    # ``Ed25519PrivateKey`` is an abstract cryptography interface; generated
    # backend keys are concrete subclasses rather than this exact type.
    if not isinstance(private_key, Ed25519PrivateKey):
        _fail(code)
    if _public_bytes(private_key.public_key()) != _public_bytes(public_key):
        _fail(code)
    return private_key


def _sign(unsigned: dict[str, object], *, private_key: Ed25519PrivateKey, domain: bytes, code: str) -> bytes:
    canonical_unsigned = _canonical(unsigned, code=code)
    try:
        signature = private_key.sign(domain + canonical_unsigned)
    except Exception as exc:
        raise PhysicalOperationalFailoverV1Error(code) from exc
    payload = dict(unsigned)
    payload["signature_base64"] = base64.b64encode(signature).decode("ascii")
    return _canonical(payload, code=code)


def _signature(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalOperationalFailoverV1Error(code) from exc
    if len(result) != 64:
        _fail(code)
    return result


def _verify_signature(
    mapping: dict[str, Any],
    *,
    public_key: Ed25519PublicKey,
    domain: bytes,
    code: str,
) -> None:
    signature = _signature(mapping["signature_base64"], code=code)
    unsigned = {key: value for key, value in mapping.items() if key != "signature_base64"}
    try:
        public_key.verify(signature, domain + _canonical(unsigned, code=code))
    except (InvalidSignature, ValueError) as exc:
        raise PhysicalOperationalFailoverV1Error(code) from exc


def _match_pins(actual: PhysicalOperationalFailoverV1Pins, expected: PhysicalOperationalFailoverV1Pins, *, code: str) -> None:
    if actual != expected:
        _fail(code)


def _termination_reason(
    reason: object,
    receipt_sha256: object,
    *,
    predecessor: PhysicalOperationalFailoverV1Term,
    now: datetime,
    code: str,
) -> tuple[str, str | None]:
    if reason not in _TERMINATION_REASONS:
        _fail(code)
    if reason == "fi-self-fence-receipt":
        return reason, _sha(receipt_sha256, code=code)
    if receipt_sha256 is not None:
        _fail(code)
    if predecessor.expires_at is None or now < predecessor.expires_at:
        _fail(code)
    return reason, None


def _require_predecessor_fi(term: PhysicalOperationalFailoverV1Term, *, code: str) -> None:
    if term.holder_site != "webapp_fi":
        _fail(code)


def _require_successor_ir(
    predecessor: PhysicalOperationalFailoverV1Term,
    successor: PhysicalOperationalFailoverV1Term,
    *,
    issued_at: datetime,
    expires_at: datetime,
    code: str,
) -> None:
    if (
        successor.holder_site != "webapp_ir"
        or successor.writer_epoch <= predecessor.writer_epoch
        or successor.issued_at != issued_at
        or successor.expires_at != expires_at
    ):
        _fail(code)


def _input_window(issued_at: object, expires_at: object, *, now: object, config: _ConfigFacts, code: str) -> tuple[datetime, datetime, datetime]:
    issued = _datetime(issued_at, code=code)
    expires = _datetime(expires_at, code=code)
    current = _datetime(now, code=code)
    _evidence_window(issued, expires, now=current, maximum_age_seconds=config.maximum_evidence_age_seconds, code=code)
    return issued, expires, current


def _record_window(mapping: dict[str, Any], *, now: object, config: _ConfigFacts, code: str) -> tuple[datetime, datetime, datetime]:
    issued = _timestamp(mapping["issued_at"], code=code)
    expires = _timestamp(mapping["expires_at"], code=code)
    current = _datetime(now, code=code)
    _evidence_window(issued, expires, now=current, maximum_age_seconds=config.maximum_evidence_age_seconds, code=code)
    return issued, expires, current


def sign_physical_operational_failover_v1_fi_self_fence_receipt(
    *,
    value: PhysicalOperationalFailoverV1FiSelfFenceReceiptInput,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    private_key: Ed25519PrivateKey,
    now: datetime,
) -> bytes:
    facts = _config(config)
    signer = _require_private_matches(private_key, facts.fi_fence, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_SIGNER_INVALID")
    if type(value) is not PhysicalOperationalFailoverV1FiSelfFenceReceiptInput:
        _fail("OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID")
    issued, expires, _current = _input_window(value.issued_at, value.expires_at, now=now, config=facts, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_TIME_INVALID")
    pins, pins_mapping = _pins_mapping(value.pins, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID")
    _match_pins(pins, facts.pins, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_PINS_MISMATCH")
    predecessor, predecessor_mapping = _term_mapping(value.predecessor_term, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID")
    _require_predecessor_fi(predecessor, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_PREDECESSOR_INVALID")
    if value.fence_reason not in _FENCE_REASONS:
        _fail("OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID")
    last_frontier = _lsn(value.last_committed_frontier_wal_lsn, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID")
    if _lsn_value(last_frontier) > _lsn_value(pins.recovery_frontier_wal_lsn):
        _fail("OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID")
    unsigned: dict[str, object] = {
        "schema": _FI_SELF_FENCE_SCHEMA,
        "version": _VERSION,
        "issuer_site": "webapp_fi",
        "receipt_id": _identifier(value.receipt_id, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID"),
        "receipt_nonce": _nonce(value.receipt_nonce, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID"),
        "issued_at": _render_timestamp(issued),
        "expires_at": _render_timestamp(expires),
        "replay_key_sha256": _sha(value.replay_key_sha256, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID"),
        "pins": pins_mapping,
        "predecessor_term": predecessor_mapping,
        "predecessor_term_sha256": _term_sha256(predecessor_mapping),
        "fence_reason": value.fence_reason,
        "last_final_ack_sha256": _sha(value.last_final_ack_sha256, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_INPUT_INVALID"),
        "last_committed_frontier_wal_lsn": last_frontier,
    }
    return _sign(unsigned, private_key=signer, domain=_FI_SELF_FENCE_DOMAIN, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_SIGN_FAILED")


def sign_physical_operational_failover_v1_ir_promotion_request(
    *,
    value: PhysicalOperationalFailoverV1IrPromotionRequestInput,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    private_key: Ed25519PrivateKey,
    now: datetime,
) -> bytes:
    facts = _config(config)
    signer = _require_private_matches(private_key, facts.ir_request, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_SIGNER_INVALID")
    if type(value) is not PhysicalOperationalFailoverV1IrPromotionRequestInput:
        _fail("OPERATIONAL_FAILOVER_V1_IR_REQUEST_INPUT_INVALID")
    issued, expires, current = _input_window(value.issued_at, value.expires_at, now=now, config=facts, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_TIME_INVALID")
    pins, pins_mapping = _pins_mapping(value.pins, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_INPUT_INVALID")
    _match_pins(pins, facts.pins, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_PINS_MISMATCH")
    predecessor, predecessor_mapping = _term_mapping(value.predecessor_term, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_INPUT_INVALID")
    _require_predecessor_fi(predecessor, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_PREDECESSOR_INVALID")
    reason, receipt = _termination_reason(
        value.predecessor_termination_reason,
        value.fi_self_fence_receipt_sha256,
        predecessor=predecessor,
        now=current,
        code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_TERMINATION_INVALID",
    )
    unsigned: dict[str, object] = {
        "schema": _IR_PROMOTION_REQUEST_SCHEMA,
        "version": _VERSION,
        "issuer_site": "webapp_ir",
        "request_id": _identifier(value.request_id, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_INPUT_INVALID"),
        "request_nonce": _nonce(value.request_nonce, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_INPUT_INVALID"),
        "issued_at": _render_timestamp(issued),
        "expires_at": _render_timestamp(expires),
        "replay_key_sha256": _sha(value.replay_key_sha256, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_INPUT_INVALID"),
        "pins": pins_mapping,
        "predecessor_term": predecessor_mapping,
        "predecessor_term_sha256": _term_sha256(predecessor_mapping),
        "predecessor_termination_reason": reason,
        "fi_self_fence_receipt_sha256": receipt,
        "recovery_evidence_sha256": _sha(value.recovery_evidence_sha256, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_INPUT_INVALID"),
        "p0_policy_bundle_sha256": _sha(value.p0_policy_bundle_sha256, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_INPUT_INVALID"),
        "requested_activation_mode": _PROMOTED_IR_MODE,
    }
    return _sign(unsigned, private_key=signer, domain=_IR_PROMOTION_REQUEST_DOMAIN, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_SIGN_FAILED")


def sign_physical_operational_failover_v1_witness_promotion_grant(
    *,
    value: PhysicalOperationalFailoverV1WitnessPromotionGrantInput,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    private_key: Ed25519PrivateKey,
    now: datetime,
    expected_request: VerifiedPhysicalOperationalFailoverV1IrPromotionRequest,
) -> bytes:
    facts = _config(config)
    signer = _require_private_matches(private_key, facts.witness, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_SIGNER_INVALID")
    if type(value) is not PhysicalOperationalFailoverV1WitnessPromotionGrantInput:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID")
    issued, expires, current = _input_window(value.issued_at, value.expires_at, now=now, config=facts, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_TIME_INVALID")
    pins, pins_mapping = _pins_mapping(value.pins, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID")
    _match_pins(pins, facts.pins, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_PINS_MISMATCH")
    predecessor, predecessor_mapping = _term_mapping(value.predecessor_term, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID")
    _require_predecessor_fi(predecessor, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_PREDECESSOR_INVALID")
    reason, receipt = _termination_reason(
        value.predecessor_termination_reason,
        value.fi_self_fence_receipt_sha256,
        predecessor=predecessor,
        now=current,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_TERMINATION_INVALID",
    )
    successor, successor_mapping = _term_mapping(value.successor_term, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID")
    _require_successor_ir(predecessor, successor, issued_at=issued, expires_at=expires, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_SUCCESSOR_INVALID")
    if type(value.witness_ledger_sequence) is not int or isinstance(value.witness_ledger_sequence, bool) or value.witness_ledger_sequence < 1:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID")
    unsigned: dict[str, object] = {
        "schema": _WITNESS_PROMOTION_GRANT_SCHEMA,
        "version": _VERSION,
        "issuer_site": "witness",
        "granted_site": "webapp_ir",
        "grant_id": _identifier(value.grant_id, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID"),
        "grant_nonce": _nonce(value.grant_nonce, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID"),
        "issued_at": _render_timestamp(issued),
        "expires_at": _render_timestamp(expires),
        "replay_key_sha256": _sha(value.replay_key_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID"),
        "pins": pins_mapping,
        "request_sha256": _sha(value.request_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID"),
        "request_id": _identifier(value.request_id, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID"),
        "request_nonce": _nonce(value.request_nonce, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID"),
        "predecessor_term": predecessor_mapping,
        "predecessor_term_sha256": _term_sha256(predecessor_mapping),
        "predecessor_termination_reason": reason,
        "fi_self_fence_receipt_sha256": receipt,
        "successor_term": successor_mapping,
        "successor_term_sha256": _term_sha256(successor_mapping),
        "activation_mode": _PROMOTED_IR_MODE,
        "activation_route_artifact_sha256": _sha(value.activation_route_artifact_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID"),
        "activation_receiver_permit_sha256": _sha(value.activation_receiver_permit_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID"),
        "witness_ledger_sequence": value.witness_ledger_sequence,
        "witness_ledger_entry_sha256": _sha(value.witness_ledger_entry_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID"),
        "witness_ledger_previous_head_sha256": _sha(value.witness_ledger_previous_head_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_INPUT_INVALID", permit_zero=True),
    }
    # A Witness signer may not mint a chain-free grant.  Parsing remains
    # generic below, but issuing evidence always requires a freshly verified
    # request and exact chain binding.
    _match_grant_request(unsigned, expected_request, config=config, now=current, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_REQUEST_MISMATCH")
    return _sign(unsigned, private_key=signer, domain=_WITNESS_PROMOTION_GRANT_DOMAIN, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_SIGN_FAILED")


def sign_physical_operational_failover_v1_ir_promotion_completion(
    *,
    value: PhysicalOperationalFailoverV1IrPromotionCompletionInput,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    private_key: Ed25519PrivateKey,
    now: datetime,
    expected_grant: VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant,
) -> bytes:
    facts = _config(config)
    signer = _require_private_matches(private_key, facts.ir_completion, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_SIGNER_INVALID")
    if type(value) is not PhysicalOperationalFailoverV1IrPromotionCompletionInput:
        _fail("OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID")
    issued, expires, current = _input_window(value.issued_at, value.expires_at, now=now, config=facts, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_TIME_INVALID")
    pins, pins_mapping = _pins_mapping(value.pins, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID")
    _match_pins(pins, facts.pins, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_PINS_MISMATCH")
    predecessor, predecessor_mapping = _term_mapping(value.predecessor_term, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID")
    _require_predecessor_fi(predecessor, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_PREDECESSOR_INVALID")
    reason, receipt = _termination_reason(
        value.predecessor_termination_reason,
        value.fi_self_fence_receipt_sha256,
        predecessor=predecessor,
        now=current,
        code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_TERMINATION_INVALID",
    )
    successor, successor_mapping = _term_mapping(value.successor_term, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID")
    if successor.holder_site != "webapp_ir" or successor.writer_epoch <= predecessor.writer_epoch:
        _fail("OPERATIONAL_FAILOVER_V1_IR_COMPLETION_SUCCESSOR_INVALID")
    unsigned: dict[str, object] = {
        "schema": _IR_PROMOTION_COMPLETION_SCHEMA,
        "version": _VERSION,
        "issuer_site": "webapp_ir",
        "completed_site": "webapp_ir",
        "completion_id": _identifier(value.completion_id, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "completion_nonce": _nonce(value.completion_nonce, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "issued_at": _render_timestamp(issued),
        "expires_at": _render_timestamp(expires),
        "replay_key_sha256": _sha(value.replay_key_sha256, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "pins": pins_mapping,
        "predecessor_term": predecessor_mapping,
        "predecessor_term_sha256": _term_sha256(predecessor_mapping),
        "predecessor_termination_reason": reason,
        "fi_self_fence_receipt_sha256": receipt,
        "grant_sha256": _sha(value.grant_sha256, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "grant_id": _identifier(value.grant_id, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "grant_nonce": _nonce(value.grant_nonce, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "successor_term": successor_mapping,
        "successor_term_sha256": _term_sha256(successor_mapping),
        "activation_mode": _PROMOTED_IR_MODE,
        "activation_route_artifact_sha256": _sha(value.activation_route_artifact_sha256, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "activation_receiver_permit_sha256": _sha(value.activation_receiver_permit_sha256, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "promotion_record_sha256": _sha(value.promotion_record_sha256, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "recovery_evidence_sha256": _sha(value.recovery_evidence_sha256, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "p0_execution_sha256": _sha(value.p0_execution_sha256, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "traffic_fence_receipt_sha256": _sha(value.traffic_fence_receipt_sha256, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_INPUT_INVALID"),
        "completion_status": _COMPLETION_STATUS,
    }
    # A completion is likewise chained evidence, never an independently
    # mintable promotion permit.
    _match_completion_grant(unsigned, expected_grant, config=config, now=current, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_GRANT_MISMATCH")
    return _sign(unsigned, private_key=signer, domain=_IR_PROMOTION_COMPLETION_DOMAIN, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_SIGN_FAILED")


def _fi_receipt_from_mapping(mapping: dict[str, Any], *, canonical: bytes, config: _ConfigFacts, now: datetime) -> VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt:
    code = "OPERATIONAL_FAILOVER_V1_FI_FENCE_WIRE_INVALID"
    if mapping["schema"] != _FI_SELF_FENCE_SCHEMA or mapping["version"] != _VERSION or mapping["issuer_site"] != "webapp_fi":
        _fail(code)
    _verify_signature(mapping, public_key=config.fi_fence, domain=_FI_SELF_FENCE_DOMAIN, code=code)
    issued, expires, _current = _record_window(mapping, now=now, config=config, code=code)
    pins, _pins_raw = _pins_from_mapping(mapping["pins"], code=code)
    _match_pins(pins, config.pins, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_PINS_MISMATCH")
    predecessor, predecessor_raw = _term_from_mapping(mapping["predecessor_term"], code=code)
    _require_predecessor_fi(predecessor, code=code)
    predecessor_sha = _sha(mapping["predecessor_term_sha256"], code=code)
    if predecessor_sha != _term_sha256(predecessor_raw):
        _fail(code)
    if mapping["fence_reason"] not in _FENCE_REASONS:
        _fail(code)
    frontier = _lsn(mapping["last_committed_frontier_wal_lsn"], code=code)
    if _lsn_value(frontier) > _lsn_value(pins.recovery_frontier_wal_lsn):
        _fail(code)
    result = VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt(
        receipt_id=_identifier(mapping["receipt_id"], code=code),
        receipt_nonce=_nonce(mapping["receipt_nonce"], code=code),
        issued_at=issued,
        expires_at=expires,
        replay_key_sha256=_sha(mapping["replay_key_sha256"], code=code),
        pins=pins,
        predecessor_term=predecessor,
        predecessor_term_sha256=predecessor_sha,
        fence_reason=mapping["fence_reason"],
        last_final_ack_sha256=_sha(mapping["last_final_ack_sha256"], code=code),
        last_committed_frontier_wal_lsn=frontier,
        receipt_sha256=_sha256_bytes(canonical),
        canonical_receipt=canonical,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def _request_from_mapping(mapping: dict[str, Any], *, canonical: bytes, config: _ConfigFacts, now: datetime) -> VerifiedPhysicalOperationalFailoverV1IrPromotionRequest:
    code = "OPERATIONAL_FAILOVER_V1_IR_REQUEST_WIRE_INVALID"
    if (
        mapping["schema"] != _IR_PROMOTION_REQUEST_SCHEMA
        or mapping["version"] != _VERSION
        or mapping["issuer_site"] != "webapp_ir"
        or mapping["requested_activation_mode"] != _PROMOTED_IR_MODE
    ):
        _fail(code)
    _verify_signature(mapping, public_key=config.ir_request, domain=_IR_PROMOTION_REQUEST_DOMAIN, code=code)
    issued, expires, current = _record_window(mapping, now=now, config=config, code=code)
    pins, _pins_raw = _pins_from_mapping(mapping["pins"], code=code)
    _match_pins(pins, config.pins, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_PINS_MISMATCH")
    predecessor, predecessor_raw = _term_from_mapping(mapping["predecessor_term"], code=code)
    _require_predecessor_fi(predecessor, code=code)
    predecessor_sha = _sha(mapping["predecessor_term_sha256"], code=code)
    if predecessor_sha != _term_sha256(predecessor_raw):
        _fail(code)
    reason, receipt = _termination_reason(
        mapping["predecessor_termination_reason"],
        mapping["fi_self_fence_receipt_sha256"],
        predecessor=predecessor,
        now=current,
        code=code,
    )
    result = VerifiedPhysicalOperationalFailoverV1IrPromotionRequest(
        request_id=_identifier(mapping["request_id"], code=code),
        request_nonce=_nonce(mapping["request_nonce"], code=code),
        issued_at=issued,
        expires_at=expires,
        replay_key_sha256=_sha(mapping["replay_key_sha256"], code=code),
        pins=pins,
        predecessor_term=predecessor,
        predecessor_term_sha256=predecessor_sha,
        predecessor_termination_reason=reason,
        fi_self_fence_receipt_sha256=receipt,
        recovery_evidence_sha256=_sha(mapping["recovery_evidence_sha256"], code=code),
        p0_policy_bundle_sha256=_sha(mapping["p0_policy_bundle_sha256"], code=code),
        request_sha256=_sha256_bytes(canonical),
        canonical_request=canonical,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def _grant_from_mapping(mapping: dict[str, Any], *, canonical: bytes, config: _ConfigFacts, now: datetime) -> VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_WIRE_INVALID"
    if (
        mapping["schema"] != _WITNESS_PROMOTION_GRANT_SCHEMA
        or mapping["version"] != _VERSION
        or mapping["issuer_site"] != "witness"
        or mapping["granted_site"] != "webapp_ir"
        or mapping["activation_mode"] != _PROMOTED_IR_MODE
    ):
        _fail(code)
    _verify_signature(mapping, public_key=config.witness, domain=_WITNESS_PROMOTION_GRANT_DOMAIN, code=code)
    issued, expires, current = _record_window(mapping, now=now, config=config, code=code)
    pins, _pins_raw = _pins_from_mapping(mapping["pins"], code=code)
    _match_pins(pins, config.pins, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_PINS_MISMATCH")
    predecessor, predecessor_raw = _term_from_mapping(mapping["predecessor_term"], code=code)
    _require_predecessor_fi(predecessor, code=code)
    predecessor_sha = _sha(mapping["predecessor_term_sha256"], code=code)
    if predecessor_sha != _term_sha256(predecessor_raw):
        _fail(code)
    reason, receipt = _termination_reason(
        mapping["predecessor_termination_reason"],
        mapping["fi_self_fence_receipt_sha256"],
        predecessor=predecessor,
        now=current,
        code=code,
    )
    successor, successor_raw = _term_from_mapping(mapping["successor_term"], code=code)
    _require_successor_ir(predecessor, successor, issued_at=issued, expires_at=expires, code=code)
    successor_sha = _sha(mapping["successor_term_sha256"], code=code)
    if successor_sha != _term_sha256(successor_raw):
        _fail(code)
    sequence = mapping["witness_ledger_sequence"]
    if type(sequence) is not int or isinstance(sequence, bool) or sequence < 1:
        _fail(code)
    result = VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant(
        grant_id=_identifier(mapping["grant_id"], code=code),
        grant_nonce=_nonce(mapping["grant_nonce"], code=code),
        issued_at=issued,
        expires_at=expires,
        replay_key_sha256=_sha(mapping["replay_key_sha256"], code=code),
        pins=pins,
        request_sha256=_sha(mapping["request_sha256"], code=code),
        request_id=_identifier(mapping["request_id"], code=code),
        request_nonce=_nonce(mapping["request_nonce"], code=code),
        predecessor_term=predecessor,
        predecessor_term_sha256=predecessor_sha,
        predecessor_termination_reason=reason,
        fi_self_fence_receipt_sha256=receipt,
        successor_term=successor,
        successor_term_sha256=successor_sha,
        activation_route_artifact_sha256=_sha(mapping["activation_route_artifact_sha256"], code=code),
        activation_receiver_permit_sha256=_sha(mapping["activation_receiver_permit_sha256"], code=code),
        witness_ledger_sequence=sequence,
        witness_ledger_entry_sha256=_sha(mapping["witness_ledger_entry_sha256"], code=code),
        witness_ledger_previous_head_sha256=_sha(mapping["witness_ledger_previous_head_sha256"], code=code, permit_zero=True),
        grant_sha256=_sha256_bytes(canonical),
        canonical_grant=canonical,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def _completion_from_mapping(mapping: dict[str, Any], *, canonical: bytes, config: _ConfigFacts, now: datetime) -> VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion:
    code = "OPERATIONAL_FAILOVER_V1_IR_COMPLETION_WIRE_INVALID"
    if (
        mapping["schema"] != _IR_PROMOTION_COMPLETION_SCHEMA
        or mapping["version"] != _VERSION
        or mapping["issuer_site"] != "webapp_ir"
        or mapping["completed_site"] != "webapp_ir"
        or mapping["activation_mode"] != _PROMOTED_IR_MODE
        or mapping["completion_status"] != _COMPLETION_STATUS
    ):
        _fail(code)
    _verify_signature(mapping, public_key=config.ir_completion, domain=_IR_PROMOTION_COMPLETION_DOMAIN, code=code)
    issued, expires, current = _record_window(mapping, now=now, config=config, code=code)
    pins, _pins_raw = _pins_from_mapping(mapping["pins"], code=code)
    _match_pins(pins, config.pins, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_PINS_MISMATCH")
    predecessor, predecessor_raw = _term_from_mapping(mapping["predecessor_term"], code=code)
    _require_predecessor_fi(predecessor, code=code)
    predecessor_sha = _sha(mapping["predecessor_term_sha256"], code=code)
    if predecessor_sha != _term_sha256(predecessor_raw):
        _fail(code)
    reason, receipt = _termination_reason(
        mapping["predecessor_termination_reason"],
        mapping["fi_self_fence_receipt_sha256"],
        predecessor=predecessor,
        now=current,
        code=code,
    )
    successor, successor_raw = _term_from_mapping(mapping["successor_term"], code=code)
    if successor.holder_site != "webapp_ir" or successor.writer_epoch <= predecessor.writer_epoch:
        _fail(code)
    successor_sha = _sha(mapping["successor_term_sha256"], code=code)
    if successor_sha != _term_sha256(successor_raw):
        _fail(code)
    result = VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion(
        completion_id=_identifier(mapping["completion_id"], code=code),
        completion_nonce=_nonce(mapping["completion_nonce"], code=code),
        issued_at=issued,
        expires_at=expires,
        replay_key_sha256=_sha(mapping["replay_key_sha256"], code=code),
        pins=pins,
        predecessor_term=predecessor,
        predecessor_term_sha256=predecessor_sha,
        predecessor_termination_reason=reason,
        fi_self_fence_receipt_sha256=receipt,
        grant_sha256=_sha(mapping["grant_sha256"], code=code),
        grant_id=_identifier(mapping["grant_id"], code=code),
        grant_nonce=_nonce(mapping["grant_nonce"], code=code),
        successor_term=successor,
        successor_term_sha256=successor_sha,
        activation_route_artifact_sha256=_sha(mapping["activation_route_artifact_sha256"], code=code),
        activation_receiver_permit_sha256=_sha(mapping["activation_receiver_permit_sha256"], code=code),
        promotion_record_sha256=_sha(mapping["promotion_record_sha256"], code=code),
        recovery_evidence_sha256=_sha(mapping["recovery_evidence_sha256"], code=code),
        p0_execution_sha256=_sha(mapping["p0_execution_sha256"], code=code),
        traffic_fence_receipt_sha256=_sha(mapping["traffic_fence_receipt_sha256"], code=code),
        completion_sha256=_sha256_bytes(canonical),
        canonical_completion=canonical,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def _require_verified_request(
    value: object,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
) -> VerifiedPhysicalOperationalFailoverV1IrPromotionRequest:
    if (
        type(value) is not VerifiedPhysicalOperationalFailoverV1IrPromotionRequest
        or value._capability is not _CAPABILITY
    ):
        _fail("OPERATIONAL_FAILOVER_V1_IR_REQUEST_EVIDENCE_REQUIRED")
    verified = verify_physical_operational_failover_v1_ir_promotion_request(value.canonical_request, config=config, now=now)
    if verified != value:
        _fail("OPERATIONAL_FAILOVER_V1_IR_REQUEST_EVIDENCE_CHANGED")
    return verified


def _require_verified_grant(
    value: object,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
) -> VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant:
    if (
        type(value) is not VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant
        or value._capability is not _CAPABILITY
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_EVIDENCE_REQUIRED")
    verified = verify_physical_operational_failover_v1_witness_promotion_grant(value.canonical_grant, config=config, now=now)
    if verified != value:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_EVIDENCE_CHANGED")
    return verified


def _match_grant_request(
    mapping: dict[str, object],
    expected: VerifiedPhysicalOperationalFailoverV1IrPromotionRequest,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
    code: str,
) -> None:
    request = _require_verified_request(expected, config=config, now=now)
    actual_pins, _raw = _pins_from_mapping(mapping["pins"], code=code)
    predecessor, predecessor_raw = _term_from_mapping(mapping["predecessor_term"], code=code)
    if (
        mapping["request_sha256"] != request.request_sha256
        or mapping["request_id"] != request.request_id
        or mapping["request_nonce"] != request.request_nonce
        or actual_pins != request.pins
        or _term_sha256(predecessor_raw) != request.predecessor_term_sha256
        or predecessor != request.predecessor_term
        or mapping["predecessor_termination_reason"] != request.predecessor_termination_reason
        or mapping["fi_self_fence_receipt_sha256"] != request.fi_self_fence_receipt_sha256
    ):
        _fail(code)


def _match_completion_grant(
    mapping: dict[str, object],
    expected: VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
    code: str,
) -> None:
    grant = _require_verified_grant(expected, config=config, now=now)
    actual_pins, _raw = _pins_from_mapping(mapping["pins"], code=code)
    predecessor, predecessor_raw = _term_from_mapping(mapping["predecessor_term"], code=code)
    successor, successor_raw = _term_from_mapping(mapping["successor_term"], code=code)
    completion_issued = _timestamp(mapping["issued_at"], code=code)
    if (
        completion_issued > grant.expires_at
        or mapping["grant_sha256"] != grant.grant_sha256
        or mapping["grant_id"] != grant.grant_id
        or mapping["grant_nonce"] != grant.grant_nonce
        or actual_pins != grant.pins
        or _term_sha256(predecessor_raw) != grant.predecessor_term_sha256
        or predecessor != grant.predecessor_term
        or mapping["predecessor_termination_reason"] != grant.predecessor_termination_reason
        or mapping["fi_self_fence_receipt_sha256"] != grant.fi_self_fence_receipt_sha256
        or _term_sha256(successor_raw) != grant.successor_term_sha256
        or successor != grant.successor_term
        or mapping["activation_route_artifact_sha256"] != grant.activation_route_artifact_sha256
        or mapping["activation_receiver_permit_sha256"] != grant.activation_receiver_permit_sha256
    ):
        _fail(code)


def verify_physical_operational_failover_v1_fi_self_fence_receipt(
    receipt: bytes,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
) -> VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt:
    facts = _config(config)
    current = _datetime(now, code="OPERATIONAL_FAILOVER_V1_CLOCK_INVALID")
    mapping, canonical = _parse_canonical(receipt, fields=_FI_SELF_FENCE_FIELDS, code="OPERATIONAL_FAILOVER_V1_FI_FENCE_WIRE_INVALID")
    return _fi_receipt_from_mapping(mapping, canonical=canonical, config=facts, now=current)


def verify_physical_operational_failover_v1_ir_promotion_request(
    request: bytes,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
) -> VerifiedPhysicalOperationalFailoverV1IrPromotionRequest:
    facts = _config(config)
    current = _datetime(now, code="OPERATIONAL_FAILOVER_V1_CLOCK_INVALID")
    mapping, canonical = _parse_canonical(request, fields=_IR_PROMOTION_REQUEST_FIELDS, code="OPERATIONAL_FAILOVER_V1_IR_REQUEST_WIRE_INVALID")
    return _request_from_mapping(mapping, canonical=canonical, config=facts, now=current)


def verify_physical_operational_failover_v1_witness_promotion_grant(
    grant: bytes,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
    expected_request: VerifiedPhysicalOperationalFailoverV1IrPromotionRequest | None = None,
) -> VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant:
    facts = _config(config)
    current = _datetime(now, code="OPERATIONAL_FAILOVER_V1_CLOCK_INVALID")
    mapping, canonical = _parse_canonical(grant, fields=_WITNESS_PROMOTION_GRANT_FIELDS, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_WIRE_INVALID")
    result = _grant_from_mapping(mapping, canonical=canonical, config=facts, now=current)
    if expected_request is not None:
        _match_grant_request(mapping, expected_request, config=config, now=current, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_REQUEST_MISMATCH")
    return result


def verify_physical_operational_failover_v1_ir_promotion_completion(
    completion: bytes,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
    expected_grant: VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant | None = None,
) -> VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion:
    facts = _config(config)
    current = _datetime(now, code="OPERATIONAL_FAILOVER_V1_CLOCK_INVALID")
    mapping, canonical = _parse_canonical(completion, fields=_IR_PROMOTION_COMPLETION_FIELDS, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_WIRE_INVALID")
    result = _completion_from_mapping(mapping, canonical=canonical, config=facts, now=current)
    if expected_grant is not None:
        _match_completion_grant(mapping, expected_grant, config=config, now=current, code="OPERATIONAL_FAILOVER_V1_IR_COMPLETION_GRANT_MISMATCH")
    return result


def require_verified_physical_operational_failover_v1_fi_self_fence_receipt(
    value: object,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
) -> VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt:
    if type(value) is not VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt or value._capability is not _CAPABILITY:
        _fail("OPERATIONAL_FAILOVER_V1_FI_FENCE_EVIDENCE_REQUIRED")
    verified = verify_physical_operational_failover_v1_fi_self_fence_receipt(value.canonical_receipt, config=config, now=now)
    if verified != value:
        _fail("OPERATIONAL_FAILOVER_V1_FI_FENCE_EVIDENCE_CHANGED")
    return verified


def require_verified_physical_operational_failover_v1_ir_promotion_request(
    value: object,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
) -> VerifiedPhysicalOperationalFailoverV1IrPromotionRequest:
    return _require_verified_request(value, config=config, now=now)


def require_verified_physical_operational_failover_v1_witness_promotion_grant(
    value: object,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
    expected_request: VerifiedPhysicalOperationalFailoverV1IrPromotionRequest | None = None,
) -> VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant:
    verified = _require_verified_grant(value, config=config, now=now)
    if expected_request is not None:
        mapping, _canonical_value = _parse_canonical(verified.canonical_grant, fields=_WITNESS_PROMOTION_GRANT_FIELDS, code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_WIRE_INVALID")
        _match_grant_request(mapping, expected_request, config=config, now=_datetime(now, code="OPERATIONAL_FAILOVER_V1_CLOCK_INVALID"), code="OPERATIONAL_FAILOVER_V1_WITNESS_GRANT_REQUEST_MISMATCH")
    return verified


def require_verified_physical_operational_failover_v1_ir_promotion_completion(
    value: object,
    *,
    config: PhysicalOperationalFailoverV1VerificationConfig,
    now: datetime,
    expected_grant: VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant | None = None,
) -> VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion:
    if type(value) is not VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion or value._capability is not _CAPABILITY:
        _fail("OPERATIONAL_FAILOVER_V1_IR_COMPLETION_EVIDENCE_REQUIRED")
    verified = verify_physical_operational_failover_v1_ir_promotion_completion(value.canonical_completion, config=config, now=now, expected_grant=expected_grant)
    if verified != value:
        _fail("OPERATIONAL_FAILOVER_V1_IR_COMPLETION_EVIDENCE_CHANGED")
    return verified
