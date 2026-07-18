from argparse import Namespace
from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from scripts import telegram_delivery_runtime_resume as cli


class TelegramRuntimeResumeCliTests(unittest.IsolatedAsyncioTestCase):
    def test_parser_and_scope_validation_are_fail_closed(self):
        parser = cli.build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([])
        common = {
            "request_id": "runtime-resume-request-0001",
            "requested_by": "on-call",
            "confirm": cli.CONFIRMATION_PHRASE,
        }
        with self.assertRaisesRegex(ValueError, "bot_identity_required"):
            cli.validate_args(Namespace(scope="bot", bot_identity=None, **common))
        with self.assertRaisesRegex(ValueError, "gateway_identity_forbidden"):
            cli.validate_args(
                Namespace(scope="gateway", bot_identity="primary", **common)
            )

    async def test_bad_confirmation_stops_before_external_resources(self):
        args = Namespace(
            scope="bot",
            bot_identity="primary",
            request_id="runtime-resume-request-0001",
            requested_by="on-call",
            confirm="wrong",
        )
        with patch.object(
            cli, "configured_telegram_delivery_credentials"
        ) as credentials, patch.object(
            cli.redis_async, "from_url"
        ) as redis_factory, patch.object(
            cli, "resume_telegram_runtime_gate", new=AsyncMock()
        ) as resume:
            with self.assertRaisesRegex(ValueError, "confirmation_mismatch"):
                await cli.run(args)
        credentials.assert_not_called()
        redis_factory.assert_not_called()
        resume.assert_not_awaited()

    async def test_output_is_minimal_and_redis_is_always_closed(self):
        redis_client = MagicMock()
        redis_client.ping = AsyncMock()
        redis_client.close = AsyncMock()
        report = MagicMock(
            scope="bot",
            gate_key="bot:primary",
            request_id="runtime-resume-request-0001",
            state="active",
            resumed_job_ids=(11, 12),
            idempotent_replay=False,
        )
        with patch.object(
            cli,
            "configured_telegram_delivery_credentials",
            return_value=object(),
        ), patch.object(
            cli.redis_async,
            "from_url",
            return_value=redis_client,
        ), patch.object(
            cli,
            "configured_redis_telegram_delivery_limiter",
            return_value=object(),
        ), patch.object(
            cli,
            "current_server",
            return_value="foreign",
        ), patch.object(
            cli,
            "resume_telegram_runtime_gate",
            new=AsyncMock(return_value=report),
        ) as resume:
            result = await cli.run(
                Namespace(
                    scope="bot",
                    bot_identity="primary",
                    request_id="runtime-resume-request-0001",
                    requested_by="operator@example.test",
                    confirm=cli.CONFIRMATION_PHRASE,
                )
            )
        self.assertEqual(result["resumed_job_count"], 2)
        self.assertNotIn("operator@example.test", repr(result))
        resume.assert_awaited_once()
        redis_client.close.assert_awaited_once()

    def test_untrusted_provider_text_is_not_returned(self):
        error = cli._safe_error(RuntimeError("token 123456:secret and 09120000000"))
        self.assertEqual(error["reason"], "RuntimeError")
        self.assertNotIn("0912", repr(error))


if __name__ == "__main__":
    unittest.main()
