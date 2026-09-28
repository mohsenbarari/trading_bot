"""Direct unit tests for pure two-site quota continuation terminal record codec."""

from __future__ import annotations

import copy
import math
import os
import unittest
from typing import Any

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_provider_broker import MAX_REQUEST_BYTES
from scripts.two_site_quota_continuation_response import (
    build_continuation_broker_response,
)
from scripts.two_site_quota_continuation_terminal_record import (
    MAX_TERMINAL_CONTINUATION_RECORD_BYTES,
    TerminalContinuationRecordError,
    build_terminal_continuation_record,
    validate_terminal_continuation_record,
)
from scripts.two_site_quota_resume_submission_intent import (
    build_quota_resume_submission_record,
)
from tests.test_two_site_quota_continuation_response import _make_valid_request


def _make_fixture(
    state: str = "READY_FOR_CODEX",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    req = _make_valid_request()
    resp = build_continuation_broker_response(req, {"state": state})
    intent = build_quota_resume_submission_record(req)
    return req, resp, intent


class TestTwoSiteQuotaContinuationTerminalRecord(unittest.TestCase):
    def test_positives_and_detached_copy(self) -> None:
        self.assertEqual(MAX_TERMINAL_CONTINUATION_RECORD_BYTES, 2_280_384)
        for state, exp_exit in (("READY_FOR_CODEX", 0), ("TASK_BLOCKED", 1)):
            req, resp, intent = _make_fixture(state)
            self.assertEqual(resp["exit_code"], exp_exit)
            rec = build_terminal_continuation_record(req, resp, intent)
            self.assertEqual(
                set(rec.keys()),
                {
                    "schema_version",
                    "request_id",
                    "request",
                    "request_sha256",
                    "response",
                    "response_sha256",
                    "submission_intent_sha256",
                },
            )
            validated = validate_terminal_continuation_record(
                rec, expected_intent=intent, expected_request=req
            )
            self.assertEqual(validated, rec)

            # Mutate inputs to prove caller independence
            req["request_id"] = "f" * 32
            resp["state"] = "MUTATED"
            intent["claim_sha256"] = "f" * 64
            fresh_req, _, fresh_intent = _make_fixture(state)
            reval = validate_terminal_continuation_record(
                rec, expected_intent=fresh_intent, expected_request=fresh_req
            )
            self.assertEqual(reval["schema_version"], "1.0")

            # Mutate validated return to prove detached copy
            reval["schema_version"] = "9.9"
            self.assertEqual(rec["schema_version"], "1.0")

    def test_record_schema_and_binding_negatives(self) -> None:
        req, resp, intent = _make_fixture()
        base_rec = build_terminal_continuation_record(req, resp, intent)

        def assert_fail(rec: Any, exp_intent: Any, exp_req: Any = None) -> None:
            with self.assertRaises(TerminalContinuationRecordError):
                validate_terminal_continuation_record(
                    rec, expected_intent=exp_intent, expected_request=exp_req
                )

        assert_fail(["not-dict"], intent, req)
        for key in (
            "schema_version",
            "request_id",
            "request",
            "request_sha256",
            "response",
            "response_sha256",
            "submission_intent_sha256",
        ):
            assert_fail({k: v for k, v in base_rec.items() if k != key}, intent, req)

        assert_fail({**base_rec, "extra": 1}, intent, req)
        assert_fail({**base_rec, "schema_version": "2.0"}, intent, req)
        assert_fail({**base_rec, "request_id": "0" * 32}, intent, req)
        assert_fail({**base_rec, "request_sha256": "0" * 64}, intent, req)
        assert_fail({**base_rec, "response_sha256": "0" * 64}, intent, req)
        assert_fail({**base_rec, "submission_intent_sha256": "0" * 64}, intent, req)

        other_req = _make_valid_request()
        other_req["request_id"] = "0" * 32
        other_intent = build_quota_resume_submission_record(other_req)
        assert_fail(base_rec, other_intent, req)

        # Mismatched expected_request values and bool-vs-int type
        bad_req = copy.deepcopy(req)
        bad_req["task_id"] = "TASK-DIFF"
        assert_fail(base_rec, intent, bad_req)

        bool_req = copy.deepcopy(req)
        bool_req["timeout"] = True
        assert_fail(base_rec, intent, bool_req)

        # Record request bool-vs-int
        bad_rec = copy.deepcopy(base_rec)
        bad_rec["request"]["timeout"] = True
        assert_fail(bad_rec, intent, req)

    def test_response_and_request_codec_negatives(self) -> None:
        def assert_build_fail(
            bad_req: dict[str, Any],
            bad_resp: dict[str, Any],
            bad_intent: dict[str, Any],
        ) -> None:
            with self.assertRaises(TerminalContinuationRecordError):
                build_terminal_continuation_record(bad_req, bad_resp, bad_intent)

        # BROKER_ERROR and invalid response states
        req, resp, intent = _make_fixture()
        resp["state"] = "BROKER_ERROR"
        assert_build_fail(req, resp, intent)

        # Nonfinite and bool finished_at
        for bad_time in (float("nan"), float("inf"), True):
            req, resp, intent = _make_fixture()
            resp["finished_at"] = bad_time
            assert_build_fail(req, resp, intent)

        # Invalid exit_code and non-empty stderr
        req, resp, intent = _make_fixture()
        resp["exit_code"] = 2
        assert_build_fail(req, resp, intent)

        req, resp, intent = _make_fixture()
        resp["stderr"] = "err"
        assert_build_fail(req, resp, intent)

        # Wrong inner state for exit code
        req, resp, intent = _make_fixture("READY_FOR_CODEX")
        resp["stdout"] = '{"state":"TASK_BLOCKED"}'
        assert_build_fail(req, resp, intent)

        req, resp, intent = _make_fixture("TASK_BLOCKED")
        resp["stdout"] = '{"state":"READY_FOR_CODEX"}'
        assert_build_fail(req, resp, intent)

        # Malformed stdout: non-JSON, noncanonical spacing, duplicate keys
        for bad_stdout in (
            "not-json",
            '{"state": "READY_FOR_CODEX"}',
            '{"state":"READY_FOR_CODEX","a":1,"a":2}',
        ):
            req, resp, intent = _make_fixture("READY_FOR_CODEX")
            resp["stdout"] = bad_stdout
            assert_build_fail(req, resp, intent)

        # Request > 64K (MAX_REQUEST_BYTES)
        req, resp, intent = _make_fixture()
        req["task_path"] = "p" * (MAX_REQUEST_BYTES + 1)
        with self.assertRaises(TerminalContinuationRecordError):
            build_terminal_continuation_record(req, resp, intent)


if __name__ == "__main__":
    unittest.main()
