import os
import tempfile
import unittest
from unittest.mock import patch

from core.audit_logger import audit_log
from core.job_logging import job_context
from core.metrics import (
    MetricsRegistry,
    metrics_response_body,
    normalize_http_route,
    record_offer_publication_health,
    record_bot_update,
    record_http_request,
    record_sync_conflict,
    registry,
)


class MetricsTests(unittest.TestCase):
    def tearDown(self):
        registry.reset()

    def test_route_normalization_removes_raw_ids(self):
        self.assertEqual(normalize_http_route("/api/users/123/sessions/abc-def-1234567890"), "/api/users/{id}/sessions/{id}")
        self.assertEqual(normalize_http_route("/api/chat/messages?token=secret"), "/api/chat/messages")

    def test_prometheus_text_renders_counter_and_histogram(self):
        record_http_request(method="GET", route="/api/users/{user_id}", status_code=404, duration_ms=12.5)

        body = metrics_response_body()

        self.assertIn("# TYPE trading_bot_http_requests_total counter", body)
        self.assertIn('route="/api/users/{user_id}"', body)
        self.assertIn('status_class="4xx"', body)
        self.assertIn("trading_bot_http_request_duration_ms_bucket", body)
        self.assertIn("trading_bot_metrics_backend_info", body)
        self.assertIn('backend="memory"', body)
        self.assertNotIn("token=secret", body)

    def test_bot_job_and_audit_metrics_are_recorded(self):
        record_bot_update(event_type="message", result="success", duration_ms=3)
        with job_context("offer_expiry"):
            pass
        audit_log("user.update", target_type="user", target_id=1, result="success")

        body = metrics_response_body()

        self.assertIn("trading_bot_bot_updates_total", body)
        self.assertIn('event_type="message"', body)
        self.assertIn("trading_bot_job_runs_total", body)
        self.assertIn('job_name="offer_expiry"', body)
        self.assertIn("trading_bot_business_actions_total", body)
        self.assertIn('action="user.update"', body)

    def test_sync_publication_and_conflict_metrics_are_recorded(self):
        record_offer_publication_health(
            server_mode="foreign",
            state_counts={"telegram_channel": {"failed": 2}},
            finding_counts={"failed_telegram_publication": 2},
        )
        record_sync_conflict(server_mode="foreign", table="offers", reason="same_version_terminal_conflict")

        body = metrics_response_body()

        self.assertIn("trading_bot_offer_publication_states", body)
        self.assertIn('surface="telegram_channel"', body)
        self.assertIn('status="failed"', body)
        self.assertIn("trading_bot_offer_publication_reconciliation_findings", body)
        self.assertIn('issue="failed_telegram_publication"', body)
        self.assertIn("trading_bot_sync_conflicts_total", body)
        self.assertIn('reason="same_version_terminal_conflict"', body)

    def test_default_memory_backend_does_not_create_sqlite_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "metrics.sqlite3")
            with patch.dict(os.environ, {"TRADING_BOT_METRICS_DB": db_path}, clear=False):
                local_registry = MetricsRegistry()
                local_registry.counter("trading_bot_test_total", "Test counter.")
                body = local_registry.render_prometheus()

            self.assertFalse(os.path.exists(db_path))
            self.assertIn('backend="memory"', body)
            self.assertIn('shared="false"', body)
            self.assertIn("trading_bot_test_total", body)

    def test_shared_sqlite_backend_is_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "metrics.sqlite3")
            with patch.dict(
                os.environ,
                {
                    "TRADING_BOT_METRICS_BACKEND": "shared_sqlite",
                    "TRADING_BOT_METRICS_DB": db_path,
                    "TRADING_BOT_SERVICE": "api",
                },
                clear=False,
            ):
                first = MetricsRegistry()
                second = MetricsRegistry()
                first.counter("trading_bot_shared_total", "Shared counter.", service_part="one")
                body = second.render_prometheus()

            self.assertTrue(os.path.exists(db_path))
            self.assertIn('backend="shared_sqlite"', body)
            self.assertIn('shared="true"', body)
            self.assertIn("trading_bot_shared_total", body)
            self.assertIn('service_part="one"', body)

    def test_metrics_output_remains_secret_free(self):
        record_http_request(
            method="GET",
            route="/api/users/123/sessions/11111111-2222-3333-4444-555555555555?token=secret",
            status_code=500,
            duration_ms=10,
        )
        record_bot_update(event_type="message:09123456789:owner@example.com:report.pdf", result="success", duration_ms=1)
        audit_log("download.report.pdf", target_type="user", target_id=1, result="success")

        body = metrics_response_body()

        for raw in (
            "11111111-2222-3333-4444-555555555555",
            "token=secret",
            "09123456789",
            "owner@example.com",
            "report.pdf",
        ):
            self.assertNotIn(raw, body)
        self.assertIn("/api/users/{id}/sessions/{id}", body)


if __name__ == "__main__":
    unittest.main()
