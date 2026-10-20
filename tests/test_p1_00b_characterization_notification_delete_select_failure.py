from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import api.routers.notifications as notifications
from models.notification import Notification


class NotificationDeleteSelectFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_select_builder_exception_precedes_database_and_unread_sync(self) -> None:
        original_handler = notifications.delete_notification
        original_select = notifications.select
        original_sync_unread_count = notifications.sync_unread_count
        self.assertIs(notifications. Notification, Notification)

        notification_id = 9401
        actor = SimpleNamespace(id=4107)
        self.assertEqual(tuple(actor.__dict__.keys()), ("id",))

        select_error = RuntimeError("synthetic notification delete select-builder failure")

        execute_result = Mock(name="execute_result")
        execute_result.scalar_one_or_none = Mock(
            name="result.scalar_one_or_none",
            side_effect=AssertionError(
                "result.scalar_one_or_none must not be called when select() fails"
            ),
        )

        db = SimpleNamespace(
            execute=AsyncMock(
                name="db.execute",
                side_effect=AssertionError("db.execute must not be called when select() fails"),
                return_value=execute_result,
            ),
            delete=AsyncMock(
                name="db.delete",
                side_effect=AssertionError("db.delete must not be called when select() fails"),
            ),
            commit=AsyncMock(
                name="db.commit",
                side_effect=AssertionError("db.commit must not be called when select() fails"),
            ),
        )
        redis = SimpleNamespace(
            set=AsyncMock(
                name="redis.set",
                side_effect=AssertionError("redis.set must not be called when select() fails"),
            ),
        )

        select_mock = Mock(name="notifications.select", side_effect=select_error)
        sync_unread_mock = AsyncMock(
            name="notifications.sync_unread_count",
            side_effect=AssertionError(
                "sync_unread_count must not be called when select() fails"
            ),
        )

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(notifications, "select", new=select_mock)
            )
            stack.enter_context(
                patch.object(notifications, "sync_unread_count", new=sync_unread_mock)
            )
            self.assertIs(notifications.delete_notification, original_handler)
            self.assertIs(notifications.select, select_mock)
            self.assertIs(notifications.sync_unread_count, sync_unread_mock)

            with self.assertRaises(RuntimeError) as raised:
                await notifications.delete_notification(
                    notification_id=notification_id,
                    current_user=actor,
                    db=db,
                    redis=redis,
                )

        self.assertIs(raised.exception, select_error)
        select_mock.assert_called_once_with(Notification)
        self.assertEqual(len(select_mock.call_args.args), 1)
        self.assertIs(select_mock.call_args.args[0], Notification)
        self.assertEqual(select_mock.call_args.kwargs, {})

        db.execute.assert_not_called()
        self.assertEqual(db.execute.await_count, 0)
        execute_result.scalar_one_or_none.assert_not_called()
        db.delete.assert_not_called()
        self.assertEqual(db.delete.await_count, 0)
        db.commit.assert_not_called()
        self.assertEqual(db.commit.await_count, 0)

        sync_unread_mock.assert_not_called()
        self.assertEqual(sync_unread_mock.await_count, 0)
        redis.set.assert_not_called()
        self.assertEqual(redis.set.await_count, 0)

        self.assertEqual(actor.__dict__, {"id": 4107})
        self.assertIs(notifications.delete_notification, original_handler)
        self.assertIs(notifications.select, original_select)
        self.assertIs(notifications.sync_unread_count, original_sync_unread_count)


if __name__ == "__main__":
    unittest.main()
