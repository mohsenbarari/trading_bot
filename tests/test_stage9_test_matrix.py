from __future__ import annotations

import copy
import json
from unittest.mock import patch
import unittest
from pathlib import Path

from scripts.run_stage9_test_matrix import (
    MatrixConfigurationError,
    _run_lane,
    resolve_artifact_dir,
    validate_manifest,
    validate_parallel_isolation,
)


ROOT = Path(__file__).resolve().parents[1]


class Stage9TestMatrixTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "config/stage9_test_matrix.json").read_text())
        self.isolation = json.loads(
            (ROOT / "config/stage9_test_matrix_isolation.json").read_text()
        )

    def test_repository_manifest_is_complete_and_bounded(self):
        validate_manifest(self.manifest)

    def test_missing_market_tuple_fails_closed(self):
        broken = copy.deepcopy(self.manifest)
        broken["market_rows"] = broken["market_rows"][:-1]
        with self.assertRaisesRegex(MatrixConfigurationError, "market_row_set_mismatch"):
            validate_manifest(broken)

    def test_nonexistent_test_reference_fails_closed(self):
        broken = copy.deepcopy(self.manifest)
        broken["market_rows"][0]["test_references"].append("tests/does_not_exist.py")
        with self.assertRaisesRegex(MatrixConfigurationError, "nonexistent_test_reference"):
            validate_manifest(broken)

    def test_missing_named_market_special_case_fails_closed(self):
        broken = copy.deepcopy(self.manifest)
        broken["market_special_cases"] = broken["market_special_cases"][:-1]
        with self.assertRaisesRegex(MatrixConfigurationError, "market_special_case_set_mismatch"):
            validate_manifest(broken)

    def test_unbounded_or_pressure_profile_fails_closed(self):
        broken = copy.deepcopy(self.manifest)
        broken["market_rows"][3]["max_contenders"] = 3
        with self.assertRaisesRegex(MatrixConfigurationError, "market_contenders_unbounded"):
            validate_manifest(broken)
        broken = copy.deepcopy(self.manifest)
        broken["market_rows"][0]["dimensions"].append("soak")
        with self.assertRaisesRegex(MatrixConfigurationError, "forbidden_load_profile"):
            validate_manifest(broken)

    def test_parallel_preflight_requires_all_resources_to_be_distinct(self):
        shared = {
            "registration": {
                "database": "db-a",
                "redis_namespace": "reg",
                "fixture_prefix": "reg",
                "port": "9001",
                "artifact_dir": "tmp/reg",
                "cleanup_owner": "reg",
            },
            "market": {
                "database": "db-a",
                "redis_namespace": "mkt",
                "fixture_prefix": "mkt",
                "port": "9002",
                "artifact_dir": "tmp/mkt",
                "cleanup_owner": "mkt",
            },
        }
        passed, failures = validate_parallel_isolation(self.manifest, shared)
        self.assertFalse(passed)
        self.assertEqual(failures, ["isolation_value_shared:database"])

        shared["market"]["database"] = "db-b"
        passed, failures = validate_parallel_isolation(self.manifest, shared)
        self.assertTrue(passed)
        self.assertEqual(failures, [])

    def test_missing_isolation_defaults_to_sequential(self):
        passed, failures = validate_parallel_isolation(self.manifest, None)
        self.assertFalse(passed)
        self.assertEqual(failures, ["isolation_contract_missing"])

    def test_repository_isolation_contract_proves_distinct_parallel_resources(self):
        passed, failures = validate_parallel_isolation(self.manifest, self.isolation)
        self.assertTrue(passed)
        self.assertEqual(failures, [])

    def test_relative_artifact_directory_resolves_inside_repository(self):
        self.assertEqual(
            resolve_artifact_dir(Path("tmp/stage9-matrix")),
            ROOT / "tmp/stage9-matrix",
        )
        with self.assertRaisesRegex(MatrixConfigurationError, "artifact_dir_outside_repository"):
            resolve_artifact_dir(Path("../outside"))

    def test_lane_fails_closed_when_unittest_reports_a_skip(self):
        completed = type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": "Ran 2 tests in 0.001s\n\nOK (skipped=1)\n",
            },
        )()
        with patch("scripts.run_stage9_test_matrix.subprocess.run", return_value=completed):
            result = _run_lane("registration", ["tests.example"], ROOT / "tmp/stage9-matrix-selftest")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["test_count"], 2)
        self.assertEqual(result["skipped"], 1)

    def test_lane_records_zero_skips_for_a_clean_run(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "Ran 3 tests in 0.002s\n\nOK\n"},
        )()
        with patch("scripts.run_stage9_test_matrix.subprocess.run", return_value=completed):
            result = _run_lane("market", ["tests.example"], ROOT / "tmp/stage9-matrix-selftest")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["test_count"], 3)
        self.assertEqual(result["skipped"], 0)

    def test_lane_injects_only_its_declared_isolation_resources(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "Ran 1 test in 0.001s\n\nOK\n"},
        )()
        resources = self.isolation["registration"]
        with patch(
            "scripts.run_stage9_test_matrix.subprocess.run", return_value=completed
        ) as run:
            result = _run_lane(
                "registration",
                ["tests.example"],
                ROOT / "tmp/stage9-matrix-selftest",
                resources,
            )
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["STAGE9_LANE"], "registration")
        self.assertEqual(env["STAGE9_DATABASE_RESOURCE"], resources["database"])
        self.assertEqual(env["STAGE9_REDIS_NAMESPACE"], resources["redis_namespace"])
        self.assertEqual(env["STAGE9_CLEANUP_OWNER"], resources["cleanup_owner"])
        self.assertEqual(result["status"], "passed")


if __name__ == "__main__":
    unittest.main()
