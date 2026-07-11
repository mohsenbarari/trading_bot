import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
import httpx
from httpx import ASGITransport

from core.request_context import get_request_context
from core.request_logging import (
    REQUEST_ID_HEADER,
    install_request_logging_middleware,
    is_sensitive_path,
    make_request_id,
    redact_sensitive_path_segments,
    should_log_request_path,
)


def make_test_app() -> FastAPI:
    app = FastAPI()
    install_request_logging_middleware(app)

    @app.get("/api/config")
    async def config():
        return {"request_id": get_request_context().get("request_id")}

    @app.get("/api/auth/token")
    async def token_probe():
        return {"ok": True}

    @app.get("/api/invitations/accept/{token}")
    async def invitation_accept(token: str):
        return {"token_length": len(token)}

    @app.patch("/api/chat/upload-sessions/{session_id}/chunk")
    async def upload_session_chunk(session_id: str):
        return {"session_length": len(session_id)}

    @app.get("/api/recovery/{code}")
    async def recovery_error(code: str):
        raise RuntimeError(f"recovery failed for {code}")

    @app.get("/assets/app.js")
    async def asset_probe():
        return {"ok": True}

    return app


async def call_app(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    raise_app_exceptions: bool = True,
) -> httpx.Response:
    transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, headers=headers)


