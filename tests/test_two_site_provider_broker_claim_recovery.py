#!/usr/bin/env python3
"""Hermetic unit tests for claim recovery C3 in provider broker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.two_site_provider_broker import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    BrokerError,
    _atomic_new_json,
    _recover_interrupted_claims,
    broker_paths,
    fsync_private_record_directory,
    publish_terminal_quota_record,
)
from tests.test_two_site_provider_broker_terminal_record import (
    _setup_quota_roundtrip_fixture,
)


class TestClaimRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name).resolve()
        self.state_root, self.status_path, self.requests, self.claimed, self.responses = broker_paths(self.root)
        self.terminal_quota = self.state_root / "terminal-quota"

    def tearDown(self) -> None:
        self.test_dir.cleanup()

    def test_positive_receipt_reconstructs_response_and_idempotent(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(self.root)
        publish_terminal_quota_record(self.root, request, response)

        claim_file = self.claimed / f"{request['request_id']}.json"
        _atomic_new_json(claim_file, request, MAX_REQUEST_BYTES)
        resp_file = self.responses / f"{request['request_id']}.json"
        self.assertFalse(resp_file.exists())

        _recover_interrupted_claims(self.root, self.claimed, self.responses)

        self.assertTrue(resp_file.is_file())
        reconstructed = json.loads(resp_file.read_text("utf-8"))
        self.assertEqual(reconstructed, response)
        self.assertFalse(claim_file.exists())

        # Second recovery is idempotent
        _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertTrue(resp_file.is_file())
        self.assertEqual(json.loads(resp_file.read_text("utf-8")), response)

        # Existing identical response with claim (crash after publish before claim unlink)
        _atomic_new_json(claim_file, request, MAX_REQUEST_BYTES)
        self.assertTrue(claim_file.is_file())
        _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertTrue(resp_file.is_file())
        self.assertEqual(json.loads(resp_file.read_text("utf-8")), response)
        self.assertFalse(claim_file.exists())

    def test_missing_receipt_behavior(self) -> None:
        # A: Missing receipt and missing response produces ambiguous BROKER_ERROR
        request, _response, _task_path, _status_path = _setup_quota_roundtrip_fixture(
            self.root, request_id="1" * 32, run_prefix="missing-rec-1"
        )
        claim_file = self.claimed / f"{request['request_id']}.json"
        _atomic_new_json(claim_file, request, MAX_REQUEST_BYTES)
        resp_file = self.responses / f"{request['request_id']}.json"

        _recover_interrupted_claims(self.root, self.claimed, self.responses)

        self.assertFalse(claim_file.exists())
        self.assertTrue(resp_file.is_file())
        resp_data = json.loads(resp_file.read_text("utf-8"))
        self.assertEqual(resp_data["state"], "BROKER_ERROR")
        self.assertEqual(resp_data["exit_code"], 2)
        self.assertIn("ambiguous in-flight provider call", resp_data["stderr"])
        self.assertFalse(self.terminal_quota.exists())

        # B: Missing receipt with valid existing response accepted and preserved
        request2, response2, _task2, _status2 = _setup_quota_roundtrip_fixture(
            self.root, request_id="2" * 32, run_prefix="missing-rec-2"
        )
        claim_file2 = self.claimed / f"{request2['request_id']}.json"
        resp_file2 = self.responses / f"{request2['request_id']}.json"
        _atomic_new_json(claim_file2, request2, MAX_REQUEST_BYTES)
        _atomic_new_json(resp_file2, response2, MAX_RESPONSE_BYTES)

        _recover_interrupted_claims(self.root, self.claimed, self.responses)

        self.assertFalse(claim_file2.exists())
        self.assertTrue(resp_file2.is_file())
        self.assertEqual(json.loads(resp_file2.read_text("utf-8")), response2)

    def test_duplicate_requests_id_collision_retains_claim(self) -> None:
        request, _response, _task_path, _status_path = _setup_quota_roundtrip_fixture(
            self.root, request_id="3" * 32, run_prefix="dup-req-1"
        )
        claim_file = self.claimed / f"{request['request_id']}.json"
        request_file = self.requests / f"{request['request_id']}.json"
        _atomic_new_json(claim_file, request, MAX_REQUEST_BYTES)
        _atomic_new_json(request_file, request, MAX_REQUEST_BYTES)

        with self.assertRaises(BrokerError) as ctx:
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertIn("collision", str(ctx.exception).lower())
        self.assertTrue(claim_file.is_file())

    def test_mismatched_claimed_request_or_response_retains_claim(self) -> None:
        # Changed claimed request vs receipt
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(
            self.root, request_id="4" * 32, run_prefix="mismatch-req"
        )
        publish_terminal_quota_record(self.root, request, response)

        tampered_request = dict(request)
        tampered_request["timeout"] = 450
        claim_file = self.claimed / f"{request['request_id']}.json"
        _atomic_new_json(claim_file, tampered_request, MAX_REQUEST_BYTES)

        with self.assertRaises(BrokerError) as ctx:
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertIn("does not match receipt request", str(ctx.exception))
        self.assertTrue(claim_file.is_file())
        claim_file.unlink()

        # Changed existing response vs receipt
        _atomic_new_json(claim_file, request, MAX_REQUEST_BYTES)
        tampered_response = dict(response)
        tampered_response["exit_code"] = 0
        resp_file = self.responses / f"{request['request_id']}.json"
        _atomic_new_json(resp_file, tampered_response, MAX_RESPONSE_BYTES)

        with self.assertRaises(BrokerError) as ctx:
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertIn("does not match receipt response", str(ctx.exception))
        self.assertTrue(claim_file.is_file())

    def test_corrupt_or_unsafe_receipt_retains_claim(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(
            self.root, request_id="5" * 32, run_prefix="corrupt-rec"
        )
        claim_file = self.claimed / f"{request['request_id']}.json"
        _atomic_new_json(claim_file, request, MAX_REQUEST_BYTES)

        # Corrupt JSON in receipt
        self.terminal_quota.mkdir(parents=True, mode=0o700, exist_ok=True)
        rec_file = self.terminal_quota / f"{request['request_id']}.json"
        rec_file.write_bytes(b"not json")
        os.chmod(rec_file, 0o600)

        with self.assertRaises(BrokerError):
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertTrue(claim_file.is_file())
        rec_file.unlink()

        # Symlink receipt
        dummy = self.root / "dummy.json"
        dummy.write_text("{}", encoding="utf-8")
        os.chmod(dummy, 0o600)
        rec_file.symlink_to(dummy)

        with self.assertRaises(BrokerError):
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertTrue(claim_file.is_file())
        rec_file.unlink()

        # Hardlink receipt
        os.link(dummy, rec_file)
        with self.assertRaises(BrokerError):
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertTrue(claim_file.is_file())
        rec_file.unlink()

        # Unsafe terminal-quota directory mode
        os.chmod(self.terminal_quota, 0o777)
        with self.assertRaises(BrokerError):
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertTrue(claim_file.is_file())

    def test_corrupt_or_mismatched_existing_response_without_receipt_retains_claim(self) -> None:
        request, _response, _task_path, _status_path = _setup_quota_roundtrip_fixture(
            self.root, request_id="6" * 32, run_prefix="resp-no-rec"
        )
        claim_file = self.claimed / f"{request['request_id']}.json"
        resp_file = self.responses / f"{request['request_id']}.json"
        _atomic_new_json(claim_file, request, MAX_REQUEST_BYTES)

        # Corrupt response file
        resp_file.write_bytes(b"corrupt payload")
        os.chmod(resp_file, 0o600)

        with self.assertRaises(BrokerError):
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertTrue(claim_file.is_file())
        resp_file.unlink()

        # Mismatched binding response (different request_id)
        bad_response = {
            "schema_version": "1.0",
            "request_id": "0" * 32,
            "run_prefix": request["run_prefix"],
            "source_head": request["source_head"],
            "state": "COMPLETED",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "finished_at": time.time(),
        }
        _atomic_new_json(resp_file, bad_response, MAX_RESPONSE_BYTES)

        with self.assertRaises(BrokerError):
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertTrue(claim_file.is_file())

    def test_fsync_order_and_failure_retains_claim(self) -> None:
        request, response, _task_path, _status_path = _setup_quota_roundtrip_fixture(
            self.root, request_id="7" * 32, run_prefix="fsync-test"
        )
        publish_terminal_quota_record(self.root, request, response)
        claim_file = self.claimed / f"{request['request_id']}.json"
        _atomic_new_json(claim_file, request, MAX_REQUEST_BYTES)

        original_fsync = fsync_private_record_directory

        # Failure injection on responses fsync
        def failing_fsync(path: Path) -> None:
            if path == self.responses:
                raise BrokerError("injected responses fsync fault")
            original_fsync(path)

        with mock.patch("scripts.two_site_provider_broker.fsync_private_record_directory", side_effect=failing_fsync):
            with self.assertRaises(BrokerError) as ctx:
                _recover_interrupted_claims(self.root, self.claimed, self.responses)
            self.assertIn("injected responses fsync fault", str(ctx.exception))
        self.assertTrue(claim_file.is_file())

        # Success case: verify exact ordering (responses fsync BEFORE claim unlink, claimed fsync AFTER)
        call_events: list[tuple[str, Path]] = []

        def tracking_fsync(path: Path) -> None:
            if path in (self.responses, self.claimed):
                call_events.append(("fsync", path))
            original_fsync(path)

        original_unlink = Path.unlink

        def tracking_unlink(path_obj: Path, *args, **kwargs) -> None:
            if path_obj == claim_file:
                call_events.append(("unlink", path_obj))
            return original_unlink(path_obj, *args, **kwargs)

        with mock.patch("scripts.two_site_provider_broker.fsync_private_record_directory", side_effect=tracking_fsync):
            with mock.patch.object(Path, "unlink", autospec=True, side_effect=tracking_unlink):
                _recover_interrupted_claims(self.root, self.claimed, self.responses)

        self.assertFalse(claim_file.exists())
        self.assertEqual(
            call_events,
            [
                ("fsync", self.responses),
                ("unlink", claim_file),
                ("fsync", self.claimed),
            ],
        )

    def test_claim_queue_entry_safety_retains_claim(self) -> None:
        # Symlink in claimed/
        target = self.root / "real_claim.json"
        target.write_text("{}", encoding="utf-8")
        os.chmod(target, 0o600)
        claim_link = self.claimed / f"{'8' * 32}.json"
        claim_link.symlink_to(target)

        with self.assertRaises(BrokerError) as ctx:
            _recover_interrupted_claims(self.root, self.claimed, self.responses)
        self.assertIn("not a trusted queue file", str(ctx.exception))
        self.assertTrue(claim_link.is_symlink())


if __name__ == "__main__":
    unittest.main()
