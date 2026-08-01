"""Pure V2 signed evidence for the physical-WAL remote-ack boundary.

This is intentionally a new protocol.  It receives only the already verified
V2 coverage join and preserves all of its route, lineage, handoff, continuity,
and exact-object-set commitments in a canonical context digest.  It has no
compatibility path to a historical single-object acknowledgement shape.

The module is deliberately side-effect free: it does not open a ledger, query
PostgreSQL, contact Object Storage or Witness, or release a writer response.
In particular, a signed receipt made here is *not* proof of a durable receiver
ledger entry.  The separate V2 ledger boundary must establish that fact before
any strict-writer boundary can consume it.
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
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_chunked_base_backup_blob_frontier_coverage import (
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
    VerifiedPhysicalWalV2BlobObjectVersionCoverage,
)
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    VerifiedPhysicalWalChunkedBaseBackupManifest,
)
from core.physical_wal_chunked_base_backup_remote_ack_bridge import (
    VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
)
from core.physical_wal_chunked_base_backup_target_wal_continuity import (
    PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
)
from core.physical_wal_v2_remote_ack_coverage import (
    PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA,
    PhysicalWalV2RemoteAckCoverageError,
    PhysicalWalV2RemoteAckCoverageScope,
    VerifiedPhysicalWalV2RemoteAckCoverage,
    require_verified_physical_wal_v2_remote_ack_coverage,
)


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_REMOTE_ACK_MAXIMUM_AGE_SECONDS",
    "PHYSICAL_WAL_V2_REMOTE_ACK_CONTEXT_SCHEMA",
    "PHYSICAL_WAL_V2_REMOTE_ACK_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_REMOTE_ACK_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_V2_REMOTE_ACK_REQUEST_SCHEMA",
    "PhysicalWalV2RemoteAckConfig",
    "PhysicalWalV2RemoteAckCoverageInputs",
    "PhysicalWalV2RemoteAckError",
    "PhysicalWalV2RemoteAckReceiverRecoveryEvidence",
    "VerifiedPhysicalWalV2RemoteAckContext",
    "VerifiedPhysicalWalV2RemoteAckEvidence",
    "VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence",
    "VerifiedPhysicalWalV2RemoteAckRequest",
    "build_physical_wal_v2_remote_ack_receipt",
    "build_physical_wal_v2_remote_ack_request",
    "mint_physical_wal_v2_remote_ack_context",
    "require_verified_physical_wal_v2_remote_ack_context",
    "require_verified_physical_wal_v2_remote_ack_evidence",
    "require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence",
    "require_verified_physical_wal_v2_remote_ack_request",
    "verify_physical_wal_v2_remote_ack_evidence",
    "verify_physical_wal_v2_remote_ack_receiver_recovery_evidence",
    "verify_physical_wal_v2_remote_ack_request",
)


PHYSICAL_WAL_V2_REMOTE_ACK_CONTEXT_SCHEMA = "gold-trade-physical-wal-v2-remote-ack-context-v2"
PHYSICAL_WAL_V2_REMOTE_ACK_REQUEST_SCHEMA = "gold-trade-physical-wal-v2-remote-ack-request-v2"
PHYSICAL_WAL_V2_REMOTE_ACK_RECEIPT_SCHEMA = "gold-trade-physical-wal-v2-remote-ack-receipt-v2"
PHYSICAL_WAL_V2_REMOTE_ACK_DEFAULT_ENABLED = False
PHYSICAL_WAL_V2_REMOTE_ACK_VERSION = 2

DEFAULT_PHYSICAL_WAL_V2_REMOTE_ACK_MAXIMUM_AGE_SECONDS = 60
MAX_PHYSICAL_WAL_V2_REMOTE_ACK_MAXIMUM_AGE_SECONDS = 300
MAX_PHYSICAL_WAL_V2_REMOTE_ACK_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_WAL_V2_REMOTE_ACK_BYTES = 128 * 1024

_CONTEXT_DOMAIN = b"gold-trade-physical-wal-v2-remote-ack-context-v2\x00"
_REQUEST_DOMAIN = b"gold-trade-physical-wal-v2-remote-ack-request-v2\x00"
_RECEIPT_DOMAIN = b"gold-trade-physical-wal-v2-remote-ack-receipt-v2\x00"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_ASCII_TEXT_RE = re.compile(r"^[\x21-\x7e]{1,255}$", re.ASCII)

_TERM_FIELDS = frozenset(
    {"writer_holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"}
)
_CONTEXT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "coverage_schema",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "object_storage_namespace",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "destination_age_recipient",
        "writer_term",
        "transport_plane",
        "direct_webapp_transport",
        "stream_generation_id",
        "canonical_manifest_sha256",
        "manifest_id",
        "handoff_receipt_id",
        "handoff_receipt_nonce",
        "handoff_expires_at",
        "lineage_sha256",
        "baseline_generation_id",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "target_lsn",
        "base_backup_scope_sha256",
        "blob_frontier_scope_sha256",
        "blob_owner_coverage_sha256",
        "blob_coverage_id",
        "blob_coverage_nonce",
        "wal_continuity_scope_sha256",
        "wal_continuity_receipt_id",
        "wal_continuity_receipt_nonce",
        "wal_continuity_selector_set_sha256",
        "object_version_set_sha256",
        "coverage_scope_sha256",
        "object_count",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "context",
        "context_sha256",
        "request_id",
        "request_nonce",
        "issued_at",
        "expires_at",
        "source_signer",
        "source_signature",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "context_sha256",
        "request_id",
        "request_nonce",
        "request_issued_at",
        "request_expires_at",
        "source_request_sha256",
        "receiver_recovery_evidence_sha256",
        "receiver_replay_lsn",
        "receipt_id",
        "receipt_nonce",
        "acknowledged_at",
        "destination_signer",
        "destination_signature",
    }
)

_CONTEXT_CAPABILITY = object()
_REQUEST_CAPABILITY = object()
_RECOVERY_CAPABILITY = object()
_EVIDENCE_CAPABILITY = object()


class PhysicalWalV2RemoteAckError(ValueError):
    """A V2 remote-ack input is foreign, stale, malformed, or non-authorizing."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2RemoteAckCoverageInputs:
    """Exact already-verified V2 inputs required to refresh a V2 context.

    The context intentionally keeps these in private process-local state.  The
    signed wire request contains only the canonical commitment, never a raw
    Python capability or a caller-selected ``objects_complete`` assertion.
    """

    base_backup_evidence: VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence
    blob_frontier_coverage: VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage
    blob_owner_coverage: VerifiedPhysicalWalV2BlobObjectVersionCoverage
    blob_expected_owner_public_key: bytes
    target_wal_continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity
    target_wal_continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    blob_scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope
    continuity_scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope
    scope: PhysicalWalV2RemoteAckCoverageScope


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2RemoteAckContext:
    """Opaque, refreshable V2 context; not a request or acknowledgement."""

    canonical_context: bytes
    context_sha256: str
    source_site: str
    destination_site: str
    target_lsn: str
    object_version_set_sha256: str
    handoff_expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_REMOTE_ACK_CONTEXT_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalWalV2RemoteAckConfig:
    """Default-off local key and exact-context pins for one V2 direction."""

    expected_context_sha256: str = ""
    expected_source_site: str = ""
    expected_destination_site: str = ""
    expected_source_public_key: bytes = b""
    expected_destination_public_key: bytes = b""
    enabled: bool = PHYSICAL_WAL_V2_REMOTE_ACK_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_WAL_V2_REMOTE_ACK_MAXIMUM_AGE_SECONDS


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2RemoteAckRequest:
    """Opaque signature-verified V2 request, never durable-ledger proof."""

    canonical_request: bytes
    context_sha256: str
    source_site: str
    destination_site: str
    target_lsn: str
    object_version_set_sha256: str
    request_id: str
    request_nonce: str
    issued_at: datetime
    expires_at: datetime
    source_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_REMOTE_ACK_REQUEST_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalWalV2RemoteAckReceiverRecoveryEvidence:
    """Typed local-recovery observation to bind to one V2 request.

    A trusted local adapter must establish the real PostgreSQL fact before it
    supplies this observation.  This value performs no PostgreSQL I/O and is
    not itself a durable receipt or a promotion permit.
    """

    source_request_sha256: str
    context_sha256: str
    receiver_recovery_evidence_sha256: str
    receiver_site: str
    source_site: str
    destination_site: str
    object_version_set_sha256: str
    target_lsn: str
    replay_lsn: str
    observed_at: datetime
    in_recovery: bool
    role: str


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence:
    """Opaque request-bound recovery observation; not a writer permit."""

    evidence: PhysicalWalV2RemoteAckReceiverRecoveryEvidence
    request_sha256: str
    context_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_REMOTE_ACK_RECOVERY_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2RemoteAckEvidence:
    """Opaque signed pair only; it deliberately excludes durable authority."""

    canonical_request: bytes
    canonical_receipt: bytes
    context_sha256: str
    request_id: str
    request_nonce: str
    receipt_id: str
    receipt_nonce: str
    source_public_key: bytes
    destination_public_key: bytes
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    acknowledged_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_REMOTE_ACK_EVIDENCE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ContextState:
    coverage: VerifiedPhysicalWalV2RemoteAckCoverage
    inputs: PhysicalWalV2RemoteAckCoverageInputs


