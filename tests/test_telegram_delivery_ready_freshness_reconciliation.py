import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.telegram_delivery_queue_service import TelegramDeliveryQueueSurfaceError
from core.services.telegram_delivery_reconciliation_service import (
    reconcile_ready_telegram_delivery_jobs_by_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryState,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from scripts.reconcile_telegram_delivery_ready_jobs import (
    CONFIRMATION_PHRASE,
    TelegramReadyFreshnessReconcileError,
    _DryRunRollback,
    _validate_args,
    _validate_database,
)


NOW = datetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc)


class ReadyFreshnessReconciliationGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_non_foreign_server_before_database_touch(self):
        with self.assertRaises(TelegramDeliveryQueueSurfaceError):
            await reconcile_ready_telegram_delivery_jobs_by_freshness(
                None,
                current_server="iran",
                freshness_validators={},
                freshness_feedbacks={},
            )

    async def test_send_decision_leaves_pending_job_untouched(self):
        job = SimpleNamespace(
            id=11,
            state=TelegramDeliveryState.PENDING,
            bot_identity="primary",
            action_kind="overtime_owner_approval",
            lease_token=0,
            worker_id=None,
            lease_until=None,
            dispatch_started_at=None,
            rate_limit_probe=False,
            outcome_reason=None,
            updated_at=NOW,
            terminal_at=None,
        )
        validator = AsyncMock(
            return_value=TelegramFreshnessDecision(
                TelegramFreshnessOutcome.SEND,
                reason="overtime_owner_approval_freshness_current",
            )
        )
        feedback = AsyncMock()
        session = SimpleNamespace()
        execute_result = SimpleNamespace()
        execute_result.scalars = lambda: iter([job])
        session.execute = AsyncMock(return_value=execute_result)

        with patch(
            "core.services.telegram_delivery_reconciliation_service._transition_time",
            AsyncMock(return_value=NOW),
        ):
            report = await reconcile_ready_telegram_delivery_jobs_by_freshness(
                session,
                current_server="foreign",
                freshness_validators={"primary": validator},
                freshness_feedbacks={"primary": feedback},
                now=NOW,
            )

        self.assertEqual(report.inspected_count, 1)
        self.assertEqual(report.still_fresh_count, 1)
        self.assertEqual(report.freshness_terminal_count, 0)
        self.assertEqual(report.provider_network_calls, 0)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING)
        self.assertIsNone(job.outcome_reason)
        feedback.assert_not_awaited()


class ReadyFreshnessReconcileCliGuardTests(unittest.TestCase):
    def test_confirmation_and_staging_database_name_are_fail_closed(self):
        with self.assertRaises(TelegramReadyFreshnessReconcileError):
            _validate_args(
                SimpleNamespace(
                    confirm="wrong",
                    requested_by="operator",
                    max_rows=10,
                )
            )
        _validate_args(
            SimpleNamespace(
                confirm=CONFIRMATION_PHRASE,
                requested_by="operator",
                max_rows=10,
            )
        )
        with self.assertRaises(TelegramReadyFreshnessReconcileError):
            _validate_database(
                environment="staging",
                expected_database_name="trading_bot",
                raw_url="postgresql://db/trading_bot",
            )
        with self.assertRaises(TelegramReadyFreshnessReconcileError):
            _validate_database(
                environment="staging",
                expected_database_name="trading_bot_staging_prod",
                raw_url="postgresql://db/trading_bot_staging_prod",
            )
        _validate_database(
            environment="staging",
            expected_database_name="trading_bot_staging",
            raw_url="postgresql://db/trading_bot_staging",
        )
        rollback = _DryRunRollback({"status": "dry_run", "inspected_count": 1})
        self.assertEqual(rollback.payload["status"], "dry_run")


if __name__ == "__main__":
    unittest.main()
