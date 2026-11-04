from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from collections.abc import Mapping
from contextlib import ExitStack
import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

import api.routers.sync as sync_router
import core.security as core_security
import models.change_log
from models.change_log import ChangeLog
import tests.test_p1_00_characterization_network_isolation as network_isolation


OBSERVABILITY_API_KEY_SENTINEL = "sync-health-grouped-rows-all-test-key"
OBSERVABILITY_API_KEY_HEADER_NAME = "X-Observability-Api-Key"
SYNC_HEALTH_ENDPOINT_PATH = "/api/sync/health"
REMOTE_CLIENT_HOST = "198.51.100.10"
SUMMARY_RESULT_ROW = (3, None, 1)
GROUPED_ROWS_ALL_FAILURE_MESSAGE = (
    "sync-health-grouped-rows-all-consumption-failure"
)
DB_MUTATOR_NAMES = (
    "commit",
    "rollback",
    "flush",
    "delete",
    "refresh",
    "add",
    "add_all",
)


class SyncHealthGroupedRowConsumptionFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_grouped_rows_all_exception_propagates_after_second_execute_before_observability(
        self,
    ) -> None:
        orig_get_sync_health = sync_router.get_sync_health
        orig_require_observability_key = sync_router._require_observability_key
        orig_constant_time_secret_equals = sync_router.constant_time_secret_equals
        orig_core_constant_time_secret_equals = (
            core_security.constant_time_secret_equals
        )
        orig_observability_api_key = sync_router.settings.observability_api_key
        orig_get_redis_client = sync_router.get_redis_client
        orig_age_seconds = sync_router._age_seconds
        orig_record_sync_health = sync_router.record_sync_health
        orig_publication_observability_summary = (
            sync_router.publication_observability_summary
        )
        orig_record_offer_publication_health = (
            sync_router.record_offer_publication_health
        )
        orig_overtime_observability_summary = (
            sync_router.overtime_observability_summary
        )
        orig_record_overtime_reconciliation_health = (
            sync_router.record_overtime_reconciliation_health
        )
        orig_audit_log = sync_router.audit_log
        orig_logger = sync_router.logger
        orig_select = sync_router.select
        orig_sa_func = sync_router.sa_func
        orig_change_log = sync_router.ChangeLog

        self.assertTrue(inspect.iscoroutinefunction(orig_get_sync_health))
        handler_sig = inspect.signature(orig_get_sync_health)
        self.assertEqual(list(handler_sig.parameters), ["request", "db"])
        self.assertEqual(
            sync_router.OBSERVABILITY_API_KEY_HEADER,
            OBSERVABILITY_API_KEY_HEADER_NAME,
        )

        request = SimpleNamespace(
            headers={
                OBSERVABILITY_API_KEY_HEADER_NAME: OBSERVABILITY_API_KEY_SENTINEL
            },
            url=SimpleNamespace(path=SYNC_HEALTH_ENDPOINT_PATH),
            client=SimpleNamespace(host=REMOTE_CLIENT_HOST),
        )
        self.assertIs(sync_router._is_loopback_client(REMOTE_CLIENT_HOST), False)
        self.assertIs(sync_router._is_loopback_sync_request(request), False)

        events: list[str] = []
        guard_calls: list[tuple[tuple[object, ...], dict[str, object], object]] = []
        execute_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        execute_returned_results: list[object] = []
        summary_one_calls: list[
            tuple[tuple[object, ...], dict[str, object], tuple[int, None, int]]
        ] = []
        table_all_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        table_all_returned_values: list[object] = []
        handler_locals_at_table_all: dict[str, object] = {}
        handler_frame_captured = False

        def spy_require_observability_key(*args: object, **kwargs: object) -> object:
            guard_result = orig_require_observability_key(*args, **kwargs)
            events.append("guard")
            guard_calls.append((args, dict(kwargs), guard_result))
            return guard_result

        def _spy_summary_one(*args: object, **kwargs: object) -> tuple[int, None, int]:
            events.append("summary.one")
            summary_one_calls.append((args, dict(kwargs), SUMMARY_RESULT_ROW))
            return SUMMARY_RESULT_ROW

        summary_one_mock = MagicMock(return_value=None, side_effect=_spy_summary_one)
        summary_result = SimpleNamespace(one=summary_one_mock)
        self.assertIs(type(summary_result), SimpleNamespace)
        self.assertEqual(list(vars(summary_result).keys()), ["one"])
        self.assertEqual(set(vars(summary_result).keys()), {"one"})
        self.assertFalse(hasattr(summary_result, "all"))
        self.assertIs(summary_result.one, summary_one_mock)
        self.assertIsInstance(summary_result.one, MagicMock)
        self.assertNotIsInstance(summary_result.one, AsyncMock)
        self.assertFalse(inspect.iscoroutinefunction(_spy_summary_one))
        self.assertFalse(inspect.iscoroutinefunction(summary_result.one))

        table_all_error = RuntimeError(GROUPED_ROWS_ALL_FAILURE_MESSAGE)

        def _raise_grouped_rows_all_failure() -> object:
            raise table_all_error

        def _spy_table_all(*args: object, **kwargs: object) -> object:
            nonlocal handler_frame_captured
            events.append("table.all")
            table_all_calls.append((args, dict(kwargs)))
            frame = inspect.currentframe()
            try:
                cursor = frame
                while cursor is not None:
                    if cursor.f_code is orig_get_sync_health.__code__:
                        handler_frame_captured = True
                        handler_locals_at_table_all.update(cursor.f_locals)
                        break
                    cursor = cursor.f_back
                del cursor
            finally:
                del frame
            row_sequence = _raise_grouped_rows_all_failure()
            table_all_returned_values.append(row_sequence)
            return row_sequence

        table_all_mock = MagicMock(return_value=None, side_effect=_spy_table_all)
        table_result = SimpleNamespace(all=table_all_mock)
        self.assertIs(type(table_result), SimpleNamespace)
        self.assertEqual(list(vars(table_result).keys()), ["all"])
        self.assertEqual(set(vars(table_result).keys()), {"all"})
        self.assertFalse(hasattr(table_result, "one"))
        self.assertIs(table_result.all, table_all_mock)
        self.assertIsInstance(table_result.all, MagicMock)
        self.assertNotIsInstance(table_result.all, AsyncMock)
        self.assertFalse(inspect.iscoroutinefunction(_spy_table_all))
        self.assertFalse(inspect.iscoroutinefunction(table_result.all))
        self.assertIsNone(table_all_mock.return_value)

        async def _execute_summary_then_grouped_table(
            *args: object,
            **kwargs: object,
        ) -> object:
            call_index = len(execute_calls) + 1
            execute_calls.append((args, dict(kwargs)))
            events.append(f"execute{call_index}")
            if call_index == 1:
                execute_returned_results.append(summary_result)
                return summary_result
            if call_index == 2:
                execute_returned_results.append(table_result)
                return table_result
            raise AssertionError(f"Unexpected extra db.execute call #{call_index}")

        db_new: set[object] = set()
        db_dirty: set[object] = set()
        db_deleted: set[object] = set()
        before_db_new = set(db_new)
        before_db_dirty = set(db_dirty)
        before_db_deleted = set(db_deleted)

        mutator_traps = {
            name: AsyncMock(
                side_effect=AssertionError(f"Unexpected fake_db.{name} invocation")
            )
            for name in DB_MUTATOR_NAMES
        }
        fake_db = SimpleNamespace(
            execute=AsyncMock(
                return_value=None,
                side_effect=_execute_summary_then_grouped_table,
            ),
            new=db_new,
            dirty=db_dirty,
            deleted=db_deleted,
            **mutator_traps,
        )

        get_redis_spy = MagicMock(
            side_effect=AssertionError("Unexpected get_redis_client call")
        )
        age_seconds_spy = MagicMock(
            side_effect=AssertionError("Unexpected _age_seconds call")
        )
        record_sync_health_spy = MagicMock(
            side_effect=AssertionError("Unexpected record_sync_health call")
        )
        publication_summary_spy = AsyncMock(
            side_effect=AssertionError(
                "Unexpected publication_observability_summary call"
            )
        )
        record_publication_health_spy = MagicMock(
            side_effect=AssertionError(
                "Unexpected record_offer_publication_health call"
            )
        )
        overtime_summary_spy = AsyncMock(
            side_effect=AssertionError(
                "Unexpected overtime_observability_summary call"
            )
        )
        record_overtime_health_spy = MagicMock(
            side_effect=AssertionError(
                "Unexpected record_overtime_reconciliation_health call"
            )
        )
        audit_log_spy = MagicMock(
            side_effect=AssertionError("Unexpected audit_log call")
        )
        logger_spy = SimpleNamespace(
            warning=MagicMock(
                side_effect=AssertionError("Unexpected logger.warning call")
            ),
            info=MagicMock(side_effect=AssertionError("Unexpected logger.info call")),
            error=MagicMock(
                side_effect=AssertionError("Unexpected logger.error call")
            ),
            exception=MagicMock(
                side_effect=AssertionError("Unexpected logger.exception call")
            ),
            debug=MagicMock(
                side_effect=AssertionError("Unexpected logger.debug call")
            ),
        )

        fence_state = install_p1_00_network_fence()
        self.assertIs(fence_state, network_isolation._FENCE)
        self.assertIs(fence_state["installed"], True)
        before_denied_connects = list(fence_state["denied_connects"])
        before_live_redis_constructs = list(fence_state["live_redis_constructs"])

        unreturned_sentinel = object()
        returned_value: object = unreturned_sentinel

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    sync_router.settings,
                    "observability_api_key",
                    OBSERVABILITY_API_KEY_SENTINEL,
                )
            )
            stack.enter_context(
                patch.object(
                    sync_router,
                    "_require_observability_key",
                    new=spy_require_observability_key,
                )
            )
            stack.enter_context(
                patch.object(sync_router, "get_redis_client", new=get_redis_spy)
            )
            stack.enter_context(
                patch.object(sync_router, "_age_seconds", new=age_seconds_spy)
            )
            stack.enter_context(
                patch.object(
                    sync_router,
                    "record_sync_health",
                    new=record_sync_health_spy,
                )
            )
            stack.enter_context(
                patch.object(
                    sync_router,
                    "publication_observability_summary",
                    new=publication_summary_spy,
                )
            )
            stack.enter_context(
                patch.object(
                    sync_router,
                    "record_offer_publication_health",
                    new=record_publication_health_spy,
                )
            )
            stack.enter_context(
                patch.object(
                    sync_router,
                    "overtime_observability_summary",
                    new=overtime_summary_spy,
                )
            )
            stack.enter_context(
                patch.object(
                    sync_router,
                    "record_overtime_reconciliation_health",
                    new=record_overtime_health_spy,
                )
            )
            stack.enter_context(
                patch.object(sync_router, "audit_log", new=audit_log_spy)
            )
            stack.enter_context(
                patch.object(sync_router, "logger", new=logger_spy)
            )

            self.assertIs(sync_router.get_sync_health, orig_get_sync_health)
            self.assertIs(
                sync_router.constant_time_secret_equals,
                orig_constant_time_secret_equals,
            )
            self.assertIs(
                core_security.constant_time_secret_equals,
                orig_core_constant_time_secret_equals,
            )
            self.assertIs(
                sync_router.constant_time_secret_equals,
                core_security.constant_time_secret_equals,
            )
            self.assertIs(sync_router.select, orig_select)
            self.assertIs(sync_router.sa_func, orig_sa_func)
            self.assertIs(sync_router.ChangeLog, orig_change_log)
            self.assertIs(sync_router.ChangeLog, ChangeLog)
            self.assertIs(sync_router.ChangeLog, models.change_log.ChangeLog)

            with self.assertRaises(RuntimeError) as exc_ctx:
                returned_value = await sync_router.get_sync_health(
                    request=request,
                    db=fake_db,
                )

        exc = exc_ctx.exception
        self.assertIs(exc, table_all_error)
        self.assertIs(type(exc), RuntimeError)
        self.assertEqual(exc.args, (GROUPED_ROWS_ALL_FAILURE_MESSAGE,))
        self.assertEqual(str(exc), GROUPED_ROWS_ALL_FAILURE_MESSAGE)
        self.assertIsNone(exc.__cause__)

        self.assertIs(returned_value, unreturned_sentinel)
        self.assertNotIsInstance(returned_value, Mapping)
        self.assertNotIsInstance(returned_value, tuple)
        self.assertNotIsInstance(returned_value, dict)

        self.assertEqual(
            events,
            ["guard", "execute1", "summary.one", "execute2", "table.all"],
        )

        self.assertEqual(len(guard_calls), 1)
        guard_args, guard_kwargs, guard_result = guard_calls[0]
        self.assertEqual(len(guard_args), 1)
        self.assertIs(guard_args[0], request)
        self.assertEqual(guard_kwargs, {})
        self.assertIsNone(guard_result)
        self.assertEqual(
            request.headers,
            {OBSERVABILITY_API_KEY_HEADER_NAME: OBSERVABILITY_API_KEY_SENTINEL},
        )
        self.assertEqual(request.url.path, SYNC_HEALTH_ENDPOINT_PATH)
        self.assertEqual(request.client.host, REMOTE_CLIENT_HOST)

        self.assertEqual(len(execute_returned_results), 2)
        self.assertIs(execute_returned_results[0], summary_result)
        self.assertIs(execute_returned_results[1], table_result)
        self.assertIsNot(execute_returned_results[0], execute_returned_results[1])

        self.assertIs(type(summary_result), SimpleNamespace)
        self.assertEqual(list(vars(summary_result).keys()), ["one"])
        self.assertEqual(set(vars(summary_result).keys()), {"one"})
        self.assertFalse(hasattr(summary_result, "all"))
        self.assertIsInstance(summary_result.one, MagicMock)
        self.assertNotIsInstance(summary_result.one, AsyncMock)
        self.assertFalse(inspect.iscoroutinefunction(summary_result.one))
        self.assertFalse(hasattr(type(summary_one_mock), "await_count"))
        self.assertNotIn("await_count", vars(summary_one_mock))

        self.assertEqual(summary_one_mock.call_count, 1)
        self.assertEqual(len(summary_one_mock.call_args_list), 1)
        one_mock_call = summary_one_mock.call_args_list[0]
        self.assertEqual(one_mock_call.args, ())
        self.assertEqual(len(one_mock_call.args), 0)
        self.assertEqual(one_mock_call.kwargs, {})

        self.assertEqual(len(summary_one_calls), 1)
        one_args, one_kwargs, one_returned = summary_one_calls[0]
        self.assertEqual(one_args, ())
        self.assertEqual(one_kwargs, {})
        self.assertFalse(inspect.isawaitable(one_returned))
        self.assertIs(one_returned, SUMMARY_RESULT_ROW)
        self.assertIs(type(one_returned), tuple)
        self.assertEqual(one_returned, (3, None, 1))
        unsynced_count, oldest_unsynced_at, quarantined_count = one_returned
        self.assertEqual(unsynced_count, 3)
        self.assertIsNone(oldest_unsynced_at)
        self.assertEqual(quarantined_count, 1)

        self.assertTrue(handler_frame_captured)
        self.assertEqual(handler_locals_at_table_all["unsynced_count"], 3)
        self.assertIn("oldest_unsynced_at", handler_locals_at_table_all)
        self.assertIsNone(handler_locals_at_table_all["oldest_unsynced_at"])
        self.assertEqual(handler_locals_at_table_all["quarantined_count"], 1)
        self.assertIs(handler_locals_at_table_all["result"], summary_result)
        self.assertIs(handler_locals_at_table_all["table_rows"], table_result)
        self.assertNotIn("unsynced_by_table", handler_locals_at_table_all)
        self.assertNotIn("outbound_queue", handler_locals_at_table_all)
        self.assertNotIn("redis_ok", handler_locals_at_table_all)
        self.assertNotIn("payload", handler_locals_at_table_all)

        self.assertIs(type(table_result), SimpleNamespace)
        self.assertEqual(list(vars(table_result).keys()), ["all"])
        self.assertEqual(set(vars(table_result).keys()), {"all"})
        self.assertFalse(hasattr(table_result, "one"))
        self.assertIsInstance(table_result.all, MagicMock)
        self.assertNotIsInstance(table_result.all, AsyncMock)
        self.assertFalse(inspect.iscoroutinefunction(table_result.all))
        self.assertFalse(hasattr(type(table_all_mock), "await_count"))
        self.assertNotIn("await_count", vars(table_all_mock))
        self.assertIsNone(table_all_mock.return_value)

        self.assertEqual(table_all_mock.call_count, 1)
        self.assertEqual(len(table_all_mock.call_args_list), 1)
        all_mock_call = table_all_mock.call_args_list[0]
        self.assertEqual(all_mock_call.args, ())
        self.assertEqual(len(all_mock_call.args), 0)
        self.assertEqual(all_mock_call.kwargs, {})

        self.assertEqual(len(table_all_calls), 1)
        all_args, all_kwargs = table_all_calls[0]
        self.assertEqual(all_args, ())
        self.assertEqual(all_kwargs, {})
        self.assertEqual(table_all_returned_values, [])
        self.assertEqual(len(table_all_returned_values), 0)

        self.assertIs(type(fake_db), SimpleNamespace)
        self.assertIsInstance(fake_db.execute, AsyncMock)
        self.assertIsNone(fake_db.execute.return_value)
        self.assertEqual(fake_db.execute.call_count, 2)
        self.assertEqual(fake_db.execute.await_count, 2)
        self.assertEqual(len(fake_db.execute.call_args_list), 2)
        self.assertEqual(len(fake_db.execute.await_args_list), 2)
        self.assertEqual(len(execute_calls), 2)

        for idx in (0, 1):
            call_entry = fake_db.execute.call_args_list[idx]
            await_entry = fake_db.execute.await_args_list[idx]
            exec_args, exec_kwargs = execute_calls[idx]
            self.assertEqual(len(call_entry.args), 1)
            self.assertEqual(call_entry.kwargs, {})
            self.assertEqual(len(await_entry.args), 1)
            self.assertEqual(await_entry.kwargs, {})
            self.assertEqual(len(exec_args), 1)
            self.assertEqual(exec_kwargs, {})
            self.assertIsInstance(await_entry.args[0], Select)
            self.assertIs(call_entry.args[0], await_entry.args[0])
            self.assertIs(exec_args[0], await_entry.args[0])

        summary_stmt = fake_db.execute.await_args_list[0].args[0]
        table_stmt = fake_db.execute.await_args_list[1].args[0]
        self.assertIsNot(summary_stmt, table_stmt)

        pg_dialect = postgresql.dialect()

        self.assertIsInstance(summary_stmt, Select)
        self.assertIs(summary_stmt.is_select, True)
        self.assertEqual(len(summary_stmt.selected_columns), 3)
        summary_froms = summary_stmt.get_final_froms()
        self.assertEqual(len(summary_froms), 1)
        self.assertEqual(
            [from_clause.name for from_clause in summary_froms],
            ["change_log"],
        )

        summary_compiled = summary_stmt.compile(dialect=pg_dialect)
        self.assertEqual(dict(summary_compiled.params), {})
        summary_sql_case = " ".join(str(summary_compiled).split())
        summary_sql = summary_sql_case.lower()

        self.assertTrue(summary_sql.startswith("select "))
        self.assertIn(
            " from change_log where change_log.synced = false",
            summary_sql,
        )
        self.assertIn("where change_log.synced = false", summary_sql)
        self.assertTrue(
            summary_sql.endswith("from change_log where change_log.synced = false")
        )
        self.assertIn("count(change_log.id)", summary_sql_case)
        self.assertIn("min(change_log.created_at)", summary_sql_case)
        self.assertIn(
            "count(change_log.id) FILTER (WHERE change_log.quarantined_at IS NOT NULL)",
            summary_sql_case,
        )
        self.assertIn("count(change_log.id)", summary_sql)
        self.assertIn("min(change_log.created_at)", summary_sql)
        self.assertIn(
            "filter (where change_log.quarantined_at is not null)",
            summary_sql,
        )
        self.assertNotIn(" group by ", summary_sql)
        self.assertNotIn(" order by ", summary_sql)
        self.assertNotIn(" join ", summary_sql)
        self.assertNotIn("change_log.table_name", summary_sql)

        self.assertIsInstance(table_stmt, Select)
        self.assertIs(table_stmt.is_select, True)
        self.assertEqual(len(table_stmt.selected_columns), 2)
        table_froms = table_stmt.get_final_froms()
        self.assertEqual(len(table_froms), 1)
        self.assertEqual(
            [from_clause.name for from_clause in table_froms],
            ["change_log"],
        )

        table_compiled = table_stmt.compile(dialect=pg_dialect)
        self.assertEqual(dict(table_compiled.params), {})
        table_sql_case = " ".join(str(table_compiled).split())
        table_sql = table_sql_case.lower()

        self.assertNotEqual(summary_sql_case, table_sql_case)
        self.assertNotEqual(summary_sql, table_sql)
        self.assertTrue(table_sql.startswith("select "))
        self.assertIn("change_log.table_name", table_sql_case)
        self.assertIn("count(change_log.id)", table_sql_case)
        self.assertIn("GROUP BY change_log.table_name", table_sql_case)
        self.assertIn("ORDER BY change_log.table_name", table_sql_case)
        self.assertIn("change_log.table_name", table_sql)
        self.assertIn("count(change_log.id)", table_sql)
        self.assertLess(
            table_sql.index("change_log.table_name"),
            table_sql.index("count(change_log.id)"),
        )
        self.assertIn(
            " from change_log where change_log.synced = false",
            table_sql,
        )
        self.assertIn("where change_log.synced = false", table_sql)
        self.assertIn("group by change_log.table_name", table_sql)
        self.assertIn("order by change_log.table_name", table_sql)
        self.assertTrue(
            table_sql.endswith(
                "from change_log where change_log.synced = false "
                "group by change_log.table_name "
                "order by change_log.table_name"
            )
        )
        self.assertNotIn("min(change_log.created_at)", table_sql)
        self.assertNotIn("quarantined_at", table_sql)
        self.assertNotIn("filter (", table_sql)
        self.assertNotIn(" join ", table_sql)
        self.assertNotIn(" having ", table_sql)

        get_redis_spy.assert_not_called()
        self.assertEqual(get_redis_spy.call_count, 0)
        age_seconds_spy.assert_not_called()
        self.assertEqual(age_seconds_spy.call_count, 0)
        record_sync_health_spy.assert_not_called()
        self.assertEqual(record_sync_health_spy.call_count, 0)
        publication_summary_spy.assert_not_called()
        publication_summary_spy.assert_not_awaited()
        self.assertEqual(publication_summary_spy.call_count, 0)
        self.assertEqual(publication_summary_spy.await_count, 0)
        record_publication_health_spy.assert_not_called()
        self.assertEqual(record_publication_health_spy.call_count, 0)
        overtime_summary_spy.assert_not_called()
        overtime_summary_spy.assert_not_awaited()
        self.assertEqual(overtime_summary_spy.call_count, 0)
        self.assertEqual(overtime_summary_spy.await_count, 0)
        record_overtime_health_spy.assert_not_called()
        self.assertEqual(record_overtime_health_spy.call_count, 0)
        audit_log_spy.assert_not_called()
        self.assertEqual(audit_log_spy.call_count, 0)
        logger_spy.warning.assert_not_called()
        logger_spy.info.assert_not_called()
        logger_spy.error.assert_not_called()
        logger_spy.exception.assert_not_called()
        logger_spy.debug.assert_not_called()
        self.assertEqual(logger_spy.warning.call_count, 0)
        self.assertEqual(logger_spy.info.call_count, 0)
        self.assertEqual(logger_spy.error.call_count, 0)
        self.assertEqual(logger_spy.exception.call_count, 0)
        self.assertEqual(logger_spy.debug.call_count, 0)

        for mutator_name in DB_MUTATOR_NAMES:
            mutator = getattr(fake_db, mutator_name)
            self.assertIsInstance(mutator, AsyncMock)
            mutator.assert_not_called()
            mutator.assert_not_awaited()
            self.assertEqual(mutator.call_count, 0)
            self.assertEqual(mutator.await_count, 0)

        self.assertIs(fake_db.new, db_new)
        self.assertIs(fake_db.dirty, db_dirty)
        self.assertIs(fake_db.deleted, db_deleted)
        self.assertEqual(fake_db.new, before_db_new)
        self.assertEqual(fake_db.dirty, before_db_dirty)
        self.assertEqual(fake_db.deleted, before_db_deleted)
        self.assertEqual(len(fake_db.new) - len(before_db_new), 0)
        self.assertEqual(len(fake_db.dirty) - len(before_db_dirty), 0)
        self.assertEqual(len(fake_db.deleted) - len(before_db_deleted), 0)

        self.assertIs(fence_state, network_isolation._FENCE)
        self.assertIs(fence_state["installed"], True)
        self.assertEqual(fence_state["denied_connects"], before_denied_connects)
        self.assertEqual(
            len(fence_state["denied_connects"]) - len(before_denied_connects),
            0,
        )
        self.assertEqual(
            fence_state["live_redis_constructs"],
            before_live_redis_constructs,
        )
        self.assertEqual(
            len(fence_state["live_redis_constructs"])
            - len(before_live_redis_constructs),
            0,
        )

        self.assertIs(sync_router.get_sync_health, orig_get_sync_health)
        self.assertIs(
            sync_router._require_observability_key,
            orig_require_observability_key,
        )
        self.assertIs(
            sync_router.constant_time_secret_equals,
            orig_constant_time_secret_equals,
        )
        self.assertIs(
            core_security.constant_time_secret_equals,
            orig_core_constant_time_secret_equals,
        )
        self.assertIs(
            sync_router.constant_time_secret_equals,
            core_security.constant_time_secret_equals,
        )
        self.assertIs(
            sync_router.settings.observability_api_key,
            orig_observability_api_key,
        )
        self.assertIs(sync_router.get_redis_client, orig_get_redis_client)
        self.assertIs(sync_router._age_seconds, orig_age_seconds)
        self.assertIs(sync_router.record_sync_health, orig_record_sync_health)
        self.assertIs(
            sync_router.publication_observability_summary,
            orig_publication_observability_summary,
        )
        self.assertIs(
            sync_router.record_offer_publication_health,
            orig_record_offer_publication_health,
        )
        self.assertIs(
            sync_router.overtime_observability_summary,
            orig_overtime_observability_summary,
        )
        self.assertIs(
            sync_router.record_overtime_reconciliation_health,
            orig_record_overtime_reconciliation_health,
        )
        self.assertIs(sync_router.audit_log, orig_audit_log)
        self.assertIs(sync_router.logger, orig_logger)
        self.assertIs(sync_router.select, orig_select)
        self.assertIs(sync_router.sa_func, orig_sa_func)
        self.assertIs(sync_router.ChangeLog, orig_change_log)
        self.assertIs(sync_router.ChangeLog, ChangeLog)
        self.assertIs(sync_router.ChangeLog, models.change_log.ChangeLog)


if __name__ == "__main__":
    unittest.main()
