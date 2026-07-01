import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers import auth
from core.enums import NotificationCategory, NotificationLevel


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, recipient_ids, telegram_rows=None):
        self.recipient_ids = recipient_ids
        self.telegram_rows = telegram_rows or []
        self.execute = AsyncMock(
            side_effect=[
                _ExecuteResult(recipient_ids),
                _ExecuteResult(self.telegram_rows),
            ]
        )
        self.rollback = AsyncMock()
        self.commit = AsyncMock()


class RegistrationNotificationTests(unittest.IsolatedAsyncioTestCase):
    def test_project_registration_announcement_skips_accountants_and_customers(self):
        self.assertTrue(auth._should_announce_project_user_registration(None, None))
        self.assertFalse(auth._should_announce_project_user_registration(object(), None))
        self.assertFalse(auth._should_announce_project_user_registration(None, object()))

    async def test_project_registration_announcement_uses_management_notification_payload(self):
        db = _FakeDb([1, 2], telegram_rows=[(7, 111), (8, 222)])
        new_user = SimpleNamespace(id=9, account_name="ali", full_name="")

        with patch("api.routers.auth.create_user_notification", new=AsyncMock()) as create_notification, patch(
            "api.routers.auth.enqueue_telegram_notifications", new=AsyncMock(return_value=[])
        ) as enqueue_telegram:
            await auth._publish_project_user_joined_notifications(db, new_user)

        self.assertEqual(create_notification.await_count, 2)
        first_call = create_notification.await_args_list[0]
        self.assertEqual(first_call.args[1], 1)
        self.assertEqual(first_call.args[2], "ali به لیست همکاران اضافه شدند.")
        self.assertEqual(first_call.args[3], NotificationLevel.INFO)
        self.assertEqual(first_call.args[4], NotificationCategory.SYSTEM)
        self.assertEqual(first_call.kwargs["extra_payload"]["title"], "پیام مدیریت")
        self.assertEqual(first_call.kwargs["extra_payload"]["route"], "/users/9?account_name=ali")
        enqueue_telegram.assert_awaited_once()
        enqueue_call = enqueue_telegram.await_args
        self.assertEqual(enqueue_call.kwargs["text"], "ali به لیست همکاران اضافه شدند.")
        self.assertEqual(enqueue_call.kwargs["source_type"], "project_user_joined")
        self.assertEqual(enqueue_call.kwargs["source_id"], 9)
        self.assertEqual(enqueue_call.kwargs["parse_mode"], None)
        self.assertTrue(enqueue_call.kwargs["extra_payload"]["exclude_customers"])
        self.assertEqual(
            [(recipient.user_id, recipient.telegram_id) for recipient in enqueue_call.kwargs["recipients"]],
            [(7, 111), (8, 222)],
        )
        db.commit.assert_awaited_once()

        self.assertEqual(db.execute.await_count, 2)
        telegram_stmt_sql = str(db.execute.await_args_list[1].args[0])
        self.assertIn("users.telegram_id IS NOT NULL", telegram_stmt_sql)
        self.assertIn("customer_relations.customer_user_id = users.id", telegram_stmt_sql)
        self.assertIn("customer_relations.status", telegram_stmt_sql)
        self.assertIn("accountant_relations.accountant_user_id = users.id", telegram_stmt_sql)
        self.assertIn("NOT (EXISTS", telegram_stmt_sql)

    async def test_project_registration_announcement_telegram_enqueue_failures_do_not_block_web_notifications(self):
        db = _FakeDb([1], telegram_rows=[(7, 111)])
        new_user = SimpleNamespace(id=9, account_name="ali", full_name="")

        with patch("api.routers.auth.create_user_notification", new=AsyncMock()) as create_notification, patch(
            "api.routers.auth.enqueue_telegram_notifications", new=AsyncMock(side_effect=RuntimeError("db down"))
        ) as enqueue_telegram, patch.object(auth.logger, "warning") as warning_mock:
            await auth._publish_project_user_joined_notifications(db, new_user)

        create_notification.assert_awaited_once()
        enqueue_telegram.assert_awaited_once()
        db.rollback.assert_awaited_once()
        warning_mock.assert_called()


if __name__ == "__main__":
    unittest.main()
