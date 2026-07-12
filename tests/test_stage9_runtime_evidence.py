from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
