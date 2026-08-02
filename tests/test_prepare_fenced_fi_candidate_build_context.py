"""Adversarial tests for the isolated non-authorizing Fenced-FI context preparer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from core import term_fenced_application_capability as capability
from scripts import prepare_fenced_fi_candidate_build_context as subject
from scripts import prepare_fenced_fi_candidate_build_inputs as input_subject
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


class CandidateBuildContextFixture:
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
        self.static_root = directory / "static" / input_subject.FRONTEND_DIST_DIRECTORY
        self.static_root.mkdir(parents=True, mode=0o755)
        _write(self.static_root / "index.html", b"<!doctype html><title>gold</title>\n")
        _write(self.static_root / "assets" / "app.js", b"console.log('gold');\n")
        self.static_outputs = directory / "static-output"
        self.static_outputs.mkdir(mode=0o700)
        self.build_outputs = directory / "build-output"
        self.build_outputs.mkdir(mode=0o700)
        self.static_manifest = self.static_outputs / "mini-app-dist.json"
        self.build_manifest = self.build_outputs / "candidate-build-inputs.json"
        self._snapshot_and_bind()
        self.independent_source_root = self._make_independent_source_copy()
        self.context_outputs = directory / "context-output"
        self.context_outputs.mkdir(mode=0o700)
        self.receipt_outputs = directory / "receipt-output"
        self.receipt_outputs.mkdir(mode=0o700)

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

    def _snapshot_and_bind(self) -> None:
        input_subject.create_mini_app_dist_manifest(
            mini_app_dist_root=self.static_root,
            output=self.static_manifest,
        )
        input_subject.bind_fenced_fi_candidate_build_inputs(
            application_release_root=self.source_root,
            term_fenced_application_evidence=self.evidence_path,
            mini_app_dist_root=self.static_root,
            mini_app_dist_manifest=self.static_manifest,
            output=self.build_manifest,
        )

    def _make_independent_source_copy(self) -> Path:
        parent = self.directory / "independent-application-releases"
        parent.mkdir(mode=0o755)
        root = parent / self.source_root.name
        shutil.copytree(self.source_root, root, copy_function=shutil.copy2)
        self.assert_clean_independent_source(root)
        return root

    @staticmethod
    def assert_clean_independent_source(root: Path) -> None:
        if _git("-C", str(root), "status", "--porcelain=v1", "--ignored=matching"):
            raise AssertionError("independent source copy must remain Git-clean")

    def outputs(self, name: str) -> tuple[Path, Path]:
        return self.context_outputs / f"{name}-context", self.receipt_outputs / f"{name}-receipt.json"

    def prepare(
        self,
        name: str,
        *,
        independent_source_root: Path | None = None,
        context_output: Path | None = None,
        receipt_output: Path | None = None,
    ) -> dict[str, object]:
        default_context, default_receipt = self.outputs(name)
        return subject.prepare_fenced_fi_candidate_build_context(
            independent_application_release_root=(
                self.independent_source_root
                if independent_source_root is None
                else independent_source_root
            ),
            build_input_manifest=self.build_manifest,
            mini_app_dist_manifest=self.static_manifest,
            context_output=default_context if context_output is None else context_output,
            receipt_output=default_receipt if receipt_output is None else receipt_output,
        )


@unittest.skipUnless(os.geteuid() == 0, "candidate context preparer is root-only")
class CandidateBuildContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = CandidateBuildContextFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepares_exact_context_and_separate_canonical_non_authorizing_receipt(self) -> None:
        result = self.fixture.prepare("success")
        context, receipt = self.fixture.outputs("success")
        receipt_bytes = receipt.read_bytes()
        receipt_value = json.loads(receipt_bytes)
        source_records = subject._tracked_source_records(self.fixture.independent_source_root)
        source_paths = {item[0] for item in source_records}
        expected_paths = source_paths | {
            input_subject.FRONTEND_DIST_DIRECTORY + "/index.html",
            input_subject.FRONTEND_DIST_DIRECTORY + "/assets/app.js",
        }
        observed_paths = {
            path.relative_to(context).as_posix()
            for path in context.rglob("*")
            if path.is_file()
        }

        self.assertEqual(expected_paths, observed_paths)
        self.assertFalse((context / ".git").exists())
        self.assertFalse((context / "untracked.py").exists())
        self.assertFalse((context / ".git" / "HEAD").exists())
        self.assertEqual(0o700, stat.S_IMODE(context.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(receipt.stat().st_mode))
        self.assertEqual(subject.BUILD_CONTEXT_RECEIPT_SCHEMA, receipt_value["schema"])
        self.assertEqual(subject.BUILD_CONTEXT_RECEIPT_STATUS, receipt_value["status"])
        self.assertEqual(receipt_bytes, subject._canonical_json_bytes(receipt_value))
        self.assertEqual(receipt_value, subject._verify_receipt_document(receipt_bytes))
        self.assertNotIn("source_root", receipt_bytes.decode("ascii"))
        self.assertNotIn(str(self.fixture.source_root), receipt_bytes.decode("ascii"))
        self.assertNotIn(str(self.fixture.independent_source_root), receipt_bytes.decode("ascii"))
        self.assertNotIn("independent", receipt_value)
        self.assertNotIn("console.log('gold')", receipt_bytes.decode("ascii"))
        self.assertTrue(all(receipt_value[name] is False for name in (
            "writer_authorized",
            "promotion_authorized",
            "deployment_authorized",
            "execution_authorized",
            "full_matrix_authorized",
            "full_matrix_executed",
        )))
        self.assertEqual(
            hashlib.sha256(receipt_bytes).hexdigest(),
            result["receipt_sha256"],
        )
        self.assertFalse(result["docker_action"])
        self.assertFalse(result["npm_action"])
        self.assertFalse(result["network_action"])
        self.assertFalse(result["service_changed"])

    def test_rejects_the_recorded_checkout_as_not_independent(self) -> None:
        context, receipt = self.fixture.outputs("same-source")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_NOT_INDEPENDENT",
        ):
            self.fixture.prepare(
                "same-source",
                independent_source_root=self.fixture.source_root,
            )
        self.assertFalse(context.exists())
        self.assertFalse(receipt.exists())

    def test_rejects_a_nested_checkout_path_as_not_independent(self) -> None:
        context, receipt = self.fixture.outputs("nested-source")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_NOT_INDEPENDENT",
        ):
            self.fixture.prepare(
                "nested-source",
                independent_source_root=self.fixture.source_root / "nested-copy",
            )
        self.assertFalse(context.exists())
        self.assertFalse(receipt.exists())

    def test_rejects_source_extra_and_changed_files_before_context_creation(self) -> None:
        for name, relative, payload in (
            ("source-extra", "unexpected.py", b"unexpected = True\n"),
            ("source-changed", "Dockerfile", b"# changed after bind\n"),
        ):
            with self.subTest(name=name):
                target = self.fixture.independent_source_root / relative
                original = target.read_bytes() if target.exists() else None
                _write(target, payload)
                context, receipt = self.fixture.outputs(name)
                with self.assertRaisesRegex(
                    subject.FencedFiCandidateBuildContextError,
                    "SOURCE_GIT_WORKTREE_POLLUTED",
                ):
                    self.fixture.prepare(name)
                self.assertFalse(context.exists())
                self.assertFalse(receipt.exists())
                target.unlink()
                if original is not None:
                    _write(target, original)
                self.fixture.assert_clean_independent_source(self.fixture.independent_source_root)

    def test_rejects_source_symlink_before_context_creation(self) -> None:
        target = self.fixture.independent_source_root / "Dockerfile"
        original = target.read_bytes()
        target.unlink()
        target.symlink_to(self.fixture.independent_source_root / ".dockerignore")
        context, receipt = self.fixture.outputs("source-symlink")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_GIT_WORKTREE_POLLUTED",
        ):
            self.fixture.prepare("source-symlink")
        self.assertFalse(context.exists())
        self.assertFalse(receipt.exists())
        target.unlink()
        _write(target, original)
        self.fixture.assert_clean_independent_source(self.fixture.independent_source_root)

    def test_rejects_ignored_source_entries(self) -> None:
        _write(
            self.fixture.independent_source_root / "ignored" / "generated.py",
            b"ignored-but-present = True\n",
        )
        context, receipt = self.fixture.outputs("source-ignored")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_GIT_WORKTREE_POLLUTED",
        ):
            self.fixture.prepare("source-ignored")
        self.assertFalse(context.exists())
        self.assertFalse(receipt.exists())

    def test_rejects_hardlinked_source_entries(self) -> None:
        source_file = self.fixture.independent_source_root / "Dockerfile"
        os.link(source_file, self.fixture.directory / "outside-source-hardlink")
        context, receipt = self.fixture.outputs("source-hardlink")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_ENTRY_UNSAFE",
        ):
            self.fixture.prepare("source-hardlink")
        self.assertFalse(context.exists())
        self.assertFalse(receipt.exists())

    def test_rejects_static_extra_changed_and_symlink_before_context_creation(self) -> None:
        cases = (
            ("static-extra", "assets/unexpected.js", b"unexpected\n", False),
            ("static-changed", "assets/app.js", b"changed\n", False),
            ("static-symlink", "assets/linked.js", b"", True),
        )
        for name, relative, payload, is_symlink in cases:
            with self.subTest(name=name):
                target = self.fixture.static_root / relative
                if is_symlink:
                    target.symlink_to(self.fixture.static_root / "assets" / "app.js")
                else:
                    _write(target, payload)
                context, receipt = self.fixture.outputs(name)
                with self.assertRaisesRegex(
                    subject.FencedFiCandidateBuildContextError,
                    "STATIC_MANIFEST_MISMATCH",
                ):
                    self.fixture.prepare(name)
                self.assertFalse(context.exists())
                self.assertFalse(receipt.exists())
                target.unlink()
                if name == "static-changed":
                    _write(target, b"console.log('gold');\n")

    def test_rejects_hardlinked_static_file_before_context_creation(self) -> None:
        static_file = self.fixture.static_root / "assets" / "app.js"
        os.link(static_file, self.fixture.directory / "outside-static-hardlink")
        context, receipt = self.fixture.outputs("static-hardlink")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "STATIC_MANIFEST_MISMATCH",
        ):
            self.fixture.prepare("static-hardlink")
        self.assertFalse(context.exists())
        self.assertFalse(receipt.exists())

    def test_rejects_partial_or_alternate_git_object_store_before_context_creation(self) -> None:
        marker = (
            self.fixture.independent_source_root
            / ".git"
            / "objects"
            / "pack"
            / "pack-test.promisor"
        )
        _write(marker, b"promisor marker\n")
        context, receipt = self.fixture.outputs("promisor")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_PARTIAL_OR_ALTERNATE_REJECTED",
        ):
            self.fixture.prepare("promisor")
        self.assertFalse(context.exists())
        self.assertFalse(receipt.exists())

    def test_rejects_partial_clone_config_and_object_alternates(self) -> None:
        _git(
            "-C",
            str(self.fixture.independent_source_root),
            "config",
            "extensions.partialClone",
            "origin",
        )
        context, receipt = self.fixture.outputs("partial-config")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_PARTIAL_OR_ALTERNATE_REJECTED",
        ):
            self.fixture.prepare("partial-config")
        self.assertFalse(context.exists())
        self.assertFalse(receipt.exists())

    def test_rejects_git_object_alternates_before_context_creation(self) -> None:
        alternates = (
            self.fixture.independent_source_root
            / ".git"
            / "objects"
            / "info"
            / "alternates"
        )
        _write(alternates, b"/unreviewed/alternate/object/store\n")
        context, receipt = self.fixture.outputs("alternates")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_PARTIAL_OR_ALTERNATE_REJECTED",
        ):
            self.fixture.prepare("alternates")
        self.assertFalse(context.exists())
        self.assertFalse(receipt.exists())

    def test_git_query_environment_disables_lazy_fetch_and_transport(self) -> None:
        class Result:
            returncode = 0
            stdout = b""

        with mock.patch.object(subject.subprocess, "run", return_value=Result()) as run:
            self.assertEqual(b"", subject._run_git(Path("/safe/source"), "status"))
        environment = run.call_args.kwargs["env"]
        self.assertEqual("1", environment["GIT_NO_LAZY_FETCH"])
        self.assertEqual("none", environment["GIT_ALLOW_PROTOCOL"])
        self.assertEqual("1", environment["GIT_NO_REPLACE_OBJECTS"])

    def test_rejects_committed_source_symlink_gitlink_and_static_namespace(self) -> None:
        symlink_tree = self.fixture.directory / "committed-symlink"
        _new_git_repository(symlink_tree)
        _write(symlink_tree / "target.txt", b"target\n")
        (symlink_tree / "linked.txt").symlink_to("target.txt")
        _git("-C", str(symlink_tree), "add", ".")
        _git("-C", str(symlink_tree), "commit", "-qm", "symlink tree")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_SYMLINK_OR_GITLINK",
        ):
            subject._tracked_source_records(symlink_tree)

        module = self.fixture.directory / "gitlink-module"
        _new_git_repository(module)
        _write(module / "module.txt", b"module\n")
        _git("-C", str(module), "add", ".")
        _git("-C", str(module), "commit", "-qm", "module")
        module_sha = _git("-C", str(module), "rev-parse", "HEAD")
        gitlink_tree = self.fixture.directory / "committed-gitlink"
        _new_git_repository(gitlink_tree)
        _write(gitlink_tree / "regular.txt", b"regular\n")
        _git("-C", str(gitlink_tree), "add", ".")
        _git("-C", str(gitlink_tree), "commit", "-qm", "regular")
        _git(
            "-C",
            str(gitlink_tree),
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{module_sha},vendor-module",
        )
        _git("-C", str(gitlink_tree), "commit", "-qm", "gitlink")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_SYMLINK_OR_GITLINK",
        ):
            subject._tracked_source_records(gitlink_tree)

        collision_tree = self.fixture.directory / "committed-static-collision"
        _new_git_repository(collision_tree)
        _write(collision_tree / "mini_app_dist" / "index.html", b"collision\n")
        _git("-C", str(collision_tree), "add", ".")
        _git("-C", str(collision_tree), "commit", "-qm", "static collision")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "SOURCE_STATIC_PATH_OVERLAP",
        ):
            subject._tracked_source_records(collision_tree)

    def test_never_emits_a_receipt_when_static_changes_during_copy(self) -> None:
        original = subject._copy_snapshot_file
        changed = False

        def mutate_after_first_static_copy(**kwargs: object) -> subject.FileSnapshot:
            nonlocal changed
            copied = original(**kwargs)
            if kwargs["label"] == "STATIC_ENTRY" and not changed:
                changed = True
                _write(
                    self.fixture.static_root / "assets" / "app.js",
                    b"changed-after-copy-began\n",
                )
            return copied

        context, receipt = self.fixture.outputs("static-race")
        with mock.patch.object(
            subject,
            "_copy_snapshot_file",
            side_effect=mutate_after_first_static_copy,
        ):
            with self.assertRaisesRegex(
                subject.FencedFiCandidateBuildContextError,
                "STATIC_MANIFEST_MISMATCH|STATIC_SOURCE_CHANGED",
            ):
                self.fixture.prepare("static-race")
        self.assertTrue(changed)
        self.assertTrue(context.exists())
        self.assertFalse(receipt.exists())

    def test_never_emits_a_receipt_when_source_changes_during_copy(self) -> None:
        original = subject._copy_snapshot_file
        source_files = len(subject._tracked_source_records(self.fixture.independent_source_root))
        source_copies = 0

        def mutate_after_last_source_copy(**kwargs: object) -> subject.FileSnapshot:
            nonlocal source_copies
            copied = original(**kwargs)
            if kwargs["label"] == "SOURCE_ENTRY":
                source_copies += 1
                if source_copies == source_files:
                    _write(
                        self.fixture.independent_source_root / "Dockerfile",
                        b"changed-after-source-copy\n",
                    )
            return copied

        context, receipt = self.fixture.outputs("source-race")
        with mock.patch.object(
            subject,
            "_copy_snapshot_file",
            side_effect=mutate_after_last_source_copy,
        ):
            with self.assertRaisesRegex(
                subject.FencedFiCandidateBuildContextError,
                "SOURCE_GIT_WORKTREE_POLLUTED|SOURCE_CHANGED",
            ):
                self.fixture.prepare("source-race")
        self.assertEqual(source_files, source_copies)
        self.assertTrue(context.exists())
        self.assertFalse(receipt.exists())

    def test_rejects_injected_empty_context_directory_without_a_receipt(self) -> None:
        original = subject._copy_snapshot_file
        expected_copies = (
            len(subject._tracked_source_records(self.fixture.independent_source_root))
            + input_subject._parse_static_manifest(
                self.fixture.static_manifest.read_bytes()
            ).file_count
        )
        copies = 0

        def inject_after_last_copy(**kwargs: object) -> subject.FileSnapshot:
            nonlocal copies
            copied = original(**kwargs)
            copies += 1
            if copies == expected_copies:
                (Path(str(kwargs["context_root"])) / "unverified-empty").mkdir(mode=0o700)
            return copied

        context, receipt = self.fixture.outputs("extra-empty-directory")
        with mock.patch.object(
            subject,
            "_copy_snapshot_file",
            side_effect=inject_after_last_copy,
        ):
            with self.assertRaisesRegex(
                subject.FencedFiCandidateBuildContextError,
                "OUTPUT_MISMATCH",
            ):
                self.fixture.prepare("extra-empty-directory")
        self.assertEqual(expected_copies, copies)
        self.assertTrue(context.exists())
        self.assertFalse(receipt.exists())

    def test_rejects_context_or_receipt_path_overlap_before_context_creation(self) -> None:
        for name, context, receipt in (
            (
                "context-inside-source",
                self.fixture.independent_source_root / "fresh-context",
                self.fixture.outputs("context-inside-source")[1],
            ),
            (
                "receipt-inside-static",
                self.fixture.outputs("receipt-inside-static")[0],
                self.fixture.static_root / "receipt.json",
            ),
            (
                "receipt-inside-context",
                self.fixture.outputs("receipt-inside-context")[0],
                self.fixture.outputs("receipt-inside-context")[0] / "receipt.json",
            ),
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    subject.FencedFiCandidateBuildContextError,
                    "PATH_OVERLAP",
                ):
                    self.fixture.prepare(
                        name,
                        context_output=context,
                        receipt_output=receipt,
                    )
                self.assertFalse(context.exists())
                self.assertFalse(receipt.exists())

    def test_context_and_receipt_outputs_are_create_only(self) -> None:
        self.fixture.prepare("create-only")
        with self.assertRaisesRegex(
            subject.FencedFiCandidateBuildContextError,
            "CONTEXT_OUTPUT_EXISTS",
        ):
            self.fixture.prepare("create-only")

    def test_preexisting_receipt_refuses_before_context_creation(self) -> None:
        for name, make_receipt in (
            (
                "receipt-file",
                lambda path: _write(path, b"already-present\n", mode=0o600),
            ),
            (
                "receipt-symlink",
                lambda path: path.symlink_to(self.fixture.directory / "missing-receipt"),
            ),
        ):
            with self.subTest(name=name):
                context, receipt = self.fixture.outputs(name)
                make_receipt(receipt)
                with self.assertRaisesRegex(
                    subject.FencedFiCandidateBuildContextError,
                    "RECEIPT_OUTPUT_EXISTS",
                ):
                    self.fixture.prepare(name)
                self.assertFalse(context.exists())
                self.assertTrue(receipt.is_symlink() or receipt.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
