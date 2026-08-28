from argparse import Namespace
from contextlib import redirect_stderr
import io
from types import SimpleNamespace
import unittest

from scripts import telegram_delivery_queue_confirm_absent as cli


class TelegramDeliveryConfirmAbsentCliTests(unittest.TestCase):
    def test_confirmation_is_exact(self):
        cli.validate_confirmation(cli.CONFIRMATION_PHRASE)
        for value in ("", cli.CONFIRMATION_PHRASE.lower(), f" {cli.CONFIRMATION_PHRASE}"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "confirmation_mismatch",
            ):
                cli.validate_confirmation(value)

    def test_only_ambiguous_channel_send_is_accepted(self):
        cli.validate_candidate(
            SimpleNamespace(
                state="ambiguous_unresolved",
                method="sendMessage",
                destination_class="channel",
            )
        )
        invalid = (
            ("sent", "sendMessage", "channel", "not_ambiguous"),
            ("ambiguous", "editMessageText", "channel", "method_not_send"),
            ("ambiguous", "sendMessage", "private", "destination_not_channel"),
        )
        for state, method, destination_class, reason in invalid:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                cli.validate_candidate(
                    SimpleNamespace(
                        state=state,
                        method=method,
                        destination_class=destination_class,
                    )
                )

    def test_parser_requires_accountable_evidence_and_confirmation(self):
        parser = cli.build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([])
        args = parser.parse_args(
            [
                "--job-id",
                "1445",
                "--requested-by",
                "on-call",
                "--evidence-reference",
                "owner-observed-channel-history",
                "--confirm",
                cli.CONFIRMATION_PHRASE,
            ]
        )
        self.assertEqual(args.job_id, 1445)

    def test_untrusted_error_text_is_redacted(self):
        error = cli._safe_error(RuntimeError("provider body with phone 09120000000"))
        self.assertEqual(error["reason"], "RuntimeError")
        self.assertNotIn("0912", repr(error))


if __name__ == "__main__":
    unittest.main()
