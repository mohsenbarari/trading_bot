"""Stage P1-00b characterization tests for resend_otp_sms structured state.

Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-AUTH-POST-AUTH-RESEND-OTP-SMS-L2612
Gap Reference: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Hermetic characterization for the real api.routers.auth.resend_otp_sms enabled
structured-state branch under telegram_login_otp_enabled=True.
Freezes the exact ACCEPTED response/side effects and both non-ACCEPTED
SMSDeliveryOutcome values (FAILED and AMBIGUOUS) without mutating product code,
documentation, existing tests, inventory status, or Stage state.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.routers.auth import OTPRequest, resend_otp_sms
from core.config import settings
from core.enums import UserAccountStatus
from core.registration_contracts import (
    OTPDeliveryStateContract,
    OTPDeliveryStatus,
    OTPRequestStatus,
)
from core.sms import SMSDeliveryOutcome
from models.user import User, UserRole

EXACT_MOBILE = "09123456789"
EXACT_OTP_CODE = "123456"
EXACT_OTP_REQUEST_ID = UUID("11111111-2222-3333-4444-555555555555")

DETERMINISTIC_USER = User(
    id=42,
    account_name="active_characterization_user",
    mobile_number=EXACT_MOBILE,
    full_name="Active Characterization User",
    address="Tehran, Iran",
    role=UserRole.STANDARD,
    account_status=UserAccountStatus.ACTIVE,
    is_deleted=False,
    home_server="foreign",
)

CREATED_AT = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
EXPIRES_AT = datetime(2026, 9, 12, 12, 2, 0, tzinfo=timezone.utc)
SMS_FALLBACK_AT = datetime(2026, 9, 12, 12, 0, 40, tzinfo=timezone.utc)
PINNED_NOW = datetime(2026, 9, 12, 12, 0, 10, tzinfo=timezone.utc)

VALID_DELIVERY_STATE = OTPDeliveryStateContract(
    otp_request_id=EXACT_OTP_REQUEST_ID,
    purpose="web_login",
    identity_digest="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    delivery_target_ciphertext="ciphertext_must_be_at_least_32_bytes_long",
    status=OTPRequestStatus.PENDING,
    created_at=CREATED_AT,
    expires_at=EXPIRES_AT,
    telegram_delivery_status=OTPDeliveryStatus.PENDING,
    sms_fallback_at=SMS_FALLBACK_AT,
    sms_delivery_status=OTPDeliveryStatus.NOT_ATTEMPTED,
)


def _build_mock_db(user: User) -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=db_result)
    return db


def _build_mock_redis(*, mobile: str, otp_code: str) -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=[
        otp_code,
        None,
    ])
    redis.ttl = AsyncMock(return_value=95)
    redis.setex = AsyncMock()
    redis.set = MagicMock()
    redis.delete = MagicMock()
    redis.hset = MagicMock()
    redis.hdel = MagicMock()
    redis.expire = MagicMock()
    return redis


class TestAuthResendOtpSmsStructuredState(unittest.IsolatedAsyncioTestCase):
    """Characterization suite for resend_otp_sms structured-state branch."""

    async def test_resend_otp_sms_structured_state_accepted(self) -> None:
        """Freeze ACCEPTED response and Redis rate-limit side effects."""
        user = DETERMINISTIC_USER
        delivery_state = VALID_DELIVERY_STATE
        db = _build_mock_db(user)
        raw_request = MagicMock(spec=Request)
        request = OTPRequest(mobile_number=EXACT_MOBILE)

        with (
            patch.object(settings, "telegram_login_otp_enabled", True),
            patch("api.routers.auth.get_redis", new_callable=AsyncMock) as mock_get_redis,
            patch("api.routers.auth._login_home_server", return_value="foreign") as mock_login_home_server,
            patch("api.routers.auth.assert_login_allowed_for_server", new_callable=AsyncMock) as mock_assert_login_allowed,
            patch("api.routers.auth._find_pending_invitation_for_mobile", new_callable=AsyncMock) as mock_find_pending_invitation,
            patch("api.routers.auth.load_otp_delivery_state", new_callable=AsyncMock) as mock_load_state,
            patch("api.routers.auth._deliver_stage6_sms", new_callable=AsyncMock) as mock_deliver_stage6_sms,
            patch("api.routers.auth.send_otp_sms") as mock_send_otp_sms,
            patch("api.routers.auth.utc_now", return_value=PINNED_NOW),
        ):
            redis = _build_mock_redis(mobile=EXACT_MOBILE, otp_code=EXACT_OTP_CODE)
            mock_get_redis.return_value = redis
            mock_load_state.return_value = delivery_state
            mock_deliver_stage6_sms.return_value = SMSDeliveryOutcome.ACCEPTED

            response = await resend_otp_sms(
                request=request,
                raw_request=raw_request,
                db=db,
            )

            mock_get_redis.assert_awaited_once_with()
            db.execute.assert_awaited_once()
            db.execute.return_value.scalar_one_or_none.assert_called_once_with()
            mock_login_home_server.assert_called_once_with(raw_request)
            mock_assert_login_allowed.assert_awaited_once_with(
                db,
                user,
                requested_server="foreign",
            )
            self.assertIs(mock_assert_login_allowed.await_args.args[0], db)
            self.assertIs(mock_assert_login_allowed.await_args.args[1], user)
            self.assertEqual(
                mock_assert_login_allowed.await_args.kwargs,
                {"requested_server": "foreign"},
            )
            mock_find_pending_invitation.assert_not_called()

            self.assertEqual(
                redis.get.await_args_list,
                [call(f"otp:{EXACT_MOBILE}"), call(f"sms_limit:{EXACT_MOBILE}")],
            )
            redis.ttl.assert_awaited_once_with(f"otp:{EXACT_MOBILE}")

            mock_load_state.assert_awaited_once_with(redis, mobile=EXACT_MOBILE)
            self.assertIs(mock_load_state.await_args.args[0], redis)
            self.assertEqual(mock_load_state.await_args.kwargs, {"mobile": EXACT_MOBILE})

            mock_deliver_stage6_sms.assert_awaited_once_with(redis, state=delivery_state)
            self.assertIs(mock_deliver_stage6_sms.await_args.args[0], redis)
            self.assertIs(mock_deliver_stage6_sms.await_args.kwargs["state"], delivery_state)

            redis.setex.assert_awaited_once_with(f"sms_limit:{EXACT_MOBILE}", 60, "1")

            mock_send_otp_sms.assert_not_called()
            self.assertEqual(redis.set.call_count, 0)
            self.assertEqual(redis.delete.call_count, 0)
            self.assertEqual(redis.hset.call_count, 0)
            self.assertEqual(redis.hdel.call_count, 0)
            self.assertEqual(redis.expire.call_count, 0)

            expected_payload = {
                "detail": "کد از طریق پیامک ارسال شد",
                "otp_request_id": str(EXACT_OTP_REQUEST_ID),
                "method": "sms",
                "expires_in": 110,
                "expires_at": delivery_state.expires_at.isoformat(),
                "sms_fallback_at": delivery_state.sms_fallback_at.isoformat(),
                "sms_fallback_in": 30,
            }
            self.assertEqual(response, expected_payload)
            self.assertEqual(response["detail"], "کد از طریق پیامک ارسال شد")
            self.assertIsInstance(response["otp_request_id"], str)
            self.assertEqual(response["otp_request_id"], str(delivery_state.otp_request_id))
            self.assertEqual(response["method"], "sms")
            self.assertEqual(response["expires_in"], 110)
            self.assertEqual(response["expires_at"], delivery_state.expires_at.isoformat())
            self.assertEqual(response["sms_fallback_at"], delivery_state.sms_fallback_at.isoformat())
            self.assertEqual(response["sms_fallback_in"], 30)

    async def _assert_non_accepted_delivery_outcome(
        self,
        outcome: SMSDeliveryOutcome,
    ) -> None:
        user = DETERMINISTIC_USER
        delivery_state = VALID_DELIVERY_STATE
        db = _build_mock_db(user)
        raw_request = MagicMock(spec=Request)
        request = OTPRequest(mobile_number=EXACT_MOBILE)

        with (
            patch.object(settings, "telegram_login_otp_enabled", True),
            patch("api.routers.auth.get_redis", new_callable=AsyncMock) as mock_get_redis,
            patch("api.routers.auth._login_home_server", return_value="foreign") as mock_login_home_server,
            patch("api.routers.auth.assert_login_allowed_for_server", new_callable=AsyncMock) as mock_assert_login_allowed,
            patch("api.routers.auth._find_pending_invitation_for_mobile", new_callable=AsyncMock) as mock_find_pending_invitation,
            patch("api.routers.auth.load_otp_delivery_state", new_callable=AsyncMock) as mock_load_state,
            patch("api.routers.auth._deliver_stage6_sms", new_callable=AsyncMock) as mock_deliver_stage6_sms,
            patch("api.routers.auth.send_otp_sms") as mock_send_otp_sms,
            patch("api.routers.auth.utc_now", return_value=PINNED_NOW),
        ):
            redis = _build_mock_redis(mobile=EXACT_MOBILE, otp_code=EXACT_OTP_CODE)
            mock_get_redis.return_value = redis
            mock_load_state.return_value = delivery_state
            mock_deliver_stage6_sms.return_value = outcome

            with self.assertRaises(HTTPException) as ctx:
                await resend_otp_sms(
                    request=request,
                    raw_request=raw_request,
                    db=db,
                )

            self.assertEqual(ctx.exception.status_code, 500)
            self.assertEqual(ctx.exception.detail, "خطا در ارسال پیامک")

            mock_get_redis.assert_awaited_once_with()
            db.execute.assert_awaited_once()
            db.execute.return_value.scalar_one_or_none.assert_called_once_with()
            mock_login_home_server.assert_called_once_with(raw_request)
            mock_assert_login_allowed.assert_awaited_once_with(
                db,
                user,
                requested_server="foreign",
            )
            self.assertIs(mock_assert_login_allowed.await_args.args[0], db)
            self.assertIs(mock_assert_login_allowed.await_args.args[1], user)
            self.assertEqual(
                mock_assert_login_allowed.await_args.kwargs,
                {"requested_server": "foreign"},
            )
            mock_find_pending_invitation.assert_not_called()

            self.assertEqual(
                redis.get.await_args_list,
                [call(f"otp:{EXACT_MOBILE}"), call(f"sms_limit:{EXACT_MOBILE}")],
            )
            redis.ttl.assert_awaited_once_with(f"otp:{EXACT_MOBILE}")

            mock_load_state.assert_awaited_once_with(redis, mobile=EXACT_MOBILE)
            self.assertIs(mock_load_state.await_args.args[0], redis)
            self.assertEqual(mock_load_state.await_args.kwargs, {"mobile": EXACT_MOBILE})

            mock_deliver_stage6_sms.assert_awaited_once_with(redis, state=delivery_state)
            self.assertIs(mock_deliver_stage6_sms.await_args.args[0], redis)
            self.assertIs(mock_deliver_stage6_sms.await_args.kwargs["state"], delivery_state)

            redis.setex.assert_not_called()
            mock_send_otp_sms.assert_not_called()
            self.assertEqual(redis.set.call_count, 0)
            self.assertEqual(redis.delete.call_count, 0)
            self.assertEqual(redis.hset.call_count, 0)
            self.assertEqual(redis.hdel.call_count, 0)
            self.assertEqual(redis.expire.call_count, 0)

    async def test_resend_otp_sms_structured_state_delivery_failed(self) -> None:
        """Freeze 500 rejection when Stage 6 delivery returns FAILED."""
        await self._assert_non_accepted_delivery_outcome(SMSDeliveryOutcome.FAILED)

    async def test_resend_otp_sms_structured_state_delivery_ambiguous(self) -> None:
        """Freeze 500 rejection when Stage 6 delivery returns AMBIGUOUS."""
        await self._assert_non_accepted_delivery_outcome(SMSDeliveryOutcome.AMBIGUOUS)


if __name__ == "__main__":
    unittest.main()
