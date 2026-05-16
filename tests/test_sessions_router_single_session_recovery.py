import uuid
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.sessions import (
    cancel_single_session_recovery,
    get_single_session_recovery_status,
    start_single_session_recovery,
)
from models.session import LoginRequestStatus, SingleSessionRecoveryStatus
from models.user import UserRole


class FakeExecuteResult:
    def __init__(self, *, value=None):
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


def make_login_request(request_id=None, **overrides):
    data = {
        "id": request_id or uuid.uuid4(),
        "user_id": 7,
        "requester_device_name": "Chrome on Windows",
        "requester_ip": "8.8.8.8",
        "requester_home_server": "foreign",
        "status": LoginRequestStatus.PENDING,
        "expires_at": datetime.utcnow() + timedelta(minutes=1),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_user(**overrides):
    data = {
        "id": 7,
        "role": UserRole.STANDARD,
        "max_sessions": 1,
        "is_deleted": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_recovery(**overrides):
    now = datetime.utcnow()
    data = {
        "id": uuid.uuid4(),
        "status": SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
        "requester_device_name": "Chrome on Windows",
        "requester_ip": "8.8.8.8",
        "created_at": now,
        "inline_action_expires_at": now + timedelta(seconds=30),
        "chat_action_expires_at": now + timedelta(hours=2),
        "identity_requested_at": None,
        "identity_submitted_at": None,
        "decided_at": None,
        "cancelled_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class SessionsRouterSingleSessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_recovery_validates_request_and_eligibility(self):
        with self.assertRaises(HTTPException) as exc_info:
            await start_single_session_recovery("bad-id", db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        rid = str(uuid.uuid4())
        with self.assertRaises(HTTPException) as exc_info:
            await start_single_session_recovery(rid, db=FakeDB([FakeExecuteResult(value=None)]))
        self.assertEqual(exc_info.exception.status_code, 404)

        login_req = make_login_request(request_id=uuid.UUID(rid), status=LoginRequestStatus.APPROVED)
        db = FakeDB([FakeExecuteResult(value=login_req), FakeExecuteResult(value=make_user())])
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await start_single_session_recovery(rid, db=db)
        self.assertEqual(exc_info.exception.status_code, 400)

        login_req = make_login_request(request_id=uuid.UUID(rid))
        db = FakeDB([FakeExecuteResult(value=login_req), FakeExecuteResult(value=make_user(role=UserRole.SUPER_ADMIN))])
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await start_single_session_recovery(rid, db=db)
        self.assertEqual(exc_info.exception.status_code, 403)

        db = FakeDB([FakeExecuteResult(value=login_req), FakeExecuteResult(value=make_user(max_sessions=2))])
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await start_single_session_recovery(rid, db=db)
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_start_recovery_reuses_active_or_creates_new(self):
        rid_uuid = uuid.uuid4()
        login_req = make_login_request(request_id=rid_uuid)
        active = make_recovery()

        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=active),
        ):
            result = await start_single_session_recovery(
                str(rid_uuid),
                db=FakeDB([FakeExecuteResult(value=login_req)]),
            )
        self.assertEqual(result["id"], str(active.id))

        created = make_recovery()
        db = FakeDB([FakeExecuteResult(value=login_req), FakeExecuteResult(value=make_user())])
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.sessions.create_recovery_request",
            new=AsyncMock(return_value=created),
        ) as create_mock:
            result = await start_single_session_recovery(str(rid_uuid), db=db)

        create_mock.assert_awaited_once_with(db, login_req)
        db.commit.assert_awaited_once()
        self.assertEqual(result["status"], SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW.value)

    async def test_cancel_recovery_validates_and_cancels(self):
        with self.assertRaises(HTTPException) as exc_info:
            await cancel_single_session_recovery("bad-id", db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        rid = uuid.uuid4()
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await cancel_single_session_recovery(str(rid), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 404)

        recovery = make_recovery()
        db = FakeDB()
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=recovery),
        ), patch("api.routers.sessions.cancel_recovery_request") as cancel_mock:
            result = await cancel_single_session_recovery(str(rid), db=db)

        cancel_mock.assert_called_once_with(recovery)
        db.commit.assert_awaited_once()
        self.assertEqual(result["detail"], "درخواست بازیابی لغو شد")

    async def test_recovery_status_returns_not_started_and_lazily_expires(self):
        with self.assertRaises(HTTPException) as exc_info:
            await get_single_session_recovery_status("bad-id", db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        rid = uuid.uuid4()
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            result = await get_single_session_recovery_status(str(rid), db=FakeDB())
        self.assertEqual(result, {"status": "not_started"})

        expired_candidate = make_recovery(chat_action_expires_at=datetime.utcnow() - timedelta(minutes=1))
        db = FakeDB()
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=expired_candidate),
        ), patch("api.routers.sessions.expire_recovery_request") as expire_mock:
            result = await get_single_session_recovery_status(str(rid), db=db)

        expire_mock.assert_called_once_with(expired_candidate)
        db.commit.assert_awaited_once()
        self.assertEqual(result["id"], str(expired_candidate.id))