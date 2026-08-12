"""A login request may only be approved or rejected from its own account.

The request id is effectively a bearer capability: the unauthenticated polling
endpoint hands out an access token for the request's user once the request is
approved. Without this guard, anyone holding a leaked id could resolve it from
their own primary session and then collect that token. The guard must also
answer exactly like a missing request, so a caller learns nothing about whether
an id exists or what state it is in.
"""

import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.services import session_service
from models.session import LoginRequestStatus


NOT_FOUND = {"error": "درخواست یافت نشد"}


def scalar_one_or_none_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


def make_login_request(*, user_id, status=LoginRequestStatus.PENDING):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        requester_device_name="Chrome",
        requester_ip="1.2.3.4",
        requester_home_server="foreign",
        status=status,
        expires_at=datetime.utcnow() + timedelta(minutes=1),
        resolved_by_session_id=None,
    )


class LoginRequestOwnershipGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_approve_refuses_request_belonging_to_another_account(self):
        login_req = make_login_request(user_id=9)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(login_req)),
            commit=AsyncMock(),
        )

        result = await session_service.approve_login_request(
            db,
            login_req.id,
            SimpleNamespace(id=uuid.uuid4(), user_id=10),
            "refresh-token",
        )

        self.assertEqual(result, NOT_FOUND)
        self.assertEqual(login_req.status, LoginRequestStatus.PENDING)
        db.commit.assert_not_awaited()
        # Only the initial load ran, so no later check leaked anything.
        self.assertEqual(db.execute.await_count, 1)

    async def test_reject_refuses_request_belonging_to_another_account(self):
        login_req = make_login_request(user_id=9)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(login_req)),
            commit=AsyncMock(),
        )

        result = await session_service.reject_login_request(
            db,
            login_req.id,
            SimpleNamespace(id=uuid.uuid4(), user_id=10),
        )

        self.assertEqual(result, NOT_FOUND)
        self.assertEqual(login_req.status, LoginRequestStatus.PENDING)
        db.commit.assert_not_awaited()
        self.assertEqual(db.execute.await_count, 1)

    async def test_foreign_request_state_is_indistinguishable_from_missing(self):
        """An already-resolved request of another account must not reveal that."""
        for status in (LoginRequestStatus.APPROVED, LoginRequestStatus.REJECTED):
            with self.subTest(status=status):
                login_req = make_login_request(user_id=9, status=status)
                approver = SimpleNamespace(id=uuid.uuid4(), user_id=10)

                approve_db = SimpleNamespace(
                    execute=AsyncMock(return_value=scalar_one_or_none_result(login_req)),
                    commit=AsyncMock(),
                )
                reject_db = SimpleNamespace(
                    execute=AsyncMock(return_value=scalar_one_or_none_result(login_req)),
                    commit=AsyncMock(),
                )

                self.assertEqual(
                    await session_service.approve_login_request(
                        approve_db, login_req.id, approver, "refresh-token"
                    ),
                    NOT_FOUND,
                )
                self.assertEqual(
                    await session_service.reject_login_request(reject_db, login_req.id, approver),
                    NOT_FOUND,
                )

    async def test_owner_is_still_allowed_through_the_guard(self):
        login_req = make_login_request(user_id=9, status=LoginRequestStatus.REJECTED)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(login_req)),
            commit=AsyncMock(),
        )

        result = await session_service.reject_login_request(
            db,
            login_req.id,
            SimpleNamespace(id=uuid.uuid4(), user_id=9),
        )

        # Past the ownership guard, so it reports the real reason instead.
        self.assertEqual(result, {"error": "درخواست قبلاً پردازش شده است"})


if __name__ == "__main__":
    unittest.main()
