"""Characterization tests for direct commodity alias update and delete handlers."""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import unittest
import types
import unittest.mock
import inspect
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
import schemas
from models.commodity import Commodity, CommodityAlias
from api.routers.commodities import update_alias, delete_alias

SQL_COMMODITY_BY_NAME = (
    "SELECT commodities.id, commodities.name "
    "FROM commodities "
    "WHERE commodities.name = %(name_1)s"
)
SQL_ALIAS_BY_TEXT = (
    "SELECT commodity_aliases.id, commodity_aliases.alias, commodity_aliases.commodity_id "
    "FROM commodity_aliases "
    "WHERE commodity_aliases.alias = %(alias_1)s"
)
SQL_ALIAS_BY_ID = (
    "SELECT commodity_aliases.id, commodity_aliases.alias, commodity_aliases.commodity_id "
    "FROM commodity_aliases "
    "WHERE commodity_aliases.id = %(id_1)s"
)

DIGIT_ERROR_DETAIL = "شما نمیتوانید در نام کالا از اعداد استفاده کنید"
MISSING_ALIAS_DETAIL = "نام مستعار یافت نشد"
COMMODITY_CONFLICT_DETAIL_GOLD = "نام «gold» از قبل به عنوان نام اصلی یک کالا ثبت شده است"
ALIAS_CONFLICT_DETAIL_GOLD = "نام «gold» از قبل به عنوان نام مستعار یک کالا ثبت شده است"


class ScalarOneOrNone:
    """Synchronous result wrapper exposing only scalar_one_or_none()."""

    def __init__(self, row: Commodity | CommodityAlias | None) -> None:
        self._row = row
        self.calls = 0

    def scalar_one_or_none(self) -> Commodity | CommodityAlias | None:
        self.calls += 1
        return self._row


class FakeDB:
    """Query-aware direct-handler DB harness dispatching only the three exact Select shapes."""

    def __init__(
        self,
        *,
        events: list[str],
        alias_by_id: dict[int, CommodityAlias | None] | None = None,
        commodity_by_name: dict[str, Commodity | None] | None = None,
        alias_by_text: dict[str, CommodityAlias | None] | None = None,
        commit_error: RuntimeError | None = None,
    ) -> None:
        self.events = events
        self._alias_by_id = dict(alias_by_id or {})
        self._commodity_by_name = dict(commodity_by_name or {})
        self._alias_by_text = dict(alias_by_text or {})
        self._commit_error = commit_error
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.commits = 0
        self.refreshes: list[object] = []
        self.rollbacks = 0
        self.executed: list[tuple[str, dict[str, object]]] = []
        self.results: list[ScalarOneOrNone] = []

    async def execute(self, stmt: object) -> ScalarOneOrNone:
        if getattr(stmt, "__visit_name__", None) != "select" or not callable(
            getattr(stmt, "compile", None)
        ):
            raise AssertionError(f"Expected real SQLAlchemy Select statement, got {stmt!r}")

        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = " ".join(str(compiled).split())
        params = dict(compiled.params)

        if sql == SQL_COMMODITY_BY_NAME:
            if set(params.keys()) != {"name_1"} or not isinstance(params["name_1"], str):
                raise AssertionError(f"Unexpected commodities.name params: {params!r}")
            requested_name = params["name_1"]
            if requested_name not in self._commodity_by_name:
                raise AssertionError(f"Unconfigured commodities.name lookup: {requested_name!r}")
            self.executed.append((sql, params))
            self.events.append("execute:commodities.name")
            result = ScalarOneOrNone(self._commodity_by_name[requested_name])
            self.results.append(result)
            return result

        if sql == SQL_ALIAS_BY_TEXT:
            if set(params.keys()) != {"alias_1"} or not isinstance(params["alias_1"], str):
                raise AssertionError(f"Unexpected commodity_aliases.alias params: {params!r}")
            requested_alias = params["alias_1"]
            if requested_alias not in self._alias_by_text:
                raise AssertionError(
                    f"Unconfigured commodity_aliases.alias lookup: {requested_alias!r}"
                )
            self.executed.append((sql, params))
            self.events.append("execute:commodity_aliases.alias")
            result = ScalarOneOrNone(self._alias_by_text[requested_alias])
            self.results.append(result)
            return result

        if sql == SQL_ALIAS_BY_ID:
            if (
                set(params.keys()) != {"id_1"}
                or isinstance(params["id_1"], bool)
                or not isinstance(params["id_1"], int)
            ):
                raise AssertionError(f"Unexpected commodity_aliases.id params: {params!r}")
            requested_id = params["id_1"]
            if requested_id not in self._alias_by_id:
                raise AssertionError(f"Unconfigured commodity_aliases.id lookup: {requested_id!r}")
            self.executed.append((sql, params))
            self.events.append("execute:commodity_aliases.id")
            result = ScalarOneOrNone(self._alias_by_id[requested_id])
            self.results.append(result)
            return result

        raise AssertionError(f"Unexpected SQL shape: {sql!r} with params {params!r}")

    def add(self, obj: object) -> None:
        self.added.append(obj)
        self.events.append("add")

    async def delete(self, obj: object) -> None:
        self.deleted.append(obj)
        self.events.append("delete")

    async def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")
        if self._commit_error is not None:
            error = self._commit_error
            self._commit_error = None
            raise error

    async def refresh(self, obj: object) -> None:
        self.refreshes.append(obj)
        self.events.append("refresh")

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.events.append("rollback")


