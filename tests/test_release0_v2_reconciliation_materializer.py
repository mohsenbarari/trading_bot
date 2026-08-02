"""Focused tests for the default-off additive V2 materializer guard."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from core import release0_v2_reconciliation_inventory as inventory
from core import release0_v2_reconciliation_materializer as materializer


SOURCE_ROOT = Path("/srv/trading-bot-three-site/audited-checkpoint")
TARGET_ROOT = Path("/srv/trading-bot-three-site/release0-target")
_HASH = "a" * 64
_OTHER_HASH = "b" * 64


def _source_object(root: Path) -> inventory.Release0ReconciliationSourceObject:
    return inventory.Release0ReconciliationSourceObject(
        path=root,
        owner_uid=0,
        mode=0o750,
        directory=True,
        symlink=False,
        ancestors_root_controlled=True,
    )


class _Inspector:
    def __init__(self) -> None:
        self.target_clean = True

    def inspect_source(self, *, source_root: Path):
        if source_root == SOURCE_ROOT:
            return inventory.Release0ReconciliationSourceInspection(
                source_root=_source_object(source_root),
                release_sha=inventory.RECONCILIATION_CHECKPOINT_SHA,
                git_tree_id=inventory.RECONCILIATION_CHECKPOINT_TREE,
                clean=True,
                stable=True,
            )
        return inventory.Release0ReconciliationSourceInspection(
            source_root=_source_object(source_root),
            release_sha=inventory.RELEASE0_RECONCILIATION_BASELINE_SHA,
            git_tree_id=inventory.RELEASE0_RECONCILIATION_BASELINE_TREE,
            clean=self.target_clean,
            stable=True,
        )


class _Reader:
    def __init__(self) -> None:
        self.contents = {
            path: ("v2-checkpoint:" + path).encode("ascii")
            for path in inventory.ADDITIVE_V2_CLOSURE_PATHS
        }
        self.target_overrides: dict[str, bytes] = {}

    def read_file(self, *, source_root: Path, relative_path: str):
        body = self.contents[relative_path]
        if source_root == TARGET_ROOT:
            body = self.target_overrides.get(relative_path, body)
        return inventory.Release0ReconciliationFileObservation(
            relative_path=relative_path,
            owner_uid=0,
            mode=0o644,
            regular_file=True,
            symlink=False,
            stable=True,
            content=body,
        )


class _Transfer:
    def __init__(self) -> None:
        self.requests: list[materializer.Release0V2ReconciliationMaterializationRequest] = []
        self.unexpected_paths: tuple[str, ...] = ()
        self.replaced_paths: tuple[str, ...] = ()
        self.no_follow = True

    def materialize_additive_overlay(self, *, request):
        self.requests.append(request)
        return materializer.Release0V2ReconciliationTransferObservation(
            schema=materializer.RELEASE0_V2_RECONCILIATION_TRANSFER_SCHEMA,
            status="transferred",
            inventory_manifest_sha256=request.inventory_manifest_sha256,
            materialized_paths=tuple(entry.relative_path for entry in request.entries),
            unexpected_paths=self.unexpected_paths,
            replaced_release0_paths=self.replaced_paths,
            source_read_no_follow=self.no_follow,
            target_write_no_follow=self.no_follow,
            atomically_committed=True,
            transfer_evidence_sha256=_HASH,
        )


class _Overlay:
    def __init__(self) -> None:
        self.unexpected_paths: tuple[str, ...] = ()
        self.replaced_paths: tuple[str, ...] = ()
        self.release0_bytes_rehashed = True
        self.changed_paths = inventory.ADDITIVE_V2_CLOSURE_PATHS

    def inspect_additive_overlay(
        self, *, target_root: Path, expected_release0_sha: str, expected_release0_tree: str
    ):
        return materializer.Release0V2ReconciliationTargetOverlayInspection(
            schema=materializer.RELEASE0_V2_RECONCILIATION_OVERLAY_SCHEMA,
            status="target-observed",
            target_root=target_root,
            release0_baseline_sha=expected_release0_sha,
            release0_baseline_tree=expected_release0_tree,
            stable=True,
            changed_paths=self.changed_paths,
            unexpected_paths=self.unexpected_paths,
            replaced_release0_paths=self.replaced_paths,
            no_symlink_paths=True,
            release0_bytes_rehashed=self.release0_bytes_rehashed,
            release0_content_tree=expected_release0_tree,
            target_git_commit_created=False,
            release_seal_created=False,
            evidence_sha256=_OTHER_HASH,
        )


class Release0V2ReconciliationMaterializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inspector = _Inspector()
        self.reader = _Reader()
        self.transfer = _Transfer()
        self.overlay = _Overlay()
        self.source_config = inventory.Release0ReconciliationInventoryConfig(
            source_root=SOURCE_ROOT, enabled=True
        )
        with patch.object(inventory.os, "geteuid", return_value=0):
            self.frozen = inventory.build_release0_v2_reconciliation_inventory(
                config=self.source_config,
                source_inspector=self.inspector,
                file_reader=self.reader,
            )

    def adapters(self):
        return materializer.Release0V2ReconciliationMaterializationAdapters(
            source_inspector=self.inspector,
            source_file_reader=self.reader,
            target_inspector=self.inspector,
            target_file_reader=self.reader,
            atomic_transfer=self.transfer,
            target_overlay_inspector=self.overlay,
        )

    def config(self, **changes: object):
        values: dict[str, object] = {
            "inventory": self.frozen,
            "source_inventory_config": self.source_config,
            "target_config": inventory.Release0ReconciliationTargetConfig(
                target_root=TARGET_ROOT
            ),
            "enabled": True,
        }
        values.update(changes)
        return materializer.Release0V2ReconciliationMaterializationConfig(**values)

    def run_materializer(self, **changes: object):
        with patch.object(inventory.os, "geteuid", return_value=0), patch.object(
            materializer.os, "geteuid", return_value=0
        ):
            return materializer.materialize_verified_release0_v2_reconciliation(
                config=self.config(**changes), adapters=self.adapters()
            )

    def test_exact_additive_overlay_rehashes_every_byte_and_stays_non_authorizing(self) -> None:
        result = self.run_materializer()
        self.assertTrue(result.overlay_materialized)
        self.assertEqual("materialized-additive-overlay", result.status)
        self.assertEqual(1, len(self.transfer.requests))
        self.assertEqual(
            inventory.ADDITIVE_V2_CLOSURE_PATHS,
            tuple(entry.relative_path for entry in self.transfer.requests[0].entries),
        )
        parsed = materializer.parse_release0_v2_reconciliation_receipt(
            result.receipt.canonical_receipt
        )
        self.assertEqual(result.receipt, parsed)
        body = json.loads(result.receipt.canonical_receipt)
        self.assertTrue(body["release0_bytes_rehashed"])
        self.assertFalse(body["image_build_authorized"])
        self.assertFalse(body["release_authorized"])
        self.assertFalse(body["execution_authorized"])

    def test_default_off_nonroot_and_dirty_target_refuse_before_transfer(self) -> None:
        with patch.object(inventory.os, "geteuid", return_value=0), patch.object(
            materializer.os, "geteuid", return_value=0
        ), self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "MATERIALIZER_DISABLED",
        ):
            materializer.materialize_verified_release0_v2_reconciliation(
                config=self.config(enabled=False), adapters=self.adapters()
            )
        self.assertEqual([], self.transfer.requests)
        with patch.object(inventory.os, "geteuid", return_value=1000), patch.object(
            materializer.os, "geteuid", return_value=1000
        ), self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "ROOT_RUNTIME_REQUIRED",
        ):
            materializer.materialize_verified_release0_v2_reconciliation(
                config=self.config(), adapters=self.adapters()
            )
        self.assertEqual([], self.transfer.requests)
        self.inspector.target_clean = False
        with self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "TARGET_BASELINE_REJECTED",
        ):
            self.run_materializer()
        self.assertEqual([], self.transfer.requests)

    def test_replaced_or_extra_path_and_target_hash_drift_fail_closed(self) -> None:
        self.transfer.replaced_paths = ("core/sync_worker.py",)
        with self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "TRANSFER_REJECTED",
        ):
            self.run_materializer()
        self.assertEqual(1, len(self.transfer.requests))

        self.setUp()
        self.reader.target_overrides[inventory.ADDITIVE_V2_CLOSURE_PATHS[0]] = b"tampered"
        with self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "TARGET_REHASH_MISMATCH",
        ):
            self.run_materializer()

    def test_overlay_requires_full_release0_rehash_and_exact_changed_path_set(self) -> None:
        self.overlay.release0_bytes_rehashed = False
        with self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "OVERLAY_REJECTED",
        ):
            self.run_materializer()

        self.setUp()
        self.overlay.changed_paths = inventory.ADDITIVE_V2_CLOSURE_PATHS[:-1]
        with self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "OVERLAY_REJECTED",
        ):
            self.run_materializer()

        self.setUp()
        self.overlay.replaced_paths = ("core/application_writer_term.py",)
        with self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "OVERLAY_REJECTED",
        ):
            self.run_materializer()

    def test_source_rehash_and_receipt_tampering_are_rejected(self) -> None:
        self.reader.contents[inventory.ADDITIVE_V2_CLOSURE_PATHS[0]] = b"swapped"
        with self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "SOURCE_REHASH_MISMATCH",
        ):
            self.run_materializer()
        self.assertEqual([], self.transfer.requests)

        self.setUp()
        result = self.run_materializer()
        body = json.loads(result.receipt.canonical_receipt)
        body["execution_authorized"] = True
        tampered = json.dumps(
            body, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii") + b"\n"
        with self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "BINDING_INVALID",
        ):
            materializer.parse_release0_v2_reconciliation_receipt(tampered)
