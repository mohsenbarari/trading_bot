"""Pure V2 evidence join for a future, separately reviewed remote acknowledgement.

This foundation joins three already-verified V2 capabilities only:

* chunked PostgreSQL base-backup evidence;
* exact Blob frontier coverage; and
* exact WAL continuity to the same target LSN.

It does not import or construct the historical generic remote-ack binding,
request, receipt, ledger, transport, readiness, provider, filesystem, or
promotion surface.  The returned capability is nonserializable evidence only.
It cannot claim an acknowledgement, authorize a writer, restore data, or
promote a standby.  A later coordinator must still bind this evidence to its
own independently reviewed acknowledgement protocol and live readbacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import re
from typing import Any

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core.physical_wal_chunked_base_backup_blob_frontier_coverage import (
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    PhysicalWalV2BlobObjectVersionSelector,
    VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
    VerifiedPhysicalWalV2BlobObjectVersionCoverage,
    require_verified_physical_wal_chunked_base_backup_blob_frontier_coverage,
)
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    PhysicalWalChunkedBaseBackupManifestChunkSelector,
    VerifiedPhysicalWalChunkedBaseBackupManifest,
)
from core.physical_wal_chunked_base_backup_remote_ack_bridge import (
    PhysicalWalChunkedBaseBackupRemoteAckBridgeError,
    PhysicalWalChunkedBaseBackupRemoteAckScope,
    VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
    require_verified_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence,
)
from core.physical_wal_chunked_base_backup_target_wal_continuity import (
    PhysicalWalChunkedBaseBackupTargetWalContinuityError,
    PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector,
    PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
    require_verified_physical_wal_chunked_base_backup_target_wal_continuity,
)
from core.physical_wal_chunked_base_backup_transfer import PhysicalWalChunkedBaseBackupBinding


__all__ = (
    "PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA",
    "PhysicalWalV2RemoteAckCoverageError",
    "PhysicalWalV2RemoteAckCoverageObjectSelector",
    "PhysicalWalV2RemoteAckCoverageScope",
    "VerifiedPhysicalWalV2RemoteAckCoverage",
    "mint_physical_wal_v2_remote_ack_coverage",
    "require_verified_physical_wal_v2_remote_ack_coverage",
)


PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA = (
    "gold-trade-physical-wal-v2-remote-ack-coverage-v2"
)
_CAPABILITY = object()
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_SOURCES = frozenset({"base_backup", "wal", "blob"})


class PhysicalWalV2RemoteAckCoverageError(ValueError):
    """V2 recovery coverage cannot safely seed a future ack join."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2RemoteAckCoverageObjectSelector:
    """One exact immutable encrypted object version in the joined coverage set."""

    source: str
    ordinal: int
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int
    age_recipient: str


@dataclass(frozen=True)
class PhysicalWalV2RemoteAckCoverageScope:
    """One target LSN over a fully revalidated V2 base-backup context."""

    base_backup_scope: PhysicalWalChunkedBaseBackupRemoteAckScope
    target_lsn: str


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2RemoteAckCoverage:
    """Opaque non-authorizing full-object coverage for a later V2 ack join."""

    schema: str
    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    stream_generation_id: str
    canonical_manifest_sha256: str
    manifest_id: str
    handoff_receipt_id: str
    handoff_receipt_nonce: str
    handoff_expires_at: datetime
    lineage_sha256: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    target_lsn: str
    base_backup_scope_sha256: str
    blob_frontier_scope_sha256: str
    blob_owner_coverage_sha256: str
    blob_coverage_id: str
    blob_coverage_nonce: str
    wal_continuity_scope_sha256: str
    wal_continuity_receipt_id: str
    wal_continuity_receipt_nonce: str
    wal_continuity_selector_set_sha256: str
    object_version_set_sha256: str
    objects: tuple[PhysicalWalV2RemoteAckCoverageObjectSelector, ...]
    coverage_scope_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Facts:
    base: VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence
    blob: VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage
    continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity
    scope: PhysicalWalV2RemoteAckCoverageScope
    scope_sha256: str
    objects: tuple[PhysicalWalV2RemoteAckCoverageObjectSelector, ...]
    object_set_sha256: str


