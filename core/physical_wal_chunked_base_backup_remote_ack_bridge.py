"""Pure V2-only base-backup evidence bridge for a future remote-ack join.

The generic physical-WAL remote-ack contract has an ``objects_complete``
assertion covering both PostgreSQL and blob recovery objects.  A chunked V2
PostgreSQL base backup cannot prove that blob inventory/frontier.  Therefore
this module deliberately does **not** produce a generic remote-ack binding or
an acknowledgement request input.

Instead it narrows opaque V2 manifest + fresh Witness handoff evidence into a
base-backup-only capability.  A later V2-only join must combine this capability
with independently verified blob-frontier coverage before it can use the
existing signed remote-ack request/receipt/ledger contracts.  No raw object
list, blob frontier, or ``objects_complete`` flag is accepted here.

This module is pure: no filesystem, network, Object Storage, Arvan, database,
restore, promotion, or remote-ack transport side effect occurs here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib

from core.append_only_sync_delta_batch import (
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    canonical_json_bytes,
)
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    PhysicalWalChunkedBaseBackupHandoffReceiptError,
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    PhysicalWalChunkedBaseBackupManifestChunkSelector,
    PhysicalWalChunkedBaseBackupManifestError,
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_transfer import PhysicalWalChunkedBaseBackupBinding


__all__ = (
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCHEMA",
    "PhysicalWalChunkedBaseBackupRemoteAckBridgeError",
    "PhysicalWalChunkedBaseBackupRemoteAckScope",
    "VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence",
    "mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence",
    "require_verified_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-remote-ack-bridge-v2"
)

_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupRemoteAckBridgeError(ValueError):
    """V2 chunked evidence cannot safely seed the next remote-ack join."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupRemoteAckScope:
    """Typed policy expected to match one verified V2 base-backup handoff.

    This is intentionally limited to source lineage and WAL geometry.  It has
    no caller-controlled blob frontier, coverage declaration, or object list:
    those need a separate, verified V2 blob-coverage contract before generic
    remote acknowledgement is safe.
    """

    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    stream_generation_id: str
    baseline_generation_id: str
    lineage_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence:
    """Opaque V2 PostgreSQL-base-only evidence; never generic ack authority."""

    schema: str
    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    stream_generation_id: str
    canonical_manifest_sha256: str
    manifest_id: str
    handoff_receipt_id: str
    handoff_receipt_nonce: str
    handoff_expires_at: datetime
    lineage_sha256: str
    scope_sha256: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    chunks: tuple[PhysicalWalChunkedBaseBackupManifestChunkSelector, ...]
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Facts:
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    scope: PhysicalWalChunkedBaseBackupRemoteAckScope
    scope_sha256: str
    manifest_sha256: str


def _fail(code: str) -> None:
    raise PhysicalWalChunkedBaseBackupRemoteAckBridgeError(code)


def _nonzero_sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _binding_mapping(binding: PhysicalWalChunkedBaseBackupBinding) -> dict[str, object]:
    return {
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
    }


def _scope_sha256(scope: PhysicalWalChunkedBaseBackupRemoteAckScope) -> str:
    try:
        payload = {
            "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCHEMA,
            "transfer_binding": _binding_mapping(scope.transfer_binding),
            "stream_generation_id": scope.stream_generation_id,
            "baseline_generation_id": scope.baseline_generation_id,
            "lineage_sha256": scope.lineage_sha256,
            "database_system_identifier": scope.database_system_identifier,
            "timeline_id": scope.timeline_id,
            "wal_segment_size_bytes": scope.wal_segment_size_bytes,
            "baseline_wal_lsn": scope.baseline_wal_lsn,
            "wal_chain_start_lsn": scope.wal_chain_start_lsn,
            "base_backup_end_lsn": scope.base_backup_end_lsn,
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError, AttributeError) as exc:
        raise PhysicalWalChunkedBaseBackupRemoteAckBridgeError(
            "CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_INVALID"
        ) from exc


def _require_scope(
    value: object,
    *,
    binding: PhysicalWalChunkedBaseBackupBinding,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
) -> tuple[PhysicalWalChunkedBaseBackupRemoteAckScope, str]:
    if type(value) is not PhysicalWalChunkedBaseBackupRemoteAckScope:
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_REQUIRED")
    scope = value
    if type(scope.transfer_binding) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_BINDING_INVALID")
    expected = scope.transfer_binding
    if (
        expected.source_site != binding.source_site
        or expected.destination_site != binding.destination_site
        or expected.campaign_id != binding.campaign_id
        or expected.release_sha != binding.release_sha
    ):
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_ROUTE_MISMATCH")
    if expected.destination_age_recipient != binding.destination_age_recipient:
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_RECIPIENT_MISMATCH")
    if expected.writer_term != binding.writer_term:
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_TERM_MISMATCH")
    if expected != binding:
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_BINDING_MISMATCH")
    if (
        type(scope.stream_generation_id) is not str
        or STREAM_GENERATION_ID_RE.fullmatch(scope.stream_generation_id) is None
    ):
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_STREAM_INVALID")
    if scope.baseline_generation_id != handoff.baseline_generation_id:
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_BASELINE_GENERATION_MISMATCH")
    if _nonzero_sha256(
        scope.lineage_sha256,
        code="CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_LINEAGE_INVALID",
    ) != handoff.lineage_sha256:
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_LINEAGE_MISMATCH")
    if (
        scope.database_system_identifier != handoff.database_system_identifier
        or scope.timeline_id != handoff.timeline_id
        or scope.wal_segment_size_bytes != handoff.wal_segment_size_bytes
        or scope.baseline_wal_lsn != handoff.baseline_wal_lsn
        or scope.wal_chain_start_lsn != handoff.wal_chain_start_lsn
        or scope.base_backup_end_lsn != handoff.base_backup_end_lsn
    ):
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCOPE_WAL_GEOMETRY_MISMATCH")
    return scope, _scope_sha256(scope)


