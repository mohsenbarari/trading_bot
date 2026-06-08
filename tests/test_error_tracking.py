import io
import json
import logging
import unittest
from unittest.mock import patch

from core.error_tracking import capture_exception, error_fingerprint
from core.log_redaction import REDACTED
from core.logging_config import configure_logging
from core.request_context import clear_request_context, set_request_context


class ErrorTrackingTests(unittest.TestCase):
    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
