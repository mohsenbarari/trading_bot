"""Adversarial tests for the non-authorizing candidate build-input binder."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from core import term_fenced_application_capability as capability
from scripts import prepare_fenced_fi_candidate_build_inputs as subject
from scripts import verify_term_fenced_application_source as source_verifier


REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(*arguments: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(
        ["/usr/bin/git", *arguments],
        cwd=cwd,
        text=True,
    ).strip()


def _write(path: Path, payload: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def _new_git_repository(path: Path) -> None:
    _git("init", "-q", str(path))
    _git("-C", str(path), "config", "user.email", "release0-test@example.invalid")
    _git("-C", str(path), "config", "user.name", "Release Zero Test")


class CandidateBuildInputFixture:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.inputs = directory / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.source_root = self._make_source_release()
        self.evidence_path = self.inputs / "term-fenced-evidence.json"
        source_tree = source_verifier.load_clean_source_tree(self.source_root)
        _write(
            self.evidence_path,
            source_verifier.build_evidence(source_tree),
            mode=0o600,
        )
        self.static_root = directory / "static" / subject.FRONTEND_DIST_DIRECTORY
        self.static_root.mkdir(parents=True, mode=0o755)
        _write(self.static_root / "index.html", b"<!doctype html><title>gold</title>\n")
        _write(self.static_root / "assets" / "app.js", b"console.log('gold');\n")
        self.static_outputs = directory / "static-output"
        self.static_outputs.mkdir(mode=0o700)
        self.build_outputs = directory / "build-output"
        self.build_outputs.mkdir(mode=0o700)
        self.static_manifest = self.static_outputs / "mini-app-dist.json"
        self.build_manifest = self.build_outputs / "candidate-build-inputs.json"

    def _make_source_release(self) -> Path:
        work = self.directory / "source-work"
        _new_git_repository(work)
        for relative in capability.TERM_FENCED_APPLICATION_CAPABILITY_FILES:
            _write(
                work / relative,
                subprocess.check_output(
                    ["/usr/bin/git", "show", f"HEAD:{relative}"], cwd=REPO_ROOT
                ),
            )
        for relative in ("Dockerfile", ".dockerignore"):
            _write(
                work / relative,
                subprocess.check_output(
                    ["/usr/bin/git", "show", f"HEAD:{relative}"], cwd=REPO_ROOT
                ),
            )
        _write(work / ".gitignore", b"ignored/\n")
        _git("-C", str(work), "add", ".")
        _git("-C", str(work), "commit", "-qm", "candidate source")
        release_sha = _git("-C", str(work), "rev-parse", "HEAD")
        parent = self.directory / "application-releases"
        parent.mkdir(mode=0o755)
        root = parent / release_sha
        work.rename(root)
        return root

    def snapshot_static(self, *, output: Path | None = None) -> dict[str, object]:
        return subject.create_mini_app_dist_manifest(
            mini_app_dist_root=self.static_root,
            output=self.static_manifest if output is None else output,
        )

    def bind(self, *, output: Path | None = None) -> dict[str, object]:
        return subject.bind_fenced_fi_candidate_build_inputs(
            application_release_root=self.source_root,
            term_fenced_application_evidence=self.evidence_path,
            mini_app_dist_root=self.static_root,
            mini_app_dist_manifest=self.static_manifest,
            output=self.build_manifest if output is None else output,
        )


@unittest.skipUnless(os.geteuid() == 0, "candidate input controller is root-only")
class CandidateBuildInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = CandidateBuildInputFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_binds_deterministic_hashes_sizes_without_emitting_asset_contents(self) -> None:
        opaque = b"browser-content-must-not-appear-in-manifest"
        _write(self.fixture.static_root / "assets" / "opaque.txt", opaque)
        first = self.fixture.snapshot_static()
        second_parent = self.fixture.directory / "second-static-output"
        second_parent.mkdir(mode=0o700)
        second_path = second_parent / "same-mini-app-dist.json"
        second = self.fixture.snapshot_static(output=second_path)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
        self.assertEqual(self.fixture.static_manifest.read_bytes(), second_path.read_bytes())
        self.assertEqual(0o600, stat.S_IMODE(self.fixture.static_manifest.stat().st_mode))
        self.assertNotIn(opaque, self.fixture.static_manifest.read_bytes())

        result = self.fixture.bind()
        document = self.fixture.build_manifest.read_bytes()
        value = json.loads(document)
        self.assertEqual(subject.BUILD_INPUT_MANIFEST_SCHEMA, value["schema"])
        self.assertEqual(subject.BUILD_INPUT_MANIFEST_STATUS, value["status"])
        self.assertEqual(
            _git("-C", str(self.fixture.source_root), "rev-parse", "HEAD"),
            value["application"]["release_sha"],
        )
        self.assertEqual(
            hashlib.sha256(self.fixture.evidence_path.read_bytes()).hexdigest(),
            value["term_fenced_application_evidence_sha256"],
        )
        self.assertEqual(3, value["mini_app_dist"]["file_count"])
        self.assertEqual(
            sum(
                path.stat().st_size
                for path in (
                    self.fixture.static_root / "index.html",
                    self.fixture.static_root / "assets" / "app.js",
                    self.fixture.static_root / "assets" / "opaque.txt",
                )
            ),
            value["mini_app_dist"]["total_bytes"],
        )
        self.assertNotIn(opaque, document)
        self.assertNotIn(self.fixture.evidence_path.read_bytes(), document)
        self.assertTrue(all(value[name] is False for name in (
            "writer_authorized",
            "promotion_authorized",
            "deployment_authorized",
            "execution_authorized",
            "full_matrix_authorized",
            "full_matrix_executed",
        )))
        self.assertFalse(result["docker_action"])
        self.assertFalse(result["network_action"])
        self.assertFalse(result["service_changed"])
        self.assertEqual(0o600, stat.S_IMODE(self.fixture.build_manifest.stat().st_mode))

    def test_static_snapshot_rejects_symlink_secret_name_and_secret_content(self) -> None:
        (self.fixture.static_root / "assets" / "linked.js").symlink_to(
            self.fixture.static_root / "assets" / "app.js"
        )
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "STATIC_ENTRY_UNSAFE",
        ):
            self.fixture.snapshot_static()
        self.assertFalse(self.fixture.static_manifest.exists())
        (self.fixture.static_root / "assets" / "linked.js").unlink()

        _write(self.fixture.static_root / ".env", b"PUBLIC=not-secret\n")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "STATIC_POLLUTION",
        ):
            self.fixture.snapshot_static()
        (self.fixture.static_root / ".env").unlink()

        _write(self.fixture.static_root / "node_modules" / "bundle.js", b"build debris\n")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "STATIC_POLLUTION",
        ):
            self.fixture.snapshot_static()
        (self.fixture.static_root / "node_modules" / "bundle.js").unlink()
        (self.fixture.static_root / "node_modules").rmdir()

        _write(
            self.fixture.static_root / "assets" / "certificate.txt",
            b"-----BEGIN PRIVATE KEY-----\nprivate-material\n",
        )
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "STATIC_SECRET_CONTENT",
        ):
            self.fixture.snapshot_static()
        self.assertFalse(self.fixture.static_manifest.exists())

    def test_static_snapshot_rejects_unsafe_mode_and_non_root_owner(self) -> None:
        target = self.fixture.static_root / "assets" / "app.js"
        target.chmod(0o666)
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "STATIC_ENTRY_UNSAFE",
        ):
            self.fixture.snapshot_static()
        target.chmod(0o644)
        original_lstat = Path.lstat

        def non_root_lstat(path: Path) -> os.stat_result:
            observed = original_lstat(path)
            if path != target:
                return observed
            fields = list(observed)
            fields[4] = 65534  # st_uid; model a foreign-owned generated asset.
            return os.stat_result(fields)

        with mock.patch.object(Path, "lstat", new=non_root_lstat):
            with self.assertRaisesRegex(
                subject.FencedFiCandidateBuildInputError,
                "STATIC_ENTRY_UNSAFE",
            ):
                self.fixture.snapshot_static()
        self.assertFalse(self.fixture.static_manifest.exists())

    def test_snapshot_detects_static_swap_between_scans(self) -> None:
        original = subject._scan_mini_app_dist
        calls = 0

        def swap_after_first_scan(root: Path) -> subject.StaticSnapshot:
            nonlocal calls
            snapshot = original(root)
            calls += 1
            if calls == 1:
                _write(
                    self.fixture.static_root / "assets" / "app.js",
                    b"console.log('replaced-between-scans');\n",
                )
            return snapshot

        with mock.patch.object(subject, "_scan_mini_app_dist", side_effect=swap_after_first_scan):
            with self.assertRaisesRegex(
                subject.FencedFiCandidateBuildInputError,
                "STATIC_SOURCE_CHANGED",
            ):
                self.fixture.snapshot_static()
        self.assertFalse(self.fixture.static_manifest.exists())

    def test_bind_rejects_dirty_and_ignored_source_pollution(self) -> None:
        self.fixture.snapshot_static()
        _write(self.fixture.source_root / "untracked.py", b"unexpected = True\n")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "SOURCE_GIT_WORKTREE_POLLUTED",
        ):
            self.fixture.bind()
        (self.fixture.source_root / "untracked.py").unlink()
        _write(self.fixture.source_root / "ignored" / "node_modules" / "bundle.js", b"ignored\n")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "SOURCE_GIT_WORKTREE_POLLUTED",
        ):
            self.fixture.bind()
        self.assertFalse(self.fixture.build_manifest.exists())

    def test_bind_rejects_static_tree_changed_after_manifest(self) -> None:
        self.fixture.snapshot_static()
        _write(
            self.fixture.static_root / "assets" / "app.js",
            b"console.log('not-the-reviewed-static-tree');\n",
        )
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "STATIC_MANIFEST_MISMATCH",
        ):
            self.fixture.bind()
        self.assertFalse(self.fixture.build_manifest.exists())

    def test_source_dockerfile_and_dockerignore_mismatches_are_refused(self) -> None:
        for relative in ("Dockerfile", ".dockerignore"):
            path = self.fixture.source_root / relative
            original = path.read_bytes()
            _write(path, original + b"# altered after reviewed commit\n")
            with self.assertRaisesRegex(
                subject.FencedFiCandidateBuildInputError,
                "SOURCE_DOCKER_SURFACE_CHANGED",
            ):
                subject._source_file_sha256(self.fixture.source_root, relative)
            _write(path, original)

    def test_source_build_entries_must_be_root_controlled(self) -> None:
        self.fixture.snapshot_static()
        target = self.fixture.source_root / "main.py"
        target.chmod(0o640)
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "SOURCE_ENTRY_UNSAFE",
        ):
            self.fixture.bind()
        target.chmod(0o644)

        original_lstat = Path.lstat

        def foreign_source_lstat(path: Path) -> os.stat_result:
            observed = original_lstat(path)
            if path != target:
                return observed
            fields = list(observed)
            fields[4] = 65534  # st_uid; a foreign user could otherwise rewrite it.
            return os.stat_result(fields)

        with mock.patch.object(Path, "lstat", new=foreign_source_lstat):
            with self.assertRaisesRegex(
                subject.FencedFiCandidateBuildInputError,
                "SOURCE_ENTRY_UNSAFE",
            ):
                subject._require_root_controlled_source_entry(
                    self.fixture.source_root,
                    relative="main.py",
                    git_mode=b"100644",
                )
        self.assertFalse(self.fixture.build_manifest.exists())

    def test_legacy_release_and_existing_outputs_are_refused(self) -> None:
        self.fixture.snapshot_static()
        legacy_root = (
            self.fixture.source_root.parent
            / subject.LEGACY_UNFENCED_APPLICATION_RELEASE_SHA
        )
        self.fixture.source_root.rename(legacy_root)
        self.fixture.source_root = legacy_root
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "LEGACY_2C08_APPLICATION_BLOCKED",
        ):
            self.fixture.bind()
        self.assertFalse(self.fixture.build_manifest.exists())

        # A fresh fixture exercises create-only behavior after a valid bind.
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = CandidateBuildInputFixture(Path(self.temporary.name))
        self.fixture.snapshot_static()
        self.fixture.bind()
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "BUILD_INPUT_MANIFEST_OUTPUT_EXISTS",
        ):
            self.fixture.bind()
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildInputError,
            "STATIC_MANIFEST_OUTPUT_EXISTS",
        ):
            self.fixture.snapshot_static()


if __name__ == "__main__":
    unittest.main()
