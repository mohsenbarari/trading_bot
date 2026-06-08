import unittest

from core.audit_logger import audit_log
from core.job_logging import job_context
from core.metrics import (
    metrics_response_body,
    normalize_http_route,
    record_bot_update,
    record_http_request,
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


if __name__ == "__main__":
    unittest.main()
