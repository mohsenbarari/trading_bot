import unittest

from models.commodity import Commodity, CommodityAlias
from scripts.restore_default_commodities import ensure_default_commodities


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


class FakeSession:
    def __init__(self, execute_results=None, assigned_id=1):
        self.execute_results = list(execute_results or [])
        self.assigned_id = assigned_id
        self.added = []
        self.flushes = 0
        self.commits = 0

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1
        for obj in self.added:
            if isinstance(obj, Commodity) and obj.id is None:
                obj.id = self.assigned_id

    async def commit(self):
        self.commits += 1


class RestoreDefaultCommoditiesScriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_default_commodities_creates_imam_and_aliases(self):
        session = FakeSession(
            execute_results=[
                FakeExecuteResult(value=None),
                FakeExecuteResult(values=[]),
            ],
            assigned_id=11,
        )

        stats = await ensure_default_commodities(session)

        self.assertTrue(stats["commodity_created"])
        self.assertEqual(stats["commodity_id"], 11)
        self.assertEqual(
            stats["aliases_added"],
            ["امامی", "سکه امام", "سکه امامی", "سکه جدید", "سکه بانکی"],
        )
        self.assertEqual(stats["aliases_existing"], [])
        self.assertEqual(stats["aliases_conflicted"], [])
        self.assertEqual(session.flushes, 1)
        self.assertEqual(session.commits, 1)

        created_commodity = next(obj for obj in session.added if isinstance(obj, Commodity))
        self.assertEqual(created_commodity.name, "امام")
        created_aliases = [obj for obj in session.added if isinstance(obj, CommodityAlias)]
        self.assertEqual(len(created_aliases), 5)
        self.assertTrue(all(alias.commodity_id == 11 for alias in created_aliases))

    async def test_ensure_default_commodities_is_idempotent_and_reports_conflicts(self):
        commodity = Commodity(name="امام")
        commodity.id = 7
        existing_alias = CommodityAlias(alias="امامی", commodity_id=7)
        conflicting_alias = CommodityAlias(alias="سکه امام", commodity_id=99)
        session = FakeSession(
            execute_results=[
                FakeExecuteResult(value=commodity),
                FakeExecuteResult(values=[existing_alias, conflicting_alias]),
            ]
        )

        stats = await ensure_default_commodities(session)

        self.assertFalse(stats["commodity_created"])
        self.assertEqual(stats["commodity_id"], 7)
        self.assertEqual(stats["aliases_existing"], ["امامی"])
        self.assertEqual(stats["aliases_conflicted"], ["سکه امام"])
        self.assertEqual(stats["aliases_added"], ["سکه امامی", "سکه جدید", "سکه بانکی"])
        self.assertEqual(session.flushes, 0)
        self.assertEqual(session.commits, 1)


if __name__ == "__main__":
    unittest.main()