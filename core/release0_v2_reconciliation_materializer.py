"""Fail-closed materializer guard for the additive Release-0 V2 inventory.

The guard has no built-in filesystem, Git, host, registry or Object Storage
adapter.  A future local-only adapter must explicitly implement the narrow
protocols below.  This preserves the distinction between reviewing an overlay
and authorizing a release, deployment, promotion, or Full-Matrix execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

from core.release0_v2_reconciliation_inventory import (
    ADDITIVE_V2_CLOSURE_PATHS,
    RELEASE0_RECONCILIATION_BASELINE_SHA,
    RELEASE0_RECONCILIATION_BASELINE_TREE,
    Release0ReconciliationError,
    Release0ReconciliationFileObservation,
    Release0ReconciliationFileReader,
    Release0ReconciliationInventory,
    Release0ReconciliationInventoryConfig,
    Release0ReconciliationInventoryEntry,
    Release0ReconciliationSourceInspector,
    Release0ReconciliationTargetConfig,
    parse_release0_v2_reconciliation_inventory,
    verify_clean_release0_reconciliation_target,
    verify_release0_v2_reconciliation_inventory,
)


__all__ = (
    "RELEASE0_V2_RECONCILIATION_MATERIALIZER_DEFAULT_ENABLED",
    "RELEASE0_V2_RECONCILIATION_MATERIALIZER_SCHEMA",
    "RELEASE0_V2_RECONCILIATION_RECEIPT_SCHEMA",
    "Release0V2ReconciliationAtomicTransfer",
    "Release0V2ReconciliationMaterializationAdapters",
    "Release0V2ReconciliationMaterializationConfig",
    "Release0V2ReconciliationMaterializationReceipt",
    "Release0V2ReconciliationMaterializationRequest",
    "Release0V2ReconciliationMaterializationResult",
    "Release0V2ReconciliationTargetOverlayInspection",
    "Release0V2ReconciliationTargetOverlayInspector",
    "Release0V2ReconciliationTransferObservation",
    "Release0V2ReconciliationMaterializerError",
    "materialize_verified_release0_v2_reconciliation",
    "parse_release0_v2_reconciliation_receipt",
)


RELEASE0_V2_RECONCILIATION_MATERIALIZER_SCHEMA = (
    "gold-trade-release0-v2-reconciliation-materializer-v1"
)
RELEASE0_V2_RECONCILIATION_RECEIPT_SCHEMA = (
    "gold-trade-release0-v2-reconciliation-materialization-receipt-v1"
)
RELEASE0_V2_RECONCILIATION_TRANSFER_SCHEMA = (
    "gold-trade-release0-v2-reconciliation-atomic-transfer-v1"
)
RELEASE0_V2_RECONCILIATION_OVERLAY_SCHEMA = (
    "gold-trade-release0-v2-reconciliation-target-overlay-v1"
)
RELEASE0_V2_RECONCILIATION_MATERIALIZER_DEFAULT_ENABLED = False
_STATUS_TRANSFERRED = "transferred"
_STATUS_TARGET_OBSERVED = "target-observed"
_STATUS_MATERIALIZED = "materialized-additive-overlay"


class Release0V2ReconciliationMaterializerError(ValueError):
    """Stable materializer refusal; never an authorization signal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise Release0V2ReconciliationMaterializerError(code)


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
            _fail("RELEASE0_V2_RECONCILIATION_RECEIPT_DUPLICATE_KEY")
        result[key] = value
    return result


