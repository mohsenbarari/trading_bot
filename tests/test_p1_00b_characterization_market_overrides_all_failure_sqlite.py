"""CODEX_AUTHORED by /root/cli_static_cause; manual characterization candidate.
Seed: API-ADMIN-GET-TRADING-SETTINGS-LIST-MARKET-OVERRIDES-L214.
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
Only direct-handler real-SQLite ScalarResult.all() failure. Consumed776B-C001
remains failed/unaccepted: manual fulfillment is never SDK replay/alias.
Excludes HTTP/auth, C006, serializer failure, retry/reload/concurrency, legacy
flush repairs, handler/seed/origin/High/Stage closure and operational work.
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
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Result, ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql import operators
from sqlalchemy.sql.selectable import Select

from api.routers import trading_settings as router
from models.market_schedule_override import MarketScheduleOverride, MarketScheduleOverrideType
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


_COLUMNS = (
    "id", "date", "override_type", "open_time_local", "close_time_local", "note",
    "created_by_user_id", "created_at", "updated_at",
)
_SNAPSHOT_SQL = (
    "SELECT id, date, override_type, open_time_local, close_time_local, note, "
    "created_by_user_id, created_at, updated_at FROM market_schedule_overrides ORDER BY id"
)
_EXPECTED_PHYSICAL_SELECT = (
    "SELECT market_schedule_overrides.id, market_schedule_overrides.date, "
    "market_schedule_overrides.override_type, market_schedule_overrides.open_time_local, "
    "market_schedule_overrides.close_time_local, market_schedule_overrides.note, "
    "market_schedule_overrides.created_by_user_id, market_schedule_overrides.created_at, "
    "market_schedule_overrides.updated_at FROM market_schedule_overrides "
    "ORDER BY market_schedule_overrides.date ASC"
)
_EXPECTED_SCHEMA = (
    (0, "id", "INTEGER", 1, None, 1),
    (1, "date", "DATE", 1, None, 0),
    (2, "override_type", "VARCHAR(14)", 1, None, 0),
    (3, "open_time_local", "TIME", 0, None, 0),
    (4, "close_time_local", "TIME", 0, None, 0),
    (5, "note", "VARCHAR(255)", 0, None, 0),
    (6, "created_by_user_id", "INTEGER", 0, None, 0),
    (7, "created_at", "DATETIME", 1, "CURRENT_TIMESTAMP", 0),
    (8, "updated_at", "DATETIME", 0, None, 0),
)
# Independent local fixture and literal oracle; insertion/PK/date orders differ.
_SEED_ROWS = (
    {"id": 22, "date": date(2026, 10, 24),
     "override_type": MarketScheduleOverrideType.CUSTOM_HOURS,
     "open_time_local": time(10, 15, 30, 123456),
     "close_time_local": time(16, 45, 59, 654321), "note": "custom-hours",
     "created_by_user_id": 7, "created_at": datetime(2026, 9, 10, 11, 12, 13, 123456),
     "updated_at": datetime(2026, 9, 12, 4, 5, 6, 654321)},
    {"id": 33, "date": date(2026, 10, 20),
     "override_type": MarketScheduleOverrideType.CLOSED_ALL_DAY,
     "open_time_local": None, "close_time_local": None, "note": None,
     "created_by_user_id": None, "created_at": datetime(2026, 9, 11, 5, 6, 7, 8),
     "updated_at": None},
    {"id": 11, "date": date(2026, 10, 22),
     "override_type": MarketScheduleOverrideType.OPEN_ALL_DAY,
     "open_time_local": None, "close_time_local": None, "note": "all-day",
     "created_by_user_id": 9, "created_at": datetime(2026, 9, 9, 22, 0, 1, 2),
     "updated_at": datetime(2026, 9, 14, 0, 1, 2, 3)},
)
_EXPECTED_PERSISTED_ROWS = (
    (11, "2026-10-22", "open_all_day", None, None, "all-day", 9,
     "2026-09-09 22:00:01.000002", "2026-09-14 00:01:02.000003"),
    (22, "2026-10-24", "custom_hours", "10:15:30.123456", "16:45:59.654321",
     "custom-hours", 7, "2026-09-10 11:12:13.123456", "2026-09-12 04:05:06.654321"),
    (33, "2026-10-20", "closed_all_day", None, None, None, None,
     "2026-09-11 05:06:07.000008", None),
)


class _AllFailureSentinel(RuntimeError):
    """The only intentionally raised product-boundary failure in this scenario."""


class MarketOverridesAllFailureSQLiteCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _snapshot(self, engine):
        self.assertEqual(inspect(engine).get_table_names(), ["market_schedule_overrides"])
        with engine.connect() as connection:
            schema = tuple(map(tuple, connection.exec_driver_sql(
                'PRAGMA table_info("market_schedule_overrides")').all()))
            ddl = tuple(map(tuple, connection.exec_driver_sql(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE tbl_name='market_schedule_overrides' ORDER BY type,name").all()))
            result = connection.exec_driver_sql(_SNAPSHOT_SQL)
            self.assertEqual(tuple(result.keys()), _COLUMNS)
            rows = tuple(map(tuple, result.all()))
        self.assertEqual(schema, _EXPECTED_SCHEMA)
        self.assertEqual(tuple(MarketScheduleOverride.__table__.columns.keys()), _COLUMNS)
        self.assertEqual(rows, _EXPECTED_PERSISTED_ROWS)
        self.assertEqual(tuple((row[0], row[1]) for row in ddl), (
            ("index", "ix_market_schedule_overrides_created_by_user_id"),
            ("index", "ix_market_schedule_overrides_id"),
            ("index", "ix_market_schedule_overrides_override_type"),
            ("index", "ux_market_schedule_overrides_date"),
            ("table", "market_schedule_overrides"),
        ))
        self.assertTrue(all(row[2] == "market_schedule_overrides" and row[3] for row in ddl))
        return schema, ddl, rows

    async def test_all_failure_propagates_after_real_scalars_without_downstream_work(self):
        engine = create_engine("sqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False, "cached_statements": 0})
        real_dispose = engine.dispose
        disposed = []

        def observe_dispose(*args, **kwargs):
            value = real_dispose(*args, **kwargs)
            disposed.append((args, kwargs))
            return value

        dispose_patch = patch.object(engine, "dispose", observe_dispose)
        dispose_patch.start()
        try:
            table = MarketScheduleOverride.__table__
            indexes = sorted(table.indexes, key=lambda index: index.name)
            definitions = {}
            for index in indexes:
                definition = str(CreateIndex(index).compile(dialect=engine.dialect))
                if index.name in definitions:
                    self.assertEqual(definition, definitions[index.name])
                definitions[index.name] = definition
            with engine.begin() as connection:
                connection.execute(CreateTable(table))
                for index in indexes:
                    connection.execute(CreateIndex(index, if_not_exists=True))
            self.assertEqual([row["id"] for row in _SEED_ROWS], [22, 33, 11])
            self.assertEqual([row[0] for row in _EXPECTED_PERSISTED_ROWS], [11, 22, 33])
            self.assertEqual([row["id"] for row in sorted(_SEED_ROWS,
                key=lambda row: row["date"])], [33, 11, 22])
            async with real_async_session(engine) as seed_db:
                for row in _SEED_ROWS:
                    seed_db.add(MarketScheduleOverride(**row))
                    await seed_db.flush()
                await seed_db.commit()
            before = self._snapshot(engine)
            with engine.connect() as connection:
                sqlite_conn = connection.connection.driver_connection
            self.assertIsInstance(sqlite_conn, sqlite3.Connection)

            real_close = AsyncSession.close
            close_started, close_completed = [], []

            async def observe_close(session_self, *args, **kwargs):
                close_started.append((session_self, args, kwargs))
                value = await real_close(session_self, *args, **kwargs)
                close_completed.append(session_self)
                return value

            try:
                with patch.object(AsyncSession, "close", observe_close):
                    async with real_async_session(engine) as db:
                        self.assertIsInstance(db, AsyncSession)
                        self.assertEqual((tuple(db.new), tuple(db.dirty), tuple(db.deleted)), ((), (), ()))
                        sentinel = _AllFailureSentinel("real ScalarResult.all synchronous failure")
                        trace, selected, ascending, ordered, results, scalar_results = [], [], [], [], [], []
                        scalars_calls, all_calls, serialized, downstream = [], [], [], []
                        autoflushes, sync_flushes, physical_sql, reads, selects = [], [], [], [], []
                        denied, forbidden, restorations, listeners, cleared = [], [], [], [], []
                        in_execute = autoflush_active = False
                        real_select = router.select
                        real_asc = type(MarketScheduleOverride.date).asc
                        real_order = Select.order_by
                        real_execute = db.execute
                        real_serializer = router._serialize_market_schedule_override
                        real_all = ScalarResult.all
                        real_autoflush = db.sync_session._autoflush
                        real_flush = db.sync_session.flush

                        def deny(*args, **kwargs):
                            forbidden.append((args, kwargs))
                            self.fail("Forbidden handler mutation or transaction")

                        def observe_autoflush(*args, **kwargs):
                            nonlocal autoflush_active
                            if (not in_execute or autoflush_active or autoflushes or physical_sql
                                    or args != () or kwargs != {}):
                                deny(*args, **kwargs)
                            autoflushes.append((args, kwargs)); trace.append("autoflush")
                            autoflush_active = True
                            try:
                                return real_autoflush(*args, **kwargs)
                            finally:
                                autoflush_active = False

                        def guard_sync_flush(*args, **kwargs):
                            if (not in_execute or not autoflush_active or sync_flushes or physical_sql
                                    or args != () or kwargs != {} or tuple(db.sync_session.new)
                                    or tuple(db.sync_session.dirty) or tuple(db.sync_session.deleted)
                                    or not db.sync_session._is_clean()):
                                deny(*args, **kwargs)
                            sync_flushes.append((args, kwargs)); trace.append("sync_flush:noop")
                            return real_flush(*args, **kwargs)

                        def authorize(action, arg1, arg2, database, trigger):
                            if action == sqlite3.SQLITE_SELECT:
                                selects.append((arg1, arg2, database, trigger))
                                return sqlite3.SQLITE_OK
                            if (action == sqlite3.SQLITE_READ and arg1 == "market_schedule_overrides"
                                    and arg2 in _COLUMNS and database == "main" and trigger is None):
                                reads.append(arg2)
                                return sqlite3.SQLITE_OK
                            denied.append((action, arg1, arg2, database, trigger))
                            return sqlite3.SQLITE_DENY

                        def observe_sql(connection, cursor, statement, parameters, context, executemany):
                            self.assertEqual(len(physical_sql), 0)
                            self.assertEqual(len(ordered), 1)
                            self.assertIs(context.compiled.statement, ordered[0])
                            self.assertTrue(context.compiled.statement.is_select)
                            self.assertFalse(executemany)
                            self.assertEqual(parameters, ())
                            self.assertEqual(" ".join(statement.split()), _EXPECTED_PHYSICAL_SELECT)
                            physical_sql.append((statement, parameters)); trace.append("sql")

                        def observe_select(*args, **kwargs):
                            self.assertEqual(len(selected), 0)
                            self.assertEqual(len(args), 1); self.assertIs(args[0], MarketScheduleOverride)
                            self.assertEqual(kwargs, {})
                            trace.append("select")
                            statement = real_select(*args, **kwargs)
                            self.assertIsInstance(statement, Select)
                            self.assertEqual(tuple(statement._order_by_clauses), ())
                            self.assertIs(statement.column_descriptions[0]["entity"], MarketScheduleOverride)
                            selected.append(statement)
                            return statement

                        def observe_asc(attribute, *args, **kwargs):
                            self.assertEqual(len(ascending), 0)
                            self.assertIs(attribute, MarketScheduleOverride.date)
                            self.assertEqual((args, kwargs), ((), {})); trace.append("asc")
                            expression = real_asc(attribute, *args, **kwargs)
                            self.assertIs(expression.modifier, operators.asc_op)
                            self.assertTrue(expression.element.compare(MarketScheduleOverride.__table__.c.date))
                            ascending.append(expression)
                            return expression

                        def observe_order(statement, *args, **kwargs):
                            self.assertEqual(len(ordered), 0); self.assertIs(statement, selected[0])
                            self.assertEqual(len(args), 1); self.assertIs(args[0], ascending[0])
                            self.assertEqual(kwargs, {}); trace.append("order_by")
                            value = real_order(statement, *args, **kwargs)
                            self.assertIsNot(value, statement)
                            self.assertEqual(len(value._order_by_clauses), 1)
                            self.assertIs(tuple(value._order_by_clauses)[0], ascending[0])
                            ordered.append(value)
                            return value

                        def reject_downstream(*args, **kwargs):
                            downstream.append((args, kwargs))
                            self.fail("Unexpected result iteration/materialization after failure")

                        def reject_serializer(*args, **kwargs):
                            serialized.append((args, kwargs))
                            self.fail("Serializer must not run when all() fails")

                        def observe_all(actual_scalars, *args, **kwargs):
                            self.assertEqual(len(all_calls), 0)
                            self.assertEqual(len(scalar_results), 1)
                            self.assertIs(actual_scalars, scalar_results[0])
                            self.assertIsInstance(actual_scalars, ScalarResult)
                            self.assertEqual((args, kwargs), ((), {}))
                            all_calls.append((actual_scalars, args, kwargs))
                            trace.append("all:raise")
                            raise sentinel

                        stack = ExitStack()
                        try:
                            def install(owner, name, replacement):
                                previous = getattr(owner, name)
                                restorations.append((owner, name, previous))
                                stack.enter_context(patch.object(owner, name, replacement))

                            async def observe_execute(*args, **kwargs):
                                nonlocal in_execute
                                self.assertEqual(len(results), 0); self.assertEqual(len(args), 1)
                                self.assertIs(args[0], ordered[0]); self.assertEqual(kwargs, {})
                                trace.append("execute:await"); in_execute = True
                                try:
                                    result = await real_execute(*args, **kwargs)
                                finally:
                                    in_execute = False
                                self.assertIsInstance(result, Result)
                                results.append(result); trace.append("execute:return")
                                real_scalars = type(result).scalars

                                def observe_scalars(actual_result, *args, **kwargs):
                                    self.assertEqual(len(scalars_calls), 0)
                                    self.assertIs(actual_result, result)
                                    self.assertEqual((args, kwargs), ((), {}))
                                    scalars_calls.append((actual_result, args, kwargs))
                                    value = real_scalars(actual_result, *args, **kwargs)
                                    self.assertIsInstance(value, ScalarResult)
                                    scalar_results.append(value); trace.append("scalars:return")
                                    return value

                                install(type(result), "scalars", observe_scalars)
                                install(type(result), "all", reject_downstream)
                                return result

                            execute_spy = AsyncMock(side_effect=observe_execute)
                            install(router, "select", observe_select)
                            install(type(MarketScheduleOverride.date), "asc", observe_asc)
                            install(Select, "order_by", observe_order)
                            install(db, "execute", execute_spy)
                            install(ScalarResult, "all", observe_all)
                            install(ScalarResult, "__iter__", reject_downstream)
                            install(router, "_serialize_market_schedule_override", reject_serializer)
                            for owner in (db, db.sync_session):
                                for name in ("add", "add_all", "delete", "merge", "flush", "commit",
                                             "rollback", "refresh", "begin", "begin_nested"):
                                    install(owner, name, guard_sync_flush
                                        if owner is db.sync_session and name == "flush" else deny)
                            install(db.sync_session, "_autoflush", observe_autoflush)
                            event.listen(db.sync_session, "before_flush", deny)
                            listeners.append((db.sync_session, "before_flush", deny))
                            event.listen(engine, "before_cursor_execute", observe_sql)
                            listeners.append((engine, "before_cursor_execute", observe_sql))
                            for name in ("commit", "rollback", "savepoint", "rollback_savepoint",
                                         "release_savepoint", "begin_twophase", "prepare_twophase",
                                         "commit_twophase", "rollback_twophase"):
                                event.listen(engine, name, deny)
                                listeners.append((engine, name, deny))
                            sqlite_conn.set_authorizer(authorize)
                            with self.assertRaises(_AllFailureSentinel) as caught:
                                await router.list_market_overrides(db=db)
                            self.assertIs(caught.exception, sentinel)
                            execute_spy.assert_called_once_with(ordered[0])
                            execute_spy.assert_awaited_once_with(ordered[0])
                            self.assertEqual(trace, ["select", "asc", "order_by", "execute:await",
                                "autoflush", "sync_flush:noop", "sql", "execute:return",
                                "scalars:return", "all:raise"])
                            self.assertEqual(tuple(map(len, (selected, ascending, ordered, results,
                                scalar_results, autoflushes, sync_flushes, physical_sql))), (1,) * 8)
                            self.assertEqual(scalars_calls, [(results[0], (), {})])
                            self.assertEqual(all_calls, [(scalar_results[0], (), {})])
                            self.assertEqual(selects, [(None, None, None, None)])
                            self.assertEqual(set(reads), set(_COLUMNS))
                            self.assertEqual((serialized, downstream, denied, forbidden), ([], [], [], []))
                            self.assertEqual((tuple(db.new), tuple(db.dirty), tuple(db.deleted)), ((), (), ()))
                        finally:
                            try:
                                sqlite_conn.set_authorizer(None)
                                cleared.append(True)
                            finally:
                                try:
                                    for owner, name, listener in reversed(listeners):
                                        event.remove(owner, name, listener)
                                finally:
                                    stack.close()
                            self.assertEqual(cleared, [True])
                            for owner, name, previous in restorations:
                                restored = getattr(owner, name)
                                if hasattr(previous, "__self__"):
                                    self.assertIs(restored.__self__, previous.__self__)
                                    self.assertIs(restored.__func__, previous.__func__)
                                else:
                                    self.assertIs(restored, previous)
                            self.assertIs(router.select, real_select)
                            self.assertIs(Select.order_by, real_order)
                            self.assertIs(type(MarketScheduleOverride.date).asc, real_asc)
                            self.assertIs(ScalarResult.all, real_all)
                            self.assertIs(router._serialize_market_schedule_override, real_serializer)
                            self.assertIs(db.sync_session._autoflush.__func__, real_autoflush.__func__)
                            self.assertIs(db.sync_session.flush.__func__, real_flush.__func__)
                            for owner, name, listener in listeners:
                                self.assertFalse(event.contains(owner, name, listener))
                    self.assertEqual(close_started, [(db, (), {})])
                    self.assertEqual(close_completed, [db])
            finally:
                self.assertIs(AsyncSession.close, real_close)
            after = self._snapshot(engine)
            self.assertEqual(after, before)
        finally:
            try:
                engine.dispose()
                self.assertEqual(disposed, [((), {})])
            finally:
                dispose_patch.stop()
                self.assertIs(engine.dispose.__self__, real_dispose.__self__)
                self.assertIs(engine.dispose.__func__, real_dispose.__func__)
