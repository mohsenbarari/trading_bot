"""HTTP boundary characterization test for api.routers.users.read_all_users empty list success.

Stage: P1-00b (IN_PROGRESS)
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-105
Gap Reference: docs/refactor/two-server/section-1/stages/P1-00b.md#work
Canonical Open Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-USERS-READ-ALL-USERS-L407
Scenario: P1-00B.users.read_all_users.http.empty_list_success_without_relation_queries

Characterizes FastAPI dispatch, query parameter binding (skip=2, limit=3),
dependency override routing, strict query compilation validation, counted
synchronous scalars().all() yielding an explicit empty list, execution of the real
attach_user_management_relation_context helper returning [] without executing relation queries,
suppression of serializer invocations, and HTTP 200 success with JSON [] and no redirects.

Single-custom-session reason:
The live path requires a custom Session adapter and exact query/result contract,
which cannot be batched with other handlers.

Exclusions:
Nonempty lists, search, include_deleted=true, middle-manager policy, read_user
self-read success or super-admin rejection (both remain deferred and unmet),
authentication implementation, persistence, mutation, retries, child-process/reload,
origin-matrix cells, relation audience/policy closure, inventory/count reconciliation,
seed/blocker or Stage closure.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import api.deps
import api.routers.users
import core.db
import models.accountant_relation
import models.customer_relation
import models.user
from tests.test_p1_00b_characterization_users_read_all_execute_failure_http import (
    _build_independent_oracle_statement,
)


class StrictScalarsResult:
    """Counted synchronous scalars wrapper yielding an explicit empty list once."""

    def __init__(self, items: list) -> None:
        self._items = items
        self.all_call_count = 0

    def all(self) -> list:
        self.all_call_count += 1
        if self.all_call_count > 1:
            raise AssertionError(
                f"scalars().all() called more than once (count={self.all_call_count})"
            )
        return self._items


class StrictEmptyQueryResult:
    """Counted synchronous query result supporting scalars().all() exactly once."""

    def __init__(self) -> None:
        self.scalars_call_count = 0
        self._scalars = StrictScalarsResult([])

    def scalars(self) -> StrictScalarsResult:
        self.scalars_call_count += 1
        if self.scalars_call_count > 1:
            raise AssertionError(
                f"result.scalars() called more than once (count={self.scalars_call_count})"
            )
        return self._scalars


class StrictEmptyListAsyncSession:
    """Strict custom async Session adapter for read_all_users empty list success."""

    def __init__(self, expected_oracle_statement: object) -> None:
        self.expected_oracle_statement = expected_oracle_statement
        self.execute_calls: list[tuple[tuple, dict]] = []
        self.validated_query_count = 0
        self.result = StrictEmptyQueryResult()
        self.closed = False

    async def execute(self, statement: object, *args: object, **kwargs: object) -> StrictEmptyQueryResult:
        self.execute_calls.append((args, kwargs))
        if args or kwargs:
            raise AssertionError(
                f"db.execute received unexpected extra args: args={args!r}, kwargs={kwargs!r}"
            )
        if len(self.execute_calls) > 1:
            raise AssertionError(
                f"db.execute called multiple times (call count={len(self.execute_calls)})"
            )

        self._validate_statement(statement)
        self.validated_query_count += 1
        return self.result

    def _validate_statement(self, statement: object) -> None:
        if not hasattr(statement, "compile") or not hasattr(statement, "column_descriptions"):
            raise AssertionError(f"Expected compilable select statement, got {statement!r}")

        col_descs = getattr(statement, "column_descriptions", [])
        if len(col_descs) != 1 or col_descs[0].get("entity") is not models.user.User:
            raise AssertionError(
                f"Expected query entity to be models.user.User, got {col_descs!r}"
            )

        actual_compiled = statement.compile()
        oracle_compiled = self.expected_oracle_statement.compile()

        if str(actual_compiled) != str(oracle_compiled):
            raise AssertionError(
                f"Statement SQL mismatch:\nActual:\n{actual_compiled}\nExpected:\n{oracle_compiled}"
            )

        actual_params = actual_compiled.params
        oracle_params = oracle_compiled.params
        if actual_params != oracle_params:
            raise AssertionError(
                f"Statement bind params mismatch:\nActual:\n{actual_params}\nExpected:\n{oracle_params}"
            )

        param_values = list(actual_params.values())
        for expected_val in [
            False,
            models.customer_relation.CustomerRelationStatus.ACTIVE,
            models.accountant_relation.AccountantRelationStatus.ACTIVE,
            1,
            2,
            0,
            2,
            3,
        ]:
            if expected_val not in param_values:
                raise AssertionError(
                    f"Bind parameters missing expected value {expected_val!r} in {actual_params!r}"
                )

    async def get(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.get unexpectedly called on StrictEmptyListAsyncSession")

    async def scalar(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalar unexpectedly called on StrictEmptyListAsyncSession")

    async def scalars(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalars unexpectedly called on StrictEmptyListAsyncSession")

    async def commit(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.commit unexpectedly called on StrictEmptyListAsyncSession")

    async def rollback(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.rollback unexpectedly called on StrictEmptyListAsyncSession")

    async def refresh(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.refresh unexpectedly called on StrictEmptyListAsyncSession")

    async def close(self, *args: object, **kwargs: object) -> None:
        if args or kwargs:
            raise AssertionError(f"db.close received unexpected args: args={args!r}, kwargs={kwargs!r}")
        self.closed = True

    def add(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.add unexpectedly called on StrictEmptyListAsyncSession")

    def delete(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.delete unexpectedly called on StrictEmptyListAsyncSession")


class UsersReadAllEmptyHttpCharacterizationTests(unittest.TestCase):
    def test_empty_list_returns_http_200_without_relation_queries(self) -> None:
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        oracle_query = _build_independent_oracle_statement(skip=2, limit=3)

        # --- Negative Probes on Fresh Adapters ---
        # Probe 1: wrong entity
        p1_adapter = StrictEmptyListAsyncSession(oracle_query)
        wrong_entity_query = select(models.customer_relation.CustomerRelation)
        with self.assertRaises(AssertionError):
            asyncio.run(p1_adapter.execute(wrong_entity_query))
        self.assertEqual(p1_adapter.validated_query_count, 0)

        # Probe 2: missing filter / order
        p2_adapter = StrictEmptyListAsyncSession(oracle_query)
        missing_filter_query = select(models.user.User).offset(2).limit(3)
        with self.assertRaises(AssertionError):
            asyncio.run(p2_adapter.execute(missing_filter_query))
        self.assertEqual(p2_adapter.validated_query_count, 0)

        p2b_adapter = StrictEmptyListAsyncSession(oracle_query)
        missing_order_query = (
            select(models.user.User)
            .where(models.user.User.is_deleted == False)
            .offset(2)
            .limit(3)
        )
        with self.assertRaises(AssertionError):
            asyncio.run(p2b_adapter.execute(missing_order_query))
        self.assertEqual(p2b_adapter.validated_query_count, 0)

        # Probe 3: wrong pagination
        p3_adapter = StrictEmptyListAsyncSession(oracle_query)
        wrong_pagination_query = _build_independent_oracle_statement(skip=0, limit=100)
        with self.assertRaises(AssertionError):
            asyncio.run(p3_adapter.execute(wrong_pagination_query))
        self.assertEqual(p3_adapter.validated_query_count, 0)

        # Probe 4: extra arguments
        p4_adapter = StrictEmptyListAsyncSession(oracle_query)
        with self.assertRaises(AssertionError):
            asyncio.run(p4_adapter.execute(oracle_query, "extra_pos_arg"))
        self.assertEqual(p4_adapter.validated_query_count, 0)

        p4b_adapter = StrictEmptyListAsyncSession(oracle_query)
        with self.assertRaises(AssertionError):
            asyncio.run(p4b_adapter.execute(oracle_query, execution_options={"opt": 1}))
        self.assertEqual(p4b_adapter.validated_query_count, 0)

        # Probe 5: duplicate execute gets separate adapter
        p5_adapter = StrictEmptyListAsyncSession(oracle_query)
        res = asyncio.run(p5_adapter.execute(oracle_query))
        self.assertIs(res, p5_adapter.result)
        self.assertEqual(p5_adapter.validated_query_count, 1)

        with self.assertRaises(AssertionError):
            asyncio.run(p5_adapter.execute(oracle_query))
        self.assertEqual(p5_adapter.validated_query_count, 1)

        # Probe 6: unsupported operations fail closed
        p6_adapter = StrictEmptyListAsyncSession(oracle_query)
        with self.assertRaises(AssertionError):
            asyncio.run(p6_adapter.get(models.user.User, 1))
        with self.assertRaises(AssertionError):
            asyncio.run(p6_adapter.scalar(oracle_query))
        with self.assertRaises(AssertionError):
            asyncio.run(p6_adapter.scalars(oracle_query))
        with self.assertRaises(AssertionError):
            asyncio.run(p6_adapter.commit())
        with self.assertRaises(AssertionError):
            asyncio.run(p6_adapter.rollback())
        with self.assertRaises(AssertionError):
            asyncio.run(p6_adapter.refresh(object()))
        with self.assertRaises(AssertionError):
            p6_adapter.add(object())
        with self.assertRaises(AssertionError):
            p6_adapter.delete(object())
        self.assertEqual(p6_adapter.validated_query_count, 0)

        # --- Live HTTP Boundary Test ---
        app = FastAPI()
        app.include_router(api.routers.users.router, prefix="/api/users")

        live_session = StrictEmptyListAsyncSession(oracle_query)

        db_override_calls = 0
        auth_override_calls = 0

        async def override_get_db():
            nonlocal db_override_calls
            db_override_calls += 1
            try:
                yield live_session
            finally:
                await live_session.close()

        def override_verify_admin_or_dev_key():
            nonlocal auth_override_calls
            auth_override_calls += 1
            return SimpleNamespace(id=901, role=models.user.UserRole.SUPER_ADMIN)

        app.dependency_overrides[core.db.get_db] = override_get_db
        app.dependency_overrides[
            api.deps.verify_admin_or_dev_key
        ] = override_verify_admin_or_dev_key

        real_attach_relation = api.routers.users.attach_user_management_relation_context
        attach_relation_spy = AsyncMock(wraps=real_attach_relation)

        serialize_mock = Mock(
            side_effect=AssertionError(
                "serialize_user_read must not be called when users list is empty"
            )
        )

        try:
            with patch(
                "api.routers.users.attach_user_management_relation_context",
                new=attach_relation_spy,
            ), patch(
                "api.routers.users.serialize_user_read",
                new=serialize_mock,
            ):
                with TestClient(app, raise_server_exceptions=True) as client:
                    response = client.get("/api/users/?skip=2&limit=3")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [])
            self.assertEqual(len(response.history), 0)

            self.assertEqual(db_override_calls, 1)
            self.assertEqual(auth_override_calls, 1)
            self.assertEqual(live_session.validated_query_count, 1)
            self.assertEqual(len(live_session.execute_calls), 1)

            # Assert counted result methods were invoked exactly once
            self.assertEqual(live_session.result.scalars_call_count, 1)
            self.assertEqual(live_session.result._scalars.all_call_count, 1)

            # Assert attach_user_management_relation_context was awaited once with the exact empty list
            self.assertEqual(attach_relation_spy.await_count, 1)
            args, kwargs = attach_relation_spy.await_args
            self.assertIs(args[0], live_session)
            self.assertIs(args[1], live_session.result._scalars._items)
            self.assertEqual(args[1], [])

            # Assert serializer was never called
            serialize_mock.assert_not_called()

            # Assert session was closed properly by get_db
            self.assertTrue(live_session.closed)

            # Assert no network leaks
            self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
            self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)
        finally:
            app.dependency_overrides.clear()
            self.assertEqual(len(app.dependency_overrides), 0)


if __name__ == "__main__":
    unittest.main()
