import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.auth import (
    PendingRegistrationContext,
    RegisterComplete,
    RegisterOTPRequest,
    RegisterOTPVerify,
    get_pending_registration,
    register_complete,
    register_otp_request,
    register_otp_verify,
)
from core.registration_contracts import TelegramRegistrationOutcome
from core.services.authoritative_registration_service import (
    AuthoritativeRegistrationError,
    AuthoritativeRegistrationResult,
)
from models.session import Platform
from models.user import UserRole


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
        self.expire_calls = []
        self.incr_calls = []

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.values[key] = value

    async def delete(self, key):
        self.delete_calls.append(key)
        self.values.pop(key, None)

    async def incr(self, key):
        self.incr_calls.append(key)
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        return True

    async def ttl(self, key):
        return 120 if key in self.values else -2


def make_request(headers=None, host="127.0.0.1"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


class AuthRouterRegistrationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_otp_request_rejects_invalid_invitation_states_and_rate_limit(self):
        req = RegisterOTPRequest(token="abc")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="دعوت‌نامه نامعتبر است")),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 404)

        valid_invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            mobile_number="0912",
        )
        rate_limited_redis = FakeRedis({"otp_limit:0912": "1"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=rate_limited_redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(valid_invitation, None, None)),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB())
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
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth.random.randint",
            return_value=12345,
        ), patch("api.routers.auth.send_otp_sms", return_value=True) as send_sms_mock:
            result = await register_otp_request(req, db=FakeDB())

        self.assertEqual(
            redis.setex_calls,
            [("reg_otp:abc", 120, "12345"), ("otp_limit:09120000000", 120, "1")],
        )
        send_sms_mock.assert_called_once_with("09120000000", "12345")
        self.assertEqual(result, {"detail": "کد تایید ارسال شد", "expires_in": 120})

        redis = FakeRedis()
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth.random.randint",
            return_value=12345,
        ), patch("api.routers.auth.send_otp_sms", return_value=False):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "خطا در ارسال پیامک")
        self.assertEqual(redis.delete_calls, ["reg_otp:abc", "otp_limit:09120000000"])

    async def test_register_otp_request_can_deliver_via_staging_log_without_sms(self):
        req = RegisterOTPRequest(token="abc")
        invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            mobile_number="09120000000",
        )
        redis = FakeRedis()

        with patch.object(register_otp_request.__globals__["settings"], "environment", "staging"), patch.object(
            register_otp_request.__globals__["settings"], "staging_log_otp_codes", True
        ), patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth.random.randint",
            return_value=54321,
        ), patch("api.routers.auth.send_otp_sms") as send_sms_mock, self.assertLogs(
            "api.routers.auth", level="WARNING"
        ) as captured:
            result = await register_otp_request(req, db=FakeDB())

        self.assertEqual(
            redis.setex_calls,
            [("reg_otp:abc", 120, "54321"), ("otp_limit:09120000000", 120, "1")],
        )
        send_sms_mock.assert_not_called()
        self.assertEqual(result, {"detail": "کد تایید در لاگ staging ثبت شد", "expires_in": 120})
        self.assertIn("STAGING_AUTH_VALUE_FOR_TEST_ONLY", "\n".join(captured.output))
        self.assertIn("value=54321", "\n".join(captured.output))

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

    async def test_register_otp_verify_throttles_repeated_invalid_codes(self):
        req = RegisterOTPVerify(token="abc", code="00000")
        redis = FakeRedis({"reg_otp:abc": "12345"})

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            for _ in range(4):
                with self.assertRaises(HTTPException) as exc_info:
                    await register_otp_verify(req, db=FakeDB())
                self.assertEqual(exc_info.exception.status_code, 400)

            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_verify(req, db=FakeDB())

        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "تعداد تلاش‌های ناموفق زیاد است. چند دقیقه دیگر دوباره تلاش کنید.")
        self.assertIn("reg_otp:abc", redis.delete_calls)
        self.assertNotIn("reg_otp:abc", redis.values)
        self.assertTrue(any(call[0].startswith("otp_verify_lock:subject:") for call in redis.setex_calls))

    async def test_register_complete_requires_verified_token_and_valid_invitation(self):
        req = RegisterComplete(token="abc", address="Tehran address")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(req, raw_request=make_request(), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "لطفاً ابتدا کد تایید را وارد کنید")

        redis = FakeRedis({"reg_verified:abc": "1"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(
                side_effect=AuthoritativeRegistrationError(
                    TelegramRegistrationOutcome.INVITATION_NOT_FOUND,
                    public_detail="دعوت‌نامه نامعتبر است",
                )
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(req, raw_request=make_request(), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "دعوت‌نامه نامعتبر است")

    async def test_register_complete_maps_authoritative_transaction_error_without_issuing_session(self):
        redis = FakeRedis({"reg_verified:abc": "1"})
        db = FakeDB()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ), patch("api.routers.auth.handle_login_session", new=AsyncMock()) as session_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(RegisterComplete(token="abc", address="Tehran address"), raw_request=make_request(), db=db)

        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "خطا در ثبت کاربر")
        session_mock.assert_not_awaited()

    async def test_register_complete_creates_user_marks_invitation_and_issues_tokens(self):
        new_user = SimpleNamespace(
            id=77,
            account_name="user1",
            mobile_number="09120000000",
            address="Tehran address",
            home_server="iran",
            has_bot_access=True,
            telegram_id=None,
        )
        registration_result = AuthoritativeRegistrationResult(
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=77,
            user=new_user,
            announce_project_user=True,
            first_terminal_transition=True,
        )
        redis = FakeRedis({"reg_verified:abc": "1", "reg_otp:abc": "12345"})
        db = FakeDB()
        request = make_request(
            headers={"user-agent": "Mobile Safari", "x-platform": "web", "x-device-name": "iPhone"},
            host="10.0.0.8",
        )
        session = SimpleNamespace(id="session-1")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(return_value=registration_result),
        ) as complete_mock, patch(
            "api.routers.auth.publish_project_user_joined_web_notifications",
            new=AsyncMock(),
        ) as notification_mock, patch(
            "api.routers.auth._login_home_server",
            return_value="foreign",
        ) as home_server_mock, patch(
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
                RegisterComplete(token="abc", address="Tehran address"),
                raw_request=request,
                db=db,
            )

        complete_mock.assert_awaited_once()
        self.assertIs(complete_mock.await_args.args[0], db)
        service_request = complete_mock.await_args.args[1]
        self.assertEqual(service_request.invitation_token, "abc")
        self.assertEqual(service_request.address, "Tehran address")
        self.assertEqual(service_request.source_surface.value, "webapp")
        self.assertEqual(service_request.identity_proof_type.value, "web_otp")
        home_server_mock.assert_not_called()
        notification_mock.assert_awaited_once_with(db, new_user=new_user)
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

    async def test_get_pending_registration_and_registration_session_complete(self):
        invitation = SimpleNamespace(
            token="INV-123",
            account_name="user1",
            mobile_number="09120000000",
            role=UserRole.STANDARD,
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        redis = FakeRedis({"registration_session:REG-123": "INV-123"})
        db = FakeDB()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ):
            context = await get_pending_registration("REG-123", db=db)

        self.assertEqual(
            context,
            PendingRegistrationContext(
                token="INV-123",
                account_name="user1",
                mobile_number="09120000000",
                role=UserRole.STANDARD,
            ),
        )

        redis = FakeRedis({"registration_session:REG-123": "INV-123"})
        session = SimpleNamespace(id="session-1")
        new_user = SimpleNamespace(id=77, home_server="iran")
        registration_result = AuthoritativeRegistrationResult(
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=77,
            user=new_user,
        )
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(return_value=registration_result),
        ) as complete_mock, patch(
            "api.routers.auth.publish_project_user_joined_web_notifications",
            new=AsyncMock(),
        ), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"session": session}),
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="access-token",
        ):
            result = await register_complete(
                RegisterComplete(registration_token="REG-123", address="Tehran address"),
                raw_request=make_request(),
                db=db,
            )

        self.assertIn("registration_session:REG-123", redis.delete_calls)
        self.assertEqual(complete_mock.await_args.args[1].invitation_token, "INV-123")
        self.assertEqual(result["access_token"], "access-token")

    async def test_register_complete_binds_pending_accountant_relation_and_disables_bot_access(self):
        relation = SimpleNamespace(
            accountant_user_id=77,
            status="active",
            activated_at=datetime.now(),
            deleted_at=None,
        )
        new_user = SimpleNamespace(id=77, home_server="iran", has_bot_access=False)
        registration_result = AuthoritativeRegistrationResult(
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=77,
            user=new_user,
            accountant_relation=relation,
        )
        redis = FakeRedis({"reg_verified:ACCT-token": "1"})
        db = FakeDB()
        session = SimpleNamespace(id="session-acc")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(return_value=registration_result),
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

        self.assertFalse(new_user.has_bot_access)
        self.assertEqual(relation.accountant_user_id, 77)
        self.assertEqual(relation.status, "active")
        self.assertIsNotNone(relation.activated_at)
        self.assertEqual(result["access_token"], "access-acc")

    async def test_register_complete_rejects_missing_accountant_relation(self):
        redis = FakeRedis({"reg_verified:ACCT-token": "1"})

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(
                side_effect=AuthoritativeRegistrationError(
                    TelegramRegistrationOutcome.INVALID_RELATION,
                    public_detail="دعوت‌نامه حسابدار نامعتبر یا منقضی شده است",
                )
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(
                    RegisterComplete(token="ACCT-token", address="Tehran address"),
                    raw_request=make_request(),
                    db=FakeDB(),
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "دعوت‌نامه حسابدار نامعتبر یا منقضی شده است")

    async def test_register_complete_binds_pending_customer_relation_and_disables_bot_access(self):
        relation = SimpleNamespace(
            customer_user_id=77,
            management_name="mohsen",
            status="active",
            activated_at=datetime.now(),
            deleted_at=None,
        )
        new_user = SimpleNamespace(id=77, home_server="iran", full_name="mohsen", has_bot_access=False)
        registration_result = AuthoritativeRegistrationResult(
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=77,
            user=new_user,
            customer_relation=relation,
        )
        redis = FakeRedis({"reg_verified:CUST-token": "1"})
        db = FakeDB()
        session = SimpleNamespace(id="session-cust")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(return_value=registration_result),
        ), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-cust",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"session": session}),
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="access-cust",
        ):
            result = await register_complete(
                RegisterComplete(token="CUST-token", address="Tehran, Vanak"),
                raw_request=make_request(),
                db=db,
            )

        self.assertEqual(new_user.full_name, "mohsen")
        self.assertFalse(new_user.has_bot_access)
        self.assertEqual(relation.customer_user_id, 77)
        self.assertEqual(relation.status, "active")
        self.assertIsNotNone(relation.activated_at)
        self.assertEqual(result["access_token"], "access-cust")

    async def test_register_complete_rejects_missing_customer_relation(self):
        redis = FakeRedis({"reg_verified:CUST-token": "1"})

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(
                side_effect=AuthoritativeRegistrationError(
                    TelegramRegistrationOutcome.INVALID_RELATION,
                    public_detail="دعوت‌نامه مشتری نامعتبر یا منقضی شده است",
                )
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(
                    RegisterComplete(token="CUST-token", address="Tehran address"),
                    raw_request=make_request(),
                    db=FakeDB(),
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "دعوت‌نامه مشتری نامعتبر یا منقضی شده است")


if __name__ == "__main__":
    unittest.main()
