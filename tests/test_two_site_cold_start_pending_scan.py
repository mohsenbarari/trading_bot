#!/usr/bin/env python3
"""Hermetic unit tests for dormant cold-adoption pending-artifact scan under AE03."""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

try:
    import scripts.two_site_cold_start_pending_scan as pending_scan_mod
    from scripts.two_site_cold_start_pending_scan import (
        ColdStartPendingScanError,
        MAX_RUNS_ENTRIES,
        MAX_RUN_CHILD_ENTRIES,
        PENDING_EXACT_NAMES,
        PendingColdAdoptionError,
        RUNS_DIRNAME,
        assert_no_pending_cold_adoption_artifacts_locked,
    )
except ImportError:
    import two_site_cold_start_pending_scan as pending_scan_mod  # type: ignore[no-redef]
    from two_site_cold_start_pending_scan import (  # type: ignore[no-redef]
        ColdStartPendingScanError,
        MAX_RUNS_ENTRIES,
        MAX_RUN_CHILD_ENTRIES,
        PENDING_EXACT_NAMES,
        PendingColdAdoptionError,
        RUNS_DIRNAME,
        assert_no_pending_cold_adoption_artifacts_locked,
    )

VALID_RUN_1 = "cycle-0001-20260929T120000Z-1"
VALID_RUN_2 = "cycle-0002-20260929T130000Z-2"


