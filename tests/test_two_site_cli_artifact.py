"""Focused hermetic regression tests for the safe disposable CLI artifact helper module."""

from __future__ import annotations

import hashlib
import inspect
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

import scripts.two_site_cli_artifact as artifact_mod
from scripts.two_site_cli_artifact import (
    CliArtifactError,
    prepare_disposable_cli,
)


class TestDisposableCliArtifact(unittest.TestCase):
    """Regression test suite for prepare_disposable_cli contract and security guarantees."""

    def setUp(self) -> None:
        self._fixture_dir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._fixture_dir.name).resolve()
        self.content = b"#!/bin/sh\necho 'disposable-cli-test-payload'\n"
        self.source_path = self.tmp_path / "sample_cli"
        self.source_path.write_bytes(self.content)
        os.chmod(self.source_path, 0o500)
        self.digest = hashlib.sha256(self.content).hexdigest()

    def tearDown(self) -> None:
        self._fixture_dir.cleanup()

    def test_successful_pinned_copy(self) -> None:
        """Requirement 1: Pinned copy yields a 0700 file, exact bytes, evidence, and cleans up."""
        init_mode = stat.S_IMODE(self.source_path.stat().st_mode)
        disposable_dir: pathlib.Path | None = None

        with prepare_disposable_cli(
            self.source_path,
            expected_sha256=self.digest,
            require_read_only=True,
        ) as (disposable_path, evidence):
            self.assertIsInstance(disposable_path, pathlib.Path)
            self.assertNotEqual(disposable_path, self.source_path)
            self.assertEqual(disposable_path.read_bytes(), self.content)
            disp_mode = stat.S_IMODE(disposable_path.stat().st_mode)
            self.assertEqual(disp_mode, 0o700)
            self.assertEqual(
                evidence,
                {
                    "source_path": str(self.source_path.resolve()),
                    "sha256": self.digest,
                },
            )
            self.assertEqual(set(evidence.keys()), {"source_path", "sha256"})

            # Verify source remains unchanged during copying and context execution
            self.assertEqual(self.source_path.read_bytes(), self.content)
            self.assertEqual(stat.S_IMODE(self.source_path.stat().st_mode), init_mode)

            # Record disposable directory
            disposable_dir = disposable_path.parent
            self.assertTrue(disposable_path.exists())
            self.assertTrue(disposable_dir.exists())

        # After context exit, disposable path and directory must both be absent
        self.assertIsNotNone(disposable_dir)
        self.assertFalse(disposable_path.exists())
        self.assertFalse(os.path.lexists(disposable_path))
        self.assertFalse(disposable_dir.exists())

        # Source bytes and permissions must remain unchanged after exit
        self.assertEqual(self.source_path.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE(self.source_path.stat().st_mode), init_mode)

    def test_expected_sha256_none_hashes_and_copies(self) -> None:
        """Requirement 2: expected_sha256=None hashes and copies stable bytes without mismatch."""
        with prepare_disposable_cli(
            self.source_path,
            expected_sha256=None,
            require_read_only=True,
        ) as (disposable_path, evidence):
            self.assertNotEqual(disposable_path, self.source_path)
            self.assertEqual(disposable_path.read_bytes(), self.content)
            self.assertEqual(
                evidence,
                {
                    "source_path": str(self.source_path.resolve()),
                    "sha256": self.digest,
                },
            )
            disp_dir = disposable_path.parent
            self.assertTrue(disposable_path.exists())
            self.assertTrue(disp_dir.exists())

        self.assertFalse(disposable_path.exists())
        self.assertFalse(disp_dir.exists())

    def test_wrong_expected_digest_fails_and_leaves_no_disposable_dir(self) -> None:
        """Requirement 2: Wrong expected digest fails and leaves no disposable directory."""
        created_temp_dirs: list[str] = []
        real_tempdir = tempfile.TemporaryDirectory

        def tracking_tempdir(*args: object, **kwargs: object) -> tempfile.TemporaryDirectory[str]:
            td = real_tempdir(*args, **kwargs)
            created_temp_dirs.append(td.name)
            return td

        wrong_digest = "e" * 64
        self.assertNotEqual(wrong_digest, self.digest)

        with mock.patch("tempfile.TemporaryDirectory", side_effect=tracking_tempdir):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(
                    self.source_path,
                    expected_sha256=wrong_digest,
                    require_read_only=True,
                ):
                    self.fail("Should have failed on SHA-256 mismatch before yielding")

        self.assertIn("SHA-256 mismatch", str(ctx.exception))
        self.assertTrue(created_temp_dirs, "Expected a temporary directory to have been created")
        for temp_dir_name in created_temp_dirs:
            self.assertFalse(
                os.path.exists(temp_dir_name),
                f"Disposable directory {temp_dir_name} was not cleaned up on failure",
            )

    def test_cleanup_on_active_exception_inside_context(self) -> None:
        """Requirement 1/2: Disposable files are cleaned up if an exception occurs during yield."""
        disposable_ref: pathlib.Path | None = None
        dir_ref: pathlib.Path | None = None

        with self.assertRaises(RuntimeError):
            with prepare_disposable_cli(
                self.source_path,
                expected_sha256=self.digest,
            ) as (disposable_path, _):
                disposable_ref = disposable_path
                dir_ref = disposable_path.parent
                self.assertTrue(disposable_ref.exists())
                self.assertTrue(dir_ref.exists())
                raise RuntimeError("User block failure")

        self.assertIsNotNone(disposable_ref)
        self.assertIsNotNone(dir_ref)
        self.assertFalse(disposable_ref.exists())
        self.assertFalse(dir_ref.exists())

    def test_fail_closed_relative_path(self) -> None:
        """Requirement 3: Relative path fails closed before yielding."""
        rel_path = pathlib.Path("sample_cli")
        with self.assertRaises(CliArtifactError) as ctx:
            with prepare_disposable_cli(rel_path, expected_sha256=self.digest):
                self.fail("Should not yield on non-absolute path")
        self.assertIn("Source path must be absolute", str(ctx.exception))

    def test_fail_closed_unresolved_components(self) -> None:
        """Requirement 3: Path containing '.' or '..' fails closed before yielding."""
        unresolved_path = self.tmp_path / "nested" / ".." / self.source_path.name
        with self.assertRaises(CliArtifactError) as ctx:
            with prepare_disposable_cli(unresolved_path, expected_sha256=self.digest):
                self.fail("Should not yield on unresolved path components")
        self.assertIn("unresolved components", str(ctx.exception))

    def test_fail_closed_symlink_source(self) -> None:
        """Requirement 3: Symlink source fails closed before yielding."""
        symlink_path = self.tmp_path / "symlink_cli"
        symlink_path.symlink_to(self.source_path)
        with self.assertRaises(CliArtifactError) as ctx:
            with prepare_disposable_cli(symlink_path, expected_sha256=self.digest):
                self.fail("Should not yield on symlink source")
        self.assertIn("symlink", str(ctx.exception).lower())

    def test_fail_closed_hardlink_source(self) -> None:
        """Requirement 3: Hardlinked source (nlink != 1) fails closed before yielding."""
        hardlink_path = self.tmp_path / "hardlink_cli"
        os.link(self.source_path, hardlink_path)
        try:
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256=self.digest):
                    self.fail("Should not yield on hardlinked source")
            self.assertIn("hard link count is not 1", str(ctx.exception))
        finally:
            hardlink_path.unlink(missing_ok=True)

    def test_fail_closed_writable_source_when_require_read_only(self) -> None:
        """Requirement 3: Writable source fails closed when require_read_only=True."""
        os.chmod(self.source_path, 0o755)
        with self.assertRaises(CliArtifactError) as ctx:
            with prepare_disposable_cli(
                self.source_path,
                expected_sha256=self.digest,
                require_read_only=True,
            ):
                self.fail("Should not yield on writable source")
        self.assertIn("write permission bits", str(ctx.exception))

    def test_writable_source_allowed_when_require_read_only_false(self) -> None:
        """Requirement 3: Writable source is permitted when require_read_only=False."""
        os.chmod(self.source_path, 0o700)
        with prepare_disposable_cli(
            self.source_path,
            expected_sha256=self.digest,
            require_read_only=False,
        ) as (disposable_path, _):
            self.assertEqual(disposable_path.read_bytes(), self.content)

    def test_fail_closed_wrong_owner_via_mock(self) -> None:
        """Requirement 3: Mismatched UID fails closed before yielding via mock."""
        wrong_euid = os.geteuid() + 9999
        with mock.patch("os.geteuid", return_value=wrong_euid):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256=self.digest):
                    self.fail("Should not yield on owner UID mismatch")
            self.assertIn("owner UID does not match", str(ctx.exception))

    def test_fail_closed_source_fingerprint_drift_fstat(self) -> None:
        """Requirement 3: Source descriptor drift detected by post-copy fstat fails closed."""
        real_fstat = os.fstat
        call_count = 0

        class DriftingStat:
            def __init__(self, orig: os.stat_result, **kwargs: object) -> None:
                self._orig = orig
                self._overrides = kwargs

            def __getattr__(self, name: str) -> object:
                if name in self._overrides:
                    return self._overrides[name]
                return getattr(self._orig, name)

        def mock_fstat(fd: int) -> object:
            nonlocal call_count
            call_count += 1
            st = real_fstat(fd)
            if call_count >= 2:
                return DriftingStat(st, st_mtime_ns=st.st_mtime_ns + 10000)
            return st

        with mock.patch("scripts.two_site_cli_artifact.os.fstat", side_effect=mock_fstat):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(
                    self.source_path, expected_sha256=self.digest
                ):
                    self.fail("Should not yield on fstat fingerprint drift")
            self.assertIn(
                "Source descriptor was mutated during copying", str(ctx.exception)
            )

    def test_fail_closed_source_fingerprint_drift_lstat(self) -> None:
        """Requirement 3: Source named path drift detected by post-copy lstat fails closed."""
        real_lstat = os.lstat
        call_count = 0

        class DriftingStat:
            def __init__(self, orig: os.stat_result, **kwargs: object) -> None:
                self._orig = orig
                self._overrides = kwargs

            def __getattr__(self, name: str) -> object:
                if name in self._overrides:
                    return self._overrides[name]
                return getattr(self._orig, name)

        def mock_lstat(path: object, *args: object, **kwargs: object) -> object:
            nonlocal call_count
            st = real_lstat(path, *args, **kwargs)
            try:
                if isinstance(path, bytes):
                    lexical_path = os.fsdecode(path)
                elif isinstance(path, (str, os.PathLike)):
                    lexical_path = str(path)
                else:
                    lexical_path = None
                is_source = (lexical_path == str(self.source_path))
            except Exception:
                is_source = False
            if is_source:
                call_count += 1
                if call_count == 3:
                    return DriftingStat(st, st_ino=st.st_ino + 1)
            return st

        with mock.patch("scripts.two_site_cli_artifact.os.lstat", side_effect=mock_lstat):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(
                    self.source_path, expected_sha256=self.digest
                ):
                    self.fail("Should not yield on lstat fingerprint drift")
            self.assertIn(
                "Source named path was replaced or mutated", str(ctx.exception)
            )

    def test_implementation_has_no_read_bytes_and_does_not_chmod_source(self) -> None:
        """Requirement 3: Confirm no read_bytes call and source path is never chmodded."""
        source_code = inspect.getsource(artifact_mod)
        self.assertNotIn(
            "read_bytes",
            source_code,
            "Implementation must stream chunks instead of calling read_bytes",
        )

        chmod_calls: list[pathlib.Path] = []
        real_chmod = os.chmod

        def tracking_chmod(
            path: int | str | bytes | os.PathLike[object],
            mode: int,
            *args: object,
            **kwargs: object,
        ) -> None:
            if isinstance(path, (str, bytes, os.PathLike)):
                chmod_calls.append(pathlib.Path(os.fsdecode(path)).resolve())
            real_chmod(path, mode, *args, **kwargs)

        init_mode = stat.S_IMODE(self.source_path.stat().st_mode)

        with mock.patch.object(
            pathlib.Path,
            "read_bytes",
            side_effect=AssertionError("Path.read_bytes must not be called"),
        ):
            with mock.patch(
                "scripts.two_site_cli_artifact.os.chmod", side_effect=tracking_chmod
            ):
                with prepare_disposable_cli(
                    self.source_path,
                    expected_sha256=self.digest,
                    require_read_only=True,
                ) as (disposable_path, _):
                    with open(disposable_path, "rb") as f:
                        read_back = f.read()
                    self.assertEqual(read_back, self.content)

        resolved_source = self.source_path.resolve()
        for target_path in chmod_calls:
            self.assertNotEqual(
                target_path,
                resolved_source,
                f"os.chmod was called on the source path: {target_path}",
            )

        self.assertEqual(
            stat.S_IMODE(self.source_path.stat().st_mode),
            init_mode,
            "Source mode was changed by prepare_disposable_cli",
        )

    def test_invalid_source_type_and_missing_file(self) -> None:
        """Defensive argument validation tests for source type and existence."""
        with self.assertRaises(CliArtifactError):
            with prepare_disposable_cli("not_a_pathlib_path", None):  # type: ignore[arg-type]
                pass

        with self.assertRaises(FileNotFoundError):
            with prepare_disposable_cli(self.tmp_path / "missing_file", None):
                pass

        with self.assertRaises(CliArtifactError):
            with prepare_disposable_cli(self.source_path, expected_sha256=""):
                pass


if __name__ == "__main__":
    unittest.main()
