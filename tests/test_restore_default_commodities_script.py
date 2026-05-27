import unittest
from unittest.mock import AsyncMock, patch

from models.commodity import Commodity, CommodityAlias
from scripts import restore_default_commodities as restore_default_commodities_script
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


class _AsyncSessionFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


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

    async def test_main_reports_success_and_invalidates_cache(self):
        session = FakeSession()
        stats = {
            "commodity_id": 11,
            "commodity_created": True,
            "aliases_added": ["امامی"],
            "aliases_existing": ["سکه امام"],
            "aliases_conflicted": ["سکه بانکی"],
        }

        with patch.object(
            restore_default_commodities_script,
            "AsyncSessionLocal",
            _AsyncSessionFactory(session),
        ), patch.object(
            restore_default_commodities_script,
            "ensure_default_commodities",
            AsyncMock(return_value=stats),
        ) as ensure_mock, patch.object(
            restore_default_commodities_script,
            "invalidate_commodity_cache",
            AsyncMock(),
        ) as invalidate_mock, patch("builtins.print") as print_mock:
            exit_code = await restore_default_commodities_script.main()

        self.assertEqual(exit_code, 0)
        ensure_mock.assert_awaited_once_with(session)
        invalidate_mock.assert_awaited_once_with()
        printed_lines = [args[0] for args, _kwargs in print_mock.call_args_list]
        self.assertIn("✅ کالای پیش فرض امام آماده است (ID: 11)", printed_lines)
        self.assertIn("   - commodity: created", printed_lines)
        self.assertIn("   - aliases added: امامی", printed_lines)
        self.assertIn("   - aliases already present: سکه امام", printed_lines)
        self.assertIn(
            "   - aliases skipped (already attached to another commodity): سکه بانکی",
            printed_lines,
        )
        self.assertIn("   - commodity cache invalidated", printed_lines)

    async def test_main_returns_one_when_restore_raises(self):
        session = FakeSession()

        with patch.object(
            restore_default_commodities_script,
            "AsyncSessionLocal",
            _AsyncSessionFactory(session),
        ), patch.object(
            restore_default_commodities_script,
            "ensure_default_commodities",
            AsyncMock(side_effect=RuntimeError("db down")),
        ), patch("builtins.print") as print_mock:
            exit_code = await restore_default_commodities_script.main()

        self.assertEqual(exit_code, 1)
        print_mock.assert_called_once_with("❌ خطا در بازسازی کالاهای پیش فرض: db down")

    async def test_main_warns_when_cache_invalidation_fails(self):
        session = FakeSession()
        stats = {
            "commodity_id": 7,
            "commodity_created": False,
            "aliases_added": [],
            "aliases_existing": [],
            "aliases_conflicted": [],
        }

        with patch.object(
            restore_default_commodities_script,
            "AsyncSessionLocal",
            _AsyncSessionFactory(session),
        ), patch.object(
            restore_default_commodities_script,
            "ensure_default_commodities",
            AsyncMock(return_value=stats),
        ), patch.object(
            restore_default_commodities_script,
            "invalidate_commodity_cache",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ), patch("builtins.print") as print_mock:
            exit_code = await restore_default_commodities_script.main()

        self.assertEqual(exit_code, 0)
        printed_lines = [args[0] for args, _kwargs in print_mock.call_args_list]
        self.assertIn("   - commodity: already present", printed_lines)
        self.assertIn("⚠️ پاکسازی cache کالاها ناموفق بود: redis down", printed_lines)


if __name__ == "__main__":
    unittest.main()