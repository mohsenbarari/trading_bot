"""Fail-closed Release-0 foundation inventory and materialization proof.

This module is intentionally a *selection* boundary.  It neither opens a
directory nor runs Git, copies a file, invokes a process, contacts a host,
builds an image, changes DNS, deploys a service, or authorizes a writer.  A
root-controlled caller supplies already-collected observations through small
protocols.  The only source bytes it can describe are the literal, digest
locked Release-0 foundation files below.

The audited three-site checkpoint was used once as review evidence for the
five file digests.  It is not imported, checked out, merged, or otherwise
treated as a release.  Any changed byte, mode, path, baseline identity, or
post-overlay path set is a refusal.

This is deliberately not a complete Release-0 implementation.  In
particular, it excludes Full-Matrix V4/V2R, experimental migration work,
retired two-site/failback activation paths, and unresolved review-only
writer-authority code.  A successful inventory or readback receipt has every
authorization field hard-coded to ``False``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol


__all__ = (
    "FIXED_RELEASE0_CANDIDATE_BASELINE_SHA",
    "FIXED_RELEASE0_CANDIDATE_BASELINE_TREE",
    "RELEASE0_CANDIDATE_DEFAULT_ENABLED",
    "RELEASE0_CANDIDATE_INVENTORY_SCHEMA",
    "RELEASE0_CANDIDATE_MATERIALIZATION_PLAN_SCHEMA",
    "RELEASE0_CANDIDATE_READBACK_RECEIPT_SCHEMA",
    "RELEASE0_CANDIDATE_SELECTION_PROFILE_SHA256",
    "RELEASE0_CANDIDATE_SELECTED_PATHS",
    "Release0CandidateError",
    "Release0CandidateFileObservation",
    "Release0CandidateFileReader",
    "Release0CandidateInventory",
    "Release0CandidateInventoryConfig",
    "Release0CandidateInventoryEntry",
    "Release0CandidateMaterializationPlan",
    "Release0CandidateReadbackReceipt",
    "Release0CandidateSourceInspection",
    "Release0CandidateSourceInspector",
    "Release0CandidateTargetInspection",
    "Release0CandidateTargetOverlayObservation",
    "build_release0_candidate_inventory",
    "classify_release0_candidate_path",
    "parse_release0_candidate_inventory",
    "prepare_release0_candidate_materialization",
    "verify_release0_candidate_inventory",
    "verify_release0_candidate_materialization_readback",
    "verify_release0_candidate_source",
)


FIXED_RELEASE0_CANDIDATE_BASELINE_SHA = "6091a020b9c66753af135e3a4dcaa919e6bd049d"
FIXED_RELEASE0_CANDIDATE_BASELINE_TREE = "bc91aee560d34e6f77dcbce0da287c38d8a1b95a"

RELEASE0_CANDIDATE_INVENTORY_SCHEMA = "gold-trade-release0-candidate-inventory-v1"
RELEASE0_CANDIDATE_MATERIALIZATION_PLAN_SCHEMA = (
    "gold-trade-release0-candidate-materialization-plan-v1"
)
RELEASE0_CANDIDATE_READBACK_RECEIPT_SCHEMA = (
    "gold-trade-release0-candidate-readback-receipt-v1"
)
RELEASE0_CANDIDATE_DEFAULT_ENABLED = False

_INVENTORY_STATUS = "draft-digest-locked-release0-foundation-not-authorized"
_PLAN_STATUS = "prepared-exact-release0-foundation-overlay-not-authorized"
_READBACK_STATUS = "observed-exact-release0-foundation-overlay-not-authorized"
_AUDITED_SOURCE_STATUS = "audited-digest-locked-source"
_TARGET_OVERLAY_STATUS = "exact-release0-foundation-overlay-observed"
_MAX_FILE_BYTES = 64 * 1024 * 1024
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._/-]{0,511}$", re.ASCII)
_GROUP_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$", re.ASCII)
_ALLOWED_MODE = 0o644


class Release0CandidateError(ValueError):
    """Stable refusal emitted by the bounded Release-0 selection boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise Release0CandidateError(code)


@dataclass(frozen=True)
class _Release0PathSpec:
    """One reviewed byte-for-byte Release-0 foundation input."""

    group: str
    relative_path: str
    expected_sha256: str
    expected_bytes: int
    expected_mode: int = _ALLOWED_MODE


