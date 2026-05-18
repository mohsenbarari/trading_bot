import uuid
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.sessions import (
    approve_single_session_recovery,
    cancel_single_session_recovery,
    get_pending_single_session_recovery_prompts,
    get_single_session_recovery_status,
    reject_single_session_recovery,
    request_single_session_recovery_identity,
    start_single_session_recovery,
    submit_single_session_recovery_identity,
)
from core.enums import MessageType
from models.session import LoginRequestStatus, Platform, SingleSessionRecoveryStatus
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


class FakeUploadFile:
    def __init__(self, content: bytes, *, filename="identity.jpg", content_type="image/jpeg"):
        self._content = content
        self.filename = filename
        self.content_type = content_type
        self.close = AsyncMock()

    async def read(self):
        return self._content


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
        "user_id": 7,
        "user": make_user(),
        "session_login_request_id": uuid.uuid4(),
        "session_login_request": SimpleNamespace(status=LoginRequestStatus.PENDING),
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
            with patch(
                "api.routers.sessions.list_recovery_admin_users",
                new=AsyncMock(return_value=[make_user(id=90, role=UserRole.SUPER_ADMIN)]),
            ), patch(
                "api.routers.sessions._deliver_initial_recovery_messages",
                new=AsyncMock(),
            ) as deliver_mock:
                result = await start_single_session_recovery(str(rid_uuid), db=db)

        create_mock.assert_awaited_once_with(db, login_req)
        deliver_mock.assert_awaited_once_with(
            db,
            created,
            unittest.mock.ANY,
            unittest.mock.ANY,
        )
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
        ), patch("api.routers.sessions.cancel_recovery_request") as cancel_mock, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ):
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
        ), patch("api.routers.sessions.expire_recovery_request") as expire_mock, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ):
            result = await get_single_session_recovery_status(str(rid), db=db)

        expire_mock.assert_called_once_with(expired_candidate)
        db.commit.assert_awaited_once()
        self.assertEqual(result["id"], str(expired_candidate.id))

    async def test_pending_recovery_prompts_are_admin_only_and_serialized(self):
        non_admin = make_user(role=UserRole.STANDARD)
        self.assertEqual(await get_pending_single_session_recovery_prompts(db=FakeDB(), current_user=non_admin), [])

        admin = make_user(id=99, role=UserRole.MIDDLE_MANAGER)
        recovery = make_recovery()
        requester = make_user(id=7)
        target = SimpleNamespace(current_action_message_id=123)
        with patch(
            "api.routers.sessions.list_pending_admin_recovery_targets",
            new=AsyncMock(return_value=[(target, recovery, requester)]),
        ) as list_mock:
            result = await get_pending_single_session_recovery_prompts(db=FakeDB(), current_user=admin)

        list_mock.assert_awaited_once_with(unittest.mock.ANY, admin_user_id=99)
        self.assertEqual(result[0]["recovery_id"], str(recovery.id))
        self.assertTrue(result[0]["can_request_identity"])

    async def test_request_identity_validates_state_and_sends_sms(self):
        recovery_id = uuid.uuid4()
        admin = make_user(id=99, role=UserRole.SUPER_ADMIN)

        with self.assertRaises(HTTPException) as exc_info:
            await request_single_session_recovery_identity("bad-id", db=FakeDB(), current_user=admin)
        self.assertEqual(exc_info.exception.status_code, 400)

        invalid_state = make_recovery(status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED)
        requester = make_user(mobile_number="09120000000")
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), invalid_state, requester)),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await request_single_session_recovery_identity(str(recovery_id), db=FakeDB(), current_user=admin)
        self.assertEqual(exc_info.exception.status_code, 400)

        recovery = make_recovery(status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW)
        db = FakeDB()
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), recovery, requester)),
        ), patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ) as clear_mock, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ) as publish_mock, patch("api.routers.sessions.send_sms", side_effect=RuntimeError("sms down")):
            result = await request_single_session_recovery_identity(str(recovery_id), db=db, current_user=admin)

        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        clear_mock.assert_awaited_once_with(db, recovery.id)
        publish_mock.assert_awaited_once_with(db, recovery, requester)
        db.commit.assert_awaited_once()
        self.assertEqual(result["detail"], "درخواست ارسال مدرک برای کاربر ثبت شد")

    async def test_approve_and_reject_recovery_flows_update_login_request_and_notify(self):
        recovery_id = uuid.uuid4()
        admin = make_user(id=99, role=UserRole.SUPER_ADMIN)
        login_req = make_login_request(status=LoginRequestStatus.PENDING)
        requester = make_user(mobile_number="09120000000")
        recovery = make_recovery(
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            session_login_request=login_req,
        )
        new_session = SimpleNamespace(
            id=uuid.uuid4(),
            device_name="Chrome",
            device_ip="1.2.3.4",
            platform=Platform.WEB,
            home_server="foreign",
            is_primary=True,
            is_active=True,
            created_at=datetime.utcnow(),
            last_active_at=datetime.utcnow(),
        )

        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), recovery, requester)),
        ), patch("api.routers.sessions.create_refresh_token", return_value="refresh-token"), patch(
            "api.routers.sessions.provision_session_for_login_request",
            new=AsyncMock(return_value=new_session),
        ) as provision_mock, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._store_temporary_refresh_token",
            new=AsyncMock(),
        ) as store_mock, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ), patch("api.routers.sessions.send_sms"):
            result = await approve_single_session_recovery(
                str(recovery_id),
                request=SimpleNamespace(client=SimpleNamespace(host="1.2.3.4")),
                db=FakeDB(),
                current_user=admin,
            )

        self.assertEqual(login_req.status, LoginRequestStatus.APPROVED)
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.APPROVED)
        provision_mock.assert_awaited_once()
        store_mock.assert_awaited_once()
        self.assertEqual(result["detail"], "درخواست بازیابی تایید شد")

        reject_login_req = make_login_request(status=LoginRequestStatus.PENDING)
        reject_recovery = make_recovery(
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            session_login_request=reject_login_req,
        )
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), reject_recovery, requester)),
        ), patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ), patch("api.routers.sessions.send_sms"):
            result = await reject_single_session_recovery(str(recovery_id), db=FakeDB(), current_user=admin)

        self.assertEqual(reject_login_req.status, LoginRequestStatus.REJECTED)
        self.assertEqual(reject_recovery.status, SingleSessionRecoveryStatus.REJECTED)
        self.assertEqual(result["detail"], "درخواست بازیابی رد شد")

    async def test_submit_identity_validates_type_builds_payload_and_closes_upload(self):
        rid = uuid.uuid4()
        recovery = make_recovery(status=SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        requester = make_user(mobile_number="09120000000")

        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await submit_single_session_recovery_identity(str(rid), file=FakeUploadFile(b"x"), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 404)

        bad_file = FakeUploadFile(b"x", content_type="application/x-msdownload")
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=recovery),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await submit_single_session_recovery_identity(
                    str(rid),
                    file=bad_file,
                    db=FakeDB([FakeExecuteResult(value=requester)]),
                )
        self.assertEqual(exc_info.exception.status_code, 400)

        ok_file = FakeUploadFile(b"img", filename="id.jpg", content_type="image/jpeg")
        db = FakeDB([FakeExecuteResult(value=requester)])
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=recovery),
        ), patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.sessions._build_identity_message_payload",
            new=AsyncMock(return_value=(MessageType.IMAGE, '{"file_id":"1"}', "caption")),
        ) as payload_mock, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._deliver_identity_submission_messages",
            new=AsyncMock(),
        ) as deliver_mock, patch("api.routers.sessions.send_sms"):
            result = await submit_single_session_recovery_identity(
                str(rid),
                file=ok_file,
                caption=" caption ",
                db=db,
            )

        payload_mock.assert_awaited_once()
        deliver_mock.assert_awaited_once()
        ok_file.close.assert_awaited_once()
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.IDENTITY_SUBMITTED)
        self.assertEqual(result["detail"], "مدرک برای بررسی ارسال شد")