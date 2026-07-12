from __future__ import annotations

import builtins
import io
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from scripts import build_stage9_runtime_evidence as runtime_evidence
from scripts import run_stage9_test_matrix as matrix_runner
from scripts.build_stage9_runtime_evidence import (
    Stage9RuntimeEvidenceError,
    _read_integration_log,
    build_runtime_evidence,
)


COMMIT = "a" * 40
LOCAL_TEST = "tests.test_local.LocalTests.test_local"
POSTGRES_TEST = "tests.test_postgres.PostgresTests.test_database"
REDIS_TEST = "tests.test_redis.RedisTests.test_atomic"
TRANSITION_TEST = "tests.test_local.LocalTests.test_transition"


def _matrix():
    return {
        "commit": COMMIT,
        "stage9_passed": True,
        "execution_mode": "parallel",
        "lanes": {
            "registration": {
                "status": "passed",
                "test_count": 2,
                "skipped": 0,
                "observed_tests": {
                    LOCAL_TEST: "passed",
                    TRANSITION_TEST: "passed",
                },
            },
            "market": {
                "status": "passed",
                "test_count": 1,
                "skipped": 0,
                "observed_tests": {"tests.test_market.MarketTests.test_row": "passed"},
            },
        },
        "market_rows": {
            "MKT-001": {"status": "passed", "scenarios": {}},
            "MKT-004": {
                "status": "deferred_stage10_real_topology",
                "scenarios": {},
            },
        },
    }


def _config():
    return {
        "registry_bindings": {
            "REG-001": {"evidence_stage": "stage9", "test_ids": [LOCAL_TEST]},
            "MIG-001": {
                "evidence_stage": "stage9_postgres",
                "test_ids": [LOCAL_TEST, POSTGRES_TEST],
            },
            "OTP-001": {
                "evidence_stage": "stage9_redis",
                "test_ids": [REDIS_TEST],
            },
            "MKT-001": {"evidence_stage": "matrix", "matrix_row": "MKT-001"},
            "MKT-004": {
                "evidence_stage": "matrix_stage10",
                "matrix_row": "MKT-004",
                "blocker": "real topology required",
            },
            "E2E-001": {
                "evidence_stage": "stage10",
                "blocker": "browser staging required",
            },
        },
        "transitions": [
            {
                "id": "MODEL-A-B",
                "legal": True,
                "test_ids": [TRANSITION_TEST],
            },
            {
                "id": "MODEL-B-A-ILLEGAL",
                "legal": False,
                "test_ids": [TRANSITION_TEST],
            },
        ],
    }


def _integration(test_id):
    return {
        "path": "evidence.log",
        "commit": COMMIT,
        "test_count": 1,
        "skipped": 0,
        "observed_tests": {test_id: "passed"},
    }