def _fail(code: str) -> None:
    raise PhysicalWalV2RemoteAckCoverageError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2RemoteAckCoverageError(code) from exc


def _nonzero_sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    if type(value) is not str or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


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


def _route_bound_wal_prefix(
    binding: PhysicalWalChunkedBaseBackupBinding,
    *,
    lineage_sha256: str,
) -> str:
    lineage = _nonzero_sha256(
        lineage_sha256,
        code="V2_REMOTE_ACK_COVERAGE_LINEAGE_INVALID",
    )
    return (
        f"{binding.object_storage_namespace}/{binding.campaign_id}/"
        f"{binding.release_sha}/wal-v2/{lineage}/"
    )


def _selector_mapping(value: PhysicalWalV2RemoteAckCoverageObjectSelector) -> dict[str, object]:
    return {
        "source": value.source,
        "ordinal": value.ordinal,
        "object_key": value.object_key,
        "version_id": value.version_id,
        "ciphertext_sha256": value.ciphertext_sha256,
        "ciphertext_bytes": value.ciphertext_bytes,
        "plaintext_sha256": value.plaintext_sha256,
        "plaintext_bytes": value.plaintext_bytes,
        "age_recipient": value.age_recipient,
    }


def _base_selector(value: PhysicalWalChunkedBaseBackupManifestChunkSelector) -> PhysicalWalV2RemoteAckCoverageObjectSelector:
    return PhysicalWalV2RemoteAckCoverageObjectSelector(
        source="base_backup",
        ordinal=value.index,
        object_key=value.object_key,
        version_id=value.version_id,
        ciphertext_sha256=value.ciphertext_sha256,
        ciphertext_bytes=value.ciphertext_bytes,
        plaintext_sha256=value.plaintext_sha256,
        plaintext_bytes=value.plaintext_bytes,
        age_recipient=value.age_recipient,
    )


def _wal_selector(
    value: PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector,
) -> PhysicalWalV2RemoteAckCoverageObjectSelector:
    return PhysicalWalV2RemoteAckCoverageObjectSelector(
        source="wal",
        ordinal=value.index,
        object_key=value.object_key,
        version_id=value.version_id,
        ciphertext_sha256=value.ciphertext_sha256,
        ciphertext_bytes=value.ciphertext_bytes,
        plaintext_sha256=value.plaintext_sha256,
        plaintext_bytes=value.plaintext_bytes,
        age_recipient=value.age_recipient,
    )


def _blob_selector(value: PhysicalWalV2BlobObjectVersionSelector) -> PhysicalWalV2RemoteAckCoverageObjectSelector:
    return PhysicalWalV2RemoteAckCoverageObjectSelector(
        source="blob",
        ordinal=value.ordinal,
        object_key=value.object_key,
        version_id=value.version_id,
        ciphertext_sha256=value.ciphertext_sha256,
        ciphertext_bytes=value.ciphertext_bytes,
        plaintext_sha256=value.plaintext_sha256,
        plaintext_bytes=value.plaintext_bytes,
        age_recipient=value.age_recipient,
    )


