import tempfile
import unittest
from pathlib import Path

from scripts import report_observability_readiness as report


class ObservabilityReadinessReportTests(unittest.TestCase):
    def test_metrics_contract_accepts_explicit_memory_backend_limitations(self) -> None:
        payload = {
            "metrics_backend_policy": {
                "default_backend": "memory",
                "authoritative_cross_service_source": "loki_logs_until_explicit_multi_surface_scrape_is_deployed",
            },
            "surfaces": [
                {
                    "service": "api",
                    "collection_mode": "http",
                    "authoritative_scope": "single_api_process_only_when_backend=memory",
                },
                {"service": "bot", "collection_mode": "logs_only"},
                {"service": "sync_worker", "collection_mode": "logs_only"},
            ],
        }

        self.assertTrue(report.metrics_contract_is_acceptable(payload))

    def test_sync_health_clean_requires_empty_backlog_and_queues(self) -> None:
        clean = {
            "status": "ok",
            "unsynced_change_log_count": 0,
            "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
        }
        dirty = {
            "status": "ok",
            "unsynced_change_log_count": 0,
            "redis_queues": {"sync:outbound": 1, "sync:retry": 0},
        }

        self.assertTrue(report.sync_health_is_clean(clean))
        self.assertFalse(report.sync_health_is_clean(dirty))

    def test_artifact_scan_reports_sensitive_patterns_without_echoing_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "safe.log").write_text('{"headers":{"X-Observability-Api-Key":"[set in env]"}}', encoding="utf-8")
            (root / "bad.log").write_text("Authorization: Bearer very-secret-token", encoding="utf-8")

            result = report.scan_artifacts_for_sensitive_values(root)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["findings"], [{"pattern": "authorization_header", "path": str(root / "bad.log")}])


if __name__ == "__main__":
    unittest.main()
