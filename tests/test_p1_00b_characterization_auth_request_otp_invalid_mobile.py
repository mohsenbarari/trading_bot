"""Stage P1-00b characterization tests for request_otp invalid-mobile guard.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-AUTH-POST-AUTH-REQUEST-OTP-L2454
"""

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()
FENCE = install_p1_00_network_fence()

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.routers.auth import OTPRequest, request_otp


class StrictFailingRawRequest:
    """Sentinel raw_request double that fails if any attribute is consulted."""

    def __getattr__(self, name: str):
        raise AssertionError(f"raw_request was consulted unexpectedly: {name!r}")


class StrictAsyncDB:
    """Strict async DB double whose execute fails if awaited and exposes an execution count."""

    def __init__(self):
        self.execution_count = 0

    async def execute(self, *args, **kwargs):
        self.execution_count += 1
        raise AssertionError("db.execute must not be awaited or called on invalid mobile")


class AuthRequestOtpInvalidMobileCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_mobile_rejects_before_db_redis_or_otp_delivery_work(self):
        invalid_short_mobile = "۰۹۱۲"
        request_payload = OTPRequest(mobile_number=invalid_short_mobile)
        raw_request_sentinel = StrictFailingRawRequest()
        db_double = StrictAsyncDB()

        denied_connects_before = len(FENCE["denied_connects"])
        live_redis_before = len(FENCE["live_redis_constructs"])

        select_spy = MagicMock(side_effect=AssertionError("select must not be called"))
        get_user_status_spy = MagicMock(
            side_effect=AssertionError("get_user_account_status must not be called")
        )
        login_home_server_spy = MagicMock(
            side_effect=AssertionError("_login_home_server must not be called")
        )
        assert_login_allowed_spy = AsyncMock(
            side_effect=AssertionError("assert_login_allowed_for_server must not be called")
        )
        find_pending_invitation_spy = AsyncMock(
            side_effect=AssertionError("_find_pending_invitation_for_mobile must not be called")
        )
        get_redis_spy = AsyncMock(side_effect=AssertionError("get_redis must not be called"))
        request_stage6_otp_spy = AsyncMock(
            side_effect=AssertionError("_request_stage6_login_otp must not be called")
        )
        configured_tg_mode_spy = MagicMock(
            side_effect=AssertionError("configured_telegram_delivery_producer_mode must not be called")
        )
        generate_otp_spy = MagicMock(
            side_effect=AssertionError("_generate_otp_code must not be called")
        )
        deliver_staging_log_spy = MagicMock(
            side_effect=AssertionError("_deliver_otp_via_staging_log must not be called")
        )
        is_internet_connected_spy = AsyncMock(
            side_effect=AssertionError("is_internet_connected must not be called")
        )
        send_tg_msg_spy = AsyncMock(
            side_effect=AssertionError("send_telegram_message must not be called")
        )
        send_otp_sms_spy = MagicMock(
            side_effect=AssertionError("send_otp_sms must not be called")
        )
        deliver_stage6_sms_spy = AsyncMock(
            side_effect=AssertionError("_deliver_stage6_sms must not be called")
        )

        with patch("api.routers.auth.select", select_spy), patch(
            "api.routers.auth.get_user_account_status", get_user_status_spy
        ), patch("api.routers.auth._login_home_server", login_home_server_spy), patch(
            "api.routers.auth.assert_login_allowed_for_server", assert_login_allowed_spy
        ), patch(
            "api.routers.auth._find_pending_invitation_for_mobile", find_pending_invitation_spy
        ), patch(
            "api.routers.auth.get_redis", get_redis_spy
        ), patch(
            "api.routers.auth._request_stage6_login_otp", request_stage6_otp_spy
        ), patch(
            "api.routers.auth.configured_telegram_delivery_producer_mode", configured_tg_mode_spy
        ), patch(
            "api.routers.auth._generate_otp_code", generate_otp_spy
        ), patch(
            "api.routers.auth._deliver_otp_via_staging_log", deliver_staging_log_spy
        ), patch(
            "api.routers.auth.is_internet_connected", is_internet_connected_spy
        ), patch(
            "api.routers.auth.send_telegram_message", send_tg_msg_spy
        ), patch(
            "api.routers.auth.send_otp_sms", send_otp_sms_spy
        ), patch(
            "api.routers.auth._deliver_stage6_sms", deliver_stage6_sms_spy
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await request_otp(
                    request=request_payload,
                    raw_request=raw_request_sentinel,
                    db=db_double,
                )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "شماره موبایل نامعتبر است")

        self.assertEqual(db_double.execution_count, 0)

        select_spy.assert_not_called()
        get_user_status_spy.assert_not_called()
        login_home_server_spy.assert_not_called()
        assert_login_allowed_spy.assert_not_called()
        find_pending_invitation_spy.assert_not_called()
        get_redis_spy.assert_not_called()
        request_stage6_otp_spy.assert_not_called()
        configured_tg_mode_spy.assert_not_called()
        generate_otp_spy.assert_not_called()
        deliver_staging_log_spy.assert_not_called()
        is_internet_connected_spy.assert_not_called()
        send_tg_msg_spy.assert_not_called()
        send_otp_sms_spy.assert_not_called()
        deliver_stage6_sms_spy.assert_not_called()

        self.assertEqual(len(FENCE["denied_connects"]), denied_connects_before)
        self.assertEqual(len(FENCE["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
