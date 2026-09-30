"""Direct-handler characterization test for empty recovery prompts lookup.

Stage Card: docs/refactor/two-server/section-1/stages/P1-00b.md
Active Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20260930-045
Tracked Inventory Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact Surface Seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-GET-SESSIONS-GET-PENDING-SINGLE-SESSION-RECOVERY-PROMPTS-L788
Scenario Key: P1-00B-SEED-AUTH-PENDING-RECOVERY-PROMPTS-ADMIN-EMPTY-V1

Disclaimer:
This module provides partial characterization only; it explicitly does NOT promote
the seed or close P1-00b.
"""

from __future__ import annotations

import os
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

# Canonical P1-00 network fence installed before application imports
from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from sqlalchemy.ext.asyncio import AsyncSession

from api.routers.sessions import get_pending_single_session_recovery_prompts
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


class PendingRecoveryPromptsAdminEmptyCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization tests for get_pending_single_session_recovery_prompts empty lookup."""

    async def test_authorized_admin_empty_lookup_returns_empty_without_serialization(self):
        """Prove authorized admin roles return [] on empty lookup without serialization or DB queries."""
        self.assertTrue(_FENCE["installed"], "P1-00 network fence must be installed")
        denied_connects_before = len(_FENCE["denied_connects"])
        live_redis_before = len(_FENCE["live_redis_constructs"])

        authorized_roles_and_ids = [
            (UserRole.SUPER_ADMIN, 801),
            (UserRole.MIDDLE_MANAGER, 802),
        ]

        for role, admin_id in authorized_roles_and_ids:
            with self.subTest(role=role, admin_id=admin_id):
                mock_db = _build_strict_mock_db()
                admin_user = User(
                    id=admin_id,
                    account_name=f"admin_{admin_id}",
                    mobile_number=f"0912000{admin_id:04d}",
                    full_name=f"Admin {admin_id}",
                    address="Tehran",
                    role=role,
                )

                call_order: list[str] = []

                async def fake_guard(db_arg: Any, user_arg: Any) -> None:
                    call_order.append("guard")

                async def fake_list_targets(db_arg: Any, *, admin_user_id: int) -> list:
                    call_order.append("lookup")
                    return []

                mock_guard = AsyncMock(side_effect=fake_guard)
                mock_list_targets = AsyncMock(side_effect=fake_list_targets)
                mock_serialize = MagicMock(
                    side_effect=AssertionError(
                        "_serialize_admin_recovery_prompt must not be called when pending targets is empty"
                    )
                )

                with patch(
                    "api.routers.sessions.ensure_non_accountant_session_management",
                    mock_guard,
                ), patch(
                    "api.routers.sessions.list_pending_admin_recovery_targets",
                    mock_list_targets,
                ), patch(
                    "api.routers.sessions._serialize_admin_recovery_prompt",
                    mock_serialize,
                ):
                    result = await get_pending_single_session_recovery_prompts(
                        db=mock_db,
                        current_user=admin_user,
                    )

                self.assertIsInstance(result, list)
                self.assertEqual(result, [])
                self.assertEqual(
                    call_order,
                    ["guard", "lookup"],
                    "ensure_non_accountant_session_management must precede list_pending_admin_recovery_targets",
                )
                mock_guard.assert_awaited_once_with(mock_db, admin_user)
                self.assertEqual(mock_guard.await_count, 1)

                mock_list_targets.assert_awaited_once_with(mock_db, admin_user_id=admin_id)
                self.assertEqual(mock_list_targets.await_count, 1)
                self.assertIs(
                    mock_list_targets.call_args.args[0],
                    mock_db,
                    "list_pending_admin_recovery_targets must receive identical db object",
                )
                self.assertEqual(
                    mock_list_targets.call_args.kwargs["admin_user_id"],
                    admin_id,
                    "list_pending_admin_recovery_targets must receive current_user.id",
                )

                mock_serialize.assert_not_called()
                self.assertEqual(
                    mock_db.method_calls,
                    [],
                    "No database operations should be invoked on the session double",
                )

        self.assertEqual(
            len(_FENCE["denied_connects"]),
            denied_connects_before,
            "Network fence denied connects counter must remain unchanged",
        )
        self.assertEqual(
            len(_FENCE["live_redis_constructs"]),
            live_redis_before,
            "Network fence live Redis construction counter must remain unchanged",
        )


if __name__ == "__main__":
    unittest.main()
