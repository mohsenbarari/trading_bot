import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.commodities import read_all_commodities


class FakeScalars:
    def __init__(self, values):
        self._values = list(values)

    def unique(self):
        return self

    def all(self):
        return list(self._values)


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return FakeScalars(self._values)


class FakeDB:
    def __init__(self, result=None):
        self.result = result
        self.execute_calls = 0

    async def execute(self, stmt):
        self.execute_calls += 1
        return self.result


def make_commodity(cid, name, aliases):
    return SimpleNamespace(id=cid, name=name, aliases=[SimpleNamespace(id=a[0], alias=a[1], commodity_id=cid) for a in aliases])


class CommoditiesRouterReadAllTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_all_commodities_returns_cache_hit_without_db_query(self):
        cached = [{"id": 1, "name": "Gold", "aliases": []}]
        db = FakeDB()

        with patch("core.cache.get_cached_commodities", new=AsyncMock(return_value=cached)), patch(
            "core.cache.set_cached_commodities", new=AsyncMock()
        ) as set_cache:
            result = await read_all_commodities(db=db)

        self.assertEqual(result, cached)
        self.assertEqual(db.execute_calls, 0)
        set_cache.assert_not_awaited()

    async def test_read_all_commodities_loads_from_db_and_caches_serialized_payload(self):
        commodities = [make_commodity(1, "Gold", [(10, "طلای آبشده")])]
        db = FakeDB(FakeExecuteResult(commodities))

        with patch("core.cache.get_cached_commodities", new=AsyncMock(return_value=None)), patch(
            "core.cache.set_cached_commodities", new=AsyncMock()
        ) as set_cache:
            result = await read_all_commodities(db=db)

        self.assertEqual(result, commodities)
        set_cache.assert_awaited_once_with(
            [{"id": 1, "name": "Gold", "aliases": [{"id": 10, "alias": "طلای آبشده", "commodity_id": 1}]}]
        )


if __name__ == "__main__":
    unittest.main()