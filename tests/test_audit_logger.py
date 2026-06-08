import io
import json
import logging
import unittest
from unittest.mock import patch

from core.audit_logger import audit_log
from core.log_redaction import REDACTED
from core.logging_config import configure_logging
from core.request_context import clear_request_context, set_request_context


class AuditLoggerTests(unittest.TestCase):
    def tearDown(self):
        clear_request_context()
        logging.getLogger().handlers.clear()

    def test_audit_log_uses_strict_schema_and_request_context(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("audit-test")

        set_request_context(request_id="req-audit-1", actor_id=42, actor_role="مدیر ارشد", client_ip="127.0.0.1")
        audit_log(
            "user.update",
            target_type="user",
            target_id=7,
            before_summary={"role": "عادی"},
            after_summary={"role": "مدیر میانی", "password": "new-secret"},
            reason="operator requested password=unsafe",
        )

        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(payload["logger"], "audit")
        self.assertEqual(payload["service"], "audit-test")
        self.assertEqual(payload["event"], "audit.user.update")
        self.assertEqual(payload["log_class"], "audit")
        self.assertTrue(payload["audit"])
        self.assertEqual(payload["action"], "user.update")
        self.assertEqual(payload["target_type"], "user")
        self.assertEqual(payload["target_id"], 7)
        self.assertEqual(payload["result"], "success")
        self.assertEqual(payload["request_id"], "req-audit-1")
        self.assertEqual(payload["client_ip"], "127.0.0.1")
        self.assertEqual(payload["actor_id"], 42)
        self.assertEqual(payload["actor_role"], "مدیر ارشد")
        self.assertEqual(payload["after_summary"]["password"], REDACTED)
        self.assertNotIn("new-secret", stream.getvalue())
        self.assertNotIn("unsafe", stream.getvalue())

    def test_audit_log_allows_explicit_actor_and_normalizes_result(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("audit-test")

        set_request_context(actor_id=1, actor_role="ignored")
        audit_log(
            "session.reject",
            target_type="session_login_request",
            target_id="request-1",
            result="unexpected",
            actor_id=99,
            actor_role="مدیر میانی",
            extra={"refresh_token": "secret-refresh", "status_code": 403},
        )

        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(payload["actor_id"], 99)
        self.assertEqual(payload["actor_role"], "مدیر میانی")
        self.assertEqual(payload["result"], "failure")
        self.assertEqual(payload["audit_extra"]["refresh_token"], REDACTED)
        self.assertEqual(payload["audit_extra"]["status_code"], 403)
        self.assertNotIn("secret-refresh", stream.getvalue())


if __name__ == "__main__":
    unittest.main()

