#!/usr/bin/env python3
"""Hermetic unit tests for exact quota receipt reattachment and directory identity."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.two_site_provider_broker import (
    BrokerError,
    broker_paths,
    publish_terminal_quota_record,
    read_terminal_quota_for_exact_request,
)
from tests.test_two_site_provider_broker_terminal_record import (
    _setup_quota_roundtrip_fixture,
)


def _broker_tree_snapshot(
    root: Path,
) -> dict[str, tuple[int, int, int, int, int, int, int, int, bytes | None]]:
    snap: dict[str, tuple[int, int, int, int, int, int, int, int, bytes | None]] = {}
    local_dir = root / ".local"
    if not os.path.lexists(local_dir):
        return snap
    all_paths = [local_dir]

    def _on_walk_error(err: OSError) -> None:
        raise err

    for current_root, dirs, files in os.walk(
        local_dir, followlinks=False, onerror=_on_walk_error
    ):
        for name in dirs + files:
            all_paths.append(Path(current_root) / name)
    for p in sorted(all_paths, key=lambda x: str(x.relative_to(root))):
        st = os.lstat(p)
        content: bytes | None = None
        if stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode):
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(p, flags)
            try:
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                content = b"".join(chunks)
            finally:
                os.close(fd)
        snap[str(p.relative_to(root))] = (
            st.st_dev,
            st.st_ino,
            st.st_mode,
            st.st_uid,
            st.st_nlink,
            st.st_size,
            st.st_mtime_ns,
            st.st_ctime_ns,
            content,
        )
    return snap


class TestExactReattach(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name).resolve()
        broker_paths(self.root)

    def tearDown(self) -> None:
        self.test_dir.cleanup()

    def test_exact_reattach_absent_response_zero_mutation_success(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        response_path = state_root / "responses" / f"{request['request_id']}.json"
        self.assertFalse(response_path.exists())

        snap_before = _broker_tree_snapshot(self.root)
        returned_response = read_terminal_quota_for_exact_request(self.root, request)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertEqual(returned_response, response)
        self.assertFalse(response_path.exists())
        self.assertEqual(snap_before, snap_after)

    def test_exact_reattach_present_matching_response_zero_mutation_success(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        response_path = state_root / "responses" / f"{request['request_id']}.json"
        encoded_resp = json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        response_path.write_bytes(encoded_resp)
        os.chmod(response_path, 0o600)

        snap_before = _broker_tree_snapshot(self.root)
        returned_response = read_terminal_quota_for_exact_request(self.root, request)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertEqual(returned_response, response)
        self.assertTrue(response_path.is_file())
        self.assertEqual(snap_before, snap_after)

    def test_exact_reattach_adversarial_directory_swap_fails_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        responses_dir = state_root / "responses"
        conflicting_response = copy.deepcopy(response)
        conflicting_response["exit_code"] = 2
        conflicting_path = responses_dir / f"{request['request_id']}.json"
        conflicting_path.write_bytes(
            json.dumps(conflicting_response, sort_keys=True).encode("utf-8")
        )
        os.chmod(conflicting_path, 0o600)

        empty_replacement_dir = state_root / "empty_responses_swap"
        empty_replacement_dir.mkdir(mode=0o700)
        temp_swap_dir = state_root / "original_responses_pinned"

        responses_stat = os.lstat(responses_dir)
        real_os_stat = os.stat
        swap_triggered = False

        def hooked_stat(path, *args, **kwargs):
            nonlocal swap_triggered
            dir_fd = kwargs.get("dir_fd")
            if dir_fd is not None and not swap_triggered:
                try:
                    fd_stat = os.fstat(dir_fd)
                    if (fd_stat.st_dev, fd_stat.st_ino) == (responses_stat.st_dev, responses_stat.st_ino):
                        swap_triggered = True
                        responses_dir.rename(temp_swap_dir)
                        empty_replacement_dir.rename(responses_dir)
                except OSError:
                    pass
            return real_os_stat(path, *args, **kwargs)

        os.stat = hooked_stat
        try:
            with self.assertRaises(BrokerError) as ctx:
                read_terminal_quota_for_exact_request(self.root, request)
            self.assertTrue(swap_triggered)
            err_msg = str(ctx.exception).lower()
            self.assertTrue(
                "mismatch" in err_msg or "changed" in err_msg or "cannot" in err_msg or "unavailable" in err_msg
            )
        finally:
            os.stat = real_os_stat
            if temp_swap_dir.exists():
                if responses_dir.exists():
                    responses_dir.rename(empty_replacement_dir)
                temp_swap_dir.rename(responses_dir)

        # Assert no provider execution or request submission occurs
        requests_dir = state_root / "requests"
        claimed_dir = state_root / "claimed"
        self.assertEqual(len(list(requests_dir.glob("*.json"))), 0)
        self.assertEqual(len(list(claimed_dir.glob("*.json"))), 0)

    def test_exact_reattach_absent_responses_directory_stays_absent(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        responses_dir = state_root / "responses"
        responses_dir.rmdir()
        self.assertFalse(responses_dir.exists())

        snap_before = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertIn("unavailable", str(ctx.exception).lower())
        self.assertFalse(responses_dir.exists())
        self.assertEqual(snap_before, snap_after)

    def test_exact_reattach_invalid_responses_directory_mode_or_symlink(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        responses_dir = state_root / "responses"

        os.chmod(responses_dir, 0o755)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        self.assertIn("mode", str(ctx.exception).lower())
        os.chmod(responses_dir, 0o700)

        responses_dir.rmdir()
        real_dir = state_root / "real_responses"
        real_dir.mkdir(mode=0o700)
        responses_dir.symlink_to(real_dir, target_is_directory=True)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        self.assertIn("symlink", str(ctx.exception).lower())

    def test_exact_reattach_request_field_mismatches_fail_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        snap_before = _broker_tree_snapshot(self.root)
        for field, invalid_val in [
            ("schema_version", "2.0"),
            ("timeout", 5),
            ("source_head", "c" * 40),
            ("task_sha256", "d" * 64),
            ("controller_run_id", "cycle-bad"),
            ("created_at", float("nan")),
            ("created_at", True),
        ]:
            bad_req = copy.deepcopy(request)
            bad_req[field] = invalid_val
            with self.assertRaises(BrokerError):
                read_terminal_quota_for_exact_request(self.root, bad_req)

        snap_after = _broker_tree_snapshot(self.root)
        self.assertEqual(snap_before, snap_after)

    def test_exact_reattach_witness_drift_fails_closed(self) -> None:
        request, response, task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        task_path.write_bytes(b'{"task_id":"task-record-001","drift":true}')
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        self.assertIn("terminal quota response not detected", str(ctx.exception))

    def test_exact_reattach_conflicting_or_corrupt_response_fails_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        response_path = state_root / "responses" / f"{request['request_id']}.json"

        response_path.write_bytes(b"not json")
        os.chmod(response_path, 0o600)
        with self.assertRaises(BrokerError):
            read_terminal_quota_for_exact_request(self.root, request)

        conflicting = copy.deepcopy(response)
        conflicting["stdout"] = "mismatched stdout"
        response_path.write_bytes(
            json.dumps(conflicting, sort_keys=True).encode("utf-8")
        )
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        self.assertIn("does not match receipt", str(ctx.exception))

        os.chmod(response_path, 0o644)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        self.assertIn("mode", str(ctx.exception).lower())

        os.chmod(response_path, 0o600)
        link_path = state_root / "responses" / "hardlink.json"
        os.link(response_path, link_path)
        try:
            with self.assertRaises(BrokerError) as ctx:
                read_terminal_quota_for_exact_request(self.root, request)
            self.assertIn("link count", str(ctx.exception).lower())
        finally:
            link_path.unlink()

    def test_exact_reattach_symlink_response_file_fails_closed_zero_mutation(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        responses_dir = state_root / "responses"
        response_path = responses_dir / f"{request['request_id']}.json"

        encoded_resp = json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        response_path.write_bytes(encoded_resp)
        os.chmod(response_path, 0o600)

        owned_target = state_root / "owned_target.json"
        owned_target.write_bytes(encoded_resp)
        os.chmod(owned_target, 0o600)
        response_path.unlink()
        response_path.symlink_to(owned_target)

        snap_before = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertIn("regular file", str(ctx.exception).lower())
        self.assertEqual(snap_before, snap_after)
        self.assertTrue(response_path.is_symlink())

    def test_exact_reattach_created_at_integer_vs_float_exact_mismatch(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        int_created_at = 1700000000
        request["created_at"] = int_created_at
        self.assertIsInstance(request["created_at"], int)

        publish_terminal_quota_record(self.root, request, response)

        float_req = copy.deepcopy(request)
        float_req["created_at"] = float(int_created_at)
        self.assertIsInstance(float_req["created_at"], float)
        self.assertEqual(request["created_at"], float_req["created_at"])
        self.assertIsNot(type(request["created_at"]), type(float_req["created_at"]))

        snap_before = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, float_req)
        snap_after = _broker_tree_snapshot(self.root)

        err_msg = str(ctx.exception).lower()
        self.assertTrue(
            "does not match receipt request" in err_msg or "digest mismatch" in err_msg
        )
        self.assertEqual(snap_before, snap_after)

    def test_exact_reattach_missing_receipt_fails_closed_no_creation(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        terminal_quota_dir = state_root / "terminal-quota"
        self.assertFalse(terminal_quota_dir.exists())

        snap_before = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertIn("unavailable", str(ctx.exception).lower())
        self.assertFalse(terminal_quota_dir.exists())
        self.assertEqual(snap_before, snap_after)

        terminal_quota_dir.mkdir(mode=0o700)
        snap_before2 = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        snap_after2 = _broker_tree_snapshot(self.root)

        err_msg = str(ctx.exception).lower()
        self.assertTrue("unavailable" in err_msg or "not found" in err_msg)
        self.assertEqual(snap_before2, snap_after2)

    def test_exact_reattach_corrupt_receipt_fails_closed_no_creation(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        receipt_path = state_root / "terminal-quota" / f"{request['request_id']}.json"
        self.assertTrue(receipt_path.is_file())

        receipt_path.write_bytes(b"not valid json")
        os.chmod(receipt_path, 0o600)

        snap_before = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertIn("not valid json", str(ctx.exception).lower())
        self.assertEqual(snap_before, snap_after)

        receipt_path.write_bytes(b'{"schema_version":"1.0","corrupted":true}')
        os.chmod(receipt_path, 0o600)

        snap_before2 = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError):
            read_terminal_quota_for_exact_request(self.root, request)
        snap_after2 = _broker_tree_snapshot(self.root)

        self.assertEqual(snap_before2, snap_after2)

    def test_exact_reattach_status_witness_drift_post_publish_fails_closed(self) -> None:
        request, response, _task_path, status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        status_path.write_bytes(b'{"run_id":"test-run-record-worker-0","drifted":true}')
        os.chmod(status_path, 0o600)

        snap_before = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_for_exact_request(self.root, request)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertIn("terminal quota response not detected", str(ctx.exception))
        self.assertEqual(snap_before, snap_after)

    def test_broker_tree_snapshot_propagates_targeted_scandir_os_error(self) -> None:
        target_dir = (
            self.root / ".local" / "two-site-refactor" / "provider-broker" / "responses"
        ).resolve()
        self.assertTrue(target_dir.is_dir())

        injected_error = OSError(13, "Permission denied: simulated scandir failure")
        real_scandir = os.scandir
        intercepted = False

        def hooked_scandir(path=None):
            nonlocal intercepted
            if path is not None and Path(path).resolve() == target_dir:
                intercepted = True
                raise injected_error
            return real_scandir(path)

        os.scandir = hooked_scandir
        try:
            with self.assertRaises(OSError) as ctx:
                _broker_tree_snapshot(self.root)
            self.assertTrue(intercepted)
            self.assertIs(ctx.exception, injected_error)
        finally:
            os.scandir = real_scandir


if __name__ == "__main__":
    unittest.main()
