"""Process-local V2 recovery-evidence bridge for physical Full Matrix.

This is deliberately a *joining* boundary, not an acknowledgement protocol.
It accepts only already-verified V2 evidence for one exact chunked base backup,
its exact WAL/Blob coverage, and a signed target-recovery readback.  The
result stays process-local and non-authorizing: it cannot replace the still
missing V2 request/receipt/durable-ledger/strict-writer-response protocol.

The bridge exists so the Full-Matrix readiness oracle can stop treating the
retired single-object V1 bundle as its recovery substrate without allowing a
caller to join independently valid evidence from different V2 campaigns,
routes, lineages, or target LSNs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import re
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core.physical_postgres_chunked_base_backup_recovery_readback_attestation import (
    VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation,
)
from core.physical_postgres_chunked_base_backup_target_recovery_preflight import (
    PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig,
    PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
    VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight,
    require_verified_physical_postgres_chunked_base_backup_target_recovery_preflight,
)
from core.physical_wal_chunked_base_backup_blob_frontier_coverage import (
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
    VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
    VerifiedPhysicalWalV2BlobObjectVersionCoverage,
)
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    PhysicalWalChunkedBaseBackupHandoffReceiptError,
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    PhysicalWalChunkedBaseBackupManifestError,
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_recovery_admission import (
    VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
)
from core.physical_wal_chunked_base_backup_remote_ack_bridge import (
    VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
)
from core.physical_wal_chunked_base_backup_target_wal_continuity import (
    PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    PhysicalWalChunkedBaseBackupTargetWalContinuityError,
    VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
)
from core.physical_wal_chunked_base_backup_transfer import (
    PhysicalWalChunkedBaseBackupBinding,
)
from core.physical_wal_v2_remote_ack_coverage import (
    PhysicalWalV2RemoteAckCoverageError,
    PhysicalWalV2RemoteAckCoverageScope,
    VerifiedPhysicalWalV2RemoteAckCoverage,
    require_verified_physical_wal_v2_remote_ack_coverage,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCHEMA",
    "PhysicalFullMatrixV2RecoveryEvidenceConfig",
    "PhysicalFullMatrixV2RecoveryEvidenceError",
    "PhysicalFullMatrixV2RecoveryEvidenceInputs",
    "PhysicalFullMatrixV2RecoveryEvidenceScope",
    "VerifiedPhysicalFullMatrixV2RecoveryEvidence",
    "mint_verified_physical_full_matrix_v2_recovery_evidence",
    "require_verified_physical_full_matrix_v2_recovery_evidence",
)


PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCHEMA = (
    "gold-trade-physical-full-matrix-v2-recovery-evidence-v1"
)
PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_DEFAULT_ENABLED = False

_CAPABILITY = object()
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)


class PhysicalFullMatrixV2RecoveryEvidenceError(ValueError):
    """V2 recovery evidence is absent, stale, foreign, or not process-local."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalFullMatrixV2RecoveryEvidenceScope:
    """Public policy pins for one exact V2 recovery target.

    ``transfer_binding`` carries the source/destination, campaign/release,
    route, four-role identity, recipient, and witnessed writer-term pins.
    The target LSN is deliberately separate so a valid base-only observation
    cannot be relabelled as evidence for a later WAL target.
    """

    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    target_replay_lsn: str


