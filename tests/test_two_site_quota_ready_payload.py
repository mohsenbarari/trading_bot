"""Direct unit tests for pure two-site quota ready-for-codex payload validator."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
from typing import Any

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_quota_continuation_response import (
    build_continuation_broker_response,
)
from scripts.two_site_quota_continuation_terminal_record import (
    build_terminal_continuation_record,
)
from scripts.two_site_quota_ready_payload import (
    QuotaReadyPayloadError,
    _validate_changed_paths,
    validate_quota_ready_payload,
)
from scripts.two_site_quota_resume_submission_intent import (
    build_quota_resume_submission_record,
)
from tests.test_two_site_quota_continuation_response import _make_valid_request


def _make_ready_payload(
    req: dict[str, Any],
    correction_count: int = 0,
    worker_retry: str = "",
    reviewer_retry: str = "",
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    run_prefix = req["run_prefix"]
    w_sfx = f"-retry-{worker_retry}" if worker_retry else ""
    r_sfx = f"-retry-{reviewer_retry}" if reviewer_retry else ""
    worker_id = f"{run_prefix}-worker-{correction_count}{w_sfx}"
    reviewer_id = f"{run_prefix}-reviewer-{correction_count}{r_sfx}"
    c_paths = (
        ["scripts/two_site_quota_ready_payload.py"]
        if changed_paths is None
        else changed_paths
    )
    return {
        "state": "READY_FOR_CODEX",
        "task_id": req["task_id"],
        "source_head": req["source_head"],
        "final_worker_run_id": worker_id,
        "final_reviewer_run_id": reviewer_id,
        "proposal_sha256": "1" * 64,
        "changed_paths": c_paths,
        "proposal_path": f".local/two-site-refactor/proposals/{worker_id}/result.json",
        "reviewer_path": f".local/two-site-refactor/proposals/{reviewer_id}/result.json",
        "correction_count": correction_count,
        "blockers": [],
        "pre_review_test_path": (
            f".local/two-site-refactor/pipeline/{run_prefix}/"
            f"pre-review-tests-{correction_count}.json"
        ),
        "pre_review_test_sha256": "2" * 64,
        "pre_review_test_state": "PASSED",
        "semantic_task_fingerprint": req["semantic_task_fingerprint"],
    }


def _make_ready_fixture(
    correction_count: int = 0,
    worker_retry: str = "",
    reviewer_retry: str = "",
    changed_paths: list[str] | None = None,
    allowed_paths: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, list[str]]:
    req = _make_valid_request()
    c_paths = (
        ["scripts/two_site_quota_ready_payload.py"]
        if changed_paths is None
        else changed_paths
    )
    a_paths = (
        [
            "scripts/two_site_quota_ready_payload.py",
            "tests/test_two_site_quota_ready_payload.py",
        ]
        if allowed_paths is None
        else allowed_paths
    )
    payload = _make_ready_payload(
        req,
        correction_count=correction_count,
        worker_retry=worker_retry,
        reviewer_retry=reviewer_retry,
        changed_paths=c_paths,
    )
    resp = build_continuation_broker_response(req, payload)
    intent = build_quota_resume_submission_record(req)
    rec = build_terminal_continuation_record(req, resp, intent)
    return rec, intent, req, req["task_id"], a_paths


class TestTwoSiteQuotaReadyPayload(unittest.TestCase):
    def test_positives_all_counts_and_retries(self) -> None:
        scenarios = [
            (0, "", ""),
            (1, "1", ""),
            (2, "", "2"),
            (2, "2", "1"),
        ]
        for count, w_retry, r_retry in scenarios:
            rec, intent, req, task_id, allowed = _make_ready_fixture(
                correction_count=count,
                worker_retry=w_retry,
                reviewer_retry=r_retry,
            )
            meta = validate_quota_ready_payload(
                rec, intent, req, task_id, allowed
            )
            self.assertEqual(meta["status"], "REVIEW_BINDING_NOT_YET_VERIFIED")
            self.assertEqual(meta["task_id"], task_id)
            self.assertEqual(meta["run_prefix"], req["run_prefix"])
            self.assertEqual(meta["source_head"], req["source_head"])
            self.assertEqual(
                meta["semantic_task_fingerprint"],
                req["semantic_task_fingerprint"],
            )
            self.assertEqual(meta["correction_count"], count)
            self.assertEqual(meta["proposal_sha256"], "1" * 64)
            self.assertEqual(meta["pre_review_test_sha256"], "2" * 64)
            self.assertEqual(
                meta["changed_paths"],
                ["scripts/two_site_quota_ready_payload.py"],
            )
            self.assertEqual(meta["response_sha256"], rec["response_sha256"])
            self.assertEqual(meta["request_id"], rec["request_id"])

            # Verify tuple allowed_output_paths accepted
            meta_tuple = validate_quota_ready_payload(
                rec, intent, req, task_id, tuple(allowed)
            )
            self.assertEqual(meta_tuple["status"], "REVIEW_BINDING_NOT_YET_VERIFIED")

            # Verify detached return copy
            meta["changed_paths"].append("extra.py")
            meta_recheck = validate_quota_ready_payload(
                rec, intent, req, task_id, allowed
            )
            self.assertEqual(
                meta_recheck["changed_paths"],
                ["scripts/two_site_quota_ready_payload.py"],
            )

    def test_reproducer_run_prefix_traversal_rejected(self) -> None:
        bad_req = _make_valid_request()
        bad_req["run_prefix"] = "x/../../escape"
        worker_id = "x/../../escape-worker-0"
        reviewer_id = "x/../../escape-reviewer-0"
        payload = {
            "state": "READY_FOR_CODEX",
            "task_id": bad_req["task_id"],
            "source_head": bad_req["source_head"],
            "final_worker_run_id": worker_id,
            "final_reviewer_run_id": reviewer_id,
            "proposal_sha256": "1" * 64,
            "changed_paths": ["a"],
            "proposal_path": (
                f".local/two-site-refactor/proposals/{worker_id}/result.json"
            ),
            "reviewer_path": (
                f".local/two-site-refactor/proposals/{reviewer_id}/result.json"
            ),
            "correction_count": 0,
            "blockers": [],
            "pre_review_test_path": (
                ".local/two-site-refactor/pipeline/x/../../escape/pre-review-tests-0.json"
            ),
            "pre_review_test_sha256": "2" * 64,
            "pre_review_test_state": "PASSED",
            "semantic_task_fingerprint": bad_req["semantic_task_fingerprint"],
        }
        resp = build_continuation_broker_response(bad_req, payload)
        intent = build_quota_resume_submission_record(bad_req)
        rec = build_terminal_continuation_record(bad_req, resp, intent)
        with self.assertRaises(QuotaReadyPayloadError):
            validate_quota_ready_payload(
                rec, intent, bad_req, bad_req["task_id"], ["a"]
            )

    def test_allowed_output_paths_negatives(self) -> None:
        rec, intent, req, task_id, _ = _make_ready_fixture(changed_paths=["a"])

        def assert_fail(allowed: Any) -> None:
            with self.assertRaises(QuotaReadyPayloadError):
                validate_quota_ready_payload(
                    rec, intent, req, task_id, allowed
                )

        assert_fail("abc")
        assert_fail(b"abc")
        assert_fail({"a": True})
        assert_fail((p for p in ["a"]))
        assert_fail([])
        assert_fail(["a", "a"])
        assert_fail(["a", 123])
        assert_fail(["a\0b"])
        assert_fail(["../a"])
        assert_fail(["a/../b"])
        assert_fail(["/a"])
        assert_fail(["a\\b"])
        assert_fail(["a//b"])
        assert_fail(["a/./b"])
        assert_fail([""])
        assert_fail([f"f_{i}" for i in range(33)])

    def test_changed_paths_negatives(self) -> None:
        req = _make_valid_request()
        allowed = ["a", "b"]

        # Tuple rejection verified directly on path validator
        with self.assertRaises(QuotaReadyPayloadError):
            _validate_changed_paths(("a",), {"a", "b"})

        def check_bad_changed(bad_changed_paths: Any) -> None:
            payload = _make_ready_payload(req)
            payload["changed_paths"] = bad_changed_paths
            resp = build_continuation_broker_response(req, payload)
            intent = build_quota_resume_submission_record(req)
            rec = build_terminal_continuation_record(req, resp, intent)
            with self.assertRaises(QuotaReadyPayloadError):
                validate_quota_ready_payload(
                    rec, intent, req, req["task_id"], allowed
                )

        check_bad_changed("a")
        check_bad_changed({"a": "b"})
        check_bad_changed(123)
        check_bad_changed([])
        check_bad_changed([f"f_{i}" for i in range(33)])
        check_bad_changed([123])
        check_bad_changed(["a", "a"])
        check_bad_changed(["not_allowed"])
        check_bad_changed(["../a"])
        check_bad_changed(["a\0b"])
        check_bad_changed(["a\\b"])
        check_bad_changed(["/a"])
        check_bad_changed([""])
        check_bad_changed(["a/../b"])
        check_bad_changed(["a/./b"])
        check_bad_changed(["a//b"])

    def test_synthetic_and_old_paths_negatives(self) -> None:
        req = _make_valid_request()
        allowed = ["scripts/two_site_quota_ready_payload.py"]

        def assert_payload_fail(mutator: Any) -> None:
            payload = _make_ready_payload(req)
            mutator(payload)
            resp = build_continuation_broker_response(req, payload)
            intent = build_quota_resume_submission_record(req)
            rec = build_terminal_continuation_record(req, resp, intent)
            with self.assertRaises(QuotaReadyPayloadError):
                validate_quota_ready_payload(
                    rec, intent, req, req["task_id"], allowed
                )

        # Synthetic -w0
        assert_payload_fail(lambda p: p.update({
            "final_worker_run_id": f"{req['run_prefix']}-w0",
            "proposal_path": (
                f".local/two-site-refactor/proposals/{req['run_prefix']}-w0/result.json"
            ),
        }))
        # Synthetic -r0
        assert_payload_fail(lambda p: p.update({
            "final_reviewer_run_id": f"{req['run_prefix']}-r0",
            "reviewer_path": (
                f".local/two-site-refactor/proposals/{req['run_prefix']}-r0/result.json"
            ),
        }))
        # Old proposal path
        assert_payload_fail(lambda p: p.update({
            "proposal_path": (
                f"artifacts/proposals/{p['final_worker_run_id']}/result.json"
            )
        }))
        # Old reviewer path
        assert_payload_fail(lambda p: p.update({
            "reviewer_path": (
                f"artifacts/proposals/{p['final_reviewer_run_id']}/result.json"
            )
        }))
        # Old test path
        assert_payload_fail(lambda p: p.update({
            "pre_review_test_path": (
                f".local/two-site-refactor/tests/{req['run_prefix']}.json"
            )
        }))
        # Count mismatch: count=0 but worker has count 1
        assert_payload_fail(lambda p: p.update({
            "final_worker_run_id": f"{req['run_prefix']}-worker-1",
            "proposal_path": (
                f".local/two-site-refactor/proposals/{req['run_prefix']}-worker-1/result.json"
            ),
        }))
        # Reviewer count mismatch
        assert_payload_fail(lambda p: p.update({
            "final_reviewer_run_id": f"{req['run_prefix']}-reviewer-1",
            "reviewer_path": (
                f".local/two-site-refactor/proposals/{req['run_prefix']}-reviewer-1/result.json"
            ),
        }))
        # Worker == Reviewer
        assert_payload_fail(lambda p: p.update({
            "final_reviewer_run_id": p["final_worker_run_id"],
            "reviewer_path": p["proposal_path"],
        }))
        # correction_count out of range 0..2 or bool
        assert_payload_fail(lambda p: p.update({"correction_count": 3}))
        assert_payload_fail(lambda p: p.update({"correction_count": -1}))
        assert_payload_fail(lambda p: p.update({"correction_count": True}))

    def test_schema_and_binding_negatives(self) -> None:
        rec, intent, req, task_id, allowed = _make_ready_fixture()

        with self.assertRaises(QuotaReadyPayloadError):
            validate_quota_ready_payload(rec, intent, req, "WRONG-TASK", allowed)
        with self.assertRaises(QuotaReadyPayloadError):
            validate_quota_ready_payload(rec, intent, req, 123, allowed)
        with self.assertRaises(QuotaReadyPayloadError):
            validate_quota_ready_payload(rec, intent, "not-dict", task_id, allowed)

        def assert_payload_fail(mutator: Any) -> None:
            payload = _make_ready_payload(req)
            mutator(payload)
            resp = build_continuation_broker_response(req, payload)
            ti = build_quota_resume_submission_record(req)
            r = build_terminal_continuation_record(req, resp, ti)
            with self.assertRaises(QuotaReadyPayloadError):
                validate_quota_ready_payload(r, ti, req, task_id, allowed)

        assert_payload_fail(lambda p: p.update({"task_id": "OTHER-TASK"}))
        assert_payload_fail(lambda p: p.update({"source_head": "0" * 40}))
        assert_payload_fail(lambda p: p.update({"source_head": "bad_sha"}))
        assert_payload_fail(lambda p: p.update({"source_head": "A" * 40}))
        assert_payload_fail(
            lambda p: p.update({"semantic_task_fingerprint": "0" * 64})
        )
        assert_payload_fail(
            lambda p: p.update({"semantic_task_fingerprint": "bad_fp"})
        )
        assert_payload_fail(lambda p: p.update({"proposal_sha256": "bad_sha"}))
        assert_payload_fail(lambda p: p.update({"proposal_sha256": "1" * 63}))
        assert_payload_fail(
            lambda p: p.update({"pre_review_test_sha256": "bad_sha"})
        )
        assert_payload_fail(lambda p: p.update({"blockers": ["something"]}))
        assert_payload_fail(
            lambda p: p.update({"pre_review_test_state": "FAILED"})
        )
        assert_payload_fail(lambda p: p.update({"state": "TASK_BLOCKED"}))
        assert_payload_fail(lambda p: p.pop("semantic_task_fingerprint"))
        assert_payload_fail(lambda p: p.update({"extra_key": "bad"}))

    def test_record_response_and_stdout_tampering_negatives(self) -> None:
        rec, intent, req, task_id, allowed = _make_ready_fixture()

        def tamper_rec(mutator: Any) -> dict[str, Any]:
            r = copy.deepcopy(rec)
            mutator(r["response"])
            resp_bytes = json.dumps(
                r["response"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            r["response_sha256"] = hashlib.sha256(resp_bytes).hexdigest()
            return r

        # exit_code = 1
        with self.assertRaises(QuotaReadyPayloadError):
            bad_rec = tamper_rec(lambda resp: resp.update({"exit_code": 1}))
            validate_quota_ready_payload(bad_rec, intent, req, task_id, allowed)

        # exit_code = True (bool-as-int)
        with self.assertRaises(QuotaReadyPayloadError):
            bad_rec = tamper_rec(lambda resp: resp.update({"exit_code": True}))
            validate_quota_ready_payload(bad_rec, intent, req, task_id, allowed)

        # stderr non-empty
        with self.assertRaises(QuotaReadyPayloadError):
            bad_rec = tamper_rec(lambda resp: resp.update({"stderr": "error"}))
            validate_quota_ready_payload(bad_rec, intent, req, task_id, allowed)

        # duplicate key in stdout
        with self.assertRaises(QuotaReadyPayloadError):
            bad_stdout = '{\"state\":\"READY_FOR_CODEX\",\"a\":1,\"a\":2}'
            bad_rec = tamper_rec(lambda resp: resp.update({"stdout": bad_stdout}))
            validate_quota_ready_payload(bad_rec, intent, req, task_id, allowed)

        # non-canonical spacing in stdout
        with self.assertRaises(QuotaReadyPayloadError):
            bad_stdout = rec["response"]["stdout"].replace(":", ": ")
            bad_rec = tamper_rec(lambda resp: resp.update({"stdout": bad_stdout}))
            validate_quota_ready_payload(bad_rec, intent, req, task_id, allowed)

        # state = "READY" in stdout
        with self.assertRaises(QuotaReadyPayloadError):
            bad_stdout = rec["response"]["stdout"].replace(
                '"state":"READY_FOR_CODEX"', '"state":"READY"'
            )
            bad_rec = tamper_rec(lambda resp: resp.update({"stdout": bad_stdout}))
            validate_quota_ready_payload(bad_rec, intent, req, task_id, allowed)

        # missing state in stdout
        with self.assertRaises(QuotaReadyPayloadError):
            bad_stdout = rec["response"]["stdout"].replace(
                '"state":"READY_FOR_CODEX",', ""
            )
            bad_rec = tamper_rec(lambda resp: resp.update({"stdout": bad_stdout}))
            validate_quota_ready_payload(bad_rec, intent, req, task_id, allowed)

        # malformed record (not dict)
        with self.assertRaises(QuotaReadyPayloadError):
            validate_quota_ready_payload("not-dict", intent, req, task_id, allowed)


if __name__ == "__main__":
    unittest.main()
