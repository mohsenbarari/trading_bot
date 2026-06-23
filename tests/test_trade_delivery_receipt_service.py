import inspect
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from core.enums import NotificationCategory, NotificationLevel
from core.services import trade_delivery_receipt_service as service
from models.notification import Notification
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)


NOW = datetime(2026, 6, 23, 8, 30, tzinfo=timezone.utc)


class AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeScalarResult:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = list(values or [])

    def scalar(self):
        if self.value is not None and not isinstance(self.value, tuple):
            return self.value
        if self.values:
            return self.values[0]
        return None

    def one(self):
        if self.value is not None:
            return self.value if isinstance(self.value, tuple) else (self.value,)
        if self.values:
            value = self.values[0]
            return value if isinstance(value, tuple) else (value,)
        return (None, None)

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        values = list(self.values)
        value = self.value
        return SimpleNamespace(
            first=lambda: value if value is not None else (values[0] if values else None),
            all=lambda: values if values else ([value] if value is not None else []),
        )


class FakeDB:
    def __init__(self, execute_results=None, flush_error=None):
        self.execute_results = list(execute_results or [])
        self.flush_error = flush_error
        self.execute_calls = []
        self.added = []
        self.flush_count = 0
        self.commit_count = 0
        self.begin_nested_count = 0

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        if self.execute_results:
            return self.execute_results.pop(0)
        return FakeScalarResult()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_count += 1
        if self.flush_error is not None:
            error = self.flush_error
            self.flush_error = None
            raise error
        for obj in self.added:
            if isinstance(obj, Notification) and getattr(obj, "id", None) is None:
                obj.id = 91
                obj.created_at = NOW
            if isinstance(obj, TradeDeliveryReceipt) and getattr(obj, "id", None) is None:
                obj.id = 41

    async def commit(self):
        self.commit_count += 1

    def begin_nested(self):
        self.begin_nested_count += 1
        return AsyncNullContext()