def _object_set(
    *,
    base: VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
    blob: VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
    continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
) -> tuple[tuple[PhysicalWalV2RemoteAckCoverageObjectSelector, ...], str]:
    selectors = (
        tuple(_base_selector(item) for item in base.chunks)
        + tuple(_wal_selector(item) for item in continuity.wal_object_selectors)
        + tuple(_blob_selector(item) for item in blob.objects)
    )
    if not selectors:
        _fail("V2_REMOTE_ACK_COVERAGE_OBJECT_SET_EMPTY")
    expected = {
        "base_backup": tuple(range(len(base.chunks))),
        "wal": tuple(range(len(continuity.wal_object_selectors))),
        "blob": tuple(range(len(blob.objects))),
    }
    for source in _SOURCES:
        actual = tuple(item.ordinal for item in selectors if item.source == source)
        if actual != expected[source]:
            _fail("V2_REMOTE_ACK_COVERAGE_OBJECT_ORDER_INVALID")
    if any(
        item.source not in _SOURCES
        or type(item.ordinal) is not int
        or item.ordinal < 0
        or item.age_recipient != base.transfer_binding.destination_age_recipient
        or _nonzero_sha256(item.ciphertext_sha256, code="V2_REMOTE_ACK_COVERAGE_OBJECT_INVALID")
        != item.ciphertext_sha256
        or _nonzero_sha256(item.plaintext_sha256, code="V2_REMOTE_ACK_COVERAGE_OBJECT_INVALID")
        != item.plaintext_sha256
        or type(item.ciphertext_bytes) is not int
        or type(item.plaintext_bytes) is not int
        or item.ciphertext_bytes < 1
        or item.plaintext_bytes < 1
        for item in selectors
    ):
        _fail("V2_REMOTE_ACK_COVERAGE_OBJECT_INVALID")
    pairs = {(item.object_key, item.version_id) for item in selectors}
    keys = {item.object_key for item in selectors}
    if len(pairs) != len(selectors) or len(keys) != len(selectors):
        _fail("V2_REMOTE_ACK_COVERAGE_OBJECT_VERSION_OVERLAP")
    digest = hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA,
                "canonical_manifest_sha256": base.canonical_manifest_sha256,
                "lineage_sha256": base.lineage_sha256,
                "base_backup_end_lsn": base.base_backup_end_lsn,
                "target_lsn": continuity.target_lsn,
                "objects": [_selector_mapping(item) for item in selectors],
            },
            code="V2_REMOTE_ACK_COVERAGE_OBJECT_INVALID",
        )
    ).hexdigest()
    return selectors, digest


