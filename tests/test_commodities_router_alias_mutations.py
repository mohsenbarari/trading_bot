import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import schemas
from api.routers.commodities import delete_alias, update_alias


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.deleted = []
        self.commits = 0
        self.refresh_calls = 0

    async def execute(self, stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refresh_calls += 1


class CommoditiesRouterAliasMutationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_alias_handles_missing_and_duplicate_alias(self):
        with self.assertRaises(HTTPException) as exc_info:
            await update_alias(1, alias_update=schemas.CommodityAliasCreate(alias="new"), db=FakeDB([FakeExecuteResult(None)]), source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 404)

        alias = SimpleNamespace(id=1, alias="old")
        db = FakeDB([FakeExecuteResult(alias), FakeExecuteResult(SimpleNamespace(id=2))])
        with self.assertRaises(HTTPException) as exc_info:
            await update_alias(1, alias_update=schemas.CommodityAliasCreate(alias="taken"), db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 409)

    async def test_update_alias_and_delete_alias_commit_and_invalidate_cache(self):
        alias = SimpleNamespace(id=1, alias="old")
        db = FakeDB([FakeExecuteResult(alias), FakeExecuteResult(None)])
        with patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()) as invalidate_mock:
            result = await update_alias(1, alias_update=schemas.CommodityAliasCreate(alias="new"), db=db, source="bot")

        self.assertIs(result, alias)
        self.assertEqual(alias.alias, "new")
        self.assertEqual(db.commits, 1)
        invalidate_mock.assert_awaited_once()

        with self.assertRaises(HTTPException) as exc_info:
            await delete_alias(1, db=FakeDB([FakeExecuteResult(None)]), source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 404)

        alias = SimpleNamespace(id=2, alias="x")
        db = FakeDB([FakeExecuteResult(alias)])
        with patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()) as invalidate_mock:
            result = await delete_alias(2, db=db, source="bot")

        self.assertIsNone(result)
        self.assertEqual(db.deleted, [alias])
        self.assertEqual(db.commits, 1)
        invalidate_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()