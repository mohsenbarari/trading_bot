import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.telegram_registration_transport import (
    _post_signed_iran_command,
    forward_telegram_account_link_command,
    forward_telegram_registration_command,
)


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self.payload = payload if payload is not None else {"outcome": "created"}
        self.text = text

    def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class _Client:
    def __init__(self, outcome, calls):
        self.outcome = outcome
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class Stage9TelegramRegistrationTransportTests(unittest.IsolatedAsyncioTestCase):
    async def _post(self, **overrides):
        values = {
            "path": "/api/internal/test",
            "payload": {"idempotency_key": "stage9-key", "value": 1},
            "command_id": uuid4(),
            "event": "stage9.transport",
            "timeout_seconds": None,
        }
        values.update(overrides)
        return await _post_signed_iran_command(**values)

    async def test_transport_rejects_wrong_role_and_missing_peer(self):
        with override_current_server(SERVER_IRAN):
            status, body = await self._post()
        self.assertEqual(status, 403)
        self.assertIn("سرور تلگرام", body["detail"])

        with override_current_server(SERVER_FOREIGN), patch(
            "core.telegram_registration_transport.peer_server_url_for",
            return_value=None,
        ):
            status, body = await self._post()
        self.assertEqual(status, 503)
        self.assertIn("سرور ایران", body["detail"])

    async def test_transport_signs_exact_body_and_returns_json(self):
        calls = []
        with override_current_server(SERVER_FOREIGN), patch(
            "core.telegram_registration_transport.peer_server_url_for",
            return_value="https://iran.example",
        ), patch(
            "core.telegram_registration_transport.sign_internal_payload",
            return_value="signature",
        ), patch(
            "core.telegram_registration_transport.httpx.AsyncClient",
            return_value=_Client(_Response(status_code=201), calls),
        ) as client:
            status, body = await self._post(timeout_seconds=0.75)

        self.assertEqual(status, 201)
        self.assertEqual(body, {"outcome": "created"})
        self.assertEqual(calls[0][0], "https://iran.example/api/internal/test")
        self.assertEqual(calls[0][1]["headers"]["X-Source-Server"], SERVER_FOREIGN)
        self.assertEqual(calls[0][1]["headers"]["X-Signature"], "signature")
        self.assertEqual(calls[0][1]["content"], '{"idempotency_key":"stage9-key","value":1}')
        self.assertEqual(client.call_args.kwargs["timeout"], 0.75)

    async def test_transport_classifies_timeout_request_error_and_invalid_json(self):
        request = httpx.Request("POST", "https://iran.example")
        cases = (
            (httpx.ReadTimeout("timeout", request=request), 504),
            (httpx.ConnectError("offline", request=request), 503),
            (_Response(status_code=502, payload=ValueError("bad"), text="not-json"), 502),
        )
        for outcome, expected in cases:
            with self.subTest(outcome=type(outcome).__name__), override_current_server(
                SERVER_FOREIGN
            ), patch(
                "core.telegram_registration_transport.peer_server_url_for",
                return_value="https://iran.example",
            ), patch(
                "core.telegram_registration_transport.httpx.AsyncClient",
                return_value=_Client(outcome, []),
            ):
                status, body = await self._post()
            self.assertEqual(status, expected)
            self.assertIn("detail", body)

    async def test_public_wrappers_pass_exact_path_event_and_payload(self):
        registration = SimpleNamespace(
            command_id=uuid4(),
            model_dump=lambda **_kwargs: {"kind": "registration"},
        )
        account_link = SimpleNamespace(
            command_id=uuid4(),
            model_dump=lambda **_kwargs: {"kind": "account-link"},
        )
        with patch(
            "core.telegram_registration_transport._post_signed_iran_command",
            new=AsyncMock(side_effect=((200, {"ok": 1}), (201, {"ok": 2}))),
        ) as post:
            first = await forward_telegram_registration_command(registration, timeout_seconds=1)
            second = await forward_telegram_account_link_command(account_link, timeout_seconds=2)

        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 201)
        self.assertEqual(
            post.await_args_list[0].kwargs["path"],
            "/api/auth/internal/telegram-registration/reconcile",
        )
        self.assertEqual(
            post.await_args_list[1].kwargs["path"],
            "/api/auth/internal/telegram-link/complete",
        )
        self.assertEqual(post.await_args_list[0].kwargs["event"], "telegram_registration.forward_attempt")
        self.assertEqual(post.await_args_list[1].kwargs["event"], "telegram_account_link.forward_attempt")


if __name__ == "__main__":
    unittest.main()
