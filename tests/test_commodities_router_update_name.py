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
    async def test_update_commodity_name_rejects_digits(self):
        commodity = SimpleNamespace(id=1, name="نیم", aliases=[])
        db = FakeDB([FakeExecuteResult(commodity)])

        with self.assertRaises(HTTPException) as exc_info:
            await update_commodity_name(
                1,
                commodity_update=schemas.CommodityCreate(name="نیم86"),
                db=db,
                source="miniapp",
            )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "شما نمیتوانید در نام کالا از اعداد استفاده کنید")

    async def test_update_commodity_name_handles_missing_and_duplicate_name(self):
        with self.assertRaises(HTTPException) as exc_info:
            await update_commodity_name(1, commodity_update=schemas.CommodityCreate(name="New"), db=FakeDB([FakeExecuteResult(None)]), source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 404)

        commodity = SimpleNamespace(id=1, name="Old", aliases=[])
        db = FakeDB([FakeExecuteResult(commodity), FakeExecuteResult(SimpleNamespace(id=2)), FakeExecuteResult(None)])
        with self.assertRaises(HTTPException) as exc_info:
            await update_commodity_name(1, commodity_update=schemas.CommodityCreate(name="Taken"), db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertIn("نام اصلی یک کالا", exc_info.exception.detail)

        commodity = SimpleNamespace(id=1, name="Old", aliases=[])
        db = FakeDB([FakeExecuteResult(commodity), FakeExecuteResult(None), FakeExecuteResult(SimpleNamespace(id=4, commodity_id=2))])
        with self.assertRaises(HTTPException) as exc_info:
            await update_commodity_name(1, commodity_update=schemas.CommodityCreate(name="TakenAlias"), db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertIn("نام مستعار یک کالا", exc_info.exception.detail)

        commodity = SimpleNamespace(id=1, name="Old", aliases=[])
        db = FakeDB([FakeExecuteResult(commodity), FakeExecuteResult(None), FakeExecuteResult(SimpleNamespace(id=4, commodity_id=1))])
        with self.assertRaises(HTTPException) as exc_info:
            await update_commodity_name(1, commodity_update=schemas.CommodityCreate(name="OwnAlias"), db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertIn("نام مستعار یک کالا", exc_info.exception.detail)

    async def test_update_commodity_name_blocks_canonical_imam_rename(self):
        commodity = SimpleNamespace(id=1, name="امام", aliases=[])
        db = FakeDB([FakeExecuteResult(commodity)])

        with self.assertRaises(HTTPException) as exc_info:
            await update_commodity_name(1, commodity_update=schemas.CommodityCreate(name="سکه امامی"), db=db, source="miniapp")

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertIn("قابل ویرایش نیست", exc_info.exception.detail)

    async def test_update_commodity_name_commits_refreshes_and_invalidates_cache(self):
        commodity = SimpleNamespace(id=1, name="Old", aliases=[])
        db = FakeDB([FakeExecuteResult(commodity), FakeExecuteResult(None), FakeExecuteResult(None)])
        with patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()) as invalidate_mock:
            result = await update_commodity_name(1, commodity_update=schemas.CommodityCreate(name="New"), db=db, source="bot")

        self.assertIs(result, commodity)
        self.assertEqual(commodity.name, "New")
        self.assertEqual(db.commits, 1)
        invalidate_mock.assert_awaited_once()

    async def test_update_commodity_name_ignores_cache_invalidation_failures(self):
        commodity = SimpleNamespace(id=1, name="Old", aliases=[])
        db = FakeDB([FakeExecuteResult(commodity), FakeExecuteResult(None), FakeExecuteResult(None)])
        with patch(
            "bot.utils.redis_helpers.invalidate_commodity_cache",
            new=AsyncMock(side_effect=RuntimeError("cache down")),
        ):
            result = await update_commodity_name(
                1,
                commodity_update=schemas.CommodityCreate(name="Fresh"),
                db=db,
                source="bot",
            )

        self.assertEqual(result.name, "Fresh")


if __name__ == "__main__":
    unittest.main()
