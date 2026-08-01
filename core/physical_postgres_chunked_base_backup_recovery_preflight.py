"""Pure V2 PostgreSQL recovery-evidence preflight for one staged base backup.

This module is deliberately an evidence boundary, not a restore adapter.  It
accepts a process-local staged-base-backup admission, an exact still-valid V2
manifest and Witness handoff, plus one bounded canonical PostgreSQL readback
injected by an owning runtime.  It never opens the stage, contacts Object
Storage, starts PostgreSQL, restores data, promotes a writer, or changes Full
Matrix readiness.

The current V2 transfer proves a base-backup endpoint only.  Consequently the
scope requires its target replay LSN to be exactly ``base_backup_end_lsn``.
Any later target needs a separately reviewed V2 WAL-continuity capability;
this preflight intentionally cannot be used to claim it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
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
from core.physical_wal_chunked_base_backup_transfer import (
    PhysicalWalChunkedBaseBackupBinding,
    PhysicalWalChunkedBaseBackupTransferError,
    build_physical_wal_chunked_base_backup_binding,
)


__all__ = (
    "DEFAULT_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_DEFAULT_ENABLED",
    "PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_SCHEMA",
    "PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_SCHEMA",
    "PhysicalPostgresChunkedBaseBackupRecoveryPreflightConfig",
    "PhysicalPostgresChunkedBaseBackupRecoveryPreflightError",
    "PhysicalPostgresChunkedBaseBackupRecoveryPreflightScope",
    "PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence",
    "VerifiedPhysicalPostgresChunkedBaseBackupRecoveryPreflight",
    "require_verified_physical_postgres_chunked_base_backup_recovery_preflight",
    "verify_physical_postgres_chunked_base_backup_recovery_preflight",
)


PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_SCHEMA = (
    "gold-trade-physical-postgres-chunked-base-backup-recovery-preflight-v2"
)
PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_SCHEMA = (
    "gold-trade-physical-postgres-chunked-base-backup-recovery-readback-v2"
)
PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_MAX_EVIDENCE_AGE_SECONDS = 90
MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BYTES = 64 * 1024
MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_FUTURE_SKEW_SECONDS = 5
REQUIRED_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_WAL_SEGMENT_SIZE_BYTES = 16 * 1024 * 1024

_CAPABILITY = object()
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_SITE_RE = re.compile(r"^webapp_(?:fi|ir)$", re.ASCII)
_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)

_READBACK_FIELDS = frozenset(
    {
        "schema",
        "status",
        "observed_at",
        "receiver_site",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "route",
        "writer_term",
        "stage",
        "baseline",
        "target_replay_lsn",
        "postgresql",
    }
)
_ROUTE_FIELDS = frozenset(
    {
        "binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "object_storage_namespace",
        "destination_age_recipient",
        "transport_plane",
        "direct_webapp_transport",
    }
)
_TERM_FIELDS = frozenset(
    {
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
    }
)
_STAGE_FIELDS = frozenset(
    {
        "recovery_admission_scope_sha256",
        "stage_receipt_sha256",
        "receipt_id",
        "receipt_nonce",
        "manifest_id",
        "manifest_sha256",
        "session_sha256",
        "finalization_permit_id",
        "finalization_permit_sha256",
        "committed_chunk_set_sha256",
        "lineage_sha256",
        "snapshot_sha256",
        "snapshot_bytes",
        "total_plaintext_sha256",
        "total_plaintext_bytes",
        "chunk_count",
    }
)
_BASELINE_FIELDS = frozenset(
    {
        "baseline_generation_id",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "completion_attestation_sha256",
        "witness_transition_id",
        "witness_public_key_sha256",
    }
)
_POSTGRES_FIELDS = frozenset(
    {
        "in_recovery",
        "role",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_generation_id",
        "replay_lsn",
    }
)


class PhysicalPostgresChunkedBaseBackupRecoveryPreflightError(ValueError):
    """One V2-only recovery-evidence input is missing, stale, or unbound."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalPostgresChunkedBaseBackupRecoveryPreflightScope:
    """Expected V2 route, lineage, geometry, and exact replay endpoint.

    This is policy, not recovery authority.  Its target is intentionally
    restricted to the V2 base-backup endpoint until a separate continuity
    capability exists for WAL beyond that endpoint.
    """

    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    receiver_site: str
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
    expected_target_replay_lsn: str


