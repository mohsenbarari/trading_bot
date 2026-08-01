"""Pure V2 evidence preflight for PostgreSQL replay beyond a staged base backup.

This is a deliberately narrow evidence boundary.  It does not fetch WAL,
open a stage directory, contact Object Storage, control PostgreSQL, restore a
cluster, promote a writer, or alter campaign readiness.  It accepts only a
previously verified, pinned-attester V2 recovery-readback capability; a bare
or merely self-hashed PostgreSQL readback can never reach this boundary.  The
only local-stage input accepted here is the process-local *membership-only*
projection of a previous recovery admission.

Unlike the base-end preflight, this module admits an exact target strictly
beyond ``base_backup_end_lsn`` only when an independently verified, signed V2
target-WAL-continuity capability proves the intervening immutable selector
chain.  It remains observation evidence, never recovery or writer authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import re
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    SHA256_RE,
    canonical_json_bytes,
)
from core.physical_postgres_chunked_base_backup_recovery_readback_attestation import (
    PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope,
    VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation,
    require_verified_physical_postgres_chunked_base_backup_recovery_readback_attestation,
)
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_recovery_admission import (
    VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    project_verified_physical_wal_chunked_base_backup_recovery_admission,
)
from core.physical_wal_chunked_base_backup_target_wal_continuity import (
    PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
    require_verified_physical_wal_chunked_base_backup_target_wal_continuity,
    require_verified_physical_wal_chunked_base_backup_target_wal_continuity_receipt,
)
from core.physical_wal_chunked_base_backup_transfer import (
    PhysicalWalChunkedBaseBackupBinding,
    PhysicalWalChunkedBaseBackupTransferError,
    build_physical_wal_chunked_base_backup_binding,
)


__all__ = (
    "DEFAULT_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_DEFAULT_ENABLED",
    "PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_SCHEMA",
    "PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig",
    "PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightContext",
    "PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError",
    "VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight",
    "mint_physical_postgres_chunked_base_backup_target_recovery_preflight",
    "require_verified_physical_postgres_chunked_base_backup_target_recovery_preflight",
)


PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_SCHEMA = (
    "gold-trade-physical-postgres-chunked-base-backup-target-recovery-preflight-v2"
)
PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_MAX_EVIDENCE_AGE_SECONDS = 90
MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_FUTURE_SKEW_SECONDS = 5
REQUIRED_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_WAL_SEGMENT_SIZE_BYTES = (
    16 * 1024 * 1024
)

_CAPABILITY = object()
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_SITE_RE = re.compile(r"^webapp_(?:fi|ir)$", re.ASCII)
_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_STAGE_DIRECTORY_RE = re.compile(r"^stage-[0-9a-f]{48}$", re.ASCII)
_ATTESTER_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)


class PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError(ValueError):
    """A V2 target-recovery evidence input is absent, stale, or unbound."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightContext:
    """Pins V2 stage, route, baseline, target, and attester trust identity.

    The public key, digest, and key id are policy commitments.  The typed
    config must repeat the exact public key; callers cannot substitute a signer
    at mint or require time.
    """

    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    receiver_site: str
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
    witness_public_key_sha256: str
    target_replay_lsn: str
    continuity_receipt_id: str
    continuity_receipt_nonce: str
    continuity_receipt_sha256: str
    continuity_scope_sha256: str
    continuity_selector_set_sha256: str
    expected_readback_attester_public_key: bytes
    expected_readback_attester_public_key_sha256: str
    expected_readback_attester_key_id: str


