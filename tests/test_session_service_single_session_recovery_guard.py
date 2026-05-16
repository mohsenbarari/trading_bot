import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.services import session_service
from models.session import LoginRequestStatus, Platform, SessionLoginRequest


def scalar_one_or_none_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


class SessionServiceSingleSessionRecoveryGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_approve_login_request_rejects_when_recovery_is_active(self):
        request_id = uuid.uuid4()
        login_req = SessionLoginRequest(
            id=request_id,
            user_id=9,
            requester_device_name="Chrome",
            requester_home_server="foreign",
            status=LoginRequestStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(minutes=1),
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=scalar_one_or_none_result(login_req)))

        with patch(
            "core.services.single_session_recovery_service.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
        ):
            result = await session_service.approve_login_request(
                db,
                request_id,
                SimpleNamespace(id=uuid.uuid4()),
                refresh_token="refresh-token",
                platform=Platform.WEB,
            )

        self.assertEqual(
            result,
            {"error": "برای این درخواست، مسیر بازیابی نشست فعال شده و تایید از دستگاه قبلی دیگر مجاز نیست"},
        )

    async def test_reject_login_request_rejects_when_recovery_is_active(self):
        request_id = uuid.uuid4()
        login_req = SessionLoginRequest(
            id=request_id,
            user_id=9,
            requester_device_name="Chrome",
            requester_home_server="foreign",
            status=LoginRequestStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(minutes=1),
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=scalar_one_or_none_result(login_req)))

        with patch(
            "core.services.single_session_recovery_service.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
        ):
            result = await session_service.reject_login_request(
                db,
                request_id,
                SimpleNamespace(id=uuid.uuid4()),
            )

        self.assertEqual(
            result,
            {"error": "برای این درخواست، مسیر بازیابی نشست فعال شده و رد از دستگاه قبلی دیگر مجاز نیست"},
        )