def _require_sha256(value: object, *, code: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail(code)
    return value


@dataclass(frozen=True)
class Release0V2ReconciliationMaterializationRequest:
    source_root: Path
    target_root: Path
    inventory_manifest_sha256: str
    entries: tuple[Release0ReconciliationInventoryEntry, ...]


@dataclass(frozen=True)
class Release0V2ReconciliationTransferObservation:
    schema: str
    status: str
    inventory_manifest_sha256: str
    materialized_paths: tuple[str, ...]
    unexpected_paths: tuple[str, ...]
    replaced_release0_paths: tuple[str, ...]
    source_read_no_follow: bool
    target_write_no_follow: bool
    atomically_committed: bool
    transfer_evidence_sha256: str


class Release0V2ReconciliationAtomicTransfer(Protocol):
    def materialize_additive_overlay(
        self, *, request: Release0V2ReconciliationMaterializationRequest
    ) -> Release0V2ReconciliationTransferObservation: ...


@dataclass(frozen=True)
class Release0V2ReconciliationTargetOverlayInspection:
    schema: str
    status: str
    target_root: Path
    release0_baseline_sha: str
    release0_baseline_tree: str
    stable: bool
    changed_paths: tuple[str, ...]
    unexpected_paths: tuple[str, ...]
    replaced_release0_paths: tuple[str, ...]
    no_symlink_paths: bool
    release0_bytes_rehashed: bool
    release0_content_tree: str
    target_git_commit_created: bool
    release_seal_created: bool
    evidence_sha256: str


class Release0V2ReconciliationTargetOverlayInspector(Protocol):
    def inspect_additive_overlay(
        self,
        *,
        target_root: Path,
        expected_release0_sha: str,
        expected_release0_tree: str,
    ) -> Release0V2ReconciliationTargetOverlayInspection: ...


@dataclass(frozen=True)
class Release0V2ReconciliationMaterializationAdapters:
    source_inspector: Release0ReconciliationSourceInspector
    source_file_reader: Release0ReconciliationFileReader
    target_inspector: Release0ReconciliationSourceInspector
    target_file_reader: Release0ReconciliationFileReader
    atomic_transfer: Release0V2ReconciliationAtomicTransfer
    target_overlay_inspector: Release0V2ReconciliationTargetOverlayInspector


@dataclass(frozen=True)
class Release0V2ReconciliationMaterializationConfig:
    inventory: Release0ReconciliationInventory
    source_inventory_config: Release0ReconciliationInventoryConfig
    target_config: Release0ReconciliationTargetConfig
    enabled: bool = RELEASE0_V2_RECONCILIATION_MATERIALIZER_DEFAULT_ENABLED


@dataclass(frozen=True)
class Release0V2ReconciliationMaterializationReceipt:
    canonical_receipt: bytes
    receipt_sha256: str
    inventory_manifest_sha256: str
    target_rehash_sha256: str
    materialized_entry_count: int


@dataclass(frozen=True)
class Release0V2ReconciliationMaterializationResult:
    status: str
    receipt: Release0V2ReconciliationMaterializationReceipt
    overlay_materialized: bool


def _paths(value: object, *, code: str) -> tuple[str, ...]:
    if type(value) is not tuple or any(not isinstance(path, str) for path in value):
        _fail(code)
    if len(value) != len(set(value)):
        _fail(code)
    return value


def _normalise_config(
    config: object,
) -> tuple[
    Release0ReconciliationInventory,
    Release0ReconciliationInventoryConfig,
    Release0ReconciliationTargetConfig,
]:
    if type(config) is not Release0V2ReconciliationMaterializationConfig:
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_CONFIG_INVALID")
    if config.enabled is not True:
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_DISABLED")
    if os.geteuid() != 0:
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_ROOT_RUNTIME_REQUIRED")
    if (
        type(config.source_inventory_config) is not Release0ReconciliationInventoryConfig
        or type(config.target_config) is not Release0ReconciliationTargetConfig
    ):
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_CONFIG_INVALID")
    if config.source_inventory_config.enabled is not True:
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_SOURCE_DISABLED")
    inventory = parse_release0_v2_reconciliation_inventory(
        config.inventory.canonical_manifest
    ) if type(config.inventory) is Release0ReconciliationInventory else None
    if inventory is None or inventory != config.inventory:
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_INVENTORY_INVALID")
    source_root = config.source_inventory_config.source_root
    target_root = config.target_config.target_root
    if source_root == target_root:
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_SOURCE_TARGET_CONFLATED")
    return inventory, config.source_inventory_config, config.target_config


def _validate_transfer(
    *,
    observation: object,
    request: Release0V2ReconciliationMaterializationRequest,
) -> Release0V2ReconciliationTransferObservation:
    if type(observation) is not Release0V2ReconciliationTransferObservation:
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_TRANSFER_INVALID")
    expected_paths = tuple(entry.relative_path for entry in request.entries)
    if (
        observation.schema != RELEASE0_V2_RECONCILIATION_TRANSFER_SCHEMA
        or observation.status != _STATUS_TRANSFERRED
        or observation.inventory_manifest_sha256 != request.inventory_manifest_sha256
        or _paths(
            observation.materialized_paths,
            code="RELEASE0_V2_RECONCILIATION_MATERIALIZER_TRANSFER_PATHS_INVALID",
        )
        != expected_paths
        or _paths(
            observation.unexpected_paths,
            code="RELEASE0_V2_RECONCILIATION_MATERIALIZER_TRANSFER_PATHS_INVALID",
        )
        != ()
        or _paths(
            observation.replaced_release0_paths,
            code="RELEASE0_V2_RECONCILIATION_MATERIALIZER_TRANSFER_PATHS_INVALID",
        )
        != ()
        or observation.source_read_no_follow is not True
        or observation.target_write_no_follow is not True
        or observation.atomically_committed is not True
    ):
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_TRANSFER_REJECTED")
    _require_sha256(
        observation.transfer_evidence_sha256,
        code="RELEASE0_V2_RECONCILIATION_MATERIALIZER_TRANSFER_REJECTED",
    )
    return observation


def _rehash_target(
    *,
    target_root: Path,
    entries: tuple[Release0ReconciliationInventoryEntry, ...],
    reader: Release0ReconciliationFileReader,
) -> str:
    observed: list[dict[str, object]] = []
    for entry in entries:
        value = reader.read_file(
            source_root=target_root, relative_path=entry.relative_path
        )
        if type(value) is not Release0ReconciliationFileObservation:
            _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_TARGET_REHASH_INVALID")
        if (
            value.relative_path != entry.relative_path
            or value.owner_uid != 0
            or value.mode != int(entry.mode, 8)
            or value.regular_file is not True
            or value.symlink is not False
            or value.stable is not True
            or not isinstance(value.content, bytes)
            or len(value.content) != entry.size_bytes
            or hashlib.sha256(value.content).hexdigest() != entry.sha256
        ):
            _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_TARGET_REHASH_MISMATCH")
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
            observed, code="RELEASE0_V2_RECONCILIATION_MATERIALIZER_TARGET_REHASH_INVALID"
        )
    ).hexdigest()


