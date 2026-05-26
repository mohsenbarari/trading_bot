import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.commodities import delete_commodity


class FakeScalars:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class FakeExecuteResult:
    def __init__(self, *, value=None, values=None):
        self._value = value
        self._values = list(values or [])

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return FakeScalars(self._values)


class FakeDB:
    def __init__(self, execute_results=None, delete_exception=None):
        self.execute_results = list(execute_results or [])
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0
        self.delete_exception = delete_exception

    async def execute(self, stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def delete(self, obj):
        self.deleted.append(obj)
        if self.delete_exception is not None and obj is self.delete_exception[0]:
            raise self.delete_exception[1]

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class CommoditiesRouterDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_commodity_handles_missing_references_and_delete_failure(self):
        with self.assertRaises(HTTPException) as exc_info:
            await delete_commodity(1, db=FakeDB([FakeExecuteResult(value=None)]), source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 404)

        commodity = SimpleNamespace(id=1, name="بهار", aliases=[])
        db = FakeDB(
            [
                FakeExecuteResult(value=commodity),
                FakeExecuteResult(value=1),
                FakeExecuteResult(value=1),
                FakeExecuteResult(value=0),
            ]
        )
        with self.assertRaises(HTTPException) as exc_info:
            await delete_commodity(1, db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertIn("1 لفظ فعال", exc_info.exception.detail)

        commodity = SimpleNamespace(id=1, name="امام", aliases=[])
        db = FakeDB([FakeExecuteResult(value=commodity)])
        with self.assertRaises(HTTPException) as exc_info:
            await delete_commodity(1, db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertIn("قابل حذف نیست", exc_info.exception.detail)

        commodity = SimpleNamespace(id=1, name="بهار", aliases=[])
        delete_error = RuntimeError("fk linked")
        db = FakeDB(
            [
                FakeExecuteResult(value=commodity),
                FakeExecuteResult(value=0),
                FakeExecuteResult(value=0),
                FakeExecuteResult(value=0),
            ],
            delete_exception=(commodity, delete_error),
        )
        with self.assertRaises(HTTPException) as exc_info:
            await delete_commodity(1, db=db, source="miniapp")
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(db.rollbacks, 1)

    async def test_delete_commodity_blocks_historical_offer_and_trade_references(self):
        commodity = SimpleNamespace(id=1, name="بهار", aliases=[])
        db = FakeDB(
            [
                FakeExecuteResult(value=commodity),
                FakeExecuteResult(value=0),
                FakeExecuteResult(value=2),
                FakeExecuteResult(value=3),
            ]
        )

        with self.assertRaises(HTTPException) as exc_info:
            await delete_commodity(1, db=db, source="miniapp")

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertIn("2 لفظ تاریخی", exc_info.exception.detail)
        self.assertIn("3 معامله", exc_info.exception.detail)

    async def test_delete_commodity_deletes_aliases_then_commodity_and_invalidates_cache(self):
        aliases = [SimpleNamespace(id=10), SimpleNamespace(id=11)]
        commodity = SimpleNamespace(id=1, name="بهار", aliases=aliases)
        db = FakeDB(
            [
                FakeExecuteResult(value=commodity),
                FakeExecuteResult(value=0),
                FakeExecuteResult(value=0),
                FakeExecuteResult(value=0),
            ]
        )
        with patch("bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()) as invalidate_mock:
            result = await delete_commodity(1, db=db, source="bot")

        self.assertIsNone(result)
        self.assertEqual(db.deleted, [aliases[0], aliases[1], commodity])
        self.assertEqual(db.commits, 1)
        invalidate_mock.assert_awaited_once()

    async def test_delete_commodity_ignores_cache_invalidation_failures(self):
        commodity = SimpleNamespace(id=1, name="بهار", aliases=[])
        db = FakeDB(
            [
                FakeExecuteResult(value=commodity),
                FakeExecuteResult(value=0),
                FakeExecuteResult(value=0),
                FakeExecuteResult(value=0),
            ]
        )
        with patch(
            "bot.utils.redis_helpers.invalidate_commodity_cache",
            new=AsyncMock(side_effect=RuntimeError("cache down")),
        ):
            result = await delete_commodity(1, db=db, source="bot")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()