import io
import json
import logging
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from core.log_redaction import (
    REDACTED,
    REDACTED_CARD,
    REDACTED_EMAIL,
    REDACTED_FILENAME,
    REDACTED_JWT,
    REDACTED_MOBILE,
    REDACTED_NATIONAL_ID,
    REDACTED_OBJECT,
    REDACTED_SHEBA,
    REDACTED_SIGNED_URL_VALUE,
    redact,
)
from core.logging_config import configure_logging
from core.request_context import clear_request_context, set_request_context


class SecretBearingObject:
    def __str__(self):
        return "leaky object mobile=09123456789 email=owner@example.com token=unsafe-token"


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
            "status_code": 200,
            "otp_code": "123456",
            "reason_code": "invalid_password",
        }

        redacted = redact(payload)

        self.assertEqual(redacted["password"], REDACTED)
        self.assertEqual(redacted["items"][0]["refresh_token"], REDACTED)
        self.assertEqual(redacted["status_code"], 200)
        self.assertEqual(redacted["otp_code"], REDACTED)
        self.assertEqual(redacted["reason_code"], "invalid_password")
        self.assertIn("0912****789", redacted["profile"]["mobile"])
        self.assertIn(REDACTED, redacted["profile"]["note"])
        self.assertIn(f"otp={REDACTED}", redacted["profile"]["note"])
        self.assertNotIn("plain-password", json.dumps(redacted))
        self.assertNotIn("123456", json.dumps(redacted))

    def test_redact_masks_jwt_like_values_inside_messages(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"

        self.assertEqual(redact(f"token={token}"), f"token={REDACTED}")
        self.assertEqual(redact(f"raw {token}"), f"raw {REDACTED_JWT}")

    def test_redact_masks_telegram_bot_api_tokens_inside_httpx_messages(self):
        message = (
            'HTTP Request: POST '
            'https://api.telegram.org/bot1234567890:abcdefghijklmnopqrstuvwxyz/editMessageText '
            '"HTTP/1.1 400 Bad Request"'
        )

        redacted = redact(message)

        self.assertIn(f"https://api.telegram.org/bot{REDACTED}/editMessageText", redacted)
        self.assertNotIn("1234567890", redacted)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", redacted)

    def test_redact_preserves_integrity_hash_fields(self):
        hash_value = "21f13ee3385597725cf7e7b578ac042f54b012c5dc735d235a60206dd67e24ca"

        redacted = redact({
            "audit_event_hash": hash_value,
            "event_hash": hash_value,
            "trail_sha256": hash_value,
            "message": f"national 0079059744 hash {hash_value}",
        })

        self.assertEqual(redacted["audit_event_hash"], hash_value)
        self.assertEqual(redacted["event_hash"], hash_value)
        self.assertEqual(redacted["trail_sha256"], hash_value)
        self.assertIn(REDACTED_NATIONAL_ID, redacted["message"])

    def test_redact_preserves_uuid_correlation_ids_while_masking_standalone_national_ids(self):
        run_id = "838ee21e-90ab-46e0-8ed2-ac9181234567"
        request_id = "11111111-2222-3333-4444-555555555555"

        redacted = redact({
            "run_id": run_id,
            "request_id": request_id,
            "message": "کد ملی 0079059744 برای بررسی ثبت شد",
        })

        self.assertEqual(redacted["run_id"], run_id)
        self.assertEqual(redacted["request_id"], request_id)
        self.assertIn(REDACTED_NATIONAL_ID, redacted["message"])
        self.assertNotIn("0079059744", redacted["message"])

    def test_redact_masks_iranian_pii_signed_urls_and_file_names(self):
        signed_url = (
            "https://cdn.example.test/private/report.pdf?"
            "X-Amz-Signature=abcdef123456&X-Amz-Credential=credential-value&safe=1"
        )
        payload = {
            "email": "owner@example.com",
            "message": (
                "mobile +98 912 345 6789 card 6037-9911-1111-1111 "
                "sheba IR820540102680020817909002 national 0079059744 "
                "file_name=identity-card.png url="
                f"{signed_url}"
            ),
            "original_file_name": "passport.png",
            "signed_url": signed_url,
            "upload_session_id": "upload-session-abcdefghijklmnopqrstuvwxyz123456",
            "sid": "11111111-2222-3333-4444-555555555555",
        }

        redacted = redact(payload)
        rendered = json.dumps(redacted, ensure_ascii=False)

        self.assertEqual(redacted["email"], REDACTED_EMAIL)
        self.assertEqual(redacted["original_file_name"], REDACTED)
        self.assertEqual(redacted["signed_url"], REDACTED)
        self.assertEqual(redacted["upload_session_id"], REDACTED)
        self.assertEqual(redacted["sid"], REDACTED)
        self.assertIn(REDACTED_MOBILE, redacted["message"])
        self.assertIn(REDACTED_CARD, redacted["message"])
        self.assertIn(REDACTED_SHEBA, redacted["message"])
        self.assertIn(REDACTED_NATIONAL_ID, redacted["message"])
        self.assertIn(REDACTED_FILENAME, redacted["message"])
        self.assertIn(REDACTED_SIGNED_URL_VALUE, redacted["message"])
        for raw in (
            "owner@example.com",
            "+98 912 345 6789",
            "6037-9911-1111-1111",
            "IR820540102680020817909002",
            "0079059744",
            "identity-card.png",
            "passport.png",
            "abcdef123456",
            "credential-value",
            "upload-session-abcdefghijklmnopqrstuvwxyz123456",
            "11111111-2222-3333-4444-555555555555",
        ):
            self.assertNotIn(raw, rendered)

    def test_unknown_objects_are_reduced_to_safe_type_metadata(self):
        redacted = redact({"unsafe": SecretBearingObject()})

        self.assertEqual(redacted["unsafe"]["redacted"], REDACTED_OBJECT)
        self.assertIn("SecretBearingObject", redacted["unsafe"]["object_type"])
        rendered = json.dumps(redacted)
        self.assertNotIn("09123456789", rendered)
        self.assertNotIn("owner@example.com", rendered)
        self.assertNotIn("unsafe-token", rendered)

    def test_configure_logging_emits_json_with_context_and_redaction(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("api-test")

        raw_session_id = "11111111-2222-3333-4444-555555555555"
        set_request_context(request_id="req-1", actor_id=42, session_id_hash="c01f4e2d0e6c8b0c")
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
        self.assertEqual(payload["session_id_hash"], "c01f4e2d0e6c8b0c")
        self.assertEqual(payload["authorization"], REDACTED)
        self.assertEqual(payload["safe_field"], "visible")
        self.assertNotIn("hunter2", log_line)
        self.assertNotIn("unsafe-token", log_line)
        self.assertNotIn(raw_session_id, log_line)
        self.assertIn("0912****789", log_line)

    def test_json_formatter_does_not_stringify_unknown_extra_objects(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("api-test")

        logging.getLogger("tests.logging").info(
            "object extra attached",
            extra={"unsafe_object": SecretBearingObject()},
        )

        log_line = stream.getvalue().strip()
        payload = json.loads(log_line)
        self.assertEqual(payload["unsafe_object"]["redacted"], REDACTED_OBJECT)
        self.assertIn("SecretBearingObject", payload["unsafe_object"]["object_type"])
        self.assertNotIn("09123456789", log_line)
        self.assertNotIn("owner@example.com", log_line)
        self.assertNotIn("unsafe-token", log_line)

    def test_redaction_does_not_hide_operational_access_level_fields(self):
        redacted = redact({"access_level": "middle-admin", "access_token": "unsafe-token"})

        self.assertEqual(redacted["access_level"], "middle-admin")
        self.assertEqual(redacted["access_token"], REDACTED)

    def test_sid_redaction_matches_exact_sensitive_keys_not_incidental_substrings(self):
        redacted = redact(
            {
                "sid": "session-1",
                "session_sid": "session-2",
                "outside_reference": "safe",
                "residency_status": "resident",
            }
        )

        self.assertEqual(redacted["sid"], REDACTED)
        self.assertEqual(redacted["session_sid"], REDACTED)
        self.assertEqual(redacted["outside_reference"], "safe")
        self.assertEqual(redacted["residency_status"], "resident")

    def test_configure_logging_is_idempotent_and_preserves_unmanaged_handlers(self):
        root_logger = logging.getLogger()
        extra_handler = logging.StreamHandler(io.StringIO())
        root_logger.addHandler(extra_handler)

        first_stream = io.StringIO()
        second_stream = io.StringIO()
        with patch("sys.stdout", first_stream):
            configure_logging("api-test")
        with patch("sys.stdout", second_stream):
            configure_logging("api-test-2")

        managed_handlers = [
            handler for handler in root_logger.handlers if getattr(handler, "_trading_bot_managed", False)
        ]
        self.assertEqual(len(managed_handlers), 1)
        self.assertIn(extra_handler, root_logger.handlers)

        logging.getLogger("tests.logging").info("hello")
        payload = json.loads(second_stream.getvalue().strip())
        self.assertEqual(payload["service"], "api-test-2")

    def test_configure_logging_initializes_sentry_from_settings_without_raw_env_gate(self):
        sentry_init_calls = []
        fake_sentry = SimpleNamespace(init=lambda **kwargs: sentry_init_calls.append(kwargs))
        fake_settings = SimpleNamespace(
            log_level="INFO",
            log_format="json",
            error_tracking_dsn="https://examplePublicKey@example.ingest.sentry.io/1",
            environment="test",
            release_sha="sha-test",
            error_tracking_sample_rate=0.5,
        )
        fake_config = ModuleType("core.config")
        fake_config.settings = fake_settings

        with (
            patch("sys.stdout", io.StringIO()),
            patch.dict(sys.modules, {"sentry_sdk": fake_sentry, "core.config": fake_config}, clear=False),
            patch.dict("os.environ", {}, clear=True),
        ):
            configure_logging("api-test")

        self.assertEqual(len(sentry_init_calls), 1)
        self.assertEqual(
            sentry_init_calls[0]["dsn"],
            "https://examplePublicKey@example.ingest.sentry.io/1",
        )
        self.assertEqual(sentry_init_calls[0]["environment"], "test")


if __name__ == "__main__":
    unittest.main()
