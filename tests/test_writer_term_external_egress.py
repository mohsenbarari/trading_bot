from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from core import sms, telegram_gateway, web_push
from core.application_writer_term import ApplicationWriterTermError


class WriterTermExternalEgressTests(unittest.IsolatedAsyncioTestCase):
    def test_sms_refuses_before_http_when_term_is_invalid(self) -> None:
        with patch.object(sms.settings, "application_writer_term_enforced", True), patch(
            "core.sms.require_active_writer_term",
            side_effect=ApplicationWriterTermError("writer term expired"),
        ), patch("core.sms.httpx.post") as http_post:
            with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                sms._post_smsir_result("v1/send/bulk", {"messageText": "x"})

        http_post.assert_not_called()

    async def test_web_push_refuses_before_database_or_provider_when_term_is_invalid(self) -> None:
        database = type("Database", (), {"execute": AsyncMock()})()
        with patch("core.web_push.is_web_push_configured", return_value=True), patch(
            "core.web_push.require_active_writer_term",
            side_effect=ApplicationWriterTermError("writer term expired"),
        ), patch("core.web_push.asyncio.to_thread", new=AsyncMock()) as to_thread:
            with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                await web_push.send_web_push_to_user(database, 1, {"title": "x", "body": "y"})

        database.execute.assert_not_awaited()
        to_thread.assert_not_awaited()

    async def test_gateway_refuses_before_http_when_term_is_invalid(self) -> None:
        with patch.object(telegram_gateway.settings, "application_writer_term_enforced", True), patch(
            "core.telegram_gateway.require_active_writer_term",
            side_effect=ApplicationWriterTermError("writer term expired"),
        ), patch("core.telegram_gateway.httpx.AsyncClient") as client_ctor:
            with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                await telegram_gateway.send_message(1, "hello", bot_token="token")

        client_ctor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
