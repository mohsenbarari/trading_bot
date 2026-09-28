"""Hermetic unit tests for broker continuation claim recovery on restart."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any
import unittest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_provider_broker import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    BrokerError,
    _atomic_new_json,
    _recover_interrupted_claims,
    broker_paths,
)


def _make_continuation_request(
    request_id: str = "a" * 32,
    *,
    extra_field: bool = False,
    unknown_type: bool = False,
) -> dict[str, Any]:
    req: dict[str, Any] = {
        "schema_version": "1.0",
        "request_type": "QUOTA_CONTINUATION",
        "request_id": request_id,
        "controller_run_id": "cycle-0001-20260927T120000Z-1",
        "original_controller_run_id": "cycle-0002-20260927T120001Z-2",
        "original_request_id": "b" * 32,
        "claim_sha256": "c" * 64,
        "original_wait_sha256": "d" * 64,
        "semantic_task_fingerprint": "e" * 64,
        "task_id": "TASK-001",
        "task_path": ".local/two-site-refactor/tasks/task-001.json",
        "task_sha256": "f" * 64,
        "source_head": "d0cac67efe45607f3da72ed4e3c9a1bd184929aa",
        "assignment_id": "ASSIGN-001",
        "assignment_sha256": "1" * 64,
        "run_prefix": "run-001",
        "role": "worker",
        "claimed_at_utc": "2026-09-27T12:00:00Z",
        "timeout": 300,
        "created_at": 1790510400.0,
    }
    if extra_field:
        req["unexpected_key"] = "forbidden"
    if unknown_type:
        req["request_type"] = "UNKNOWN_TYPE"
    return req


def _make_ordinary_request(
    request_id: str = "1" * 32,
    *,
    missing_key: bool = False,
) -> dict[str, Any]:
    req: dict[str, Any] = {
        "schema_version": "1.0",
        "request_id": request_id,
        "task_path": ".local/two-site-refactor/tasks/task-001.json",
        "task_sha256": "f" * 64,
        "run_prefix": "run-001",
        "timeout": 300,
        "source_head": "d0cac67efe45607f3da72ed4e3c9a1bd184929aa",
        "assignment_id": "ASSIGN-001",
        "assignment_sha256": "1" * 64,
        "controller_run_id": "cycle-0001-20260927T120000Z-1",
        "created_at": 1790510400.0,
    }
    if missing_key:
        req.pop("controller_run_id")
    return req


class TestTwoSiteQuotaBrokerClaimRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve(strict=True)
        self.state_root, _status, self.requests, self.claimed, self.responses = broker_paths(self.root)
        self.terminal_quota = self.state_root / "terminal-quota"
        self.terminal_quota.mkdir(mode=0o700, exist_ok=True)
        tasks_dir = self.root / ".local/two-site-refactor/tasks"
        tasks_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        task_file = tasks_dir / "task-001.json"
        task_file.write_text("{}", encoding="utf-8")
        os.chmod(task_file, 0o600)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_continuation_no_receipt_publishes_ambiguous_error_and_unlinks_claim(self) -> None:
        req_id = "a" * 32
        req = _make_continuation_request(req_id)
        claim_file = self.claimed / f"{req_id}.json"
        _atomic_new_json(claim_file, req, MAX_REQUEST_BYTES)
        self.assertTrue(claim_file.exists())

        _recover_interrupted_claims(
            self.root,
            self.claimed,
            self.responses,
            self.requests,
            self.terminal_quota,
        )

        self.assertFalse(claim_file.exists())
        resp_file = self.responses / f"{req_id}.json"
        self.assertTrue(resp_file.exists())
        resp = json.loads(resp_file.read_text(encoding="utf-8"))
        self.assertEqual(resp["schema_version"], "1.0")
        self.assertEqual(resp["request_id"], req_id)
        self.assertEqual(resp["run_prefix"], "run-001")
        self.assertEqual(resp["state"], "BROKER_ERROR")
        self.assertEqual(resp["exit_code"], 2)
        self.assertIn("broker restarted after an ambiguous in-flight provider call", resp["stderr"])

    def test_continuation_existing_response_preserved_and_unlinks_claim(self) -> None:
        req_id = "b" * 32
        req = _make_continuation_request(req_id)
        claim_file = self.claimed / f"{req_id}.json"
        _atomic_new_json(claim_file, req, MAX_REQUEST_BYTES)

        resp_file = self.responses / f"{req_id}.json"
        existing_resp = {
            "schema_version": "1.0",
            "request_id": req_id,
            "run_prefix": "run-001",
            "source_head": req["source_head"],
            "state": "COMPLETED",
            "exit_code": 0,
            "stdout": "continuation completed successfully",
            "stderr": "",
            "finished_at": 1790510500.0,
        }
        _atomic_new_json(resp_file, existing_resp, MAX_RESPONSE_BYTES)

        _recover_interrupted_claims(
            self.root,
            self.claimed,
            self.responses,
            self.requests,
            self.terminal_quota,
        )

        self.assertFalse(claim_file.exists())
        self.assertTrue(resp_file.exists())
        preserved = json.loads(resp_file.read_text(encoding="utf-8"))
        self.assertEqual(preserved["state"], "COMPLETED")
        self.assertEqual(preserved["stdout"], "continuation completed successfully")
        self.assertEqual(preserved["exit_code"], 0)

    def test_continuation_with_unexpected_terminal_receipt_blocks_and_retains_claim(self) -> None:
        req_id = "c" * 32
        req = _make_continuation_request(req_id)
        claim_file = self.claimed / f"{req_id}.json"
        _atomic_new_json(claim_file, req, MAX_REQUEST_BYTES)

        receipt_file = self.terminal_quota / f"{req_id}.json"
        _atomic_new_json(receipt_file, {"schema_version": "1.0"}, MAX_REQUEST_BYTES)

        with self.assertRaises(BrokerError) as ctx:
            _recover_interrupted_claims(
                self.root,
                self.claimed,
                self.responses,
                self.requests,
                self.terminal_quota,
            )

        self.assertIn("unexpected terminal quota receipt for continuation request", str(ctx.exception))
        self.assertTrue(claim_file.exists())
        resp_file = self.responses / f"{req_id}.json"
        self.assertFalse(resp_file.exists())

    def test_malformed_unknown_and_extra_shape_retained(self) -> None:
        cases = [
            ("d" * 32, _make_continuation_request("d" * 32, extra_field=True)),
            ("e" * 32, _make_continuation_request("e" * 32, unknown_type=True)),
            ("f" * 32, _make_ordinary_request("f" * 32, missing_key=True)),
        ]
        for req_id, malformed_req in cases:
            claim_file = self.claimed / f"{req_id}.json"
            _atomic_new_json(claim_file, malformed_req, MAX_REQUEST_BYTES)
            self.assertTrue(claim_file.exists())

            with self.assertRaises(BrokerError):
                _recover_interrupted_claims(
                    self.root,
                    self.claimed,
                    self.responses,
                    self.requests,
                    self.terminal_quota,
                )

            self.assertTrue(claim_file.exists())
            claim_file.unlink()

    def test_ordinary_request_normal_recovery_unaffected(self) -> None:
        req_id = "1" * 32
        req = _make_ordinary_request(req_id)
        claim_file = self.claimed / f"{req_id}.json"
        _atomic_new_json(claim_file, req, MAX_REQUEST_BYTES)

        _recover_interrupted_claims(
            self.root,
            self.claimed,
            self.responses,
            self.requests,
            self.terminal_quota,
        )

        self.assertFalse(claim_file.exists())
        resp_file = self.responses / f"{req_id}.json"
        self.assertTrue(resp_file.exists())
        resp = json.loads(resp_file.read_text(encoding="utf-8"))
        self.assertEqual(resp["state"], "BROKER_ERROR")
        self.assertEqual(resp["exit_code"], 2)
        self.assertIn("broker restarted after an ambiguous in-flight provider call", resp["stderr"])


if __name__ == "__main__":
    unittest.main()
