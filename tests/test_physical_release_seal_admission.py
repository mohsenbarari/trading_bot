"""Focused local-only tests for physical source/image release sealing."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID

import core.physical_release_seal_admission as seal


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
WORKTREE = Path("/srv/trading-bot-three-site/seal-worktree")
RELEASE = "a" * 40
TREE = "b" * 40


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def tree_listing(*, duplicate_path: bool = False) -> bytes:
    entries = (
        ("100644", "c" * 40, ".gitignore"),
        ("100644", "d" * 40, "README.md"),
        ("100755", "e" * 40, "scripts/release.sh"),
    )
    output = b"".join(
        f"{mode} blob {object_id}\t{path}".encode("ascii") + b"\0"
        for mode, object_id, path in entries
    )
    if duplicate_path:
        output += b"100644 blob " + b"f" * 40 + b"\tREADME.md\0"
    return output


def filesystem_object(
    path: Path,
    *,
    mode: int,
    regular_file: bool,
    directory: bool,
    executable: bool,
    owner_uid: int = 0,
    symlink: bool = False,
    ancestors_root_controlled: bool = True,
) -> seal.PhysicalReleaseSealFilesystemObject:
    return seal.PhysicalReleaseSealFilesystemObject(
        path=path,
        owner_uid=owner_uid,
        mode=mode,
        regular_file=regular_file,
        directory=directory,
        symlink=symlink,
        executable=executable,
        ancestors_root_controlled=ancestors_root_controlled,
        device=1,
        inode={
            WORKTREE: 101,
            WORKTREE / ".git": 102,
            seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY: 103,
        }[path],
        ctime_ns=1_000_000_000,
        mtime_ns=1_000_000_000,
    )


def inspection(*, worktree_mode: int = 0o750) -> seal.PhysicalReleaseSealWorktreeInspection:
    return seal.PhysicalReleaseSealWorktreeInspection(
        worktree=filesystem_object(
            WORKTREE,
            mode=worktree_mode,
            regular_file=False,
            directory=True,
            executable=True,
        ),
        git_metadata=filesystem_object(
            WORKTREE / ".git",
            mode=0o700,
            regular_file=False,
            directory=True,
            executable=True,
        ),
        git_binary=filesystem_object(
            seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY,
            mode=0o755,
            regular_file=True,
            directory=False,
            executable=True,
        ),
    )


class _FilesystemInspector:
    def __init__(
        self,
        *,
        before: seal.PhysicalReleaseSealWorktreeInspection | None = None,
        after: seal.PhysicalReleaseSealWorktreeInspection | None = None,
    ) -> None:
        self.before = inspection() if before is None else before
        self.after = self.before if after is None else after
        self.calls: list[Path] = []

    def inspect_worktree(self, *, worktree: Path):
        self.calls.append(worktree)
        return self.before if len(self.calls) == 1 else self.after


class _GitRunner:
    def __init__(self) -> None:
        self.calls: list[seal.PhysicalReleaseSealGitInvocation] = []
        self.heads = [RELEASE, RELEASE]
        self.statuses = [b"", b""]
        self.tree_id = TREE
        self.tree_raw = tree_listing()
        self.fail_commands: set[tuple[str, ...]] = set()

    def run(self, *, invocation: seal.PhysicalReleaseSealGitInvocation):
        self.calls.append(invocation)
        arguments = invocation.arguments[3:]
        if arguments in self.fail_commands:
            return seal.PhysicalReleaseSealGitCommandResult(exit_code=1, stdout_bytes=b"")
        if arguments == ("rev-parse", "--verify", "HEAD^{commit}"):
            if not self.heads:
                raise AssertionError("unexpected third HEAD inspection")
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=(self.heads.pop(0) + "\n").encode("ascii"),
            )
        if arguments == (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
        ):
            if not self.statuses:
                raise AssertionError("unexpected third status inspection")
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=self.statuses.pop(0),
            )
        if arguments == ("rev-parse", "--verify", RELEASE + "^{tree}"):
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=(self.tree_id + "\n").encode("ascii"),
            )
        if arguments == ("ls-tree", "-r", "-z", "--full-tree", RELEASE):
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=self.tree_raw,
            )
        raise AssertionError(f"unexpected invocation: {arguments!r}")


class PhysicalReleaseSealAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.images = tuple(
            seal.PhysicalReleaseSealImage(
                role=role,
                reference=(
                    f"registry.example:5000/gold-trade/{role}@sha256:"
                    + digest("image:" + role)
                ),
            )
            for role in seal.REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES
        )
        self.filesystem = _FilesystemInspector()
        self.git = _GitRunner()

    def config(self, **changes: object) -> seal.PhysicalReleaseSealAdmissionConfig:
        values: dict[str, object] = {
            "worktree": WORKTREE,
            "campaign_id": "physical-release-seal-20260731",
            "expected_release_sha": RELEASE,
            "images": self.images,
            "seal_id": UUID("d79e6de3-58ff-4bf2-b6df-6b111bd90701"),
            "sealed_at": NOW - timedelta(seconds=1),
            "enabled": True,
            "maximum_freshness_seconds": 180,
        }
        values.update(changes)
        return seal.PhysicalReleaseSealAdmissionConfig(**values)

    def admit(
        self,
        *,
        config: seal.PhysicalReleaseSealAdmissionConfig | None = None,
    ) -> seal.SealedPhysicalReleaseDescriptor:
        with patch.object(seal.os, "geteuid", return_value=0):
            return seal.admit_physical_release_seal(
                config=self.config() if config is None else config,
                filesystem_inspector=self.filesystem,
                git_runner=self.git,
                now=NOW,
            )

    def test_clean_root_owned_worktree_mints_canonical_non_authorizing_descriptor_and_bootstrap_projection(
        self,
    ) -> None:
        descriptor = self.admit()
        projected = seal.parse_physical_release_seal_descriptor(
            descriptor.canonical_descriptor
        )
        self.assertEqual(RELEASE, descriptor.release_sha)
        self.assertEqual(RELEASE, descriptor.control_release_sha)
        self.assertEqual(TREE, descriptor.git_tree_id)
        self.assertEqual(descriptor.descriptor_sha256, projected.descriptor_sha256)
        self.assertEqual(
            seal.REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES,
            tuple(image.role for image in projected.images),
        )
        self.assertFalse(descriptor.publish_authorized)
        self.assertFalse(descriptor.deployment_authorized)
        self.assertFalse(descriptor.execution_authorized)
        self.assertNotIn(str(WORKTREE).encode("ascii"), descriptor.canonical_descriptor)
        self.assertNotIn(b"credential", descriptor.canonical_descriptor.lower())
        self.assertIs(
            descriptor,
            seal.require_sealed_physical_release_descriptor(descriptor, now=NOW),
        )

        bootstrap = seal.project_physical_release_seal_for_wa_ir_bootstrap(
            descriptor, now=NOW
        )
        self.assertEqual(descriptor.campaign_id, bootstrap.campaign_id)
        self.assertEqual(descriptor.release_sha, bootstrap.release_sha)
        self.assertEqual(descriptor.control_release_sha, bootstrap.control_release_sha)
        self.assertEqual(descriptor.release_bundle_sha256, bootstrap.release_bundle_sha256)
        self.assertEqual(descriptor.image_set_sha256, bootstrap.image_set_sha256)
        self.assertEqual(
            descriptor.release_provenance_sha256,
            bootstrap.release_provenance_sha256,
        )
        self.assertEqual("webapp_fi", bootstrap.source_site)
        self.assertEqual("webapp_ir", bootstrap.destination_site)
        from core.physical_wa_ir_bootstrap_bundle_builder import (
            seal_wa_ir_bootstrap_exact_release_binding,
        )

        downstream_seal = seal_wa_ir_bootstrap_exact_release_binding(bootstrap)
        self.assertEqual(descriptor.release_sha, downstream_seal.release_sha)
        self.assertEqual(descriptor.image_set_sha256, downstream_seal.image_set_sha256)
        self.assertEqual(
            descriptor.release_provenance_sha256,
            downstream_seal.release_provenance_sha256,
        )

        expected_commands = (
            ("rev-parse", "--verify", "HEAD^{commit}"),
            (
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignored=matching",
            ),
            ("rev-parse", "--verify", RELEASE + "^{tree}"),
            ("ls-tree", "-r", "-z", "--full-tree", RELEASE),
            (
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignored=matching",
            ),
            ("rev-parse", "--verify", "HEAD^{commit}"),
        )
        self.assertEqual(
            expected_commands,
            tuple(invocation.arguments[3:] for invocation in self.git.calls),
        )
        for invocation in self.git.calls:
            self.assertEqual(seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY, invocation.executable)
            self.assertEqual(WORKTREE, invocation.worktree)
            self.assertEqual(
                seal._GIT_ENVIRONMENT,
                invocation.environment,
            )
            self.assertFalse(
                {"build", "pull", "push", "fetch", "clone", "docker", "ssh"}
                & set(invocation.arguments)
            )
        self.assertEqual([WORKTREE, WORKTREE], self.filesystem.calls)

    def test_disabled_nonroot_and_stale_inputs_do_not_inspect_source(self) -> None:
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError, "PHYSICAL_RELEASE_SEAL_DISABLED"
        ):
            self.admit(config=self.config(enabled=False))
        self.assertEqual([], self.filesystem.calls)
        self.assertEqual([], self.git.calls)

        with patch.object(seal.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_ROOT_RUNTIME_REQUIRED",
        ):
            seal.admit_physical_release_seal(
                config=self.config(),
                filesystem_inspector=self.filesystem,
                git_runner=self.git,
                now=NOW,
            )
        self.assertEqual([], self.filesystem.calls)
        self.assertEqual([], self.git.calls)

        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError, "PHYSICAL_RELEASE_SEAL_STALE"
        ):
            self.admit(config=self.config(sealed_at=NOW - timedelta(seconds=181)))
        self.assertEqual([], self.filesystem.calls)
        self.assertEqual([], self.git.calls)

    def test_dirty_untracked_and_ignored_files_fail_before_tree_digest(self) -> None:
        for status in (
            b" M tracked.py\0",
            b"?? untracked-source.py\0",
            b"!! ignored-source.py\0",
        ):
            with self.subTest(status=status):
                self.filesystem = _FilesystemInspector()
                self.git = _GitRunner()
                self.git.statuses = [status]
                with self.assertRaisesRegex(
                    seal.PhysicalReleaseSealAdmissionError,
                    "PHYSICAL_RELEASE_SEAL_DIRTY_OR_UNTRACKED_WORKTREE",
                ):
                    self.admit()
                self.assertEqual(2, len(self.git.calls))
                self.assertNotIn(
                    ("ls-tree", "-r", "-z", "--full-tree", RELEASE),
                    tuple(invocation.arguments[3:] for invocation in self.git.calls),
                )

    def test_head_mismatch_or_change_during_inspection_fails_closed(self) -> None:
        self.git.heads = ["f" * 40]
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_HEAD_RELEASE_MISMATCH",
        ):
            self.admit()
        self.assertEqual(1, len(self.git.calls))

        self.filesystem = _FilesystemInspector()
        self.git = _GitRunner()
        self.git.heads = [RELEASE, "f" * 40]
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_UNSTABLE_WORKTREE",
        ):
            self.admit()
        self.assertEqual(6, len(self.git.calls))

    def test_unsafe_root_ownership_mode_or_changed_filesystem_evidence_fails_before_or_after_git(
        self,
    ) -> None:
        self.filesystem = _FilesystemInspector(before=inspection(worktree_mode=0o775))
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_FILESYSTEM_OWNERSHIP_OR_MODE_INVALID",
        ):
            self.admit()
        self.assertEqual([], self.git.calls)

        broken = inspection()
        broken = replace(
            broken,
            git_metadata=replace(broken.git_metadata, owner_uid=1000),
        )
        self.filesystem = _FilesystemInspector(before=broken)
        self.git = _GitRunner()
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_FILESYSTEM_OWNERSHIP_OR_MODE_INVALID",
        ):
            self.admit()
        self.assertEqual([], self.git.calls)

        self.filesystem = _FilesystemInspector(
            before=inspection(), after=inspection(worktree_mode=0o700)
        )
        self.git = _GitRunner()
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_UNSTABLE_WORKTREE",
        ):
            self.admit()

        changed_identity = inspection()
        changed_identity = replace(
            changed_identity,
            git_metadata=replace(changed_identity.git_metadata, inode=999),
        )
        self.filesystem = _FilesystemInspector(
            before=inspection(), after=changed_identity
        )
        self.git = _GitRunner()
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_UNSTABLE_WORKTREE",
        ):
            self.admit()

    def test_incomplete_duplicate_or_unpinned_image_set_never_inspects_worktree(self) -> None:
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_IMAGE_SET_INVALID",
        ):
            self.admit(config=self.config(images=self.images[:-1]))
        self.assertEqual([], self.filesystem.calls)

        duplicate = list(self.images)
        duplicate[-1] = replace(duplicate[-1], role="webapp_fi_app")
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_IMAGE_SET_INVALID",
        ):
            self.admit(config=self.config(images=tuple(duplicate)))
        self.assertEqual([], self.filesystem.calls)

        unpinned = list(self.images)
        unpinned[0] = replace(unpinned[0], reference="registry.example/gold-trade/app:latest")
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_IMAGE_SET_INVALID",
        ):
            self.admit(config=self.config(images=tuple(unpinned)))
        self.assertEqual([], self.filesystem.calls)

    def test_noncanonical_tampered_or_stale_descriptor_is_rejected(self) -> None:
        descriptor = self.admit()
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_DESCRIPTOR_NONCANONICAL",
        ):
            seal.parse_physical_release_seal_descriptor(
                descriptor.canonical_descriptor[:-1] + b" \n"
            )
        decoded = json.loads(descriptor.canonical_descriptor)
        decoded["descriptor_sha256"] = "f" * 64
        tampered = seal.canonical_json_bytes(decoded) + b"\n"
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID",
        ):
            seal.parse_physical_release_seal_descriptor(tampered)
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_DESCRIPTOR_STALE",
        ):
            seal.require_sealed_physical_release_descriptor(
                descriptor, now=NOW + timedelta(seconds=181)
            )

    def test_noncanonical_tracked_tree_cannot_be_sealed(self) -> None:
        self.git.tree_raw = tree_listing(duplicate_path=True)
        with self.assertRaisesRegex(
            seal.PhysicalReleaseSealAdmissionError,
            "PHYSICAL_RELEASE_SEAL_TRACKED_TREE_NONCANONICAL",
        ):
            self.admit()
        self.assertEqual(4, len(self.git.calls))


if __name__ == "__main__":
    unittest.main()
