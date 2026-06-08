import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.request_context import get_request_context
from core.request_logging import (
    REQUEST_ID_HEADER,
    install_request_logging_middleware,
    is_sensitive_path,
    make_request_id,
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

    @app.get("/assets/app.js")
    async def asset_probe():
        return {"ok": True}

    return app


class RequestLoggingTests(unittest.TestCase):
    def test_make_request_id_uses_incoming_header_with_length_cap(self):
        self.assertEqual(make_request_id(" req-1 "), "req-1")
        self.assertEqual(len(make_request_id("x" * 200)), 128)
        self.assertTrue(make_request_id())

    def test_path_policy_marks_sensitive_and_static_paths(self):
        self.assertTrue(is_sensitive_path("/api/auth/login"))
        self.assertTrue(is_sensitive_path("/api/sessions/refresh"))
        self.assertTrue(is_sensitive_path("/api/chat/upload/finalize"))
        self.assertFalse(is_sensitive_path("/api/config"))
        self.assertFalse(should_log_request_path("/assets/app.js"))
        self.assertFalse(should_log_request_path("/font.woff2"))
        self.assertTrue(should_log_request_path("/api/config"))

    def test_middleware_sets_request_id_header_and_sanitized_access_log(self):
        app = make_test_app()
        client = TestClient(app)

        with patch("core.request_logging._logger") as logger:
            response = client.get("/api/config?token=secret", headers={REQUEST_ID_HEADER: "req-123"})

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
        client = TestClient(app)

        with patch("core.request_logging._logger") as logger:
            response = client.get("/api/auth/token?password=secret", headers={"authorization": "Bearer unsafe"})

        self.assertEqual(response.status_code, 200)
        extra = logger.info.call_args.kwargs["extra"]
        self.assertTrue(extra["sensitive_route"])
        self.assertEqual(extra["path"], "/api/auth/token")
        self.assertNotIn("password=secret", repr(extra))
        self.assertNotIn("unsafe", repr(extra))

    def test_static_paths_receive_request_id_but_skip_access_log(self):
        app = make_test_app()
        client = TestClient(app)

        with patch("core.request_logging._logger") as logger:
            response = client.get("/assets/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn(REQUEST_ID_HEADER, response.headers)
        logger.info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
