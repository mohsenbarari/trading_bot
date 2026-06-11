import unittest

from scripts import report_deployment_restart_benchmark as report


class DeploymentRestartBenchmarkTests(unittest.TestCase):
    def test_release_phase_parser_extracts_durations(self) -> None:
        log_text = """
[2026-06-11T10:00:00Z] Checking local prerequisites
[2026-06-11T10:00:05Z] Local checks passed
[2026-06-11T10:00:10Z] Building frontend locally
[2026-06-11T10:02:10Z] Local release build complete
[2026-06-11T10:02:20Z] Syncing production payload to the Iran host
[2026-06-11T10:02:50Z] Production payload sync complete
"""

        events = report.parse_release_events(log_text)
        phases = report.summarize_release_phases(events)

        self.assertEqual(phases["local_validation"]["duration_seconds"], 5.0)
        self.assertEqual(phases["build"]["duration_seconds"], 120.0)
        self.assertEqual(phases["sync"]["duration_seconds"], 30.0)

    def test_sync_health_clean_requires_empty_backlog_and_queues(self) -> None:
        clean = {
            "status": "ok",
            "unsynced_change_log_count": 0,
            "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
        }
        dirty = {
            "status": "ok",
            "unsynced_change_log_count": 2,
            "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
        }

        self.assertTrue(report.sync_health_is_clean(clean))
        self.assertFalse(report.sync_health_is_clean(dirty))

    def test_gates_require_backup_and_restart_recovery(self) -> None:
        checks = {
            "release": {"status": "passed"},
            "backup": {"status": "ok", "bytes": 1024},
            "restarts": {"app": {"status": "ok"}},
            "sync_health": {
                "iran": {"status": "ok", "unsynced_change_log_count": 0, "redis_queues": {"sync:outbound": 0, "sync:retry": 0}},
                "foreign": {"status": "ok", "unsynced_change_log_count": 0, "redis_queues": {"sync:outbound": 0, "sync:retry": 0}},
            },
        }

        self.assertEqual(report.evaluate_gates(checks), {"status": "passed", "failures": []})

    def test_backup_script_uses_stable_json_payload(self) -> None:
        script = report.backup_script("/srv/trading-bot/current", "unit-test")

        self.assertIn("backup_path=/srv/trading-bot/backups/p10-deployment-unit-test.sql", script)
        self.assertIn('"status": "ok"', script)
        self.assertIn('"bytes": int(sys.argv[2])', script)
        self.assertNotIn("printf '{\"status\":\"ok\"", script)


if __name__ == "__main__":
    unittest.main()
