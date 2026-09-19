import hashlib
import json
from pathlib import Path
import unittest


class SeedHandlerCoverageContractTests(unittest.TestCase):
    def test_canonical_remaining_seed_count_matches_inventory_and_reconciled_records(self):
        repo_root = Path(__file__).resolve().parent.parent

        manifest_rel_path = "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
        manifest_path = repo_root / manifest_rel_path
        self.assertTrue(manifest_path.is_file(), f"Manifest not found at {manifest_path}")

        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))

        expected_top_level_keys = {
            "schema_version",
            "stage",
            "assignment_id",
            "inventory",
            "evidence_sources",
            "counts",
            "coverage_status",
            "canonical_blockers",
        }
        self.assertEqual(set(manifest.keys()), expected_top_level_keys)
        self.assertIs(type(manifest["schema_version"]), int)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["stage"], "P1-00b")
        self.assertEqual(manifest["assignment_id"], "WA-P1-00B-FREEZE-20260904-001")
        self.assertEqual(manifest["coverage_status"], "PARTIAL")
        self.assertEqual(
            manifest["canonical_blockers"],
            [
                "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
            ],
        )

        expected_inventory_keys = {"expected_seed_count", "paths"}
        self.assertEqual(set(manifest["inventory"].keys()), expected_inventory_keys)
        self.assertEqual(manifest["inventory"]["expected_seed_count"], 465)
        expected_surface_paths = [
            "docs/refactor/two-server/section-1/inventory/surface-api.json",
            "docs/refactor/two-server/section-1/inventory/surface-bot.json",
            "docs/refactor/two-server/section-1/inventory/surface-web.json",
            "docs/refactor/two-server/section-1/inventory/surface-jobs.json",
        ]
        self.assertEqual(manifest["inventory"]["paths"], expected_surface_paths)

        expected_evidence_keys = {"baseline_path", "base_path", "continuation_path"}
        self.assertEqual(set(manifest["evidence_sources"].keys()), expected_evidence_keys)
        self.assertEqual(
            manifest["evidence_sources"]["baseline_path"],
            "docs/refactor/two-server/section-1/inventory/scenario-records.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["base_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json",
        )

        expected_counts_keys = {
            "baseline_evidenced_seed_count",
            "reconciled_new_seed_count",
            "evidenced_seed_count",
            "remaining_seed_count",
        }
        self.assertEqual(set(manifest["counts"].keys()), expected_counts_keys)
        self.assertEqual(manifest["counts"]["baseline_evidenced_seed_count"], 135)
        self.assertEqual(manifest["counts"]["reconciled_new_seed_count"], 204)
        self.assertEqual(manifest["counts"]["evidenced_seed_count"], 339)
        self.assertEqual(manifest["counts"]["remaining_seed_count"], 126)

        expected_per_file_counts = [217, 203, 30, 15]
        inventory_behavior_ids = []
        for rel_path, expected_len in zip(expected_surface_paths, expected_per_file_counts):
            surface_file_path = repo_root / rel_path
            self.assertTrue(surface_file_path.is_file(), f"Surface file not found at {surface_file_path}")
            surface_data = json.loads(surface_file_path.read_bytes().decode("utf-8"))
            entries = surface_data["entries"]
            self.assertEqual(len(entries), expected_len, f"Entry count mismatch for {rel_path}")
            file_ids = [entry["behavior_id"] for entry in entries]
            self.assertEqual(len(file_ids), expected_len)
            inventory_behavior_ids.extend(file_ids)

        self.assertEqual(len(inventory_behavior_ids), 465)
        inventory_seed_set = set(inventory_behavior_ids)
        self.assertEqual(len(inventory_seed_set), 465)

        surface_summary_path = repo_root / "docs/refactor/two-server/section-1/inventory/surface-behavior-inventory.json"
        self.assertTrue(surface_summary_path.is_file(), f"Surface behavior inventory summary not found at {surface_summary_path}")
        surface_summary_data = json.loads(surface_summary_path.read_bytes().decode("utf-8"))
        self.assertEqual(surface_summary_data.get("executing_assignment_id"), "WA-P1-00B-FREEZE-20260904-001")

        baseline_file_path = repo_root / manifest["evidence_sources"]["baseline_path"]
        self.assertTrue(baseline_file_path.is_file(), f"Baseline scenario records not found at {baseline_file_path}")
        scenario_data = json.loads(baseline_file_path.read_bytes().decode("utf-8"))
        blocker_items = scenario_data.get("blocker_registry", {}).get("items", [])
        blocker = next(
            (
                item
                for item in blocker_items
                if item.get("id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
                or item.get("blocker_id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
            ),
            None,
        )
        self.assertIsNotNone(blocker, "Blocker P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE not found in blocker_registry.items")
        reason = blocker["reason"]
        start_delim = "Evidenced seeds are "
        end_delim = ". Sibling family"
        self.assertIn(start_delim, reason)
        self.assertIn(end_delim, reason)
        start_idx = reason.index(start_delim) + len(start_delim)
        end_idx = reason.index(end_delim, start_idx)
        raw_seeds_str = reason[start_idx:end_idx]
        baseline_seed_ids = [s.strip() for s in raw_seeds_str.split(",") if s.strip()]
        baseline_seed_set = set(baseline_seed_ids)
        self.assertEqual(len(baseline_seed_ids), 135)
        self.assertEqual(len(baseline_seed_set), 135)
        self.assertTrue(baseline_seed_set.issubset(inventory_seed_set))

        base_file_path = repo_root / manifest["evidence_sources"]["base_path"]
        self.assertTrue(base_file_path.is_file(), f"Base evidence file not found at {base_file_path}")
        base_bytes = base_file_path.read_bytes()
        calculated_base_sha256 = hashlib.sha256(base_bytes).hexdigest()
        base_data = json.loads(base_bytes.decode("utf-8"))

        continuation_file_path = repo_root / manifest["evidence_sources"]["continuation_path"]
        self.assertTrue(continuation_file_path.is_file(), f"Continuation evidence file not found at {continuation_file_path}")
        continuation_bytes = continuation_file_path.read_bytes()
        continuation_data = json.loads(continuation_bytes.decode("utf-8"))

        self.assertEqual(base_data.get("stage"), "P1-00b")
        self.assertEqual(continuation_data.get("stage"), "P1-00b")

        base_inventory_binding = continuation_data["base_inventory"]
        self.assertEqual(base_inventory_binding["path"], manifest["evidence_sources"]["base_path"])
        self.assertEqual(base_inventory_binding["sha256"], calculated_base_sha256)
        self.assertEqual(base_inventory_binding["inspected_head"], base_data["inspected_head"])

        prior_recent_ids = []
        for record in base_data.get("records", []):
            prior_recent_ids.extend(record.get("seed_ids", []))
        continuation_records = continuation_data.get("records", [])
        self.assertEqual(len(continuation_records), 25)
        for record in continuation_records[:5]:
            prior_recent_ids.extend(record.get("seed_ids", []))

        prior_recent_seed_set = set(prior_recent_ids)
        prior_checkpoint_set = baseline_seed_set | prior_recent_seed_set
        self.assertEqual(len(prior_checkpoint_set), 312)

        second_five_seed_ids = []
        for record in continuation_records[5:10]:
            second_five_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(len(second_five_seed_ids), 12)
        second_five_seed_set = set(second_five_seed_ids)
        self.assertEqual(len(second_five_seed_set), 12)
        self.assertTrue(second_five_seed_set.issubset(inventory_seed_set))
        self.assertTrue(second_five_seed_set.isdisjoint(prior_checkpoint_set))

        prior_324_checkpoint_set = prior_checkpoint_set | second_five_seed_set
        self.assertEqual(len(prior_324_checkpoint_set), 324)

        third_five_seed_ids = []
        for record in continuation_records[10:15]:
            third_five_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(len(third_five_seed_ids), 5)
        third_five_seed_set = set(third_five_seed_ids)
        self.assertEqual(len(third_five_seed_set), 5)
        self.assertTrue(third_five_seed_set.issubset(inventory_seed_set))
        self.assertTrue(third_five_seed_set.isdisjoint(prior_324_checkpoint_set))

        prior_329_checkpoint_set = prior_324_checkpoint_set | third_five_seed_set
        self.assertEqual(len(prior_329_checkpoint_set), 329)

        fourth_five_seed_ids = []
        for record in continuation_records[15:20]:
            fourth_five_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(len(fourth_five_seed_ids), 5)
        fourth_five_seed_set = set(fourth_five_seed_ids)
        self.assertEqual(len(fourth_five_seed_set), 5)
        self.assertTrue(fourth_five_seed_set.issubset(inventory_seed_set))
        self.assertTrue(fourth_five_seed_set.isdisjoint(prior_329_checkpoint_set))

        prior_334_checkpoint_set = prior_329_checkpoint_set | fourth_five_seed_set
        self.assertEqual(len(prior_334_checkpoint_set), 334)

        fifth_five_seed_ids = []
        for record in continuation_records[20:25]:
            fifth_five_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(len(fifth_five_seed_ids), 5)
        fifth_five_seed_set = set(fifth_five_seed_ids)
        self.assertEqual(len(fifth_five_seed_set), 5)
        self.assertTrue(fifth_five_seed_set.issubset(inventory_seed_set))
        self.assertTrue(fifth_five_seed_set.isdisjoint(prior_334_checkpoint_set))

        total_evidenced_set = prior_334_checkpoint_set | fifth_five_seed_set
        self.assertEqual(len(total_evidenced_set), 339)

        remaining_seed_set = inventory_seed_set - total_evidenced_set
        self.assertEqual(len(remaining_seed_set), 126)
        self.assertEqual(len(total_evidenced_set) + len(remaining_seed_set), 465)

        flattened_recent_ids = (
            prior_recent_ids
            + second_five_seed_ids
            + third_five_seed_ids
            + fourth_five_seed_ids
            + fifth_five_seed_ids
        )
        recent_seed_set = set(flattened_recent_ids)
        self.assertTrue(recent_seed_set.issubset(inventory_seed_set))
        new_beyond_baseline = recent_seed_set - baseline_seed_set
        self.assertEqual(len(new_beyond_baseline), 204)

        self.assertEqual(manifest["counts"]["baseline_evidenced_seed_count"], len(baseline_seed_set))
        self.assertEqual(manifest["counts"]["reconciled_new_seed_count"], len(new_beyond_baseline))
        self.assertEqual(manifest["counts"]["evidenced_seed_count"], len(total_evidenced_set))
        self.assertEqual(manifest["counts"]["remaining_seed_count"], len(remaining_seed_set))


if __name__ == "__main__":
    unittest.main()
