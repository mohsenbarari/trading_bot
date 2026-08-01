"""Fail-closed local collector for PostgreSQL recovery receiver readback.

This is an observation adapter, not a recovery executor.  It validates a
root-owned, route-pinned policy plus the already verified Object-Storage
bundle, stage receipt pins, and Witness-term lineage *before* it invokes one
narrow injected local inspector.  The inspector receives an internally built
request only; it never receives a caller-supplied SQL string, path, command,
environment, host, URL, credential, or restore instruction.

The collector converts the bounded typed inspection into the exact canonical
``PhysicalPostgresRecoveryReceiverReadbackEvidence`` envelope accepted by the
pure recovery preflight.  It never restores, starts, stops, promotes, or
connects PostgreSQL, Docker, SSH, a network, Object Storage, or deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Protocol

from core.append_only_sync_delta_batch import (
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_verified_object_delta_role_matrix_witnessed_term,
)
from core.physical_postgres_recovery_preflight import (
    DEFAULT_MAX_RECOVERY_EVIDENCE_AGE_SECONDS,
    MAX_PHYSICAL_POSTGRES_RECOVERY_READBACK_BYTES,
    PHYSICAL_POSTGRES_RECOVERY_RECEIVER_READBACK_SCHEMA,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED,
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryReceiverReadbackEvidence,
    PhysicalPostgresRecoveryStageBinding,
    assess_physical_postgres_recovery_preflight,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalObjectStorageBundle,
    require_verified_physical_wal_object_storage_bundle,
)


__all__ = (
    "DEFAULT_PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_MAX_AGE_SECONDS",
    "PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_DEFAULT_ENABLED",
    "PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_IDENTITY",
    "PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_INSPECTION_CONTRACT",
    "PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_RECEIVER_ROLE",
    "PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_SCHEMA",
    "DisabledPhysicalPostgresRecoveryLocalInspector",
    "PhysicalPostgresRecoveryLocalInspection",
    "PhysicalPostgresRecoveryReadbackCollectorError",
    "PhysicalPostgresRecoveryReadbackInspectionRequest",
    "PhysicalPostgresRecoveryReadbackLocalInspector",
    "PhysicalPostgresRecoveryReadbackRootConfig",
    "collect_physical_postgres_recovery_receiver_readback",
)


PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_SCHEMA = (
    "gold-trade-physical-postgres-recovery-readback-collector-v1"
)
PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_DEFAULT_ENABLED = False
PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_IDENTITY = (
    "root-owned-postgres-recovery-readback-collector-v1"
)
PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_INSPECTION_CONTRACT = (
    "fixed-root-owned-postgres-recovery-inspection-v1"
)
PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_RECEIVER_ROLE = "standby"
DEFAULT_PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_MAX_AGE_SECONDS = (
    DEFAULT_MAX_RECOVERY_EVIDENCE_AGE_SECONDS
)

_MAX_EVIDENCE_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_REQUIRED_WAL_SEGMENT_SIZE_BYTES = 16 * 1024 * 1024
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_TRANSITION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)


class PhysicalPostgresRecoveryReadbackCollectorError(ValueError):
    """The local recovery readback cannot safely be collected."""


@dataclass(frozen=True)
class PhysicalPostgresRecoveryReadbackRootConfig:
    """Root-bootstrap policy with one exact standby route and stage pin set.

    This object intentionally has no command, SQL, path, environment, URL, or
    credential field.  The bootstrap that constructs it must separately obtain
    it from a root-owned source; this pure collector can only validate its
    explicit owner marker and fixed safe identities.
    """

    schema: str = PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_SCHEMA
    enabled: bool = PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_DEFAULT_ENABLED
    root_owner_uid: int = 0
    collector_identity: str = PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_IDENTITY
    inspection_contract: str = PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_INSPECTION_CONTRACT
    receiver_role: str = PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_RECEIVER_ROLE
    source_site: str = ""
    receiver_site: str = ""
    stage_bundle_id: str = ""
    stage_receipt_sha256: str = ""
    route_binding_sha256: str = ""
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_MAX_AGE_SECONDS
    )


@dataclass(frozen=True)
class PhysicalPostgresRecoveryReadbackInspectionRequest:
    """Exact non-secret receiver facts the internal inspector must echo."""

    source_site: str
    receiver_site: str
    destination_site: str
    stage_bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str
    bundle_terminal_wal_lsn: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int


@dataclass(frozen=True)
class PhysicalPostgresRecoveryLocalInspection:
    """Narrow local observation; it carries no SQL, path, command, or secret."""

    observed_at: datetime
    receiver_site: str
    source_site: str
    destination_site: str
    stage_bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str
    bundle_terminal_wal_lsn: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    in_recovery: bool
    role: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_generation_id: str
    replay_lsn: str


class PhysicalPostgresRecoveryReadbackLocalInspector(Protocol):
    """One fixed local observation interface, deliberately not an executor."""

    def inspect_bound_recovery_receiver(
        self,
        *,
        request: PhysicalPostgresRecoveryReadbackInspectionRequest,
    ) -> PhysicalPostgresRecoveryLocalInspection:
        """Return only the bounded readback for the collector's fixed request."""


