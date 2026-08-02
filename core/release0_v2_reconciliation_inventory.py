"""Fail-closed inventory for an additive V2 reconciliation overlay.

This is deliberately a *reconciliation* boundary, not a release builder.  It
may inventory a small, explicitly reviewed closure from the audited three-site
checkpoint, but it never writes a target, creates a Git commit, builds an
image, contacts Object Storage, or authorizes a deployment.

The target anchor is the clean Release-0 tree.  Every selected path is absent
from that tree, so a conforming materializer can only add files; it cannot
replace a Release-0 byte.  The inventory is pinned to the audited checkpoint
instead of accepting a similarly named file from an arbitrary worktree.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Protocol


__all__ = (
    "ADDITIVE_V2_CLOSURE_PATHS",
    "ADDITIVE_V2_CLOSURE_PATH_SET",
    "RECONCILIATION_CHECKPOINT_SHA",
    "RECONCILIATION_CHECKPOINT_TREE",
    "RELEASE0_RECONCILIATION_DEFAULT_ENABLED",
    "RELEASE0_RECONCILIATION_INVENTORY_SCHEMA",
    "RELEASE0_RECONCILIATION_BASELINE_SHA",
    "RELEASE0_RECONCILIATION_BASELINE_TREE",
    "STALE_RELEASE0_DENY_PATHS",
    "Release0ReconciliationError",
    "Release0ReconciliationFileObservation",
    "Release0ReconciliationFileReader",
    "Release0ReconciliationInventory",
    "Release0ReconciliationInventoryConfig",
    "Release0ReconciliationInventoryEntry",
    "Release0ReconciliationSourceInspection",
    "Release0ReconciliationSourceInspector",
    "Release0ReconciliationSourceObject",
    "Release0ReconciliationTargetConfig",
    "build_release0_v2_reconciliation_inventory",
    "parse_release0_v2_reconciliation_inventory",
    "verify_clean_release0_reconciliation_target",
    "verify_release0_v2_reconciliation_inventory",
)


RELEASE0_RECONCILIATION_INVENTORY_SCHEMA = (
    "gold-trade-release0-v2-reconciliation-inventory-v1"
)
RELEASE0_RECONCILIATION_DEFAULT_ENABLED = False

# These are Git object identities only.  They contain no host, credential,
# bucket, image, or execution authority.
RELEASE0_RECONCILIATION_BASELINE_SHA = "b9d2c7a7aae36af64eddcddfb6858b66f9e8a3c6"
RELEASE0_RECONCILIATION_BASELINE_TREE = "466438f2085a019e8989365941157285610c397c"
RECONCILIATION_CHECKPOINT_SHA = "1a07b9df0f717bd62fe5eda61b9a0f8f81aba5dc"
RECONCILIATION_CHECKPOINT_TREE = "bf267e9a44d55d932d3274bf9c3a1475c79a5cc2"

_STATUS_INVENTORIED = "inventoried"
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._/-]{0,511}$", re.ASCII)
_MODE_BY_TEXT = {"0644": 0o644, "0755": 0o755}
_MAX_ENTRY_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024


# These legacy Release-0 paths are deliberately retained as fences and
# forensic history.  They must never enter a reconciliation inventory.  The
# additive rule below would reject them independently; this literal deny-list
# makes a future architecture regression review-visible.
STALE_RELEASE0_DENY_PATHS = frozenset(
    {
        "core/legacy_two_server_full_matrix_fence.py",
        "scripts/build_production_full_matrix_manifest.py",
        "scripts/build_staging_two_server_full_matrix_manifest.py",
        "scripts/manage_webapp_ir_snapshot.py",
        "scripts/plan_production_full_matrix.py",
        "scripts/run_production_full_matrix.py",
        "scripts/run_staging_two_server_full_matrix.py",
        "scripts/run_webapp_ir_seven_object_stage.py",
        "deploy/production/docker-compose.webapp-ir-promoted-2c08.yml",
        "deploy/production/docker-compose.webapp-ir-snapshot-standby-2c08.yml",
        "deploy/production/webapp-fi-snapshot-publish.service.template",
        "deploy/production/webapp-fi-snapshot-publish.timer.template",
        "deploy/production/webapp-ir-snapshot-refresh.service.template",
        "deploy/production/webapp-ir-snapshot-refresh.timer.template",
    }
)


# This is the exact additive Object-Delta V2 closure selected from the audited
# checkpoint.  It intentionally excludes every checkpoint modification to an
# existing Release-0 path (for example sync_worker.py, migrations/env.py and
# models/__init__.py).  Adding a V2-looking file elsewhere requires an explicit
# reviewed change to this literal tuple and its tests; no prefix or glob is
# accepted.
ADDITIVE_V2_CLOSURE_PATHS = (
    "core/append_only_sync_delta_batch.py",
    "core/append_only_sync_delta_payload.py",
    "core/authorized_object_delta_receiver_transaction.py",
    "core/dedicated_object_delta_atomic_applier.py",
    "core/legacy_source_publication_fence.py",
    "core/object_delta_baseline_manifest.py",
    "core/object_delta_batch_assembler.py",
    "core/object_delta_delivery_control_packet.py",
    "core/object_delta_import_plan.py",
    "core/object_delta_mvp_canonical.py",
    "core/object_delta_mvp_full_mirror_fence.py",
    "core/object_delta_mvp_scope.py",
    "core/object_delta_outbox_allocator.py",
    "core/object_delta_outbox_runtime.py",
    "core/object_delta_receiver_apply_scope.py",
    "core/object_delta_receiver_delivery_binding.py",
    "core/object_delta_receiver_delivery_nonce.py",
    "core/object_delta_receiver_delivery_nonce_persistence.py",
    "core/object_delta_receiver_genesis_admission.py",
    "core/object_delta_receiver_mvp_handlers.py",
    "core/object_delta_receiver_payload_admission.py",
    "core/object_delta_receiver_registry.py",
    "core/object_delta_role_matrix.py",
    "core/object_delta_role_matrix_rollover.py",
    "core/object_delta_runtime_binding.py",
    "core/object_delta_source_batch_attestation.py",
    "core/object_delta_source_batch_ledger.py",
    "core/object_delta_source_batch_publication.py",
    "core/object_delta_source_batch_selection.py",
    "core/object_delta_source_cutover_attestation.py",
    "core/object_delta_source_cutover_publication_gate.py",
    "core/object_delta_source_ledger_persistence.py",
    "core/object_delta_source_preupload_authorization.py",
    "core/object_delta_source_publication_attempt.py",
    "core/object_delta_source_publication_attempt_persistence.py",
    "core/object_delta_source_publication_snapshot.py",
    "core/object_delta_source_transport_contract.py",
    "core/object_delta_transport_binding.py",
    "core/sqlalchemy_authorized_object_delta_receiver_transaction.py",
    "migrations/versions/a1b2c3d4e5f6_add_object_delta_schema.py",
    "migrations/versions/b2c3d4e5f6a7_add_object_delta_source_batch_ledger.py",
    "migrations/versions/c3d4e5f6a7b8_add_object_delta_receiver_delivery_nonce_receipts.py",
    "migrations/versions/d4e5f6a7b8c9_add_object_delta_source_cutover.py",
    "migrations/versions/e5f6a7b8c9d0_add_object_delta_nonce_import_binding.py",
    "migrations/versions/f6a7b8c9d0e2_add_object_delta_source_append_only_guards.py",
    "migrations/versions/g7a8b9c0d1e2_add_object_delta_source_publication_attempts.py",
    "migrations/versions/h8i9j0k1l2m3_add_promotion_auth_epoch.py",
    "migrations/versions/i9j0k1l2m3n4_add_promotion_auth_epoch_operations.py",
    "models/object_delta.py",
    "models/object_delta_receiver_delivery.py",
    "models/object_delta_source_batch.py",
    "models/object_delta_source_publication_attempt.py",
    "models/promotion_auth_epoch.py",
)
ADDITIVE_V2_CLOSURE_PATH_SET = frozenset(ADDITIVE_V2_CLOSURE_PATHS)


class Release0ReconciliationError(ValueError):
    """Stable refusal from the default-off reconciliation boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise Release0ReconciliationError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail(code)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_DUPLICATE_KEY")
        result[key] = value
    return result


