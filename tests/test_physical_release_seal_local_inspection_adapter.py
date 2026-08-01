"""Focused proof for the concrete local-only release-seal inspection adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from core import physical_release_seal_admission as seal
from core import physical_release_seal_local_inspection_adapter as local


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY), "-C", str(repo), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@unittest.skipUnless(os.geteuid() == 0, "adapter intentionally requires root-owned paths")
class PhysicalReleaseSealLocalInspectionAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="physical-release-seal-local-adapter-"
        )
        self.root = Path(self._temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir(mode=0o700)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "seal-test@example.invalid")
        _git(self.repo, "config", "user.name", "Physical Seal Test")
        (self.repo / "README.md").write_text("sealed source fixture\n", encoding="ascii")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-qm", "fixture")
        self.release = _git(self.repo, "rev-parse", "HEAD").stdout.decode("ascii").strip()
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _images(self) -> tuple[seal.PhysicalReleaseSealImage, ...]:
        return tuple(
            seal.PhysicalReleaseSealImage(
                role=role,
                reference=(
                    f"registry.example:5000/gold-trade/{role}@sha256:"
                    + _digest("image:" + role)
                ),
            )
            for role in seal.REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES
        )

    def _adapter(self) -> local.PhysicalReleaseSealLocalInspectionAdapter:
        return local.PhysicalReleaseSealLocalInspectionAdapter(
            config=local.PhysicalReleaseSealLocalInspectionAdapterConfig(
                worktree=self.repo,
                expected_release_sha=self.release,
                enabled=True,
            )
        )

    def _admission_config(self) -> seal.PhysicalReleaseSealAdmissionConfig:
        return seal.PhysicalReleaseSealAdmissionConfig(
            worktree=self.repo,
            campaign_id="physical-release-local-adapter-20260801",
            expected_release_sha=self.release,
            images=self._images(),
            seal_id=UUID("e4d6df20-c0f7-4edb-aea0-cf8b810c67b9"),
            sealed_at=self.now,
            enabled=True,
        )

    def _expected_head_invocation(self) -> seal.PhysicalReleaseSealGitInvocation:
        return seal.PhysicalReleaseSealGitInvocation(
            executable=seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY,
            arguments=(
                str(seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY),
                "-C",
                str(self.repo),
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ),
            environment=seal._GIT_ENVIRONMENT,
            worktree=self.repo,
        )

    def test_disposable_clean_repo_mints_only_non_authorizing_descriptor(self) -> None:
        adapter = self._adapter()
        descriptor = seal.admit_physical_release_seal(
            config=self._admission_config(),
            filesystem_inspector=adapter,
            git_runner=adapter,
            now=self.now,
        )

        self.assertEqual(self.release, descriptor.release_sha)
        self.assertFalse(descriptor.publish_authorized)
        self.assertFalse(descriptor.deployment_authorized)
        self.assertFalse(descriptor.execution_authorized)
        self.assertEqual(
            descriptor,
            seal.require_sealed_physical_release_descriptor(descriptor, now=self.now),
        )

        inspected = adapter.inspect_worktree(worktree=self.repo)
        self.assertEqual(self.repo, inspected.worktree.path)
        self.assertEqual(self.repo / ".git", inspected.git_metadata.path)
        self.assertEqual(seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY, inspected.git_binary.path)
        self.assertGreater(inspected.worktree.inode, 0)
        self.assertGreater(inspected.git_metadata.inode, 0)

    def test_repository_alias_and_fsmonitor_configuration_cannot_execute(self) -> None:
        marker = self.root / "unexpected-git-side-effect"
        helper = self.root / "unexpected-git-side-effect.sh"
        helper.write_text(
            "#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\nexit 1\n",
            encoding="ascii",
        )
        helper.chmod(0o700)
        for command_name in ("rev-parse", "status", "ls-tree"):
            _git(self.repo, "config", "--local", "alias." + command_name, "!" + str(helper))
        _git(self.repo, "config", "--local", "core.fsmonitor", str(helper))

        adapter = self._adapter()
        descriptor = seal.admit_physical_release_seal(
            config=self._admission_config(),
            filesystem_inspector=adapter,
            git_runner=adapter,
            now=self.now,
        )

        self.assertEqual(self.release, descriptor.release_sha)
        self.assertFalse(marker.exists())

    def test_forbidden_or_tampered_invocation_never_starts_subprocess(self) -> None:
        adapter = self._adapter()
        valid = self._expected_head_invocation()
        attempts = (
            replace(
                valid,
                arguments=(
                    str(seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY),
                    "-C",
                    str(self.repo),
                    "fetch",
                    "origin",
                ),
            ),
            replace(valid, environment=()),
            replace(valid, executable=Path("/bin/sh")),
            replace(valid, worktree=self.root),
        )
        for invocation in attempts:
            with self.subTest(invocation=invocation), patch.object(
                local.subprocess, "Popen"
            ) as process:
                with self.assertRaises(local.PhysicalReleaseSealLocalInspectionAdapterError):
                    adapter.run(invocation=invocation)
                process.assert_not_called()

    def test_disabled_adapter_and_symlink_worktree_fail_before_git(self) -> None:
        disabled = local.PhysicalReleaseSealLocalInspectionAdapterConfig(
            worktree=self.repo,
            expected_release_sha=self.release,
        )
        with patch.object(local.os, "open") as open_file, self.assertRaisesRegex(
            local.PhysicalReleaseSealLocalInspectionAdapterError,
            "PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_DISABLED",
        ):
            local.PhysicalReleaseSealLocalInspectionAdapter(config=disabled)
        open_file.assert_not_called()

        symlink = self.root / "worktree-link"
        symlink.symlink_to(self.repo, target_is_directory=True)
        adapter = local.PhysicalReleaseSealLocalInspectionAdapter(
            config=local.PhysicalReleaseSealLocalInspectionAdapterConfig(
                worktree=symlink,
                expected_release_sha=self.release,
                enabled=True,
            )
        )
        with self.assertRaisesRegex(
            local.PhysicalReleaseSealLocalInspectionAdapterError,
            "PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_WORKTREE_UNSAFE",
        ):
            adapter.inspect_worktree(worktree=symlink)

        os.chmod(self.root, 0o777)
        try:
            with self.assertRaisesRegex(
                local.PhysicalReleaseSealLocalInspectionAdapterError,
                "PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_WORKTREE_UNSAFE",
            ):
                self._adapter().inspect_worktree(worktree=self.repo)
        finally:
            os.chmod(self.root, 0o700)


if __name__ == "__main__":
    unittest.main()
