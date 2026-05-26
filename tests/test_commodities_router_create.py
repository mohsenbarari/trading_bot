import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import schemas
from api.routers.commodities import create_commodity


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.added = []
        self.commits = 0
        self.refresh_calls = []

    async def execute(self, stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj, attrs=None):
        self.refresh_calls.append((obj, attrs))


class CommoditiesRouterCreateTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_commodity_rejects_digits_in_name_or_alias(self):
        db = FakeDB()
        with self.assertRaises(HTTPException) as exc_info:
            await create_commodity(
                commodity_data=schemas.CommodityCreate(name="نیم86"),
                aliases=["نیم86"],
                db=db,
                source="miniapp",
            )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "شما نمیتوانید در نام کالا از اعداد استفاده کنید")

        db = FakeDB()
        with self.assertRaises(HTTPException) as exc_info:
            await create_commodity(
                commodity_data=schemas.CommodityCreate(name="نیم"),
                aliases=["نیم", "ربع403"],
                db=db,
                source="miniapp",
            )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "شما نمیتوانید در نام کالا از اعداد استفاده کنید")

    async def test_create_commodity_rejects_duplicate_name(self):
        db = FakeDB([FakeExecuteResult(object())])
        with self.assertRaises(HTTPException) as exc_info:
            await create_commodity(
                commodity_data=schemas.CommodityCreate(name="Gold"),
                aliases=["gold"],
                db=db,
                source="miniapp",
            )
        self.assertEqual(exc_info.exception.status_code, 409)

    async def test_create_commodity_adds_unique_aliases_and_invalidates_cache(self):
        db = FakeDB([FakeExecuteResult(None)])
        with patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()) as invalidate_mock:
            result = await create_commodity(
                commodity_data=schemas.CommodityCreate(name="Gold"),
                aliases=["gold", "gold", "طلا"],
                db=db,
                source="bot",
            )

        self.assertIs(result, db.added[0])
        self.assertEqual(result.name, "Gold")
        self.assertEqual({alias.alias for alias in result.aliases}, {"gold", "طلا"})
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.refresh_calls[0][1], ["aliases"])
        invalidate_mock.assert_awaited_once()

    async def test_create_commodity_ignores_cache_invalidation_failures(self):
        db = FakeDB([FakeExecuteResult(None)])
        with patch(
            "bot.utils.redis_helpers.invalidate_commodity_cache",
            new=AsyncMock(side_effect=RuntimeError("cache down")),
        ):
            result = await create_commodity(
                commodity_data=schemas.CommodityCreate(name="Silver"),
                aliases=["silver"],
                db=db,
                source="bot",
            )

        self.assertEqual(result.name, "Silver")


if __name__ == "__main__":
    unittest.main()