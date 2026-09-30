"""Hermetic characterization test for cancel_single_session_recovery malformed UUID rejection.

Stage: P1-00b
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20260930-051
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Canonical seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717
Scenario: P1-00B-SEED-AUTH-CANCEL-RECOVERY-MALFORMED-UUID-V1

Exclusions:
- Valid UUID outcomes
- Persistence
- Session/table setup
- Origin matrix
- Recovery delivery
- Production access
- Ledger/seed promotion
- Stage closure
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
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from api.routers.sessions import cancel_single_session_recovery


class FakeCharacterizationDB:
    def __init__(self):
        self.execute = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.add = Mock()
        self.delete = Mock()

    def assert_untouched(self):
        self.execute.assert_not_called()
        self.commit.assert_not_called()
        self.rollback.assert_not_called()
        self.flush.assert_not_called()
        self.refresh.assert_not_called()
        self.add.assert_not_called()
        self.delete.assert_not_called()


class CancelRecoveryMalformedUuidCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_uuid_rejects_before_lookup_or_db_work(self):
        db = FakeCharacterizationDB()
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
            with self.assertRaises(HTTPException) as ctx:
                await cancel_single_session_recovery("bad-id", db=db)

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.detail, "شناسه درخواست نامعتبر است")

            mock_get_active.assert_not_called()
            mock_get_active.assert_not_awaited()
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
