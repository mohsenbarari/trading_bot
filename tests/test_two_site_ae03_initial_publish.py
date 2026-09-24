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
    publish_initial_rescope_receipt,
    parse_rescope_receipt,
    PROTECTED_REFS,
    CONTROL_PATHS,
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

    def _make_valid_fixtures(self) -> tuple[dict, dict]:
        head = "a" * 40
        result = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "RESCOPE",
            "category": "INTERNAL_REJECTION",
            "summary": "Rescope cycle 1",
            "commit": None,
            "next_action": "rescope-next",
            "blockers": ["test-blocker-1"],
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
            "head_before": head,
            "head_after": head,
            "head_changed": False,
        }
        repo = {
            "head": head,
            "status": "",
            "protected_refs": {ref: "b" * 40 for ref in PROTECTED_REFS},
            "control_blobs": {p.as_posix(): "c" * 40 for p in CONTROL_PATHS},
            "assignment_scope": {
                "assignment_id": "test-assignment-scope",
                "stage_id": "P1-00b",
                "sha256": "d" * 64,
                "expires_at": "2026-12-31T23:59:59Z",
                "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
                "forbidden_paths": [],
            },
        }
        return result, repo

    def test_publish_initial_rescope_receipt_success_exact_full_envelope(self) -> None:
        state_root = Path(self.temp_dir.name).resolve()
        result, repo = self._make_valid_fixtures()

        publish_initial_rescope_receipt(state_root, result, repo)

        target_path = state_root / _RESCOPE_RECEIPT_FILENAME
        self.assertTrue(target_path.is_file())
        st = target_path.stat(follow_symlinks=False)
        self.assertEqual(st.st_mode & 0o777, 0o600)
        self.assertEqual(st.st_nlink, 1)

        raw_bytes = target_path.read_bytes()
        envelope = parse_rescope_receipt(raw_bytes, repo)
        self.assertEqual(envelope["schema_version"], 1)
        self.assertEqual(envelope["attempt"], 1)
        self.assertFalse(envelope["terminal"])
        self.assertEqual(envelope["result"]["summary"], result["summary"])
        self.assertEqual(envelope["repository"]["head"], repo["head"])

        temp_files = [f for f in os.listdir(state_root) if f.startswith(".rescope_receipt_tmp_")]
        self.assertEqual(temp_files, [])

    def test_publish_initial_rescope_receipt_second_call_and_unsafe_leaf_preserve_bytes(self) -> None:
        state_root = Path(self.temp_dir.name).resolve()
        result, repo = self._make_valid_fixtures()

        publish_initial_rescope_receipt(state_root, result, repo)
        target_path = state_root / _RESCOPE_RECEIPT_FILENAME
        before_bytes = target_path.read_bytes()
        before_ino = target_path.stat(follow_symlinks=False).st_ino

        with self.assertRaises(AutopilotError):
            publish_initial_rescope_receipt(state_root, result, repo)

        self.assertEqual(target_path.read_bytes(), before_bytes)
        self.assertEqual(target_path.stat(follow_symlinks=False).st_ino, before_ino)

        with tempfile.TemporaryDirectory() as other_tmp:
            other_root = Path(other_tmp).resolve()
            os.chmod(other_root, 0o700)
            symlink_target = other_root / "real_file.txt"
            symlink_target.write_bytes(b"unsafe-leaf-data")
            symlink_path = other_root / _RESCOPE_RECEIPT_FILENAME
            os.symlink(symlink_target, symlink_path)

            with self.assertRaises(AutopilotError):
                publish_initial_rescope_receipt(other_root, result, repo)

            self.assertTrue(symlink_path.is_symlink())
            self.assertEqual(symlink_target.read_bytes(), b"unsafe-leaf-data")
            temp_files = [f for f in os.listdir(other_root) if f.startswith(".rescope_receipt_tmp_")]
            self.assertEqual(temp_files, [])

    def test_publish_initial_rescope_receipt_proven_collision_cleans_temp(self) -> None:
        state_root = Path(self.temp_dir.name).resolve()
        result, repo = self._make_valid_fixtures()
        target_path = state_root / _RESCOPE_RECEIPT_FILENAME
        target_path.write_bytes(b"existing-unrelated-receipt")
        os.chmod(target_path, 0o600)
        target_ino_before = target_path.stat(follow_symlinks=False).st_ino

        orig_stat = os.stat
        first_stat_skipped = False

        def stat_hide_target_once(path, *args, **kwargs):
            nonlocal first_stat_skipped
            if path == _RESCOPE_RECEIPT_FILENAME and not first_stat_skipped:
                first_stat_skipped = True
                raise FileNotFoundError(errno.ENOENT, "Target not yet visible")
            return orig_stat(path, *args, **kwargs)

        with mock.patch("scripts.two_site_codex_autopilot.os.stat", side_effect=stat_hide_target_once):
            with self.assertRaises(AutopilotError) as ctx:
                publish_initial_rescope_receipt(state_root, result, repo)
            self.assertIn("collision", str(ctx.exception).lower())

        self.assertEqual(target_path.read_bytes(), b"existing-unrelated-receipt")
        self.assertEqual(target_path.stat(follow_symlinks=False).st_ino, target_ino_before)
        temp_files = [f for f in os.listdir(state_root) if f.startswith(".rescope_receipt_tmp_")]
        self.assertEqual(temp_files, [])

    def test_publish_initial_rescope_receipt_monkeypatched_link_eio_ambiguous_preserves_evidence(self) -> None:
        state_root = Path(self.temp_dir.name).resolve()
        result, repo = self._make_valid_fixtures()
        target_path = state_root / _RESCOPE_RECEIPT_FILENAME

        orig_link = os.link

        def link_then_eio(*args, **kwargs):
            orig_link(*args, **kwargs)
            raise OSError(errno.EIO, "Input/output error")

        with mock.patch("scripts.two_site_codex_autopilot.os.link", side_effect=link_then_eio):
            with self.assertRaises(AutopilotError) as ctx:
                publish_initial_rescope_receipt(state_root, result, repo)
            self.assertIn("ambiguous", str(ctx.exception).lower())

        self.assertTrue(target_path.is_file())
        target_st = target_path.stat(follow_symlinks=False)
        self.assertEqual(target_st.st_nlink, 2)

        temp_files = [f for f in os.listdir(state_root) if f.startswith(".rescope_receipt_tmp_")]
        self.assertEqual(len(temp_files), 1)
        temp_path = state_root / temp_files[0]
        temp_st = temp_path.stat(follow_symlinks=False)
        self.assertEqual(temp_st.st_nlink, 2)
        self.assertEqual((temp_st.st_dev, temp_st.st_ino), (target_st.st_dev, target_st.st_ino))

    def test_publish_initial_rescope_receipt_link_keyboard_interrupt_propagates_and_preserves_evidence(self) -> None:
        state_root = Path(self.temp_dir.name).resolve()
        result, repo = self._make_valid_fixtures()
        target_path = state_root / _RESCOPE_RECEIPT_FILENAME

        orig_link = os.link

        def link_then_ki(*args, **kwargs):
            orig_link(*args, **kwargs)
            raise KeyboardInterrupt("simulated interrupt")

        with mock.patch("scripts.two_site_codex_autopilot.os.link", side_effect=link_then_ki):
            with self.assertRaises(KeyboardInterrupt):
                publish_initial_rescope_receipt(state_root, result, repo)

        self.assertTrue(target_path.is_file())
        target_st = target_path.stat(follow_symlinks=False)
        self.assertEqual(target_st.st_nlink, 2)

        temp_files = [f for f in os.listdir(state_root) if f.startswith(".rescope_receipt_tmp_")]
        self.assertEqual(len(temp_files), 1)
        temp_path = state_root / temp_files[0]
        temp_st = temp_path.stat(follow_symlinks=False)
        self.assertEqual(temp_st.st_nlink, 2)
        self.assertEqual((temp_st.st_dev, temp_st.st_ino), (target_st.st_dev, target_st.st_ino))

    def test_publish_initial_rescope_receipt_physical_post_link_replacement_ambiguous(self) -> None:
        state_root = Path(self.temp_dir.name).resolve()
        result, repo = self._make_valid_fixtures()
        target_path = state_root / _RESCOPE_RECEIPT_FILENAME

        orig_fsync = os.fsync

        def fsync_replace_target(fd):
            orig_fsync(fd)
            if target_path.exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
                diff_result = dict(result)
                diff_result["summary"] = "Replaced receipt summary"
                from scripts.two_site_codex_autopilot import serialize_rescope_receipt
                other_payload = serialize_rescope_receipt(diff_result, repo, attempt=1)
                target_path.write_bytes(other_payload)

        with mock.patch("scripts.two_site_codex_autopilot.os.fsync", side_effect=fsync_replace_target):
            with self.assertRaises(AutopilotError) as ctx:
                publish_initial_rescope_receipt(state_root, result, repo)
            self.assertIn("ambiguous", str(ctx.exception).lower())

    def test_publish_initial_rescope_receipt_post_link_faults_ambiguity_and_primary_cause(self) -> None:
        state_root = Path(self.temp_dir.name).resolve()
        result, repo = self._make_valid_fixtures()
        target_path = state_root / _RESCOPE_RECEIPT_FILENAME

        # Fault A: post-link unlink fault
        with mock.patch("scripts.two_site_codex_autopilot.os.unlink", side_effect=OSError(errno.EIO, "unlink failed")):
            with self.assertRaises(AutopilotError) as ctx:
                publish_initial_rescope_receipt(state_root, result, repo)
            self.assertIn("ambiguous", str(ctx.exception).lower())
            self.assertIsInstance(ctx.exception.__cause__, OSError)
            self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)

        for f in os.listdir(state_root):
            p = state_root / f
            p.unlink() if p.is_file() else None

        # Fault B: post-link fsync fault
        orig_fsync = os.fsync

        def fsync_fail_post_link(fd):
            if target_path.exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "fsync failed")
            return orig_fsync(fd)

        with mock.patch("scripts.two_site_codex_autopilot.os.fsync", side_effect=fsync_fail_post_link):
            with self.assertRaises(AutopilotError) as ctx:
                publish_initial_rescope_receipt(state_root, result, repo)
            self.assertIn("ambiguous", str(ctx.exception).lower())
            self.assertIsInstance(ctx.exception.__cause__, OSError)
            self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)

        for f in os.listdir(state_root):
            p = state_root / f
            p.unlink() if p.is_file() else None

        # Fault C: post-link close fault alone
        import scripts.two_site_codex_autopilot as autopilot_mod
        orig_load = autopilot_mod.load_rescope_receipt
        orig_close = os.close
        after_load = False

        def wrapped_load(*args, **kwargs):
            nonlocal after_load
            res = orig_load(*args, **kwargs)
            after_load = True
            return res

        def close_fail(fd):
            if after_load:
                orig_close(fd)
                raise OSError(errno.EIO, "close dir_fd failed")
            return orig_close(fd)

        with mock.patch("scripts.two_site_codex_autopilot.load_rescope_receipt", side_effect=wrapped_load), \
             mock.patch("scripts.two_site_codex_autopilot.os.close", side_effect=close_fail):
            with self.assertRaises(AutopilotError) as ctx:
                publish_initial_rescope_receipt(state_root, result, repo)
            self.assertIn("ambiguous", str(ctx.exception).lower())
            self.assertIsInstance(ctx.exception.__cause__, OSError)
            self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)


if __name__ == "__main__":
    unittest.main()
