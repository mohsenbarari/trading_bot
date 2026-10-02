from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import unittest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()


class RecentCharacterizationContinuationFiveContractTests(unittest.TestCase):
    def _assert_schema_version(self, mapping: dict) -> None:
        if "schema_version" not in mapping:
            raise AssertionError("schema_version: missing required field")
        value = mapping["schema_version"]
        if type(value) is not int:
            raise AssertionError(
                f"schema_version: exact builtin int required, got {type(value).__name__}"
            )
        if value != 1:
            raise AssertionError(f"schema_version: wrong value, expected 1, got {value}")

    def test_first_two_recovery_records_exact_provenance_and_seed_membership(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_5_rel_path = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-5.json"
        continuation_5_path = repo_root / continuation_5_rel_path
        self.assertTrue(continuation_5_path.is_file(), f"Continuation 5 not found at {continuation_5_path}")

        continuation_5_bytes = continuation_5_path.read_bytes()
        continuation_5_data = json.loads(continuation_5_bytes.decode("utf-8"))

        self._assert_schema_version(continuation_5_data)
        self.assertEqual(continuation_5_data.get("stage"), "P1-00b")

        predecessor = continuation_5_data.get("predecessor_inventory", {})
        expected_predecessor_path = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json"
        expected_predecessor_sha = "38fa121e4b18991360acfae077c2d1f81b925e921e3005ceb95cfe5fb018aac5"
        expected_predecessor_commit = "bcf435472e73b916a9555650f99af948eb257004"

        self.assertEqual(predecessor.get("path"), expected_predecessor_path)
        self.assertEqual(predecessor.get("sha256"), expected_predecessor_sha)
        self.assertEqual(predecessor.get("last_commit"), expected_predecessor_commit)

        predecessor_file_path = repo_root / expected_predecessor_path
        self.assertTrue(predecessor_file_path.is_file(), f"Predecessor file not found at {predecessor_file_path}")
        predecessor_bytes = predecessor_file_path.read_bytes()
        self.assertEqual(hashlib.sha256(predecessor_bytes).hexdigest(), expected_predecessor_sha)

        records = continuation_5_data.get("records", [])
        self.assertEqual(len(records), 2)

        expected_records = [
            {
                "path": "tests/test_p1_00b_characterization_get_single_session_recovery_status_missing_refresh.py",
                "sha256": "24cdecb61baff66b49a8985b984c28eed4cbca87f4a6e2004a44f1713b822e93",
                "last_commit": "e5ef2655414d83e775fc707dc535311e630c0f1d",
                "seed_ids": [
                    "API-AUTH-GET-SESSIONS-GET-SINGLE-SESSION-RECOVERY-STATUS-L749"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_get_single_session_recovery_status_missing_refresh.py::RecoveryStatusMissingRefreshCharacterizationTests::test_approved_active_session_without_refresh_token_returns_access_only"
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_pending_recovery_prompts_admin_empty.py",
                "sha256": "4b995d7a3cc395391b63d64ec8667d855bf6d4d0ae80c630fd83574c9eb6796f",
                "last_commit": "6056eb8e3da7b98d4cdebe3ae94d619852a8ae47",
                "seed_ids": [
                    "API-AUTH-GET-SESSIONS-GET-PENDING-SINGLE-SESSION-RECOVERY-PROMPTS-L788"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_pending_recovery_prompts_admin_empty.py::PendingRecoveryPromptsAdminEmptyCharacterizationTests::test_authorized_admin_empty_lookup_returns_empty_without_serialization"
                ],
            },
        ]

        continuation_5_seeds = []
        for i, expected in enumerate(expected_records):
            actual = records[i]
            self.assertEqual(actual["path"], expected["path"])
            self.assertEqual(actual["sha256"], expected["sha256"])
            self.assertEqual(actual["last_commit"], expected["last_commit"])
            self.assertEqual(actual["seed_ids"], expected["seed_ids"])
            self.assertEqual(actual["test_selectors"], expected["test_selectors"])

            file_path = repo_root / expected["path"]
            self.assertTrue(file_path.is_file(), f"Record file not found at {file_path}")
            file_bytes = file_path.read_bytes()
            self.assertEqual(hashlib.sha256(file_bytes).hexdigest(), expected["sha256"])
            file_text = file_bytes.decode("utf-8")
            for selector in expected["test_selectors"]:
                parts = selector.split("::")
                self.assertEqual(len(parts), 3)
                class_name, method_name = parts[1], parts[2]
                self.assertIn(f"class {class_name}", file_text)
                self.assertIn(f"def {method_name}(", file_text)

            continuation_5_seeds.extend(actual["seed_ids"])

        continuation_5_seed_set = set(continuation_5_seeds)
        self.assertEqual(len(continuation_5_seed_set), 2)

        surface_api_path = repo_root / "docs/refactor/two-server/section-1/inventory/surface-api.json"
        self.assertTrue(surface_api_path.is_file(), f"surface-api.json not found at {surface_api_path}")
        surface_api_data = json.loads(surface_api_path.read_bytes().decode("utf-8"))
        surface_api_seeds = {entry["behavior_id"] for entry in surface_api_data.get("entries", [])}
        self.assertTrue(continuation_5_seed_set.issubset(surface_api_seeds))

        manifest_path = repo_root / "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
        self.assertTrue(manifest_path.is_file(), f"seed-handler-coverage.json not found at {manifest_path}")
        manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))

        baseline_path = repo_root / manifest["evidence_sources"]["baseline_path"]
        baseline_data = json.loads(baseline_path.read_bytes().decode("utf-8"))
        blocker_items = baseline_data.get("blocker_registry", {}).get("items", [])
        blocker = next(
            item for item in blocker_items
            if item.get("id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
            or item.get("blocker_id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
        )
        reason = blocker["reason"]
        start_delim = "Evidenced seeds are "
        end_delim = ". Sibling family"
        start_idx = reason.index(start_delim) + len(start_delim)
        end_idx = reason.index(end_delim, start_idx)
        baseline_seed_set = {s.strip() for s in reason[start_idx:end_idx].split(",") if s.strip()}
        self.assertEqual(len(baseline_seed_set), 135)

        prior_continuation_paths = [
            repo_root / manifest["evidence_sources"]["base_path"],
            repo_root / manifest["evidence_sources"]["continuation_path"],
            repo_root / manifest["evidence_sources"]["continuation_2_path"],
            repo_root / manifest["evidence_sources"]["continuation_3_path"],
            repo_root / manifest["evidence_sources"]["continuation_4_path"],
        ]
        prior_recent_seeds = set()
        for p in prior_continuation_paths:
            self.assertTrue(p.is_file(), f"Prior evidence file not found at {p}")
            data = json.loads(p.read_bytes().decode("utf-8"))
            for record in data.get("records", []):
                prior_recent_seeds.update(record.get("seed_ids", []))

        prior_evidenced_seed_set = baseline_seed_set | prior_recent_seeds
        self.assertEqual(len(prior_evidenced_seed_set), 381)

        self.assertTrue(continuation_5_seed_set.isdisjoint(baseline_seed_set))
        self.assertTrue(continuation_5_seed_set.isdisjoint(prior_recent_seeds))
        self.assertTrue(continuation_5_seed_set.isdisjoint(prior_evidenced_seed_set))

    def test_schema_version_rejects_invalid_types_and_values_with_specific_failure(self):
        repo_root = Path(__file__).resolve().parent.parent
        continuation_5_rel_path = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-5.json"
        continuation_5_path = repo_root / continuation_5_rel_path
        self.assertTrue(continuation_5_path.is_file(), f"Continuation 5 not found at {continuation_5_path}")

        canonical_bytes = continuation_5_path.read_bytes()
        continuation_5_data = json.loads(canonical_bytes.decode("utf-8"))

        self._assert_schema_version(continuation_5_data)

        valid_copy = dict(continuation_5_data)
        valid_copy["schema_version"] = 1
        self._assert_schema_version(valid_copy)

        class IntSubclass(int):
            pass

        cases = [
            ("missing_field", None, "schema_version: missing required field"),
            ("bool_true", True, "schema_version: exact builtin int required, got bool"),
            ("bool_false", False, "schema_version: exact builtin int required, got bool"),
            ("float_1_0", 1.0, "schema_version: exact builtin int required, got float"),
            ("str_1", "1", "schema_version: exact builtin int required, got str"),
            ("none", None, "schema_version: exact builtin int required, got NoneType"),
            ("int_subclass_1", IntSubclass(1), "schema_version: exact builtin int required, got IntSubclass"),
            ("int_0", 0, "schema_version: wrong value, expected 1, got 0"),
            ("int_2", 2, "schema_version: wrong value, expected 1, got 2"),
        ]

        for label, val, expected_msg in cases:
            with self.subTest(case=label):
                mutated = dict(continuation_5_data)
                if label == "missing_field":
                    mutated.pop("schema_version", None)
                else:
                    mutated["schema_version"] = val
                with self.assertRaises(AssertionError) as ctx:
                    self._assert_schema_version(mutated)
                self.assertEqual(str(ctx.exception), expected_msg)

        self.assertEqual(continuation_5_path.read_bytes(), canonical_bytes)


if __name__ == "__main__":
    unittest.main()
