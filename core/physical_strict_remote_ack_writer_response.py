"""Root-owned strict remote-ack to writer-response coupling boundary.

This module closes the gap between an IR signed durable/replay receipt and an
FI application response.  A signed remote acknowledgement alone is evidence;
it does not make the FI write response wait, does not fence FI when the
acknowledgement disappears, and does not bind a local durable response to that
receipt.  The owning boundary below requires all of those local inputs before
it calls an injected writer transaction callback.

The module is default-disabled and contains no database client, PostgreSQL
query, network request, Object Storage request, SSH, Docker, shell, route
change, deployment, or promotion code.  The injected callback is the only
place a future root-owned writer adapter may perform a local transaction.  It
must atomically persist its own durable response/uniqueness record before
returning the signed receipt required here.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Iterator, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_remote_ack import (
    PhysicalWalRemoteAckBinding,
    PhysicalWalRemoteAckError,
    VerifiedPhysicalWalRemoteAckEvidence,
    verify_physical_wal_remote_ack_request,
    require_verified_physical_wal_remote_ack_evidence,
)
from core.physical_wal_remote_ack_receiver_ledger import (
    PhysicalWalRemoteAckReceiverLedgerError,
    PhysicalWalRemoteAckReceiverLedgerResult,
    VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence,
    require_verified_physical_wal_remote_ack_receiver_recovery_evidence,
)


__all__ = (
    "DEFAULT_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_SCHEMA",
    "PHYSICAL_STRICT_REMOTE_ACK_WRITER_FENCE_RECEIPT_SCHEMA",
    "PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_DEFAULT_ENABLED",
    "PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_LEDGER_SCHEMA",
    "PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_OBSERVATION_SCHEMA",
    "PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA",
    "PhysicalStrictRemoteAckWriterCommitBoundary",
    "PhysicalStrictRemoteAckWriterCommitInstruction",
    "PhysicalStrictRemoteAckWriterResponseBinding",
    "PhysicalStrictRemoteAckWriterResponseConfig",
    "PhysicalStrictRemoteAckWriterResponseError",
    "StrictRemoteAckWriterResponseOracleProjection",
    "VerifiedPhysicalStrictRemoteAckWriterCommitEvidence",
    "VerifiedPhysicalStrictRemoteAckWriterCommitPermit",
    "VerifiedPhysicalStrictRemoteAckWriterFence",
    "VerifiedPhysicalStrictRemoteAckWriterResponseObservation",
    "commit_physical_strict_remote_ack_writer_response",
    "issue_physical_strict_remote_ack_writer_commit_permit",
    "mint_physical_strict_remote_ack_writer_response_observation",
    "project_verified_physical_strict_remote_ack_writer_response_observation",
    "require_verified_physical_strict_remote_ack_writer_commit_evidence",
    "require_verified_physical_strict_remote_ack_writer_commit_permit",
    "require_verified_physical_strict_remote_ack_writer_fence",
    "require_verified_physical_strict_remote_ack_writer_response_observation",
    "verify_physical_strict_remote_ack_writer_fence",
)


PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA = (
    "gold-trade-physical-strict-remote-ack-writer-response-v1"
)
PHYSICAL_STRICT_REMOTE_ACK_WRITER_FENCE_RECEIPT_SCHEMA = (
    "gold-trade-physical-strict-remote-ack-writer-fence-receipt-v1"
)
PHYSICAL_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_SCHEMA = (
    "gold-trade-physical-strict-remote-ack-writer-commit-receipt-v1"
)
PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_OBSERVATION_SCHEMA = (
    "gold-trade-physical-strict-remote-ack-writer-response-observation-v1"
)
PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_LEDGER_SCHEMA = (
    "gold-trade-physical-strict-remote-ack-writer-response-ledger-v1"
)
PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_DEFAULT_ENABLED = False

STRICT_REMOTE_ACK_WRITER_FENCE_MODE = "term-fenced-before-commit-v1"
STRICT_REMOTE_ACK_WRITER_RESPONSE_MODE = (
    "strict-remote-durable-replay-before-local-ack-v1"
)
STRICT_REMOTE_ACK_WRITER_RECEIVER_RESPONSE_SOURCE = "durable-ledger-receipt-v1"
STRICT_REMOTE_ACK_WRITER_ATOMIC_COMMIT_BOUNDARY = (
    "writer-transaction-remote-ack-response-v1"
)

DEFAULT_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAX_EVIDENCE_AGE_SECONDS = 60
MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_EVIDENCE_AGE_SECONDS = 300
MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_FUTURE_SKEW_SECONDS = 5
DEFAULT_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAXIMUM_ENTRIES = 1024
MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAXIMUM_ENTRIES = 8192
MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_LEDGER_BYTES = 16 * 1024 * 1024
MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_RECEIPT_BYTES = 64 * 1024
MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_FENCE_BYTES = 32 * 1024

_FENCE_DOMAIN = b"gold-trade-physical-strict-remote-ack-writer-fence-receipt-v1\x00"
_COMMIT_DOMAIN = b"gold-trade-physical-strict-remote-ack-writer-commit-receipt-v1\x00"
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_FENCE_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "fence_id",
        "issued_at",
        "expires_at",
        "signature_base64",
    }
)
_COMMIT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "configuration_sha256",
        "permit_binding_sha256",
        "commit_id",
        "source_request_sha256",
        "destination_receipt_sha256",
        "request_id",
        "request_nonce",
        "receipt_id",
        "receipt_nonce",
        "receiver_recovery_evidence_sha256",
        "receiver_replay_lsn",
        "binding",
        "atomic_commit_boundary",
        "local_commit_record_id",
        "local_response_id",
        "committed_at",
        "signature_base64",
    }
)
_LEDGER_FIELDS = frozenset({"schema", "version", "configuration_sha256", "entries"})
_LEDGER_ENTRY_FIELDS = frozenset(
    {
        "source_request_sha256",
        "destination_receipt_sha256",
        "receipt_id",
        "receipt_nonce",
        "commit_receipt_sha256",
        "commit_id",
        "local_commit_record_id",
        "local_response_id",
        "committed_at",
        "permit_binding_sha256",
    }
)
_VERIFIED_FENCE_CAPABILITY = object()
_VERIFIED_PERMIT_CAPABILITY = object()
_VERIFIED_COMMIT_EVIDENCE_CAPABILITY = object()
_VERIFIED_OBSERVATION_CAPABILITY = object()


class PhysicalStrictRemoteAckWriterResponseError(ValueError):
    """A fail-closed strict writer-response admission code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalStrictRemoteAckWriterResponseBinding:
    """Exact FI-to-IR route/term/frontier identity for one write response."""

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    schema_revision: str
    stream_generation_id: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    timeline_id: int
    destination_age_recipient: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    target_acknowledged_wal_lsn: str
    blob_object_frontier_wal_lsn: str
    manifest_sha256es: tuple[str, ...]
    object_versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PhysicalStrictRemoteAckWriterResponseConfig:
    """Root-only defaults-off config and key pins for one exact direction."""

    state_root: Path | None = None
    expected_binding: PhysicalStrictRemoteAckWriterResponseBinding | None = None
    expected_source_remote_ack_public_key: bytes = b""
    expected_destination_remote_ack_public_key: bytes = b""
    fence_signer_public_key: bytes = b""
    local_commit_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAX_EVIDENCE_AGE_SECONDS
    )
    maximum_entries: int = DEFAULT_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAXIMUM_ENTRIES


@dataclass(frozen=True)
class VerifiedPhysicalStrictRemoteAckWriterFence:
    """Opaque signed active source-fence observation; not a fence action."""

    canonical_receipt: bytes
    fence_signer_public_key: bytes
    binding: PhysicalStrictRemoteAckWriterResponseBinding
    fence_id: str
    issued_at: datetime
    expires_at: datetime
    receipt_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalStrictRemoteAckWriterCommitPermit:
    """Opaque one-continuity-point permit, never a writer/start permit."""

    configuration_sha256: str
    binding: PhysicalStrictRemoteAckWriterResponseBinding
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    remote_ack_evidence: VerifiedPhysicalWalRemoteAckEvidence
    receiver_recovery_evidence: VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence
    durable_ledger_result: PhysicalWalRemoteAckReceiverLedgerResult
    fence: VerifiedPhysicalStrictRemoteAckWriterFence
    source_request_sha256: str
    destination_receipt_sha256: str
    permit_binding_sha256: str
    issued_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalStrictRemoteAckWriterCommitInstruction:
    """Exact input given to the injected local transaction boundary.

    Its value is insufficient to mint evidence: the boundary must return a
    pinned-key signed canonical receipt only after it atomically records the
    local response and unique remote receipt consumption in its own durable
    transaction.
    """

    schema: str
    configuration_sha256: str
    permit_binding_sha256: str
    commit_id: str
    source_request_sha256: str
    destination_receipt_sha256: str
    request_id: str
    request_nonce: str
    receipt_id: str
    receipt_nonce: str
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    binding: PhysicalStrictRemoteAckWriterResponseBinding
    issued_at: datetime


class PhysicalStrictRemoteAckWriterCommitBoundary(Protocol):
    """Injected, root-owned local transaction boundary.

    ``commit_after_verified_remote_ack`` must atomically persist its local
    response record and a unique remote receipt-consumption record before
    producing the signed receipt. It must not issue an application response
    before that transaction commits. This contract intentionally has no
    default database implementation.
    """

    def commit_after_verified_remote_ack(
        self,
        *,
        instruction: PhysicalStrictRemoteAckWriterCommitInstruction,
    ) -> bytes: ...


