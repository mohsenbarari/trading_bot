"""Characterization tests for direct commodity read handlers and cache/detail boundaries."""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import contextlib
import inspect
from typing import Any, Iterator
import unittest
import unittest.mock

from fastapi import HTTPException
import sqlalchemy.dialects.postgresql

from models.commodity import Commodity, CommodityAlias
from api.routers.commodities import read_all_commodities, read_commodity
import api.routers.commodities as commodities_router
import core.cache

_READ_ALL_SQL = (
    "SELECT commodities.id, commodities.name FROM commodities ORDER BY commodities.id"
)
_READ_ONE_SQL = (
    "SELECT commodities.id, commodities.name FROM commodities "
    "WHERE commodities.id = %(id_1)s"
)
_PG_DIALECT = sqlalchemy.dialects.postgresql.dialect()


def _make_commodity(
    commodity_id: int,
    name: str,
    alias_specs: list[tuple[int, str]],
) -> Commodity:
    commodity = Commodity(id=commodity_id, name=name)
    commodity.aliases = [
        CommodityAlias(id=alias_id, alias=alias_name, commodity_id=commodity_id)
        for alias_id, alias_name in alias_specs
    ]
    return commodity


class _SyncRowResult:
    """Synchronous row-result double with synchronous scalars/unique/all/first methods."""

    def __init__(
        self,
        *,
        route: str,
        all_rows: list[Commodity] | None = None,
        first_row: Commodity | None = None,
    ) -> None:
        if route not in ("all", "id"):
            raise ValueError(f"Unsupported route: {route!r}")
        self._route = route
        self._all_rows = list(all_rows) if all_rows is not None else []
        self._first_row = first_row
        self.call_chain: list[str] = []

    def scalars(self) -> _SyncRowResult:
        if self.call_chain:
            raise AssertionError(f"Unexpected scalars() after {self.call_chain!r}")
        self.call_chain.append("scalars")
        return self

    def unique(self) -> _SyncRowResult:
        if self.call_chain != ["scalars"]:
            raise AssertionError(
                f"Expected unique() immediately after scalars(), got {self.call_chain!r}"
            )
        self.call_chain.append("unique")
        return self

    def all(self) -> list[Commodity]:
        if self._route != "all":
            raise AssertionError(f"all() not permitted on route={self._route!r}")
        if self.call_chain != ["scalars", "unique"]:
            raise AssertionError(
                f"Expected all() after scalars().unique(), got {self.call_chain!r}"
            )
        self.call_chain.append("all")
        return list(self._all_rows)

    def first(self) -> Commodity | None:
        if self._route != "id":
            raise AssertionError(f"first() not permitted on route={self._route!r}")
        if self.call_chain != ["scalars", "unique"]:
            raise AssertionError(
                f"Expected first() after scalars().unique(), got {self.call_chain!r}"
            )
        self.call_chain.append("first")
        return self._first_row


