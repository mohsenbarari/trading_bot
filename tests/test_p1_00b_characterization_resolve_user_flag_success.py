"""Characterization tests for resolve_user_flag super-admin success outcome.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-POST-USER-FLAGS-RESOLVE-USER-FLAG-L111

This module exercises the single successful super-admin resolution outcome of
api.routers.user_flags.resolve_user_flag. It freezes current behavior only:
an open UserFlag joined to its standard User target is resolved with a stripped
non-empty note, the exact utc_now value and actor id, then committed, refreshed,
relation-context-attached, audited, serialized, and returned in the source-observed
order. No closure claims are made for Stage P1-00b or the seed blocker.

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

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

from api.routers import user_flags
from models.user import UserRole


class _DedicatedRowResult:
    """Dedicated result object returning exactly the configured (flag, target) tuple."""

    def __init__(self, row: tuple[Any, Any]):
        self._row = row

    def one_or_none(self) -> tuple[Any, Any]:
        return self._row


class _StatementValidatingAsyncSession:
    """Bounded async session adapter validating compiled statement parameters."""

    def __init__(
        self, expected_flag_id: int, configured_row: tuple[Any, Any], timeline: list[str]
    ):
        self._expected_flag_id = expected_flag_id
        self._configured_row = configured_row
        self._timeline = timeline
        self.execute_calls: list[Any] = []
        self.commit_calls: list[tuple[()]] = []
        self.refresh_calls: list[Any] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _DedicatedRowResult:
        if not hasattr(statement, "compile"):
            raise AssertionError("Executed statement is not compilable")
        compiled = statement.compile()
        params = list(compiled.params.values())
        if self._expected_flag_id not in params or "open" not in params:
            raise AssertionError(
                f"Compiled statement parameters {compiled.params} do not contain expected flag_id {self._expected_flag_id} and status 'open'"
            )
        compiled_sql = str(statement)
        if "user_flags" not in compiled_sql or "users" not in compiled_sql:
            raise AssertionError(
                f"Statement {compiled_sql} is not the expected UserFlag/User join query"
            )
        self.execute_calls.append(statement)
        self._timeline.append("execute")
        return _DedicatedRowResult(self._configured_row)

    async def commit(self) -> None:
        self.commit_calls.append(())
        self._timeline.append("commit")

    async def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_calls.append(instance)
        self._timeline.append("refresh")


class ResolveUserFlagSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_super_admin_resolution_trims_note_commits_and_audits(self):
        flag_id = 101
        actor_id = 1
        target_id = 42

        actor = SimpleNamespace(
            id=actor_id,
            account_name="admin_user",
            mobile_number="+989120000000",
            normalized_account_name="admin_user",
            normalized_mobile_number="+989120000000",
            full_name="Super Admin",
            address="Tehran, Central HQ",
            role=UserRole.SUPER_ADMIN,
        )

        target = SimpleNamespace(
            id=target_id,
            account_name="target_user",
            mobile_number="+989123456789",
            normalized_account_name="target_user",
            normalized_mobile_number="+989123456789",
            full_name="Target User",
            address="Tehran, Branch Office",
            role=UserRole.STANDARD,
        )

        first_flagged = datetime(2026, 9, 16, 8, 0, 0, tzinfo=timezone.utc)
        last_flagged = datetime(2026, 9, 16, 9, 0, 0, tzinfo=timezone.utc)
        created_at = datetime(2026, 9, 16, 8, 0, 0, tzinfo=timezone.utc)
        updated_at = datetime(2026, 9, 16, 9, 0, 0, tzinfo=timezone.utc)

        flag = SimpleNamespace(
            id=flag_id,
            user_id=target_id,
            flag_type="session_replacement_frequency",
            reason_code="session_replacement_threshold_exceeded",
            status="open",
            severity="warning",
            details={"metric": "frequency", "threshold": 5},
            trigger_count=1,
            first_flagged_at=first_flagged,
            last_flagged_at=last_flagged,
            resolved_at=None,
            resolved_by_user_id=None,
            resolution_note=None,
            created_at=created_at,
            updated_at=updated_at,
        )

        raw_note = "   \t  بررسی شد و پرچم با تأیید مدیر مرتفع گردید   \n "
        expected_stripped_note = "بررسی شد و پرچم با تأیید مدیر مرتفع گردید"
        payload = user_flags.ResolveUserFlagRequest(note=raw_note)

        fixed_now = datetime(2026, 9, 16, 12, 34, 56, tzinfo=timezone.utc)
        serializer_marker = SimpleNamespace(serialized=True, flag_id=flag_id)

        timeline: list[str] = []
        db = _StatementValidatingAsyncSession(flag_id, (flag, target), timeline)

        async def fake_attach(db_arg: Any, users_arg: Any) -> list[Any]:
            timeline.append("attach")
            return list(users_arg)

        def fake_audit(*args: Any, **kwargs: Any) -> None:
            timeline.append("audit")

        def fake_serialize(flag_arg: Any, target_arg: Any) -> Any:
            timeline.append("serialize")
            return serializer_marker

        with patch.object(
            user_flags, "utc_now", return_value=fixed_now
        ) as mock_utc_now, patch.object(
            user_flags,
            "attach_user_management_relation_context",
            new=AsyncMock(side_effect=fake_attach),
        ) as mock_attach, patch.object(
            user_flags,
            "audit_log",
            side_effect=fake_audit,
        ) as mock_audit, patch.object(
            user_flags,
            "_serialize_flag",
            side_effect=fake_serialize,
        ) as mock_serialize:
            result = await user_flags.resolve_user_flag(
                flag_id=flag_id,
                payload=payload,
                db=db,
                actor=actor,
            )

        self.assertIs(result, serializer_marker)
        mock_utc_now.assert_called_once_with()

        self.assertEqual(flag.status, "resolved")
        self.assertEqual(flag.resolved_at, fixed_now)
        self.assertEqual(flag.resolved_by_user_id, actor_id)
        self.assertEqual(flag.resolution_note, expected_stripped_note)

        self.assertEqual(len(db.execute_calls), 1)
        self.assertEqual(len(db.commit_calls), 1)
        self.assertEqual(len(db.refresh_calls), 1)
        self.assertIs(db.refresh_calls[0], flag)

        mock_attach.assert_awaited_once_with(db, [target])
        self.assertIs(mock_attach.await_args[0][0], db)
        self.assertIs(mock_attach.await_args[0][1][0], target)

        mock_serialize.assert_called_once_with(flag, target)
        self.assertIs(mock_serialize.call_args[0][0], flag)
        self.assertIs(mock_serialize.call_args[0][1], target)

        mock_audit.assert_called_once()
        audit_args, audit_kwargs = mock_audit.call_args
        self.assertEqual(audit_args, ("user_flag.resolve",))
        self.assertEqual(audit_kwargs["target_type"], "user_flag")
        self.assertEqual(audit_kwargs["target_id"], flag_id)
        self.assertEqual(audit_kwargs["actor_id"], actor_id)
        self.assertEqual(audit_kwargs["actor_role"], UserRole.SUPER_ADMIN.value)
        self.assertEqual(
            audit_kwargs["after_summary"],
            {
                "flag_type": "session_replacement_frequency",
                "flagged_user_id": target_id,
                "status": "resolved",
                "has_note": True,
            },
        )

        self.assertEqual(
            timeline,
            ["execute", "commit", "refresh", "attach", "audit", "serialize"],
        )


if __name__ == "__main__":
    unittest.main()