def _scope_facts(
    value: object,
    *,
    base: VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
) -> tuple[PhysicalWalV2RemoteAckCoverageScope, str]:
    if type(value) is not PhysicalWalV2RemoteAckCoverageScope:
        _fail("V2_REMOTE_ACK_COVERAGE_SCOPE_REQUIRED")
    scope = value
    if type(scope.base_backup_scope) is not PhysicalWalChunkedBaseBackupRemoteAckScope:
        _fail("V2_REMOTE_ACK_COVERAGE_SCOPE_BASE_CONTEXT_INVALID")
    target, target_value = _lsn(scope.target_lsn, code="V2_REMOTE_ACK_COVERAGE_SCOPE_TARGET_INVALID")
    _base_end, base_end_value = _lsn(
        base.base_backup_end_lsn,
        code="V2_REMOTE_ACK_COVERAGE_SCOPE_BASE_CONTEXT_INVALID",
    )
    if target_value < base_end_value:
        _fail("V2_REMOTE_ACK_COVERAGE_SCOPE_TARGET_PRECEDES_BASE")
    if scope.base_backup_scope.transfer_binding != base.transfer_binding:
        candidate = scope.base_backup_scope.transfer_binding
        if (
            candidate.source_site != base.transfer_binding.source_site
            or candidate.destination_site != base.transfer_binding.destination_site
            or candidate.campaign_id != base.transfer_binding.campaign_id
            or candidate.release_sha != base.transfer_binding.release_sha
            or candidate.route_commitment_sha256 != base.transfer_binding.route_commitment_sha256
        ):
            _fail("V2_REMOTE_ACK_COVERAGE_SCOPE_ROUTE_MISMATCH")
        if candidate.destination_age_recipient != base.transfer_binding.destination_age_recipient:
            _fail("V2_REMOTE_ACK_COVERAGE_SCOPE_RECIPIENT_MISMATCH")
        if candidate.writer_term != base.transfer_binding.writer_term:
            _fail("V2_REMOTE_ACK_COVERAGE_SCOPE_TERM_MISMATCH")
        _fail("V2_REMOTE_ACK_COVERAGE_SCOPE_BASE_CONTEXT_INVALID")
    if (
        scope.base_backup_scope.stream_generation_id != base.stream_generation_id
        or scope.base_backup_scope.baseline_generation_id != base.baseline_generation_id
        or scope.base_backup_scope.lineage_sha256 != base.lineage_sha256
        or scope.base_backup_scope.database_system_identifier
        != base.database_system_identifier
        or scope.base_backup_scope.timeline_id != base.timeline_id
        or scope.base_backup_scope.wal_segment_size_bytes != base.wal_segment_size_bytes
        or scope.base_backup_scope.baseline_wal_lsn != base.baseline_wal_lsn
        or scope.base_backup_scope.wal_chain_start_lsn != base.wal_chain_start_lsn
        or scope.base_backup_scope.base_backup_end_lsn != base.base_backup_end_lsn
    ):
        _fail("V2_REMOTE_ACK_COVERAGE_SCOPE_BASE_CONTEXT_INVALID")
    normalized = PhysicalWalV2RemoteAckCoverageScope(
        base_backup_scope=scope.base_backup_scope,
        target_lsn=target,
    )
    digest = hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA,
                "base_backup_scope": {
                    "binding": _binding_mapping(base.transfer_binding),
                    "stream_generation_id": base.stream_generation_id,
                    "baseline_generation_id": base.baseline_generation_id,
                    "lineage_sha256": base.lineage_sha256,
                    "database_system_identifier": base.database_system_identifier,
                    "timeline_id": base.timeline_id,
                    "wal_segment_size_bytes": base.wal_segment_size_bytes,
                    "baseline_wal_lsn": base.baseline_wal_lsn,
                    "wal_chain_start_lsn": base.wal_chain_start_lsn,
                    "base_backup_end_lsn": base.base_backup_end_lsn,
                },
                "target_lsn": target,
            },
            code="V2_REMOTE_ACK_COVERAGE_SCOPE_BASE_CONTEXT_INVALID",
        )
    ).hexdigest()
    return normalized, digest


