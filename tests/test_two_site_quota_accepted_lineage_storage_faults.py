"""Hermetic fault matrix for accepted lineage storage."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import json
from pathlib import Path
import stat
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from scripts.two_site_quota_accepted_lineage_storage import (
    ACCEPTED_LINEAGE_FILENAME,
    MAX_ACCEPTED_LINEAGE_RECORD_BYTES,
    AcceptedLineageStorageError,
    publish_accepted_lineage_record_candidate,
    read_accepted_lineage_record_candidate,
    read_untrusted_accepted_lineage_record_candidate,
)
from tests.test_two_site_quota_accepted_lineage_storage import (
    _create_cycle_dir,
    _make_fixtures,
)


def _setup_cycle(
    root: Path, run_id: str
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    cycle_dir = _create_cycle_dir(root, run_id)
    exp, raw, ctx, up = _make_fixtures(run_id)
    res = cycle_dir / "result.json"
    res.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    os.chmod(res, 0o644)
    return cycle_dir, exp, ctx, up


def _stat_snap(p: Path) -> tuple[bytes, int, int, int]:
    st = os.stat(p)
    return p.read_bytes(), st.st_mode, st.st_ino, st.st_mtime_ns


class _ModifiedStat:
    def __init__(self, real: os.stat_result, **kw: Any) -> None:
        self._real = real
        self._kw = kw

    def __getattr__(self, name: str) -> Any:
        return self._kw.get(name, getattr(self._real, name))


class TestAcceptedLineageStorageFaults(unittest.TestCase):
    def test_r1_existing_target_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cd1, exp1, ctx1, up1 = _setup_cycle(root, "cycle-0001-20260929T120000Z-101")
            publish_accepted_lineage_record_candidate(root, "cycle-0001-20260929T120000Z-101", exp1, ctx1, up1)
            snap1 = _stat_snap(cd1 / ACCEPTED_LINEAGE_FILENAME)
            with self.assertRaises(AcceptedLineageStorageError) as err:
                publish_accepted_lineage_record_candidate(root, "cycle-0001-20260929T120000Z-101", exp1, ctx1, up1)
            self.assertIn("already exists", str(err.exception))
            self.assertEqual(snap1, _stat_snap(cd1 / ACCEPTED_LINEAGE_FILENAME))

            cd2, exp2, ctx2, up2 = _setup_cycle(root, "cycle-0002-20260929T120000Z-102")
            t2 = cd2 / ACCEPTED_LINEAGE_FILENAME
            t2.write_bytes(b"{\"bad\": true")
            os.chmod(t2, 0o600)
            snap2 = _stat_snap(t2)
            with self.assertRaises(AcceptedLineageStorageError) as err:
                publish_accepted_lineage_record_candidate(root, "cycle-0002-20260929T120000Z-102", exp2, ctx2, up2)
            self.assertIn("already exists", str(err.exception))
            self.assertEqual(snap2, _stat_snap(t2))

            cd3, exp3, ctx3, up3 = _setup_cycle(root, "cycle-0003-20260929T120000Z-103")
            t3 = cd3 / ACCEPTED_LINEAGE_FILENAME
            t3.symlink_to(cd3 / "result.json")
            with self.assertRaises(AcceptedLineageStorageError) as err:
                publish_accepted_lineage_record_candidate(root, "cycle-0003-20260929T120000Z-103", exp3, ctx3, up3)
            self.assertIn("already exists", str(err.exception))
            self.assertTrue(t3.is_symlink())

            cd4, exp4, ctx4, up4 = _setup_cycle(root, "cycle-0004-20260929T120000Z-104")
            publish_accepted_lineage_record_candidate(root, "cycle-0004-20260929T120000Z-104", exp4, ctx4, up4)
            t4 = cd4 / ACCEPTED_LINEAGE_FILENAME
            hl4 = cd4 / "accepted-lineage.hl"
            os.link(t4, hl4)
            snap4 = _stat_snap(t4)
            with self.assertRaises(AcceptedLineageStorageError) as err:
                publish_accepted_lineage_record_candidate(root, "cycle-0004-20260929T120000Z-104", exp4, ctx4, up4)
            self.assertIn("already exists", str(err.exception))
            self.assertEqual(snap4, _stat_snap(t4))
            self.assertEqual(os.stat(t4).st_nlink, 2)

    def test_r1_pre_link_temp_o_excl_collision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_id = "cycle-0005-20260929T120000Z-105"
            cd, exp, ctx, up = _setup_cycle(root, run_id)
            tmp = cd / f".{ACCEPTED_LINEAGE_FILENAME}.tmp.{os.getpid()}"
            sentinel = b"pre-existing-unrelated-temp-content"
            tmp.write_bytes(sentinel)
            os.chmod(tmp, 0o600)
            st0 = os.stat(tmp)

            with self.assertRaises(AcceptedLineageStorageError) as err:
                publish_accepted_lineage_record_candidate(root, run_id, exp, ctx, up)
            self.assertIn("cannot safely create temp file", str(err.exception))

            self.assertFalse((cd / ACCEPTED_LINEAGE_FILENAME).exists())
            self.assertTrue(tmp.exists())
            self.assertEqual(tmp.read_bytes(), sentinel)
            self.assertEqual(st0.st_ino, os.stat(tmp).st_ino)

    def test_r1_pre_link_io_faults_clean_only_own_temp(self) -> None:
        real_write = os.write
        real_close = os.close

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            cd, exp, ctx, up = _setup_cycle(root, "cycle-0006-20260929T120000Z-001")
            w_cnt = 0
            def fake_write_stalled(fd: int, data: bytes) -> int:
                nonlocal w_cnt
                w_cnt += 1
                return real_write(fd, data[:4]) if w_cnt == 1 else 0
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.write", side_effect=fake_write_stalled):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, "cycle-0006-20260929T120000Z-001", exp, ctx, up)
                self.assertIn("write stalled", str(err.exception))
            self.assertFalse((cd / ACCEPTED_LINEAGE_FILENAME).exists())
            self.assertEqual([f for f in os.listdir(cd) if f.startswith(".")], [])

            cd, exp, ctx, up = _setup_cycle(root, "cycle-0006-20260929T120000Z-002")
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.write", side_effect=OSError(5, "Write err")):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, "cycle-0006-20260929T120000Z-002", exp, ctx, up)
                self.assertIn("cannot write", str(err.exception))
            self.assertFalse((cd / ACCEPTED_LINEAGE_FILENAME).exists())
            self.assertEqual([f for f in os.listdir(cd) if f.startswith(".")], [])

            cd, exp, ctx, up = _setup_cycle(root, "cycle-0006-20260929T120000Z-003")
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.fchmod", side_effect=OSError(1, "fchmod err")):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, "cycle-0006-20260929T120000Z-003", exp, ctx, up)
                self.assertIn("cannot fchmod", str(err.exception))
            self.assertFalse((cd / ACCEPTED_LINEAGE_FILENAME).exists())
            self.assertEqual([f for f in os.listdir(cd) if f.startswith(".")], [])

            cd, exp, ctx, up = _setup_cycle(root, "cycle-0006-20260929T120000Z-004")
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.fstat", side_effect=OSError(5, "fstat err")):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, "cycle-0006-20260929T120000Z-004", exp, ctx, up)
                self.assertIn("cannot fstat", str(err.exception))
            self.assertFalse((cd / ACCEPTED_LINEAGE_FILENAME).exists())
            self.assertEqual([f for f in os.listdir(cd) if f.startswith(".")], [])

            cd, exp, ctx, up = _setup_cycle(root, "cycle-0006-20260929T120000Z-005")
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.fsync", side_effect=OSError(5, "fsync err")):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, "cycle-0006-20260929T120000Z-005", exp, ctx, up)
                self.assertIn("cannot fsync", str(err.exception))
            self.assertFalse((cd / ACCEPTED_LINEAGE_FILENAME).exists())
            self.assertEqual([f for f in os.listdir(cd) if f.startswith(".")], [])

            cd, exp, ctx, up = _setup_cycle(root, "cycle-0006-20260929T120000Z-006")
            def fake_close(fd: int) -> None:
                try:
                    st = os.fstat(fd)
                    is_temp = stat.S_ISREG(st.st_mode) and (stat.S_IMODE(st.st_mode) == 0o600)
                except OSError:
                    is_temp = False
                real_close(fd)
                if is_temp:
                    raise OSError(5, "Simulated close error on temp file descriptor")
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.close", side_effect=fake_close):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, "cycle-0006-20260929T120000Z-006", exp, ctx, up)
                self.assertIn("cannot safely close temp file", str(err.exception))
            self.assertFalse((cd / ACCEPTED_LINEAGE_FILENAME).exists())
            self.assertEqual([f for f in os.listdir(cd) if f.startswith(".")], [])

            cd, exp, ctx, up = _setup_cycle(root, "cycle-0006-20260929T120000Z-007")
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.link", side_effect=FileExistsError(17, "Link EEXIST")):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, "cycle-0006-20260929T120000Z-007", exp, ctx, up)
                self.assertIn("target already exists", str(err.exception))
            self.assertFalse((cd / ACCEPTED_LINEAGE_FILENAME).exists())
            self.assertEqual([f for f in os.listdir(cd) if f.startswith(".")], [])

    def test_r2_post_link_faults_ambiguity_and_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            run1 = "cycle-0020-20260929T120000Z-201"
            cd1, exp1, ctx1, up1 = _setup_cycle(root, run1)
            tmp1 = cd1 / f".{ACCEPTED_LINEAGE_FILENAME}.tmp.{os.getpid()}"
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.unlink",
                       side_effect=lambda p, **kw: (_ for _ in ()).throw(OSError(13, "unlink denied"))):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, run1, exp1, ctx1, up1)
                self.assertIn("ambiguous post-link publication failure: cannot unlink", str(err.exception))
            tgt1 = cd1 / ACCEPTED_LINEAGE_FILENAME
            self.assertTrue(tgt1.exists() and tmp1.exists())
            st_tgt1, st_tmp1 = os.stat(tgt1), os.stat(tmp1)
            self.assertEqual(st_tgt1.st_ino, st_tmp1.st_ino)
            self.assertEqual(st_tgt1.st_nlink, 2)
            self.assertEqual(stat.S_IMODE(st_tgt1.st_mode), 0o600)

            run2 = "cycle-0021-20260929T120000Z-202"
            cd2, exp2, ctx2, up2 = _setup_cycle(root, run2)
            real_fsync = os.fsync
            calls2 = 0
            def fake_fsync2(fd: int) -> None:
                nonlocal calls2
                calls2 += 1
                if calls2 == 2:
                    raise OSError(5, "fsync dir err")
                real_fsync(fd)
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.fsync", side_effect=fake_fsync2):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, run2, exp2, ctx2, up2)
                self.assertIn("ambiguous post-link publication failure: cannot fsync directory", str(err.exception))
            tgt2 = cd2 / ACCEPTED_LINEAGE_FILENAME
            self.assertTrue(tgt2.exists())
            self.assertEqual(os.stat(tgt2).st_nlink, 1)
            self.assertEqual([f for f in os.listdir(cd2) if f.startswith(".")], [])

            run3 = "cycle-0022-20260929T120000Z-203"
            cd3, exp3, ctx3, up3 = _setup_cycle(root, run3)
            with patch("scripts.two_site_quota_accepted_lineage_storage.read_accepted_lineage_record_candidate",
                       side_effect=AcceptedLineageStorageError("Simulated readback err")):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    publish_accepted_lineage_record_candidate(root, run3, exp3, ctx3, up3)
                self.assertIn("ambiguous post-link publication failure: readback failed", str(err.exception))
            tgt3 = cd2 / ACCEPTED_LINEAGE_FILENAME if False else cd3 / ACCEPTED_LINEAGE_FILENAME
            self.assertTrue(tgt3.exists())
            self.assertEqual(os.stat(tgt3).st_nlink, 1)

    def test_r3_reader_format_and_metadata_faults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = "cycle-0030-20260929T120000Z-301"
            cd, exp, ctx, up = _setup_cycle(root, run)
            publish_accepted_lineage_record_candidate(root, run, exp, ctx, up)
            tgt = cd / ACCEPTED_LINEAGE_FILENAME
            canonical_bytes = tgt.read_bytes()

            os.chmod(tgt, 0o644)
            with self.assertRaises(AcceptedLineageStorageError):
                read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
            os.chmod(tgt, 0o600)
            self.assertEqual(tgt.read_bytes(), canonical_bytes)

            hl = cd / "accepted-lineage.hl"
            os.link(tgt, hl)
            with self.assertRaises(AcceptedLineageStorageError):
                read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
            os.unlink(hl)

            tgt.write_bytes(b"")
            with self.assertRaises(AcceptedLineageStorageError):
                read_accepted_lineage_record_candidate(root, run, exp, ctx, up)

            tgt.write_bytes(b"{\"a\":\"" + b"x" * MAX_ACCEPTED_LINEAGE_RECORD_BYTES + b"\"}")
            with self.assertRaises(AcceptedLineageStorageError):
                read_accepted_lineage_record_candidate(root, run, exp, ctx, up)

            tgt.write_bytes(b"\xff\xfe")
            with self.assertRaises(AcceptedLineageStorageError):
                read_untrusted_accepted_lineage_record_candidate(root, run)

            tgt.write_bytes(b"{\"k\":1,\"k\":2}")
            with self.assertRaises(AcceptedLineageStorageError):
                read_untrusted_accepted_lineage_record_candidate(root, run)

            tgt.write_bytes(canonical_bytes.replace(b":", b": "))
            with self.assertRaises(AcceptedLineageStorageError):
                read_accepted_lineage_record_candidate(root, run, exp, ctx, up)

            tgt.write_bytes(canonical_bytes)
            os.chmod(tgt, 0o600)
            snap = _stat_snap(tgt)
            tgt_ino = snap[2]

            real_stat, real_fstat, real_lstat, real_read = os.stat, os.fstat, os.lstat, os.read

            with patch("scripts.two_site_quota_accepted_lineage_storage.os.stat",
                       side_effect=lambda name, **kw: _ModifiedStat(real_stat(name, **kw), st_mtime_ns=real_stat(name, **kw).st_mtime_ns + 1000) if kw.get("dir_fd") is not None and name == ACCEPTED_LINEAGE_FILENAME else real_stat(name, **kw)):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
                self.assertIn("dir_fd stat mismatch before open", str(err.exception))
            self.assertEqual(snap, _stat_snap(tgt))

            def fake_fstat_before(fd: int) -> Any:
                st = real_fstat(fd)
                if st.st_ino == tgt_ino:
                    return _ModifiedStat(st, st_mtime_ns=st.st_mtime_ns + 1000)
                return st
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.fstat", side_effect=fake_fstat_before):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
                self.assertIn("descriptor metadata mismatch before read", str(err.exception))
            self.assertEqual(snap, _stat_snap(tgt))

            c_fstat_after = 0
            def fake_fstat_after(fd: int) -> Any:
                nonlocal c_fstat_after
                st = real_fstat(fd)
                if st.st_ino == tgt_ino:
                    c_fstat_after += 1
                    if c_fstat_after == 2:
                        return _ModifiedStat(st, st_mtime_ns=st.st_mtime_ns + 1000)
                return st
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.fstat", side_effect=fake_fstat_after):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
                self.assertIn("descriptor metadata changed after read", str(err.exception))
            self.assertEqual(snap, _stat_snap(tgt))

            c_dirfd = 0
            def fake_dirfd(name: Any, **kw: Any) -> Any:
                nonlocal c_dirfd
                st = real_stat(name, **kw)
                if kw.get("dir_fd") is not None and name == ACCEPTED_LINEAGE_FILENAME:
                    c_dirfd += 1
                    if c_dirfd == 2:
                        return _ModifiedStat(st, st_mtime_ns=st.st_mtime_ns + 1000)
                return st
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.stat", side_effect=fake_dirfd):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
                self.assertIn("dir_fd metadata changed after read", str(err.exception))
            self.assertEqual(snap, _stat_snap(tgt))

            c_lstat = 0
            def fake_lstat(path: Any, **kw: Any) -> Any:
                nonlocal c_lstat
                st = real_lstat(path, **kw)
                if Path(path).name == ACCEPTED_LINEAGE_FILENAME:
                    c_lstat += 1
                    if c_lstat == 2:
                        return _ModifiedStat(st, st_mtime_ns=st.st_mtime_ns + 1000)
                return st
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.lstat", side_effect=fake_lstat):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
                self.assertIn("path metadata changed after read", str(err.exception))
            self.assertEqual(snap, _stat_snap(tgt))

            def fake_read_err(fd: int, n: int) -> bytes:
                ino = None
                try:
                    ino = real_fstat(fd).st_ino
                except OSError:
                    pass
                if ino == tgt_ino:
                    raise OSError(5, "read err")
                return real_read(fd, n)
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.read", side_effect=fake_read_err):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
                self.assertIn("cannot read accepted-lineage.json", str(err.exception))
            self.assertEqual(snap, _stat_snap(tgt))

            c_read = 0
            def fake_read_trunc(fd: int, n: int) -> bytes:
                nonlocal c_read
                ino = None
                try:
                    ino = real_fstat(fd).st_ino
                except OSError:
                    pass
                if ino == tgt_ino:
                    c_read += 1
                    return real_read(fd, n)[:4] if c_read == 1 else b""
                return real_read(fd, n)
            with patch("scripts.two_site_quota_accepted_lineage_storage.os.read", side_effect=fake_read_trunc):
                with self.assertRaises(AcceptedLineageStorageError) as err:
                    read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
                self.assertIn("read size mismatch", str(err.exception))
            self.assertEqual(snap, _stat_snap(tgt))

    def test_r4_zero_live_authority_under_faults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = "cycle-0040-20260929T120000Z-401"
            cd, exp, ctx, up = _setup_cycle(root, run)
            with patch("subprocess.run", side_effect=AssertionError("subprocess forbidden")),                 patch("subprocess.Popen", side_effect=AssertionError("subprocess forbidden")),                 patch("socket.socket", side_effect=AssertionError("socket forbidden")):
                pub = publish_accepted_lineage_record_candidate(root, run, exp, ctx, up)
                self.assertEqual(pub["controller_run_id"], run)
                rec = read_accepted_lineage_record_candidate(root, run, exp, ctx, up)
                self.assertEqual(rec["controller_run_id"], run)
                untrusted = read_untrusted_accepted_lineage_record_candidate(root, run)
                self.assertEqual(untrusted["controller_run_id"], run)


if __name__ == "__main__":
    unittest.main()