def _derive_facts(
    *,
    manifest: object,
    handoff_receipt: object,
    scope: object,
    now: datetime,
) -> _Facts:
    try:
        verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(manifest, now=now)
    except PhysicalWalChunkedBaseBackupManifestError as exc:
        raise PhysicalWalChunkedBaseBackupRemoteAckBridgeError(
            "CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_MANIFEST_INVALID"
        ) from exc
    try:
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt,
            manifest=verified_manifest,
            now=now,
        )
    except PhysicalWalChunkedBaseBackupHandoffReceiptError as exc:
        raise PhysicalWalChunkedBaseBackupRemoteAckBridgeError(
            "CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_HANDOFF_INVALID"
        ) from exc
    binding = verified_manifest.finalization_permit.session.binding
    typed_scope, scope_sha = _require_scope(scope, binding=binding, handoff=handoff)
    manifest_sha = hashlib.sha256(verified_manifest.canonical_manifest).hexdigest()
    if handoff.manifest_sha256 != manifest_sha:
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_MANIFEST_HASH_MISMATCH")
    if (
        handoff.destination_age_recipient != binding.destination_age_recipient
        or handoff.binding_sha256 == "0" * 64
        or handoff.session_sha256 != hashlib.sha256(
            verified_manifest.finalization_permit.session.canonical_session
        ).hexdigest()
    ):
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_HANDOFF_BINDING_MISMATCH")
    selectors = verified_manifest.chunks
    if not selectors or tuple(item.index for item in selectors) != tuple(range(len(selectors))):
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_CHUNK_INDEX_INVALID")
    pairs = tuple((item.object_key, item.version_id) for item in selectors)
    if len(set(pairs)) != len(pairs) or len({item.object_key for item in selectors}) != len(selectors):
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_CHUNK_SELECTOR_DUPLICATE")
    if any(item.age_recipient != binding.destination_age_recipient for item in selectors):
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_CHUNK_RECIPIENT_MISMATCH")
    return _Facts(
        manifest=verified_manifest,
        handoff=handoff,
        scope=typed_scope,
        scope_sha256=scope_sha,
        manifest_sha256=manifest_sha,
    )


def _require_capability(
    value: object,
    *,
    manifest: object,
    handoff_receipt: object,
    scope: object,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence:
    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence
        or value._capability is not _CAPABILITY
        or value.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCHEMA
    ):
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_CAPABILITY_REQUIRED")
    facts = _derive_facts(
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )
    if (
        value.transfer_binding != facts.manifest.finalization_permit.session.binding
        or value.stream_generation_id != facts.scope.stream_generation_id
        or value.canonical_manifest_sha256 != facts.manifest_sha256
        or value.manifest_id != facts.manifest.manifest_id
        or value.handoff_receipt_id != facts.handoff.receipt_id
        or value.handoff_receipt_nonce != facts.handoff.receipt_nonce
        or value.handoff_expires_at != facts.handoff.expires_at
        or value.lineage_sha256 != facts.handoff.lineage_sha256
        or value.scope_sha256 != facts.scope_sha256
        or value.baseline_generation_id != facts.handoff.baseline_generation_id
        or value.database_system_identifier != facts.handoff.database_system_identifier
        or value.timeline_id != facts.handoff.timeline_id
        or value.wal_segment_size_bytes != facts.handoff.wal_segment_size_bytes
        or value.baseline_wal_lsn != facts.handoff.baseline_wal_lsn
        or value.wal_chain_start_lsn != facts.handoff.wal_chain_start_lsn
        or value.base_backup_end_lsn != facts.handoff.base_backup_end_lsn
        or value.chunks != facts.manifest.chunks
    ):
        _fail("CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_CAPABILITY_TAMPERED")
    return value


def mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    scope: PhysicalWalChunkedBaseBackupRemoteAckScope,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence:
    """Mint opaque V2 PostgreSQL-base-only evidence, never a generic ack input."""

    facts = _derive_facts(
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )
    result = VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence(
        schema=PHYSICAL_WAL_CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCHEMA,
        transfer_binding=facts.manifest.finalization_permit.session.binding,
        stream_generation_id=facts.scope.stream_generation_id,
        canonical_manifest_sha256=facts.manifest_sha256,
        manifest_id=facts.manifest.manifest_id,
        handoff_receipt_id=facts.handoff.receipt_id,
        handoff_receipt_nonce=facts.handoff.receipt_nonce,
        handoff_expires_at=facts.handoff.expires_at,
        lineage_sha256=facts.handoff.lineage_sha256,
        scope_sha256=facts.scope_sha256,
        baseline_generation_id=facts.handoff.baseline_generation_id,
        database_system_identifier=facts.handoff.database_system_identifier,
        timeline_id=facts.handoff.timeline_id,
        wal_segment_size_bytes=facts.handoff.wal_segment_size_bytes,
        baseline_wal_lsn=facts.handoff.baseline_wal_lsn,
        wal_chain_start_lsn=facts.handoff.wal_chain_start_lsn,
        base_backup_end_lsn=facts.handoff.base_backup_end_lsn,
        chunks=facts.manifest.chunks,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return _require_capability(
        result,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )


def require_verified_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
    value: object,
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    scope: PhysicalWalChunkedBaseBackupRemoteAckScope,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence:
    """Revalidate base-only evidence and its still-fresh Witness handoff."""

    return _require_capability(
        value,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )
