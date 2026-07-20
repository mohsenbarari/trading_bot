import unittest
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from core import telegram_gateway


class FakeAsyncClientContext:
    def __init__(self, *, response=None, error=None, exit_error=None):
        self.response = response
        self.error = error
        self.exit_error = exit_error
        self.post = AsyncMock(side_effect=self._post)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.exit_error is not None:
            raise self.exit_error
        return False

    async def _post(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.response


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"ok": True, "result": {"message_id": 42}}


class TelegramGatewayPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_hard_fails_on_iran_before_http_call(self):
        with patch("core.telegram_gateway.current_server", return_value="iran"), patch(
            "core.telegram_gateway.httpx.AsyncClient"
        ) as client_ctor:
            with self.assertRaises(telegram_gateway.TelegramGatewaySurfaceError):
                await telegram_gateway.send_message(1, "hello", bot_token="token")

        client_ctor.assert_not_called()

    async def test_foreign_gateway_delegates_to_telegram_http_client(self):
        client = FakeAsyncClientContext(response=FakeResponse())

        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            result = await telegram_gateway.send_message(
                9,
                "hello",
                parse_mode="HTML",
                bot_token="token",
                idempotency_key="offer-publish:9",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.transport_phase, "response_received")
        self.assertEqual(result.message_id, 42)
        self.assertEqual(result.idempotency_key, "offer-publish:9")
        self.assertEqual(client.post.await_args.args[0], "https://api.telegram.org/bottoken/sendMessage")
        self.assertEqual(
            client.post.await_args.kwargs["json"],
            {"chat_id": 9, "text": "hello", "parse_mode": "HTML"},
        )

    async def test_document_gateway_decodes_verified_content_into_multipart(self):
        document = b"safe-binary-report"
        client = FakeAsyncClientContext(response=FakeResponse())
        payload = {
            "chat_id": 9,
            "caption": "گزارش",
            "reply_markup": {"inline_keyboard": []},
            "document_base64": base64.b64encode(document).decode("ascii"),
            "document_filename": "report.xlsx",
            "document_sha256": hashlib.sha256(document).hexdigest(),
        }
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            result = await telegram_gateway.post_telegram_method(
                "sendDocument",
                payload,
                bot_token="token",
            )

        self.assertTrue(result.ok)
        request = client.post.await_args
        self.assertNotIn("json", request.kwargs)
        self.assertNotIn(payload["document_base64"], request.kwargs["data"].values())
        self.assertEqual(request.kwargs["data"]["chat_id"], "9")
        self.assertEqual(
            request.kwargs["data"]["reply_markup"],
            '{"inline_keyboard":[]}',
        )
        self.assertEqual(
            request.kwargs["files"]["document"],
            ("report.xlsx", document, "application/octet-stream"),
        )

    async def test_document_gateway_rejects_hash_mismatch_before_http_write(self):
        client = FakeAsyncClientContext(response=FakeResponse())
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            result = await telegram_gateway.post_telegram_method(
                "sendDocument",
                {
                    "chat_id": 9,
                    "caption": "گزارش",
                    "document_base64": base64.b64encode(b"report").decode("ascii"),
                    "document_filename": "report.xlsx",
                    "document_sha256": "0" * 64,
                },
                bot_token="token",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "ValueError")
        self.assertEqual(result.transport_phase, "pre_write")
        client.post.assert_not_awaited()

    async def test_missing_token_returns_failed_result_without_http_call(self):
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch.object(
            telegram_gateway.settings, "bot_token", None
        ), patch("core.telegram_gateway.os.getenv", return_value=None), patch(
            "core.telegram_gateway.httpx.AsyncClient"
        ) as client_ctor:
            result = await telegram_gateway.send_message(9, "hello")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "missing_bot_token")
        self.assertEqual(result.transport_phase, "pre_write")
        client_ctor.assert_not_called()

    async def test_response_survives_client_close_error_after_provider_reply(self):
        client = FakeAsyncClientContext(
            response=FakeResponse(),
            exit_error=RuntimeError("synthetic close failure"),
        )
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            result = await telegram_gateway.send_message(
                9,
                "hello",
                bot_token="token",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.message_id, 42)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.error, "RuntimeError")
        self.assertEqual(result.transport_phase, "response_received")

    async def test_transport_failures_record_prewrite_vs_unknown_write(self):
        for error, expected in (
            (httpx.ConnectError("connect failed"), "pre_write"),
            (httpx.ReadError("read failed"), "write_unknown"),
            (httpx.WriteTimeout("write timed out"), "write_unknown"),
        ):
            with self.subTest(error=type(error).__name__), patch(
                "core.telegram_gateway.current_server",
                return_value="foreign",
            ), patch(
                "core.telegram_gateway.httpx.AsyncClient",
                return_value=FakeAsyncClientContext(error=error),
            ):
                result = await telegram_gateway.send_message(
                    9,
                    "hello",
                    bot_token="token",
                )
            self.assertFalse(result.ok)
            self.assertEqual(result.transport_phase, expected)

    async def test_failure_log_uses_one_way_correlation_not_raw_queue_identity(self):
        raw_identity = "offer:source-user-7788:destination-channel-9911"
        with patch(
            "core.telegram_gateway.current_server",
            return_value="foreign",
        ), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeAsyncClientContext(error=httpx.ReadError("read failed")),
        ), self.assertLogs("core.telegram_gateway", level="DEBUG") as captured:
            await telegram_gateway.send_message(
                9,
                "hello",
                bot_token="token",
                idempotency_key=raw_identity,
            )

        record = captured.records[0]
        self.assertFalse(hasattr(record, "idempotency_key"))
        self.assertNotIn(raw_identity, "\n".join(captured.output))
        self.assertEqual(
            record.delivery_correlation_hash,
            telegram_gateway._delivery_correlation_hash(raw_identity),
        )
        self.assertNotEqual(record.delivery_correlation_hash, raw_identity)

    def test_correlation_hash_is_stable_domain_separated_and_null_safe(self):
        first = telegram_gateway._delivery_correlation_hash("queue-identity")
        self.assertEqual(first, telegram_gateway._delivery_correlation_hash("queue-identity"))
        self.assertNotEqual(first, telegram_gateway._delivery_correlation_hash("other"))
        self.assertEqual(len(first), 64)
        self.assertIsNone(telegram_gateway._delivery_correlation_hash(None))

    def test_sync_gateway_delegates_to_telegram_http_client(self):
        response = SimpleNamespace(status_code=200, text="", json=lambda: {"ok": True})

        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.post",
            return_value=response,
        ) as http_post:
            result = telegram_gateway.send_message_sync(
                9,
                "hello",
                parse_mode="HTML",
                bot_token="token",
                idempotency_key="trade-notify:9",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.idempotency_key, "trade-notify:9")
        http_post.assert_called_once_with(
            "https://api.telegram.org/bottoken/sendMessage",
            json={"chat_id": 9, "text": "hello", "parse_mode": "HTML"},
            timeout=10,
        )

    def test_sync_document_gateway_uses_verified_multipart(self):
        document = b"sync-report"
        response = SimpleNamespace(status_code=200, text="", json=lambda: {"ok": True})
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.post",
            return_value=response,
        ) as http_post:
            result = telegram_gateway.post_telegram_method_sync(
                "sendDocument",
                {
                    "chat_id": 9,
                    "caption": "report",
                    "document_base64": base64.b64encode(document).decode("ascii"),
                    "document_filename": "report.pdf",
                    "document_sha256": hashlib.sha256(document).hexdigest(),
                },
                bot_token="token",
            )

        self.assertTrue(result.ok)
        request = http_post.call_args
        self.assertNotIn("json", request.kwargs)
        self.assertEqual(request.kwargs["data"]["chat_id"], "9")
        self.assertEqual(
            request.kwargs["files"]["document"],
            ("report.pdf", document, "application/octet-stream"),
        )


class TelegramGatewayInventoryTests(unittest.TestCase):
    def test_telegram_api_url_is_centralized_outside_connectivity_probe(self):
        repo = Path(__file__).resolve().parents[1]
        scanned_paths = [
            *repo.joinpath("api").rglob("*.py"),
            *repo.joinpath("core").rglob("*.py"),
            *repo.joinpath("bot").rglob("*.py"),
            repo / "run_bot.py",
        ]
        allowed = {
            "core/telegram_gateway.py",
            "core/connectivity.py",
        }

        offenders = []
        for path in scanned_paths:
            relative = path.relative_to(repo).as_posix()
            if "__pycache__" in relative:
                continue
            if "api.telegram.org" in path.read_text(encoding="utf-8"):
                if relative not in allowed:
                    offenders.append(relative)

        self.assertEqual(offenders, [])

    def test_gateway_exception_document_is_present(self):
        repo = Path(__file__).resolve().parents[1]
        doc = repo / "docs" / "TELEGRAM_GATEWAY_EXCEPTIONS.md"
        text = doc.read_text(encoding="utf-8")

        self.assertIn("temporary bot-runtime exceptions", text)
        self.assertIn("core.telegram_gateway", text)


if __name__ == "__main__":
    unittest.main()
