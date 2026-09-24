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


def _broker_tree_snapshot(root: Path) -> dict[str, tuple[int, int, int, int, int, int, int]]:
    snap: dict[str, tuple[int, int, int, int, int, int, int]] = {}
    local_dir = root / ".local"
    if not local_dir.exists():
        return snap
    for p in sorted(local_dir.rglob("*")):
        try:
            st = os.lstat(p)
            snap[str(p.relative_to(root))] = (
                st.st_dev,
                st.st_ino,
                st.st_mode,
                st.st_uid,
                st.st_size,
                st.st_mtime_ns,
                st.st_ctime_ns,
            )
        except OSError:
            pass
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


if __name__ == "__main__":
    unittest.main()
