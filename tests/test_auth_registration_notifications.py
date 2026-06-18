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
    def __init__(self, recipient_ids):
        self.recipient_ids = recipient_ids
        self.execute = AsyncMock(return_value=_ScalarResult(recipient_ids))
        self.rollback = AsyncMock()


class RegistrationNotificationTests(unittest.IsolatedAsyncioTestCase):
    def test_project_registration_announcement_skips_accountants_and_customers(self):
        self.assertTrue(auth._should_announce_project_user_registration(None, None))
        self.assertFalse(auth._should_announce_project_user_registration(object(), None))
        self.assertFalse(auth._should_announce_project_user_registration(None, object()))

    async def test_project_registration_announcement_uses_management_notification_payload(self):
        db = _FakeDb([1, 2])
        new_user = SimpleNamespace(id=9, account_name="ali", full_name="")

        with patch("api.routers.auth.create_user_notification", new=AsyncMock()) as create_notification:
            await auth._publish_project_user_joined_notifications(db, new_user)

        self.assertEqual(create_notification.await_count, 2)
        first_call = create_notification.await_args_list[0]
        self.assertEqual(first_call.args[1], 1)
        self.assertEqual(first_call.args[2], "ali به لیست همکاران اضافه شدند.")
        self.assertEqual(first_call.args[3], NotificationLevel.INFO)
        self.assertEqual(first_call.args[4], NotificationCategory.SYSTEM)
        self.assertEqual(first_call.kwargs["extra_payload"]["title"], "پیام مدیریت")
        self.assertEqual(first_call.kwargs["extra_payload"]["route"], "/users/9?account_name=ali")


if __name__ == "__main__":
    unittest.main()