class DisabledPhysicalPostgresRecoveryLocalInspector:
    """Safe default for a root bootstrap before a reviewed local observer exists."""

    def inspect_bound_recovery_receiver(
        self,
        *,
        request: PhysicalPostgresRecoveryReadbackInspectionRequest,
    ) -> PhysicalPostgresRecoveryLocalInspection:
        del request
        raise PhysicalPostgresRecoveryReadbackCollectorError("LOCAL_INSPECTOR_DISABLED")


@dataclass(frozen=True)
class _RootConfigFacts:
    source_site: str
    receiver_site: str
    stage_bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str
    maximum_evidence_age_seconds: int


@dataclass(frozen=True)
class _StageFacts:
    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str


@dataclass(frozen=True)
class _TermFacts:
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    proof_sha256: str


@dataclass(frozen=True)
class _BundleFacts:
    bundle: VerifiedPhysicalWalObjectStorageBundle
    source_site: str
    destination_site: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    base_backup_manifest_sha256: str
    terminal_wal_lsn: str
    terminal_wal_lsn_value: int
    manifest_sha256es: tuple[str, ...]
    object_versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _InspectionFacts:
    observed_at: datetime
    replay_lsn: str
    replay_lsn_value: int
    in_recovery: bool
    role: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_generation_id: str


def _fail(code: str) -> None:
    raise PhysicalPostgresRecoveryReadbackCollectorError(code)


