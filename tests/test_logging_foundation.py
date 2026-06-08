import io
import json
import logging
import unittest
from unittest.mock import patch

from core.log_redaction import REDACTED, REDACTED_JWT, redact
from core.logging_config import configure_logging
from core.request_context import clear_request_context, set_request_context


class LoggingFoundationTests(unittest.TestCase):
    def tearDown(self):
        clear_request_context()
        logging.getLogger().handlers.clear()

    def test_redact_masks_nested_secrets_and_common_token_patterns(self):
        payload = {
            "password": "plain-password",
            "profile": {
                "mobile": "09123456789",
                "note": "authorization: Bearer abc.def.ghi otp=123456",
            },
            "items": [{"refresh_token": "secret-refresh"}],
        }

        redacted = redact(payload)

        self.assertEqual(redacted["password"], REDACTED)
        self.assertEqual(redacted["items"][0]["refresh_token"], REDACTED)
        self.assertIn("0912****789", redacted["profile"]["mobile"])
        self.assertIn(REDACTED, redacted["profile"]["note"])
        self.assertIn(f"otp={REDACTED}", redacted["profile"]["note"])
        self.assertNotIn("plain-password", json.dumps(redacted))
        self.assertNotIn("123456", json.dumps(redacted))

    def test_redact_masks_jwt_like_values_inside_messages(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"

        self.assertEqual(redact(f"token={token}"), f"token={REDACTED}")
        self.assertEqual(redact(f"raw {token}"), f"raw {REDACTED_JWT}")

    def test_configure_logging_emits_json_with_context_and_redaction(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("api-test")

        set_request_context(request_id="req-1", actor_id=42)
        logging.getLogger("tests.logging").info(
            "login failed password=hunter2 mobile=09123456789",
            extra={"authorization": "Bearer unsafe-token", "safe_field": "visible"},
        )

        log_line = stream.getvalue().strip()
        self.assertTrue(log_line)
        payload = json.loads(log_line)
        self.assertEqual(payload["service"], "api-test")
        self.assertEqual(payload["request_id"], "req-1")
        self.assertEqual(payload["actor_id"], 42)
        self.assertEqual(payload["authorization"], REDACTED)
        self.assertEqual(payload["safe_field"], "visible")
        self.assertNotIn("hunter2", log_line)
        self.assertNotIn("unsafe-token", log_line)
        self.assertIn("0912****789", log_line)


if __name__ == "__main__":
    unittest.main()
