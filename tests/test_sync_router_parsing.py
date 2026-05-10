import unittest
from datetime import datetime

from api.routers.sync import (
    _notification_user_ids_from_items,
    _parse_item,
    get_model_class,
)
from models.chat import Chat
from models.chat_member import ChatMember
from models.notification import Notification
from models.user import User


class SyncRouterParsingTests(unittest.TestCase):
    def test_get_model_class_resolves_known_models_and_none_for_unknown(self):
        self.assertIs(get_model_class("users"), User)
        self.assertIs(get_model_class("chats"), Chat)
        self.assertIs(get_model_class("chat_members"), ChatMember)
        self.assertIs(get_model_class("notifications"), Notification)
        self.assertIsNone(get_model_class("missing_table"))

    def test_notification_user_ids_from_items_collects_valid_notification_user_ids(self):
        items = [
            {"table": "notifications", "data": {"user_id": 5}},
            {"table": "notifications", "data": '{"user_id": "7"}'},
            {"table": "notifications", "data": '{"user_id": "oops"}'},
            {"table": "notifications", "data": "not-json"},
            {"table": "users", "data": {"user_id": 99}},
        ]

        self.assertEqual(_notification_user_ids_from_items(items), {5, 7})

    def test_parse_item_handles_json_and_datetime_fields_and_skips_unknown_tables(self):
        item = {
            "table": "notifications",
            "operation": "INSERT",
            "id": 12,
            "data": '{"message":"hi","created_at":"2026-01-01T12:00:00","read_at":"invalid"}',
        }

        table, operation, model, data, record_id = _parse_item(item)
        self.assertEqual(table, "notifications")
        self.assertEqual(operation, "INSERT")
        self.assertEqual(record_id, 12)
        self.assertIs(model, Notification)
        self.assertEqual(data["message"], "hi")
        self.assertIsInstance(data["created_at"], datetime)
        self.assertEqual(data["read_at"], "invalid")

        self.assertIsNone(_parse_item({"table": "missing", "operation": "INSERT", "id": 1, "data": {}}))


if __name__ == "__main__":
    unittest.main()