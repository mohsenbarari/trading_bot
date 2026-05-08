import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from api.routers.sync import _apply_item


class AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.execute_calls = []

    def begin_nested(self):
        return AsyncNullContext()

    async def execute(self, stmt, execution_options=None):
        self.execute_calls.append((stmt, execution_options))
        if self.execute_results:
            next_result = self.execute_results.pop(0)
            if isinstance(next_result, Exception):
                raise next_result
            return next_result
        return SimpleNamespace()


class DeleteBuilder:
    def where(self, _clause):
        return "DELETE_STMT"


class SyncRouterApplyItemErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_item_rejects_invalid_trading_setting_and_non_mergeable_duplicate(self):
        result = await _apply_item(
            FakeDB(),
            "trading_settings",
            "INSERT",
            1,
            {"value": "15"},
            model=object,
            new_offers=[],
        )
        self.assertEqual(result, "error")

        duplicate_error = IntegrityError("stmt", {}, Exception("duplicate key value violates unique constraint"))
        db = FakeDB([duplicate_error])
        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"):
            result = await _apply_item(
                db,
                "offers",
                "INSERT",
                9,
                {"price": 11},
                model=object,
                new_offers=[],
            )
        self.assertEqual(result, "error")

    async def test_apply_item_returns_deferred_on_foreign_key_and_handles_delete_paths(self):
        fk_error = IntegrityError("stmt", {}, Exception("foreign key violation"))
        db = FakeDB([fk_error])
        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                3,
                {"telegram_id": 1},
                model=object,
                new_offers=[],
            )
        self.assertEqual(result, "deferred")

        db = FakeDB()
        with patch("api.routers.sync.delete", return_value=DeleteBuilder()):
            result = await _apply_item(
                db,
                "users",
                "DELETE",
                4,
                {},
                model=type("M", (), {"id": object()}),
                new_offers=[],
            )
        self.assertEqual(result, "ok")

        delete_error = IntegrityError("stmt", {}, Exception("fk dependency"))
        db = FakeDB([delete_error])
        with patch("api.routers.sync.delete", return_value=DeleteBuilder()):
            result = await _apply_item(
                db,
                "users",
                "DELETE",
                4,
                {},
                model=type("M", (), {"id": object()}),
                new_offers=[],
            )
        self.assertEqual(result, "error")


if __name__ == "__main__":
    unittest.main()