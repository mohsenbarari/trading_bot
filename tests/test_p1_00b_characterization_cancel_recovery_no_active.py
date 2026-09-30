"""Hermetic characterization test for cancel_single_session_recovery when no active recovery exists.

Stage: P1-00b
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20260930-051
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Canonical seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717
Scenario: P1-00B-SEED-AUTH-CANCEL-RECOVERY-NO-ACTIVE-V1

Exclusions:
- Valid active cancellation
- Real lookup/database query
- Persistence
- Retry
- Process reload
- Origin matrix
- Channel rejection
- Ledger/seed promotion
- Stage closure
- Production
- Deploy
- DNS
- Writer
- Secrets
- External mutation

Disclaimer: This characterization does not promote the seed
API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717 nor close Stage P1-00b.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from api.routers.sessions import cancel_single_session_recovery


class FailClosedCharacterizationDB:
    """Fake DB session that fails closed on any unexpected operation or mutation."""

    def __init__(self):
        self.execute = AsyncMock(side_effect=AssertionError("Unexpected DB execute call"))
        self.commit = AsyncMock(side_effect=AssertionError("Unexpected DB commit call"))
        self.rollback = AsyncMock(side_effect=AssertionError("Unexpected DB rollback call"))
        self.flush = AsyncMock(side_effect=AssertionError("Unexpected DB flush call"))
        self.refresh = AsyncMock(side_effect=AssertionError("Unexpected DB refresh call"))
        self.add = Mock(side_effect=AssertionError("Unexpected DB add call"))
        self.delete = Mock(side_effect=AssertionError("Unexpected DB delete call"))

    def assert_untouched(self):
        self.execute.assert_not_called()
        self.commit.assert_not_called()
        self.rollback.assert_not_called()
        self.flush.assert_not_called()
        self.refresh.assert_not_called()
        self.add.assert_not_called()
        self.delete.assert_not_called()


class CancelRecoveryNoActiveCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_uuid_without_active_recovery_stops_before_mutation_or_delivery(self):
        raw_request_id = "550e8400-e29b-41d4-a716-446655440000"
        expected_uuid = uuid.UUID(raw_request_id)
        db = FailClosedCharacterizationDB()

        fence = install_p1_00_network_fence()
        fence_connects_before = len(fence["denied_connects"])
        fence_redis_before = len(fence["live_redis_constructs"])

        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_active, patch(
            "api.routers.sessions.cancel_recovery_request",
            new_callable=Mock,
        ) as mock_cancel, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
        ) as mock_clear, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new_callable=AsyncMock,
        ) as mock_publish, patch(
            "api.routers.sessions.recovery_request_to_dict",
            new_callable=Mock,
        ) as mock_to_dict:
            mock_get_active.return_value = None

            with self.assertRaises(HTTPException) as ctx:
                await cancel_single_session_recovery(raw_request_id, db=db)

            self.assertEqual(ctx.exception.status_code, 404)
            self.assertEqual(ctx.exception.detail, "درخواست بازیابی فعالی یافت نشد")

            mock_get_active.assert_called_once()
            mock_get_active.assert_awaited_once_with(db, expected_uuid)
            self.assertIs(mock_get_active.await_args[0][0], db)
            self.assertEqual(mock_get_active.await_args[0][1], expected_uuid)
            self.assertIsInstance(mock_get_active.await_args[0][1], uuid.UUID)

            mock_cancel.assert_not_called()
            mock_clear.assert_not_called()
            mock_clear.assert_not_awaited()
            mock_publish.assert_not_called()
            mock_publish.assert_not_awaited()
            mock_to_dict.assert_not_called()

            db.assert_untouched()

            self.assertEqual(len(fence["denied_connects"]), fence_connects_before)
            self.assertEqual(len(fence["live_redis_constructs"]), fence_redis_before)


if __name__ == "__main__":
    unittest.main()
