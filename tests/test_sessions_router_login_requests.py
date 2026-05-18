import uuid
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.sessions import (
    approve_request,
    get_pending_login_requests,
    get_pending_requests,
    poll_login_request_status,
    reject_request,
)
from models.session import LoginRequestStatus, Platform


class FakeScalarRows:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class FakeExecuteResult:
    def __init__(self, *, value=None, values=None):
        self._value = value
        self._values = list(values or [])

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return FakeScalarRows(self._values)


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class FakeRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.setex_calls = []
        self.delete_calls = []

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.delete_calls.append(key)
        self.values.pop(key, None)


def make_request(token=None, host="10.0.0.8"):
    headers = {}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


def make_session(session_id, **overrides):
    data = {
        "id": session_id,
        "device_name": "Pixel",
        "device_ip": "10.0.0.8",
        "platform": Platform.WEB,
        "home_server": "foreign",
        "is_primary": True,
        "is_active": True,
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
        "last_active_at": datetime(2026, 1, 1, 13, 0, 0),
        "user_id": 5,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_login_request(request_id=None, **overrides):
    data = {
        "id": request_id or uuid.uuid4(),
        "user_id": 5,
        "requester_device_name": "Galaxy",
        "requester_ip": "1.2.3.4",
        "requester_home_server": "iran",
        "status": LoginRequestStatus.PENDING,
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
        "expires_at": datetime(2026, 1, 1, 12, 5, 0),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class SessionsRouterLoginRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_pending_requests_filters_non_primary_session_and_formats_output(self):
        current_user = SimpleNamespace(id=5)
        caller_id = uuid.uuid4()
        request = make_request(token="jwt-token")

        pending = make_login_request(request_id=uuid.uuid4())
        with patch("jose.jwt.decode", return_value={"sid": str(caller_id)}):
            result = await get_pending_requests(
                request=request,
                db=FakeDB([
                    FakeExecuteResult(value=make_session(caller_id, is_primary=False)),
                ]),
                current_user=current_user,
            )
        self.assertEqual(result, [])

        with patch("jose.jwt.decode", return_value={"sid": str(caller_id)}):
            result = await get_pending_requests(
                request=request,
                db=FakeDB([
                    FakeExecuteResult(value=make_session(caller_id, is_primary=True)),
                    FakeExecuteResult(values=[pending]),
                ]),
                current_user=current_user,
            )
        self.assertEqual(
            result,
            [{
                "request_id": str(pending.id),
                "device_name": "Galaxy",
                "device_ip": "1.2.3.4",
                "expires_at": "2026-01-01T12:05:00Z",
            }],
        )

    async def test_get_pending_login_requests_uses_login_request_serializer(self):
        current_user = SimpleNamespace(id=5)
        pending = make_login_request(request_id=uuid.uuid4())

        with patch(
            "api.routers.sessions.login_request_to_dict",
            side_effect=[{"id": str(pending.id), "status": "pending"}],
        ) as serializer:
            result = await get_pending_login_requests(
                db=FakeDB([FakeExecuteResult(values=[pending])]),
                current_user=current_user,
            )

        serializer.assert_called_once_with(pending)
        self.assertEqual(result, [{"id": str(pending.id), "status": "pending"}])

    async def test_approve_request_handles_invalid_id_missing_primary_missing_request_and_error(self):
        current_user = SimpleNamespace(id=5)
        request = make_request(host="10.0.0.8")

        with self.assertRaises(HTTPException) as exc_info:
            await approve_request("bad-id", request=request, db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)

        rid = str(uuid.uuid4())
        with self.assertRaises(HTTPException) as exc_info:
            await approve_request(rid, request=request, db=FakeDB([FakeExecuteResult(value=None)]), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "فقط از نشست اصلی مجاز به تایید هستید")

        primary = make_session(uuid.uuid4(), is_primary=True)
        with self.assertRaises(HTTPException) as exc_info:
            await approve_request(
                rid,
                request=request,
                db=FakeDB([FakeExecuteResult(value=primary), FakeExecuteResult(value=None)]),
                current_user=current_user,
            )
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "درخواست یافت نشد")

        login_req = make_login_request(request_id=uuid.UUID(rid))
        with patch("api.routers.sessions.create_refresh_token", return_value="refresh-token"), patch(
            "api.routers.sessions.approve_login_request",
            new=AsyncMock(return_value={"error": "failed approve"}),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await approve_request(
                    rid,
                    request=request,
                    db=FakeDB([FakeExecuteResult(value=primary), FakeExecuteResult(value=login_req)]),
                    current_user=current_user,
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "failed approve")

    async def test_approve_request_stores_refresh_token_and_returns_session(self):
        rid_uuid = uuid.uuid4()
        rid = str(rid_uuid)
        current_user = SimpleNamespace(id=5)
        request = make_request(host="10.0.0.8")
        primary = make_session(uuid.uuid4(), is_primary=True)
        login_req = make_login_request(request_id=rid_uuid, requester_home_server="iran")
        approved_session = make_session(uuid.uuid4(), is_primary=False, home_server="iran")
        redis = FakeRedis()

        with patch("api.routers.sessions.create_refresh_token", return_value="refresh-token"), patch(
            "api.routers.sessions.approve_login_request",
            new=AsyncMock(return_value={"session": approved_session}),
        ) as approve_mock, patch(
            "bot.utils.redis_helpers.get_redis",
            new=AsyncMock(return_value=redis),
        ):
            result = await approve_request(
                rid,
                request=request,
                db=FakeDB([FakeExecuteResult(value=primary), FakeExecuteResult(value=login_req)]),
                current_user=current_user,
            )

        approve_mock.assert_awaited_once_with(
            unittest.mock.ANY,
            rid_uuid,
            primary,
            "refresh-token",
            device_ip="10.0.0.8",
            home_server="iran",
        )
        self.assertEqual(redis.setex_calls, [(f"login_req_token:{rid}", 300, "refresh-token")])
        self.assertEqual(result["detail"], "درخواست ورود تایید شد")
        self.assertEqual(result["session"]["id"], str(approved_session.id))

    async def test_reject_request_covers_invalid_primary_error_and_success(self):
        current_user = SimpleNamespace(id=5)

        with self.assertRaises(HTTPException) as exc_info:
            await reject_request("bad-id", db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)

        rid = str(uuid.uuid4())
        with self.assertRaises(HTTPException) as exc_info:
            await reject_request(rid, db=FakeDB([FakeExecuteResult(value=None)]), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)

        primary = make_session(uuid.uuid4(), is_primary=True)
        with patch(
            "api.routers.sessions.reject_login_request",
            new=AsyncMock(return_value={"error": "reject failed"}),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await reject_request(rid, db=FakeDB([FakeExecuteResult(value=primary)]), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "reject failed")

        with patch(
            "api.routers.sessions.reject_login_request",
            new=AsyncMock(return_value={"ok": True}),
        ) as reject_mock:
            result = await reject_request(rid, db=FakeDB([FakeExecuteResult(value=primary)]), current_user=current_user)

        reject_mock.assert_awaited_once_with(unittest.mock.ANY, uuid.UUID(rid), primary)
        self.assertEqual(result, {"detail": "درخواست ورود رد شد"})

    async def test_poll_login_request_status_covers_invalid_missing_approved_and_expired(self):
        with self.assertRaises(HTTPException) as exc_info:
            await poll_login_request_status("bad-id", db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        rid = str(uuid.uuid4())
        with self.assertRaises(HTTPException) as exc_info:
            await poll_login_request_status(rid, db=FakeDB([FakeExecuteResult(value=None)]))
        self.assertEqual(exc_info.exception.status_code, 404)

        approved_req = make_login_request(
            request_id=uuid.UUID(rid),
            status=LoginRequestStatus.APPROVED,
            user_id=5,
        )
        new_session = make_session(uuid.uuid4(), home_server="iran")
        redis = FakeRedis({f"login_req_token:{rid}": "refresh-token"})

        with patch("api.routers.sessions.create_access_token", return_value="access-token"), patch(
            "bot.utils.redis_helpers.get_redis",
            new=AsyncMock(return_value=redis),
        ):
            result = await poll_login_request_status(
                rid,
                db=FakeDB([
                    FakeExecuteResult(value=approved_req),
                    FakeExecuteResult(value=new_session),
                ]),
            )

        self.assertEqual(
            result,
            {
                "status": "approved",
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "token_type": "bearer",
            },
        )
        self.assertEqual(redis.delete_calls, [f"login_req_token:{rid}"])

        expired_req = make_login_request(
            request_id=uuid.uuid4(),
            status=LoginRequestStatus.PENDING,
            expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
        result = await poll_login_request_status(
            str(expired_req.id),
            db=FakeDB([FakeExecuteResult(value=expired_req)]),
        )
        self.assertEqual(result, {"status": "expired"})


if __name__ == "__main__":
    unittest.main()