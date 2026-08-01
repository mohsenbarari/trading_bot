from __future__ import annotations

import base64
import importlib.util
import io
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "build_emergency_ir_receiver_bundle.py"
SPEC = importlib.util.spec_from_file_location("build_emergency_ir_receiver_bundle", MODULE_PATH)
assert SPEC and SPEC.loader
bundle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bundle
SPEC.loader.exec_module(bundle)


class BuildEmergencyIrReceiverBundleTests(unittest.TestCase):
    def public_key(self, directory: Path) -> Path:
        key = Ed25519PrivateKey.generate().public_key()
        path = directory / "signing-public.key"
        path.write_text(
            base64.b64encode(
                key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            ).decode("ascii") + "\n",
            encoding="ascii",
        )
        path.chmod(0o600)
        return path

    def test_builds_exact_small_bundle_with_only_allowlisted_members(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-receiver-bundle-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            output = directory / "receiver.tar.gz"
            key = self.public_key(directory)
            expected_digest, expected_size = bundle.bundle_digest(
                signing_public_key=key
            )
            digest, size = bundle.build_bundle(
                signing_public_key=key, output=output
            )
            self.assertEqual(digest, expected_digest)
            self.assertEqual(size, expected_size)
            self.assertEqual(size, output.stat().st_size)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with tarfile.open(output, "r:gz") as archive:
                self.assertEqual(
                    {member.name for member in archive.getmembers()},
                    {target for _, target in bundle.BUNDLE_MEMBERS} | {"signing-public.key"},
                )
                self.assertNotIn("scripts/__init__.py", {member.name for member in archive.getmembers()})
                self.assertTrue(all(member.isreg() for member in archive.getmembers()))

    def test_refuses_to_overwrite_a_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-receiver-bundle-") as raw:
            directory = Path(raw)
            directory.chmod(0o700)
            output = directory / "receiver.tar.gz"
            public_key = self.public_key(directory)
            bundle.build_bundle(signing_public_key=public_key, output=output)
            with self.assertRaisesRegex(bundle.ReceiverBundleError, "overwrite"):
                bundle.build_bundle(signing_public_key=public_key, output=output)

    def test_bundle_reads_committed_blobs_not_a_mutable_checkout_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-receiver-source-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            repository = root / "repository"
            source = repository / "scripts" / "bootstrap.py"
            source.parent.mkdir(parents=True)
            source.write_text("committed source\n", encoding="utf-8")
            source.chmod(0o600)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "Emergency Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "scripts/bootstrap.py"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)
            revision = subprocess.check_output(
                ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
            ).strip()
            source.write_text("mutable replacement\n", encoding="utf-8")
            source.chmod(0o600)
            with (
                patch.object(bundle, "REPO_ROOT", repository),
                patch.object(bundle, "BUNDLE_MEMBERS", (("scripts/bootstrap.py", "scripts/bootstrap.py"),)),
            ):
                payload = bundle.render_bundle(
                    signing_public_key=self.public_key(root), source_revision=revision
                )
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                bundled = archive.extractfile("scripts/bootstrap.py").read()
            self.assertEqual(bundled, b"committed source\n")

    def test_entrypoint_runs_with_the_same_isolated_python_flag_as_wa_ir_bootstrap(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(REPO_ROOT / "deploy/emergency-ir/run_object_storage_receiver.py"),
                "--help",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--signing-public-key", result.stdout)
        self.assertNotIn("--repo", result.stdout)

    def test_builder_entrypoint_requires_the_isolated_direct_script_contract(self) -> None:
        plain = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--help"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(plain.returncode, 2)
        self.assertIn("-I -B", plain.stderr)

        result = subprocess.run(
            [sys.executable, "-I", "-B", str(MODULE_PATH), "--help"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--signing-public-key", result.stdout)

    def test_isolated_entrypoints_ignore_an_ambient_scripts_regular_package(self) -> None:
        """An implicit namespace must not be shadowed by a system-site package."""

        with tempfile.TemporaryDirectory(prefix="emergency-ir-ambient-scripts-") as raw:
            root = Path(raw)
            ambient = root / "ambient"
            package = ambient / "scripts"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                "raise RuntimeError('ambient scripts initializer executed')\n", encoding="utf-8"
            )
            code = (
                "import runpy, sys; "
                f"sys.path.append({str(ambient)!r}); "
                f"runpy.run_path({str(MODULE_PATH)!r}, run_name='__main__')"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", code, "--help"],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--signing-public-key", result.stdout)

            for entrypoint, expected_option in (
                (REPO_ROOT / "deploy/emergency-ir/run_object_storage_receiver.py", "--signing-public-key"),
                (REPO_ROOT / "scripts/emergency_ir_object_storage_receiver.py", "--signing-public-key"),
                (REPO_ROOT / "scripts/emergency_ir_standalone_activate.py", "--stage"),
            ):
                with self.subTest(entrypoint=entrypoint.name):
                    code = (
                        "import runpy, sys; "
                        f"sys.path.append({str(ambient)!r}); "
                        f"runpy.run_path({str(entrypoint)!r}, run_name='__main__')"
                    )
                    result = subprocess.run(
                        [sys.executable, "-I", "-B", "-c", code, "--help"],
                        cwd=REPO_ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(expected_option, result.stdout)

    def test_activator_runs_with_isolated_python_from_the_pinned_bundle_layout(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(REPO_ROOT / "scripts/emergency_ir_standalone_activate.py"),
                "--help",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--stage", result.stdout)


if __name__ == "__main__":
    unittest.main()
