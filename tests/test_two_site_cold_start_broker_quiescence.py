#!/usr/bin/env python3
"""Hermetic unit tests for dormant cold-adoption broker quiescence under AE03."""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import fcntl
import json
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    import scripts.two_site_provider_broker as broker
    from scripts.two_site_cold_start_broker_quiescence import (
        CLAIMED_DIRNAME,
        CLIENT_LOCK_FILENAME,
        ColdBrokerQuiescenceError,
        ColdBrokerQuiescencePostBodyError,
        REQUESTS_DIRNAME,
        STATUS_FILENAME,
        hold_idle_broker_client_lock_for_cold_adoption,
    )
except ImportError:
    import two_site_provider_broker as broker  # type: ignore[no-redef]
    from two_site_cold_start_broker_quiescence import (  # type: ignore[no-redef]
        CLAIMED_DIRNAME,
        CLIENT_LOCK_FILENAME,
        ColdBrokerQuiescenceError,
        ColdBrokerQuiescencePostBodyError,
        REQUESTS_DIRNAME,
        STATUS_FILENAME,
        hold_idle_broker_client_lock_for_cold_adoption,
    )


class TestTwoSiteColdStartBrokerQuiescence(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="two-site-test-broker-quiescence-")
        self.root = Path(self._tmp.name).resolve(strict=True)
        os.chmod(self.root, 0o700)

        state_rel = broker.STATE_REL
        if isinstance(state_rel, str):
            state_rel = Path(state_rel)
        self.state_root = self.root / state_rel
        self.state_root.mkdir(parents=True, mode=0o700)
        os.chmod(self.state_root, 0o700)

        self.requests_dir = self.state_root / REQUESTS_DIRNAME
        self.requests_dir.mkdir(mode=0o700)
        os.chmod(self.requests_dir, 0o700)

        self.claimed_dir = self.state_root / CLAIMED_DIRNAME
        self.claimed_dir.mkdir(mode=0o700)
        os.chmod(self.claimed_dir, 0o700)

        self.lock_path = self.state_root / CLIENT_LOCK_FILENAME
        self.lock_path.write_bytes(b"")
        os.chmod(self.lock_path, 0o600)

        self.status_path = self.state_root / STATUS_FILENAME
        self.started_at = time.time() - 100.0
        self._write_valid_status(started_at=self.started_at)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_valid_status(
        self,
        *,
        state: str = "IDLE",
        schema_version: str = "1.0",
        pid: int = 12345,
        started_at: float | None = None,
        updated_at: float | None = None,
    ) -> None:
        now = time.time()
        payload = {
            "schema_version": schema_version,
            "state": state,
            "pid": pid,
            "started_at": self.started_at if started_at is None else started_at,
            "updated_at": now - 1.0 if updated_at is None else updated_at,
        }
        self.status_path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(self.status_path, 0o600)

    def _atomic_replace_status(
        self,
        *,
        state: str = "IDLE",
        schema_version: str = "1.0",
        pid: int = 12345,
        started_at: float | None = None,
        updated_at: float | None = None,
    ) -> None:
        now = time.time()
        payload = {
            "schema_version": schema_version,
            "state": state,
            "pid": pid,
            "started_at": self.started_at if started_at is None else started_at,
            "updated_at": now - 0.5 if updated_at is None else updated_at,
        }
        tmp_path = self.state_root / ".status.json.tmp"
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self.status_path)

    def _snapshot_tree(self) -> dict[str, tuple[int, int, bytes | None]]:
        tree: dict[str, tuple[int, int, bytes | None]] = {}
        for root, dirs, files in os.walk(self.state_root):
            for d in sorted(dirs):
                dp = Path(root) / d
                st = dp.stat()
                tree[str(dp)] = (st.st_mode, st.st_size, None)
            for f in sorted(files):
                if f == CLIENT_LOCK_FILENAME:
                    continue
                fp = Path(root) / f
                st = fp.stat()
                tree[str(fp)] = (st.st_mode, st.st_size, fp.read_bytes())
        return tree

    def test_happy_path_holds_lock_and_zero_mutation(self) -> None:
        responses_dir = self.state_root / "responses"
        responses_dir.mkdir(mode=0o700)
        (responses_dir / "historical-001.json").write_text('{"done": true}', encoding="utf-8")
        os.chmod(responses_dir / "historical-001.json", 0o600)

        snapshot_before = self._snapshot_tree()
        body_executed = False

        with hold_idle_broker_client_lock_for_cold_adoption(self.root):
            body_executed = True
            test_fd = os.open(str(self.lock_path), os.O_RDWR)
            try:
                with self.assertRaises(OSError) as ctx:
                    fcntl.flock(test_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertIn(ctx.exception.errno, (11, 35))
            finally:
                os.close(test_fd)

        self.assertTrue(body_executed)
        snapshot_after = self._snapshot_tree()
        self.assertEqual(snapshot_before, snapshot_after)

    def test_lock_and_state_root_pre_check_failures(self) -> None:
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption("not_a_path"):  # type: ignore[arg-type]
                pass

        self.lock_path.unlink()
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass

        fake_target = self.state_root / "fake_lock_target"
        fake_target.write_bytes(b"")
        self.lock_path.symlink_to(fake_target)
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass
        self.lock_path.unlink()
        fake_target.unlink()

        self.lock_path.write_bytes(b"")
        os.chmod(self.lock_path, 0o644)
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass

        os.chmod(self.lock_path, 0o600)
        hardlink_lock = self.state_root / "client.lock.hard"
        os.link(self.lock_path, hardlink_lock)
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass
        hardlink_lock.unlink()

        os.chmod(self.state_root, 0o755)
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass
        os.chmod(self.state_root, 0o700)

        with patch("os.geteuid", return_value=os.geteuid() + 500):
            with self.assertRaises(ColdBrokerQuiescenceError):
                with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                    pass

    def test_queue_directory_pre_check_failures(self) -> None:
        self.requests_dir.rmdir()
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass
        self.requests_dir.mkdir(mode=0o700)

        self.claimed_dir.rmdir()
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass
        self.claimed_dir.mkdir(mode=0o700)

        req_file = self.requests_dir / "pending_req.json"
        req_file.write_bytes(b"{}")
        os.chmod(req_file, 0o600)
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass
        req_file.unlink()

        claim_file = self.claimed_dir / ".partial_claim"
        claim_file.write_bytes(b"{}")
        os.chmod(claim_file, 0o600)
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass
        claim_file.unlink()

    def test_status_pre_check_failures(self) -> None:
        self.status_path.unlink()
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass

        self._write_valid_status(state="RUNNING")
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass

        self._write_valid_status(schema_version="2.0")
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass

        self._write_valid_status(pid=-1)
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass

        self._write_valid_status(started_at=float("nan"))
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass

        self._write_valid_status(updated_at=time.time() - (broker.STATUS_MAX_AGE_SECONDS + 10.0))
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass

        self._write_valid_status(updated_at=time.time() + 60.0)
        with self.assertRaises(ColdBrokerQuiescenceError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                pass

    def test_lock_contention_fails_closed(self) -> None:
        holder_fd = os.open(str(self.lock_path), os.O_RDWR)
        try:
            fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ColdBrokerQuiescenceError):
                with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                    pass
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_post_body_failures_raise_explicit_distinguishable_error(self) -> None:
        with self.assertRaises(ColdBrokerQuiescencePostBodyError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                new_req = self.requests_dir / "drift_req.json"
                new_req.write_bytes(b"{}")
                os.chmod(new_req, 0o600)
        (self.requests_dir / "drift_req.json").unlink()

        with self.assertRaises(ColdBrokerQuiescencePostBodyError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                new_claim = self.claimed_dir / "drift_claim.json"
                new_claim.write_bytes(b"{}")
                os.chmod(new_claim, 0o600)
        (self.claimed_dir / "drift_claim.json").unlink()

        with self.assertRaises(ColdBrokerQuiescencePostBodyError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                self._write_valid_status(pid=99999)
        self._write_valid_status(pid=12345)

        with self.assertRaises(ColdBrokerQuiescencePostBodyError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                self._write_valid_status(started_at=time.time() - 5.0)
        self._write_valid_status(started_at=self.started_at)

        with self.assertRaises(ColdBrokerQuiescencePostBodyError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                self._write_valid_status(state="RUNNING")
        self._write_valid_status()

        with self.assertRaises(ColdBrokerQuiescencePostBodyError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                os.chmod(self.requests_dir, 0o755)
        os.chmod(self.requests_dir, 0o700)

        with self.assertRaises(ColdBrokerQuiescencePostBodyError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                os.chmod(self.state_root, 0o755)
        os.chmod(self.state_root, 0o700)

        self.assertTrue(issubclass(ColdBrokerQuiescencePostBodyError, ColdBrokerQuiescenceError))

    def test_legitimate_atomic_status_replacement_under_lock(self) -> None:
        st_root_before = os.lstat(self.state_root)
        with hold_idle_broker_client_lock_for_cold_adoption(self.root):
            self._atomic_replace_status(started_at=self.started_at, updated_at=time.time())
            st_now = os.lstat(self.state_root)
            if st_now.st_mtime_ns == st_root_before.st_mtime_ns:
                os.utime(self.state_root, ns=(st_now.st_atime_ns, st_now.st_mtime_ns + 1_000_000))
        st_root_after = os.lstat(self.state_root)
        self.assertNotEqual(st_root_before.st_mtime_ns, st_root_after.st_mtime_ns)

        saved_state = self.root / "saved_state_root"
        alt_state = self.root / "alt_state_root"
        try:
            with self.assertRaises(ColdBrokerQuiescencePostBodyError):
                with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                    alt_state.mkdir(mode=0o700)
                    self.state_root.rename(saved_state)
                    alt_state.rename(self.state_root)
        finally:
            if self.state_root.exists() and saved_state.exists():
                self.state_root.rmdir()
                saved_state.rename(self.state_root)
            elif saved_state.exists():
                saved_state.rename(self.state_root)

    def test_unsafe_status_replacement_post_body_fails(self) -> None:
        with self.assertRaises(ColdBrokerQuiescencePostBodyError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                os.chmod(self.status_path, 0o644)
        os.chmod(self.status_path, 0o600)

    def test_client_lock_replacement_during_hold_fails_closed(self) -> None:
        with self.assertRaises(ColdBrokerQuiescencePostBodyError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                self.lock_path.unlink()
                self.lock_path.write_bytes(b"")
                os.chmod(self.lock_path, 0o600)

    def test_lseek_failure_before_post_body_scan_fails_closed(self) -> None:
        real_lseek = os.lseek
        in_body = False

        def failing_lseek(fd: int, pos: int, how: int) -> int:
            if in_body:
                raise OSError("Injected lseek rewind error")
            return real_lseek(fd, pos, how)

        with patch("os.lseek", side_effect=failing_lseek):
            with self.assertRaises(ColdBrokerQuiescencePostBodyError):
                with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                    in_body = True

    def test_all_descriptors_attempted_on_close_failure(self) -> None:
        real_close = os.close
        in_cleanup = False
        closed_during_cleanup: list[int] = []
        fail_first = {"done": False}

        def close_tracking(fd: int) -> None:
            is_dir = False
            try:
                st = os.fstat(fd)
                is_dir = stat.S_ISDIR(st.st_mode)
            except OSError:
                pass

            if in_cleanup and is_dir:
                closed_during_cleanup.append(fd)
                real_close(fd)
                if not fail_first["done"]:
                    fail_first["done"] = True
                    raise OSError("Injected directory descriptor close failure")
            else:
                real_close(fd)

        with patch("os.close", side_effect=close_tracking):
            with self.assertRaises(ColdBrokerQuiescencePostBodyError):
                with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                    in_cleanup = True

        self.assertEqual(len(closed_during_cleanup), 3)
        self.assertTrue(fail_first["done"])

    def test_interrupt_and_exception_handling(self) -> None:
        class CustomCallerError(Exception):
            pass

        with self.assertRaises(CustomCallerError):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                raise CustomCallerError("caller error must not be masked")

        with self.assertRaises(KeyboardInterrupt):
            with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                raise KeyboardInterrupt("caller interrupt must propagate")

        real_close = os.close
        in_body = False

        def faulty_close_in_body(fd: int) -> None:
            real_close(fd)
            if in_body:
                raise OSError("Simulated close error during interrupt cleanup")

        with patch("os.close", side_effect=faulty_close_in_body):
            with self.assertRaises(KeyboardInterrupt):
                with hold_idle_broker_client_lock_for_cold_adoption(self.root):
                    in_body = True
                    raise KeyboardInterrupt("interrupt in body")


if __name__ == "__main__":
    unittest.main()
