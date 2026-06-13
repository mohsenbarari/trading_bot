import argparse
import json
import unittest
from pathlib import Path

from scripts.report_production_load_sampler import roles_for, run_sampler, summarize


class ProductionLoadSamplerTests(unittest.TestCase):
    def test_roles_for_both_samples_foreign_and_iran(self):
        args = argparse.Namespace(roles="both")
        self.assertEqual(roles_for(args), ["foreign", "iran"])

    def test_summarize_empty_samples_is_ok(self):
        summary = summarize([])
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["sample_count"], 0)
        self.assertEqual(summary["collector_errors"], 0)
        self.assertEqual(summary["max_postgres_connections"], 0)

    def test_summarize_counts_collector_errors_and_peak_values(self):
        summary = summarize(
            [
                {
                    "hosts": [
                        {
                            "postgres": {"connection_budget": {"current_connections": 42}},
                            "redis": {"memory": {"used_memory": 1234}},
                            "sync": {"unsynced_change_log_count": 7},
                            "nginx": {"access": {"status_families": {"5xx": 2}}},
                            "docker": {"collector_error": {"exit_code": 1}},
                        }
                    ]
                },
                {"collector_error": {"type": "RuntimeError"}, "hosts": []},
            ]
        )
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["collector_errors"], 2)
        self.assertEqual(summary["max_postgres_connections"], 42)
        self.assertEqual(summary["max_redis_used_memory"], 1234)
        self.assertEqual(summary["max_sync_backlog"], 7)
        self.assertEqual(summary["nginx_5xx_count_in_log_windows"], 2)

    def test_dry_run_writes_redacted_contract(self):
        artifact_dir = Path("tmp/test-production-load-sampler")
        args = argparse.Namespace(
            manifest="./deploy/production/online.env",
            timestamp="test",
            artifact_root="tmp",
            artifact_dir=str(artifact_dir),
            roles="both",
            duration_seconds=0.0,
            interval_seconds=10.0,
            sample_count=1,
            dry_run=True,
            json=True,
        )
        payload = run_sampler(args)
        self.assertEqual(payload["status"], "passed")
        self.assertFalse(payload["contract"]["raw_logs"])
        results = json.loads((artifact_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(results["status"], "passed")


if __name__ == "__main__":
    unittest.main()
