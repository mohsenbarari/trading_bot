import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_notification_outbox_worker as worker


class FakeSession:
    def __init__(self):
        self.commit = AsyncMock()


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class StrictLoopErrorLogger:
    def __init__(self):
        self.calls = []

    def log(self, logger, message, exc, metric_recorded=False, **extra):
        self.calls.append(
            {
                "logger": logger,
                "message": message,
                "exc": exc,
                "metric_recorded": metric_recorded,
                "extra": extra,
            }
        )


class TelegramNotificationOutboxWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_logs_alert_required_delivery_results(self):
        sessions = [FakeSession(), FakeSession()]
        alert_result = SimpleNamespace(
            status="terminal_failed",
            alert_required=True,
            reason="telegram_invalid_bot_config",
            outbox=SimpleNamespace(id=44),
            recipient_user_id=9,
        )
        empty_result = SimpleNamespace(status=worker.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_NO_ROW, alert_required=False)

        with patch("core.telegram_notification_outbox_worker.assert_background_job_authority") as authority_mock, patch(
            "core.telegram_notification_outbox_worker._recover_leases",
            new=AsyncMock(return_value=0),
        ), patch(
            "core.telegram_notification_outbox_worker.AsyncSessionLocal",
            side_effect=[FakeSessionContext(session) for session in sessions],
        ), patch(
            "core.telegram_notification_outbox_worker.claim_and_deliver_next_telegram_notification_outbox",
            new=AsyncMock(side_effect=[alert_result, empty_result]),
        ) as claim_mock, patch(
            "core.telegram_notification_outbox_worker.current_server",
            return_value="foreign",
        ), patch(
            "core.telegram_notification_outbox_worker.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "core.telegram_notification_outbox_worker.logger.warning"
        ) as warning_mock:
            report = await worker.run_telegram_notification_outbox_delivery_cycle(limit=5)

        authority_mock.assert_called_once_with("telegram_notification_outbox_delivery")
        self.assertEqual(claim_mock.await_count, 2)
        sessions[0].commit.assert_awaited_once()
        sessions[1].commit.assert_awaited_once()
        self.assertEqual(report.processed_count, 1)
        self.assertEqual(report.alert_count, 1)
        self.assertEqual(report.status_counts["terminal_failed"], 1)
        warning_mock.assert_called_once()
        self.assertEqual(
            warning_mock.call_args.kwargs["extra"]["event"],
            "telegram_notification_outbox.delivery_alert",
        )
        self.assertEqual(warning_mock.call_args.kwargs["extra"]["outbox_id"], 44)

    async def test_loop_continues_after_cycle_failure(self):
        cycle_error = RuntimeError("database unavailable")
        loop_errors = StrictLoopErrorLogger()

        with patch(
            "core.telegram_notification_outbox_worker.assert_background_job_authority"
        ) as authority_mock, patch(
            "core.telegram_notification_outbox_worker.run_telegram_notification_outbox_delivery_cycle",
            new=AsyncMock(side_effect=[cycle_error, asyncio.CancelledError()]),
        ) as cycle_mock, patch(
            "core.telegram_notification_outbox_worker.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep_mock, patch(
            "core.telegram_notification_outbox_worker._loop_errors",
            new=loop_errors,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await worker.telegram_notification_outbox_delivery_loop()

        authority_mock.assert_called_once_with("telegram_notification_outbox_delivery")
        self.assertEqual(cycle_mock.await_count, 2)
        sleep_mock.assert_awaited_once_with(worker._worker_interval_seconds())
        self.assertEqual(len(loop_errors.calls), 1)
        failure_call = loop_errors.calls[0]
        self.assertIs(failure_call["exc"], cycle_error)
        self.assertEqual(
            failure_call["message"],
            "Error in Telegram notification outbox worker loop: %s",
        )
        self.assertEqual(
            failure_call["extra"]["job_name"],
            "telegram_notification_outbox_delivery",
        )
        self.assertEqual(failure_call["extra"]["iteration"], 1)
        self.assertIn("run_id", failure_call["extra"])
        self.assertIn("duration_ms", failure_call["extra"])

    async def test_loop_propagates_cancellation_without_logging_failure(self):
        loop_errors = StrictLoopErrorLogger()

        with patch(
            "core.telegram_notification_outbox_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_notification_outbox_worker.run_telegram_notification_outbox_delivery_cycle",
            new=AsyncMock(side_effect=asyncio.CancelledError()),
        ) as cycle_mock, patch(
            "core.telegram_notification_outbox_worker.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep_mock, patch(
            "core.telegram_notification_outbox_worker._loop_errors",
            new=loop_errors,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await worker.telegram_notification_outbox_delivery_loop()

        cycle_mock.assert_awaited_once()
        sleep_mock.assert_not_awaited()
        self.assertEqual(loop_errors.calls, [])


if __name__ == "__main__":
    unittest.main()
