import errno
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import (
    _RESCOPE_RECEIPT_FILENAME,
    AutopilotError,
    _prove_rescope_link_collision,
)


class TestTwoSiteAe03InitialPublish(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        os.chmod(self.temp_dir.name, 0o700)
        self.dir_fd = os.open(self.temp_dir.name, os.O_RDONLY | os.O_DIRECTORY)

    def tearDown(self) -> None:
        try:
            os.close(self.dir_fd)
        except OSError:
            pass
        self.temp_dir.cleanup()

    def test_proven_unrelated_eexist_collision_returns_none_and_preserves_files(self) -> None:
        temp_name = ".rescope_receipt_tmp_1234_abcdef0123456789"
        temp_path = os.path.join(self.temp_dir.name, temp_name)
        target_path = os.path.join(self.temp_dir.name, _RESCOPE_RECEIPT_FILENAME)

        with open(temp_path, "wb") as f:
            f.write(b"temp-content-receipt")
        os.chmod(temp_path, 0o600)

        with open(target_path, "wb") as f:
            f.write(b"existing-target-content")
        os.chmod(target_path, 0o600)

        before_temp_stat = os.stat(temp_path)
        before_target_stat = os.stat(target_path)
        self.assertNotEqual(
            (before_temp_stat.st_dev, before_temp_stat.st_ino),
            (before_target_stat.st_dev, before_target_stat.st_ino),
        )

        link_error = OSError(errno.EEXIST, "File exists")
        result = _prove_rescope_link_collision(self.dir_fd, temp_name, link_error)
        self.assertIsNone(result)

        after_temp_stat = os.stat(temp_path)
        after_target_stat = os.stat(target_path)
        self.assertEqual(before_temp_stat.st_ino, after_temp_stat.st_ino)
        self.assertEqual(before_target_stat.st_ino, after_target_stat.st_ino)
        self.assertEqual(after_temp_stat.st_nlink, 1)
        self.assertEqual(after_target_stat.st_nlink, 1)
        with open(temp_path, "rb") as f:
            self.assertEqual(f.read(), b"temp-content-receipt")
        with open(target_path, "rb") as f:
            self.assertEqual(f.read(), b"existing-target-content")

    def test_real_link_then_eio_leaves_target_temp_nlink2_and_raises_ambiguous(self) -> None:
        temp_name = ".rescope_receipt_tmp_2345_beefcafe01234567"
        temp_path = os.path.join(self.temp_dir.name, temp_name)
        target_path = os.path.join(self.temp_dir.name, _RESCOPE_RECEIPT_FILENAME)

        with open(temp_path, "wb") as f:
            f.write(b"payload-data")
        os.chmod(temp_path, 0o600)

        os.link(temp_path, target_path)

        link_error = OSError(errno.EIO, "Input/output error")
        with self.assertRaises(AutopilotError) as ctx:
            _prove_rescope_link_collision(self.dir_fd, temp_name, link_error)

        self.assertIn("ambiguous", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, link_error)

        temp_st = os.stat(temp_path)
        target_st = os.stat(target_path)
        self.assertEqual(temp_st.st_nlink, 2)
        self.assertEqual(target_st.st_nlink, 2)
        self.assertEqual((temp_st.st_dev, temp_st.st_ino), (target_st.st_dev, target_st.st_ino))

    def test_eexist_same_inode_raises_ambiguous(self) -> None:
        temp_name = ".rescope_receipt_tmp_3456_cafebabe01234567"
        temp_path = os.path.join(self.temp_dir.name, temp_name)
        target_path = os.path.join(self.temp_dir.name, _RESCOPE_RECEIPT_FILENAME)

        with open(temp_path, "wb") as f:
            f.write(b"payload-same-inode")
        os.chmod(temp_path, 0o600)

        os.link(temp_path, target_path)

        link_error = OSError(errno.EEXIST, "File exists")
        with self.assertRaises(AutopilotError) as ctx:
            _prove_rescope_link_collision(self.dir_fd, temp_name, link_error)

        self.assertIn("ambiguous", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, link_error)
        self.assertTrue(os.path.exists(temp_path))
        self.assertTrue(os.path.exists(target_path))

        real_stat = os.stat(temp_path)
        synthetic_temp_stat = mock.MagicMock(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=os.geteuid(),
            st_nlink=1,
            st_dev=real_stat.st_dev,
            st_ino=real_stat.st_ino,
        )
        orig_stat = os.stat

        def stat_same_inode(path, *args, **kwargs):
            if path == temp_name:
                return synthetic_temp_stat
            return orig_stat(path, *args, **kwargs)

        with mock.patch("scripts.two_site_codex_autopilot.os.stat", side_effect=stat_same_inode):
            with self.assertRaises(AutopilotError) as ctx:
                _prove_rescope_link_collision(self.dir_fd, temp_name, link_error)
            self.assertIn("ambiguous", str(ctx.exception))
            self.assertIn("shares inode", str(ctx.exception))
            self.assertIs(ctx.exception.__cause__, link_error)

    def test_injected_temp_and_target_stat_eacces_raises_ambiguous_chained(self) -> None:
        temp_name = ".rescope_receipt_tmp_4567_deadbeef01234567"
        temp_path = os.path.join(self.temp_dir.name, temp_name)
        target_path = os.path.join(self.temp_dir.name, _RESCOPE_RECEIPT_FILENAME)

        with open(temp_path, "wb") as f:
            f.write(b"temp-data")
        os.chmod(temp_path, 0o600)
        with open(target_path, "wb") as f:
            f.write(b"target-data")
        os.chmod(target_path, 0o600)

        link_error = OSError(errno.EEXIST, "File exists")
        orig_stat = os.stat

        def fail_temp(path, *args, **kwargs):
            if path == temp_name:
                raise PermissionError(errno.EACCES, "Permission denied")
            return orig_stat(path, *args, **kwargs)

        with mock.patch("scripts.two_site_codex_autopilot.os.stat", side_effect=fail_temp):
            with self.assertRaises(AutopilotError) as ctx:
                _prove_rescope_link_collision(self.dir_fd, temp_name, link_error)
            self.assertIn("ambiguous", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, PermissionError)
            self.assertEqual(ctx.exception.__cause__.errno, errno.EACCES)

        def fail_target(path, *args, **kwargs):
            if path == _RESCOPE_RECEIPT_FILENAME:
                raise PermissionError(errno.EACCES, "Permission denied")
            return orig_stat(path, *args, **kwargs)

        with mock.patch("scripts.two_site_codex_autopilot.os.stat", side_effect=fail_target):
            with self.assertRaises(AutopilotError) as ctx:
                _prove_rescope_link_collision(self.dir_fd, temp_name, link_error)
            self.assertIn("ambiguous", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, PermissionError)
            self.assertEqual(ctx.exception.__cause__.errno, errno.EACCES)

        self.assertTrue(os.path.exists(temp_path))
        self.assertTrue(os.path.exists(target_path))

    def test_non_eexist_with_target_absent_raises_ambiguous(self) -> None:
        temp_name = ".rescope_receipt_tmp_5678_feedface01234567"
        temp_path = os.path.join(self.temp_dir.name, temp_name)
        target_path = os.path.join(self.temp_dir.name, _RESCOPE_RECEIPT_FILENAME)

        with open(temp_path, "wb") as f:
            f.write(b"temp-data")
        os.chmod(temp_path, 0o600)
        self.assertFalse(os.path.exists(target_path))

        link_error = OSError(errno.ENOENT, "No such file or directory")
        with self.assertRaises(AutopilotError) as ctx:
            _prove_rescope_link_collision(self.dir_fd, temp_name, link_error)

        self.assertIn("ambiguous", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, link_error)
        self.assertTrue(os.path.exists(temp_path))
        self.assertFalse(os.path.exists(target_path))

    def test_temp_nlink2_or_symlink_or_malformed_raises_ambiguous(self) -> None:
        link_error = OSError(errno.EEXIST, "File exists")
        target_path = os.path.join(self.temp_dir.name, _RESCOPE_RECEIPT_FILENAME)
        with open(target_path, "wb") as f:
            f.write(b"target-file")
        os.chmod(target_path, 0o600)

        # Subcase A: temp file has nlink == 2
        temp_name_a = ".rescope_receipt_tmp_6789_aaaa012345678901"
        temp_path_a = os.path.join(self.temp_dir.name, temp_name_a)
        with open(temp_path_a, "wb") as f:
            f.write(b"payload-a")
        os.chmod(temp_path_a, 0o600)
        other_path_a = os.path.join(self.temp_dir.name, "other_hardlink")
        os.link(temp_path_a, other_path_a)

        with self.assertRaises(AutopilotError) as ctx:
            _prove_rescope_link_collision(self.dir_fd, temp_name_a, link_error)
        self.assertIn("ambiguous", str(ctx.exception))
        self.assertIn("link count", str(ctx.exception))

        # Subcase B: temp is a symlink
        temp_name_b = ".rescope_receipt_tmp_6789_bbbb012345678902"
        temp_path_b = os.path.join(self.temp_dir.name, temp_name_b)
        os.symlink(target_path, temp_path_b)

        with self.assertRaises(AutopilotError) as ctx:
            _prove_rescope_link_collision(self.dir_fd, temp_name_b, link_error)
        self.assertIn("ambiguous", str(ctx.exception))

        # Subcase C: path traversal in temp_name
        with self.assertRaises(AutopilotError) as ctx:
            _prove_rescope_link_collision(self.dir_fd, "../.rescope_receipt_tmp_evil", link_error)
        self.assertIn("ambiguous", str(ctx.exception))

        # Subcase D: invalid fd
        with self.assertRaises(AutopilotError) as ctx:
            _prove_rescope_link_collision(-1, temp_name_a, link_error)
        self.assertIn("ambiguous", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
