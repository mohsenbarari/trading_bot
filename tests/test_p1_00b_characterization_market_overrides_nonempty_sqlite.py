from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
from datetime import date, datetime, time
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql import operators
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

# Insertion order: [20, 30, 10]; PK order: [10, 20, 30]; Date order: [30, 10, 20].
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
        "note": "  نیمه‌وقت ویژه بازار  ",
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
        "  نیمه‌وقت ویژه بازار  ",
        701,
        "2026-05-01 08:30:15.123456",
        None,
    ),
)

_EXPECTED_SERIALIZED_DICTS = [
    {
        "id": 30,
        "date": "2026-06-10",
        "override_type": MarketScheduleOverrideType.CUSTOM_HOURS,
        "open_time_local": "09:15",
        "close_time_local": "16:45",
        "note": "  نیمه‌وقت ویژه بازار  ",
    },
    {
        "id": 10,
        "date": "2026-06-12",
        "override_type": MarketScheduleOverrideType.CLOSED_ALL_DAY,
        "open_time_local": None,
        "close_time_local": None,
        "note": None,
    },
    {
        "id": 20,
        "date": "2026-06-15",
        "override_type": MarketScheduleOverrideType.OPEN_ALL_DAY,
        "open_time_local": "08:05",
        "close_time_local": None,
        "note": "\tبازگشایی اضطراری\n",
    },
]


class MarketOverridesNonemptySQLiteCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _snapshot(self, engine):
        self.assertEqual(inspect(engine).get_table_names(), ["market_schedule_overrides"])
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
        self.assertEqual(tuple(MarketScheduleOverride.__table__.columns.keys()), _COLUMNS)
        self.assertEqual(rows, _EXPECTED_PERSISTED_ROWS)
        self.assertEqual(schema[7][1:2], ("created_at",))
        self.assertEqual(schema[7][3], 1)
        self.assertEqual(schema[8][1:2], ("updated_at",))
        self.assertEqual(schema[8][3], 0)
        return schema, ddl, rows

    def _assert_clean_session_state(self, db: AsyncSession) -> None:
        self.assertEqual(tuple(db.new), ())
        self.assertEqual(tuple(db.dirty), ())
        self.assertEqual(tuple(db.deleted), ())
        self.assertEqual(tuple(db.sync_session.new), ())
        self.assertEqual(tuple(db.sync_session.dirty), ())
        self.assertEqual(tuple(db.sync_session.deleted), ())
        self.assertTrue(db.sync_session._is_clean())

    async def _create_seeded_nonempty_engine(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            query_cache_size=0,
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

            insertion_ids = [row["id"] for row in _SEED_ROWS]
            pk_ids = [row[0] for row in _EXPECTED_PERSISTED_ROWS]
            date_ids = [item["id"] for item in _EXPECTED_SERIALIZED_DICTS]
            self.assertEqual(insertion_ids, [20, 30, 10])
            self.assertEqual(pk_ids, [10, 20, 30])
            self.assertEqual(date_ids, [30, 10, 20])
            self.assertNotEqual(insertion_ids, pk_ids)
            self.assertNotEqual(insertion_ids, date_ids)
            self.assertNotEqual(pk_ids, date_ids)

            async with real_async_session(engine) as seed_db:
                for seed_row in _SEED_ROWS:
                    seed_db.add(MarketScheduleOverride(**seed_row))
                    await seed_db.flush()
                await seed_db.commit()
            return engine
        except Exception:
            engine.dispose()
            raise

    async def _exercise_nonempty_handler(self, engine, invocation=None, trace=None):
        before = self._snapshot(engine)
        with engine.connect() as connection:
            sqlite_connection = connection.connection.driver_connection
        self.assertIsInstance(sqlite_connection, sqlite3.Connection)

        trace = {} if trace is None else trace
        trace.clear()
        try:
            async with real_async_session(engine) as db:
                self.assertIsInstance(db, AsyncSession)
                self._assert_clean_session_state(db)
                events = []
                selected = []
                ascending = []
                ordered = []
                autoflush_calls = []
                sync_flush_calls = []
                results = []
                scalars = []
                all_rows_batches = []
                serialized_calls = []
                original_select = router.select
                original_asc = type(MarketScheduleOverride.date).asc
                original_order_by = Select.order_by
                original_execute = db.execute
                original_autoflush = db.sync_session._autoflush
                original_sync_flush = db.sync_session.flush
                original_serializer = router._serialize_market_schedule_override
                restorations = []
                listeners = []
                sql_observations = []
                sqlite_reads = []
                sqlite_selects = []
                denied_actions = []
                forbidden_calls = []
                forbidden_sync_flushes = []
                guard_state = {"in_execute": False, "in_autoflush": False}
                trace.update(
                    {
                        "events": events,
                        "selected": selected,
                        "ascending": ascending,
                        "ordered": ordered,
                        "autoflush_calls": autoflush_calls,
                        "sync_flush_calls": sync_flush_calls,
                        "results": results,
                        "scalars": scalars,
                        "all_rows_batches": all_rows_batches,
                        "serialized_calls": serialized_calls,
                        "sql_observations": sql_observations,
                        "sqlite_reads": sqlite_reads,
                        "sqlite_selects": sqlite_selects,
                        "denied_actions": denied_actions,
                        "forbidden_calls": forbidden_calls,
                        "forbidden_sync_flushes": forbidden_sync_flushes,
                    }
                )

                def deny(*args, **kwargs):
                    forbidden_calls.append((args, kwargs))
                    self.fail("Handler attempted a forbidden mutation or transaction")

                def observe_autoflush(*args, **kwargs):
                    if (
                        not guard_state["in_execute"]
                        or guard_state["in_autoflush"]
                        or len(autoflush_calls) != 0
                        or len(sync_flush_calls) != 0
                        or len(sql_observations) != 0
                        or args != ()
                        or kwargs != {}
                    ):
                        forbidden_calls.append((args, kwargs))
                        events.append("autoflush:denied")
                        self.fail("Handler attempted a forbidden or out-of-order _autoflush")
                    self._assert_clean_session_state(db)
                    events.append("autoflush")
                    guard_state["in_autoflush"] = True
                    try:
                        outcome = original_autoflush(*args, **kwargs)
                    finally:
                        guard_state["in_autoflush"] = False
                    self.assertIsNone(outcome)
                    self.assertEqual(len(sync_flush_calls), 1)
                    self._assert_clean_session_state(db)
                    autoflush_calls.append((args, kwargs, outcome))
                    return outcome

                def observe_sync_flush(*args, **kwargs):
                    if (
                        not guard_state["in_execute"]
                        or not guard_state["in_autoflush"]
                        or len(autoflush_calls) != 0
                        or len(sync_flush_calls) != 0
                        or len(sql_observations) != 0
                        or args != ()
                        or kwargs != {}
                    ):
                        forbidden_sync_flushes.append((args, kwargs))
                        forbidden_calls.append((args, kwargs))
                        events.append("sync_flush:denied")
                        self.fail(
                            "Handler attempted a forbidden explicit or non-autoflush sync_session.flush"
                        )
                    self._assert_clean_session_state(db)
                    events.append("sync_flush")
                    outcome = original_sync_flush(*args, **kwargs)
                    self.assertIsNone(outcome)
                    self._assert_clean_session_state(db)
                    sync_flush_calls.append((args, kwargs, outcome))
                    return outcome

                def authorize(action, argument1, argument2, database, trigger):
                    if action == sqlite3.SQLITE_SELECT:
                        sqlite_selects.append((argument1, argument2, database, trigger))
                        return sqlite3.SQLITE_OK
                    if (
                        action == sqlite3.SQLITE_READ
                        and argument1 == "market_schedule_overrides"
                        and argument2 in _COLUMNS
                        and database == "main"
                        and trigger is None
                    ):
                        sqlite_reads.append(argument2)
                        return sqlite3.SQLITE_OK
                    denied_actions.append((action, argument1, argument2, database, trigger))
                    return sqlite3.SQLITE_DENY

                def observe_sql(connection, cursor, statement, parameters, context, executemany):
                    self.assertEqual(len(sql_observations), 0, "Extra physical query")
                    self.assertEqual(len(ordered), 1)
                    self.assertIs(context.compiled.statement, ordered[0])
                    self.assertTrue(context.compiled.statement.is_select)
                    self.assertFalse(executemany)
                    self.assertEqual(parameters, ())
                    sql_observations.append((statement, parameters))
                    events.append("sql")

                def observe_select(*args, **kwargs):
                    self.assertEqual(len(selected), 0)
                    self.assertEqual(len(args), 1)
                    self.assertIs(args[0], MarketScheduleOverride)
                    self.assertEqual(kwargs, {})
                    events.append("select")
                    statement = original_select(*args, **kwargs)
                    self.assertIsInstance(statement, Select)
                    self.assertEqual(tuple(statement._order_by_clauses), ())
                    self.assertEqual(
                        statement.column_descriptions[0]["entity"],
                        MarketScheduleOverride,
                    )
                    selected.append(statement)
                    return statement

                def observe_asc(attribute, *args, **kwargs):
                    self.assertEqual(len(ascending), 0)
                    self.assertIs(attribute, MarketScheduleOverride.date)
                    self.assertEqual(args, ())
                    self.assertEqual(kwargs, {})
                    events.append("asc")
                    expression = original_asc(attribute, *args, **kwargs)
                    self.assertIs(expression.modifier, operators.asc_op)
                    self.assertTrue(
                        expression.element.compare(MarketScheduleOverride.__table__.c.date)
                    )
                    ascending.append(expression)
                    return expression

                def observe_order_by(statement, *args, **kwargs):
                    self.assertEqual(len(ordered), 0)
                    self.assertIs(statement, selected[0])
                    self.assertEqual(len(args), 1)
                    self.assertIs(args[0], ascending[0])
                    self.assertEqual(kwargs, {})
                    events.append("order_by")
                    result = original_order_by(statement, *args, **kwargs)
                    self.assertIsNot(result, statement)
                    clauses = tuple(result._order_by_clauses)
                    self.assertEqual(len(clauses), 1)
                    self.assertIs(clauses[0], ascending[0])
                    ordered.append(result)
                    return result

                def observe_serializer(*args, **kwargs):
                    self.assertEqual(len(all_rows_batches), 1)
                    self.assertEqual(len(args), 1)
                    self.assertEqual(kwargs, {})
                    index = len(serialized_calls)
                    expected_row = all_rows_batches[0][index]
                    self.assertIs(args[0], expected_row)
                    self.assertIsInstance(args[0], MarketScheduleOverride)
                    events.append(f"serialize:{args[0].id}")
                    response = original_serializer(*args, **kwargs)
                    serialized_calls.append((args[0], response))
                    return response

                stack = ExitStack()
                try:
                    def install(owner, name, replacement):
                        previous = getattr(owner, name)
                        restorations.append((owner, name, previous))
                        stack.enter_context(patch.object(owner, name, replacement))

                    async def observe_execute(*args, **kwargs):
                        self.assertEqual(len(results), 0)
                        self.assertEqual(len(args), 1)
                        self.assertIs(args[0], ordered[0])
                        self.assertEqual(kwargs, {})
                        self._assert_clean_session_state(db)
                        events.append("execute:await")
                        guard_state["in_execute"] = True
                        try:
                            result = await original_execute(*args, **kwargs)
                        finally:
                            guard_state["in_execute"] = False
                        self.assertEqual(len(autoflush_calls), 1)
                        self.assertEqual(len(sync_flush_calls), 1)
                        self._assert_clean_session_state(db)
                        results.append(result)
                        events.append("execute:return")
                        real_scalars = type(result).scalars

                        def observe_scalars(actual_result, *scalar_args, **scalar_kwargs):
                            self.assertEqual(len(scalars), 0)
                            self.assertIs(actual_result, result)
                            self.assertEqual(scalar_args, ())
                            self.assertEqual(scalar_kwargs, {})
                            events.append("scalars")
                            scalar_result = real_scalars(
                                actual_result, *scalar_args, **scalar_kwargs
                            )
                            scalars.append(scalar_result)
                            real_all = type(scalar_result).all

                            def observe_all(actual_scalars, *all_args, **all_kwargs):
                                self.assertEqual(len(all_rows_batches), 0)
                                self.assertIs(actual_scalars, scalar_result)
                                self.assertEqual(all_args, ())
                                self.assertEqual(all_kwargs, {})
                                events.append("all")
                                rows = real_all(actual_scalars, *all_args, **all_kwargs)
                                self.assertEqual([row.id for row in rows], [30, 10, 20])
                                all_rows_batches.append(rows)
                                return rows

                            install(type(scalar_result), "all", observe_all)
                            return scalar_result

                        install(type(result), "scalars", observe_scalars)
                        return result

                    execute_spy = AsyncMock(side_effect=observe_execute)
                    install(router, "select", observe_select)
                    install(type(MarketScheduleOverride.date), "asc", observe_asc)
                    install(Select, "order_by", observe_order_by)
                    install(db, "execute", execute_spy)
                    install(router, "_serialize_market_schedule_override", observe_serializer)
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
                            if owner is db.sync_session and name == "flush":
                                install(owner, name, observe_sync_flush)
                                continue
                            install(owner, name, deny)
                    install(db.sync_session, "_autoflush", observe_autoflush)
                    event.listen(db.sync_session, "before_flush", deny)
                    listeners.append((db.sync_session, "before_flush", deny))
                    event.listen(engine, "before_cursor_execute", observe_sql)
                    listeners.append((engine, "before_cursor_execute", observe_sql))
                    for name in (
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
                        event.listen(engine, name, deny)
                        listeners.append((engine, name, deny))
                    sqlite_connection.set_authorizer(authorize)

                    runner = router.list_market_overrides if invocation is None else invocation
                    output = await runner(db=db)

                    self.assertIs(type(output), list)
                    self.assertEqual(len(output), 3)
                    self.assertTrue(
                        all(
                            isinstance(item, router.MarketScheduleOverrideResponse)
                            for item in output
                        )
                    )
                    self.assertEqual(
                        output,
                        [
                            router.MarketScheduleOverrideResponse(**expected)
                            for expected in _EXPECTED_SERIALIZED_DICTS
                        ],
                    )
                    self.assertEqual(
                        [item.model_dump() for item in output],
                        _EXPECTED_SERIALIZED_DICTS,
                    )
                    self.assertEqual(
                        [item for _, item in serialized_calls],
                        output,
                    )
                    execute_spy.assert_called_once_with(ordered[0])
                    execute_spy.assert_awaited_once_with(ordered[0])
                    self.assertEqual(
                        events,
                        [
                            "select",
                            "asc",
                            "order_by",
                            "execute:await",
                            "autoflush",
                            "sync_flush",
                            "sql",
                            "execute:return",
                            "scalars",
                            "all",
                            "serialize:30",
                            "serialize:10",
                            "serialize:20",
                        ],
                    )
                    self.assertEqual(autoflush_calls, [((), {}, None)])
                    self.assertEqual(sync_flush_calls, [((), {}, None)])
                    self.assertEqual(len(results), 1)
                    self.assertEqual(len(scalars), 1)
                    self.assertEqual(len(all_rows_batches), 1)
                    self.assertEqual(len(serialized_calls), 3)
                    self.assertEqual(len(sql_observations), 1)
                    self.assertEqual(sqlite_selects, [(None, None, None, None)])
                    self.assertEqual(set(sqlite_reads), set(_COLUMNS))
                    self.assertEqual(denied_actions, [])
                    self.assertEqual(forbidden_calls, [])
                    self.assertEqual(forbidden_sync_flushes, [])
                    self._assert_clean_session_state(db)
                    trace["output"] = output
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
                    self.assertIs(Select.order_by, original_order_by)
                    self.assertIs(type(MarketScheduleOverride.date).asc, original_asc)
                    self.assertIs(
                        router._serialize_market_schedule_override,
                        original_serializer,
                    )
                    self.assertIs(db.execute.__self__, original_execute.__self__)
                    self.assertIs(db.execute.__func__, original_execute.__func__)
                    self.assertIs(
                        db.sync_session._autoflush.__self__,
                        original_autoflush.__self__,
                    )
                    self.assertIs(
                        db.sync_session._autoflush.__func__,
                        original_autoflush.__func__,
                    )
                    self.assertIs(
                        db.sync_session.flush.__self__,
                        original_sync_flush.__self__,
                    )
                    self.assertIs(
                        db.sync_session.flush.__func__,
                        original_sync_flush.__func__,
                    )
                    for owner, name, listener in listeners:
                        self.assertFalse(event.contains(owner, name, listener))
                    self._assert_clean_session_state(db)
                    trace["restored"] = True
        finally:
            after = self._snapshot(engine)
            self.assertEqual(after, before)
            trace["post_snapshot"] = after
        return trace

    async def test_nonempty_real_sqlite_sorted_serialization_preserves_full_state(self):
        engine = await self._create_seeded_nonempty_engine()
        try:
            trace = await self._exercise_nonempty_handler(engine)
            self.assertTrue(trace["restored"])
        finally:
            engine.dispose()

    async def test_explicit_clean_sync_flush_is_rejected(self):
        engine = await self._create_seeded_nonempty_engine()
        try:
            baseline = self._snapshot(engine)
            control_trace = await self._exercise_nonempty_handler(engine)
            self.assertTrue(control_trace["restored"])
            self.assertEqual(control_trace["post_snapshot"], baseline)
            self.assertEqual(control_trace["autoflush_calls"], [((), {}, None)])
            self.assertEqual(control_trace["sync_flush_calls"], [((), {}, None)])
            self.assertEqual(control_trace["forbidden_sync_flushes"], [])

            before_trace = {}

            async def flush_before_handler(*, db: AsyncSession):
                self._assert_clean_session_state(db)
                db.sync_session.flush()
                return await router.list_market_overrides(db=db)

            with self.assertRaisesRegex(
                AssertionError,
                "Handler attempted a forbidden explicit or non-autoflush sync_session.flush",
            ):
                await self._exercise_nonempty_handler(
                    engine,
                    flush_before_handler,
                    before_trace,
                )
            self.assertEqual(before_trace["events"], ["sync_flush:denied"])
            self.assertEqual(before_trace["selected"], [])
            self.assertEqual(before_trace["ascending"], [])
            self.assertEqual(before_trace["ordered"], [])
            self.assertEqual(before_trace["autoflush_calls"], [])
            self.assertEqual(before_trace["sync_flush_calls"], [])
            self.assertEqual(before_trace["results"], [])
            self.assertEqual(before_trace["scalars"], [])
            self.assertEqual(before_trace["all_rows_batches"], [])
            self.assertEqual(before_trace["serialized_calls"], [])
            self.assertEqual(before_trace["sql_observations"], [])
            self.assertEqual(before_trace["sqlite_reads"], [])
            self.assertEqual(before_trace["sqlite_selects"], [])
            self.assertEqual(before_trace["denied_actions"], [])
            self.assertEqual(before_trace["forbidden_calls"], [((), {})])
            self.assertEqual(before_trace["forbidden_sync_flushes"], [((), {})])
            self.assertTrue(before_trace["restored"])
            self.assertEqual(before_trace["post_snapshot"], baseline)
            self.assertNotIn("output", before_trace)
            self.assertEqual(self._snapshot(engine), baseline)

            after_trace = {}
            after_events = []

            async def flush_after_handler(*, db: AsyncSession):
                response = await router.list_market_overrides(db=db)
                self._assert_clean_session_state(db)
                after_events.append([item.id for item in response])
                db.sync_session.flush()
                return response

            with self.assertRaisesRegex(
                AssertionError,
                "Handler attempted a forbidden explicit or non-autoflush sync_session.flush",
            ):
                await self._exercise_nonempty_handler(
                    engine,
                    flush_after_handler,
                    after_trace,
                )
            self.assertEqual(after_events, [[30, 10, 20]])
            self.assertEqual(
                after_trace["events"],
                [
                    "select",
                    "asc",
                    "order_by",
                    "execute:await",
                    "autoflush",
                    "sync_flush",
                    "sql",
                    "execute:return",
                    "scalars",
                    "all",
                    "serialize:30",
                    "serialize:10",
                    "serialize:20",
                    "sync_flush:denied",
                ],
            )
            self.assertEqual(len(after_trace["selected"]), 1)
            self.assertEqual(len(after_trace["ascending"]), 1)
            self.assertEqual(len(after_trace["ordered"]), 1)
            self.assertEqual(after_trace["autoflush_calls"], [((), {}, None)])
            self.assertEqual(after_trace["sync_flush_calls"], [((), {}, None)])
            self.assertEqual(len(after_trace["results"]), 1)
            self.assertEqual(len(after_trace["scalars"]), 1)
            self.assertEqual(len(after_trace["all_rows_batches"]), 1)
            self.assertEqual(len(after_trace["serialized_calls"]), 3)
            self.assertEqual(len(after_trace["sql_observations"]), 1)
            self.assertEqual(after_trace["sqlite_selects"], [(None, None, None, None)])
            self.assertEqual(set(after_trace["sqlite_reads"]), set(_COLUMNS))
            self.assertEqual(after_trace["denied_actions"], [])
            self.assertEqual(after_trace["forbidden_calls"], [((), {})])
            self.assertEqual(after_trace["forbidden_sync_flushes"], [((), {})])
            self.assertTrue(after_trace["restored"])
            self.assertEqual(after_trace["post_snapshot"], baseline)
            self.assertNotIn("output", after_trace)
            self.assertEqual(self._snapshot(engine), baseline)

            control_after = await self._exercise_nonempty_handler(engine)
            self.assertTrue(control_after["restored"])
            self.assertEqual(control_after["post_snapshot"], baseline)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
