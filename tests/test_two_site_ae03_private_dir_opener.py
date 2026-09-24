#!/usr/bin/env python3
"""Hermetic unit tests for _open_private_rescope_state_dir."""
import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

import errno
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_codex_autopilot import (
    AutopilotError,
    _open_private_rescope_state_dir,
)


def _open_fds() -> set[int]:
    try:
        return {int(name) for name in os.listdir("/proc/self/fd")}
    except OSError:
        return set()


class TestPrivateDirOpener(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory(prefix="test_ae03_state_")
        temp_path = Path(self.temp_dir_obj.name).resolve(strict=True)
        self.state_root = temp_path / "state"
        self.state_root.mkdir(mode=0o700)

    def tearDown(self) -> None:
        try:
            self.temp_dir_obj.cleanup()
        except OSError:
            pass

    def test_1_success_fd_identity_and_caller_close(self) -> None:
        before_fds = _open_fds()
        fd = _open_private_rescope_state_dir(self.state_root)
        self.assertIsInstance(fd, int)
        self.assertGreaterEqual(fd, 0)
        if before_fds:
            self.assertIn(fd, _open_fds())
        fd_stat = os.fstat(fd)
        self.assertTrue(stat.S_ISDIR(fd_stat.st_mode))
        self.assertEqual(fd_stat.st_mode & 0o777, 0o700)
        self.assertEqual(fd_stat.st_uid, os.geteuid())
        expected_stat = self.state_root.stat(follow_symlinks=False)
        self.assertEqual(
            (fd_stat.st_dev, fd_stat.st_ino),
            (expected_stat.st_dev, expected_stat.st_ino),
        )
        os.close(fd)
        with self.assertRaises(OSError) as ctx:
            os.fstat(fd)
        self.assertEqual(ctx.exception.errno, errno.EBADF)
        if before_fds:
            self.assertEqual(before_fds, _open_fds())

    def test_2_relative_dotdot_symlink_rejection(self) -> None:
        before_fds = _open_fds()
        with self.assertRaises(AutopilotError) as ctx:
            _open_private_rescope_state_dir(str(self.state_root))  # type: ignore[arg-type]
        self.assertIn("state_root must be a Path", str(ctx.exception))

        with self.assertRaises(AutopilotError) as ctx:
            _open_private_rescope_state_dir(Path("relative/path/dir"))
        self.assertIn("state_root must be an absolute Path", str(ctx.exception))

        dotdot_path = Path(f"{self.state_root}/../{self.state_root.name}")
        with self.assertRaises(AutopilotError) as ctx:
            _open_private_rescope_state_dir(dotdot_path)
        self.assertIn("without relative components", str(ctx.exception))

        symlink_leaf = self.state_root.parent / f"{self.state_root.name}_link"
        try:
            symlink_leaf.symlink_to(self.state_root)
            with self.assertRaises(AutopilotError) as ctx:
                _open_private_rescope_state_dir(symlink_leaf)
            self.assertIn("cannot contain symlinks or path aliases", str(ctx.exception))
        finally:
            symlink_leaf.unlink(missing_ok=True)

        parent_link = self.state_root.parent / f"{self.state_root.name}_parent_link"
        child_dir = self.state_root / "nested"
        child_dir.mkdir(mode=0o700)
        try:
            parent_link.symlink_to(self.state_root)
            aliased_child = parent_link / "nested"
            with self.assertRaises(AutopilotError) as ctx:
                _open_private_rescope_state_dir(aliased_child)
            self.assertIn("cannot contain symlinks or path aliases", str(ctx.exception))
        finally:
            parent_link.unlink(missing_ok=True)

        if before_fds:
            self.assertEqual(before_fds, _open_fds())

    def test_3_mode_owner_non_directory(self) -> None:
        before_fds = _open_fds()
        os.chmod(self.state_root, 0o755)
        with self.assertRaises(AutopilotError) as ctx:
            _open_private_rescope_state_dir(self.state_root)
        self.assertIn("state_root directory mode must be 0700", str(ctx.exception))

        os.chmod(self.state_root, 0o600)
        with self.assertRaises(AutopilotError) as ctx:
            _open_private_rescope_state_dir(self.state_root)
        self.assertIn("state_root directory mode must be 0700", str(ctx.exception))

        os.chmod(self.state_root, 0o700)
        regular_file = self.state_root / "regular.txt"
        regular_file.write_text("not a directory")
        os.chmod(regular_file, 0o700)
        with self.assertRaises(AutopilotError) as ctx:
            _open_private_rescope_state_dir(regular_file)
        self.assertTrue(
            "state_root is not a directory" in str(ctx.exception)
            or "cannot open directory component" in str(ctx.exception)
        )

        with patch("scripts.two_site_codex_autopilot.os.geteuid", return_value=os.geteuid() + 1000):
            with self.assertRaises(AutopilotError) as ctx:
                _open_private_rescope_state_dir(self.state_root)
            self.assertIn("state_root directory owner must be process euid", str(ctx.exception))

        if before_fds:
            self.assertEqual(before_fds, _open_fds())

    def test_4_missing_flags_dir_fd_and_injected_failures(self) -> None:
        before_fds = _open_fds()
        with patch.object(os, "O_CLOEXEC", None):
            with self.assertRaises(AutopilotError) as ctx:
                _open_private_rescope_state_dir(self.state_root)
            self.assertIn("missing required", str(ctx.exception))

        with patch.object(os, "supports_dir_fd", set()):
            with self.assertRaises(AutopilotError) as ctx:
                _open_private_rescope_state_dir(self.state_root)
            self.assertIn("missing dir_fd support", str(ctx.exception))

        real_open = os.open

        def failing_open(path, flags, *args, **kwargs):
            if kwargs.get("dir_fd") is not None and path == self.state_root.name:
                raise OSError(errno.EACCES, "simulated permission denied")
            return real_open(path, flags, *args, **kwargs)

        with patch("scripts.two_site_codex_autopilot.os.open", side_effect=failing_open):
            with self.assertRaises(AutopilotError) as ctx:
                _open_private_rescope_state_dir(self.state_root)
            self.assertIn("cannot open directory component", str(ctx.exception))

        def failing_fstat(fd):
            raise OSError(errno.EIO, "simulated fstat error")

        with patch("scripts.two_site_codex_autopilot.os.fstat", side_effect=failing_fstat):
            with self.assertRaises(AutopilotError) as ctx:
                _open_private_rescope_state_dir(self.state_root)
            self.assertIn("cannot fstat state_root directory", str(ctx.exception))

        real_close = os.close
        close_fail_target: list[int] = []

        def failing_close(fd):
            if close_fail_target and fd == close_fail_target[0]:
                real_close(fd)
                raise OSError(errno.EIO, "simulated close error")
            return real_close(fd)

        def spying_open(path, flags, *args, **kwargs):
            result_fd = real_open(path, flags, *args, **kwargs)
            if not close_fail_target:
                close_fail_target.append(result_fd)
            return result_fd

        with patch("scripts.two_site_codex_autopilot.os.open", side_effect=spying_open):
            with patch("scripts.two_site_codex_autopilot.os.close", side_effect=failing_close):
                with self.assertRaises(AutopilotError) as ctx:
                    _open_private_rescope_state_dir(self.state_root)
                self.assertTrue(
                    "failed to close predecessor" in str(ctx.exception)
                    or "error closing descriptor" in str(ctx.exception)
                )

        if before_fds:
            self.assertEqual(before_fds, _open_fds())

    def test_5_inode_drift_and_keyboard_interrupt(self) -> None:
        before_fds = _open_fds()
        real_open = os.open

        def swap_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            if kwargs.get("dir_fd") is not None and path == self.state_root.name:
                backup = self.state_root.parent / f"{self.state_root.name}_drifted"
                self.state_root.rename(backup)
                self.state_root.mkdir(mode=0o700)
            return fd

        with patch("scripts.two_site_codex_autopilot.os.open", side_effect=swap_open):
            with self.assertRaises(AutopilotError) as ctx:
                _open_private_rescope_state_dir(self.state_root)
            self.assertIn("pinned directory does not match state_root inode", str(ctx.exception))

        def interrupt_fstat(fd):
            raise KeyboardInterrupt("simulated cancel")

        with patch("scripts.two_site_codex_autopilot.os.fstat", side_effect=interrupt_fstat):
            with self.assertRaises(KeyboardInterrupt):
                _open_private_rescope_state_dir(self.state_root)

        if before_fds:
            self.assertEqual(before_fds, _open_fds())


if __name__ == "__main__":
    unittest.main()
