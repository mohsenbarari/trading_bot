"""Characterization tests for cancel_upload_batch_endpoint failure boundaries.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Tracked Seed: API-MSG-POST-CHAT-CANCEL-UPLOAD-BATCH-ENDPOINT-L2751
Gap Reference: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides direct-handler characterization for failure boundaries in
api.routers.chat.cancel_upload_batch_endpoint:
1. Cancellation failure: propagation when cancel_upload_batch raises RuntimeError.
2. Session listing failure: propagation when _list_batch_upload_sessions raises RuntimeError.
3. Second-session publication failure: propagation when _publish_upload_session_runtime_event raises RuntimeError.

This characterization executes the endpoint directly with mocked dependencies
and synthetic sentinels. It does not prove durability, concurrency, process reload,
underlying service persistence, test execution, registry advancement, or Stage closure.
The HIGH inventory gap P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE remains open.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import (
    UploadBatchCancelResponse,
    cancel_upload_batch_endpoint,
)
from models.upload_session import UploadBatchStatus


class CancelUploadBatchFailureBoundariesCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_cancellation_runtime_error_propagates_without_list_publish_or_response(
        self,
    ) -> None:
        cancellation_error = RuntimeError(
            "Synthetic upload batch cancellation service failure"
        )
        fake_db = object()
        fake_current_user = object()
        target_batch_id = "batch-cancel-service-failure-001"
        looked_up_batch = SimpleNamespace(
            id=target_batch_id,
            status=UploadBatchStatus.COLLECTING,
        )

        events: list[str] = []

        async def fake_get_upload_batch(
            db: object,
            *,
            batch_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(batch_id, target_batch_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            return looked_up_batch

        async def fake_cancel_upload_batch(
            db: object,
            *,
            batch: object,
        ) -> object:
            self.assertEqual(events, ["lookup"])
            self.assertIs(db, fake_db)
            self.assertIs(batch, looked_up_batch)
            events.append("cancel")
            raise cancellation_error

        with patch(
            "api.routers.chat.get_upload_batch_for_current_user",
            new_callable=AsyncMock,
            side_effect=fake_get_upload_batch,
        ) as mock_get_upload_batch, patch(
            "api.routers.chat.cancel_upload_batch",
            new_callable=AsyncMock,
            side_effect=fake_cancel_upload_batch,
        ) as mock_cancel_upload_batch, patch(
            "api.routers.chat._list_batch_upload_sessions",
            new_callable=AsyncMock,
        ) as mock_list_batch_sessions, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new_callable=AsyncMock,
        ) as mock_publish_event, patch(
            "api.routers.chat.UploadBatchCancelResponse",
            wraps=UploadBatchCancelResponse,
        ) as mock_response_constructor:
            with self.assertRaises(RuntimeError) as exc_info:
                await cancel_upload_batch_endpoint(
                    batch_id=target_batch_id,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            self.assertIs(exc_info.exception, cancellation_error)
            self.assertEqual(events, ["lookup", "cancel"])

            mock_get_upload_batch.assert_awaited_once_with(
                fake_db,
                batch_id=target_batch_id,
                current_user=fake_current_user,
            )
            mock_cancel_upload_batch.assert_awaited_once_with(
                fake_db,
                batch=looked_up_batch,
            )

            self.assertEqual(mock_get_upload_batch.await_count, 1)
            self.assertEqual(mock_cancel_upload_batch.await_count, 1)
            self.assertEqual(mock_get_upload_batch.call_count, 1)
            self.assertEqual(mock_cancel_upload_batch.call_count, 1)

            mock_list_batch_sessions.assert_not_called()
            mock_list_batch_sessions.assert_not_awaited()
            mock_publish_event.assert_not_called()
            mock_publish_event.assert_not_awaited()
            mock_response_constructor.assert_not_called()

    async def test_session_list_runtime_error_propagates_after_cancellation(
        self,
    ) -> None:
        list_error = RuntimeError(
            "Synthetic upload batch session list failure"
        )
        fake_db = object()
        fake_current_user = object()
        looked_up_batch_id = "batch-lookup-pre-cancel-002"
        cancelled_batch_id = "batch-cancelled-post-op-002"
        looked_up_batch = SimpleNamespace(
            id=looked_up_batch_id,
            status=UploadBatchStatus.UPLOADING,
        )
        cancelled_batch = SimpleNamespace(
            id=cancelled_batch_id,
            status=UploadBatchStatus.CANCELLED,
        )
        self.assertIsNot(looked_up_batch, cancelled_batch)
        self.assertNotEqual(looked_up_batch.id, cancelled_batch.id)
        self.assertNotEqual(looked_up_batch.status, cancelled_batch.status)

        events: list[str] = []

        async def fake_get_upload_batch(
            db: object,
            *,
            batch_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(batch_id, looked_up_batch_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            return looked_up_batch

        async def fake_cancel_upload_batch(
            db: object,
            *,
            batch: object,
        ) -> object:
            self.assertEqual(events, ["lookup"])
            self.assertIs(db, fake_db)
            self.assertIs(batch, looked_up_batch)
            events.append("cancel")
            return cancelled_batch

        async def fake_list_batch_sessions(
            db: object,
            *,
            batch_id: str,
        ) -> list[object]:
            self.assertEqual(events, ["lookup", "cancel"])
            self.assertIs(db, fake_db)
            self.assertEqual(batch_id, cancelled_batch_id)
            self.assertNotEqual(batch_id, looked_up_batch_id)
            events.append("list")
            raise list_error

        with patch(
            "api.routers.chat.get_upload_batch_for_current_user",
            new_callable=AsyncMock,
            side_effect=fake_get_upload_batch,
        ) as mock_get_upload_batch, patch(
            "api.routers.chat.cancel_upload_batch",
            new_callable=AsyncMock,
            side_effect=fake_cancel_upload_batch,
        ) as mock_cancel_upload_batch, patch(
            "api.routers.chat._list_batch_upload_sessions",
            new_callable=AsyncMock,
            side_effect=fake_list_batch_sessions,
        ) as mock_list_batch_sessions, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new_callable=AsyncMock,
        ) as mock_publish_event, patch(
            "api.routers.chat.UploadBatchCancelResponse",
            wraps=UploadBatchCancelResponse,
        ) as mock_response_constructor:
            with self.assertRaises(RuntimeError) as exc_info:
                await cancel_upload_batch_endpoint(
                    batch_id=looked_up_batch_id,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            self.assertIs(exc_info.exception, list_error)
            self.assertEqual(events, ["lookup", "cancel", "list"])

            mock_get_upload_batch.assert_awaited_once_with(
                fake_db,
                batch_id=looked_up_batch_id,
                current_user=fake_current_user,
            )
            mock_cancel_upload_batch.assert_awaited_once_with(
                fake_db,
                batch=looked_up_batch,
            )
            mock_list_batch_sessions.assert_awaited_once_with(
                fake_db,
                batch_id=cancelled_batch_id,
            )

            self.assertEqual(
                mock_list_batch_sessions.call_args.kwargs["batch_id"],
                cancelled_batch.id,
            )
            self.assertNotEqual(
                mock_list_batch_sessions.call_args.kwargs["batch_id"],
                looked_up_batch.id,
            )

            self.assertEqual(mock_get_upload_batch.await_count, 1)
            self.assertEqual(mock_cancel_upload_batch.await_count, 1)
            self.assertEqual(mock_list_batch_sessions.await_count, 1)
            self.assertEqual(mock_get_upload_batch.call_count, 1)
            self.assertEqual(mock_cancel_upload_batch.call_count, 1)
            self.assertEqual(mock_list_batch_sessions.call_count, 1)

            mock_publish_event.assert_not_called()
            mock_publish_event.assert_not_awaited()
            mock_response_constructor.assert_not_called()

    async def test_second_session_publication_runtime_error_propagates_after_first_completion(
        self,
    ) -> None:
        publish_error = RuntimeError(
            "Synthetic second session publication failure"
        )
        fake_db = object()
        fake_current_user = object()
        looked_up_batch_id = "batch-lookup-pre-cancel-003"
        cancelled_batch_id = "batch-cancelled-post-op-003"
        looked_up_batch = SimpleNamespace(
            id=looked_up_batch_id,
            status=UploadBatchStatus.COLLECTING,
        )
        cancelled_batch = SimpleNamespace(
            id=cancelled_batch_id,
            status=UploadBatchStatus.CANCELLED,
        )
        self.assertIsNot(looked_up_batch, cancelled_batch)
        self.assertNotEqual(looked_up_batch.id, cancelled_batch.id)
        self.assertNotEqual(looked_up_batch.status, cancelled_batch.status)

        session_beta = SimpleNamespace(
            id="session-beta-002",
            order_marker="second_in_sort_first_in_list",
        )
        session_alpha = SimpleNamespace(
            id="session-alpha-001",
            order_marker="first_in_sort_second_in_list",
        )
        session_gamma = SimpleNamespace(
            id="session-gamma-003",
            order_marker="third_in_sort_third_in_list",
        )

        self.assertIsNot(session_beta, session_alpha)
        self.assertIsNot(session_alpha, session_gamma)
        self.assertIsNot(session_beta, session_gamma)
        self.assertNotEqual(session_beta.id, session_alpha.id)
        self.assertNotEqual(session_alpha.id, session_gamma.id)
        self.assertNotEqual(session_beta.id, session_gamma.id)

        batch_sessions = [session_beta, session_alpha, session_gamma]

        events: list[str] = []

        async def fake_get_upload_batch(
            db: object,
            *,
            batch_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(batch_id, looked_up_batch_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            return looked_up_batch

        async def fake_cancel_upload_batch(
            db: object,
            *,
            batch: object,
        ) -> object:
            self.assertEqual(events, ["lookup"])
            self.assertIs(db, fake_db)
            self.assertIs(batch, looked_up_batch)
            events.append("cancel")
            return cancelled_batch

        async def fake_list_batch_sessions(
            db: object,
            *,
            batch_id: str,
        ) -> list[object]:
            self.assertEqual(events, ["lookup", "cancel"])
            self.assertIs(db, fake_db)
            self.assertEqual(batch_id, cancelled_batch_id)
            events.append("list")
            return batch_sessions

        async def fake_publish_event(
            *,
            db: object,
            current_user: object,
            session: object,
            event_name: str,
            batch_status: object | None = None,
        ) -> None:
            self.assertIs(db, fake_db)
            self.assertIs(current_user, fake_current_user)
            self.assertEqual(event_name, "cancelled")
            self.assertIs(batch_status, cancelled_batch.status)

            if session is session_beta:
                self.assertEqual(events, ["lookup", "cancel", "list"])
                events.append("first_publish_complete")
                return
            elif session is session_alpha:
                self.assertEqual(
                    events,
                    ["lookup", "cancel", "list", "first_publish_complete"],
                )
                events.append("second_publish_attempt")
                raise publish_error
            else:
                self.fail(f"Unexpected session passed to publisher: {session}")

        with patch(
            "api.routers.chat.get_upload_batch_for_current_user",
            new_callable=AsyncMock,
            side_effect=fake_get_upload_batch,
        ) as mock_get_upload_batch, patch(
            "api.routers.chat.cancel_upload_batch",
            new_callable=AsyncMock,
            side_effect=fake_cancel_upload_batch,
        ) as mock_cancel_upload_batch, patch(
            "api.routers.chat._list_batch_upload_sessions",
            new_callable=AsyncMock,
            side_effect=fake_list_batch_sessions,
        ) as mock_list_batch_sessions, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new_callable=AsyncMock,
            side_effect=fake_publish_event,
        ) as mock_publish_event, patch(
            "api.routers.chat.UploadBatchCancelResponse",
            wraps=UploadBatchCancelResponse,
        ) as mock_response_constructor:
            with self.assertRaises(RuntimeError) as exc_info:
                await cancel_upload_batch_endpoint(
                    batch_id=looked_up_batch_id,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            self.assertIs(exc_info.exception, publish_error)
            self.assertEqual(
                events,
                [
                    "lookup",
                    "cancel",
                    "list",
                    "first_publish_complete",
                    "second_publish_attempt",
                ],
            )

            mock_get_upload_batch.assert_awaited_once_with(
                fake_db,
                batch_id=looked_up_batch_id,
                current_user=fake_current_user,
            )
            mock_cancel_upload_batch.assert_awaited_once_with(
                fake_db,
                batch=looked_up_batch,
            )
            mock_list_batch_sessions.assert_awaited_once_with(
                fake_db,
                batch_id=cancelled_batch_id,
            )

            self.assertEqual(mock_get_upload_batch.await_count, 1)
            self.assertEqual(mock_cancel_upload_batch.await_count, 1)
            self.assertEqual(mock_list_batch_sessions.await_count, 1)
            self.assertEqual(mock_publish_event.await_count, 2)
            self.assertEqual(mock_get_upload_batch.call_count, 1)
            self.assertEqual(mock_cancel_upload_batch.call_count, 1)
            self.assertEqual(mock_list_batch_sessions.call_count, 1)
            self.assertEqual(mock_publish_event.call_count, 2)

            first_publish_kwargs = mock_publish_event.call_args_list[0].kwargs
            self.assertIs(first_publish_kwargs["db"], fake_db)
            self.assertIs(first_publish_kwargs["current_user"], fake_current_user)
            self.assertIs(first_publish_kwargs["session"], session_beta)
            self.assertEqual(first_publish_kwargs["event_name"], "cancelled")
            self.assertIs(first_publish_kwargs["batch_status"], cancelled_batch.status)

            second_publish_kwargs = mock_publish_event.call_args_list[1].kwargs
            self.assertIs(second_publish_kwargs["db"], fake_db)
            self.assertIs(second_publish_kwargs["current_user"], fake_current_user)
            self.assertIs(second_publish_kwargs["session"], session_alpha)
            self.assertEqual(second_publish_kwargs["event_name"], "cancelled")
            self.assertIs(second_publish_kwargs["batch_status"], cancelled_batch.status)

            for call_item in mock_publish_event.call_args_list:
                self.assertIsNot(call_item.kwargs["session"], session_gamma)

            mock_response_constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