def _sha256(value: object, *, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _site(value: object, *, code: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        _fail(code)
    return value


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    return value


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _root_config(value: object) -> _RootConfigFacts:
    if type(value) is not PhysicalPostgresRecoveryReadbackRootConfig:
        _fail("RECOVERY_READBACK_ROOT_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("RECOVERY_READBACK_COLLECTOR_DISABLED")
    if value.schema != PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_SCHEMA:
        _fail("RECOVERY_READBACK_ROOT_CONFIG_INVALID")
    if type(value.root_owner_uid) is not int or value.root_owner_uid != 0:
        _fail("RECOVERY_READBACK_ROOT_CONFIG_NOT_ROOT")
    if value.collector_identity != PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_IDENTITY:
        _fail("RECOVERY_READBACK_ROOT_CONFIG_IDENTITY_INVALID")
    if value.inspection_contract != PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_INSPECTION_CONTRACT:
        _fail("RECOVERY_READBACK_ROOT_CONFIG_INSPECTION_CONTRACT_INVALID")
    if value.receiver_role != PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_RECEIVER_ROLE:
        _fail("RECOVERY_READBACK_ROOT_CONFIG_RECEIVER_ROLE_INVALID")
    source_site = _site(value.source_site, code="RECOVERY_READBACK_ROOT_CONFIG_ROUTE_INVALID")
    receiver_site = _site(value.receiver_site, code="RECOVERY_READBACK_ROOT_CONFIG_ROUTE_INVALID")
    if source_site == receiver_site:
        _fail("RECOVERY_READBACK_ROOT_CONFIG_ROUTE_INVALID")
    maximum_age = _positive_int(
        value.maximum_evidence_age_seconds,
        maximum=_MAX_EVIDENCE_AGE_SECONDS,
        code="RECOVERY_READBACK_ROOT_CONFIG_AGE_INVALID",
    )
    return _RootConfigFacts(
        source_site=source_site,
        receiver_site=receiver_site,
        stage_bundle_id=_sha256(value.stage_bundle_id, code="RECOVERY_READBACK_ROOT_CONFIG_STAGE_PIN_INVALID"),
        stage_receipt_sha256=_sha256(
            value.stage_receipt_sha256,
            code="RECOVERY_READBACK_ROOT_CONFIG_STAGE_PIN_INVALID",
        ),
        route_binding_sha256=_sha256(
            value.route_binding_sha256,
            code="RECOVERY_READBACK_ROOT_CONFIG_STAGE_PIN_INVALID",
        ),
        maximum_evidence_age_seconds=maximum_age,
    )


def _stage(value: object) -> _StageFacts:
    if type(value) is not PhysicalPostgresRecoveryStageBinding:
        _fail("RECOVERY_READBACK_STAGE_BINDING_INVALID")
    return _StageFacts(
        bundle_id=_sha256(value.bundle_id, code="RECOVERY_READBACK_STAGE_BINDING_INVALID"),
        stage_receipt_sha256=_sha256(
            value.stage_receipt_sha256,
            code="RECOVERY_READBACK_STAGE_BINDING_INVALID",
        ),
        route_binding_sha256=_sha256(
            value.route_binding_sha256,
            code="RECOVERY_READBACK_STAGE_BINDING_INVALID",
        ),
    )


def _term(value: object, *, now: datetime, code: str) -> _TermFacts:
    try:
        term = require_verified_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail(code)
    return _TermFacts(
        holder_site=_site(term.holder_site, code=code),
        writer_epoch=_positive_int(term.writer_epoch, maximum=2**63 - 1, code=code),
        writer_lease_id=_text(term.writer_lease_id, pattern=LEASE_ID_RE, code=code),
        witness_transition_id=_text(
            term.witness_transition_id,
            pattern=_TRANSITION_ID_RE,
            code=code,
        ),
        proof_sha256=_sha256(term.proof_sha256, code=code),
    )


def _binding(
    value: object,
    *,
    current_term: _TermFacts,
    now: datetime,
) -> tuple[str, _StageFacts, _TermFacts]:
    if type(value) is not PhysicalPostgresRecoveryPreflightBinding:
        _fail("RECOVERY_READBACK_PREFLIGHT_BINDING_INVALID")
    local_standby = _site(
        value.local_standby_site,
        code="RECOVERY_READBACK_PREFLIGHT_BINDING_INVALID",
    )
    stage = _stage(value.stage_binding)
    expected_term = _term(
        value.expected_witnessed_term,
        now=now,
        code="RECOVERY_READBACK_EXPECTED_TERM_INVALID",
    )
    if expected_term != current_term:
        _fail("RECOVERY_READBACK_CURRENT_TERM_MISMATCH")
    return local_standby, stage, expected_term


def _object_versions(bundle: VerifiedPhysicalWalObjectStorageBundle) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = [
        (
            bundle.baseline.base_backup_object.object_key,
            bundle.baseline.base_backup_object.version_id,
        )
    ]
    for manifest in bundle.wal_manifests:
        pairs.extend((segment.object.object_key, segment.object.version_id) for segment in manifest.segments)
    pairs.extend(
        (shard.object.object_key, shard.object.version_id)
        for shard in bundle.blob_frontier.inventory_shards
    )
    checked: list[tuple[str, str]] = []
    for object_key, version_id in pairs:
        key = _text(object_key, pattern=OBJECT_KEY_RE, code="RECOVERY_READBACK_BUNDLE_OBJECT_INVALID")
        version = _text(
            version_id,
            pattern=VERSION_ID_RE,
            code="RECOVERY_READBACK_BUNDLE_OBJECT_INVALID",
        )
        if not key.endswith(".age") or version.casefold() in {"null", "none", "latest", "current"}:
            _fail("RECOVERY_READBACK_BUNDLE_OBJECT_INVALID")
        checked.append((key, version))
    normalized = tuple(checked)
    if not normalized or len(set(normalized)) != len(normalized):
        _fail("RECOVERY_READBACK_BUNDLE_OBJECT_INVALID")
    return normalized


def _bundle(value: object, *, current_term: _TermFacts) -> _BundleFacts:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(value)
    except (PhysicalWalObjectManifestError, AttributeError, TypeError):
        _fail("RECOVERY_READBACK_BUNDLE_UNVERIFIED")
    baseline = bundle.baseline
    source_site = _site(baseline.source_site, code="RECOVERY_READBACK_BUNDLE_ROUTE_INVALID")
    destination_site = _site(
        baseline.destination_site,
        code="RECOVERY_READBACK_BUNDLE_ROUTE_INVALID",
    )
    if source_site == destination_site:
        _fail("RECOVERY_READBACK_BUNDLE_ROUTE_INVALID")
    if (
        baseline.writer_term.epoch != current_term.writer_epoch
        or baseline.writer_term.lease_id != current_term.writer_lease_id
        or baseline.writer_term.witnessed_term_proof_sha256 != current_term.proof_sha256
        or current_term.holder_site != source_site
    ):
        _fail("RECOVERY_READBACK_BUNDLE_CURRENT_TERM_MISMATCH")
    timeline = _positive_int(
        baseline.timeline_id,
        maximum=0xFFFFFFFF,
        code="RECOVERY_READBACK_BUNDLE_BASELINE_INVALID",
    )
    wal_segment_size = _positive_int(
        baseline.wal_segment_size_bytes,
        maximum=2**31 - 1,
        code="RECOVERY_READBACK_BUNDLE_BASELINE_INVALID",
    )
    if (
        wal_segment_size != _REQUIRED_WAL_SEGMENT_SIZE_BYTES
        or wal_segment_size not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES
    ):
        _fail("RECOVERY_READBACK_BUNDLE_BASELINE_INVALID")
    terminal_lsn, terminal_value = _lsn(
        bundle.terminal_wal_lsn,
        code="RECOVERY_READBACK_BUNDLE_TERMINAL_LSN_INVALID",
    )
    manifests = tuple(
        _sha256(item, code="RECOVERY_READBACK_BUNDLE_MANIFEST_INVALID")
        for item in bundle.manifest_sha256es
    )
    if not manifests or len(set(manifests)) != len(manifests):
        _fail("RECOVERY_READBACK_BUNDLE_MANIFEST_INVALID")
    return _BundleFacts(
        bundle=bundle,
        source_site=source_site,
        destination_site=destination_site,
        baseline_generation_id=_text(
            baseline.baseline_generation_id,
            pattern=STREAM_GENERATION_ID_RE,
            code="RECOVERY_READBACK_BUNDLE_BASELINE_INVALID",
        ),
        database_system_identifier=_text(
            baseline.database_system_identifier,
            pattern=_SYSTEM_IDENTIFIER_RE,
            code="RECOVERY_READBACK_BUNDLE_BASELINE_INVALID",
        ),
        timeline_id=timeline,
        wal_segment_size_bytes=wal_segment_size,
        base_backup_manifest_sha256=_sha256(
            baseline.manifest_sha256,
            code="RECOVERY_READBACK_BUNDLE_BASELINE_INVALID",
        ),
        terminal_wal_lsn=terminal_lsn,
        terminal_wal_lsn_value=terminal_value,
        manifest_sha256es=manifests,
        object_versions=_object_versions(bundle),
    )


def _request(
    *,
    root: _RootConfigFacts,
    stage: _StageFacts,
    term: _TermFacts,
    bundle: _BundleFacts,
) -> PhysicalPostgresRecoveryReadbackInspectionRequest:
    return PhysicalPostgresRecoveryReadbackInspectionRequest(
        source_site=bundle.source_site,
        receiver_site=root.receiver_site,
        destination_site=bundle.destination_site,
        stage_bundle_id=stage.bundle_id,
        stage_receipt_sha256=stage.stage_receipt_sha256,
        route_binding_sha256=stage.route_binding_sha256,
        bundle_terminal_wal_lsn=bundle.terminal_wal_lsn,
        writer_holder_site=term.holder_site,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witness_transition_id=term.witness_transition_id,
        witnessed_term_proof_sha256=term.proof_sha256,
        baseline_generation_id=bundle.baseline_generation_id,
        database_system_identifier=bundle.database_system_identifier,
        timeline_id=bundle.timeline_id,
        wal_segment_size_bytes=bundle.wal_segment_size_bytes,
    )


def _inspection(
    value: object,
    *,
    request: PhysicalPostgresRecoveryReadbackInspectionRequest,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> _InspectionFacts:
    if type(value) is not PhysicalPostgresRecoveryLocalInspection:
        _fail("LOCAL_INSPECTION_INVALID")
    observed_at = _utc(value.observed_at, code="LOCAL_INSPECTION_TIME_INVALID")
    if observed_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        _fail("LOCAL_INSPECTION_TIME_INVALID")
    if now - observed_at > timedelta(seconds=maximum_evidence_age_seconds):
        _fail("LOCAL_INSPECTION_TIME_STALE")
    if (
        _site(value.receiver_site, code="LOCAL_INSPECTION_ROUTE_OR_STAGE_MISMATCH")
        != request.receiver_site
        or _site(value.source_site, code="LOCAL_INSPECTION_ROUTE_OR_STAGE_MISMATCH")
        != request.source_site
        or _site(value.destination_site, code="LOCAL_INSPECTION_ROUTE_OR_STAGE_MISMATCH")
        != request.destination_site
        or _sha256(value.stage_bundle_id, code="LOCAL_INSPECTION_ROUTE_OR_STAGE_MISMATCH")
        != request.stage_bundle_id
        or _sha256(value.stage_receipt_sha256, code="LOCAL_INSPECTION_ROUTE_OR_STAGE_MISMATCH")
        != request.stage_receipt_sha256
        or _sha256(value.route_binding_sha256, code="LOCAL_INSPECTION_ROUTE_OR_STAGE_MISMATCH")
        != request.route_binding_sha256
    ):
        _fail("LOCAL_INSPECTION_ROUTE_OR_STAGE_MISMATCH")
    terminal_lsn, _terminal_value = _lsn(
        value.bundle_terminal_wal_lsn,
        code="LOCAL_INSPECTION_TERMINAL_LSN_INVALID",
    )
    if terminal_lsn != request.bundle_terminal_wal_lsn:
        _fail("LOCAL_INSPECTION_TERMINAL_LSN_MISMATCH")
    if (
        _site(value.writer_holder_site, code="LOCAL_INSPECTION_TERM_MISMATCH")
        != request.writer_holder_site
        or _positive_int(value.writer_epoch, maximum=2**63 - 1, code="LOCAL_INSPECTION_TERM_MISMATCH")
        != request.writer_epoch
        or _text(value.writer_lease_id, pattern=LEASE_ID_RE, code="LOCAL_INSPECTION_TERM_MISMATCH")
        != request.writer_lease_id
        or _text(
            value.witness_transition_id,
            pattern=_TRANSITION_ID_RE,
            code="LOCAL_INSPECTION_TERM_MISMATCH",
        )
        != request.witness_transition_id
        or _sha256(value.witnessed_term_proof_sha256, code="LOCAL_INSPECTION_TERM_MISMATCH")
        != request.witnessed_term_proof_sha256
    ):
        _fail("LOCAL_INSPECTION_TERM_MISMATCH")
    if type(value.in_recovery) is not bool:
        _fail("LOCAL_INSPECTION_RECOVERY_STATE_INVALID")
    if (
        value.in_recovery is not True
        or value.role != PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_RECEIVER_ROLE
    ):
        _fail("LOCAL_INSPECTION_NOT_STANDBY")
    system_identifier = _text(
        value.database_system_identifier,
        pattern=_SYSTEM_IDENTIFIER_RE,
        code="LOCAL_INSPECTION_POSTGRES_INVALID",
    )
    timeline_id = _positive_int(
        value.timeline_id,
        maximum=0xFFFFFFFF,
        code="LOCAL_INSPECTION_POSTGRES_INVALID",
    )
    wal_segment_size = _positive_int(
        value.wal_segment_size_bytes,
        maximum=2**31 - 1,
        code="LOCAL_INSPECTION_POSTGRES_INVALID",
    )
    baseline_generation_id = _text(
        value.baseline_generation_id,
        pattern=STREAM_GENERATION_ID_RE,
        code="LOCAL_INSPECTION_POSTGRES_INVALID",
    )
    if (
        system_identifier != request.database_system_identifier
        or timeline_id != request.timeline_id
        or wal_segment_size != request.wal_segment_size_bytes
        or baseline_generation_id != request.baseline_generation_id
    ):
        _fail("LOCAL_INSPECTION_POSTGRES_BINDING_MISMATCH")
    replay_lsn, replay_value = _lsn(value.replay_lsn, code="LOCAL_INSPECTION_REPLAY_LSN_INVALID")
    return _InspectionFacts(
        observed_at=observed_at,
        replay_lsn=replay_lsn,
        replay_lsn_value=replay_value,
        in_recovery=True,
        role=PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_RECEIVER_ROLE,
        database_system_identifier=system_identifier,
        timeline_id=timeline_id,
        wal_segment_size_bytes=wal_segment_size,
        baseline_generation_id=baseline_generation_id,
    )


def _raw_evidence(
    *,
    request: PhysicalPostgresRecoveryReadbackInspectionRequest,
    bundle: _BundleFacts,
    inspection: _InspectionFacts,
) -> bytes:
    status = (
        PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED
        if inspection.replay_lsn_value >= bundle.terminal_wal_lsn_value
        else PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED
    )
    payload = {
        "schema": PHYSICAL_POSTGRES_RECOVERY_RECEIVER_READBACK_SCHEMA,
        "status": status,
        "observed_at": inspection.observed_at.isoformat(),
        "receiver_site": request.receiver_site,
        "source_site": request.source_site,
        "destination_site": request.destination_site,
        "stage_bundle_id": request.stage_bundle_id,
        "stage_receipt_sha256": request.stage_receipt_sha256,
        "route_binding_sha256": request.route_binding_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "object_versions": [
            {"object_key": object_key, "version_id": version_id}
            for object_key, version_id in bundle.object_versions
        ],
        "base_backup_manifest_sha256": bundle.base_backup_manifest_sha256,
        "bundle_terminal_wal_lsn": bundle.terminal_wal_lsn,
        "writer_term": {
            "holder_site": request.writer_holder_site,
            "writer_epoch": request.writer_epoch,
            "writer_lease_id": request.writer_lease_id,
            "witness_transition_id": request.witness_transition_id,
            "witnessed_term_proof_sha256": request.witnessed_term_proof_sha256,
        },
        "postgresql": {
            "in_recovery": inspection.in_recovery,
            "role": inspection.role,
            "database_system_identifier": inspection.database_system_identifier,
            "timeline_id": inspection.timeline_id,
            "wal_segment_size_bytes": inspection.wal_segment_size_bytes,
            "baseline_generation_id": inspection.baseline_generation_id,
            "replay_lsn": inspection.replay_lsn,
        },
    }
    try:
        raw = canonical_json_bytes(payload)
    except (TypeError, ValueError):  # pragma: no cover - normalized fields above.
        _fail("RECOVERY_READBACK_EVIDENCE_SERIALIZATION_INVALID")
    if not 1 <= len(raw) <= MAX_PHYSICAL_POSTGRES_RECOVERY_READBACK_BYTES:
        _fail("RECOVERY_READBACK_EVIDENCE_BYTES_INVALID")
    return raw


def collect_physical_postgres_recovery_receiver_readback(
    *,
    root_config: PhysicalPostgresRecoveryReadbackRootConfig,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    binding: PhysicalPostgresRecoveryPreflightBinding,
    current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    inspector: PhysicalPostgresRecoveryReadbackLocalInspector,
    now: datetime,
) -> PhysicalPostgresRecoveryReceiverReadbackEvidence:
    """Collect one exact non-authorizing local recovery observation.

    Every configuration, stage, term, and bundle check precedes the sole
    inspector call. This function neither opens nor supplies an execution
    boundary for restore/start/promote work.
    """

    root = _root_config(root_config)
    observed_now = _utc(now, code="RECOVERY_READBACK_CLOCK_INVALID")
    current_term = _term(
        current_witnessed_term,
        now=observed_now,
        code="RECOVERY_READBACK_CURRENT_TERM_INVALID",
    )
    local_standby, stage, expected_term = _binding(
        binding,
        current_term=current_term,
        now=observed_now,
    )
    bundle_facts = _bundle(bundle, current_term=expected_term)
    if (
        root.source_site != bundle_facts.source_site
        or root.receiver_site != bundle_facts.destination_site
        or root.receiver_site != local_standby
    ):
        _fail("RECOVERY_READBACK_ROOT_CONFIG_ROUTE_PIN_MISMATCH")
    if (
        root.stage_bundle_id != stage.bundle_id
        or root.stage_receipt_sha256 != stage.stage_receipt_sha256
        or root.route_binding_sha256 != stage.route_binding_sha256
    ):
        _fail("RECOVERY_READBACK_STAGE_PIN_MISMATCH")
    request = _request(
        root=root,
        stage=stage,
        term=expected_term,
        bundle=bundle_facts,
    )
    inspect = getattr(inspector, "inspect_bound_recovery_receiver", None)
    if not callable(inspect):
        _fail("LOCAL_INSPECTOR_INVALID")
    try:
        local = inspect(request=request)
    except PhysicalPostgresRecoveryReadbackCollectorError:
        raise
    except Exception:
        _fail("LOCAL_INSPECTOR_FAILED")
    inspection = _inspection(
        local,
        request=request,
        now=observed_now,
        maximum_evidence_age_seconds=root.maximum_evidence_age_seconds,
    )
    raw = _raw_evidence(
        request=request,
        bundle=bundle_facts,
        inspection=inspection,
    )
    evidence = PhysicalPostgresRecoveryReceiverReadbackEvidence(
        raw_evidence=raw,
        evidence_sha256=hashlib.sha256(raw).hexdigest(),
    )
    preflight = assess_physical_postgres_recovery_preflight(
        bundle=bundle_facts.bundle,
        binding=binding,
        receiver_readback_evidence=evidence,
        now=observed_now,
        maximum_evidence_age_seconds=root.maximum_evidence_age_seconds,
    )
    expected_status = (
        PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED
        if inspection.replay_lsn_value >= bundle_facts.terminal_wal_lsn_value
        else PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED
    )
    if (
        preflight.status == PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED
        or preflight.status != expected_status
        or preflight.evidence_sha256 != evidence.evidence_sha256
    ):
        _fail("COLLECTED_RECOVERY_READBACK_PREFLIGHT_REJECTED")
    return evidence
