"""HTTP boundary characterization test for api.routers.users.read_all_users execute failure.

Stage: P1-00b (IN_PROGRESS)
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-105
Gap Reference: docs/refactor/two-server/section-1/stages/P1-00b.md#work
Canonical Open Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-USERS-READ-ALL-USERS-L407
Scenario: P1-00B.users.read_all_users.http.execute_failure_before_relation_context

Characterizes FastAPI dispatch, query parameter binding (skip=2, limit=3),
dependency override routing, strict query compilation validation, and default HTTP 500
Internal Server Error propagation when db.execute fails before relation enrichment
or serialization occurs.
Exclusions: read_user self-read success and super-admin rejection, list success/search/
middle-manager policy, persistence/table/engine setup, retries, origin matrix, product changes,
documentation/inventory/count reconciliation, and seed/blocker/Stage closure.
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
from sqlalchemy import case, select

import api.deps
import api.routers.users
import core.db
import models.accountant_relation
import models.customer_relation
import models.user


def _build_independent_oracle_statement(skip: int = 2, limit: int = 3):
    """Construct independent explicit oracle query without using production helpers."""
    customer_exists = (
        select(models.customer_relation.CustomerRelation.id)
        .where(
            models.customer_relation.CustomerRelation.customer_user_id == models.user.User.id,
            models.customer_relation.CustomerRelation.status
            == models.customer_relation.CustomerRelationStatus.ACTIVE,
            models.customer_relation.CustomerRelation.deleted_at.is_(None),
        )
        .exists()
    )
    accountant_exists = (
        select(models.accountant_relation.AccountantRelation.id)
        .where(
            models.accountant_relation.AccountantRelation.accountant_user_id == models.user.User.id,
            models.accountant_relation.AccountantRelation.status
            == models.accountant_relation.AccountantRelationStatus.ACTIVE,
            models.accountant_relation.AccountantRelation.deleted_at.is_(None),
        )
        .exists()
    )
    rank_expr = case(
        (customer_exists, 1),
        (accountant_exists, 2),
        else_=0,
    )
    return (
        select(models.user.User)
        .where(models.user.User.is_deleted == False)
        .order_by(rank_expr.asc(), models.user.User.id.desc())
        .offset(skip)
        .limit(limit)
    )


class FailClosedExecuteAsyncSession:
    """Strict custom async Session adapter for read_all_users execute failure."""

    def __init__(
        self,
        expected_oracle_statement: object,
        sentinel_error: RuntimeError,
    ) -> None:
        self.expected_oracle_statement = expected_oracle_statement
        self.sentinel_error = sentinel_error
        self.execute_calls: list[tuple[tuple, dict]] = []
        self.validated_query_count = 0

    async def execute(self, statement: object, *args: object, **kwargs: object) -> object:
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
        raise self.sentinel_error

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
        raise AssertionError("db.get unexpectedly called on FailClosedExecuteAsyncSession")

    async def scalar(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalar unexpectedly called on FailClosedExecuteAsyncSession")

    async def scalars(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalars unexpectedly called on FailClosedExecuteAsyncSession")

    async def commit(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.commit unexpectedly called on FailClosedExecuteAsyncSession")

    async def rollback(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.rollback unexpectedly called on FailClosedExecuteAsyncSession")

    async def refresh(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.refresh unexpectedly called on FailClosedExecuteAsyncSession")

    async def close(self, *args: object, **kwargs: object) -> None:
        pass

    def add(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.add unexpectedly called on FailClosedExecuteAsyncSession")

    def delete(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.delete unexpectedly called on FailClosedExecuteAsyncSession")


class UsersReadAllExecuteFailureHttpCharacterizationTests(unittest.TestCase):
    def test_list_query_failure_returns_http_500_before_relation_context(self) -> None:
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        oracle_query = _build_independent_oracle_statement(skip=2, limit=3)

        # --- Negative Probes on Fresh Adapters ---
        # Probe 1: wrong entity
        p1_adapter = FailClosedExecuteAsyncSession(oracle_query, RuntimeError("sentinel-p1"))
        wrong_entity_query = select(models.customer_relation.CustomerRelation)
        with self.assertRaises(AssertionError):
            asyncio.run(p1_adapter.execute(wrong_entity_query))
        self.assertEqual(p1_adapter.validated_query_count, 0)

        # Probe 2: missing filter
        p2_adapter = FailClosedExecuteAsyncSession(oracle_query, RuntimeError("sentinel-p2"))
        missing_filter_query = select(models.user.User).offset(2).limit(3)
        with self.assertRaises(AssertionError):
            asyncio.run(p2_adapter.execute(missing_filter_query))
        self.assertEqual(p2_adapter.validated_query_count, 0)

        # Probe 3: wrong pagination
        p3_adapter = FailClosedExecuteAsyncSession(oracle_query, RuntimeError("sentinel-p3"))
        wrong_pagination_query = _build_independent_oracle_statement(skip=0, limit=100)
        with self.assertRaises(AssertionError):
            asyncio.run(p3_adapter.execute(wrong_pagination_query))
        self.assertEqual(p3_adapter.validated_query_count, 0)

        # Probe 4: extra arguments
        p4_adapter = FailClosedExecuteAsyncSession(oracle_query, RuntimeError("sentinel-p4"))
        with self.assertRaises(AssertionError):
            asyncio.run(p4_adapter.execute(oracle_query, "extra_pos_arg"))
        self.assertEqual(p4_adapter.validated_query_count, 0)

        p4b_adapter = FailClosedExecuteAsyncSession(oracle_query, RuntimeError("sentinel-p4b"))
        with self.assertRaises(AssertionError):
            asyncio.run(p4b_adapter.execute(oracle_query, execution_options={"opt": 1}))
        self.assertEqual(p4b_adapter.validated_query_count, 0)

        # Probe 5: duplicate execute gets separate adapter
        p5_sentinel = RuntimeError("sentinel-p5")
        p5_adapter = FailClosedExecuteAsyncSession(oracle_query, p5_sentinel)
        with self.assertRaises(RuntimeError) as p5_exc_info:
            asyncio.run(p5_adapter.execute(oracle_query))
        self.assertIs(p5_exc_info.exception, p5_sentinel)
        self.assertEqual(p5_adapter.validated_query_count, 1)

        with self.assertRaises(AssertionError):
            asyncio.run(p5_adapter.execute(oracle_query))
        self.assertEqual(p5_adapter.validated_query_count, 1)

        # Probe 6: unsupported operations fail closed
        p6_adapter = FailClosedExecuteAsyncSession(oracle_query, RuntimeError("sentinel-p6"))
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

        live_sentinel = RuntimeError("live db execute failure sentinel")
        live_session = FailClosedExecuteAsyncSession(oracle_query, live_sentinel)

        db_override_calls = 0
        auth_override_calls = 0
        observed_db_exceptions: list[BaseException] = []

        async def override_get_db():
            nonlocal db_override_calls
            db_override_calls += 1
            try:
                yield live_session
            except BaseException as exc:
                observed_db_exceptions.append(exc)
                raise

        def override_verify_admin_or_dev_key():
            nonlocal auth_override_calls
            auth_override_calls += 1
            return SimpleNamespace(id=901, role=models.user.UserRole.SUPER_ADMIN)

        app.dependency_overrides[core.db.get_db] = override_get_db
        app.dependency_overrides[
            api.deps.verify_admin_or_dev_key
        ] = override_verify_admin_or_dev_key

        attach_relation_mock = AsyncMock(
            side_effect=AssertionError(
                "attach_user_management_relation_context must not be awaited on execute failure"
            )
        )
        serialize_mock = Mock(
            side_effect=AssertionError(
                "serialize_user_read must not be called on execute failure"
            )
        )

        try:
            with patch(
                "api.routers.users.attach_user_management_relation_context",
                new=attach_relation_mock,
            ), patch(
                "api.routers.users.serialize_user_read",
                new=serialize_mock,
            ):
                with TestClient(app, raise_server_exceptions=False) as client:
                    response = client.get("/api/users/?skip=2&limit=3")

            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.text, "Internal Server Error")
            self.assertEqual(len(response.history), 0)

            self.assertEqual(len(observed_db_exceptions), 1)
            self.assertIs(observed_db_exceptions[0], live_sentinel)

            self.assertEqual(db_override_calls, 1)
            self.assertEqual(auth_override_calls, 1)
            self.assertEqual(live_session.validated_query_count, 1)
            self.assertEqual(len(live_session.execute_calls), 1)

            attach_relation_mock.assert_not_awaited()
            serialize_mock.assert_not_called()

            self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
            self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)
        finally:
            app.dependency_overrides.clear()
            self.assertEqual(len(app.dependency_overrides), 0)


if __name__ == "__main__":
    unittest.main()
