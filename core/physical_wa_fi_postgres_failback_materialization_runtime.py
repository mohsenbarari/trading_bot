"""Root-only detached WA-FI replay materializer for the reverse route.

This is intentionally a different boundary from both the normal WA-IR
materializer and the normal WA-FI capture path.  It consumes only the typed
``physical-failback`` exact-pull result, establishes that WA-FI's writer root
is currently fenced by a separately signed receipt, and gives one injected
local runner a fixed, network-isolated replay invocation.

The runner receives neither a command, environment, peer, URL, credential,
Object-Storage client, nor a traffic/promotion control.  It must return a
strict local PostgreSQL recovery readback evidence object; the generic
recovery assessor independently checks that the detached target remains a
standby and has replayed through the exact bundle frontier.  A success is
therefore evidence of a detached FI standby candidate only.  It never
promotes WA-FI, opens traffic, changes the Witness term, or authorizes Full
Matrix.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    canonical_json_bytes,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.physical_ir_to_fi_object_storage_failback_preflight import (
    PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    VerifiedPhysicalIrToFiObjectStorageFailbackPreflight,
    require_verified_physical_ir_to_fi_object_storage_failback_preflight,
)
from core.physical_postgres_recovery_preflight import (
    DEFAULT_MAX_RECOVERY_EVIDENCE_AGE_SECONDS,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryPreflightResult,
    PhysicalPostgresRecoveryReceiverReadbackEvidence,
    assess_physical_postgres_recovery_preflight,
)
from core.physical_release_candidate_writer_quiescence_receipt import (
    RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig,
    VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt,
    require_verified_physical_release_candidate_writer_quiescence_receipt,
)
from core.physical_wa_fi_postgres_failback_pull_runtime import (
    PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED,
    PhysicalWaFiPostgresFailbackPullRedactedReceipt,
    PhysicalWaFiPostgresFailbackPullResult,
    PhysicalWaFiPostgresFailbackStageEvidence,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalObjectStorageBundle,
    require_verified_physical_wal_object_storage_bundle,
)


__all__ = (
    "DEFAULT_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_DEFAULT_ENABLED",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RECEIPT_SCHEMA",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RUNTIME_SCHEMA",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_STATUS",
    "PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_STATUS_BLOCKED",
    "PhysicalWaFiPostgresFailbackMaterializationAck",
    "PhysicalWaFiPostgresFailbackMaterializationDurableEvidence",
    "PhysicalWaFiPostgresFailbackMaterializationError",
    "PhysicalWaFiPostgresFailbackMaterializationInvocation",
    "PhysicalWaFiPostgresFailbackMaterializationResult",
    "PhysicalWaFiPostgresFailbackMaterializationRunner",
    "PhysicalWaFiPostgresFailbackWriterQuiescenceBinding",
    "RootOwnedWaFiPostgresFailbackMaterializationRuntime",
    "RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig",
    "run_root_owned_wa_fi_postgres_failback_materialization",
    "validate_root_owned_wa_fi_postgres_failback_materialization_runtime_config",
)


PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RUNTIME_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-failback-materialization-runtime-v1"
)
PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RECEIPT_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-failback-materialization-receipt-v1"
)
PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_DEFAULT_ENABLED = False
PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_STATUS = (
    "local-failback-replay-observed-not-promoted"
)
PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_STATUS_BLOCKED = "blocked"

DEFAULT_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_MAX_EVIDENCE_AGE_SECONDS = 120

_MAX_EVIDENCE_AGE_SECONDS = 300
_MAX_PATH_LENGTH = 4096
_SOURCE_SITE = "webapp_ir"
_DESTINATION_SITE = "webapp_fi"
_RUNTIME_MODE = "root-owned-wa-fi-detached-failback-replay-v1"
_RUNNER_ACK_SCHEMA = "gold-trade-physical-wa-fi-postgres-failback-runner-ack-v1"
_RECEIPTS_DIRECTORY = "failback-replay-receipts"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "bundle_id",
        "stage_receipt_sha256",
        "stage_route_binding_sha256",
        "manifest_sha256es",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
        "preflight_evidence_sha256",
        "writer_quiescence_receipt_sha256",
        "runner_profile_sha256",
        "recovery_evidence_sha256",
        "target_pgdata_device",
        "target_pgdata_inode",
        "completed_at",
        "promotion_authorized",
        "writer_authorized",
        "traffic_switch_authorized",
        "full_matrix_authorized",
        "receipt_integrity_sha256",
    }
)


class PhysicalWaFiPostgresFailbackMaterializationError(ValueError):
    """A stable redacted refusal from the detached FI replay boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaFiPostgresFailbackWriterQuiescenceBinding:
    """Exact non-secret binding expected in the independently signed fence."""

    fenced_writer_root: Path
    inventory_manifest_sha256: str
    frozen_generation_sha256: str
    quiescence_evidence_sha256: str