@dataclass(frozen=True)
class _ContextFacts:
    canonical_context: bytes
    context_sha256: str
    source_site: str
    destination_site: str
    target_lsn: str
    target_lsn_value: int
    object_version_set_sha256: str
    handoff_expires_at: datetime


@dataclass(frozen=True)
class _ConfigFacts:
    context_sha256: str
    source_site: str
    destination_site: str
    source_public_key: bytes
    destination_public_key: bytes
    maximum_age_seconds: int


@dataclass(frozen=True)
class _RequestFacts:
    canonical_request: bytes
    context: _ContextFacts
    request_id: str
    request_nonce: str
    issued_at: datetime
    expires_at: datetime
    source_public_key: bytes


@dataclass(frozen=True)
class _ReceiptFacts:
    canonical_receipt: bytes
    receipt_id: str
    receipt_nonce: str
    acknowledged_at: datetime
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    receiver_replay_lsn_value: int
    destination_public_key: bytes


_CONTEXT_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalV2RemoteAckContext, _ContextState
] = WeakKeyDictionary()


def _fail(code: str) -> None:
    raise PhysicalWalV2RemoteAckError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_REMOTE_ACK_CANONICAL_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_REMOTE_ACK_CANONICAL_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2RemoteAckError(code) from exc


def _parse_canonical_mapping(value: object, *, code: str) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, Mapping):
        raw = _canonical(dict(value), code=code)
    elif type(value) is bytes:
        raw = value
    else:
        _fail(code)
    if not 1 <= len(raw) <= MAX_PHYSICAL_WAL_V2_REMOTE_ACK_BYTES:
        _fail(code)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalV2RemoteAckError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PhysicalWalV2RemoteAckError(code) from exc
    if type(parsed) is not dict or _canonical(parsed, code=code) != raw:
        _fail(code)
    return dict(parsed), raw


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        _fail(code)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    normalized = _utc(result, code=code)
    if _render_timestamp(normalized) != value:
        _fail(code)
    return normalized


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    if type(value) is not str or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _signer_mapping(public_key: bytes) -> dict[str, str]:
    return {
        "algorithm": "ed25519",
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "key_id": _key_id(public_key),
    }


