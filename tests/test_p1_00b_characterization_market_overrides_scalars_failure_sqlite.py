from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Result, ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql import operators
from sqlalchemy.sql.selectable import Select

from api.routers import trading_settings as router
from models.market_schedule_override import MarketScheduleOverride
from tests import (
    test_p1_00b_characterization_market_overrides_nonempty_sqlite as nonempty_fixture,
)
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


_COLUMNS = nonempty_fixture._COLUMNS
_SNAPSHOT_SQL = nonempty_fixture._SNAPSHOT_SQL
_SEED_ROWS = nonempty_fixture._SEED_ROWS
_EXPECTED_PERSISTED_ROWS = nonempty_fixture._EXPECTED_PERSISTED_ROWS
_EXPECTED_SERIALIZED_DICTS = nonempty_fixture._EXPECTED_SERIALIZED_DICTS


class _ScalarsFailureSentinel(RuntimeError):
    """Dedicated scenario sentinel raised only by synchronous Result.scalars()."""


class MarketOverridesScalarsFailureSQLiteCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
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

    async def test_scalars_failure_propagates_after_real_execute_without_downstream_work(
        self,
    ):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False, "cached_statements": 0},
        )
        original_dispose = engine.dispose
        engine_disposed = []

        def observe_dispose(*args, **kwargs):
            engine_disposed.append((args, kwargs))
            return original_dispose(*args, **kwargs)

        engine.dispose = observe_dispose
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

            async with real_async_session(engine) as seed_db:
                for seed_row in _SEED_ROWS:
                    seed_db.add(MarketScheduleOverride(**seed_row))
                    await seed_db.flush()
                await seed_db.commit()

            before = self._snapshot(engine)
            with engine.connect() as connection:
                sqlite_connection = connection.connection.driver_connection
            self.assertIsInstance(sqlite_connection, sqlite3.Connection)

            session_closed = []
            original_async_close = AsyncSession.close

            async def observe_async_close(session_self, *args, **kwargs):
                session_closed.append((session_self, args, kwargs))
                return await original_async_close(session_self, *args, **kwargs)

            with patch.object(AsyncSession, "close", observe_async_close):
                async with real_async_session(engine) as db:
                    self.assertIsInstance(db, AsyncSession)
                    self.assertEqual(tuple(db.new), ())
                    self.assertEqual(tuple(db.dirty), ())
                    self.assertEqual(tuple(db.deleted), ())

                    sentinel_error = _ScalarsFailureSentinel(
                        "synchronous scalars failure on real Result"
                    )
                    events = []
                    selected = []
                    ascending = []
                    ordered = []
                    results = []
                    scalars_calls = []
                    all_calls = []
                    serialized_calls = []
                    autoflush_calls = []
                    sync_flush_calls = []
                    sql_observations = []
                    sqlite_reads = []
                    sqlite_selects = []
                    denied_actions = []
                    forbidden_calls = []
                    restorations = []
                    listeners = []
                    authorizer_Cleared = []
                    in_execute = False
                    autoflush_active = False

                    original_select = router.select
                    original_asc = type(MarketScheduleOverride.date).asc
                    original_order_by = Select.order_by
                    original_execute = db.execute
                    original_serializer = router._serialize_market_schedule_override
                    original_scalar_result_all = ScalarResult.all
                    original_autoflush = db.sync_session._autoflush
                    original_sync_flush = db.sync_session.flush

                    def deny(*args, **kwargs):
                        forbidden_calls.append((args, kwargs))
                        self.fail("Handler attempted a forbidden mutation or transaction")

                    def observe_autoflush(*args, **kwargs):
                        nonlocal autoflush_active
                        if (
                            not in_execute
                            or autoflush_active
                            or len(autoflush_calls) != 0
                            or len(sql_observations) != 0
                            or args != ()
                            or kwargs != {}
                        ):
                            deny(*args, **kwargs)
                        autoflush_calls.append("autoflush")
                        events.append("autoflush")
                        autoflush_active = True
                        try:
                            return original_autoflush(*args, **kwargs)
                        finally:
                            autoflush_active = False

                    def guard_sync_flush(*args, **kwargs):
                        if (
                            not in_execute
                            or not autoflush_active
                            or len(sync_flush_calls) != 0
                            or len(sql_observations) != 0
                            or args != ()
                            or kwargs != {}
                            or tuple(db.sync_session.new) != ()
                            or tuple(db.sync_session.dirty) != ()
                            or tuple(db.sync_session.deleted) != ()
                            or not db.sync_session._is_clean()
                        ):
                            deny(*args, **kwargs)
                        sync_flush_calls.append("flush")
                        events.append("sync_flush:noop")
                        return original_sync_flush(*args, **kwargs)

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
                        denied_actions.append(
                            (action, argument1, argument2, database, trigger)
                        )
                        return sqlite3.SQLITE_DENY

                    def observe_sql(
                        connection, cursor, statement, parameters, context, executemany
                    ):
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
                            expression.element.compare(
                                MarketScheduleOverride.__table__.c.date
                            )
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

                    def observe_all(*args, **kwargs):
                        all_calls.append((args, kwargs))
                        events.append("all")
                        self.fail("all() must not be called when scalars() fails")

                    def observe_serializer(*args, **kwargs):
                        serialized_calls.append((args, kwargs))
                        events.append("serialize")
                        self.fail("serializer must not be called when scalars() fails")

                    stack = ExitStack()
                    try:
                        def install(owner, name, replacement):
                            previous = getattr(owner, name)
                            restorations.append((owner, name, previous))
                            stack.enter_context(patch.object(owner, name, replacement))

                        async def observe_execute(*args, **kwargs):
                            nonlocal in_execute
                            self.assertEqual(len(results), 0)
                            self.assertEqual(len(args), 1)
                            self.assertIs(args[0], ordered[0])
                            self.assertEqual(kwargs, {})
                            events.append("execute:await")
                            in_execute = True
                            try:
                                result = await original_execute(*args, **kwargs)
                            finally:
                                in_execute = False
                            self.assertIsInstance(result, Result)
                            results.append(result)
                            events.append("execute:return")
                            real_scalars = type(result).scalars
                            self.assertTrue(callable(real_scalars))

                            def observe_scalars(
                                actual_result, *scalar_args, **scalar_kwargs
                            ):
                                self.assertEqual(len(scalars_calls), 0)
                                self.assertIs(actual_result, result)
                                self.assertEqual(scalar_args, ())
                                self.assertEqual(scalar_kwargs, {})
                                scalars_calls.append(
                                    (actual_result, scalar_args, scalar_kwargs)
                                )
                                events.append("scalars:raise")
                                raise sentinel_error

                            install(type(result), "scalars", observe_scalars)
                            install(type(result), "all", observe_all)
                            return result

                        execute_spy = AsyncMock(side_effect=observe_execute)
                        install(router, "select", observe_select)
                        install(type(MarketScheduleOverride.date), "asc", observe_asc)
                        install(Select, "order_by", observe_order_by)
                        install(db, "execute", execute_spy)
                        install(ScalarResult, "all", observe_all)
                        install(
                            router,
                            "_serialize_market_schedule_override",
                            observe_serializer,
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
                                if owner is db.sync_session and name == "flush":
                                    install(owner, name, guard_sync_flush)
                                else:
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

                        with self.assertRaises(_ScalarsFailureSentinel) as exc_info:
                            await router.list_market_overrides(db=db)

                        self.assertIs(exc_info.exception, sentinel_error)
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
                                "sync_flush:noop",
                                "sql",
                                "execute:return",
                                "scalars:raise",
                            ],
                        )
                        self.assertEqual(len(selected), 1)
                        self.assertEqual(len(ascending), 1)
                        self.assertEqual(len(ordered), 1)
                        self.assertEqual(len(results), 1)
                        self.assertEqual(len(autoflush_calls), 1)
                        self.assertEqual(len(sync_flush_calls), 1)
                        self.assertEqual(
                            scalars_calls,
                            [(results[0], (), {})],
                        )
                        self.assertEqual(all_calls, [])
                        self.assertEqual(serialized_calls, [])
                        self.assertEqual(len(sql_observations), 1)
                        self.assertEqual(sqlite_selects, [(None, None, None, None)])
                        self.assertEqual(set(sqlite_reads), set(_COLUMNS))
                        self.assertEqual(denied_actions, [])
                        self.assertEqual(forbidden_calls, [])
                        self.assertEqual(tuple(db.new), ())
                        self.assertEqual(tuple(db.dirty), ())
                        self.assertEqual(tuple(db.deleted), ())
                    finally:
                        try:
                            sqlite_connection.set_authorizer(None)
                            authorizer_Cleared.append(True)
                        finally:
                            try:
                                for owner, name, listener in reversed(listeners):
                                    event.remove(owner, name, listener)
                            finally:
                                stack.close()
                        self.assertEqual(authorizer_Cleared, [True])
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
                        self.assertIs(ScalarResult.all, original_scalar_result_all)
                        self.assertIs(
                            router._serialize_market_schedule_override,
                            original_serializer,
                        )
                        self.assertIs(
                            db.sync_session._autoflush.__func__,
                            original_autoflush.__func__,
                        )
                        self.assertIs(
                            db.sync_session.flush.__func__,
                            original_sync_flush.__func__,
                        )
                        for owner, name, listener in listeners:
                            self.assertFalse(event.contains(owner, name, listener))

                self.assertEqual(len(session_closed), 1)
                self.assertIs(session_closed[0][0], db)

            after = self._snapshot(engine)
            self.assertEqual(after, before)
        finally:
            engine.dispose()
            self.assertEqual(len(engine_disposed), 1)


if __name__ == "__main__":
    unittest.main()
