#!/usr/bin/env python3
"""Hermetic unit tests for bounded read-only receipt discovery."""
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

import scripts.two_site_provider_broker as broker_mod
from scripts.two_site_provider_broker import (
    BrokerError,
    broker_paths,
    find_terminal_quota_for_reservation,
    publish_terminal_quota_record,
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


def _reservation_from_request(req: dict) -> dict[str, str]:
    return {
        "controller_run_id": req["controller_run_id"],
        "run_prefix": req["run_prefix"],
        "task_path": req["task_path"],
        "task_sha256": req["task_sha256"],
        "source_head": req["source_head"],
        "assignment_id": req["assignment_id"],
        "assignment_sha256": req["assignment_sha256"],
    }


class TestReceiptDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name).resolve()
        broker_paths(self.root)

    def tearDown(self) -> None:
        self.test_dir.cleanup()

    def test_discovery_unique_reservation_zero_mutation_success(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)

        snap_before = _broker_tree_snapshot(self.root)
        returned = find_terminal_quota_for_reservation(self.root, reservation)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertEqual(returned, response)
        self.assertEqual(snap_before, snap_after)

    def test_discovery_no_matching_reservation_fails_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)
        reservation["controller_run_id"] = "cycle-0002-20260924T120000Z-2"

        snap_before = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError) as ctx:
            find_terminal_quota_for_reservation(self.root, reservation)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertIn("no terminal quota record matched", str(ctx.exception).lower())
        self.assertEqual(snap_before, snap_after)

    def test_discovery_duplicate_matching_reservation_fails_closed(self) -> None:
        request1, response1, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        receipt1 = publish_terminal_quota_record(self.root, request1, response1)
        reservation = _reservation_from_request(request1)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        tq_dir = state_root / "terminal-quota"
        dup_id = "8" * 32 if request1["request_id"] != "8" * 32 else "9" * 32
        dup_path = tq_dir / f"{dup_id}.json"
        receipt2 = copy.deepcopy(receipt1)
        receipt2["request"]["request_id"] = dup_id
        receipt2["response"]["request_id"] = dup_id
        req2_canon = json.dumps(
            receipt2["request"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        resp2_canon = json.dumps(
            receipt2["response"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        import hashlib
        receipt2["request_sha256"] = hashlib.sha256(req2_canon).hexdigest()
        receipt2["response_sha256"] = hashlib.sha256(resp2_canon).hexdigest()
        dup_path.write_bytes(
            json.dumps(receipt2, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        os.chmod(dup_path, 0o600)

        snap_before = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError) as ctx:
            find_terminal_quota_for_reservation(self.root, reservation)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertIn("multiple terminal quota records matched", str(ctx.exception).lower())
        self.assertEqual(snap_before, snap_after)

    def test_discovery_with_unrelated_valid_entry_success(self) -> None:
        request1, response1, _task_path, status_path1 = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request1, response1)
        res1 = _reservation_from_request(request1)

        run_id2 = "otherrun-worker-0"
        proposal_dir2 = self.root / ".local/two-site-refactor/proposals" / run_id2
        proposal_dir2.mkdir(parents=True, mode=0o700)
        orig_status = json.loads(status_path1.read_text("utf-8"))
        status2 = copy.deepcopy(orig_status)
        status2["run_id"] = run_id2
        status2_bytes = json.dumps(status2, sort_keys=True).encode("utf-8")
        status2_path = proposal_dir2 / "status.json"
        status2_path.write_bytes(status2_bytes)
        os.chmod(status2_path, 0o600)

        import hashlib
        status2_sha = hashlib.sha256(status2_bytes).hexdigest()
        request2 = copy.deepcopy(request1)
        request2["request_id"] = "f" * 32 if request1["request_id"] != "f" * 32 else "e" * 32
        request2["run_prefix"] = "otherrun"
        request2["controller_run_id"] = "cycle-0002-20260924T120000Z-2"
        response2 = copy.deepcopy(response1)
        response2["request_id"] = request2["request_id"]
        response2["run_prefix"] = "otherrun"
        stdout_payload = json.loads(response2["stdout"])
        stdout_payload["quota_attempt"]["run_id"] = run_id2
        stdout_payload["quota_attempt"]["status_sha256"] = status2_sha
        response2["stdout"] = json.dumps(stdout_payload, sort_keys=True)

        publish_terminal_quota_record(self.root, request2, response2)
        res2 = _reservation_from_request(request2)

        snap_before = _broker_tree_snapshot(self.root)
        ret1 = find_terminal_quota_for_reservation(self.root, res1)
        ret2 = find_terminal_quota_for_reservation(self.root, res2)
        snap_after = _broker_tree_snapshot(self.root)

        self.assertEqual(ret1, response1)
        self.assertEqual(ret2, response2)
        self.assertEqual(snap_before, snap_after)

    def test_discovery_unrelated_corrupt_entry_hard_stops(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        corrupt_id = "e" * 32 if request["request_id"] != "e" * 32 else "d" * 32
        corrupt_path = state_root / "terminal-quota" / f"{corrupt_id}.json"
        corrupt_path.write_bytes(b"not json at all")
        os.chmod(corrupt_path, 0o600)

        snap_before = _broker_tree_snapshot(self.root)
        with self.assertRaises(BrokerError):
            find_terminal_quota_for_reservation(self.root, reservation)
        snap_after = _broker_tree_snapshot(self.root)
        self.assertEqual(snap_before, snap_after)

    def test_discovery_missing_symlink_incorrect_mode_directory_fails_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)
        state_root = self.root / ".local/two-site-refactor/provider-broker"
        tq_dir = state_root / "terminal-quota"

        os.chmod(tq_dir, 0o755)
        with self.assertRaises(BrokerError) as ctx:
            find_terminal_quota_for_reservation(self.root, reservation)
        self.assertIn("mode", str(ctx.exception).lower())
        os.chmod(tq_dir, 0o700)

        receipt_file = tq_dir / f"{request['request_id']}.json"
        receipt_bytes = receipt_file.read_bytes()
        receipt_file.unlink()
        tq_dir.rmdir()

        with self.assertRaises(BrokerError) as ctx:
            find_terminal_quota_for_reservation(self.root, reservation)
        self.assertIn("unavailable", str(ctx.exception).lower())

        real_target = state_root / "real_terminal_quota"
        real_target.mkdir(mode=0o700)
        (real_target / f"{request['request_id']}.json").write_bytes(receipt_bytes)
        os.chmod(real_target / f"{request['request_id']}.json", 0o600)
        tq_dir.symlink_to(real_target, target_is_directory=True)

        with self.assertRaises(BrokerError) as ctx:
            find_terminal_quota_for_reservation(self.root, reservation)
        self.assertIn("symlink", str(ctx.exception).lower())

    def test_discovery_record_symlink_hardlink_mode_fails_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)
        state_root = self.root / ".local/two-site-refactor/provider-broker"
        tq_dir = state_root / "terminal-quota"
        record_path = tq_dir / f"{request['request_id']}.json"

        os.chmod(record_path, 0o644)
        with self.assertRaises(BrokerError) as ctx:
            find_terminal_quota_for_reservation(self.root, reservation)
        self.assertIn("mode", str(ctx.exception).lower())
        os.chmod(record_path, 0o600)

        link_id = "b" * 32 if request["request_id"] != "b" * 32 else "c" * 32
        link_path = tq_dir / f"{link_id}.json"
        os.link(record_path, link_path)
        try:
            with self.assertRaises(BrokerError) as ctx:
                find_terminal_quota_for_reservation(self.root, reservation)
            self.assertIn("link count", str(ctx.exception).lower())
        finally:
            link_path.unlink()

        content = record_path.read_bytes()
        record_path.unlink()
        outside_target = state_root / "outside.json"
        outside_target.write_bytes(content)
        os.chmod(outside_target, 0o600)
        record_path.symlink_to(outside_target)
        try:
            with self.assertRaises(BrokerError) as ctx:
                find_terminal_quota_for_reservation(self.root, reservation)
            self.assertIn("symlink", str(ctx.exception).lower())
        finally:
            record_path.unlink()
            outside_target.unlink()
            record_path.write_bytes(content)
            os.chmod(record_path, 0o600)

    def test_discovery_directory_overflow_65_entries_fails_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)
        state_root = self.root / ".local/two-site-refactor/provider-broker"
        tq_dir = state_root / "terminal-quota"

        created: list[Path] = []
        for i in range(1, 66):
            name = f"{i:032x}.json"
            p = tq_dir / name
            if not p.exists():
                p.write_bytes(b'{"test":1}')
                os.chmod(p, 0o600)
                created.append(p)

        try:
            with self.assertRaises(BrokerError) as ctx:
                find_terminal_quota_for_reservation(self.root, reservation)
            self.assertIn("limit", str(ctx.exception).lower())
        finally:
            for p in created:
                p.unlink(missing_ok=True)

    def test_discovery_conflicting_response_fails_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        response_path = state_root / "responses" / f"{request['request_id']}.json"
        bad_resp = copy.deepcopy(response)
        bad_resp["exit_code"] = 2
        response_path.write_bytes(json.dumps(bad_resp, sort_keys=True).encode("utf-8"))
        os.chmod(response_path, 0o600)

        with self.assertRaises(BrokerError) as ctx:
            find_terminal_quota_for_reservation(self.root, reservation)
        self.assertIn("does not match receipt", str(ctx.exception).lower())

    def test_discovery_task_and_status_witness_drift_fails_closed(self) -> None:
        request, response, task_path, status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)

        orig_task = task_path.read_bytes()
        task_path.write_bytes(b'{"task_id":"task-record-001","drift":true}')
        try:
            with self.assertRaises(BrokerError) as ctx:
                find_terminal_quota_for_reservation(self.root, reservation)
            self.assertIn("terminal quota response not detected", str(ctx.exception).lower())
        finally:
            task_path.write_bytes(orig_task)

        orig_status = status_path.read_bytes()
        status_path.write_bytes(b'{"run_id":"test-run-record-worker-0","drift":true}')
        try:
            with self.assertRaises(BrokerError) as ctx:
                find_terminal_quota_for_reservation(self.root, reservation)
            self.assertIn("terminal quota response not detected", str(ctx.exception).lower())
        finally:
            status_path.write_bytes(orig_status)

    def test_discovery_directory_path_swap_fails_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        tq_dir = state_root / "terminal-quota"
        swap_dir = state_root / "empty_swap_dir"
        swap_dir.mkdir(mode=0o700)
        temp_pinned = state_root / "tq_pinned"

        orig_c4b1 = broker_mod.read_terminal_quota_for_exact_request
        swapped = False

        def hooked_c4b1(root_arg, req_arg):
            nonlocal swapped
            swapped = True
            tq_dir.rename(temp_pinned)
            swap_dir.rename(tq_dir)
            return orig_c4b1(root_arg, req_arg)

        broker_mod.read_terminal_quota_for_exact_request = hooked_c4b1
        try:
            with self.assertRaises(BrokerError) as ctx:
                find_terminal_quota_for_reservation(self.root, reservation)
            self.assertTrue(swapped)
            err = str(ctx.exception).lower()
            self.assertTrue("changed" in err or "identity" in err or "unavailable" in err)
        finally:
            broker_mod.read_terminal_quota_for_exact_request = orig_c4b1
            if temp_pinned.exists():
                if tq_dir.exists():
                    tq_dir.rename(swap_dir)
                temp_pinned.rename(tq_dir)

    def test_discovery_target_receipt_replacement_new_inode_fails_closed(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)
        reservation = _reservation_from_request(request)

        state_root = self.root / ".local/two-site-refactor/provider-broker"
        record_path = state_root / "terminal-quota" / f"{request['request_id']}.json"
        exact_bytes = record_path.read_bytes()

        orig_c4b1 = broker_mod.read_terminal_quota_for_exact_request
        replaced = False

        def hooked_c4b1(root_arg, req_arg):
            nonlocal replaced
            replaced = True
            record_path.unlink()
            record_path.write_bytes(exact_bytes)
            os.chmod(record_path, 0o600)
            return orig_c4b1(root_arg, req_arg)

        broker_mod.read_terminal_quota_for_exact_request = hooked_c4b1
        try:
            with self.assertRaises(BrokerError) as ctx:
                find_terminal_quota_for_reservation(self.root, reservation)
            self.assertTrue(replaced)
            err = str(ctx.exception).lower()
            self.assertTrue("identity" in err or "changed" in err or "metadata" in err)
        finally:
            broker_mod.read_terminal_quota_for_exact_request = orig_c4b1

    def test_discovery_reservation_validation_fails_closed(self) -> None:
        request, _response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        valid_res = _reservation_from_request(request)

        with self.assertRaises(BrokerError):
            find_terminal_quota_for_reservation(self.root, "not a dict")

        bad_keys = copy.deepcopy(valid_res)
        bad_keys.pop("task_sha256")
        with self.assertRaises(BrokerError):
            find_terminal_quota_for_reservation(self.root, bad_keys)

        for field, invalid_val in [
            ("controller_run_id", "cycle-bad"),
            ("run_prefix", "bad prefix with spaces"),
            ("source_head", "1234"),
            ("task_sha256", "bad sha"),
            ("assignment_id", ""),
            ("assignment_sha256", "short"),
            ("task_path", "../outside"),
            ("task_path", 123),
        ]:
            bad_field = copy.deepcopy(valid_res)
            bad_field[field] = invalid_val
            with self.assertRaises(BrokerError):
                find_terminal_quota_for_reservation(self.root, bad_field)

    def test_snapshot_traversal_propagates_scandir_os_error(self) -> None:
        target_dir = (self.root / ".local/two-site-refactor/provider-broker").resolve()
        injected_error = OSError(13, "Simulated permission error")
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
