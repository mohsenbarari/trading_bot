from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


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
                repo=REPO_ROOT,
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
                self.assertNotIn(f"{package.PACKAGE_ROOT}/app/main.py", members)
                release = json.loads(archive.extractfile(f"{package.PACKAGE_ROOT}/RELEASE.json").read())
                self.assertEqual(release["emergency_patch_sha"], self.head())

    def test_refuses_non_head_identity_and_existing_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-package-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            output = directory / "package.tar.gz"
            with self.assertRaisesRegex(package.EmergencyPackageError, "worktree HEAD"):
                package.build_package(
                    repo=REPO_ROOT,
                    source_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                    emergency_patch_sha="f" * 40,
                    output=output,
                )
            package.build_package(
                repo=REPO_ROOT,
                source_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                emergency_patch_sha=self.head(),
                output=output,
            )
            with self.assertRaisesRegex(package.EmergencyPackageError, "overwrite"):
                package.build_package(
                    repo=REPO_ROOT,
                    source_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                    emergency_patch_sha=self.head(),
                    output=output,
                )


if __name__ == "__main__":
    unittest.main()
