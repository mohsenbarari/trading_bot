"""Direct unit tests for pure two-site quota ready receipt artifacts validator."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_quota_ready_receipt_artifacts import (
    QuotaReadyReceiptArtifactsError,
    ReadyReceiptArtifactsError,
    validate_ready_receipt_artifacts,
)


class StrSubclass(str):
    """Subclass of str to test exact-type enforcement."""


class EqualitySpoof:
    """Non-string object with equality-spoofing behavior."""

    def __init__(self, target: object) -> None:
        self.target = target

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False


class MappingImpostor:
    """Non-mapping object that implements __getitem__."""

    def __getitem__(self, item: str) -> str:
        return "spoofed"


def _make_valid_ready(
    run_prefix: str = "run-prefix-1",
    correction_count: int = 0,
    proposal_sha: str = "a" * 64,
    test_sha: str = "b" * 64,
) -> dict[str, object]:
    return {
        "run_prefix": run_prefix,
        "correction_count": correction_count,
        "pre_review_test_path": (
            f".local/two-site-refactor/pipeline/{run_prefix}/"
            f"pre-review-tests-{correction_count}.json"
        ),
        "pre_review_test_sha256": test_sha,
        "pre_review_test_state": "PASSED",
        "proposal_sha256": proposal_sha,
    }


class TestTwoSiteQuotaReadyReceiptArtifacts(unittest.TestCase):
    def test_positives_all_correction_counts(self) -> None:
        for count in (0, 1, 2):
            ready = _make_valid_ready(correction_count=count)
            res = validate_ready_receipt_artifacts(ready)
            expected_keys = {
                "original_run_prefix",
                "correction_count",
                "pre_review_test_path",
                "pre_review_test_sha256",
                "pre_review_test_state",
                "proposal_sha256",
            }
            self.assertEqual(set(res.keys()), expected_keys)
            self.assertEqual(len(res), 6)
            self.assertEqual(res["original_run_prefix"], ready["run_prefix"])
            self.assertEqual(res["correction_count"], count)
            self.assertEqual(res["pre_review_test_path"], ready["pre_review_test_path"])
            self.assertEqual(res["pre_review_test_sha256"], ready["pre_review_test_sha256"])
            self.assertEqual(res["pre_review_test_state"], "PASSED")
            self.assertEqual(res["proposal_sha256"], ready["proposal_sha256"])

    def test_parameterized_type_matrix_all_six_fields(self) -> None:
        bad_types = [None, 123, 12.3, [], {}, object(), True]
        for field in (
            "run_prefix",
            "pre_review_test_path",
            "pre_review_test_sha256",
            "pre_review_test_state",
            "proposal_sha256",
        ):
            for bad_val in bad_types:
                ready = _make_valid_ready()
                ready[field] = bad_val
                with self.assertRaises(ReadyReceiptArtifactsError):
                    validate_ready_receipt_artifacts(ready)

        bad_count_types = [None, "0", 0.0, [], {}, object(), True, False]
        for bad_val in bad_count_types:
            ready = _make_valid_ready()
            ready["correction_count"] = bad_val
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(ready)

    def test_str_subclass_and_equality_spoofing(self) -> None:
        for field in (
            "run_prefix",
            "pre_review_test_path",
            "pre_review_test_sha256",
            "pre_review_test_state",
            "proposal_sha256",
        ):
            ready = _make_valid_ready()
            ready[field] = StrSubclass(str(ready[field]))
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(ready)

        ready = _make_valid_ready()
        ready["pre_review_test_path"] = EqualitySpoof(ready["pre_review_test_path"])
        with self.assertRaises(ReadyReceiptArtifactsError):
            validate_ready_receipt_artifacts(ready)

        ready = _make_valid_ready()
        ready["pre_review_test_state"] = EqualitySpoof("PASSED")
        with self.assertRaises(ReadyReceiptArtifactsError):
            validate_ready_receipt_artifacts(ready)

    def test_missing_keys(self) -> None:
        for field in (
            "run_prefix",
            "correction_count",
            "pre_review_test_path",
            "pre_review_test_sha256",
            "pre_review_test_state",
            "proposal_sha256",
        ):
            ready = _make_valid_ready()
            del ready[field]
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(ready)

    def test_mapping_impostors_and_non_mappings(self) -> None:
        for bad_input in (None, "string", b"bytes", 123, [1, 2], MappingImpostor()):
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(bad_input)  # type: ignore

    def test_correction_count_bounds_and_bool(self) -> None:
        for bad_count in (-1, 3, 10, -100, True, False):
            ready = _make_valid_ready()
            ready["correction_count"] = bad_count
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(ready)

    def test_malformed_values(self) -> None:
        for bad_pfx in ("", "x/../../escape", "-leading-dash", "a" * 62, "prefix with space"):
            ready = _make_valid_ready(run_prefix="run-1")
            ready["run_prefix"] = bad_pfx
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(ready)

        for bad_sha in ("", "a" * 63, "a" * 65, "A" * 64, "g" * 64):
            ready = _make_valid_ready()
            ready["proposal_sha256"] = bad_sha
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(ready)

        for bad_sha in ("", "b" * 63, "b" * 65, "B" * 64, "z" * 64):
            ready = _make_valid_ready()
            ready["pre_review_test_sha256"] = bad_sha
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(ready)

        for bad_state in ("", "FAILED", "passed", "COMPLETED", "TIMEOUT"):
            ready = _make_valid_ready()
            ready["pre_review_test_state"] = bad_state
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(ready)

        bad_paths = [
            ".local/two-site-refactor/pipeline/other-pfx/pre-review-tests-0.json",
            ".local/two-site-refactor/pipeline/run-prefix-1/pre-review-tests-1.json",
            ".local/two-site-refactor/pipeline/run-prefix-1/pre-review-tests-0.json.tmp",
            "other/path.json",
            ".local/two-site-refactor/pipeline/run-prefix-1/../../escape.json",
        ]
        for bad_path in bad_paths:
            ready = _make_valid_ready(run_prefix="run-prefix-1", correction_count=0)
            ready["pre_review_test_path"] = bad_path
            with self.assertRaises(ReadyReceiptArtifactsError):
                validate_ready_receipt_artifacts(ready)

    def test_mutation_isolation(self) -> None:
        ready = _make_valid_ready(correction_count=0)
        res = validate_ready_receipt_artifacts(ready)

        res["correction_count"] = 2
        res["extra"] = "injected"
        self.assertEqual(ready["correction_count"], 0)
        self.assertNotIn("extra", ready)

        fresh_ready = _make_valid_ready(correction_count=1)
        res2 = validate_ready_receipt_artifacts(fresh_ready)
        fresh_ready["correction_count"] = 0
        fresh_ready["proposal_sha256"] = "f" * 64
        self.assertEqual(res2["correction_count"], 1)
        self.assertEqual(res2["proposal_sha256"], "a" * 64)

    def test_no_file_io_performed(self) -> None:
        ready = _make_valid_ready()
        forbidden = AssertionError("Pure validator must not perform file I/O")
        with patch("builtins.open", side_effect=forbidden), \
             patch("pathlib.Path.open", side_effect=forbidden), \
             patch("pathlib.Path.read_bytes", side_effect=forbidden), \
             patch("pathlib.Path.read_text", side_effect=forbidden), \
             patch("os.listdir", side_effect=forbidden), \
             patch("os.stat", side_effect=forbidden):
            res = validate_ready_receipt_artifacts(ready)
            self.assertEqual(res["pre_review_test_state"], "PASSED")


if __name__ == "__main__":
    unittest.main()
