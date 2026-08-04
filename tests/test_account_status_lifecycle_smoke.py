import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import schemas
from fastapi import HTTPException
from api.routers.auth import RefreshTokenRequest, refresh_access_token
from api.routers.users import update_user
from core.enums import UserAccountStatus, UserRole
from core.services import user_account_status_service as status_service


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeScalarListResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class FakeListExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return FakeScalarListResult(self._items)


class FakeUserDB:
    def __init__(self, user):
        self.user = user
        self.commits = 0
        self.refreshes = 0

    async def get(self, _model, _user_id):
        return self.user

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        self.refreshes += 1


class FakeAuthDB:
    def __init__(self, user):
        self.user = user
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        return FakeExecuteResult(self.user)


class FakeLockDB:
    def __init__(self, users):
        self.users = list(users)

    async def execute(self, _stmt):
        return FakeListExecuteResult(self.users)


def make_user(**overrides):
    data = {
        'id': 41,
        'telegram_id': 410,
        'full_name': 'Test User',
        'account_name': 'test-user',
        'mobile_number': '09120000041',
        'role': UserRole.STANDARD,
        'account_status': UserAccountStatus.ACTIVE,
        'deactivated_at': None,
        'messenger_grace_expires_at': None,
        'messenger_blocked_at': None,
        'is_deleted': False,
        'deleted_at': None,
        'has_bot_access': True,
        'home_server': 'foreign',
        'trading_restricted_until': None,
        'max_daily_trades': None,
        'max_active_commodities': None,
        'max_daily_requests': None,
        'limitations_expire_at': None,
        'trades_count': 0,
        'commodities_traded_count': 0,
        'channel_messages_count': 0,
        'max_sessions': 1,
        'max_accountants': 3,
        'max_customers': 5,
        'can_block_users': True,
        'max_blocked_users': 10,
        'created_at': datetime(2026, 5, 18, 8, 0, 0),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class AccountStatusLifecycleSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enqueue_account_notice = AsyncMock()
        self.enqueue_account_notice_patcher = patch(
            'core.services.telegram_notification_outbox_service.'
            'enqueue_account_status_telegram_notification_once',
            new=self.enqueue_account_notice,
        )
        self.enqueue_account_notice_patcher.start()

    async def asyncTearDown(self):
        self.enqueue_account_notice_patcher.stop()

    async def test_admin_deactivate_to_global_lock_refresh_deny_and_reactivate(self):
        admin = SimpleNamespace(role=UserRole.SUPER_ADMIN)
        user = make_user()
        user_db = FakeUserDB(user)
        deactivated_at = datetime(2026, 5, 18, 9, 0, 0)
        locked_at = deactivated_at + status_service.INACTIVE_GLOBAL_LOCK_GRACE_PERIOD + timedelta(minutes=5)

        with patch('api.routers.users.is_user_accountant', new=AsyncMock(return_value=False)), patch(
            'api.routers.users.track_limitation_changes', return_value=([], False, False)
        ), patch(
            'api.routers.users.sync_mandatory_channel_for_user_state_change', new=AsyncMock()
        ), patch('core.cache.invalidate_user_cache', new=AsyncMock()), patch(
            'api.routers.users.send_block_notification', new=AsyncMock()
        ), patch(
            'api.routers.users.send_limitation_notification', new=AsyncMock()
        ), patch('api.routers.users.asyncio.create_task'), patch.object(
            status_service, '_utcnow_naive', return_value=deactivated_at
        ), patch(
            'core.services.user_account_status_service.create_user_notification', new=AsyncMock()
        ) as create_notification, patch(
            'core.services.user_account_status_service.send_telegram_notification', new=AsyncMock()
        ) as send_telegram, patch(
            'core.services.user_account_status_service.remove_user_from_telegram_channel', new=AsyncMock()
        ) as remove_from_channel:
            result = await update_user(
                user.id,
                schemas.UserUpdate(account_status=UserAccountStatus.INACTIVE),
                db=user_db,
                actor=admin,
            )

        self.assertEqual(result.id, user.id)
        self.assertEqual(result.account_status, UserAccountStatus.INACTIVE)
        self.assertEqual(user.account_status, UserAccountStatus.INACTIVE)
        self.assertEqual(user.deactivated_at, deactivated_at)
        self.assertEqual(
            user.messenger_grace_expires_at,
            deactivated_at + status_service.INACTIVE_GLOBAL_LOCK_GRACE_PERIOD,
        )
        self.assertIsNone(user.messenger_blocked_at)
        self.assertFalse(status_service.is_user_global_web_locked(user, now=deactivated_at + timedelta(minutes=1)))
        create_notification.assert_awaited_once()
        send_telegram.assert_not_awaited()
        remove_from_channel.assert_not_awaited()
        self.assertEqual(self.enqueue_account_notice.await_count, 1)

        lock_db = FakeLockDB([user])
        with patch.object(status_service, '_utcnow_naive', return_value=locked_at), patch.object(
            status_service, 'list_active_accountants_for_owner', new=AsyncMock(return_value=[])
        ), patch(
            'core.services.user_account_status_service.force_clear_sessions', new=AsyncMock(return_value=1)
        ) as force_clear_sessions, patch(
            'core.services.user_account_status_service.create_user_notification', new=AsyncMock()
        ) as lock_notification, patch(
            'core.services.user_account_status_service.send_telegram_notification', new=AsyncMock()
        ) as lock_telegram:
            blocked_count = await status_service.mark_due_users_globally_locked(lock_db)

        self.assertEqual(blocked_count, 1)
        self.assertEqual(user.messenger_blocked_at, locked_at)
        self.assertTrue(status_service.is_user_global_web_locked(user, now=locked_at))
        force_clear_sessions.assert_awaited_once_with(lock_db, user.id)
        lock_notification.assert_awaited_once()
        lock_telegram.assert_not_awaited()
        self.assertEqual(self.enqueue_account_notice.await_count, 2)

        with patch('jose.jwt.decode', return_value={'type': 'refresh', 'sub': user.id}):
            with self.assertRaises(HTTPException) as exc_info:
                await refresh_access_token(RefreshTokenRequest(refresh_token='refresh-token'), db=FakeAuthDB(user))
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, 'حساب کاربری غیرفعال شده است')

        reactivated_db = FakeUserDB(user)
        with patch('api.routers.users.is_user_accountant', new=AsyncMock(return_value=False)), patch(
            'api.routers.users.track_limitation_changes', return_value=([], False, False)
        ), patch(
            'api.routers.users.sync_mandatory_channel_for_user_state_change', new=AsyncMock()
        ), patch('core.cache.invalidate_user_cache', new=AsyncMock()), patch(
            'api.routers.users.send_block_notification', new=AsyncMock()
        ), patch(
            'api.routers.users.send_limitation_notification', new=AsyncMock()
        ), patch('api.routers.users.asyncio.create_task'), patch(
            'core.services.user_account_status_service.create_user_notification', new=AsyncMock()
        ) as reactivated_notification, patch(
            'core.services.user_account_status_service.send_telegram_notification', new=AsyncMock()
        ) as reactivated_telegram, patch.object(
            status_service, '_build_activation_join_line', new=AsyncMock(return_value='join-line')
        ):
            result = await update_user(
                user.id,
                schemas.UserUpdate(account_status=UserAccountStatus.ACTIVE),
                db=reactivated_db,
                actor=admin,
            )

        self.assertEqual(result.id, user.id)
        self.assertEqual(result.account_status, UserAccountStatus.ACTIVE)
        self.assertEqual(user.account_status, UserAccountStatus.ACTIVE)
        self.assertIsNone(user.deactivated_at)
        self.assertIsNone(user.messenger_grace_expires_at)
        self.assertIsNone(user.messenger_blocked_at)
        self.assertFalse(status_service.is_user_global_web_locked(user, now=locked_at))
        reactivated_notification.assert_awaited_once()
        reactivated_telegram.assert_not_awaited()
        self.assertEqual(self.enqueue_account_notice.await_count, 3)


if __name__ == '__main__':
    unittest.main()
