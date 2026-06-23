import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "run_bot_webapp_candidate_full_matrix.py"

spec = importlib.util.spec_from_file_location("run_bot_webapp_candidate_full_matrix", MODULE_PATH)
candidate_matrix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = candidate_matrix
spec.loader.exec_module(candidate_matrix)


class BotWebAppCandidateFullMatrixTests(unittest.TestCase):
    def test_dry_run_plans_old_market_matrix_and_new_notification_matrix_without_filters(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_dir = Path(tmp_dir)

            with patch("sys.stdout", new_callable=io.StringIO):
                exit_code = candidate_matrix.main(
                    [
                        "--dry-run",
                        "--prefix",
                        "P7_STAGE_TEST_",
                        "--artifact-dir",
                        str(artifact_dir),
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads((artifact_dir / "candidate-full-matrix-summary.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["schema_version"], "bot_webapp_candidate_full_matrix_v1")
        self.assertTrue(summary["dry_run"])
        self.assertTrue(summary["matrix_design"]["path"].endswith("BOT_WEBAPP_CANDIDATE_FULL_MATRIX_DESIGN.md"))
        self.assertEqual(summary["matrix_design"]["status"], "required_before_owner_manual_staging_validation")
        self.assertEqual(
            summary["matrix_design"]["outage_scope"],
            ["stable", "short_under_2m", "medium_around_60m"],
        )
        self.assertIn("targeted_join_scenarios", summary["matrix_design"]["layers"])
        self.assertEqual(summary["production_gate"]["status"], "blocked_until_owner_staging_validation")
        self.assertEqual(summary["no_pressure_profile"]["users"], 200)
        self.assertEqual(summary["no_pressure_profile"]["target_rps"], 20.0)
        self.assertEqual(summary["no_pressure_profile"]["write_max_concurrency"], 4)

        market_command = summary["market_matrix"]["run"]["command"]
        self.assertTrue(any(command.endswith("run_staging_comprehensive_load_matrix.sh") for command in market_command))
        self.assertNotIn("--max-scenarios", market_command)
        self.assertNotIn("--family", market_command)
        self.assertNotIn("--scenario", market_command)

        notification_command = summary["notification_delivery_matrix"]["run"]["command"]
        self.assertIn(str(REPO_ROOT / "scripts" / "report_trade_notification_delivery_matrix.py"), notification_command)
        self.assertIn("--check", notification_command)

        stage11_command = summary["trade_delivery_stage11_matrix"]["run"]["command"]
        self.assertIn(str(REPO_ROOT / "scripts" / "report_trade_delivery_staging_validation.py"), stage11_command)
        self.assertIn("matrix", stage11_command)


if __name__ == "__main__":
    unittest.main()
