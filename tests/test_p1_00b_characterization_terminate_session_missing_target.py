"""Characterization test for terminate_session missing target UUID.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md (P1-00b)
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20260930-051
Surface behavior ID: API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075
Gap reference: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario: P1-00B-SEED-AUTH-TERMINATE-MISSING-TARGET-V1
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from typing import Any
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import HTTPException, Request
from jose import jwt as jose_jwt
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from api.routers.sessions import terminate_session  # noqa: E402
from core.config import settings  # noqa: E402
from models.session import UserSession  # noqa: E402
from models.user import User, UserRole  # noqa: E402
from tests.test_p1_00b_characterization_real_async_session_helper import (  # noqa: E402
    real_async_session,
)
from tests.test_p1_00b_characterization_terminate_session import (  # noqa: E402
    assert_user_session_query,
)


class TerminateSessionMissingTargetCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_uuid_missing_target_returns_404(self) -> None:
        current_user = User(
            id=1001,
            account_name="terminate_missing_test_user",
            mobile_number="+989120000001",
            normalized_account_name="terminate_missing_test_user",
            normalized_mobile_number="09120000001",
            full_name="Terminate Missing Target User",
            address="Tehran, Iran",
            role=UserRole.STANDARD,
        )

        caller_sid = uuid.uuid4()
        target_sid = uuid.uuid4()
        self.assertNotEqual(caller_sid, target_sid)

        bearer_payload = {
            "sid": str(caller_sid),
            "sub": str(current_user.id),
        }
        token = jose_jwt.encode(
            bearer_payload,
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

        request = Request(
            scope={
                "type": "http",
                "method": "DELETE",
                "path": f"/{target_sid}",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("utf-8")),
                ],
            }
        )

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            UserSession.__table__.create(bind=engine, checkfirst=True)

            observed_statements: list[Any] = []

            def _capture_orm_execute(orm_execute_state: Any) -> None:
                observed_statements.append(orm_execute_state.statement)

            async with real_async_session(engine) as db:
                event.listen(db.sync_session, "do_orm_execute", _capture_orm_execute)
                try:
                    with patch(
                        "api.routers.sessions.is_user_accountant",
                        new_callable=AsyncMock,
                    ) as mock_is_accountant, patch(
                        "api.routers.sessions.get_active_sessions",
                        side_effect=AssertionError("get_active_sessions must not be called"),
                    ) as mock_get_active_sessions, patch(
                        "api.routers.sessions.logout_session",
                        side_effect=AssertionError("logout_session must not be called"),
                    ) as mock_logout_session, patch(
                        "api.routers.sessions.audit_log",
                        side_effect=AssertionError("audit_log must not be called"),
                    ) as mock_audit_log:
                        mock_is_accountant.return_value = False

                        fence = install_p1_00_network_fence()
                        denied_connects_before = len(fence["denied_connects"])
                        live_redis_constructs_before = len(fence["live_redis_constructs"])

                        with self.assertRaises(HTTPException) as exc_context:
                            await terminate_session(
                                session_id=str(target_sid),
                                request=request,
                                db=db,
                                current_user=current_user,
                            )

                        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
                        self.assertEqual(
                            len(fence["live_redis_constructs"]),
                            live_redis_constructs_before,
                        )

                        self.assertEqual(exc_context.exception.status_code, 404)
                        self.assertEqual(exc_context.exception.detail, "نشست یافت نشد")

                        mock_is_accountant.assert_awaited_once_with(db, current_user.id)
                        mock_get_active_sessions.assert_not_called()
                        mock_logout_session.assert_not_called()
                        mock_audit_log.assert_not_called()

                        self.assertEqual(len(observed_statements), 1)
                        target_query = observed_statements[0]
                        assert_user_session_query(
                            self,
                            target_query,
                            expected_session_id=target_sid,
                            expected_user_id=current_user.id,
                        )
                finally:
                    event.remove(db.sync_session, "do_orm_execute", _capture_orm_execute)
        finally:
            try:
                UserSession.__table__.drop(bind=engine, checkfirst=True)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
