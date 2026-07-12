from __future__ import annotations

import copy
import json
import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path

from scripts import run_stage9_test_matrix as matrix_runner
from scripts.run_stage9_test_matrix import (
    MatrixConfigurationError,
    _observed_test_results,
    _run_lane,
    load_json,
    resolve_artifact_dir,
    run_matrix,
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

    def test_unmapped_market_dimension_fails_closed(self):
        broken = copy.deepcopy(self.manifest)
        broken["market_rows"][0]["dimensions"].append("unexercised_dimension")
        with self.assertRaisesRegex(MatrixConfigurationError, "market_dimension_mapping_mismatch"):
            validate_manifest(broken)

    def test_stage9_scenario_without_concrete_test_id_fails_closed(self):
        broken = copy.deepcopy(self.manifest)
        broken["market_rows"][0]["scenarios"][0]["test_id"] = ""
        with self.assertRaisesRegex(MatrixConfigurationError, "stage9_market_scenario_test_required"):
            validate_manifest(broken)

    def test_stage10_scenario_requires_an_explicit_blocker(self):
        broken = copy.deepcopy(self.manifest)
        deferred = broken["market_rows"][3]["scenarios"][-1]
        deferred["blocker"] = ""
        with self.assertRaisesRegex(MatrixConfigurationError, "stage10_market_scenario_blocker_required"):
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
        shared = copy.deepcopy(self.isolation)
        shared["registration"]["database"] = "db-a"
        shared["market"]["database"] = "db-a"
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
                "stdout": (
                    "test_one (tests.example.ExampleTests.test_one) ... ok\n"
                    "test_two (tests.example.ExampleTests.test_two) ... skipped 'probe'\n"
                    "Ran 2 tests in 0.001s\n\nOK (skipped=1)\n"
                ),
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
            {
                "returncode": 0,
                "stdout": (
                    "test_one (tests.example.ExampleTests.test_one) ... ok\n"
                    "test_two (tests.example.ExampleTests.test_two) ... ok\n"
                    "test_three (tests.example.ExampleTests.test_three) ... ok\n"
                    "Ran 3 tests in 0.002s\n\nOK\n"
                ),
            },
        )()
        with patch("scripts.run_stage9_test_matrix.subprocess.run", return_value=completed):
            result = _run_lane("market", ["tests.example"], ROOT / "tmp/stage9-matrix-selftest")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["test_count"], 3)
        self.assertEqual(result["skipped"], 0)

    def test_lane_with_zero_discovered_tests_fails_closed(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "Ran 0 tests in 0.000s\n\nOK\n"},
        )()
        with patch("scripts.run_stage9_test_matrix.subprocess.run", return_value=completed):
            result = _run_lane("market", ["tests.example"], ROOT / "tmp/stage9-matrix-selftest")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["test_count"], 0)

    def test_observed_test_parser_handles_structured_logs_between_test_and_status(self):
        output = (
            "test_probe (tests.example.ExampleTests.test_probe) ... {\"event\":\"probe\"}\n"
            "ok\n"
        )
        self.assertEqual(
            _observed_test_results(output),
            {"tests.example.ExampleTests.test_probe": "passed"},
        )

    def test_observed_test_parser_includes_hypothesis_state_machine_run_test(self):
        output = (
            "runTest (hypothesis.stateful.RegistrationIntentStateMachine.TestCase.runTest) ... ok\n"
        )
        self.assertEqual(
            _observed_test_results(output),
            {
                "hypothesis.stateful.RegistrationIntentStateMachine.TestCase.runTest": "passed"
            },
        )

    def test_lane_fails_when_declared_and_observed_test_counts_differ(self):
        completed = type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": (
                    "test_one (tests.example.ExampleTests.test_one) ... ok\n"
                    "Ran 2 tests in 0.001s\n\nOK\n"
                ),
            },
        )()
        with patch("scripts.run_stage9_test_matrix.subprocess.run", return_value=completed):
            result = _run_lane(
                "market",
                ["tests.example"],
                ROOT / "tmp/stage9-matrix-selftest",
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["observed_tests"]), 1)
        self.assertEqual(result["test_count"], 2)

    def test_lane_injects_only_its_declared_isolation_resources(self):
        completed = type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": (
                    "test_one (tests.example.ExampleTests.test_one) ... ok\n"
                    "Ran 1 test in 0.001s\n\nOK\n"
                ),
            },
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
        self.assertEqual(env["DATABASE_URL"], resources["database_url"])
        self.assertEqual(env["SYNC_DATABASE_URL"], resources["sync_database_url"])
        self.assertEqual(env["REDIS_URL"], resources["redis_url"])
        self.assertEqual(Path(env["TMPDIR"]), ROOT / resources["tmp_dir"])
        self.assertEqual(result["status"], "passed")

    def test_manifest_rejects_every_invalid_structural_boundary(self):
        cases = []

        def broken(message, mutate):
            payload = copy.deepcopy(self.manifest)
            mutate(payload)
            cases.append((payload, message))

        broken("unsupported_schema_version", lambda value: value.__setitem__("schema_version", 2))
        broken("semantic_contenders_must_equal_two", lambda value: value.__setitem__("max_semantic_contenders", 3))
        broken("forbidden_load_profiles_incomplete", lambda value: value.__setitem__("forbidden_profiles", ["soak"]))
        broken("exactly_two_lanes_required", lambda value: value["lanes"].pop("market"))
        broken("lane_tests_required", lambda value: value["lanes"]["market"].__setitem__("test_modules", []))
        broken("lane_minimum_test_count_required", lambda value: value["lanes"]["market"].__setitem__("minimum_test_count", 0))
        broken("nonexistent_test_module", lambda value: value["lanes"]["market"].__setitem__("test_modules", ["tests.not_real"]))
        broken("market_rows_required", lambda value: value.__setitem__("market_rows", {}))
        broken("market_dimensions_required", lambda value: value["market_rows"][0].__setitem__("dimensions", []))
        broken("market_scenarios_required", lambda value: value["market_rows"][0].__setitem__("scenarios", []))
        broken("invalid_market_scenario", lambda value: value["market_rows"][0].__setitem__("scenarios", ["bad"]))
        broken(
            "invalid_market_scenario_id",
            lambda value: value["market_rows"][0]["scenarios"][1].__setitem__(
                "id", value["market_rows"][0]["scenarios"][0]["id"]
            ),
        )
        broken("invalid_market_scenario_stage", lambda value: value["market_rows"][0]["scenarios"][0].__setitem__("evidence_stage", "stage11"))
        broken("incomplete_market_scenario", lambda value: value["market_rows"][0]["scenarios"][0].__setitem__("dimensions", []))
        broken("market_special_cases_required", lambda value: value.__setitem__("market_special_cases", {}))
        broken("market_special_case_tests_required", lambda value: value["market_special_cases"][0].__setitem__("test_references", []))
        broken("market_special_case_not_executed", lambda value: value["lanes"]["market"]["test_modules"].remove(value["market_special_cases"][0]["test_references"][0].removesuffix(".py").replace("/", ".")))
        broken("market_special_case_test_id_required", lambda value: value["market_special_cases"][0].__setitem__("test_id", ""))
        for manifest, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(MatrixConfigurationError, message):
                validate_manifest(manifest)

    def test_isolation_rejects_schema_mode_records_missing_values_and_mutable_io(self):
        cases = []
        broken = copy.deepcopy(self.isolation)
        broken["schema_version"] = 1
        cases.append((self.manifest, broken, "isolation_schema_version_2_required"))
        broken = copy.deepcopy(self.isolation)
        broken["resource_mode"] = "shared"
        cases.append((self.manifest, broken, "unsupported_isolation_resource_mode"))
        broken = copy.deepcopy(self.isolation)
        broken["market"] = None
        cases.append((self.manifest, broken, "both_lane_isolation_records_required"))
        broken = copy.deepcopy(self.isolation)
        broken["market"]["database"] = ""
        cases.append((self.manifest, broken, "isolation_value_missing:database"))
        mutable_manifest = copy.deepcopy(self.manifest)
        mutable_manifest["lanes"]["market"]["external_mutable_service_io"] = True
        cases.append((mutable_manifest, self.isolation, "lane_external_mutable_io_not_forbidden:market"))
        for manifest, isolation, failure in cases:
            with self.subTest(failure=failure):
                passed, failures = validate_parallel_isolation(manifest, isolation)
                self.assertFalse(passed)
                self.assertIn(failure, failures)

    def test_load_json_requires_an_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(MatrixConfigurationError, "object_required"):
                load_json(path)
            path.write_text('{"valid": true}', encoding="utf-8")
            self.assertEqual(load_json(path), {"valid": True})

    def test_special_case_rejects_nonexistent_reference(self):
        broken = copy.deepcopy(self.manifest)
        broken["market_special_cases"][0]["test_references"] = [
            "tests/not_a_real_special_case.py"
        ]
        with self.assertRaisesRegex(MatrixConfigurationError, "nonexistent_test_reference"):
            validate_manifest(broken)

    def test_observed_parser_records_fail_error_skip_and_qualified_method(self):
        output = (
            "test_a (tests.example.Case.test_a) ... FAIL\n"
            "test_b (tests.example.Case) ... ERROR\n"
            "test_c (tests.example.Case.test_c) ... skipped 'reason'\n"
            "test_d (tests.example.Case) ...\nERROR\n"
            "unrelated\n"
        )
        self.assertEqual(
            _observed_test_results(output),
            {
                "tests.example.Case.test_a": "failed",
                "tests.example.Case.test_b": "failed",
                "tests.example.Case.test_c": "skipped",
                "tests.example.Case.test_d": "failed",
            },
        )

    def test_lane_fails_for_process_error_or_missing_ran_summary(self):
        for returncode, output in ((1, "Ran 1 test in 0.1s\n"), (0, "no summary\n")):
            completed = type("Completed", (), {"returncode": returncode, "stdout": output})()
            with self.subTest(returncode=returncode), patch.object(
                matrix_runner.subprocess, "run", return_value=completed
            ):
                result = _run_lane(
                    "market",
                    ["tests.example"],
                    ROOT / "tmp/stage9-matrix-selftest",
                )
            self.assertEqual(result["status"], "failed")

    def _lane_results(self, manifest, *, failed_test=None, lane_status="passed"):
        observed = {
            str(scenario["test_id"]): "passed"
            for row in manifest["market_rows"]
            for scenario in row["scenarios"]
            if scenario.get("evidence_stage", "stage9") == "stage9"
        }
        observed.update(
            {str(case["test_id"]): "passed" for case in manifest["market_special_cases"]}
        )
        if failed_test:
            observed[failed_test] = "failed"
        return {
            "registration": {"status": lane_status, "observed_tests": {}, "skipped": 0},
            "market": {"status": lane_status, "observed_tests": observed, "skipped": 0},
        }

    def test_run_matrix_maps_parallel_sequential_failed_and_complete_rows(self):
        for parallel in (True, False):
            lane_results = self._lane_results(self.manifest)
            with self.subTest(parallel=parallel), patch.object(
                matrix_runner,
                "_run_lane",
                side_effect=lambda name, *_args, **_kwargs: lane_results[name],
            ), patch.object(
                matrix_runner.subprocess,
                "check_output",
                return_value="c" * 40 + "\n",
            ):
                result = run_matrix(
                    self.manifest,
                    parallel=parallel,
                    artifact_dir=Path("tmp/stage9-matrix-selftest"),
                    isolation=self.isolation,
                )
            self.assertTrue(result["stage9_passed"])
            self.assertFalse(result["complete"])
            self.assertEqual(
                result["execution_mode"], "parallel" if parallel else "sequential"
            )
            self.assertEqual(result["market_rows"]["MKT-004"]["status"], "deferred_stage10_real_topology")

        failed_manifest = copy.deepcopy(self.manifest)
        failed_test = failed_manifest["market_rows"][0]["scenarios"][0]["test_id"]
        lane_results = self._lane_results(failed_manifest, failed_test=failed_test, lane_status="failed")
        with patch.object(
            matrix_runner,
            "_run_lane",
            side_effect=lambda name, *_args, **_kwargs: lane_results[name],
        ), patch.object(matrix_runner.subprocess, "check_output", return_value="d" * 40 + "\n"):
            failed = run_matrix(
                failed_manifest,
                parallel=False,
                artifact_dir=Path("tmp/stage9-matrix-selftest"),
            )
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["market_rows"]["MKT-001"]["status"], "failed")

        complete_manifest = copy.deepcopy(self.manifest)
        for row in complete_manifest["market_rows"]:
            for scenario in row["scenarios"]:
                if scenario.get("evidence_stage") == "stage10":
                    scenario["evidence_stage"] = "stage9"
                    scenario["test_id"] = f"tests.synthetic.{scenario['id']}"
        lane_results = self._lane_results(complete_manifest)
        with patch.object(
            matrix_runner,
            "_run_lane",
            side_effect=lambda name, *_args, **_kwargs: lane_results[name],
        ), patch.object(matrix_runner.subprocess, "check_output", return_value="e" * 40 + "\n"):
            complete = run_matrix(
                complete_manifest,
                parallel=False,
                artifact_dir=Path("tmp/stage9-matrix-selftest"),
            )
        self.assertTrue(complete["complete"])

    def test_main_preflight_configuration_errors_and_result_status(self):
        with patch.object(matrix_runner, "load_json", side_effect=OSError("missing")):
            self.assertEqual(matrix_runner.main([]), 2)

        with patch.object(matrix_runner, "load_json", side_effect=[self.manifest, self.isolation]), patch.object(
            matrix_runner, "validate_manifest"
        ), patch.object(
            matrix_runner,
            "validate_parallel_isolation",
            return_value=(False, ["not_isolated"]),
        ):
            self.assertEqual(matrix_runner.main(["--parallel", "required"]), 2)

        with patch.object(matrix_runner, "load_json", side_effect=[self.manifest, self.isolation]), patch.object(
            matrix_runner, "validate_manifest"
        ), patch.object(
            matrix_runner,
            "validate_parallel_isolation",
            return_value=(True, []),
        ):
            self.assertEqual(matrix_runner.main(["--preflight-only"]), 0)

        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            output = Path(directory) / "results.json"
            for passed, expected in ((True, 0), (False, 1)):
                with patch.object(
                    matrix_runner,
                    "load_json",
                    side_effect=[self.manifest, self.isolation],
                ), patch.object(matrix_runner, "validate_manifest"), patch.object(
                    matrix_runner,
                    "validate_parallel_isolation",
                    return_value=(True, []),
                ), patch.object(
                    matrix_runner,
                    "run_matrix",
                    return_value={"passed": passed},
                ):
                    result = matrix_runner.main(["--output", str(output)])
                self.assertEqual(result, expected)
                self.assertEqual(json.loads(output.read_text())["passed"], passed)


if __name__ == "__main__":
    unittest.main()
