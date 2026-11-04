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
from models.change_log import ChangeLog
import tests.test_p1_00_characterization_network_isolation as network_isolation


OBSERVABILITY_API_KEY_SENTINEL = "sync-health-summary-one-test-key"
SYNC_HEALTH_ENDPOINT_PATH = "/api/sync/health"
REMOTE_CLIENT_HOST = "198.51.100.10"
SUMMARY_ONE_FAILURE_MESSAGE = "sync-health-summary-one-consumption-failure"
DB_MUTATOR_NAMES = (
    "commit",
    "rollback",
    "flush",
    "delete",
    "refresh",
    "add",
    "add_all",
)


class SyncHealthSummaryConsumptionFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_summary_one_exception_propagates_after_first_execute_before_group_query_and_redis(
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
        self.assertTrue(callable(orig_age_seconds))
        handler_sig = inspect.signature(orig_get_sync_health)
        self.assertEqual(list(handler_sig.parameters), ["request", "db"])

        request = SimpleNamespace(
            headers={"X-Observability-Api-Key": OBSERVABILITY_API_KEY_SENTINEL},
            url=SimpleNamespace(path=SYNC_HEALTH_ENDPOINT_PATH),
            client=SimpleNamespace(host=REMOTE_CLIENT_HOST),
        )
        self.assertEqual(
            sync_router.OBSERVABILITY_API_KEY_HEADER,
            "X-Observability-Api-Key",
        )
        self.assertIs(sync_router._is_loopback_sync_request(request), False)

        events: list[str] = []
        guard_calls: list[tuple[tuple[object, ...], dict[str, object], object]] = []
        execute_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        execute_returned_results: list[object] = []
        summary_one_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        summary_one_returned_values: list[object] = []

        def spy_require_observability_key(*args: object, **kwargs: object) -> object:
            guard_result = orig_require_observability_key(*args, **kwargs)
            events.append("guard")
            guard_calls.append((args, dict(kwargs), guard_result))
            return guard_result

        summary_one_error = RuntimeError(SUMMARY_ONE_FAILURE_MESSAGE)

        def _raise_summary_one_consumption_failure(
            *args: object,
            **kwargs: object,
        ) -> object:
            summary_one_calls.append((args, dict(kwargs)))
            events.append("summary.one")
            raise summary_one_error

        summary_one_spy = MagicMock(
            return_value=None,
            side_effect=_raise_summary_one_consumption_failure,
        )
        summary_result = SimpleNamespace(one=summary_one_spy)

        async def _execute_first_summary_only(
            *args: object,
            **kwargs: object,
        ) -> object:
            call_index = len(execute_calls) + 1
            execute_calls.append((args, dict(kwargs)))
            events.append(f"execute{call_index}")
            if call_index == 1:
                execute_returned_results.append(summary_result)
                return summary_result
            raise AssertionError(f"Unexpected extra db.execute call #{call_index}")

        db_new: set[object] = set()
        db_dirty: set[object] = set()
        db_deleted: set[object] = set()
        before_db_new = set(db_new)
        before_db_dirty = set(db_dirty)
        before_db_deleted = set(db_deleted)

        mutator_traps = {
            name: AsyncMock(
                side_effect=AssertionError(f"Unexpected db.{name} invocation")
            )
            for name in DB_MUTATOR_NAMES
        }
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=summary_result,
                side_effect=_execute_first_summary_only,
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
            self.assertIs(sync_router.select, orig_select)
            self.assertIs(sync_router.sa_func, orig_sa_func)
            self.assertIs(sync_router.ChangeLog, orig_change_log)
            self.assertIs(sync_router.ChangeLog, ChangeLog)

            with self.assertRaises(RuntimeError) as exc_ctx:
                returned_value = await sync_router.get_sync_health(
                    request=request,
                    db=db,
                )

        exc = exc_ctx.exception
        self.assertIs(exc, summary_one_error)
        self.assertIs(type(exc), RuntimeError)
        self.assertEqual(exc.args, (SUMMARY_ONE_FAILURE_MESSAGE,))
        self.assertEqual(str(exc), SUMMARY_ONE_FAILURE_MESSAGE)
        self.assertIsNone(exc.__cause__)

        self.assertIs(returned_value, unreturned_sentinel)
        self.assertNotIsInstance(returned_value, Mapping)
        self.assertNotIsInstance(returned_value, tuple)

        self.assertEqual(events, ["guard", "execute1", "summary.one"])

        self.assertEqual(len(guard_calls), 1)
        guard_args, guard_kwargs, guard_result = guard_calls[0]
        self.assertEqual(len(guard_args), 1)
        self.assertIs(guard_args[0], request)
        self.assertEqual(guard_kwargs, {})
        self.assertIsNone(guard_result)
        self.assertEqual(
            request.headers,
            {"X-Observability-Api-Key": OBSERVABILITY_API_KEY_SENTINEL},
        )
        self.assertEqual(request.url.path, SYNC_HEALTH_ENDPOINT_PATH)
        self.assertEqual(request.client.host, REMOTE_CLIENT_HOST)

        self.assertIs(type(db), SimpleNamespace)
        self.assertIsInstance(db.execute, AsyncMock)
        self.assertIs(db.execute.return_value, summary_result)
        self.assertEqual(db.execute.call_count, 1)
        self.assertEqual(db.execute.await_count, 1)
        self.assertEqual(len(db.execute.call_args_list), 1)
        self.assertEqual(len(db.execute.await_args_list), 1)
        exec_call = db.execute.call_args_list[0]
        await_call = db.execute.await_args_list[0]
        self.assertEqual(len(exec_call.args), 1)
        self.assertEqual(exec_call.kwargs, {})
        self.assertEqual(len(await_call.args), 1)
        self.assertEqual(await_call.kwargs, {})

        self.assertEqual(len(execute_calls), 1)
        exec_args, exec_kwargs = execute_calls[0]
        self.assertEqual(len(exec_args), 1)
        self.assertEqual(exec_kwargs, {})
        self.assertEqual(len(execute_returned_results), 1)
        self.assertIs(execute_returned_results[0], summary_result)

        self.assertIs(type(summary_result), SimpleNamespace)
        self.assertEqual(list(vars(summary_result)), ["one"])
        self.assertFalse(hasattr(summary_result, "all"))
        self.assertIs(summary_result.one, summary_one_spy)
        self.assertIsInstance(summary_one_spy, MagicMock)
        self.assertNotIsInstance(summary_one_spy, AsyncMock)
        self.assertFalse(inspect.iscoroutinefunction(summary_one_spy))
        self.assertFalse(
            inspect.iscoroutinefunction(_raise_summary_one_consumption_failure)
        )
        self.assertIsNone(summary_one_spy.return_value)
        self.assertEqual(summary_one_spy.call_count, 1)
        self.assertEqual(len(summary_one_spy.call_args_list), 1)
        one_call = summary_one_spy.call_args_list[0]
        self.assertEqual(one_call.args, ())
        self.assertEqual(one_call.kwargs, {})
        self.assertEqual(summary_one_calls, [((), {})])
        self.assertEqual(summary_one_returned_values, [])

        captured_stmt = await_call.args[0]
        self.assertIs(exec_call.args[0], captured_stmt)
        self.assertIs(exec_args[0], captured_stmt)
        self.assertIsInstance(captured_stmt, Select)
        self.assertIs(captured_stmt.is_select, True)
        self.assertEqual(len(captured_stmt.selected_columns), 3)
        final_froms = captured_stmt.get_final_froms()
        self.assertEqual(len(final_froms), 1)
        self.assertEqual(
            [from_clause.name for from_clause in final_froms],
            ["change_log"],
        )

        compiled = captured_stmt.compile(dialect=postgresql.dialect())
        self.assertEqual(dict(compiled.params), {})
        normalized_sql_case = " ".join(str(compiled).split())
        normalized_sql = normalized_sql_case.lower()

        self.assertTrue(normalized_sql.startswith("select "))
        self.assertIn(" from change_log where change_log.synced = false", normalized_sql)
        self.assertTrue(
            normalized_sql.endswith("from change_log where change_log.synced = false")
        )
        self.assertIn("where change_log.synced = false", normalized_sql)
        self.assertIn("count(change_log.id)", normalized_sql_case)
        self.assertIn("min(change_log.created_at)", normalized_sql_case)
        self.assertIn(
            "count(change_log.id) FILTER (WHERE change_log.quarantined_at IS NOT NULL)",
            normalized_sql_case,
        )
        self.assertIn("count(change_log.id)", normalized_sql)
        self.assertIn("min(change_log.created_at)", normalized_sql)
        self.assertIn(
            "filter (where change_log.quarantined_at is not null)",
            normalized_sql,
        )
        self.assertNotIn(" group by ", normalized_sql)
        self.assertNotIn(" order by ", normalized_sql)
        self.assertNotIn(" join ", normalized_sql)
        self.assertNotIn("change_log.table_name", normalized_sql)

        get_redis_spy.assert_not_called()
        age_seconds_spy.assert_not_called()
        record_sync_health_spy.assert_not_called()
        publication_summary_spy.assert_not_called()
        publication_summary_spy.assert_not_awaited()
        record_publication_health_spy.assert_not_called()
        overtime_summary_spy.assert_not_called()
        overtime_summary_spy.assert_not_awaited()
        record_overtime_health_spy.assert_not_called()
        audit_log_spy.assert_not_called()
        logger_spy.warning.assert_not_called()
        logger_spy.info.assert_not_called()
        logger_spy.error.assert_not_called()
        logger_spy.exception.assert_not_called()
        logger_spy.debug.assert_not_called()

        for mutator_name in DB_MUTATOR_NAMES:
            mutator = getattr(db, mutator_name)
            self.assertIsInstance(mutator, AsyncMock)
            mutator.assert_not_called()
            mutator.assert_not_awaited()
            self.assertEqual(mutator.call_count, 0)
            self.assertEqual(mutator.await_count, 0)

        self.assertIs(db.new, db_new)
        self.assertIs(db.dirty, db_dirty)
        self.assertIs(db.deleted, db_deleted)
        self.assertEqual(db.new, before_db_new)
        self.assertEqual(db.dirty, before_db_dirty)
        self.assertEqual(db.deleted, before_db_deleted)
        self.assertEqual(len(db.new) - len(before_db_new), 0)
        self.assertEqual(len(db.dirty) - len(before_db_dirty), 0)
        self.assertEqual(len(db.deleted) - len(before_db_deleted), 0)

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


if __name__ == "__main__":
    unittest.main()
