import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from models.trade_delivery_receipt import TradeDeliveryReceipt
from models.user import User
from scripts.seed_shared_sync_tables import (
    DEFAULT_TABLES,
    SeedReferenceIndex,
    enrich_seed_payload,
    row_payload,
    seed_table,
)


class SeedSharedSyncTablesTests(unittest.TestCase):
    def setUp(self):
        self.references = SeedReferenceIndex(
            commodity_names_by_id={14: "gold-main"},
            offer_public_ids_by_id={30: "ofr_source_30", 31: "ofr_source_31"},
            trade_numbers_by_id={88: 10088},
            customer_relation_tokens_by_id={17: "customer-relation-token-17"},
        )

    def test_snapshot_payload_applies_shared_field_policy(self):
        user = User(
            id=7,
            mobile_number="09120000000",
            admin_password_hash="local-password-secret",
            must_change_password=True,
            avatar_file_id="local-chat-file",
        )
        user_data = row_payload("users", user)

        self.assertEqual(user_data["mobile_number"], "09120000000")
        self.assertNotIn("admin_password_hash", user_data)
        self.assertNotIn("must_change_password", user_data)
        self.assertNotIn("avatar_file_id", user_data)

        receipt = TradeDeliveryReceipt(
            id=41,
            trade_id=88,
            offer_id=30,
            notification_id=31184,
            worker_id="foreign-worker",
        )
        receipt_data = row_payload("trade_delivery_receipts", receipt)
        for field_name in {"trade_id", "offer_id", "notification_id", "worker_id", "lease_until"}:
            self.assertNotIn(field_name, receipt_data)

    def test_seed_order_places_trades_before_terminal_offer_request_ledgers(self):
        self.assertLess(DEFAULT_TABLES.index("trades"), DEFAULT_TABLES.index("offer_requests"))
        self.assertLess(DEFAULT_TABLES.index("offer_requests"), DEFAULT_TABLES.index("trade_delivery_receipts"))
        self.assertEqual(DEFAULT_TABLES[-1], "notifications")

    def test_commodity_references_are_enriched_by_canonical_name(self):
        for table_name in ("commodity_aliases", "offers", "trades"):
            with self.subTest(table_name=table_name):
                row = SimpleNamespace(commodity_id=14, offer_id=None, republished_offer_id=None)
                data = enrich_seed_payload(table_name, row, {"commodity_id": 14}, self.references)
                self.assertEqual(data["commodity_name"], "gold-main")

    def test_trade_offer_reference_is_enriched_by_public_id(self):
        row = SimpleNamespace(commodity_id=14, offer_id=30)

        data = enrich_seed_payload("trades", row, {"offer_id": 30}, self.references)

        self.assertEqual(data["offer_public_id"], "ofr_source_30")
        self.assertEqual(data["commodity_name"], "gold-main")

    def test_offer_republication_reference_is_enriched_by_public_id(self):
        row = SimpleNamespace(commodity_id=14, republished_offer_id=31)

        data = enrich_seed_payload("offers", row, {"republished_offer_id": 31}, self.references)

        self.assertEqual(data["republished_offer_public_id"], "ofr_source_31")

    def test_offer_request_local_references_use_stable_identities(self):
        row = SimpleNamespace(resulting_trade_id=88, customer_relation_id=17)

        data = enrich_seed_payload(
            "offer_requests",
            row,
            {"resulting_trade_id": 88, "customer_relation_id": 17},
            self.references,
        )

        self.assertEqual(data["resulting_trade_number"], 10088)
        self.assertEqual(data["customer_relation_invitation_token"], "customer-relation-token-17")

    def test_missing_required_reference_fails_before_network_delivery(self):
        row = SimpleNamespace(commodity_id=999, offer_id=None)

        with self.assertRaisesRegex(ValueError, "trades.commodity_id"):
            enrich_seed_payload("trades", row, {"commodity_id": 999}, self.references)


class SeedSharedSyncDryRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_validates_references_without_network_delivery(self):
        references = SeedReferenceIndex(
            commodity_names_by_id={},
            offer_public_ids_by_id={},
            trade_numbers_by_id={},
            customer_relation_tokens_by_id={},
        )
        row = SimpleNamespace(
            __table__=SimpleNamespace(columns=[]),
            commodity_id=999,
            offer_id=None,
        )

        with patch(
            "scripts.seed_shared_sync_tables.load_table_rows",
            new=AsyncMock(return_value=[row]),
        ), patch("scripts.seed_shared_sync_tables.send_items", new=AsyncMock()) as sender:
            with self.assertRaisesRegex(ValueError, "trades.commodity_id"):
                await seed_table(
                    "trades",
                    target_url="https://peer.example",
                    api_key="test-key",
                    batch_size=100,
                    dry_run=True,
                    references=references,
                )

        sender.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