# This is intentionally a tiny, literal set rather than a directory, glob, or
# dependency discovery mechanism.  The SHA-256 values are the audited evidence
# values; changing a selected file requires a new focused review and a new
# selection profile, never a silent refresh of this list.
_RELEASE0_CANDIDATE_PATH_SPECS: tuple[_Release0PathSpec, ...] = (
    _Release0PathSpec(
        group="writer-term-safety",
        relative_path="core/application_writer_term.py",
        expected_sha256="000cc65c4ef1bf77e68e9f59be4d77b255ce3d26f0d066fbf7a1c191c29b1a6d",
        expected_bytes=10313,
    ),
    _Release0PathSpec(
        group="writer-term-safety",
        relative_path="core/external_effect_execution_gate.py",
        expected_sha256="c4bc72f956a684b9b7063a874f46393b6dc9bbe172c55ff3088e14e8ed57f082",
        expected_bytes=35581,
    ),
    _Release0PathSpec(
        group="dark-standby-preflight",
        relative_path="core/webapp_ir_dark_snapshot_preflight.py",
        expected_sha256="52148650f5d7f1f5c37b0b6724499f0fd17869d121ad053bf18107b4b2b9c926",
        expected_bytes=8134,
    ),
    _Release0PathSpec(
        group="writer-term-safety",
        relative_path="scripts/preflight_fenced_fi_writer.py",
        expected_sha256="009f8ef337d43cb099bc173e888761bbff19700c92ed9166401ad4ec1fd19ba8",
        expected_bytes=38160,
    ),
    _Release0PathSpec(
        group="dark-standby-preflight",
        relative_path="scripts/preflight_webapp_ir_dark_snapshot_standby.py",
        expected_sha256="2976ad7bc07a9964dbfa72b5a02c02d589797a53677c9caf6fe3f37794ce0ed4",
        expected_bytes=9773,
    ),
)

RELEASE0_CANDIDATE_SELECTED_PATHS = tuple(
    spec.relative_path for spec in _RELEASE0_CANDIDATE_PATH_SPECS
)

