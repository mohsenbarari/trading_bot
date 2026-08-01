"""Fail-closed, adapter-only materialization of a frozen release inventory.

This module is intentionally a boundary rather than a concrete copier.  It
does not create a worktree, run Git, open a network connection, copy a file,
commit, build an image, seal a release, or invoke Full-Matrix.  A future
root-owned local implementation must inject narrowly scoped local Git,
quiescence, file-transfer, and target-readback adapters.

The only permitted overlay is the complete literal inventory from
``physical_release_candidate_inventory``.  A dirty source is acceptable only
when the already-frozen inventory says it was dirty *and* a separate local
writer-quiescence observation remains stable across the atomic overlay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from core.physical_release_candidate_inventory import (
    FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
    FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
    PhysicalReleaseCandidateFileObservation,
    PhysicalReleaseCandidateFileReader,
    PhysicalReleaseCandidateInventory,
    PhysicalReleaseCandidateInventoryConfig,
    PhysicalReleaseCandidateInventoryEntry,
    PhysicalReleaseCandidateSourceInspection,
    PhysicalReleaseCandidateSourceInspector,
    PhysicalReleaseCandidateSourceObject,
    REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
    parse_physical_release_candidate_inventory,
    verify_clean_physical_release_candidate_base,
    verify_physical_release_candidate_inventory,
)
from core.physical_release_candidate_writer_quiescence_receipt import (
    RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig,
    VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt,
    require_verified_physical_release_candidate_writer_quiescence_receipt,
    validate_root_owned_physical_release_candidate_writer_quiescence_receipt_verifier_config,
)


__all__ = (
    "DEFAULT_PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_MAX_QUIESCENCE_AGE_SECONDS",
    "PHYSICAL_RELEASE_CANDIDATE_ATOMIC_TRANSFER_SCHEMA",
    "PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_SCHEMA",
    "PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_DEFAULT_ENABLED",
    "PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_SCHEMA",
    "PHYSICAL_RELEASE_CANDIDATE_QUIESCENCE_SCHEMA",
    "PHYSICAL_RELEASE_CANDIDATE_TARGET_OVERLAY_SCHEMA",
    "PhysicalReleaseCandidateAtomicFileTransfer",
    "PhysicalReleaseCandidateAtomicTransferObservation",
    "PhysicalReleaseCandidateMaterializationAdapters",
    "PhysicalReleaseCandidateMaterializationConfig",
    "PhysicalReleaseCandidateMaterializationReceipt",
    "PhysicalReleaseCandidateMaterializationRequest",
    "PhysicalReleaseCandidateMaterializationResult",
    "PhysicalReleaseCandidateMaterializerError",
    "PhysicalReleaseCandidateQuiescenceObservation",
    "PhysicalReleaseCandidateQuiescenceObserver",
    "PhysicalReleaseCandidateTargetOverlayInspection",
    "PhysicalReleaseCandidateTargetOverlayInspector",
    "materialize_verified_physical_release_candidate",
    "parse_physical_release_candidate_materialization_receipt",
    "prepare_physical_release_candidate_materialization_adapters",
)


PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_SCHEMA = (
    "gold-trade-physical-release-candidate-materializer-v1"
)
PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_SCHEMA = (
    "gold-trade-physical-release-candidate-materialization-receipt-v1"
)
PHYSICAL_RELEASE_CANDIDATE_QUIESCENCE_SCHEMA = (
    "gold-trade-physical-release-candidate-quiescence-v1"
)
PHYSICAL_RELEASE_CANDIDATE_ATOMIC_TRANSFER_SCHEMA = (
    "gold-trade-physical-release-candidate-atomic-transfer-v1"
)
PHYSICAL_RELEASE_CANDIDATE_TARGET_OVERLAY_SCHEMA = (
    "gold-trade-physical-release-candidate-target-overlay-v1"
)
PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_MAX_QUIESCENCE_AGE_SECONDS = 120

_STATUS_QUIESCENT = "writers-quiesced-source-stable"
_STATUS_TRANSFERRED = "committed-atomic-exact-overlay"
_STATUS_TARGET_OBSERVED = "post-materialization-exact-overlay-observed"
_STATUS_MATERIALIZED = "materialized-clean-baseline-overlay-uncommitted"
_MAX_QUIESCENCE_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_ZERO_SHA256 = "0" * 64
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._/-]{0,511}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)


class PhysicalReleaseCandidateMaterializerError(ValueError):
    """One stable refusal from the physical release materialization boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalReleaseCandidateMaterializerError(code)


