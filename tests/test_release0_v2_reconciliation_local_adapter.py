"""Adversarial tests for the concrete local-only reconciliation adapter."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from core import release0_v2_reconciliation_inventory as inventory
from core import release0_v2_reconciliation_local_adapter as subject
from core import release0_v2_reconciliation_materializer as materializer


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repository(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(mode=0o755)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Local Adapter Tests")
    for relative_path, body in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        path.chmod(0o644)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")


@unittest.skipUnless(os.geteuid() == 0, "adapter ownership contract needs root fixtures")
class Release0V2ReconciliationLocalAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.services_root = Path(self.temporary.name) / "services"
        self.services_root.mkdir(mode=0o755)
        self.source = self.services_root / "checkpoint"
        self.target = self.services_root / "release0"
        self.source_files = {
            path: ("checkpoint:" + path).encode("ascii")
            for path in inventory.ADDITIVE_V2_CLOSURE_PATHS
        }
        self.source_files["README.md"] = b"checkpoint\n"
        _init_repository(self.source, self.source_files)
        _init_repository(
            self.target,
            {
                "README.md": b"release0\n",
                "core/baseline.py": b"baseline\n",
                "models/baseline.py": b"baseline\n",
                "migrations/versions/baseline.py": b"baseline\n",
            },
        )
        self.adapter = subject.Release0V2ReconciliationLocalAdapter()
        self._services_patch = mock.patch.object(
            subject, "LOCAL_RECONCILIATION_SERVICES_ROOT", self.services_root
        )
        self._services_patch.start()
        self._anchor_patch = mock.patch.object(
            subject, "_open_trusted_services_root", self._open_test_services_root
        )
        self._anchor_patch.start()

    def tearDown(self) -> None:
        self._anchor_patch.stop()
        self._services_patch.stop()
        self.temporary.cleanup()

    @contextmanager
    def _open_test_services_root(self):
        flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
        )
        descriptor = os.open(self.services_root, flags)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    def _source_pins(self) -> tuple[str, str]:
        return _git(self.source, "rev-parse", "HEAD"), _git(
            self.source, "rev-parse", "HEAD^{tree}"
        )

    def _target_pins(self) -> tuple[str, str]:
        return _git(self.target, "rev-parse", "HEAD"), _git(
            self.target, "rev-parse", "HEAD^{tree}"
        )

    @contextmanager
    def _patched_pins(self):
        source_sha, source_tree = self._source_pins()
        target_sha, target_tree = self._target_pins()
        patches = (
            mock.patch.object(inventory, "RECONCILIATION_CHECKPOINT_SHA", source_sha),
            mock.patch.object(inventory, "RECONCILIATION_CHECKPOINT_TREE", source_tree),
            mock.patch.object(inventory, "RELEASE0_RECONCILIATION_BASELINE_SHA", target_sha),
            mock.patch.object(inventory, "RELEASE0_RECONCILIATION_BASELINE_TREE", target_tree),
            mock.patch.object(materializer, "RELEASE0_RECONCILIATION_BASELINE_SHA", target_sha),
            mock.patch.object(materializer, "RELEASE0_RECONCILIATION_BASELINE_TREE", target_tree),
            mock.patch.object(subject, "RECONCILIATION_CHECKPOINT_SHA", source_sha),
            mock.patch.object(subject, "RECONCILIATION_CHECKPOINT_TREE", source_tree),
            mock.patch.object(subject, "RELEASE0_RECONCILIATION_BASELINE_SHA", target_sha),
            mock.patch.object(subject, "RELEASE0_RECONCILIATION_BASELINE_TREE", target_tree),
        )
        for patch in patches:
            patch.start()
        try:
            yield source_sha, source_tree, target_sha, target_tree
        finally:
            for patch in reversed(patches):
                patch.stop()

    def _inventory_and_config(self):
        source_sha, source_tree = self._source_pins()
        source_config = inventory.Release0ReconciliationInventoryConfig(
            source_root=self.source,
            enabled=True,
            expected_checkpoint_sha=source_sha,
            expected_checkpoint_tree=source_tree,
        )
        frozen = inventory.build_release0_v2_reconciliation_inventory(
            config=source_config,
            source_inspector=self.adapter,
            file_reader=self.adapter,
        )
        target_sha, target_tree = self._target_pins()
        target_config = inventory.Release0ReconciliationTargetConfig(
            target_root=self.target,
            expected_release0_sha=target_sha,
            expected_release0_tree=target_tree,
        )
        return frozen, source_config, target_config

    def _materialize(self):
        frozen, source_config, target_config = self._inventory_and_config()
        adapters = materializer.Release0V2ReconciliationMaterializationAdapters(
            source_inspector=self.adapter,
            source_file_reader=self.adapter,
            target_inspector=self.adapter,
            target_file_reader=self.adapter,
            atomic_transfer=self.adapter,
            target_overlay_inspector=self.adapter,
        )
        return materializer.materialize_verified_release0_v2_reconciliation(
            config=materializer.Release0V2ReconciliationMaterializationConfig(
                inventory=frozen,
                source_inventory_config=source_config,
                target_config=target_config,
                enabled=True,
            ),
            adapters=adapters,
        )

    def test_real_git_inspection_and_dirfd_reads_work_for_a_secure_root(self) -> None:
        observed = self.adapter.inspect_source(source_root=self.source)
        source_sha, source_tree = self._source_pins()
        self.assertEqual(source_sha, observed.release_sha)
        self.assertEqual(source_tree, observed.git_tree_id)
        self.assertTrue(observed.clean)
        self.assertTrue(observed.stable)
        path = inventory.ADDITIVE_V2_CLOSURE_PATHS[0]
        file_observed = self.adapter.read_file(
            source_root=self.source, relative_path=path
        )
        self.assertEqual(self.source_files[path], file_observed.content)
        self.assertTrue(file_observed.stable)

    def test_git_subprocess_uses_fixed_binary_and_drops_caller_environment(self) -> None:
        with mock.patch.dict(
            subject.os.environ,
            {
                # If the adapter selected Git using this inherited PATH, its
                # plumbing calls would fail before inspecting the fixture.
                "PATH": "/definitely-not-an-executable-directory",
                "GIT_DIR": "/definitely-not-the-fixture",
                "GIT_WORK_TREE": "/definitely-not-the-fixture",
                "LD_PRELOAD": "/definitely-not-a-library",
            },
            clear=False,
        ):
            observed = self.adapter.inspect_source(source_root=self.source)
        self.assertTrue(observed.clean)

    def test_full_direct_rehash_rejects_assume_unchanged_content_drift(self) -> None:
        path = inventory.ADDITIVE_V2_CLOSURE_PATHS[0]
        _git(self.source, "update-index", "--assume-unchanged", path)
        (self.source / path).write_bytes(b"subverted")
        with self.assertRaisesRegex(
            subject.Release0V2ReconciliationLocalAdapterError,
            "TREE_HASH_MISMATCH",
        ):
            self.adapter.inspect_source(source_root=self.source)

    def test_component_symlink_is_rejected_even_when_git_can_describe_the_path(self) -> None:
        core = self.source / "core"
        core.rename(self.source / "core-real")
        core.symlink_to(self.source / "core-real", target_is_directory=True)
        with self.assertRaisesRegex(
            subject.Release0V2ReconciliationLocalAdapterError,
            "FILE_PARENT_UNSAFE",
        ):
            self.adapter.read_file(
                source_root=self.source,
                relative_path="core/append_only_sync_delta_batch.py",
            )

    def test_only_literal_allow_list_paths_can_be_read(self) -> None:
        with self.assertRaisesRegex(
            subject.Release0V2ReconciliationLocalAdapterError,
            "FILE_PATH_NOT_ALLOWED",
        ):
            self.adapter.read_file(source_root=self.source, relative_path="README.md")

    def test_git_worktree_file_is_rejected_instead_of_followed(self) -> None:
        candidate = self.services_root / "linked-worktree-shape"
        candidate.mkdir()
        (candidate / ".git").write_text("gitdir: /outside\n")
        with self.assertRaisesRegex(
            subject.Release0V2ReconciliationLocalAdapterError,
            "GIT_DIR_INVALID",
        ):
            self.adapter.inspect_source(source_root=candidate)

    def test_full_guard_materialization_is_exact_create_only_and_non_authorizing(self) -> None:
        with self._patched_pins():
            result = self._materialize()
        self.assertTrue(result.overlay_materialized)
        self.assertFalse(
            json.loads(result.receipt.canonical_receipt)["execution_authorized"]
        )
        for path, content in self.source_files.items():
            if path in inventory.ADDITIVE_V2_CLOSURE_PATH_SET:
                self.assertEqual(content, (self.target / path).read_bytes())
        self.assertEqual("release0\n", (self.target / "README.md").read_text())
        self.assertEqual(
            [],
            list(self.target.rglob(".release0-v2-*.tmp")),
        )

    def test_existing_target_path_refuses_before_any_replacement(self) -> None:
        path = inventory.ADDITIVE_V2_CLOSURE_PATHS[0]
        candidate = self.target / path
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"do-not-overwrite")
        with self._patched_pins(), self.assertRaisesRegex(
            materializer.Release0V2ReconciliationMaterializerError,
            "TARGET_BASELINE_REJECTED|TREE_LAYOUT_MISMATCH",
        ):
            self._materialize()
        self.assertEqual(b"do-not-overwrite", candidate.read_bytes())

    def test_overlay_inspection_rejects_an_extra_or_symlink_path(self) -> None:
        with self._patched_pins() as (_source_sha, _source_tree, target_sha, target_tree):
            self._materialize()
            unexpected = self.target / "core" / "unexpected.py"
            unexpected.write_bytes(b"unexpected")
            with self.assertRaisesRegex(
                subject.Release0V2ReconciliationLocalAdapterError,
                "TREE_LAYOUT_MISMATCH",
            ):
                self.adapter.inspect_additive_overlay(
                    target_root=self.target,
                    expected_release0_sha=target_sha,
                    expected_release0_tree=target_tree,
                )

    def test_literal_overlap_is_rejected_before_source_or_target_transfer(self) -> None:
        source_sha, source_tree = self._source_pins()
        frozen = inventory.Release0ReconciliationInventory(
            canonical_manifest=b"unused\n",
            manifest_sha256="a" * 64,
            entries=tuple(
                inventory.Release0ReconciliationInventoryEntry(
                    relative_path=path,
                    mode="0644",
                    size_bytes=len(self.source_files[path]),
                    sha256=hashlib.sha256(self.source_files[path]).hexdigest(),
                )
                for path in inventory.ADDITIVE_V2_CLOSURE_PATHS
            ),
            total_bytes=sum(
                len(self.source_files[path])
                for path in inventory.ADDITIVE_V2_CLOSURE_PATHS
            ),
        )
        request = materializer.Release0V2ReconciliationMaterializationRequest(
            source_root=self.source,
            target_root=self.source,
            inventory_manifest_sha256="b" * 64,
            entries=frozen.entries,
        )
        with self._patched_pins(), self.assertRaisesRegex(
            subject.Release0V2ReconciliationLocalAdapterError,
            "SOURCE_TARGET_CONFLATED",
        ):
            self.adapter.materialize_additive_overlay(request=request)
        self.assertEqual(source_sha, _git(self.source, "rev-parse", "HEAD"))
        self.assertEqual(source_tree, _git(self.source, "rev-parse", "HEAD^{tree}"))

    def test_nested_target_is_rejected_before_any_git_or_file_operation(self) -> None:
        with self._patched_pins():
            frozen, _source_config, _target_config = self._inventory_and_config()
            nested_target = self.source / "nested-target"
            nested_target.mkdir()
            request = materializer.Release0V2ReconciliationMaterializationRequest(
                source_root=self.source,
                target_root=nested_target,
                inventory_manifest_sha256=frozen.manifest_sha256,
                entries=frozen.entries,
            )
            with self.assertRaisesRegex(
                subject.Release0V2ReconciliationLocalAdapterError,
                "SOURCE_TARGET_CONFLATED",
            ):
                self.adapter.materialize_additive_overlay(request=request)

    def test_ignored_untracked_source_file_is_not_hidden_from_direct_tree_scan(self) -> None:
        (self.source / ".gitignore").write_text("ignored-rogue\n")
        _git(self.source, "add", ".gitignore")
        _git(self.source, "commit", "-q", "-m", "ignore fixture")
        (self.source / "ignored-rogue").write_text("rogue")
        self.assertEqual("", _git(self.source, "status", "--porcelain"))
        with self.assertRaisesRegex(
            subject.Release0V2ReconciliationLocalAdapterError,
            "TREE_LAYOUT_MISMATCH",
        ):
            self.adapter.inspect_source(source_root=self.source)

    def test_second_atomic_final_link_failure_never_returns_success_or_replaces_data(self) -> None:
        original_link = subject.os.link
        calls = 0

        def fail_second_link(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.ENOSPC, "simulated no space")
            return original_link(*args, **kwargs)

        with self._patched_pins(), mock.patch.object(
            subject.os, "link", side_effect=fail_second_link
        ), self.assertRaisesRegex(
            subject.Release0V2ReconciliationLocalAdapterError,
            "TARGET_COMMIT_FAILED",
        ):
            self._materialize()
        first = inventory.ADDITIVE_V2_CLOSURE_PATHS[0]
        second = inventory.ADDITIVE_V2_CLOSURE_PATHS[1]
        self.assertEqual(self.source_files[first], (self.target / first).read_bytes())
        self.assertFalse((self.target / second).exists())
        self.assertEqual([], list(self.target.rglob(".release0-v2-*.tmp")))

    def test_production_adapter_rejects_tmp_roots_before_git_inspection(self) -> None:
        self._anchor_patch.stop()
        self._services_patch.stop()
        try:
            with self.assertRaisesRegex(
                subject.Release0V2ReconciliationLocalAdapterError,
                "ROOT_OUTSIDE_SERVICES",
            ):
                self.adapter.inspect_source(source_root=self.source)
        finally:
            self._services_patch.start()
            self._anchor_patch.start()