@dataclass(frozen=True)
class PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig:
    """Default-off pure policy; it has no paths, endpoints, or credentials.

    ``expected_readback_attester_public_key`` must come from immutable
    receiver-site trust policy in a future owning runtime.  It is public
    verification material only and confers no recovery, promotion, or writer
    authority.
    """

    context: PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightContext | None = None
    expected_readback_attester_public_key: bytes | None = None
    enabled: bool = PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_MAX_EVIDENCE_AGE_SECONDS
    )
    local_stage_readback: str = "already-admitted-membership-only"
    target_wal_continuity: str = "required"
    direct_site_control: str = "forbidden"
    remote_object_storage: str = "forbidden"
    restore_or_promotion: str = "forbidden"
    v1_fallback: str = "forbidden"


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight:
    """Opaque target replay observation, never a restore or promotion permit."""

    schema: str
    canonical_readback: bytes
    readback_evidence_sha256: str
    readback_attestation_sha256: str
    readback_attestation_id: str
    readback_attestation_nonce: str
    readback_attestation_scope_sha256: str
    expected_readback_attester_public_key: bytes
    expected_readback_attester_public_key_sha256: str
    expected_readback_attester_key_id: str
    observed_at: datetime
    context_sha256: str
    source_site: str
    destination_site: str
    receiver_site: str
    campaign_id: str
    release_sha: str
    binding_sha256: str
    manifest_id: str
    manifest_sha256: str
    receipt_id: str
    receipt_nonce: str
    session_sha256: str
    finalization_permit_id: str
    finalization_permit_sha256: str
    committed_chunk_set_sha256: str
    recovery_admission_scope_sha256: str
    stage_directory_name: str
    stage_receipt_sha256: str
    lineage_sha256: str
    snapshot_sha256: str
    snapshot_bytes: int
    total_plaintext_sha256: str
    total_plaintext_bytes: int
    chunk_count: int
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    completion_attestation_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    continuity_receipt_id: str
    continuity_receipt_nonce: str
    continuity_receipt_sha256: str
    continuity_scope_sha256: str
    continuity_selector_set_sha256: str
    continuity_selector_count: int
    target_replay_lsn: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ContextFacts:
    context: PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightContext
    binding: PhysicalWalChunkedBaseBackupBinding
    context_sha256: str


@dataclass(frozen=True)
class _Facts:
    context: _ContextFacts
    admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt
    continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity
    readback_attestation: VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation


@dataclass(frozen=True)
class _State:
    config: PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig
    admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity
    continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt
    continuity_scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope
    readback_attestation: VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation


_STATES: WeakKeyDictionary[
    VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight, _State
] = WeakKeyDictionary()


def _fail(code: str) -> None:
    raise PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError(code) from exc


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


def _site(value: object, *, code: str) -> str:
    if type(value) is not str or _SITE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _generation(value: object, *, code: str) -> str:
    if type(value) is not str or _GENERATION_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _system_identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _SYSTEM_IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _stage_directory(value: object, *, code: str) -> str:
    if type(value) is not str or _STAGE_DIRECTORY_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _positive(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    if type(value) is not str or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) + int(low, 16)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _fresh(value: object, *, now: datetime, maximum_age: int, code: str) -> datetime:
    observed = _utc(value, code=code)
    if observed > now + timedelta(
        seconds=MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_FUTURE_SKEW_SECONDS
    ) or now - observed > timedelta(seconds=maximum_age):
        _fail(code)
    return observed


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


def _normalise_binding(value: object) -> PhysicalWalChunkedBaseBackupBinding:
    if type(value) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONTEXT_INVALID")
    try:
        normalized = build_physical_wal_chunked_base_backup_binding(
            source_site=value.source_site,
            destination_site=value.destination_site,
            campaign_id=value.campaign_id,
            release_sha=value.release_sha,
            object_storage_namespace=value.object_storage_namespace,
            route_commitment_sha256=value.route_commitment_sha256,
            four_role_binding_sha256=value.four_role_binding_sha256,
            destination_age_recipient=value.destination_age_recipient,
            writer_holder_site=value.writer_term.writer_holder_site,
            writer_epoch=value.writer_term.writer_epoch,
            writer_lease_id=value.writer_term.writer_lease_id,
            witnessed_term_proof_sha256=value.writer_term.witnessed_term_proof_sha256,
        )
    except (AttributeError, PhysicalWalChunkedBaseBackupTransferError) as exc:
        raise PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError(
            "POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONTEXT_INVALID"
        ) from exc
    if normalized != value:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONTEXT_INVALID")
    return normalized


