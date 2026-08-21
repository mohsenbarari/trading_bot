import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers import user_flags
from models.user import UserRole


class _RowResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class UserFlagsRouterTests(unittest.IsolatedAsyncioTestCase):
    def test_middle_manager_cannot_resolve_self_or_an_admin_flag(self):
        actor = SimpleNamespace(id=7, role=UserRole.MIDDLE_MANAGER)

        with self.assertRaises(HTTPException) as own_error:
            user_flags._assert_can_resolve_flag(actor, SimpleNamespace(id=7, role=UserRole.STANDARD))
        self.assertEqual(own_error.exception.status_code, 403)

        with self.assertRaises(HTTPException) as admin_error:
            user_flags._assert_can_resolve_flag(
                actor,
                SimpleNamespace(id=8, role=UserRole.SUPER_ADMIN),
            )
        self.assertEqual(admin_error.exception.status_code, 403)

    async def test_resolve_marks_only_the_selected_open_case_and_audits_it(self):
        flag = SimpleNamespace(
            id=41,
            flag_type="session_replacement_frequency",
            status="open",
            resolved_at=None,
            resolved_by_user_id=None,
            resolution_note=None,
        )
        target = SimpleNamespace(id=9, role=UserRole.STANDARD)
        actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_RowResult((flag, target))),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        serialized = {"id": 41, "status": "resolved"}

        with patch.object(
            user_flags,
            "attach_user_management_relation_context",
            new=AsyncMock(),
        ) as attach_mock, patch.object(
            user_flags,
            "_serialize_flag",
            return_value=serialized,
        ), patch.object(user_flags, "audit_log") as audit_mock:
            result = await user_flags.resolve_user_flag(
                41,
                user_flags.ResolveUserFlagRequest(note="  بررسی شد  "),
                db=db,
                actor=actor,
            )

        self.assertEqual(result, serialized)
        self.assertEqual(flag.status, "resolved")
        self.assertEqual(flag.resolved_by_user_id, 1)
        self.assertEqual(flag.resolution_note, "بررسی شد")
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(flag)
        attach_mock.assert_awaited_once_with(db, [target])
        audit_mock.assert_called_once()

    async def test_resolve_returns_not_found_for_a_closed_or_missing_case(self):
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_RowResult(None)),
            commit=AsyncMock(),
        )

        with self.assertRaises(HTTPException) as error:
            await user_flags.resolve_user_flag(
                41,
                user_flags.ResolveUserFlagRequest(),
                db=db,
                actor=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )

        self.assertEqual(error.exception.status_code, 404)
        db.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