@dataclass(frozen=True)
class PhysicalWaFiPostgresFailbackMaterializationInvocation:
    """Canonical fixed local input released to one detached replay runner."""

    schema: str
    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    object_storage_namespace: str
    bundle_id: str
    stage_receipt_sha256: str
    stage_route_binding_sha256: str
    manifest_sha256es: tuple[str, ...]
    terminal_wal_lsn: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    preflight_evidence_sha256: str
    writer_quiescence_receipt_sha256: str
    runner_profile_sha256: str
    source_candidate: Path
    target_pgdata_candidate: Path
    network_mode: str
    tcp_listener: str
    recovery_mode: str
    invocation_sha256: str


@dataclass(frozen=True)
class PhysicalWaFiPostgresFailbackMaterializationAck:
    """Runner acknowledgement plus opaque local recovery readback evidence."""

    schema: str
    status: str
    invocation_sha256: str
    target_pgdata_candidate: Path
    target_pgdata_device: int
    target_pgdata_inode: int
    recovery_readback_evidence: PhysicalPostgresRecoveryReceiverReadbackEvidence


class PhysicalWaFiPostgresFailbackMaterializationRunner(Protocol):
    """One injected local-only materialize/replay/inspect seam."""

    def materialize_and_inspect_detached_failback_standby(
        self,
        *,
        invocation: PhysicalWaFiPostgresFailbackMaterializationInvocation,
        source_stage: PhysicalWaFiPostgresFailbackStageEvidence,
    ) -> PhysicalWaFiPostgresFailbackMaterializationAck:
        """Produce a detached FI standby candidate and strict local evidence."""


@dataclass(frozen=True)
class RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig:
    """Default-off root policy; it deliberately has no command or secret field."""

    schema: str = PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RUNTIME_SCHEMA
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig | None = field(
        default=None, repr=False, compare=False
    )
    preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight | None = field(
        default=None, repr=False, compare=False
    )
    writer_quiescence_config: (
        RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig | None
    ) = field(default=None, repr=False, compare=False)
    writer_quiescence_receipt: VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt | None = field(
        default=None, repr=False, compare=False
    )
    writer_quiescence_binding: PhysicalWaFiPostgresFailbackWriterQuiescenceBinding | None = field(
        default=None, repr=False, compare=False
    )
    source_stage_candidates_root: Path | None = field(default=None, repr=False, compare=False)
    target_pgdata_candidates_root: Path | None = field(default=None, repr=False, compare=False)
    redacted_receipt_root: Path | None = field(default=None, repr=False, compare=False)
    runner_profile_sha256: str = ""
    maximum_recovery_evidence_age_seconds: int = (
        DEFAULT_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_MAX_EVIDENCE_AGE_SECONDS
    )
    enabled: bool = PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_DEFAULT_ENABLED
    source_site: str = _SOURCE_SITE
    destination_site: str = _DESTINATION_SITE
    object_storage_namespace: str = PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    runtime_mode: str = _RUNTIME_MODE
    network_mode: str = "none"
    tcp_listener: str = "disabled"
    recovery_mode: str = "standby-replay-only"
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalWaFiPostgresFailbackMaterializationDurableEvidence:
    """Non-secret local receipt recording one detached replay observation."""

    receipt_path: Path
    raw_receipt: bytes
    receipt_sha256: str
    recovery_evidence_sha256: str


@dataclass(frozen=True)
class PhysicalWaFiPostgresFailbackMaterializationResult:
    """A result with no writer, traffic, promotion, or matrix authority."""

    schema: str
    status: str
    reason_codes: tuple[str, ...]
    recovery_preflight: PhysicalPostgresRecoveryPreflightResult | None = None
    durable_evidence: PhysicalWaFiPostgresFailbackMaterializationDurableEvidence | None = None
    promotion_authorized: bool = False
    writer_authorized: bool = False
    traffic_switch_authorized: bool = False
    full_matrix_authorized: bool = False


@dataclass(frozen=True)
class _QuiescenceFacts:
    config: RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig
    receipt: VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt
    fenced_writer_root: Path
    inventory_manifest_sha256: str
    frozen_generation_sha256: str
    quiescence_evidence_sha256: str


@dataclass(frozen=True)
class _RuntimeFacts:
    preflight_config: PhysicalIrToFiObjectStorageFailbackPreflightConfig
    preflight: VerifiedPhysicalIrToFiObjectStorageFailbackPreflight
    quiescence: _QuiescenceFacts
    source_stage_candidates_root: Path
    target_pgdata_candidates_root: Path
    redacted_receipt_root: Path
    runner_profile_sha256: str
    maximum_recovery_evidence_age_seconds: int


@dataclass(frozen=True)
class _TermFacts:
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    proof_sha256: str