# These are diagnostic deny rules only.  The literal allow-list above is the
# actual authority: every path not in it is refused even if it does not match a
# listed prefix.  Keeping the high-risk families named produces actionable,
# stable refusals instead of a generic "not selected" response.
_FULL_MATRIX_V4_PREFIXES = (
    "core/physical_full_matrix_v4",
    "models/physical_full_matrix_v4",
    "tests/test_physical_full_matrix_v4",
    "scripts/run_physical_full_matrix_v4",
)
_FULL_MATRIX_V2R_PREFIXES = (
    "core/physical_wal_v2r",
    "tests/test_physical_wal_v2r",
    "docs/PHYSICAL_FULL_MATRIX_V4R_",
)
_EXPERIMENTAL_PREFIXES = (
    "migrations/experimental/",
    "experimental/",
)
_RETIRED_PREFIXES = (
    "core/physical_arvan_s3_failback_",
    "core/physical_wa_fi_postgres_failback_",
    "core/physical_wa_ir_postgres_failback_",
)
_RETIRED_EXACT_PATHS = frozenset(
    {
        "core/legacy_two_server_full_matrix_fence.py",
        "core/physical_arvan_s3_immutability_probe_runner.py",
        "core/physical_arvan_s3_separated_client_factory.py",
        "core/physical_arvan_s3_separated_credential_loader.py",
        "core/physical_postgres_standby_bootstrap_materialization.py",
    }
)
_REVIEW_ONLY_PREFIXES = (
    "core/physical_operational_failover_v1",
    "core/fenced_fi_release_identity",
    "core/application_writer_transaction_envelope_guard.py",
    "core/object_delta_",
    "core/physical_full_matrix_v2",
    "core/physical_wal_v2_",
    "scripts/run_production_full_matrix.py",
    "scripts/run_staging_two_server_full_matrix.py",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_hex64(value: object, code: str) -> str:
    if not isinstance(value, str) or _HEX64_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _require_safe_relative_path(value: object, code: str) -> str:
    if not isinstance(value, str) or _SAFE_PATH_RE.fullmatch(value) is None:
        _fail(code)
    if value.startswith("/") or "//" in value or any(
        segment in {"", ".", ".."} for segment in value.split("/")
    ):
        _fail(code)
    return value


def _require_group(value: object, code: str) -> str:
    if not isinstance(value, str) or _GROUP_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _selection_profile_fields() -> dict[str, object]:
    specs = _RELEASE0_CANDIDATE_PATH_SPECS
    paths = [spec.relative_path for spec in specs]
    if not specs or len(paths) != len(set(paths)) or tuple(paths) != tuple(sorted(paths)):
        _fail("RELEASE0_SELECTION_PROFILE_INVALID")
    entries: list[dict[str, object]] = []
    for spec in specs:
        _require_group(spec.group, "RELEASE0_SELECTION_PROFILE_INVALID")
        _require_safe_relative_path(spec.relative_path, "RELEASE0_SELECTION_PROFILE_INVALID")
        _require_hex64(spec.expected_sha256, "RELEASE0_SELECTION_PROFILE_INVALID")
        if (
            type(spec.expected_bytes) is not int
            or not 0 < spec.expected_bytes <= _MAX_FILE_BYTES
            or spec.expected_mode != _ALLOWED_MODE
        ):
            _fail("RELEASE0_SELECTION_PROFILE_INVALID")
        if classify_release0_candidate_path(spec.relative_path) != "selected":
            _fail("RELEASE0_SELECTION_PROFILE_INVALID")
        entries.append(
            {
                "group": spec.group,
                "mode": f"{spec.expected_mode:06o}",
                "path": spec.relative_path,
                "sha256": spec.expected_sha256,
                "size_bytes": spec.expected_bytes,
            }
        )
    return {
        "baseline_git_tree_id": FIXED_RELEASE0_CANDIDATE_BASELINE_TREE,
        "baseline_release_sha": FIXED_RELEASE0_CANDIDATE_BASELINE_SHA,
        "entries": entries,
        "schema": RELEASE0_CANDIDATE_INVENTORY_SCHEMA + "/selection-profile-v1",
    }


def _selection_profile_sha256() -> str:
    return _sha256(_canonical_json(_selection_profile_fields()))


def classify_release0_candidate_path(relative_path: object) -> str:
    """Return the narrow selection disposition for one proposed relative path.

    ``selected`` is the only disposition that can enter this Release-0
    foundation profile.  All other values are refusal categories, not broader
    allow-lists.
    """

    try:
        path = _require_safe_relative_path(relative_path, "RELEASE0_PATH_INVALID")
    except Release0CandidateError:
        return "invalid"
    if path in {spec.relative_path for spec in _RELEASE0_CANDIDATE_PATH_SPECS}:
        return "selected"
    if path.startswith(_FULL_MATRIX_V4_PREFIXES):
        return "forbidden-full-matrix-v4"
    if path.startswith(_FULL_MATRIX_V2R_PREFIXES):
        return "forbidden-full-matrix-v2r"
    if path.startswith(_EXPERIMENTAL_PREFIXES):
        return "forbidden-experimental"
    if path in _RETIRED_EXACT_PATHS or path.startswith(_RETIRED_PREFIXES):
        return "forbidden-retired"
    if path.startswith(_REVIEW_ONLY_PREFIXES):
        return "forbidden-review-only"
    return "not-selected"


def _require_selected_path(path: object, code: str) -> str:
    normalized = _require_safe_relative_path(path, code)
    disposition = classify_release0_candidate_path(normalized)
    if disposition == "selected":
        return normalized
    diagnostic = {
        "forbidden-full-matrix-v4": "RELEASE0_FULL_MATRIX_V4_FORBIDDEN",
        "forbidden-full-matrix-v2r": "RELEASE0_FULL_MATRIX_V2R_FORBIDDEN",
        "forbidden-experimental": "RELEASE0_EXPERIMENTAL_PATH_FORBIDDEN",
        "forbidden-retired": "RELEASE0_RETIRED_PATH_FORBIDDEN",
        "forbidden-review-only": "RELEASE0_REVIEW_ONLY_PATH_FORBIDDEN",
        "invalid": "RELEASE0_PATH_INVALID",
        "not-selected": "RELEASE0_PATH_NOT_SELECTED",
    }
    _fail(diagnostic[disposition])


# A public fixed value makes the intended selection reviewable in code.  The
# builder independently recomputes it, so a malformed in-memory mutation does
# not inherit this value as authority.
RELEASE0_CANDIDATE_SELECTION_PROFILE_SHA256 = _selection_profile_sha256()


@dataclass(frozen=True)
class Release0CandidateSourceInspection:
    """Already-collected source safety facts; this module does not inspect it."""

    schema: str
    status: str
    baseline_release_sha: str
    baseline_git_tree_id: str
    stable: bool
    root_owned: bool
    source_no_follow: bool


class Release0CandidateSourceInspector(Protocol):
    def inspect_source(self) -> Release0CandidateSourceInspection:
        """Return a fresh, redacted source inspection without materializing it."""


@dataclass(frozen=True)
class Release0CandidateFileObservation:
    """One no-follow regular-file observation supplied by a narrow adapter."""

    relative_path: str
    owner_uid: int
    mode: int
    regular_file: bool
    symlink: bool
    stable: bool
    content: bytes


class Release0CandidateFileReader(Protocol):
    def read_file(self, *, relative_path: str) -> Release0CandidateFileObservation:
        """Read exactly one caller-selected path without following a symlink."""


@dataclass(frozen=True)
class Release0CandidateInventoryConfig:
    """Explicit, default-off configuration for freezing the five input bytes."""

    enabled: bool = RELEASE0_CANDIDATE_DEFAULT_ENABLED


@dataclass(frozen=True)
class Release0CandidateInventoryEntry:
    group: str
    relative_path: str
    mode: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class Release0CandidateInventory:
    schema: str
    status: str
    baseline_release_sha: str
    baseline_git_tree_id: str
    selection_profile_sha256: str
    entries: tuple[Release0CandidateInventoryEntry, ...]
    manifest_sha256: str
    canonical_manifest: bytes
    release_authorized: bool
    deployment_authorized: bool
    full_matrix_authorized: bool


def _validate_source_inspection(inspection: object) -> Release0CandidateSourceInspection:
    if not isinstance(inspection, Release0CandidateSourceInspection):
        _fail("RELEASE0_SOURCE_INSPECTION_INVALID")
    if (
        inspection.schema != RELEASE0_CANDIDATE_INVENTORY_SCHEMA + "/source-inspection-v1"
        or inspection.status != _AUDITED_SOURCE_STATUS
        or inspection.baseline_release_sha != FIXED_RELEASE0_CANDIDATE_BASELINE_SHA
        or inspection.baseline_git_tree_id != FIXED_RELEASE0_CANDIDATE_BASELINE_TREE
        or not inspection.stable
        or not inspection.root_owned
        or not inspection.source_no_follow
    ):
        _fail("RELEASE0_SOURCE_INSPECTION_REJECTED")
    return inspection


def _entry_from_observation(
    spec: _Release0PathSpec,
    observation: object,
) -> Release0CandidateInventoryEntry:
    if not isinstance(observation, Release0CandidateFileObservation):
        _fail("RELEASE0_SOURCE_FILE_OBSERVATION_INVALID")
    if (
        observation.relative_path != spec.relative_path
        or observation.owner_uid != 0
        or observation.mode != spec.expected_mode
        or not observation.regular_file
        or observation.symlink
        or not observation.stable
        or not isinstance(observation.content, bytes)
    ):
        _fail("RELEASE0_SOURCE_FILE_UNSAFE")
    size_bytes = len(observation.content)
    if size_bytes != spec.expected_bytes or size_bytes > _MAX_FILE_BYTES:
        _fail("RELEASE0_SOURCE_FILE_SIZE_MISMATCH")
    digest = _sha256(observation.content)
    if digest != spec.expected_sha256:
        _fail("RELEASE0_SOURCE_FILE_DIGEST_MISMATCH")
    return Release0CandidateInventoryEntry(
        group=spec.group,
        relative_path=spec.relative_path,
        mode=spec.expected_mode,
        size_bytes=size_bytes,
        sha256=digest,
    )


def _entry_fields(entry: Release0CandidateInventoryEntry) -> dict[str, object]:
    return {
        "group": entry.group,
        "mode": f"{entry.mode:06o}",
        "path": entry.relative_path,
        "sha256": entry.sha256,
        "size_bytes": entry.size_bytes,
    }


def _inventory_fields(entries: tuple[Release0CandidateInventoryEntry, ...]) -> dict[str, object]:
    return {
        "baseline_git_tree_id": FIXED_RELEASE0_CANDIDATE_BASELINE_TREE,
        "baseline_release_sha": FIXED_RELEASE0_CANDIDATE_BASELINE_SHA,
        "deployment_authorized": False,
        "entries": [_entry_fields(entry) for entry in entries],
        "full_matrix_authorized": False,
        "release_authorized": False,
        "schema": RELEASE0_CANDIDATE_INVENTORY_SCHEMA,
        "selection_profile_sha256": _selection_profile_sha256(),
        "status": _INVENTORY_STATUS,
    }


def _frozen_inventory_from_entries(
    entries: tuple[Release0CandidateInventoryEntry, ...],
) -> Release0CandidateInventory:
    fields = _inventory_fields(entries)
    canonical_manifest = _canonical_json(fields)
    return Release0CandidateInventory(
        schema=RELEASE0_CANDIDATE_INVENTORY_SCHEMA,
        status=_INVENTORY_STATUS,
        baseline_release_sha=FIXED_RELEASE0_CANDIDATE_BASELINE_SHA,
        baseline_git_tree_id=FIXED_RELEASE0_CANDIDATE_BASELINE_TREE,
        selection_profile_sha256=_selection_profile_sha256(),
        entries=entries,
        manifest_sha256=_sha256(canonical_manifest),
        canonical_manifest=canonical_manifest,
        release_authorized=False,
        deployment_authorized=False,
        full_matrix_authorized=False,
    )


def build_release0_candidate_inventory(
    *,
    config: Release0CandidateInventoryConfig,
    source_inspector: Release0CandidateSourceInspector,
    file_reader: Release0CandidateFileReader,
) -> Release0CandidateInventory:
    """Freeze only the literal audited bytes into a non-authorizing manifest."""

    if not isinstance(config, Release0CandidateInventoryConfig) or not config.enabled:
        _fail("RELEASE0_CANDIDATE_DISABLED")
    _validate_source_inspection(source_inspector.inspect_source())
    entries = tuple(
        _entry_from_observation(
            spec,
            file_reader.read_file(relative_path=spec.relative_path),
        )
        for spec in _RELEASE0_CANDIDATE_PATH_SPECS
    )
    return _frozen_inventory_from_entries(entries)


def _parse_entry(value: object, spec: _Release0PathSpec) -> Release0CandidateInventoryEntry:
    if not isinstance(value, dict) or set(value) != {
        "group",
        "mode",
        "path",
        "sha256",
        "size_bytes",
    }:
        _fail("RELEASE0_INVENTORY_ENTRY_INVALID")
    group = _require_group(value["group"], "RELEASE0_INVENTORY_ENTRY_INVALID")
    path = _require_selected_path(value["path"], "RELEASE0_INVENTORY_ENTRY_INVALID")
    digest = _require_hex64(value["sha256"], "RELEASE0_INVENTORY_ENTRY_INVALID")
    if (
        group != spec.group
        or path != spec.relative_path
        or digest != spec.expected_sha256
        or value["mode"] != f"{spec.expected_mode:06o}"
        or type(value["size_bytes"]) is not int
        or value["size_bytes"] != spec.expected_bytes
    ):
        _fail("RELEASE0_INVENTORY_ENTRY_MISMATCH")
    return Release0CandidateInventoryEntry(
        group=group,
        relative_path=path,
        mode=spec.expected_mode,
        size_bytes=spec.expected_bytes,
        sha256=digest,
    )


def parse_release0_candidate_inventory(value: object) -> Release0CandidateInventory:
    """Parse only canonical, complete, digest-locked inventory bytes."""

    if not isinstance(value, bytes) or len(value) > _MAX_FILE_BYTES:
        _fail("RELEASE0_INVENTORY_INVALID")
    try:
        raw = json.loads(value.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("RELEASE0_INVENTORY_INVALID")
    if not isinstance(raw, dict) or set(raw) != {
        "baseline_git_tree_id",
        "baseline_release_sha",
        "deployment_authorized",
        "entries",
        "full_matrix_authorized",
        "release_authorized",
        "schema",
        "selection_profile_sha256",
        "status",
    }:
        _fail("RELEASE0_INVENTORY_INVALID")
    if (
        raw["schema"] != RELEASE0_CANDIDATE_INVENTORY_SCHEMA
        or raw["status"] != _INVENTORY_STATUS
        or raw["baseline_release_sha"] != FIXED_RELEASE0_CANDIDATE_BASELINE_SHA
        or raw["baseline_git_tree_id"] != FIXED_RELEASE0_CANDIDATE_BASELINE_TREE
        or raw["selection_profile_sha256"] != _selection_profile_sha256()
        or raw["release_authorized"] is not False
        or raw["deployment_authorized"] is not False
        or raw["full_matrix_authorized"] is not False
        or not isinstance(raw["entries"], list)
        or len(raw["entries"]) != len(_RELEASE0_CANDIDATE_PATH_SPECS)
    ):
        _fail("RELEASE0_INVENTORY_INVALID")
    entries = tuple(
        _parse_entry(item, spec)
        for item, spec in zip(raw["entries"], _RELEASE0_CANDIDATE_PATH_SPECS, strict=True)
    )
    expected_fields = _inventory_fields(entries)
    canonical = _canonical_json(expected_fields)
    if canonical != value:
        _fail("RELEASE0_INVENTORY_NONCANONICAL")
    return _frozen_inventory_from_entries(entries)


def verify_release0_candidate_inventory(inventory: object) -> Release0CandidateInventory:
    """Re-parse the frozen bytes and reject a forged in-memory dataclass."""

    if not isinstance(inventory, Release0CandidateInventory):
        _fail("RELEASE0_INVENTORY_INVALID")
    parsed = parse_release0_candidate_inventory(inventory.canonical_manifest)
    if (
        parsed != inventory
        or inventory.manifest_sha256 != _sha256(inventory.canonical_manifest)
    ):
        _fail("RELEASE0_INVENTORY_TAMPERED")
    return parsed


def verify_release0_candidate_source(
    *,
    inventory: Release0CandidateInventory,
    source_inspector: Release0CandidateSourceInspector,
    file_reader: Release0CandidateFileReader,
) -> None:
    """Re-read every frozen source byte before a separate copier may use it."""

    verified = verify_release0_candidate_inventory(inventory)
    _validate_source_inspection(source_inspector.inspect_source())
    specs_by_path = {spec.relative_path: spec for spec in _RELEASE0_CANDIDATE_PATH_SPECS}
    for entry in verified.entries:
        _entry_from_observation(
            specs_by_path[entry.relative_path],
            file_reader.read_file(relative_path=entry.relative_path),
        )


@dataclass(frozen=True)
class Release0CandidateTargetInspection:
    """Proof that a destination is a clean checkout of the fixed baseline."""

    schema: str
    status: str
    baseline_release_sha: str
    baseline_git_tree_id: str
    clean: bool
    stable: bool
    root_owned: bool
    target_no_follow: bool


@dataclass(frozen=True)
class Release0CandidateMaterializationPlan:
    """Exact overlay contract; it neither copies files nor authorizes a release."""

    schema: str
    status: str
    baseline_release_sha: str
    baseline_git_tree_id: str
    inventory_manifest_sha256: str
    selection_profile_sha256: str
    expected_changed_paths: tuple[str, ...]
    plan_sha256: str
    canonical_plan: bytes
    release_authorized: bool
    deployment_authorized: bool
    full_matrix_authorized: bool


def _validate_target_inspection(inspection: object) -> Release0CandidateTargetInspection:
    if not isinstance(inspection, Release0CandidateTargetInspection):
        _fail("RELEASE0_TARGET_INSPECTION_INVALID")
    if (
        inspection.schema != RELEASE0_CANDIDATE_MATERIALIZATION_PLAN_SCHEMA + "/target-inspection-v1"
        or inspection.status != "clean-fixed-baseline-target"
        or inspection.baseline_release_sha != FIXED_RELEASE0_CANDIDATE_BASELINE_SHA
        or inspection.baseline_git_tree_id != FIXED_RELEASE0_CANDIDATE_BASELINE_TREE
        or not inspection.clean
        or not inspection.stable
        or not inspection.root_owned
        or not inspection.target_no_follow
    ):
        _fail("RELEASE0_TARGET_INSPECTION_REJECTED")
    return inspection


def _plan_fields(
    *,
    inventory_manifest_sha256: str,
    selection_profile_sha256: str,
    expected_changed_paths: tuple[str, ...],
) -> dict[str, object]:
    return {
        "baseline_git_tree_id": FIXED_RELEASE0_CANDIDATE_BASELINE_TREE,
        "baseline_release_sha": FIXED_RELEASE0_CANDIDATE_BASELINE_SHA,
        "deployment_authorized": False,
        "expected_changed_paths": list(expected_changed_paths),
        "full_matrix_authorized": False,
        "inventory_manifest_sha256": inventory_manifest_sha256,
        "release_authorized": False,
        "schema": RELEASE0_CANDIDATE_MATERIALIZATION_PLAN_SCHEMA,
        "selection_profile_sha256": selection_profile_sha256,
        "status": _PLAN_STATUS,
    }


def prepare_release0_candidate_materialization(
    *,
    inventory: Release0CandidateInventory,
    target_inspection: Release0CandidateTargetInspection,
    enabled: bool = RELEASE0_CANDIDATE_DEFAULT_ENABLED,
) -> Release0CandidateMaterializationPlan:
    """Prepare an exact local-overlay proof contract for a separate copier."""

    if enabled is not True:
        _fail("RELEASE0_MATERIALIZATION_DISABLED")
    verified = verify_release0_candidate_inventory(inventory)
    _validate_target_inspection(target_inspection)
    expected_changed_paths = tuple(entry.relative_path for entry in verified.entries)
    fields = _plan_fields(
        inventory_manifest_sha256=verified.manifest_sha256,
        selection_profile_sha256=verified.selection_profile_sha256,
        expected_changed_paths=expected_changed_paths,
    )
    canonical_plan = _canonical_json(fields)
    return Release0CandidateMaterializationPlan(
        schema=RELEASE0_CANDIDATE_MATERIALIZATION_PLAN_SCHEMA,
        status=_PLAN_STATUS,
        baseline_release_sha=FIXED_RELEASE0_CANDIDATE_BASELINE_SHA,
        baseline_git_tree_id=FIXED_RELEASE0_CANDIDATE_BASELINE_TREE,
        inventory_manifest_sha256=verified.manifest_sha256,
        selection_profile_sha256=verified.selection_profile_sha256,
        expected_changed_paths=expected_changed_paths,
        plan_sha256=_sha256(canonical_plan),
        canonical_plan=canonical_plan,
        release_authorized=False,
        deployment_authorized=False,
        full_matrix_authorized=False,
    )


@dataclass(frozen=True)
class Release0CandidateTargetOverlayObservation:
    """Already-collected target result after an external exact-overlay writer."""

    schema: str
    status: str
    baseline_release_sha: str
    baseline_git_tree_id: str
    stable: bool
    root_owned: bool
    target_no_follow: bool
    complete_changed_path_observation: bool
    changed_paths: tuple[str, ...]
    target_git_commit_created: bool
    release_seal_created: bool


@dataclass(frozen=True)
class Release0CandidateReadbackReceipt:
    schema: str
    status: str
    inventory_manifest_sha256: str
    materialization_plan_sha256: str
    observed_changed_paths: tuple[str, ...]
    receipt_sha256: str
    canonical_receipt: bytes
    release_authorized: bool
    deployment_authorized: bool
    full_matrix_authorized: bool


def _validate_plan(plan: object) -> Release0CandidateMaterializationPlan:
    if not isinstance(plan, Release0CandidateMaterializationPlan):
        _fail("RELEASE0_MATERIALIZATION_PLAN_INVALID")
    expected_paths = tuple(spec.relative_path for spec in _RELEASE0_CANDIDATE_PATH_SPECS)
    if (
        plan.schema != RELEASE0_CANDIDATE_MATERIALIZATION_PLAN_SCHEMA
        or plan.status != _PLAN_STATUS
        or plan.baseline_release_sha != FIXED_RELEASE0_CANDIDATE_BASELINE_SHA
        or plan.baseline_git_tree_id != FIXED_RELEASE0_CANDIDATE_BASELINE_TREE
        or plan.selection_profile_sha256 != _selection_profile_sha256()
        or plan.expected_changed_paths != expected_paths
        or plan.release_authorized
        or plan.deployment_authorized
        or plan.full_matrix_authorized
        or _HEX64_RE.fullmatch(plan.inventory_manifest_sha256) is None
        or plan.plan_sha256 != _sha256(plan.canonical_plan)
        or plan.canonical_plan
        != _canonical_json(
            _plan_fields(
                inventory_manifest_sha256=plan.inventory_manifest_sha256,
                selection_profile_sha256=plan.selection_profile_sha256,
                expected_changed_paths=expected_paths,
            )
        )
    ):
        _fail("RELEASE0_MATERIALIZATION_PLAN_INVALID")
    return plan


def _validate_overlay_observation(
    observation: object,
    expected_paths: tuple[str, ...],
) -> Release0CandidateTargetOverlayObservation:
    if not isinstance(observation, Release0CandidateTargetOverlayObservation):
        _fail("RELEASE0_TARGET_OVERLAY_INVALID")
    if (
        observation.schema
        != RELEASE0_CANDIDATE_READBACK_RECEIPT_SCHEMA + "/target-overlay-v1"
        or observation.status != _TARGET_OVERLAY_STATUS
        or observation.baseline_release_sha != FIXED_RELEASE0_CANDIDATE_BASELINE_SHA
        or observation.baseline_git_tree_id != FIXED_RELEASE0_CANDIDATE_BASELINE_TREE
        or not observation.stable
        or not observation.root_owned
        or not observation.target_no_follow
        or not observation.complete_changed_path_observation
        or observation.changed_paths != expected_paths
        or observation.target_git_commit_created
        or observation.release_seal_created
    ):
        _fail("RELEASE0_TARGET_OVERLAY_REJECTED")
    return observation


def _receipt_fields(
    *,
    plan: Release0CandidateMaterializationPlan,
    observed_changed_paths: tuple[str, ...],
) -> dict[str, object]:
    return {
        "deployment_authorized": False,
        "full_matrix_authorized": False,
        "inventory_manifest_sha256": plan.inventory_manifest_sha256,
        "materialization_plan_sha256": plan.plan_sha256,
        "observed_changed_paths": list(observed_changed_paths),
        "release_authorized": False,
        "schema": RELEASE0_CANDIDATE_READBACK_RECEIPT_SCHEMA,
        "status": _READBACK_STATUS,
    }


def verify_release0_candidate_materialization_readback(
    *,
    inventory: Release0CandidateInventory,
    plan: Release0CandidateMaterializationPlan,
    target_observation: Release0CandidateTargetOverlayObservation,
    target_file_reader: Release0CandidateFileReader,
) -> Release0CandidateReadbackReceipt:
    """Prove that an external copier changed exactly the frozen five files."""

    verified = verify_release0_candidate_inventory(inventory)
    checked_plan = _validate_plan(plan)
    if checked_plan.inventory_manifest_sha256 != verified.manifest_sha256:
        _fail("RELEASE0_MATERIALIZATION_INVENTORY_MISMATCH")
    observation = _validate_overlay_observation(
        target_observation,
        checked_plan.expected_changed_paths,
    )
    specs_by_path = {spec.relative_path: spec for spec in _RELEASE0_CANDIDATE_PATH_SPECS}
    for entry in verified.entries:
        _entry_from_observation(
            specs_by_path[entry.relative_path],
            target_file_reader.read_file(relative_path=entry.relative_path),
        )
    fields = _receipt_fields(
        plan=checked_plan,
        observed_changed_paths=observation.changed_paths,
    )
    canonical_receipt = _canonical_json(fields)
    return Release0CandidateReadbackReceipt(
        schema=RELEASE0_CANDIDATE_READBACK_RECEIPT_SCHEMA,
        status=_READBACK_STATUS,
        inventory_manifest_sha256=verified.manifest_sha256,
        materialization_plan_sha256=checked_plan.plan_sha256,
        observed_changed_paths=observation.changed_paths,
        receipt_sha256=_sha256(canonical_receipt),
        canonical_receipt=canonical_receipt,
        release_authorized=False,
        deployment_authorized=False,
        full_matrix_authorized=False,
    )
