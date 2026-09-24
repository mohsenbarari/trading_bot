#!/usr/bin/env python3
"""Hermetic unit tests for terminal quota record publication in provider broker."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.two_site_provider_broker import (
    MAX_RECORD_BYTES,
    MAX_RECORD_SCAN_ENTRIES,
    BrokerError,
    broker_paths,
    publish_terminal_quota_record,
    scan_private_record_directory,
)
from scripts.two_site_proposal_runner import WORKER_MODEL


def make_valid_request(
    root: Path,
    *,
    request_id: str = "a" * 32,
    run_prefix: str = "prefix1",
    timeout: int = 300,
    source_head: str = "b" * 40,
    task_path: str = ".local/two-site-refactor/tasks/task-1.json",
    task_sha256: str = "c" * 64,
    assignment_id: str = "assign-1",
    assignment_sha256: str = "d" * 64,
    controller_run_id: str = "cycle-0001-20260924T120000Z-1",
    created_at: float | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "request_id": request_id,
        "run_prefix": run_prefix,
        "timeout": timeout,
        "source_head": source_head,
        "task_path": task_path,
        "task_sha256": task_sha256,
        "assignment_id": assignment_id,
        "assignment_sha256": assignment_sha256,
        "controller_run_id": controller_run_id,
        "created_at": time.time() if created_at is None else created_at,
    }


def make_valid_response(
    request: dict,
    *,
    state: str = "COMPLETED",
    exit_code: int = 1,
    stdout: str = '{"state":"TASK_BLOCKED"}',
    stderr: str = "",
    finished_at: float | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "request_id": request["request_id"],
        "run_prefix": request["run_prefix"],
        "source_head": request["source_head"],
        "state": state,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "finished_at": time.time() if finished_at is None else finished_at,
    }


def make_actual_quota_evidence(
    *,
    kind: str = "QUOTA_RESET_UNKNOWN",
    provider: str = "gemini",
    model: str = WORKER_MODEL,
    conversation_id: str = "conv-test-1234",
    reset_at_utc: str | None = None,
) -> dict:
    return {
        "kind": kind,
        "provider": provider,
        "model": model,
        "conversation_id": conversation_id,
        "reset_at_utc": reset_at_utc,
    }


class TestTerminalQuotaPublish(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name).resolve()
        broker_paths(self.root)

    def tearDown(self) -> None:
        self.test_dir.cleanup()

    def test_publish_success_receipt_and_zero_quota_dir_chmod(self) -> None:
        req = make_valid_request(self.root)
        resp = make_valid_response(req)
        evidence = make_actual_quota_evidence()

        chmod_calls: list[tuple[str, int]] = []
        real_chmod = os.chmod

        def spy_chmod(path: object, mode: int, *args: object, **kwargs: object) -> None:
            if "terminal-quota" in str(path):
                chmod_calls.append((str(path), mode))
            real_chmod(path, mode, *args, **kwargs)

        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ), patch("os.chmod", side_effect=spy_chmod):
            receipt = publish_terminal_quota_record(self.root, req, resp)

        self.assertEqual(chmod_calls, [], "os.chmod must not be called on terminal-quota directory")

        expected_req_bytes = json.dumps(
            req, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        expected_resp_bytes = json.dumps(
            resp, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

        self.assertEqual(receipt["schema_version"], "1.0")
        self.assertEqual(receipt["request"], req)
        self.assertEqual(receipt["request_sha256"], hashlib.sha256(expected_req_bytes).hexdigest())
        self.assertEqual(receipt["response"], resp)
        self.assertEqual(receipt["response_sha256"], hashlib.sha256(expected_resp_bytes).hexdigest())
        self.assertEqual(receipt["quota_evidence"], evidence)
        self.assertEqual(receipt["finished_at"], resp["finished_at"])

        state_root, _status, _reqs, _claimed, _resps = broker_paths(self.root)
        quota_dir = state_root / "terminal-quota"
        self.assertTrue(quota_dir.is_dir())
        self.assertEqual(stat.S_IMODE(quota_dir.stat().st_mode), 0o700)

        record_file = quota_dir / f"{req['request_id']}.json"
        self.assertTrue(record_file.is_file())
        self.assertEqual(stat.S_IMODE(record_file.stat().st_mode), 0o600)
        self.assertEqual(record_file.stat().st_nlink, 1)
        self.assertLessEqual(record_file.stat().st_size, MAX_RECORD_BYTES)

        with open(record_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved, receipt)

    def test_publish_no_overwrite(self) -> None:
        req = make_valid_request(self.root)
        resp = make_valid_response(req)
        evidence = make_actual_quota_evidence()

        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ):
            publish_terminal_quota_record(self.root, req, resp)
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req, resp)
            self.assertIn("already exists", str(ctx.exception))

    def test_invalid_detector_or_exit_code_or_broker_error(self) -> None:
        req = make_valid_request(self.root)
        evidence = make_actual_quota_evidence()

        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=None,
        ):
            resp = make_valid_response(req)
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req, resp)
            self.assertIn("not detected", str(ctx.exception))

        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ):
            resp_bad_exit = make_valid_response(req, exit_code=0)
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req, resp_bad_exit)
            self.assertIn("COMPLETED exit_code=1", str(ctx.exception))

            resp_broker_error = make_valid_response(req, state="BROKER_ERROR", exit_code=2)
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req, resp_broker_error)
            self.assertIn("COMPLETED exit_code=1", str(ctx.exception))

    def test_mismatched_binding_and_malformed_request(self) -> None:
        req = make_valid_request(self.root)
        evidence = make_actual_quota_evidence()

        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ):
            resp_mismatch = make_valid_response(req)
            resp_mismatch["request_id"] = "f" * 32
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req, resp_mismatch)
            self.assertIn("binding is invalid", str(ctx.exception))

            req_bad_run_id = dict(req, controller_run_id="invalid-run-id")
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req_bad_run_id, make_valid_response(req))
            self.assertIn("controller run ID is invalid", str(ctx.exception))

            req_missing = {k: v for k, v in req.items() if k != "assignment_sha256"}
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req_missing, make_valid_response(req))
            self.assertIn("does not match the exact contract", str(ctx.exception))

    def test_reject_nan_or_infinite_timestamps(self) -> None:
        evidence = make_actual_quota_evidence()
        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ):
            req_nan = make_valid_request(self.root, created_at=float("nan"))
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req_nan, make_valid_response(req_nan))
            self.assertIn("created_at is invalid", str(ctx.exception))

            req_inf = make_valid_request(self.root, created_at=float("inf"))
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req_inf, make_valid_response(req_inf))
            self.assertIn("created_at is invalid", str(ctx.exception))

            req_valid = make_valid_request(self.root)
            resp_nan = make_valid_response(req_valid, finished_at=float("nan"))
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req_valid, resp_nan)
            self.assertIn("finished_at is invalid", str(ctx.exception))

            resp_inf = make_valid_response(req_valid, finished_at=float("inf"))
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req_valid, resp_inf)
            self.assertIn("finished_at is invalid", str(ctx.exception))

    def test_directory_scan_limit_and_entry_integrity(self) -> None:
        state_root, _status, _reqs, _claimed, _resps = broker_paths(self.root)
        quota_dir = state_root / "terminal-quota"
        quota_dir.mkdir(mode=0o700, exist_ok=True)

        evidence = make_actual_quota_evidence()

        # Populate directory to MAX_RECORD_SCAN_ENTRIES
        for i in range(MAX_RECORD_SCAN_ENTRIES):
            name = f"{i:032x}.json"
            record_path = quota_dir / name
            record_path.write_text(json.dumps({"index": i}), encoding="utf-8")
            os.chmod(record_path, 0o600)

        req = make_valid_request(self.root, request_id="e" * 32)
        resp = make_valid_response(req)
        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ):
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req, resp)
            self.assertIn("entry limit", str(ctx.exception))

        # Clean up populated files and test rogue .tmp entry
        for entry in list(quota_dir.iterdir()):
            entry.unlink()

        bad_tmp = quota_dir / ".sample.tmp"
        bad_tmp.write_text("tmp", encoding="utf-8")
        os.chmod(bad_tmp, 0o600)
        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ):
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req, resp)
            self.assertIn("invalid entry name", str(ctx.exception))
        bad_tmp.unlink()

    def test_unsafe_preexisting_directory_rejected_without_chmod(self) -> None:
        state_root, _status, _reqs, _claimed, _resps = broker_paths(self.root)
        quota_dir = state_root / "terminal-quota"
        quota_dir.mkdir(mode=0o755, exist_ok=True)
        os.chmod(quota_dir, 0o755)

        chmod_calls: list[tuple[str, int]] = []
        real_chmod = os.chmod

        def spy_chmod(path: object, mode: int, *args: object, **kwargs: object) -> None:
            if "terminal-quota" in str(path):
                chmod_calls.append((str(path), mode))
            real_chmod(path, mode, *args, **kwargs)

        req = make_valid_request(self.root)
        resp = make_valid_response(req)
        evidence = make_actual_quota_evidence()

        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ), patch("os.chmod", side_effect=spy_chmod):
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req, resp)
            self.assertIn("mode must be 0700", str(ctx.exception))

        self.assertEqual(chmod_calls, [], "Unsafe directory must not be repaired via os.chmod")

    def test_fsync_directory_failure_preserves_published_file(self) -> None:
        req = make_valid_request(self.root)
        resp = make_valid_response(req)
        evidence = make_actual_quota_evidence()

        real_fsync = os.fsync

        def fake_fsync(fd: int) -> None:
            st = os.fstat(fd)
            if stat.S_ISDIR(st.st_mode):
                raise OSError("injected directory fsync failure")
            real_fsync(fd)

        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ), patch("os.fsync", side_effect=fake_fsync):
            with self.assertRaises(BrokerError) as ctx:
                publish_terminal_quota_record(self.root, req, resp)
            self.assertIn("cannot fsync record directory", str(ctx.exception))

        state_root, _status, _reqs, _claimed, _resps = broker_paths(self.root)
        record_path = state_root / "terminal-quota" / f"{req['request_id']}.json"
        self.assertTrue(record_path.is_file(), "File must remain intact on directory fsync failure")
        self.assertEqual(stat.S_IMODE(record_path.stat().st_mode), 0o600)
        with open(record_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["request"], req)
        self.assertEqual(saved["response"], resp)


if __name__ == "__main__":
    unittest.main()
