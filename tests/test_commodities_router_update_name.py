import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import schemas
from api.routers.commodities import update_commodity_name


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commits = 0
        self.refresh_calls = []

    async def execute(self, stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refresh_calls.append(obj)


class CommoditiesRouterUpdateNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_commodity_name_handles_missing_and_duplicate_name(self):
        with self.assertRaises(HTTPException) as exc_info:
            await update_commodity_name(1, commodity_update=schemas.CommodityCreate(name="New"), db=FakeDB([FakeExecuteResult(None)]), source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 404)

        commodity = SimpleNamespace(id=1, name="Old", aliases=[])
        db = FakeDB([FakeExecuteResult(commodity), FakeExecuteResult(SimpleNamespace(id=2))])
        with self.assertRaises(HTTPException) as exc_info:
            await update_commodity_name(1, commodity_update=schemas.CommodityCreate(name="Taken"), db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 409)

    async def test_update_commodity_name_commits_refreshes_and_invalidates_cache(self):
        commodity = SimpleNamespace(id=1, name="Old", aliases=[])
        db = FakeDB([FakeExecuteResult(commodity), FakeExecuteResult(None)])
        with patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()) as invalidate_mock:
            result = await update_commodity_name(1, commodity_update=schemas.CommodityCreate(name="New"), db=db, source="bot")

        self.assertIs(result, commodity)
        self.assertEqual(commodity.name, "New")
        self.assertEqual(db.commits, 1)
        invalidate_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()