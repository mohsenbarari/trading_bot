"""Two new, direct-handler prequery failure characterizations for P1-00b.

Prospective assignment: WA-P1-00B-NOTIFICATION-MARK-ALL-PREQUERY-20261019-750N.
Seed: API-NOTIFY-POST-NOTIFICATIONS-MARK-ALL-NOTIFICATIONS-READ-L318.
Scenario: mark-all-select-and-execute-prequery-failure-batch-v1.

Only synthetic exception identity, visible boundary calls/awaits and suppression
are established. No HTTP/authentication/Depends, real DB query/transaction or
persistence, full-row immutability, Redis delivery, concurrency/retry/reload,
origin/seed promotion, blocker or Stage closure is established. These are two
scenarios in one new Source unit, not two new canonical seed credits.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from api.routers import notifications
from models.notification import Notification
from models.user import User, UserRole


class NotificationMarkAllPrequeryFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Do not replace these failure boundaries with existing success/retry cases."""

    def _fixture(self) -> SimpleNamespace:
        actor = User(id=75001, role=UserRole.STANDARD)
        actor.is_accountant = False

        # Visible mock boundaries only: no custom Session or SQLite adapter.
        mutations = {
            name: AsyncMock(
                name=f"db.{name}",
                side_effect=AssertionError(f"unreached db.{name}"),
            )
            for name in ("commit", "delete", "flush", "refresh", "rollback")
        }
        additions = {
            name: Mock(
                name=f"db.{name}",
                side_effect=AssertionError(f"unreached db.{name}"),
            )
            for name in ("add", "add_all")
        }
        rows = MagicMock(name="unreached_rows", spec=list)
        for name in ("__iter__", "__len__", "__getitem__"):
            getattr(rows, name).side_effect = AssertionError(
                f"unreached rows.{name}"
            )
        scalars = SimpleNamespace(all=Mock(name="result.scalars.all", return_value=rows))
        result = SimpleNamespace(
            scalars=Mock(name="result.scalars", return_value=scalars)
        )
        db = SimpleNamespace(
            execute=AsyncMock(name="db.execute", return_value=result),
            **mutations,
            **additions,
        )
        return SimpleNamespace(
            actor=actor,
            actor_fields_before=(actor.id, actor.role, actor.is_accountant),
            db=db,
            redis=object(),
            sync=AsyncMock(
                name="sync_unread_count",
                side_effect=AssertionError("unreached unread-count sync"),
            ),
            mutations=mutations,
            additions=additions,
            result=result,
            scalars=scalars,
            rows=rows,
        )

    def _assert_no_downstream_work(self, fixture: SimpleNamespace) -> None:
        fixture.result.scalars.assert_not_called()
        fixture.scalars.all.assert_not_called()
        for name in ("__iter__", "__len__", "__getitem__"):
            self.assertEqual(getattr(fixture.rows, name).call_count, 0, name)
        for name, boundary in fixture.mutations.items():
            self.assertEqual(boundary.call_count, 0, name)
            self.assertEqual(boundary.await_count, 0, name)
        for name, boundary in fixture.additions.items():
            self.assertEqual(boundary.call_count, 0, name)
        self.assertEqual(fixture.sync.call_count, 0)
        self.assertEqual(fixture.sync.await_count, 0)
        # Exactly these selected fields only; never a full-row/persistence claim.
        self.assertEqual(
            (
                fixture.actor.id,
                fixture.actor.role,
                fixture.actor.is_accountant,
            ),
            fixture.actor_fields_before,
        )

    async def test_select_builder_exception_precedes_database_and_unread_sync(self) -> None:
        """Initial select raises before DB execution or downstream row access."""
        fixture = self._fixture()
        failure = RuntimeError("750N synthetic initial select-builder failure")
        real_select = notifications.select
        real_sync = notifications.sync_unread_count
        builder = Mock(name="select", side_effect=failure)

        with patch.object(notifications, "select", builder), patch.object(
            notifications, "sync_unread_count", fixture.sync
        ):
            with self.assertRaises(RuntimeError) as raised:
                await notifications.mark_all_notifications_read(
                    current_user=fixture.actor,
                    db=fixture.db,
                    redis=fixture.redis,
                )
            self.assertIs(raised.exception, failure)
            builder.assert_called_once_with(Notification)
            self.assertEqual(fixture.db.execute.call_count, 0)
            self.assertEqual(fixture.db.execute.await_count, 0)
            self._assert_no_downstream_work(fixture)

        self.assertIs(notifications.select, real_select)
        self.assertIs(notifications.sync_unread_count, real_sync)

    async def test_initial_execute_exception_precedes_rows_commit_and_unread_sync(self) -> None:
        """The first awaited execute raises before scalars, iteration or commit."""
        fixture = self._fixture()
        failure = RuntimeError("750N synthetic initial awaited execute failure")
        fixture.db.execute.side_effect = failure
        real_select = notifications.select
        real_sync = notifications.sync_unread_count
        builder = Mock(name="select", wraps=real_select)

        with patch.object(notifications, "select", builder), patch.object(
            notifications, "sync_unread_count", fixture.sync
        ):
            with self.assertRaises(RuntimeError) as raised:
                await notifications.mark_all_notifications_read(
                    current_user=fixture.actor,
                    db=fixture.db,
                    redis=fixture.redis,
                )
            self.assertIs(raised.exception, failure)
            builder.assert_called_once_with(Notification)
            self.assertEqual(fixture.db.execute.call_count, 1)
            self.assertEqual(fixture.db.execute.await_count, 1)
            called = fixture.db.execute.call_args
            awaited = fixture.db.execute.await_args
            self.assertEqual(len(called.args), 1)
            self.assertEqual(len(awaited.args), 1)
            self.assertEqual(called.kwargs, {})
            self.assertEqual(awaited.kwargs, {})
            self.assertIs(called.args[0], awaited.args[0])
            statement = called.args[0]
            self.assertIs(statement.column_descriptions[0]["entity"], Notification)
            # Actual SQLAlchemy construction only, not DB execution/SQL proof.
            self.assertEqual(
                str(statement.whereclause),
                "notifications.user_id = :user_id_1 AND notifications.is_read = false",
            )
            self.assertEqual(statement.compile().params, {"user_id_1": fixture.actor.id})
            self._assert_no_downstream_work(fixture)

        self.assertIs(notifications.select, real_select)
        self.assertIs(notifications.sync_unread_count, real_sync)
