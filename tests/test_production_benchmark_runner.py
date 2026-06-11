from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts import run_production_benchmark as runner


class ProductionBenchmarkRunnerTests(unittest.TestCase):
    def test_quick_mode_selects_short_core_tasks_only(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"))
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(task, mode="quick", profile=None, include_long=False, skip_long=False)
        ]

        self.assertIn("baseline_capture", selected)
        self.assertIn("foreign_api_config", selected)
        self.assertIn("iran_api_config", selected)
        self.assertIn("sync_health_foreign", selected)
        self.assertIn("observability_overhead", selected)
        self.assertNotIn("frontend_e2e_chromium", selected)
        self.assertNotIn("messenger_full", selected)

    def test_targeted_profile_filters_to_requested_profile_and_core_default(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"))
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(task, mode="targeted", profile="observability", include_long=False, skip_long=False)
        ]

        self.assertEqual(selected, ["observability_overhead", "metrics_targets", "observability_gate"])

    def test_dry_run_writes_results_without_executing_benchmark_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = runner.main([
                    "--mode",
                    "quick",
                    "--timestamp",
                    "dry-run",
                    "--artifact-root",
                    tmpdir,
                    "--dry-run",
                ])
            result_path = Path(tmpdir) / "dry-run" / "results.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["results"])
        self.assertTrue(all(item["status"] == "planned" for item in payload["results"]))


if __name__ == "__main__":
    unittest.main()