def _require_sha40(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _HEX40_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _require_sha256(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _HEX64_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _require_safe_relative_path(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _SAFE_PATH_RE.fullmatch(value) is None:
        _fail(code)
    candidate = Path(value)
    if (
        candidate.is_absolute()
        or value.startswith("./")
        or value.endswith("/")
        or "//" in value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        _fail(code)
    return value


def _require_absolute_root(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    return value


def _mode_text(mode: int, *, code: str) -> str:
    if type(mode) is not int:
        _fail(code)
    for text, expected in _MODE_BY_TEXT.items():
        if mode == expected:
            return text
    _fail(code)


@dataclass(frozen=True)
class Release0ReconciliationSourceObject:
    path: Path
    owner_uid: int
    mode: int
    directory: bool
    symlink: bool
    ancestors_root_controlled: bool


@dataclass(frozen=True)
class Release0ReconciliationSourceInspection:
    source_root: Release0ReconciliationSourceObject
    release_sha: str
    git_tree_id: str
    clean: bool
    stable: bool


class Release0ReconciliationSourceInspector(Protocol):
    def inspect_source(
        self, *, source_root: Path
    ) -> Release0ReconciliationSourceInspection: ...


@dataclass(frozen=True)
class Release0ReconciliationFileObservation:
    relative_path: str
    owner_uid: int
    mode: int
    regular_file: bool
    symlink: bool
    stable: bool
    content: bytes


class Release0ReconciliationFileReader(Protocol):
    def read_file(
        self, *, source_root: Path, relative_path: str
    ) -> Release0ReconciliationFileObservation: ...


@dataclass(frozen=True)
class Release0ReconciliationInventoryConfig:
    source_root: Path
    enabled: bool = RELEASE0_RECONCILIATION_DEFAULT_ENABLED
    expected_checkpoint_sha: str = RECONCILIATION_CHECKPOINT_SHA
    expected_checkpoint_tree: str = RECONCILIATION_CHECKPOINT_TREE


@dataclass(frozen=True)
class Release0ReconciliationTargetConfig:
    target_root: Path
    expected_release0_sha: str = RELEASE0_RECONCILIATION_BASELINE_SHA
    expected_release0_tree: str = RELEASE0_RECONCILIATION_BASELINE_TREE


@dataclass(frozen=True)
class Release0ReconciliationInventoryEntry:
    relative_path: str
    mode: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class Release0ReconciliationInventory:
    canonical_manifest: bytes
    manifest_sha256: str
    entries: tuple[Release0ReconciliationInventoryEntry, ...]
    total_bytes: int


_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "release0_baseline_sha",
        "release0_baseline_tree",
        "checkpoint_source_sha",
        "checkpoint_source_tree",
        "entry_count",
        "total_bytes",
        "entries",
        "materialization_authorized",
        "release_authorized",
        "execution_authorized",
        "manifest_sha256",
    }
)


def _validate_common_root(
    *,
    inspection: object,
    root: Path,
    expected_sha: str,
    expected_tree: str,
    code: str,
    require_clean: bool,
) -> Release0ReconciliationSourceInspection:
    if type(inspection) is not Release0ReconciliationSourceInspection:
        _fail(code)
    value = inspection
    source = value.source_root
    if (
        type(source) is not Release0ReconciliationSourceObject
        or not isinstance(source.path, Path)
        or source.path != root
        or source.owner_uid != 0
        or type(source.mode) is not int
        or source.mode & 0o022
        or source.directory is not True
        or source.symlink is not False
        or source.ancestors_root_controlled is not True
        or value.release_sha != expected_sha
        or value.git_tree_id != expected_tree
        or value.stable is not True
        or (require_clean and value.clean is not True)
    ):
        _fail(code)
    return value


def _validate_inventory_config(
    config: object,
) -> tuple[Release0ReconciliationInventoryConfig, Path]:
    if type(config) is not Release0ReconciliationInventoryConfig:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_CONFIG_INVALID")
    if config.enabled is not True:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_DISABLED")
    if os.geteuid() != 0:
        _fail("RELEASE0_V2_RECONCILIATION_ROOT_RUNTIME_REQUIRED")
    root = _require_absolute_root(
        config.source_root, code="RELEASE0_V2_RECONCILIATION_INVENTORY_SOURCE_INVALID"
    )
    if (
        config.expected_checkpoint_sha != RECONCILIATION_CHECKPOINT_SHA
        or config.expected_checkpoint_tree != RECONCILIATION_CHECKPOINT_TREE
    ):
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_SOURCE_PIN_INVALID")
    return config, root


def _validate_target_config(
    config: object,
) -> tuple[Release0ReconciliationTargetConfig, Path]:
    if type(config) is not Release0ReconciliationTargetConfig:
        _fail("RELEASE0_V2_RECONCILIATION_TARGET_CONFIG_INVALID")
    if os.geteuid() != 0:
        _fail("RELEASE0_V2_RECONCILIATION_ROOT_RUNTIME_REQUIRED")
    root = _require_absolute_root(
        config.target_root, code="RELEASE0_V2_RECONCILIATION_TARGET_ROOT_INVALID"
    )
    if (
        config.expected_release0_sha != RELEASE0_RECONCILIATION_BASELINE_SHA
        or config.expected_release0_tree != RELEASE0_RECONCILIATION_BASELINE_TREE
    ):
        _fail("RELEASE0_V2_RECONCILIATION_TARGET_PIN_INVALID")
    return config, root


def _entry_from_observation(
    *, relative_path: str, observation: object
) -> Release0ReconciliationInventoryEntry:
    if type(observation) is not Release0ReconciliationFileObservation:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_FILE_INVALID")
    value = observation
    if (
        _require_safe_relative_path(
            value.relative_path,
            code="RELEASE0_V2_RECONCILIATION_INVENTORY_FILE_PATH_INVALID",
        )
        != relative_path
        or relative_path not in ADDITIVE_V2_CLOSURE_PATH_SET
        or relative_path in STALE_RELEASE0_DENY_PATHS
        or value.owner_uid != 0
        or value.regular_file is not True
        or value.symlink is not False
        or value.stable is not True
        or not isinstance(value.content, bytes)
        or len(value.content) > _MAX_ENTRY_BYTES
    ):
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_FILE_REJECTED")
    return Release0ReconciliationInventoryEntry(
        relative_path=relative_path,
        mode=_mode_text(
            value.mode, code="RELEASE0_V2_RECONCILIATION_INVENTORY_FILE_MODE_INVALID"
        ),
        size_bytes=len(value.content),
        sha256=hashlib.sha256(value.content).hexdigest(),
    )


def _manifest_body(
    entries: tuple[Release0ReconciliationInventoryEntry, ...]
) -> dict[str, object]:
    return {
        "schema": RELEASE0_RECONCILIATION_INVENTORY_SCHEMA,
        "status": _STATUS_INVENTORIED,
        "release0_baseline_sha": RELEASE0_RECONCILIATION_BASELINE_SHA,
        "release0_baseline_tree": RELEASE0_RECONCILIATION_BASELINE_TREE,
        "checkpoint_source_sha": RECONCILIATION_CHECKPOINT_SHA,
        "checkpoint_source_tree": RECONCILIATION_CHECKPOINT_TREE,
        "entry_count": len(entries),
        "total_bytes": sum(entry.size_bytes for entry in entries),
        "entries": [
            {
                "path": entry.relative_path,
                "mode": entry.mode,
                "size_bytes": entry.size_bytes,
                "sha256": entry.sha256,
            }
            for entry in entries
        ],
        "materialization_authorized": False,
        "release_authorized": False,
        "execution_authorized": False,
    }


def _inventory_from_body(
    body: dict[str, object]
) -> Release0ReconciliationInventory:
    value = dict(body)
    digest = hashlib.sha256(
        _canonical(value, code="RELEASE0_V2_RECONCILIATION_INVENTORY_CANONICAL_INVALID")
    ).hexdigest()
    value["manifest_sha256"] = digest
    canonical = _canonical(
        value, code="RELEASE0_V2_RECONCILIATION_INVENTORY_CANONICAL_INVALID"
    ) + b"\n"
    entries_value = body["entries"]
    if not isinstance(entries_value, list) or any(
        not isinstance(item, dict) for item in entries_value
    ):
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_ENTRIES_INVALID")
    entries = tuple(
        Release0ReconciliationInventoryEntry(
            relative_path=item["path"],
            mode=item["mode"],
            size_bytes=item["size_bytes"],
            sha256=item["sha256"],
        )
        for item in entries_value
    )
    return Release0ReconciliationInventory(
        canonical_manifest=canonical,
        manifest_sha256=digest,
        entries=entries,
        total_bytes=body["total_bytes"],
    )


def build_release0_v2_reconciliation_inventory(
    *,
    config: Release0ReconciliationInventoryConfig,
    source_inspector: Release0ReconciliationSourceInspector,
    file_reader: Release0ReconciliationFileReader,
) -> Release0ReconciliationInventory:
    """Freeze exactly the additive V2 closure after source identity checks.

    This is default-off and non-authorizing.  It does not materialize any
    file; it merely records the bytes that a later, separate guard must rehash.
    """

    _config, source_root = _validate_inventory_config(config)
    _validate_common_root(
        inspection=source_inspector.inspect_source(source_root=source_root),
        root=source_root,
        expected_sha=RECONCILIATION_CHECKPOINT_SHA,
        expected_tree=RECONCILIATION_CHECKPOINT_TREE,
        code="RELEASE0_V2_RECONCILIATION_INVENTORY_SOURCE_REJECTED",
        require_clean=True,
    )
    entries = tuple(
        _entry_from_observation(
            relative_path=relative_path,
            observation=file_reader.read_file(
                source_root=source_root, relative_path=relative_path
            ),
        )
        for relative_path in ADDITIVE_V2_CLOSURE_PATHS
    )
    if sum(entry.size_bytes for entry in entries) > _MAX_TOTAL_BYTES:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_TOTAL_BYTES_EXCEEDED")
    return _inventory_from_body(_manifest_body(entries))


def _parse_entry(value: object) -> Release0ReconciliationInventoryEntry:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "mode",
        "size_bytes",
        "sha256",
    }:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_ENTRY_FIELDS_INVALID")
    path = _require_safe_relative_path(
        value["path"], code="RELEASE0_V2_RECONCILIATION_INVENTORY_ENTRY_PATH_INVALID"
    )
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in _MODE_BY_TEXT:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_ENTRY_MODE_INVALID")
    size = value["size_bytes"]
    if type(size) is not int or not 0 <= size <= _MAX_ENTRY_BYTES:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_ENTRY_SIZE_INVALID")
    return Release0ReconciliationInventoryEntry(
        relative_path=path,
        mode=mode,
        size_bytes=size,
        sha256=_require_sha256(
            value["sha256"],
            code="RELEASE0_V2_RECONCILIATION_INVENTORY_ENTRY_HASH_INVALID",
        ),
    )


def parse_release0_v2_reconciliation_inventory(
    value: object,
) -> Release0ReconciliationInventory:
    """Parse one canonical, complete and explicitly non-authorizing manifest."""

    if not isinstance(value, bytes) or not value.endswith(b"\n"):
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_ENCODING_INVALID")
    try:
        decoded = json.loads(
            value[:-1].decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _: _fail(
                "RELEASE0_V2_RECONCILIATION_INVENTORY_JSON_INVALID"
            ),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        Release0ReconciliationError,
    ):
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_ENCODING_INVALID")
    if not isinstance(decoded, dict) or set(decoded) != _MANIFEST_FIELDS:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_FIELDS_INVALID")
    if (
        decoded["schema"] != RELEASE0_RECONCILIATION_INVENTORY_SCHEMA
        or decoded["status"] != _STATUS_INVENTORIED
        or decoded["release0_baseline_sha"] != RELEASE0_RECONCILIATION_BASELINE_SHA
        or decoded["release0_baseline_tree"] != RELEASE0_RECONCILIATION_BASELINE_TREE
        or decoded["checkpoint_source_sha"] != RECONCILIATION_CHECKPOINT_SHA
        or decoded["checkpoint_source_tree"] != RECONCILIATION_CHECKPOINT_TREE
        or decoded["materialization_authorized"] is not False
        or decoded["release_authorized"] is not False
        or decoded["execution_authorized"] is not False
    ):
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_BINDING_INVALID")
    entries_value = decoded["entries"]
    if not isinstance(entries_value, list):
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_ENTRIES_INVALID")
    entries = tuple(_parse_entry(item) for item in entries_value)
    if (
        tuple(entry.relative_path for entry in entries) != ADDITIVE_V2_CLOSURE_PATHS
        or len(entries) != len(ADDITIVE_V2_CLOSURE_PATHS)
        or any(entry.relative_path in STALE_RELEASE0_DENY_PATHS for entry in entries)
    ):
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_SELECTION_REJECTED")
    total = sum(entry.size_bytes for entry in entries)
    if (
        type(decoded["entry_count"]) is not int
        or decoded["entry_count"] != len(entries)
        or type(decoded["total_bytes"]) is not int
        or decoded["total_bytes"] != total
        or total > _MAX_TOTAL_BYTES
    ):
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_COUNTS_INVALID")
    digest = _require_sha256(
        decoded["manifest_sha256"],
        code="RELEASE0_V2_RECONCILIATION_INVENTORY_HASH_INVALID",
    )
    body = dict(decoded)
    body.pop("manifest_sha256")
    if hashlib.sha256(
        _canonical(body, code="RELEASE0_V2_RECONCILIATION_INVENTORY_CANONICAL_INVALID")
    ).hexdigest() != digest:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_DIGEST_MISMATCH")
    result = _inventory_from_body(body)
    if result.manifest_sha256 != digest or result.canonical_manifest != value:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_NONCANONICAL")
    return result


def verify_release0_v2_reconciliation_inventory(
    *,
    inventory: Release0ReconciliationInventory,
    config: Release0ReconciliationInventoryConfig,
    source_inspector: Release0ReconciliationSourceInspector,
    file_reader: Release0ReconciliationFileReader,
) -> Release0ReconciliationInventory:
    """Re-read all selected bytes and reject a stale or swapped inventory."""

    if type(inventory) is not Release0ReconciliationInventory:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_OBJECT_INVALID")
    parsed = parse_release0_v2_reconciliation_inventory(inventory.canonical_manifest)
    if parsed != inventory:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_OBJECT_MISMATCH")
    rebuilt = build_release0_v2_reconciliation_inventory(
        config=config, source_inspector=source_inspector, file_reader=file_reader
    )
    if rebuilt != inventory:
        _fail("RELEASE0_V2_RECONCILIATION_INVENTORY_SOURCE_REHASH_MISMATCH")
    return rebuilt


def verify_clean_release0_reconciliation_target(
    *,
    config: Release0ReconciliationTargetConfig,
    target_inspector: Release0ReconciliationSourceInspector,
) -> Release0ReconciliationSourceInspection:
    """Require an unchanged b9 Release-0 target before any additive overlay."""

    _config, target_root = _validate_target_config(config)
    return _validate_common_root(
        inspection=target_inspector.inspect_source(source_root=target_root),
        root=target_root,
        expected_sha=RELEASE0_RECONCILIATION_BASELINE_SHA,
        expected_tree=RELEASE0_RECONCILIATION_BASELINE_TREE,
        code="RELEASE0_V2_RECONCILIATION_TARGET_BASELINE_REJECTED",
        require_clean=True,
    )
