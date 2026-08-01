from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.orm import Session

from core import db
from core.application_snapshot_write_barrier import (
    ApplicationSnapshotWriteBarrierError,
    ApplicationSnapshotWriteBarrierPolicy,
    SNAPSHOT_WRITE_BARRIER_LOCK_KEY,
    SNAPSHOT_WRITE_BARRIER_LOCK_NAMESPACE,
    acquire_shared_snapshot_write_barrier,
    coordinator_snapshot_write_barrier_scope,
    policy_from_settings,
    session_is_authorized_for_snapshot_write_barrier_coordinator,
)


class FakeSession:
    def __init__(self) -> None:
        self.info: dict[object, object] = {}

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
        return False

    def in_transaction(self) -> bool:
        return False


class FakeSyncConnection:
    def __init__(self, *, dialect_name: str = "postgresql") -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.executed: list[tuple[str, dict[str, int]]] = []

    def execute(self, statement, parameters):
        self.executed.append((str(statement), dict(parameters)))
        return object()


class FakeAsyncResult:
    def __init__(self, scalar: object) -> None:
        self.scalar = scalar

    def scalar_one(self) -> object:
        return self.scalar


class FakeAsyncConnection:
    def __init__(
        self,
        *,
        dialect_name: str = "postgresql",
        fail_acquire: bool = False,
        cancel_acquire: bool = False,
        fail_unlock: bool = False,
    ) -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.executed: list[tuple[str, dict[str, int]]] = []
        self.commits = 0
        self.fail_acquire = fail_acquire
        self.cancel_acquire = cancel_acquire
        self.fail_unlock = fail_unlock
        self.invalidations = 0

    async def execute(self, statement, parameters):
        rendered = str(statement)
        self.executed.append((rendered, dict(parameters)))
        if "pg_advisory_lock" in rendered and "pg_advisory_unlock" not in rendered:
            if self.cancel_acquire:
                raise asyncio.CancelledError()
            if self.fail_acquire:
                raise RuntimeError("acquire failed")
        if self.fail_unlock and "pg_advisory_unlock" in rendered:
            raise RuntimeError("unlock failed")
        return FakeAsyncResult("pg_advisory_unlock" in rendered)

    async def commit(self) -> None:
        self.commits += 1

    async def invalidate(self) -> None:
        self.invalidations += 1


class FakeAsyncConnectContext:
    def __init__(self, connection: FakeAsyncConnection) -> None:
        self.connection = connection
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeAsyncConnection:
        self.entered = True
        return self.connection

    async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
        self.exited = True
        return False


class FakeAsyncEngine:
    def __init__(self, connection: FakeAsyncConnection) -> None:
        self.context = FakeAsyncConnectContext(connection)
        self.connect_calls = 0

    def connect(self) -> FakeAsyncConnectContext:
        self.connect_calls += 1
        return self.context


class FakeSessionFactory:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.bound_connection = None

    def __call__(self, *, bind):
        self.bound_connection = bind
        return self.session


ENABLED_POLICY = ApplicationSnapshotWriteBarrierPolicy(
    enabled=True,
    local_site="webapp_fi",
)


class ApplicationSnapshotWriteBarrierPolicyTests(unittest.TestCase):
    def test_default_off_settings_do_not_require_site_or_connection(self) -> None:
        settings = SimpleNamespace(application_snapshot_write_barrier_enabled=False)

        self.assertEqual(policy_from_settings(settings), ApplicationSnapshotWriteBarrierPolicy())

        connection = FakeSyncConnection()
        acquire_shared_snapshot_write_barrier(
            FakeSession(),
            connection,
            policy=ApplicationSnapshotWriteBarrierPolicy(),
        )
        self.assertEqual(connection.executed, [])

    def test_enabled_policy_is_scoped_to_webapp_fi(self) -> None:
        connection = FakeSyncConnection()

        with self.assertRaisesRegex(ApplicationSnapshotWriteBarrierError, "WebApp-FI"):
            acquire_shared_snapshot_write_barrier(
                FakeSession(),
                connection,
                policy=ApplicationSnapshotWriteBarrierPolicy(enabled=True, local_site="webapp_ir"),
            )

        self.assertEqual(connection.executed, [])

    def test_enabled_policy_acquires_the_shared_postgresql_lock(self) -> None:
        connection = FakeSyncConnection()

        acquire_shared_snapshot_write_barrier(
            FakeSession(),
            connection,
            policy=ENABLED_POLICY,
        )

        self.assertEqual(len(connection.executed), 1)
        statement, parameters = connection.executed[0]
        self.assertIn("pg_advisory_xact_lock_shared", statement)
        self.assertEqual(
            parameters,
            {
                "namespace": SNAPSHOT_WRITE_BARRIER_LOCK_NAMESPACE,
                "lock_key": SNAPSHOT_WRITE_BARRIER_LOCK_KEY,
            },
        )

    def test_enabled_policy_fails_closed_for_a_non_postgresql_session(self) -> None:
        connection = FakeSyncConnection(dialect_name="sqlite")

        with self.assertRaisesRegex(ApplicationSnapshotWriteBarrierError, "PostgreSQL"):
            acquire_shared_snapshot_write_barrier(
                FakeSession(),
                connection,
                policy=ENABLED_POLICY,
            )

        self.assertEqual(connection.executed, [])