def _signer(value: object, *, expected_public_key: bytes, code: str) -> None:
    item = _exact_mapping(value, fields=_SIGNER_FIELDS, code=code)
    if item["algorithm"] != "ed25519" or type(item["public_key_base64"]) is not str:
        _fail(code)
    if type(item["key_id"]) is not str or _KEY_ID_RE.fullmatch(item["key_id"]) is None:
        _fail(code)
    try:
        public_key = base64.b64decode(item["public_key_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if public_key != expected_public_key or item["key_id"] != _key_id(public_key):
        _fail(code)


def _signature(value: object, *, code: str) -> bytes:
    item = _exact_mapping(value, fields=_SIGNATURE_FIELDS, code=code)
    if item["algorithm"] != "ed25519" or type(item["signature_base64"]) is not str:
        _fail(code)
    try:
        result = base64.b64decode(item["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(result) != 64:
        _fail(code)
    return result


def _private_signer(value: object, *, expected_public_key: bytes, code: str) -> Ed25519PrivateKey:
    if not isinstance(value, Ed25519PrivateKey):
        _fail(code)
    try:
        public_key = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError:
        _fail(code)
    if public_key != expected_public_key:
        _fail(code)
    return value


def _sign(unsigned: Mapping[str, Any], *, signer: Ed25519PrivateKey, domain: bytes, code: str) -> dict[str, str]:
    try:
        signature = signer.sign(domain + _canonical(dict(unsigned), code=code))
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2RemoteAckError(code) from exc
    return {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")}


def _verify_signature(
    *,
    unsigned: Mapping[str, Any],
    signature: object,
    public_key: bytes,
    domain: bytes,
    code: str,
) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            _signature(signature, code=code),
            domain + _canonical(dict(unsigned), code=code),
        )
    except (InvalidSignature, ValueError):
        _fail(code)


def _site(value: object, *, code: str) -> str:
    if type(value) is not str or value not in WEBAPP_SITES:
        _fail(code)
    return value


def _ascii_text(value: object, *, code: str) -> str:
    if type(value) is not str or _ASCII_TEXT_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _context_mapping(coverage: VerifiedPhysicalWalV2RemoteAckCoverage) -> dict[str, object]:
    binding = coverage.transfer_binding
    return {
        "schema": PHYSICAL_WAL_V2_REMOTE_ACK_CONTEXT_SCHEMA,
        "version": PHYSICAL_WAL_V2_REMOTE_ACK_VERSION,
        "coverage_schema": coverage.schema,
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "object_storage_namespace": binding.object_storage_namespace,
        "route_commitment_sha256": binding.route_commitment_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "destination_age_recipient": binding.destination_age_recipient,
        "writer_term": {
            "writer_holder_site": binding.writer_term.writer_holder_site,
            "writer_epoch": binding.writer_term.writer_epoch,
            "writer_lease_id": binding.writer_term.writer_lease_id,
            "witnessed_term_proof_sha256": binding.writer_term.witnessed_term_proof_sha256,
        },
        "transport_plane": binding.transport_plane,
        "direct_webapp_transport": binding.direct_webapp_transport,
        "stream_generation_id": coverage.stream_generation_id,
        "canonical_manifest_sha256": coverage.canonical_manifest_sha256,
        "manifest_id": coverage.manifest_id,
        "handoff_receipt_id": coverage.handoff_receipt_id,
        "handoff_receipt_nonce": coverage.handoff_receipt_nonce,
        "handoff_expires_at": _render_timestamp(coverage.handoff_expires_at),
        "lineage_sha256": coverage.lineage_sha256,
        "baseline_generation_id": coverage.baseline_generation_id,
        "database_system_identifier": coverage.database_system_identifier,
        "timeline_id": coverage.timeline_id,
        "wal_segment_size_bytes": coverage.wal_segment_size_bytes,
        "baseline_wal_lsn": coverage.baseline_wal_lsn,
        "wal_chain_start_lsn": coverage.wal_chain_start_lsn,
        "base_backup_end_lsn": coverage.base_backup_end_lsn,
        "target_lsn": coverage.target_lsn,
        "base_backup_scope_sha256": coverage.base_backup_scope_sha256,
        "blob_frontier_scope_sha256": coverage.blob_frontier_scope_sha256,
        "blob_owner_coverage_sha256": coverage.blob_owner_coverage_sha256,
        "blob_coverage_id": coverage.blob_coverage_id,
        "blob_coverage_nonce": coverage.blob_coverage_nonce,
        "wal_continuity_scope_sha256": coverage.wal_continuity_scope_sha256,
        "wal_continuity_receipt_id": coverage.wal_continuity_receipt_id,
        "wal_continuity_receipt_nonce": coverage.wal_continuity_receipt_nonce,
        "wal_continuity_selector_set_sha256": coverage.wal_continuity_selector_set_sha256,
        "object_version_set_sha256": coverage.object_version_set_sha256,
        "coverage_scope_sha256": coverage.coverage_scope_sha256,
        "object_count": len(coverage.objects),
    }


def _context_facts(value: object, *, code: str) -> _ContextFacts:
    item = _exact_mapping(value, fields=_CONTEXT_FIELDS, code=code)
    if (
        item["schema"] != PHYSICAL_WAL_V2_REMOTE_ACK_CONTEXT_SCHEMA
        or item["version"] != PHYSICAL_WAL_V2_REMOTE_ACK_VERSION
        or item["coverage_schema"] != PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA
    ):
        _fail(code)
    source = _site(item["source_site"], code=code)
    destination = _site(item["destination_site"], code=code)
    if source == destination:
        _fail(code)
    if type(item["campaign_id"]) is not str or CAMPAIGN_ID_RE.fullmatch(item["campaign_id"]) is None:
        _fail(code)
    if type(item["release_sha"]) is not str or RELEASE_SHA_RE.fullmatch(item["release_sha"]) is None:
        _fail(code)
    if type(item["object_storage_namespace"]) is not str or OBJECT_KEY_RE.fullmatch(item["object_storage_namespace"]) is None:
        _fail(code)
    _sha256(item["route_commitment_sha256"], code=code)
    _sha256(item["four_role_binding_sha256"], code=code)
    if type(item["destination_age_recipient"]) is not str or AGE_RECIPIENT_RE.fullmatch(item["destination_age_recipient"]) is None:
        _fail(code)
    term = _exact_mapping(item["writer_term"], fields=_TERM_FIELDS, code=code)
    if (
        _site(term["writer_holder_site"], code=code) != source
        or type(term["writer_epoch"]) is not int
        or not 1 <= term["writer_epoch"] <= 2**31 - 1
        or type(term["writer_lease_id"]) is not str
        or LEASE_ID_RE.fullmatch(term["writer_lease_id"]) is None
    ):
        _fail(code)
    _sha256(term["witnessed_term_proof_sha256"], code=code)
    if item["transport_plane"] != "private-versioned-object-storage-witness-mediated-v1":
        _fail(code)
    if item["direct_webapp_transport"] != "forbidden":
        _fail(code)
    if type(item["stream_generation_id"]) is not str or STREAM_GENERATION_ID_RE.fullmatch(item["stream_generation_id"]) is None:
        _fail(code)
    for field_name in (
        "canonical_manifest_sha256",
        "lineage_sha256",
        "base_backup_scope_sha256",
        "blob_frontier_scope_sha256",
        "blob_owner_coverage_sha256",
        "wal_continuity_scope_sha256",
        "wal_continuity_selector_set_sha256",
        "object_version_set_sha256",
        "coverage_scope_sha256",
    ):
        _sha256(item[field_name], code=code)
    for field_name in (
        "manifest_id",
        "handoff_receipt_id",
        "blob_coverage_id",
        "wal_continuity_receipt_id",
    ):
        _identifier(item[field_name], code=code)
    for field_name in (
        "handoff_receipt_nonce",
        "blob_coverage_nonce",
        "wal_continuity_receipt_nonce",
    ):
        _nonce(item[field_name], code=code)
    handoff_expires = _timestamp(item["handoff_expires_at"], code=code)
    if type(item["baseline_generation_id"]) is not str or STREAM_GENERATION_ID_RE.fullmatch(item["baseline_generation_id"]) is None:
        _fail(code)
    _ascii_text(item["database_system_identifier"], code=code)
    if type(item["timeline_id"]) is not int or not 1 <= item["timeline_id"] <= 2**31 - 1:
        _fail(code)
    if type(item["wal_segment_size_bytes"]) is not int or not 1 <= item["wal_segment_size_bytes"] <= 2**31:
        _fail(code)
    _lsn(item["baseline_wal_lsn"], code=code)
    _lsn(item["wal_chain_start_lsn"], code=code)
    base_end, base_end_value = _lsn(item["base_backup_end_lsn"], code=code)
    del base_end
    target_lsn, target_value = _lsn(item["target_lsn"], code=code)
    if target_value < base_end_value:
        _fail(code)
    if type(item["object_count"]) is not int or not 1 <= item["object_count"] <= 1_000_000:
        _fail(code)
    canonical = _canonical(item, code=code)
    return _ContextFacts(
        canonical_context=canonical,
        context_sha256=hashlib.sha256(_CONTEXT_DOMAIN + canonical).hexdigest(),
        source_site=source,
        destination_site=destination,
        target_lsn=target_lsn,
        target_lsn_value=target_value,
        object_version_set_sha256=item["object_version_set_sha256"],
        handoff_expires_at=handoff_expires,
    )


def _coverage_from_inputs(
    coverage: object,
    inputs: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckCoverage:
    if type(inputs) is not PhysicalWalV2RemoteAckCoverageInputs:
        _fail("V2_REMOTE_ACK_COVERAGE_INPUTS_REQUIRED")
    try:
        return require_verified_physical_wal_v2_remote_ack_coverage(
            coverage,
            base_backup_evidence=inputs.base_backup_evidence,
            blob_frontier_coverage=inputs.blob_frontier_coverage,
            blob_owner_coverage=inputs.blob_owner_coverage,
            blob_expected_owner_public_key=inputs.blob_expected_owner_public_key,
            target_wal_continuity=inputs.target_wal_continuity,
            target_wal_continuity_receipt=inputs.target_wal_continuity_receipt,
            manifest=inputs.manifest,
            handoff_receipt=inputs.handoff_receipt,
            blob_scope=inputs.blob_scope,
            continuity_scope=inputs.continuity_scope,
            scope=inputs.scope,
            now=now,
        )
    except PhysicalWalV2RemoteAckCoverageError as exc:
        raise PhysicalWalV2RemoteAckError("V2_REMOTE_ACK_COVERAGE_INVALID") from exc


def mint_physical_wal_v2_remote_ack_context(
    *,
    coverage: VerifiedPhysicalWalV2RemoteAckCoverage,
    inputs: PhysicalWalV2RemoteAckCoverageInputs,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckContext:
    """Refresh V2 coverage and turn it into a private canonical context."""

    observed_now = _utc(now, code="V2_REMOTE_ACK_CONTEXT_CLOCK_INVALID")
    checked = _coverage_from_inputs(coverage, inputs, now=observed_now)
    facts = _context_facts(_context_mapping(checked), code="V2_REMOTE_ACK_CONTEXT_INVALID")
    result = VerifiedPhysicalWalV2RemoteAckContext(
        canonical_context=facts.canonical_context,
        context_sha256=facts.context_sha256,
        source_site=facts.source_site,
        destination_site=facts.destination_site,
        target_lsn=facts.target_lsn,
        object_version_set_sha256=facts.object_version_set_sha256,
        handoff_expires_at=facts.handoff_expires_at,
    )
    object.__setattr__(result, "_capability", _CONTEXT_CAPABILITY)
    _CONTEXT_STATES[result] = _ContextState(coverage=checked, inputs=inputs)
    return require_verified_physical_wal_v2_remote_ack_context(result, now=observed_now)


def require_verified_physical_wal_v2_remote_ack_context(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckContext:
    """Revalidate V2 coverage before a source can sign a request."""

    if (
        type(value) is not VerifiedPhysicalWalV2RemoteAckContext
        or value._capability is not _CONTEXT_CAPABILITY
    ):
        _fail("V2_REMOTE_ACK_CONTEXT_CAPABILITY_REQUIRED")
    state = _CONTEXT_STATES.get(value)
    if state is None:
        _fail("V2_REMOTE_ACK_CONTEXT_PROVENANCE_MISSING")
    observed_now = _utc(now, code="V2_REMOTE_ACK_CONTEXT_CLOCK_INVALID")
    checked = _coverage_from_inputs(state.coverage, state.inputs, now=observed_now)
    facts = _context_facts(_context_mapping(checked), code="V2_REMOTE_ACK_CONTEXT_INVALID")
    if (
        value.canonical_context != facts.canonical_context
        or value.context_sha256 != facts.context_sha256
        or value.source_site != facts.source_site
        or value.destination_site != facts.destination_site
        or value.target_lsn != facts.target_lsn
        or value.object_version_set_sha256 != facts.object_version_set_sha256
        or value.handoff_expires_at != facts.handoff_expires_at
    ):
        _fail("V2_REMOTE_ACK_CONTEXT_TAMPERED")
    return value


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalWalV2RemoteAckConfig:
        _fail("V2_REMOTE_ACK_CONFIG_REQUIRED")
    if value.enabled is not True:
        _fail("V2_REMOTE_ACK_CONFIG_DISABLED")
    context_sha256 = _sha256(value.expected_context_sha256, code="V2_REMOTE_ACK_CONFIG_INVALID")
    source = _site(value.expected_source_site, code="V2_REMOTE_ACK_CONFIG_INVALID")
    destination = _site(value.expected_destination_site, code="V2_REMOTE_ACK_CONFIG_INVALID")
    if source == destination:
        _fail("V2_REMOTE_ACK_CONFIG_INVALID")
    source_key = _public_key(value.expected_source_public_key, code="V2_REMOTE_ACK_CONFIG_INVALID")
    destination_key = _public_key(value.expected_destination_public_key, code="V2_REMOTE_ACK_CONFIG_INVALID")
    if source_key == destination_key:
        _fail("V2_REMOTE_ACK_CONFIG_INVALID")
    if (
        type(value.maximum_evidence_age_seconds) is not int
        or not 1 <= value.maximum_evidence_age_seconds <= MAX_PHYSICAL_WAL_V2_REMOTE_ACK_MAXIMUM_AGE_SECONDS
    ):
        _fail("V2_REMOTE_ACK_CONFIG_INVALID")
    return _ConfigFacts(
        context_sha256=context_sha256,
        source_site=source,
        destination_site=destination,
        source_public_key=source_key,
        destination_public_key=destination_key,
        maximum_age_seconds=value.maximum_evidence_age_seconds,
    )


def _request_facts(value: object, *, config: _ConfigFacts, now: datetime) -> _RequestFacts:
    mapping, raw = _parse_canonical_mapping(value, code="V2_REMOTE_ACK_REQUEST_INVALID")
    item = _exact_mapping(mapping, fields=_REQUEST_FIELDS, code="V2_REMOTE_ACK_REQUEST_INVALID")
    if (
        item["schema"] != PHYSICAL_WAL_V2_REMOTE_ACK_REQUEST_SCHEMA
        or item["version"] != PHYSICAL_WAL_V2_REMOTE_ACK_VERSION
        or item["kind"] != "physical-wal-v2-replay-ack-request"
    ):
        _fail("V2_REMOTE_ACK_REQUEST_INVALID")
    context = _context_facts(item["context"], code="V2_REMOTE_ACK_REQUEST_CONTEXT_INVALID")
    if (
        item["context_sha256"] != context.context_sha256
        or context.context_sha256 != config.context_sha256
        or context.source_site != config.source_site
        or context.destination_site != config.destination_site
    ):
        _fail("V2_REMOTE_ACK_REQUEST_CONTEXT_MISMATCH")
    request_id = _identifier(item["request_id"], code="V2_REMOTE_ACK_REQUEST_INVALID")
    request_nonce = _nonce(item["request_nonce"], code="V2_REMOTE_ACK_REQUEST_INVALID")
    if request_id == request_nonce:
        _fail("V2_REMOTE_ACK_REQUEST_INVALID")
    issued_at = _timestamp(item["issued_at"], code="V2_REMOTE_ACK_REQUEST_INVALID")
    expires_at = _timestamp(item["expires_at"], code="V2_REMOTE_ACK_REQUEST_INVALID")
    if (
        expires_at <= issued_at
        or expires_at - issued_at > timedelta(seconds=config.maximum_age_seconds)
        or issued_at > now + timedelta(seconds=MAX_PHYSICAL_WAL_V2_REMOTE_ACK_FUTURE_SKEW_SECONDS)
        or expires_at <= now
        or expires_at > context.handoff_expires_at
    ):
        _fail("V2_REMOTE_ACK_REQUEST_STALE_OR_EXPIRED")
    _signer(item["source_signer"], expected_public_key=config.source_public_key, code="V2_REMOTE_ACK_REQUEST_SIGNATURE_INVALID")
    unsigned = dict(item)
    signature = unsigned.pop("source_signature")
    _verify_signature(
        unsigned=unsigned,
        signature=signature,
        public_key=config.source_public_key,
        domain=_REQUEST_DOMAIN,
        code="V2_REMOTE_ACK_REQUEST_SIGNATURE_INVALID",
    )
    return _RequestFacts(
        canonical_request=raw,
        context=context,
        request_id=request_id,
        request_nonce=request_nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        source_public_key=config.source_public_key,
    )


def build_physical_wal_v2_remote_ack_request(
    *,
    config: PhysicalWalV2RemoteAckConfig,
    context: VerifiedPhysicalWalV2RemoteAckContext,
    request_id: str,
    request_nonce: str,
    expires_at: datetime,
    source_signer: object,
    now: datetime,
) -> dict[str, object]:
    """Build a source-signed V2 request from freshly verified V2 coverage."""

    observed_now = _utc(now, code="V2_REMOTE_ACK_REQUEST_CLOCK_INVALID")
    normalized = _config(config)
    checked_context = require_verified_physical_wal_v2_remote_ack_context(context, now=observed_now)
    facts = _context_facts_from_verified(checked_context)
    if (
        facts.context_sha256 != normalized.context_sha256
        or facts.source_site != normalized.source_site
        or facts.destination_site != normalized.destination_site
    ):
        _fail("V2_REMOTE_ACK_REQUEST_CONTEXT_MISMATCH")
    request_identity = _identifier(request_id, code="V2_REMOTE_ACK_REQUEST_INVALID")
    nonce = _nonce(request_nonce, code="V2_REMOTE_ACK_REQUEST_INVALID")
    if request_identity == nonce:
        _fail("V2_REMOTE_ACK_REQUEST_INVALID")
    expiry = _utc(expires_at, code="V2_REMOTE_ACK_REQUEST_INVALID")
    if (
        expiry <= observed_now
        or expiry - observed_now > timedelta(seconds=normalized.maximum_age_seconds)
        or expiry > facts.handoff_expires_at
    ):
        _fail("V2_REMOTE_ACK_REQUEST_STALE_OR_EXPIRED")
    signer = _private_signer(
        source_signer,
        expected_public_key=normalized.source_public_key,
        code="V2_REMOTE_ACK_REQUEST_SIGNER_INVALID",
    )
    unsigned: dict[str, object] = {
        "schema": PHYSICAL_WAL_V2_REMOTE_ACK_REQUEST_SCHEMA,
        "version": PHYSICAL_WAL_V2_REMOTE_ACK_VERSION,
        # This signed wire request asks for a replay acknowledgement; neither
        # it nor the paired wire receipt proves a durable ledger commit.
        "kind": "physical-wal-v2-replay-ack-request",
        "context": json.loads(checked_context.canonical_context.decode("ascii")),
        "context_sha256": facts.context_sha256,
        "request_id": request_identity,
        "request_nonce": nonce,
        "issued_at": _render_timestamp(observed_now),
        "expires_at": _render_timestamp(expiry),
        "source_signer": _signer_mapping(normalized.source_public_key),
    }
    result = {**unsigned, "source_signature": _sign(unsigned, signer=signer, domain=_REQUEST_DOMAIN, code="V2_REMOTE_ACK_REQUEST_SIGNER_INVALID")}
    # Force the same parser used by a destination before returning bytes to a caller.
    _request_facts(result, config=normalized, now=observed_now)
    return result


def _context_facts_from_verified(value: VerifiedPhysicalWalV2RemoteAckContext) -> _ContextFacts:
    mapping, raw = _parse_canonical_mapping(value.canonical_context, code="V2_REMOTE_ACK_CONTEXT_TAMPERED")
    facts = _context_facts(mapping, code="V2_REMOTE_ACK_CONTEXT_TAMPERED")
    if facts.canonical_context != raw:
        _fail("V2_REMOTE_ACK_CONTEXT_TAMPERED")
    return facts


def verify_physical_wal_v2_remote_ack_request(
    *,
    source_request: Mapping[str, Any] | bytes,
    config: PhysicalWalV2RemoteAckConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckRequest:
    """Verify one V2 source request without replay consumption or I/O."""

    observed_now = _utc(now, code="V2_REMOTE_ACK_REQUEST_CLOCK_INVALID")
    facts = _request_facts(source_request, config=_config(config), now=observed_now)
    result = VerifiedPhysicalWalV2RemoteAckRequest(
        canonical_request=facts.canonical_request,
        context_sha256=facts.context.context_sha256,
        source_site=facts.context.source_site,
        destination_site=facts.context.destination_site,
        target_lsn=facts.context.target_lsn,
        object_version_set_sha256=facts.context.object_version_set_sha256,
        request_id=facts.request_id,
        request_nonce=facts.request_nonce,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        source_public_key=facts.source_public_key,
    )
    object.__setattr__(result, "_capability", _REQUEST_CAPABILITY)
    return result


def require_verified_physical_wal_v2_remote_ack_request(
    value: object,
    *,
    config: PhysicalWalV2RemoteAckConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckRequest:
    """Refresh an opaque V2 request against current key/context pins."""

    if type(value) is not VerifiedPhysicalWalV2RemoteAckRequest or value._capability is not _REQUEST_CAPABILITY:
        _fail("V2_REMOTE_ACK_REQUEST_CAPABILITY_REQUIRED")
    facts = _request_facts(value.canonical_request, config=_config(config), now=_utc(now, code="V2_REMOTE_ACK_REQUEST_CLOCK_INVALID"))
    if (
        value.context_sha256 != facts.context.context_sha256
        or value.source_site != facts.context.source_site
        or value.destination_site != facts.context.destination_site
        or value.target_lsn != facts.context.target_lsn
        or value.object_version_set_sha256 != facts.context.object_version_set_sha256
        or value.request_id != facts.request_id
        or value.request_nonce != facts.request_nonce
        or value.issued_at != facts.issued_at
        or value.expires_at != facts.expires_at
        or value.source_public_key != facts.source_public_key
    ):
        _fail("V2_REMOTE_ACK_REQUEST_TAMPERED")
    return value


def _request_from_verified(
    value: VerifiedPhysicalWalV2RemoteAckRequest,
    *,
    config: PhysicalWalV2RemoteAckConfig,
    now: datetime,
) -> _RequestFacts:
    checked = require_verified_physical_wal_v2_remote_ack_request(value, config=config, now=now)
    facts = _request_facts(checked.canonical_request, config=_config(config), now=_utc(now, code="V2_REMOTE_ACK_REQUEST_CLOCK_INVALID"))
    if (
        checked.context_sha256 != facts.context.context_sha256
        or checked.request_id != facts.request_id
        or checked.request_nonce != facts.request_nonce
    ):
        _fail("V2_REMOTE_ACK_REQUEST_TAMPERED")
    return facts


def _recovery_facts(
    value: object,
    *,
    request: _RequestFacts,
    config: _ConfigFacts,
    now: datetime,
) -> PhysicalWalV2RemoteAckReceiverRecoveryEvidence:
    if type(value) is not PhysicalWalV2RemoteAckReceiverRecoveryEvidence:
        _fail("V2_REMOTE_ACK_RECEIVER_RECOVERY_REQUIRED")
    evidence = value
    request_sha = hashlib.sha256(request.canonical_request).hexdigest()
    replay, replay_value = _lsn(evidence.replay_lsn, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID")
    observed_at = _utc(evidence.observed_at, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID")
    if (
        _sha256(evidence.source_request_sha256, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID") != request_sha
        or _sha256(evidence.context_sha256, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID") != request.context.context_sha256
        or _sha256(evidence.receiver_recovery_evidence_sha256, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID")
        != evidence.receiver_recovery_evidence_sha256
        or _site(evidence.receiver_site, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID") != config.destination_site
        or _site(evidence.source_site, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID") != request.context.source_site
        or _site(evidence.destination_site, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID") != request.context.destination_site
        or _sha256(evidence.object_version_set_sha256, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID")
        != request.context.object_version_set_sha256
        or _lsn(evidence.target_lsn, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_INVALID")[0] != request.context.target_lsn
        or replay != evidence.replay_lsn
        or replay_value < request.context.target_lsn_value
        or evidence.in_recovery is not True
        or evidence.role != "standby"
        or observed_at > now + timedelta(seconds=MAX_PHYSICAL_WAL_V2_REMOTE_ACK_FUTURE_SKEW_SECONDS)
        or observed_at < now - timedelta(seconds=config.maximum_age_seconds)
    ):
        _fail("V2_REMOTE_ACK_RECEIVER_RECOVERY_MISMATCH")
    return evidence


def verify_physical_wal_v2_remote_ack_receiver_recovery_evidence(
    *,
    evidence: PhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    source_request: VerifiedPhysicalWalV2RemoteAckRequest,
    config: PhysicalWalV2RemoteAckConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence:
    """Bind a trusted local recovery observation to one V2 request."""

    observed_now = _utc(now, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_CLOCK_INVALID")
    request = _request_from_verified(source_request, config=config, now=observed_now)
    checked = _recovery_facts(evidence, request=request, config=_config(config), now=observed_now)
    result = VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence(
        evidence=checked,
        request_sha256=hashlib.sha256(request.canonical_request).hexdigest(),
        context_sha256=request.context.context_sha256,
    )
    object.__setattr__(result, "_capability", _RECOVERY_CAPABILITY)
    return result


def require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence(
    value: object,
    *,
    source_request: VerifiedPhysicalWalV2RemoteAckRequest,
    config: PhysicalWalV2RemoteAckConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence:
    """Refresh an opaque recovery observation; no database read occurs here."""

    if (
        type(value) is not VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence
        or value._capability is not _RECOVERY_CAPABILITY
    ):
        _fail("V2_REMOTE_ACK_RECEIVER_RECOVERY_CAPABILITY_REQUIRED")
    observed_now = _utc(now, code="V2_REMOTE_ACK_RECEIVER_RECOVERY_CLOCK_INVALID")
    request = _request_from_verified(source_request, config=config, now=observed_now)
    evidence = _recovery_facts(value.evidence, request=request, config=_config(config), now=observed_now)
    if (
        value.request_sha256 != hashlib.sha256(request.canonical_request).hexdigest()
        or value.context_sha256 != request.context.context_sha256
        or value.evidence != evidence
    ):
        _fail("V2_REMOTE_ACK_RECEIVER_RECOVERY_TAMPERED")
    return value


def build_physical_wal_v2_remote_ack_receipt(
    *,
    config: PhysicalWalV2RemoteAckConfig,
    source_request: VerifiedPhysicalWalV2RemoteAckRequest,
    receiver_recovery_evidence: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    receipt_id: str,
    receipt_nonce: str,
    destination_signer: object,
    now: datetime,
) -> dict[str, object]:
    """Build a signed wire receipt only.

    This low-level builder deliberately does not persist anything.  A real V2
    receiver ledger must call equivalent logic only inside its atomic durable
    commit; callers must never treat this return value as ledger proof.
    """

    observed_now = _utc(now, code="V2_REMOTE_ACK_RECEIPT_CLOCK_INVALID")
    normalized = _config(config)
    request = _request_from_verified(source_request, config=config, now=observed_now)
    recovery = require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence(
        receiver_recovery_evidence,
        source_request=source_request,
        config=config,
        now=observed_now,
    )
    evidence = recovery.evidence
    if observed_now < request.issued_at or observed_now > request.expires_at:
        _fail("V2_REMOTE_ACK_RECEIPT_STALE_OR_EXPIRED")
    receipt_identity = _identifier(receipt_id, code="V2_REMOTE_ACK_RECEIPT_INVALID")
    nonce = _nonce(receipt_nonce, code="V2_REMOTE_ACK_RECEIPT_INVALID")
    if len({request.request_id, request.request_nonce, receipt_identity, nonce}) != 4:
        _fail("V2_REMOTE_ACK_RECEIPT_INVALID")
    signer = _private_signer(
        destination_signer,
        expected_public_key=normalized.destination_public_key,
        code="V2_REMOTE_ACK_RECEIPT_SIGNER_INVALID",
    )
    unsigned: dict[str, object] = {
        "schema": PHYSICAL_WAL_V2_REMOTE_ACK_RECEIPT_SCHEMA,
        "version": PHYSICAL_WAL_V2_REMOTE_ACK_VERSION,
        # Deliberately do not label this non-persisting signed wire receipt as
        # durable.  Only the separate atomic receiver-ledger boundary may
        # make that claim in a future protocol revision.
        "kind": "physical-wal-v2-replay-ack-receipt",
        "context_sha256": request.context.context_sha256,
        "request_id": request.request_id,
        "request_nonce": request.request_nonce,
        "request_issued_at": _render_timestamp(request.issued_at),
        "request_expires_at": _render_timestamp(request.expires_at),
        "source_request_sha256": hashlib.sha256(request.canonical_request).hexdigest(),
        "receiver_recovery_evidence_sha256": evidence.receiver_recovery_evidence_sha256,
        "receiver_replay_lsn": evidence.replay_lsn,
        "receipt_id": receipt_identity,
        "receipt_nonce": nonce,
        "acknowledged_at": _render_timestamp(observed_now),
        "destination_signer": _signer_mapping(normalized.destination_public_key),
    }
    result = {**unsigned, "destination_signature": _sign(unsigned, signer=signer, domain=_RECEIPT_DOMAIN, code="V2_REMOTE_ACK_RECEIPT_SIGNER_INVALID")}
    _receipt_facts(result, request=request, config=normalized, now=observed_now)
    return result


def _receipt_facts(value: object, *, request: _RequestFacts, config: _ConfigFacts, now: datetime) -> _ReceiptFacts:
    mapping, raw = _parse_canonical_mapping(value, code="V2_REMOTE_ACK_RECEIPT_INVALID")
    item = _exact_mapping(mapping, fields=_RECEIPT_FIELDS, code="V2_REMOTE_ACK_RECEIPT_INVALID")
    if (
        item["schema"] != PHYSICAL_WAL_V2_REMOTE_ACK_RECEIPT_SCHEMA
        or item["version"] != PHYSICAL_WAL_V2_REMOTE_ACK_VERSION
        or item["kind"] != "physical-wal-v2-replay-ack-receipt"
        or _sha256(item["context_sha256"], code="V2_REMOTE_ACK_RECEIPT_INVALID") != request.context.context_sha256
        or _identifier(item["request_id"], code="V2_REMOTE_ACK_RECEIPT_INVALID") != request.request_id
        or _nonce(item["request_nonce"], code="V2_REMOTE_ACK_RECEIPT_INVALID") != request.request_nonce
        or _timestamp(item["request_issued_at"], code="V2_REMOTE_ACK_RECEIPT_INVALID") != request.issued_at
        or _timestamp(item["request_expires_at"], code="V2_REMOTE_ACK_RECEIPT_INVALID") != request.expires_at
        or _sha256(item["source_request_sha256"], code="V2_REMOTE_ACK_RECEIPT_INVALID")
        != hashlib.sha256(request.canonical_request).hexdigest()
    ):
        _fail("V2_REMOTE_ACK_RECEIPT_REQUEST_MISMATCH")
    recovery_sha = _sha256(item["receiver_recovery_evidence_sha256"], code="V2_REMOTE_ACK_RECEIPT_INVALID")
    replay, replay_value = _lsn(item["receiver_replay_lsn"], code="V2_REMOTE_ACK_RECEIPT_INVALID")
    if replay_value < request.context.target_lsn_value:
        _fail("V2_REMOTE_ACK_RECEIPT_REQUEST_MISMATCH")
    receipt_id = _identifier(item["receipt_id"], code="V2_REMOTE_ACK_RECEIPT_INVALID")
    receipt_nonce = _nonce(item["receipt_nonce"], code="V2_REMOTE_ACK_RECEIPT_INVALID")
    if len({request.request_id, request.request_nonce, receipt_id, receipt_nonce}) != 4:
        _fail("V2_REMOTE_ACK_RECEIPT_INVALID")
    acknowledged_at = _timestamp(item["acknowledged_at"], code="V2_REMOTE_ACK_RECEIPT_INVALID")
    if (
        acknowledged_at < request.issued_at
        or acknowledged_at > request.expires_at
        or acknowledged_at > now + timedelta(seconds=MAX_PHYSICAL_WAL_V2_REMOTE_ACK_FUTURE_SKEW_SECONDS)
        or acknowledged_at < now - timedelta(seconds=config.maximum_age_seconds)
    ):
        _fail("V2_REMOTE_ACK_RECEIPT_STALE_OR_EXPIRED")
    _signer(item["destination_signer"], expected_public_key=config.destination_public_key, code="V2_REMOTE_ACK_RECEIPT_SIGNATURE_INVALID")
    unsigned = dict(item)
    signature = unsigned.pop("destination_signature")
    _verify_signature(
        unsigned=unsigned,
        signature=signature,
        public_key=config.destination_public_key,
        domain=_RECEIPT_DOMAIN,
        code="V2_REMOTE_ACK_RECEIPT_SIGNATURE_INVALID",
    )
    return _ReceiptFacts(
        canonical_receipt=raw,
        receipt_id=receipt_id,
        receipt_nonce=receipt_nonce,
        acknowledged_at=acknowledged_at,
        receiver_recovery_evidence_sha256=recovery_sha,
        receiver_replay_lsn=replay,
        receiver_replay_lsn_value=replay_value,
        destination_public_key=config.destination_public_key,
    )


def verify_physical_wal_v2_remote_ack_evidence(
    *,
    source_request: Mapping[str, Any] | bytes,
    destination_receipt: Mapping[str, Any] | bytes,
    config: PhysicalWalV2RemoteAckConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckEvidence:
    """Verify the V2 signed pair; it remains less than ledger authority."""

    observed_now = _utc(now, code="V2_REMOTE_ACK_EVIDENCE_CLOCK_INVALID")
    normalized = _config(config)
    request = _request_facts(source_request, config=normalized, now=observed_now)
    receipt = _receipt_facts(destination_receipt, request=request, config=normalized, now=observed_now)
    result = VerifiedPhysicalWalV2RemoteAckEvidence(
        canonical_request=request.canonical_request,
        canonical_receipt=receipt.canonical_receipt,
        context_sha256=request.context.context_sha256,
        request_id=request.request_id,
        request_nonce=request.request_nonce,
        receipt_id=receipt.receipt_id,
        receipt_nonce=receipt.receipt_nonce,
        source_public_key=request.source_public_key,
        destination_public_key=receipt.destination_public_key,
        receiver_recovery_evidence_sha256=receipt.receiver_recovery_evidence_sha256,
        receiver_replay_lsn=receipt.receiver_replay_lsn,
        acknowledged_at=receipt.acknowledged_at,
    )
    object.__setattr__(result, "_capability", _EVIDENCE_CAPABILITY)
    return result


def require_verified_physical_wal_v2_remote_ack_evidence(
    value: object,
    *,
    config: PhysicalWalV2RemoteAckConfig,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckEvidence:
    """Refresh signed V2 evidence against current key/context pins only."""

    if type(value) is not VerifiedPhysicalWalV2RemoteAckEvidence or value._capability is not _EVIDENCE_CAPABILITY:
        _fail("V2_REMOTE_ACK_EVIDENCE_CAPABILITY_REQUIRED")
    fresh = verify_physical_wal_v2_remote_ack_evidence(
        source_request=value.canonical_request,
        destination_receipt=value.canonical_receipt,
        config=config,
        now=now,
    )
    fields = (
        "canonical_request",
        "canonical_receipt",
        "context_sha256",
        "request_id",
        "request_nonce",
        "receipt_id",
        "receipt_nonce",
        "source_public_key",
        "destination_public_key",
        "receiver_recovery_evidence_sha256",
        "receiver_replay_lsn",
        "acknowledged_at",
    )
    if any(getattr(value, name) != getattr(fresh, name) for name in fields):
        _fail("V2_REMOTE_ACK_EVIDENCE_TAMPERED")
    return value