@dataclass(frozen=True)
class VerifiedPhysicalStrictRemoteAckWriterCommitEvidence:
    """Opaque evidence of one signed durable local writer response."""

    permit: VerifiedPhysicalStrictRemoteAckWriterCommitPermit
    instruction: PhysicalStrictRemoteAckWriterCommitInstruction
    canonical_commit_receipt: bytes
    commit_receipt_sha256: str
    committed_at: datetime
    local_commit_record_id: str
    local_response_id: str
    local_commit_signer_public_key: bytes
    maximum_evidence_age_seconds: int
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalStrictRemoteAckWriterResponseObservation:
    """Opaque typed observation consumable by the Full-Matrix oracle only."""

    commit_evidence: VerifiedPhysicalStrictRemoteAckWriterCommitEvidence
    observation_sha256: str
    observed_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class StrictRemoteAckWriterResponseOracleProjection:
    """Non-authorizing projection of an opaque strict writer-response record."""

    schema: str
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    schema_revision: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    timeline_id: int
    stream_generation_id: str
    destination_age_recipient: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    target_acknowledged_wal_lsn: str
    blob_object_frontier_wal_lsn: str
    committed_at: datetime
    observed_at: datetime
    source_request_sha256: str
    destination_receipt_sha256: str
    local_commit_record_id: str
    local_response_id: str


@dataclass(frozen=True)
class _BindingFacts:
    binding: PhysicalStrictRemoteAckWriterResponseBinding
    baseline_wal_lsn_value: int
    target_acknowledged_wal_lsn_value: int
    blob_object_frontier_wal_lsn_value: int
    object_versions: tuple[tuple[str, str], ...]
    binding_sha256: str


@dataclass(frozen=True)
class _ConfigFacts:
    state_root: Path
    binding: _BindingFacts
    source_remote_ack_public_key: bytes
    destination_remote_ack_public_key: bytes
    fence_signer_public_key: bytes
    local_commit_signer_public_key: bytes
    maximum_evidence_age_seconds: int
    maximum_entries: int
    configuration_sha256: str


@dataclass(frozen=True)
class _FenceFacts:
    canonical_receipt: bytes
    fence_id: str
    issued_at: datetime
    expires_at: datetime
    receipt_sha256: str


@dataclass(frozen=True)
class _CommitFacts:
    canonical_receipt: bytes
    commit_receipt_sha256: str
    committed_at: datetime
    local_commit_record_id: str
    local_response_id: str


