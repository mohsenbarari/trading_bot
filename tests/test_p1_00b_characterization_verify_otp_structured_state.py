"""Characterization tests for verify_otp structured-state request-ID behavior.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-AUTH-POST-AUTH-VERIFY-OTP-L2695
Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
"""

from __future__ import annotations

# Install existing P1 network fence before application imports
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import hashlib
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.routers.auth import (
    OTPVerify,
    _otp_verify_ip_lock_key,
    _otp_verify_subject_key,
    _otp_verify_subject_lock_key,
    verify_otp,
)
from core.config import settings
from core.enums import UserAccountStatus
from core.registration_contracts import (
    OTPDeliveryStateContract,
    OTPDeliveryStatus,
    OTPRequestStatus,
)
from models.session import Platform, UserSession
from models.user import User, UserRole


class TestVerifyOtpStructuredStateCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_verify_otp_active_standard_user_success_path(self):
        """Active standard user completes full successful local-session/token path with request-ID."""
        test_request_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
        resolved_mobile = "09123456789"
        code_str = "123456"
        now = datetime.now(timezone.utc)

        delivery_state = OTPDeliveryStateContract(
            otp_request_id=test_request_id,
            identity_digest=hashlib.sha256(resolved_mobile.encode("utf-8")).hexdigest(),
            delivery_target_ciphertext="A" * 32,
            status=OTPRequestStatus.PENDING,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            telegram_delivery_status=OTPDeliveryStatus.NOT_ATTEMPTED,
            sms_delivery_status=OTPDeliveryStatus.NOT_ATTEMPTED,
        )

        user = User(
            id=42,
            account_name="active_standard_user",
            mobile_number=resolved_mobile,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            home_server="foreign",
        )

        test_session_id = uuid.UUID("99999999-8888-7777-6666-555555555555")
        fake_session = MagicMock(spec=UserSession)
        fake_session.id = test_session_id
        fake_session.user_id = user.id

        request_obj = OTPVerify(otp_request_id=test_request_id, code=code_str)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/verify-otp",
            "headers": [
                (b"user-agent", b"Mozilla/5.0"),
                (b"x-device-name", b"Desktop Chrome"),
                (b"x-platform", b"web"),
            ],
            "client": ("192.168.1.50", 12345),
            "server": ("testserver", 80),
        }
        raw_request = Request(scope)

        otp_key = f"otp:{resolved_mobile}"
        otp_limit_key = f"otp_limit:{resolved_mobile}"
        subject_lock_key = _otp_verify_subject_lock_key(resolved_mobile)
        ip_lock_key = _otp_verify_ip_lock_key(raw_request)
        subject_fail_key = _otp_verify_subject_key(resolved_mobile)

        mock_redis = AsyncMock()

        async def fake_redis_get(key: str):
            if key == otp_key:
                return code_str
            if key == subject_lock_key:
                return None
            if key == ip_lock_key:
                return None
            if key == subject_fail_key:
                return "1"
            return None

        mock_redis.get.side_effect = fake_redis_get
        mock_redis.delete = AsyncMock()

        mock_db = AsyncMock(spec=AsyncSession)
        mock_db_result = MagicMock()
        mock_db_result.scalar_one_or_none.return_value = user

        call_order: list[str] = []

        async def tracked_consume_otp_code(*args, **kwargs):
            call_order.append("consume_otp_code")
            return True

        async def tracked_db_execute(*args, **kwargs):
            call_order.append("db_execute")
            return mock_db_result

        mock_db.execute.side_effect = tracked_db_execute

        with (
            patch.object(settings, "telegram_login_otp_enabled", True),
            patch("api.routers.auth.get_redis", new=AsyncMock(return_value=mock_redis)),
            patch("api.routers.auth.load_otp_delivery_state", new=AsyncMock(return_value=delivery_state)) as mock_load_state,
            patch("api.routers.auth.mobile_for_delivery_state", return_value=resolved_mobile) as mock_mobile_resolver,
            patch("api.routers.auth._resume_registration_required_context", new=AsyncMock(return_value=None)) as mock_resume_reg,
            patch("api.routers.auth.consume_otp_code", side_effect=tracked_consume_otp_code) as mock_consume,
            patch("api.routers.auth._find_pending_invitation_for_mobile", new=AsyncMock()) as mock_find_invitation,
            patch("api.routers.auth.server_from_request", return_value="foreign") as mock_server_from_request,
            patch("api.routers.auth.prepare_verified_login_for_server", new=AsyncMock(return_value=0)) as mock_prepare_login,
            patch("api.routers.auth.create_refresh_token", return_value="test-refresh-token-xyz") as mock_create_refresh,
            patch(
                "api.routers.auth.handle_login_session",
                new=AsyncMock(
                    return_value={
                        "action": "session_created",
                        "session": fake_session,
                        "replaced_session_count": 0,
                    }
                ),
            ) as mock_handle_session,
            patch("api.routers.auth.create_access_token", return_value="test-access-token-abc") as mock_create_access,
            patch("api.routers.auth.audit_log") as mock_audit_log,
            patch("api.routers.auth.record_otp_event") as mock_record_otp_event,
        ):
            response = await verify_otp(request=request_obj, raw_request=raw_request, db=mock_db)

        # Requirement 2 assertions
        mock_load_state.assert_awaited_once_with(mock_redis, request_id=test_request_id, mobile=None)
        mock_mobile_resolver.assert_called_once_with(delivery_state)
        mock_resume_reg.assert_awaited_once_with(mock_db, mock_redis, raw_request, mobile=resolved_mobile)

        self.assertEqual(
            mock_redis.get.await_args_list[:3],
            [
                call(otp_key),
                call(subject_lock_key),
                call(ip_lock_key),
            ],
        )

        mock_consume.assert_awaited_once_with(mock_redis, mobile=resolved_mobile, expected_code=code_str)
        self.assertEqual(call_order, ["consume_otp_code", "db_execute"])

        # Requirement 3 assertions
        self.assertEqual(mock_db.execute.await_count, 1)
        mock_db_result.scalar_one_or_none.assert_called_once()
        self.assertEqual(user.role, UserRole.STANDARD)
        self.assertEqual(user.account_status, UserAccountStatus.ACTIVE)
        mock_find_invitation.assert_not_called()

        mock_audit_log.assert_called_once_with(
            "otp.verified",
            target_type="otp_request",
            target_id=str(test_request_id),
            result="success",
        )
        mock_record_otp_event.assert_called_once_with(
            event="verified",
            outcome="before_sms_fallback",
        )

        self.assertEqual(
            mock_redis.delete.await_args_list,
            [
                call(otp_key),
                call(otp_limit_key),
                call(subject_fail_key),
            ],
        )

        mock_server_from_request.assert_called_once_with(raw_request, force_telegram_foreign=False)
        mock_prepare_login.assert_awaited_once_with(mock_db, user, requested_server="foreign")
        mock_create_refresh.assert_called_once_with(
            subject=user.id,
            expires_delta=timedelta(days=30),
        )
        mock_create_access.assert_called_once_with(
            subject=user.id,
            expires_delta=timedelta(minutes=60),
            session_id=str(test_session_id),
            server_id="foreign",
        )
        mock_handle_session.assert_awaited_once_with(
            mock_db,
            user,
            "test-refresh-token-xyz",
            device_name="Desktop Chrome",
            device_ip="192.168.1.50",
            platform=Platform.WEB,
            suspended_refresh_token=None,
            home_server="foreign",
        )
        self.assertEqual(
            response,
            {
                "access_token": "test-access-token-abc",
                "refresh_token": "test-refresh-token-xyz",
                "token_type": "bearer",
            },
        )

    async def test_verify_otp_atomic_consume_loss_rejection(self):
        """Atomic consume_otp_code race loss triggers HTTP 400 before DB and downstream mutations."""
        test_request_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
        resolved_mobile = "09123456789"
        code_str = "123456"
        now = datetime.now(timezone.utc)

        delivery_state = OTPDeliveryStateContract(
            otp_request_id=test_request_id,
            identity_digest=hashlib.sha256(resolved_mobile.encode("utf-8")).hexdigest(),
            delivery_target_ciphertext="A" * 32,
            status=OTPRequestStatus.PENDING,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            telegram_delivery_status=OTPDeliveryStatus.NOT_ATTEMPTED,
            sms_delivery_status=OTPDeliveryStatus.NOT_ATTEMPTED,
        )

        request_obj = OTPVerify(otp_request_id=test_request_id, code=code_str)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/verify-otp",
            "headers": [
                (b"user-agent", b"Mozilla/5.0"),
                (b"x-device-name", b"Desktop Chrome"),
                (b"x-platform", b"web"),
            ],
            "client": ("192.168.1.50", 12345),
            "server": ("testserver", 80),
        }
        raw_request = Request(scope)

        otp_key = f"otp:{resolved_mobile}"
        subject_lock_key = _otp_verify_subject_lock_key(resolved_mobile)
        ip_lock_key = _otp_verify_ip_lock_key(raw_request)

        mock_redis = AsyncMock()

        async def fake_redis_get(key: str):
            if key == otp_key:
                return code_str
            if key == subject_lock_key:
                return None
            if key == ip_lock_key:
                return None
            return None

        mock_redis.get.side_effect = fake_redis_get
        mock_redis.delete = AsyncMock()

        mock_db = AsyncMock(spec=AsyncSession)

        with (
            patch.object(settings, "telegram_login_otp_enabled", True),
            patch("api.routers.auth.get_redis", new=AsyncMock(return_value=mock_redis)),
            patch("api.routers.auth.load_otp_delivery_state", new=AsyncMock(return_value=delivery_state)) as mock_load_state,
            patch("api.routers.auth.mobile_for_delivery_state", return_value=resolved_mobile) as mock_mobile_resolver,
            patch("api.routers.auth._resume_registration_required_context", new=AsyncMock(return_value=None)) as mock_resume_reg,
            patch("api.routers.auth.consume_otp_code", new=AsyncMock(return_value=False)) as mock_consume,
            patch("api.routers.auth._find_pending_invitation_for_mobile", new=AsyncMock()) as mock_find_invitation,
            patch("api.routers.auth.server_from_request", return_value="foreign") as mock_server_from_request,
            patch("api.routers.auth.prepare_verified_login_for_server", new=AsyncMock(return_value=0)) as mock_prepare_login,
            patch("api.routers.auth.create_refresh_token", return_value="test-refresh-token-xyz") as mock_create_refresh,
            patch("api.routers.auth.handle_login_session", new=AsyncMock()) as mock_handle_session,
            patch("api.routers.auth.create_access_token", return_value="test-access-token-abc") as mock_create_access,
            patch("api.routers.auth.audit_log") as mock_audit_log,
            patch("api.routers.auth.record_otp_event") as mock_record_otp_event,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await verify_otp(request=request_obj, raw_request=raw_request, db=mock_db)

        # Requirement 4 assertions
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "کد تایید نامعتبر یا منقضی شده است")

        # Proved state checks leading to atomic consume
        mock_load_state.assert_awaited_once_with(mock_redis, request_id=test_request_id, mobile=None)
        mock_mobile_resolver.assert_called_once_with(delivery_state)
        mock_resume_reg.assert_awaited_once_with(mock_db, mock_redis, raw_request, mobile=resolved_mobile)
        self.assertEqual(
            mock_redis.get.await_args_list,
            [
                call(otp_key),
                call(subject_lock_key),
                call(ip_lock_key),
            ],
        )
        mock_consume.assert_awaited_once_with(mock_redis, mobile=resolved_mobile, expected_code=code_str)

        # Proved forbidden side effects / calls after atomic consume loss
        mock_db.execute.assert_not_called()
        mock_find_invitation.assert_not_called()
        mock_audit_log.assert_not_called()
        mock_record_otp_event.assert_not_called()
        mock_redis.delete.assert_not_called()
        mock_server_from_request.assert_not_called()
        mock_prepare_login.assert_not_called()
        mock_handle_session.assert_not_called()
        mock_create_refresh.assert_not_called()
        mock_create_access.assert_not_called()


if __name__ == "__main__":
    unittest.main()
