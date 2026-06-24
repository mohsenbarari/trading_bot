import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "build_production_full_matrix_manifest.py"

spec = importlib.util.spec_from_file_location("build_production_full_matrix_manifest", MODULE_PATH)
manifest_builder = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = manifest_builder
spec.loader.exec_module(manifest_builder)


class ProductionFullMatrixManifestTests(unittest.TestCase):
    def build_manifest(self):
        return manifest_builder.build_manifest(prefix="PFM_20260624_180000_")

    def test_manifest_expands_required_sections_and_counts(self):
        manifest = self.build_manifest()
        summary = manifest["summary"]

        self.assertEqual(manifest["schema_version"], "production_full_matrix_manifest_v1")
        self.assertFalse(manifest["mutates_production"])
        self.assertEqual(summary["market_behavior_scenarios"], 228)
        self.assertEqual(summary["delivery_contract_scenarios"], 204)
        self.assertEqual(summary["targeted_join_scenarios"], 204)
        self.assertEqual(summary["targeted_join_policy_supported"], 108)
        self.assertEqual(summary["targeted_join_policy_unsupported"], 96)
        self.assertEqual(summary["base_trade_shape_scenarios"], 1224)
        self.assertEqual(summary["base_trade_shape_policy_supported"], 648)
        self.assertEqual(summary["base_trade_shape_policy_unsupported"], 576)
        self.assertEqual(summary["stress_overlay_scenarios"], 3672)
        self.assertEqual(
            summary["total_manifest_scenarios"],
            228 + 204 + 204 + 1224 + 3672 + len(manifest_builder.NEGATIVE_BUSINESS_CASES),
        )

    def test_base_trade_shape_covers_quadrants_actor_pairs_outages_types_and_shapes(self):
        manifest = self.build_manifest()
        base = manifest["sections"]["production_base_trade_shape"]

        self.assertEqual({item["quadrant"] for item in base}, set(manifest_builder.SURFACE_PAIR_QUADRANTS.values()))
        self.assertEqual({item["actor_pair_id"] for item in base}, {
            actor["pair_id"] for actor in manifest["axes"]["actor_pairs"]
        })
        self.assertEqual({item["outage_id"] for item in base}, {
            outage["outage_id"] for outage in manifest["axes"]["outage_classes"]
        })
        self.assertEqual({item["offer_type"] for item in base}, {"buy", "sell"})
        self.assertEqual({item["shape"] for item in base}, {"wholesale_full", "retail_two_lot", "retail_three_lot"})

    def test_policy_unsupported_cases_remain_visible_as_negative_evidence(self):
        manifest = self.build_manifest()
        base = manifest["sections"]["production_base_trade_shape"]
        unsupported = [item for item in base if not item["policy_supported"]]

        self.assertTrue(
            any("tier2_cannot_create_offer" in item["unsupported_reasons"] for item in unsupported)
        )
        self.assertTrue(
            any("tier2_cannot_use_telegram_request" in item["unsupported_reasons"] for item in unsupported)
        )
        self.assertTrue(all(item["expected_outcome"] == "policy_rejected" for item in unsupported))
        self.assertTrue(
            all("no_partial_mutation_on_reject" in item["assertion_refs"] for item in unsupported)
        )

    def test_stress_overlays_reference_only_supported_base_scenarios(self):
        manifest = self.build_manifest()
        base_by_id = {
            item["manifest_id"]: item
            for item in manifest["sections"]["production_base_trade_shape"]
        }

        for overlay in manifest["sections"]["production_stress_overlay"]:
            base = base_by_id[overlay["base_manifest_id"]]
            self.assertTrue(base["policy_supported"])
            self.assertGreaterEqual(overlay["target_rps_floor"], 600)
            self.assertGreaterEqual(overlay["min_parallel_requests"], 2)

    def test_manifest_validation_and_token_guard(self):
        manifest = self.build_manifest()

        self.assertEqual(manifest_builder.validate_manifest(manifest), [])
        encoded = json.dumps(manifest, ensure_ascii=False)
        self.assertNotIn("BOT_TOKEN", encoded)
        self.assertNotIn("895973", encoded)

    def test_cli_writes_manifest_artifact_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "manifest.json"
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = manifest_builder.main(
                    [
                        "--prefix",
                        "PFM_20260624_180000_",
                        "--check",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            stdout_payload = json.loads(stdout.getvalue())

        self.assertEqual(payload["schema_version"], "production_full_matrix_manifest_v1")
        self.assertEqual(payload["summary"]["base_trade_shape_scenarios"], 1224)
        self.assertEqual(stdout_payload["status"], "ok")
        self.assertEqual(stdout_payload["output"], str(output))
        self.assertNotIn("sections", stdout_payload)


if __name__ == "__main__":
    unittest.main()
