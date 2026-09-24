"""Tests for named collision classification in two-site autopilot rescope receipts."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_codex_autopilot import (
    AutopilotError,
    _prove_rescope_link_collision,
    _RESCOPE_RECEIPT_FILENAME,
    _RESCOPE_RECEIPT_FILENAMES,
)


class TestNamedCollisionClassification(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temp_dir.name).resolve(strict=True)
        os.chmod(self.state_root, 0o700)
        self.pinned_dir_fd = os.open(
            self.state_root,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
        )
        self.temp_name = f".rescope_receipt_tmp_{os.getpid()}_test"
        self._write_file(self.temp_name, b"temporary payload")

    def tearDown(self) -> None:
        try:
            os.close(self.pinned_dir_fd)
        except OSError:
            pass
        self.temp_dir.cleanup()

    def _write_file(self, name: str, content: bytes) -> None:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=self.pinned_dir_fd,
        )
        try:
            os.write(fd, content)
        finally:
            os.close(fd)

    def test_unrelated_eexist_target2_and_target3_return_none_and_preserve_files(self) -> None:
        """Prove real unrelated EEXIST returns None for target 2 and 3 and preserves files."""
        for target_name in ("rescope_receipt_2.json", "rescope_receipt_3.json"):
            target_payload = f"existing payload for {target_name}".encode("utf-8")
            self._write_file(target_name, target_payload)

            target_st_before = os.stat(target_name, dir_fd=self.pinned_dir_fd, follow_symlinks=False)
            temp_st_before = os.stat(self.temp_name, dir_fd=self.pinned_dir_fd, follow_symlinks=False)
            self.assertNotEqual(
                (target_st_before.st_dev, target_st_before.st_ino),
                (temp_st_before.st_dev, temp_st_before.st_ino),
            )

            link_error = OSError(errno.EEXIST, "File exists")
            res = _prove_rescope_link_collision(
                self.pinned_dir_fd,
                self.temp_name,
                link_error,
                target_filename=target_name,
            )
            self.assertIsNone(res)

            target_st_after = os.stat(target_name, dir_fd=self.pinned_dir_fd, follow_symlinks=False)
            temp_st_after = os.stat(self.temp_name, dir_fd=self.pinned_dir_fd, follow_symlinks=False)
            self.assertEqual(target_st_after.st_ino, target_st_before.st_ino)
            self.assertEqual(target_st_after.st_nlink, 1)
            self.assertEqual(temp_st_after.st_ino, temp_st_before.st_ino)
            self.assertEqual(temp_st_after.st_nlink, 1)

        default_target = _RESCOPE_RECEIPT_FILENAME
        self._write_file(default_target, b"default payload")
        res_default = _prove_rescope_link_collision(
            self.pinned_dir_fd,
            self.temp_name,
            OSError(errno.EEXIST, "File exists"),
        )
        self.assertIsNone(res_default)

    def test_real_link_then_injected_eio_leaves_shared_inode_and_raises_ambiguous(self) -> None:
        """Prove real link side effect with injected EIO produces ambiguous AutopilotError, never success."""
        target_name = "rescope_receipt_2.json"
        os.link(
            self.temp_name,
            target_name,
            src_dir_fd=self.pinned_dir_fd,
            dst_dir_fd=self.pinned_dir_fd,
            follow_symlinks=False,
        )
        target_st = os.stat(target_name, dir_fd=self.pinned_dir_fd, follow_symlinks=False)
        temp_st = os.stat(self.temp_name, dir_fd=self.pinned_dir_fd, follow_symlinks=False)
        self.assertEqual((target_st.st_dev, target_st.st_ino), (temp_st.st_dev, temp_st.st_ino))
        self.assertEqual(target_st.st_nlink, 2)
        self.assertEqual(temp_st.st_nlink, 2)

        link_error = OSError(errno.EIO, "Input/output error")
        with self.assertRaises(AutopilotError) as ctx:
            _prove_rescope_link_collision(
                self.pinned_dir_fd,
                self.temp_name,
                link_error,
                target_filename=target_name,
            )
        self.assertTrue(str(ctx.exception).startswith("ambiguous:"))
        self.assertIn("errno is not EEXIST", str(ctx.exception))

        with self.assertRaises(AutopilotError) as ctx_eexist:
            _prove_rescope_link_collision(
                self.pinned_dir_fd,
                self.temp_name,
                OSError(errno.EEXIST, "File exists"),
                target_filename=target_name,
            )
        self.assertTrue(str(ctx_eexist.exception).startswith("ambiguous:"))
        self.assertIn("temporary rescope file link count is 2, expected 1", str(ctx_eexist.exception))

        target_st_after = os.stat(target_name, dir_fd=self.pinned_dir_fd, follow_symlinks=False)
        temp_st_after = os.stat(self.temp_name, dir_fd=self.pinned_dir_fd, follow_symlinks=False)
        self.assertEqual(target_st_after.st_ino, temp_st_after.st_ino)
        self.assertEqual(target_st_after.st_nlink, 2)
        self.assertEqual(temp_st_after.st_nlink, 2)

    def test_invalid_target_rejected_before_target_stat(self) -> None:
        """Prove invalid target Path/bool/traversal/unknown rejected with ambiguous AutopilotError before stat."""
        invalid_targets = [
            Path("rescope_receipt_2.json"),
            True,
            False,
            "../rescope_receipt.json",
            "unknown_rescope_receipt.json",
            "rescope_receipt_4.json",
            "",
            123,
        ]
        link_error = OSError(errno.EEXIST, "File exists")

        real_stat = os.stat

        for invalid_target in invalid_targets:
            target_stat_called = False

            def fake_stat(path, *args, **kwargs):
                nonlocal target_stat_called
                if path == invalid_target or (isinstance(path, Path) and path == invalid_target):
                    target_stat_called = True
                return real_stat(path, *args, **kwargs)

            with patch("os.stat", side_effect=fake_stat):
                with self.assertRaises(AutopilotError) as ctx:
                    _prove_rescope_link_collision(
                        self.pinned_dir_fd,
                        self.temp_name,
                        link_error,
                        target_filename=invalid_target,  # type: ignore[arg-type]
                    )
                self.assertTrue(str(ctx.exception).startswith("ambiguous:"))
                self.assertFalse(
                    target_stat_called,
                    f"target stat was called for invalid target {invalid_target!r}",
                )

    def test_eexist_same_inode_is_ambiguous(self) -> None:
        """Prove EEXIST error when target shares inode with temp raises ambiguous AutopilotError."""
        real_stat = os.stat
        temp_st = real_stat(self.temp_name, dir_fd=self.pinned_dir_fd, follow_symlinks=False)
        self.assertEqual(temp_st.st_nlink, 1)

        for target_name in ("rescope_receipt_2.json", "rescope_receipt_3.json"):
            def fake_stat(path, *args, **kwargs):
                if path == target_name:
                    return temp_st
                return real_stat(path, *args, **kwargs)

            link_error = OSError(errno.EEXIST, "File exists")
            with patch("os.stat", side_effect=fake_stat):
                with self.assertRaises(AutopilotError) as ctx:
                    _prove_rescope_link_collision(
                        self.pinned_dir_fd,
                        self.temp_name,
                        link_error,
                        target_filename=target_name,
                    )
                self.assertTrue(str(ctx.exception).startswith("ambiguous:"))
                self.assertIn("shares inode with temporary file", str(ctx.exception))

        def fake_stat_default(path, *args, **kwargs):
            if path == _RESCOPE_RECEIPT_FILENAME:
                return temp_st
            return real_stat(path, *args, **kwargs)

        with patch("os.stat", side_effect=fake_stat_default):
            with self.assertRaises(AutopilotError) as ctx:
                _prove_rescope_link_collision(
                    self.pinned_dir_fd,
                    self.temp_name,
                    OSError(errno.EEXIST, "File exists"),
                )
            self.assertTrue(str(ctx.exception).startswith("ambiguous:"))
            self.assertIn("shares inode with temporary file", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
