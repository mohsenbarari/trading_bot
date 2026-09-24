#!/usr/bin/env python3
"""Hermetic unit tests for terminal quota record composition and reader in provider broker."""
from __future__ import annotations

import hashlib
import json
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
    read_terminal_quota_record,
)
from tests.test_two_site_provider_broker import (
    _make_quota_payload,
    _make_quota_response,
)


def _setup_quota_roundtrip_fixture(
    root: Path,
    *,
    request_id: str = "a" * 32,
    run_prefix: str = "test-run-record",
    task_id: str = "task-record-001",
    source_head: str = "b" * 40,
) -> tuple[dict, dict, Path, Path]:
    task_rel = ".local/two-site-refactor/tasks/task-record-001.json"
    task_path = root / task_rel
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_bytes = json.dumps({"task_id": task_id, "scope": "record-test"}, sort_keys=True).encode("utf-8")
    task_path.write_bytes(task_bytes)
    task_sha256 = hashlib.sha256(task_bytes).hexdigest()

    request = {
        "schema_version": "1.0",
        "request_id": request_id,
        "run_prefix": run_prefix,
        "timeout": 300,
        "source_head": source_head,
        "task_path": task_rel,
        "task_sha256": task_sha256,
        "assignment_id": "assignment-record-1",
        "assignment_sha256": "c" * 64,
        "controller_run_id": "cycle-0001-20260924T120000Z-1",
        "created_at": time.time(),
    }

    payload = _make_quota_payload(root, task_id, source_head, run_prefix=run_prefix)
    response = _make_quota_response(request, payload)
    status_path = root / ".local/two-site-refactor/proposals" / f"{run_prefix}-worker-0" / "status.json"
    return request, response, task_path, status_path


class TestTerminalQuotaRecord(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name).resolve()
        broker_paths(self.root)

    def tearDown(self) -> None:
        self.test_dir.cleanup()

    def test_publisher_to_reader_real_roundtrip_success(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)

        published_receipt = publish_terminal_quota_record(self.root, request, response)
        read_receipt = read_terminal_quota_record(self.root, request["request_id"])

        self.assertEqual(read_receipt, published_receipt)
        self.assertEqual(read_receipt["request"], request)
        self.assertEqual(read_receipt["response"], response)

        expected_req_canonical = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        expected_resp_canonical = json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        self.assertEqual(
            read_receipt["request_sha256"],
            hashlib.sha256(expected_req_canonical).hexdigest(),
        )
        self.assertEqual(
            read_receipt["response_sha256"],
            hashlib.sha256(expected_resp_canonical).hexdigest(),
        )
        self.assertEqual(
            read_receipt["quota_evidence"],
            published_receipt["quota_evidence"],
        )

        state_root, _status, _reqs, _claimed, _resps = broker_paths(self.root)
        record_path = state_root / "terminal-quota" / f"{request['request_id']}.json"
        self.assertTrue(record_path.is_file())
        self.assertEqual(stat.S_IMODE(record_path.stat().st_mode), 0o600)
        self.assertEqual(record_path.stat().st_nlink, 1)

    def test_missing_record_fails_closed(self) -> None:
        missing_request_id = "f" * 32
        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_record(self.root, missing_request_id)
        self.assertIn("unavailable", str(ctx.exception).lower())

    def test_post_publication_task_sha_drift_fails_closed(self) -> None:
        request, response, task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)

        publish_terminal_quota_record(self.root, request, response)

        task_path.write_bytes(b'{"task_id":"task-record-001","drifted":true}')

        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_record(self.root, request["request_id"])
        self.assertIn("terminal quota response not detected", str(ctx.exception))

    def test_post_publication_status_witness_drift_fails_closed(self) -> None:
        request, response, _task_path, status_path = _setup_quota_roundtrip_fixture(
            self.root, request_id="b" * 32, run_prefix="test-run-witness"
        )

        publish_terminal_quota_record(self.root, request, response)

        status_path.write_bytes(b'{"run_id":"test-run-witness-worker-0","drifted":true}')

        with self.assertRaises(BrokerError) as ctx:
            read_terminal_quota_record(self.root, request["request_id"])
        self.assertIn("terminal quota response not detected", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