@dataclass(frozen=True)
class PhysicalReleaseCandidateQuiescenceObservation:
    """Redacted proof that local source writers were held quiescent."""

    schema: str
    status: str
    inventory_manifest_sha256: str
    baseline_release_sha: str
    baseline_git_tree_id: str
    quiescence_generation_sha256: str
    evidence_sha256: str
    observed_at: datetime
    writers_active: bool
    source_stable: bool
    writer_lease_state: str


class PhysicalReleaseCandidateQuiescenceObserver(Protocol):
    """Future local-only writer-fence observer; it performs no transfer."""

    def observe_quiescence(
        self, *, source_root: Path, inventory_manifest_sha256: str
    ) -> PhysicalReleaseCandidateQuiescenceObservation:
        """Observe a local quiescent generation bound to this frozen inventory."""


@dataclass(frozen=True)
class PhysicalReleaseCandidateMaterializationRequest:
    """Exact bounded source/target transfer request for one frozen inventory."""

    source_root: Path
    target_root: Path
    inventory_manifest_sha256: str
    target_baseline_binding_sha256: str
    source_quiescence_generation_sha256: str
    source_quiescence_evidence_sha256: str
    entries: tuple[PhysicalReleaseCandidateInventoryEntry, ...]


@dataclass(frozen=True)
class PhysicalReleaseCandidateAtomicTransferObservation:
    """Redacted result from an injected whole-overlay atomic transfer adapter."""

    schema: str
    status: str
    inventory_manifest_sha256: str
    target_baseline_binding_sha256: str
    source_quiescence_generation_sha256: str
    source_quiescence_evidence_sha256: str
    transfer_evidence_sha256: str
    materialized_paths: tuple[str, ...]
    unexpected_paths: tuple[str, ...]
    atomically_committed: bool
    source_read_no_follow: bool
    target_write_no_follow: bool
    target_git_commit_created: bool


class PhysicalReleaseCandidateAtomicFileTransfer(Protocol):
    """Future local-only copier that must commit one exact overlay atomically."""

    def materialize_exact_overlay(
        self, *, request: PhysicalReleaseCandidateMaterializationRequest
    ) -> PhysicalReleaseCandidateAtomicTransferObservation:
        """Transfer only request entries using no-follow reads/writes and an atomic commit."""


@dataclass(frozen=True)
class PhysicalReleaseCandidateTargetOverlayInspection:
    """Independent, complete local-Git observation after the atomic overlay."""

    schema: str
    status: str
    target_root: PhysicalReleaseCandidateSourceObject
    baseline_release_sha: str
    baseline_git_tree_id: str
    stable: bool
    complete_changed_path_observation: bool
    no_symlink_paths: bool
    changed_paths: tuple[str, ...]
    evidence_sha256: str
    target_git_commit_created: bool
    release_seal_created: bool


class PhysicalReleaseCandidateTargetOverlayInspector(Protocol):
    """Future local-only Git delta reader; it must include tracked and untracked paths."""

    def inspect_overlay(
        self,
        *,
        target_root: Path,
        expected_baseline_sha: str,
        expected_baseline_tree: str,
    ) -> PhysicalReleaseCandidateTargetOverlayInspection:
        """Return the complete target worktree delta without modifying it."""


@dataclass(frozen=True)
class PhysicalReleaseCandidateMaterializationAdapters:
    """Every live local boundary is explicit; defaults deliberately do nothing."""

    source_git_inspector: PhysicalReleaseCandidateSourceInspector | None = None
    target_git_inspector: PhysicalReleaseCandidateSourceInspector | None = None
    source_file_reader: PhysicalReleaseCandidateFileReader | None = None
    target_file_reader: PhysicalReleaseCandidateFileReader | None = None
    quiescence_observer: PhysicalReleaseCandidateQuiescenceObserver | None = None
    atomic_file_transfer: PhysicalReleaseCandidateAtomicFileTransfer | None = None
    target_overlay_inspector: PhysicalReleaseCandidateTargetOverlayInspector | None = None


