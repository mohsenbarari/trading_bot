import errno
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import two_site_codex_autopilot
from scripts.two_site_codex_autopilot import AutopilotError, _RESCOPE_INTENT_FILENAMES, rescope_intent_exists


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


if __name__ == "__main__":
    unittest.main()
