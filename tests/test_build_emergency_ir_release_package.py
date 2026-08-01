from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "build_emergency_ir_release_package.py"
SPEC = importlib.util.spec_from_file_location("build_emergency_ir_release_package", MODULE_PATH)
assert SPEC and SPEC.loader
package = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = package
SPEC.loader.exec_module(package)


class BuildEmergencyIrReleasePackageTests(unittest.TestCase):
    def head(self) -> str:
        return subprocess.check_output(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True).strip()

    def test_builds_minimal_deterministic_release_package(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-package-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            output = directory / "package.tar.gz"
            digest, size = package.build_package(
                source_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                emergency_patch_sha=self.head(),
                output=output,
            )
            self.assertEqual(len(digest), 64)
            self.assertEqual(size, output.stat().st_size)
            with tarfile.open(output, "r:gz") as archive:
                members = {member.name for member in archive.getmembers()}
                self.assertIn(f"{package.PACKAGE_ROOT}/RELEASE.json", members)
                self.assertIn(
                    f"{package.PACKAGE_ROOT}/deploy/emergency-ir/docker-compose.standalone.yml", members
                )
                self.assertIn(
                    f"{package.PACKAGE_ROOT}/scripts/verify_emergency_ir_sms_egress_image.py", members
                )
                self.assertIn(
                    f"{package.PACKAGE_ROOT}/scripts/emergency_ir_standalone_activate.py", members
                )
                self.assertNotIn(f"{package.PACKAGE_ROOT}/app/main.py", members)
                release = json.loads(archive.extractfile(f"{package.PACKAGE_ROOT}/RELEASE.json").read())
                self.assertEqual(release["emergency_patch_sha"], self.head())
                self.assertNotIn(f"{package.PACKAGE_ROOT}/scripts/__init__.py", members)
                extracted = directory / "extracted"
                archive.extractall(extracted)
            activation = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(extracted / package.PACKAGE_ROOT / "scripts/emergency_ir_standalone_activate.py"),
                    "--help",
                ],
                cwd=extracted / package.PACKAGE_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(activation.returncode, 0, activation.stderr)
            self.assertIn("--stage", activation.stdout)

    def test_refuses_non_head_identity_and_existing_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-package-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            output = directory / "package.tar.gz"
            with self.assertRaisesRegex(package.EmergencyPackageError, "worktree HEAD"):
                package.build_package(
                    source_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                    emergency_patch_sha="f" * 40,
                    output=output,
                )
            package.build_package(
                source_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                emergency_patch_sha=self.head(),
                output=output,
            )
            with self.assertRaisesRegex(package.EmergencyPackageError, "overwrite"):
                package.build_package(
                    source_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                    emergency_patch_sha=self.head(),
                    output=output,
                )

    def test_uses_head_blobs_not_an_uncommitted_worktree_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-package-repo-") as raw:
            repository = Path(raw) / "repo"
            (repository / "deploy/emergency-ir").mkdir(parents=True)
            (repository / "scripts").mkdir(parents=True)
            tracked = repository / "deploy/emergency-ir/docker-compose.standalone.yml"
            tracked.write_text("committed-content\n", encoding="utf-8")
            builder = repository / package.PACKAGE_BUILDER_SOURCE_PATH
            builder.write_text("committed package builder source\n", encoding="utf-8")
            builder.chmod(0o600)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "Emergency Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)
            head = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
            tracked.write_text("uncommitted-content\n", encoding="utf-8")
            output = Path(raw) / "package.tar.gz"
            with patch.object(package, "REPO_ROOT", repository):
                package.build_package(
                    source_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                    emergency_patch_sha=head,
                    output=output,
                )
            with tarfile.open(output, "r:gz") as archive:
                payload = archive.extractfile(
                    f"{package.PACKAGE_ROOT}/deploy/emergency-ir/docker-compose.standalone.yml"
                ).read().decode("utf-8")
            self.assertEqual(payload, "committed-content\n")

    def test_rejects_a_skip_worktree_package_builder_substitution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-package-builder-") as raw:
            repository = Path(raw) / "repo"
            builder = repository / package.PACKAGE_BUILDER_SOURCE_PATH
            builder.parent.mkdir(parents=True)
            builder.write_text("committed package builder source\n", encoding="utf-8")
            builder.chmod(0o600)
            (repository / "deploy/emergency-ir").mkdir(parents=True)
            (repository / "deploy/emergency-ir/docker-compose.standalone.yml").write_text(
                "committed content\n", encoding="utf-8"
            )
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "Emergency Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)
            head = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "update-index",
                    "--skip-worktree",
                    package.PACKAGE_BUILDER_SOURCE_PATH,
                ],
                check=True,
            )
            builder.write_text("substituted package builder source\n", encoding="utf-8")
            builder.chmod(0o600)
            with patch.object(package, "REPO_ROOT", repository):
                with self.assertRaisesRegex(package.EmergencyPackageError, "builder source differs"):
                    package.build_package(
                        source_release_sha=package.SOURCE_RELEASE_SHA,
                        emergency_patch_sha=head,
                        output=Path(raw) / "package.tar.gz",
                    )

    def test_cli_rejects_a_caller_selected_repository(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                package.parse_args(
                    [
                        "--repo", "/tmp/substituted-checkout",
                        "--source-release-sha", "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                        "--emergency-patch-sha", self.head(),
                        "--output", "/tmp/package.tar.gz",
                    ]
                )
        self.assertIn("--repo", stderr.getvalue())

    def test_cli_requires_the_isolated_direct_script_contract(self) -> None:
        plain = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--help"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(plain.returncode, 2)
        self.assertIn("-I -B", plain.stdout)
        isolated = subprocess.run(
            [sys.executable, "-I", "-B", str(MODULE_PATH), "--help"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(isolated.returncode, 0, isolated.stderr)
        self.assertNotIn("--repo", isolated.stdout)


if __name__ == "__main__":
    unittest.main()