@dataclass(frozen=True)
class PhysicalReleaseCandidateMaterializationConfig:
    """Root-only, default-off configuration with no release/deploy authority."""

    inventory: PhysicalReleaseCandidateInventory | None = None
    source_inventory_config: PhysicalReleaseCandidateInventoryConfig | None = None
    target_baseline_config: PhysicalReleaseCandidateInventoryConfig | None = None
    enabled: bool = PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_DEFAULT_ENABLED
    maximum_quiescence_age_seconds: int = (
        DEFAULT_PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_MAX_QUIESCENCE_AGE_SECONDS
    )
    writer_quiescence_receipt_verifier_config: (
        RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig | None
    ) = field(default=None, repr=False, compare=False)
    verified_writer_quiescence_receipt: (
        VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt | None
    ) = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalReleaseCandidateMaterializationReceipt:
    """Canonical redacted evidence of an uncommitted exact target overlay."""

    canonical_receipt: bytes
    receipt_sha256: str
    inventory_manifest_sha256: str
    target_rehash_sha256: str
    materialized_entry_count: int
    recorded_at: datetime
    release_authorized: bool = False
    image_build_authorized: bool = False
    execution_authorized: bool = False


@dataclass(frozen=True)
class PhysicalReleaseCandidateMaterializationResult:
    """Successful local overlay evidence, never a release, image, or deployment permit."""

    status: str
    receipt: PhysicalReleaseCandidateMaterializationReceipt
    overlay_materialized: bool
    release_authorized: bool = False
    image_build_authorized: bool = False
    execution_authorized: bool = False


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalReleaseCandidateMaterializerError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if type(key) is not str or key in value:
            _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_JSON_INVALID")
        value[key] = item
    return value


def _require_sha256(value: object, *, code: str, allow_zero: bool = False) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None:
        _fail(code)
    if not allow_zero and value == _ZERO_SHA256:
        _fail(code)
    return value


def _require_safe_relative_path(value: object, *, code: str) -> str:
    if type(value) is not str or _SAFE_PATH_RE.fullmatch(value) is None:
        _fail(code)
    if value.startswith("/") or "//" in value:
        _fail(code)
    if any(part in {"", ".", ".."} for part in value.split("/")):
        _fail(code)
    return value


def _require_root(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or value == Path("/"):
        _fail(code)
    if ".." in value.parts:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime) -> str:
    return _utc(
        value, code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_CLOCK_INVALID"
    ).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        result = _utc(datetime.fromisoformat(value.replace("Z", "+00:00")), code=code)
    except ValueError:
        _fail(code)
    if _render_timestamp(result) != value:
        _fail(code)
    return result


def _require_inventory(value: object) -> PhysicalReleaseCandidateInventory:
    if type(value) is not PhysicalReleaseCandidateInventory:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_INVENTORY_INVALID")
    parsed = parse_physical_release_candidate_inventory(value.canonical_manifest)
    if parsed != value:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_INVENTORY_OBJECT_MISMATCH")
    return parsed


def _require_inventory_config(
    value: object,
    *,
    code: str,
    allow_dirty_staging_source: bool,
) -> PhysicalReleaseCandidateInventoryConfig:
    if type(value) is not PhysicalReleaseCandidateInventoryConfig:
        _fail(code)
    if value.enabled is not True:
        _fail(code)
    _require_root(value.source_root, code=code)
    if (
        value.expected_baseline_sha != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA
        or value.expected_baseline_tree != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE
        or value.allow_dirty_staging_source is not allow_dirty_staging_source
    ):
        _fail(code)
    return value


