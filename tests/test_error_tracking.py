import io
import json
import logging
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.error_tracking import _reset_error_tracking_rate_limiter, capture_exception, error_fingerprint, scrub_sentry_event
from core.log_redaction import REDACTED
from core.logging_config import configure_logging
from core.request_context import clear_request_context, set_request_context


class ErrorTrackingTests(unittest.TestCase):
    def tearDown(self):
        _reset_error_tracking_rate_limiter()
        clear_request_context()
        logging.getLogger().handlers.clear()

    def _raise_secret_error(self):
        raise RuntimeError("password=hunter2 token=unsafe 09123456789")

    def test_capture_exception_emits_grouped_scrubbed_event(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("error-test")

        set_request_context(request_id="req-error-1", actor_id=7, actor_role="مدیر میانی", path="/api/auth/token")

        try:
            self._raise_secret_error()
        except RuntimeError as exc:
            fingerprint = capture_exception(exc, source="api.request", extra={"authorization": "Bearer unsafe"})

        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(payload["logger"], "error.tracking")
        self.assertEqual(payload["event"], "error.exception.captured")
        self.assertEqual(payload["log_class"], "error")
        self.assertEqual(payload["error_fingerprint"], fingerprint)
        self.assertEqual(payload["error_source"], "api.request")
        self.assertEqual(payload["request_id"], "req-error-1")
        self.assertEqual(payload["actor_id"], 7)
        self.assertEqual(payload["actor_role"], "مدیر میانی")
        self.assertEqual(payload["extra"]["authorization"], REDACTED)
        self.assertIn("0912****789", payload["exception_message"])
        self.assertNotIn("hunter2", stream.getvalue())
        self.assertNotIn("unsafe", stream.getvalue())

    def test_error_fingerprint_is_stable_for_same_exception_site(self):
        try:
            self._raise_secret_error()
        except RuntimeError as first:
            first_fingerprint = error_fingerprint(first, source="test")

        try:
            self._raise_secret_error()
        except RuntimeError as second:
            second_fingerprint = error_fingerprint(second, source="test")

        self.assertEqual(first_fingerprint, second_fingerprint)
        self.assertEqual(len(first_fingerprint), 16)

    def test_capture_exception_uses_project_relative_frame_paths(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("error-test")

        try:
            self._raise_secret_error()
        except RuntimeError as exc:
            capture_exception(exc, source="api.request")

        payload = json.loads(stream.getvalue().strip())
        self.assertTrue(payload["frames"])
        top_frame = payload["frames"][-1]["file"]
        self.assertFalse(top_frame.startswith("/"))
        self.assertIn("tests/test_error_tracking.py", top_frame)

    def test_capture_exception_rate_limits_repeated_fingerprints(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("error-test")

        with patch("core.error_tracking._rate_limit_config", return_value=(60, 1, 16)):
            for _ in range(3):
                try:
                    self._raise_secret_error()
                except RuntimeError as exc:
                    capture_exception(exc, source="api.request")

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["event"], "error.exception.captured")

    def test_sentry_before_send_scrubs_raw_event_payloads(self):
        raw_event = {
            "message": "failed password=hunter2 token=unsafe 09123456789 owner@example.com",
            "request": {
                "url": "https://example.test/api/auth/token?token=unsafe",
                "query_string": "token=unsafe&mobile=09123456789",
                "data": {"password": "hunter2", "mobile": "09123456789"},
                "cookies": {"session": "unsafe-cookie"},
                "headers": {"authorization": "Bearer unsafe-token", "x-safe": "visible"},
            },
            "exception": {
                "values": [
                    {
                        "type": "RuntimeError",
                        "value": "otp=123456 email=owner@example.com",
                    }
                ]
            },
        }

        scrubbed = scrub_sentry_event(raw_event, {"exc_info": "ignored"})
        rendered = json.dumps(scrubbed, ensure_ascii=False)

        self.assertEqual(scrubbed["request"]["data"], REDACTED)
        self.assertEqual(scrubbed["request"]["cookies"], REDACTED)
        self.assertEqual(scrubbed["request"]["headers"]["authorization"], REDACTED)
        self.assertEqual(scrubbed["request"]["headers"]["x-safe"], "visible")
        for raw in ("hunter2", "unsafe-token", "unsafe-cookie", "09123456789", "owner@example.com", "123456"):
            self.assertNotIn(raw, rendered)

    def test_capture_exception_forwards_only_sanitized_sentry_event(self):
        sentry_calls = {"events": [], "exceptions": [], "tags": []}

        fake_sentry = SimpleNamespace(
            set_tag=lambda key, value: sentry_calls["tags"].append((key, value)),
            capture_event=lambda event: sentry_calls["events"].append(event),
            capture_exception=lambda exc: sentry_calls["exceptions"].append(exc),
        )

        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("error-test")

        with patch.dict(sys.modules, {"sentry_sdk": fake_sentry}):
            try:
                self._raise_secret_error()
            except RuntimeError as exc:
                fingerprint = capture_exception(
                    exc,
                    source="api.request",
                    extra={
                        "authorization": "Bearer unsafe-token",
                        "request_body": {"password": "hunter2", "mobile": "09123456789"},
                        "signed_url": "https://cdn.example.test/file?X-Amz-Signature=abcdef123456",
                    },
                )

        self.assertEqual(sentry_calls["exceptions"], [])
        self.assertEqual(len(sentry_calls["events"]), 1)
        event = sentry_calls["events"][0]
        rendered = json.dumps(event, ensure_ascii=False)
        self.assertEqual(event["fingerprint"], [fingerprint])
        self.assertEqual(event["tags"]["error_source"], "api.request")
        self.assertEqual(event["extra"]["extra"]["authorization"], REDACTED)
        self.assertEqual(event["extra"]["extra"]["request_body"]["password"], REDACTED)
        self.assertEqual(event["extra"]["extra"]["signed_url"], REDACTED)
        for raw in ("hunter2", "unsafe-token", "09123456789", "abcdef123456"):
            self.assertNotIn(raw, rendered)


if __name__ == "__main__":
    unittest.main()