class ApplicationSnapshotWriteBarrierHookTests(unittest.TestCase):
    def test_global_session_after_begin_hook_is_registered(self) -> None:
        self.assertTrue(
            event.contains(
                Session,
                "after_begin",
                db._acquire_application_snapshot_write_barrier_after_begin,
            )
        )

    def test_hook_projects_only_the_dedicated_default_off_policy(self) -> None:
        session = FakeSession()
        connection = FakeSyncConnection()

        with patch.object(
            db.settings,
            "application_snapshot_write_barrier_enabled",
            True,
        ), patch.object(
            db.settings,
            "application_snapshot_write_barrier_local_site",
            "webapp_fi",
        ):
            db._acquire_application_snapshot_write_barrier_after_begin(
                session,
                object(),
                connection,
            )

        self.assertEqual(len(connection.executed), 1)
        self.assertIn("pg_advisory_xact_lock_shared", connection.executed[0][0])

    def test_nested_transaction_does_not_acquire_a_second_shared_lock(self) -> None:
        session = FakeSession()
        connection = FakeSyncConnection()

        with patch.object(
            db.settings,
            "application_snapshot_write_barrier_enabled",
            True,
        ), patch.object(
            db.settings,
            "application_snapshot_write_barrier_local_site",
            "webapp_fi",
        ):
            db._acquire_application_snapshot_write_barrier_after_begin(
                session,
                SimpleNamespace(nested=True),
                connection,
            )

        self.assertEqual(connection.executed, [])


class ApplicationSnapshotWriteBarrierCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_coordinator_holds_exclusive_lock_and_bypasses_marked_session(self) -> None:
        connection = FakeAsyncConnection()
        engine = FakeAsyncEngine(connection)
        factory = FakeSessionFactory()

        async with coordinator_snapshot_write_barrier_scope(
            async_engine=engine,
            session_factory=factory,
            policy=ENABLED_POLICY,
        ) as session:
            self.assertIs(session, factory.session)
            self.assertIs(factory.bound_connection, connection)
            self.assertTrue(session_is_authorized_for_snapshot_write_barrier_coordinator(session))

            sync_connection = FakeSyncConnection()
            acquire_shared_snapshot_write_barrier(
                session,
                sync_connection,
                policy=ENABLED_POLICY,
            )
            self.assertEqual(sync_connection.executed, [])

        self.assertFalse(
            session_is_authorized_for_snapshot_write_barrier_coordinator(factory.session)
        )
        self.assertEqual(factory.session.info, {})
        self.assertTrue(engine.context.entered)
        self.assertTrue(engine.context.exited)
        # The acquisition query's implicit transaction is committed before the
        # fresh coordinator session is bound. Advisory unlock is session-level
        # and does not need a second commit.
        self.assertEqual(connection.commits, 1)
        self.assertEqual(len(connection.executed), 2)
        self.assertIn("pg_advisory_lock", connection.executed[0][0])
        self.assertIn("pg_advisory_unlock", connection.executed[1][0])

    async def test_coordinator_attempts_unlock_when_coordinator_work_fails(self) -> None:
        connection = FakeAsyncConnection()

        with self.assertRaisesRegex(RuntimeError, "cutover work failed"):
            async with coordinator_snapshot_write_barrier_scope(
                async_engine=FakeAsyncEngine(connection),
                session_factory=FakeSessionFactory(),
                policy=ENABLED_POLICY,
            ):
                raise RuntimeError("cutover work failed")

        self.assertEqual(len(connection.executed), 2)
        self.assertIn("pg_advisory_unlock", connection.executed[1][0])

    async def test_disabled_coordinator_policy_never_connects(self) -> None:
        engine = FakeAsyncEngine(FakeAsyncConnection())

        with self.assertRaisesRegex(ApplicationSnapshotWriteBarrierError, "requires an enabled"):
            async with coordinator_snapshot_write_barrier_scope(
                async_engine=engine,
                session_factory=FakeSessionFactory(),
                policy=ApplicationSnapshotWriteBarrierPolicy(),
            ):
                self.fail("disabled barrier scope must not yield")

        self.assertEqual(engine.connect_calls, 0)

    async def test_uncertain_unlock_invalidates_the_pooled_connection(self) -> None:
        connection = FakeAsyncConnection(fail_unlock=True)

        with self.assertRaisesRegex(ApplicationSnapshotWriteBarrierError, "coordinator SQL failed"):
            async with coordinator_snapshot_write_barrier_scope(
                async_engine=FakeAsyncEngine(connection),
                session_factory=FakeSessionFactory(),
                policy=ENABLED_POLICY,
            ):
                pass

        self.assertEqual(connection.invalidations, 1)

    async def test_uncertain_acquire_invalidates_the_pooled_connection(self) -> None:
        connection = FakeAsyncConnection(fail_acquire=True)

        with self.assertRaisesRegex(ApplicationSnapshotWriteBarrierError, "coordinator SQL failed"):
            async with coordinator_snapshot_write_barrier_scope(
                async_engine=FakeAsyncEngine(connection),
                session_factory=FakeSessionFactory(),
                policy=ENABLED_POLICY,
            ):
                self.fail("failed acquisition must not yield")

        self.assertEqual(connection.invalidations, 1)

    async def test_cancelled_acquire_invalidates_the_pooled_connection(self) -> None:
        connection = FakeAsyncConnection(cancel_acquire=True)

        with self.assertRaises(asyncio.CancelledError):
            async with coordinator_snapshot_write_barrier_scope(
                async_engine=FakeAsyncEngine(connection),
                session_factory=FakeSessionFactory(),
                policy=ENABLED_POLICY,
            ):
                self.fail("cancelled acquisition must not yield")

        self.assertEqual(connection.invalidations, 1)


if __name__ == "__main__":
    unittest.main()
