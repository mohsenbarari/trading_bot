import json
import unittest

from scripts import report_frontend_ux_benchmark as report


class FrontendUxBenchmarkTests(unittest.TestCase):
    def test_redact_payload_hides_nested_tokens(self) -> None:
        payload = {
            "status": "ok",
            "token": "secret-access",
            "nested": {
                "refresh_token": "secret-refresh",
                "safe": "value",
            },
            "items": [{"auth_token": "secret-auth"}],
        }

        self.assertEqual(
            report.redact_payload(payload),
            {
                "status": "ok",
                "token": "[REDACTED]",
                "nested": {
                    "refresh_token": "[REDACTED]",
                    "safe": "value",
                },
                "items": [{"auth_token": "[REDACTED]"}],
            },
        )

    def test_redact_stdout_redacts_last_json_object(self) -> None:
        stdout = 'noise\n{"status":"ok","token":"secret"}\n'
        redacted = json.loads(report.redact_stdout(stdout))

        self.assertEqual(redacted, {"status": "ok", "token": "[REDACTED]"})

    def test_sync_health_clean_requires_empty_backlog_and_queues(self) -> None:
        clean = {
            "status": "ok",
            "unsynced_change_log_count": 0,
            "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
        }
        dirty = {
            "status": "ok",
            "unsynced_change_log_count": 1,
            "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
        }

        self.assertTrue(report.sync_health_is_clean(clean))
        self.assertFalse(report.sync_health_is_clean(dirty))


if __name__ == "__main__":
    unittest.main()
