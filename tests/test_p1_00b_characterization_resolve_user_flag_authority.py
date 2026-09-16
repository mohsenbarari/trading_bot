"""Characterization tests for resolve_user_flag authority denials.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-POST-USER-FLAGS-RESOLVE-USER-FLAG-L111

This module exercises the single authority-denial outcome of resolve_user_flag
itself for two middle-manager denial cases: resolving a flag on the middle
manager's own account, and resolving a flag on another middle manager's account.
It verifies handler query execution, statement parameter routing, exact 403
denial details, and absence of mutations or downstream side effects.

Exclusions:
- product source
- inventories/counts/blockers
- reconciliation
- Stage closure
- network/Redis
- deploy
- DNS
- Web Writer
- secrets
- unrelated resolve outcomes
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers import user_flags
from models.user import UserRole

SELF_RESOLVE_DENIAL_DETAIL = (
    "\u0645\u062f\u06cc\u0631 \u0646\u0645\u06cc\u200c\u062a\u0648\u0627\u0646\u062f "
    "\u067e\u0631\u0686\u0645 \u062d\u0633\u0627\u0628 \u062e\u0648\u062f\u0634 "
    "\u0631\u0627 \u062a\u0639\u06cc\u06cc\u0646 \u062a\u06a9\u0644\u06cc\u0641 \u06a9\u0646\u062f"
)

MIDDLE_MANAGER_TARGET_DENIAL_DETAIL = (
    "\u0645\u062f\u06cc\u0631 \u0645\u06cc\u0627\u0646\u06cc \u0641\u0642\u0637 "
    "\u0645\u06cc\u200c\u062a\u0648\u0627\u0646\u062f \u06a9\u0627\u0631\u0628\u0631\u0627\u0646 "
    "\u063a\u06cc\u0631\u0627\u062f\u0645\u06cc\u0646 \u0631\u0627 \u0628\u0631\u0631\u0633\u06cc \u06a9\u0646\u062f"
)


class _DedicatedRowResult:
    """Dedicated result object returning exactly the configured (flag, target) tuple."""

    def __init__(self, row: tuple[Any, Any]):
        self._row = row

    def one_or_none(self) -> tuple[Any, Any]:
        return self._row


class _StatementValidatingAsyncSession:
    """Bounded async session adapter validating compiled statement parameters."""

    def __init__(self, expected_flag_id: int, configured_row: tuple[Any, Any]):
        self._expected_flag_id = expected_flag_id
        self._configured_row = configured_row
        self.execute_calls: list[Any] = []
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _DedicatedRowResult:
        self.execute_calls.append(statement)
        compiled = statement.compile()
        if self._expected_flag_id not in compiled.params.values():
            raise AssertionError(
                f"Compiled statement parameters {compiled.params} do not contain expected flag_id {self._expected_flag_id}"
            )
        return _DedicatedRowResult(self._configured_row)


class ResolveUserFlagAuthorityCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_middle_manager_forbidden_targets_are_rejected_before_mutation(self):
        # Case 1: Middle manager actor resolving a flag on their own user row
        self_flag_id = 101
        self_flag = SimpleNamespace(
            id=self_flag_id,
            user_id=77,
            flag_type="session_replacement_frequency",
            reason_code="session_replacement_threshold_exceeded",
            status="open",
            severity="warning",
            details={},
            trigger_count=1,
            first_flagged_at="2026-09-16T10:00:00Z",
            last_flagged_at="2026-09-16T12:00:00Z",
            resolved_at=None,
            resolved_by_user_id=None,
            resolution_note=None,
        )
        self_target = SimpleNamespace(id=77, role=UserRole.MIDDLE_MANAGER)
        self_actor = SimpleNamespace(id=77, role=UserRole.MIDDLE_MANAGER)
        self_payload = user_flags.ResolveUserFlagRequest(note="تلاش برای تعیین تکلیف پرچم خود")
        self_db = _StatementValidatingAsyncSession(self_flag_id, (self_flag, self_target))

        with patch.object(user_flags, "utc_now") as mock_utc_now, patch.object(
            user_flags,
            "attach_user_management_relation_context",
            new=AsyncMock(),
        ) as mock_attach, patch.object(
            user_flags,
            "audit_log",
        ) as mock_audit, patch.object(
            user_flags,
            "_serialize_flag",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx_self:
                await user_flags.resolve_user_flag(
                    flag_id=self_flag_id,
                    payload=self_payload,
                    db=self_db,
                    actor=self_actor,
                )

            self.assertEqual(ctx_self.exception.status_code, 403)
            self.assertEqual(ctx_self.exception.detail, SELF_RESOLVE_DENIAL_DETAIL)

            self.assertEqual(len(self_db.execute_calls), 1)
            compiled_self = self_db.execute_calls[0].compile()
            self.assertIn(self_flag_id, compiled_self.params.values())

            self_db.commit.assert_not_awaited()
            self_db.refresh.assert_not_awaited()

            self.assertEqual(self_flag.status, "open")
            self.assertIsNone(self_flag.resolved_at)
            self.assertIsNone(self_flag.resolved_by_user_id)
            self.assertIsNone(self_flag.resolution_note)

            mock_utc_now.assert_not_called()
            mock_attach.assert_not_awaited()
            mock_audit.assert_not_called()
            mock_serialize.assert_not_called()

        # Case 2: Middle manager actor resolving a flag on another middle manager
        other_flag_id = 202
        other_flag = SimpleNamespace(
            id=other_flag_id,
            user_id=88,
            flag_type="session_replacement_frequency",
            reason_code="session_replacement_threshold_exceeded",
            status="open",
            severity="warning",
            details={},
            trigger_count=2,
            first_flagged_at="2026-09-16T08:00:00Z",
            last_flagged_at="2026-09-16T11:00:00Z",
            resolved_at=None,
            resolved_by_user_id=None,
            resolution_note=None,
        )
        other_target = SimpleNamespace(id=88, role=UserRole.MIDDLE_MANAGER)
        other_actor = SimpleNamespace(id=77, role=UserRole.MIDDLE_MANAGER)
        other_payload = user_flags.ResolveUserFlagRequest(note="تلاش برای تعیین تکلیف همکار مدیر")
        other_db = _StatementValidatingAsyncSession(other_flag_id, (other_flag, other_target))

        with patch.object(user_flags, "utc_now") as mock_utc_now, patch.object(
            user_flags,
            "attach_user_management_relation_context",
            new=AsyncMock(),
        ) as mock_attach, patch.object(
            user_flags,
            "audit_log",
        ) as mock_audit, patch.object(
            user_flags,
            "_serialize_flag",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx_other:
                await user_flags.resolve_user_flag(
                    flag_id=other_flag_id,
                    payload=other_payload,
                    db=other_db,
                    actor=other_actor,
                )

            self.assertEqual(ctx_other.exception.status_code, 403)
            self.assertEqual(ctx_other.exception.detail, MIDDLE_MANAGER_TARGET_DENIAL_DETAIL)

            self.assertEqual(len(other_db.execute_calls), 1)
            compiled_other = other_db.execute_calls[0].compile()
            self.assertIn(other_flag_id, compiled_other.params.values())

            other_db.commit.assert_not_awaited()
            other_db.refresh.assert_not_awaited()

            self.assertEqual(other_flag.status, "open")
            self.assertIsNone(other_flag.resolved_at)
            self.assertIsNone(other_flag.resolved_by_user_id)
            self.assertIsNone(other_flag.resolution_note)

            mock_utc_now.assert_not_called()
            mock_attach.assert_not_awaited()
            mock_audit.assert_not_called()
            mock_serialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