class _ScenarioHarness:
    """Per-scenario patch and shared-event harness with strict restoration checks."""

    def __init__(
        self,
        case: unittest.TestCase,
        *,
        alias_by_id: dict[int, CommodityAlias | None] | None = None,
        commodity_by_name: dict[str, Commodity | None] | None = None,
        alias_by_text: dict[str, CommodityAlias | None] | None = None,
        commit_error: RuntimeError | None = None,
        cache_error: RuntimeError | None = None,
    ) -> None:
        self._case = case
        self.events: list[str] = []
        self.db = FakeDB(
            events=self.events,
            alias_by_id=alias_by_id,
            commodity_by_name=commodity_by_name,
            alias_by_text=alias_by_text,
            commit_error=commit_error,
        )
        self._cache_errors: list[RuntimeError] = (
            [cache_error] if cache_error is not None else []
        )
        self.info_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def _info_side_effect(*args: object, **kwargs: object) -> None:
            self.info_calls.append((args, kwargs))
            self.events.append("log")

        def _unexpected_log(*args: object, **kwargs: object) -> None:
            raise AssertionError(f"Unexpected non-info logger call: {args!r} {kwargs!r}")

        self.logger_recorder = types.SimpleNamespace(
            info=unittest.mock.MagicMock(side_effect=_info_side_effect),
            warning=unittest.mock.MagicMock(side_effect=_unexpected_log),
            error=unittest.mock.MagicMock(side_effect=_unexpected_log),
            debug=unittest.mock.MagicMock(side_effect=_unexpected_log),
            exception=unittest.mock.MagicMock(side_effect=_unexpected_log),
        )

        async def _invalidate_side_effect() -> None:
            self.events.append("invalidate")
            if self._cache_errors:
                raise self._cache_errors.pop(0)

        self.invalidate_mock = unittest.mock.AsyncMock(side_effect=_invalidate_side_effect)
        self._fence: dict[str, object] | None = None
        self._denied_before = 0
        self._live_redis_before = 0
        self._redis_helpers_mod: types.ModuleType | None = None
        self._original_logger: object = None
        self._original_invalidate: object = None
        self._logger_patcher: object = None
        self._invalidate_patcher: object = None

    def __enter__(self) -> _ScenarioHarness:
        from bot.utils import redis_helpers

        self._fence = install_p1_00_network_fence()
        self._case.assertTrue(self._fence["installed"])
        self._denied_before = len(self._fence["denied_connects"])  # type: ignore[arg-type]
        self._live_redis_before = len(self._fence["live_redis_constructs"])  # type: ignore[arg-type]

        self._redis_helpers_mod = redis_helpers
        self._original_logger = update_alias.__globals__["logger"]
        self._original_invalidate = redis_helpers.invalidate_commodity_cache
        self._case.assertTrue(inspect.iscoroutinefunction(self._original_invalidate))
        self._case.assertNotIsInstance(self._original_invalidate, unittest.mock.AsyncMock)

        self._logger_patcher = unittest.mock.patch(
            "api.routers.commodities.logger",
            new=self.logger_recorder,
        )
        self._invalidate_patcher = unittest.mock.patch(
            "bot.utils.redis_helpers.invalidate_commodity_cache",
            new=self.invalidate_mock,
        )
        self._logger_patcher.start()  # type: ignore[attr-defined]
        try:
            self._invalidate_patcher.start()  # type: ignore[attr-defined]
        except Exception:
            self._logger_patcher.stop()  # type: ignore[attr-defined]
            raise

        self._case.assertIsInstance(self.invalidate_mock, unittest.mock.AsyncMock)
        self._case.assertIs(update_alias.__globals__["logger"], self.logger_recorder)
        self._case.assertIs(redis_helpers.invalidate_commodity_cache, self.invalidate_mock)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        try:
            if self._invalidate_patcher is not None:
                self._invalidate_patcher.stop()  # type: ignore[attr-defined]
        finally:
            if self._logger_patcher is not None:
                self._logger_patcher.stop()  # type: ignore[attr-defined]

        if exc_type is None:
            assert self._redis_helpers_mod is not None
            assert self._fence is not None
            self._case.assertIs(update_alias.__globals__["logger"], self._original_logger)
            self._case.assertIs(
                self._redis_helpers_mod.invalidate_commodity_cache,
                self._original_invalidate,
            )
            self._case.assertEqual(
                len(self._fence["denied_connects"]) - self._denied_before,  # type: ignore[arg-type]
                0,
            )
            self._case.assertEqual(
                len(self._fence["live_redis_constructs"]) - self._live_redis_before,  # type: ignore[arg-type]
                0,
            )
            for result in self.db.results:
                self._case.assertIsInstance(result, ScalarOneOrNone)
                self._case.assertNotIsInstance(result, unittest.mock.AsyncMock)
                self._case.assertNotIsInstance(
                    result.scalar_one_or_none, unittest.mock.AsyncMock
                )
                self._case.assertFalse(
                    inspect.iscoroutinefunction(result.scalar_one_or_none)
                )
                self._case.assertEqual(result.calls, 1)
        return False


class CommodityAliasUpdateDeleteCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _assert_real_handlers_and_helpers_unpatched(self) -> None:
        self.assertTrue(inspect.iscoroutinefunction(update_alias))
        self.assertTrue(inspect.iscoroutinefunction(delete_alias))
        self.assertNotIsInstance(update_alias, unittest.mock.Mock)
        self.assertNotIsInstance(delete_alias, unittest.mock.Mock)
        self.assertIs(update_alias.__globals__["Commodity"], Commodity)
        self.assertIs(update_alias.__globals__["CommodityAlias"], CommodityAlias)
        self.assertIs(delete_alias.__globals__["CommodityAlias"], CommodityAlias)
        digits_helper = update_alias.__globals__["ensure_commodity_text_has_no_digits"]
        availability_helper = update_alias.__globals__["ensure_commodity_text_is_available"]
        self.assertFalse(inspect.iscoroutinefunction(digits_helper))
        self.assertTrue(inspect.iscoroutinefunction(availability_helper))
        self.assertNotIsInstance(digits_helper, unittest.mock.Mock)
        self.assertNotIsInstance(availability_helper, unittest.mock.Mock)

    def _assert_alias_columns(
        self,
        row: CommodityAlias,
        *,
        alias_id: int,
        commodity_id: int,
        alias_text: str,
    ) -> None:
        self.assertIsInstance(row, CommodityAlias)
        self.assertEqual(row.id, alias_id)
        self.assertEqual(row.commodity_id, commodity_id)
        self.assertEqual(row.alias, alias_text)

    def _assert_commodity_columns(
        self,
        row: Commodity,
        *,
        commodity_id: int,
        name: str,
    ) -> None:
        self.assertIsInstance(row, Commodity)
        self.assertEqual(row.id, commodity_id)
        self.assertEqual(row.name, name)

    def _assert_logger_called_once(
        self,
        harness: _ScenarioHarness,
        expected_message: str,
    ) -> None:
        self.assertEqual(harness.logger_recorder.info.call_count, 1)
        self.assertEqual(harness.info_calls, [((expected_message,), {})])

    def _assert_invalidate_not_called(self, harness: _ScenarioHarness) -> None:
        self.assertEqual(harness.invalidate_mock.call_count, 0)
        self.assertEqual(harness.invalidate_mock.await_count, 0)
        self.assertEqual(harness.invalidate_mock.await_args_list, [])

    def _assert_invalidate_awaited_once(self, harness: _ScenarioHarness) -> None:
        self.assertEqual(harness.invalidate_mock.call_count, 1)
        self.assertEqual(harness.invalidate_mock.await_count, 1)
        self.assertEqual(harness.invalidate_mock.await_args_list, [unittest.mock.call()])

    async def test_update_alias_validates_target_and_persists_once(self) -> None:
        from bot.utils import redis_helpers

        self._assert_real_handlers_and_helpers_unpatched()
        fence = install_p1_00_network_fence()
        denied_start = len(fence["denied_connects"])
        live_redis_start = len(fence["live_redis_constructs"])
        original_logger = update_alias.__globals__["logger"]
        original_invalidate = redis_helpers.invalidate_commodity_cache

        # 1. digits rejection before any DB execute
        loaded = CommodityAlias(id=3, commodity_id=1, alias="old")
        with _ScenarioHarness(self, alias_by_id={3: loaded}) as harness:
            with self.assertRaises(HTTPException) as exc_info:
                await update_alias(
                    alias_id=3,
                    alias_update=schemas.CommodityAliasCreate(alias="تمام1404"),
                    db=harness.db,
                    source="bot",
                )
            self.assertEqual(exc_info.exception.status_code, 400)
            self.assertEqual(exc_info.exception.detail, DIGIT_ERROR_DETAIL)
            self._assert_logger_called_once(
                harness, "Updating alias ID 3 to 'تمام1404' via source: bot"
            )
            self._assert_alias_columns(loaded, alias_id=3, commodity_id=1, alias_text="old")
            self.assertEqual(harness.db.executed, [])
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 0)
            self.assertEqual(harness.db.refreshes, [])
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_not_called(harness)
            self.assertEqual(harness.events, ["log"])

        # 2. missing target alias
        with _ScenarioHarness(self, alias_by_id={3: None}) as harness:
            with self.assertRaises(HTTPException) as exc_info:
                await update_alias(
                    alias_id=3,
                    alias_update=schemas.CommodityAliasCreate(alias="gold"),
                    db=harness.db,
                    source="bot",
                )
            self.assertEqual(exc_info.exception.status_code, 404)
            self.assertEqual(exc_info.exception.detail, MISSING_ALIAS_DETAIL)
            self._assert_logger_called_once(
                harness, "Updating alias ID 3 to 'gold' via source: bot"
            )
            self.assertEqual(harness.db.executed, [(SQL_ALIAS_BY_ID, {"id_1": 3})])
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 0)
            self.assertEqual(harness.db.refreshes, [])
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_not_called(harness)
            self.assertEqual(harness.events, ["log", "execute:commodity_aliases.id"])

        # 3. unchanged text skips availability queries and persists once
        loaded = CommodityAlias(id=3, commodity_id=1, alias="old")
        with _ScenarioHarness(self, alias_by_id={3: loaded}) as harness:
            result = await update_alias(
                alias_id=3,
                alias_update=schemas.CommodityAliasCreate(alias="old"),
                db=harness.db,
                source="bot",
            )
            self.assertIs(result, loaded)
            self._assert_alias_columns(loaded, alias_id=3, commodity_id=1, alias_text="old")
            self._assert_logger_called_once(
                harness, "Updating alias ID 3 to 'old' via source: bot"
            )
            self.assertEqual(harness.db.executed, [(SQL_ALIAS_BY_ID, {"id_1": 3})])
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 1)
            self.assertEqual(harness.db.refreshes, [loaded])
            self.assertIs(harness.db.refreshes[0], loaded)
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_awaited_once(harness)
            self.assertEqual(
                harness.events,
                ["log", "execute:commodity_aliases.id", "commit", "refresh", "invalidate"],
            )

        # 4. changed text checks availability, updates alias, commits, refreshes, invalidates
        loaded = CommodityAlias(id=3, commodity_id=1, alias="old")
        with _ScenarioHarness(
            self,
            alias_by_id={3: loaded},
            commodity_by_name={"gold": None},
            alias_by_text={"gold": None},
        ) as harness:
            result = await update_alias(
                alias_id=3,
                alias_update=schemas.CommodityAliasCreate(alias="gold"),
                db=harness.db,
                source="bot",
            )
            self.assertIs(result, loaded)
            self._assert_alias_columns(loaded, alias_id=3, commodity_id=1, alias_text="gold")
            self._assert_logger_called_once(
                harness, "Updating alias ID 3 to 'gold' via source: bot"
            )
            self.assertEqual(
                harness.db.executed,
                [
                    (SQL_ALIAS_BY_ID, {"id_1": 3}),
                    (SQL_COMMODITY_BY_NAME, {"name_1": "gold"}),
                    (SQL_ALIAS_BY_TEXT, {"alias_1": "gold"}),
                ],
            )
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 1)
            self.assertEqual(harness.db.refreshes, [loaded])
            self.assertIs(harness.db.refreshes[0], loaded)
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_awaited_once(harness)
            self.assertEqual(
                harness.events,
                [
                    "log",
                    "execute:commodity_aliases.id",
                    "execute:commodities.name",
                    "execute:commodity_aliases.alias",
                    "commit",
                    "refresh",
                    "invalidate",
                ],
            )

        # 5. commodity-name conflict on another commodity
        loaded = CommodityAlias(id=3, commodity_id=1, alias="old")
        conflicting_commodity = Commodity(id=7, name="gold")
        with _ScenarioHarness(
            self,
            alias_by_id={3: loaded},
            commodity_by_name={"gold": conflicting_commodity},
        ) as harness:
            with self.assertRaises(HTTPException) as exc_info:
                await update_alias(
                    alias_id=3,
                    alias_update=schemas.CommodityAliasCreate(alias="gold"),
                    db=harness.db,
                    source="bot",
                )
            self.assertEqual(exc_info.exception.status_code, 409)
            self.assertEqual(exc_info.exception.detail, COMMODITY_CONFLICT_DETAIL_GOLD)
            self._assert_alias_columns(loaded, alias_id=3, commodity_id=1, alias_text="old")
            self._assert_commodity_columns(
                conflicting_commodity, commodity_id=7, name="gold"
            )
            self._assert_logger_called_once(
                harness, "Updating alias ID 3 to 'gold' via source: bot"
            )
            self.assertEqual(
                harness.db.executed,
                [
                    (SQL_ALIAS_BY_ID, {"id_1": 3}),
                    (SQL_COMMODITY_BY_NAME, {"name_1": "gold"}),
                ],
            )
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 0)
            self.assertEqual(harness.db.refreshes, [])
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_not_called(harness)
            self.assertEqual(
                harness.events,
                ["log", "execute:commodity_aliases.id", "execute:commodities.name"],
            )

        # 6. alias-text conflict on another commodity
        loaded = CommodityAlias(id=3, commodity_id=1, alias="old")
        conflicting_alias = CommodityAlias(id=9, commodity_id=2, alias="gold")
        with _ScenarioHarness(
            self,
            alias_by_id={3: loaded},
            commodity_by_name={"gold": None},
            alias_by_text={"gold": conflicting_alias},
        ) as harness:
            with self.assertRaises(HTTPException) as exc_info:
                await update_alias(
                    alias_id=3,
                    alias_update=schemas.CommodityAliasCreate(alias="gold"),
                    db=harness.db,
                    source="bot",
                )
            self.assertEqual(exc_info.exception.status_code, 409)
            self.assertEqual(exc_info.exception.detail, ALIAS_CONFLICT_DETAIL_GOLD)
            self._assert_alias_columns(loaded, alias_id=3, commodity_id=1, alias_text="old")
            self._assert_alias_columns(
                conflicting_alias, alias_id=9, commodity_id=2, alias_text="gold"
            )
            self._assert_logger_called_once(
                harness, "Updating alias ID 3 to 'gold' via source: bot"
            )
            self.assertEqual(
                harness.db.executed,
                [
                    (SQL_ALIAS_BY_ID, {"id_1": 3}),
                    (SQL_COMMODITY_BY_NAME, {"name_1": "gold"}),
                    (SQL_ALIAS_BY_TEXT, {"alias_1": "gold"}),
                ],
            )
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 0)
            self.assertEqual(harness.db.refreshes, [])
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_not_called(harness)
            self.assertEqual(
                harness.events,
                [
                    "log",
                    "execute:commodity_aliases.id",
                    "execute:commodities.name",
                    "execute:commodity_aliases.alias",
                ],
            )

        # 7. same-commodity alias allowance observed in handler
        loaded = CommodityAlias(id=3, commodity_id=1, alias="old")
        sibling_alias = CommodityAlias(id=9, commodity_id=1, alias="gold")
        with _ScenarioHarness(
            self,
            alias_by_id={3: loaded},
            commodity_by_name={"gold": None},
            alias_by_text={"gold": sibling_alias},
        ) as harness:
            result = await update_alias(
                alias_id=3,
                alias_update=schemas.CommodityAliasCreate(alias="gold"),
                db=harness.db,
                source="bot",
            )
            self.assertIs(result, loaded)
            self._assert_alias_columns(loaded, alias_id=3, commodity_id=1, alias_text="gold")
            self._assert_alias_columns(
                sibling_alias, alias_id=9, commodity_id=1, alias_text="gold"
            )
            self._assert_logger_called_once(
                harness, "Updating alias ID 3 to 'gold' via source: bot"
            )
            self.assertEqual(
                harness.db.executed,
                [
                    (SQL_ALIAS_BY_ID, {"id_1": 3}),
                    (SQL_COMMODITY_BY_NAME, {"name_1": "gold"}),
                    (SQL_ALIAS_BY_TEXT, {"alias_1": "gold"}),
                ],
            )
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 1)
            self.assertEqual(harness.db.refreshes, [loaded])
            self.assertIs(harness.db.refreshes[0], loaded)
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_awaited_once(harness)
            self.assertEqual(
                harness.events,
                [
                    "log",
                    "execute:commodity_aliases.id",
                    "execute:commodities.name",
                    "execute:commodity_aliases.alias",
                    "commit",
                    "refresh",
                    "invalidate",
                ],
            )

        # 8. commit failure propagates sentinel unchanged without refresh/invalidate/rollback
        loaded = CommodityAlias(id=3, commodity_id=1, alias="old")
        commit_sentinel = RuntimeError("alias-update-commit-failure")
        with _ScenarioHarness(
            self,
            alias_by_id={3: loaded},
            commodity_by_name={"gold": None},
            alias_by_text={"gold": None},
            commit_error=commit_sentinel,
        ) as harness:
            with self.assertRaises(RuntimeError) as exc_info:
                await update_alias(
                    alias_id=3,
                    alias_update=schemas.CommodityAliasCreate(alias="gold"),
                    db=harness.db,
                    source="bot",
                )
            self.assertIs(exc_info.exception, commit_sentinel)
            self.assertNotIsInstance(exc_info.exception, HTTPException)
            self._assert_alias_columns(loaded, alias_id=3, commodity_id=1, alias_text="gold")
            self._assert_logger_called_once(
                harness, "Updating alias ID 3 to 'gold' via source: bot"
            )
            self.assertEqual(
                harness.db.executed,
                [
                    (SQL_ALIAS_BY_ID, {"id_1": 3}),
                    (SQL_COMMODITY_BY_NAME, {"name_1": "gold"}),
                    (SQL_ALIAS_BY_TEXT, {"alias_1": "gold"}),
                ],
            )
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 1)
            self.assertEqual(harness.db.refreshes, [])
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_not_called(harness)
            self.assertEqual(
                harness.events,
                [
                    "log",
                    "execute:commodity_aliases.id",
                    "execute:commodities.name",
                    "execute:commodity_aliases.alias",
                    "commit",
                ],
            )

        # 9. cache invalidation failure is swallowed after commit and refresh
        loaded = CommodityAlias(id=3, commodity_id=1, alias="old")
        cache_sentinel = RuntimeError("alias-update-cache-down")
        with _ScenarioHarness(
            self,
            alias_by_id={3: loaded},
            commodity_by_name={"gold": None},
            alias_by_text={"gold": None},
            cache_error=cache_sentinel,
        ) as harness:
            result = await update_alias(
                alias_id=3,
                alias_update=schemas.CommodityAliasCreate(alias="gold"),
                db=harness.db,
                source="bot",
            )
            self.assertIs(result, loaded)
            self._assert_alias_columns(loaded, alias_id=3, commodity_id=1, alias_text="gold")
            self._assert_logger_called_once(
                harness, "Updating alias ID 3 to 'gold' via source: bot"
            )
            self.assertEqual(
                harness.db.executed,
                [
                    (SQL_ALIAS_BY_ID, {"id_1": 3}),
                    (SQL_COMMODITY_BY_NAME, {"name_1": "gold"}),
                    (SQL_ALIAS_BY_TEXT, {"alias_1": "gold"}),
                ],
            )
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 1)
            self.assertEqual(harness.db.refreshes, [loaded])
            self.assertIs(harness.db.refreshes[0], loaded)
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_awaited_once(harness)
            self.assertEqual(
                harness.events,
                [
                    "log",
                    "execute:commodity_aliases.id",
                    "execute:commodities.name",
                    "execute:commodity_aliases.alias",
                    "commit",
                    "refresh",
                    "invalidate",
                ],
            )

        self.assertIs(update_alias.__globals__["logger"], original_logger)
        self.assertIs(redis_helpers.invalidate_commodity_cache, original_invalidate)
        self.assertEqual(len(fence["denied_connects"]) - denied_start, 0)
        self.assertEqual(len(fence["live_redis_constructs"]) - live_redis_start, 0)

    async def test_delete_alias_removes_once_and_invalidates_cache(self) -> None:
        from bot.utils import redis_helpers

        self._assert_real_handlers_and_helpers_unpatched()
        fence = install_p1_00_network_fence()
        denied_start = len(fence["denied_connects"])
        live_redis_start = len(fence["live_redis_constructs"])
        original_logger = delete_alias.__globals__["logger"]
        original_invalidate = redis_helpers.invalidate_commodity_cache

        # 1. missing target alias
        with _ScenarioHarness(self, alias_by_id={2: None}) as harness:
            with self.assertRaises(HTTPException) as exc_info:
                await delete_alias(alias_id=2, db=harness.db, source="bot")
            self.assertEqual(exc_info.exception.status_code, 404)
            self.assertEqual(exc_info.exception.detail, MISSING_ALIAS_DETAIL)
            self._assert_logger_called_once(
                harness, "Deleting alias ID 2 via source: bot"
            )
            self.assertEqual(harness.db.executed, [(SQL_ALIAS_BY_ID, {"id_1": 2})])
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [])
            self.assertEqual(harness.db.commits, 0)
            self.assertEqual(harness.db.refreshes, [])
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_not_called(harness)
            self.assertEqual(harness.events, ["log", "execute:commodity_aliases.id"])

        # 2. success deletes loaded alias once, commits once, and invalidates cache
        loaded = CommodityAlias(id=2, commodity_id=1, alias="gold")
        with _ScenarioHarness(self, alias_by_id={2: loaded}) as harness:
            result = await delete_alias(alias_id=2, db=harness.db, source="bot")
            self.assertIsNone(result)
            self._assert_alias_columns(loaded, alias_id=2, commodity_id=1, alias_text="gold")
            self._assert_logger_called_once(
                harness, "Deleting alias ID 2 via source: bot"
            )
            self.assertEqual(harness.db.executed, [(SQL_ALIAS_BY_ID, {"id_1": 2})])
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [loaded])
            self.assertIs(harness.db.deleted[0], loaded)
            self.assertEqual(harness.db.commits, 1)
            self.assertEqual(harness.db.refreshes, [])
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_awaited_once(harness)
            self.assertEqual(
                harness.events,
                ["log", "execute:commodity_aliases.id", "delete", "commit", "invalidate"],
            )

        # 3. cache invalidation failure is swallowed after delete and commit
        loaded = CommodityAlias(id=2, commodity_id=1, alias="gold")
        cache_sentinel = RuntimeError("alias-delete-cache-down")
        with _ScenarioHarness(
            self,
            alias_by_id={2: loaded},
            cache_error=cache_sentinel,
        ) as harness:
            result = await delete_alias(alias_id=2, db=harness.db, source="bot")
            self.assertIsNone(result)
            self._assert_alias_columns(loaded, alias_id=2, commodity_id=1, alias_text="gold")
            self._assert_logger_called_once(
                harness, "Deleting alias ID 2 via source: bot"
            )
            self.assertEqual(harness.db.executed, [(SQL_ALIAS_BY_ID, {"id_1": 2})])
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [loaded])
            self.assertIs(harness.db.deleted[0], loaded)
            self.assertEqual(harness.db.commits, 1)
            self.assertEqual(harness.db.refreshes, [])
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_awaited_once(harness)
            self.assertEqual(
                harness.events,
                ["log", "execute:commodity_aliases.id", "delete", "commit", "invalidate"],
            )

        # 4. commit failure propagates sentinel unchanged after delete without invalidate/rollback
        loaded = CommodityAlias(id=2, commodity_id=1, alias="gold")
        commit_sentinel = RuntimeError("alias-delete-commit-failure")
        with _ScenarioHarness(
            self,
            alias_by_id={2: loaded},
            commit_error=commit_sentinel,
        ) as harness:
            with self.assertRaises(RuntimeError) as exc_info:
                await delete_alias(alias_id=2, db=harness.db, source="bot")
            self.assertIs(exc_info.exception, commit_sentinel)
            self.assertNotIsInstance(exc_info.exception, HTTPException)
            self._assert_alias_columns(loaded, alias_id=2, commodity_id=1, alias_text="gold")
            self._assert_logger_called_once(
                harness, "Deleting alias ID 2 via source: bot"
            )
            self.assertEqual(harness.db.executed, [(SQL_ALIAS_BY_ID, {"id_1": 2})])
            self.assertEqual(harness.db.added, [])
            self.assertEqual(harness.db.deleted, [loaded])
            self.assertIs(harness.db.deleted[0], loaded)
            self.assertEqual(harness.db.commits, 1)
            self.assertEqual(harness.db.refreshes, [])
            self.assertEqual(harness.db.rollbacks, 0)
            self._assert_invalidate_not_called(harness)
            self.assertEqual(
                harness.events,
                ["log", "execute:commodity_aliases.id", "delete", "commit"],
            )

        self.assertIs(delete_alias.__globals__["logger"], original_logger)
        self.assertIs(redis_helpers.invalidate_commodity_cache, original_invalidate)
        self.assertEqual(len(fence["denied_connects"]) - denied_start, 0)
        self.assertEqual(len(fence["live_redis_constructs"]) - live_redis_start, 0)


if __name__ == "__main__":
    unittest.main()
