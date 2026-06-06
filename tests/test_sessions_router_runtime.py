import uuid
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.sessions import (
    VerifySessionRequest,
    list_active_sessions,
    logout_all_sessions,
    terminate_session,
    verify_my_session,
)
from models.session import Platform


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


def make_request(token=None):
    headers = {}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return SimpleNamespace(headers=headers)


def make_session(session_id, **overrides):
    data = {
        "id": session_id,
        "device_name": "Pixel",
        "device_ip": "10.0.0.8",
        "platform": Platform.WEB,
        "home_server": "foreign",
        "is_primary": False,
        "is_active": True,
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
        "last_active_at": datetime(2026, 1, 1, 13, 0, 0),
        "user_id": 5,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class SessionsRouterRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_my_session_rejects_missing_refresh_token_and_accepts_active_session(self):
        req = VerifySessionRequest(refresh_token="refresh-token")

        with patch("core.services.session_service.get_session_by_refresh_token", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as exc_info:
                await verify_my_session(req, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "نشست شما باطل شده است")

        with patch(
            "core.services.session_service.get_session_by_refresh_token",
            new=AsyncMock(return_value=make_session(uuid.uuid4())),
        ):
            result = await verify_my_session(req, db=FakeDB())
        self.assertEqual(result, {"status": "active"})

    async def test_list_active_sessions_marks_current_session(self):
        current_session_id = uuid.uuid4()
        other_session_id = uuid.uuid4()
        sessions = [make_session(current_session_id), make_session(other_session_id)]
        request = make_request(token="jwt-token")
        current_user = SimpleNamespace(id=5)

        with patch("jose.jwt.decode", return_value={"sid": str(current_session_id)}), patch(
            "api.routers.sessions.get_active_sessions",
            new=AsyncMock(return_value=sessions),
        ):
            result = await list_active_sessions(request=request, db=FakeDB(), current_user=current_user)

        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["is_current"])
        self.assertFalse(result[1]["is_current"])
        self.assertEqual(result[0]["platform"], "web")

    async def test_accountants_cannot_access_session_management(self):
        current_user = SimpleNamespace(id=44, is_accountant=True)
        request = make_request(token="jwt-token")

        with patch("api.routers.sessions.get_active_sessions", new=AsyncMock()) as get_sessions_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await list_active_sessions(request=request, db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertIn("حسابداران", exc_info.exception.detail)
        get_sessions_mock.assert_not_awaited()

        with self.assertRaises(HTTPException) as exc_info:
            await terminate_session(str(uuid.uuid4()), request=request, db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)

        with self.assertRaises(HTTPException) as exc_info:
            await logout_all_sessions(request=request, db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)

    async def test_terminate_session_enforces_access_rules_and_can_logout(self):
        current_user = SimpleNamespace(id=5)
        target_id = uuid.uuid4()
        caller_id = uuid.uuid4()
        request = make_request(token="jwt-token")

        with self.assertRaises(HTTPException) as exc_info:
            await terminate_session("bad-id", request=request, db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)

        with patch("jose.jwt.decode", return_value={"sid": str(caller_id)}):
            with self.assertRaises(HTTPException) as exc_info:
                await terminate_session(str(target_id), request=request, db=FakeDB([FakeExecuteResult(None)]), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 404)

        target_session = make_session(target_id)
        caller_session = make_session(caller_id, is_primary=False)
        with patch("jose.jwt.decode", return_value={"sid": str(caller_id)}):
            with self.assertRaises(HTTPException) as exc_info:
                await terminate_session(
                    str(target_id),
                    request=request,
                    db=FakeDB([FakeExecuteResult(target_session), FakeExecuteResult(caller_session)]),
                    current_user=current_user,
                )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شما دسترسی برای حذف نشست دستگاه‌های دیگر را ندارید")

        primary_target = make_session(target_id, is_primary=True)
        with patch("jose.jwt.decode", return_value={"sid": str(target_id)}), patch(
            "api.routers.sessions.get_active_sessions",
            new=AsyncMock(return_value=[primary_target, make_session(uuid.uuid4())]),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await terminate_session(
                    str(target_id),
                    request=request,
                    db=FakeDB([FakeExecuteResult(primary_target)]),
                    current_user=current_user,
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "نشست اصلی را نمی‌توان حذف کرد. ابتدا نشست‌های دیگر را حذف کنید.")

        target_session = make_session(target_id)
        with patch("jose.jwt.decode", return_value={"sid": str(target_id)}), patch(
            "api.routers.sessions.logout_session",
            new=AsyncMock(),
        ) as logout_mock:
            result = await terminate_session(
                str(target_id),
                request=request,
                db=FakeDB([FakeExecuteResult(target_session)]),
                current_user=current_user,
            )
        logout_mock.assert_awaited_once_with(unittest.mock.ANY, target_session)
        self.assertEqual(result, {"detail": "نشست با موفقیت پایان یافت"})

    async def test_logout_all_sessions_requires_primary_and_returns_count(self):
        current_user = SimpleNamespace(id=5)
        caller_id = uuid.uuid4()
        request = make_request(token="jwt-token")

        with patch("jose.jwt.decode", return_value={}):
            with self.assertRaises(HTTPException) as exc_info:
                await logout_all_sessions(request=request, db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شناسه نشست شما مشخص نیست")

        with patch("jose.jwt.decode", return_value={"sid": str(caller_id)}):
            with self.assertRaises(HTTPException) as exc_info:
                await logout_all_sessions(
                    request=request,
                    db=FakeDB([FakeExecuteResult(make_session(caller_id, is_primary=False))]),
                    current_user=current_user,
                )
        self.assertEqual(exc_info.exception.status_code, 403)

        primary = make_session(caller_id, is_primary=True)
        with patch("jose.jwt.decode", return_value={"sid": str(caller_id)}), patch(
            "api.routers.sessions.force_clear_sessions",
            new=AsyncMock(return_value=3),
        ) as clear_mock:
            result = await logout_all_sessions(
                request=request,
                db=FakeDB([FakeExecuteResult(primary)]),
                current_user=current_user,
            )

        clear_mock.assert_awaited_once_with(unittest.mock.ANY, 5, exclude_session_id=caller_id)
        self.assertEqual(result, {"detail": "3 نشست پایان یافت"})

    async def test_session_runtime_decode_failure_and_missing_current_session_id_paths(self):
        current_user = SimpleNamespace(id=5)
        target_id = uuid.uuid4()
        sessions = [make_session(target_id)]

        with patch("jose.jwt.decode", side_effect=RuntimeError("bad token")), patch(
            "api.routers.sessions.get_active_sessions",
            new=AsyncMock(return_value=sessions),
        ):
            result = await list_active_sessions(
                request=make_request(token="jwt-token"),
                db=FakeDB(),
                current_user=current_user,
            )
        self.assertFalse(result[0]["is_current"])

        with self.assertRaises(HTTPException) as exc_info:
            await terminate_session(
                str(target_id),
                request=make_request(),
                db=FakeDB([FakeExecuteResult(make_session(target_id))]),
                current_user=current_user,
            )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شناسه نشست شما مشخص نیست")

        with patch("jose.jwt.decode", side_effect=RuntimeError("bad token")):
            with self.assertRaises(HTTPException) as exc_info:
                await logout_all_sessions(
                    request=make_request(token="jwt-token"),
                    db=FakeDB(),
                    current_user=current_user,
                )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شناسه نشست شما مشخص نیست")


if __name__ == "__main__":
    unittest.main()