def _cross_pin(
    *,
    base: VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
    blob: VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
    continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    scope: PhysicalWalV2RemoteAckCoverageScope,
) -> None:
    binding = base.transfer_binding
    for label, candidate in (("BLOB", blob.transfer_binding), ("WAL", continuity.transfer_binding)):
        if candidate != binding:
            if (
                candidate.source_site != binding.source_site
                or candidate.destination_site != binding.destination_site
                or candidate.campaign_id != binding.campaign_id
                or candidate.release_sha != binding.release_sha
                or candidate.route_commitment_sha256 != binding.route_commitment_sha256
            ):
                _fail(f"V2_REMOTE_ACK_COVERAGE_{label}_ROUTE_MISMATCH")
            if candidate.destination_age_recipient != binding.destination_age_recipient:
                _fail(f"V2_REMOTE_ACK_COVERAGE_{label}_RECIPIENT_MISMATCH")
            if candidate.writer_term != binding.writer_term:
                _fail(f"V2_REMOTE_ACK_COVERAGE_{label}_TERM_MISMATCH")
            _fail(f"V2_REMOTE_ACK_COVERAGE_{label}_BINDING_MISMATCH")
    if (
        blob.canonical_base_backup_manifest_sha256 != base.canonical_manifest_sha256
        or continuity.canonical_manifest_sha256 != base.canonical_manifest_sha256
        or continuity.manifest_id != base.manifest_id
    ):
        _fail("V2_REMOTE_ACK_COVERAGE_MANIFEST_MISMATCH")
    if blob.lineage_sha256 != base.lineage_sha256 or continuity.lineage_sha256 != base.lineage_sha256:
        _fail("V2_REMOTE_ACK_COVERAGE_LINEAGE_MISMATCH")
    wal_prefix = _route_bound_wal_prefix(binding, lineage_sha256=base.lineage_sha256)
    if any(
        not item.object_key.startswith(wal_prefix)
        or len(item.object_key) == len(wal_prefix)
        or item.object_key[len(wal_prefix)] == "/"
        for item in continuity.wal_object_selectors
    ):
        _fail("V2_REMOTE_ACK_COVERAGE_WAL_OBJECT_PREFIX_INVALID")
    if (
        blob.baseline_generation_id != base.baseline_generation_id
        or blob.database_system_identifier != base.database_system_identifier
        or blob.timeline_id != base.timeline_id
        or blob.wal_segment_size_bytes != base.wal_segment_size_bytes
        or blob.baseline_wal_lsn != base.baseline_wal_lsn
        or blob.wal_chain_start_lsn != base.wal_chain_start_lsn
        or blob.base_backup_end_lsn != base.base_backup_end_lsn
        or continuity.base_backup_end_lsn != base.base_backup_end_lsn
    ):
        _fail("V2_REMOTE_ACK_COVERAGE_BASELINE_WAL_MISMATCH")
    if blob.target_wal_lsn != scope.target_lsn or continuity.target_lsn != scope.target_lsn:
        _fail("V2_REMOTE_ACK_COVERAGE_TARGET_MISMATCH")
    if (
        blob.handoff_receipt_id != base.handoff_receipt_id
        or blob.handoff_receipt_nonce != base.handoff_receipt_nonce
        or blob.handoff_expires_at != base.handoff_expires_at
        or continuity.handoff_receipt_id != base.handoff_receipt_id
        or continuity.handoff_receipt_nonce != base.handoff_receipt_nonce
        or continuity.handoff_expires_at != base.handoff_expires_at
    ):
        _fail("V2_REMOTE_ACK_COVERAGE_HANDOFF_MISMATCH")


def _derive_facts(
    *,
    base_backup_evidence: object,
    blob_frontier_coverage: object,
    blob_owner_coverage: object,
    blob_expected_owner_public_key: bytes,
    target_wal_continuity: object,
    target_wal_continuity_receipt: object,
    manifest: object,
    handoff_receipt: object,
    blob_scope: object,
    continuity_scope: object,
    scope: object,
    now: datetime,
) -> _Facts:
    if type(scope) is not PhysicalWalV2RemoteAckCoverageScope:
        _fail("V2_REMOTE_ACK_COVERAGE_SCOPE_REQUIRED")
    try:
        base = require_verified_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
            base_backup_evidence,
            manifest=manifest,
            handoff_receipt=handoff_receipt,
            scope=scope.base_backup_scope,
            now=now,
        )
    except PhysicalWalChunkedBaseBackupRemoteAckBridgeError as exc:
        raise PhysicalWalV2RemoteAckCoverageError(
            "V2_REMOTE_ACK_COVERAGE_BASE_EVIDENCE_INVALID"
        ) from exc
    typed_scope, scope_sha = _scope_facts(scope, base=base)
    try:
        blob = require_verified_physical_wal_chunked_base_backup_blob_frontier_coverage(
            blob_frontier_coverage,
            owner_coverage=blob_owner_coverage,
            expected_owner_public_key=blob_expected_owner_public_key,
            manifest=manifest,
            handoff_receipt=handoff_receipt,
            scope=blob_scope,
            now=now,
        )
    except PhysicalWalChunkedBaseBackupBlobFrontierCoverageError as exc:
        raise PhysicalWalV2RemoteAckCoverageError(
            "V2_REMOTE_ACK_COVERAGE_BLOB_FRONTIER_INVALID"
        ) from exc
    try:
        continuity = require_verified_physical_wal_chunked_base_backup_target_wal_continuity(
            target_wal_continuity,
            manifest=manifest,
            handoff_receipt=handoff_receipt,
            continuity_receipt=target_wal_continuity_receipt,
            scope=continuity_scope,
            now=now,
        )
    except PhysicalWalChunkedBaseBackupTargetWalContinuityError as exc:
        raise PhysicalWalV2RemoteAckCoverageError(
            "V2_REMOTE_ACK_COVERAGE_TARGET_WAL_INVALID"
        ) from exc
    _cross_pin(base=base, blob=blob, continuity=continuity, scope=typed_scope)
    objects, object_set_sha = _object_set(base=base, blob=blob, continuity=continuity)
    return _Facts(
        base=base,
        blob=blob,
        continuity=continuity,
        scope=typed_scope,
        scope_sha256=scope_sha,
        objects=objects,
        object_set_sha256=object_set_sha,
    )