class _QueryAwareFakeDB:
    """Single-scenario query-aware FakeDB dispatching only the two exact Select shapes."""

    def __init__(
        self,
        *,
        events: list[str],
        route: str,
        all_rows: list[Commodity] | None = None,
        expected_id: int | None = None,
        first_row: Commodity | None = None,
        db_error: RuntimeError | None = None,
    ) -> None:
        if route not in ("none", "all", "id"):
            raise ValueError(f"Unsupported route: {route!r}")
        if route == "id" and type(expected_id) is not int:
            raise ValueError("expected_id int is required when route='id'")
        self._events = events
        self._route = route
        self._all_rows = list(all_rows) if all_rows is not None else []
        self._expected_id = expected_id
        self._first_row = first_row
        self._db_error = db_error
        self._dispatched = False
        self.executed_statements: list[Any] = []
        self.executed_queries: list[tuple[str, dict[str, Any]]] = []
        self.last_result: _SyncRowResult | None = None

    async def execute(self, stmt: Any) -> _SyncRowResult:
        if (
            isinstance(stmt, unittest.mock.Mock)
            or getattr(stmt, "__visit_name__", None) != "select"
        ):
            raise AssertionError(f"Expected real SQLAlchemy Select statement, got {stmt!r}")
        if self._dispatched:
            raise AssertionError(
                "Single-scenario FakeDB received more than one execute() call"
            )
        self._dispatched = True

        compiled = stmt.compile(dialect=_PG_DIALECT)
        normalized_sql = " ".join(str(compiled).split())
        params = dict(compiled.params)
        self.executed_statements.append(stmt)
        self.executed_queries.append((normalized_sql, params))

        if normalized_sql == _READ_ALL_SQL and params == {}:
            if self._route != "all":
                raise AssertionError(
                    f"Unexpected read-all SQL on FakeDB configured for route={self._route!r}"
                )
            self._events.append("execute:commodities.all")
            if self._db_error is not None:
                err, self._db_error = self._db_error, None
                raise err
            row_result = _SyncRowResult(route="all", all_rows=self._all_rows)
            self.last_result = row_result
            return row_result

        if (
            normalized_sql == _READ_ONE_SQL
            and set(params.keys()) == {"id_1"}
            and type(params["id_1"]) is int
        ):
            if self._route != "id":
                raise AssertionError(
                    f"Unexpected read-one SQL on FakeDB configured for route={self._route!r}"
                )
            if params != {"id_1": self._expected_id}:
                raise AssertionError(
                    f"Expected params {{'id_1': {self._expected_id!r}}}, got {params!r}"
                )
            self._events.append("execute:commodities.id")
            if self._db_error is not None:
                err, self._db_error = self._db_error, None
                raise err
            row_result = _SyncRowResult(route="id", first_row=self._first_row)
            self.last_result = row_result
            return row_result

        raise AssertionError(
            f"Unexpected SQL shape or params: sql={normalized_sql!r}, params={params!r}"
        )


class _ScenarioBundle:
    def __init__(
        self,
        *,
        events: list[str],
        db: _QueryAwareFakeDB,
        get_mock: unittest.mock.AsyncMock,
        set_mock: unittest.mock.AsyncMock,
    ) -> None:
        self.events = events
        self.db = db
        self.get_mock = get_mock
        self.set_mock = set_mock


class CommodityReadCacheDetailCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _assert_sync_row_result(self, row_result: _SyncRowResult | None) -> None:
        self.assertIsNotNone(row_result)
        assert row_result is not None
        self.assertNotIsInstance(row_result, unittest.mock.Mock)
        self.assertNotIsInstance(row_result, unittest.mock.AsyncMock)
        for method in (
            row_result.scalars,
            row_result.unique,
            row_result.all,
            row_result.first,
        ):
            self.assertFalse(isinstance(method, unittest.mock.AsyncMock))
            self.assertFalse(inspect.iscoroutinefunction(method))

    @contextlib.contextmanager
    def _scenario(
        self,
        *,
        route: str,
        cache_get_return: Any = None,
        cache_get_error: RuntimeError | None = None,
        cache_set_error: RuntimeError | None = None,
        all_rows: list[Commodity] | None = None,
        expected_id: int | None = None,
        first_row: Commodity | None = None,
        db_error: RuntimeError | None = None,
    ) -> Iterator[_ScenarioBundle]:
        fence = install_p1_00_network_fence()
        self.assertTrue(fence["installed"])
        denied_before = len(fence["denied_connects"])
        redis_before = len(fence["live_redis_constructs"])

        orig_get = core.cache.get_cached_commodities
        orig_set = core.cache.set_cached_commodities
        self.assertFalse(isinstance(orig_get, unittest.mock.AsyncMock))
        self.assertFalse(isinstance(orig_set, unittest.mock.AsyncMock))
        self.assertTrue(inspect.iscoroutinefunction(orig_get))
        self.assertTrue(inspect.iscoroutinefunction(orig_set))
        self.assertIs(commodities_router.read_all_commodities, read_all_commodities)
        self.assertIs(commodities_router.read_commodity, read_commodity)
        self.assertIs(commodities_router.Commodity, Commodity)
        self.assertIs(commodities_router.CommodityAlias, CommodityAlias)

        events: list[str] = []
        fake_db = _QueryAwareFakeDB(
            events=events,
            route=route,
            all_rows=all_rows,
            expected_id=expected_id,
            first_row=first_row,
            db_error=db_error,
        )

        get_error_slot: list[RuntimeError | None] = [cache_get_error]
        set_error_slot: list[RuntimeError | None] = [cache_set_error]

        async def _get_side_effect(*args: Any, **kwargs: Any) -> Any:
            if args or kwargs:
                raise AssertionError(
                    f"get_cached_commodities expected no args, got args={args!r}, kwargs={kwargs!r}"
                )
            events.append("cache_get")
            if get_error_slot[0] is not None:
                err, get_error_slot[0] = get_error_slot[0], None
                raise err
            return cache_get_return

        async def _set_side_effect(*args: Any, **kwargs: Any) -> bool:
            if len(args) != 1 or kwargs:
                raise AssertionError(
                    f"set_cached_commodities expected 1 positional arg, got args={args!r}, kwargs={kwargs!r}"
                )
            events.append("cache_set")
            if set_error_slot[0] is not None:
                err, set_error_slot[0] = set_error_slot[0], None
                raise err
            return True

        get_mock = unittest.mock.AsyncMock(side_effect=_get_side_effect)
        set_mock = unittest.mock.AsyncMock(side_effect=_set_side_effect)

        with unittest.mock.patch(
            "core.cache.get_cached_commodities", new=get_mock
        ), unittest.mock.patch(
            "core.cache.set_cached_commodities", new=set_mock
        ):
            self.assertIs(core.cache.get_cached_commodities, get_mock)
            self.assertIs(core.cache.set_cached_commodities, set_mock)
            self.assertIsInstance(
                core.cache.get_cached_commodities, unittest.mock.AsyncMock
            )
            self.assertIsInstance(
                core.cache.set_cached_commodities, unittest.mock.AsyncMock
            )
            self.assertTrue(
                inspect.iscoroutinefunction(core.cache.get_cached_commodities)
            )
            self.assertTrue(
                inspect.iscoroutinefunction(core.cache.set_cached_commodities)
            )
            yield _ScenarioBundle(
                events=events,
                db=fake_db,
                get_mock=get_mock,
                set_mock=set_mock,
            )

        self.assertIs(core.cache.get_cached_commodities, orig_get)
        self.assertIs(core.cache.set_cached_commodities, orig_set)
        self.assertFalse(
            isinstance(core.cache.get_cached_commodities, unittest.mock.AsyncMock)
        )
        self.assertFalse(
            isinstance(core.cache.set_cached_commodities, unittest.mock.AsyncMock)
        )
        self.assertEqual(len(fence["denied_connects"]) - denied_before, 0)
        self.assertEqual(len(fence["live_redis_constructs"]) - redis_before, 0)
        self.assertEqual(fence["denied_connects"][denied_before:], [])
        self.assertEqual(fence["live_redis_constructs"][redis_before:], [])

    async def test_read_all_commodities_cache_and_database_paths(self) -> None:
        fence = install_p1_00_network_fence()
        denied_start = len(fence["denied_connects"])
        redis_start = len(fence["live_redis_constructs"])
        orig_get = core.cache.get_cached_commodities
        orig_set = core.cache.set_cached_commodities
        self.assertTrue(inspect.iscoroutinefunction(read_all_commodities))
        self.assertFalse(isinstance(read_all_commodities, unittest.mock.Mock))

        # 1. Truthy cache hit returns identical list without DB execute or cache write.
        cached_hit = [{"id": 5, "name": "کش‌شده", "aliases": []}]
        with self._scenario(route="none", cache_get_return=cached_hit) as bundle:
            result_hit = await read_all_commodities(db=bundle.db)

            self.assertIs(result_hit, cached_hit)
            self.assertEqual(result_hit, [{"id": 5, "name": "کش‌شده", "aliases": []}])
            self.assertEqual(bundle.events, ["cache_get"])
            self.assertEqual(bundle.db.executed_queries, [])
            self.assertEqual(bundle.db.executed_statements, [])
            self.assertIsNone(bundle.db.last_result)
            bundle.get_mock.assert_awaited_once_with()
            self.assertEqual(bundle.get_mock.await_args_list, [unittest.mock.call()])
            bundle.set_mock.assert_not_awaited()
            bundle.set_mock.assert_not_called()

        # 2. Cache miss with populated DB serializes in fixture/alias order and returns real fixtures.
        commodity_first = _make_commodity(
            2,
            "سکه تمام",
            [(22, "بهار آزادی"), (11, "طرح قدیم")],
        )
        commodity_second = _make_commodity(
            9,
            "شمش نقره",
            [(55, "نقره خالص"), (33, "شمش عیار بالا")],
        )
        alias_first_0, alias_first_1 = commodity_first.aliases[0], commodity_first.aliases[1]
        alias_second_0, alias_second_1 = commodity_second.aliases[0], commodity_second.aliases[1]

        expected_serialized = [
            {
                "id": 2,
                "name": "سکه تمام",
                "aliases": [
                    {"id": 22, "alias": "بهار آزادی", "commodity_id": 2},
                    {"id": 11, "alias": "طرح قدیم", "commodity_id": 2},
                ],
            },
            {
                "id": 9,
                "name": "شمش نقره",
                "aliases": [
                    {"id": 55, "alias": "نقره خالص", "commodity_id": 9},
                    {"id": 33, "alias": "شمش عیار بالا", "commodity_id": 9},
                ],
            },
        ]

        with self._scenario(
            route="all",
            cache_get_return=None,
            all_rows=[commodity_first, commodity_second],
        ) as bundle:
            result_miss = await read_all_commodities(db=bundle.db)

            self.assertEqual(
                bundle.events,
                ["cache_get", "execute:commodities.all", "cache_set"],
            )
            self.assertEqual(bundle.db.executed_queries, [(_READ_ALL_SQL, {})])
            self.assertEqual(len(bundle.db.executed_statements), 1)
            self._assert_sync_row_result(bundle.db.last_result)
            assert bundle.db.last_result is not None
            self.assertEqual(
                bundle.db.last_result.call_chain, ["scalars", "unique", "all"]
            )
            bundle.get_mock.assert_awaited_once_with()
            bundle.set_mock.assert_awaited_once_with(expected_serialized)
            sent_args, sent_kwargs = bundle.set_mock.await_args_list[0]
            self.assertEqual(len(sent_args), 1)
            self.assertEqual(sent_kwargs, {})
            for item in sent_args[0]:
                self.assertEqual(set(item.keys()), {"id", "name", "aliases"})
                for alias_dict in item["aliases"]:
                    self.assertEqual(
                        set(alias_dict.keys()), {"id", "alias", "commodity_id"}
                    )
            self.assertIsInstance(result_miss, list)
            self.assertEqual(len(result_miss), 2)
            self.assertIs(result_miss[0], commodity_first)
            self.assertIs(result_miss[1], commodity_second)
            self.assertIs(result_miss[0].aliases[0], alias_first_0)
            self.assertIs(result_miss[0].aliases[1], alias_first_1)
            self.assertIs(result_miss[1].aliases[0], alias_second_0)
            self.assertIs(result_miss[1].aliases[1], alias_second_1)

        # 3. Observed truthiness behavior: empty list from cache is falsy and falls through to DB.
        empty_cached: list[dict[str, Any]] = []
        with self._scenario(
            route="all",
            cache_get_return=empty_cached,
            all_rows=[],
        ) as bundle:
            result_empty = await read_all_commodities(db=bundle.db)

            self.assertEqual(
                bundle.events,
                ["cache_get", "execute:commodities.all", "cache_set"],
            )
            self.assertEqual(bundle.db.executed_queries, [(_READ_ALL_SQL, {})])
            self.assertEqual(len(bundle.db.executed_statements), 1)
            self._assert_sync_row_result(bundle.db.last_result)
            assert bundle.db.last_result is not None
            self.assertEqual(
                bundle.db.last_result.call_chain, ["scalars", "unique", "all"]
            )
            bundle.get_mock.assert_awaited_once_with()
            bundle.set_mock.assert_awaited_once_with([])
            self.assertIsInstance(result_empty, list)
            self.assertEqual(result_empty, [])
            self.assertIsNot(result_empty, empty_cached)

        # 4. Cache write failure propagates identical sentinel after DB query ran once.
        cache_write_sentinel = RuntimeError("read-all-cache-write-failure")
        commodity_for_write_err = _make_commodity(
            4,
            "شمش طلا",
            [(42, "شمش بانکی"), (41, "شمش استاندارد")],
        )
        expected_write_err_payload = [
            {
                "id": 4,
                "name": "شمش طلا",
                "aliases": [
                    {"id": 42, "alias": "شمش بانکی", "commodity_id": 4},
                    {"id": 41, "alias": "شمش استاندارد", "commodity_id": 4},
                ],
            }
        ]
        with self._scenario(
            route="all",
            cache_get_return=None,
            all_rows=[commodity_for_write_err],
            cache_set_error=cache_write_sentinel,
        ) as bundle:
            with self.assertRaises(RuntimeError) as write_exc:
                await read_all_commodities(db=bundle.db)

            self.assertIs(write_exc.exception, cache_write_sentinel)
            self.assertNotIsInstance(write_exc.exception, HTTPException)
            self.assertEqual(
                bundle.events,
                ["cache_get", "execute:commodities.all", "cache_set"],
            )
            self.assertEqual(bundle.db.executed_queries, [(_READ_ALL_SQL, {})])
            self.assertEqual(len(bundle.db.executed_statements), 1)
            self._assert_sync_row_result(bundle.db.last_result)
            bundle.get_mock.assert_awaited_once_with()
            bundle.set_mock.assert_awaited_once_with(expected_write_err_payload)

        # 5. Cache read failure propagates identical sentinel before DB execute or cache write.
        cache_read_sentinel = RuntimeError("read-all-cache-read-failure")
        with self._scenario(
            route="none",
            cache_get_error=cache_read_sentinel,
        ) as bundle:
            with self.assertRaises(RuntimeError) as read_exc:
                await read_all_commodities(db=bundle.db)

            self.assertIs(read_exc.exception, cache_read_sentinel)
            self.assertNotIsInstance(read_exc.exception, HTTPException)
            self.assertEqual(bundle.events, ["cache_get"])
            self.assertEqual(bundle.db.executed_queries, [])
            self.assertEqual(bundle.db.executed_statements, [])
            self.assertIsNone(bundle.db.last_result)
            bundle.get_mock.assert_awaited_once_with()
            bundle.set_mock.assert_not_awaited()
            bundle.set_mock.assert_not_called()

        # 6. DB failure propagates identical sentinel without awaiting set_cached_commodities.
        db_all_sentinel = RuntimeError("read-all-db-failure")
        with self._scenario(
            route="all",
            cache_get_return=None,
            db_error=db_all_sentinel,
        ) as bundle:
            with self.assertRaises(RuntimeError) as db_exc:
                await read_all_commodities(db=bundle.db)

            self.assertIs(db_exc.exception, db_all_sentinel)
            self.assertNotIsInstance(db_exc.exception, HTTPException)
            self.assertEqual(bundle.events, ["cache_get", "execute:commodities.all"])
            self.assertEqual(bundle.db.executed_queries, [(_READ_ALL_SQL, {})])
            self.assertEqual(len(bundle.db.executed_statements), 1)
            self.assertIsNone(bundle.db.last_result)
            bundle.get_mock.assert_awaited_once_with()
            bundle.set_mock.assert_not_awaited()
            bundle.set_mock.assert_not_called()

        self.assertIs(core.cache.get_cached_commodities, orig_get)
        self.assertIs(core.cache.set_cached_commodities, orig_set)
        self.assertFalse(
            isinstance(core.cache.get_cached_commodities, unittest.mock.AsyncMock)
        )
        self.assertFalse(
            isinstance(core.cache.set_cached_commodities, unittest.mock.AsyncMock)
        )
        self.assertEqual(len(fence["denied_connects"]) - denied_start, 0)
        self.assertEqual(len(fence["live_redis_constructs"]) - redis_start, 0)
        self.assertEqual(fence["denied_connects"][denied_start:], [])
        self.assertEqual(fence["live_redis_constructs"][redis_start:], [])

    async def test_read_commodity_eager_lookup_paths(self) -> None:
        fence = install_p1_00_network_fence()
        denied_start = len(fence["denied_connects"])
        redis_start = len(fence["live_redis_constructs"])
        orig_get = core.cache.get_cached_commodities
        orig_set = core.cache.set_cached_commodities
        self.assertTrue(inspect.iscoroutinefunction(read_commodity))
        self.assertFalse(isinstance(read_commodity, unittest.mock.Mock))

        # 1. Found: returns identical Commodity fixture with exact SQL/params and zero cache calls.
        commodity_found = _make_commodity(
            7,
            "سکه تمام",
            [(72, "بهار آزادی"), (71, "طرح قدیم")],
        )
        alias_7_first, alias_7_second = (
            commodity_found.aliases[0],
            commodity_found.aliases[1],
        )
        with self._scenario(
            route="id",
            expected_id=7,
            first_row=commodity_found,
        ) as bundle:
            result_found = await read_commodity(7, db=bundle.db)

            self.assertIs(result_found, commodity_found)
            self.assertEqual(len(result_found.aliases), 2)
            self.assertIs(result_found.aliases[0], alias_7_first)
            self.assertIs(result_found.aliases[1], alias_7_second)
            self.assertEqual(
                bundle.db.executed_queries,
                [(_READ_ONE_SQL, {"id_1": 7})],
            )
            self.assertEqual(len(bundle.db.executed_statements), 1)
            self._assert_sync_row_result(bundle.db.last_result)
            assert bundle.db.last_result is not None
            self.assertEqual(
                bundle.db.last_result.call_chain, ["scalars", "unique", "first"]
            )
            self.assertEqual(bundle.events, ["execute:commodities.id"])
            bundle.get_mock.assert_not_awaited()
            bundle.get_mock.assert_not_called()
            bundle.set_mock.assert_not_awaited()
            bundle.set_mock.assert_not_called()

        # 2. Missing: raises HTTPException 404 with exact Persian detail and zero cache calls.
        with self._scenario(
            route="id",
            expected_id=99,
            first_row=None,
        ) as bundle:
            with self.assertRaises(HTTPException) as missing_exc:
                await read_commodity(99, db=bundle.db)

            self.assertEqual(missing_exc.exception.status_code, 404)
            self.assertEqual(
                missing_exc.exception.detail,
                "کالا یافت نشد",
            )
            self.assertEqual(
                bundle.db.executed_queries,
                [(_READ_ONE_SQL, {"id_1": 99})],
            )
            self.assertEqual(len(bundle.db.executed_statements), 1)
            self._assert_sync_row_result(bundle.db.last_result)
            assert bundle.db.last_result is not None
            self.assertEqual(
                bundle.db.last_result.call_chain, ["scalars", "unique", "first"]
            )
            self.assertEqual(bundle.events, ["execute:commodities.id"])
            bundle.get_mock.assert_not_awaited()
            bundle.get_mock.assert_not_called()
            bundle.set_mock.assert_not_awaited()
            bundle.set_mock.assert_not_called()

        # 3. DB failure: propagates identical sentinel RuntimeError with zero cache calls.
        db_one_sentinel = RuntimeError("read-one-db-failure")
        with self._scenario(
            route="id",
            expected_id=7,
            db_error=db_one_sentinel,
        ) as bundle:
            with self.assertRaises(RuntimeError) as db_one_exc:
                await read_commodity(7, db=bundle.db)

            self.assertIs(db_one_exc.exception, db_one_sentinel)
            self.assertNotIsInstance(db_one_exc.exception, HTTPException)
            self.assertEqual(
                bundle.db.executed_queries,
                [(_READ_ONE_SQL, {"id_1": 7})],
            )
            self.assertEqual(len(bundle.db.executed_statements), 1)
            self.assertIsNone(bundle.db.last_result)
            self.assertEqual(bundle.events, ["execute:commodities.id"])
            bundle.get_mock.assert_not_awaited()
            bundle.get_mock.assert_not_called()
            bundle.set_mock.assert_not_awaited()
            bundle.set_mock.assert_not_called()

        self.assertIs(core.cache.get_cached_commodities, orig_get)
        self.assertIs(core.cache.set_cached_commodities, orig_set)
        self.assertFalse(
            isinstance(core.cache.get_cached_commodities, unittest.mock.AsyncMock)
        )
        self.assertFalse(
            isinstance(core.cache.set_cached_commodities, unittest.mock.AsyncMock)
        )
        self.assertEqual(len(fence["denied_connects"]) - denied_start, 0)
        self.assertEqual(len(fence["live_redis_constructs"]) - redis_start, 0)
        self.assertEqual(fence["denied_connects"][denied_start:], [])
        self.assertEqual(fence["live_redis_constructs"][redis_start:], [])


if __name__ == "__main__":
    unittest.main()
