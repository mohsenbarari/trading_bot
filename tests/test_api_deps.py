from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from jose import JWTError

from api import deps
from core.enums import UserAccountStatus
from models.user import UserRole


class _ResultStub:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class ApiDepsTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_current_user_rejects_missing_subject(self):
        db = AsyncMock()
        with patch('api.deps.jwt.decode', return_value={}):
            with self.assertRaises(HTTPException) as ctx:
                await deps.get_current_user(db=db, token='token')

        self.assertEqual(ctx.exception.status_code, 401)

    async def test_get_current_user_rejects_invalid_token(self):
        db = AsyncMock()
        with patch('api.deps.jwt.decode', side_effect=JWTError('bad token')):
            with self.assertRaises(HTTPException) as ctx:
                await deps.get_current_user(db=db, token='token')

        self.assertEqual(ctx.exception.status_code, 401)

    async def test_get_current_user_rejects_blacklisted_session(self):
        session_id = str(uuid.uuid4())
        db = AsyncMock()
        with patch('api.deps.jwt.decode', return_value={'sub': '7', 'sid': session_id}), patch(
            'core.services.session_service.is_session_blacklisted', AsyncMock(return_value=True)
        ):
            with self.assertRaises(HTTPException) as ctx:
                await deps.get_current_user(db=db, token='token')

        self.assertEqual(ctx.exception.status_code, 401)

    async def test_get_current_user_rejects_missing_user_invalid_session_uuid_and_inactive_session(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_ResultStub(None), _ResultStub(None)])
        with patch('api.deps.jwt.decode', return_value={'sub': '7'}):
            with self.assertRaises(HTTPException) as ctx:
                await deps.get_current_user(db=db, token='token')
        self.assertEqual(ctx.exception.status_code, 404)

        user = SimpleNamespace(
            id=7,
            telegram_id=None,
            is_deleted=False,
            must_change_password=False,
            role=UserRole.STANDARD,
            last_seen_at=datetime.utcnow(),
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ResultStub(user))
        with patch('api.deps.jwt.decode', return_value={'sub': '7', 'sid': 'not-a-uuid'}), patch(
            'core.services.session_service.is_session_blacklisted', AsyncMock(return_value=False)
        ):
            with self.assertRaises(HTTPException) as ctx:
                await deps.get_current_user(db=db, token='token')
        self.assertEqual(ctx.exception.status_code, 401)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ResultStub(user))
        db.get = AsyncMock(return_value=SimpleNamespace(is_active=False, user_id=7))
        with patch('api.deps.jwt.decode', return_value={'sub': '7', 'sid': str(uuid.uuid4())}), patch(
            'core.services.session_service.is_session_blacklisted', AsyncMock(return_value=False)
        ):
            with self.assertRaises(HTTPException) as ctx:
                await deps.get_current_user(db=db, token='token')
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_get_current_user_falls_back_to_telegram_lookup_without_writing_on_read(self):
        session_id = str(uuid.uuid4())
        user = SimpleNamespace(
            id=7,
            telegram_id=700,
            is_deleted=False,
            must_change_password=False,
            role=UserRole.STANDARD,
            last_seen_at=datetime.utcnow() - timedelta(minutes=5),
        )
        active_session = SimpleNamespace(is_active=True, user_id=7)
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_ResultStub(None), _ResultStub(user)])
        db.get = AsyncMock(return_value=active_session)
        db.commit = AsyncMock()
        previous_last_seen = user.last_seen_at

        with patch('api.deps.jwt.decode', return_value={'sub': '700', 'sid': session_id}), patch(
            'core.services.session_service.is_session_blacklisted', AsyncMock(return_value=False)
        ):
            current_user = await deps.get_current_user(db=db, token='token')

        self.assertIs(current_user, user)
        db.commit.assert_not_awaited()
        self.assertEqual(user.last_seen_at, previous_last_seen)

    async def test_get_current_user_rejects_deleted_and_password_change_users(self):
        deleted_user = SimpleNamespace(
            id=1,
            telegram_id=None,
            is_deleted=True,
            must_change_password=False,
            role=UserRole.STANDARD,
            last_seen_at=datetime.utcnow(),
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ResultStub(deleted_user))

        with patch('api.deps.jwt.decode', return_value={'sub': '1'}):
            with self.assertRaises(HTTPException) as ctx:
                await deps.get_current_user(db=db, token='token')
        self.assertEqual(ctx.exception.status_code, 403)

        restricted_user = SimpleNamespace(
            id=2,
            telegram_id=None,
            is_deleted=False,
            must_change_password=True,
            role=UserRole.SUPER_ADMIN,
            last_seen_at=datetime.utcnow(),
        )
        db.execute = AsyncMock(return_value=_ResultStub(restricted_user))

        with patch('api.deps.jwt.decode', return_value={'sub': '2'}):
            with self.assertRaises(HTTPException) as ctx:
                await deps.get_current_user(db=db, token='token')
        self.assertEqual(ctx.exception.detail, 'REQUIRES_PASSWORD_CHANGE')

        blocked_user = SimpleNamespace(
            id=3,
            telegram_id=None,
            is_deleted=False,
            account_status=UserAccountStatus.INACTIVE,
            messenger_blocked_at=object(),
            messenger_grace_expires_at=datetime.utcnow() - timedelta(minutes=5),
            must_change_password=False,
            role=UserRole.STANDARD,
            last_seen_at=datetime.utcnow(),
        )
        db.execute = AsyncMock(return_value=_ResultStub(blocked_user))

        with patch('api.deps.jwt.decode', return_value={'sub': '3'}):
            with self.assertRaises(HTTPException) as ctx:
                await deps.get_current_user(db=db, token='token')
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, 'حساب کاربری غیرفعال شده است')

    async def test_optional_and_admin_dependencies(self):
        db = AsyncMock()
        self.assertIsNone(await deps.get_current_user_optional(db=db, token=None))

        with patch('api.deps.get_current_user', AsyncMock(side_effect=HTTPException(status_code=401))):
            self.assertIsNone(await deps.get_current_user_optional(db=db, token='token'))

        current_user = SimpleNamespace(id=4)
        context = SimpleNamespace(owner_user=SimpleNamespace(id=9))
        with patch('api.deps.resolve_effective_owner_actor', AsyncMock(return_value=context)):
            self.assertIs(await deps.get_effective_owner_actor_context(current_user=current_user, db=db), context)
        self.assertEqual((await deps.get_effective_owner_user(context=context)).id, 9)

        admin = SimpleNamespace(role=UserRole.SUPER_ADMIN)
        self.assertIs(await deps.verify_super_admin(current_user=admin), admin)

        with self.assertRaises(HTTPException):
            await deps.verify_super_admin(current_user=SimpleNamespace(role=UserRole.STANDARD))

        middle = SimpleNamespace(role=UserRole.MIDDLE_MANAGER)
        self.assertIs(await deps.verify_admin_user(current_user=middle), middle)
        with self.assertRaises(HTTPException):
            await deps.verify_admin_user(current_user=SimpleNamespace(role=UserRole.STANDARD))

    async def test_verify_super_admin_or_dev_key_supports_dev_key_and_admin_token(self):
        db = AsyncMock()
        with patch.object(deps.settings, 'dev_api_key', 'dev-key'):
            self.assertIsNone(await deps.verify_super_admin_or_dev_key(token=None, dev_key='dev-key', db=db))

        admin = SimpleNamespace(role=UserRole.SUPER_ADMIN)
        with patch('api.deps.get_current_user', AsyncMock(return_value=admin)):
            self.assertIs(
                await deps.verify_super_admin_or_dev_key(token='token', dev_key=None, db=db),
                admin,
            )

        with patch('api.deps.get_current_user', AsyncMock(return_value=SimpleNamespace(role=UserRole.STANDARD))):
            with self.assertRaises(HTTPException) as ctx:
                await deps.verify_super_admin_or_dev_key(token='token', dev_key=None, db=db)
        self.assertEqual(ctx.exception.status_code, 403)

        with patch.object(deps.settings, 'dev_api_key', 'dev-key'):
            self.assertIsNone(await deps.verify_admin_or_dev_key(token=None, dev_key='dev-key', db=db))

        admin = SimpleNamespace(role=UserRole.MIDDLE_MANAGER)
        with patch('api.deps.get_current_user', AsyncMock(return_value=admin)):
            self.assertIs(await deps.verify_admin_or_dev_key(token='token', dev_key=None, db=db), admin)

        with patch('api.deps.get_current_user', AsyncMock(return_value=SimpleNamespace(role=UserRole.STANDARD))):
            with self.assertRaises(HTTPException) as ctx:
                await deps.verify_admin_or_dev_key(token='token', dev_key=None, db=db)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == '__main__':
    unittest.main()