def _validate_overlay(
    *,
    observation: object,
    target_root: Path,
    entries: tuple[Release0ReconciliationInventoryEntry, ...],
) -> Release0V2ReconciliationTargetOverlayInspection:
    if type(observation) is not Release0V2ReconciliationTargetOverlayInspection:
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_OVERLAY_INVALID")
    expected_paths = tuple(entry.relative_path for entry in entries)
    if (
        observation.schema != RELEASE0_V2_RECONCILIATION_OVERLAY_SCHEMA
        or observation.status != _STATUS_TARGET_OBSERVED
        or observation.target_root != target_root
        or observation.release0_baseline_sha != RELEASE0_RECONCILIATION_BASELINE_SHA
        or observation.release0_baseline_tree != RELEASE0_RECONCILIATION_BASELINE_TREE
        or observation.stable is not True
        or _paths(
            observation.changed_paths,
            code="RELEASE0_V2_RECONCILIATION_MATERIALIZER_OVERLAY_PATHS_INVALID",
        )
        != expected_paths
        or _paths(
            observation.unexpected_paths,
            code="RELEASE0_V2_RECONCILIATION_MATERIALIZER_OVERLAY_PATHS_INVALID",
        )
        != ()
        or _paths(
            observation.replaced_release0_paths,
            code="RELEASE0_V2_RECONCILIATION_MATERIALIZER_OVERLAY_PATHS_INVALID",
        )
        != ()
        or observation.no_symlink_paths is not True
        or observation.release0_bytes_rehashed is not True
        or observation.release0_content_tree != RELEASE0_RECONCILIATION_BASELINE_TREE
        or observation.target_git_commit_created is not False
        or observation.release_seal_created is not False
    ):
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_OVERLAY_REJECTED")
    _require_sha256(
        observation.evidence_sha256,
        code="RELEASE0_V2_RECONCILIATION_MATERIALIZER_OVERLAY_REJECTED",
    )
    return observation