@dataclass(frozen=True)
class _PullFacts:
    result: PhysicalWaFiPostgresFailbackPullResult
    redacted_receipt: PhysicalWaFiPostgresFailbackPullRedactedReceipt
    recovery_binding: PhysicalPostgresRecoveryPreflightBinding
    stage_evidence: PhysicalWaFiPostgresFailbackStageEvidence
    bundle_id: str
    stage_receipt_sha256: str
    stage_route_binding_sha256: str
    source_candidate: Path


def _fail(code: str) -> None:
    raise PhysicalWaFiPostgresFailbackMaterializationError(code)


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("WA_FI_FAILBACK_MATERIALIZATION_ROOT_REQUIRED")
    except OSError:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_ROOT_REQUIRED")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _private_root(value: object, *, code: str) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or value == Path("/")
        or ".." in value.parts
        or len(str(value)) > _MAX_PATH_LENGTH
    ):
        _fail(code)
    try:
        before = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != value
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _private_child_directory(
    value: object,
    *,
    root: Path,
    code: str,
) -> tuple[Path, int, int]:
    if not isinstance(value, Path) or value.parent != root or value.name in {"", ".", ".."}:
        _fail(code)
    try:
        before = os.lstat(value)
        resolved = value.resolve(strict=True)
        descriptor = os.open(
            value,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        _fail(code)
    try:
        metadata = os.fstat(descriptor)
    except OSError:
        os.close(descriptor)
        _fail(code)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    if (
        resolved != value
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_dev != before.st_dev
        or metadata.st_ino != before.st_ino
    ):
        _fail(code)
    return resolved, metadata.st_dev, metadata.st_ino


def _roots_disjoint(values: tuple[Path, ...]) -> None:
    for index, left in enumerate(values):
        for right in values[index + 1 :]:
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                _fail("WA_FI_FAILBACK_MATERIALIZATION_ROOTS_OVERLAP")


def _quiescence_binding(
    value: object,
) -> tuple[Path, str, str, str]:
    if type(value) is not PhysicalWaFiPostgresFailbackWriterQuiescenceBinding:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_QUIESCENCE_BINDING_INVALID")
    root = _private_root(
        value.fenced_writer_root,
        code="WA_FI_FAILBACK_MATERIALIZATION_QUIESCENCE_ROOT_UNSAFE",
    )
    return (
        root,
        _sha256(
            value.inventory_manifest_sha256,
            code="WA_FI_FAILBACK_MATERIALIZATION_QUIESCENCE_BINDING_INVALID",
        ),
        _sha256(
            value.frozen_generation_sha256,
            code="WA_FI_FAILBACK_MATERIALIZATION_QUIESCENCE_BINDING_INVALID",
        ),
        _sha256(
            value.quiescence_evidence_sha256,
            code="WA_FI_FAILBACK_MATERIALIZATION_QUIESCENCE_BINDING_INVALID",
        ),
    )


def _inert_config(
    value: object,
) -> RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig:
    if type(value) is not RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RUNTIME_SCHEMA
        or type(value.enabled) is not bool
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or value.runtime_mode != _RUNTIME_MODE
        or value.network_mode != "none"
        or value.tcp_listener != "disabled"
        or value.recovery_mode != "standby-replay-only"
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
        or type(value.preflight_config) is not PhysicalIrToFiObjectStorageFailbackPreflightConfig
        or type(value.preflight) is not VerifiedPhysicalIrToFiObjectStorageFailbackPreflight
        or type(value.writer_quiescence_config)
        is not RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig
        or type(value.writer_quiescence_receipt)
        is not VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt
        or type(value.writer_quiescence_binding)
        is not PhysicalWaFiPostgresFailbackWriterQuiescenceBinding
        or not isinstance(value.source_stage_candidates_root, Path)
        or not isinstance(value.target_pgdata_candidates_root, Path)
        or not isinstance(value.redacted_receipt_root, Path)
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_CONFIG_INVALID")
    _sha256(value.runner_profile_sha256, code="WA_FI_FAILBACK_MATERIALIZATION_CONFIG_INVALID")
    if (
        type(value.maximum_recovery_evidence_age_seconds) is not int
        or not 1 <= value.maximum_recovery_evidence_age_seconds <= _MAX_EVIDENCE_AGE_SECONDS
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_EVIDENCE_AGE_INVALID")
    return value


def validate_root_owned_wa_fi_postgres_failback_materialization_runtime_config(
    config: RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig,
) -> RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig:
    """Validate inert policy shape without starting PostgreSQL or a runner."""

    return _inert_config(config)


def _facts(
    config: RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig,
    *,
    now: datetime,
    require_enabled: bool,
) -> _RuntimeFacts:
    checked = _inert_config(config)
    if require_enabled and checked.enabled is not True:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_DISABLED")
    assert checked.preflight_config is not None and checked.preflight is not None
    try:
        preflight = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            checked.preflight,
            config=checked.preflight_config,
            now=now,
        )
    except Exception:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_PREFLIGHT_INVALID_OR_STALE")
    assert checked.writer_quiescence_binding is not None
    fenced_root, inventory_hash, frozen_hash, evidence_hash = _quiescence_binding(
        checked.writer_quiescence_binding
    )
    assert checked.writer_quiescence_config is not None
    assert checked.writer_quiescence_receipt is not None
    try:
        receipt = require_verified_physical_release_candidate_writer_quiescence_receipt(
            checked.writer_quiescence_receipt,
            config=checked.writer_quiescence_config,
            source_root=fenced_root,
            inventory_manifest_sha256=inventory_hash,
            frozen_generation_sha256=frozen_hash,
            quiescence_evidence_sha256=evidence_hash,
            now=now,
        )
    except Exception:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_WRITER_QUIESCENCE_INVALID_OR_STALE")
    stage_root = _private_root(
        checked.source_stage_candidates_root,
        code="WA_FI_FAILBACK_MATERIALIZATION_STAGE_ROOT_UNSAFE",
    )
    target_root = _private_root(
        checked.target_pgdata_candidates_root,
        code="WA_FI_FAILBACK_MATERIALIZATION_TARGET_ROOT_UNSAFE",
    )
    receipt_root = _private_root(
        checked.redacted_receipt_root,
        code="WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_ROOT_UNSAFE",
    )
    _roots_disjoint((fenced_root, stage_root, target_root, receipt_root))
    return _RuntimeFacts(
        preflight_config=checked.preflight_config,
        preflight=preflight,
        quiescence=_QuiescenceFacts(
            config=checked.writer_quiescence_config,
            receipt=receipt,
            fenced_writer_root=fenced_root,
            inventory_manifest_sha256=inventory_hash,
            frozen_generation_sha256=frozen_hash,
            quiescence_evidence_sha256=evidence_hash,
        ),
        source_stage_candidates_root=stage_root,
        target_pgdata_candidates_root=target_root,
        redacted_receipt_root=receipt_root,
        runner_profile_sha256=checked.runner_profile_sha256,
        maximum_recovery_evidence_age_seconds=checked.maximum_recovery_evidence_age_seconds,
    )


def _term(value: object, *, now: datetime) -> _TermFacts:
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_TERM_INVALID_OR_STALE")
    if term.holder_site != _SOURCE_SITE or type(term.writer_epoch) is not int or term.writer_epoch < 1:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_TERM_ROUTE_INVALID")
    return _TermFacts(
        term=term,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witness_transition_id=term.witness_transition_id,
        proof_sha256=_sha256(
            term.proof_sha256,
            code="WA_FI_FAILBACK_MATERIALIZATION_TERM_INVALID_OR_STALE",
        ),
    )


def _same_term(left: _TermFacts, right: _TermFacts) -> bool:
    return (
        left.term.holder_site,
        left.writer_epoch,
        left.writer_lease_id,
        left.witness_transition_id,
        left.proof_sha256,
    ) == (
        right.term.holder_site,
        right.writer_epoch,
        right.writer_lease_id,
        right.witness_transition_id,
        right.proof_sha256,
    )


def _bundle(
    value: object,
    *,
    facts: _RuntimeFacts,
    term: _TermFacts,
) -> VerifiedPhysicalWalObjectStorageBundle:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(value)
    except (PhysicalWalObjectManifestError, AttributeError, TypeError):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_BUNDLE_INVALID")
    baseline = bundle.baseline
    if (
        baseline.source_site != _SOURCE_SITE
        or baseline.destination_site != _DESTINATION_SITE
        or baseline.campaign_id != facts.preflight.binding.campaign_id
        or baseline.release_sha != facts.preflight.binding.release_sha
        or baseline.writer_term.epoch != term.writer_epoch
        or baseline.writer_term.lease_id != term.writer_lease_id
        or baseline.writer_term.witnessed_term_proof_sha256 != term.proof_sha256
        or not bundle.manifest_sha256es
        or len(set(bundle.manifest_sha256es)) != len(bundle.manifest_sha256es)
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_BUNDLE_BINDING_MISMATCH")
    objects = [baseline.base_backup_object]
    objects.extend(segment.object for manifest in bundle.wal_manifests for segment in manifest.segments)
    objects.extend(shard.object for shard in bundle.blob_frontier.inventory_shards)
    if not objects or any(
        not item.object_key.startswith(PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE + "/")
        for item in objects
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_OBJECT_NAMESPACE_INVALID")
    return bundle


def _live_expected_term(value: object, *, now: datetime) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    try:
        return require_live_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_PULL_TERM_INVALID_OR_STALE")


def _pull(
    value: object,
    *,
    facts: _RuntimeFacts,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    term: _TermFacts,
    now: datetime,
) -> _PullFacts:
    if type(value) is not PhysicalWaFiPostgresFailbackPullResult:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_PULL_RESULT_INVALID")
    if (
        value.status != PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED
        or value.reason_codes != ()
        or value.promotion_authorized is not False
        or value.full_matrix_authorized is not False
        or type(value.redacted_receipt) is not PhysicalWaFiPostgresFailbackPullRedactedReceipt
        or type(value.recovery_preflight_binding) is not PhysicalPostgresRecoveryPreflightBinding
        or type(value.failback_stage_evidence) is not PhysicalWaFiPostgresFailbackStageEvidence
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_PULL_RESULT_INVALID")
    receipt = value.redacted_receipt
    binding = value.recovery_preflight_binding
    evidence = value.failback_stage_evidence
    raw_receipt = receipt.raw_receipt
    if (
        not isinstance(raw_receipt, bytes)
        or not 1 <= len(raw_receipt) <= 64 * 1024
        or hashlib.sha256(raw_receipt).hexdigest()
        != _sha256(receipt.receipt_sha256, code="WA_FI_FAILBACK_MATERIALIZATION_PULL_BINDING_MISMATCH")
        or not isinstance(evidence.raw_stage_receipt, bytes)
        or not 1 <= len(evidence.raw_stage_receipt) <= 64 * 1024
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_PULL_BINDING_MISMATCH")
    stage = binding.stage_binding
    bundle_id = _sha256(
        receipt.bundle_id,
        code="WA_FI_FAILBACK_MATERIALIZATION_PULL_BINDING_MISMATCH",
    )
    stage_receipt_sha256 = _sha256(
        receipt.stage_receipt_sha256,
        code="WA_FI_FAILBACK_MATERIALIZATION_PULL_BINDING_MISMATCH",
    )
    stage_route_binding_sha256 = _sha256(
        receipt.route_binding_sha256,
        code="WA_FI_FAILBACK_MATERIALIZATION_PULL_BINDING_MISMATCH",
    )
    if (
        binding.local_standby_site != _DESTINATION_SITE
        or stage.bundle_id != bundle_id
        or stage.stage_receipt_sha256 != stage_receipt_sha256
        or stage.route_binding_sha256 != stage_route_binding_sha256
        or evidence.stage_receipt_sha256 != stage_receipt_sha256
        or hashlib.sha256(evidence.raw_stage_receipt).hexdigest() != stage_receipt_sha256
        or evidence.source_candidate != facts.source_stage_candidates_root / bundle_id
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_PULL_BINDING_MISMATCH")
    expected_term = _live_expected_term(binding.expected_witnessed_term, now=now)
    if (
        expected_term.holder_site != _SOURCE_SITE
        or expected_term.writer_epoch != term.writer_epoch
        or expected_term.writer_lease_id != term.writer_lease_id
        or expected_term.witness_transition_id != term.witness_transition_id
        or expected_term.proof_sha256 != term.proof_sha256
        or bundle.baseline.writer_term.epoch != term.writer_epoch
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_PULL_TERM_MISMATCH")
    source_candidate, _device, _inode = _private_child_directory(
        evidence.source_candidate,
        root=facts.source_stage_candidates_root,
        code="WA_FI_FAILBACK_MATERIALIZATION_SOURCE_STAGE_UNSAFE",
    )
    return _PullFacts(
        result=value,
        redacted_receipt=receipt,
        recovery_binding=binding,
        stage_evidence=evidence,
        bundle_id=bundle_id,
        stage_receipt_sha256=stage_receipt_sha256,
        stage_route_binding_sha256=stage_route_binding_sha256,
        source_candidate=source_candidate,
    )


def _invocation(
    *,
    facts: _RuntimeFacts,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pull: _PullFacts,
    term: _TermFacts,
) -> PhysicalWaFiPostgresFailbackMaterializationInvocation:
    target = facts.target_pgdata_candidates_root / pull.bundle_id
    payload: dict[str, Any] = {
        "schema": PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RUNTIME_SCHEMA,
        "campaign_id": facts.preflight.binding.campaign_id,
        "release_sha": facts.preflight.binding.release_sha,
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "object_storage_namespace": PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        "bundle_id": pull.bundle_id,
        "stage_receipt_sha256": pull.stage_receipt_sha256,
        "stage_route_binding_sha256": pull.stage_route_binding_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "terminal_wal_lsn": bundle.terminal_wal_lsn,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": term.proof_sha256,
        "preflight_evidence_sha256": facts.preflight.observation.evidence_sha256,
        "writer_quiescence_receipt_sha256": facts.quiescence.receipt.receipt_sha256,
        "runner_profile_sha256": facts.runner_profile_sha256,
        "source_candidate": str(pull.source_candidate),
        "target_pgdata_candidate": str(target),
        "network_mode": "none",
        "tcp_listener": "disabled",
        "recovery_mode": "standby-replay-only",
    }
    try:
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_INVOCATION_INVALID")
    return PhysicalWaFiPostgresFailbackMaterializationInvocation(
        schema=PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RUNTIME_SCHEMA,
        campaign_id=facts.preflight.binding.campaign_id,
        release_sha=facts.preflight.binding.release_sha,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        bundle_id=pull.bundle_id,
        stage_receipt_sha256=pull.stage_receipt_sha256,
        stage_route_binding_sha256=pull.stage_route_binding_sha256,
        manifest_sha256es=bundle.manifest_sha256es,
        terminal_wal_lsn=bundle.terminal_wal_lsn,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witness_transition_id=term.witness_transition_id,
        witnessed_term_proof_sha256=term.proof_sha256,
        preflight_evidence_sha256=facts.preflight.observation.evidence_sha256,
        writer_quiescence_receipt_sha256=facts.quiescence.receipt.receipt_sha256,
        runner_profile_sha256=facts.runner_profile_sha256,
        source_candidate=pull.source_candidate,
        target_pgdata_candidate=target,
        network_mode="none",
        tcp_listener="disabled",
        recovery_mode="standby-replay-only",
        invocation_sha256=digest,
    )


def _assert_no_preexisting_materialization(
    *,
    facts: _RuntimeFacts,
    pull: _PullFacts,
) -> None:
    """Refuse a stale target or receipt; this runtime never overwrites either."""

    target = facts.target_pgdata_candidates_root / pull.bundle_id
    try:
        os.lstat(target)
    except FileNotFoundError:
        pass
    except OSError:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_TARGET_UNSAFE")
    else:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_TARGET_PREEXISTS")
    directory = facts.redacted_receipt_root / _RECEIPTS_DIRECTORY
    try:
        os.lstat(directory)
    except FileNotFoundError:
        return
    except OSError:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_ROOT_UNSAFE")
    _private_root(directory, code="WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_ROOT_UNSAFE")
    try:
        os.lstat(directory / (pull.bundle_id + ".json"))
    except FileNotFoundError:
        return
    except OSError:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_UNSAFE")
    _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_PREEXISTS")


def _ack(
    value: object,
    *,
    invocation: PhysicalWaFiPostgresFailbackMaterializationInvocation,
    target_root: Path,
) -> PhysicalWaFiPostgresFailbackMaterializationAck:
    if type(value) is not PhysicalWaFiPostgresFailbackMaterializationAck:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RUNNER_ACK_INVALID")
    if (
        value.schema != _RUNNER_ACK_SCHEMA
        or value.status != "local-detached-standby-replay-observed"
        or value.invocation_sha256 != invocation.invocation_sha256
        or value.target_pgdata_candidate != invocation.target_pgdata_candidate
        or type(value.target_pgdata_device) is not int
        or value.target_pgdata_device < 0
        or type(value.target_pgdata_inode) is not int
        or value.target_pgdata_inode < 1
        or type(value.recovery_readback_evidence) is not PhysicalPostgresRecoveryReceiverReadbackEvidence
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RUNNER_ACK_INVALID")
    target, device, inode = _private_child_directory(
        value.target_pgdata_candidate,
        root=target_root,
        code="WA_FI_FAILBACK_MATERIALIZATION_TARGET_UNSAFE",
    )
    if (
        target != invocation.target_pgdata_candidate
        or device != value.target_pgdata_device
        or inode != value.target_pgdata_inode
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_TARGET_UNSAFE")
    return value


def _require_replay_observation(
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pull: _PullFacts,
    ack: PhysicalWaFiPostgresFailbackMaterializationAck,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> PhysicalPostgresRecoveryPreflightResult:
    result = assess_physical_postgres_recovery_preflight(
        bundle=bundle,
        binding=pull.recovery_binding,
        receiver_readback_evidence=ack.recovery_readback_evidence,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )
    if (
        result.status != PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED
        or result.reason_codes != ()
        or result.stage_bundle_id != pull.bundle_id
        or result.stage_receipt_sha256 != pull.stage_receipt_sha256
        or result.route_binding_sha256 != pull.stage_route_binding_sha256
        or result.evidence_sha256 != ack.recovery_readback_evidence.evidence_sha256
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_REPLAY_NOT_OBSERVED")
    return result


def _receipt_mapping(
    *,
    facts: _RuntimeFacts,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pull: _PullFacts,
    term: _TermFacts,
    ack: PhysicalWaFiPostgresFailbackMaterializationAck,
    completed_at: datetime,
) -> dict[str, Any]:
    unsigned: dict[str, Any] = {
        "schema": PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RECEIPT_SCHEMA,
        "status": PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_STATUS,
        "campaign_id": facts.preflight.binding.campaign_id,
        "release_sha": facts.preflight.binding.release_sha,
        "bundle_id": pull.bundle_id,
        "stage_receipt_sha256": pull.stage_receipt_sha256,
        "stage_route_binding_sha256": pull.stage_route_binding_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "witnessed_term_proof_sha256": term.proof_sha256,
        "preflight_evidence_sha256": facts.preflight.observation.evidence_sha256,
        "writer_quiescence_receipt_sha256": facts.quiescence.receipt.receipt_sha256,
        "runner_profile_sha256": facts.runner_profile_sha256,
        "recovery_evidence_sha256": ack.recovery_readback_evidence.evidence_sha256,
        "target_pgdata_device": ack.target_pgdata_device,
        "target_pgdata_inode": ack.target_pgdata_inode,
        "completed_at": completed_at.isoformat(),
        "promotion_authorized": False,
        "writer_authorized": False,
        "traffic_switch_authorized": False,
        "full_matrix_authorized": False,
    }
    return {
        **unsigned,
        "receipt_integrity_sha256": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
    }


def _secure_receipts_directory(root: Path) -> Path:
    path = root / _RECEIPTS_DIRECTORY
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_WRITE_FAILED")
    return _private_root(path, code="WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_ROOT_UNSAFE")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_INVALID")
        result[key] = value
    return result


def _open_exact_receipt(path: Path, *, expected: bytes) -> bytes:
    descriptor = -1
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(descriptor)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_size != len(expected)
        ):
            _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_UNSAFE")
        chunks: list[bytes] = []
        remaining = len(expected)
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_UNSAFE")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_UNSAFE")
        after = os.fstat(descriptor)
        raw = b"".join(chunks)
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or raw != expected
        ):
            _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_UNSAFE")
        return raw
    except PhysicalWaFiPostgresFailbackMaterializationError:
        raise
    except OSError:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_or_verify_receipt(
    *,
    root: Path,
    bundle_id: str,
    mapping: Mapping[str, Any],
    recovery_evidence_sha256: str,
) -> PhysicalWaFiPostgresFailbackMaterializationDurableEvidence:
    if set(mapping) != _RECEIPT_FIELDS:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_INVALID")
    try:
        raw = canonical_json_bytes(dict(mapping))
    except (TypeError, ValueError):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_INVALID")
    digest = hashlib.sha256(raw).hexdigest()
    directory = _secure_receipts_directory(root)
    path = directory / (bundle_id + ".json")
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        created = True
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    except FileExistsError:
        pass
    except PhysicalWaFiPostgresFailbackMaterializationError:
        raise
    except OSError:
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if created:
        directory_fd = -1
        try:
            directory_fd = os.open(
                directory,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
            )
            os.fsync(directory_fd)
        except OSError:
            _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_WRITE_FAILED")
        finally:
            if directory_fd >= 0:
                os.close(directory_fd)
    exact = _open_exact_receipt(path, expected=raw)
    try:
        parsed = json.loads(exact.decode("ascii", "strict"), object_pairs_hook=_strict_object)
    except PhysicalWaFiPostgresFailbackMaterializationError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_INVALID")
    if (
        not isinstance(parsed, dict)
        or set(parsed) != _RECEIPT_FIELDS
        or canonical_json_bytes(parsed) != raw
        or parsed != dict(mapping)
    ):
        _fail("WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_INVALID")
    return PhysicalWaFiPostgresFailbackMaterializationDurableEvidence(
        receipt_path=path,
        raw_receipt=exact,
        receipt_sha256=digest,
        recovery_evidence_sha256=_sha256(
            recovery_evidence_sha256,
            code="WA_FI_FAILBACK_MATERIALIZATION_RECEIPT_INVALID",
        ),
    )


class RootOwnedWaFiPostgresFailbackMaterializationRuntime:
    """Inert construction plus one detached local FI replay observation."""

    def __init__(
        self,
        config: RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig,
        *,
        clock: Callable[[], datetime] | None,
    ) -> None:
        self._config = validate_root_owned_wa_fi_postgres_failback_materialization_runtime_config(
            config
        )
        self._clock = clock

    def _now(self) -> datetime:
        if self._clock is None or not callable(self._clock):
            _fail("WA_FI_FAILBACK_MATERIALIZATION_CLOCK_REQUIRED")
        try:
            return _utc(self._clock(), code="WA_FI_FAILBACK_MATERIALIZATION_CLOCK_INVALID")
        except PhysicalWaFiPostgresFailbackMaterializationError:
            raise
        except Exception:
            _fail("WA_FI_FAILBACK_MATERIALIZATION_CLOCK_INVALID")

    def materialize(
        self,
        *,
        bundle: object,
        pulled: object,
        current_witnessed_term: object,
        runner: object,
    ) -> PhysicalWaFiPostgresFailbackMaterializationResult:
        """Replay the exact staged bundle only into one detached FI candidate."""

        try:
            _require_root()
            started = self._now()
            facts = _facts(self._config, now=started, require_enabled=True)
            term = _term(current_witnessed_term, now=started)
            verified_bundle = _bundle(bundle, facts=facts, term=term)
            pull = _pull(pulled, facts=facts, bundle=verified_bundle, term=term, now=started)
            invocation = _invocation(facts=facts, bundle=verified_bundle, pull=pull, term=term)
            _assert_no_preexisting_materialization(facts=facts, pull=pull)
            method = getattr(runner, "materialize_and_inspect_detached_failback_standby", None)
            if not callable(method):
                _fail("WA_FI_FAILBACK_MATERIALIZATION_RUNNER_INVALID")
            try:
                reported = method(invocation=invocation, source_stage=pull.stage_evidence)
            except PhysicalWaFiPostgresFailbackMaterializationError:
                raise
            except Exception:
                _fail("WA_FI_FAILBACK_MATERIALIZATION_RUNNER_FAILED")
            ack = _ack(
                reported,
                invocation=invocation,
                target_root=facts.target_pgdata_candidates_root,
            )
            completed = self._now()
            if completed < started:
                _fail("WA_FI_FAILBACK_MATERIALIZATION_CLOCK_INVALID")
            completed_facts = _facts(self._config, now=completed, require_enabled=True)
            completed_term = _term(current_witnessed_term, now=completed)
            if (
                completed_facts.preflight != facts.preflight
                or completed_facts.preflight_config != facts.preflight_config
                or completed_facts.quiescence.receipt != facts.quiescence.receipt
                or completed_facts.runner_profile_sha256 != facts.runner_profile_sha256
                or completed_facts.source_stage_candidates_root != facts.source_stage_candidates_root
                or completed_facts.target_pgdata_candidates_root != facts.target_pgdata_candidates_root
                or completed_facts.redacted_receipt_root != facts.redacted_receipt_root
                or completed_facts.maximum_recovery_evidence_age_seconds
                != facts.maximum_recovery_evidence_age_seconds
            ):
                _fail("WA_FI_FAILBACK_MATERIALIZATION_POLICY_CHANGED")
            if not _same_term(term, completed_term):
                _fail("WA_FI_FAILBACK_MATERIALIZATION_WITNESS_TERM_CHANGED")
            verified_bundle = _bundle(bundle, facts=completed_facts, term=completed_term)
            pull = _pull(
                pulled,
                facts=completed_facts,
                bundle=verified_bundle,
                term=completed_term,
                now=completed,
            )
            _ack(
                ack,
                invocation=invocation,
                target_root=completed_facts.target_pgdata_candidates_root,
            )
            recovery = _require_replay_observation(
                bundle=verified_bundle,
                pull=pull,
                ack=ack,
                now=completed,
                maximum_evidence_age_seconds=completed_facts.maximum_recovery_evidence_age_seconds,
            )
            durable = _write_or_verify_receipt(
                root=completed_facts.redacted_receipt_root,
                bundle_id=pull.bundle_id,
                mapping=_receipt_mapping(
                    facts=completed_facts,
                    bundle=verified_bundle,
                    pull=pull,
                    term=completed_term,
                    ack=ack,
                    completed_at=completed,
                ),
                recovery_evidence_sha256=ack.recovery_readback_evidence.evidence_sha256,
            )
            return PhysicalWaFiPostgresFailbackMaterializationResult(
                schema=PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_STATUS,
                reason_codes=(),
                recovery_preflight=recovery,
                durable_evidence=durable,
                promotion_authorized=False,
                writer_authorized=False,
                traffic_switch_authorized=False,
                full_matrix_authorized=False,
            )
        except PhysicalWaFiPostgresFailbackMaterializationError as exc:
            return PhysicalWaFiPostgresFailbackMaterializationResult(
                schema=PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_STATUS_BLOCKED,
                reason_codes=(exc.code,),
            )
        except Exception:
            return PhysicalWaFiPostgresFailbackMaterializationResult(
                schema=PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_RUNTIME_SCHEMA,
                status=PHYSICAL_WA_FI_POSTGRES_FAILBACK_MATERIALIZATION_STATUS_BLOCKED,
                reason_codes=("WA_FI_FAILBACK_MATERIALIZATION_UNEXPECTED_FAILURE",),
            )


def run_root_owned_wa_fi_postgres_failback_materialization(
    *,
    config: RootOwnedWaFiPostgresFailbackMaterializationRuntimeConfig,
    bundle: object,
    pulled: object,
    current_witnessed_term: object,
    runner: object,
    now: datetime,
) -> PhysicalWaFiPostgresFailbackMaterializationResult:
    """One-shot no-network wrapper around the detached FI materializer."""

    runtime = RootOwnedWaFiPostgresFailbackMaterializationRuntime(config, clock=lambda: now)
    return runtime.materialize(
        bundle=bundle,
        pulled=pulled,
        current_witnessed_term=current_witnessed_term,
        runner=runner,
    )