@dataclass(frozen=True)
class PhysicalFullMatrixV2RecoveryEvidenceConfig:
    """Default-off, no-secret policy and upstream V2 verifier contexts."""

    scope: PhysicalFullMatrixV2RecoveryEvidenceScope | None = None
    target_recovery_config: (
        PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig | None
    ) = None
    blob_expected_owner_public_key: bytes = b""
    blob_frontier_scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope | None = None
    target_wal_continuity_scope: (
        PhysicalWalChunkedBaseBackupTargetWalContinuityScope | None
    ) = None
    remote_ack_coverage_scope: PhysicalWalV2RemoteAckCoverageScope | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalFullMatrixV2RecoveryEvidenceInputs:
    """Only opaque V2 capabilities needed for one revalidated evidence join."""

    manifest: object | None = None
    handoff_receipt: object | None = None
    recovery_admission: object | None = None
    target_wal_continuity_receipt: object | None = None
    target_wal_continuity: object | None = None
    recovery_readback_attestation: object | None = None
    target_recovery_preflight: object | None = None
    base_backup_evidence: object | None = None
    blob_owner_coverage: object | None = None
    blob_frontier_coverage: object | None = None
    remote_ack_coverage: object | None = None


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalFullMatrixV2RecoveryEvidence:
    """Opaque V2 recovery/coverage projection, never an acknowledgement permit."""

    schema: str
    evidence_sha256: str
    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    stream_generation_id: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    manifest_id: str
    manifest_sha256: str
    handoff_receipt_id: str
    handoff_receipt_nonce: str
    handoff_expires_at: datetime
    recovery_admission_scope_sha256: str
    stage_directory_name: str
    stage_receipt_sha256: str
    lineage_sha256: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    completion_attestation_sha256: str
    witness_transition_id: str
    target_replay_lsn: str
    wal_continuity_receipt_id: str
    wal_continuity_receipt_nonce: str
    wal_continuity_receipt_sha256: str
    wal_continuity_scope_sha256: str
    wal_continuity_selector_set_sha256: str
    blob_frontier_scope_sha256: str
    blob_owner_coverage_sha256: str
    blob_coverage_id: str
    blob_coverage_nonce: str
    object_version_set_sha256: str
    coverage_scope_sha256: str
    target_recovery_context_sha256: str
    readback_evidence_sha256: str
    readback_attestation_sha256: str
    readback_attestation_id: str
    readback_attestation_nonce: str
    readback_attestation_scope_sha256: str
    readback_attester_public_key_sha256: str
    readback_attester_key_id: str
    observed_at: datetime
    recovery_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Facts:
    scope: PhysicalFullMatrixV2RecoveryEvidenceScope
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    coverage: VerifiedPhysicalWalV2RemoteAckCoverage
    target: VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV2RecoveryEvidenceConfig
    inputs: PhysicalFullMatrixV2RecoveryEvidenceInputs


