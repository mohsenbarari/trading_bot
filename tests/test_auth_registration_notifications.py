import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers import auth
from core.enums import NotificationCategory, NotificationLevel


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, recipient_ids, telegram_ids=None):
        self.recipient_ids = recipient_ids
        self.telegram_ids = telegram_ids or []
        self.execute = AsyncMock(
            side_effect=[
                _ScalarResult(recipient_ids),
                _ScalarResult(self.telegram_ids),
            ]
        )
        self.rollback = AsyncMock()


class RegistrationNotificationTests(unittest.IsolatedAsyncioTestCase):
    def test_project_registration_announcement_skips_accountants_and_customers(self):
        self.assertTrue(auth._should_announce_project_user_registration(None, None))
        self.assertFalse(auth._should_announce_project_user_registration(object(), None))
        self.assertFalse(auth._should_announce_project_user_registration(None, object()))

    async def test_project_registration_announcement_uses_management_notification_payload(self):
        db = _FakeDb([1, 2], telegram_ids=[111, 222])
        new_user = SimpleNamespace(id=9, account_name="ali", full_name="")

        with patch("api.routers.auth.create_user_notification", new=AsyncMock()) as create_notification, patch(
            "api.routers.auth.send_telegram_message", new=AsyncMock()
        ) as send_telegram:
            await auth._publish_project_user_joined_notifications(db, new_user)

        self.assertEqual(create_notification.await_count, 2)
        first_call = create_notification.await_args_list[0]
        self.assertEqual(first_call.args[1], 1)
        self.assertEqual(first_call.args[2], "ali به لیست همکاران اضافه شدند.")
        self.assertEqual(first_call.args[3], NotificationLevel.INFO)
        self.assertEqual(first_call.args[4], NotificationCategory.SYSTEM)
        self.assertEqual(first_call.kwargs["extra_payload"]["title"], "پیام مدیریت")
        self.assertEqual(first_call.kwargs["extra_payload"]["route"], "/users/9?account_name=ali")
        self.assertEqual(send_telegram.await_count, 2)
        send_telegram.assert_any_await(111, "ali به لیست همکاران اضافه شدند.")
        send_telegram.assert_any_await(222, "ali به لیست همکاران اضافه شدند.")

        self.assertEqual(db.execute.await_count, 2)
        telegram_stmt_sql = str(db.execute.await_args_list[1].args[0])
        self.assertIn("users.telegram_id IS NOT NULL", telegram_stmt_sql)
        self.assertIn("customer_relations.customer_user_id = users.id", telegram_stmt_sql)
        self.assertIn("customer_relations.status", telegram_stmt_sql)
        self.assertIn("NOT (EXISTS", telegram_stmt_sql)

    async def test_project_registration_announcement_telegram_failures_do_not_block_web_notifications(self):
        db = _FakeDb([1], telegram_ids=[111])
        new_user = SimpleNamespace(id=9, account_name="ali", full_name="")

        with patch("api.routers.auth.create_user_notification", new=AsyncMock()) as create_notification, patch(
            "api.routers.auth.send_telegram_message", new=AsyncMock(side_effect=RuntimeError("telegram down"))
        ) as send_telegram, patch.object(auth.logger, "warning") as warning_mock:
            await auth._publish_project_user_joined_notifications(db, new_user)

        create_notification.assert_awaited_once()
        send_telegram.assert_awaited_once_with(111, "ali به لیست همکاران اضافه شدند.")
        warning_mock.assert_called()


if __name__ == "__main__":
    unittest.main()
