import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import NotificationCategory, NotificationLevel
from core.services import registration_notification_service as notifications


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, *execute_rows):
        self.execute = AsyncMock(
            side_effect=[_ExecuteResult(rows) for rows in execute_rows]
        )
        self.rollback = AsyncMock()
        self.commit = AsyncMock()


class _SessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _session_factory(db):
    return lambda: _SessionContext(db)


class RegistrationNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_notification_uses_application_session_factory_by_default(self):
        db = _FakeDb([])
        with patch(
            "core.db.AsyncSessionLocal",
            new=_session_factory(db),
        ):
            await notifications.publish_project_user_joined_web_notifications(
                new_user_id=9,
                account_name="ali",
                full_name="Ali",
            )
        db.execute.assert_awaited_once()

    async def test_notification_empty_recipient_and_session_failures_are_isolated(self):
        db = _FakeDb([])
        rows = await notifications.enqueue_project_user_joined_telegram_outbox(
            db,
            new_user=SimpleNamespace(id=9, account_name="ali", full_name="Ali"),
        )
        self.assertEqual(rows, [])

        await notifications.publish_project_user_joined_web_notifications(
            new_user_id=0,
            account_name="",
            full_name="",
            session_factory=_session_factory(db),
        )
        db.execute.assert_awaited_once()

        class FailingSessionContext:
            async def __aenter__(self):
                raise RuntimeError("session failed")

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch.object(notifications.logger, "warning") as warning:
            await notifications.publish_project_user_joined_web_notifications(
                new_user_id=9,
                account_name="ali",
                full_name="Ali",
                session_factory=lambda: FailingSessionContext(),
            )
        self.assertEqual(
            warning.call_args.kwargs["extra"]["event"],
            "registration.notification_session_failed",
        )
    def test_project_registration_announcement_skips_accountants_and_customers(self):
        self.assertTrue(notifications.should_announce_project_user_registration(None, None))
        self.assertFalse(notifications.should_announce_project_user_registration(object(), None))
        self.assertFalse(notifications.should_announce_project_user_registration(None, object()))

    async def test_project_registration_web_announcement_uses_management_payload(self):
        db = _FakeDb([1, 2])
        new_user = SimpleNamespace(id=9, account_name="ali", full_name="")

        with patch(
            "core.services.registration_notification_service.create_user_notification",
            new=AsyncMock(),
        ) as create_notification:
            await notifications.publish_project_user_joined_web_notifications(
                new_user_id=new_user.id,
                account_name=new_user.account_name,
                full_name=new_user.full_name,
                session_factory=_session_factory(db),
            )

        self.assertEqual(create_notification.await_count, 2)
        first_call = create_notification.await_args_list[0]
        self.assertEqual(first_call.args[1], 1)
        self.assertEqual(first_call.args[2], "ali به لیست همکاران اضافه شدند.")
        self.assertEqual(first_call.args[3], NotificationLevel.INFO)
        self.assertEqual(first_call.args[4], NotificationCategory.SYSTEM)
        self.assertEqual(first_call.kwargs["extra_payload"]["title"], "پیام مدیریت")
        self.assertEqual(first_call.kwargs["extra_payload"]["route"], "/users/9?account_name=ali")
        db.commit.assert_not_awaited()

    async def test_project_registration_telegram_outbox_is_unique_transactional_enqueue(self):
        db = _FakeDb([(7, 111), (8, 222)])
        new_user = SimpleNamespace(id=9, account_name="ali", full_name="")

        with patch(
            "core.services.registration_notification_service.enqueue_telegram_notifications",
            new=AsyncMock(return_value=[object(), object()]),
        ) as enqueue_telegram:
            rows = await notifications.enqueue_project_user_joined_telegram_outbox(
                db,
                new_user=new_user,
            )

        self.assertEqual(len(rows), 2)
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
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()

        telegram_stmt_sql = str(db.execute.await_args.args[0])
        self.assertIn("users.telegram_id IS NOT NULL", telegram_stmt_sql)
        self.assertIn("customer_relations.customer_user_id = users.id", telegram_stmt_sql)
        self.assertIn("customer_relations.status", telegram_stmt_sql)
        self.assertIn("accountant_relations.accountant_user_id = users.id", telegram_stmt_sql)
        self.assertIn("NOT (EXISTS", telegram_stmt_sql)

    async def test_web_notification_failure_does_not_reopen_registration_transaction(self):
        db = _FakeDb([1])
        new_user = SimpleNamespace(id=9, account_name="ali", full_name="")

        with patch(
            "core.services.registration_notification_service.create_user_notification",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ) as create_notification, patch.object(notifications.logger, "warning") as warning_mock:
            await notifications.publish_project_user_joined_web_notifications(
                new_user_id=new_user.id,
                account_name=new_user.account_name,
                full_name=new_user.full_name,
                session_factory=_session_factory(db),
            )

        create_notification.assert_awaited_once()
        db.rollback.assert_awaited_once()
        warning_mock.assert_called_once()

    async def test_web_notification_recipient_lookup_failure_rolls_back_only_dedicated_session(self):
        notification_db = _FakeDb()
        notification_db.execute = AsyncMock(side_effect=RuntimeError("lookup failed"))

        with patch.object(notifications.logger, "warning") as warning_mock:
            await notifications.publish_project_user_joined_web_notifications(
                new_user_id=9,
                account_name="ali",
                full_name="Ali",
                session_factory=_session_factory(notification_db),
            )

        notification_db.rollback.assert_awaited_once()
        self.assertEqual(
            warning_mock.call_args.kwargs["extra"]["event"],
            "registration.notification_recipient_lookup_failed",
        )

    async def test_telegram_outbox_failure_propagates_to_outer_registration_transaction(self):
        db = _FakeDb([(7, 111)])
        new_user = SimpleNamespace(id=9, account_name="ali", full_name="")

        with patch(
            "core.services.registration_notification_service.enqueue_telegram_notifications",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            with self.assertRaisesRegex(RuntimeError, "db down"):
                await notifications.enqueue_project_user_joined_telegram_outbox(db, new_user=new_user)

        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