def mint_physical_wal_v2_remote_ack_coverage(
    *,
    base_backup_evidence: VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
    blob_frontier_coverage: VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
    blob_owner_coverage: VerifiedPhysicalWalV2BlobObjectVersionCoverage,
    blob_expected_owner_public_key: bytes,
    target_wal_continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    target_wal_continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    blob_scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    continuity_scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    scope: PhysicalWalV2RemoteAckCoverageScope,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckCoverage:
    """Mint V2-only coverage evidence; no generic ack claim is emitted."""

    facts = _derive_facts(
        base_backup_evidence=base_backup_evidence,
        blob_frontier_coverage=blob_frontier_coverage,
        blob_owner_coverage=blob_owner_coverage,
        blob_expected_owner_public_key=blob_expected_owner_public_key,
        target_wal_continuity=target_wal_continuity,
        target_wal_continuity_receipt=target_wal_continuity_receipt,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        blob_scope=blob_scope,
        continuity_scope=continuity_scope,
        scope=scope,
        now=now,
    )
    result = VerifiedPhysicalWalV2RemoteAckCoverage(
        schema=PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA,
        transfer_binding=facts.base.transfer_binding,
        stream_generation_id=facts.base.stream_generation_id,
        canonical_manifest_sha256=facts.base.canonical_manifest_sha256,
        manifest_id=facts.base.manifest_id,
        handoff_receipt_id=facts.base.handoff_receipt_id,
        handoff_receipt_nonce=facts.base.handoff_receipt_nonce,
        handoff_expires_at=facts.base.handoff_expires_at,
        lineage_sha256=facts.base.lineage_sha256,
        baseline_generation_id=facts.base.baseline_generation_id,
        database_system_identifier=facts.base.database_system_identifier,
        timeline_id=facts.base.timeline_id,
        wal_segment_size_bytes=facts.base.wal_segment_size_bytes,
        baseline_wal_lsn=facts.base.baseline_wal_lsn,
        wal_chain_start_lsn=facts.base.wal_chain_start_lsn,
        base_backup_end_lsn=facts.base.base_backup_end_lsn,
        target_lsn=facts.scope.target_lsn,
        base_backup_scope_sha256=facts.base.scope_sha256,
        blob_frontier_scope_sha256=facts.blob.scope_sha256,
        blob_owner_coverage_sha256=facts.blob.owner_coverage_sha256,
        blob_coverage_id=facts.blob.coverage_id,
        blob_coverage_nonce=facts.blob.coverage_nonce,
        wal_continuity_scope_sha256=facts.continuity.scope_sha256,
        wal_continuity_receipt_id=facts.continuity.continuity_receipt_id,
        wal_continuity_receipt_nonce=facts.continuity.continuity_receipt_nonce,
        wal_continuity_selector_set_sha256=facts.continuity.selector_set_sha256,
        object_version_set_sha256=facts.object_set_sha256,
        objects=facts.objects,
        coverage_scope_sha256=facts.scope_sha256,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return require_verified_physical_wal_v2_remote_ack_coverage(
        result,
        base_backup_evidence=base_backup_evidence,
        blob_frontier_coverage=blob_frontier_coverage,
        blob_owner_coverage=blob_owner_coverage,
        blob_expected_owner_public_key=blob_expected_owner_public_key,
        target_wal_continuity=target_wal_continuity,
        target_wal_continuity_receipt=target_wal_continuity_receipt,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        blob_scope=blob_scope,
        continuity_scope=continuity_scope,
        scope=scope,
        now=now,
    )


def require_verified_physical_wal_v2_remote_ack_coverage(
    value: object,
    *,
    base_backup_evidence: VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
    blob_frontier_coverage: VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
    blob_owner_coverage: VerifiedPhysicalWalV2BlobObjectVersionCoverage,
    blob_expected_owner_public_key: bytes,
    target_wal_continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    target_wal_continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    blob_scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    continuity_scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    scope: PhysicalWalV2RemoteAckCoverageScope,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckCoverage:
    """Revalidate all V2 inputs and reject forged or stale joined evidence."""

    if (
        type(value) is not VerifiedPhysicalWalV2RemoteAckCoverage
        or value._capability is not _CAPABILITY
        or value.schema != PHYSICAL_WAL_V2_REMOTE_ACK_COVERAGE_SCHEMA
    ):
        _fail("V2_REMOTE_ACK_COVERAGE_CAPABILITY_REQUIRED")
    facts = _derive_facts(
        base_backup_evidence=base_backup_evidence,
        blob_frontier_coverage=blob_frontier_coverage,
        blob_owner_coverage=blob_owner_coverage,
        blob_expected_owner_public_key=blob_expected_owner_public_key,
        target_wal_continuity=target_wal_continuity,
        target_wal_continuity_receipt=target_wal_continuity_receipt,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        blob_scope=blob_scope,
        continuity_scope=continuity_scope,
        scope=scope,
        now=now,
    )
    if (
        value.transfer_binding != facts.base.transfer_binding
        or value.stream_generation_id != facts.base.stream_generation_id
        or value.canonical_manifest_sha256 != facts.base.canonical_manifest_sha256
        or value.manifest_id != facts.base.manifest_id
        or value.handoff_receipt_id != facts.base.handoff_receipt_id
        or value.handoff_receipt_nonce != facts.base.handoff_receipt_nonce
        or value.handoff_expires_at != facts.base.handoff_expires_at
        or value.lineage_sha256 != facts.base.lineage_sha256
        or value.baseline_generation_id != facts.base.baseline_generation_id
        or value.database_system_identifier != facts.base.database_system_identifier
        or value.timeline_id != facts.base.timeline_id
        or value.wal_segment_size_bytes != facts.base.wal_segment_size_bytes
        or value.baseline_wal_lsn != facts.base.baseline_wal_lsn
        or value.wal_chain_start_lsn != facts.base.wal_chain_start_lsn
        or value.base_backup_end_lsn != facts.base.base_backup_end_lsn
        or value.target_lsn != facts.scope.target_lsn
        or value.base_backup_scope_sha256 != facts.base.scope_sha256
        or value.blob_frontier_scope_sha256 != facts.blob.scope_sha256
        or value.blob_owner_coverage_sha256 != facts.blob.owner_coverage_sha256
        or value.blob_coverage_id != facts.blob.coverage_id
        or value.blob_coverage_nonce != facts.blob.coverage_nonce
        or value.wal_continuity_scope_sha256 != facts.continuity.scope_sha256
        or value.wal_continuity_receipt_id != facts.continuity.continuity_receipt_id
        or value.wal_continuity_receipt_nonce != facts.continuity.continuity_receipt_nonce
        or value.wal_continuity_selector_set_sha256 != facts.continuity.selector_set_sha256
        or value.object_version_set_sha256 != facts.object_set_sha256
        or value.objects != facts.objects
        or value.coverage_scope_sha256 != facts.scope_sha256
    ):
        _fail("V2_REMOTE_ACK_COVERAGE_CAPABILITY_TAMPERED")
    return value
