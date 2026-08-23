from pathlib import Path
import unittest

from models.telegram_publisher_dispatch_command import (
    TelegramPublisherDispatchCommand,
)


class TelegramPublisherDispatchSchemaTests(unittest.TestCase):
    def test_model_has_durable_command_identity_receipt_and_lease_fields(self):
        columns = TelegramPublisherDispatchCommand.__table__.columns
        self.assertTrue(
            {
                "command_id",
                "job_id",
                "publisher_bot_identity",
                "dispatch_sequence",
                "state",
                "attempt_count",
                "next_retry_at",
                "lease_token",
                "lease_until",
                "sent_at",
                "acknowledged_at",
                "receipt_sequence",
                "receipt_received_at",
                "last_error_class",
                "last_error_message",
            }.issubset(columns.keys())
        )
        constraint_names = {
            constraint.name
            for constraint in TelegramPublisherDispatchCommand.__table__.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        self.assertTrue(
            {
                "ck_telegram_publisher_dispatch_commands_publisher",
                "ck_telegram_publisher_dispatch_commands_state",
                "ck_telegram_publisher_dispatch_commands_counters",
                "ck_telegram_publisher_dispatch_commands_acknowledged_at",
                "ck_telegram_publisher_dispatch_commands_receipt_sequence",
                "ck_telegram_publisher_dispatch_commands_receipt_timestamp",
            }.issubset(constraint_names)
        )
        index_names = {
            index.name for index in TelegramPublisherDispatchCommand.__table__.indexes
        }
        self.assertTrue(
            {
                "ix_telegram_publisher_dispatch_commands_claim",
                "ix_telegram_publisher_dispatch_commands_lease_recovery",
                "ix_telegram_publisher_dispatch_commands_lane_state",
            }.issubset(index_names)
        )
        claim_index = next(
            index
            for index in TelegramPublisherDispatchCommand.__table__.indexes
            if index.name == "ix_telegram_publisher_dispatch_commands_claim"
        )
        self.assertEqual([column.name for column in claim_index.columns], ["id"])
        self.assertIn(
            "state IN ('pending', 'retry_due', 'sent')",
            str(claim_index.dialect_options["postgresql"]["where"]),
        )

    def test_local_acknowledgement_needs_no_extra_liveness_table(self):
        # The durable command is the handoff, so lane liveness is not a
        # precondition and must not require its own table or migration.
        self.assertFalse(
            Path("models/telegram_publisher_lane_heartbeat.py").exists()
        )
        self.assertFalse(
            Path(
                "migrations/versions/ff7d8e9f0a12_add_publisher_lane_heartbeats.py"
            ).exists()
        )

    def test_claim_index_migration_replaces_the_narrow_predicate(self):
        source = Path(
            "migrations/versions/"
            "ff6c7d8e9f01_align_publisher_dispatch_claim_index.py"
        ).read_text(encoding="utf-8")

        self.assertIn('revision: str = "ff6c7d8e9f01"', source)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "a496c8d0e1f2"',
            source,
        )
        self.assertIn("state IN ('pending', 'retry_due', 'sent')", source)
        self.assertIn("state IN ('pending', 'retry_due')", source)
        self.assertIn('["id"]', source)
        upgrade = source[source.index("def upgrade") : source.index("def downgrade")]
        downgrade = source[source.index("def downgrade") :]
        self.assertIn("op.drop_index", upgrade)
        self.assertIn("op.create_index", upgrade)
        self.assertIn("op.drop_index", downgrade)
        self.assertIn('["state", "next_retry_at", "id"]', downgrade)

    def test_migration_backfills_legacy_owner_and_fails_closed_on_downgrade(self):
        source = Path(
            "migrations/versions/"
            "f9a0b1c2d3e4_add_telegram_publisher_dispatch_outbox.py"
        ).read_text(encoding="utf-8")

        self.assertIn('revision: str = "f9a0b1c2d3e4"', source)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "e8a4b5c6d7e9"',
            source,
        )
        self.assertIn('SET publisher_bot_identity = \'primary\'', source)
        self.assertIn('WHERE surface = \'telegram_channel\'', source)
        self.assertIn('"telegram_publisher_dispatch_commands"', source)
        self.assertIn(
            "enforce_offer_publication_telegram_owner_immutable",
            source,
        )
        self.assertIn(
            "enforce_telegram_publisher_dispatch_command_owner",
            source,
        )
        self.assertIn(
            "enforce_telegram_delivery_job_dispatch_owner_immutable",
            source,
        )
        downgrade_guard = source.rindex("multi-publisher Telegram evidence")
        drop_table = source.rindex('op.drop_table("telegram_publisher_dispatch_commands")')
        self.assertLess(downgrade_guard, drop_table)
        self.assertIn("RAISE EXCEPTION", source[downgrade_guard - 300 : downgrade_guard + 300])


if __name__ == "__main__":
    unittest.main()
