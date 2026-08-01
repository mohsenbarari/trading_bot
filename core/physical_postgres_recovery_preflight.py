"""Pure, default-off recovery-evidence admission for a physical PostgreSQL standby.

This module is deliberately one step *after* local receiver staging.  It
accepts an already-verified physical Object-Storage bundle, a typed projection
of the local staging receipt (never a path), a signed Witness-term projection,
and bounded canonical PostgreSQL receiver readback supplied by an external
adapter.  It only decides whether that evidence describes a standby that has
replayed the exact staged bundle terminal frontier.

It does not restore data, start a database, change recovery state, promote a
standby, contact any service, or issue writer authority.  In particular,
``replay-evidence-observed`` is evidence observation only, never a promotion
permit or an acknowledgement claim.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

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
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalObjectStorageBundle,
    require_verified_physical_wal_object_storage_bundle,
)


__all__ = (
    "DEFAULT_MAX_RECOVERY_EVIDENCE_AGE_SECONDS",
    "MAX_PHYSICAL_POSTGRES_RECOVERY_READBACK_BYTES",
    "PHYSICAL_POSTGRES_RECOVERY_PREFLIGHT_DEFAULT_ENABLED",
    "PHYSICAL_POSTGRES_RECOVERY_PREFLIGHT_SCHEMA",
    "PHYSICAL_POSTGRES_RECOVERY_RECEIVER_READBACK_SCHEMA",
    "PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED",
    "PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED",
    "PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED",
    "PhysicalPostgresRecoveryPreflightBinding",
    "PhysicalPostgresRecoveryPreflightError",
    "PhysicalPostgresRecoveryPreflightResult",
    "PhysicalPostgresRecoveryReceiverReadbackEvidence",
    "PhysicalPostgresRecoveryStageBinding",
    "assess_physical_postgres_recovery_preflight",
)


PHYSICAL_POSTGRES_RECOVERY_PREFLIGHT_SCHEMA = (
    "gold-trade-physical-postgres-recovery-preflight-v1"
)
PHYSICAL_POSTGRES_RECOVERY_RECEIVER_READBACK_SCHEMA = (
    "gold-trade-physical-postgres-recovery-receiver-readback-v1"
)
PHYSICAL_POSTGRES_RECOVERY_PREFLIGHT_DEFAULT_ENABLED = False

PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED = (
    "staged-not-replay-verified"
)
PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED = (
    "replay-evidence-observed"
)
PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED = "blocked"

MAX_PHYSICAL_POSTGRES_RECOVERY_READBACK_BYTES = 64 * 1024
DEFAULT_MAX_RECOVERY_EVIDENCE_AGE_SECONDS = 90
MAX_RECOVERY_EVIDENCE_AGE_SECONDS = 300
MAX_RECOVERY_EVIDENCE_FUTURE_SKEW_SECONDS = 5
REQUIRED_WAL_SEGMENT_SIZE_BYTES = 16 * 1024 * 1024

_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$")
_EVIDENCE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "observed_at",
        "receiver_site",
        "source_site",
        "destination_site",
        "stage_bundle_id",
        "stage_receipt_sha256",
        "route_binding_sha256",
        "manifest_sha256es",
        "object_versions",
        "base_backup_manifest_sha256",
        "bundle_terminal_wal_lsn",
        "writer_term",
        "postgresql",
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
_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
    }
)
_OBJECT_VERSION_FIELDS = frozenset({"object_key", "version_id"})


class PhysicalPostgresRecoveryPreflightError(ValueError):
    """A non-secret recovery-evidence admission code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalPostgresRecoveryStageBinding:
    """Non-secret caller-supplied projection of a completed local stage receipt.

    The receiver staging boundary owns the receipt bytes and any local path.
    This contract intentionally receives only its immutable hash, opaque bundle
    identifier, and pre-established route binding hash; it never opens a stage
    directory or imports the receiver implementation.
    """

    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str


@dataclass(frozen=True)
class PhysicalPostgresRecoveryPreflightBinding:
    """Trusted local standby and signed expected Witness-term projection.

    ``expected_witnessed_term`` is an opaque, signature-verified, non-secret
    term artifact.  Its use here validates lineage only; it is not checked as
    live and cannot authorize a writer transition.
    """

    local_standby_site: str
    stage_binding: PhysicalPostgresRecoveryStageBinding
    expected_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm


@dataclass(frozen=True)
class PhysicalPostgresRecoveryReceiverReadbackEvidence:
    """Bounded canonical PostgreSQL receiver readback injected by an adapter."""

    raw_evidence: bytes
    evidence_sha256: str


@dataclass(frozen=True)
class PhysicalPostgresRecoveryPreflightResult:
    """Non-authorizing observation of staging/replay evidence only."""

    schema: str
    status: str
    reason_codes: tuple[str, ...]
    evidence_sha256: str | None = None
    local_standby_site: str | None = None
    source_site: str | None = None
    destination_site: str | None = None
    stage_bundle_id: str | None = None
    stage_receipt_sha256: str | None = None
    route_binding_sha256: str | None = None
    manifest_sha256es: tuple[str, ...] = ()
    object_versions: tuple[tuple[str, str], ...] = ()
    terminal_wal_lsn: str | None = None
    replay_lsn: str | None = None

    @property
    def replay_evidence_observed(self) -> bool:
        """True is descriptive only and never grants recovery/promotion rights."""

        return self.status == PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED


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
class _ReceiverEvidenceFacts:
    status: str
    observed_at: datetime
    evidence_sha256: str
    receiver_site: str
    source_site: str
    destination_site: str
    stage: _StageFacts
    manifest_sha256es: tuple[str, ...]
    object_versions: tuple[tuple[str, str], ...]
    base_backup_manifest_sha256: str
    terminal_wal_lsn: str
    terminal_wal_lsn_value: int
    term: _TermFacts
    in_recovery: bool
    role: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_generation_id: str
    replay_lsn: str
    replay_lsn_value: int


