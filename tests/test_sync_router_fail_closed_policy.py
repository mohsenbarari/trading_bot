import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.sync import receive_sync_data


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, *args, **kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()


class SyncRouterFailClosedPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_table_returns_partial_failure_without_apply(self):
        db = FakeDB()
        items = [{"table": "mystery", "operation": "INSERT", "id": 8, "data": {}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock()) as apply_mock, patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        apply_mock.assert_not_awaited()
        self.assertEqual(
            result,
            {
                "status": "partial",
                "processed": 0,
                "errors": 1,
                "error_items": [{"table": "mystery", "record_id": 8, "reason": "unregistered_table"}],
            },
        )

    async def test_policy_forbidden_messenger_table_returns_partial_failure(self):
        db = FakeDB()
        items = [
            {
                "table": "messages",
                "operation": "INSERT",
                "id": 55,
                "data": {"chat_id": 7, "sender_id": 9, "text": "local messenger"},
            }
        ]

        with patch("api.routers.sync._apply_item", new=AsyncMock()) as apply_mock, patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        apply_mock.assert_not_awaited()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(
            result["error_items"],
            [{"table": "messages", "record_id": 55, "reason": "policy_forbidden:no-sync"}],
        )

    async def test_sync_policy_table_without_receiver_model_returns_partial_failure(self):
        db = FakeDB()
        items = [
            {
                "table": "user_notification_preferences",
                "operation": "INSERT",
                "id": 88,
                "data": {"user_id": 10, "channel": "web_push", "enabled": True},
            }
        ]

        with patch("api.routers.sync._apply_item", new=AsyncMock()) as apply_mock, patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        apply_mock.assert_not_awaited()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(
            result["error_items"],
            [
                {
                    "table": "user_notification_preferences",
                    "record_id": 88,
                    "reason": "receiver_model_not_registered",
                }
            ],
        )

    async def test_mixed_batch_applies_valid_items_and_reports_invalid_item(self):
        db = FakeDB()
        items = [
            {"table": "users", "operation": "INSERT", "id": 10, "data": {"telegram_id": 10010}},
            {"table": "messages", "operation": "INSERT", "id": 56, "data": {"chat_id": 7, "sender_id": 10}},
        ]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")) as apply_mock, patch(
            "api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()
        ) as rollout_mock, patch("api.routers.sync.settings.server_mode", "iran"):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        apply_mock.assert_awaited_once()
        rollout_mock.assert_awaited_once_with(db)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(
            result["error_items"],
            [{"table": "messages", "record_id": 56, "reason": "policy_forbidden:no-sync"}],
        )

    async def test_mandatory_channel_projection_is_the_only_messenger_exception(self):
        db = FakeDB()
        items = [
            {
                "table": "chats",
                "operation": "INSERT",
                "id": 12,
                "data": {"type": "channel", "is_system": True, "is_mandatory": True},
            },
            {
                "table": "chat_members",
                "operation": "INSERT",
                "id": 13,
                "data": {
                    "chat_id": 12,
                    "user_id": 9,
                    "chat_type": "channel",
                    "chat_is_system": True,
                    "chat_is_mandatory": True,
                },
            },
            {
                "table": "chats",
                "operation": "INSERT",
                "id": 14,
                "data": {"type": "group", "is_system": False, "is_mandatory": False},
            },
        ]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")) as apply_mock, patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(apply_mock.await_count, 2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(
            result["error_items"],
            [{"table": "chats", "record_id": 14, "reason": "policy_forbidden:no-sync"}],
        )


if __name__ == "__main__":
    unittest.main()
