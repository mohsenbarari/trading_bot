import hashlib
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
try:
    from tests.test_p1_00_characterization_network_isolation import (
        install_p1_00_network_fence,
    )

    install_p1_00_network_fence()
except ImportError:
    pass


class RecentCharacterizationContinuationSixContractTests(unittest.TestCase):
    def _assert_schema_version(self, data, expected: int = 1):
        if isinstance(data, dict):
            if "schema_version" not in data:
                self.fail("schema_version: missing required field")
            val = data["schema_version"]
        else:
            val = data
        if type(val) is not int:
            self.fail(
                f"schema_version: exact builtin int required, got {type(val).__name__}"
            )
        if val != expected:
            self.fail(f"schema_version: wrong value, expected {expected}, got {val}")

    def test_remaining_three_recovery_records_exact_provenance_and_seed_membership(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_6_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-6.json"
        )
        continuation_6_path = repo_root / continuation_6_rel_path
        self.assertTrue(continuation_6_path.is_file(), f"Continuation 6 not found at {continuation_6_path}")

        continuation_6_bytes = continuation_6_path.read_bytes()
        continuation_6_data = json.loads(continuation_6_bytes.decode("utf-8"))

        self._assert_schema_version(continuation_6_data)
        self.assertEqual(continuation_6_data.get("stage"), "P1-00b")

        predecessor_binding = continuation_6_data.get("predecessor_inventory", {})
        expected_predecessor_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-5.json"
        )
        expected_predecessor_sha = (
            "6ed554a3261009905d4364397199bcf01ec902de55c6b09b33d0770141dea806"
        )
        expected_predecessor_commit = "f9c21b3bfc04e3711ad5882ce112f6abdcf582b9"

        self.assertEqual(predecessor_binding.get("path"), expected_predecessor_path)
        self.assertEqual(predecessor_binding.get("sha256"), expected_predecessor_sha)
        self.assertEqual(predecessor_binding.get("last_commit"), expected_predecessor_commit)

        continuation_5_path = repo_root / expected_predecessor_path
        self.assertTrue(continuation_5_path.is_file(), f"Continuation 5 not found at {continuation_5_path}")
        continuation_5_bytes = continuation_5_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(continuation_5_bytes).hexdigest(),
            expected_predecessor_sha,
        )

        records = continuation_6_data.get("records", [])
        self.assertEqual(len(records), 3)

        expected_records_metadata = [
            {
                "path": "tests/test_p1_00b_characterization_request_single_session_recovery_identity.py",
                "sha256": "c08d70948e71e6bfccd56fcafe45d9263cf8119e3370f5f2bc71067202730520",
                "last_commit": "af3f0bc88111ad373a92759419e6708a96a9e74c",
                "seed_ids": [
                    "API-AUTH-POST-SESSIONS-REQUEST-SINGLE-SESSION-RECOVERY-IDENTITY-L807"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_request_single_session_recovery_identity.py::TestRequestSingleSessionRecoveryIdentityCharacterization::test_invalid_uuid_aborts_after_non_accountant_guard",
                    "tests/test_p1_00b_characterization_request_single_session_recovery_identity.py::TestRequestSingleSessionRecoveryIdentityCharacterization::test_valid_uuid_non_pending_status_rejected_after_admin_access",
                    "tests/test_p1_00b_characterization_request_single_session_recovery_identity.py::TestRequestSingleSessionRecoveryIdentityCharacterization::test_pending_review_success_transitions_state_commits_warns_sms_and_serializes",
                    "tests/test_p1_00b_characterization_request_single_session_recovery_identity.py::TestRequestSingleSessionRecoveryIdentityCharacterization::test_pending_review_sms_success_after_commit_and_publishes",
                    "tests/test_p1_00b_characterization_request_single_session_recovery_identity.py::TestRequestSingleSessionRecoveryIdentityCharacterization::test_pending_review_publish_failure_propagates_after_commit",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_cancel_recovery_malformed_uuid.py",
                "sha256": "642c9a960a4a20d021995a41ddb4681cf3471674da0025073018bc19e48dad39",
                "last_commit": "647f10d37f2298336a8ca41d052bded46c68e668",
                "seed_ids": [
                    "API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_cancel_recovery_malformed_uuid.py::CancelRecoveryMalformedUuidCharacterizationTests::test_malformed_uuid_rejects_before_lookup_or_db_work"
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_cancel_recovery_no_active.py",
                "sha256": "22c801bc4848a261bf53a14a02d1c679ead7c7f47d35dedbebdaf303d9f49846",
                "last_commit": "4dcd6e2bfb57d5d1015d00a909afdfc8eaf3356e",
                "seed_ids": [
                    "API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_cancel_recovery_no_active.py::CancelRecoveryNoActiveCharacterizationTests::test_valid_uuid_without_active_recovery_stops_before_mutation_or_delivery"
                ],
            },
        ]

        expected_record_keys = {"path", "sha256", "last_commit", "seed_ids", "test_selectors"}
        for i, expected in enumerate(expected_records_metadata):
            record = records[i]
            self.assertEqual(set(record.keys()), expected_record_keys)
            self.assertEqual(record["path"], expected["path"])
            self.assertEqual(record["sha256"], expected["sha256"])
            self.assertEqual(record["last_commit"], expected["last_commit"])
            self.assertEqual(record["seed_ids"], expected["seed_ids"])
            self.assertEqual(record["test_selectors"], expected["test_selectors"])

            live_file_path = repo_root / expected["path"]
            self.assertTrue(live_file_path.is_file(), f"File {live_file_path} not found")
            live_bytes = live_file_path.read_bytes()
            self.assertEqual(
                hashlib.sha256(live_bytes).hexdigest(),
                expected["sha256"],
                f"SHA256 mismatch for {expected['path']}",
            )
            live_text = live_bytes.decode("utf-8")
            for selector in expected["test_selectors"]:
                parts = selector.split("::")
                self.assertEqual(len(parts), 3)
                self.assertEqual(parts[0], expected["path"])
                cls_name = parts[1]
                func_name = parts[2]
                self.assertIn(f"class {cls_name}", live_text)
                self.assertIn(f"def {func_name}(", live_text)

        # Deliberate duplicate cancellation seed across records 2 and 3
        self.assertEqual(
            records[1]["seed_ids"],
            ["API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717"],
        )
        self.assertEqual(
            records[2]["seed_ids"],
            ["API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717"],
        )
        self.assertEqual(records[1]["seed_ids"], records[2]["seed_ids"])
        self.assertNotEqual(records[1]["path"], records[2]["path"])

        all_continuation_6_seeds = []
        for r in records:
            all_continuation_6_seeds.extend(r["seed_ids"])
        self.assertEqual(len(all_continuation_6_seeds), 3)
        distinct_new_seeds = set(all_continuation_6_seeds)
        self.assertEqual(len(distinct_new_seeds), 2)
        expected_distinct_new_seeds = {
            "API-AUTH-POST-SESSIONS-REQUEST-SINGLE-SESSION-RECOVERY-IDENTITY-L807",
            "API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717",
        }
        self.assertEqual(distinct_new_seeds, expected_distinct_new_seeds)

        # Seed membership in surface-api.json
        surface_api_path = repo_root / "docs/refactor/two-server/section-1/inventory/surface-api.json"
        self.assertTrue(surface_api_path.is_file(), f"Surface API inventory not found at {surface_api_path}")
        surface_api_data = json.loads(surface_api_path.read_bytes().decode("utf-8"))
        surface_api_seed_ids = {entry["behavior_id"] for entry in surface_api_data.get("entries", [])}
        self.assertEqual(len(surface_api_seed_ids), 217)
        self.assertTrue(
            distinct_new_seeds.issubset(surface_api_seed_ids),
            "Continuation-6 seeds not found in surface-api.json",
        )

        # Disjointness from all 383 previously evidenced seeds
        coverage_manifest_path = repo_root / "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
        self.assertTrue(coverage_manifest_path.is_file(), f"Coverage manifest not found at {coverage_manifest_path}")
        coverage_manifest = json.loads(coverage_manifest_path.read_bytes().decode("utf-8"))
        evidence_sources = coverage_manifest["evidence_sources"]

        # 1. Baseline seeds from scenario-records.json
        baseline_path = repo_root / evidence_sources["baseline_path"]
        self.assertTrue(baseline_path.is_file())
        scenario_data = json.loads(baseline_path.read_bytes().decode("utf-8"))
        blocker = next(
            item
            for item in scenario_data["blocker_registry"]["items"]
            if item.get("id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
            or item.get("blocker_id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
        )
        reason = blocker["reason"]
        start_delim = "Evidenced seeds are "
        end_delim = ". Sibling family"
        start_idx = reason.index(start_delim) + len(start_delim)
        end_idx = reason.index(end_delim, start_idx)
        baseline_seeds = {s.strip() for s in reason[start_idx:end_idx].split(",") if s.strip()}
        self.assertEqual(len(baseline_seeds), 135)

        # 2. Reconciled seeds from base and continuations 1 through 5
        prior_evidence_paths = [
            evidence_sources["base_path"],
            evidence_sources["continuation_path"],
            evidence_sources["continuation_2_path"],
            evidence_sources["continuation_3_path"],
            evidence_sources["continuation_4_path"],
            evidence_sources["continuation_5_path"],
        ]
        prior_reconciled_seeds = set()
        for p in prior_evidence_paths:
            file_path = repo_root / p
            self.assertTrue(file_path.is_file(), f"Evidence file not found: {file_path}")
            data = json.loads(file_path.read_bytes().decode("utf-8"))
            for r in data.get("records", []):
                prior_reconciled_seeds.update(r.get("seed_ids", []))

        all_prior_383_seeds = baseline_seeds | prior_reconciled_seeds
        self.assertEqual(
            len(all_prior_383_seeds),
            383,
            f"Expected exactly 383 prior evidenced seeds, found {len(all_prior_383_seeds)}",
        )

        self.assertTrue(
            distinct_new_seeds.isdisjoint(all_prior_383_seeds),
            f"Distinct new seeds {distinct_new_seeds} overlap with prior 383 evidenced seeds",
        )

    def test_schema_version_rejects_invalid_types_and_values_with_specific_failure(self):
        repo_root = Path(__file__).resolve().parent.parent
        continuation_6_path = (
            repo_root
            / "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-6.json"
        )
        self.assertTrue(
            continuation_6_path.is_file(),
            f"Continuation 6 not found at {continuation_6_path}",
        )

        canonical_bytes = continuation_6_path.read_bytes()
        canonical_data = json.loads(canonical_bytes.decode("utf-8"))

        # Positively validate canonical JSON and builtin int 1
        self._assert_schema_version(canonical_data)
        canonical_with_builtin_int_1 = dict(canonical_data)
        canonical_with_builtin_int_1["schema_version"] = int(1)
        self._assert_schema_version(canonical_with_builtin_int_1)
        self._assert_schema_version(1)

        # Missing field
        missing_field_data = dict(canonical_data)
        del missing_field_data["schema_version"]
        with self.assertRaisesRegex(AssertionError, r"schema_version: missing required field"):
            self._assert_schema_version(missing_field_data)

        # Non-exact builtin integer types
        class _IntSubclass(int):
            pass

        invalid_type_cases = [
            (True, "bool"),
            (False, "bool"),
            (1.0, "float"),
            ("1", "str"),
            (None, "NoneType"),
            (_IntSubclass(1), "_IntSubclass"),
        ]
        for bad_val, type_name in invalid_type_cases:
            corrupt_data = dict(canonical_data)
            corrupt_data["schema_version"] = bad_val
            with self.assertRaisesRegex(
                AssertionError,
                rf"schema_version: exact builtin int required, got {type_name}",
            ):
                self._assert_schema_version(corrupt_data)

        # Wrong builtin integer values
        for wrong_val in (0, 2):
            corrupt_data = dict(canonical_data)
            corrupt_data["schema_version"] = wrong_val
            with self.assertRaisesRegex(
                AssertionError,
                rf"schema_version: wrong value, expected 1, got {wrong_val}",
            ):
                self._assert_schema_version(corrupt_data)

        self.assertEqual(continuation_6_path.read_bytes(), canonical_bytes)


if __name__ == "__main__":
    unittest.main()
