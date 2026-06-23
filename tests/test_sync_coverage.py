import json
import unittest

from api.routers import sync
from models.accountant_relation import AccountantRelation
from models.customer_relation import CustomerRelation
from models.invitation import Invitation
from models.notification import Notification
from models.admin_message import AdminBroadcastMessage, AdminMarketMessage
from models.trade_delivery_receipt import TradeDeliveryReceipt


class SyncCoverageTests(unittest.TestCase):
    def test_non_messenger_tables_are_registered_for_sync(self):
        self.assertIs(sync.get_model_class("accountant_relations"), AccountantRelation)
        self.assertIs(sync.get_model_class("customer_relations"), CustomerRelation)
        self.assertIs(sync.get_model_class("invitations"), Invitation)
        self.assertIs(sync.get_model_class("admin_market_messages"), AdminMarketMessage)
        self.assertIs(sync.get_model_class("admin_broadcast_messages"), AdminBroadcastMessage)
        self.assertIs(sync.get_model_class("notifications"), Notification)
        self.assertIs(sync.get_model_class("trade_delivery_receipts"), TradeDeliveryReceipt)
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["accountant_relations"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["customer_relations"])
        self.assertLess(sync.TABLE_ORDER["accountant_relations"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["customer_relations"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["invitations"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["notifications"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["admin_market_messages"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["admin_broadcast_messages"])
        self.assertLess(sync.TABLE_ORDER["invitations"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["notifications"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["trades"], sync.TABLE_ORDER["trade_delivery_receipts"])

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
