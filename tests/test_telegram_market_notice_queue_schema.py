import pathlib
import unittest

from models.market_channel_notice_receipt import MarketChannelNoticeReceipt


class TelegramMarketNoticeQueueSchemaTests(unittest.TestCase):
    def test_queue_binding_columns_constraints_and_indexes_exist(self):
        table = MarketChannelNoticeReceipt.__table__
        self.assertIn("queue_job_id", table.columns)
        self.assertIn("queue_handed_off_at", table.columns)
        self.assertIn("queue_reconciliation_required_at", table.columns)

        indexes = {index.name for index in table.indexes}
        self.assertIn("ix_market_channel_notice_receipts_queue_handoff", indexes)
        self.assertIn("ux_market_channel_notice_receipts_queue_job", indexes)

        constraints = {constraint.name for constraint in table.constraints}
        self.assertIn("ck_market_channel_notice_receipts_queue_binding", constraints)
        self.assertIn("ck_market_channel_notice_receipts_queue_owner", constraints)

    def test_queue_binding_migration_follows_notification_outbox_head(self):
        source = pathlib.Path(
            "migrations/versions/"
            "f9c4d5e6f7ae_bind_market_notices_to_delivery_queue.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "f8b3c4d5e6fd"',
            source,
        )
        self.assertIn('"queue_job_id"', source)
        self.assertIn('"queue_handed_off_at"', source)
        self.assertIn('"queue_reconciliation_required_at"', source)
        self.assertIn("ck_market_channel_notice_receipts_queue_binding", source)
        self.assertIn("ck_market_channel_notice_receipts_queue_owner", source)
        self.assertIn("ux_market_channel_notice_receipts_queue_job", source)
        self.assertIn("ix_market_channel_notice_receipts_queue_handoff", source)


if __name__ == "__main__":
    unittest.main()