class Stage9RuntimeEvidenceTests(unittest.TestCase):
    def test_direct_script_import_fallback_is_supported(self):
        original_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "scripts.run_stage9_test_matrix":
                raise ModuleNotFoundError(name)
            return original_import(name, globals, locals, fromlist, level)

        with patch.dict(
            sys.modules,
            {"run_stage9_test_matrix": matrix_runner},
        ), patch.object(builtins, "__import__", guarded_import):
            namespace = runpy.run_path(
                str(runtime_evidence.__file__),
                run_name="stage9_runtime_fallback_probe",
            )

        self.assertIs(
            namespace["_observed_test_results"],
            matrix_runner._observed_test_results,
        )

    def test_read_json_requires_an_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text('{"status":"ok"}', encoding="utf-8")
            self.assertEqual(runtime_evidence._read_json(path), {"status": "ok"})

            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(Stage9RuntimeEvidenceError, "object_required"):
                runtime_evidence._read_json(path)

    def test_build_requires_observed_exact_tests_and_preserves_deferred_rows(self):
        result = build_runtime_evidence(
            _config(),
            _matrix(),
            _integration(POSTGRES_TEST),
            _integration(REDIS_TEST),
            commit=COMMIT,
        )

        self.assertTrue(result["stage9_passed"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["scenarios"]["REG-001"]["status"], "passed")
        self.assertEqual(result["scenarios"]["MKT-004"]["status"], "deferred")
        self.assertEqual(
            result["transitions"]["MODEL-B-A-ILLEGAL"]["observed_outcome"],
            "rejected_without_mutation",
        )

    def test_missing_or_wrong_source_test_fails_closed(self):
        postgres = _integration("tests.test_other.OtherTests.test_other")
        with self.assertRaisesRegex(Stage9RuntimeEvidenceError, "registry_tests_missing"):
            build_runtime_evidence(
                _config(),
                _matrix(),
                postgres,
                _integration(REDIS_TEST),
                commit=COMMIT,
            )

        postgres = _integration(POSTGRES_TEST)
        redis = _integration("tests.test_other.OtherTests.test_other")
        with self.assertRaisesRegex(Stage9RuntimeEvidenceError, "registry_tests_missing"):
            build_runtime_evidence(
                _config(),
                _matrix(),
                postgres,
                redis,
                commit=COMMIT,
            )

    def test_integration_log_requires_commit_count_no_skips_and_exact_ids(self):
        output = (
            f"stage9_evidence_commit={COMMIT}\n"
            "test_database (tests.test_postgres.PostgresTests.test_database) ... ok\n"
            "Ran 1 test in 0.100s\n\nOK\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "postgres.log"
            path.write_text(output, encoding="utf-8")
            parsed = _read_integration_log(path, expected_commit=COMMIT)
            self.assertEqual(parsed["test_count"], 1)
            self.assertEqual(parsed["observed_tests"], {POSTGRES_TEST: "passed"})

            path.write_text(output.replace(COMMIT, "b" * 40), encoding="utf-8")
            with self.assertRaisesRegex(
                Stage9RuntimeEvidenceError, "integration_log_commit_mismatch"
            ):
                _read_integration_log(path, expected_commit=COMMIT)

    def test_integration_log_rejects_empty_mismatched_skipped_and_failed_runs(self):
        cases = {
            "integration_log_has_no_tests": (
                f"stage9_evidence_commit={COMMIT}\n"
                "Ran 0 tests in 0.100s\n\nOK\n"
            ),
            "integration_test_count_mismatch": (
                f"stage9_evidence_commit={COMMIT}\n"
                "test_database (tests.test_postgres.PostgresTests.test_database) ... ok\n"
                "Ran 2 tests in 0.100s\n\nOK\n"
            ),
            "integration_log_contains_skips": (
                f"stage9_evidence_commit={COMMIT}\n"
                "test_database (tests.test_postgres.PostgresTests.test_database) "
                "... skipped 'optional'\n"
                "Ran 1 test in 0.100s\n\nOK (skipped=1)\n"
            ),
            "integration_tests_not_passed": (
                f"stage9_evidence_commit={COMMIT}\n"
                "test_database (tests.test_postgres.PostgresTests.test_database) ... FAIL\n"
                "Ran 1 test in 0.100s\n\nFAILED (failures=1)\n"
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "integration.log"
            for error, output in cases.items():
                with self.subTest(error=error):
                    path.write_text(output, encoding="utf-8")
                    with self.assertRaisesRegex(Stage9RuntimeEvidenceError, error):
                        _read_integration_log(path, expected_commit=COMMIT)

    def test_lane_and_source_validation_fail_closed(self):
        cases = []

        matrix = _matrix()
        matrix["lanes"] = None
        cases.append(("matrix_lanes_required", matrix))

        matrix = _matrix()
        matrix["lanes"]["registration"] = "invalid"
        cases.append(("matrix_lane_not_passed", matrix))

        matrix = _matrix()
        matrix["lanes"]["registration"]["status"] = "failed"
        cases.append(("matrix_lane_not_passed", matrix))

        matrix = _matrix()
        matrix["lanes"]["registration"]["skipped"] = 1
        cases.append(("matrix_lane_contains_skips", matrix))

        matrix = _matrix()
        matrix["lanes"]["registration"]["observed_tests"] = []
        cases.append(("matrix_lane_observed_tests_required", matrix))

        matrix = _matrix()
        matrix["lanes"]["registration"]["observed_tests"] = {}
        cases.append(("matrix_lane_observed_tests_required", matrix))

        matrix = _matrix()
        matrix["lanes"]["market"]["observed_tests"] = {LOCAL_TEST: "failed"}
        cases.append(("conflicting_test_result", matrix))

        for error, matrix in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(Stage9RuntimeEvidenceError, error):
                    build_runtime_evidence(
                        _config(),
                        matrix,
                        _integration(POSTGRES_TEST),
                        _integration(REDIS_TEST),
                        commit=COMMIT,
                    )

        postgres = _integration(POSTGRES_TEST)
        postgres["observed_tests"][LOCAL_TEST] = "failed"
        with self.assertRaisesRegex(Stage9RuntimeEvidenceError, "conflicting_test_result"):
            build_runtime_evidence(
                _config(),
                _matrix(),
                postgres,
                _integration(REDIS_TEST),
                commit=COMMIT,
            )

    def test_registry_and_transition_validation_fail_closed(self):
        cases = []

        matrix = _matrix()
        matrix["commit"] = "b" * 40
        cases.append(("matrix_commit_mismatch", _config(), matrix))

        matrix = _matrix()
        matrix["stage9_passed"] = False
        cases.append(("matrix_stage9_gate_not_passed", _config(), matrix))

        config = _config()
        config["registry_bindings"] = None
        cases.append(("registry_bindings_required", config, _matrix()))

        config = _config()
        config["registry_bindings"]["REG-001"] = "invalid"
        cases.append(("invalid_registry_binding", config, _matrix()))

        matrix = _matrix()
        matrix["market_rows"]["MKT-004"] = "invalid"
        cases.append(("matrix_deferred_row_mismatch", _config(), matrix))

        matrix = _matrix()
        matrix["market_rows"]["MKT-004"]["status"] = "passed"
        cases.append(("matrix_deferred_row_mismatch", _config(), matrix))

        matrix = _matrix()
        matrix["market_rows"]["MKT-001"] = "invalid"
        cases.append(("matrix_row_not_passed", _config(), matrix))

        matrix = _matrix()
        matrix["market_rows"]["MKT-001"]["status"] = "failed"
        cases.append(("matrix_row_not_passed", _config(), matrix))

        matrix = _matrix()
        matrix["lanes"]["registration"]["observed_tests"][LOCAL_TEST] = "failed"
        cases.append(("registry_tests_not_passed", _config(), matrix))

        config = _config()
        config["registry_bindings"]["MIG-001"]["test_ids"] = [LOCAL_TEST]
        cases.append(("postgres_evidence_required", config, _matrix()))

        config = _config()
        config["registry_bindings"]["OTP-001"]["test_ids"] = [LOCAL_TEST]
        cases.append(("redis_evidence_required", config, _matrix()))

        config = _config()
        config["transitions"][0]["test_ids"] = ["tests.missing.Case.test_missing"]
        cases.append(("transition_evidence_incomplete", config, _matrix()))

        for error, config, matrix in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(Stage9RuntimeEvidenceError, error):
                    build_runtime_evidence(
                        config,
                        matrix,
                        _integration(POSTGRES_TEST),
                        _integration(REDIS_TEST),
                        commit=COMMIT,
                    )

    def test_main_writes_commit_bound_evidence_and_returns_one_on_invalid_input(self):
        integration_template = (
            "stage9_evidence_commit={commit}\n"
            "{method} ({test_id}) ... ok\n"
            "Ran 1 test in 0.100s\n\nOK\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            matrix_path = root / "matrix.json"
            postgres_path = root / "postgres.log"
            redis_path = root / "redis.log"
            output_path = root / "result.json"
            config_path.write_text(json.dumps(_config()), encoding="utf-8")
            matrix_path.write_text(json.dumps(_matrix()), encoding="utf-8")
            postgres_path.write_text(
                integration_template.format(
                    commit=COMMIT,
                    method="test_database",
                    test_id=POSTGRES_TEST,
                ),
                encoding="utf-8",
            )
            redis_path.write_text(
                integration_template.format(
                    commit=COMMIT,
                    method="test_atomic",
                    test_id=REDIS_TEST,
                ),
                encoding="utf-8",
            )
            argv = [
                "--config",
                str(config_path),
                "--matrix",
                str(matrix_path),
                "--postgres-log",
                str(postgres_path),
                "--redis-log",
                str(redis_path),
                "--output",
                str(output_path),
            ]
            with patch.object(
                runtime_evidence.subprocess,
                "check_output",
                return_value=COMMIT + "\n",
            ):
                self.assertEqual(runtime_evidence.main(argv), 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["commit"], COMMIT)
            self.assertTrue(payload["stage9_passed"])

            config_path.write_text("[]", encoding="utf-8")
            stderr = io.StringIO()
            with patch.object(
                runtime_evidence.subprocess,
                "check_output",
                return_value=COMMIT + "\n",
            ), redirect_stderr(stderr):
                self.assertEqual(runtime_evidence.main(argv), 1)
            self.assertIn("object_required", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
