"""Characterization tests for cancel_upload_session_endpoint failure boundaries.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Tracked Seed: API-MSG-POST-CHAT-CANCEL-UPLOAD-SESSION-ENDPOINT-L2726
Gap Reference: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides direct-handler characterization for failure boundaries in
api.routers.chat.cancel_upload_session_endpoint:
1. Lookup failure: propagation when get_upload_session_for_current_user raises RuntimeError.
2. Cancellation failure: propagation when cancel_upload_session raises RuntimeError.
3. Publication failure: propagation when _publish_upload_session_runtime_event raises RuntimeError.

This characterization executes the endpoint directly with mocked dependencies
and synthetic sentinels. It does not prove durability, concurrency, process reload,
underlying service persistence, test execution, registry advancement, or Stage closure.
The HIGH inventory gap P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE remains open.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import (
    UploadSessionStatusChangeResponse,
    cancel_upload_session_endpoint,
)


class CancelUploadSessionFailureBoundariesCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_lookup_runtime_error_propagates_without_cancellation_or_publish(
        self,
    ) -> None:
        lookup_error = RuntimeError("Synthetic upload session lookup failure")
        fake_db = object()
        fake_current_user = object()
        target_session_id = "session-cancel-lookup-failure-001"

        events: list[str] = []

        async def fake_get_upload_session(
            db: object,
            *,
            session_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(session_id, target_session_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            raise lookup_error

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            new_callable=AsyncMock,
            side_effect=fake_get_upload_session,
        ) as mock_get_upload_session, patch(
            "api.routers.chat.cancel_upload_session",
            new_callable=AsyncMock,
        ) as mock_cancel_upload_session, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new_callable=AsyncMock,
        ) as mock_publish_event, patch(
            "api.routers.chat.UploadSessionStatusChangeResponse",
            wraps=UploadSessionStatusChangeResponse,
        ) as mock_response_constructor:
            with self.assertRaises(RuntimeError) as exc_info:
                await cancel_upload_session_endpoint(
                    session_id=target_session_id,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            self.assertIs(exc_info.exception, lookup_error)
            self.assertEqual(events, ["lookup"])
            mock_get_upload_session.assert_awaited_once_with(
                fake_db,
                session_id=target_session_id,
                current_user=fake_current_user,
            )
            mock_cancel_upload_session.assert_not_called()
            mock_cancel_upload_session.assert_not_awaited()
            mock_publish_event.assert_not_called()
            mock_publish_event.assert_not_awaited()
            mock_response_constructor.assert_not_called()

    async def test_cancellation_runtime_error_propagates_without_publish(
        self,
    ) -> None:
        cancellation_error = RuntimeError(
            "Synthetic upload session cancellation failure"
        )
        fake_db = object()
        fake_current_user = object()
        looked_up_session = object()
        target_session_id = "session-cancel-op-failure-002"

        events: list[str] = []

        async def fake_get_upload_session(
            db: object,
            *,
            session_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(session_id, target_session_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            return looked_up_session

        async def fake_cancel_upload_session(
            db: object,
            *,
            session: object,
        ) -> object:
            self.assertEqual(events, ["lookup"])
            self.assertIs(db, fake_db)
            self.assertIs(session, looked_up_session)
            events.append("cancel")
            raise cancellation_error

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            new_callable=AsyncMock,
            side_effect=fake_get_upload_session,
        ) as mock_get_upload_session, patch(
            "api.routers.chat.cancel_upload_session",
            new_callable=AsyncMock,
            side_effect=fake_cancel_upload_session,
        ) as mock_cancel_upload_session, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new_callable=AsyncMock,
        ) as mock_publish_event, patch(
            "api.routers.chat.UploadSessionStatusChangeResponse",
            wraps=UploadSessionStatusChangeResponse,
        ) as mock_response_constructor:
            with self.assertRaises(RuntimeError) as exc_info:
                await cancel_upload_session_endpoint(
                    session_id=target_session_id,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            self.assertIs(exc_info.exception, cancellation_error)
            self.assertEqual(events, ["lookup", "cancel"])
            mock_get_upload_session.assert_awaited_once_with(
                fake_db,
                session_id=target_session_id,
                current_user=fake_current_user,
            )
            mock_cancel_upload_session.assert_awaited_once_with(
                fake_db,
                session=looked_up_session,
            )
            mock_publish_event.assert_not_called()
            mock_publish_event.assert_not_awaited()
            mock_response_constructor.assert_not_called()

    async def test_publication_runtime_error_propagates_after_cancellation(
        self,
    ) -> None:
        publish_error = RuntimeError("Synthetic upload session publish failure")
        fake_db = object()
        fake_current_user = object()
        looked_up_session = object()
        cancelled_session = object()
        self.assertIsNot(looked_up_session, cancelled_session)
        target_session_id = "session-cancel-publish-failure-003"

        events: list[str] = []

        async def fake_get_upload_session(
            db: object,
            *,
            session_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(session_id, target_session_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            return looked_up_session

        async def fake_cancel_upload_session(
            db: object,
            *,
            session: object,
        ) -> object:
            self.assertEqual(events, ["lookup"])
            self.assertIs(db, fake_db)
            self.assertIs(session, looked_up_session)
            events.append("cancel")
            return cancelled_session

        async def fake_publish_event(
            *,
            db: object,
            current_user: object,
            session: object,
            event_name: str,
            batch_status: object | None = None,
        ) -> None:
            self.assertEqual(events, ["lookup", "cancel"])
            self.assertIs(db, fake_db)
            self.assertIs(current_user, fake_current_user)
            self.assertIs(session, cancelled_session)
            self.assertEqual(event_name, "cancelled")
            events.append("publish")
            raise publish_error

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            new_callable=AsyncMock,
            side_effect=fake_get_upload_session,
        ) as mock_get_upload_session, patch(
            "api.routers.chat.cancel_upload_session",
            new_callable=AsyncMock,
            side_effect=fake_cancel_upload_session,
        ) as mock_cancel_upload_session, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new_callable=AsyncMock,
            side_effect=fake_publish_event,
        ) as mock_publish_event, patch(
            "api.routers.chat.UploadSessionStatusChangeResponse",
            wraps=UploadSessionStatusChangeResponse,
        ) as mock_response_constructor:
            with self.assertRaises(RuntimeError) as exc_info:
                await cancel_upload_session_endpoint(
                    session_id=target_session_id,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            self.assertIs(exc_info.exception, publish_error)
            self.assertEqual(events, ["lookup", "cancel", "publish"])
            mock_get_upload_session.assert_awaited_once_with(
                fake_db,
                session_id=target_session_id,
                current_user=fake_current_user,
            )
            mock_cancel_upload_session.assert_awaited_once_with(
                fake_db,
                session=looked_up_session,
            )
            mock_publish_event.assert_awaited_once_with(
                db=fake_db,
                current_user=fake_current_user,
                session=cancelled_session,
                event_name="cancelled",
            )
            mock_response_constructor.assert_not_called()
            self.assertEqual(mock_get_upload_session.await_count, 1)
            self.assertEqual(mock_cancel_upload_session.await_count, 1)
            self.assertEqual(mock_publish_event.await_count, 1)


if __name__ == "__main__":
    unittest.main()
