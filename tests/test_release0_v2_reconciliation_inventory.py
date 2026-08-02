"""Focused contract tests for the Release-0 additive V2 inventory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from core import release0_v2_reconciliation_inventory as inventory


SOURCE_ROOT = Path("/srv/trading-bot-three-site/audited-checkpoint")
TARGET_ROOT = Path("/srv/trading-bot-three-site/release0-target")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _object(root: Path) -> inventory.Release0ReconciliationSourceObject:
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
        self.source_clean = True
        self.target_clean = True
        self.source_stable = True
        self.calls: list[Path] = []

    def inspect_source(self, *, source_root: Path):
        self.calls.append(source_root)
        if source_root == SOURCE_ROOT:
            return inventory.Release0ReconciliationSourceInspection(
                source_root=_object(source_root),
                release_sha=inventory.RECONCILIATION_CHECKPOINT_SHA,
                git_tree_id=inventory.RECONCILIATION_CHECKPOINT_TREE,
                clean=self.source_clean,
                stable=self.source_stable,
            )
        return inventory.Release0ReconciliationSourceInspection(
            source_root=_object(source_root),
            release_sha=inventory.RELEASE0_RECONCILIATION_BASELINE_SHA,
            git_tree_id=inventory.RELEASE0_RECONCILIATION_BASELINE_TREE,
            clean=self.target_clean,
            stable=True,
        )


class _Reader:
    def __init__(self) -> None:
        self.contents = {
            path: ("checkpoint-v2:" + path).encode("ascii")
            for path in inventory.ADDITIVE_V2_CLOSURE_PATHS
        }
        self.calls: list[tuple[Path, str]] = []

    def read_file(self, *, source_root: Path, relative_path: str):
        self.calls.append((source_root, relative_path))
        return inventory.Release0ReconciliationFileObservation(
            relative_path=relative_path,
            owner_uid=0,
            mode=0o644,
            regular_file=True,
            symlink=False,
            stable=True,
            content=self.contents[relative_path],
        )


class Release0V2ReconciliationInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inspector = _Inspector()
        self.reader = _Reader()

    def config(self, **changes: object):
        values: dict[str, object] = {
            "source_root": SOURCE_ROOT,
            "enabled": True,
        }
        values.update(changes)
        return inventory.Release0ReconciliationInventoryConfig(**values)

    def build(self, **changes: object):
        with patch.object(inventory.os, "geteuid", return_value=0):
            return inventory.build_release0_v2_reconciliation_inventory(
                config=self.config(**changes),
                source_inspector=self.inspector,
                file_reader=self.reader,
            )

    def test_literal_additive_closure_is_complete_narrow_and_disjoint_from_release0(self) -> None:
        self.assertEqual(53, len(inventory.ADDITIVE_V2_CLOSURE_PATHS))
        self.assertEqual(
            inventory.ADDITIVE_V2_CLOSURE_PATHS,
            tuple(sorted(inventory.ADDITIVE_V2_CLOSURE_PATHS)),
        )
        self.assertEqual(
            len(inventory.ADDITIVE_V2_CLOSURE_PATHS),
            len(inventory.ADDITIVE_V2_CLOSURE_PATH_SET),
        )
        self.assertFalse(
            inventory.ADDITIVE_V2_CLOSURE_PATH_SET
            & inventory.STALE_RELEASE0_DENY_PATHS
        )
        self.assertIn(
            "core/object_delta_source_preupload_authorization.py",
            inventory.ADDITIVE_V2_CLOSURE_PATH_SET,
        )
        self.assertIn(
            "models/object_delta_receiver_delivery.py",
            inventory.ADDITIVE_V2_CLOSURE_PATH_SET,
        )
        self.assertNotIn(
            "core/sync_worker.py", inventory.ADDITIVE_V2_CLOSURE_PATH_SET
        )
        self.assertNotIn(
            "migrations/env.py", inventory.ADDITIVE_V2_CLOSURE_PATH_SET
        )
        self.assertNotIn("models/__init__.py", inventory.ADDITIVE_V2_CLOSURE_PATH_SET)
        for path in inventory.ADDITIVE_V2_CLOSURE_PATHS:
            with self.subTest(path=path):
                # This branch is b9-anchored: no allowed path may already be a
                # Release-0 byte that a materializer could overwrite.
                self.assertFalse((PROJECT_ROOT / path).exists())

    def test_inventory_is_deterministic_canonical_and_non_authorizing(self) -> None:
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        parsed = inventory.parse_release0_v2_reconciliation_inventory(
            first.canonical_manifest
        )
        self.assertEqual(first, parsed)
        body = json.loads(first.canonical_manifest)
        self.assertFalse(body["materialization_authorized"])
        self.assertFalse(body["release_authorized"])
        self.assertFalse(body["execution_authorized"])
        self.assertEqual(
            inventory.RELEASE0_RECONCILIATION_BASELINE_SHA,
            body["release0_baseline_sha"],
        )
        self.assertEqual(
            inventory.RECONCILIATION_CHECKPOINT_SHA,
            body["checkpoint_source_sha"],
        )
        self.assertEqual(
            inventory.ADDITIVE_V2_CLOSURE_PATHS,
            tuple(item["path"] for item in body["entries"]),
        )
        self.assertEqual(
            hashlib.sha256(
                json.dumps(
                    {key: value for key, value in body.items() if key != "manifest_sha256"},
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest(),
            body["manifest_sha256"],
        )

    def test_default_off_and_nonroot_refuse_before_source_read(self) -> None:
        with patch.object(inventory.os, "geteuid", return_value=0), self.assertRaisesRegex(
            inventory.Release0ReconciliationError,
            "INVENTORY_DISABLED",
        ):
            inventory.build_release0_v2_reconciliation_inventory(
                config=self.config(enabled=False),
                source_inspector=self.inspector,
                file_reader=self.reader,
            )
        self.assertEqual([], self.inspector.calls)
        self.assertEqual([], self.reader.calls)
        with patch.object(inventory.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            inventory.Release0ReconciliationError,
            "ROOT_RUNTIME_REQUIRED",
        ):
            inventory.build_release0_v2_reconciliation_inventory(
                config=self.config(),
                source_inspector=self.inspector,
                file_reader=self.reader,
            )
        self.assertEqual([], self.inspector.calls)
        self.assertEqual([], self.reader.calls)

    def test_source_identity_and_rehash_are_strict(self) -> None:
        self.inspector.source_clean = False
        with self.assertRaisesRegex(
            inventory.Release0ReconciliationError, "SOURCE_REJECTED"
        ):
            self.build()
        self.assertEqual([], self.reader.calls)

        self.inspector.source_clean = True
        frozen = self.build()
        self.reader.contents[inventory.ADDITIVE_V2_CLOSURE_PATHS[0]] = b"changed"
        with patch.object(inventory.os, "geteuid", return_value=0), self.assertRaisesRegex(
            inventory.Release0ReconciliationError,
            "SOURCE_REHASH_MISMATCH",
        ):
            inventory.verify_release0_v2_reconciliation_inventory(
                inventory=frozen,
                config=self.config(),
                source_inspector=self.inspector,
                file_reader=self.reader,
            )

    def test_parser_rejects_resealed_stale_path_and_authorization_bit(self) -> None:
        frozen = self.build()
        body = json.loads(frozen.canonical_manifest)
        body["entries"][0]["path"] = "scripts/plan_production_full_matrix.py"
        body["manifest_sha256"] = hashlib.sha256(
            json.dumps(
                {key: value for key, value in body.items() if key != "manifest_sha256"},
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        stale = json.dumps(
            body, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii") + b"\n"
        with self.assertRaisesRegex(
            inventory.Release0ReconciliationError, "SELECTION_REJECTED"
        ):
            inventory.parse_release0_v2_reconciliation_inventory(stale)

        body = json.loads(frozen.canonical_manifest)
        body["execution_authorized"] = True
        body["manifest_sha256"] = hashlib.sha256(
            json.dumps(
                {key: value for key, value in body.items() if key != "manifest_sha256"},
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        forged = json.dumps(
            body, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii") + b"\n"
        with self.assertRaisesRegex(
            inventory.Release0ReconciliationError, "BINDING_INVALID"
        ):
            inventory.parse_release0_v2_reconciliation_inventory(forged)

    def test_target_must_be_the_clean_b9_release0_tree(self) -> None:
        config = inventory.Release0ReconciliationTargetConfig(target_root=TARGET_ROOT)
        with patch.object(inventory.os, "geteuid", return_value=0):
            observed = inventory.verify_clean_release0_reconciliation_target(
                config=config, target_inspector=self.inspector
            )
        self.assertTrue(observed.clean)
        self.inspector.target_clean = False
        with patch.object(inventory.os, "geteuid", return_value=0), self.assertRaisesRegex(
            inventory.Release0ReconciliationError, "TARGET_BASELINE_REJECTED"
        ):
            inventory.verify_clean_release0_reconciliation_target(
                config=config, target_inspector=self.inspector
            )