def _context_facts(value: object) -> _ContextFacts:
    if type(value) is not PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightContext:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONTEXT_INVALID")
    binding = _normalise_binding(value.transfer_binding)
    code = "POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONTEXT_INVALID"
    receiver = _site(value.receiver_site, code=code)
    admission_scope = _sha256(value.recovery_admission_scope_sha256, code=code)
    stage_name = _stage_directory(value.stage_directory_name, code=code)
    stage_sha = _sha256(value.stage_receipt_sha256, code=code)
    lineage = _sha256(value.lineage_sha256, code=code)
    generation = _generation(value.baseline_generation_id, code=code)
    system_identifier = _system_identifier(value.database_system_identifier, code=code)
    timeline = _positive(value.timeline_id, maximum=0xFFFFFFFF, code=code)
    wal_size = _positive(value.wal_segment_size_bytes, maximum=2**31 - 1, code=code)
    baseline, baseline_value = _lsn(value.baseline_wal_lsn, code=code)
    chain_start, chain_start_value = _lsn(value.wal_chain_start_lsn, code=code)
    base_end, base_end_value = _lsn(value.base_backup_end_lsn, code=code)
    completion = _sha256(value.completion_attestation_sha256, code=code)
    transition = _identifier(value.witness_transition_id, code=code)
    witness = _sha256(value.witness_public_key_sha256, code=code)
    target, target_value = _lsn(value.target_replay_lsn, code=code)
    continuity_id = _identifier(value.continuity_receipt_id, code=code)
    continuity_nonce = _nonce(value.continuity_receipt_nonce, code=code)
    continuity_sha = _sha256(value.continuity_receipt_sha256, code=code)
    continuity_scope = _sha256(value.continuity_scope_sha256, code=code)
    selector_sha = _sha256(value.continuity_selector_set_sha256, code=code)
    attester_key = value.expected_readback_attester_public_key
    if type(attester_key) is not bytes or len(attester_key) != 32:
        _fail(code)
    attester_key_sha = _sha256(
        value.expected_readback_attester_public_key_sha256,
        code=code,
    )
    attester_key_id = value.expected_readback_attester_key_id
    if (
        type(attester_key_id) is not str
        or _ATTESTER_KEY_ID_RE.fullmatch(attester_key_id) is None
        or attester_key_id != "ed25519-sha256:" + attester_key_sha
        or hashlib.sha256(attester_key).hexdigest() != attester_key_sha
    ):
        _fail(code)
    if (
        binding.destination_site != receiver
        or binding.source_site == binding.destination_site
        or binding.writer_term.writer_holder_site != binding.source_site
        or wal_size != REQUIRED_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_WAL_SEGMENT_SIZE_BYTES
        or baseline_value > base_end_value
        or chain_start_value > base_end_value
        or target_value <= base_end_value
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONTEXT_MISMATCH")
    context = PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightContext(
        transfer_binding=binding,
        receiver_site=receiver,
        recovery_admission_scope_sha256=admission_scope,
        stage_directory_name=stage_name,
        stage_receipt_sha256=stage_sha,
        lineage_sha256=lineage,
        baseline_generation_id=generation,
        database_system_identifier=system_identifier,
        timeline_id=timeline,
        wal_segment_size_bytes=wal_size,
        baseline_wal_lsn=baseline,
        wal_chain_start_lsn=chain_start,
        base_backup_end_lsn=base_end,
        completion_attestation_sha256=completion,
        witness_transition_id=transition,
        witness_public_key_sha256=witness,
        target_replay_lsn=target,
        continuity_receipt_id=continuity_id,
        continuity_receipt_nonce=continuity_nonce,
        continuity_receipt_sha256=continuity_sha,
        continuity_scope_sha256=continuity_scope,
        continuity_selector_set_sha256=selector_sha,
        expected_readback_attester_public_key=attester_key,
        expected_readback_attester_public_key_sha256=attester_key_sha,
        expected_readback_attester_key_id=attester_key_id,
    )
    if context != value:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONTEXT_INVALID")
    payload = {
        "schema": PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_SCHEMA,
        "transfer_binding": _binding_mapping(binding),
        "receiver_site": receiver,
        "recovery_admission_scope_sha256": admission_scope,
        "stage_directory_name": stage_name,
        "stage_receipt_sha256": stage_sha,
        "lineage_sha256": lineage,
        "baseline_generation_id": generation,
        "database_system_identifier": system_identifier,
        "timeline_id": timeline,
        "wal_segment_size_bytes": wal_size,
        "baseline_wal_lsn": baseline,
        "wal_chain_start_lsn": chain_start,
        "base_backup_end_lsn": base_end,
        "completion_attestation_sha256": completion,
        "witness_transition_id": transition,
        "witness_public_key_sha256": witness,
        "target_replay_lsn": target,
        "continuity_receipt_id": continuity_id,
        "continuity_receipt_nonce": continuity_nonce,
        "continuity_receipt_sha256": continuity_sha,
        "continuity_scope_sha256": continuity_scope,
        "continuity_selector_set_sha256": selector_sha,
        "expected_readback_attester_public_key_sha256": attester_key_sha,
        "expected_readback_attester_key_id": attester_key_id,
    }
    return _ContextFacts(
        context=context,
        binding=binding,
        context_sha256=hashlib.sha256(_canonical(payload, code=code)).hexdigest(),
    )


def _config_facts(value: object) -> tuple[_ContextFacts, int, bytes]:
    if type(value) is not PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONFIG_INVALID")
    if (
        value.enabled is not True
        or value.local_stage_readback != "already-admitted-membership-only"
        or value.target_wal_continuity != "required"
        or value.direct_site_control != "forbidden"
        or value.remote_object_storage != "forbidden"
        or value.restore_or_promotion != "forbidden"
        or value.v1_fallback != "forbidden"
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONFIG_INVALID")
    maximum = value.maximum_evidence_age_seconds
    if (
        type(maximum) is not int
        or not 1
        <= maximum
        <= MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_EVIDENCE_AGE_SECONDS
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONFIG_INVALID")
    context = _context_facts(value.context)
    expected_attester = value.expected_readback_attester_public_key
    if type(expected_attester) is not bytes or len(expected_attester) != 32:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONFIG_INVALID")
    expected_sha = hashlib.sha256(expected_attester).hexdigest()
    if (
        expected_sha != context.context.expected_readback_attester_public_key_sha256
        or expected_attester != context.context.expected_readback_attester_public_key
        or "ed25519-sha256:" + expected_sha
        != context.context.expected_readback_attester_key_id
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CONFIG_INVALID")
    return context, maximum, expected_attester


def _assert_cross_pins(
    *,
    context: _ContextFacts,
    admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
    continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    now: datetime,
    maximum_age: int,
) -> None:
    value = context.context
    binding = context.binding
    permit = manifest.finalization_permit
    manifest_sha = hashlib.sha256(manifest.canonical_manifest).hexdigest()
    permit_sha = hashlib.sha256(permit.canonical_finalization_permit).hexdigest()
    session_sha = hashlib.sha256(permit.session.canonical_session).hexdigest()
    witness_sha = hashlib.sha256(handoff.witness_public_key).hexdigest()
    _fresh(
        admission.admitted_at,
        now=now,
        maximum_age=maximum_age,
        code="POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_ADMISSION_STALE",
    )
    if (
        manifest.finalization_permit.session.binding != binding
        or handoff.binding_sha256 != admission.binding_sha256
        or handoff.manifest_id != manifest.manifest_id
        or handoff.manifest_sha256 != manifest_sha
        or handoff.session_sha256 != session_sha
        or handoff.finalization_permit_id != permit.finalization_permit_id
        or handoff.finalization_permit_sha256 != permit_sha
        or handoff.committed_chunk_set_sha256 != permit.committed_chunk_set_sha256
        or handoff.lineage_sha256 != value.lineage_sha256
        or handoff.baseline_generation_id != value.baseline_generation_id
        or handoff.database_system_identifier != value.database_system_identifier
        or handoff.timeline_id != value.timeline_id
        or handoff.wal_segment_size_bytes != value.wal_segment_size_bytes
        or handoff.baseline_wal_lsn != value.baseline_wal_lsn
        or handoff.wal_chain_start_lsn != value.wal_chain_start_lsn
        or handoff.base_backup_end_lsn != value.base_backup_end_lsn
        or handoff.completion_attestation_sha256 != value.completion_attestation_sha256
        or handoff.witness_transition_id != value.witness_transition_id
        or witness_sha != value.witness_public_key_sha256
        or admission.receiver_site != value.receiver_site
        or admission.scope_sha256 != value.recovery_admission_scope_sha256
        or admission.stage_directory_name != value.stage_directory_name
        or admission.stage_receipt_sha256 != value.stage_receipt_sha256
        or admission.receipt_id != handoff.receipt_id
        or admission.receipt_nonce != handoff.receipt_nonce
        or admission.manifest_id != manifest.manifest_id
        or admission.manifest_sha256 != manifest_sha
        or admission.binding_sha256 != handoff.binding_sha256
        or admission.session_sha256 != session_sha
        or admission.finalization_permit_id != permit.finalization_permit_id
        or admission.finalization_permit_sha256 != permit_sha
        or admission.committed_chunk_set_sha256 != permit.committed_chunk_set_sha256
        or admission.lineage_sha256 != handoff.lineage_sha256
        or admission.snapshot_sha256 != handoff.snapshot_sha256
        or admission.snapshot_bytes != handoff.snapshot_bytes
        or admission.total_plaintext_sha256 != manifest.total_plaintext_sha256
        or admission.total_plaintext_bytes != manifest.total_plaintext_bytes
        or admission.chunk_count != len(manifest.chunks)
        or admission.baseline_generation_id != handoff.baseline_generation_id
        or admission.database_system_identifier != handoff.database_system_identifier
        or admission.timeline_id != handoff.timeline_id
        or admission.wal_segment_size_bytes != handoff.wal_segment_size_bytes
        or admission.baseline_wal_lsn != handoff.baseline_wal_lsn
        or admission.wal_chain_start_lsn != handoff.wal_chain_start_lsn
        or admission.base_backup_end_lsn != handoff.base_backup_end_lsn
        or admission.completion_attestation_sha256 != handoff.completion_attestation_sha256
        or admission.witness_transition_id != handoff.witness_transition_id
        or admission.witness_public_key_sha256 != witness_sha
        or continuity.transfer_binding != binding
        or continuity.canonical_manifest_sha256 != manifest_sha
        or continuity.manifest_id != manifest.manifest_id
        or continuity.handoff_receipt_id != handoff.receipt_id
        or continuity.handoff_receipt_nonce != handoff.receipt_nonce
        or continuity.handoff_expires_at != handoff.expires_at
        or continuity.continuity_receipt_id != continuity_receipt.receipt_id
        or continuity.continuity_receipt_nonce != continuity_receipt.receipt_nonce
        or continuity.continuity_receipt_sha256 != continuity_receipt.receipt_sha256
        or continuity.continuity_receipt_expires_at != continuity_receipt.expires_at
        or continuity.lineage_sha256 != value.lineage_sha256
        or continuity.scope_sha256 != value.continuity_scope_sha256
        or continuity.base_backup_end_lsn != value.base_backup_end_lsn
        or continuity.target_lsn != value.target_replay_lsn
        or continuity.selector_set_sha256 != value.continuity_selector_set_sha256
        or continuity.wal_object_selectors != continuity_receipt.selectors
        or continuity_receipt.manifest_id != manifest.manifest_id
        or continuity_receipt.manifest_sha256 != manifest_sha
        or continuity_receipt.lineage_sha256 != value.lineage_sha256
        or continuity_receipt.scope_sha256 != value.continuity_scope_sha256
        or continuity_receipt.target_lsn != value.target_replay_lsn
        or continuity_receipt.receipt_id != value.continuity_receipt_id
        or continuity_receipt.receipt_nonce != value.continuity_receipt_nonce
        or continuity_receipt.receipt_sha256 != value.continuity_receipt_sha256
        or continuity_receipt.selector_set_sha256 != value.continuity_selector_set_sha256
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CROSS_PIN_MISMATCH")


def _readback_attestation_scope(
    context: _ContextFacts,
) -> PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope:
    """Derive the only accepted signed-readback scope from target policy."""

    value = context.context
    return PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope(
        transfer_binding=context.binding,
        receiver_site=value.receiver_site,
        lineage_sha256=value.lineage_sha256,
        baseline_generation_id=value.baseline_generation_id,
        database_system_identifier=value.database_system_identifier,
        timeline_id=value.timeline_id,
        wal_segment_size_bytes=value.wal_segment_size_bytes,
        baseline_wal_lsn=value.baseline_wal_lsn,
        wal_chain_start_lsn=value.wal_chain_start_lsn,
        base_backup_end_lsn=value.base_backup_end_lsn,
        completion_attestation_sha256=value.completion_attestation_sha256,
        witness_transition_id=value.witness_transition_id,
        witness_public_key_sha256=value.witness_public_key_sha256,
        expected_target_replay_lsn=value.target_replay_lsn,
    )


def _assert_readback_attestation_cross_pins(
    *,
    context: _ContextFacts,
    admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    readback_attestation: VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation,
    expected_attester_public_key: bytes,
    now: datetime,
    maximum_age: int,
) -> None:
    """Cross-pin signed host observation to the exact target-WAL evidence."""

    value = context.context
    manifest_sha = hashlib.sha256(manifest.canonical_manifest).hexdigest()
    expected_key_sha = hashlib.sha256(expected_attester_public_key).hexdigest()
    if (
        readback_attestation.transfer_binding != context.binding
        or readback_attestation.binding_sha256 != handoff.binding_sha256
        or readback_attestation.manifest_id != manifest.manifest_id
        or readback_attestation.manifest_sha256 != manifest_sha
        or readback_attestation.handoff_receipt_id != handoff.receipt_id
        or readback_attestation.handoff_receipt_nonce != handoff.receipt_nonce
        or readback_attestation.recovery_admission_scope_sha256 != admission.scope_sha256
        or readback_attestation.stage_directory_name != admission.stage_directory_name
        or readback_attestation.stage_receipt_sha256 != admission.stage_receipt_sha256
        or readback_attestation.target_replay_lsn != value.target_replay_lsn
        or readback_attestation.target_replay_lsn != continuity.target_lsn
        or readback_attestation.attester_public_key != expected_attester_public_key
        or expected_key_sha != value.expected_readback_attester_public_key_sha256
        or "ed25519-sha256:" + expected_key_sha
        != value.expected_readback_attester_key_id
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_READBACK_ATTESTATION_CROSS_PIN_MISMATCH")
    _fresh(
        readback_attestation.observed_at,
        now=now,
        maximum_age=maximum_age,
        code="POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_READBACK_ATTESTATION_STALE",
    )


def _facts(
    *,
    config: object,
    recovery_admission: object,
    manifest: object,
    handoff_receipt: object,
    target_wal_continuity: object,
    target_wal_continuity_receipt: object,
    target_wal_continuity_scope: object,
    recovery_readback_attestation: object,
    now: datetime,
) -> _Facts:
    context, maximum_age, expected_attester_public_key = _config_facts(config)
    observed_now = _utc(now, code="POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_CLOCK_INVALID")
    try:
        admission = project_verified_physical_wal_chunked_base_backup_recovery_admission(
            recovery_admission
        )
        verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(
            manifest,
            now=observed_now,
        )
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt,
            manifest=verified_manifest,
            now=observed_now,
        )
        receipt = require_verified_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
            target_wal_continuity_receipt,
            manifest=verified_manifest,
            handoff_receipt=handoff,
            scope=target_wal_continuity_scope,
            now=observed_now,
        )
        continuity = require_verified_physical_wal_chunked_base_backup_target_wal_continuity(
            target_wal_continuity,
            manifest=verified_manifest,
            handoff_receipt=handoff,
            continuity_receipt=receipt,
            scope=target_wal_continuity_scope,
            now=observed_now,
        )
    except PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError:
        raise
    except Exception as exc:
        raise PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError(
            "POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_V2_CAPABILITY_INVALID"
        ) from exc
    _assert_cross_pins(
        context=context,
        admission=admission,
        manifest=verified_manifest,
        handoff=handoff,
        continuity_receipt=receipt,
        continuity=continuity,
        now=observed_now,
        maximum_age=maximum_age,
    )
    try:
        readback_attestation = (
            require_verified_physical_postgres_chunked_base_backup_recovery_readback_attestation(
                recovery_readback_attestation,
                expected_attester_public_key=expected_attester_public_key,
                scope=_readback_attestation_scope(context),
                recovery_admission=recovery_admission,
                manifest=manifest,
                handoff_receipt=handoff_receipt,
                now=observed_now,
            )
        )
    except Exception as exc:
        raise PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError(
            "POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_READBACK_ATTESTATION_INVALID"
        ) from exc
    _assert_readback_attestation_cross_pins(
        context=context,
        admission=admission,
        manifest=verified_manifest,
        handoff=handoff,
        continuity=continuity,
        readback_attestation=readback_attestation,
        expected_attester_public_key=expected_attester_public_key,
        now=observed_now,
        maximum_age=maximum_age,
    )
    return _Facts(
        context=context,
        admission=admission,
        manifest=verified_manifest,
        handoff=handoff,
        continuity_receipt=receipt,
        continuity=continuity,
        readback_attestation=readback_attestation,
    )


def _result_from_facts(
    facts: _Facts,
) -> VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight:
    binding = facts.context.binding
    admission = facts.admission
    continuity = facts.continuity
    readback_attestation = facts.readback_attestation
    policy = facts.context.context
    return VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight(
        schema=PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_SCHEMA,
        canonical_readback=readback_attestation.canonical_readback,
        readback_evidence_sha256=readback_attestation.readback_sha256,
        readback_attestation_sha256=readback_attestation.attestation_sha256,
        readback_attestation_id=readback_attestation.attestation_id,
        readback_attestation_nonce=readback_attestation.attestation_nonce,
        readback_attestation_scope_sha256=readback_attestation.scope_sha256,
        expected_readback_attester_public_key=readback_attestation.attester_public_key,
        expected_readback_attester_public_key_sha256=(
            policy.expected_readback_attester_public_key_sha256
        ),
        expected_readback_attester_key_id=policy.expected_readback_attester_key_id,
        observed_at=readback_attestation.observed_at,
        context_sha256=facts.context.context_sha256,
        source_site=binding.source_site,
        destination_site=binding.destination_site,
        receiver_site=facts.context.context.receiver_site,
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        binding_sha256=admission.binding_sha256,
        manifest_id=admission.manifest_id,
        manifest_sha256=admission.manifest_sha256,
        receipt_id=admission.receipt_id,
        receipt_nonce=admission.receipt_nonce,
        session_sha256=admission.session_sha256,
        finalization_permit_id=admission.finalization_permit_id,
        finalization_permit_sha256=admission.finalization_permit_sha256,
        committed_chunk_set_sha256=admission.committed_chunk_set_sha256,
        recovery_admission_scope_sha256=admission.scope_sha256,
        stage_directory_name=admission.stage_directory_name,
        stage_receipt_sha256=admission.stage_receipt_sha256,
        lineage_sha256=admission.lineage_sha256,
        snapshot_sha256=admission.snapshot_sha256,
        snapshot_bytes=admission.snapshot_bytes,
        total_plaintext_sha256=admission.total_plaintext_sha256,
        total_plaintext_bytes=admission.total_plaintext_bytes,
        chunk_count=admission.chunk_count,
        baseline_generation_id=admission.baseline_generation_id,
        database_system_identifier=admission.database_system_identifier,
        timeline_id=admission.timeline_id,
        wal_segment_size_bytes=admission.wal_segment_size_bytes,
        baseline_wal_lsn=admission.baseline_wal_lsn,
        wal_chain_start_lsn=admission.wal_chain_start_lsn,
        base_backup_end_lsn=admission.base_backup_end_lsn,
        completion_attestation_sha256=admission.completion_attestation_sha256,
        writer_epoch=binding.writer_term.writer_epoch,
        writer_lease_id=binding.writer_term.writer_lease_id,
        witnessed_term_proof_sha256=binding.writer_term.witnessed_term_proof_sha256,
        continuity_receipt_id=continuity.continuity_receipt_id,
        continuity_receipt_nonce=continuity.continuity_receipt_nonce,
        continuity_receipt_sha256=continuity.continuity_receipt_sha256,
        continuity_scope_sha256=continuity.scope_sha256,
        continuity_selector_set_sha256=continuity.selector_set_sha256,
        continuity_selector_count=len(continuity.wal_object_selectors),
        target_replay_lsn=continuity.target_lsn,
    )


def _assert_result(
    value: VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight,
    facts: _Facts,
) -> None:
    expected = _result_from_facts(facts)
    for field_name in (
        "schema", "canonical_readback", "readback_evidence_sha256",
        "readback_attestation_sha256", "readback_attestation_id",
        "readback_attestation_nonce", "readback_attestation_scope_sha256",
        "expected_readback_attester_public_key",
        "expected_readback_attester_public_key_sha256",
        "expected_readback_attester_key_id", "observed_at",
        "context_sha256", "source_site", "destination_site", "receiver_site",
        "campaign_id", "release_sha", "binding_sha256", "manifest_id",
        "manifest_sha256", "receipt_id", "receipt_nonce", "session_sha256",
        "finalization_permit_id", "finalization_permit_sha256",
        "committed_chunk_set_sha256", "recovery_admission_scope_sha256",
        "stage_directory_name", "stage_receipt_sha256", "lineage_sha256",
        "snapshot_sha256", "snapshot_bytes", "total_plaintext_sha256",
        "total_plaintext_bytes", "chunk_count", "baseline_generation_id",
        "database_system_identifier", "timeline_id", "wal_segment_size_bytes",
        "baseline_wal_lsn", "wal_chain_start_lsn", "base_backup_end_lsn",
        "completion_attestation_sha256", "writer_epoch", "writer_lease_id",
        "witnessed_term_proof_sha256", "continuity_receipt_id",
        "continuity_receipt_nonce", "continuity_receipt_sha256",
        "continuity_scope_sha256", "continuity_selector_set_sha256",
        "continuity_selector_count", "target_replay_lsn",
    ):
        if getattr(value, field_name) != getattr(expected, field_name):
            _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_TAMPERED")


def mint_physical_postgres_chunked_base_backup_target_recovery_preflight(
    *,
    config: PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig,
    recovery_admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    target_wal_continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    target_wal_continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
    target_wal_continuity_scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    recovery_readback_attestation: VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation,
    now: datetime,
) -> VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight:
    """Mint an opaque V2 target-replay observation without runtime I/O."""

    facts = _facts(
        config=config,
        recovery_admission=recovery_admission,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        target_wal_continuity=target_wal_continuity,
        target_wal_continuity_receipt=target_wal_continuity_receipt,
        target_wal_continuity_scope=target_wal_continuity_scope,
        recovery_readback_attestation=recovery_readback_attestation,
        now=now,
    )
    result = _result_from_facts(facts)
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = _State(
        config=config,
        admission=recovery_admission,
        manifest=manifest,
        handoff=handoff_receipt,
        continuity=target_wal_continuity,
        continuity_receipt=target_wal_continuity_receipt,
        continuity_scope=target_wal_continuity_scope,
        readback_attestation=recovery_readback_attestation,
    )
    _assert_result(result, facts)
    return result


def require_verified_physical_postgres_chunked_base_backup_target_recovery_preflight(
    value: object,
    *,
    config: PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig,
    recovery_admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    target_wal_continuity: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity,
    target_wal_continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
    target_wal_continuity_scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    recovery_readback_attestation: VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation,
    now: datetime,
) -> VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight:
    """Revalidate all V2 pins without opening staged state or contacting a service."""

    if (
        type(value) is not VerifiedPhysicalPostgresChunkedBaseBackupTargetRecoveryPreflight
        or value._capability is not _CAPABILITY
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_CAPABILITY_REQUIRED")
    if (
        config != state.config
        or recovery_admission is not state.admission
        or manifest is not state.manifest
        or handoff_receipt is not state.handoff
        or target_wal_continuity is not state.continuity
        or target_wal_continuity_receipt is not state.continuity_receipt
        or target_wal_continuity_scope != state.continuity_scope
        or recovery_readback_attestation is not state.readback_attestation
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_TARGET_RECOVERY_PREFLIGHT_INPUT_MISMATCH")
    facts = _facts(
        config=config,
        recovery_admission=recovery_admission,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        target_wal_continuity=target_wal_continuity,
        target_wal_continuity_receipt=target_wal_continuity_receipt,
        target_wal_continuity_scope=target_wal_continuity_scope,
        recovery_readback_attestation=recovery_readback_attestation,
        now=now,
    )
    _assert_result(value, facts)
    return value
