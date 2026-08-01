"""Opaque v2 bridge from verified source base-backup lineage to chunked transfer.

The only legacy surface read here is the already-verified binding contract;
no spool result, capture function, uploader, Object Storage client, or v1
runtime is imported or invoked.  This bridge turns those trusted non-secret
facts into an opaque envelope so a v2 manifest/receipt cannot be connected to
WAL/blob recovery with caller-invented baseline metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from typing import Any
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wal_base_backup_spool import (
    PhysicalWalBaseBackupSpoolError,
    VerifiedPhysicalWalBaseBackupBinding,
    require_verified_physical_wal_base_backup_binding,
)
from core.physical_wal_chunked_base_backup_transfer import PhysicalWalChunkedBaseBackupBinding


__all__ = (
    "PhysicalWalChunkedBaseBackupLineageEnvelopeError",
    "VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope",
    "build_physical_wal_chunked_base_backup_lineage_envelope",
    "require_verified_physical_wal_chunked_base_backup_lineage_envelope",
)


_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupLineageEnvelopeError(ValueError):
    """The v2 transfer cannot safely join its verified source lineage."""


@dataclass(frozen=True)
class VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope:
    """Opaque source lineage + immutable snapshot identity, never promotion authority."""

    snapshot_sha256: str
    snapshot_bytes: int
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    completion_attestation_sha256: str
    legacy_route_binding_sha256: str
    witness_transition_id: str
    lineage_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


# Do not surface the legacy verified binding through the envelope's public
# shape.  It stays process-local and is only recoverable by this verifier for
# an envelope minted in this process; a serialized/forged/replaced value has
# no hidden source bridge and therefore fails closed.
_ENVELOPE_SOURCE_BINDINGS: WeakKeyDictionary[
    VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope,
    VerifiedPhysicalWalBaseBackupBinding,
] = WeakKeyDictionary()


def _fail(code: str) -> None:
    raise PhysicalWalChunkedBaseBackupLineageEnvelopeError(code)


def _facts(
    *,
    source_binding: object,
    transfer_binding: object,
    now: datetime,
) -> tuple[VerifiedPhysicalWalBaseBackupBinding, str]:
    try:
        binding = require_verified_physical_wal_base_backup_binding(source_binding, now=now)
    except PhysicalWalBaseBackupSpoolError as exc:
        raise PhysicalWalChunkedBaseBackupLineageEnvelopeError(
            "CHUNKED_BASE_BACKUP_LINEAGE_SOURCE_BINDING_INVALID"
        ) from exc
    if type(transfer_binding) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("CHUNKED_BASE_BACKUP_LINEAGE_TRANSFER_BINDING_INVALID")
    manifest = binding.manifest_binding
    artifact = binding.completed_artifact
    term = binding.witnessed_term
    if (
        manifest.source_site != transfer_binding.source_site
        or manifest.destination_site != transfer_binding.destination_site
        or manifest.campaign_id != transfer_binding.campaign_id
        or manifest.release_sha != transfer_binding.release_sha
        or manifest.object_storage_namespace != transfer_binding.object_storage_namespace
        or manifest.destination_age_recipient != transfer_binding.destination_age_recipient
        or term.holder_site != transfer_binding.writer_term.writer_holder_site
        or term.writer_epoch != transfer_binding.writer_term.writer_epoch
        or term.writer_lease_id != transfer_binding.writer_term.writer_lease_id
        or term.proof_sha256 != transfer_binding.writer_term.witnessed_term_proof_sha256
    ):
        _fail("CHUNKED_BASE_BACKUP_LINEAGE_TRANSFER_MISMATCH")
    payload: dict[str, Any] = {
        "schema": "gold-trade-physical-wal-chunked-base-backup-lineage-envelope-v2",
        "source_site": manifest.source_site,
        "destination_site": manifest.destination_site,
        "campaign_id": manifest.campaign_id,
        "release_sha": manifest.release_sha,
        "object_storage_namespace": manifest.object_storage_namespace,
        "destination_age_recipient": manifest.destination_age_recipient,
        "baseline_generation_id": manifest.baseline_generation_id,
        "database_system_identifier": manifest.database_system_identifier,
        "timeline_id": manifest.timeline_id,
        "wal_segment_size_bytes": manifest.wal_segment_size_bytes,
        "baseline_wal_lsn": manifest.baseline_wal_lsn,
        "wal_chain_start_lsn": manifest.wal_chain_start_lsn,
        "base_backup_end_lsn": manifest.base_backup_end_lsn,
        "snapshot_sha256": artifact.plaintext_sha256,
        "snapshot_bytes": artifact.plaintext_bytes,
        "completion_attestation_sha256": artifact.completion_attestation_sha256,
        "legacy_route_binding_sha256": binding.route_binding_sha256,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": term.proof_sha256,
        "v2_route_commitment_sha256": transfer_binding.route_commitment_sha256,
        "v2_four_role_binding_sha256": transfer_binding.four_role_binding_sha256,
    }
    try:
        lineage_sha = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError) as exc:  # pragma: no cover - normalized above.
        raise PhysicalWalChunkedBaseBackupLineageEnvelopeError(
            "CHUNKED_BASE_BACKUP_LINEAGE_CANONICAL_INVALID"
        ) from exc
    return binding, lineage_sha


def build_physical_wal_chunked_base_backup_lineage_envelope(
    *,
    source_binding: VerifiedPhysicalWalBaseBackupBinding,
    transfer_binding: PhysicalWalChunkedBaseBackupBinding,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope:
    """Create opaque v2 lineage evidence from verified source facts only."""

    binding, lineage_sha = _facts(
        source_binding=source_binding,
        transfer_binding=transfer_binding,
        now=now,
    )
    manifest = binding.manifest_binding
    artifact = binding.completed_artifact
    term = binding.witnessed_term
    envelope = VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope(
        snapshot_sha256=artifact.plaintext_sha256,
        snapshot_bytes=artifact.plaintext_bytes,
        baseline_generation_id=manifest.baseline_generation_id,
        database_system_identifier=manifest.database_system_identifier,
        timeline_id=manifest.timeline_id,
        wal_segment_size_bytes=manifest.wal_segment_size_bytes,
        baseline_wal_lsn=manifest.baseline_wal_lsn,
        wal_chain_start_lsn=manifest.wal_chain_start_lsn,
        base_backup_end_lsn=manifest.base_backup_end_lsn,
        completion_attestation_sha256=artifact.completion_attestation_sha256,
        legacy_route_binding_sha256=binding.route_binding_sha256,
        witness_transition_id=term.witness_transition_id,
        lineage_sha256=lineage_sha,
    )
    object.__setattr__(envelope, "_capability", _CAPABILITY)
    _ENVELOPE_SOURCE_BINDINGS[envelope] = binding
    return envelope


def require_verified_physical_wal_chunked_base_backup_lineage_envelope(
    value: object,
    *,
    transfer_binding: PhysicalWalChunkedBaseBackupBinding,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope:
    """Revalidate source term/lineage and reject any forged opaque envelope."""

    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope
        or value._capability is not _CAPABILITY
    ):
        _fail("CHUNKED_BASE_BACKUP_LINEAGE_ENVELOPE_REQUIRED")
    source_binding = _ENVELOPE_SOURCE_BINDINGS.get(value)
    if source_binding is None:
        _fail("CHUNKED_BASE_BACKUP_LINEAGE_ENVELOPE_REQUIRED")
    binding, lineage_sha = _facts(
        source_binding=source_binding,
        transfer_binding=transfer_binding,
        now=now,
    )
    manifest = binding.manifest_binding
    artifact = binding.completed_artifact
    term = binding.witnessed_term
    if (
        binding.completed_artifact.plaintext_sha256 != value.snapshot_sha256
        or binding.completed_artifact.plaintext_bytes != value.snapshot_bytes
        or manifest.baseline_generation_id != value.baseline_generation_id
        or manifest.database_system_identifier != value.database_system_identifier
        or manifest.timeline_id != value.timeline_id
        or manifest.wal_segment_size_bytes != value.wal_segment_size_bytes
        or manifest.baseline_wal_lsn != value.baseline_wal_lsn
        or manifest.wal_chain_start_lsn != value.wal_chain_start_lsn
        or manifest.base_backup_end_lsn != value.base_backup_end_lsn
        or artifact.completion_attestation_sha256 != value.completion_attestation_sha256
        or binding.route_binding_sha256 != value.legacy_route_binding_sha256
        or term.witness_transition_id != value.witness_transition_id
        or lineage_sha != value.lineage_sha256
    ):
        _fail("CHUNKED_BASE_BACKUP_LINEAGE_ENVELOPE_TAMPERED")
    return value
