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
        self.is_closed = False
        self.post = AsyncMock(side_effect=self._post)
        self.aclose = AsyncMock(side_effect=self._aclose)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()
        return False

    async def _aclose(self):
        self.is_closed = True
        if self.exit_error is not None:
            raise self.exit_error

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
    def setUp(self):
        telegram_gateway.reset_telegram_gateway_http_client_for_test()

    async def asyncTearDown(self):
        await telegram_gateway.aclose_telegram_http_client()
        telegram_gateway.reset_telegram_gateway_http_client_for_test()

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

    async def test_async_gateway_omits_none_optional_fields_only_at_http_boundary(self):
        client = FakeAsyncClientContext(response=FakeResponse())
        payload = {
            "chat_id": 9,
            "text": "hello",
            "parse_mode": None,
            "disable_notification": False,
        }

        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            result = await telegram_gateway.post_telegram_method(
                "sendMessage",
                payload,
                bot_token="token",
            )

        self.assertTrue(result.ok)
        self.assertIn("parse_mode", payload)
        self.assertIsNone(payload["parse_mode"])
        self.assertEqual(
            client.post.await_args.kwargs["json"],
            {
                "chat_id": 9,
                "text": "hello",
                "disable_notification": False,
            },
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

    async def test_two_calls_reuse_one_http_client(self):
        client = FakeAsyncClientContext(response=FakeResponse())
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ) as client_ctor:
            first = await telegram_gateway.send_message(9, "hello", bot_token="token")
            second = await telegram_gateway.send_message(
                9,
                "again",
                bot_token="token",
                timeout=3,
            )

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        client_ctor.assert_called_once()
        self.assertEqual(
            client_ctor.call_args.kwargs["limits"],
            telegram_gateway._HTTP_CLIENT_LIMITS,
        )
        self.assertEqual(client.post.await_count, 2)
        self.assertEqual(client.post.await_args_list[0].kwargs["timeout"], 10)
        self.assertEqual(client.post.await_args_list[1].kwargs["timeout"], 3)
        client.aclose.assert_not_awaited()

    async def test_caller_timeout_is_passed_on_every_request(self):
        client = FakeAsyncClientContext(response=FakeResponse())
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            await telegram_gateway.post_telegram_method(
                "sendMessage",
                {"chat_id": 9, "text": "hello"},
                timeout=1.5,
                bot_token="token",
            )
            await telegram_gateway.post_telegram_method(
                "sendDocument",
                {
                    "chat_id": 9,
                    "document_base64": base64.b64encode(b"report").decode("ascii"),
                    "document_filename": "report.xlsx",
                    "document_sha256": hashlib.sha256(b"report").hexdigest(),
                },
                timeout=7,
                bot_token="token",
            )

        self.assertEqual(client.post.await_args_list[0].kwargs["timeout"], 1.5)
        self.assertEqual(client.post.await_args_list[1].kwargs["timeout"], 7)

    async def test_shutdown_closes_the_shared_client(self):
        client = FakeAsyncClientContext(response=FakeResponse())
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            result = await telegram_gateway.send_message(
                9,
                "hello",
                bot_token="token",
            )
            await telegram_gateway.aclose_telegram_http_client()

        self.assertTrue(result.ok)
        self.assertIsNone(result.error)
        client.aclose.assert_awaited_once()
        self.assertTrue(client.is_closed)

    async def test_close_error_on_shutdown_does_not_rewrite_prior_result(self):
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
            await telegram_gateway.aclose_telegram_http_client()

        self.assertTrue(result.ok)
        self.assertEqual(result.message_id, 42)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.error)
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

    def test_sync_gateway_omits_none_optional_fields_only_at_http_boundary(self):
        response = SimpleNamespace(status_code=200, text="", json=lambda: {"ok": True})

        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.post",
            return_value=response,
        ) as http_post:
            result = telegram_gateway.post_telegram_method_sync(
                "sendMessage",
                {
                    "chat_id": 9,
                    "text": "hello",
                    "parse_mode": None,
                    "disable_notification": False,
                },
                bot_token="token",
            )

        self.assertTrue(result.ok)
        http_post.assert_called_once_with(
            "https://api.telegram.org/bottoken/sendMessage",
            json={
                "chat_id": 9,
                "text": "hello",
                "disable_notification": False,
            },
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