def make_receipt(**overrides):
    data = {
        "id": 41,
        "event_type": "trade_completed",
        "dedupe_key": "trade_completed:webapp:10025:7",
        "trade_id": 501,
        "trade_number": 10025,
        "offer_id": 77,
        "recipient_user_id": 7,
        "recipient_role": "offer_owner",
        "channel": TradeDeliveryChannel.WEBAPP,
        "destination_server": "iran",
        "status": TradeDeliveryReceiptStatus.PENDING,
        "reason": "webapp_required",
        "notification_id": None,
        "telegram_message_id": None,
        "worker_id": None,
        "lease_until": None,
        "attempt_count": 0,
        "next_retry_at": None,
        "last_error": None,
        "last_error_class": None,
        "audit_payload": None,
        "event_created_at": NOW,
        "sent_at": None,
        "terminal_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TradeDeliveryReceiptServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_receipt_dedupe_key_uses_trade_number_not_local_trade_id(self):
        self.assertEqual(
            service.webapp_notification_dedupe_key(trade_number=10025, recipient_user_id=7),
            "trade_completed:webapp:10025:7",
        )
        self.assertEqual(
            service.trade_completed_receipt_dedupe_key(
                channel=TradeDeliveryChannel.TELEGRAM,
                trade_number=10025,
                recipient_user_id=8,
            ),
            "trade_completed:telegram:10025:8",
        )

    async def test_upsert_creates_pending_or_not_required_receipts_by_identity(self):
        db = FakeDB([FakeScalarResult()])

        result = await service.upsert_trade_delivery_receipt(
            db,
            event_type="trade_completed",
            trade_number=10025,
            recipient_user_id=7,
            recipient_role="offer_owner",
            channel=TradeDeliveryChannel.WEBAPP,
            destination_server="iran",
            required=True,
            reason="webapp_required",
            trade_id=501,
            offer_id=77,
            event_created_at=NOW,
            now=NOW,
        )

        self.assertTrue(result.created)
        self.assertEqual(result.receipt.status, TradeDeliveryReceiptStatus.PENDING)
        self.assertEqual(result.receipt.dedupe_key, "trade_completed:webapp:10025:7")
        self.assertEqual(result.receipt.trade_number, 10025)
        self.assertEqual(db.flush_count, 1)

        db = FakeDB([FakeScalarResult()])
        result = await service.upsert_trade_delivery_receipt(
            db,
            event_type="trade_completed",
            trade_number=10025,
            recipient_user_id=9,
            recipient_role="accountant",
            channel=TradeDeliveryChannel.TELEGRAM,
            destination_server="foreign",
            required=False,
            reason="accountant_webapp_only",
            event_created_at=NOW,
            now=NOW,
        )

        self.assertEqual(result.receipt.status, TradeDeliveryReceiptStatus.NOT_REQUIRED)
        self.assertEqual(result.receipt.terminal_at, NOW)

    async def test_upsert_preserves_terminal_receipt_from_reopen(self):
        existing = make_receipt(
            status=TradeDeliveryReceiptStatus.SENT,
            terminal_at=NOW,
            sent_at=NOW,
            notification_id=90,
        )
        db = FakeDB([FakeScalarResult(existing)])

        result = await service.upsert_trade_delivery_receipt(
            db,
            event_type="trade_completed",
            trade_number=10025,
            recipient_user_id=7,
            recipient_role="offer_owner",
            channel=TradeDeliveryChannel.WEBAPP,
            destination_server="iran",
            required=True,
            reason="webapp_required",
            now=NOW,
        )

        self.assertFalse(result.changed)
        self.assertTrue(result.terminal_preserved)
        self.assertEqual(existing.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(db.flush_count, 0)

    def test_state_machine_allows_expected_transitions_and_blocks_terminal_reopen(self):
        receipt = make_receipt(status=TradeDeliveryReceiptStatus.PENDING)

        result = service.transition_receipt_status(
            receipt,
            TradeDeliveryReceiptStatus.PROCESSING,
            current_server="iran",
            now=NOW,
        )
        self.assertTrue(result.changed)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.PROCESSING)
        self.assertEqual(receipt.attempt_count, 1)

        result = service.transition_receipt_status(
            receipt,
            TradeDeliveryReceiptStatus.SENT,
            current_server="iran",
            now=NOW,
            notification_id=91,
        )
        self.assertTrue(result.changed)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(receipt.notification_id, 91)
        self.assertEqual(receipt.terminal_at, NOW)

        with self.assertRaises(service.ReceiptLifecycleError):
            service.transition_receipt_status(
                receipt,
                TradeDeliveryReceiptStatus.PROCESSING,
                current_server="iran",
                now=NOW,
            )

    def test_processing_to_retry_pending_releases_worker_lease(self):
        receipt = make_receipt(
            status=TradeDeliveryReceiptStatus.PROCESSING,
            worker_id="worker-1",
            lease_until=NOW + timedelta(seconds=30),
            attempt_count=2,
        )

        result = service.transition_receipt_status(
            receipt,
            TradeDeliveryReceiptStatus.RETRY_PENDING,
            current_server="iran",
            now=NOW,
            error_class="TelegramRetryAfter",
            error_message="retry later",
            next_retry_at=NOW + timedelta(seconds=5),
        )

        self.assertTrue(result.changed)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.RETRY_PENDING)
        self.assertIsNone(receipt.worker_id)
        self.assertIsNone(receipt.lease_until)
        self.assertEqual(receipt.attempt_count, 2)
        self.assertEqual(receipt.last_error_class, "TelegramRetryAfter")
        self.assertEqual(receipt.last_error, "retry later")

    def test_state_machine_blocks_invalid_or_opposite_server_mutations(self):
        receipt = make_receipt(status=TradeDeliveryReceiptStatus.PENDING, destination_server="foreign")

        with self.assertRaises(service.ReceiptOwnershipError):
            service.transition_receipt_status(
                receipt,
                TradeDeliveryReceiptStatus.PROCESSING,
                current_server="iran",
                now=NOW,
            )

        receipt = make_receipt(status=TradeDeliveryReceiptStatus.PENDING)
        with self.assertRaises(service.ReceiptLifecycleError):
            service.transition_receipt_status(
                receipt,
                TradeDeliveryReceiptStatus.SENT,
                current_server="iran",
                now=NOW,
                notification_id=91,
            )

    def test_atomic_claim_uses_destination_status_due_time_and_skip_locked(self):
        stmt = service.build_claim_receipt_statement(
            destination_server="iran",
            worker_id="worker-1",
            lease_until=NOW + timedelta(seconds=30),
            now=NOW,
            channel=TradeDeliveryChannel.WEBAPP,
        )
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("FOR UPDATE SKIP LOCKED", compiled)
        self.assertIn("destination_server = 'iran'", compiled)
        self.assertIn("channel = 'webapp'", compiled)
        self.assertIn("status IN ('pending', 'retry_pending')", compiled)
        self.assertIn("next_retry_at IS NULL", compiled)
        self.assertNotIn("status IN ('processing'", compiled)

    def test_atomic_claim_by_identity_scopes_exact_webapp_receipt(self):
        stmt = service.build_claim_receipt_by_identity_statement(
            event_type="trade_completed",
            trade_number=10025,
            recipient_user_id=7,
            channel=TradeDeliveryChannel.WEBAPP,
            destination_server="iran",
            worker_id="worker-1",
            lease_until=NOW + timedelta(seconds=30),
            now=NOW,
        )
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("FOR UPDATE SKIP LOCKED", compiled)
        self.assertIn("event_type = 'trade_completed'", compiled)
        self.assertIn("trade_number = 10025", compiled)
        self.assertIn("recipient_user_id = 7", compiled)
        self.assertIn("channel = 'webapp'", compiled)
        self.assertIn("destination_server = 'iran'", compiled)
        self.assertIn("status IN ('pending', 'retry_pending')", compiled)

    def test_lease_recovery_only_targets_expired_local_processing_rows(self):
        stmt = service.build_recover_expired_leases_statement(
            destination_server="iran",
            now=NOW,
            max_rows=50,
        )
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("FOR UPDATE SKIP LOCKED", compiled)
        self.assertIn("destination_server = 'iran'", compiled)
        self.assertIn("status = 'processing'", compiled)
        self.assertIn("lease_until IS NOT NULL", compiled)
        self.assertIn("lease_until <= '2026-06-23 08:30:00+00:00'", compiled)
        self.assertIn("status='retry_pending'", compiled.replace(" ", ""))

    async def test_duplicate_webapp_notification_conflict_loads_existing_without_commit(self):
        existing = Notification(
            id=91,
            user_id=7,
            message="old",
            is_read=False,
            level=NotificationLevel.SUCCESS,
            category=NotificationCategory.TRADE,
            dedupe_key="trade_completed:webapp:10025:7",
            extra_payload={"route": "/users/7"},
        )
        integrity_error = IntegrityError("stmt", {}, Exception("duplicate key"))
        db = FakeDB([FakeScalarResult(), FakeScalarResult(existing)], flush_error=integrity_error)

        result = await service.create_or_load_webapp_notification_no_commit(
            db,
            user_id=7,
            message="new",
            dedupe_key="trade_completed:webapp:10025:7",
            extra_payload={"route": "/users/7"},
        )

        self.assertFalse(result.created)
        self.assertIs(result.notification, existing)
        self.assertEqual(db.begin_nested_count, 1)
        self.assertEqual(db.commit_count, 0)

    async def test_webapp_receipt_sent_and_notification_creation_share_outer_transaction(self):
        receipt = make_receipt(status=TradeDeliveryReceiptStatus.PROCESSING)
        db = FakeDB([FakeScalarResult()])

        result = await service.complete_webapp_receipt_with_notification(
            db,
            receipt=receipt,
            message="trade completed",
            extra_payload={"route": "/users/7", "trade_number": 10025},
            current_server="iran",
            now=NOW,
        )

        self.assertTrue(result.notification_created)
        self.assertTrue(result.sent_changed)
        self.assertEqual(result.notification.id, 91)
        self.assertEqual(result.notification.dedupe_key, "trade_completed:webapp:10025:7")
        self.assertEqual(result.notification.extra_payload["delivery_receipt_id"], 41)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(receipt.notification_id, 91)
        self.assertEqual(receipt.terminal_at, NOW)
        self.assertEqual(db.commit_count, 0)
        self.assertGreaterEqual(db.flush_count, 2)

    def test_outage_classification_skips_only_medium_or_long_opposite_server_delivery(self):
        short_remote = make_receipt(
            destination_server="iran",
            audit_payload={"extra_payload": {"offer_home_server": "foreign"}},
            event_created_at=NOW - timedelta(seconds=119),
        )
        medium_remote = make_receipt(
            destination_server="iran",
            audit_payload={"extra_payload": {"offer_home_server": "foreign"}},
            event_created_at=NOW - timedelta(minutes=10),
        )
        long_remote = make_receipt(
            destination_server="foreign",
            audit_payload={"extra_payload": {"offer_home_server": "iran"}},
            event_created_at=NOW - timedelta(hours=2),
        )
        delayed_local = make_receipt(
            destination_server="iran",
            audit_payload={"extra_payload": {"offer_home_server": "iran"}},
            event_created_at=NOW - timedelta(hours=2),
        )

        self.assertFalse(service.classify_receipt_outage_for_delivery(short_remote, now=NOW).should_skip_remote_delivery)
        medium = service.classify_receipt_outage_for_delivery(medium_remote, now=NOW)
        self.assertEqual(medium.outage_class, service.TRADE_DELIVERY_OUTAGE_MEDIUM)
        self.assertTrue(medium.should_skip_remote_delivery)
        long = service.classify_receipt_outage_for_delivery(long_remote, now=NOW)
        self.assertEqual(long.outage_class, service.TRADE_DELIVERY_OUTAGE_LONG)
        self.assertTrue(long.should_skip_remote_delivery)
        self.assertFalse(service.classify_receipt_outage_for_delivery(delayed_local, now=NOW).should_skip_remote_delivery)

    async def test_medium_outage_skip_marks_processing_receipt_terminal_without_user_side_effect(self):
        receipt = make_receipt(
            status=TradeDeliveryReceiptStatus.PROCESSING,
            destination_server="iran",
            audit_payload={"extra_payload": {"offer_home_server": "foreign"}},
            event_created_at=NOW - timedelta(minutes=10),
        )
        db = FakeDB()

        result = await service.skip_receipt_after_outage_if_needed(
            db,
            receipt,
            current_server="iran",
            now=NOW,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.outage_class, service.TRADE_DELIVERY_OUTAGE_MEDIUM)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SKIPPED)
        self.assertEqual(receipt.reason, service.TRADE_DELIVERY_EXPIRED_AFTER_OUTAGE_REASON)
        self.assertEqual(receipt.terminal_at, NOW)
        self.assertEqual(db.flush_count, 1)
        self.assertEqual(db.added, [])

    def test_terminal_retention_cleanup_statement_never_targets_non_terminal_rows(self):
        cutoff = NOW - timedelta(days=365)
        stmt = service.build_terminal_receipt_retention_cleanup_statement(cutoff=cutoff, limit=50)
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("DELETE FROM trade_delivery_receipts", compiled)
        self.assertIn("terminal_at < '2025-06-23 08:30:00+00:00'", compiled)
        self.assertIn("status IN", compiled)
        self.assertIn("sent", compiled)
        self.assertIn("skipped", compiled)
        self.assertIn("not_required", compiled)
        self.assertIn("permanent_failed", compiled)
        self.assertNotIn("pending", compiled)
        self.assertNotIn("retry_pending", compiled)

    async def test_retention_cleanup_alerts_on_old_non_terminal_before_deleting_terminal_rows(self):
        old_non_terminal_at = NOW - timedelta(days=370)
        db = FakeDB(
            [
                FakeScalarResult(value=3),
                FakeScalarResult(value=(2, old_non_terminal_at)),
                FakeScalarResult(values=[11, 12]),
            ]
        )

        with patch.object(service.logger, "warning") as warning_mock:
            report = await service.cleanup_terminal_trade_delivery_receipts(
                db,
                now=NOW,
                dry_run=False,
                limit=2,
            )

        self.assertFalse(report.dry_run)
        self.assertEqual(report.terminal_candidate_count, 3)
        self.assertEqual(report.terminal_deleted_count, 2)
        self.assertEqual(report.non_terminal_old_count, 2)
        self.assertTrue(report.alert_required)
        warning_mock.assert_called_once()
        self.assertEqual(
            warning_mock.call_args.kwargs["extra"]["event"],
            "trade_delivery_receipt.retention_non_terminal_old",
        )

    def test_receipt_backed_service_does_not_use_generic_auto_commit_helper(self):
        source = inspect.getsource(service)

        self.assertNotIn("create_user_notification", source)
        self.assertNotIn("await db.commit", source)


if __name__ == "__main__":
    unittest.main()
