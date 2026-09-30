"""Tests for controlled STOP helper."""

from __future__ import annotations

import errno
import fcntl
import inspect
import io
import math
import multiprocessing as mp
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_controlled_stop import (
    ControlledStopError,
    DEFAULT_LOCK_WAIT_SECONDS,
    GENERIC_CLI_ERROR_MESSAGE,
    STOP_MARKER,
    main,
    publish_controlled_stop,
)


def _lock_holder_child(lock_path_str: str, ready_w: int, exit_r: int) -> None:
    """Hold flock on runner lock until exit pipe receives a byte."""
    try:
        fd = os.open(lock_path_str, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(ready_w, b"READY")
        os.read(exit_r, 1)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    finally:
        try:
            os.close(ready_w)
        except OSError:
            pass
        try:
            os.close(exit_r)
        except OSError:
            pass


class TestTwoSiteControlledStop(unittest.TestCase):
    """Test suite for controlled STOP publication and durability."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        os.chmod(self.root, 0o700)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_fresh_root_success_and_repeated_success(self) -> None:
        """Normal fresh-root publication and repeated idempotent success."""
        stop_path = publish_controlled_stop(self.root)
        expected_stop = self.root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
        self.assertEqual(stop_path, expected_stop)
        self.assertTrue(stop_path.is_file())
        self.assertEqual(stop_path.read_bytes(), STOP_MARKER)

        st = os.lstat(stop_path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertEqual(st.st_uid, os.geteuid())
        self.assertEqual(st.st_nlink, 1)

        lock_path = self.root / ".local" / "two-site-refactor" / "proposals" / ".runner.lock"
        self.assertTrue(lock_path.is_file())
        lock_st = os.lstat(lock_path)
        self.assertEqual(stat.S_IMODE(lock_st.st_mode), 0o600)
        self.assertEqual(lock_st.st_nlink, 1)

        for comp in [
            self.root / ".local",
            self.root / ".local" / "two-site-refactor",
            self.root / ".local" / "two-site-refactor" / "proposals",
            self.root / ".local" / "two-site-refactor" / "autopilot",
        ]:
            self.assertTrue(comp.is_dir())
            comp_st = os.lstat(comp)
            self.assertEqual(stat.S_IMODE(comp_st.st_mode), 0o700)
            self.assertEqual(comp_st.st_uid, os.geteuid())

        # Repeated idempotent call
        repeated_path = publish_controlled_stop(self.root)
        self.assertEqual(repeated_path, expected_stop)
        self.assertEqual(repeated_path.read_bytes(), STOP_MARKER)

    def test_default_lock_wait_constant_and_signature(self) -> None:
        """Fixed default wait is >= 900 seconds and matches 1200.0s signature."""
        self.assertGreaterEqual(DEFAULT_LOCK_WAIT_SECONDS, 900.0)
        self.assertEqual(DEFAULT_LOCK_WAIT_SECONDS, 1200.0)
        sig = inspect.signature(publish_controlled_stop)
        self.assertIn("lock_wait_seconds", sig.parameters)
        self.assertEqual(sig.parameters["lock_wait_seconds"].default, 1200.0)

    def test_invalid_timeout_rejection(self) -> None:
        """Invalid timeout values fail immediately with ControlledStopError."""
        for invalid_val in [
            0,
            0.0,
            -1,
            -0.5,
            True,
            False,
            float("nan"),
            float("inf"),
            float("-inf"),
            "1200",
            None,
        ]:
            with self.subTest(val=invalid_val):
                with self.assertRaises(ControlledStopError):
                    publish_controlled_stop(self.root, lock_wait_seconds=invalid_val)  # type: ignore

    def test_symlink_and_writable_parent_rejections(self) -> None:
        """Root or storage components with symlinks or group/world write are rejected."""
        # 1. Symlink root
        symlink_root = Path(self.temp_dir.name) / "symlink_root"
        symlink_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ControlledStopError):
            publish_controlled_stop(symlink_root)

        # 2. Group writable root
        os.chmod(self.root, 0o770)
        with self.assertRaises(ControlledStopError):
            publish_controlled_stop(self.root)

        # 3. World writable root
        os.chmod(self.root, 0o707)
        with self.assertRaises(ControlledStopError):
            publish_controlled_stop(self.root)
        os.chmod(self.root, 0o700)

        # 4. Storage component is symlink
        local_dir = self.root / ".local"
        dummy_dir = Path(self.temp_dir.name) / "dummy"
        dummy_dir.mkdir(mode=0o700)
        local_dir.symlink_to(dummy_dir, target_is_directory=True)
        with self.assertRaises(ControlledStopError):
            publish_controlled_stop(self.root)
        local_dir.unlink()

        # 5. Storage component has group write (explicit os.chmod to avoid umask masking)
        local_dir.mkdir(mode=0o700)
        os.chmod(local_dir, 0o770)
        with self.assertRaises(ControlledStopError):
            publish_controlled_stop(self.root)

    def test_hardlinked_lock_rejection(self) -> None:
        """Hardlinked runner lock file is rejected."""
        props = self.root / ".local" / "two-site-refactor" / "proposals"
        props.mkdir(parents=True, mode=0o700)
        lock_path = props / ".runner.lock"
        lock_path.write_bytes(b"")
        os.chmod(lock_path, 0o600)

        other_link = self.root / "lock.hardlink"
        os.link(lock_path, other_link)
        self.assertEqual(os.lstat(lock_path).st_nlink, 2)

        with self.assertRaises(ControlledStopError):
            publish_controlled_stop(self.root)

    def test_immediate_non_contention_error(self) -> None:
        """Unsafe lock inode fails closed immediately without waiting."""
        props = self.root / ".local" / "two-site-refactor" / "proposals"
        props.mkdir(parents=True, mode=0o700)
        lock_path = props / ".runner.lock"
        lock_path.mkdir(mode=0o700)

        t0 = time.monotonic()
        with self.assertRaises(ControlledStopError):
            publish_controlled_stop(self.root, lock_wait_seconds=1200.0)
        t_elapsed = time.monotonic() - t0
        self.assertLess(t_elapsed, 0.5)

    def test_cross_process_lock_contention_and_timeout(self) -> None:
        """Deterministic cross-process contention with actual BlockingIOError."""
        props = self.root / ".local" / "two-site-refactor" / "proposals"
        props.mkdir(parents=True, mode=0o700)
        lock_path = props / ".runner.lock"
        lock_path.write_bytes(b"")
        os.chmod(lock_path, 0o600)

        r_ready, w_ready = os.pipe()
        r_exit, w_exit = os.pipe()

        ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context()
        proc = ctx.Process(
            target=_lock_holder_child,
            args=(str(lock_path), w_ready, r_exit),
        )
        proc.start()
        os.close(w_ready)
        os.close(r_exit)

        try:
            ready_msg = os.read(r_ready, 5)
            self.assertEqual(ready_msg, b"READY")

            t0 = time.monotonic()
            with self.assertRaises(ControlledStopError) as ctx_err:
                publish_controlled_stop(self.root, lock_wait_seconds=0.15)
            elapsed = time.monotonic() - t0
            self.assertGreaterEqual(elapsed, 0.14)
            self.assertIn("timed out waiting for proposal runner lock", str(ctx_err.exception))

            os.write(w_exit, b"X")
            proc.join(timeout=5.0)

            published = publish_controlled_stop(self.root, lock_wait_seconds=5.0)
            self.assertTrue(published.is_file())
            self.assertEqual(published.read_bytes(), STOP_MARKER)
        finally:
            try:
                os.close(r_ready)
            except OSError:
                pass
            try:
                os.close(w_exit)
            except OSError:
                pass
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=2.0)

    def test_partial_positive_write_followed_by_error_preserves_stop(self) -> None:
        """Partial write fails but leaves newly visible STOP without unlinking."""
        orig_write = os.write

        def mock_write(fd: int, data: bytes) -> int:
            if data == STOP_MARKER:
                return orig_write(fd, b"ST")
            return orig_write(fd, data)

        with mock.patch("os.write", side_effect=mock_write):
            with self.assertRaises(ControlledStopError) as ctx_err:
                publish_controlled_stop(self.root)
            self.assertIn("partial write", str(ctx_err.exception))

        stop_path = self.root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
        self.assertTrue(stop_path.is_file(), "Visible STOP must NEVER be unlinked on partial write")
        self.assertEqual(stop_path.read_bytes(), b"ST")

    def test_file_fsync_failure_preserves_visible_stop(self) -> None:
        """Failure during STOP file fsync preserves visible STOP."""
        orig_fsync = os.fsync
        stop_path = self.root / ".local" / "two-site-refactor" / "autopilot" / "STOP"

        def mock_fsync(fd: int) -> None:
            try:
                st = os.fstat(fd)
                if stop_path.exists() and os.lstat(stop_path).st_ino == st.st_ino:
                    raise OSError(errno.EIO, "injected file fsync failure")
            except OSError as exc:
                if exc.errno == errno.EIO:
                    raise
            orig_fsync(fd)

        with mock.patch("os.fsync", side_effect=mock_fsync):
            with self.assertRaises(ControlledStopError):
                publish_controlled_stop(self.root)

        self.assertTrue(stop_path.is_file(), "Visible STOP must NEVER be unlinked on file fsync failure")
        self.assertEqual(stop_path.read_bytes(), STOP_MARKER)

    def test_dir_fsync_failure_preserves_visible_stop(self) -> None:
        """Failure during parent directory fsync preserves visible STOP."""
        orig_fsync = os.fsync
        stop_path = self.root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
        autopilot_dir = self.root / ".local" / "two-site-refactor" / "autopilot"

        def mock_fsync(fd: int) -> None:
            try:
                st = os.fstat(fd)
                if (
                    stop_path.exists()
                    and autopilot_dir.exists()
                    and os.lstat(autopilot_dir).st_ino == st.st_ino
                ):
                    raise OSError(errno.EIO, "injected dir fsync failure")
            except OSError as exc:
                if exc.errno == errno.EIO:
                    raise
            orig_fsync(fd)

        with mock.patch("os.fsync", side_effect=mock_fsync):
            with self.assertRaises(ControlledStopError):
                publish_controlled_stop(self.root)

        self.assertTrue(stop_path.is_file(), "Visible STOP must NEVER be unlinked on dir fsync failure")
        self.assertEqual(stop_path.read_bytes(), STOP_MARKER)

    def test_injected_parent_fsync_failure_before_stop_success(self) -> None:
        """Parent directory fsync failure during creation fails closed before STOP creation."""
        with mock.patch("os.fsync", side_effect=OSError(errno.EIO, "disk error")):
            with self.assertRaises(ControlledStopError):
                publish_controlled_stop(self.root)

        stop_path = self.root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
        self.assertFalse(stop_path.exists())

    def test_existing_stop_mode_and_symlink_failures(self) -> None:
        """Existing STOP with non-0600 mode or symlink fails closed."""
        autopilot_dir = self.root / ".local" / "two-site-refactor" / "autopilot"
        autopilot_dir.mkdir(parents=True, mode=0o700)
        stop_path = autopilot_dir / "STOP"

        # 1. Non-0600 mode (e.g. 0644)
        stop_path.write_bytes(STOP_MARKER)
        os.chmod(stop_path, 0o644)
        with self.assertRaises(ControlledStopError) as ctx_err:
            publish_controlled_stop(self.root)
        self.assertIn("mode 0600", str(ctx_err.exception))

        # 2. Existing STOP is symlink
        stop_path.unlink()
        target = self.root / "target"
        target.write_bytes(STOP_MARKER)
        os.chmod(target, 0o600)
        stop_path.symlink_to(target)
        with self.assertRaises(ControlledStopError) as ctx_err:
            publish_controlled_stop(self.root)
        self.assertIn("symlink", str(ctx_err.exception).lower())

    def test_existing_stop_fsync_failure(self) -> None:
        """Existing STOP fsync failure fails closed without unlinking STOP."""
        published = publish_controlled_stop(self.root)
        self.assertTrue(published.is_file())

        with mock.patch("os.fsync", side_effect=OSError(errno.EIO, "fsync failure")):
            with self.assertRaises(ControlledStopError):
                publish_controlled_stop(self.root)

        self.assertTrue(published.is_file(), "Existing STOP must remain intact after fsync failure")
        self.assertEqual(published.read_bytes(), STOP_MARKER)

    def test_cli_main_success(self) -> None:
        """CLI main publishes STOP marker and returns 0."""
        ret = main(["--root", str(self.root)])
        self.assertEqual(ret, 0)
        stop_path = self.root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
        self.assertTrue(stop_path.is_file())
        self.assertEqual(stop_path.read_bytes(), STOP_MARKER)
        self.assertEqual(stat.S_IMODE(os.lstat(stop_path).st_mode), 0o600)

    def test_cli_main_failure_fixed_generic_error_redaction(self) -> None:
        """All CLI main failure cases emit only fixed generic error text with no raw details."""
        nonexistent_root = self.root / "nonexistent_sub_dir_12345"
        failure_cases = [
            [],
            ["--root"],
            ["--unknown-flag"],
            ["--root", str(nonexistent_root)],
            ["--root", "relative/path/not/absolute"],
            ["--root", str(self.root), "--lock-wait-seconds", "-10"],
            ["--root", str(self.root), "--lock-wait-seconds", "not_a_number"],
            ["--root", str(self.root), "extra_positional_arg"],
        ]
        for argv in failure_cases:
            with self.subTest(argv=argv):
                stderr_buf = io.StringIO()
                orig_stderr = sys.stderr
                try:
                    sys.stderr = stderr_buf
                    ret = main(argv)
                finally:
                    sys.stderr = orig_stderr
                self.assertEqual(ret, 1)
                stderr_output = stderr_buf.getvalue()
                self.assertEqual(stderr_output, GENERIC_CLI_ERROR_MESSAGE)
                self.assertNotIn(str(nonexistent_root), stderr_output)
                self.assertNotIn("ControlledStopError", stderr_output)
                self.assertNotIn("Traceback", stderr_output)

    def test_cli_subprocess_execution(self) -> None:
        """Direct subprocess invocation matches expected success and error redaction."""
        repo_root = Path(__file__).resolve().parent.parent
        script_file = repo_root / "scripts" / "two_site_controlled_stop.py"

        res_ok = subprocess.run(
            ["/usr/bin/python3", str(script_file), "--root", str(self.root)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_ok.returncode, 0)
        self.assertEqual(res_ok.stderr, "")
        stop_path = self.root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
        self.assertTrue(stop_path.is_file())

        bogus_dir = self.root / "sub_nonexistent_secret_dir"
        res_fail = subprocess.run(
            ["/usr/bin/python3", str(script_file), "--root", str(bogus_dir)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_fail.returncode, 1)
        self.assertEqual(res_fail.stderr, GENERIC_CLI_ERROR_MESSAGE)
        self.assertNotIn(str(bogus_dir), res_fail.stderr)


if __name__ == "__main__":
    unittest.main()
