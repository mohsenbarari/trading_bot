"""Hermetic direct-handler characterization unit for POST /verify (verify_my_session).

Under Stage card docs/refactor/two-server/section-1/stages/P1-00b.md and Assignment
WA-P1-00B-FREEZE-20260904-001.md, this test module provides bounded characterization
evidence for surface-api.json#API-AUTH-POST-SESSIONS-VERIFY-MY-SESSION-L1031 under
scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.

Evidence promotion and Stage closure remain separate work; this test does not close
the seed blocker or complete Stage P1-00b.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Install the existing P1-00 fail-closed network fence before importing the sessions router.
install_p1_00_network_fence()

from fastapi import HTTPException  # noqa: E402

from api.routers.sessions import VerifySessionRequest, verify_my_session  # noqa: E402


class VerifyMySessionCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Direct-handler characterization for api.routers.sessions.verify_my_session."""

    async def test_verify_my_session_active_token_returns_active_status(self) -> None:
        """Characterize an active refresh token: helper returns a session sentinel and handler returns status active."""
        sentinel_db = object()
        session_sentinel = object()
        token = "token-active-evidence-123"
        req = VerifySessionRequest(refresh_token=token)

        with patch(
            "core.services.session_service.get_session_by_refresh_token",
            new_callable=AsyncMock,
        ) as mock_get_session:
            mock_get_session.return_value = session_sentinel

            result = await verify_my_session(req=req, db=sentinel_db)

            mock_get_session.assert_awaited_once_with(sentinel_db, token)
            self.assertEqual(result, {"status": "active"})

    async def test_verify_my_session_missing_token_raises_401(self) -> None:
        """Characterize a missing refresh token: helper returns None and handler raises 401 with Persian detail."""
        sentinel_db = object()
        token = "token-missing-evidence-404"
        req = VerifySessionRequest(refresh_token=token)

        with patch(
            "core.services.session_service.get_session_by_refresh_token",
            new_callable=AsyncMock,
        ) as mock_get_session:
            mock_get_session.return_value = None

            with self.assertRaises(HTTPException) as ctx:
                await verify_my_session(req=req, db=sentinel_db)

            self.assertEqual(ctx.exception.status_code, 401)
            self.assertEqual(ctx.exception.detail, "نشست شما باطل شده است")
            mock_get_session.assert_awaited_once_with(sentinel_db, token)

    async def test_verify_my_session_revoked_token_raises_401(self) -> None:
        """Characterize a revoked refresh token: helper returns None and handler raises 401 with Persian detail."""
        sentinel_db = object()
        token = "token-revoked-evidence-999"
        req = VerifySessionRequest(refresh_token=token)

        with patch(
            "core.services.session_service.get_session_by_refresh_token",
            new_callable=AsyncMock,
        ) as mock_get_session:
            mock_get_session.return_value = None

            with self.assertRaises(HTTPException) as ctx:
                await verify_my_session(req=req, db=sentinel_db)

            self.assertEqual(ctx.exception.status_code, 401)
            self.assertEqual(ctx.exception.detail, "نشست شما باطل شده است")
            mock_get_session.assert_awaited_once_with(sentinel_db, token)


if __name__ == "__main__":
    unittest.main()
