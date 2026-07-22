import pathlib
import unittest

from models.telegram_admin_broadcast import (
    TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES,
    TelegramAdminBroadcast,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
)


class TelegramAdminBroadcastSchemaTests(unittest.TestCase):
    def test_broadcast_models_have_required_columns_constraints_and_indexes(self):
        broadcast_columns = TelegramAdminBroadcast.__table__.columns
        receipt_columns = TelegramAdminBroadcastReceipt.__table__.columns

        for column_name in {
            "content",
            "created_by_id",
            "audience_type",
            "target_groups",
            "recipient_count",
            "status",
            "queued_at",
            "queue_last_handed_off_at",
            "completed_at",
            "created_at",
            "updated_at",
        }:
            self.assertIn(column_name, broadcast_columns)

        broadcast_index_names = {
            index.name for index in TelegramAdminBroadcast.__table__.indexes
        }
        self.assertIn(
            "ix_telegram_admin_broadcasts_queue_fairness",
            broadcast_index_names,
        )

        for column_name in {
            "broadcast_id",
            "recipient_user_id",
            "telegram_id_at_enqueue",
            "telegram_id_at_send",
            "dedupe_key",
            "status",
            "reason",
            "telegram_message_id",
            "attempt_count",
            "next_retry_at",
            "last_error_class",
            "last_error_message",
            "worker_id",
            "lease_until",
            "queue_job_id",
            "queue_handed_off_at",
            "sent_at",
            "terminal_at",
            "created_at",
            "updated_at",
        }:
            self.assertIn(column_name, receipt_columns)

        unique_constraints = {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in TelegramAdminBroadcastReceipt.__table__.constraints
            if getattr(constraint, "unique", False) or constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertEqual(
            unique_constraints["ux_telegram_admin_broadcast_receipts_broadcast_recipient"],
            ("broadcast_id", "recipient_user_id"),
        )
        self.assertEqual(
            unique_constraints["ux_telegram_admin_broadcast_receipts_dedupe_key"],
            ("dedupe_key",),
        )

        index_names = {index.name for index in TelegramAdminBroadcastReceipt.__table__.indexes}
        self.assertIn("ix_telegram_admin_broadcast_receipts_active_queue", index_names)
        self.assertIn("ix_telegram_admin_broadcast_receipts_lease_recovery", index_names)
        self.assertIn("ix_telegram_admin_broadcast_receipts_recipient", index_names)
        self.assertIn("ix_telegram_admin_broadcast_receipts_queue_handoff", index_names)
        self.assertIn("ux_telegram_admin_broadcast_receipts_queue_job", index_names)

        self.assertEqual(
            TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES,
            {
                TelegramAdminBroadcastReceiptStatus.SENT.value,
                TelegramAdminBroadcastReceiptStatus.SKIPPED.value,
                TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED.value,
            },
        )

    def test_migration_is_additive_and_uses_dedupe_identity(self):
        migration_path = pathlib.Path("migrations/versions/f6c7d8e9f0a2_add_telegram_admin_broadcasts.py")
        source = migration_path.read_text(encoding="utf-8")

        self.assertIn('down_revision: Union[str, Sequence[str], None] = "f5c6d7e8f9a1"', source)
        self.assertIn('"telegram_admin_broadcasts"', source)
        self.assertIn('"telegram_admin_broadcast_receipts"', source)
        self.assertIn('"dedupe_key"', source)
        self.assertIn('"ux_telegram_admin_broadcast_receipts_dedupe_key"', source)
        self.assertIn("postgresql.JSONB", source)

    def test_queue_binding_migration_is_additive_and_local(self):
        migration_path = pathlib.Path(
            "migrations/versions/"
            "f7a2b3c4d5ec_bind_admin_broadcast_to_delivery_queue.py"
        )
        source = migration_path.read_text(encoding="utf-8")

        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "f6f1c2d3e4fb"',
            source,
        )
        self.assertIn('"queue_job_id"', source)
        self.assertIn('"queue_handed_off_at"', source)
        self.assertIn('"queue_last_handed_off_at"', source)
        self.assertIn("ck_telegram_admin_broadcast_receipts_queue_binding", source)
        self.assertIn("ix_telegram_admin_broadcasts_queue_fairness", source)
        self.assertIn("ux_telegram_admin_broadcast_receipts_queue_job", source)
        self.assertIn("ix_telegram_admin_broadcast_receipts_queue_handoff", source)


if __name__ == "__main__":
    unittest.main()