@dataclass(frozen=True)
class PhysicalPostgresChunkedBaseBackupRecoveryPreflightConfig:
    """Default-off pure policy; it contains neither paths nor endpoints."""

    scope: PhysicalPostgresChunkedBaseBackupRecoveryPreflightScope | None = None
    enabled: bool = PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_MAX_EVIDENCE_AGE_SECONDS
    )
    local_stage_readback: str = "already-admitted-only"
    direct_site_control: str = "forbidden"
    remote_object_storage: str = "forbidden"
    restore_or_promotion: str = "forbidden"


@dataclass(frozen=True)
class PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence:
    """Bounded canonical PostgreSQL state supplied by an owning runtime."""

    raw_evidence: bytes
    evidence_sha256: str


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalPostgresChunkedBaseBackupRecoveryPreflight:
    """Opaque recovery observation; never a restore, promotion, or writer permit."""

    schema: str
    canonical_readback: bytes
    readback_evidence_sha256: str
    observed_at: datetime
    scope_sha256: str
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
    target_replay_lsn: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ScopeFacts:
    scope: PhysicalPostgresChunkedBaseBackupRecoveryPreflightScope
    binding: PhysicalWalChunkedBaseBackupBinding
    scope_sha256: str


@dataclass(frozen=True)
class _ReadbackFacts:
    raw: bytes
    evidence_sha256: str
    observed_at: datetime


@dataclass(frozen=True)
class _Facts:
    scope: _ScopeFacts
    admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    readback: _ReadbackFacts


@dataclass(frozen=True)
class _State:
    config: PhysicalPostgresChunkedBaseBackupRecoveryPreflightConfig
    admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    readback: PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence


_STATES: WeakKeyDictionary[VerifiedPhysicalPostgresChunkedBaseBackupRecoveryPreflight, _State] = (
    WeakKeyDictionary()
)


def _fail(code: str) -> None:
    raise PhysicalPostgresChunkedBaseBackupRecoveryPreflightError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryPreflightError(code) from exc


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _lease_id(value: object, *, code: str) -> str:
    if type(value) is not str or LEASE_ID_RE.fullmatch(value) is None:
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


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryPreflightError(code) from exc
    rendered = parsed.isoformat().replace("+00:00", "Z")
    if rendered != value:
        _fail(code)
    return parsed


def _fresh(value: object, *, now: datetime, maximum_age: int, code: str) -> datetime:
    observed = _utc(value, code=code)
    if observed > now + timedelta(seconds=MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_FUTURE_SKEW_SECONDS):
        _fail(code)
    if now - observed > timedelta(seconds=maximum_age):
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
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
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
        raise PhysicalPostgresChunkedBaseBackupRecoveryPreflightError(
            "POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID"
        ) from exc
    if normalized != value:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    return normalized


