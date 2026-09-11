"""Direct-handler characterization tests for get_pending_single_session_recovery_prompts.

Stage Card: docs/refactor/two-server/section-1/stages/P1-00b.md
Shared Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Tracked Inventory Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact Surface Seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-GET-SESSIONS-GET-PENDING-SINGLE-SESSION-RECOVERY-PROMPTS-L788

Disclaimer:
This module provides partial characterization only; it explicitly does NOT promote
the seed or close P1-00b.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from typing import Any
import uuid

# Canonical P1-00 network fence installed before application imports
from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.routers.sessions import (
    _serialize_admin_recovery_prompt,
    get_pending_single_session_recovery_prompts,
)
from core.services.single_session_recovery_service import (
    build_identity_submitted_text,
    build_initial_recovery_request_text,
    get_recovery_requester_display_name,
    should_show_inline_recovery_prompt,
)
from models.session import (
    SingleSessionRecoveryAdminTarget,
    SingleSessionRecoveryRequest,
    SingleSessionRecoveryStatus,
)
from models.user import User, UserRole


def _build_strict_mock_db() -> MagicMock:
    mock_db = MagicMock(spec=AsyncSession)
    mock_db.execute = MagicMock(side_effect=AssertionError("Unexpected db.execute call"))
    mock_db.commit = MagicMock(side_effect=AssertionError("Unexpected db.commit call"))
    mock_db.rollback = MagicMock(side_effect=AssertionError("Unexpected db.rollback call"))
    mock_db.flush = MagicMock(side_effect=AssertionError("Unexpected db.flush call"))
    mock_db.add = MagicMock(side_effect=AssertionError("Unexpected db.add call"))
    mock_db.delete = MagicMock(side_effect=AssertionError("Unexpected db.delete call"))
    return mock_db


class StrictGuardOrderUser:
    """User double that fails if role is inspected before guard completion."""

    def __init__(self, user_id: int):
        self.id = user_id
        self.role_inspected = False

    @property
    def role(self) -> UserRole:
        self.role_inspected = True
        raise AssertionError(
            "current_user.role accessed before ensure_non_accountant_session_management completed"
        )


class PendingSingleSessionRecoveryPromptsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization tests for get_pending_single_session_recovery_prompts."""

    def _build_deterministic_fixture_rows(
        self, admin_user_id: int = 802
    ) -> list[tuple[SingleSessionRecoveryAdminTarget, SingleSessionRecoveryRequest, User]]:
        # Row 1: PENDING_ADMIN_REVIEW, account_name display, future inline action -> visible: True, can_request_identity: True
        req_1_id = uuid.UUID("10000000-0000-0000-0000-000000000001")
        target_1_id = uuid.UUID("20000000-0000-0000-0000-000000000001")
        req_1 = SingleSessionRecoveryRequest(
            id=req_1_id,
            user_id=101,
            requester_device_name="Chrome 128 on macOS",
            requester_ip="192.168.1.55",
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            inline_action_expires_at=datetime(2099, 5, 20, 10, 15, 30),
            chat_action_expires_at=datetime(2099, 5, 20, 11, 15, 30),
        )
        target_1 = SingleSessionRecoveryAdminTarget(
            id=target_1_id,
            recovery_request_id=req_1_id,
            admin_user_id=admin_user_id,
            current_action_message_id=1001,
        )
        user_1 = User(
            id=101,
            account_name="sarah_trader",
            full_name="Sarah Trader",
            mobile_number="09121111111",
        )

        # Row 2: IDENTITY_SUBMITTED, account_name display ("omid_trader"), current_action_message_id is None -> visible: False, can_request_identity: False
        req_2_id = uuid.UUID("10000000-0000-0000-0000-000000000002")
        target_2_id = uuid.UUID("20000000-0000-0000-0000-000000000002")
        req_2 = SingleSessionRecoveryRequest(
            id=req_2_id,
            user_id=202,
            requester_device_name="Firefox 130 on Windows",
            requester_ip="10.10.0.42",
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            inline_action_expires_at=datetime(2099, 6, 1, 8, 0, 0),
            chat_action_expires_at=datetime(2099, 6, 1, 9, 0, 0),
        )
        target_2 = SingleSessionRecoveryAdminTarget(
            id=target_2_id,
            recovery_request_id=req_2_id,
            admin_user_id=admin_user_id,
            current_action_message_id=None,
        )
        user_2 = User(
            id=202,
            account_name="omid_trader",
            full_name="Omid Trader",
            mobile_number="09122222222",
        )

        # Row 3: IDENTITY_SUBMITTED, full_name display ("Reza Omidi"), current_action_message_id is 3003 -> visible: True, can_request_identity: False
        req_3_id = uuid.UUID("10000000-0000-0000-0000-000000000003")
        target_3_id = uuid.UUID("20000000-0000-0000-0000-000000000003")
        req_3 = SingleSessionRecoveryRequest(
            id=req_3_id,
            user_id=303,
            requester_device_name="Safari 17 on iPhone",
            requester_ip="172.16.0.99",
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            inline_action_expires_at=datetime(2099, 7, 1, 14, 30, 0),
            chat_action_expires_at=datetime(2099, 7, 1, 15, 30, 0),
        )
        target_3 = SingleSessionRecoveryAdminTarget(
            id=target_3_id,
            recovery_request_id=req_3_id,
            admin_user_id=admin_user_id,
            current_action_message_id=3003,
        )
        user_3 = User(
            id=303,
            account_name="",
            full_name="Reza Omidi",
            mobile_number="09123333333",
        )

        return [
            (target_1, req_1, user_1),
            (target_2, req_2, user_2),
            (target_3, req_3, user_3),
        ]

    def test_requirement_1_fence_and_docstring_conformance(self):
        """Verify network fence installation and absence of invented UserRole members."""
        self.assertTrue(_FENCE["installed"], "P1-00 network fence must be marked installed")
        self.assertNotIn("BUYER", UserRole.__members__, "UserRole.BUYER does not exist in authoritative schema")
        self.assertFalse(hasattr(UserRole, "BUYER"), "UserRole must not possess BUYER member")

    async def test_requirement_2_guard_rejection_precedes_all_handler_actions(self):
        """Prove session-management guard rejection occurs before any other collaborator action."""
        mock_db = _build_strict_mock_db()
        current_user = StrictGuardOrderUser(user_id=403)
        sentinel_exc = HTTPException(
            status_code=403,
            detail="sentinel-guard-session-management-rejection",
        )

        mock_guard = AsyncMock(side_effect=sentinel_exc)
        mock_list_targets = AsyncMock(
            side_effect=AssertionError("list_pending_admin_recovery_targets should not be called")
        )
        mock_serialize = MagicMock(
            side_effect=AssertionError("_serialize_admin_recovery_prompt should not be called")
        )

        with patch("api.routers.sessions.ensure_non_accountant_session_management", mock_guard), \
             patch("api.routers.sessions.list_pending_admin_recovery_targets", mock_list_targets), \
             patch("api.routers.sessions._serialize_admin_recovery_prompt", mock_serialize):
            with self.assertRaises(HTTPException) as ctx:
                await get_pending_single_session_recovery_prompts(
                    db=mock_db,
                    current_user=current_user,
                )

            self.assertIs(ctx.exception, sentinel_exc)
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "sentinel-guard-session-management-rejection")

        mock_guard.assert_awaited_once_with(mock_db, current_user)
        self.assertFalse(current_user.role_inspected, "Role must not be inspected when guard raises")
        mock_list_targets.assert_not_called()
        mock_serialize.assert_not_called()
        self.assertEqual(mock_db.method_calls, [])

    async def test_requirement_3_non_admin_returns_empty_list_after_guard(self):
        """Prove authoritative non-admin roles return [] only after guard completion without lookup/serialization."""
        non_admin_roles = [
            UserRole.WATCH,
            UserRole.STANDARD,
            UserRole.POLICE,
        ]

        for non_admin_role in non_admin_roles:
            with self.subTest(role=non_admin_role):
                mock_db = _build_strict_mock_db()
                user = User(id=55, role=non_admin_role)

                call_order: list[str] = []

                async def fake_guard(db_arg: Any, user_arg: Any) -> None:
                    call_order.append("guard_completed")

                mock_guard = AsyncMock(side_effect=fake_guard)
                mock_list_targets = AsyncMock(
                    side_effect=AssertionError("list_pending_admin_recovery_targets must not be called for non-admin")
                )
                mock_serialize = MagicMock(
                    side_effect=AssertionError("_serialize_admin_recovery_prompt must not be called for non-admin")
                )

                with patch("api.routers.sessions.ensure_non_accountant_session_management", mock_guard), \
                     patch("api.routers.sessions.list_pending_admin_recovery_targets", mock_list_targets), \
                     patch("api.routers.sessions._serialize_admin_recovery_prompt", mock_serialize):
                    result = await get_pending_single_session_recovery_prompts(
                        db=mock_db,
                        current_user=user,
                    )

                self.assertEqual(result, [])
                self.assertEqual(call_order, ["guard_completed"])
                mock_guard.assert_awaited_once_with(mock_db, user)
                mock_list_targets.assert_not_called()
                mock_serialize.assert_not_called()
                self.assertEqual(mock_db.method_calls, [])

    async def test_requirement_4_authorized_roles_lookup_ordering_and_row_order(self):
        """Prove authorized roles await guard then lookup with identical DB and admin_user_id, preserving row order."""
        authorized_roles_and_ids = [
            (UserRole.MIDDLE_MANAGER, 701),
            (UserRole.SUPER_ADMIN, 802),
        ]

        fixture_rows = self._build_deterministic_fixture_rows(admin_user_id=701)

        for role, admin_id in authorized_roles_and_ids:
            with self.subTest(role=role, admin_id=admin_id):
                mock_db = _build_strict_mock_db()
                user = User(id=admin_id, role=role)

                call_log: list[tuple[str, Any, Any]] = []

                async def fake_guard(db_arg: Any, user_arg: Any) -> None:
                    call_log.append(("guard", db_arg, user_arg))

                async def fake_list_targets(db_arg: Any, *, admin_user_id: int) -> list:
                    call_log.append(("lookup", db_arg, admin_user_id))
                    return fixture_rows

                mock_guard = AsyncMock(side_effect=fake_guard)
                mock_list_targets = AsyncMock(side_effect=fake_list_targets)

                with patch("api.routers.sessions.ensure_non_accountant_session_management", mock_guard), \
                     patch("api.routers.sessions.list_pending_admin_recovery_targets", mock_list_targets):
                    result = await get_pending_single_session_recovery_prompts(
                        db=mock_db,
                        current_user=user,
                    )

                self.assertEqual(len(call_log), 2)
                self.assertEqual(call_log[0], ("guard", mock_db, user))
                self.assertEqual(call_log[1], ("lookup", mock_db, admin_id))
                self.assertIs(call_log[1][1], mock_db, "Lookup must receive the identical db instance")
                self.assertEqual(call_log[1][2], admin_id, "Lookup must receive current_user.id as admin_user_id")

                mock_guard.assert_awaited_once_with(mock_db, user)
                mock_list_targets.assert_awaited_once_with(mock_db, admin_user_id=admin_id)
                self.assertEqual(mock_list_targets.await_count, 1, "Reject extra lookups")

                self.assertEqual(len(result), len(fixture_rows))
                for i, (expected_target, expected_req, expected_user) in enumerate(fixture_rows):
                    self.assertEqual(result[i]["recovery_id"], str(expected_req.id))
                    self.assertEqual(result[i]["user_id"], expected_user.id)

                self.assertEqual(mock_db.method_calls, [])

    async def test_requirement_5_authorized_serialization_exact_equality_and_branches(self):
        """Prove real prompt serialization produces exact expected dictionaries across all status/visibility branches."""
        mock_db = _build_strict_mock_db()
        current_admin = User(id=802, role=UserRole.SUPER_ADMIN)

        fixture_rows = self._build_deterministic_fixture_rows(admin_user_id=current_admin.id)

        denied_connects_before = len(_FENCE["denied_connects"])
        live_redis_before = len(_FENCE["live_redis_constructs"])

        mock_guard = AsyncMock(return_value=None)
        mock_list_targets = AsyncMock(return_value=fixture_rows)

        with patch("api.routers.sessions.ensure_non_accountant_session_management", mock_guard), \
             patch("api.routers.sessions.list_pending_admin_recovery_targets", mock_list_targets):
            result = await get_pending_single_session_recovery_prompts(
                db=mock_db,
                current_user=current_admin,
            )

        expected_prompts = [
            {
                "recovery_id": "10000000-0000-0000-0000-000000000001",
                "status": "pending_admin_review",
                "prompt_type": "initial_request",
                "user_id": 101,
                "user_name": "sarah_trader",
                "requester_device_name": "Chrome 128 on macOS",
                "requester_ip": "192.168.1.55",
                "inline_action_expires_at": "2099-05-20T10:15:30",
                "chat_action_expires_at": "2099-05-20T11:15:30",
                "current_action_message_id": 1001,
                "can_approve": True,
                "can_reject": True,
                "can_request_identity": True,
                "visible": True,
                "message": "کاربر [sarah_trader] بعلت عدم دسترسی به دستگاه قبلی خود؛ درخواست منقضی کردن اطلاعات لاگین خود از دستگاه قدیم و لاگین کردن در دستگاه جدید را دارد.",
            },
            {
                "recovery_id": "10000000-0000-0000-0000-000000000002",
                "status": "identity_submitted",
                "prompt_type": "identity_submitted",
                "user_id": 202,
                "user_name": "omid_trader",
                "requester_device_name": "Firefox 130 on Windows",
                "requester_ip": "10.10.0.42",
                "inline_action_expires_at": "2099-06-01T08:00:00",
                "chat_action_expires_at": "2099-06-01T09:00:00",
                "current_action_message_id": None,
                "can_approve": True,
                "can_reject": True,
                "can_request_identity": False,
                "visible": False,
                "message": "کاربر [omid_trader] مدارک احراز هویت خود را برای بررسی ارسال کرده است.",
            },
            {
                "recovery_id": "10000000-0000-0000-0000-000000000003",
                "status": "identity_submitted",
                "prompt_type": "identity_submitted",
                "user_id": 303,
                "user_name": "Reza Omidi",
                "requester_device_name": "Safari 17 on iPhone",
                "requester_ip": "172.16.0.99",
                "inline_action_expires_at": "2099-07-01T14:30:00",
                "chat_action_expires_at": "2099-07-01T15:30:00",
                "current_action_message_id": 3003,
                "can_approve": True,
                "can_reject": True,
                "can_request_identity": False,
                "visible": True,
                "message": "کاربر [Reza Omidi] مدارک احراز هویت خود را برای بررسی ارسال کرده است.",
            },
        ]

        self.assertEqual(result, expected_prompts)

        required_keys = {
            "recovery_id",
            "status",
            "prompt_type",
            "user_id",
            "user_name",
            "requester_device_name",
            "requester_ip",
            "inline_action_expires_at",
            "chat_action_expires_at",
            "current_action_message_id",
            "can_approve",
            "can_reject",
            "can_request_identity",
            "visible",
            "message",
        }
        for item in result:
            self.assertEqual(set(item.keys()), required_keys)

        self.assertEqual(mock_db.method_calls, [])
        self.assertEqual(len(_FENCE["denied_connects"]), denied_connects_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), live_redis_before)

    def test_requirement_5_direct_serializer_parity(self):
        """Verify _serialize_admin_recovery_prompt directly on fixture rows."""
        fixture_rows = self._build_deterministic_fixture_rows(admin_user_id=999)
        for target, req, user in fixture_rows:
            serialized = _serialize_admin_recovery_prompt(req, user, target)
            self.assertEqual(serialized["recovery_id"], str(req.id))
            self.assertEqual(serialized["user_id"], user.id)
            self.assertEqual(serialized["current_action_message_id"], target.current_action_message_id)
            self.assertEqual(serialized["visible"], should_show_inline_recovery_prompt(req, target))
            self.assertTrue(serialized["can_approve"])
            self.assertTrue(serialized["can_reject"])


if __name__ == "__main__":
    unittest.main()
