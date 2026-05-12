import json
import unittest

from api.routers import sync
from models.accountant_relation import AccountantRelation
from models.invitation import Invitation
from models.notification import Notification


class SyncCoverageTests(unittest.TestCase):
    def test_non_messenger_tables_are_registered_for_sync(self):
        self.assertIs(sync.get_model_class("accountant_relations"), AccountantRelation)
        self.assertIs(sync.get_model_class("invitations"), Invitation)
        self.assertIs(sync.get_model_class("notifications"), Notification)
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["accountant_relations"])
        self.assertLess(sync.TABLE_ORDER["accountant_relations"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["invitations"])
        self.assertLess(sync.TABLE_ORDER["users"], sync.TABLE_ORDER["notifications"])
        self.assertLess(sync.TABLE_ORDER["invitations"], sync.TABLE_ORDER["offers"])
        self.assertLess(sync.TABLE_ORDER["notifications"], sync.TABLE_ORDER["offers"])

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
