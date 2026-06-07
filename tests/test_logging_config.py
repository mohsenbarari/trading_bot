import io
import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.logging_config import JsonLogFormatter, RequestContextFilter, install_request_logging_middleware, redact
from core.request_context import bind_actor_context, clear_request_context, set_request_context


def _format_record(message: str, **extra):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(JsonLogFormatter())

    logger = logging.getLogger("tests.logging_config")
    old_handlers = logger.handlers[:]
    old_level = logger.level
    old_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info(message, extra=extra)
        return json.loads(stream.getvalue())
    finally:
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate
        clear_request_context()


def test_json_formatter_includes_request_context():
    set_request_context(
        request_id="req-123",
        method="POST",
        path="/api/trades",
        client_ip="192.0.2.10",
    )
    bind_actor_context(user_id=42, session_id="session-1", actor_role="مدیر ارشد")

    payload = _format_record("trade created", event="trade.created", trade_id=10024)

    assert payload["level"] == "INFO"
    assert payload["event"] == "trade.created"
    assert payload["request_id"] == "req-123"
    assert payload["method"] == "POST"
    assert payload["path"] == "/api/trades"
    assert payload["client_ip"] == "192.0.2.10"
    assert payload["user_id"] == 42
    assert payload["session_id"] == "session-1"
    assert payload["actor_role"] == "مدیر ارشد"
    assert payload["trade_id"] == 10024


def test_redaction_masks_common_secrets_and_mobile_numbers():
    redacted = redact(
        {
            "Authorization": "Bearer eyJabc.def.ghi",
            "password": "plain-text",
            "mobile": "09123456789",
            "nested": {"otp_code": "123456"},
            "message": "x-api-key=super-secret code=12345 09121112233",
        }
    )

    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["otp_code"] == "[REDACTED]"
    assert redacted["mobile"] == "0912****789"
    assert "super-secret" not in redacted["message"]
    assert "12345" not in redacted["message"]
    assert "0912****233" in redacted["message"]


def test_request_logging_middleware_returns_request_id_header():
    app = FastAPI()
    install_request_logging_middleware(app)

    @app.get("/health")
    async def health():
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/health", headers={"X-Request-ID": "req-test"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-test"
