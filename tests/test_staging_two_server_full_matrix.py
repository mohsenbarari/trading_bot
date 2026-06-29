import tempfile
import unittest
from pathlib import Path

from scripts import build_staging_two_server_full_matrix_manifest as manifest_builder
from scripts import run_staging_two_server_full_matrix as runner


class StagingTwoServerFullMatrixTests(unittest.TestCase):
    def test_manifest_is_complete_controlled_and_branch_change_tagged(self):
        manifest = manifest_builder.build_manifest(prefix="FMX_STAGE_UNIT_20260629_")
        errors = manifest_builder.validate_manifest(manifest)

        self.assertEqual(errors, [])
        self.assertEqual(manifest["environment"], "staging_two_server")
        self.assertFalse(manifest["mutates_production"])
        self.assertEqual(manifest["summary"]["total_manifest_scenarios"], 5611)
        self.assertEqual(manifest["summary"]["branch_change_regression_scenarios"], 56)
        self.assertTrue(manifest["summary"]["controlled_no_pressure"])

        stress_records = manifest["sections"]["production_stress_overlay"]
        self.assertLessEqual(max(item["min_parallel_requests"] for item in stress_records), 12)
        self.assertEqual({item["target_rps_floor"] for item in stress_records}, {0})

        counts = manifest["summary"]["branch_change_area_counts"]
        for area in manifest_builder.BRANCH_CHANGE_REQUIREMENTS:
            self.assertGreater(counts.get(area, 0), 0, area)

    def test_plan_writes_equivalent_agent_log_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            original_claude_root = runner.CLAUDE_LOG_ROOT
            original_chatgpt_root = runner.CHATGPT_LOG_ROOT
            try:
                runner.CLAUDE_LOG_ROOT = tmp_path / "claude" / "full_matrix_logs"
                runner.CHATGPT_LOG_ROOT = tmp_path / "chatgpt" / "full_matrix_logs"
                args = runner.parse_args(
                    [
                        "--mode",
                        "plan",
                        "--run-id",
                        "S2FM-UNIT",
                        "--prefix",
                        "FMX_STAGE_UNIT_20260629_",
                        "--artifact-dir",
                        str(tmp_path / "artifacts"),
                    ]
                )
                payload = runner.build_plan(args)
            finally:
                runner.CLAUDE_LOG_ROOT = original_claude_root
                runner.CHATGPT_LOG_ROOT = original_chatgpt_root

            self.assertEqual(payload["summary"]["status"], "plan_ready")
            for root in (tmp_path / "claude" / "full_matrix_logs", tmp_path / "chatgpt" / "full_matrix_logs"):
                log_dir = root / "S2FM-UNIT"
                self.assertTrue((log_dir / "README.md").exists())
                self.assertTrue((log_dir / "manifest.json").exists())
                self.assertTrue((log_dir / "scenario-results.jsonl").exists())
                self.assertTrue((log_dir / "summary.json").exists())
                self.assertTrue((log_dir / "redaction-report.json").exists())


if __name__ == "__main__":
    unittest.main()
