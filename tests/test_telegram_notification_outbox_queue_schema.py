import pathlib
import unittest

from core.telegram_delivery_notification_action_contract import (
    TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES,
)
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

    def test_base_action_queue_index_migration_follows_feeder_state(self):
        source = pathlib.Path(
            "migrations/versions/"
            "fad4e5f6a7b8_expand_notification_action_queue.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "fac3d4e5f6a7"',
            source,
        )
        self.assertIn("queue_action:account_status", source)
        self.assertIn("queue_action:general_immediate", source)
        self.assertIn("queue_action:offer_validation_response", source)
        self.assertIn("queue_action:trade_unavailable", source)
        for source_type in TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES - {
            "queue_action:delayed_restriction",
            "queue_action:timed_security",
        }:
            self.assertIn(source_type, source)
        self.assertIn("('project_user_joined', 'offer_repeat_response')", source)

    def test_offer_success_queue_index_migration_is_current_head_child(self):
        source = pathlib.Path(
            "migrations/versions/fae5f6a7b8c9_expand_offer_success_queue.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "fad4e5f6a7b8"',
            source,
        )
        self.assertIn("offer_success_preview", source)
        self.assertIn("include_offer_success=False", source)

    def test_scheduled_operation_migration_is_current_head_and_adds_timed_sources(self):
        source = pathlib.Path(
            "migrations/versions/"
            "faf6a7b8c9d0_add_telegram_scheduled_operations.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "fae5f6a7b8c9"',
            source,
        )
        self.assertIn("telegram_scheduled_operations", source)
        self.assertIn("queue_action:delayed_restriction", source)
        self.assertIn("queue_action:timed_security", source)


if __name__ == "__main__":
    unittest.main()