class TestTwoSiteColdStartPendingScan(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="two-site-test-pending-scan-")
        self.state_root = Path(self._tmp.name).resolve(strict=True)
        os.chmod(self.state_root, 0o700)
        self.runs_dir = self.state_root / RUNS_DIRNAME
        self.runs_dir.mkdir(mode=0o700)
        os.chmod(self.runs_dir, 0o700)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _create_historical_run(self, name: str, mode: int) -> Path:
        run_dir = self.runs_dir / name
        run_dir.mkdir(mode=mode)
        os.chmod(run_dir, mode)
        allowed_files = {
            "status.json": b'{"status": "OK"}',
            "result.json": b'{"result": "terminal"}',
            "prompt.txt": b"Instruction prompt content",
            "broker-reservation.json": b'{"reserved": false}',
        }
        for fname, payload in allowed_files.items():
            fpath = run_dir / fname
            fpath.write_bytes(payload)
            os.chmod(fpath, 0o600)
        return run_dir

    def _snapshot_tree(self) -> dict[str, tuple[int, int, int, bytes | None]]:
        tree: dict[str, tuple[int, int, int, bytes | None]] = {}
        for root, dirs, files in os.walk(self.state_root):
            for d in dirs:
                dp = Path(root) / d
                st = dp.stat()
                tree[str(dp)] = (st.st_mode, st.st_size, st.st_mtime_ns, None)
            for f in files:
                fp = Path(root) / f
                st = fp.stat()
                tree[str(fp)] = (st.st_mode, st.st_size, st.st_mtime_ns, fp.read_bytes())
        return tree

    def test_clean_historical_runs_pass_without_mutation(self) -> None:
        self._create_historical_run(VALID_RUN_1, 0o700)
        self._create_historical_run(VALID_RUN_2, 0o755)

        snapshot_before = self._snapshot_tree()
        result = assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        self.assertIsNone(result)
        snapshot_after = self._snapshot_tree()
        self.assertEqual(snapshot_before, snapshot_after)

    def test_pending_markers_fail_closed_without_read(self) -> None:
        run_dir = self._create_historical_run(VALID_RUN_1, 0o700)
        markers = sorted(list(PENDING_EXACT_NAMES)) + [
            "quota-unrecognized.json",
            ".quota-wait.json.tmp",
            "final-ack.json.bak",
            ".final-ack.tmp",
            "accepted-lineage-snapshot.json",
            ".accepted-lineage.tmp",
        ]

        read_called = {"count": 0}
        real_read = os.read

        def guarded_read(fd: int, n: int) -> bytes:
            read_called["count"] += 1
            return real_read(fd, n)

        with patch("os.read", side_effect=guarded_read):
            for marker in markers:
                marker_path = run_dir / marker
                marker_path.write_bytes(b"marker content must never be read")
                os.chmod(marker_path, 0o600)
                read_before = read_called["count"]

                with self.assertRaises(PendingColdAdoptionError):
                    assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

                self.assertEqual(read_called["count"], read_before)
                marker_path.unlink()

    def test_missing_runs_and_invalid_state_root(self) -> None:
        self.runs_dir.rmdir()
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

        os.chmod(self.state_root, 0o755)
        self.runs_dir.mkdir(mode=0o700)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        os.chmod(self.state_root, 0o700)

        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(Path("relative/path"))

    def test_runs_dir_bad_mode_and_symlink_rejection(self) -> None:
        os.chmod(self.runs_dir, 0o755)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        os.chmod(self.runs_dir, 0o700)

        self.runs_dir.rmdir()
        real_runs = self.state_root / "real_runs"
        real_runs.mkdir(mode=0o700)
        self.runs_dir.symlink_to(real_runs)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        self.runs_dir.unlink()
        real_runs.rmdir()
        self.runs_dir.mkdir(mode=0o700)

    def test_run_entry_validation_and_topology_rejection(self) -> None:
        run_invalid_name = self.runs_dir / "not-a-valid-run-name"
        run_invalid_name.mkdir(mode=0o700)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        run_invalid_name.rmdir()

        run_file = self.runs_dir / VALID_RUN_1
        run_file.write_bytes(b"file instead of dir")
        os.chmod(run_file, 0o600)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        run_file.unlink()

        real_target = self.state_root / "outside_run"
        real_target.mkdir(mode=0o700)
        symlink_run = self.runs_dir / VALID_RUN_1
        symlink_run.symlink_to(real_target)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        symlink_run.unlink()
        real_target.rmdir()

        run_bad_mode = self.runs_dir / VALID_RUN_1
        run_bad_mode.mkdir(mode=0o777)
        os.chmod(run_bad_mode, 0o777)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        run_bad_mode.rmdir()

    def test_run_children_nested_dir_symlink_special_and_mode_rejection(self) -> None:
        run_dir = self._create_historical_run(VALID_RUN_1, 0o700)

        nested = run_dir / "nested_dir"
        nested.mkdir(mode=0o700)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        nested.rmdir()

        symlink_child = run_dir / "status.json.link"
        symlink_child.symlink_to(run_dir / "status.json")
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        symlink_child.unlink()

        fifo_child = run_dir / "fifo_special"
        try:
            os.mkfifo(str(fifo_child))
            with self.assertRaises(PendingColdAdoptionError):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
            fifo_child.unlink()
        except OSError:
            pass

        setuid_child = run_dir / "setuid_file"
        setuid_child.write_bytes(b"payload")
        os.chmod(setuid_child, 0o4600)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)
        setuid_child.unlink()

    def test_private_qoder_state_is_admitted_with_strict_metadata(self) -> None:
        run_dir = self._create_historical_run(
            "cycle-0008-20260917T010203Z-123456", 0o700
        )
        qoder_state = run_dir / "coordinator-qoder-state"
        qoder_state.mkdir(mode=0o700)
        (qoder_state / "provider-internal").mkdir(mode=0o700)
        self.assertIsNone(assert_no_pending_cold_adoption_artifacts_locked(self.state_root))

        os.chmod(qoder_state, 0o755)
        with self.assertRaises(PendingColdAdoptionError):
            assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

    def test_only_closed_runtime_directory_names_are_admitted(self) -> None:
        run_dir = self._create_historical_run(
            "cycle-0008-20260917T010204Z-123456", 0o700
        )
        for name in sorted(pending_scan_mod.OPAQUE_RUN_DIRECTORIES):
            nested = run_dir / name
            nested.mkdir(mode=0o700)
            self.assertIsNone(assert_no_pending_cold_adoption_artifacts_locked(self.state_root))
            nested.rmdir()

    def test_bounded_scandir_caps_and_early_abort(self) -> None:
        self._create_historical_run(VALID_RUN_1, 0o700)

        class MockDirEntry:
            def __init__(self, name: str) -> None:
                self.name = name

        class TrackingScandirIterator:
            def __init__(self, names: list[str]) -> None:
                self._names = names
                self._idx = 0
                self.yielded_count = 0
                self.closed = False

            def __iter__(self) -> TrackingScandirIterator:
                return self

            def __next__(self) -> MockDirEntry:
                if self.closed or self._idx >= len(self._names):
                    raise StopIteration
                entry = MockDirEntry(self._names[self._idx])
                self._idx += 1
                self.yielded_count += 1
                return entry

            def close(self) -> None:
                self.closed = True

        runs_tracker = TrackingScandirIterator([f"cycle-{i:04d}-20260929T120000Z-1" for i in range(1000)])
        real_scandir = os.scandir

        def capped_runs_scandir(fd: int) -> Any:
            try:
                st = os.fstat(fd)
                runs_st = os.stat(self.runs_dir)
                if (st.st_dev, st.st_ino) == (runs_st.st_dev, runs_st.st_ino):
                    return runs_tracker
            except OSError:
                pass
            return real_scandir(fd)

        with patch("os.scandir", side_effect=capped_runs_scandir):
            with self.assertRaises(PendingColdAdoptionError):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

        self.assertEqual(runs_tracker.yielded_count, MAX_RUNS_ENTRIES + 1)
        self.assertTrue(runs_tracker.closed)

        child_tracker = TrackingScandirIterator([f"file_{i}.txt" for i in range(500)])

        def capped_child_scandir(fd: int) -> Any:
            try:
                st = os.fstat(fd)
                run_st = os.stat(self.runs_dir / VALID_RUN_1)
                if (st.st_dev, st.st_ino) == (run_st.st_dev, run_st.st_ino):
                    return child_tracker
            except OSError:
                pass
            return real_scandir(fd)

        with patch("os.scandir", side_effect=capped_child_scandir):
            with self.assertRaises(PendingColdAdoptionError):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

        self.assertEqual(child_tracker.yielded_count, MAX_RUN_CHILD_ENTRIES + 1)
        self.assertTrue(child_tracker.closed)

    def test_scandir_close_failure_and_interrupt_propagation(self) -> None:
        self._create_historical_run(VALID_RUN_1, 0o700)

        class FaultyCloseIterator:
            def __init__(self, names: list[str]) -> None:
                self._names = names
                self._idx = 0

            def __iter__(self) -> FaultyCloseIterator:
                return self

            def __next__(self) -> Any:
                if self._idx >= len(self._names):
                    raise StopIteration
                name = self._names[self._idx]
                self._idx += 1

                class _Entry:
                    pass

                e = _Entry()
                e.name = name
                return e

            def close(self) -> None:
                raise OSError("Simulated scandir close error")

        real_scandir = os.scandir

        def faulty_scandir(fd: int) -> Any:
            try:
                st = os.fstat(fd)
                runs_st = os.stat(self.runs_dir)
                if (st.st_dev, st.st_ino) == (runs_st.st_dev, runs_st.st_ino):
                    return FaultyCloseIterator([VALID_RUN_1])
            except OSError:
                pass
            return real_scandir(fd)

        with patch("os.scandir", side_effect=faulty_scandir):
            with self.assertRaises(PendingColdAdoptionError):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

        class InterruptIterator:
            def __iter__(self) -> InterruptIterator:
                return self

            def __next__(self) -> Any:
                raise KeyboardInterrupt("Interrupt during scandir iteration")

            def close(self) -> None:
                raise OSError("Close error during interrupt")

        def interrupt_scandir(fd: int) -> Any:
            try:
                st = os.fstat(fd)
                runs_st = os.stat(self.runs_dir)
                if (st.st_dev, st.st_ino) == (runs_st.st_dev, runs_st.st_ino):
                    return InterruptIterator()
            except OSError:
                pass
            return real_scandir(fd)

        with patch("os.scandir", side_effect=interrupt_scandir):
            with self.assertRaises(KeyboardInterrupt):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

    def test_metadata_drift_and_owner_mismatch(self) -> None:
        self._create_historical_run(VALID_RUN_1, 0o700)

        with patch("os.geteuid", return_value=os.geteuid() + 999):
            with self.assertRaises(PendingColdAdoptionError):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

        real_fstat = os.fstat
        drift_counter = {"count": 0}

        def drifting_fstat(fd: int) -> os.stat_result:
            res = real_fstat(fd)
            drift_counter["count"] += 1
            if drift_counter["count"] > 6:
                return os.stat_result((
                    res.st_mode, res.st_ino, res.st_dev, res.st_nlink,
                    res.st_uid, res.st_gid, res.st_size,
                    res.st_atime, res.st_mtime, res.st_ctime + 1000,
                ))
            return res

        with patch("os.fstat", side_effect=drifting_fstat):
            with self.assertRaises(PendingColdAdoptionError):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

    def test_interrupt_and_close_fault_propagation(self) -> None:
        self._create_historical_run(VALID_RUN_1, 0o700)

        with patch("os.scandir", side_effect=KeyboardInterrupt("Interrupt during scandir open")):
            with self.assertRaises(KeyboardInterrupt):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

        with patch("os.scandir", side_effect=SystemExit(2)):
            with self.assertRaises(SystemExit):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

        real_close = os.close

        def faulty_close(fd: int) -> None:
            real_close(fd)
            raise OSError("Disk descriptor close error")

        with patch("os.close", side_effect=faulty_close):
            with self.assertRaises(PendingColdAdoptionError):
                assert_no_pending_cold_adoption_artifacts_locked(self.state_root)

        def faulty_close_on_interrupt(fd: int) -> None:
            real_close(fd)
            raise OSError("Close error during interrupt cleanup")

        with patch("os.scandir", side_effect=KeyboardInterrupt("Abort")):
            with patch("os.close", side_effect=faulty_close_on_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    assert_no_pending_cold_adoption_artifacts_locked(self.state_root)


if __name__ == "__main__":
    unittest.main()
