"""P1-00b select-construction failure characterization for list_market_overrides.

Source: api/routers/trading_settings.py:list_market_overrides
Seed: API-ADMIN-GET-TRADING-SETTINGS-LIST-MARKET-OVERRIDES-L214
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Batch exclusion: excluded from P1-00B-BATCH-V1 because it requires custom real_async_session
and MarketScheduleOverride table/index custody.
Remaining unproved failure boundaries: MarketScheduleOverride.date.asc / Select.order_by,
awaited db.execute, synchronous Result.scalars, and synchronous ScalarResult.all.
No whole-handler PROVEN, seed, origin, High blocker, Stage, or operational credit is claimed.
"""
from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
from datetime import date, datetime, time
import sqlite3
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Result, ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql.selectable import Select

from api.routers import trading_settings as router
from models.market_schedule_override import (
    MarketScheduleOverride,
    MarketScheduleOverrideType,
)
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


_COLUMNS = (
    "id",
    "date",
    "override_type",
    "open_time_local",
    "close_time_local",
    "note",
    "created_by_user_id",
    "created_at",
    "updated_at",
)
_SNAPSHOT_SQL = (
    "SELECT id, date, override_type, open_time_local, close_time_local, note, "
    "created_by_user_id, created_at, updated_at "
    "FROM market_schedule_overrides ORDER BY id"
)

_SEED_ROWS = (
    {
        "id": 20,
        "date": date(2026, 6, 15),
        "override_type": MarketScheduleOverrideType.OPEN_ALL_DAY,
        "open_time_local": time(8, 5, 7, 654321),
        "close_time_local": None,
        "note": "\tبازگشایی اضطراری\n",
        "created_by_user_id": 404,
        "created_at": datetime(2026, 5, 3, 10, 15, 30, 500000),
        "updated_at": datetime(2026, 5, 4, 11, 20, 45, 250000),
    },
    {
        "id": 30,
        "date": date(2026, 6, 10),
        "override_type": MarketScheduleOverrideType.CUSTOM_HOURS,
        "open_time_local": time(9, 15, 42, 987654),
        "close_time_local": time(16, 45, 59, 123456),
        "note": "  نیمهوقت ویژه بازار  ",
        "created_by_user_id": 701,
        "created_at": datetime(2026, 5, 1, 8, 30, 15, 123456),
        "updated_at": None,
    },
    {
        "id": 10,
        "date": date(2026, 6, 12),
        "override_type": MarketScheduleOverrideType.CLOSED_ALL_DAY,
        "open_time_local": None,
        "close_time_local": None,
        "note": None,
        "created_by_user_id": None,
        "created_at": datetime(2026, 5, 2, 9, 0, 0),
        "updated_at": None,
    },
)

_EXPECTED_PERSISTED_ROWS = (
    (
        10,
        "2026-06-12",
        "closed_all_day",
        None,
        None,
        None,
        None,
        "2026-05-02 09:00:00.000000",
        None,
    ),
    (
        20,
        "2026-06-15",
        "open_all_day",
        "08:05:07.654321",
        None,
        "\tبازگشایی اضطراری\n",
        404,
        "2026-05-03 10:15:30.500000",
        "2026-05-04 11:20:45.250000",
    ),
    (
        30,
        "2026-06-10",
        "custom_hours",
        "09:15:42.987654",
        "16:45:59.123456",
        "  نیمهوقت ویژه بازار  ",
        701,
        "2026-05-01 08:30:15.123456",
        None,
    ),
)


class MarketOverridesSelectFailureSQLiteCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def _snapshot(self, engine):
        self.assertEqual(
            inspect(engine).get_table_names(), ["market_schedule_overrides"]
        )
        with engine.connect() as connection:
            schema = tuple(
                map(
                    tuple,
                    connection.exec_driver_sql(
                        'PRAGMA table_info("market_schedule_overrides")'
                    ).all(),
                )
            )
            ddl = tuple(
                map(
                    tuple,
                    connection.exec_driver_sql(
                        "SELECT type, name, tbl_name, sql FROM sqlite_master "
                        "WHERE tbl_name = 'market_schedule_overrides' ORDER BY type, name"
                    ).all(),
                )
            )
            result = connection.exec_driver_sql(_SNAPSHOT_SQL)
            self.assertEqual(tuple(result.keys()), _COLUMNS)
            rows = tuple(map(tuple, result.all()))
        self.assertEqual(tuple(row[1] for row in schema), _COLUMNS)
        self.assertEqual(
            tuple(MarketScheduleOverride.__table__.columns.keys()), _COLUMNS
        )
        self.assertEqual(rows, _EXPECTED_PERSISTED_ROWS)
        self.assertEqual(schema[7][1:2], ("created_at",))
        self.assertEqual(schema[7][3], 1)
        self.assertEqual(schema[8][1:2], ("updated_at",))
        self.assertEqual(schema[8][3], 0)
        return schema, ddl, rows

    async def test_select_construction_failure_propagates_before_downstream_work(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False, "cached_statements": 0},
        )
        try:
            table = MarketScheduleOverride.__table__
            indexes = sorted(table.indexes, key=lambda index: index.name)
            index_definitions = {}
            for index in indexes:
                definition = str(CreateIndex(index).compile(dialect=engine.dialect))
                if index.name in index_definitions:
                    self.assertEqual(definition, index_definitions[index.name])
                index_definitions[index.name] = definition
            with engine.begin() as connection:
                connection.execute(CreateTable(table))
                for index in indexes:
                    connection.execute(CreateIndex(index, if_not_exists=True))

            async with real_async_session(engine) as seed_db:
                for seed_row in _SEED_ROWS:
                    seed_db.add(MarketScheduleOverride(**seed_row))
                    await seed_db.flush()
                await seed_db.commit()

            before = self._snapshot(engine)
            with engine.connect() as connection:
                sqlite_connection = connection.connection.driver_connection
            self.assertIsInstance(sqlite_connection, sqlite3.Connection)

            async with real_async_session(engine) as db:
                self.assertIsInstance(db, AsyncSession)
                self.assertEqual(tuple(db.new), ())
                self.assertEqual(tuple(db.dirty), ())
                self.assertEqual(tuple(db.deleted), ())
                events = []
                select_calls = []
                injected_error = RuntimeError("p1-00b select construction failure")
                original_select = router.select
                original_asc = type(MarketScheduleOverride.date).asc
                original_order_by = Select.order_by
                original_execute = db.execute
                original_scalars = Result.scalars
                original_all = ScalarResult.all
                original_serializer = router._serialize_market_schedule_override
                restorations = []
                listeners = []
                sql_observations = []
                authorizer_calls = []
                forbidden_calls = []

                def make_guard(label):
                    def _guard(*args, **kwargs):
                        forbidden_calls.append((label, args, kwargs))
                        self.fail(f"Forbidden downstream or mutation call: {label}")

                    return _guard

                def authorize(action, argument1, argument2, database, trigger):
                    authorizer_calls.append(
                        (action, argument1, argument2, database, trigger)
                    )
                    return sqlite3.SQLITE_DENY

                def observe_sql(
                    connection, cursor, statement, parameters, context, executemany
                ):
                    sql_observations.append((statement, parameters, executemany))
                    self.fail("Forbidden physical SQL execution during select failure")

                def fail_select(*args, **kwargs):
                    self.assertEqual(len(select_calls), 0)
                    self.assertEqual(len(args), 1)
                    self.assertIs(args[0], MarketScheduleOverride)
                    self.assertEqual(kwargs, {})
                    select_calls.append((args, kwargs))
                    events.append("select")
                    raise injected_error

                asc_guard = MagicMock(side_effect=make_guard("date.asc"))
                order_by_guard = MagicMock(side_effect=make_guard("Select.order_by"))
                execute_guard = AsyncMock(side_effect=make_guard("db.execute"))
                scalars_guard = MagicMock(side_effect=make_guard("Result.scalars"))
                all_guard = MagicMock(side_effect=make_guard("ScalarResult.all"))
                serializer_guard = MagicMock(
                    side_effect=make_guard("_serialize_market_schedule_override")
                )

                stack = ExitStack()
                try:
                    def install(owner, name, replacement):
                        previous = getattr(owner, name)
                        restorations.append((owner, name, previous))
                        stack.enter_context(patch.object(owner, name, replacement))

                    install(router, "select", fail_select)
                    install(type(MarketScheduleOverride.date), "asc", asc_guard)
                    install(Select, "order_by", order_by_guard)
                    install(db, "execute", execute_guard)
                    install(Result, "scalars", scalars_guard)
                    install(ScalarResult, "all", all_guard)
                    install(
                        router,
                        "_serialize_market_schedule_override",
                        serializer_guard,
                    )
                    mutators = (
                        "add",
                        "add_all",
                        "delete",
                        "merge",
                        "flush",
                        "commit",
                        "rollback",
                        "refresh",
                    )
                    for owner in (db, db.sync_session):
                        for name in mutators:
                            install(owner, name, make_guard(f"{type(owner).__name__}.{name}"))
                    before_flush_guard = make_guard("before_flush")
                    event.listen(db.sync_session, "before_flush", before_flush_guard)
                    listeners.append(
                        (db.sync_session, "before_flush", before_flush_guard)
                    )
                    event.listen(engine, "before_cursor_execute", observe_sql)
                    listeners.append((engine, "before_cursor_execute", observe_sql))
                    for name in (
                        "begin",
                        "commit",
                        "rollback",
                        "savepoint",
                        "rollback_savepoint",
                        "release_savepoint",
                        "begin_twophase",
                        "prepare_twophase",
                        "commit_twophase",
                        "rollback_twophase",
                    ):
                        tx_guard = make_guard(f"engine.{name}")
                        event.listen(engine, name, tx_guard)
                        listeners.append((engine, name, tx_guard))
                    sqlite_connection.set_authorizer(authorize)

                    with self.assertRaises(RuntimeError) as caught:
                        await router.list_market_overrides(db=db)

                    self.assertIs(caught.exception, injected_error)
                    self.assertEqual(select_calls, [((MarketScheduleOverride,), {})])
                    self.assertEqual(events, ["select"])
                    asc_guard.assert_not_called()
                    order_by_guard.assert_not_called()
                    execute_guard.assert_not_called()
                    execute_guard.assert_not_awaited()
                    scalars_guard.assert_not_called()
                    all_guard.assert_not_called()
                    serializer_guard.assert_not_called()
                    self.assertEqual(sql_observations, [])
                    self.assertEqual(authorizer_calls, [])
                    self.assertEqual(forbidden_calls, [])
                    self.assertEqual(tuple(db.new), ())
                    self.assertEqual(tuple(db.dirty), ())
                    self.assertEqual(tuple(db.deleted), ())
                finally:
                    try:
                        sqlite_connection.set_authorizer(None)
                    finally:
                        try:
                            for owner, name, listener in reversed(listeners):
                                event.remove(owner, name, listener)
                        finally:
                            stack.close()
                    for owner, name, previous in restorations:
                        restored = getattr(owner, name)
                        if hasattr(previous, "__self__"):
                            self.assertIs(restored.__self__, previous.__self__)
                            self.assertIs(restored.__func__, previous.__func__)
                        else:
                            self.assertIs(restored, previous)
                    self.assertIs(router.select, original_select)
                    self.assertIs(type(MarketScheduleOverride.date).asc, original_asc)
                    self.assertIs(Select.order_by, original_order_by)
                    self.assertIs(db.execute.__self__, original_execute.__self__)
                    self.assertIs(db.execute.__func__, original_execute.__func__)
                    self.assertIs(Result.scalars, original_scalars)
                    self.assertIs(ScalarResult.all, original_all)
                    self.assertIs(
                        router._serialize_market_schedule_override,
                        original_serializer,
                    )
                    for owner, name, listener in listeners:
                        self.assertFalse(event.contains(owner, name, listener))

            after = self._snapshot(engine)
            self.assertEqual(after, before)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
