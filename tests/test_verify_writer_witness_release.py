import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts/verify_writer_witness_release.py"
SPEC = importlib.util.spec_from_file_location("writer_witness_release_verifier", VERIFIER_PATH)
assert SPEC is not None and SPEC.loader is not None
release_verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_verifier)


EXPECTED_EXECUTABLE_PATHS = {
    "scripts/run_writer_witness_clock_jump_probe.py",
    "scripts/smoke_writer_witness_client.py",
    "scripts/verify_writer_witness_nftables.py",
    "scripts/verify_writer_witness_release.py",
    "scripts/verify_writer_witness_runtime.py",
    "scripts/verify_writer_witness_runtime_provenance.py",
    "scripts/verify_writer_witness_process_maps.py",
    "scripts/verify_writer_witness_wheelhouse.py",
}


class WriterWitnessReleaseMetadataTests(unittest.TestCase):
    def test_cli_rejects_unisolated_python_before_argument_parsing(self):
        completed = subprocess.run(
            ["/usr/bin/python3.12", str(VERIFIER_PATH)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("requires isolated clean Python startup", completed.stderr)

    def _build_release(self, parent: Path) -> tuple[Path, str]:
        release = parent / "release"
        release.mkdir(mode=0o755)
        release.chmod(0o755)
        subprocess.run(
            ["bash", str(ROOT / "scripts/build_writer_witness_release.sh"), str(release)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        manifest_sha256 = hashlib.sha256(
            (release / "release-manifest.json").read_bytes()
        ).hexdigest()
        return release, manifest_sha256

    def _verify(
        self,
        release: Path,
        manifest_sha256: str,
        *,
        expected_uid: int | None = None,
        expected_gid: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "/usr/bin/env",
                "-i",
                "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                "pycache_prefix=/dev/null",
                str(VERIFIER_PATH),
                "--release-root",
                str(release),
                "--expected-manifest-sha256",
                manifest_sha256,
                "--expected-uid",
                str(os.geteuid() if expected_uid is None else expected_uid),
                "--expected-gid",
                str(os.getegid() if expected_gid is None else expected_gid),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_built_release_has_the_exact_reviewed_metadata_contract(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-release-metadata-") as value:
            release, manifest_sha256 = self._build_release(Path(value))
            manifest = json.loads((release / "release-manifest.json").read_text())
            observed_executables = {
                relative
                for relative in manifest
                if stat.S_IMODE((release / relative).lstat().st_mode) == 0o755
            }
            self.assertEqual(observed_executables, EXPECTED_EXECUTABLE_PATHS)
            self.assertEqual(release_verifier.EXECUTABLE_RELEASE_PATHS, EXPECTED_EXECUTABLE_PATHS)
            self.assertEqual(stat.S_IMODE(release.lstat().st_mode), 0o755)
            for directory in (item for item in release.rglob("*") if item.is_dir()):
                self.assertEqual(stat.S_IMODE(directory.lstat().st_mode), 0o755, directory)
            for file_path in (item for item in release.rglob("*") if item.is_file()):
                relative = file_path.relative_to(release).as_posix()
                expected_mode = 0o755 if relative in EXPECTED_EXECUTABLE_PATHS else 0o644
                metadata = file_path.lstat()
                self.assertEqual(stat.S_IMODE(metadata.st_mode), expected_mode, relative)
                self.assertEqual(metadata.st_nlink, 1, relative)

            verified = self._verify(release, manifest_sha256)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertIn("release_metadata_attested=yes", verified.stdout)
            self.assertIn(f"release_expected_uid={os.geteuid()}", verified.stdout)
            self.assertIn(f"release_expected_gid={os.getegid()}", verified.stdout)
            self.assertIn("release_executable_entries=8", verified.stdout)

    def test_release_rejects_an_owner_binding_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-release-owner-") as value:
            release, manifest_sha256 = self._build_release(Path(value))
            verified = self._verify(
                release,
                manifest_sha256,
                expected_uid=os.geteuid() + 1,
            )
            self.assertNotEqual(verified.returncode, 0)
            self.assertIn("release entry owner mismatch", verified.stderr)

            verified = self._verify(
                release,
                manifest_sha256,
                expected_gid=os.getegid() + 1,
            )
            self.assertNotEqual(verified.returncode, 0)
            self.assertIn("release entry owner mismatch", verified.stderr)

    def test_release_rejects_root_directory_and_file_mode_drift(self):
        cases = (
            (lambda release: release.chmod(0o700), "release directory mode mismatch"),
            (lambda release: (release / "core").chmod(0o775), "release directory mode mismatch"),
            (
                lambda release: (release / "writer_witness_app.py").chmod(0o664),
                "release file mode mismatch: writer_witness_app.py",
            ),
            (
                lambda release: (release / "scripts/verify_writer_witness_release.py").chmod(
                    0o644
                ),
                "release file mode mismatch: scripts/verify_writer_witness_release.py",
            ),
        )
        for mutate, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with tempfile.TemporaryDirectory(
                    prefix="writer-witness-release-mode-"
                ) as value:
                    release, manifest_sha256 = self._build_release(Path(value))
                    mutate(release)
                    verified = self._verify(release, manifest_sha256)
                    self.assertNotEqual(verified.returncode, 0)
                    self.assertIn(expected_error, verified.stderr)

    def test_release_rejects_a_hardlinked_manifested_file(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-release-link-") as value:
            parent = Path(value)
            release, manifest_sha256 = self._build_release(parent)
            target = release / "writer_witness_app.py"
            os.link(target, parent / "outside-hardlink")
            verified = self._verify(release, manifest_sha256)
            self.assertNotEqual(verified.returncode, 0)
            self.assertIn(
                "release file has an unsafe hard-link count: writer_witness_app.py",
                verified.stderr,
            )

    def test_release_rejects_file_metadata_changed_during_descriptor_read(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-release-race-") as value:
            release, manifest_sha256 = self._build_release(Path(value))
            target = (release / "writer_witness_app.py").resolve()
            original_read = os.read
            mutated = False

            def mutating_read(descriptor: int, count: int) -> bytes:
                nonlocal mutated
                data = original_read(descriptor, count)
                try:
                    opened_path = Path(os.readlink(f"/proc/self/fd/{descriptor}")).resolve()
                except OSError:
                    opened_path = None
                if data and not mutated and opened_path == target:
                    target.chmod(0o600)
                    mutated = True
                return data

            with mock.patch.object(release_verifier.os, "read", side_effect=mutating_read):
                with self.assertRaisesRegex(
                    release_verifier.AttestationError,
                    "release file changed during attestation",
                ):
                    release_verifier.attest_release(
                        release,
                        manifest_sha256,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                    )
            self.assertTrue(mutated)

    def test_release_rejects_directory_metadata_changed_between_tree_scans(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-release-dir-race-") as value:
            release, manifest_sha256 = self._build_release(Path(value))
            target = release / "core"
            original_hash = release_verifier._sha256_regular_file
            mutated = False

            def mutating_hash(*args, **kwargs):
                nonlocal mutated
                digest = original_hash(*args, **kwargs)
                if not mutated:
                    target.chmod(0o700)
                    mutated = True
                return digest

            with mock.patch.object(
                release_verifier,
                "_sha256_regular_file",
                side_effect=mutating_hash,
            ):
                with self.assertRaisesRegex(
                    release_verifier.AttestationError,
                    "release directory mode mismatch",
                ):
                    release_verifier.attest_release(
                        release,
                        manifest_sha256,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                    )
            self.assertTrue(mutated)


if __name__ == "__main__":
    unittest.main()
