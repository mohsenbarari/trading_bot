import errno
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import two_site_codex_autopilot
from scripts.two_site_codex_autopilot import AutopilotError, _RESCOPE_INTENT_FILENAMES, publish_rescope_intent, rescope_intent_exists


class TestRescopeIntent(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.epoch_dir = self.tmp / "epoch"
        self.epoch_dir.mkdir(mode=0o700)
        os.chmod(self.tmp, 0o700)
        os.chmod(self.epoch_dir, 0o700)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_then_present_regular_all_attempts(self) -> None:
        for att in (1, 2, 3):
            self.assertFalse(rescope_intent_exists(self.epoch_dir, att))
            leaf = self.epoch_dir / _RESCOPE_INTENT_FILENAMES[att]
            leaf.touch(mode=0o600)
            self.assertTrue(rescope_intent_exists(self.epoch_dir, att))

    def test_symlink_broken_link_and_dir_present(self) -> None:
        target = self.epoch_dir / "target"
        target.touch(mode=0o600)
        (self.epoch_dir / "rescope_intent_1").symlink_to(target)
        (self.epoch_dir / "rescope_intent_2").symlink_to(self.epoch_dir / "missing_target")
        (self.epoch_dir / "rescope_intent_3").mkdir(mode=0o700)
        for att in (1, 2, 3):
            self.assertTrue(rescope_intent_exists(self.epoch_dir, att))

    def test_invalid_arguments(self) -> None:
        for bad_dir in ("/not/a/path", None, 123):
            with self.assertRaises(AutopilotError):
                rescope_intent_exists(bad_dir, 1)  # type: ignore[arg-type]
        for bad_att in (True, False, 0, 4, -1, "1", 1.5, None):
            with self.assertRaises(AutopilotError):
                rescope_intent_exists(self.epoch_dir, bad_att)  # type: ignore[arg-type]

    def test_stat_eacces_and_type_error(self) -> None:
        orig_stat = os.stat
        for exc_to_raise in (OSError(errno.EACCES, "denied"), TypeError("bad arg")):
            def stat_err(name, *args, _exc=exc_to_raise, **kwargs):
                if name == "rescope_intent_1":
                    raise _exc
                return orig_stat(name, *args, **kwargs)
            with patch.object(os, "stat", side_effect=stat_err):
                with self.assertRaises(AutopilotError):
                    rescope_intent_exists(self.epoch_dir, 1)

    def test_swapped_root_causes_recheck_failure(self) -> None:
        orig_stat = os.stat
        for present in (False, True):
            sub_dir = self.tmp / f"epoch_{present}"
            sub_dir.mkdir(mode=0o700)
            os.chmod(sub_dir, 0o700)
            if present:
                (sub_dir / "rescope_intent_1").touch(mode=0o600)
            def stat_swap(name, *args, **kwargs):
                try:
                    res, exc = orig_stat(name, *args, **kwargs), None
                except OSError as err:
                    res, exc = None, err
                if name == "rescope_intent_1":
                    alt = sub_dir.parent / f"{sub_dir.name}_alt"
                    sub_dir.rename(alt)
                    sub_dir.mkdir(mode=0o700)
                    os.chmod(sub_dir, 0o700)
                if exc is not None:
                    raise exc
                return res
            with patch.object(os, "stat", side_effect=stat_swap):
                with self.assertRaises(AutopilotError):
                    rescope_intent_exists(sub_dir, 1)

    def test_close_fault_and_interrupt(self) -> None:
        target_fd = [None]
        orig_open, orig_close, orig_stat = two_site_codex_autopilot._open_private_rescope_state_dir, os.close, os.stat
        def track_open(path):
            target_fd[0] = None
            fd = orig_open(path)
            target_fd[0] = fd
            return fd
        def close_fault(fd):
            orig_close(fd)
            if target_fd[0] is not None and fd == target_fd[0]:
                target_fd[0] = None
                raise OSError(errno.EIO, "close error")
        def leaf_stat(side_effect):
            def _stat(name, *args, **kwargs):
                if name in _RESCOPE_INTENT_FILENAMES.values():
                    raise side_effect
                return orig_stat(name, *args, **kwargs)
            return _stat
        def close_interrupt(fd):
            orig_close(fd)
            if target_fd[0] is not None and fd == target_fd[0]:
                target_fd[0] = None
                raise KeyboardInterrupt
        with patch.object(two_site_codex_autopilot, "_open_private_rescope_state_dir", side_effect=track_open):
            with patch.object(os, "close", side_effect=close_fault):
                with self.assertRaises(AutopilotError) as ctx_clean:
                    rescope_intent_exists(self.epoch_dir, 1)
                self.assertIsInstance(ctx_clean.exception.__cause__, OSError)
                with patch.object(os, "stat", side_effect=leaf_stat(OSError(errno.EACCES, "denied"))):
                    with self.assertRaises(AutopilotError) as ctx:
                        rescope_intent_exists(self.epoch_dir, 1)
                    self.assertIsInstance(ctx.exception.__cause__, AutopilotError)
                with patch.object(os, "stat", side_effect=leaf_stat(KeyboardInterrupt)):
                    with self.assertRaises(KeyboardInterrupt):
                        rescope_intent_exists(self.epoch_dir, 1)
            with patch.object(os, "close", side_effect=close_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    rescope_intent_exists(self.epoch_dir, 1)

    def test_publish_exact_attempts_and_args(self) -> None:
        for att in (1, 2, 3):
            self.assertFalse(rescope_intent_exists(self.epoch_dir, att))
            publish_rescope_intent(self.epoch_dir, att)
            self.assertTrue(rescope_intent_exists(self.epoch_dir, att))
            leaf = self.epoch_dir / _RESCOPE_INTENT_FILENAMES[att]
            self.assertEqual(leaf.read_bytes(), f"{leaf.name}\n".encode("ascii"))
        for bad_dir in ("/not/a/path", None, 123):
            with self.assertRaises(AutopilotError):
                publish_rescope_intent(bad_dir, 1)  # type: ignore[arg-type]
        for bad_att in (True, False, 0, 4, -1, "1", 1.5, None):
            with self.assertRaises(AutopilotError):
                publish_rescope_intent(self.epoch_dir, bad_att)  # type: ignore[arg-type]

    def test_publish_collision_and_symlink(self) -> None:
        publish_rescope_intent(self.epoch_dir, 1)
        leaf = self.epoch_dir / _RESCOPE_INTENT_FILENAMES[1]
        st1 = leaf.stat()
        bytes1 = leaf.read_bytes()
        with self.assertRaises(AutopilotError):
            publish_rescope_intent(self.epoch_dir, 1)
        st2 = leaf.stat()
        self.assertEqual((st1.st_dev, st1.st_ino), (st2.st_dev, st2.st_ino))
        self.assertEqual(bytes1, leaf.read_bytes())
        target = self.epoch_dir / "target"
        target.write_bytes(b"target")
        (self.epoch_dir / _RESCOPE_INTENT_FILENAMES[2]).symlink_to(target)
        with self.assertRaises(AutopilotError):
            publish_rescope_intent(self.epoch_dir, 2)
        self.assertEqual(target.read_bytes(), b"target")

    def test_publish_partial_write_and_no_unlink(self) -> None:
        orig_write = os.write
        write_calls = [0]
        def partial_write(fd, data):
            if write_calls[0] == 0:
                write_calls[0] += 1
                return orig_write(fd, data[:4])
            return orig_write(fd, data)
        with patch.object(os, "write", side_effect=partial_write), \
             patch.object(os, "unlink", side_effect=AssertionError("unlink called")), \
             patch.object(os, "replace", side_effect=AssertionError("replace called")):
            publish_rescope_intent(self.epoch_dir, 1)
        self.assertTrue(rescope_intent_exists(self.epoch_dir, 1))
        self.assertGreater(write_calls[0], 0)

    def test_publish_faults_leave_marker(self) -> None:
        def fsync_fail(fd):
            raise OSError(errno.EIO, "fsync fail")
        with patch.object(os, "fsync", side_effect=fsync_fail):
            with self.assertRaises(AutopilotError):
                publish_rescope_intent(self.epoch_dir, 1)
        self.assertTrue((self.epoch_dir / _RESCOPE_INTENT_FILENAMES[1]).exists())
        calls = [0]
        def recheck_fail(path, fd):
            calls[0] += 1
            if calls[0] > 1:
                raise AutopilotError("root swapped")
        with patch.object(two_site_codex_autopilot, "_recheck_final_root", side_effect=recheck_fail):
            with self.assertRaises(AutopilotError):
                publish_rescope_intent(self.epoch_dir, 2)
        self.assertTrue((self.epoch_dir / _RESCOPE_INTENT_FILENAMES[2]).exists())
        fsync_cnt = [0]
        def dir_fsync_fail(fd):
            fsync_cnt[0] += 1
            if fsync_cnt[0] == 2:
                raise OSError(errno.EIO, "dir fsync fail")
        with patch.object(os, "fsync", side_effect=dir_fsync_fail):
            with self.assertRaises(AutopilotError):
                publish_rescope_intent(self.epoch_dir, 3)
        self.assertTrue((self.epoch_dir / _RESCOPE_INTENT_FILENAMES[3]).exists())

    def test_publish_inode_mismatch_dual_fault_and_interrupt(self) -> None:
        orig_stat = os.stat
        def fake_stat(path, *args, **kwargs):
            res = orig_stat(path, *args, **kwargs)
            if path == _RESCOPE_INTENT_FILENAMES[1]:
                return os.stat_result((res.st_mode, res.st_ino + 999) + res[2:])
            return res
        with patch.object(os, "stat", side_effect=fake_stat):
            with self.assertRaises(AutopilotError) as ctx:
                publish_rescope_intent(self.epoch_dir, 1)
            self.assertIn("inode mismatch", str(ctx.exception))
        orig_write, orig_close = os.write, os.close
        write_occurred = [False]
        def write_fail(fd, data):
            write_occurred[0] = True
            raise OSError(errno.EIO, "write error")
        def close_fail(fd):
            orig_close(fd)
            if write_occurred[0]:
                raise OSError(errno.EIO, "close error")
        with patch.object(os, "write", side_effect=write_fail), \
             patch.object(os, "close", side_effect=close_fail):
            with self.assertRaises(AutopilotError) as ctx:
                publish_rescope_intent(self.epoch_dir, 2)
            self.assertIn("error closing descriptor", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, AutopilotError)
            self.assertIn("cannot write", str(ctx.exception.__cause__))
        with patch.object(os, "write", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                publish_rescope_intent(self.epoch_dir, 3)


if __name__ == "__main__":
    unittest.main()
