import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.sync import receive_sync_data


class FakeDB:
    def __init__(self):
        self.execute_calls = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, *args, **kwargs):
        self.execute_calls.append((str(stmt), args, kwargs))
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


class SyncRouterReceiveSequencesTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_db_helper_paths(self):
        db = FakeDB()
        await db.rollback()
        self.assertEqual(db.rollbacks, 1)

        async with db.begin_nested() as nested:
            self.assertIsNone(nested)

    async def test_receive_sync_data_repairs_sequences_for_synced_tables(self):
        db = FakeDB()
        items = [
            {"table": "users", "operation": "INSERT", "id": 1, "data": {"full_name": "U"}},
            {"table": "chats", "operation": "INSERT", "id": 2, "data": {"title": "اطلاع‌رسانی"}},
            {"table": "chat_members", "operation": "INSERT", "id": 3, "data": {"chat_id": 2, "user_id": 1}},
            {"table": "offers", "operation": "INSERT", "id": 4, "data": {"price": 100}},
        ]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 4})
        statements = [call[0] for call in db.execute_calls]
        self.assertTrue(any("users_id_seq" in stmt for stmt in statements))
        self.assertTrue(any("chats_id_seq" in stmt for stmt in statements))
        self.assertTrue(any("chat_members_id_seq" in stmt for stmt in statements))
        self.assertTrue(any("offers_id_seq" in stmt for stmt in statements))

    async def test_receive_sync_data_survives_sequence_repair_failures(self):
        class SequenceFailDB(FakeDB):
            async def execute(self, stmt, *args, **kwargs):
                text = str(stmt)
                self.execute_calls.append((text, args, kwargs))
                if "users_id_seq" in text:
                    raise RuntimeError("setval failed")
                return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

        db = SequenceFailDB()
        items = [{"table": "users", "operation": "INSERT", "id": 1, "data": {"full_name": "U"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 1})


if __name__ == "__main__":
    unittest.main()