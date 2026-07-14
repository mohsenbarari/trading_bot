import unittest

from scripts import report_production_alerts as alerts


def host_payload(**overrides):
    payload = {
        "role": "iran",
        "postgres": {
            "connection_budget": {"current_connections": 32},
            "idle_in_transaction": {"old_count": 0, "max_age_seconds": 0},
        },
        "redis": {
            "memory": {"used_memory": 2 * alerts.GIB},
            "persistence": {"aof_last_write_status": "ok", "aof_last_bgrewrite_status": "ok"},
        },
        "sync": {
            "unsynced_change_log_count": 0,
            "oldest_unsynced_age_seconds": 0,
            "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
        },
        "disk": {"filesystems": [{"path": "/", "used_percent": 40.0}]},
        "backup": {"ok": True, "latest_status": "ok", "age_seconds": 3600},
    }
    payload.update(overrides)
    return payload


class ProductionAlertsTests(unittest.TestCase):
    def test_evaluate_host_report_returns_no_alerts_for_healthy_payload(self):
        self.assertEqual(alerts.evaluate_host_report(host_payload()), [])

    def test_evaluate_host_report_flags_db_redis_disk_sync_and_backup_thresholds(self):
        result = alerts.evaluate_host_report(
            host_payload(
                postgres={
                    "connection_budget": {"current_connections": 325},
                    "idle_in_transaction": {"old_count": 1, "max_age_seconds": 1200},
                },
                redis={
                    "memory": {"used_memory": 9 * alerts.GIB},
                    "persistence": {"aof_last_write_status": "err", "aof_last_bgrewrite_status": "ok"},
                },
                sync={
                    "unsynced_change_log_count": 1500,
                    "quarantined_change_log_count": 1,
                    "oldest_unsynced_age_seconds": 3700,
                    "redis_queues": {"sync:outbound": 1200, "sync:retry": 120},
                },
                disk={"filesystems": [{"path": "/", "used_percent": 90.0}]},
                backup={"ok": False, "latest_status": "failed", "age_seconds": 49 * 3600},
            )
        )

        critical_metrics = {item["metric"] for item in result if item["severity"] == "critical"}
        self.assertIn("current_connections", critical_metrics)
        self.assertIn("idle_in_transaction_max_age_seconds", critical_metrics)
        self.assertIn("used_memory", critical_metrics)
        self.assertIn("aof_last_write_status", critical_metrics)
        self.assertIn("unsynced_change_log_count", critical_metrics)
        self.assertIn("quarantined_change_log_count", critical_metrics)
        self.assertIn("oldest_unsynced_age_seconds", critical_metrics)
        self.assertIn("sync:retry", critical_metrics)
        self.assertIn("sync:outbound", critical_metrics)
        self.assertIn("/:used_percent", critical_metrics)
        self.assertIn("latest_backup_status", critical_metrics)
        self.assertIn("latest_backup_age_seconds", critical_metrics)
        self.assertEqual(alerts.overall_status(result), "critical")

    def test_warning_exit_policy_is_configurable(self):
        warning_alert = [
            {
                "role": "foreign",
                "component": "disk",
                "severity": "warning",
                "metric": "/:used_percent",
                "value": 76,
                "threshold": "> 75%",
                "summary": "disk warning",
            }
        ]

        status = alerts.overall_status(warning_alert)

        self.assertEqual(status, "warning")
        self.assertFalse(alerts.should_fail(status, "critical"))
        self.assertTrue(alerts.should_fail(status, "warning"))
        self.assertFalse(alerts.should_fail(status, "never"))


if __name__ == "__main__":
    unittest.main()