_STATES: WeakKeyDictionary[
    VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    _State,
] = WeakKeyDictionary()


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV2RecoveryEvidenceError(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> str:
    if type(value) is not str or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _binding_mapping(value: PhysicalWalChunkedBaseBackupBinding) -> dict[str, object]:
    return {
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "object_storage_namespace": value.object_storage_namespace,
        "route_commitment_sha256": value.route_commitment_sha256,
        "four_role_binding_sha256": value.four_role_binding_sha256,
        "destination_age_recipient": value.destination_age_recipient,
        "writer_term": {
            "writer_holder_site": value.writer_term.writer_holder_site,
            "writer_epoch": value.writer_term.writer_epoch,
            "writer_lease_id": value.writer_term.writer_lease_id,
            "witnessed_term_proof_sha256": value.writer_term.witnessed_term_proof_sha256,
        },
        "transport_plane": value.transport_plane,
        "direct_webapp_transport": value.direct_webapp_transport,
    }


def _scope_facts(
    config: object,
) -> tuple[
    PhysicalFullMatrixV2RecoveryEvidenceScope,
    PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig,
    bytes,
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    PhysicalWalV2RemoteAckCoverageScope,
]:
    if (
        type(config) is not PhysicalFullMatrixV2RecoveryEvidenceConfig
        or config.enabled is not True
        or type(config.scope) is not PhysicalFullMatrixV2RecoveryEvidenceScope
        or type(config.target_recovery_config)
        is not PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig
        or type(config.blob_expected_owner_public_key) is not bytes
        or len(config.blob_expected_owner_public_key) != 32
        or config.blob_expected_owner_public_key == b"\x00" * 32
        or type(config.blob_frontier_scope)
        is not PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope
        or type(config.target_wal_continuity_scope)
        is not PhysicalWalChunkedBaseBackupTargetWalContinuityScope
        or type(config.remote_ack_coverage_scope) is not PhysicalWalV2RemoteAckCoverageScope
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_CONFIG_INVALID")
    scope = config.scope
    binding = scope.transfer_binding
    if type(binding) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCOPE_INVALID")
    target_lsn = _lsn(
        scope.target_replay_lsn,
        code="PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCOPE_INVALID",
    )
    target_config = config.target_recovery_config
    target_context = target_config.context
    if (
        target_config.enabled is not True
        or target_context is None
        or target_context.transfer_binding != binding
        or target_context.target_replay_lsn != target_lsn
        or config.blob_frontier_scope.transfer_binding != binding
        or config.blob_frontier_scope.target_wal_lsn != target_lsn
        or config.target_wal_continuity_scope.transfer_binding != binding
        or config.target_wal_continuity_scope.target_lsn != target_lsn
        or config.remote_ack_coverage_scope.base_backup_scope.transfer_binding != binding
        or config.remote_ack_coverage_scope.target_lsn != target_lsn
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCOPE_MISMATCH")
    return (
        scope,
        target_config,
        config.blob_expected_owner_public_key,
        config.blob_frontier_scope,
        config.target_wal_continuity_scope,
        config.remote_ack_coverage_scope,
    )


def _inputs(value: object) -> PhysicalFullMatrixV2RecoveryEvidenceInputs:
    if type(value) is not PhysicalFullMatrixV2RecoveryEvidenceInputs:
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_INPUTS_INVALID")
    required = (
        (value.manifest, VerifiedPhysicalWalChunkedBaseBackupManifest),
        (value.handoff_receipt, VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt),
        (value.recovery_admission, VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission),
        (
            value.target_wal_continuity_receipt,
            VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
        ),
        (value.target_wal_continuity, VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity),
        (
            value.recovery_readback_attestation,
            VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation,
        ),
        (
            value.target_recovery_preflight,
            VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight,
        ),
        (
            value.base_backup_evidence,
            VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
        ),
        (value.blob_owner_coverage, VerifiedPhysicalWalV2BlobObjectVersionCoverage),
        (
            value.blob_frontier_coverage,
            VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage,
        ),
        (value.remote_ack_coverage, VerifiedPhysicalWalV2RemoteAckCoverage),
    )
    if any(type(item) is not expected for item, expected in required):
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_INPUTS_INVALID")
    return value


def _derive(
    *,
    config: object,
    inputs: object,
    now: datetime,
) -> _Facts:
    (
        scope,
        target_config,
        blob_owner_public_key,
        blob_scope,
        continuity_scope,
        coverage_scope,
    ) = _scope_facts(config)
    supplied = _inputs(inputs)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_CLOCK_INVALID")
    try:
        manifest = require_verified_physical_wal_chunked_base_backup_manifest(
            supplied.manifest,
            now=now,
        )
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            supplied.handoff_receipt,
            manifest=manifest,
            now=now,
        )
        coverage = require_verified_physical_wal_v2_remote_ack_coverage(
            supplied.remote_ack_coverage,
            base_backup_evidence=supplied.base_backup_evidence,
            blob_frontier_coverage=supplied.blob_frontier_coverage,
            blob_owner_coverage=supplied.blob_owner_coverage,
            blob_expected_owner_public_key=blob_owner_public_key,
            target_wal_continuity=supplied.target_wal_continuity,
            target_wal_continuity_receipt=supplied.target_wal_continuity_receipt,
            manifest=manifest,
            handoff_receipt=handoff,
            blob_scope=blob_scope,
            continuity_scope=continuity_scope,
            scope=coverage_scope,
            now=now,
        )
        target = require_verified_physical_postgres_chunked_base_backup_target_recovery_preflight(
            supplied.target_recovery_preflight,
            config=target_config,
            recovery_admission=supplied.recovery_admission,
            manifest=manifest,
            handoff_receipt=handoff,
            target_wal_continuity=supplied.target_wal_continuity,
            target_wal_continuity_receipt=supplied.target_wal_continuity_receipt,
            target_wal_continuity_scope=continuity_scope,
            recovery_readback_attestation=supplied.recovery_readback_attestation,
            now=now,
        )
    except (
        PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
        PhysicalWalChunkedBaseBackupBlobFrontierCoverageError,
        PhysicalWalChunkedBaseBackupHandoffReceiptError,
        PhysicalWalChunkedBaseBackupManifestError,
        PhysicalWalChunkedBaseBackupTargetWalContinuityError,
        PhysicalWalV2RemoteAckCoverageError,
        TypeError,
        ValueError,
    ) as exc:
        raise PhysicalFullMatrixV2RecoveryEvidenceError(
            "PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_UPSTREAM_INVALID"
        ) from exc
    _cross_pin(scope=scope, coverage=coverage, target=target, handoff=handoff)
    return _Facts(scope=scope, handoff=handoff, coverage=coverage, target=target)


def _cross_pin(
    *,
    scope: PhysicalFullMatrixV2RecoveryEvidenceScope,
    coverage: VerifiedPhysicalWalV2RemoteAckCoverage,
    target: VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
) -> None:
    binding = scope.transfer_binding
    manifest_sha = coverage.canonical_manifest_sha256
    if (
        coverage.transfer_binding != binding
        or coverage.target_lsn != scope.target_replay_lsn
        or target.source_site != binding.source_site
        or target.destination_site != binding.destination_site
        or target.receiver_site != binding.destination_site
        or target.campaign_id != binding.campaign_id
        or target.release_sha != binding.release_sha
        or target.binding_sha256 != handoff.binding_sha256
        or target.manifest_id != coverage.manifest_id
        or target.manifest_sha256 != manifest_sha
        or target.receipt_id != coverage.handoff_receipt_id
        or target.receipt_nonce != coverage.handoff_receipt_nonce
        or target.lineage_sha256 != coverage.lineage_sha256
        or target.baseline_generation_id != coverage.baseline_generation_id
        or target.database_system_identifier != coverage.database_system_identifier
        or target.timeline_id != coverage.timeline_id
        or target.wal_segment_size_bytes != coverage.wal_segment_size_bytes
        or target.baseline_wal_lsn != coverage.baseline_wal_lsn
        or target.wal_chain_start_lsn != coverage.wal_chain_start_lsn
        or target.base_backup_end_lsn != coverage.base_backup_end_lsn
        or target.completion_attestation_sha256 != handoff.completion_attestation_sha256
        or target.target_replay_lsn != coverage.target_lsn
        or target.continuity_receipt_id != coverage.wal_continuity_receipt_id
        or target.continuity_receipt_nonce != coverage.wal_continuity_receipt_nonce
        or target.continuity_scope_sha256 != coverage.wal_continuity_scope_sha256
        or target.continuity_selector_set_sha256
        != coverage.wal_continuity_selector_set_sha256
        or target.writer_epoch != binding.writer_term.writer_epoch
        or target.writer_lease_id != binding.writer_term.writer_lease_id
        or target.witnessed_term_proof_sha256
        != binding.writer_term.witnessed_term_proof_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_CROSS_PIN_MISMATCH")


def _result_from_facts(
    facts: _Facts,
) -> VerifiedPhysicalFullMatrixV2RecoveryEvidence:
    coverage = facts.coverage
    target = facts.target
    handoff = facts.handoff
    payload = {
        "schema": PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCHEMA,
        "transfer_binding": _binding_mapping(facts.scope.transfer_binding),
        "stream_generation_id": coverage.stream_generation_id,
        "route_commitment_sha256": coverage.transfer_binding.route_commitment_sha256,
        "four_role_binding_sha256": coverage.transfer_binding.four_role_binding_sha256,
        "manifest_id": coverage.manifest_id,
        "manifest_sha256": coverage.canonical_manifest_sha256,
        "handoff_receipt_id": coverage.handoff_receipt_id,
        "handoff_receipt_nonce": coverage.handoff_receipt_nonce,
        "handoff_expires_at": coverage.handoff_expires_at.isoformat(),
        "recovery_admission_scope_sha256": target.recovery_admission_scope_sha256,
        "stage_directory_name": target.stage_directory_name,
        "stage_receipt_sha256": target.stage_receipt_sha256,
        "lineage_sha256": coverage.lineage_sha256,
        "baseline_generation_id": coverage.baseline_generation_id,
        "database_system_identifier": coverage.database_system_identifier,
        "timeline_id": coverage.timeline_id,
        "wal_segment_size_bytes": coverage.wal_segment_size_bytes,
        "baseline_wal_lsn": coverage.baseline_wal_lsn,
        "wal_chain_start_lsn": coverage.wal_chain_start_lsn,
        "base_backup_end_lsn": coverage.base_backup_end_lsn,
        "completion_attestation_sha256": handoff.completion_attestation_sha256,
        "witness_transition_id": handoff.witness_transition_id,
        "target_replay_lsn": coverage.target_lsn,
        "wal_continuity_receipt_id": coverage.wal_continuity_receipt_id,
        "wal_continuity_receipt_nonce": coverage.wal_continuity_receipt_nonce,
        # The V2 coverage capability intentionally exposes the receipt id,
        # nonce, scope, and selector commitment, but not a receipt digest.
        # Both V2 verifiers above are supplied the exact same opaque receipt;
        # take the digest only from the independently revalidated target
        # recovery preflight rather than inventing a field on coverage.
        "wal_continuity_receipt_sha256": target.continuity_receipt_sha256,
        "wal_continuity_scope_sha256": coverage.wal_continuity_scope_sha256,
        "wal_continuity_selector_set_sha256": coverage.wal_continuity_selector_set_sha256,
        "blob_frontier_scope_sha256": coverage.blob_frontier_scope_sha256,
        "blob_owner_coverage_sha256": coverage.blob_owner_coverage_sha256,
        "blob_coverage_id": coverage.blob_coverage_id,
        "blob_coverage_nonce": coverage.blob_coverage_nonce,
        "object_version_set_sha256": coverage.object_version_set_sha256,
        "coverage_scope_sha256": coverage.coverage_scope_sha256,
        "target_recovery_context_sha256": target.context_sha256,
        "readback_evidence_sha256": target.readback_evidence_sha256,
        "readback_attestation_sha256": target.readback_attestation_sha256,
        "readback_attestation_id": target.readback_attestation_id,
        "readback_attestation_nonce": target.readback_attestation_nonce,
        "readback_attestation_scope_sha256": target.readback_attestation_scope_sha256,
        "readback_attester_public_key_sha256": target.expected_readback_attester_public_key_sha256,
        "readback_attester_key_id": target.expected_readback_attester_key_id,
        "observed_at": target.observed_at.isoformat(),
        "recovery_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }
    try:
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV2RecoveryEvidenceError(
            "PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_FACTS_INVALID"
        ) from exc
    return VerifiedPhysicalFullMatrixV2RecoveryEvidence(
        schema=PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCHEMA,
        evidence_sha256=digest,
        transfer_binding=facts.scope.transfer_binding,
        stream_generation_id=coverage.stream_generation_id,
        route_commitment_sha256=coverage.transfer_binding.route_commitment_sha256,
        four_role_binding_sha256=coverage.transfer_binding.four_role_binding_sha256,
        manifest_id=coverage.manifest_id,
        manifest_sha256=coverage.canonical_manifest_sha256,
        handoff_receipt_id=coverage.handoff_receipt_id,
        handoff_receipt_nonce=coverage.handoff_receipt_nonce,
        handoff_expires_at=coverage.handoff_expires_at,
        recovery_admission_scope_sha256=target.recovery_admission_scope_sha256,
        stage_directory_name=target.stage_directory_name,
        stage_receipt_sha256=target.stage_receipt_sha256,
        lineage_sha256=coverage.lineage_sha256,
        baseline_generation_id=coverage.baseline_generation_id,
        database_system_identifier=coverage.database_system_identifier,
        timeline_id=coverage.timeline_id,
        wal_segment_size_bytes=coverage.wal_segment_size_bytes,
        baseline_wal_lsn=coverage.baseline_wal_lsn,
        wal_chain_start_lsn=coverage.wal_chain_start_lsn,
        base_backup_end_lsn=coverage.base_backup_end_lsn,
        completion_attestation_sha256=handoff.completion_attestation_sha256,
        witness_transition_id=handoff.witness_transition_id,
        target_replay_lsn=coverage.target_lsn,
        wal_continuity_receipt_id=coverage.wal_continuity_receipt_id,
        wal_continuity_receipt_nonce=coverage.wal_continuity_receipt_nonce,
        wal_continuity_receipt_sha256=target.continuity_receipt_sha256,
        wal_continuity_scope_sha256=coverage.wal_continuity_scope_sha256,
        wal_continuity_selector_set_sha256=coverage.wal_continuity_selector_set_sha256,
        blob_frontier_scope_sha256=coverage.blob_frontier_scope_sha256,
        blob_owner_coverage_sha256=coverage.blob_owner_coverage_sha256,
        blob_coverage_id=coverage.blob_coverage_id,
        blob_coverage_nonce=coverage.blob_coverage_nonce,
        object_version_set_sha256=coverage.object_version_set_sha256,
        coverage_scope_sha256=coverage.coverage_scope_sha256,
        target_recovery_context_sha256=target.context_sha256,
        readback_evidence_sha256=target.readback_evidence_sha256,
        readback_attestation_sha256=target.readback_attestation_sha256,
        readback_attestation_id=target.readback_attestation_id,
        readback_attestation_nonce=target.readback_attestation_nonce,
        readback_attestation_scope_sha256=target.readback_attestation_scope_sha256,
        readback_attester_public_key_sha256=target.expected_readback_attester_public_key_sha256,
        readback_attester_key_id=target.expected_readback_attester_key_id,
        observed_at=target.observed_at,
    )


def _assert_result(
    value: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    facts: _Facts,
) -> None:
    expected = _result_from_facts(facts)
    if type(value) is not VerifiedPhysicalFullMatrixV2RecoveryEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_CAPABILITY_REQUIRED")
    for field_name in (
        "schema",
        "evidence_sha256",
        "transfer_binding",
        "stream_generation_id",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "manifest_id",
        "manifest_sha256",
        "handoff_receipt_id",
        "handoff_receipt_nonce",
        "handoff_expires_at",
        "recovery_admission_scope_sha256",
        "stage_directory_name",
        "stage_receipt_sha256",
        "lineage_sha256",
        "baseline_generation_id",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "completion_attestation_sha256",
        "witness_transition_id",
        "target_replay_lsn",
        "wal_continuity_receipt_id",
        "wal_continuity_receipt_nonce",
        "wal_continuity_receipt_sha256",
        "wal_continuity_scope_sha256",
        "wal_continuity_selector_set_sha256",
        "blob_frontier_scope_sha256",
        "blob_owner_coverage_sha256",
        "blob_coverage_id",
        "blob_coverage_nonce",
        "object_version_set_sha256",
        "coverage_scope_sha256",
        "target_recovery_context_sha256",
        "readback_evidence_sha256",
        "readback_attestation_sha256",
        "readback_attestation_id",
        "readback_attestation_nonce",
        "readback_attestation_scope_sha256",
        "readback_attester_public_key_sha256",
        "readback_attester_key_id",
        "observed_at",
        "recovery_authorized",
        "promotion_authorized",
        "execution_authorized",
    ):
        if getattr(value, field_name) != getattr(expected, field_name):
            _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_CAPABILITY_TAMPERED")


def mint_verified_physical_full_matrix_v2_recovery_evidence(
    *,
    config: PhysicalFullMatrixV2RecoveryEvidenceConfig,
    inputs: PhysicalFullMatrixV2RecoveryEvidenceInputs,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2RecoveryEvidence:
    """Mint one non-authorizing process-local V2 recovery evidence capability."""

    facts = _derive(config=config, inputs=inputs, now=now)
    result = _result_from_facts(facts)
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = _State(config=config, inputs=inputs)
    _assert_result(result, facts)
    return result


def require_verified_physical_full_matrix_v2_recovery_evidence(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2RecoveryEvidence:
    """Revalidate every retained V2 proof at the caller's current clock."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV2RecoveryEvidence
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_CAPABILITY_REQUIRED")
    facts = _derive(config=state.config, inputs=state.inputs, now=now)
    _assert_result(value, facts)
    return value
