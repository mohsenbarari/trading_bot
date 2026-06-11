import unittest
from collections import OrderedDict
from unittest.mock import patch

from scripts import report_postgres_runtime as report


class PostgresRuntimeReportTests(unittest.TestCase):
    def test_normalize_setting_value_handles_units(self):
        self.assertEqual(report.normalize_setting_value("shared_buffers", "8GB"), str(8 * 1024**3))
        self.assertEqual(report.normalize_setting_value("work_mem", "8192kB"), str(8 * 1024**2))
        self.assertEqual(report.normalize_setting_value("checkpoint_timeout", "15min"), "900")
        self.assertEqual(report.normalize_setting_value("random_page_cost", "1.20"), "1.2")

    def test_evaluate_settings_matches_equivalent_values(self):
        checks = report.evaluate_settings(
            {
                "shared_buffers": "8192MB",
                "checkpoint_timeout": "900s",
                "random_page_cost": "1.20",
            },
            OrderedDict(
                (
                    ("shared_buffers", "8GB"),
                    ("checkpoint_timeout", "15min"),
                    ("random_page_cost", "1.2"),
                )
            ),
        )

        self.assertTrue(all(check.ok for check in checks))

    def test_build_connection_budget_uses_iran_api_and_sync_worker_ceiling(self):
        with patch.dict("os.environ", {"API_WORKERS": "8", "DB_POOL_SIZE": "8", "DB_MAX_OVERFLOW": "6"}):
            budget = report.build_connection_budget(max_connections=500, current_connections=32, limit_ratio=0.8)

        self.assertEqual(budget["estimated_sqlalchemy_ceiling"], 126)
        self.assertEqual(budget["safe_connection_limit"], 400)
        self.assertTrue(budget["estimated_within_limit"])
        self.assertTrue(budget["current_within_limit"])


if __name__ == "__main__":
    unittest.main()
