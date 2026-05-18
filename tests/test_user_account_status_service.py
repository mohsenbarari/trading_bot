import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.enums import NotificationCategory, NotificationLevel, UserAccountStatus
from core.services import user_account_status_service as status_service


class _ScalarListResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarListResult(self._items)


class UserAccountStatusHelperTests(unittest.TestCase):
    def test_helper_defaults_and_messenger_block_logic(self):
        user = SimpleNamespace(account_status=None, messenger_grace_expires_at=None, messenger_blocked_at=None)
        self.assertEqual(status_service.get_user_account_status(user), UserAccountStatus.ACTIVE)
        self.assertFalse(status_service.is_user_market_blocked(user))

        now = datetime(2026, 5, 18, 12, 0, 0)
        inactive_user = SimpleNamespace(
            account_status=UserAccountStatus.INACTIVE,
            messenger_grace_expires_at=now - timedelta(minutes=1),
            messenger_blocked_at=None,
        )
        self.assertTrue(status_service.is_user_messenger_blocked(inactive_user, now=now))


class UserAccountStatusTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_transition_user_account_status_deactivates_and_notifies(self):
        now = datetime(2026, 5, 18, 12, 0, 0)
        user = SimpleNamespace(
            id=7,
            telegram_id=71,
            account_status=UserAccountStatus.ACTIVE,
            deactivated_at=None,
            messenger_grace_expires_at=None,
            messenger_blocked_at=None,
        )
        db = SimpleNamespace()

        with patch.object(status_service, "_utcnow_naive", return_value=now), patch(
            "core.services.user_account_status_service.create_user_notification",
            new=AsyncMock(),
        ) as create_notification, patch(
            "core.services.user_account_status_service.send_telegram_notification",
            new=AsyncMock(),
        ) as send_telegram, patch(
            "core.services.user_account_status_service.remove_user_from_telegram_channel",
            new=AsyncMock(),
        ) as remove_from_channel:
            result = await status_service.transition_user_account_status(db, user, UserAccountStatus.INACTIVE)

        self.assertTrue(result.changed)
        self.assertEqual(user.account_status, UserAccountStatus.INACTIVE)
        self.assertEqual(user.deactivated_at, now)
        self.assertEqual(user.messenger_grace_expires_at, now + status_service.INACTIVE_MESSENGER_GRACE_PERIOD)
        self.assertIsNone(user.messenger_blocked_at)
        self.assertEqual(create_notification.await_args.args[1], 7)
        self.assertEqual(create_notification.await_args.args[3], NotificationLevel.WARNING)
        self.assertEqual(create_notification.await_args.args[4], NotificationCategory.SYSTEM)
        send_telegram.assert_awaited_once()
        remove_from_channel.assert_awaited_once_with(71)

    async def test_transition_user_account_status_reactivates_and_attaches_join_line(self):
        user = SimpleNamespace(
            id=8,
            telegram_id=81,
            account_status=UserAccountStatus.INACTIVE,
            deactivated_at=datetime(2026, 5, 16, 12, 0, 0),
            messenger_grace_expires_at=datetime(2026, 5, 18, 12, 0, 0),
            messenger_blocked_at=datetime(2026, 5, 18, 12, 1, 0),
        )
        db = SimpleNamespace()

        with patch(
            "core.services.user_account_status_service.create_user_notification",
            new=AsyncMock(),
        ) as create_notification, patch(
            "core.services.user_account_status_service.send_telegram_notification",
            new=AsyncMock(),
        ) as send_telegram, patch.object(
            status_service,
            "_build_activation_join_line",
            new=AsyncMock(return_value="🔗 [درخواست عضویت](https://example.test/join)"),
        ):
            result = await status_service.transition_user_account_status(db, user, UserAccountStatus.ACTIVE)

        self.assertTrue(result.changed)
        self.assertEqual(user.account_status, UserAccountStatus.ACTIVE)
        self.assertIsNone(user.deactivated_at)
        self.assertIsNone(user.messenger_grace_expires_at)
        self.assertIsNone(user.messenger_blocked_at)
        create_notification.assert_awaited_once()
        send_telegram.assert_awaited_once()
        self.assertIn("درخواست عضویت", send_telegram.await_args.args[1])

    async def test_transition_user_account_status_is_idempotent_for_existing_inactive_state(self):
        now = datetime(2026, 5, 18, 12, 0, 0)
        existing_grace = now + timedelta(days=1)
        user = SimpleNamespace(
            id=9,
            telegram_id=None,
            account_status=UserAccountStatus.INACTIVE,
            deactivated_at=now - timedelta(days=1),
            messenger_grace_expires_at=existing_grace,
            messenger_blocked_at=None,
        )

        with patch(
            "core.services.user_account_status_service.create_user_notification",
            new=AsyncMock(),
        ) as create_notification:
            result = await status_service.transition_user_account_status(SimpleNamespace(), user, UserAccountStatus.INACTIVE)

        self.assertFalse(result.changed)
        self.assertEqual(user.messenger_grace_expires_at, existing_grace)
        create_notification.assert_not_awaited()

    async def test_mark_due_users_messenger_blocked_marks_owner_and_notifies_accountants(self):
        now = datetime(2026, 5, 18, 12, 0, 0)
        owner = SimpleNamespace(
            id=11,
            telegram_id=111,
            account_status=UserAccountStatus.INACTIVE,
            messenger_grace_expires_at=now - timedelta(minutes=5),
            messenger_blocked_at=None,
            is_deleted=False,
        )
        accountant_user = SimpleNamespace(id=12, telegram_id=222)
        relation = SimpleNamespace(accountant_user=accountant_user)
        db = SimpleNamespace(execute=AsyncMock(return_value=_ExecuteResult([owner])))

        with patch.object(status_service, "_utcnow_naive", return_value=now), patch.object(
            status_service,
            "list_active_accountants_for_owner",
            new=AsyncMock(return_value=[relation]),
        ), patch(
            "core.services.user_account_status_service.force_clear_sessions",
            new=AsyncMock(return_value=2),
        ) as force_clear_sessions, patch(
            "core.services.user_account_status_service.create_user_notification",
            new=AsyncMock(),
        ) as create_notification, patch(
            "core.services.user_account_status_service.send_telegram_notification",
            new=AsyncMock(),
        ) as send_telegram:
            blocked_count = await status_service.mark_due_users_messenger_blocked(db)

        self.assertEqual(blocked_count, 1)
        self.assertEqual(owner.messenger_blocked_at, now)
        self.assertEqual(create_notification.await_count, 2)
        self.assertEqual(send_telegram.await_count, 2)
        force_clear_sessions.assert_awaited_once_with(db, owner.id)