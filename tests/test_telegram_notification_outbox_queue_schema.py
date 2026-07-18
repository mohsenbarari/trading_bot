import pathlib
import unittest

from models.telegram_notification_outbox import TelegramNotificationOutbox


class TelegramNotificationOutboxQueueSchemaTests(unittest.TestCase):
    def test_queue_binding_columns_constraints_and_indexes_exist(self):
        table = TelegramNotificationOutbox.__table__
        self.assertIn("queue_job_id", table.columns)
        self.assertIn("queue_handed_off_at", table.columns)

        indexes = {index.name for index in table.indexes}
        self.assertIn("ix_telegram_notification_outbox_queue_handoff", indexes)
        self.assertIn("ux_telegram_notification_outbox_queue_job", indexes)

        constraints = {constraint.name for constraint in table.constraints}
        self.assertIn("ck_telegram_notification_outbox_queue_binding", constraints)

    def test_queue_binding_migration_follows_admin_broadcast_head(self):
        source = pathlib.Path(
            "migrations/versions/"
            "f8b3c4d5e6fd_bind_notification_outbox_to_delivery_queue.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "f7a2b3c4d5ec"',
            source,
        )
        self.assertIn('"queue_job_id"', source)
        self.assertIn('"queue_handed_off_at"', source)
        self.assertIn("ck_telegram_notification_outbox_queue_binding", source)
        self.assertIn("ux_telegram_notification_outbox_queue_job", source)
        self.assertIn("ix_telegram_notification_outbox_queue_handoff", source)
        self.assertIn("source_type = 'project_user_joined'", source)


if __name__ == "__main__":
    unittest.main()
