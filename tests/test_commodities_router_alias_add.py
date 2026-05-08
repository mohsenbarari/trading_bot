import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import schemas
from api.routers.commodities import add_alias_to_commodity


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None, commit_exception=None):
        self.execute_results = list(execute_results or [])
        self.commit_exception = commit_exception
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.refresh_calls = 0

    async def execute(self, stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1
        if self.commit_exception is not None:
            raise self.commit_exception

    async def refresh(self, obj):
        self.refresh_calls += 1

    async def rollback(self):
        self.rollbacks += 1


class CommoditiesRouterAliasAddTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_alias_to_commodity_rejects_duplicate_alias(self):
        db = FakeDB([FakeExecuteResult(object())])
        with self.assertRaises(HTTPException) as exc_info:
            await add_alias_to_commodity(1, alias=schemas.CommodityAliasCreate(alias="gold"), db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 409)

    async def test_add_alias_to_commodity_maps_commit_failure_to_404_and_success_refreshes_cache(self):
        db = FakeDB([FakeExecuteResult(None)], commit_exception=RuntimeError("missing commodity"))
        with self.assertRaises(HTTPException) as exc_info:
            await add_alias_to_commodity(1, alias=schemas.CommodityAliasCreate(alias="gold"), db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(db.rollbacks, 1)

        db = FakeDB([FakeExecuteResult(None)])
        with patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()) as invalidate_mock:
            result = await add_alias_to_commodity(1, alias=schemas.CommodityAliasCreate(alias="gold"), db=db, source="bot")

        self.assertIs(result, db.added[0])
        self.assertEqual(result.commodity_id, 1)
        self.assertEqual(result.alias, "gold")
        invalidate_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()