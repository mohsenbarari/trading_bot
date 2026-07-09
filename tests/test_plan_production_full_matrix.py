import json
import unittest
from types import SimpleNamespace

from scripts.plan_production_full_matrix import (
    PlanError,
    build_plan,
    default_prefix,
    validate_prefix,
)


class ProductionFullMatrixPlanTests(unittest.TestCase):
    def make_args(self, **overrides):
        values = {
            "prefix": "PFM_20260624_180000_",
            "artifact_dir": None,
            "operator_account": "mohsen",
            "reason": "production_full_matrix_test",
            "users": 1000,
            "attempts_per_scenario": 40,
            "target_rps": 600.0,
            "telegram_ratio": 0.6,
            "write_max_concurrency": 24,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_default_prefix_is_cleanup_guard_compatible(self):
        self.assertTrue(default_prefix().startswith("PFM_"))
        self.assertGreaterEqual(len(default_prefix()), 8)

    def test_validate_prefix_rejects_broad_or_unsafe_values(self):
        with self.assertRaises(PlanError):
            validate_prefix("test")
        with self.assertRaises(PlanError):
            validate_prefix("PFM_*")
        with self.assertRaises(PlanError):
            validate_prefix("STAGE_20260624_")

    def test_plan_includes_isolation_and_two_sided_cleanup(self):
        plan = build_plan(self.make_args())
        by_name = {stage["name"]: stage for stage in plan["stages"]}

        self.assertEqual(
            plan["scenario_catalog"]["path"],
            "docs/PRODUCTION_FULL_MATRIX_SCENARIO_CATALOG.md",
        )
        self.assertEqual(
            plan["scenario_catalog"]["manifest_contract_path"],
            "docs/PRODUCTION_FULL_MATRIX_MANIFEST.md",
        )
        self.assertEqual(
            plan["scenario_catalog"]["manifest_generator"],
            "scripts/build_production_full_matrix_manifest.py",
        )
        self.assertEqual(plan["scenario_catalog"]["baseline_counts"]["market_behavior_scenarios"], 228)
        self.assertEqual(plan["scenario_catalog"]["baseline_counts"]["notification_delivery_scenarios"], 204)
        self.assertEqual(plan["scenario_catalog"]["baseline_counts"]["targeted_join_policy_supported"], 108)
        self.assertEqual(plan["scenario_catalog"]["baseline_counts"]["targeted_join_policy_unsupported"], 96)
        self.assertIn("enable_isolation", by_name)
        self.assertIn("pre_run_cleanup_dry_run", by_name)
        self.assertIn("catalog_dry_run", by_name)
        self.assertIn("hard_delete_cleanup", by_name)
        self.assertTrue(
            any(
                "docs/PRODUCTION_FULL_MATRIX_SCENARIO_CATALOG.md" in item
                for item in by_name["catalog_dry_run"]["commands"]
            )
        )
        self.assertTrue(
            any(
                "scripts/build_production_full_matrix_manifest.py" in item
                for item in by_name["catalog_dry_run"]["commands"]
            )
        )
        self.assertTrue(
            any(
                "scripts/run_production_full_matrix.py" in item
                for item in by_name["catalog_dry_run"]["commands"]
            )
        )
        self.assertTrue(
            any(
                "production-full-matrix-execution-plan.json" in item
                for item in by_name["catalog_dry_run"]["commands"]
            )
        )
        self.assertEqual(len(by_name["pre_run_cleanup_dry_run"]["commands"]), 2)
        self.assertTrue(any("docker compose exec -T app" in item for item in by_name["pre_run_cleanup_dry_run"]["commands"]))
        self.assertTrue(any("ssh -p 37067 root@62.220.124.174" in item for item in by_name["pre_run_cleanup_dry_run"]["commands"]))
        self.assertTrue(
            all("--dry-run" in item for item in by_name["pre_run_cleanup_dry_run"]["commands"])
        )
        self.assertTrue(
            all(
                "PRODUCTION_TEST_CLEANUP_CONFIRM=hard-delete-test-data" in item
                for item in by_name["hard_delete_cleanup"]["commands"]
            )
        )

    def test_plan_is_json_serializable_and_side_effect_free(self):
        plan = build_plan(self.make_args())

        encoded = json.dumps(plan, ensure_ascii=False)
        self.assertIn('"mutates_production": false', encoded)
        self.assertNotIn("BOT_TOKEN", encoded)
        self.assertNotIn("895973", encoded)


if __name__ == "__main__":
    unittest.main()
