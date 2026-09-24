#!/usr/bin/env python3
"""Hermetic unit tests for terminal quota record payload validation in provider broker."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_provider_broker import (
    MAX_RECORD_BYTES,
    RECEIPT_KEYS,
    BrokerError,
    broker_paths,
    validate_terminal_quota_payload,
)
from tests.test_two_site_provider_broker_terminal_publish import (
    make_actual_quota_evidence,
    make_valid_request,
    make_valid_response,
)


def _build_canonical_receipt_and_bytes(
    root: Path,
    *,
    request: dict | None = None,
    response: dict | None = None,
    evidence: dict | None = None,
    finished_at: float | int | None = 1700000000.0,
) -> tuple[dict, bytes]:
    req = make_valid_request(root) if request is None else request
    resp = (
        make_valid_response(req, finished_at=finished_at)
        if response is None
        else response
    )
    ev = make_actual_quota_evidence() if evidence is None else evidence

    req_bytes = json.dumps(
        req, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    resp_bytes = json.dumps(
        resp, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")

    receipt = {
        "schema_version": "1.0",
        "request": req,
        "request_sha256": hashlib.sha256(req_bytes).hexdigest(),
        "response": resp,
        "response_sha256": hashlib.sha256(resp_bytes).hexdigest(),
        "quota_evidence": ev,
        "finished_at": resp["finished_at"],
    }
    raw_bytes = json.dumps(
        receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return receipt, raw_bytes


class TestTerminalQuotaPayloadValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name).resolve()
        broker_paths(self.root)

    def tearDown(self) -> None:
        self.test_dir.cleanup()

    def test_valid_payload_success(self) -> None:
        evidence = make_actual_quota_evidence()
        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ):
            receipt, raw_bytes = _build_canonical_receipt_and_bytes(
                self.root, evidence=evidence, finished_at=1700000000.5
            )
            validated = validate_terminal_quota_payload(
                self.root, receipt["request"]["request_id"], raw_bytes
            )
            self.assertEqual(validated, receipt)

            receipt_int, raw_bytes_int = _build_canonical_receipt_and_bytes(
                self.root, evidence=evidence, finished_at=1700000000
            )
            validated_int = validate_terminal_quota_payload(
                self.root, receipt_int["request"]["request_id"], raw_bytes_int
            )
            self.assertEqual(validated_int, receipt_int)
            self.assertIs(type(validated_int["finished_at"]), int)

    def test_raw_bytes_types_and_bounds(self) -> None:
        req_id = "a" * 32
        for non_bytes in ("string", 12345, None, ["bytes"]):
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, req_id, non_bytes)  # type: ignore[arg-type]
            self.assertIn("must be bytes", str(ctx.exception))

        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, b"x" * (MAX_RECORD_BYTES + 1))
        self.assertIn("exceeds maximum record bytes", str(ctx.exception))

        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, b"")
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_expected_request_id_validation(self) -> None:
        _receipt, raw_bytes = _build_canonical_receipt_and_bytes(self.root)
        for bad_id in ("A" * 32, "1" * 31, "1" * 33, "g" * 32, "", 12345, None):
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, bad_id, raw_bytes)  # type: ignore[arg-type]
            self.assertIn("expected request ID is invalid", str(ctx.exception))

        mismatched_id = "f" * 32
        evidence = make_actual_quota_evidence()
        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=evidence,
        ):
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, mismatched_id, raw_bytes)
            self.assertIn("does not match expected request ID", str(ctx.exception))

    def test_utf8_and_unique_json_rejection(self) -> None:
        req_id = "a" * 32
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, b"\xff\xfe\x00\x00")
        self.assertIn("not valid UTF-8", str(ctx.exception))

        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, b'{"schema_version":')
        self.assertIn("not valid JSON", str(ctx.exception))

        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, b"[]")
        self.assertIn("must be a JSON object", str(ctx.exception))

        receipt, _ = _build_canonical_receipt_and_bytes(self.root)
        dup_top = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        dup_top_bytes = dup_top[:-1].encode("utf-8") + b',"schema_version":"1.0"}'
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, dup_top_bytes)
        self.assertIn("not valid JSON", str(ctx.exception))

        raw_req = json.dumps(receipt["request"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        bad_req_str = raw_req[:-1] + ',"timeout":300}'
        bad_nested = dup_top.replace(raw_req, bad_req_str).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, bad_nested)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_noncanonical_encoding_rejection(self) -> None:
        receipt, raw_bytes = _build_canonical_receipt_and_bytes(self.root)
        req_id = receipt["request"]["request_id"]

        for noncanon in (
            raw_bytes + b" ",
            b" " + raw_bytes,
            raw_bytes + b"\n",
            raw_bytes + b"\r\n",
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(", ", ": ")).encode("utf-8"),
            json.dumps(receipt, ensure_ascii=False, sort_keys=False, separators=(",", ":")).encode("utf-8"),
            raw_bytes.replace(b'"schema_version":"1.0"', b'"schema_version":"\\u0031.0"'),
        ):
            if noncanon == raw_bytes:
                continue
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, req_id, noncanon)
            self.assertIn("byte-for-byte canonical JSON", str(ctx.exception))

        for trailing_junk in (raw_bytes + b"{}", raw_bytes + b"trailing"):
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, req_id, trailing_junk)
            self.assertIn("not valid JSON", str(ctx.exception))

    def test_nan_and_infinity_rejection(self) -> None:
        receipt, _ = _build_canonical_receipt_and_bytes(self.root)
        req_id = receipt["request"]["request_id"]
        base = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        for target, bad_val in [
            ('"timeout":300', "NaN"),
            ('"timeout":300', "Infinity"),
            (f'"created_at":{receipt["request"]["created_at"]}', "NaN"),
            (f'"finished_at":{receipt["finished_at"]}', "Infinity"),
        ]:
            bad_bytes = base.replace(target, target.split(":")[0] + f":{bad_val}").encode("utf-8")
            with self.assertRaises(BrokerError):
                validate_terminal_quota_payload(self.root, req_id, bad_bytes)

    def test_top_level_receipt_schema_violations(self) -> None:
        receipt, _ = _build_canonical_receipt_and_bytes(self.root)
        req_id = receipt["request"]["request_id"]

        for key in RECEIPT_KEYS:
            bad = {k: v for k, v in receipt.items() if k != key}
            bad_bytes = json.dumps(bad, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, req_id, bad_bytes)
            self.assertIn("exact contract", str(ctx.exception))

        bad_extra = dict(receipt, extra="unexpected")
        bad_extra_bytes = json.dumps(bad_extra, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, bad_extra_bytes)
            self.assertIn("exact contract", str(ctx.exception))

        bad_ver = dict(receipt, schema_version="2.0")
        bad_ver_bytes = json.dumps(bad_ver, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, bad_ver_bytes)
        self.assertIn("schema version is invalid", str(ctx.exception))

    def test_request_field_violations(self) -> None:
        evidence = make_actual_quota_evidence()
        req_valid = make_valid_request(self.root)

        cases: list[tuple[str, object, str]] = [
            ("request_id", "0" * 31, "request ID is invalid"),
            ("run_prefix", "", "run-prefix is invalid"),
            ("timeout", 9, "timeout is invalid"),
            ("timeout", 901, "timeout is invalid"),
            ("timeout", True, "timeout is invalid"),
            ("timeout", "300", "timeout is invalid"),
            ("source_head", "b" * 39, "source HEAD is invalid"),
            ("task_sha256", "c" * 63, "task SHA is invalid"),
            ("assignment_id", "bad id with space", "assignment ID is invalid"),
            ("assignment_sha256", "d" * 63, "assignment SHA is invalid"),
            ("controller_run_id", "invalid-cycle-id", "controller run ID is invalid"),
            ("created_at", True, "created_at is invalid"),
            ("created_at", "2026-09-24T00:00:00Z", "created_at is invalid"),
            ("task_path", "/etc/passwd", "canonical and relative"),
            ("task_path", ".local/other/task.json", "task path is outside"),
            ("task_path", ".local/two-site-refactor/tasks/../../escape", "canonical and relative"),
        ]

        for field, value, match in cases:
            bad_req = copy.deepcopy(req_valid)
            bad_req[field] = value
            receipt, raw_bytes = _build_canonical_receipt_and_bytes(
                self.root, request=bad_req, evidence=evidence
            )
            req_id = bad_req.get("request_id") if isinstance(bad_req.get("request_id"), str) else "a" * 32
            if not req_id or len(req_id) != 32:
                req_id = "a" * 32
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, req_id, raw_bytes)
            self.assertIn(match, str(ctx.exception))

        bad_req_missing = {k: v for k, v in req_valid.items() if k != "created_at"}
        _r, raw_missing = _build_canonical_receipt_and_bytes(
            self.root, request=bad_req_missing, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_valid["request_id"], raw_missing)
        self.assertIn("does not match the exact contract", str(ctx.exception))

        bad_req_extra = dict(req_valid, extra_field="bad")
        _r, raw_extra = _build_canonical_receipt_and_bytes(
            self.root, request=bad_req_extra, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_valid["request_id"], raw_extra)
        self.assertIn("does not match the exact contract", str(ctx.exception))

    def test_response_field_violations(self) -> None:
        evidence = make_actual_quota_evidence()
        req = make_valid_request(self.root)
        resp_valid = make_valid_response(req)

        # State must be COMPLETED
        resp_broker_error = copy.deepcopy(resp_valid)
        resp_broker_error["state"] = "BROKER_ERROR"
        resp_broker_error["exit_code"] = 2
        _r, raw_broker_error = _build_canonical_receipt_and_bytes(
            self.root, request=req, response=resp_broker_error, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req["request_id"], raw_broker_error)
        self.assertIn("state must be COMPLETED", str(ctx.exception))

        # Invalid state in validate_response
        resp_failed = copy.deepcopy(resp_valid)
        resp_failed["state"] = "FAILED"
        _r, raw_failed = _build_canonical_receipt_and_bytes(
            self.root, request=req, response=resp_failed, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req["request_id"], raw_failed)
        self.assertIn("binding is invalid", str(ctx.exception))

        # Exit code violations
        for bad_code in (0, 2):
            bad_resp = copy.deepcopy(resp_valid)
            bad_resp["exit_code"] = bad_code
            _r, raw_bad_code = _build_canonical_receipt_and_bytes(
                self.root, request=req, response=bad_resp, evidence=evidence
            )
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, req["request_id"], raw_bad_code)
            self.assertIn("exit_code must be 1", str(ctx.exception))

        # Exit code bool is rejected by validate_response
        bad_resp_bool = copy.deepcopy(resp_valid)
        bad_resp_bool["exit_code"] = True
        _r, raw_bad_bool = _build_canonical_receipt_and_bytes(
            self.root, request=req, response=bad_resp_bool, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req["request_id"], raw_bad_bool)
        self.assertIn("binding is invalid", str(ctx.exception))

        # Stderr must be empty
        bad_stderr = copy.deepcopy(resp_valid)
        bad_stderr["stderr"] = "non-empty-stderr"
        _r, raw_bad_stderr = _build_canonical_receipt_and_bytes(
            self.root, request=req, response=bad_stderr, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req["request_id"], raw_bad_stderr)
        self.assertIn("stderr must be empty", str(ctx.exception))

        # Mismatched request_id binding
        bad_req_id = copy.deepcopy(resp_valid)
        bad_req_id["request_id"] = "f" * 32
        _r, raw_bad_req_id = _build_canonical_receipt_and_bytes(
            self.root, request=req, response=bad_req_id, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req["request_id"], raw_bad_req_id)
        self.assertIn("binding is invalid", str(ctx.exception))

        # Invalid finished_at
        bad_fin = copy.deepcopy(resp_valid)
        bad_fin["finished_at"] = True
        _r, raw_bad_fin = _build_canonical_receipt_and_bytes(
            self.root, request=req, response=bad_fin, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req["request_id"], raw_bad_fin)
        self.assertIn("finished_at is invalid", str(ctx.exception))

        bad_resp_missing = {k: v for k, v in resp_valid.items() if k != "stderr"}
        _r, raw_missing = _build_canonical_receipt_and_bytes(
            self.root, request=req, response=bad_resp_missing, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req["request_id"], raw_missing)
        self.assertIn("exact contract", str(ctx.exception))

        bad_resp_extra = dict(resp_valid, extra=123)
        _r, raw_extra = _build_canonical_receipt_and_bytes(
            self.root, request=req, response=bad_resp_extra, evidence=evidence
        )
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req["request_id"], raw_extra)
        self.assertIn("exact contract", str(ctx.exception))

    def test_finished_at_mismatch_and_type_equality(self) -> None:
        evidence = make_actual_quota_evidence()
        req = make_valid_request(self.root)
        req_id = req["request_id"]

        receipt, _ = _build_canonical_receipt_and_bytes(
            self.root, request=req, evidence=evidence, finished_at=1700000000.0
        )
        receipt["finished_at"] = 1700000001.0
        raw = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, raw)
        self.assertIn("finished_at mismatch", str(ctx.exception))

        resp_int = make_valid_response(req, finished_at=1700000000)
        receipt_float, _ = _build_canonical_receipt_and_bytes(
            self.root, request=req, response=resp_int, evidence=evidence, finished_at=1700000000
        )
        receipt_float["finished_at"] = 1700000000.0
        raw_float = json.dumps(receipt_float, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, raw_float)
        self.assertIn("finished_at mismatch", str(ctx.exception))

        receipt_bool = copy.deepcopy(receipt)
        receipt_bool["finished_at"] = True
        raw_bool = json.dumps(receipt_bool, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, raw_bool)
        self.assertIn("finished_at is invalid", str(ctx.exception))

    def test_digest_and_hash_mismatch(self) -> None:
        evidence = make_actual_quota_evidence()
        receipt, _ = _build_canonical_receipt_and_bytes(self.root, evidence=evidence)
        req_id = receipt["request"]["request_id"]

        bad_sha_receipt = copy.deepcopy(receipt)
        bad_sha_receipt["request_sha256"] = receipt["request_sha256"].upper()
        raw = json.dumps(bad_sha_receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, raw)
        self.assertIn("request_sha256 is invalid", str(ctx.exception))

        bad_resp_sha_receipt = copy.deepcopy(receipt)
        bad_resp_sha_receipt["response_sha256"] = "1" * 63
        raw_resp = json.dumps(bad_resp_sha_receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, raw_resp)
        self.assertIn("response_sha256 is invalid", str(ctx.exception))

        mismatch_req_receipt = copy.deepcopy(receipt)
        mismatch_req_receipt["request"]["timeout"] = 400
        raw_mismatch_req = json.dumps(mismatch_req_receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, raw_mismatch_req)
        self.assertIn("request_sha256 digest mismatch", str(ctx.exception))

        mismatch_resp_receipt = copy.deepcopy(receipt)
        mismatch_resp_receipt["response"]["stdout"] = '{"mutated":true}'
        raw_mismatch_resp = json.dumps(mismatch_resp_receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(BrokerError) as ctx:
            validate_terminal_quota_payload(self.root, req_id, raw_mismatch_resp)
        self.assertIn("response_sha256 digest mismatch", str(ctx.exception))

    def test_detector_evidence_validation(self) -> None:
        evidence = make_actual_quota_evidence()
        receipt, raw_bytes = _build_canonical_receipt_and_bytes(self.root, evidence=evidence)
        req_id = receipt["request"]["request_id"]

        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=None,
        ):
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, req_id, raw_bytes)
            self.assertIn("terminal quota response not detected", str(ctx.exception))

        diff_evidence = make_actual_quota_evidence(conversation_id="conv-different-5678")
        with patch(
            "scripts.two_site_provider_broker.detect_quota_response",
            return_value=diff_evidence,
        ):
            with self.assertRaises(BrokerError) as ctx:
                validate_terminal_quota_payload(self.root, req_id, raw_bytes)
            self.assertIn("receipt quota evidence mismatch", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
