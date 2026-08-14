from argparse import Namespace
from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from scripts import telegram_delivery_queue_resume as cli


class TelegramDeliveryResumeCliTests(unittest.IsolatedAsyncioTestCase):
    def test_confirmation_is_exact_and_case_sensitive(self):
        cli.validate_confirmation(cli.CONFIRMATION_PHRASE)
        for value in (
            "",
            "resume configured telegram channel",
            " RESUME CONFIGURED TELEGRAM CHANNEL",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "confirmation_mismatch",
            ):
                cli.validate_confirmation(value)

    def test_parser_requires_request_actor_and_confirmation(self):
        parser = cli.build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([])
        args = parser.parse_args(
            [
                "--request-id",
                "resume-cli-request-0001",
                "--requested-by",
                "on-call",
                "--confirm",
                cli.CONFIRMATION_PHRASE,
            ]
        )
        self.assertEqual(args.request_id, "resume-cli-request-0001")

    async def test_bad_confirmation_stops_before_credentials_redis_or_database(self):
        with patch.object(
            cli,
            "configured_telegram_delivery_credentials",
        ) as credentials, patch.object(
            cli.redis_async,
            "from_url",
        ) as redis_factory, patch.object(
            cli,
            "resume_configured_telegram_channel",
            new=AsyncMock(),
        ) as resume:
            with self.assertRaisesRegex(ValueError, "confirmation_mismatch"):
                await cli.run(
                    Namespace(
                        request_id="resume-cli-request-0001",
                        requested_by="on-call",
                        confirm="wrong",
                    )
                )
        credentials.assert_not_called()
        redis_factory.assert_not_called()
        resume.assert_not_awaited()

    async def test_output_is_redacted_and_redis_is_closed(self):
        redis_client = MagicMock()
        redis_client.ping = AsyncMock()
        redis_client.close = AsyncMock()
        limiter = object()
        report = MagicMock(
            operation_id=9,
            request_id="resume-cli-request-0001",
            destination_key="channel:-100-secret-like",
            state="completed",
            resumed_job_ids=(1, 2),
            attempt_count=1,
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
            return_value=limiter,
        ), patch.object(
            cli,
            "current_server",
            return_value="foreign",
        ), patch.object(
            cli,
            "resume_configured_telegram_channel",
            new=AsyncMock(return_value=report),
        ) as resume:
            result = await cli.run(
                Namespace(
                    request_id="resume-cli-request-0001",
                    requested_by="on-call",
                    confirm=cli.CONFIRMATION_PHRASE,
                )
            )
        self.assertNotIn("channel:-100", repr(result))
        self.assertEqual(result["resumed_job_count"], 2)
        resume.assert_awaited_once()
        redis_client.close.assert_awaited_once()

    async def test_iran_stops_before_credentials_redis_or_database(self):
        with patch.object(
            cli,
            "current_server",
            return_value="iran",
        ), patch.object(
            cli,
            "configured_telegram_delivery_credentials",
        ) as credentials, patch.object(
            cli.redis_async,
            "from_url",
        ) as redis_factory, patch.object(
            cli,
            "resume_configured_telegram_channel",
            new=AsyncMock(),
        ) as resume:
            with self.assertRaisesRegex(ValueError, "foreign_local"):
                await cli.run(
                    Namespace(
                        request_id="resume-cli-request-0001",
                        requested_by="on-call",
                        confirm=cli.CONFIRMATION_PHRASE,
                    )
                )
        credentials.assert_not_called()
        redis_factory.assert_not_called()
        resume.assert_not_awaited()

    def test_untrusted_error_text_is_not_printable(self):
        error = cli._safe_error(RuntimeError("provider body with phone 09120000000"))
        self.assertEqual(error["reason"], "RuntimeError")
        self.assertNotIn("0912", repr(error))


if __name__ == "__main__":
    unittest.main()