def _fail(code: str) -> None:
    raise PhysicalStrictRemoteAckWriterResponseError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("CANONICAL_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("CANONICAL_JSON_CONSTANT_FORBIDDEN")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    return _utc(parsed, code=code)


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    digest = _text(value, pattern=SHA256_RE, code=code)
    if digest == "0" * 64:
        _fail(code)
    return digest


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    text = _text(value, pattern=_LSN_RE, code=code)
    high, low = text.split("/", 1)
    return text, (int(high, 16) << 32) | int(low, 16)


def _public_key(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _signature(value: object, *, code: str) -> bytes:
    if not isinstance(value, str):
        _fail(code)
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(decoded) != 64:
        _fail(code)
    return decoded


def _identifier(value: object, *, code: str) -> str:
    return _text(value, pattern=_IDENTIFIER_RE, code=code)


def _nonce(value: object, *, code: str) -> str:
    return _text(value, pattern=_NONCE_RE, code=code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _parse_canonical_mapping(value: object, *, maximum_bytes: int, code: str) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, Mapping):
        mapping = dict(value)
        raw = _canonical(mapping, code=code)
    elif isinstance(value, bytes):
        raw = value
        if not 1 <= len(raw) <= maximum_bytes:
            _fail(code)
        try:
            mapping = json.loads(
                raw.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except PhysicalStrictRemoteAckWriterResponseError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _fail(code)
        if not isinstance(mapping, dict) or _canonical(mapping, code=code) != raw:
            _fail(code)
    else:
        _fail(code)
    if not 1 <= len(raw) <= maximum_bytes:
        _fail(code)
    return mapping, raw


def _normalise_object_versions(value: object, *, code: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple) or not value:
        _fail(code)
    result: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            _fail(code)
        result.append(
            (
                _text(item[0], pattern=OBJECT_KEY_RE, code=code),
                _text(item[1], pattern=VERSION_ID_RE, code=code),
            )
        )
    if len(set(result)) != len(result):
        _fail(code)
    return tuple(result)


def _normalise_manifest_hashes(value: object, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        _fail(code)
    result = tuple(_sha256(item, code=code) for item in value)
    if len(set(result)) != len(result):
        _fail(code)
    return result


def _binding_payload(facts: _BindingFacts) -> dict[str, object]:
    value = facts.binding
    return {
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "schema_revision": value.schema_revision,
        "stream_generation_id": value.stream_generation_id,
        "baseline_generation_id": value.baseline_generation_id,
        "baseline_manifest_sha256": value.baseline_manifest_sha256,
        "baseline_wal_lsn": value.baseline_wal_lsn,
        "timeline_id": value.timeline_id,
        "destination_age_recipient": value.destination_age_recipient,
        "route_binding_sha256": value.route_binding_sha256,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": value.writer_lease_id,
        "witness_transition_id": value.witness_transition_id,
        "witnessed_term_proof_sha256": value.witnessed_term_proof_sha256,
        "target_acknowledged_wal_lsn": value.target_acknowledged_wal_lsn,
        "blob_object_frontier_wal_lsn": value.blob_object_frontier_wal_lsn,
        "manifest_sha256es": list(value.manifest_sha256es),
        "object_versions": [
            {"object_key": object_key, "version_id": version_id}
            for object_key, version_id in facts.object_versions
        ],
    }


def _normalise_binding(value: object, *, code: str) -> _BindingFacts:
    if type(value) is not PhysicalStrictRemoteAckWriterResponseBinding:
        _fail(code)
    if value.source_site != "webapp_fi" or value.destination_site != "webapp_ir":
        _fail(code)
    campaign_id = _text(value.campaign_id, pattern=CAMPAIGN_ID_RE, code=code)
    release_sha = _text(value.release_sha, pattern=RELEASE_SHA_RE, code=code)
    schema_revision = _identifier(value.schema_revision, code=code)
    stream_generation_id = _text(value.stream_generation_id, pattern=STREAM_GENERATION_ID_RE, code=code)
    baseline_generation_id = _text(
        value.baseline_generation_id,
        pattern=STREAM_GENERATION_ID_RE,
        code=code,
    )
    baseline_manifest_sha256 = _sha256(value.baseline_manifest_sha256, code=code)
    baseline_wal_lsn, baseline_wal_lsn_value = _lsn(value.baseline_wal_lsn, code=code)
    if type(value.timeline_id) is not int or not 1 <= value.timeline_id <= 0xFFFFFFFF:
        _fail(code)
    destination_age_recipient = _text(value.destination_age_recipient, pattern=AGE_RECIPIENT_RE, code=code)
    route_binding_sha256 = _sha256(value.route_binding_sha256, code=code)
    if type(value.writer_epoch) is not int or value.writer_epoch < 1:
        _fail(code)
    writer_lease_id = _text(value.writer_lease_id, pattern=LEASE_ID_RE, code=code)
    witness_transition_id = _identifier(value.witness_transition_id, code=code)
    witnessed_term_proof_sha256 = _sha256(value.witnessed_term_proof_sha256, code=code)
    target_acknowledged_wal_lsn, target_acknowledged_wal_lsn_value = _lsn(
        value.target_acknowledged_wal_lsn,
        code=code,
    )
    blob_object_frontier_wal_lsn, blob_object_frontier_wal_lsn_value = _lsn(
        value.blob_object_frontier_wal_lsn,
        code=code,
    )
    if (
        target_acknowledged_wal_lsn_value < baseline_wal_lsn_value
        or blob_object_frontier_wal_lsn_value < target_acknowledged_wal_lsn_value
    ):
        _fail(code)
    manifests = _normalise_manifest_hashes(value.manifest_sha256es, code=code)
    versions = _normalise_object_versions(value.object_versions, code=code)
    normalized = PhysicalStrictRemoteAckWriterResponseBinding(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=campaign_id,
        release_sha=release_sha,
        schema_revision=schema_revision,
        stream_generation_id=stream_generation_id,
        baseline_generation_id=baseline_generation_id,
        baseline_manifest_sha256=baseline_manifest_sha256,
        baseline_wal_lsn=baseline_wal_lsn,
        timeline_id=value.timeline_id,
        destination_age_recipient=destination_age_recipient,
        route_binding_sha256=route_binding_sha256,
        writer_epoch=value.writer_epoch,
        writer_lease_id=writer_lease_id,
        witness_transition_id=witness_transition_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        target_acknowledged_wal_lsn=target_acknowledged_wal_lsn,
        blob_object_frontier_wal_lsn=blob_object_frontier_wal_lsn,
        manifest_sha256es=manifests,
        object_versions=versions,
    )
    provisional = _BindingFacts(
        binding=normalized,
        baseline_wal_lsn_value=baseline_wal_lsn_value,
        target_acknowledged_wal_lsn_value=target_acknowledged_wal_lsn_value,
        blob_object_frontier_wal_lsn_value=blob_object_frontier_wal_lsn_value,
        object_versions=versions,
        binding_sha256="",
    )
    return _BindingFacts(
        binding=normalized,
        baseline_wal_lsn_value=baseline_wal_lsn_value,
        target_acknowledged_wal_lsn_value=target_acknowledged_wal_lsn_value,
        blob_object_frontier_wal_lsn_value=blob_object_frontier_wal_lsn_value,
        object_versions=versions,
        binding_sha256=hashlib.sha256(_canonical(_binding_payload(provisional), code=code)).hexdigest(),
    )


def _secure_root(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        _fail(code)
    try:
        resolved = value.resolve(strict=True)
        metadata = os.lstat(value)
    except OSError:
        _fail(code)
    if (
        resolved != value
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _normalise_config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalStrictRemoteAckWriterResponseConfig:
        _fail("CONFIG_INVALID")
    if value.enabled is not True:
        _fail("STRICT_REMOTE_ACK_WRITER_RESPONSE_DISABLED")
    if os.geteuid() != 0:
        _fail("ROOT_RUNTIME_REQUIRED")
    root = _secure_root(value.state_root, code="STATE_ROOT_UNSAFE")
    binding = _normalise_binding(value.expected_binding, code="CONFIG_BINDING_INVALID")
    source_key = _public_key(value.expected_source_remote_ack_public_key, code="SOURCE_REMOTE_ACK_KEY_INVALID")
    destination_key = _public_key(
        value.expected_destination_remote_ack_public_key,
        code="DESTINATION_REMOTE_ACK_KEY_INVALID",
    )
    if source_key == destination_key:
        _fail("REMOTE_ACK_ROUTE_KEYS_MUST_DIFFER")
    fence_key = _public_key(value.fence_signer_public_key, code="FENCE_SIGNER_KEY_INVALID")
    commit_key = _public_key(value.local_commit_signer_public_key, code="COMMIT_SIGNER_KEY_INVALID")
    if (
        type(value.maximum_evidence_age_seconds) is not int
        or not 1 <= value.maximum_evidence_age_seconds <= MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_EVIDENCE_AGE_SECONDS
    ):
        _fail("CONFIG_EVIDENCE_AGE_INVALID")
    if (
        type(value.maximum_entries) is not int
        or not 1 <= value.maximum_entries <= MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAXIMUM_ENTRIES
    ):
        _fail("CONFIG_MAXIMUM_ENTRIES_INVALID")
    payload = {
        "schema": PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
        "binding": _binding_payload(binding),
        "source_remote_ack_public_key_sha256": hashlib.sha256(source_key).hexdigest(),
        "destination_remote_ack_public_key_sha256": hashlib.sha256(destination_key).hexdigest(),
        "fence_signer_public_key_sha256": hashlib.sha256(fence_key).hexdigest(),
        "local_commit_signer_public_key_sha256": hashlib.sha256(commit_key).hexdigest(),
        "maximum_evidence_age_seconds": value.maximum_evidence_age_seconds,
        "maximum_entries": value.maximum_entries,
    }
    return _ConfigFacts(
        state_root=root,
        binding=binding,
        source_remote_ack_public_key=source_key,
        destination_remote_ack_public_key=destination_key,
        fence_signer_public_key=fence_key,
        local_commit_signer_public_key=commit_key,
        maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
        maximum_entries=value.maximum_entries,
        configuration_sha256=hashlib.sha256(_canonical(payload, code="CONFIG_INVALID")).hexdigest(),
    )


def _binding_from_mapping(value: object, *, code: str) -> _BindingFacts:
    fields = {
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "schema_revision",
        "stream_generation_id",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "baseline_wal_lsn",
        "timeline_id",
        "destination_age_recipient",
        "route_binding_sha256",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
        "target_acknowledged_wal_lsn",
        "blob_object_frontier_wal_lsn",
        "manifest_sha256es",
        "object_versions",
    }
    mapping = _exact_mapping(value, fields=frozenset(fields), code=code)
    manifests_value = mapping["manifest_sha256es"]
    if not isinstance(manifests_value, list):
        _fail(code)
    versions_value = mapping["object_versions"]
    if not isinstance(versions_value, list):
        _fail(code)
    version_pairs: list[tuple[str, str]] = []
    for item in versions_value:
        descriptor = _exact_mapping(
            item,
            fields=frozenset({"object_key", "version_id"}),
            code=code,
        )
        version_pairs.append((descriptor["object_key"], descriptor["version_id"]))
    return _normalise_binding(
        PhysicalStrictRemoteAckWriterResponseBinding(
            source_site=mapping["source_site"],
            destination_site=mapping["destination_site"],
            campaign_id=mapping["campaign_id"],
            release_sha=mapping["release_sha"],
            schema_revision=mapping["schema_revision"],
            stream_generation_id=mapping["stream_generation_id"],
            baseline_generation_id=mapping["baseline_generation_id"],
            baseline_manifest_sha256=mapping["baseline_manifest_sha256"],
            baseline_wal_lsn=mapping["baseline_wal_lsn"],
            timeline_id=mapping["timeline_id"],
            destination_age_recipient=mapping["destination_age_recipient"],
            route_binding_sha256=mapping["route_binding_sha256"],
            writer_epoch=mapping["writer_epoch"],
            writer_lease_id=mapping["writer_lease_id"],
            witness_transition_id=mapping["witness_transition_id"],
            witnessed_term_proof_sha256=mapping["witnessed_term_proof_sha256"],
            target_acknowledged_wal_lsn=mapping["target_acknowledged_wal_lsn"],
            blob_object_frontier_wal_lsn=mapping["blob_object_frontier_wal_lsn"],
            manifest_sha256es=tuple(manifests_value),
            object_versions=tuple(version_pairs),
        ),
        code=code,
    )


def _fresh(
    value: datetime,
    *,
    now: datetime,
    maximum_age_seconds: int,
    code: str,
) -> datetime:
    observed = _utc(value, code=code)
    if observed > now + timedelta(seconds=MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_FUTURE_SKEW_SECONDS):
        _fail(code)
    if observed < now - timedelta(seconds=maximum_age_seconds):
        _fail(code)
    return observed


def _verify_signature(
    *,
    signer_public_key: bytes,
    domain: bytes,
    unsigned: dict[str, Any],
    signature_base64: object,
    code: str,
) -> None:
    signature = _signature(signature_base64, code=code)
    try:
        Ed25519PublicKey.from_public_bytes(signer_public_key).verify(
            signature,
            domain + _canonical(unsigned, code=code),
        )
    except (InvalidSignature, ValueError):
        _fail(code)


def _fence_facts(
    value: object,
    *,
    binding: _BindingFacts,
    signer_public_key: bytes,
    maximum_evidence_age_seconds: int,
    now: datetime,
) -> _FenceFacts:
    mapping, raw = _parse_canonical_mapping(
        value,
        maximum_bytes=MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_FENCE_BYTES,
        code="FENCE_RECEIPT_INVALID",
    )
    item = _exact_mapping(mapping, fields=_FENCE_FIELDS, code="FENCE_RECEIPT_INVALID")
    if (
        item["schema"] != PHYSICAL_STRICT_REMOTE_ACK_WRITER_FENCE_RECEIPT_SCHEMA
        or item["version"] != 1
        or item["kind"] != "active-source-fence"
    ):
        _fail("FENCE_RECEIPT_INVALID")
    bound = _binding_from_mapping(item["binding"], code="FENCE_RECEIPT_INVALID")
    if bound.binding != binding.binding:
        _fail("FENCE_RECEIPT_BINDING_MISMATCH")
    fence_id = _identifier(item["fence_id"], code="FENCE_RECEIPT_INVALID")
    issued_at = _parse_timestamp(item["issued_at"], code="FENCE_RECEIPT_INVALID")
    expires_at = _parse_timestamp(item["expires_at"], code="FENCE_RECEIPT_INVALID")
    if expires_at <= issued_at or expires_at - issued_at > timedelta(
        seconds=MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_EVIDENCE_AGE_SECONDS
    ):
        _fail("FENCE_RECEIPT_INVALID")
    _fresh(
        issued_at,
        now=now,
        maximum_age_seconds=maximum_evidence_age_seconds,
        code="FENCE_RECEIPT_STALE_OR_FUTURE",
    )
    if expires_at <= now:
        _fail("FENCE_RECEIPT_EXPIRED")
    unsigned = dict(item)
    signature = unsigned.pop("signature_base64")
    _verify_signature(
        signer_public_key=signer_public_key,
        domain=_FENCE_DOMAIN,
        unsigned=unsigned,
        signature_base64=signature,
        code="FENCE_RECEIPT_SIGNATURE_INVALID",
    )
    return _FenceFacts(
        canonical_receipt=raw,
        fence_id=fence_id,
        issued_at=issued_at,
        expires_at=expires_at,
        receipt_sha256=hashlib.sha256(raw).hexdigest(),
    )


def verify_physical_strict_remote_ack_writer_fence(
    value: object,
    *,
    config: PhysicalStrictRemoteAckWriterResponseConfig,
    now: datetime,
) -> VerifiedPhysicalStrictRemoteAckWriterFence:
    """Verify an active signed FI writer-fence receipt without fencing FI."""

    normalized = _normalise_config(config)
    observed_now = _utc(now, code="FENCE_RECEIPT_CLOCK_INVALID")
    facts = _fence_facts(
        value,
        binding=normalized.binding,
        signer_public_key=normalized.fence_signer_public_key,
        maximum_evidence_age_seconds=normalized.maximum_evidence_age_seconds,
        now=observed_now,
    )
    result = VerifiedPhysicalStrictRemoteAckWriterFence(
        canonical_receipt=facts.canonical_receipt,
        fence_signer_public_key=normalized.fence_signer_public_key,
        binding=normalized.binding.binding,
        fence_id=facts.fence_id,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        receipt_sha256=facts.receipt_sha256,
    )
    object.__setattr__(result, "_capability", _VERIFIED_FENCE_CAPABILITY)
    return result


def require_verified_physical_strict_remote_ack_writer_fence(
    value: object,
    *,
    config: PhysicalStrictRemoteAckWriterResponseConfig,
    now: datetime,
) -> VerifiedPhysicalStrictRemoteAckWriterFence:
    """Revalidate an opaque active source-fence observation."""

    if (
        type(value) is not VerifiedPhysicalStrictRemoteAckWriterFence
        or value._capability is not _VERIFIED_FENCE_CAPABILITY
    ):
        _fail("VERIFIED_FENCE_REQUIRED")
    normalized = _normalise_config(config)
    facts = _fence_facts(
        value.canonical_receipt,
        binding=normalized.binding,
        signer_public_key=normalized.fence_signer_public_key,
        maximum_evidence_age_seconds=normalized.maximum_evidence_age_seconds,
        now=_utc(now, code="FENCE_RECEIPT_CLOCK_INVALID"),
    )
    if (
        value.fence_signer_public_key != normalized.fence_signer_public_key
        or value.binding != normalized.binding.binding
        or value.fence_id != facts.fence_id
        or value.issued_at != facts.issued_at
        or value.expires_at != facts.expires_at
        or value.receipt_sha256 != facts.receipt_sha256
    ):
        _fail("VERIFIED_FENCE_TAMPERED")
    return value


def _ledger_path(config: _ConfigFacts) -> Path:
    return config.state_root / "strict-remote-ack-writer-response-ledger.json"


def _ledger_lock_path(config: _ConfigFacts) -> Path:
    return config.state_root / "strict-remote-ack-writer-response-ledger.lock"


def _safe_lock(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("LEDGER_PLATFORM_NO_NOFOLLOW")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        _fail("LEDGER_LOCK_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            _fail("LEDGER_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def _locked_ledger(config: _ConfigFacts) -> Iterator[None]:
    descriptor = _safe_lock(_ledger_lock_path(config))
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _read_ledger(config: _ConfigFacts) -> list[dict[str, Any]]:
    path = _ledger_path(config)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("LEDGER_PLATFORM_NO_NOFOLLOW")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return []
    except OSError:
        _fail("LEDGER_STATE_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or not 1 <= metadata.st_size <= MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_LEDGER_BYTES
        ):
            _fail("LEDGER_STATE_UNSAFE")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            try:
                chunk = os.read(descriptor, remaining)
            except OSError:
                _fail("LEDGER_STATE_READ_FAILED")
            if not chunk:
                _fail("LEDGER_STATE_READ_FAILED")
            chunks.append(chunk)
            remaining -= len(chunk)
        try:
            if os.read(descriptor, 1):
                _fail("LEDGER_STATE_READ_FAILED")
        except OSError:
            _fail("LEDGER_STATE_READ_FAILED")
        after = os.fstat(descriptor)
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or after.st_mode != metadata.st_mode
            or after.st_uid != metadata.st_uid
            or after.st_nlink != metadata.st_nlink
        ):
            _fail("LEDGER_STATE_CHANGED_DURING_READ")
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalStrictRemoteAckWriterResponseError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("LEDGER_STATE_INVALID")
    if not isinstance(parsed, dict) or _canonical(parsed, code="LEDGER_STATE_INVALID") != raw:
        _fail("LEDGER_STATE_INVALID")
    ledger = _exact_mapping(parsed, fields=_LEDGER_FIELDS, code="LEDGER_STATE_INVALID")
    if (
        ledger["schema"] != PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_LEDGER_SCHEMA
        or ledger["version"] != 1
        or ledger["configuration_sha256"] != config.configuration_sha256
        or not isinstance(ledger["entries"], list)
        or len(ledger["entries"]) > config.maximum_entries
    ):
        _fail("LEDGER_STATE_CONFIGURATION_MISMATCH")
    entries: list[dict[str, Any]] = []
    seen_remote: set[tuple[str, str, str, str]] = set()
    seen_commit: set[str] = set()
    for raw_entry in ledger["entries"]:
        entry = _exact_mapping(raw_entry, fields=_LEDGER_ENTRY_FIELDS, code="LEDGER_ENTRY_INVALID")
        normalized = {
            "source_request_sha256": _sha256(entry["source_request_sha256"], code="LEDGER_ENTRY_INVALID"),
            "destination_receipt_sha256": _sha256(entry["destination_receipt_sha256"], code="LEDGER_ENTRY_INVALID"),
            "receipt_id": _identifier(entry["receipt_id"], code="LEDGER_ENTRY_INVALID"),
            "receipt_nonce": _nonce(entry["receipt_nonce"], code="LEDGER_ENTRY_INVALID"),
            "commit_receipt_sha256": _sha256(entry["commit_receipt_sha256"], code="LEDGER_ENTRY_INVALID"),
            "commit_id": _identifier(entry["commit_id"], code="LEDGER_ENTRY_INVALID"),
            "local_commit_record_id": _identifier(entry["local_commit_record_id"], code="LEDGER_ENTRY_INVALID"),
            "local_response_id": _identifier(entry["local_response_id"], code="LEDGER_ENTRY_INVALID"),
            "committed_at": _render_timestamp(
                _parse_timestamp(entry["committed_at"], code="LEDGER_ENTRY_INVALID")
            ),
            "permit_binding_sha256": _sha256(entry["permit_binding_sha256"], code="LEDGER_ENTRY_INVALID"),
        }
        remote_identity = (
            normalized["source_request_sha256"],
            normalized["destination_receipt_sha256"],
            normalized["receipt_id"],
            normalized["receipt_nonce"],
        )
        if remote_identity in seen_remote or normalized["commit_id"] in seen_commit:
            _fail("LEDGER_ENTRY_REPLAYED")
        seen_remote.add(remote_identity)
        seen_commit.add(normalized["commit_id"])
        entries.append(normalized)
    return entries


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        _fail("LEDGER_PLATFORM_NO_DIRECTORY_FSYNC")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail("LEDGER_DIRECTORY_FSYNC_FAILED")
    try:
        os.fsync(descriptor)
    except OSError:
        _fail("LEDGER_DIRECTORY_FSYNC_FAILED")
    finally:
        os.close(descriptor)


def _write_ledger(config: _ConfigFacts, entries: list[dict[str, Any]]) -> None:
    if len(entries) > config.maximum_entries:
        _fail("LEDGER_MAXIMUM_ENTRIES_EXCEEDED")
    payload = {
        "schema": PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_LEDGER_SCHEMA,
        "version": 1,
        "configuration_sha256": config.configuration_sha256,
        "entries": entries,
    }
    raw = _canonical(payload, code="LEDGER_STATE_WRITE_INVALID")
    if len(raw) > MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_LEDGER_BYTES:
        _fail("LEDGER_STATE_WRITE_INVALID")
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("LEDGER_PLATFORM_NO_NOFOLLOW")
    temporary = config.state_root / (
        ".strict-remote-ack-writer-response-" + secrets.token_hex(16) + ".tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            _fail("LEDGER_TEMPORARY_UNSAFE")
        offset = 0
        while offset < len(raw):
            try:
                written = os.write(descriptor, raw[offset:])
            except OSError:
                _fail("LEDGER_STATE_WRITE_FAILED")
            if written <= 0:
                _fail("LEDGER_STATE_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, _ledger_path(config))
        _fsync_directory(config.state_root)
    except PhysicalStrictRemoteAckWriterResponseError:
        raise
    except OSError:
        _fail("LEDGER_STATE_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        except OSError:
            # A failed cleanup must not hide a successfully committed ledger.
            pass


def _remote_consumed(
    entries: Sequence[Mapping[str, Any]],
    *,
    source_request_sha256: str,
    destination_receipt_sha256: str,
    receipt_id: str,
    receipt_nonce: str,
) -> bool:
    return any(
        entry["source_request_sha256"] == source_request_sha256
        or entry["destination_receipt_sha256"] == destination_receipt_sha256
        or entry["receipt_id"] == receipt_id
        or entry["receipt_nonce"] == receipt_nonce
        for entry in entries
    )


def _current_witness_term(
    value: object,
    *,
    config: _ConfigFacts,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(value, now=now)
    except (ObjectDeltaRoleMatrixRolloverError, AttributeError, TypeError) as exc:
        raise PhysicalStrictRemoteAckWriterResponseError("CURRENT_WITNESS_TERM_INVALID") from exc
    expected = config.binding.binding
    if (
        term.holder_site != expected.source_site
        or type(term.writer_epoch) is not int
        or term.writer_epoch != expected.writer_epoch
        or term.writer_lease_id != expected.writer_lease_id
        or term.witness_transition_id != expected.witness_transition_id
        or term.proof_sha256 != expected.witnessed_term_proof_sha256
    ):
        _fail("CURRENT_WITNESS_TERM_BINDING_MISMATCH")
    return term


def _remote_ack_facts(
    value: object,
    *,
    config: _ConfigFacts,
    now: datetime,
) -> tuple[VerifiedPhysicalWalRemoteAckEvidence, object, str, str]:
    try:
        evidence = require_verified_physical_wal_remote_ack_evidence(value, now=now)
        remote = evidence.binding
        if type(remote) is not PhysicalWalRemoteAckBinding:
            _fail("REMOTE_ACK_EVIDENCE_INVALID")
        expected = config.binding.binding
        object_versions = tuple((item.object_key, item.version_id) for item in remote.object_versions)
        if (
            remote.source_site != expected.source_site
            or remote.destination_site != expected.destination_site
            or remote.destination_age_recipient != expected.destination_age_recipient
            or remote.campaign_id != expected.campaign_id
            or remote.release_sha != expected.release_sha
            or remote.stream_generation_id != expected.stream_generation_id
            or remote.baseline_generation_id != expected.baseline_generation_id
            or remote.baseline_manifest_sha256 != expected.baseline_manifest_sha256
            or remote.writer_term.writer_holder_site != expected.source_site
            or type(remote.writer_term.writer_epoch) is not int
            or remote.writer_term.writer_epoch != expected.writer_epoch
            or remote.writer_term.writer_lease_id != expected.writer_lease_id
            or remote.writer_term.witnessed_term_proof_sha256
            != expected.witnessed_term_proof_sha256
            or remote.target_acknowledged_wal_lsn != expected.target_acknowledged_wal_lsn
            or remote.blob_object_frontier_wal_lsn != expected.blob_object_frontier_wal_lsn
            or remote.objects_complete is not True
            or remote.manifest_sha256es != expected.manifest_sha256es
            or object_versions != config.binding.object_versions
            or evidence.source_public_key != config.source_remote_ack_public_key
            or evidence.destination_public_key != config.destination_remote_ack_public_key
        ):
            _fail("REMOTE_ACK_EVIDENCE_BINDING_MISMATCH")
        request = verify_physical_wal_remote_ack_request(
            source_request=evidence.source_request,
            expected_binding=remote,
            expected_source_public_key=config.source_remote_ack_public_key,
            now=now,
        )
    except (PhysicalWalRemoteAckError, AttributeError, TypeError) as exc:
        raise PhysicalStrictRemoteAckWriterResponseError("REMOTE_ACK_EVIDENCE_INVALID") from exc
    return (
        evidence,
        request,
        hashlib.sha256(evidence.source_request).hexdigest(),
        hashlib.sha256(evidence.destination_receipt).hexdigest(),
    )


def _receiver_recovery_facts(
    value: object,
    *,
    request: object,
    config: _ConfigFacts,
    now: datetime,
) -> VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence:
    try:
        recovery = require_verified_physical_wal_remote_ack_receiver_recovery_evidence(
            value,
            source_request=request,
            now=now,
        )
        record = recovery.evidence
        replay, replay_value = _lsn(record.replay_lsn, code="RECEIVER_RECOVERY_INVALID")
        expected = config.binding.binding
        if (
            record.source_site != expected.source_site
            or record.destination_site != expected.destination_site
            or record.receiver_site != expected.destination_site
            or record.manifest_sha256es != expected.manifest_sha256es
            or tuple((item.object_key, item.version_id) for item in record.object_versions)
            != config.binding.object_versions
            or replay != record.replay_lsn
            or replay_value < config.binding.target_acknowledged_wal_lsn_value
            or record.in_recovery is not True
            or record.role != "standby"
        ):
            _fail("RECEIVER_RECOVERY_BINDING_MISMATCH")
    except (
        PhysicalWalRemoteAckReceiverLedgerError,
        PhysicalWalRemoteAckError,
        AttributeError,
        TypeError,
    ) as exc:
        raise PhysicalStrictRemoteAckWriterResponseError("RECEIVER_RECOVERY_INVALID") from exc
    return recovery


def _durable_ledger_facts(
    value: object,
    *,
    evidence: VerifiedPhysicalWalRemoteAckEvidence,
    source_request_sha256: str,
    destination_receipt_sha256: str,
    recovery: VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence,
    binding: _BindingFacts,
) -> PhysicalWalRemoteAckReceiverLedgerResult:
    if type(value) is not PhysicalWalRemoteAckReceiverLedgerResult:
        _fail("DURABLE_REMOTE_ACK_LEDGER_INVALID")
    try:
        replay, replay_value = _lsn(value.receiver_replay_lsn, code="DURABLE_REMOTE_ACK_LEDGER_INVALID")
        record = recovery.evidence
        if (
            value.destination_receipt != evidence.destination_receipt
            or value.destination_receipt_sha256 != destination_receipt_sha256
            or value.source_request_sha256 != source_request_sha256
            or value.receipt_id != evidence.receipt_id
            or value.receipt_nonce != evidence.receipt_nonce
            or _utc(value.acknowledged_at, code="DURABLE_REMOTE_ACK_LEDGER_INVALID")
            != evidence.acknowledged_at
            or value.receiver_recovery_evidence_sha256
            != record.receiver_recovery_evidence_sha256
            or replay != record.replay_lsn
            or replay_value < binding.target_acknowledged_wal_lsn_value
            or not isinstance(value.ledger_path, Path)
            or not value.ledger_path.is_absolute()
            or type(value.idempotent) is not bool
        ):
            _fail("DURABLE_REMOTE_ACK_LEDGER_BINDING_MISMATCH")
    except (AttributeError, PhysicalStrictRemoteAckWriterResponseError):
        raise
    return value


def _permit_binding_sha256(
    *,
    config: _ConfigFacts,
    source_request_sha256: str,
    destination_receipt_sha256: str,
    request_id: str,
    request_nonce: str,
    receipt_id: str,
    receipt_nonce: str,
    receiver_recovery_evidence_sha256: str,
    receiver_replay_lsn: str,
    fence_receipt_sha256: str,
) -> str:
    return _permit_binding_sha256_for(
        configuration_sha256=config.configuration_sha256,
        binding=config.binding,
        source_request_sha256=source_request_sha256,
        destination_receipt_sha256=destination_receipt_sha256,
        request_id=request_id,
        request_nonce=request_nonce,
        receipt_id=receipt_id,
        receipt_nonce=receipt_nonce,
        receiver_recovery_evidence_sha256=receiver_recovery_evidence_sha256,
        receiver_replay_lsn=receiver_replay_lsn,
        fence_receipt_sha256=fence_receipt_sha256,
    )


def _permit_binding_sha256_for(
    *,
    configuration_sha256: str,
    binding: _BindingFacts,
    source_request_sha256: str,
    destination_receipt_sha256: str,
    request_id: str,
    request_nonce: str,
    receipt_id: str,
    receipt_nonce: str,
    receiver_recovery_evidence_sha256: str,
    receiver_replay_lsn: str,
    fence_receipt_sha256: str,
) -> str:
    payload = {
        "configuration_sha256": configuration_sha256,
        "binding": _binding_payload(binding),
        "source_request_sha256": source_request_sha256,
        "destination_receipt_sha256": destination_receipt_sha256,
        "request_id": request_id,
        "request_nonce": request_nonce,
        "receipt_id": receipt_id,
        "receipt_nonce": receipt_nonce,
        "receiver_recovery_evidence_sha256": receiver_recovery_evidence_sha256,
        "receiver_replay_lsn": receiver_replay_lsn,
        "fence_receipt_sha256": fence_receipt_sha256,
    }
    return hashlib.sha256(_canonical(payload, code="PERMIT_BINDING_INVALID")).hexdigest()


def _issue_permit(
    *,
    config: _ConfigFacts,
    witnessed_term: object,
    remote_ack_evidence: object,
    receiver_recovery_evidence: object,
    durable_ledger_result: object,
    fence: object,
    now: datetime,
    allow_consumed: bool = False,
    ledger_entries: Sequence[Mapping[str, Any]] | None = None,
) -> VerifiedPhysicalStrictRemoteAckWriterCommitPermit:
    term = _current_witness_term(witnessed_term, config=config, now=now)
    remote, request, source_request_sha256, destination_receipt_sha256 = _remote_ack_facts(
        remote_ack_evidence,
        config=config,
        now=now,
    )
    recovery = _receiver_recovery_facts(
        receiver_recovery_evidence,
        request=request,
        config=config,
        now=now,
    )
    ledger = _durable_ledger_facts(
        durable_ledger_result,
        evidence=remote,
        source_request_sha256=source_request_sha256,
        destination_receipt_sha256=destination_receipt_sha256,
        recovery=recovery,
        binding=config.binding,
    )
    verified_fence = require_verified_physical_strict_remote_ack_writer_fence(
        fence,
        config=_config_from_facts(config),
        now=now,
    )
    if verified_fence.expires_at <= now:
        _fail("WRITER_FENCE_EXPIRED")
    # The commit path already holds this lock for its final revalidation.
    # Taking a second independently-opened flock there would risk blocking on
    # the process's own lock.  The caller supplies the just-read entries only
    # while it holds that exact root-owned lock.
    if ledger_entries is None:
        with _locked_ledger(config):
            entries = _read_ledger(config)
    else:
        entries = list(ledger_entries)
    if not allow_consumed and _remote_consumed(
        entries,
        source_request_sha256=source_request_sha256,
        destination_receipt_sha256=destination_receipt_sha256,
        receipt_id=remote.receipt_id,
        receipt_nonce=remote.receipt_nonce,
    ):
        _fail("REMOTE_ACK_ALREADY_CONSUMED")
    permit_binding_sha256 = _permit_binding_sha256(
        config=config,
        source_request_sha256=source_request_sha256,
        destination_receipt_sha256=destination_receipt_sha256,
        request_id=remote.request_id,
        request_nonce=remote.request_nonce,
        receipt_id=remote.receipt_id,
        receipt_nonce=remote.receipt_nonce,
        receiver_recovery_evidence_sha256=recovery.evidence.receiver_recovery_evidence_sha256,
        receiver_replay_lsn=recovery.evidence.replay_lsn,
        fence_receipt_sha256=verified_fence.receipt_sha256,
    )
    permit = VerifiedPhysicalStrictRemoteAckWriterCommitPermit(
        configuration_sha256=config.configuration_sha256,
        binding=config.binding.binding,
        witnessed_term=term,
        remote_ack_evidence=remote,
        receiver_recovery_evidence=recovery,
        durable_ledger_result=ledger,
        fence=verified_fence,
        source_request_sha256=source_request_sha256,
        destination_receipt_sha256=destination_receipt_sha256,
        permit_binding_sha256=permit_binding_sha256,
        issued_at=now,
    )
    object.__setattr__(permit, "_capability", _VERIFIED_PERMIT_CAPABILITY)
    return permit


def _config_from_facts(facts: _ConfigFacts) -> PhysicalStrictRemoteAckWriterResponseConfig:
    """Recreate an exact config for nested opaque-capability revalidation."""

    return PhysicalStrictRemoteAckWriterResponseConfig(
        state_root=facts.state_root,
        expected_binding=facts.binding.binding,
        expected_source_remote_ack_public_key=facts.source_remote_ack_public_key,
        expected_destination_remote_ack_public_key=facts.destination_remote_ack_public_key,
        fence_signer_public_key=facts.fence_signer_public_key,
        local_commit_signer_public_key=facts.local_commit_signer_public_key,
        enabled=True,
        maximum_evidence_age_seconds=facts.maximum_evidence_age_seconds,
        maximum_entries=facts.maximum_entries,
    )


def issue_physical_strict_remote_ack_writer_commit_permit(
    *,
    config: PhysicalStrictRemoteAckWriterResponseConfig,
    witnessed_term: object,
    remote_ack_evidence: object,
    receiver_recovery_evidence: object,
    durable_ledger_result: object,
    fence: object,
    now: datetime,
) -> VerifiedPhysicalStrictRemoteAckWriterCommitPermit:
    """Issue no permit unless exact live term, ack, recovery, ledger, fence match."""

    normalized = _normalise_config(config)
    return _issue_permit(
        config=normalized,
        witnessed_term=witnessed_term,
        remote_ack_evidence=remote_ack_evidence,
        receiver_recovery_evidence=receiver_recovery_evidence,
        durable_ledger_result=durable_ledger_result,
        fence=fence,
        now=_utc(now, code="COMMIT_PERMIT_CLOCK_INVALID"),
    )


def require_verified_physical_strict_remote_ack_writer_commit_permit(
    value: object,
    *,
    config: PhysicalStrictRemoteAckWriterResponseConfig,
    now: datetime,
) -> VerifiedPhysicalStrictRemoteAckWriterCommitPermit:
    """Revalidate a permit and reject a changed term/ack/fence before commit."""

    return _require_verified_physical_strict_remote_ack_writer_commit_permit(
        value,
        config=config,
        now=now,
        allow_consumed=False,
    )


def _require_verified_physical_strict_remote_ack_writer_commit_permit(
    value: object,
    *,
    config: PhysicalStrictRemoteAckWriterResponseConfig,
    now: datetime,
    allow_consumed: bool,
    ledger_entries: Sequence[Mapping[str, Any]] | None = None,
) -> VerifiedPhysicalStrictRemoteAckWriterCommitPermit:

    if (
        type(value) is not VerifiedPhysicalStrictRemoteAckWriterCommitPermit
        or value._capability is not _VERIFIED_PERMIT_CAPABILITY
    ):
        _fail("VERIFIED_COMMIT_PERMIT_REQUIRED")
    normalized = _normalise_config(config)
    fresh = _issue_permit(
        config=normalized,
        witnessed_term=value.witnessed_term,
        remote_ack_evidence=value.remote_ack_evidence,
        receiver_recovery_evidence=value.receiver_recovery_evidence,
        durable_ledger_result=value.durable_ledger_result,
        fence=value.fence,
        now=_utc(now, code="COMMIT_PERMIT_CLOCK_INVALID"),
        allow_consumed=allow_consumed,
        ledger_entries=ledger_entries,
    )
    fields = (
        "configuration_sha256",
        "binding",
        "witnessed_term",
        "remote_ack_evidence",
        "receiver_recovery_evidence",
        "durable_ledger_result",
        "fence",
        "source_request_sha256",
        "destination_receipt_sha256",
        "permit_binding_sha256",
    )
    if any(getattr(fresh, field_name) != getattr(value, field_name) for field_name in fields):
        _fail("VERIFIED_COMMIT_PERMIT_TAMPERED_OR_DIVERGED")
    if value.issued_at > _utc(now, code="COMMIT_PERMIT_CLOCK_INVALID"):
        _fail("VERIFIED_COMMIT_PERMIT_TAMPERED_OR_DIVERGED")
    return value


def _new_commit_id() -> str:
    while True:
        candidate = "strict-remote-ack-commit-" + secrets.token_urlsafe(24)
        if _IDENTIFIER_RE.fullmatch(candidate) is not None:
            return candidate


def _commit_instruction(
    permit: VerifiedPhysicalStrictRemoteAckWriterCommitPermit,
    *,
    now: datetime,
) -> PhysicalStrictRemoteAckWriterCommitInstruction:
    remote = permit.remote_ack_evidence
    recovery = permit.receiver_recovery_evidence.evidence
    return PhysicalStrictRemoteAckWriterCommitInstruction(
        schema=PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
        configuration_sha256=permit.configuration_sha256,
        permit_binding_sha256=permit.permit_binding_sha256,
        commit_id=_new_commit_id(),
        source_request_sha256=permit.source_request_sha256,
        destination_receipt_sha256=permit.destination_receipt_sha256,
        request_id=remote.request_id,
        request_nonce=remote.request_nonce,
        receipt_id=remote.receipt_id,
        receipt_nonce=remote.receipt_nonce,
        receiver_recovery_evidence_sha256=recovery.receiver_recovery_evidence_sha256,
        receiver_replay_lsn=recovery.replay_lsn,
        binding=permit.binding,
        issued_at=now,
    )


def _commit_facts(
    value: object,
    *,
    instruction: PhysicalStrictRemoteAckWriterCommitInstruction,
    binding: _BindingFacts,
    local_commit_signer_public_key: bytes,
    maximum_evidence_age_seconds: int,
    now: datetime,
) -> _CommitFacts:
    mapping, raw = _parse_canonical_mapping(
        value,
        maximum_bytes=MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_RECEIPT_BYTES,
        code="DURABLE_COMMIT_RECEIPT_INVALID",
    )
    item = _exact_mapping(mapping, fields=_COMMIT_FIELDS, code="DURABLE_COMMIT_RECEIPT_INVALID")
    if (
        item["schema"] != PHYSICAL_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_SCHEMA
        or item["version"] != 1
        or item["kind"] != "durable-local-writer-response"
        or item["atomic_commit_boundary"] != STRICT_REMOTE_ACK_WRITER_ATOMIC_COMMIT_BOUNDARY
    ):
        _fail("DURABLE_COMMIT_RECEIPT_INVALID")
    bound = _binding_from_mapping(item["binding"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    if bound.binding != binding.binding or bound.binding != instruction.binding:
        _fail("DURABLE_COMMIT_RECEIPT_BINDING_MISMATCH")
    exact_scalars = {
        "configuration_sha256": instruction.configuration_sha256,
        "permit_binding_sha256": instruction.permit_binding_sha256,
        "commit_id": instruction.commit_id,
        "source_request_sha256": instruction.source_request_sha256,
        "destination_receipt_sha256": instruction.destination_receipt_sha256,
        "request_id": instruction.request_id,
        "request_nonce": instruction.request_nonce,
        "receipt_id": instruction.receipt_id,
        "receipt_nonce": instruction.receipt_nonce,
        "receiver_recovery_evidence_sha256": instruction.receiver_recovery_evidence_sha256,
        "receiver_replay_lsn": instruction.receiver_replay_lsn,
    }
    for key, expected in exact_scalars.items():
        if item[key] != expected:
            _fail("DURABLE_COMMIT_RECEIPT_BINDING_MISMATCH")
    _sha256(item["configuration_sha256"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _sha256(item["permit_binding_sha256"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _identifier(item["commit_id"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _sha256(item["source_request_sha256"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _sha256(item["destination_receipt_sha256"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _identifier(item["request_id"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _nonce(item["request_nonce"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _identifier(item["receipt_id"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _nonce(item["receipt_nonce"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _sha256(item["receiver_recovery_evidence_sha256"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _lsn(item["receiver_replay_lsn"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    local_commit_record_id = _identifier(item["local_commit_record_id"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    local_response_id = _identifier(item["local_response_id"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    if local_commit_record_id == local_response_id:
        _fail("DURABLE_COMMIT_RECEIPT_IDENTITY_REUSED")
    committed_at = _parse_timestamp(item["committed_at"], code="DURABLE_COMMIT_RECEIPT_INVALID")
    _fresh(
        committed_at,
        now=now,
        maximum_age_seconds=maximum_evidence_age_seconds,
        code="DURABLE_COMMIT_RECEIPT_STALE_OR_FUTURE",
    )
    if committed_at < instruction.issued_at:
        _fail("DURABLE_COMMIT_RECEIPT_PREDATES_PERMIT")
    unsigned = dict(item)
    signature = unsigned.pop("signature_base64")
    _verify_signature(
        signer_public_key=local_commit_signer_public_key,
        domain=_COMMIT_DOMAIN,
        unsigned=unsigned,
        signature_base64=signature,
        code="DURABLE_COMMIT_RECEIPT_SIGNATURE_INVALID",
    )
    return _CommitFacts(
        canonical_receipt=raw,
        commit_receipt_sha256=hashlib.sha256(raw).hexdigest(),
        committed_at=committed_at,
        local_commit_record_id=local_commit_record_id,
        local_response_id=local_response_id,
    )


def _entry_for(
    *,
    permit: VerifiedPhysicalStrictRemoteAckWriterCommitPermit,
    instruction: PhysicalStrictRemoteAckWriterCommitInstruction,
    commit: _CommitFacts,
) -> dict[str, Any]:
    remote = permit.remote_ack_evidence
    return {
        "source_request_sha256": permit.source_request_sha256,
        "destination_receipt_sha256": permit.destination_receipt_sha256,
        "receipt_id": remote.receipt_id,
        "receipt_nonce": remote.receipt_nonce,
        "commit_receipt_sha256": commit.commit_receipt_sha256,
        "commit_id": instruction.commit_id,
        "local_commit_record_id": commit.local_commit_record_id,
        "local_response_id": commit.local_response_id,
        "committed_at": _render_timestamp(commit.committed_at),
        "permit_binding_sha256": permit.permit_binding_sha256,
    }


def commit_physical_strict_remote_ack_writer_response(
    *,
    config: PhysicalStrictRemoteAckWriterResponseConfig,
    permit: object,
    boundary: PhysicalStrictRemoteAckWriterCommitBoundary,
    now: datetime,
) -> VerifiedPhysicalStrictRemoteAckWriterCommitEvidence:
    """Call the injected transaction callback only after all permits recheck.

    A missing/stale/foreign acknowledgement, changed term/fence, consumed
    receipt, or malformed callback cannot reach ``boundary``. The callback
    must atomically record the local response and remote receipt consumption
    before it returns a pinned-key signed durable receipt.
    """

    normalized = _normalise_config(config)
    observed_now = _utc(now, code="COMMIT_CLOCK_INVALID")
    verified_permit = require_verified_physical_strict_remote_ack_writer_commit_permit(
        permit,
        config=config,
        now=observed_now,
    )
    callback = getattr(boundary, "commit_after_verified_remote_ack", None)
    if not callable(callback):
        _fail("COMMIT_BOUNDARY_INVALID")
    with _locked_ledger(normalized):
        # Revalidate again while holding the local replay lock. A change after
        # the first validation cannot race into the callback.
        entries = _read_ledger(normalized)
        verified_permit = _require_verified_physical_strict_remote_ack_writer_commit_permit(
            verified_permit,
            config=config,
            now=observed_now,
            allow_consumed=False,
            ledger_entries=entries,
        )
        remote = verified_permit.remote_ack_evidence
        if _remote_consumed(
            entries,
            source_request_sha256=verified_permit.source_request_sha256,
            destination_receipt_sha256=verified_permit.destination_receipt_sha256,
            receipt_id=remote.receipt_id,
            receipt_nonce=remote.receipt_nonce,
        ):
            _fail("REMOTE_ACK_ALREADY_CONSUMED")
        instruction = _commit_instruction(verified_permit, now=observed_now)
        try:
            raw_receipt = callback(instruction=instruction)
        except Exception as exc:
            raise PhysicalStrictRemoteAckWriterResponseError("COMMIT_BOUNDARY_FAILED") from exc
        if not isinstance(raw_receipt, bytes):
            _fail("COMMIT_BOUNDARY_RECEIPT_BYTES_REQUIRED")
        commit = _commit_facts(
            raw_receipt,
            instruction=instruction,
            binding=normalized.binding,
            local_commit_signer_public_key=normalized.local_commit_signer_public_key,
            maximum_evidence_age_seconds=normalized.maximum_evidence_age_seconds,
            now=observed_now,
        )
        # A callback that returns an old receipt cannot make us overwrite a
        # newly consumed acknowledgement while it was executing.
        if _remote_consumed(
            entries,
            source_request_sha256=verified_permit.source_request_sha256,
            destination_receipt_sha256=verified_permit.destination_receipt_sha256,
            receipt_id=remote.receipt_id,
            receipt_nonce=remote.receipt_nonce,
        ):
            _fail("REMOTE_ACK_ALREADY_CONSUMED")
        _write_ledger(normalized, entries + [_entry_for(
            permit=verified_permit,
            instruction=instruction,
            commit=commit,
        )])
    evidence = VerifiedPhysicalStrictRemoteAckWriterCommitEvidence(
        permit=verified_permit,
        instruction=instruction,
        canonical_commit_receipt=commit.canonical_receipt,
        commit_receipt_sha256=commit.commit_receipt_sha256,
        committed_at=commit.committed_at,
        local_commit_record_id=commit.local_commit_record_id,
        local_response_id=commit.local_response_id,
        local_commit_signer_public_key=normalized.local_commit_signer_public_key,
        maximum_evidence_age_seconds=normalized.maximum_evidence_age_seconds,
    )
    object.__setattr__(evidence, "_capability", _VERIFIED_COMMIT_EVIDENCE_CAPABILITY)
    return evidence


def _matching_ledger_entry(
    entries: Sequence[Mapping[str, Any]],
    *,
    evidence: VerifiedPhysicalStrictRemoteAckWriterCommitEvidence,
) -> bool:
    permit = evidence.permit
    remote = permit.remote_ack_evidence
    return any(
        entry["source_request_sha256"] == permit.source_request_sha256
        and entry["destination_receipt_sha256"] == permit.destination_receipt_sha256
        and entry["receipt_id"] == remote.receipt_id
        and entry["receipt_nonce"] == remote.receipt_nonce
        and entry["commit_receipt_sha256"] == evidence.commit_receipt_sha256
        and entry["commit_id"] == evidence.instruction.commit_id
        and entry["local_commit_record_id"] == evidence.local_commit_record_id
        and entry["local_response_id"] == evidence.local_response_id
        and entry["committed_at"] == _render_timestamp(evidence.committed_at)
        and entry["permit_binding_sha256"] == permit.permit_binding_sha256
        for entry in entries
    )


def require_verified_physical_strict_remote_ack_writer_commit_evidence(
    value: object,
    *,
    config: PhysicalStrictRemoteAckWriterResponseConfig,
    now: datetime,
) -> VerifiedPhysicalStrictRemoteAckWriterCommitEvidence:
    """Revalidate signed local commit evidence and its root-owned mirror ledger."""

    if (
        type(value) is not VerifiedPhysicalStrictRemoteAckWriterCommitEvidence
        or value._capability is not _VERIFIED_COMMIT_EVIDENCE_CAPABILITY
    ):
        _fail("VERIFIED_COMMIT_EVIDENCE_REQUIRED")
    normalized = _normalise_config(config)
    observed_now = _utc(now, code="COMMIT_EVIDENCE_CLOCK_INVALID")
    permit = _require_verified_physical_strict_remote_ack_writer_commit_permit(
        value.permit,
        config=config,
        now=observed_now,
        allow_consumed=True,
    )
    if (
        value.local_commit_signer_public_key != normalized.local_commit_signer_public_key
        or type(value.maximum_evidence_age_seconds) is not int
        or value.maximum_evidence_age_seconds != normalized.maximum_evidence_age_seconds
        or value.permit != permit
        or value.instruction.binding != permit.binding
        or value.instruction.configuration_sha256 != permit.configuration_sha256
        or value.instruction.permit_binding_sha256 != permit.permit_binding_sha256
    ):
        _fail("VERIFIED_COMMIT_EVIDENCE_TAMPERED")
    commit = _commit_facts(
        value.canonical_commit_receipt,
        instruction=value.instruction,
        binding=normalized.binding,
        local_commit_signer_public_key=normalized.local_commit_signer_public_key,
        maximum_evidence_age_seconds=normalized.maximum_evidence_age_seconds,
        now=observed_now,
    )
    if (
        commit.commit_receipt_sha256 != value.commit_receipt_sha256
        or commit.committed_at != value.committed_at
        or commit.local_commit_record_id != value.local_commit_record_id
        or commit.local_response_id != value.local_response_id
    ):
        _fail("VERIFIED_COMMIT_EVIDENCE_TAMPERED")
    with _locked_ledger(normalized):
        if not _matching_ledger_entry(_read_ledger(normalized), evidence=value):
            _fail("DURABLE_COMMIT_LEDGER_ENTRY_MISSING")
    return value


def _observation_sha256(
    evidence: VerifiedPhysicalStrictRemoteAckWriterCommitEvidence,
    *,
    observed_at: datetime,
) -> str:
    permit = evidence.permit
    payload = {
        "schema": PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_OBSERVATION_SCHEMA,
        "configuration_sha256": permit.configuration_sha256,
        "binding": _binding_payload(_normalise_binding(permit.binding, code="OBSERVATION_INVALID")),
        "commit_receipt_sha256": evidence.commit_receipt_sha256,
        "source_request_sha256": permit.source_request_sha256,
        "destination_receipt_sha256": permit.destination_receipt_sha256,
        "local_commit_record_id": evidence.local_commit_record_id,
        "local_response_id": evidence.local_response_id,
        "committed_at": _render_timestamp(evidence.committed_at),
        "observed_at": _render_timestamp(observed_at),
    }
    return hashlib.sha256(_canonical(payload, code="OBSERVATION_INVALID")).hexdigest()


def mint_physical_strict_remote_ack_writer_response_observation(
    value: object,
    *,
    config: PhysicalStrictRemoteAckWriterResponseConfig,
    now: datetime,
) -> VerifiedPhysicalStrictRemoteAckWriterResponseObservation:
    """Mint an oracle input only from verified durable local commit evidence.

    There is deliberately no raw data-class/boolean constructor for this
    observation. A caller must first complete the permit and signed durable
    commit sequence above.
    """

    observed_now = _utc(now, code="OBSERVATION_CLOCK_INVALID")
    evidence = require_verified_physical_strict_remote_ack_writer_commit_evidence(
        value,
        config=config,
        now=observed_now,
    )
    result = VerifiedPhysicalStrictRemoteAckWriterResponseObservation(
        commit_evidence=evidence,
        observation_sha256=_observation_sha256(evidence, observed_at=observed_now),
        observed_at=observed_now,
    )
    object.__setattr__(result, "_capability", _VERIFIED_OBSERVATION_CAPABILITY)
    return result


def require_verified_physical_strict_remote_ack_writer_response_observation(
    value: object,
    *,
    config: PhysicalStrictRemoteAckWriterResponseConfig,
    now: datetime,
) -> VerifiedPhysicalStrictRemoteAckWriterResponseObservation:
    """Revalidate an observation against the full owning config/ledger."""

    if (
        type(value) is not VerifiedPhysicalStrictRemoteAckWriterResponseObservation
        or value._capability is not _VERIFIED_OBSERVATION_CAPABILITY
    ):
        _fail("VERIFIED_WRITER_RESPONSE_OBSERVATION_REQUIRED")
    normalized = _normalise_config(config)
    observed_now = _utc(now, code="OBSERVATION_CLOCK_INVALID")
    _fresh(
        value.observed_at,
        now=observed_now,
        maximum_age_seconds=normalized.maximum_evidence_age_seconds,
        code="WRITER_RESPONSE_OBSERVATION_STALE_OR_FUTURE",
    )
    evidence = require_verified_physical_strict_remote_ack_writer_commit_evidence(
        value.commit_evidence,
        config=config,
        now=observed_now,
    )
    if value.observation_sha256 != _observation_sha256(evidence, observed_at=value.observed_at):
        _fail("VERIFIED_WRITER_RESPONSE_OBSERVATION_TAMPERED")
    return value


def _lightweight_observation_evidence(
    value: VerifiedPhysicalStrictRemoteAckWriterResponseObservation,
    *,
    now: datetime,
) -> VerifiedPhysicalStrictRemoteAckWriterCommitEvidence:
    """Recheck non-I/O evidence for the non-authorizing readiness oracle.

    The owning boundary's root-owned ledger recheck happens when minting or
    requiring the observation. This projection intentionally does not open
    that ledger, preserving the readiness oracle's no-filesystem contract.
    """

    if value._capability is not _VERIFIED_OBSERVATION_CAPABILITY:
        _fail("VERIFIED_WRITER_RESPONSE_OBSERVATION_REQUIRED")
    evidence = value.commit_evidence
    if (
        type(evidence) is not VerifiedPhysicalStrictRemoteAckWriterCommitEvidence
        or evidence._capability is not _VERIFIED_COMMIT_EVIDENCE_CAPABILITY
        or type(evidence.maximum_evidence_age_seconds) is not int
        or not 1
        <= evidence.maximum_evidence_age_seconds
        <= MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_EVIDENCE_AGE_SECONDS
    ):
        _fail("VERIFIED_COMMIT_EVIDENCE_REQUIRED")
    permit = evidence.permit
    if (
        type(permit) is not VerifiedPhysicalStrictRemoteAckWriterCommitPermit
        or permit._capability is not _VERIFIED_PERMIT_CAPABILITY
    ):
        _fail("VERIFIED_COMMIT_PERMIT_REQUIRED")
    facts = _normalise_binding(permit.binding, code="VERIFIED_COMMIT_EVIDENCE_TAMPERED")
    local_commit_key = _public_key(
        evidence.local_commit_signer_public_key,
        code="VERIFIED_COMMIT_EVIDENCE_TAMPERED",
    )
    instruction = evidence.instruction
    if type(instruction) is not PhysicalStrictRemoteAckWriterCommitInstruction:
        _fail("VERIFIED_COMMIT_EVIDENCE_TAMPERED")
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(permit.witnessed_term, now=now)
        remote = require_verified_physical_wal_remote_ack_evidence(permit.remote_ack_evidence, now=now)
        request = verify_physical_wal_remote_ack_request(
            source_request=remote.source_request,
            expected_binding=remote.binding,
            expected_source_public_key=remote.source_public_key,
            now=now,
        )
        recovery = require_verified_physical_wal_remote_ack_receiver_recovery_evidence(
            permit.receiver_recovery_evidence,
            source_request=request,
            now=now,
        )
    except (
        ObjectDeltaRoleMatrixRolloverError,
        PhysicalWalRemoteAckError,
        PhysicalWalRemoteAckReceiverLedgerError,
        AttributeError,
        TypeError,
    ) as exc:
        raise PhysicalStrictRemoteAckWriterResponseError("ORACLE_PROJECTION_EVIDENCE_INVALID") from exc
    recovery_record = recovery.evidence
    remote_objects = tuple(
        (item.object_key, item.version_id) for item in remote.binding.object_versions
    )
    if (
        term.holder_site != facts.binding.source_site
        or type(term.writer_epoch) is not int
        or term.writer_epoch != facts.binding.writer_epoch
        or term.writer_lease_id != facts.binding.writer_lease_id
        or term.witness_transition_id != facts.binding.witness_transition_id
        or term.proof_sha256 != facts.binding.witnessed_term_proof_sha256
        or remote.binding.source_site != facts.binding.source_site
        or remote.binding.destination_site != facts.binding.destination_site
        or remote.binding.destination_age_recipient != facts.binding.destination_age_recipient
        or remote.binding.campaign_id != facts.binding.campaign_id
        or remote.binding.release_sha != facts.binding.release_sha
        or remote.binding.stream_generation_id != facts.binding.stream_generation_id
        or remote.binding.baseline_generation_id != facts.binding.baseline_generation_id
        or remote.binding.baseline_manifest_sha256 != facts.binding.baseline_manifest_sha256
        or remote.binding.writer_term.writer_holder_site != facts.binding.source_site
        or type(remote.binding.writer_term.writer_epoch) is not int
        or remote.binding.writer_term.writer_epoch != facts.binding.writer_epoch
        or remote.binding.writer_term.writer_lease_id != facts.binding.writer_lease_id
        or remote.binding.writer_term.witnessed_term_proof_sha256
        != facts.binding.witnessed_term_proof_sha256
        or remote.binding.target_acknowledged_wal_lsn
        != facts.binding.target_acknowledged_wal_lsn
        or remote.binding.blob_object_frontier_wal_lsn
        != facts.binding.blob_object_frontier_wal_lsn
        or remote.binding.objects_complete is not True
        or remote.binding.manifest_sha256es != facts.binding.manifest_sha256es
        or remote_objects != facts.object_versions
        or recovery_record.source_site != facts.binding.source_site
        or recovery_record.destination_site != facts.binding.destination_site
        or recovery_record.receiver_site != facts.binding.destination_site
        or recovery_record.manifest_sha256es != facts.binding.manifest_sha256es
        or tuple((item.object_key, item.version_id) for item in recovery_record.object_versions)
        != facts.object_versions
        or recovery_record.in_recovery is not True
        or recovery_record.role != "standby"
        or recovery_record.receiver_recovery_evidence_sha256
        != permit.receiver_recovery_evidence.evidence.receiver_recovery_evidence_sha256
    ):
        _fail("ORACLE_PROJECTION_EVIDENCE_DIVERGED")
    source_request_sha256 = hashlib.sha256(remote.source_request).hexdigest()
    destination_receipt_sha256 = hashlib.sha256(remote.destination_receipt).hexdigest()
    if (
        permit.source_request_sha256 != source_request_sha256
        or permit.destination_receipt_sha256 != destination_receipt_sha256
    ):
        _fail("ORACLE_PROJECTION_EVIDENCE_DIVERGED")
    _durable_ledger_facts(
        permit.durable_ledger_result,
        evidence=remote,
        source_request_sha256=source_request_sha256,
        destination_receipt_sha256=destination_receipt_sha256,
        recovery=recovery,
        binding=facts,
    )
    fence = permit.fence
    if (
        type(fence) is not VerifiedPhysicalStrictRemoteAckWriterFence
        or fence._capability is not _VERIFIED_FENCE_CAPABILITY
        or fence.binding != facts.binding
    ):
        _fail("ORACLE_PROJECTION_FENCE_INVALID")
    fence_key = _public_key(fence.fence_signer_public_key, code="ORACLE_PROJECTION_FENCE_INVALID")
    fence_facts = _fence_facts(
        fence.canonical_receipt,
        binding=facts,
        signer_public_key=fence_key,
        maximum_evidence_age_seconds=evidence.maximum_evidence_age_seconds,
        now=now,
    )
    if (
        fence.fence_id != fence_facts.fence_id
        or fence.issued_at != fence_facts.issued_at
        or fence.expires_at != fence_facts.expires_at
        or fence.receipt_sha256 != fence_facts.receipt_sha256
        or fence.expires_at <= now
    ):
        _fail("ORACLE_PROJECTION_FENCE_INVALID")
    expected_permit_binding = _permit_binding_sha256_for(
        configuration_sha256=_sha256(
            permit.configuration_sha256,
            code="ORACLE_PROJECTION_EVIDENCE_DIVERGED",
        ),
        binding=facts,
        source_request_sha256=source_request_sha256,
        destination_receipt_sha256=destination_receipt_sha256,
        request_id=remote.request_id,
        request_nonce=remote.request_nonce,
        receipt_id=remote.receipt_id,
        receipt_nonce=remote.receipt_nonce,
        receiver_recovery_evidence_sha256=recovery_record.receiver_recovery_evidence_sha256,
        receiver_replay_lsn=recovery_record.replay_lsn,
        fence_receipt_sha256=fence_facts.receipt_sha256,
    )
    if permit.permit_binding_sha256 != expected_permit_binding:
        _fail("ORACLE_PROJECTION_EVIDENCE_DIVERGED")
    if (
        instruction.schema != PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA
        or instruction.configuration_sha256 != permit.configuration_sha256
        or instruction.permit_binding_sha256 != permit.permit_binding_sha256
        or instruction.source_request_sha256 != permit.source_request_sha256
        or instruction.destination_receipt_sha256 != permit.destination_receipt_sha256
        or instruction.request_id != remote.request_id
        or instruction.request_nonce != remote.request_nonce
        or instruction.receipt_id != remote.receipt_id
        or instruction.receipt_nonce != remote.receipt_nonce
        or instruction.receiver_recovery_evidence_sha256
        != recovery_record.receiver_recovery_evidence_sha256
        or instruction.receiver_replay_lsn != recovery_record.replay_lsn
        or instruction.binding != facts.binding
        or _utc(instruction.issued_at, code="VERIFIED_COMMIT_EVIDENCE_TAMPERED")
        > now + timedelta(seconds=MAX_STRICT_REMOTE_ACK_WRITER_RESPONSE_FUTURE_SKEW_SECONDS)
    ):
        _fail("ORACLE_PROJECTION_EVIDENCE_DIVERGED")
    commit = _commit_facts(
        evidence.canonical_commit_receipt,
        instruction=evidence.instruction,
        binding=facts,
        local_commit_signer_public_key=local_commit_key,
        maximum_evidence_age_seconds=evidence.maximum_evidence_age_seconds,
        now=now,
    )
    if (
        commit.commit_receipt_sha256 != evidence.commit_receipt_sha256
        or commit.committed_at != evidence.committed_at
        or commit.local_commit_record_id != evidence.local_commit_record_id
        or commit.local_response_id != evidence.local_response_id
        or value.observation_sha256
        != _observation_sha256(evidence, observed_at=value.observed_at)
    ):
        _fail("ORACLE_PROJECTION_EVIDENCE_TAMPERED")
    _fresh(
        value.observed_at,
        now=now,
        maximum_age_seconds=evidence.maximum_evidence_age_seconds,
        code="WRITER_RESPONSE_OBSERVATION_STALE_OR_FUTURE",
    )
    return evidence


def project_verified_physical_strict_remote_ack_writer_response_observation(
    value: object,
    *,
    now: datetime,
) -> StrictRemoteAckWriterResponseOracleProjection:
    """Project verified strict evidence for a no-I/O Full-Matrix oracle."""

    if type(value) is not VerifiedPhysicalStrictRemoteAckWriterResponseObservation:
        _fail("VERIFIED_WRITER_RESPONSE_OBSERVATION_REQUIRED")
    evidence = _lightweight_observation_evidence(value, now=_utc(now, code="OBSERVATION_CLOCK_INVALID"))
    binding = evidence.permit.binding
    return StrictRemoteAckWriterResponseOracleProjection(
        schema=PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_OBSERVATION_SCHEMA,
        source_site=binding.source_site,
        destination_site=binding.destination_site,
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        schema_revision=binding.schema_revision,
        baseline_generation_id=binding.baseline_generation_id,
        baseline_manifest_sha256=binding.baseline_manifest_sha256,
        baseline_wal_lsn=binding.baseline_wal_lsn,
        timeline_id=binding.timeline_id,
        stream_generation_id=binding.stream_generation_id,
        destination_age_recipient=binding.destination_age_recipient,
        route_binding_sha256=binding.route_binding_sha256,
        writer_epoch=binding.writer_epoch,
        writer_lease_id=binding.writer_lease_id,
        witness_transition_id=binding.witness_transition_id,
        witnessed_term_proof_sha256=binding.witnessed_term_proof_sha256,
        target_acknowledged_wal_lsn=binding.target_acknowledged_wal_lsn,
        blob_object_frontier_wal_lsn=binding.blob_object_frontier_wal_lsn,
        committed_at=evidence.committed_at,
        observed_at=value.observed_at,
        source_request_sha256=evidence.permit.source_request_sha256,
        destination_receipt_sha256=evidence.permit.destination_receipt_sha256,
        local_commit_record_id=evidence.local_commit_record_id,
        local_response_id=evidence.local_response_id,
    )
