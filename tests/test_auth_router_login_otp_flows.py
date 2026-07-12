import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from api.routers.auth import (
    OTPRequest,
    OTPVerify,
    _extract_device_info,
    _login_home_server,
    _otp_verify_subject_key,
    request_otp,
    resend_otp_sms,
    verify_otp,
)
from core.session_authority import ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE
from core.enums import UserAccountStatus
from models.session import Platform
from models.user import UserRole


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class FakeRedis:
    def __init__(self, values=None, ttl_map=None):
        self.values = dict(values or {})
        self.ttl_map = dict(ttl_map or {})
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

    async def ttl(self, key):
        return self.ttl_map.get(key, -2)

    async def incr(self, key):
        self.incr_calls.append(key)
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        self.ttl_map[key] = ttl
        return True


def make_request(headers=None, host="127.0.0.1"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


class AuthRouterLoginOtpFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_device_info_and_login_home_server_cover_mobile_invalid_platform_and_wrapper(self):
        request = make_request(headers={"user-agent": "Mobile Safari", "x-platform": "unknown"}, host="10.1.2.3")
        info = _extract_device_info(request)
        self.assertEqual(info["device_name"], "Mobile Browser")
        self.assertEqual(info["device_ip"], "10.1.2.3")
        self.assertEqual(info["platform"], Platform.WEB)

        with patch("api.routers.auth.server_from_request", return_value="foreign") as server_mock:
            self.assertEqual(_login_home_server(request, is_telegram=True), "foreign")
        server_mock.assert_called_once_with(request, force_telegram_foreign=True)

    async def test_request_otp_rejects_invalid_mobile_or_user_state(self):
        with self.assertRaises(HTTPException) as exc_info:
            await request_otp(OTPRequest(mobile_number="123"), raw_request=make_request(), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "شماره موبایل نامعتبر است")

        with patch("api.routers.auth._find_pending_invitation_for_mobile", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as exc_info:
                await request_otp(
                    OTPRequest(mobile_number="09120000000"),
                    raw_request=make_request(),
                    db=FakeDB([FakeExecuteResult(None)]),
                )
        self.assertEqual(exc_info.exception.status_code, 404)

        deleted_user = SimpleNamespace(is_deleted=True, telegram_id=None)
        with self.assertRaises(HTTPException) as exc_info:
            await request_otp(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(deleted_user)]),
            )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حساب کاربری غیرفعال شده است")

        inactive_user = SimpleNamespace(is_deleted=False, telegram_id=None, account_status=UserAccountStatus.INACTIVE)
        with self.assertRaises(HTTPException) as exc_info:
            await request_otp(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(inactive_user)]),
            )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حساب کاربری غیرفعال شده است")

    async def test_request_otp_blocks_rate_limited_or_active_codes(self):
        user = SimpleNamespace(is_deleted=False, telegram_id=123)
        redis = FakeRedis({"otp_limit:09120000000": "1"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await request_otp(
                    OTPRequest(mobile_number="09120000000"),
                    raw_request=make_request(),
                    db=FakeDB([FakeExecuteResult(user)]),
                )
        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "لطفاً ۲ دقیقه صبر کنید")

        redis = FakeRedis({"otp:09120000000": "12345"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await request_otp(
                    OTPRequest(mobile_number="09120000000"),
                    raw_request=make_request(),
                    db=FakeDB([FakeExecuteResult(user)]),
                )
        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "کد قبلی هنوز معتبر است. لطفاً صبر کنید.")

    async def test_flags_off_active_otp_returns_structured_recovery_without_code(self):
        user = SimpleNamespace(is_deleted=False, telegram_id=123)
        redis = FakeRedis(
            {
                "otp_limit:09120000000": "1",
                "otp:09120000000": "12345",
            },
            ttl_map={"otp:09120000000": 73},
        )
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            response = await request_otp(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(user)]),
            )

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 429)
        payload = json.loads(response.body)
        self.assertEqual(payload["code"], "otp_active")
        self.assertEqual(payload["delivery_contract"], "legacy")
        self.assertEqual(payload["expires_in"], 73)
        self.assertNotIn("12345", response.body.decode())
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_otp_request_and_resend_logs_do_not_include_code_digits(self):
        user = SimpleNamespace(is_deleted=False, telegram_id=123)
        redis = FakeRedis()
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._generate_otp_code",
            return_value="12345",
        ), patch("api.routers.auth.is_internet_connected", new=AsyncMock(return_value=True)), patch(
            "api.routers.auth.send_telegram_message",
            new=AsyncMock(),
        ), patch("api.routers.auth.send_otp_sms", return_value=True), self.assertLogs(
            "api.routers.auth",
            level="INFO",
        ) as captured:
            await request_otp(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(user)]),
            )

        joined = "\n".join(captured.output)
        self.assertNotIn("12345", joined)
        self.assertNotIn("12***", joined)

        redis = FakeRedis({"otp:09120000000": "12345"}, ttl_map={"otp:09120000000": 87})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.send_otp_sms",
            return_value=True,
        ), self.assertLogs("api.routers.auth", level="INFO") as captured:
            await resend_otp_sms(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(user)]),
            )

        joined = "\n".join(captured.output)
        self.assertNotIn("12345", joined)
        self.assertNotIn("12***", joined)

    async def test_request_otp_blocks_remote_home_active_session_before_generating_code(self):
        user = SimpleNamespace(id=7, is_deleted=False, telegram_id=None, home_server="iran")

        with patch("api.routers.auth._login_home_server", return_value="foreign"), patch(
            "api.routers.auth.assert_login_allowed_for_server",
            new=AsyncMock(side_effect=HTTPException(status_code=409, detail=ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE)),
        ) as authority_mock, patch("api.routers.auth.get_redis", new=AsyncMock(side_effect=AssertionError("OTP must not be created"))):
            with self.assertRaises(HTTPException) as exc_info:
                await request_otp(
                    OTPRequest(mobile_number="09120000000"),
                    raw_request=make_request(),
                    db=FakeDB([FakeExecuteResult(user)]),
                )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE)
        authority_mock.assert_awaited_once()

    async def test_request_otp_sends_via_telegram_when_available(self):
        user = SimpleNamespace(is_deleted=False, telegram_id=456)
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._generate_otp_code",
            return_value="12345",
        ), patch(
            "api.routers.auth.is_internet_connected",
            new=AsyncMock(return_value=True),
        ), patch(
            "api.routers.auth.send_telegram_message",
            new=AsyncMock(),
        ) as send_tg_mock, patch("api.routers.auth.send_otp_sms", return_value=True) as send_sms_mock:
            result = await request_otp(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(user)]),
            )

        self.assertEqual(
            redis.setex_calls,
            [("otp:09120000000", 120, "12345"), ("otp_limit:09120000000", 120, "1")],
        )
        send_tg_mock.assert_awaited_once()
        send_sms_mock.assert_not_called()
        self.assertEqual(result, {"detail": "کد تایید ارسال شد", "method": "telegram", "expires_in": 120})

    async def test_request_otp_allows_pending_invitation_mobile_without_user(self):
        invitation = SimpleNamespace(token="INV-1")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._find_pending_invitation_for_mobile",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth._generate_otp_code",
            return_value="22222",
        ), patch(
            "api.routers.auth.is_internet_connected",
            new=AsyncMock(return_value=False),
        ), patch("api.routers.auth.send_otp_sms", return_value=True) as send_sms_mock:
            result = await request_otp(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(None)]),
            )

        send_sms_mock.assert_called_once_with("09120000000", "22222")
        self.assertEqual(result["method"], "sms")

    async def test_request_otp_can_deliver_via_staging_log_without_sms_or_telegram(self):
        invitation = SimpleNamespace(token="INV-1")
        redis = FakeRedis()

        with patch.object(request_otp.__globals__["settings"], "environment", "staging"), patch.object(
            request_otp.__globals__["settings"], "staging_log_otp_codes", True
        ), patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._find_pending_invitation_for_mobile",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth._generate_otp_code",
            return_value="67890",
        ), patch(
            "api.routers.auth.is_internet_connected",
            new=AsyncMock(side_effect=AssertionError("staging log delivery must not check connectivity")),
        ), patch("api.routers.auth.send_otp_sms") as send_sms_mock, patch(
            "api.routers.auth.send_telegram_message",
            new=AsyncMock(side_effect=AssertionError("staging log delivery must not use telegram")),
        ), self.assertLogs("api.routers.auth", level="WARNING") as captured:
            result = await request_otp(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(None)]),
            )

        self.assertEqual(
            redis.setex_calls,
            [("otp:09120000000", 120, "67890"), ("otp_limit:09120000000", 120, "1")],
        )
        send_sms_mock.assert_not_called()
        self.assertEqual(result, {"detail": "کد تایید در لاگ staging ثبت شد", "method": "log", "expires_in": 120})
        self.assertIn("STAGING_AUTH_VALUE_FOR_TEST_ONLY", "\n".join(captured.output))
        self.assertIn("value=67890", "\n".join(captured.output))

    async def test_request_otp_falls_back_to_sms_when_telegram_unavailable(self):
        user = SimpleNamespace(is_deleted=False, telegram_id=456)
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._generate_otp_code",
            return_value="54321",
        ), patch(
            "api.routers.auth.is_internet_connected",
            new=AsyncMock(return_value=True),
        ), patch(
            "api.routers.auth.send_telegram_message",
            new=AsyncMock(side_effect=RuntimeError("telegram down")),
        ), patch("api.routers.auth.send_otp_sms", return_value=True) as send_sms_mock:
            result = await request_otp(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(user)]),
            )

        send_sms_mock.assert_called_once_with("09120000000", "54321")
        self.assertEqual(result["method"], "sms")

        user = SimpleNamespace(is_deleted=False, telegram_id=None)
        redis = FakeRedis()
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._generate_otp_code",
            return_value="11111",
        ), patch(
            "api.routers.auth.is_internet_connected",
            new=AsyncMock(return_value=False),
        ), patch("api.routers.auth.send_otp_sms", return_value=False):
            with self.assertRaises(HTTPException) as exc_info:
                await request_otp(
                    OTPRequest(mobile_number="09120000000"),
                    raw_request=make_request(),
                    db=FakeDB([FakeExecuteResult(user)]),
                )
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "خطا در ارسال کد تایید")
        self.assertEqual(redis.delete_calls, ["otp:09120000000", "otp_limit:09120000000"])

    async def test_resend_otp_sms_blocks_remote_home_active_session_before_sms(self):
        user = SimpleNamespace(id=7, is_deleted=False, home_server="iran")
        redis = FakeRedis({"otp:09120000000": "12345"}, ttl_map={"otp:09120000000": 87})

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._login_home_server",
            return_value="foreign",
        ), patch(
            "api.routers.auth.assert_login_allowed_for_server",
            new=AsyncMock(side_effect=HTTPException(status_code=409, detail=ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE)),
        ) as authority_mock, patch("api.routers.auth.send_otp_sms", return_value=True) as send_sms_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await resend_otp_sms(
                    OTPRequest(mobile_number="09120000000"),
                    raw_request=make_request(),
                    db=FakeDB([FakeExecuteResult(user)]),
                )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE)
        authority_mock.assert_awaited_once()
        send_sms_mock.assert_not_called()

    async def test_resend_otp_sms_handles_missing_rate_limited_and_success_paths(self):
        redis = FakeRedis()
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await resend_otp_sms(
                    OTPRequest(mobile_number="09120000000"),
                    raw_request=make_request(),
                    db=FakeDB(),
                )
        self.assertEqual(exc_info.exception.status_code, 400)

        redis = FakeRedis({"otp:09120000000": "12345", "sms_limit:09120000000": "1"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await resend_otp_sms(
                    OTPRequest(mobile_number="09120000000"),
                    raw_request=make_request(),
                    db=FakeDB([FakeExecuteResult(SimpleNamespace(is_deleted=False, home_server="foreign"))]),
                )
        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "لطفاً ۱ دقیقه صبر کنید")

        redis = FakeRedis({"otp:09120000000": "12345"}, ttl_map={"otp:09120000000": 87})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.send_otp_sms",
            return_value=True,
        ) as send_sms_mock:
            result = await resend_otp_sms(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(SimpleNamespace(is_deleted=False, home_server="foreign"))]),
            )

        send_sms_mock.assert_called_once_with("09120000000", "12345")
        self.assertIn(("sms_limit:09120000000", 60, "1"), redis.setex_calls)
        self.assertEqual(result, {"detail": "کد از طریق پیامک ارسال شد", "expires_in": 87})

        redis = FakeRedis({"otp:09120000000": "12345"}, ttl_map={"otp:09120000000": -1})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.send_otp_sms",
            return_value=True,
        ):
            result = await resend_otp_sms(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(SimpleNamespace(is_deleted=False, home_server="foreign"))]),
            )
        self.assertEqual(result["expires_in"], 0)

        redis = FakeRedis({"otp:09120000000": "12345"}, ttl_map={"otp:09120000000": 10})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.send_otp_sms",
            return_value=False,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await resend_otp_sms(
                    OTPRequest(mobile_number="09120000000"),
                    raw_request=make_request(),
                    db=FakeDB([FakeExecuteResult(SimpleNamespace(is_deleted=False, home_server="foreign"))]),
                )
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "خطا در ارسال پیامک")

    async def test_resend_otp_sms_allows_pending_invitation_mobile_without_user(self):
        redis = FakeRedis({"otp:09120000000": "12345"}, ttl_map={"otp:09120000000": 54})
        invitation = SimpleNamespace(token="INV-2")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._find_pending_invitation_for_mobile",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch("api.routers.auth.send_otp_sms", return_value=True) as send_sms_mock:
            result = await resend_otp_sms(
                OTPRequest(mobile_number="09120000000"),
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(None)]),
            )

        send_sms_mock.assert_called_once_with("09120000000", "12345")
        self.assertEqual(result["expires_in"], 54)

    async def test_verify_otp_blocks_remote_home_active_session_and_requires_new_request(self):
        request = OTPVerify(mobile_number="09120000000", code="12345")
        user = SimpleNamespace(id=7, home_server="iran", account_status=UserAccountStatus.ACTIVE)
        redis = FakeRedis({"otp:09120000000": "12345"})

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._login_home_server",
            return_value="foreign",
        ), patch(
            "api.routers.auth.assert_login_allowed_for_server",
            new=AsyncMock(side_effect=HTTPException(status_code=409, detail=ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE)),
        ) as authority_mock, patch("api.routers.auth.handle_login_session", new=AsyncMock()) as handle_session_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await verify_otp(request, raw_request=make_request(), db=FakeDB([FakeExecuteResult(user)]))

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE)
        self.assertEqual(redis.delete_calls, ["otp:09120000000", "otp_limit:09120000000"])
        authority_mock.assert_awaited_once()
        handle_session_mock.assert_not_awaited()

    async def test_verify_otp_rejects_invalid_code_missing_user_and_blocked_session(self):
        request = OTPVerify(mobile_number="09120000000", code="12345")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await verify_otp(request, raw_request=make_request(), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        redis = FakeRedis({"otp:09120000000": "12345"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._find_pending_invitation_for_mobile",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await verify_otp(request, raw_request=make_request(), db=FakeDB([FakeExecuteResult(None)]))
        self.assertEqual(exc_info.exception.status_code, 404)

        inactive_user = SimpleNamespace(id=7, home_server="foreign", account_status=UserAccountStatus.INACTIVE)
        redis = FakeRedis({"otp:09120000000": "12345"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await verify_otp(request, raw_request=make_request(), db=FakeDB([FakeExecuteResult(inactive_user)]))
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حساب کاربری غیرفعال شده است")

    async def test_verify_otp_throttles_failed_attempts_and_invalidates_code(self):
        request = OTPVerify(mobile_number="09120000000", code="00000")
        redis = FakeRedis({"otp:09120000000": "12345"}, ttl_map={"otp:09120000000": 90})

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            for _ in range(4):
                with self.assertRaises(HTTPException) as exc_info:
                    await verify_otp(request, raw_request=make_request(host="10.0.0.1"), db=FakeDB())
                self.assertEqual(exc_info.exception.status_code, 400)

            with self.assertRaises(HTTPException) as exc_info:
                await verify_otp(request, raw_request=make_request(host="10.0.0.1"), db=FakeDB())

        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "تعداد تلاش‌های ناموفق زیاد است. چند دقیقه دیگر دوباره تلاش کنید.")
        self.assertNotIn("otp:09120000000", redis.values)
        self.assertIn("otp:09120000000", redis.delete_calls)
        self.assertTrue(any(call[0].startswith("otp_verify_lock:subject:") for call in redis.setex_calls))

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await verify_otp(
                    OTPVerify(mobile_number="09120000000", code="12345"),
                    raw_request=make_request(host="10.0.0.1"),
                    db=FakeDB(),
                )
        self.assertEqual(exc_info.exception.status_code, 429)

    async def test_verify_otp_success_clears_failed_attempt_counter(self):
        failure_key = _otp_verify_subject_key("09120000000")
        redis = FakeRedis({"otp:09120000000": "12345", failure_key: "2"})
        user = SimpleNamespace(id=7, home_server="iran")
        session = SimpleNamespace(id="session-1")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth._login_home_server",
            return_value="iran",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"action": "ok", "session": session}),
        ), patch("api.routers.auth.create_access_token", return_value="access-token"):
            result = await verify_otp(
                OTPVerify(mobile_number="09120000000", code="12345"),
                raw_request=make_request(host="10.0.0.1"),
                db=FakeDB([FakeExecuteResult(user)]),
            )

        self.assertEqual(result["access_token"], "access-token")
        self.assertIn(failure_key, redis.delete_calls)

    async def test_verify_otp_returns_registration_session_for_pending_invitation_mobile(self):
        request = OTPVerify(mobile_number="09120000000", code="12345")
        invitation = SimpleNamespace(
            token="INV-REG",
            account_name="user1",
            mobile_number="09120000000",
            role=UserRole.STANDARD,
        )
        redis = FakeRedis({"otp:09120000000": "12345"})

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._find_pending_invitation_for_mobile",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth._store_registration_session",
            new=AsyncMock(return_value=("REG-token", 600)),
        ) as store_mock:
            result = await verify_otp(
                request,
                raw_request=make_request(),
                db=FakeDB([FakeExecuteResult(None)]),
            )

        store_mock.assert_awaited_once()
        self.assertEqual(redis.delete_calls, ["otp:09120000000", "otp_limit:09120000000"])
        self.assertEqual(result["status"], "registration_required")
        self.assertEqual(result["registration_token"], "REG-token")
        self.assertEqual(redis.delete_calls, ["otp:09120000000", "otp_limit:09120000000"])

        user = SimpleNamespace(id=7, home_server="iran")
        redis = FakeRedis({"otp:09120000000": "12345"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth._login_home_server",
            return_value="iran",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"action": "blocked", "reason": "too many"}),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await verify_otp(request, raw_request=make_request(), db=FakeDB([FakeExecuteResult(user)]))
        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "too many")

        redis = FakeRedis({"otp:09120000000": "12345"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth._login_home_server",
            return_value="iran",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"action": "blocked", "reason": "ACCOUNT_INACTIVE_BLOCK_REASON"}),
        ), patch("api.routers.auth.ACCOUNT_INACTIVE_BLOCK_REASON", "ACCOUNT_INACTIVE_BLOCK_REASON"):
            with self.assertRaises(HTTPException) as exc_info:
                await verify_otp(request, raw_request=make_request(), db=FakeDB([FakeExecuteResult(user)]))
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حساب کاربری غیرفعال شده است")

    async def test_verify_otp_returns_approval_required_or_tokens(self):
        request = OTPVerify(mobile_number="09120000000", code="12345", suspended_refresh_token="old-refresh")
        raw_request = make_request(headers={"x-device-name": "Pixel", "x-platform": "web"}, host="10.0.0.8")
        user = SimpleNamespace(id=7, home_server="iran")

        approval_request = SimpleNamespace(id="req-1", expires_at=datetime.utcnow() + timedelta(minutes=5))
        redis = FakeRedis({"otp:09120000000": "12345"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth._login_home_server",
            return_value="iran",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"action": "approval_required", "request": approval_request}),
        ) as handle_session_mock:
            result = await verify_otp(request, raw_request=raw_request, db=FakeDB([FakeExecuteResult(user)]))

        self.assertEqual(redis.delete_calls, ["otp:09120000000", "otp_limit:09120000000"])
        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(result["login_request_id"], "req-1")
        self.assertEqual(handle_session_mock.await_args.kwargs["device_name"], "Pixel")
        self.assertEqual(handle_session_mock.await_args.kwargs["device_ip"], "10.0.0.8")
        self.assertEqual(handle_session_mock.await_args.kwargs["platform"], Platform.WEB)
        self.assertEqual(handle_session_mock.await_args.kwargs["suspended_refresh_token"], "old-refresh")

        redis = FakeRedis({"otp:09120000000": "12345"})
        session = SimpleNamespace(id="session-1")
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth._login_home_server",
            return_value="iran",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"action": "ok", "session": session}),
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="access-token",
        ) as access_mock:
            result = await verify_otp(request, raw_request=raw_request, db=FakeDB([FakeExecuteResult(user)]))

        access_mock.assert_called_once_with(
            subject=7,
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


if __name__ == "__main__":
    unittest.main()
