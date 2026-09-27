"""Unit tests for two-site quota continuation broker response codec."""

from __future__ import annotations

import json
import math
import unittest
from unittest.mock import patch
from typing import Any

from scripts.two_site_provider_broker import (
    MAX_PIPELINE_OUTPUT_BYTES,
    RESPONSE_KEYS,
    validate_response,
)
from scripts.two_site_quota_continuation_response import (
    ContinuationResponseError,
    build_continuation_broker_response,
)


def _make_valid_request() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "request_type": "QUOTA_CONTINUATION",
        "request_id": "a" * 32,
        "controller_run_id": "cycle-0001-20260927T120000Z-1",
        "original_controller_run_id": "cycle-0002-20260927T120001Z-2",
        "original_request_id": "b" * 32,
        "claim_sha256": "c" * 64,
        "original_wait_sha256": "d" * 64,
        "semantic_task_fingerprint": "e" * 64,
        "task_id": "TASK-001",
        "task_path": "tasks/task-001.json",
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


class TestTwoSiteQuotaContinuationResponse(unittest.TestCase):
    def test_build_response_ready_for_codex_success(self) -> None:
        req = _make_valid_request()
        result = {
            "state": "READY_FOR_CODEX",
            "summary": "All gates completed successfully.",
            "covered_requirements": [1, 2],
            "details": {"status": "ok", "count": 42},
        }
        fixed_time = 1790510450.5
        response = build_continuation_broker_response(req, result, now_epoch=fixed_time)

        self.assertEqual(set(response.keys()), RESPONSE_KEYS)
        self.assertEqual(response["schema_version"], "1.0")
        self.assertEqual(response["request_id"], req["request_id"])
        self.assertEqual(response["run_prefix"], req["run_prefix"])
        self.assertEqual(response["source_head"], req["source_head"])
        self.assertEqual(response["state"], "COMPLETED")
        self.assertEqual(response["exit_code"], 0)
        self.assertEqual(response["stderr"], "")
        self.assertEqual(response["finished_at"], fixed_time)

        decoded = json.loads(response["stdout"])
        self.assertEqual(decoded, result)
        canonical_stdout = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.assertEqual(response["stdout"], canonical_stdout)

        broker_validated = validate_response(req, response)
        self.assertEqual(broker_validated, response)

    def test_build_response_task_blocked_success(self) -> None:
        req = _make_valid_request()
        result = {
            "state": "TASK_BLOCKED",
            "findings": ["dependency_missing"],
        }
        response = build_continuation_broker_response(req, result)

        self.assertEqual(response["state"], "COMPLETED")
        self.assertEqual(response["exit_code"], 1)
        self.assertEqual(response["stderr"], "")
        self.assertIsInstance(response["finished_at"], (int, float))
        self.assertTrue(math.isfinite(response["finished_at"]))

        broker_validated = validate_response(req, response)
        self.assertEqual(broker_validated, response)

    def test_reject_invalid_inner_result_states(self) -> None:
        req = _make_valid_request()
        for invalid_state in ["SUCCEEDED", "COMPLETED", "BROKER_ERROR", "UNKNOWN", "", 123, None]:
            res: dict[str, Any] = {"state": invalid_state} if invalid_state is not None else {}
            with self.subTest(invalid_state=invalid_state):
                with self.assertRaises(ContinuationResponseError):
                    build_continuation_broker_response(req, res)

    def test_reject_non_dict_result_or_request(self) -> None:
        req = _make_valid_request()
        res = {"state": "READY_FOR_CODEX"}
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(req, [res])  # type: ignore
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response("not-dict", res)  # type: ignore

    def test_reject_malformed_request_binding(self) -> None:
        base_req = _make_valid_request()
        res = {"state": "READY_FOR_CODEX"}

        bad_req_missing = dict(base_req)
        del bad_req_missing["created_at"]
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(bad_req_missing, res)

        bad_req_extra = dict(base_req)
        bad_req_extra["extra_key"] = "bad"
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(bad_req_extra, res)

        bad_cid = dict(base_req)
        bad_cid["controller_run_id"] = "bad_cycle_id"
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(bad_cid, res)

        bad_timeout = dict(base_req)
        bad_timeout["timeout"] = 5
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(bad_timeout, res)

        bool_created = dict(base_req)
        bool_created["created_at"] = True
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(bool_created, res)

    def test_reject_nonfinite_or_invalid_now_epoch(self) -> None:
        req = _make_valid_request()
        res = {"state": "READY_FOR_CODEX"}
        for invalid_now in [float("nan"), float("inf"), float("-inf"), True, False, "2026-09-27"]:
            with self.subTest(invalid_now=invalid_now):
                with self.assertRaises(ContinuationResponseError):
                    build_continuation_broker_response(req, res, now_epoch=invalid_now)  # type: ignore

    def test_reject_mocked_nonfinite_time_time(self) -> None:
        req = _make_valid_request()
        res = {"state": "READY_FOR_CODEX"}
        for bad_time in [float("nan"), float("inf"), float("-inf")]:
            with self.subTest(bad_time=bad_time):
                with patch("time.time", return_value=bad_time):
                    with self.assertRaises(ContinuationResponseError):
                        build_continuation_broker_response(req, res)

    def test_reject_oversized_int_now_epoch_overflow(self) -> None:
        req = _make_valid_request()
        res = {"state": "READY_FOR_CODEX"}
        for huge_epoch in [10**1000, -(10**1000)]:
            with self.subTest(huge_epoch=huge_epoch):
                with self.assertRaises(ContinuationResponseError):
                    build_continuation_broker_response(req, res, now_epoch=huge_epoch)

    def test_reject_silent_coercion_and_non_json_native_values(self) -> None:
        req = _make_valid_request()
        # Tuple value coerced to list by naive json serialization
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(
                req, {"state": "READY_FOR_CODEX", "items": (1, 2)}
            )
        # Non-string mapping key (int) coerced to string by naive json serialization
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(
                req, {"state": "READY_FOR_CODEX", "details": {123: "coerced_key"}}
            )
        # Top-level non-string mapping key
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(
                req, {"state": "READY_FOR_CODEX", 999: "top_level_int_key"}
            )
        # Set value
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(
                req, {"state": "READY_FOR_CODEX", "tags": {"a", "b"}}
            )
        # Non-finite float values in result
        for nonfinite_val in [float("nan"), float("inf"), float("-inf")]:
            with self.subTest(nonfinite_val=nonfinite_val):
                with self.assertRaises(ContinuationResponseError):
                    build_continuation_broker_response(
                        req, {"state": "READY_FOR_CODEX", "val": nonfinite_val}
                    )
        # Custom object value
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(
                req, {"state": "READY_FOR_CODEX", "custom": object()}
            )

    def test_reject_cyclic_container_dict_self_reference(self) -> None:
        req = _make_valid_request()
        res: dict[str, Any] = {"state": "READY_FOR_CODEX"}
        res["details"] = res
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(req, res)

    def test_reject_cyclic_container_nested_list_reference(self) -> None:
        req = _make_valid_request()
        inner_list: list[Any] = []
        res = {"state": "READY_FOR_CODEX", "items": inner_list}
        inner_list.append(res)
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(req, res)

    def test_reject_repeated_container_identity(self) -> None:
        req = _make_valid_request()
        shared_subdict: dict[str, Any] = {"inner": 1}
        res = {
            "state": "READY_FOR_CODEX",
            "first": shared_subdict,
            "second": shared_subdict,
        }
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(req, res)

    def test_reject_oversized_individual_string_pre_encoding(self) -> None:
        req = _make_valid_request()
        huge_str = "x" * (MAX_PIPELINE_OUTPUT_BYTES + 1)
        res = {"state": "READY_FOR_CODEX", "data": huge_str}
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(req, res)

    def test_reject_oversized_dict_key_pre_encoding(self) -> None:
        req = _make_valid_request()
        huge_key = "k" * (MAX_PIPELINE_OUTPUT_BYTES + 1)
        res = {"state": "READY_FOR_CODEX", huge_key: "value"}
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(req, res)

    def test_reject_cumulative_oversized_chunks_during_iterencode(self) -> None:
        req = _make_valid_request()
        chunk_size = 500_000
        res = {
            "state": "READY_FOR_CODEX",
            "part1": "a" * chunk_size,
            "part2": "b" * chunk_size,
            "part3": "c" * chunk_size,
            "part4": "d" * chunk_size,
            "part5": "e" * chunk_size,
        }
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(req, res)

    def test_preserves_complete_unchanged_result_and_keys(self) -> None:
        req = _make_valid_request()
        result = {
            "state": "READY_FOR_CODEX",
            "summary": "verified",
            "covered_requirements": [1, 2, 3, 4],
            "details": {
                "active": True,
                "score": 99.5,
                "extra": None,
                "items": ["foo", 123, False],
                "nested": {"key": "val"},
            },
        }
        resp = build_continuation_broker_response(req, result, now_epoch=1790510500.0)
        self.assertEqual(json.loads(resp["stdout"]), result)
        decoded = json.loads(resp["stdout"])
        self.assertEqual(decoded["details"]["active"], True)
        self.assertEqual(decoded["details"]["score"], 99.5)
        self.assertIsNone(decoded["details"]["extra"])
        self.assertEqual(decoded["details"]["items"], ["foo", 123, False])

    def test_reject_oversized_stdout(self) -> None:
        req = _make_valid_request()
        large_payload = "x" * (MAX_PIPELINE_OUTPUT_BYTES + 10)
        res = {"state": "READY_FOR_CODEX", "data": large_payload}
        with self.assertRaises(ContinuationResponseError):
            build_continuation_broker_response(req, res)


if __name__ == "__main__":
    unittest.main()
