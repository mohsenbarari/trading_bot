"""Direct characterization tests for api.routers.users actor/target guard helpers.

This module directly characterizes only the in-memory authorization and audit
helpers and does not complete P1-00b, seed-handler coverage, origin-interaction
coverage, or Stage acceptance.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from fastapi import HTTPException

from api.routers.users import (
    ADMIN_ROLE_VALUES,
    _audit_actor,
    _audit_user_summary,
    _ensure_actor_can_manage_target,
    _ensure_actor_can_mutate_target,
    _is_admin_role,
    _normalize_actor,
    _role_value,
)
from core.enums import UserAccountStatus, UserRole


class UsersGuardMatrixCharacterizationTests(unittest.TestCase):
    """Characterization tests for actor normalization, role helpers, and guard matrices."""

    def test_admin_role_constants_and_enum_definitions(self):
        self.assertEqual(
            ADMIN_ROLE_VALUES,
            {UserRole.SUPER_ADMIN.value, UserRole.MIDDLE_MANAGER.value},
        )
        self.assertEqual(UserRole.WATCH.value, "تماشا")
        self.assertEqual(UserRole.STANDARD.value, "عادی")
        self.assertEqual(UserRole.POLICE.value, "پلیس")
        self.assertEqual(UserRole.MIDDLE_MANAGER.value, "مدیر میانی")
        self.assertEqual(UserRole.SUPER_ADMIN.value, "مدیر ارشد")
        self.assertFalse(hasattr(UserRole, "BUYER"))

    def test_normalization_and_role_helpers(self):
        # Actor without role attribute or with None role normalizes to None
        self.assertIsNone(_normalize_actor(SimpleNamespace(id=1)))
        self.assertIsNone(_normalize_actor(SimpleNamespace(id=2, role=None)))
        self.assertIsNone(_normalize_actor(None))

        # Actor with a non-None role is returned by identity
        actor_standard = SimpleNamespace(id=3, role=UserRole.STANDARD)
        self.assertIs(_normalize_actor(actor_standard), actor_standard)
        actor_str_role = SimpleNamespace(id=4, role="custom_role")
        self.assertIs(_normalize_actor(actor_str_role), actor_str_role)

        # _role_value returns None for None, resolves Enum members, and passes strings through
        self.assertIsNone(_role_value(None))
        self.assertEqual(
            _role_value(UserRole.MIDDLE_MANAGER),
            UserRole.MIDDLE_MANAGER.value,
        )
        self.assertEqual(
            _role_value(UserRole.SUPER_ADMIN),
            UserRole.SUPER_ADMIN.value,
        )
        self.assertEqual(_role_value("مدیر ارشد"), "مدیر ارشد")
        self.assertEqual(_role_value("any_str"), "any_str")

        # _is_admin_role is true only for the two ADMIN_ROLE_VALUES
        self.assertTrue(_is_admin_role(UserRole.SUPER_ADMIN))
        self.assertTrue(_is_admin_role(UserRole.SUPER_ADMIN.value))
        self.assertTrue(_is_admin_role(UserRole.MIDDLE_MANAGER))
        self.assertTrue(_is_admin_role(UserRole.MIDDLE_MANAGER.value))
        self.assertFalse(_is_admin_role(UserRole.STANDARD))
        self.assertFalse(_is_admin_role(UserRole.STANDARD.value))
        self.assertFalse(_is_admin_role(UserRole.POLICE))
        self.assertFalse(_is_admin_role(UserRole.WATCH))
        self.assertFalse(_is_admin_role(None))

    def test_audit_actor(self):
        # None or role-less actor returns actor_id: None, actor_role: None
        self.assertEqual(_audit_actor(None), {"actor_id": None, "actor_role": None})
        self.assertEqual(
            _audit_actor(SimpleNamespace()),
            {"actor_id": None, "actor_role": None},
        )
        self.assertEqual(
            _audit_actor(SimpleNamespace(id=10)),
            {"actor_id": None, "actor_role": None},
        )
        self.assertEqual(
            _audit_actor(SimpleNamespace(id=11, role=None)),
            {"actor_id": None, "actor_role": None},
        )

        # Actor with id and UserRole.MIDDLE_MANAGER returns id and enum value string
        manager_actor = SimpleNamespace(id=55, role=UserRole.MIDDLE_MANAGER)
        self.assertEqual(
            _audit_actor(manager_actor),
            {"actor_id": 55, "actor_role": UserRole.MIDDLE_MANAGER.value},
        )

    def test_audit_user_summary_with_all_authoritative_fields(self):
        dt = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)
        user = SimpleNamespace(
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
            trading_restricted_until=dt,
            max_sessions=2,
            can_block_users=True,
            max_blocked_users=50,
            max_accountants=3,
            max_customers=10,
        )
        summary = _audit_user_summary(user)
        self.assertEqual(
            summary,
            {
                "role": UserRole.STANDARD.value,
                "account_status": UserAccountStatus.ACTIVE.value,
                "is_deleted": False,
                "trading_restricted": True,
                "max_sessions": 2,
                "can_block_users": True,
                "max_blocked_users": 50,
                "max_accountants": 3,
                "max_customers": 10,
            },
        )

    def test_audit_user_summary_empty_defaults(self):
        empty_user = SimpleNamespace()
        summary = _audit_user_summary(empty_user)
        self.assertEqual(
            summary,
            {
                "role": None,
                "account_status": None,
                "is_deleted": None,
                "trading_restricted": False,
                "max_sessions": None,
                "can_block_users": None,
                "max_blocked_users": None,
                "max_accountants": None,
                "max_customers": None,
            },
        )

    def test_ensure_actor_can_manage_target(self):
        # actor=None returns None
        self.assertIsNone(
            _ensure_actor_can_manage_target(
                None, SimpleNamespace(id=10, role=UserRole.STANDARD)
            )
        )

        # Middle manager may manage a STANDARD target
        mm = SimpleNamespace(id=1, role=UserRole.MIDDLE_MANAGER)
        std_target = SimpleNamespace(id=2, role=UserRole.STANDARD)
        self.assertIs(_ensure_actor_can_manage_target(mm, std_target), mm)

        # Middle manager may manage their own MIDDLE_MANAGER target when ids are equal
        mm_self_target = SimpleNamespace(id=1, role=UserRole.MIDDLE_MANAGER)
        self.assertIs(_ensure_actor_can_manage_target(mm, mm_self_target), mm)

        # Middle manager targeting a different MIDDLE_MANAGER raises 403 with exact ZWNJ string
        other_mm_target = SimpleNamespace(id=2, role=UserRole.MIDDLE_MANAGER)
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_manage_target(mm, other_mm_target)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )

        # Middle manager targeting SUPER_ADMIN raises 403 with exact ZWNJ string
        sa_target = SimpleNamespace(id=3, role=UserRole.SUPER_ADMIN)
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_manage_target(mm, sa_target)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )

        # Missing-id fail-closed case for an admin target:
        # 1. actor id is None
        mm_no_id = SimpleNamespace(id=None, role=UserRole.MIDDLE_MANAGER)
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_manage_target(mm_no_id, other_mm_target)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )

        # 2. target id is None
        admin_target_no_id = SimpleNamespace(id=None, role=UserRole.MIDDLE_MANAGER)
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_manage_target(mm, admin_target_no_id)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )

        # 3. both ids are None
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_manage_target(mm_no_id, admin_target_no_id)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )

    def test_ensure_actor_can_mutate_target(self):
        # actor=None returns None
        self.assertIsNone(
            _ensure_actor_can_mutate_target(
                None, SimpleNamespace(id=10, role=UserRole.STANDARD)
            )
        )

        # SUPER_ADMIN may mutate a STANDARD target and returns the actor by identity
        sa = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        std_target = SimpleNamespace(id=2, role=UserRole.STANDARD)
        self.assertIs(_ensure_actor_can_mutate_target(sa, std_target), sa)

        # Any actor with the same non-None id as the target raises 403
        sa_self = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_mutate_target(sa, sa_self)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر نمی‌تواند عملیات حساس مدیریتی را روی حساب خودش انجام دهد",
        )

        mm = SimpleNamespace(id=5, role=UserRole.MIDDLE_MANAGER)
        mm_self = SimpleNamespace(id=5, role=UserRole.MIDDLE_MANAGER)
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_mutate_target(mm, mm_self)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر نمی‌تواند عملیات حساس مدیریتی را روی حساب خودش انجام دهد",
        )

        # MIDDLE_MANAGER targeting a different admin raises the manage-target 403 detail
        other_mm = SimpleNamespace(id=6, role=UserRole.MIDDLE_MANAGER)
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_mutate_target(mm, other_mm)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )

        other_sa = SimpleNamespace(id=7, role=UserRole.SUPER_ADMIN)
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_mutate_target(mm, other_sa)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )

        # SUPER_ADMIN targeting a different SUPER_ADMIN raises 403 with exact super-admin detail
        with self.assertRaises(HTTPException) as ctx:
            _ensure_actor_can_mutate_target(sa, other_sa)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مدیر ارشد نمی‌تواند عملیات حساس مدیریتی را روی حساب مدیر ارشد انجام دهد",
        )


if __name__ == "__main__":
    unittest.main()
