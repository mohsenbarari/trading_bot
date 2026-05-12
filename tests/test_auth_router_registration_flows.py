import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.auth import (
    RegisterComplete,
    RegisterOTPRequest,
    RegisterOTPVerify,
    register_complete,
    register_otp_request,
    register_otp_verify,
)
from models.session import Platform


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None, commit_side_effect=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock(side_effect=commit_side_effect)
        self.flush = AsyncMock(side_effect=self._flush)
        self.refresh = AsyncMock(side_effect=self._refresh)
        self.rollback = AsyncMock()
        self.added = []

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, item):
        self.added.append(item)

    async def _flush(self):
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = 77

    async def _refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = 77
        return item


class FakeRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.setex_calls = []
        self.delete_calls = []

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.values[key] = value

    async def delete(self, key):
        self.delete_calls.append(key)
        self.values.pop(key, None)


def make_request(headers=None, host="127.0.0.1"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


class AuthRouterRegistrationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_otp_request_rejects_invalid_invitation_states_and_rate_limit(self):
        req = RegisterOTPRequest(token="abc")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB([FakeExecuteResult(None)]))
        self.assertEqual(exc_info.exception.status_code, 404)

        used_invitation = SimpleNamespace(
            is_used=True,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            mobile_number="0912",
        )
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB([FakeExecuteResult(used_invitation)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "دعوت‌نامه قبلاً استفاده شده است")

        expired_invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() - timedelta(seconds=1),
            mobile_number="0912",
        )
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB([FakeExecuteResult(expired_invitation)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "دعوت‌نامه منقضی شده است")

        valid_invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            mobile_number="0912",
        )
        rate_limited_redis = FakeRedis({"otp_limit:0912": "1"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=rate_limited_redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB([FakeExecuteResult(valid_invitation)]))
        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "لطفاً ۲ دقیقه صبر کنید")

    async def test_register_otp_request_sets_otp_and_returns_success(self):
        req = RegisterOTPRequest(token="abc")
        invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            mobile_number="09120000000",
        )
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.random.randint",
            return_value=12345,
        ), patch("api.routers.auth.send_otp_sms", return_value=True) as send_sms_mock:
            result = await register_otp_request(req, db=FakeDB([FakeExecuteResult(invitation)]))

        self.assertEqual(
            redis.setex_calls,
            [("reg_otp:abc", 120, "12345"), ("otp_limit:09120000000", 120, "1")],
        )
        send_sms_mock.assert_called_once_with("09120000000", "12345")
        self.assertEqual(result, {"detail": "کد تایید ارسال شد", "expires_in": 120})

    async def test_register_otp_request_rejects_invalid_accountant_relation_tokens(self):
        req = RegisterOTPRequest(token="ACCT-token")
        invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            mobile_number="09120000000",
        )
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.get_pending_accountant_relation_by_invitation_token",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB([FakeExecuteResult(invitation)]))
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_register_otp_verify_rejects_invalid_code_and_persists_verified_flag(self):
        req = RegisterOTPVerify(token="abc", code="12345")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_verify(req, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "کد تایید نامعتبر یا منقضی شده است")

        redis = FakeRedis({"reg_otp:abc": "12345"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            result = await register_otp_verify(req, db=FakeDB())

        self.assertEqual(redis.delete_calls, ["reg_otp:abc"])
        self.assertIn(("reg_verified:abc", 600, "1"), redis.setex_calls)
        self.assertEqual(result, {"detail": "کد تایید شد"})

    async def test_register_complete_requires_verified_token_and_valid_invitation(self):
        req = RegisterComplete(token="abc", address="Tehran")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(req, raw_request=make_request(), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "لطفاً ابتدا کد تایید را وارد کنید")

        redis = FakeRedis({"reg_verified:abc": "1"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(req, raw_request=make_request(), db=FakeDB([FakeExecuteResult(None)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "دعوت‌نامه نامعتبر است")

    async def test_register_complete_rolls_back_on_commit_error(self):
        invitation = SimpleNamespace(
            token="abc",
            account_name="user1",
            mobile_number="0912",
            role="standard",
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        redis = FakeRedis({"reg_verified:abc": "1"})
        db = FakeDB([FakeExecuteResult(invitation)], commit_side_effect=RuntimeError("db down"))

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._login_home_server",
            return_value="foreign",
        ), patch(
            "api.routers.auth.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(RegisterComplete(token="abc", address="Tehran"), raw_request=make_request(), db=db)

        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "خطا در ثبت کاربر")
        db.rollback.assert_awaited_once()

    async def test_register_complete_creates_user_marks_invitation_and_issues_tokens(self):
        invitation = SimpleNamespace(
            token="abc",
            account_name="user1",
            mobile_number="09120000000",
            role="standard",
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        redis = FakeRedis({"reg_verified:abc": "1", "reg_otp:abc": "12345"})
        db = FakeDB([FakeExecuteResult(invitation)])
        request = make_request(
            headers={"user-agent": "Mobile Safari", "x-platform": "web", "x-device-name": "iPhone"},
            host="10.0.0.8",
        )
        session = SimpleNamespace(id="session-1")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._login_home_server",
            return_value="iran",
        ), patch(
            "api.routers.auth.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ) as mandatory_mock, patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ) as refresh_mock, patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"session": session}),
        ) as handle_session_mock, patch(
            "api.routers.auth.create_access_token",
            return_value="access-token",
        ) as access_mock:
            result = await register_complete(
                RegisterComplete(token="abc", address="Tehran"),
                raw_request=request,
                db=db,
            )

        self.assertEqual(len(db.added), 1)
        new_user = db.added[0]
        self.assertEqual(new_user.account_name, "user1")
        self.assertEqual(new_user.mobile_number, "09120000000")
        self.assertEqual(new_user.address, "Tehran")
        self.assertEqual(new_user.home_server, "iran")
        self.assertTrue(new_user.has_bot_access)
        self.assertIsNone(new_user.telegram_id)
        self.assertTrue(invitation.is_used)
        db.flush.assert_awaited_once()
        self.assertIs(mandatory_mock.await_args.kwargs["user"], new_user)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(new_user)
        self.assertEqual(redis.delete_calls, ["reg_otp:abc", "reg_verified:abc"])
        refresh_mock.assert_called_once_with(subject=77, expires_delta=timedelta(days=30))
        handle_session_mock.assert_awaited_once()
        self.assertEqual(handle_session_mock.await_args.args, (db, new_user, "refresh-token"))
        self.assertEqual(handle_session_mock.await_args.kwargs["device_name"], "iPhone")
        self.assertEqual(handle_session_mock.await_args.kwargs["device_ip"], "10.0.0.8")
        self.assertEqual(handle_session_mock.await_args.kwargs["platform"], Platform.WEB)
        self.assertEqual(handle_session_mock.await_args.kwargs["home_server"], "iran")
        access_mock.assert_called_once_with(
            subject=77,
            expires_delta=timedelta(minutes=60),
            session_id="session-1",
            server_id="iran",
        )
        self.assertEqual(
            result,
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "token_type": "bearer",
            },
        )

    async def test_register_complete_binds_pending_accountant_relation_and_disables_bot_access(self):
        invitation = SimpleNamespace(
            token="ACCT-token",
            account_name="accountant1",
            mobile_number="09120000000",
            role="watch",
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        relation = SimpleNamespace(
            accountant_user_id=None,
            status="pending",
            activated_at=None,
            deleted_at=None,
        )
        redis = FakeRedis({"reg_verified:ACCT-token": "1"})
        db = FakeDB([FakeExecuteResult(invitation)])
        session = SimpleNamespace(id="session-acc")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.get_pending_accountant_relation_by_invitation_token",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.auth._login_home_server",
            return_value="foreign",
        ), patch(
            "api.routers.auth.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-acc",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"session": session}),
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="access-acc",
        ):
            result = await register_complete(
                RegisterComplete(token="ACCT-token", address="Tehran, Valiasr"),
                raw_request=make_request(),
                db=db,
            )

        new_user = db.added[0]
        self.assertFalse(new_user.has_bot_access)
        self.assertEqual(relation.accountant_user_id, 77)
        self.assertEqual(relation.status, "active")
        self.assertIsNotNone(relation.activated_at)
        self.assertEqual(result["access_token"], "access-acc")


if __name__ == "__main__":
    unittest.main()