def _maximum_quiescence_age(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_QUIESCENCE_AGE_SECONDS:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_AGE_INVALID")
    return value


def _normalise_config(
    config: object,
) -> tuple[
    PhysicalReleaseCandidateInventory,
    PhysicalReleaseCandidateInventoryConfig,
    PhysicalReleaseCandidateInventoryConfig,
    int,
    RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig,
    VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt,
]:
    if type(config) is not PhysicalReleaseCandidateMaterializationConfig:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_CONFIG_INVALID")
    if config.enabled is not True:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_DISABLED")
    if os.geteuid() != 0:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_ROOT_RUNTIME_REQUIRED")
    frozen = _require_inventory(config.inventory)
    source = _require_inventory_config(
        config.source_inventory_config,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_SOURCE_CONFIG_INVALID",
        allow_dirty_staging_source=frozen.source_dirty_at_capture,
    )
    target = _require_inventory_config(
        config.target_baseline_config,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_CONFIG_INVALID",
        allow_dirty_staging_source=False,
    )
    if source.source_root == target.source_root:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_SOURCE_TARGET_ALIAS")
    maximum_age = _maximum_quiescence_age(config.maximum_quiescence_age_seconds)
    if (
        type(config.writer_quiescence_receipt_verifier_config)
        is not RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_RECEIPT_CONFIG_INVALID")
    if (
        type(config.verified_writer_quiescence_receipt)
        is not VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_RECEIPT_REQUIRED")
    try:
        verifier_config = (
            validate_root_owned_physical_release_candidate_writer_quiescence_receipt_verifier_config(
                config.writer_quiescence_receipt_verifier_config
            )
        )
    except Exception:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_RECEIPT_CONFIG_INVALID")
    if (
        verifier_config.enabled is not True
        or verifier_config.maximum_receipt_age_seconds > maximum_age
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_RECEIPT_CONFIG_INVALID")
    return (
        frozen,
        source,
        target,
        maximum_age,
        verifier_config,
        config.verified_writer_quiescence_receipt,
    )


def prepare_physical_release_candidate_materialization_adapters(
    *, adapters: PhysicalReleaseCandidateMaterializationAdapters
) -> None:
    """Reject missing, conflated, or non-callable future local adapter slots."""

    if type(adapters) is not PhysicalReleaseCandidateMaterializationAdapters:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_ADAPTERS_INVALID")
    required = (
        (adapters.source_git_inspector, "inspect_source"),
        (adapters.target_git_inspector, "inspect_source"),
        (adapters.source_file_reader, "read_file"),
        (adapters.target_file_reader, "read_file"),
        (adapters.quiescence_observer, "observe_quiescence"),
        (adapters.atomic_file_transfer, "materialize_exact_overlay"),
        (adapters.target_overlay_inspector, "inspect_overlay"),
    )
    if any(not callable(getattr(adapter, method, None)) for adapter, method in required):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_ADAPTER_MISSING")
    if adapters.source_git_inspector is adapters.target_git_inspector:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_NOT_INDEPENDENT")
    if adapters.source_file_reader is adapters.target_file_reader:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_REHASH_NOT_INDEPENDENT")
    if (
        adapters.atomic_file_transfer is adapters.target_file_reader
        or adapters.atomic_file_transfer is adapters.target_overlay_inspector
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_REHASH_NOT_INDEPENDENT")


def _validate_quiescence(
    *,
    value: object,
    inventory: PhysicalReleaseCandidateInventory,
    now: datetime,
    maximum_age: int,
) -> PhysicalReleaseCandidateQuiescenceObservation:
    if type(value) is not PhysicalReleaseCandidateQuiescenceObservation:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_INVALID")
    if (
        value.schema != PHYSICAL_RELEASE_CANDIDATE_QUIESCENCE_SCHEMA
        or value.status != _STATUS_QUIESCENT
        or value.inventory_manifest_sha256 != inventory.manifest_sha256
        or value.baseline_release_sha != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA
        or value.baseline_git_tree_id != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE
        or value.writers_active is not False
        or value.source_stable is not True
        or value.writer_lease_state != "quiesced-no-writers"
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_REJECTED")
    _require_sha256(
        value.quiescence_generation_sha256,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_REJECTED",
    )
    _require_sha256(
        value.evidence_sha256,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_REJECTED",
    )
    observed_at = _utc(
        value.observed_at,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_CLOCK_INVALID",
    )
    if observed_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_FUTURE")
    if now - observed_at > timedelta(seconds=maximum_age):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_STALE")
    return value


def _require_writer_quiescence_receipt_gate(
    *,
    value: VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt,
    config: RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig,
    source_root: Path,
    inventory: PhysicalReleaseCandidateInventory,
    quiescence: PhysicalReleaseCandidateQuiescenceObservation,
    now: datetime,
) -> VerifiedPhysicalReleaseCandidateWriterQuiescenceReceipt:
    """Consume only separately verified signed source-fence evidence.

    The verifier itself is intentionally pure.  This boundary does not infer
    writer state from Git, mtime, a source path, or any local filesystem fact.
    """

    try:
        return require_verified_physical_release_candidate_writer_quiescence_receipt(
            value,
            config=config,
            source_root=source_root,
            inventory_manifest_sha256=inventory.manifest_sha256,
            frozen_generation_sha256=quiescence.quiescence_generation_sha256,
            quiescence_evidence_sha256=quiescence.evidence_sha256,
            now=now,
        )
    except Exception:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_RECEIPT_REJECTED")


def _validate_target_root(
    *,
    value: object,
    target_root: Path,
    require_clean: bool,
    code: str,
) -> PhysicalReleaseCandidateSourceInspection:
    if type(value) is not PhysicalReleaseCandidateSourceInspection:
        _fail(code)
    source = value.source_root
    if type(source) is not PhysicalReleaseCandidateSourceObject:
        _fail(code)
    if (
        source.path != target_root
        or source.directory is not True
        or source.symlink is not False
        or source.owner_uid != 0
        or source.ancestors_root_controlled is not True
        or source.mode & 0o022
        or value.release_sha != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA
        or value.git_tree_id != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE
        or value.stable is not True
        or (require_clean and value.clean is not True)
    ):
        _fail(code)
    return value


def _target_baseline_binding(inspection: PhysicalReleaseCandidateSourceInspection) -> str:
    source = inspection.source_root
    body = {
        "schema": PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_SCHEMA,
        "baseline_release_sha": inspection.release_sha,
        "baseline_git_tree_id": inspection.git_tree_id,
        "clean": inspection.clean,
        "stable": inspection.stable,
        "owner_uid": source.owner_uid,
        "mode": source.mode,
        "directory": source.directory,
        "symlink": source.symlink,
        "ancestors_root_controlled": source.ancestors_root_controlled,
    }
    return hashlib.sha256(
        _canonical(body, code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_BINDING_INVALID")
    ).hexdigest()


def _normalise_path_tuple(value: object, *, code: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        _fail(code)
    paths = tuple(_require_safe_relative_path(item, code=code) for item in value)
    if len(paths) != len(set(paths)):
        _fail(code)
    return paths


def _validate_transfer(
    *,
    value: object,
    request: PhysicalReleaseCandidateMaterializationRequest,
) -> PhysicalReleaseCandidateAtomicTransferObservation:
    if type(value) is not PhysicalReleaseCandidateAtomicTransferObservation:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TRANSFER_INVALID")
    if (
        value.schema != PHYSICAL_RELEASE_CANDIDATE_ATOMIC_TRANSFER_SCHEMA
        or value.status != _STATUS_TRANSFERRED
        or value.inventory_manifest_sha256 != request.inventory_manifest_sha256
        or value.target_baseline_binding_sha256 != request.target_baseline_binding_sha256
        or value.source_quiescence_generation_sha256
        != request.source_quiescence_generation_sha256
        or value.source_quiescence_evidence_sha256
        != request.source_quiescence_evidence_sha256
        or value.atomically_committed is not True
        or value.source_read_no_follow is not True
        or value.target_write_no_follow is not True
        or value.target_git_commit_created is not False
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TRANSFER_REJECTED")
    _require_sha256(
        value.transfer_evidence_sha256,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TRANSFER_REJECTED",
    )
    expected_paths = tuple(entry.relative_path for entry in request.entries)
    if _normalise_path_tuple(
        value.materialized_paths,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TRANSFER_PATHS_INVALID",
    ) != expected_paths:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TRANSFER_PATHS_INVALID")
    if _normalise_path_tuple(
        value.unexpected_paths,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TRANSFER_PATHS_INVALID",
    ) != ():
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TRANSFER_EXTRA_PATHS")
    return value


def _target_rehash(
    *,
    target_root: Path,
    entries: tuple[PhysicalReleaseCandidateInventoryEntry, ...],
    reader: PhysicalReleaseCandidateFileReader,
) -> str:
    observed: list[dict[str, object]] = []
    for entry in entries:
        value = reader.read_file(
            source_root=target_root,
            relative_path=entry.relative_path,
        )
        if type(value) is not PhysicalReleaseCandidateFileObservation:
            _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_REHASH_INVALID")
        if (
            _require_safe_relative_path(
                value.relative_path,
                code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_PATH_INVALID",
            )
            != entry.relative_path
            or value.owner_uid != 0
            or value.mode != int(entry.mode, 8)
            or value.regular_file is not True
            or value.symlink is not False
            or value.stable is not True
            or not isinstance(value.content, bytes)
            or len(value.content) != entry.size_bytes
            or hashlib.sha256(value.content).hexdigest() != entry.sha256
        ):
            _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_REHASH_MISMATCH")
        observed.append(
            {
                "path": entry.relative_path,
                "mode": entry.mode,
                "size_bytes": entry.size_bytes,
                "sha256": entry.sha256,
            }
        )
    return hashlib.sha256(
        _canonical(
            observed,
            code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_REHASH_INVALID",
        )
    ).hexdigest()


def _validate_target_overlay(
    *,
    value: object,
    target_root: Path,
    entries: tuple[PhysicalReleaseCandidateInventoryEntry, ...],
) -> PhysicalReleaseCandidateTargetOverlayInspection:
    if type(value) is not PhysicalReleaseCandidateTargetOverlayInspection:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_OVERLAY_INVALID")
    if (
        value.schema != PHYSICAL_RELEASE_CANDIDATE_TARGET_OVERLAY_SCHEMA
        or value.status != _STATUS_TARGET_OBSERVED
        or value.baseline_release_sha != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA
        or value.baseline_git_tree_id != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE
        or value.stable is not True
        or value.complete_changed_path_observation is not True
        or value.no_symlink_paths is not True
        or value.target_git_commit_created is not False
        or value.release_seal_created is not False
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_OVERLAY_REJECTED")
    _validate_target_root(
        value=PhysicalReleaseCandidateSourceInspection(
            source_root=value.target_root,
            release_sha=value.baseline_release_sha,
            git_tree_id=value.baseline_git_tree_id,
            clean=False,
            stable=value.stable,
        ),
        target_root=target_root,
        require_clean=False,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_OVERLAY_REJECTED",
    )
    changed_paths = _normalise_path_tuple(
        value.changed_paths,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_OVERLAY_PATHS_INVALID",
    )
    expected_paths = {entry.relative_path for entry in entries}
    if set(changed_paths) - expected_paths:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_OVERLAY_EXTRA_PATHS")
    _require_sha256(
        value.evidence_sha256,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_OVERLAY_REJECTED",
    )
    return value


_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "inventory_manifest_sha256",
        "baseline_release_sha",
        "baseline_git_tree_id",
        "source_quiescence_generation_sha256",
        "source_quiescence_evidence_sha256",
        "target_baseline_binding_sha256",
        "transfer_evidence_sha256",
        "target_overlay_evidence_sha256",
        "target_rehash_sha256",
        "materialized_entry_count",
        "materialized_total_bytes",
        "recorded_at",
        "atomically_committed",
        "target_git_commit_created",
        "release_seal_created",
        "image_build_authorized",
        "release_authorized",
        "execution_authorized",
        "receipt_sha256",
    }
)


def _receipt_body(
    *,
    inventory: PhysicalReleaseCandidateInventory,
    quiescence: PhysicalReleaseCandidateQuiescenceObservation,
    target_baseline_binding_sha256: str,
    transfer: PhysicalReleaseCandidateAtomicTransferObservation,
    overlay: PhysicalReleaseCandidateTargetOverlayInspection,
    target_rehash_sha256: str,
    recorded_at: datetime,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_SCHEMA,
        "status": _STATUS_MATERIALIZED,
        "inventory_manifest_sha256": inventory.manifest_sha256,
        "baseline_release_sha": FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
        "baseline_git_tree_id": FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
        "source_quiescence_generation_sha256": quiescence.quiescence_generation_sha256,
        "source_quiescence_evidence_sha256": quiescence.evidence_sha256,
        "target_baseline_binding_sha256": target_baseline_binding_sha256,
        "transfer_evidence_sha256": transfer.transfer_evidence_sha256,
        "target_overlay_evidence_sha256": overlay.evidence_sha256,
        "target_rehash_sha256": target_rehash_sha256,
        "materialized_entry_count": len(inventory.entries),
        "materialized_total_bytes": sum(entry.size_bytes for entry in inventory.entries),
        "recorded_at": _render_timestamp(recorded_at),
        "atomically_committed": True,
        "target_git_commit_created": False,
        "release_seal_created": False,
        "image_build_authorized": False,
        "release_authorized": False,
        "execution_authorized": False,
    }


def _receipt_from_value(value: dict[str, object]) -> PhysicalReleaseCandidateMaterializationReceipt:
    body = dict(value)
    receipt_sha256 = body.pop("receipt_sha256")
    canonical = _canonical(
        {**body, "receipt_sha256": receipt_sha256},
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_INVALID",
    ) + b"\n"
    return PhysicalReleaseCandidateMaterializationReceipt(
        canonical_receipt=canonical,
        receipt_sha256=str(receipt_sha256),
        inventory_manifest_sha256=str(value["inventory_manifest_sha256"]),
        target_rehash_sha256=str(value["target_rehash_sha256"]),
        materialized_entry_count=int(value["materialized_entry_count"]),
        recorded_at=_parse_timestamp(
            value["recorded_at"],
            code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_CLOCK_INVALID",
        ),
    )


def _make_receipt(
    *,
    inventory: PhysicalReleaseCandidateInventory,
    quiescence: PhysicalReleaseCandidateQuiescenceObservation,
    target_baseline_binding_sha256: str,
    transfer: PhysicalReleaseCandidateAtomicTransferObservation,
    overlay: PhysicalReleaseCandidateTargetOverlayInspection,
    target_rehash_sha256: str,
    recorded_at: datetime,
) -> PhysicalReleaseCandidateMaterializationReceipt:
    body = _receipt_body(
        inventory=inventory,
        quiescence=quiescence,
        target_baseline_binding_sha256=target_baseline_binding_sha256,
        transfer=transfer,
        overlay=overlay,
        target_rehash_sha256=target_rehash_sha256,
        recorded_at=recorded_at,
    )
    value = dict(body)
    value["receipt_sha256"] = hashlib.sha256(
        _canonical(body, code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_INVALID")
    ).hexdigest()
    return _receipt_from_value(value)


def parse_physical_release_candidate_materialization_receipt(
    value: object,
) -> PhysicalReleaseCandidateMaterializationReceipt:
    """Parse one complete canonical, redacted, non-authorizing materialization receipt."""

    if not isinstance(value, bytes) or not value.endswith(b"\n"):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_ENCODING_INVALID")
    try:
        decoded = json.loads(
            value[:-1].decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _: (_fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_JSON_INVALID")),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        PhysicalReleaseCandidateMaterializerError,
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_ENCODING_INVALID")
    if not isinstance(decoded, dict) or set(decoded) != _RECEIPT_FIELDS:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_FIELDS_INVALID")
    if (
        decoded["schema"] != PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_SCHEMA
        or decoded["status"] != _STATUS_MATERIALIZED
        or decoded["baseline_release_sha"] != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA
        or decoded["baseline_git_tree_id"] != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE
        or decoded["atomically_committed"] is not True
        or decoded["target_git_commit_created"] is not False
        or decoded["release_seal_created"] is not False
        or decoded["image_build_authorized"] is not False
        or decoded["release_authorized"] is not False
        or decoded["execution_authorized"] is not False
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_BINDING_INVALID")
    for field_name in (
        "inventory_manifest_sha256",
        "source_quiescence_generation_sha256",
        "source_quiescence_evidence_sha256",
        "target_baseline_binding_sha256",
        "transfer_evidence_sha256",
        "target_overlay_evidence_sha256",
        "target_rehash_sha256",
        "receipt_sha256",
    ):
        _require_sha256(
            decoded[field_name],
            code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_HASH_INVALID",
        )
    if (
        type(decoded["materialized_entry_count"]) is not int
        or decoded["materialized_entry_count"] != len(REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS)
        or type(decoded["materialized_total_bytes"]) is not int
        or not 0 <= decoded["materialized_total_bytes"] <= _MAX_TOTAL_BYTES
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_COUNTS_INVALID")
    _parse_timestamp(
        decoded["recorded_at"],
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_CLOCK_INVALID",
    )
    body = dict(decoded)
    receipt_sha256 = body.pop("receipt_sha256")
    if hashlib.sha256(
        _canonical(body, code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_INVALID")
    ).hexdigest() != receipt_sha256:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_DIGEST_MISMATCH")
    result = _receipt_from_value(decoded)
    if result.canonical_receipt != value:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_RECEIPT_NONCANONICAL")
    return result


def materialize_verified_physical_release_candidate(
    *,
    config: PhysicalReleaseCandidateMaterializationConfig,
    adapters: PhysicalReleaseCandidateMaterializationAdapters,
    now: datetime,
) -> PhysicalReleaseCandidateMaterializationResult:
    """Materialize exactly one frozen inventory through future local-only adapters.

    The function deliberately performs no implicit adapter construction.  A
    caller must separately provide a live, root-owned quiescent source and a
    distinct clean target.  It never commits, seals, builds, deploys, or starts
    Full-Matrix.
    """

    (
        inventory,
        source_config,
        target_config,
        maximum_age,
        quiescence_receipt_verifier_config,
        verified_quiescence_receipt,
    ) = _normalise_config(config)
    prepare_physical_release_candidate_materialization_adapters(adapters=adapters)
    observed_now = _utc(
        now, code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZATION_CLOCK_INVALID"
    )
    source_root = source_config.source_root
    target_root = target_config.source_root
    assert source_root is not None and target_root is not None

    quiescence_before = _validate_quiescence(
        value=adapters.quiescence_observer.observe_quiescence(
            source_root=source_root,
            inventory_manifest_sha256=inventory.manifest_sha256,
        ),
        inventory=inventory,
        now=observed_now,
        maximum_age=maximum_age,
    )
    _require_writer_quiescence_receipt_gate(
        value=verified_quiescence_receipt,
        config=quiescence_receipt_verifier_config,
        source_root=source_root,
        inventory=inventory,
        quiescence=quiescence_before,
        now=observed_now,
    )
    # This re-reads every selected source byte and uses two source inspections.
    # It rejects a stale/tampered frozen manifest before the target is touched.
    verify_physical_release_candidate_inventory(
        inventory=inventory,
        config=source_config,
        source_inspector=adapters.source_git_inspector,
        file_reader=adapters.source_file_reader,
    )
    # The public inventory predicate is intentionally called in addition to
    # retaining a binding for the atomic transfer.  The latter protects the
    # adapter boundary from swapping a target after this clean-baseline check.
    verify_clean_physical_release_candidate_base(
        config=target_config,
        source_inspector=adapters.target_git_inspector,
    )
    target_before = _validate_target_root(
        value=adapters.target_git_inspector.inspect_source(source_root=target_root),
        target_root=target_root,
        require_clean=True,
        code="PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_TARGET_BASELINE_REJECTED",
    )
    target_baseline_binding_sha256 = _target_baseline_binding(target_before)
    request = PhysicalReleaseCandidateMaterializationRequest(
        source_root=source_root,
        target_root=target_root,
        inventory_manifest_sha256=inventory.manifest_sha256,
        target_baseline_binding_sha256=target_baseline_binding_sha256,
        source_quiescence_generation_sha256=quiescence_before.quiescence_generation_sha256,
        source_quiescence_evidence_sha256=quiescence_before.evidence_sha256,
        entries=inventory.entries,
    )
    transfer = _validate_transfer(
        value=adapters.atomic_file_transfer.materialize_exact_overlay(request=request),
        request=request,
    )
    target_rehash_sha256 = _target_rehash(
        target_root=target_root,
        entries=inventory.entries,
        reader=adapters.target_file_reader,
    )
    overlay = _validate_target_overlay(
        value=adapters.target_overlay_inspector.inspect_overlay(
            target_root=target_root,
            expected_baseline_sha=FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
            expected_baseline_tree=FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
        ),
        target_root=target_root,
        entries=inventory.entries,
    )
    quiescence_after = _validate_quiescence(
        value=adapters.quiescence_observer.observe_quiescence(
            source_root=source_root,
            inventory_manifest_sha256=inventory.manifest_sha256,
        ),
        inventory=inventory,
        now=observed_now,
        maximum_age=maximum_age,
    )
    if (
        quiescence_after.quiescence_generation_sha256
        != quiescence_before.quiescence_generation_sha256
        or quiescence_after.evidence_sha256 != quiescence_before.evidence_sha256
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MATERIALIZER_QUIESCENCE_CHANGED_DURING_TRANSFER")
    _require_writer_quiescence_receipt_gate(
        value=verified_quiescence_receipt,
        config=quiescence_receipt_verifier_config,
        source_root=source_root,
        inventory=inventory,
        quiescence=quiescence_after,
        now=observed_now,
    )
    receipt = _make_receipt(
        inventory=inventory,
        quiescence=quiescence_before,
        target_baseline_binding_sha256=target_baseline_binding_sha256,
        transfer=transfer,
        overlay=overlay,
        target_rehash_sha256=target_rehash_sha256,
        recorded_at=observed_now,
    )
    # Parse our own artifact before exposing it, ensuring the receipt is
    # canonical and contains no authorization bit that a caller could mistake
    # for a release or Full-Matrix permit.
    parsed_receipt = parse_physical_release_candidate_materialization_receipt(
        receipt.canonical_receipt
    )
    return PhysicalReleaseCandidateMaterializationResult(
        status=_STATUS_MATERIALIZED,
        receipt=parsed_receipt,
        overlay_materialized=True,
    )