_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "inventory_manifest_sha256",
        "release0_baseline_sha",
        "release0_baseline_tree",
        "transfer_evidence_sha256",
        "overlay_evidence_sha256",
        "target_rehash_sha256",
        "materialized_entry_count",
        "materialized_total_bytes",
        "atomically_committed",
        "release0_bytes_rehashed",
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
    inventory: Release0ReconciliationInventory,
    transfer: Release0V2ReconciliationTransferObservation,
    overlay: Release0V2ReconciliationTargetOverlayInspection,
    target_rehash_sha256: str,
) -> dict[str, object]:
    return {
        "schema": RELEASE0_V2_RECONCILIATION_RECEIPT_SCHEMA,
        "status": _STATUS_MATERIALIZED,
        "inventory_manifest_sha256": inventory.manifest_sha256,
        "release0_baseline_sha": RELEASE0_RECONCILIATION_BASELINE_SHA,
        "release0_baseline_tree": RELEASE0_RECONCILIATION_BASELINE_TREE,
        "transfer_evidence_sha256": transfer.transfer_evidence_sha256,
        "overlay_evidence_sha256": overlay.evidence_sha256,
        "target_rehash_sha256": target_rehash_sha256,
        "materialized_entry_count": len(inventory.entries),
        "materialized_total_bytes": inventory.total_bytes,
        "atomically_committed": True,
        "release0_bytes_rehashed": True,
        "target_git_commit_created": False,
        "release_seal_created": False,
        "image_build_authorized": False,
        "release_authorized": False,
        "execution_authorized": False,
    }


def _receipt_from_body(
    body: dict[str, object]
) -> Release0V2ReconciliationMaterializationReceipt:
    digest = hashlib.sha256(
        _canonical(body, code="RELEASE0_V2_RECONCILIATION_RECEIPT_CANONICAL_INVALID")
    ).hexdigest()
    value = {**body, "receipt_sha256": digest}
    canonical = _canonical(
        value, code="RELEASE0_V2_RECONCILIATION_RECEIPT_CANONICAL_INVALID"
    ) + b"\n"
    return Release0V2ReconciliationMaterializationReceipt(
        canonical_receipt=canonical,
        receipt_sha256=digest,
        inventory_manifest_sha256=body["inventory_manifest_sha256"],
        target_rehash_sha256=body["target_rehash_sha256"],
        materialized_entry_count=body["materialized_entry_count"],
    )


def parse_release0_v2_reconciliation_receipt(
    value: object,
) -> Release0V2ReconciliationMaterializationReceipt:
    if not isinstance(value, bytes) or not value.endswith(b"\n"):
        _fail("RELEASE0_V2_RECONCILIATION_RECEIPT_ENCODING_INVALID")
    try:
        decoded = json.loads(
            value[:-1].decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _: _fail(
                "RELEASE0_V2_RECONCILIATION_RECEIPT_JSON_INVALID"
            ),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        Release0V2ReconciliationMaterializerError,
    ):
        _fail("RELEASE0_V2_RECONCILIATION_RECEIPT_ENCODING_INVALID")
    if not isinstance(decoded, dict) or set(decoded) != _RECEIPT_FIELDS:
        _fail("RELEASE0_V2_RECONCILIATION_RECEIPT_FIELDS_INVALID")
    if (
        decoded["schema"] != RELEASE0_V2_RECONCILIATION_RECEIPT_SCHEMA
        or decoded["status"] != _STATUS_MATERIALIZED
        or decoded["release0_baseline_sha"] != RELEASE0_RECONCILIATION_BASELINE_SHA
        or decoded["release0_baseline_tree"] != RELEASE0_RECONCILIATION_BASELINE_TREE
        or decoded["atomically_committed"] is not True
        or decoded["release0_bytes_rehashed"] is not True
        or decoded["target_git_commit_created"] is not False
        or decoded["release_seal_created"] is not False
        or decoded["image_build_authorized"] is not False
        or decoded["release_authorized"] is not False
        or decoded["execution_authorized"] is not False
    ):
        _fail("RELEASE0_V2_RECONCILIATION_RECEIPT_BINDING_INVALID")
    for field in (
        "inventory_manifest_sha256",
        "transfer_evidence_sha256",
        "overlay_evidence_sha256",
        "target_rehash_sha256",
        "receipt_sha256",
    ):
        _require_sha256(
            decoded[field], code="RELEASE0_V2_RECONCILIATION_RECEIPT_HASH_INVALID"
        )
    if (
        type(decoded["materialized_entry_count"]) is not int
        or decoded["materialized_entry_count"] != len(ADDITIVE_V2_CLOSURE_PATHS)
        or type(decoded["materialized_total_bytes"]) is not int
        or decoded["materialized_total_bytes"] < 0
    ):
        _fail("RELEASE0_V2_RECONCILIATION_RECEIPT_COUNTS_INVALID")
    body = dict(decoded)
    digest = body.pop("receipt_sha256")
    if hashlib.sha256(
        _canonical(body, code="RELEASE0_V2_RECONCILIATION_RECEIPT_CANONICAL_INVALID")
    ).hexdigest() != digest:
        _fail("RELEASE0_V2_RECONCILIATION_RECEIPT_DIGEST_MISMATCH")
    result = _receipt_from_body(body)
    if result.canonical_receipt != value or result.receipt_sha256 != digest:
        _fail("RELEASE0_V2_RECONCILIATION_RECEIPT_NONCANONICAL")
    return result


