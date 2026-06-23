import pathlib
import unittest

from models.notification import Notification
from models.trade_delivery_receipt import (
    TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)


class TradeDeliveryReceiptSchemaTests(unittest.TestCase):
    def test_receipt_model_has_required_identity_constraints_and_indexes(self):
        columns = TradeDeliveryReceipt.__table__.columns

        for column_name in {
            "event_type",
            "dedupe_key",
            "trade_id",
            "trade_number",
            "offer_id",
            "recipient_user_id",
            "recipient_role",
            "channel",
            "destination_server",
            "status",
            "reason",
            "notification_id",
            "telegram_message_id",
            "worker_id",
            "lease_until",
            "attempt_count",
            "next_retry_at",
            "last_error",
            "last_error_class",
            "audit_payload",
            "event_created_at",
            "sent_at",
            "terminal_at",
            "created_at",
            "updated_at",
        }:
            with self.subTest(column_name=column_name):
                self.assertIn(column_name, columns)

        unique_constraints = {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in TradeDeliveryReceipt.__table__.constraints
            if getattr(constraint, "unique", False) or constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertEqual(
            unique_constraints["ux_trade_delivery_receipts_event_trade_recipient_channel"],
            ("event_type", "trade_number", "recipient_user_id", "channel"),
        )
        self.assertEqual(unique_constraints["ux_trade_delivery_receipts_dedupe_key"], ("dedupe_key",))

        index_names = {index.name for index in TradeDeliveryReceipt.__table__.indexes}
        for index_name in {
            "ix_trade_delivery_receipts_queue",
            "ix_trade_delivery_receipts_active_state",
            "ix_trade_delivery_receipts_lease_recovery",
            "ix_trade_delivery_receipts_terminal_cleanup",
            "ix_trade_delivery_receipts_recipient",
            "ix_trade_delivery_receipts_trade_audit",
        }:
            self.assertIn(index_name, index_names)

        self.assertEqual(
            TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES,
            {
                TradeDeliveryReceiptStatus.SENT.value,
                TradeDeliveryReceiptStatus.SKIPPED.value,
                TradeDeliveryReceiptStatus.NOT_REQUIRED.value,
                TradeDeliveryReceiptStatus.PERMANENT_FAILED.value,
            },
        )

    def test_notification_model_has_dedupe_and_payload_metadata(self):
        self.assertIn("dedupe_key", Notification.__table__.columns)
        self.assertIn("extra_payload", Notification.__table__.columns)

        index = next(
            index
            for index in Notification.__table__.indexes
            if index.name == "ux_notifications_dedupe_key_not_null"
        )
        self.assertTrue(index.unique)
        self.assertEqual([column.name for column in index.columns], ["dedupe_key"])

    def test_migration_is_additive_and_uses_trade_number_identity(self):
        migration_path = pathlib.Path("migrations/versions/f2a3b4c5d6e9_add_trade_delivery_receipts.py")
        source = migration_path.read_text(encoding="utf-8")

        self.assertIn('down_revision = "f1a2b3c4d5e8"', source)
        self.assertIn('"trade_delivery_receipts"', source)
        self.assertIn('"trade_number"', source)
        self.assertIn('"ux_trade_delivery_receipts_event_trade_recipient_channel"', source)
        self.assertIn('"dedupe_key IS NOT NULL"', source)
        self.assertIn("postgresql.JSONB", source)
        self.assertNotIn("drop_table(\"notifications\")", source)


if __name__ == "__main__":
    unittest.main()
