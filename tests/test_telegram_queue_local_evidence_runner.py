import asyncio
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import run_telegram_queue_local_evidence as runner


class TelegramQueueLocalEvidenceRunnerTests(unittest.TestCase):
    def test_rejects_nonlocal_or_nonscratch_database(self):
        for value in (
            "postgresql://u:p@db/trading_bot",
            "postgresql://u:p@127.0.0.1/production",
        ):
            with self.subTest(value=value), self.assertRaises(
                runner.LocalEvidenceConfigurationError
            ):
                runner._validated_database_urls(value)

    def test_rejects_default_or_remote_redis(self):
        for value in (
            "redis://127.0.0.1:6379/0",
            "redis://redis.internal:6379/15",
        ):
            with self.subTest(value=value), self.assertRaises(
                runner.LocalEvidenceConfigurationError
            ):
                runner._validated_redis_url(value)

    def test_environment_forces_empty_provider_credentials(self):
        with patch.dict(
            os.environ,
            {"BOT_TOKEN": "must-not-survive", "CHANNEL_EDITOR_BOT_TOKEN": "secret"},
            clear=False,
        ):
            runner._configure_synthetic_environment(
                database_url=(
                    "postgresql://u:p@127.0.0.1:55433/"
                    "telegram_queue_stage3_runner_test"
                ),
                redis_url="redis://127.0.0.1:56379/15",
            )
            self.assertEqual(os.environ["BOT_TOKEN"], "")
            self.assertEqual(os.environ["CHANNEL_EDITOR_BOT_TOKEN"], "")
            self.assertEqual(os.environ["ENVIRONMENT"], "synthetic-test")

    def test_scratch_lock_identity_is_stable_and_credential_free(self):
        first = runner._scratch_lock_identity(
            "postgresql://secret_user:secret_pass@127.0.0.1:55433/telegram_queue_stage3_runner_test",
            "redis://:redis_secret@127.0.0.1:56379/15",
        )
        second = runner._scratch_lock_identity(
            "postgresql://other:credentials@127.0.0.1:55433/telegram_queue_stage3_runner_test",
            "redis://:different@127.0.0.1:56379/15",
        )

        self.assertEqual(first, second)
        self.assertIsInstance(first[0], int)
        self.assertEqual(len(first[1]), 16)
        self.assertNotIn("secret", first[1])

    def test_scratch_lock_fails_closed_and_release_is_idempotent(self):
        class FakeCursor:
            def __init__(self, acquired):
                self.acquired = acquired
                self.executions = []

            def execute(self, statement, parameters):
                self.executions.append((statement, parameters))

            def fetchone(self):
                return (self.acquired,)

            def close(self):
                return None

        class FakeConnection:
            def __init__(self, acquired):
                self.autocommit = False
                self.closed = 0
                self.cursors = [FakeCursor(acquired), FakeCursor(True)]

            def cursor(self):
                return self.cursors.pop(0)

            def close(self):
                self.closed += 1

        acquired_connection = FakeConnection(True)
        with patch("psycopg2.connect", return_value=acquired_connection):
            scratch_lock = runner._acquire_exclusive_scratch_lock(
                database_url=(
                    "postgresql://u:p@127.0.0.1:55433/"
                    "telegram_queue_stage3_runner_test"
                ),
                redis_url="redis://127.0.0.1:56379/15",
            )
        scratch_lock.release()
        scratch_lock.release()
        self.assertEqual(acquired_connection.closed, 1)

        rejected_connection = FakeConnection(False)
        with patch("psycopg2.connect", return_value=rejected_connection), self.assertRaisesRegex(
            runner.LocalEvidenceConfigurationError,
            "already owned",
        ):
            runner._acquire_exclusive_scratch_lock(
                database_url=(
                    "postgresql://u:p@127.0.0.1:55433/"
                    "telegram_queue_stage3_runner_test"
                ),
                redis_url="redis://127.0.0.1:56379/15",
            )
        self.assertEqual(rejected_connection.closed, 1)

        broken_connection = FakeConnection(True)
        broken_connection.cursors[0].execute = Mock(
            side_effect=RuntimeError("synthetic lock failure")
        )
        with patch("psycopg2.connect", return_value=broken_connection), self.assertRaisesRegex(
            RuntimeError,
            "synthetic lock failure",
        ):
            runner._acquire_exclusive_scratch_lock(
                database_url=(
                    "postgresql://u:p@127.0.0.1:55433/"
                    "telegram_queue_stage3_runner_test"
                ),
                redis_url="redis://127.0.0.1:56379/15",
            )
        self.assertEqual(broken_connection.closed, 1)

    def test_loop_factory_uses_declared_threshold_and_captures_failures(self):
        failures: list[str] = []
        original = unittest.IsolatedAsyncioTestCase._setupAsyncioRunner
        try:
            runner._install_async_hygiene(failures)
            case = unittest.IsolatedAsyncioTestCase()
            case._setupAsyncioRunner()
            loop = case._asyncioRunner.get_loop()
            self.assertEqual(
                loop.slow_callback_duration,
                runner._SLOW_CALLBACK_THRESHOLD_SECONDS,
            )
            loop.call_exception_handler(
                {"message": "synthetic_unhandled", "exception": RuntimeError()}
            )
            self.assertEqual(failures, ["synthetic_unhandled:RuntimeError"])
            case._asyncioRunner.close()
        finally:
            unittest.IsolatedAsyncioTestCase._setupAsyncioRunner = original

    def test_report_is_machine_readable(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            runner._write_report(str(path), {"success": True, "tests_run": 3})
            self.assertIn('"success": true', path.read_text(encoding="utf-8"))

    def test_raw_log_stream_mirrors_output(self):
        console = io.StringIO()
        evidence = io.StringIO()
        stream = runner._TeeTextStream(console, evidence)

        stream.write("Ran 3 tests\nOK\n")
        stream.flush()

        self.assertEqual(console.getvalue(), evidence.getvalue())
        self.assertIn("Ran 3 tests", evidence.getvalue())


if __name__ == "__main__":
    unittest.main()
