from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import main
import run_bot
from core.application_writer_term import ApplicationWriterTermError


class WriterTermStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_rejects_bad_term_before_validation_or_database_startup(self) -> None:
        with patch(
            "main.validate_application_writer_term_runtime_settings",
            return_value=SimpleNamespace(enabled=True),
        ), patch.object(main.settings, "background_jobs_enabled", False), patch(
            "main.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is missing"),
        ), patch("main.configure_logging") as configure_logging, patch(
            "main.validate_otp_delivery_runtime_settings"
        ) as validate_otp, patch(
            "main.init_db", new=AsyncMock()
        ) as init_db_mock:
            with self.assertRaisesRegex(ApplicationWriterTermError, "missing"):
                async with main.lifespan(main.app):
                    pass

        configure_logging.assert_not_called()
        validate_otp.assert_not_called()
        init_db_mock.assert_not_awaited()

    async def test_api_rejects_background_jobs_in_fenced_runtime_before_term_or_db(self) -> None:
        with patch(
            "main.validate_application_writer_term_runtime_settings",
            return_value=SimpleNamespace(enabled=True),
        ), patch.object(main.settings, "background_jobs_enabled", True), patch(
            "main.require_application_writer_term"
        ) as require_term, patch("main.init_db", new=AsyncMock()) as init_db_mock:
            with self.assertRaisesRegex(ApplicationWriterTermError, "BACKGROUND_JOBS_ENABLED"):
                async with main.lifespan(main.app):
                    pass

        require_term.assert_not_called()
        init_db_mock.assert_not_awaited()

    async def test_http_gate_returns_503_without_calling_route_when_term_is_lost(self) -> None:
        request = SimpleNamespace(
            url=SimpleNamespace(path="/api/config"),
            client=SimpleNamespace(host="127.0.0.1"),
        )
        route = AsyncMock()
        with patch(
            "main.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term expired"),
        ):
            response = await main.enforce_application_writer_term(request, route)

        self.assertEqual(response.status_code, 503)
        route.assert_not_awaited()

    async def test_bot_rejects_bad_term_before_database_or_bot_creation(self) -> None:
        with patch("run_bot.assert_bot_runtime_surface"), patch(
            "run_bot.validate_application_writer_term_runtime_settings",
            return_value=SimpleNamespace(enabled=True),
        ), patch(
            "run_bot.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term missing"),
        ), patch("run_bot.configure_logging") as configure_logging, patch(
            "run_bot.init_db", new=AsyncMock()
        ) as init_db_mock, patch("run_bot.Bot") as bot:
            with self.assertRaisesRegex(ApplicationWriterTermError, "missing"):
                await run_bot.main()

        configure_logging.assert_not_called()
        init_db_mock.assert_not_awaited()
        bot.assert_not_called()

    async def test_bot_request_middleware_rechecks_term_before_provider_call(self) -> None:
        request = AsyncMock(return_value="ok")
        with patch("run_bot.require_application_writer_term") as require_term:
            result = await run_bot._writer_term_request_middleware()(request, object(), object())

        self.assertEqual(result, "ok")
        require_term.assert_called_once_with()
        request.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
