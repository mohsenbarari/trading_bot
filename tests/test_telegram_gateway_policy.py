import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.application_writer_term import ApplicationWriterTermError
from core import telegram_gateway


class FakeAsyncClientContext:
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.post = AsyncMock(side_effect=self._post)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
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
        self.assertEqual(result.message_id, 42)
        self.assertEqual(result.idempotency_key, "offer-publish:9")
        self.assertEqual(client.post.await_args.args[0], "https://api.telegram.org/bottoken/sendMessage")
        self.assertEqual(
            client.post.await_args.kwargs["json"],
            {"chat_id": 9, "text": "hello", "parse_mode": "HTML"},
        )

    async def test_async_gateway_refuses_a_stale_writer_term_before_token_or_http(self):
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.db.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term expired"),
        ), patch("core.telegram_gateway._resolve_bot_token") as resolve_token, patch(
            "core.telegram_gateway.httpx.AsyncClient"
        ) as client_ctor:
            with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                await telegram_gateway.send_message(9, "hello", bot_token="token")

        resolve_token.assert_not_called()
        client_ctor.assert_not_called()

    async def test_missing_token_returns_failed_result_without_http_call(self):
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch.object(
            telegram_gateway.settings, "bot_token", None
        ), patch("core.telegram_gateway.os.getenv", return_value=None), patch(
            "core.telegram_gateway.httpx.AsyncClient"
        ) as client_ctor:
            result = await telegram_gateway.send_message(9, "hello")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "missing_bot_token")
        client_ctor.assert_not_called()

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

    def test_sync_gateway_refuses_a_stale_writer_term_before_token_or_http(self):
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.db.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term expired"),
        ), patch("core.telegram_gateway._resolve_bot_token") as resolve_token, patch(
            "core.telegram_gateway.httpx.post"
        ) as http_post:
            with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                telegram_gateway.send_message_sync(9, "hello", bot_token="token")

        resolve_token.assert_not_called()
        http_post.assert_not_called()


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

    def test_aiogram_bot_constructor_inventory_has_only_fenced_runtime_paths(self):
        """Make a new raw Bot client an explicit three-site review event.

        The normal constructor is fenced by ``run_bot``'s session middleware.
        The one short-lived account-status constructor must delegate to the
        channel-invite provider boundary, which independently revalidates the
        Writer term.  Any additional constructor could bypass both guards.
        """

        repo = Path(__file__).resolve().parents[1]
        observed: set[str] = set()
        source_paths = [
            repo / "run_bot.py",
            *repo.joinpath("api").rglob("*.py"),
            *repo.joinpath("bot").rglob("*.py"),
            *repo.joinpath("core").rglob("*.py"),
        ]
        for path in source_paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            bot_names: set[str] = set()
            aiogram_names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "aiogram":
                    bot_names.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "Bot"
                    )
                elif isinstance(node, ast.Import):
                    aiogram_names.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "aiogram"
                    )
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if isinstance(function, ast.Name) and function.id in bot_names:
                    observed.add(path.relative_to(repo).as_posix())
                elif (
                    isinstance(function, ast.Attribute)
                    and function.attr == "Bot"
                    and isinstance(function.value, ast.Name)
                    and function.value.id in aiogram_names
                ):
                    observed.add(path.relative_to(repo).as_posix())

        self.assertEqual(
            observed,
            {
                "core/services/user_account_status_service.py",
                "run_bot.py",
            },
        )

        account_status_source = (repo / "core/services/user_account_status_service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "return await build_channel_join_request_line(bot, user_id=user_id)",
            account_status_source,
        )


if __name__ == "__main__":
    unittest.main()
