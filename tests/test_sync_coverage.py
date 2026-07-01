import ast
import json
from pathlib import Path
import unittest

from api.routers import sync
from core.sync_registry import SyncPolicy, get_sync_registry_entry, sync_registry_entries
from models.accountant_relation import AccountantRelation
from models.customer_relation import CustomerRelation
from models.invitation import Invitation
from models.notification import Notification
from models.admin_message import AdminBroadcastMessage, AdminMarketMessage
from models.telegram_admin_broadcast import TelegramAdminBroadcast, TelegramAdminBroadcastReceipt
from models.telegram_notification_outbox import TelegramNotificationOutbox
from models.trade_delivery_receipt import TradeDeliveryReceipt
from models.user_notification_preference import UserNotificationPreference


class SyncCoverageTests(unittest.TestCase):
    def test_non_messenger_tables_are_registered_for_sync(self):
        self.assertIs(sync.get_model_class("accountant_relations"), AccountantRelation)
        self.assertIs(sync.get_model_class("customer_relations"), CustomerRelation)
        self.assertIs(sync.get_model_class("invitations"), Invitation)
        self.assertIs(sync.get_model_class("admin_market_messages"), AdminMarketMessage)
        self.assertIs(sync.get_model_class("admin_broadcast_messages"), AdminBroadcastMessage)
        self.assertIs(sync.get_model_class("notifications"), Notification)
        self.assertIs(sync.get_model_class("user_notification_preferences"), UserNotificationPreference)
        self.assertIs(sync.get_model_class("trade_delivery_receipts"), TradeDeliveryReceipt)
        self.assertIs(sync.get_model_class("telegram_admin_broadcasts"), TelegramAdminBroadcast)
        self.assertIs(sync.get_model_class("telegram_admin_broadcast_receipts"), TelegramAdminBroadcastReceipt)
        self.assertIs(sync.get_model_class("telegram_notification_outbox"), TelegramNotificationOutbox)
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["accountant_relations"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["customer_relations"])
        self.assertLess(sync.TABLE_ORDER["accountant_relations"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["customer_relations"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["invitations"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["notifications"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["user_notification_preferences"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["admin_market_messages"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["admin_broadcast_messages"])
        self.assertLess(sync.TABLE_ORDER["invitations"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["notifications"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["trades"], sync.TABLE_ORDER["trade_delivery_receipts"])
        self.assertLess(sync.TABLE_ORDER["telegram_admin_broadcasts"], sync.TABLE_ORDER["telegram_admin_broadcast_receipts"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["telegram_notification_outbox"])

    def test_every_sync_registry_table_has_receiver_coverage(self):
        missing_model = []
        missing_order = []

        for table_name, entry in sync_registry_entries().items():
            if entry.policy != SyncPolicy.SYNC:
                continue
            if sync.get_model_class(table_name) is None:
                missing_model.append(table_name)
            if table_name not in sync.TABLE_ORDER:
                missing_order.append(table_name)

        self.assertEqual(missing_model, [])
        self.assertEqual(missing_order, [])

    def test_event_listener_sync_tables_have_receiver_coverage_or_no_sync_policy(self):
        events_source = Path(__file__).resolve().parents[1] / "core" / "events.py"
        tree = ast.parse(events_source.read_text(encoding="utf-8"))
        logged_tables = set()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "log_change":
                continue
            if len(node.args) < 2:
                continue
            table_arg = node.args[1]
            if isinstance(table_arg, ast.Constant) and isinstance(table_arg.value, str):
                logged_tables.add(table_arg.value)

        self.assertTrue(logged_tables)
        for table_name in sorted(logged_tables):
            with self.subTest(table_name=table_name):
                entry = get_sync_registry_entry(table_name)
                if entry.policy == SyncPolicy.SYNC:
                    self.assertIsNotNone(sync.get_model_class(table_name))
                    self.assertIn(table_name, sync.TABLE_ORDER)
                else:
                    self.assertIn(entry.policy, {SyncPolicy.NO_SYNC, SyncPolicy.INTERNAL_BOOKKEEPING})

    def test_notification_user_ids_are_extracted_from_sync_items(self):
        items = [
            {"table": "notifications", "data": {"user_id": 10}},
            {"table": "notifications", "data": json.dumps({"user_id": "11"})},
            {"table": "notifications", "data": json.dumps({"user_id": None})},
            {"table": "offers", "data": {"user_id": 99}},
        ]

        self.assertEqual(sync._notification_user_ids_from_items(items), {10, 11})


if __name__ == "__main__":
    unittest.main()
