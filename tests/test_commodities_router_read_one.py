import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from api.routers.commodities import read_commodity


class FakeScalars:
    def __init__(self, first_value):
        self._first_value = first_value

    def unique(self):
        return self

    def first(self):
        return self._first_value


class FakeExecuteResult:
    def __init__(self, first_value):
        self._first_value = first_value

    def scalars(self):
        return FakeScalars(self._first_value)


class FakeDB:
    def __init__(self, first_value):
        self.first_value = first_value

    async def execute(self, stmt):
        return FakeExecuteResult(self.first_value)


class CommoditiesRouterReadOneTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_commodity_returns_found_row(self):
        commodity = SimpleNamespace(id=1, name="Gold", aliases=[])
        result = await read_commodity(1, db=FakeDB(commodity))
        self.assertIs(result, commodity)

    async def test_read_commodity_raises_404_when_missing(self):
        with self.assertRaises(HTTPException) as exc_info:
            await read_commodity(1, db=FakeDB(None))
        self.assertEqual(exc_info.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()