class RequestLoggingTests(unittest.TestCase):
    def test_make_request_id_uses_incoming_header_with_length_cap(self):
        self.assertEqual(make_request_id(" req-1 "), "req-1")
        generated = make_request_id("x" * 200)
        self.assertEqual(len(generated), 36)
        self.assertNotEqual(generated, "x" * 200)
        self.assertEqual(len(make_request_id("trace.id:1-2_3")), len("trace.id:1-2_3"))
        self.assertEqual(len(make_request_id("bad value with spaces")), 36)
        self.assertTrue(make_request_id())

    def test_path_policy_marks_sensitive_and_static_paths(self):
        self.assertTrue(is_sensitive_path("/api/auth/login"))
        self.assertTrue(is_sensitive_path("/api/chat/files/abc123"))
        self.assertTrue(is_sensitive_path("/api/sessions/refresh"))
        self.assertTrue(is_sensitive_path("/api/chat/upload/finalize"))
        self.assertFalse(is_sensitive_path("/api/config"))
        self.assertFalse(should_log_request_path("/assets/app.js"))
        self.assertFalse(should_log_request_path("/font.woff2"))
        self.assertTrue(should_log_request_path("/api/config"))

    def test_sensitive_path_segment_redaction_keeps_safe_actions(self):
        self.assertEqual(
            redact_sensitive_path_segments("/api/invitations/accept/abcdefghijklmnopqrstuvwxyz"),
            "/api/invitations/accept/[REDACTED]",
        )
        self.assertEqual(
            redact_sensitive_path_segments("/api/chat/upload-sessions/upload-session-123456/chunk"),
            "/api/chat/upload-sessions/[REDACTED]/chunk",
        )
        self.assertEqual(
            redact_sensitive_path_segments("/api/sessions/recovery/123/approve"),
            "/api/sessions/recovery/[REDACTED]/approve",
        )
        self.assertEqual(
            redact_sensitive_path_segments("/api/auth/register-otp-request"),
            "/api/auth/register-otp-request",
        )
        self.assertEqual(
            redact_sensitive_path_segments("/api/invitations/lookup/short-secret"),
            "/api/invitations/lookup/[REDACTED]",
        )
        self.assertEqual(
            redact_sensitive_path_segments("/api/invitations/validate/INV-secret"),
            "/api/invitations/validate/[REDACTED]",
        )

    def test_middleware_sets_request_id_header_and_sanitized_access_log(self):
        app = make_test_app()

        with patch("core.request_logging._logger") as logger:
            response = asyncio.run(
                call_app(app, "GET", "/api/config?token=secret", headers={REQUEST_ID_HEADER: "req-123"})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers[REQUEST_ID_HEADER], "req-123")
        self.assertEqual(response.json()["request_id"], "req-123")

        logger.info.assert_called_once()
        extra = logger.info.call_args.kwargs["extra"]
        self.assertEqual(extra["request_id"], "req-123")
        self.assertEqual(extra["path"], "/api/config")
        self.assertEqual(extra["status_code"], 200)
        self.assertNotIn("token=secret", repr(extra))

    def test_sensitive_routes_are_flagged_without_logging_payloads(self):
        app = make_test_app()

        with patch("core.request_logging._logger") as logger:
            response = asyncio.run(
                call_app(app, "GET", "/api/auth/token?password=secret", headers={"authorization": "Bearer unsafe"})
            )

        self.assertEqual(response.status_code, 200)
        extra = logger.info.call_args.kwargs["extra"]
        self.assertTrue(extra["sensitive_route"])
        self.assertEqual(extra["path"], "/api/auth/token")
        self.assertNotIn("password=secret", repr(extra))
        self.assertNotIn("unsafe", repr(extra))

    def test_token_bearing_sensitive_paths_use_route_template(self):
        app = make_test_app()
        raw_token = "tok_abcdefghijklmnopqrstuvwxyz123456"

        with patch("core.request_logging._logger") as logger:
            response = asyncio.run(call_app(app, "GET", f"/api/invitations/accept/{raw_token}"))

        self.assertEqual(response.status_code, 200)
        extra = logger.info.call_args.kwargs["extra"]
        self.assertTrue(extra["sensitive_route"])
        self.assertEqual(extra["path"], "/api/invitations/accept/{token}")
        self.assertNotIn(raw_token, repr(extra))

    def test_unmatched_sensitive_paths_redact_secret_segments(self):
        app = make_test_app()
        raw_token = "abcdefghijklmnopqrstuvwxyz"

        with patch("core.request_logging._logger") as logger:
            response = asyncio.run(call_app(app, "GET", f"/api/invitations/accept/{raw_token}/missing"))

        self.assertEqual(response.status_code, 404)
        extra = logger.info.call_args.kwargs["extra"]
        self.assertTrue(extra["sensitive_route"])
        self.assertEqual(extra["path"], "/api/invitations/accept/[REDACTED]/missing")
        self.assertNotIn(raw_token, repr(extra))

    def test_upload_session_paths_use_route_template(self):
        app = make_test_app()
        raw_session_id = "upload-session-abcdefghijklmnopqrstuvwxyz123456"

        with patch("core.request_logging._logger") as logger:
            response = asyncio.run(call_app(app, "PATCH", f"/api/chat/upload-sessions/{raw_session_id}/chunk"))

        self.assertEqual(response.status_code, 200)
        extra = logger.info.call_args.kwargs["extra"]
        self.assertTrue(extra["sensitive_route"])
        self.assertEqual(extra["path"], "/api/chat/upload-sessions/{session_id}/chunk")
        self.assertNotIn(raw_session_id, repr(extra))

    def test_exception_logs_use_safe_path(self):
        app = make_test_app()
        raw_code = "recovery-code-abcdefghijklmnopqrstuvwxyz123456"

        with patch("core.request_logging.capture_exception", return_value="err-1") as capture, patch(
            "core.request_logging._logger"
        ) as logger:
            response = asyncio.run(
                call_app(app, "GET", f"/api/recovery/{raw_code}", raise_app_exceptions=False)
            )

        self.assertEqual(response.status_code, 500)
        capture_extra = capture.call_args.kwargs["extra"]
        log_extra = logger.exception.call_args.kwargs["extra"]
        self.assertEqual(capture_extra["path"], "/api/recovery/{code}")
        self.assertEqual(log_extra["path"], "/api/recovery/{code}")
        self.assertNotIn(raw_code, repr(capture_extra))
        self.assertNotIn(raw_code, repr(log_extra))

    def test_static_paths_receive_request_id_but_skip_access_log(self):
        app = make_test_app()

        with patch("core.request_logging._logger") as logger:
            response = asyncio.run(call_app(app, "GET", "/assets/app.js"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(REQUEST_ID_HEADER, response.headers)
        logger.info.assert_not_called()

    def test_client_ip_only_trusts_forwarded_headers_from_configured_proxy(self):
        app = make_test_app()

        with patch("core.request_logging._logger") as logger, patch(
            "core.request_logging._trusted_proxy_networks",
            return_value=(),
        ):
            response = asyncio.run(
                call_app(
                    app,
                    "GET",
                    "/api/config",
                    headers={"x-forwarded-for": "203.0.113.9", "x-real-ip": "198.51.100.4"},
                )
            )

        self.assertEqual(response.status_code, 200)
        extra = logger.info.call_args.kwargs["extra"]
        self.assertEqual(extra["client_ip"], "127.0.0.1")

    def test_client_ip_uses_forwarded_headers_from_trusted_proxy(self):
        app = make_test_app()

        with patch("core.request_logging._logger") as logger, patch(
            "core.request_logging._trusted_proxy_networks",
            return_value=(__import__("ipaddress").ip_network("127.0.0.1/32"),),
        ):
            response = asyncio.run(
                call_app(
                    app,
                    "GET",
                    "/api/config",
                    headers={"x-forwarded-for": "203.0.113.9, 10.0.0.2", "x-real-ip": "198.51.100.4"},
                )
            )

        self.assertEqual(response.status_code, 200)
        extra = logger.info.call_args.kwargs["extra"]
        self.assertEqual(extra["client_ip"], "198.51.100.4")

    def test_forwarded_host_only_trusted_from_configured_proxy(self):
        from core.request_logging import trusted_forwarded_host_from_request

        trusted_request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"x-forwarded-host": "coin.gold-trade.ir:443"},
        )
        untrusted_request = SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.10"),
            headers={"x-forwarded-host": "coin.gold-trade.ir:443"},
        )

        with patch(
            "core.request_logging._trusted_proxy_networks",
            return_value=(__import__("ipaddress").ip_network("127.0.0.1/32"),),
        ):
            self.assertEqual(trusted_forwarded_host_from_request(trusted_request), "coin.gold-trade.ir:443")
            self.assertIsNone(trusted_forwarded_host_from_request(untrusted_request))

    def test_metrics_use_sanitized_path_for_unmatched_sensitive_route(self):
        app = make_test_app()
        raw_token = "tok_abcdefghijklmnopqrstuvwxyz123456"

        with patch("core.request_logging.record_http_request") as record_http_request:
            response = asyncio.run(call_app(app, "GET", f"/api/invitations/accept/{raw_token}/missing"))

        self.assertEqual(response.status_code, 404)
        record_http_request.assert_called_once()
        self.assertEqual(
            record_http_request.call_args.kwargs["route"],
            "/api/invitations/accept/[REDACTED]/missing",
        )
        self.assertNotIn(raw_token, repr(record_http_request.call_args.kwargs))

    def test_metrics_use_route_template_for_matched_sensitive_route(self):
        app = make_test_app()
        raw_token = "tok_abcdefghijklmnopqrstuvwxyz123456"

        with patch("core.request_logging.record_http_request") as record_http_request:
            response = asyncio.run(call_app(app, "GET", f"/api/invitations/accept/{raw_token}"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(record_http_request.call_args.kwargs["route"], "/api/invitations/accept/{token}")


if __name__ == "__main__":
    unittest.main()
