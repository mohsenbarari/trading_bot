"""Direct characterization tests for api.routers.users.terminate_user_sessions.

This module directly characterizes only api.routers.users.terminate_user_sessions,
does not complete P1-00b, and does not establish Offer/Request origin-interaction
evidence.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import terminate_user_sessions
from core.enums import UserRole
import models.user


class MinimalAsyncDB:
    def __init__(self, target_user=None):
        self.target_user = target_user
        self.get_calls = []

    async def get(self, entity_cls, ident):
        self.get_calls.append((entity_cls, ident))
        return self.target_user


class UsersTerminateSessionsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminate_user_sessions_success_lower_role_target(self):
        actor = SimpleNamespace(id=10, role=UserRole.SUPER_ADMIN)
        target = SimpleNamespace(id=22, role=UserRole.STANDARD, is_deleted=False)
        db = MinimalAsyncDB(target_user=target)
        clear_mock = AsyncMock(return_value=3)
        audit_mock = Mock()

        with patch(
            "api.routers.users.force_clear_sessions",
            new=clear_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            response = await terminate_user_sessions(22, db=db, actor=actor)

        self.assertEqual(
            response,
            {"detail": "3 نشست پایان یافت", "terminated_sessions": 3},
        )
        self.assertEqual(db.get_calls, [(models.user.User, 22)])
        clear_mock.assert_awaited_once_with(db, 22)
        audit_mock.assert_called_once_with(
            "user.sessions_terminate_all",
            target_type="user",
            target_id=22,
            after_summary={"terminated_sessions": 3},
            actor_id=10,
            actor_role=UserRole.SUPER_ADMIN.value,
        )

    async def test_terminate_user_sessions_not_found_when_missing(self):
        actor = SimpleNamespace(id=10, role=UserRole.SUPER_ADMIN)
        db = MinimalAsyncDB(target_user=None)
        clear_mock = AsyncMock()
        audit_mock = Mock()

        with patch(
            "api.routers.users.force_clear_sessions",
            new=clear_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await terminate_user_sessions(22, db=db, actor=actor)

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        self.assertEqual(db.get_calls, [(models.user.User, 22)])
        clear_mock.assert_not_awaited()
        audit_mock.assert_not_called()

    async def test_terminate_user_sessions_not_found_when_is_deleted(self):
        actor = SimpleNamespace(id=10, role=UserRole.SUPER_ADMIN)
        target = SimpleNamespace(id=22, role=UserRole.STANDARD, is_deleted=True)
        db = MinimalAsyncDB(target_user=target)
        clear_mock = AsyncMock()
        audit_mock = Mock()

        with patch(
            "api.routers.users.force_clear_sessions",
            new=clear_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await terminate_user_sessions(22, db=db, actor=actor)

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        self.assertEqual(db.get_calls, [(models.user.User, 22)])
        clear_mock.assert_not_awaited()
        audit_mock.assert_not_called()

    async def test_terminate_user_sessions_authorization_guard_middle_manager_targeting_super_admin(self):
        actor = SimpleNamespace(id=10, role=UserRole.MIDDLE_MANAGER)
        target = SimpleNamespace(id=22, role=UserRole.SUPER_ADMIN, is_deleted=False)
        db = MinimalAsyncDB(target_user=target)
        clear_mock = AsyncMock()
        audit_mock = Mock()

        with patch(
            "api.routers.users.force_clear_sessions",
            new=clear_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await terminate_user_sessions(22, db=db, actor=actor)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )
        self.assertEqual(db.get_calls, [(models.user.User, 22)])
        clear_mock.assert_not_awaited()
        audit_mock.assert_not_called()

    async def test_terminate_user_sessions_force_clear_failure_propagates_and_skips_audit(self):
        actor = SimpleNamespace(id=10, role=UserRole.SUPER_ADMIN)
        target = SimpleNamespace(id=22, role=UserRole.STANDARD, is_deleted=False)
        db = MinimalAsyncDB(target_user=target)
        clear_mock = AsyncMock(side_effect=RuntimeError("clear failed"))
        audit_mock = Mock()

        with patch(
            "api.routers.users.force_clear_sessions",
            new=clear_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            with self.assertRaisesRegex(RuntimeError, "clear failed"):
                await terminate_user_sessions(22, db=db, actor=actor)

        self.assertEqual(db.get_calls, [(models.user.User, 22)])
        clear_mock.assert_awaited_once_with(db, 22)
        audit_mock.assert_not_called()

    async def test_terminate_user_sessions_audit_failure_propagates_after_clearing_sessions(self):
        actor = SimpleNamespace(id=10, role=UserRole.SUPER_ADMIN)
        target = SimpleNamespace(id=22, role=UserRole.STANDARD, is_deleted=False)
        db = MinimalAsyncDB(target_user=target)
        clear_mock = AsyncMock(return_value=2)
        audit_mock = Mock(side_effect=RuntimeError("audit failed"))

        with patch(
            "api.routers.users.force_clear_sessions",
            new=clear_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                await terminate_user_sessions(22, db=db, actor=actor)

        self.assertEqual(db.get_calls, [(models.user.User, 22)])
        clear_mock.assert_awaited_once_with(db, 22)
        audit_mock.assert_called_once_with(
            "user.sessions_terminate_all",
            target_type="user",
            target_id=22,
            after_summary={"terminated_sessions": 2},
            actor_id=10,
            actor_role=UserRole.SUPER_ADMIN.value,
        )


if __name__ == "__main__":
    unittest.main()