def _fail(code: str) -> None:
    raise PhysicalPostgresRecoveryPreflightError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("RECEIVER_EVIDENCE_DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("RECEIVER_EVIDENCE_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalPostgresRecoveryPreflightError(code) from exc


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _sha256(value: object, *, code: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        _fail(code)
    return value


def _site(value: object, *, code: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _timestamp(value: object, *, code: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(code)
    return normalized


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _object_version(value: object, *, code: str) -> tuple[str, str]:
    item = _exact_mapping(value, fields=_OBJECT_VERSION_FIELDS, code=code)
    key = item["object_key"]
    version = item["version_id"]
    if (
        not isinstance(key, str)
        or OBJECT_KEY_RE.fullmatch(key) is None
        or not key.endswith(".age")
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        _fail(code)
    if (
        not isinstance(version, str)
        or VERSION_ID_RE.fullmatch(version) is None
        or version.casefold() in {"null", "none", "latest", "current"}
    ):
        _fail(code)
    return key, version


def _stage_facts(value: object) -> _StageFacts:
    if type(value) is not PhysicalPostgresRecoveryStageBinding:
        _fail("STAGE_BINDING_INVALID")
    return _StageFacts(
        bundle_id=_sha256(value.bundle_id, code="STAGE_BINDING_INVALID"),
        stage_receipt_sha256=_sha256(value.stage_receipt_sha256, code="STAGE_BINDING_INVALID"),
        route_binding_sha256=_sha256(value.route_binding_sha256, code="STAGE_BINDING_INVALID"),
    )


def _expected_term(value: object, *, now: datetime) -> _TermFacts:
    try:
        term = require_verified_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError as exc:
        raise PhysicalPostgresRecoveryPreflightError("EXPECTED_WITNESS_TERM_INVALID") from exc
    return _TermFacts(
        holder_site=_site(term.holder_site, code="EXPECTED_WITNESS_TERM_INVALID"),
        writer_epoch=(
            term.writer_epoch
            if type(term.writer_epoch) is int and term.writer_epoch >= 1
            else _invalid_int("EXPECTED_WITNESS_TERM_INVALID")
        ),
        writer_lease_id=(
            term.writer_lease_id
            if isinstance(term.writer_lease_id, str)
            and LEASE_ID_RE.fullmatch(term.writer_lease_id) is not None
            else _invalid_text("EXPECTED_WITNESS_TERM_INVALID")
        ),
        witness_transition_id=(
            term.witness_transition_id
            if isinstance(term.witness_transition_id, str)
            and term.witness_transition_id
            else _invalid_text("EXPECTED_WITNESS_TERM_INVALID")
        ),
        proof_sha256=_sha256(term.proof_sha256, code="EXPECTED_WITNESS_TERM_INVALID"),
    )


def _invalid_int(code: str) -> int:
    _fail(code)
    raise AssertionError("unreachable")


def _invalid_text(code: str) -> str:
    _fail(code)
    raise AssertionError("unreachable")


def _bundle_object_versions(bundle: VerifiedPhysicalWalObjectStorageBundle) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = [
        (bundle.baseline.base_backup_object.object_key, bundle.baseline.base_backup_object.version_id)
    ]
    for manifest in bundle.wal_manifests:
        pairs.extend((segment.object.object_key, segment.object.version_id) for segment in manifest.segments)
    pairs.extend(
        (shard.object.object_key, shard.object.version_id)
        for shard in bundle.blob_frontier.inventory_shards
    )
    normalized = tuple(
        _object_version({"object_key": key, "version_id": version}, code="BUNDLE_OBJECT_VERSION_INVALID")
        for key, version in pairs
    )
    if not normalized or len(set(normalized)) != len(normalized):
        _fail("BUNDLE_OBJECT_VERSION_INVALID")
    return normalized


def _bundle_facts(value: object, *, expected_term: _TermFacts) -> _BundleFacts:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(value)
    except (PhysicalWalObjectManifestError, AttributeError, TypeError) as exc:
        raise PhysicalPostgresRecoveryPreflightError("BUNDLE_UNVERIFIED_OR_TAMPERED") from exc
    baseline = bundle.baseline
    source = _site(baseline.source_site, code="BUNDLE_ROUTE_INVALID")
    destination = _site(baseline.destination_site, code="BUNDLE_ROUTE_INVALID")
    if source == destination or expected_term.holder_site != source:
        _fail("BUNDLE_EXPECTED_WITNESS_TERM_MISMATCH")
    term = baseline.writer_term
    if (
        term.epoch != expected_term.writer_epoch
        or term.lease_id != expected_term.writer_lease_id
        or term.witnessed_term_proof_sha256 != expected_term.proof_sha256
    ):
        _fail("BUNDLE_EXPECTED_WITNESS_TERM_MISMATCH")
    if baseline.wal_segment_size_bytes != REQUIRED_WAL_SEGMENT_SIZE_BYTES:
        _fail("BUNDLE_WAL_GEOMETRY_INVALID")
    if baseline.wal_segment_size_bytes not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        _fail("BUNDLE_WAL_GEOMETRY_INVALID")
    if (
        not isinstance(baseline.baseline_generation_id, str)
        or STREAM_GENERATION_ID_RE.fullmatch(baseline.baseline_generation_id) is None
        or not isinstance(baseline.database_system_identifier, str)
        or _SYSTEM_IDENTIFIER_RE.fullmatch(baseline.database_system_identifier) is None
        or type(baseline.timeline_id) is not int
        or not 1 <= baseline.timeline_id <= 0xFFFFFFFF
    ):
        _fail("BUNDLE_BASELINE_INVALID")
    terminal_lsn, terminal_value = _lsn(bundle.terminal_wal_lsn, code="BUNDLE_TERMINAL_FRONTIER_INVALID")
    manifests = tuple(_sha256(item, code="BUNDLE_MANIFEST_HASH_INVALID") for item in bundle.manifest_sha256es)
    if not manifests or len(set(manifests)) != len(manifests):
        _fail("BUNDLE_MANIFEST_HASH_INVALID")
    return _BundleFacts(
        bundle=bundle,
        source_site=source,
        destination_site=destination,
        baseline_generation_id=baseline.baseline_generation_id,
        database_system_identifier=baseline.database_system_identifier,
        timeline_id=baseline.timeline_id,
        wal_segment_size_bytes=baseline.wal_segment_size_bytes,
        base_backup_manifest_sha256=_sha256(
            baseline.manifest_sha256, code="BUNDLE_BASELINE_INVALID"
        ),
        terminal_wal_lsn=terminal_lsn,
        terminal_wal_lsn_value=terminal_value,
        manifest_sha256es=manifests,
        object_versions=_bundle_object_versions(bundle),
    )


def _term_from_evidence(value: object) -> _TermFacts:
    item = _exact_mapping(value, fields=_TERM_FIELDS, code="RECEIVER_EVIDENCE_TERM_INVALID")
    holder = _site(item["holder_site"], code="RECEIVER_EVIDENCE_TERM_INVALID")
    epoch = item["writer_epoch"]
    lease = item["writer_lease_id"]
    transition = item["witness_transition_id"]
    if type(epoch) is not int or epoch < 1:
        _fail("RECEIVER_EVIDENCE_TERM_INVALID")
    if not isinstance(lease, str) or LEASE_ID_RE.fullmatch(lease) is None:
        _fail("RECEIVER_EVIDENCE_TERM_INVALID")
    if not isinstance(transition, str) or not transition or len(transition) > 128:
        _fail("RECEIVER_EVIDENCE_TERM_INVALID")
    return _TermFacts(
        holder_site=holder,
        writer_epoch=epoch,
        writer_lease_id=lease,
        witness_transition_id=transition,
        proof_sha256=_sha256(
            item["witnessed_term_proof_sha256"], code="RECEIVER_EVIDENCE_TERM_INVALID"
        ),
    )


def _receiver_evidence(
    value: object,
    *,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> _ReceiverEvidenceFacts:
    if type(value) is not PhysicalPostgresRecoveryReceiverReadbackEvidence:
        _fail("RECEIVER_EVIDENCE_INVALID")
    raw = value.raw_evidence
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_PHYSICAL_POSTGRES_RECOVERY_READBACK_BYTES:
        _fail("RECEIVER_EVIDENCE_BYTES_INVALID")
    evidence_sha256 = _sha256(value.evidence_sha256, code="RECEIVER_EVIDENCE_HASH_INVALID")
    if hashlib.sha256(raw).hexdigest() != evidence_sha256:
        _fail("RECEIVER_EVIDENCE_HASH_MISMATCH")
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalPostgresRecoveryPreflightError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PhysicalPostgresRecoveryPreflightError("RECEIVER_EVIDENCE_INVALID_JSON") from exc
    if not isinstance(parsed, dict) or _canonical(parsed, code="RECEIVER_EVIDENCE_NOT_CANONICAL") != raw:
        _fail("RECEIVER_EVIDENCE_NOT_CANONICAL")
    item = _exact_mapping(parsed, fields=_EVIDENCE_FIELDS, code="RECEIVER_EVIDENCE_FIELDS_INVALID")
    if item["schema"] != PHYSICAL_POSTGRES_RECOVERY_RECEIVER_READBACK_SCHEMA:
        _fail("RECEIVER_EVIDENCE_SCHEMA_INVALID")
    status = item["status"]
    if status not in {
        PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED,
        PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
    }:
        _fail("RECEIVER_EVIDENCE_STATUS_INVALID")
    observed_at = _timestamp(item["observed_at"], code="RECEIVER_EVIDENCE_TIME_INVALID")
    if observed_at > now + timedelta(seconds=MAX_RECOVERY_EVIDENCE_FUTURE_SKEW_SECONDS):
        _fail("RECEIVER_EVIDENCE_TIME_INVALID")
    if now - observed_at > timedelta(seconds=maximum_evidence_age_seconds):
        _fail("RECEIVER_EVIDENCE_TIME_STALE")
    source = _site(item["source_site"], code="RECEIVER_EVIDENCE_ROUTE_INVALID")
    destination = _site(item["destination_site"], code="RECEIVER_EVIDENCE_ROUTE_INVALID")
    receiver = _site(item["receiver_site"], code="RECEIVER_EVIDENCE_ROUTE_INVALID")
    if source == destination:
        _fail("RECEIVER_EVIDENCE_ROUTE_INVALID")
    manifests_raw = item["manifest_sha256es"]
    if not isinstance(manifests_raw, list):
        _fail("RECEIVER_EVIDENCE_MANIFESTS_INVALID")
    manifests = tuple(
        _sha256(member, code="RECEIVER_EVIDENCE_MANIFESTS_INVALID") for member in manifests_raw
    )
    if not manifests or len(set(manifests)) != len(manifests):
        _fail("RECEIVER_EVIDENCE_MANIFESTS_INVALID")
    object_versions_raw = item["object_versions"]
    if not isinstance(object_versions_raw, list):
        _fail("RECEIVER_EVIDENCE_OBJECT_VERSIONS_INVALID")
    object_versions = tuple(
        _object_version(member, code="RECEIVER_EVIDENCE_OBJECT_VERSIONS_INVALID")
        for member in object_versions_raw
    )
    if not object_versions or len(set(object_versions)) != len(object_versions):
        _fail("RECEIVER_EVIDENCE_OBJECT_VERSIONS_INVALID")
    postgres = _exact_mapping(item["postgresql"], fields=_POSTGRES_FIELDS, code="POSTGRES_READBACK_INVALID")
    in_recovery = postgres["in_recovery"]
    role = postgres["role"]
    timeline_id = postgres["timeline_id"]
    wal_size = postgres["wal_segment_size_bytes"]
    generation = postgres["baseline_generation_id"]
    system_identifier = postgres["database_system_identifier"]
    if type(in_recovery) is not bool or role not in {"standby", "primary"}:
        _fail("POSTGRES_READBACK_INVALID")
    if type(timeline_id) is not int or not 1 <= timeline_id <= 0xFFFFFFFF:
        _fail("POSTGRES_READBACK_INVALID")
    if type(wal_size) is not int or wal_size not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        _fail("POSTGRES_READBACK_INVALID")
    if not isinstance(generation, str) or STREAM_GENERATION_ID_RE.fullmatch(generation) is None:
        _fail("POSTGRES_READBACK_INVALID")
    if not isinstance(system_identifier, str) or _SYSTEM_IDENTIFIER_RE.fullmatch(system_identifier) is None:
        _fail("POSTGRES_READBACK_INVALID")
    terminal_lsn, terminal_value = _lsn(
        item["bundle_terminal_wal_lsn"], code="RECEIVER_EVIDENCE_TERMINAL_LSN_INVALID"
    )
    replay_lsn, replay_value = _lsn(postgres["replay_lsn"], code="POSTGRES_REPLAY_LSN_INVALID")
    return _ReceiverEvidenceFacts(
        status=status,
        observed_at=observed_at,
        evidence_sha256=evidence_sha256,
        receiver_site=receiver,
        source_site=source,
        destination_site=destination,
        stage=_StageFacts(
            bundle_id=_sha256(item["stage_bundle_id"], code="RECEIVER_EVIDENCE_STAGE_INVALID"),
            stage_receipt_sha256=_sha256(
                item["stage_receipt_sha256"], code="RECEIVER_EVIDENCE_STAGE_INVALID"
            ),
            route_binding_sha256=_sha256(
                item["route_binding_sha256"], code="RECEIVER_EVIDENCE_STAGE_INVALID"
            ),
        ),
        manifest_sha256es=manifests,
        object_versions=object_versions,
        base_backup_manifest_sha256=_sha256(
            item["base_backup_manifest_sha256"], code="RECEIVER_EVIDENCE_BASELINE_INVALID"
        ),
        terminal_wal_lsn=terminal_lsn,
        terminal_wal_lsn_value=terminal_value,
        term=_term_from_evidence(item["writer_term"]),
        in_recovery=in_recovery,
        role=role,
        database_system_identifier=system_identifier,
        timeline_id=timeline_id,
        wal_segment_size_bytes=wal_size,
        baseline_generation_id=generation,
        replay_lsn=replay_lsn,
        replay_lsn_value=replay_value,
    )


def _normalise_binding(value: object, *, now: datetime) -> tuple[str, _StageFacts, _TermFacts]:
    if type(value) is not PhysicalPostgresRecoveryPreflightBinding:
        _fail("RECOVERY_PREFLIGHT_BINDING_INVALID")
    local_standby = _site(value.local_standby_site, code="RECOVERY_PREFLIGHT_BINDING_INVALID")
    return local_standby, _stage_facts(value.stage_binding), _expected_term(
        value.expected_witnessed_term, now=now
    )


def _result(
    *,
    status: str,
    reason_codes: tuple[str, ...],
    evidence: _ReceiverEvidenceFacts | None = None,
    bundle: _BundleFacts | None = None,
    local_standby_site: str | None = None,
) -> PhysicalPostgresRecoveryPreflightResult:
    return PhysicalPostgresRecoveryPreflightResult(
        schema=PHYSICAL_POSTGRES_RECOVERY_PREFLIGHT_SCHEMA,
        status=status,
        reason_codes=reason_codes,
        evidence_sha256=None if evidence is None else evidence.evidence_sha256,
        local_standby_site=local_standby_site,
        source_site=None if bundle is None else bundle.source_site,
        destination_site=None if bundle is None else bundle.destination_site,
        stage_bundle_id=None if evidence is None else evidence.stage.bundle_id,
        stage_receipt_sha256=None if evidence is None else evidence.stage.stage_receipt_sha256,
        route_binding_sha256=None if evidence is None else evidence.stage.route_binding_sha256,
        manifest_sha256es=() if bundle is None else bundle.manifest_sha256es,
        object_versions=() if bundle is None else bundle.object_versions,
        terminal_wal_lsn=None if bundle is None else bundle.terminal_wal_lsn,
        replay_lsn=None if evidence is None else evidence.replay_lsn,
    )


def _assess(
    *,
    bundle: object,
    binding: object,
    receiver_readback_evidence: object,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> PhysicalPostgresRecoveryPreflightResult:
    observed_now = _utc(now, code="RECOVERY_PREFLIGHT_CLOCK_INVALID")
    if (
        type(maximum_evidence_age_seconds) is not int
        or not 1 <= maximum_evidence_age_seconds <= MAX_RECOVERY_EVIDENCE_AGE_SECONDS
    ):
        _fail("RECOVERY_PREFLIGHT_EVIDENCE_AGE_INVALID")
    local_standby, expected_stage, expected_term = _normalise_binding(
        binding, now=observed_now
    )
    bundle_facts = _bundle_facts(bundle, expected_term=expected_term)
    if bundle_facts.destination_site != local_standby:
        _fail("BUNDLE_DESTINATION_IS_NOT_LOCAL_STANDBY")
    evidence = _receiver_evidence(
        receiver_readback_evidence,
        now=observed_now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )
    if (
        evidence.receiver_site != local_standby
        or evidence.source_site != bundle_facts.source_site
        or evidence.destination_site != bundle_facts.destination_site
        or evidence.stage != expected_stage
    ):
        _fail("RECEIVER_EVIDENCE_ROUTE_OR_STAGE_MISMATCH")
    if (
        evidence.manifest_sha256es != bundle_facts.manifest_sha256es
        or evidence.object_versions != bundle_facts.object_versions
        or evidence.base_backup_manifest_sha256 != bundle_facts.base_backup_manifest_sha256
        or evidence.terminal_wal_lsn != bundle_facts.terminal_wal_lsn
    ):
        _fail("RECEIVER_EVIDENCE_EXACT_BUNDLE_MISMATCH")
    if evidence.term != expected_term:
        _fail("RECEIVER_EVIDENCE_WITNESS_TERM_MISMATCH")
    if not evidence.in_recovery or evidence.role != "standby":
        _fail("POSTGRES_NOT_RECOVERY_STANDBY")
    if evidence.database_system_identifier != bundle_facts.database_system_identifier:
        _fail("POSTGRES_SYSTEM_IDENTIFIER_MISMATCH")
    if evidence.timeline_id != bundle_facts.timeline_id:
        _fail("POSTGRES_TIMELINE_MISMATCH")
    if evidence.wal_segment_size_bytes != REQUIRED_WAL_SEGMENT_SIZE_BYTES:
        _fail("POSTGRES_WAL_GEOMETRY_MISMATCH")
    if evidence.wal_segment_size_bytes != bundle_facts.wal_segment_size_bytes:
        _fail("POSTGRES_WAL_GEOMETRY_MISMATCH")
    if evidence.baseline_generation_id != bundle_facts.baseline_generation_id:
        _fail("POSTGRES_BASE_GENERATION_MISMATCH")
    if evidence.status == PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED:
        return _result(
            status=PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED,
            reason_codes=("REPLAY_EVIDENCE_NOT_OBSERVED",),
            evidence=evidence,
            bundle=bundle_facts,
            local_standby_site=local_standby,
        )
    if evidence.replay_lsn_value < bundle_facts.terminal_wal_lsn_value:
        _fail("REPLAY_LSN_BEHIND_BUNDLE_TERMINAL_FRONTIER")
    return _result(
        status=PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
        reason_codes=(),
        evidence=evidence,
        bundle=bundle_facts,
        local_standby_site=local_standby,
    )


def assess_physical_postgres_recovery_preflight(
    *,
    bundle: object,
    binding: object,
    receiver_readback_evidence: object,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_MAX_RECOVERY_EVIDENCE_AGE_SECONDS,
) -> PhysicalPostgresRecoveryPreflightResult:
    """Assess injected recovery evidence without touching PostgreSQL or staging files.

    The result status is always exactly one of ``blocked``,
    ``staged-not-replay-verified``, or ``replay-evidence-observed``.  The last
    status is intentionally not an activation, restore, or promotion right.
    """

    try:
        return _assess(
            bundle=bundle,
            binding=binding,
            receiver_readback_evidence=receiver_readback_evidence,
            now=now,
            maximum_evidence_age_seconds=maximum_evidence_age_seconds,
        )
    except PhysicalPostgresRecoveryPreflightError as exc:
        return _result(
            status=PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED,
            reason_codes=(exc.code,),
        )
    except Exception:
        return _result(
            status=PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED,
            reason_codes=("UNEXPECTED_RECOVERY_PREFLIGHT_FAILURE",),
        )
