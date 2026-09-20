import hashlib
import json
from pathlib import Path
import re
import unittest


class RecentCharacterizationContinuationTwoContractTests(unittest.TestCase):
    def test_manifest_indexes_five_users_public_units_and_is_sha_bound_to_first_continuation(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        shard_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-2.json"
        )
        shard_file_path = repo_root / shard_rel_path
        self.assertTrue(
            shard_file_path.is_file(),
            f"Continuation 2 file not found at {shard_file_path}",
        )

        shard_bytes = shard_file_path.read_bytes()
        data = json.loads(shard_bytes.decode("utf-8"))

        expected_top_keys = {"schema_version", "stage", "predecessor_inventory", "records"}
        self.assertEqual(set(data.keys()), expected_top_keys)
        self.assertIs(type(data["schema_version"]), int)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["stage"], "P1-00b")

        pred_binding = data["predecessor_inventory"]
        expected_pred_keys = {"path", "sha256", "last_commit"}
        self.assertEqual(set(pred_binding.keys()), expected_pred_keys)
        expected_pred_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json"
        )
        self.assertEqual(pred_binding["path"], expected_pred_path)
        expected_pred_sha256 = (
            "6756471cd3126794f0d7f9e4fb0995e5e395fa599cf137577d6f4e59a6a67e3c"
        )
        self.assertEqual(pred_binding["sha256"], expected_pred_sha256)
        expected_pred_last_commit = "2852e5a8f0a4aca4758103e9eae57ec4eaca9107"
        self.assertEqual(pred_binding["last_commit"], expected_pred_last_commit)

        pred_file_path = repo_root / expected_pred_path
        self.assertTrue(
            pred_file_path.is_file(),
            f"Predecessor file not found at {pred_file_path}",
        )
        actual_pred_bytes = pred_file_path.read_bytes()
        actual_pred_sha256 = hashlib.sha256(actual_pred_bytes).hexdigest()
        self.assertEqual(pred_binding["sha256"], actual_pred_sha256)

        commit_regex = re.compile(r"^[0-9a-f]{40}$")
        self.assertRegex(pred_binding["last_commit"], commit_regex)

        records = data["records"]
        self.assertEqual(len(records), 5)

        expected_records = [
            {
                "path": "tests/test_p1_00b_characterization_users_public_read_ordinary_customer_allowed.py",
                "sha256": "d25e93e15330c2f3626e5f1b9220b6348c16c587fc6fa08b69c561a8aa21b176",
                "last_commit": "12a9bf73db78cbe6d883b0436578f06c37067fbb",
                "seed_ids": [
                    "API-IDENTITY-GET-USERS-PUBLIC-READ-PUBLIC-USER-L548",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_users_public_read_ordinary_customer_allowed.py::UsersPublicReadOrdinaryCustomerAllowedCharacterizationTests::test_active_customer_viewer_allowed_ordinary_target_returns_contact_with_exact_lookup_order",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_users_public_read_ordinary_customer_denied.py",
                "sha256": "8428bb8c2cfb853a5bc20b78b15e48917fcb5338c3f367aa9333f32ab984bdd0",
                "last_commit": "ae3658ba3d5009999ea94e1a54e2deb11ce891fa",
                "seed_ids": [
                    "API-IDENTITY-GET-USERS-PUBLIC-READ-PUBLIC-USER-L548",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_users_public_read_ordinary_customer_denied.py::UsersPublicReadOrdinaryCustomerDeniedCharacterizationTests::test_active_customer_viewer_disallowed_ordinary_target_raises_exact_404_with_exact_lookup_order",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_users_public_read_accountant_customer_allowed.py",
                "sha256": "085f75263720538d6cd8b4d82b56c813559a6e465de6323965ed886e0647e5be",
                "last_commit": "4901c6929b42fa002d28640ab61b206400e61e60",
                "seed_ids": [
                    "API-IDENTITY-GET-USERS-PUBLIC-READ-PUBLIC-USER-L548",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_users_public_read_accountant_customer_allowed.py::UsersPublicReadAccountantCustomerAllowedCharacterizationTests::test_active_customer_viewer_allowed_accountant_target_returns_owner_contact_with_exact_lookup_order",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_users_public_read_accountant_customer_denied.py",
                "sha256": "528a04b1b2eff501148a44122293108158354237aed0c8e7b77e284a46c94582",
                "last_commit": "009f858346b788fd8d0d19f59a0bd6345d0db8c6",
                "seed_ids": [
                    "API-IDENTITY-GET-USERS-PUBLIC-READ-PUBLIC-USER-L548",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_users_public_read_accountant_customer_denied.py::UsersPublicReadAccountantCustomerDeniedCharacterizationTests::test_active_customer_viewer_disallowed_accountant_target_raises_exact_404_with_exact_lookup_order",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_users_public_customer_short_circuits_batch.py",
                "sha256": "a5d41f07d810b6f0ec68406e1152e022b02560e8ddc77a6b33d397bb8409da3e",
                "last_commit": "521b2b3d7d537a730f08915009aabd959c7b790e",
                "seed_ids": [
                    "API-IDENTITY-GET-USERS-PUBLIC-SEARCH-PUBLIC-USERS-L428",
                    "API-IDENTITY-GET-USERS-PUBLIC-LIST-PROJECT-USERS-DIRECTORY-L512",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_users_public_customer_short_circuits_batch.py::UsersPublicCustomerShortCircuitsCharacterizationTests::test_search_public_users_active_customer_without_allowed_targets_returns_empty_before_query_execution",
                    "tests/test_p1_00b_characterization_users_public_customer_short_circuits_batch.py::UsersPublicCustomerShortCircuitsCharacterizationTests::test_list_project_users_directory_active_customer_raises_exact_403_before_accountant_lookup_or_query",
                ],
            },
        ]

        expected_record_keys = {"path", "sha256", "last_commit", "seed_ids", "test_selectors"}
        collected_seed_ids: list[str] = []
        collected_selectors: list[str] = []

        for record, expected in zip(records, expected_records):
            self.assertEqual(set(record.keys()), expected_record_keys)
            self.assertEqual(record["path"], expected["path"])
            self.assertEqual(record["sha256"], expected["sha256"])
            self.assertEqual(record["last_commit"], expected["last_commit"])
            self.assertRegex(record["last_commit"], commit_regex)
            self.assertEqual(record["seed_ids"], expected["seed_ids"])
            self.assertEqual(record["test_selectors"], expected["test_selectors"])

            collected_seed_ids.extend(record["seed_ids"])
            collected_selectors.extend(record["test_selectors"])

            rec_file_path = repo_root / record["path"]
            self.assertTrue(rec_file_path.is_file(), f"Referenced file not found: {rec_file_path}")
            rec_file_bytes = rec_file_path.read_bytes()
            rec_file_sha256 = hashlib.sha256(rec_file_bytes).hexdigest()
            self.assertEqual(rec_file_sha256, record["sha256"])

        self.assertEqual(len(collected_seed_ids), 6)
        self.assertEqual(len(collected_selectors), 6)

        surface_api_path = repo_root / "docs/refactor/two-server/section-1/inventory/surface-api.json"
        self.assertTrue(
            surface_api_path.is_file(),
            f"surface-api.json not found at {surface_api_path}",
        )
        surface_api_data = json.loads(surface_api_path.read_bytes().decode("utf-8"))
        surface_api_behavior_ids = {
            entry["behavior_id"] for entry in surface_api_data.get("entries", [])
        }

        unique_shard_seeds = set(collected_seed_ids)
        expected_unique_seeds = {
            "API-IDENTITY-GET-USERS-PUBLIC-READ-PUBLIC-USER-L548",
            "API-IDENTITY-GET-USERS-PUBLIC-SEARCH-PUBLIC-USERS-L428",
            "API-IDENTITY-GET-USERS-PUBLIC-LIST-PROJECT-USERS-DIRECTORY-L512",
        }
        self.assertEqual(unique_shard_seeds, expected_unique_seeds)
        self.assertEqual(len(unique_shard_seeds), 3)
        self.assertTrue(unique_shard_seeds.issubset(surface_api_behavior_ids))

        base_path = repo_root / "docs/refactor/two-server/section-1/inventory/recent-characterization.json"
        self.assertTrue(base_path.is_file(), f"Base evidence not found at {base_path}")
        base_data = json.loads(base_path.read_bytes().decode("utf-8"))
        base_seeds = set()
        for rec in base_data.get("records", []):
            base_seeds.update(rec.get("seed_ids", []))

        pred_data = json.loads(actual_pred_bytes.decode("utf-8"))
        continuation_1_seeds = set()
        for rec in pred_data.get("records", []):
            continuation_1_seeds.update(rec.get("seed_ids", []))

        prior_evidence_seeds = base_seeds | continuation_1_seeds
        self.assertTrue(unique_shard_seeds.issubset(prior_evidence_seeds))
        self.assertEqual(len(unique_shard_seeds - prior_evidence_seeds), 0)


if __name__ == "__main__":
    unittest.main()
