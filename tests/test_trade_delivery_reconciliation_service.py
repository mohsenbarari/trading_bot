import inspect
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from core.services import trade_delivery_reconciliation_service as service
from core.services.trade_notification_audience_service import (
    TradeNotificationAudience,
    TradeNotificationAudienceRecipient,
    TradeNotificationChannelRequirement,
)
from models.notification import Notification
from models.trade import TradeStatus, TradeType
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceiptStatus,
)


NOW = datetime(2026, 6, 23, 9, 15, tzinfo=timezone.utc)


class FakeScalarResult:
    def __init__(self, values=None):
        self.values = list(values or [])

    def scalars(self):
        values = list(self.values)
        return SimpleNamespace(all=lambda: values)


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.execute_calls = []
        self.added = []
        self.commit_count = 0
        self.flush_count = 0

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        if self.execute_results:
            return self.execute_results.pop(0)
        return FakeScalarResult()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_count += 1

    async def flush(self):
        self.flush_count += 1


def make_trade(*, trade_number=10025, status=TradeStatus.COMPLETED):
    return SimpleNamespace(
        id=501,
        trade_number=trade_number,
        offer_id=77,
        offer=SimpleNamespace(home_server="iran", notes="notes"),
        offer_user_id=10,
        offer_user=SimpleNamespace(id=10, account_name="seller"),
        responder_user_id=20,
        responder_user=SimpleNamespace(id=20, account_name="buyer"),
        commodity_id=3,
        commodity=SimpleNamespace(name="coin"),
        trade_type=TradeType.BUY,
        quantity=20,
        price=150000,
        status=status,
        created_at=NOW,
    )


def make_requirement(channel, *, destination, required=True, reason=None):
    return TradeNotificationChannelRequirement(
        channel=channel,
        destination_server=destination,
        required=required,
        reason=reason or f"{channel}_required",
        telegram_id=9020 if channel == "telegram" and required else None,
        message=f"{channel} message",
    )


def make_recipient(user_id, *, role="responder", requirements=None):
    return TradeNotificationAudienceRecipient(
        recipient_user_id=user_id,
        recipient_role=role,
        principal_user_id=user_id if role != "accountant" else 10,
        side=role,
        counterparty_user_id=10 if user_id != 10 else 20,
        webapp_message="webapp message",
        extra_payload={
            "trade_number": 10025,
            "recipient_role": role,
            "route": f"/users/{user_id}",
        },
        channel_requirements=tuple(
            requirements
            or [
                make_requirement("webapp", destination="iran", required=True, reason="webapp_required"),
                make_requirement("telegram", destination="foreign", required=True, reason="telegram_required"),
            ]
        ),
    )


def make_audience(*, recipients=None, skipped_reason=None):
    return TradeNotificationAudience(
        event_type="trade_completed",
        trade_id=501,
        trade_number=10025,
        offer_id=77,
        offer_home_server="iran",
        trade_path_kind=None,
        trade_path_summary=None,
        recipients=tuple(recipients or [make_recipient(20), make_recipient(10, role="offer_owner")]),
        skipped_reason=skipped_reason,
    )


def make_receipt(
    *,
    user_id=20,
    channel="webapp",
    status=TradeDeliveryReceiptStatus.SENT,
    destination="iran",
    notification_id=91,
    telegram_message_id=None,
):
    return SimpleNamespace(
        id=41,
        event_type="trade_completed",
        dedupe_key=f"trade_completed:{channel}:10025:{user_id}",
        trade_id=501,
        trade_number=10025,
        offer_id=77,
        recipient_user_id=user_id,
        recipient_role="responder",
        channel=TradeDeliveryChannel.WEBAPP if channel == "webapp" else TradeDeliveryChannel.TELEGRAM,
        destination_server=destination,
        status=status,
        notification_id=notification_id,
        telegram_message_id=telegram_message_id,
        reason=f"{channel}_required",
    )


class TradeDeliveryReconciliationServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_report_includes_expected_recipients_and_missing_local_remote_obligations(self):
        audience = make_audience()

        report = service.build_expectation_reports_for_audience(
            audience,
            current_server="iran",
            receipt_map={},
            notification_map={},
        )

        self.assertEqual(report.trade_number, 10025)
        self.assertEqual(len(report.expectations), 4)
        self.assertEqual(
            {(expectation.recipient_user_id, expectation.channel) for expectation in report.expectations},
            {(20, "webapp"), (20, "telegram"), (10, "webapp"), (10, "telegram")},
        )
        webapp_expectations = [expectation for expectation in report.expectations if expectation.channel == "webapp"]
        telegram_expectations = [expectation for expectation in report.expectations if expectation.channel == "telegram"]
        self.assertTrue(all(expectation.local_owner for expectation in webapp_expectations))
        self.assertTrue(all(expectation.repairable for expectation in webapp_expectations))
        self.assertTrue(
            all(
                expectation.repair_action == service.LOCAL_REPAIR_ACTION_CREATE_PENDING_RECEIPT
                for expectation in webapp_expectations
            )
        )
        self.assertTrue(all(expectation.read_only for expectation in telegram_expectations))
        self.assertTrue(all(not expectation.repairable for expectation in telegram_expectations))
        self.assertTrue(
            all(
                expectation.repair_action == service.LOCAL_REPAIR_ACTION_READ_ONLY
                for expectation in telegram_expectations
            )
        )

    def test_replayed_completed_trade_missing_receipt_is_reported_repairable_without_duplicate_notification_gap(self):
        notification = Notification(
            id=91,
            user_id=20,
            message="existing",
            dedupe_key="trade_completed:webapp:10025:20",
            extra_payload={"trade_number": 10025},
        )
        report = service.build_expectation_reports_for_audience(
            make_audience(recipients=[make_recipient(20, requirements=[
                make_requirement("webapp", destination="iran", required=True, reason="webapp_required"),
            ])]),
            current_server="iran",
            receipt_map={},
            notification_map={"trade_completed:webapp:10025:20": notification},
        )

        expectation = report.expectations[0]
        self.assertTrue(expectation.missing_receipt)
        self.assertFalse(expectation.missing_notification)
        self.assertTrue(expectation.delivery_gap)
        self.assertTrue(expectation.repairable)
        self.assertEqual(expectation.repair_action, service.LOCAL_REPAIR_ACTION_CREATE_PENDING_RECEIPT)
        self.assertEqual(expectation.current_side_effect_state, "webapp_notification_with_dedupe_exists")

    def test_existing_sent_receipt_and_notification_have_no_delivery_gap(self):
        receipt = make_receipt(user_id=20, channel="webapp", status=TradeDeliveryReceiptStatus.SENT, notification_id=91)
        identity = ("trade_completed", 10025, 20, "webapp")
        notification = Notification(id=91, user_id=20, message="sent", dedupe_key="trade_completed:webapp:10025:20")

        report = service.build_expectation_reports_for_audience(
            make_audience(recipients=[make_recipient(20, requirements=[
                make_requirement("webapp", destination="iran", required=True, reason="webapp_required"),
            ])]),
            current_server="iran",
            receipt_map={identity: receipt},
            notification_map={"trade_completed:webapp:10025:20": notification},
        )

        expectation = report.expectations[0]
        self.assertFalse(expectation.missing_receipt)
        self.assertFalse(expectation.missing_notification)
        self.assertFalse(expectation.delivery_gap)
        self.assertFalse(expectation.repairable)
        self.assertEqual(expectation.repair_action, service.LOCAL_REPAIR_ACTION_NONE)

    def test_webapp_receipt_notification_id_without_dedupe_notification_is_still_a_gap(self):
        receipt = make_receipt(user_id=20, channel="webapp", status=TradeDeliveryReceiptStatus.SENT, notification_id=91)
        identity = ("trade_completed", 10025, 20, "webapp")

        report = service.build_expectation_reports_for_audience(
            make_audience(recipients=[make_recipient(20, requirements=[
                make_requirement("webapp", destination="iran", required=True, reason="webapp_required"),
            ])]),
            current_server="iran",
            receipt_map={identity: receipt},
            notification_map={},
        )

        expectation = report.expectations[0]
        self.assertTrue(expectation.missing_notification)
        self.assertTrue(expectation.delivery_gap)
        self.assertTrue(expectation.repairable)
        self.assertEqual(expectation.current_side_effect_state, "webapp_receipt_links_notification_id")

    def test_not_required_telegram_receipt_missing_is_auditable_on_foreign(self):
        audience = make_audience(
            recipients=[
                make_recipient(
                    77,
                    role="accountant",
                    requirements=[
                        make_requirement(
                            "telegram",
                            destination="foreign",
                            required=False,
                            reason="accountant_webapp_only",
                        ),
                    ],
                )
            ]
        )

        report = service.build_expectation_reports_for_audience(
            audience,
            current_server="foreign",
            receipt_map={},
            notification_map={},
        )

        expectation = report.expectations[0]
        self.assertFalse(expectation.required)
        self.assertTrue(expectation.missing_receipt)
        self.assertTrue(expectation.delivery_gap)
        self.assertTrue(expectation.repairable)
        self.assertEqual(
            expectation.repair_action,
            service.LOCAL_REPAIR_ACTION_CREATE_NOT_REQUIRED_RECEIPT,
        )

    def test_completed_trade_scan_statement_only_targets_completed_trades(self):
        stmt = service.build_completed_trade_shadow_scan_statement(limit=50, trade_numbers=[10025])
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("trades.status = 'COMPLETED'", compiled)
        self.assertIn("trades.trade_number IN (10025)", compiled)
        self.assertIn("ORDER BY trades.created_at DESC, trades.id DESC", compiled)
        self.assertIn("LIMIT 50", compiled)
        self.assertNotIn("archived", compiled.split("WHERE", 1)[1])

    async def test_shadow_report_reads_completed_trades_and_never_writes_or_sends(self):
        trade = make_trade()
        db = FakeDB([FakeScalarResult([trade])])

        with patch(
            "core.services.trade_delivery_reconciliation_service.build_trade_completion_notification_audience",
            new=AsyncMock(return_value=make_audience(recipients=[make_recipient(20)])),
        ), patch(
            "core.services.trade_delivery_reconciliation_service.load_trade_delivery_receipts_for_trade_numbers",
            new=AsyncMock(return_value={}),
        ), patch(
            "core.services.trade_delivery_reconciliation_service.load_trade_notifications_for_dedupe_keys",
            new=AsyncMock(return_value={}),
        ):
            report = await service.run_trade_delivery_shadow_reconciliation(
                db,
                current_server="iran",
                limit=10,
            )

        self.assertTrue(report.dry_run)
        self.assertEqual(report.trade_count, 1)
        self.assertEqual(report.expectation_count, 2)
        self.assertEqual(report.missing_receipt_count, 2)
        self.assertEqual(report.local_expectation_count, 1)
        self.assertEqual(report.read_only_count, 1)
        self.assertEqual(db.added, [])
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.flush_count, 0)

    async def test_support_audit_by_trade_number_filters_recipient_and_explains_delivery_state(self):
        trade = make_trade()
        db = FakeDB([FakeScalarResult([trade])])
        webapp_receipt = make_receipt(
            user_id=20,
            channel="webapp",
            status=TradeDeliveryReceiptStatus.SENT,
            destination="iran",
            notification_id=91,
        )
        webapp_notification = Notification(
            id=91,
            user_id=20,
            message="sent",
            dedupe_key="trade_completed:webapp:10025:20",
        )

        with patch(
            "core.services.trade_delivery_reconciliation_service.build_trade_completion_notification_audience",
            new=AsyncMock(return_value=make_audience(recipients=[make_recipient(20)])),
        ), patch(
            "core.services.trade_delivery_reconciliation_service.load_trade_delivery_receipts_for_trade_numbers",
            new=AsyncMock(return_value={("trade_completed", 10025, 20, "webapp"): webapp_receipt}),
        ), patch(
            "core.services.trade_delivery_reconciliation_service.load_trade_notifications_for_dedupe_keys",
            new=AsyncMock(return_value={"trade_completed:webapp:10025:20": webapp_notification}),
        ):
            report = await service.run_trade_delivery_support_audit_by_trade_number(
                db,
                current_server="iran",
                trade_number=10025,
                recipient_user_id=20,
            )

        self.assertTrue(report.dry_run)
        self.assertEqual(report.trade_count, 1)
        self.assertEqual(report.expectation_count, 2)
        by_channel = {expectation.channel: expectation for expectation in report.expectations}
        self.assertFalse(by_channel["webapp"].delivery_gap)
        self.assertEqual(by_channel["webapp"].current_side_effect_state, "webapp_notification_with_dedupe_exists")
        self.assertTrue(by_channel["telegram"].delivery_gap)
        self.assertTrue(by_channel["telegram"].read_only)
        self.assertEqual(by_channel["telegram"].repair_action, service.LOCAL_REPAIR_ACTION_READ_ONLY)
        self.assertIn("foreign", by_channel["telegram"].explanation)
        self.assertEqual(db.added, [])
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.flush_count, 0)

    def test_shadow_reconciler_source_has_no_user_facing_side_effects(self):
        source = inspect.getsource(service)

        self.assertNotIn("create_user_notification", source)
        self.assertNotIn("publish_user_event", source)
        self.assertNotIn("send_telegram_message_sync", source)
        self.assertNotIn("_queue_trade_telegram_message", source)
        self.assertNotIn(".add(", source)
        self.assertNotIn(".commit(", source)
        self.assertNotIn(".flush(", source)


if __name__ == "__main__":
    unittest.main()
