import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from jose import JWTError

from api.routers.auth import (
    RefreshTokenRequest,
    SetupPasswordRequest,
    refresh_access_token,
    setup_admin_password,
)
from core.enums import UserAccountStatus


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class AuthRouterSessionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_access_token_rejects_invalid_jwt_and_bad_payload(self):
        req = RefreshTokenRequest(refresh_token="bad")

        with patch("jose.jwt.decode", side_effect=JWTError()):
            with self.assertRaises(HTTPException) as exc_info:
                await refresh_access_token(req, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "توکن منقضی یا نامعتبر است")

        with patch("jose.jwt.decode", return_value={"type": "access", "sub": 5}):
            with self.assertRaises(HTTPException) as exc_info:
                await refresh_access_token(req, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "توکن نامعتبر است")

    async def test_refresh_access_token_handles_missing_deleted_or_expired_session(self):
        req = RefreshTokenRequest(refresh_token="good")
        payload = {"type": "refresh", "sub": 5}
        user = SimpleNamespace(id=5, is_deleted=False, home_server="foreign")

        with patch("jose.jwt.decode", return_value=payload), patch(
            "api.routers.auth.get_session_by_refresh_token",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await refresh_access_token(req, db=FakeDB([FakeExecuteResult(user)]))
        self.assertEqual(exc_info.exception.status_code, 401)

        deleted_user = SimpleNamespace(id=5, is_deleted=True, home_server="foreign")
        with patch("jose.jwt.decode", return_value=payload):
            with self.assertRaises(HTTPException) as exc_info:
                await refresh_access_token(req, db=FakeDB([FakeExecuteResult(deleted_user)]))
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حساب کاربری غیرفعال شده است")

        session = SimpleNamespace(
            id="sess-1",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            home_server="foreign",
            last_active_at=None,
        )
        with patch("jose.jwt.decode", return_value=payload), patch(
            "api.routers.auth.get_session_by_refresh_token",
            new=AsyncMock(return_value=session),
        ), patch("core.utils.utc_now", return_value=datetime.now(timezone.utc)):
            with self.assertRaises(HTTPException) as exc_info:
                await refresh_access_token(req, db=FakeDB([FakeExecuteResult(user)]))
        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "SESSION_EXPIRED_REQUIRE_OTP")

    async def test_refresh_access_token_updates_session_and_returns_same_refresh_token(self):
        req = RefreshTokenRequest(refresh_token="good")
        payload = {"type": "refresh", "sub": 5}
        user = SimpleNamespace(id=5, is_deleted=False, home_server="foreign")
        now = datetime.now(timezone.utc)
        session = SimpleNamespace(
            id="sess-1",
            expires_at=now + timedelta(days=1),
            home_server="iran",
            last_active_at=None,
        )
        db = FakeDB([FakeExecuteResult(user)])

        with patch("jose.jwt.decode", return_value=payload), patch(
            "api.routers.auth.get_session_by_refresh_token",
            new=AsyncMock(return_value=session),
        ), patch("core.utils.utc_now", return_value=now), patch(
            "api.routers.auth.create_access_token",
            return_value="new-access",
        ) as create_mock:
            result = await refresh_access_token(req, db=db)

        create_mock.assert_called_once_with(
            subject=5,
            expires_delta=timedelta(minutes=60),
            session_id="sess-1",
            server_id="iran",
        )
        self.assertEqual(session.last_active_at, now)
        db.commit.assert_awaited_once()
        self.assertEqual(result["access_token"], "new-access")
        self.assertEqual(result["refresh_token"], "good")

    async def test_refresh_access_token_rejects_users_whose_messenger_access_is_now_blocked(self):
        req = RefreshTokenRequest(refresh_token="good")
        payload = {"type": "refresh", "sub": 5}
        user = SimpleNamespace(
            id=5,
            is_deleted=False,
            home_server="foreign",
            account_status=UserAccountStatus.INACTIVE,
            messenger_blocked_at=datetime.now(timezone.utc),
            messenger_grace_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        with patch("jose.jwt.decode", return_value=payload):
            with self.assertRaises(HTTPException) as exc_info:
                await refresh_access_token(req, db=FakeDB([FakeExecuteResult(user)]))

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حساب کاربری غیرفعال شده است")

    async def test_setup_admin_password_rejects_invalid_token_and_missing_requirement(self):
        req = SetupPasswordRequest(password="secret1")

        with patch("jose.jwt.decode", side_effect=JWTError()):
            with self.assertRaises(HTTPException) as exc_info:
                await setup_admin_password(req, db=FakeDB(), token="bad")
        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "Invalid token")

        user = SimpleNamespace(id=5, telegram_id=None, must_change_password=False)
        db = FakeDB([FakeExecuteResult(user)])
        with patch("jose.jwt.decode", return_value={"sub": "5"}):
            with self.assertRaises(HTTPException) as exc_info:
                await setup_admin_password(req, db=db, token="good")
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "شما نیازی به تغییر رمز عبور ندارید")

    async def test_setup_admin_password_handles_short_password_and_success(self):
        user = SimpleNamespace(id=5, telegram_id=None, must_change_password=True, admin_password_hash=None)

        with patch("jose.jwt.decode", return_value={"sub": "5"}):
            with self.assertRaises(HTTPException) as exc_info:
                await setup_admin_password(SetupPasswordRequest(password="123"), db=FakeDB([FakeExecuteResult(user)]), token="good")
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "رمز عبور باید حداقل ۶ کاراکتر باشد")

        user = SimpleNamespace(id=5, telegram_id=None, must_change_password=True, admin_password_hash=None)
        db = FakeDB([FakeExecuteResult(user)])
        with patch("jose.jwt.decode", return_value={"sub": "5"}), patch(
            "api.routers.auth.get_password_hash",
            return_value="hashed-password",
        ) as hash_mock:
            result = await setup_admin_password(SetupPasswordRequest(password="123456"), db=db, token="good")

        hash_mock.assert_called_once_with("123456")
        self.assertEqual(user.admin_password_hash, "hashed-password")
        self.assertFalse(user.must_change_password)
        db.commit.assert_awaited_once()
        self.assertEqual(result, {"detail": "رمز عبور با موفقیت ثبت شد"})


if __name__ == "__main__":
    unittest.main()