def materialize_verified_release0_v2_reconciliation(
    *,
    config: Release0V2ReconciliationMaterializationConfig,
    adapters: Release0V2ReconciliationMaterializationAdapters,
) -> Release0V2ReconciliationMaterializationResult:
    """Materialize only a proven additive overlay through explicit adapters.

    The caller still has no image-build, release, deployment, promotion, or
    Full-Matrix permission after a successful return.
    """

    try:
        inventory, source_config, target_config = _normalise_config(config)
    except Release0ReconciliationError as exc:
        _fail(exc.code)
    if type(adapters) is not Release0V2ReconciliationMaterializationAdapters:
        _fail("RELEASE0_V2_RECONCILIATION_MATERIALIZER_ADAPTERS_INVALID")
    try:
        verified_inventory = verify_release0_v2_reconciliation_inventory(
            inventory=inventory,
            config=source_config,
            source_inspector=adapters.source_inspector,
            file_reader=adapters.source_file_reader,
        )
        verify_clean_release0_reconciliation_target(
            config=target_config, target_inspector=adapters.target_inspector
        )
    except Release0ReconciliationError as exc:
        _fail(exc.code)
    request = Release0V2ReconciliationMaterializationRequest(
        source_root=source_config.source_root,
        target_root=target_config.target_root,
        inventory_manifest_sha256=verified_inventory.manifest_sha256,
        entries=verified_inventory.entries,
    )
    transfer = _validate_transfer(
        observation=adapters.atomic_transfer.materialize_additive_overlay(request=request),
        request=request,
    )
    target_rehash_sha256 = _rehash_target(
        target_root=target_config.target_root,
        entries=verified_inventory.entries,
        reader=adapters.target_file_reader,
    )
    overlay = _validate_overlay(
        observation=adapters.target_overlay_inspector.inspect_additive_overlay(
            target_root=target_config.target_root,
            expected_release0_sha=RELEASE0_RECONCILIATION_BASELINE_SHA,
            expected_release0_tree=RELEASE0_RECONCILIATION_BASELINE_TREE,
        ),
        target_root=target_config.target_root,
        entries=verified_inventory.entries,
    )
    receipt = _receipt_from_body(
        _receipt_body(
            inventory=verified_inventory,
            transfer=transfer,
            overlay=overlay,
            target_rehash_sha256=target_rehash_sha256,
        )
    )
    parsed = parse_release0_v2_reconciliation_receipt(receipt.canonical_receipt)
    return Release0V2ReconciliationMaterializationResult(
        status=_STATUS_MATERIALIZED,
        receipt=parsed,
        overlay_materialized=True,
    )