def _scope_facts(value: object) -> _ScopeFacts:
    if type(value) is not PhysicalPostgresChunkedBaseBackupRecoveryPreflightScope:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    binding = _normalise_binding(value.transfer_binding)
    receiver = _site(value.receiver_site, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    lineage = _sha256(value.lineage_sha256, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    generation = _generation(value.baseline_generation_id, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    system_identifier = _system_identifier(
        value.database_system_identifier,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID",
    )
    timeline = _positive(value.timeline_id, maximum=0xFFFFFFFF, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    wal_size = _positive(
        value.wal_segment_size_bytes,
        maximum=2**31 - 1,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID",
    )
    baseline, baseline_value = _lsn(value.baseline_wal_lsn, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    chain_start, chain_start_value = _lsn(value.wal_chain_start_lsn, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    base_end, base_end_value = _lsn(value.base_backup_end_lsn, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    target, target_value = _lsn(value.expected_target_replay_lsn, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    completion = _sha256(value.completion_attestation_sha256, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    transition = _identifier(value.witness_transition_id, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    witness = _sha256(value.witness_public_key_sha256, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    if (
        binding.destination_site != receiver
        or binding.source_site == binding.destination_site
        or binding.writer_term.writer_holder_site != binding.source_site
        or wal_size != REQUIRED_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_WAL_SEGMENT_SIZE_BYTES
        or baseline_value > base_end_value
        or chain_start_value > base_end_value
        or target != base_end
        or target_value != base_end_value
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_MISMATCH")
    normalized_scope = PhysicalPostgresChunkedBaseBackupRecoveryPreflightScope(
        transfer_binding=binding,
        receiver_site=receiver,
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
        expected_target_replay_lsn=target,
    )
    if normalized_scope != value:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")
    payload = {
        "schema": PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_SCHEMA,
        "transfer_binding": _binding_mapping(binding),
        "receiver_site": receiver,
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
        "expected_target_replay_lsn": target,
    }
    return _ScopeFacts(
        scope=normalized_scope,
        binding=binding,
        scope_sha256=hashlib.sha256(_canonical(payload, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_SCOPE_INVALID")).hexdigest(),
    )


def _config_facts(value: object) -> tuple[_ScopeFacts, int]:
    if type(value) is not PhysicalPostgresChunkedBaseBackupRecoveryPreflightConfig:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_CONFIG_INVALID")
    if (
        value.enabled is not True
        or value.local_stage_readback != "already-admitted-only"
        or value.direct_site_control != "forbidden"
        or value.remote_object_storage != "forbidden"
        or value.restore_or_promotion != "forbidden"
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_CONFIG_INVALID")
    maximum = value.maximum_evidence_age_seconds
    if type(maximum) is not int or not 1 <= maximum <= MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_EVIDENCE_AGE_SECONDS:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_CONFIG_INVALID")
    return _scope_facts(value.scope), maximum


def _parse_readback(
    value: object,
    *,
    now: datetime,
    maximum_age: int,
) -> tuple[dict[str, Any], _ReadbackFacts]:
    if type(value) is not PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    raw = value.raw_evidence
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BYTES:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    evidence_sha = _sha256(value.evidence_sha256, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    if hashlib.sha256(raw).hexdigest() != evidence_sha:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_HASH_MISMATCH")
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalPostgresChunkedBaseBackupRecoveryPreflightError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryPreflightError(
            "POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID"
        ) from exc
    item = _exact_mapping(
        parsed,
        fields=_READBACK_FIELDS,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID",
    )
    if _canonical(item, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID") != raw:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_NONCANONICAL")
    if (
        item["schema"] != PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_SCHEMA
        or item["status"] != "replay-evidence-observed"
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    observed = _timestamp(item["observed_at"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    _fresh(observed, now=now, maximum_age=maximum_age, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_STALE")
    return item, _ReadbackFacts(raw=raw, evidence_sha256=evidence_sha, observed_at=observed)


def _assert_admission_manifest_handoff(
    *,
    scope: _ScopeFacts,
    admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    now: datetime,
    maximum_age: int,
) -> None:
    binding = manifest.finalization_permit.session.binding
    permit = manifest.finalization_permit
    manifest_sha = hashlib.sha256(manifest.canonical_manifest).hexdigest()
    permit_sha = hashlib.sha256(permit.canonical_finalization_permit).hexdigest()
    session_sha = hashlib.sha256(permit.session.canonical_session).hexdigest()
    admitted_at = _fresh(
        admission.admitted_at,
        now=now,
        maximum_age=maximum_age,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STALE",
    )
    del admitted_at
    if (
        binding != scope.binding
        or handoff.binding_sha256 != admission.binding_sha256
        or handoff.manifest_id != manifest.manifest_id
        or handoff.manifest_sha256 != manifest_sha
        or handoff.session_sha256 != session_sha
        or handoff.finalization_permit_id != permit.finalization_permit_id
        or handoff.finalization_permit_sha256 != permit_sha
        or handoff.committed_chunk_set_sha256 != permit.committed_chunk_set_sha256
        or handoff.lineage_sha256 != scope.scope.lineage_sha256
        or handoff.baseline_generation_id != scope.scope.baseline_generation_id
        or handoff.database_system_identifier != scope.scope.database_system_identifier
        or handoff.timeline_id != scope.scope.timeline_id
        or handoff.wal_segment_size_bytes != scope.scope.wal_segment_size_bytes
        or handoff.baseline_wal_lsn != scope.scope.baseline_wal_lsn
        or handoff.wal_chain_start_lsn != scope.scope.wal_chain_start_lsn
        or handoff.base_backup_end_lsn != scope.scope.base_backup_end_lsn
        or handoff.completion_attestation_sha256 != scope.scope.completion_attestation_sha256
        or handoff.witness_transition_id != scope.scope.witness_transition_id
        or hashlib.sha256(handoff.witness_public_key).hexdigest()
        != scope.scope.witness_public_key_sha256
        or admission.receiver_site != scope.scope.receiver_site
        or admission.scope_sha256 == "0" * 64
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
        or admission.snapshot_sha256 != manifest.total_plaintext_sha256
        or admission.snapshot_bytes != manifest.total_plaintext_bytes
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
        or admission.witness_public_key_sha256 != hashlib.sha256(handoff.witness_public_key).hexdigest()
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_CROSS_PIN_MISMATCH")


def _assert_readback(
    *,
    item: dict[str, Any],
    scope: _ScopeFacts,
    admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
) -> None:
    binding = scope.binding
    if (
        _site(item["receiver_site"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != scope.scope.receiver_site
        or _site(item["source_site"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != binding.source_site
        or _site(item["destination_site"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != binding.destination_site
        or type(item["campaign_id"]) is not str
        or CAMPAIGN_ID_RE.fullmatch(item["campaign_id"]) is None
        or item["campaign_id"] != binding.campaign_id
        or type(item["release_sha"]) is not str
        or RELEASE_SHA_RE.fullmatch(item["release_sha"]) is None
        or item["release_sha"] != binding.release_sha
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ROUTE_MISMATCH")
    route = _exact_mapping(item["route"], fields=_ROUTE_FIELDS, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    if (
        _sha256(route["binding_sha256"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != handoff.binding_sha256
        or _sha256(route["route_commitment_sha256"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != binding.route_commitment_sha256
        or _sha256(route["four_role_binding_sha256"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != binding.four_role_binding_sha256
        or route["object_storage_namespace"] != binding.object_storage_namespace
        or type(route["destination_age_recipient"]) is not str
        or AGE_RECIPIENT_RE.fullmatch(route["destination_age_recipient"]) is None
        or route["destination_age_recipient"] != binding.destination_age_recipient
        or route["transport_plane"] != binding.transport_plane
        or route["direct_webapp_transport"] != binding.direct_webapp_transport
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ROUTE_MISMATCH")
    term = _exact_mapping(item["writer_term"], fields=_TERM_FIELDS, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    if (
        _site(term["writer_holder_site"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != binding.writer_term.writer_holder_site
        or _positive(term["writer_epoch"], maximum=2**63 - 1, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != binding.writer_term.writer_epoch
        or _lease_id(term["writer_lease_id"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != binding.writer_term.writer_lease_id
        or _sha256(term["witnessed_term_proof_sha256"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
        != binding.writer_term.witnessed_term_proof_sha256
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_TERM_MISMATCH")
    stage = _exact_mapping(item["stage"], fields=_STAGE_FIELDS, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    expected_stage = {
        "recovery_admission_scope_sha256": admission.scope_sha256,
        "stage_receipt_sha256": admission.stage_receipt_sha256,
        "receipt_id": admission.receipt_id,
        "receipt_nonce": admission.receipt_nonce,
        "manifest_id": admission.manifest_id,
        "manifest_sha256": admission.manifest_sha256,
        "session_sha256": admission.session_sha256,
        "finalization_permit_id": admission.finalization_permit_id,
        "finalization_permit_sha256": admission.finalization_permit_sha256,
        "committed_chunk_set_sha256": admission.committed_chunk_set_sha256,
        "lineage_sha256": admission.lineage_sha256,
        "snapshot_sha256": admission.snapshot_sha256,
        "snapshot_bytes": admission.snapshot_bytes,
        "total_plaintext_sha256": admission.total_plaintext_sha256,
        "total_plaintext_bytes": admission.total_plaintext_bytes,
        "chunk_count": admission.chunk_count,
    }
    for key, expected in expected_stage.items():
        actual = stage[key]
        if isinstance(expected, str):
            if key.endswith("_id"):
                checked = _identifier(actual, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_STAGE_MISMATCH")
            elif key.endswith("_nonce"):
                checked = _nonce(actual, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_STAGE_MISMATCH")
            else:
                checked = _sha256(actual, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_STAGE_MISMATCH")
        else:
            checked = _positive(actual, maximum=2**63 - 1, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_STAGE_MISMATCH")
        if checked != expected:
            _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_STAGE_MISMATCH")
    baseline = _exact_mapping(item["baseline"], fields=_BASELINE_FIELDS, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    expected_baseline = {
        "baseline_generation_id": scope.scope.baseline_generation_id,
        "database_system_identifier": scope.scope.database_system_identifier,
        "timeline_id": scope.scope.timeline_id,
        "wal_segment_size_bytes": scope.scope.wal_segment_size_bytes,
        "baseline_wal_lsn": scope.scope.baseline_wal_lsn,
        "wal_chain_start_lsn": scope.scope.wal_chain_start_lsn,
        "base_backup_end_lsn": scope.scope.base_backup_end_lsn,
        "completion_attestation_sha256": scope.scope.completion_attestation_sha256,
        "witness_transition_id": scope.scope.witness_transition_id,
        "witness_public_key_sha256": scope.scope.witness_public_key_sha256,
    }
    for key, expected in expected_baseline.items():
        actual = baseline[key]
        if key == "baseline_generation_id":
            checked = _generation(actual, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BASELINE_MISMATCH")
        elif key == "database_system_identifier":
            checked = _system_identifier(actual, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BASELINE_MISMATCH")
        elif key in {"timeline_id", "wal_segment_size_bytes"}:
            checked = _positive(actual, maximum=2**63 - 1, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BASELINE_MISMATCH")
        elif key in {"baseline_wal_lsn", "wal_chain_start_lsn", "base_backup_end_lsn"}:
            checked, _unused = _lsn(actual, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BASELINE_MISMATCH")
        elif key == "witness_transition_id":
            checked = _identifier(actual, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BASELINE_MISMATCH")
        else:
            checked = _sha256(actual, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BASELINE_MISMATCH")
        if checked != expected:
            _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BASELINE_MISMATCH")
    target, _target_value = _lsn(item["target_replay_lsn"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    if target != scope.scope.expected_target_replay_lsn:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_TARGET_MISMATCH")
    postgres = _exact_mapping(item["postgresql"], fields=_POSTGRES_FIELDS, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_INVALID")
    if (
        postgres["in_recovery"] is not True
        or postgres["role"] != "standby"
        or _system_identifier(postgres["database_system_identifier"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_POSTGRES_MISMATCH")
        != scope.scope.database_system_identifier
        or _positive(postgres["timeline_id"], maximum=0xFFFFFFFF, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_POSTGRES_MISMATCH")
        != scope.scope.timeline_id
        or _positive(postgres["wal_segment_size_bytes"], maximum=2**31 - 1, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_POSTGRES_MISMATCH")
        != scope.scope.wal_segment_size_bytes
        or _generation(postgres["baseline_generation_id"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_POSTGRES_MISMATCH")
        != scope.scope.baseline_generation_id
        or _lsn(postgres["replay_lsn"], code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_POSTGRES_MISMATCH")[0]
        != target
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_POSTGRES_MISMATCH")


def _facts(
    *,
    config: object,
    recovery_admission: object,
    manifest: object,
    handoff_receipt: object,
    receiver_readback: object,
    now: datetime,
) -> _Facts:
    scope, maximum_age = _config_facts(config)
    observed_now = _utc(now, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_CLOCK_INVALID")
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
    except PhysicalPostgresChunkedBaseBackupRecoveryPreflightError:
        raise
    except Exception as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryPreflightError(
            "POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_V2_CAPABILITY_INVALID"
        ) from exc
    _assert_admission_manifest_handoff(
        scope=scope,
        admission=admission,
        manifest=verified_manifest,
        handoff=handoff,
        now=observed_now,
        maximum_age=maximum_age,
    )
    readback_item, readback = _parse_readback(
        receiver_readback,
        now=observed_now,
        maximum_age=maximum_age,
    )
    _assert_readback(
        item=readback_item,
        scope=scope,
        admission=admission,
        manifest=verified_manifest,
        handoff=handoff,
    )
    return _Facts(
        scope=scope,
        admission=admission,
        manifest=verified_manifest,
        handoff=handoff,
        readback=readback,
    )


def _result_from_facts(facts: _Facts) -> VerifiedPhysicalPostgresChunkedBaseBackupRecoveryPreflight:
    binding = facts.scope.binding
    admission = facts.admission
    return VerifiedPhysicalPostgresChunkedBaseBackupRecoveryPreflight(
        schema=PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_SCHEMA,
        canonical_readback=facts.readback.raw,
        readback_evidence_sha256=facts.readback.evidence_sha256,
        observed_at=facts.readback.observed_at,
        scope_sha256=facts.scope.scope_sha256,
        source_site=binding.source_site,
        destination_site=binding.destination_site,
        receiver_site=facts.scope.scope.receiver_site,
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
        target_replay_lsn=facts.scope.scope.expected_target_replay_lsn,
    )


def _assert_result(
    value: VerifiedPhysicalPostgresChunkedBaseBackupRecoveryPreflight,
    facts: _Facts,
) -> None:
    expected = _result_from_facts(facts)
    for field_name in (
        "schema",
        "canonical_readback",
        "readback_evidence_sha256",
        "observed_at",
        "scope_sha256",
        "source_site",
        "destination_site",
        "receiver_site",
        "campaign_id",
        "release_sha",
        "binding_sha256",
        "manifest_id",
        "manifest_sha256",
        "receipt_id",
        "receipt_nonce",
        "session_sha256",
        "finalization_permit_id",
        "finalization_permit_sha256",
        "committed_chunk_set_sha256",
        "recovery_admission_scope_sha256",
        "stage_receipt_sha256",
        "lineage_sha256",
        "snapshot_sha256",
        "snapshot_bytes",
        "total_plaintext_sha256",
        "total_plaintext_bytes",
        "chunk_count",
        "baseline_generation_id",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "completion_attestation_sha256",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "target_replay_lsn",
    ):
        if getattr(value, field_name) != getattr(expected, field_name):
            _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_TAMPERED")


def verify_physical_postgres_chunked_base_backup_recovery_preflight(
    *,
    config: PhysicalPostgresChunkedBaseBackupRecoveryPreflightConfig,
    recovery_admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    receiver_readback: PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence,
    now: datetime,
) -> VerifiedPhysicalPostgresChunkedBaseBackupRecoveryPreflight:
    """Mint one opaque V2-only recovery observation without runtime I/O."""

    facts = _facts(
        config=config,
        recovery_admission=recovery_admission,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        receiver_readback=receiver_readback,
        now=now,
    )
    result = _result_from_facts(facts)
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = _State(
        config=config,
        admission=recovery_admission,
        manifest=manifest,
        handoff=handoff_receipt,
        readback=receiver_readback,
    )
    _assert_result(result, facts)
    return result


def require_verified_physical_postgres_chunked_base_backup_recovery_preflight(
    value: object,
    *,
    config: PhysicalPostgresChunkedBaseBackupRecoveryPreflightConfig,
    recovery_admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    receiver_readback: PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence,
    now: datetime,
) -> VerifiedPhysicalPostgresChunkedBaseBackupRecoveryPreflight:
    """Revalidate a pure V2 recovery observation without opening local state."""

    if (
        type(value) is not VerifiedPhysicalPostgresChunkedBaseBackupRecoveryPreflight
        or value._capability is not _CAPABILITY
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_CAPABILITY_REQUIRED")
    if (
        config != state.config
        or recovery_admission is not state.admission
        or manifest is not state.manifest
        or handoff_receipt is not state.handoff
        or receiver_readback != state.readback
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_PREFLIGHT_INPUT_MISMATCH")
    facts = _facts(
        config=config,
        recovery_admission=recovery_admission,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        receiver_readback=receiver_readback,
        now=now,
    )
    _assert_result(value, facts)
    return value
