"""Characterization tests for cancel_upload_batch_endpoint success boundaries.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Tracked Seed: API-MSG-POST-CHAT-CANCEL-UPLOAD-BATCH-ENDPOINT-L2751
Gap Reference: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage Card: docs/refactor/two-server/section-1/stages/P1-00b.md

This module provides direct-handler characterization for success boundaries in
api.routers.chat.cancel_upload_batch_endpoint:
1. Non-empty batch sessions: ordered sequential runtime event publication fan-out
   across non-sorted sessions, argument forwarding, and response mapping.
2. Empty batch sessions: zero publication awaits, argument forwarding, and response
   mapping with a synthetic contract probe.

This characterization executes the endpoint directly with mocked dependencies
and synthetic sentinels. It does not prove durability, concurrency, process reload,
underlying service persistence, test execution, registry advancement, or Stage closure.
The HIGH inventory gap P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE remains open.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
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


class CancelUploadBatchSuccessCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_cancel_upload_batch_success_ordered_fanout_and_response_mapping(
        self,
    ) -> None:
        """Test successful cancellation with ordered publication fan-out and response mapping."""
        fake_db = object()
        fake_current_user = object()
        caller_batch_id = "batch-caller-nonempty-001"
        looked_up_batch = SimpleNamespace(
            id=caller_batch_id,
            status=UploadBatchStatus.UPLOADING,
        )
        cancelled_batch = SimpleNamespace(
            id="batch-cancelled-distinct-002",
            status=UploadBatchStatus.CANCELLED,
        )

        self.assertIsNot(looked_up_batch, cancelled_batch)
        self.assertNotEqual(looked_up_batch.id, cancelled_batch.id)
        self.assertNotEqual(looked_up_batch.status, cancelled_batch.status)

        session_beta = SimpleNamespace(
            id="session-beta-002",
            label="second_lexicographical_first_in_stream",
        )
        session_alpha = SimpleNamespace(
            id="session-alpha-001",
            label="first_lexicographical_second_in_stream",
        )
        session_gamma = SimpleNamespace(
            id="session-gamma-003",
            label="third_lexicographical_third_in_stream",
        )

        self.assertIsNot(session_beta, session_alpha)
        self.assertIsNot(session_alpha, session_gamma)
        self.assertIsNot(session_beta, session_gamma)
        self.assertNotEqual(session_beta.id, session_alpha.id)
        self.assertNotEqual(session_alpha.id, session_gamma.id)
        self.assertNotEqual(session_beta.id, session_gamma.id)

        # Deliberately non-sorted order (beta, alpha, gamma)
        batch_sessions = [session_beta, session_alpha, session_gamma]

        events: list[str] = []
        active_publisher_count = 0

        async def fake_get_upload_batch(
            db: object,
            *,
            batch_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(batch_id, caller_batch_id)
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
            self.assertEqual(batch_id, cancelled_batch.id)
            self.assertNotEqual(batch_id, caller_batch_id)
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
            nonlocal active_publisher_count
            self.assertEqual(
                active_publisher_count,
                0,
                "Concurrent publication detected: prior await must complete before next begins",
            )
            active_publisher_count += 1
            self.assertIs(db, fake_db)
            self.assertIs(current_user, fake_current_user)
            self.assertEqual(event_name, "cancelled")
            self.assertIs(batch_status, cancelled_batch.status)

            session_id = getattr(session, "id", str(session))
            events.append(f"publish_begin:{session_id}")
            # Explicit cooperative asynchronous scheduling yield (no wall-clock sleep or timing guess)
            await asyncio.sleep(0)
            self.assertEqual(
                active_publisher_count,
                1,
                "Concurrent execution detected during yield in publisher",
            )
            active_publisher_count -= 1
            events.append(f"publish_complete:{session_id}")

        def spy_response_constructor(
            *args: Any, **kwargs: Any
        ) -> UploadBatchCancelResponse:
            self.assertEqual(
                active_publisher_count,
                0,
                "Response constructor invoked while publisher is active",
            )
            events.append("response_construct")
            return UploadBatchCancelResponse(*args, **kwargs)

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
            side_effect=spy_response_constructor,
        ) as mock_response_constructor:
            response = await cancel_upload_batch_endpoint(
                batch_id=caller_batch_id,
                current_user=fake_current_user,
                db=fake_db,
            )

            # Assert sequential execution progression
            self.assertEqual(
                events,
                [
                    "lookup",
                    "cancel",
                    "list",
                    "publish_begin:session-beta-002",
                    "publish_complete:session-beta-002",
                    "publish_begin:session-alpha-001",
                    "publish_complete:session-alpha-001",
                    "publish_begin:session-gamma-003",
                    "publish_complete:session-gamma-003",
                    "response_construct",
                ],
            )

            # Assert lookup collaborator calls, awaits, and argument identity
            mock_get_upload_batch.assert_awaited_once_with(
                fake_db,
                batch_id=caller_batch_id,
                current_user=fake_current_user,
            )
            self.assertEqual(mock_get_upload_batch.await_count, 1)
            self.assertEqual(mock_get_upload_batch.call_count, 1)
            self.assertIs(mock_get_upload_batch.call_args.args[0], fake_db)
            self.assertEqual(
                mock_get_upload_batch.call_args.kwargs["batch_id"],
                caller_batch_id,
            )
            self.assertIs(
                mock_get_upload_batch.call_args.kwargs["current_user"],
                fake_current_user,
            )

            # Assert cancel collaborator calls, awaits, and argument identity
            mock_cancel_upload_batch.assert_awaited_once_with(
                fake_db,
                batch=looked_up_batch,
            )
            self.assertEqual(mock_cancel_upload_batch.await_count, 1)
            self.assertEqual(mock_cancel_upload_batch.call_count, 1)
            self.assertIs(mock_cancel_upload_batch.call_args.args[0], fake_db)
            self.assertIs(
                mock_cancel_upload_batch.call_args.kwargs["batch"],
                looked_up_batch,
            )

            # Assert list collaborator calls, awaits, and argument identity using returned cancelled batch id
            mock_list_batch_sessions.assert_awaited_once_with(
                fake_db,
                batch_id=cancelled_batch.id,
            )
            self.assertEqual(mock_list_batch_sessions.await_count, 1)
            self.assertEqual(mock_list_batch_sessions.call_count, 1)
            self.assertIs(mock_list_batch_sessions.call_args.args[0], fake_db)
            self.assertEqual(
                mock_list_batch_sessions.call_args.kwargs["batch_id"],
                cancelled_batch.id,
            )
            self.assertNotEqual(
                mock_list_batch_sessions.call_args.kwargs["batch_id"],
                caller_batch_id,
            )

            # Assert publication: exactly once per supplied session, in order, without drift
            self.assertEqual(mock_publish_event.await_count, 3)
            self.assertEqual(mock_publish_event.call_count, 3)
            for idx, expected_session in enumerate(batch_sessions):
                call_args, call_kwargs = mock_publish_event.call_args_list[idx]
                self.assertEqual(
                    call_args, (), f"Positional argument drift at call index {idx}"
                )
                self.assertEqual(
                    set(call_kwargs.keys()),
                    {"db", "current_user", "session", "event_name", "batch_status"},
                    f"Keyword argument drift at call index {idx}",
                )
                self.assertIs(call_kwargs["db"], fake_db)
                self.assertIs(call_kwargs["current_user"], fake_current_user)
                self.assertIs(call_kwargs["session"], expected_session)
                self.assertEqual(call_kwargs["event_name"], "cancelled")
                self.assertIs(
                    call_kwargs["batch_status"], cancelled_batch.status
                )

            # Assert response constructor invocation arguments and ordering
            mock_response_constructor.assert_called_once_with(
                batch_id=cancelled_batch.id,
                status=cancelled_batch.status,
            )
            self.assertEqual(mock_response_constructor.call_count, 1)
            self.assertEqual(mock_response_constructor.call_args.args, ())
            self.assertEqual(
                mock_response_constructor.call_args.kwargs["batch_id"],
                cancelled_batch.id,
            )
            self.assertIs(
                mock_response_constructor.call_args.kwargs["status"],
                cancelled_batch.status,
            )

            # Assert real response type and field mappings
            self.assertIsInstance(response, UploadBatchCancelResponse)
            self.assertEqual(response.batch_id, cancelled_batch.id)
            self.assertNotEqual(response.batch_id, caller_batch_id)
            self.assertEqual(response.status, cancelled_batch.status)
            self.assertNotEqual(response.status, looked_up_batch.status)
            self.assertEqual(
                response.model_dump(mode="json"),
                {
                    "batch_id": cancelled_batch.id,
                    "status": cancelled_batch.status.value,
                },
            )

    async def test_cancel_upload_batch_success_empty_sessions_and_synthetic_status_forwarding(
        self,
    ) -> None:
        """Test successful cancellation with empty session list and synthetic status forwarding."""
        fake_db = object()
        fake_current_user = object()
        caller_batch_id = "batch-caller-empty-001"
        looked_up_batch = SimpleNamespace(
            id=caller_batch_id,
            status=UploadBatchStatus.COLLECTING,
        )

        # Synthetic handler contract probe using UploadBatchStatus.EXPIRED to discriminate
        # status forwarding from hardcoding. This tests the handler boundary's propagation
        # of whatever status cancel_upload_batch returns, rather than reflecting an underlying
        # cancel-service production outcome.
        cancelled_batch = SimpleNamespace(
            id="batch-cancelled-synthetic-expired-002",
            status=UploadBatchStatus.EXPIRED,
        )

        self.assertIsNot(looked_up_batch, cancelled_batch)
        self.assertNotEqual(looked_up_batch.id, cancelled_batch.id)
        self.assertNotEqual(looked_up_batch.status, cancelled_batch.status)

        batch_sessions: list[object] = []
        events: list[str] = []

        async def fake_get_upload_batch(
            db: object,
            *,
            batch_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(batch_id, caller_batch_id)
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
            self.assertEqual(batch_id, cancelled_batch.id)
            self.assertNotEqual(batch_id, caller_batch_id)
            events.append("list")
            return batch_sessions

        def spy_response_constructor(
            *args: Any, **kwargs: Any
        ) -> UploadBatchCancelResponse:
            self.assertEqual(
                events,
                ["lookup", "cancel", "list"],
                "Response constructor must be called strictly after session list completion",
            )
            events.append("response_construct")
            return UploadBatchCancelResponse(*args, **kwargs)

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
            side_effect=spy_response_constructor,
        ) as mock_response_constructor:
            response = await cancel_upload_batch_endpoint(
                batch_id=caller_batch_id,
                current_user=fake_current_user,
                db=fake_db,
            )

            # Assert event progression: lookup -> cancel -> list -> response_construct
            self.assertEqual(
                events,
                ["lookup", "cancel", "list", "response_construct"],
            )

            # Assert lookup collaborator calls, awaits, and argument identity
            mock_get_upload_batch.assert_awaited_once_with(
                fake_db,
                batch_id=caller_batch_id,
                current_user=fake_current_user,
            )
            self.assertEqual(mock_get_upload_batch.await_count, 1)
            self.assertEqual(mock_get_upload_batch.call_count, 1)
            self.assertIs(mock_get_upload_batch.call_args.args[0], fake_db)
            self.assertEqual(
                mock_get_upload_batch.call_args.kwargs["batch_id"],
                caller_batch_id,
            )
            self.assertIs(
                mock_get_upload_batch.call_args.kwargs["current_user"],
                fake_current_user,
            )

            # Assert cancel collaborator calls, awaits, and argument identity
            mock_cancel_upload_batch.assert_awaited_once_with(
                fake_db,
                batch=looked_up_batch,
            )
            self.assertEqual(mock_cancel_upload_batch.await_count, 1)
            self.assertEqual(mock_cancel_upload_batch.call_count, 1)
            self.assertIs(mock_cancel_upload_batch.call_args.args[0], fake_db)
            self.assertIs(
                mock_cancel_upload_batch.call_args.kwargs["batch"],
                looked_up_batch,
            )

            # Assert list collaborator calls, awaits, and argument identity using returned cancelled batch id
            mock_list_batch_sessions.assert_awaited_once_with(
                fake_db,
                batch_id=cancelled_batch.id,
            )
            self.assertEqual(mock_list_batch_sessions.await_count, 1)
            self.assertEqual(mock_list_batch_sessions.call_count, 1)
            self.assertIs(mock_list_batch_sessions.call_args.args[0], fake_db)
            self.assertEqual(
                mock_list_batch_sessions.call_args.kwargs["batch_id"],
                cancelled_batch.id,
            )
            self.assertNotEqual(
                mock_list_batch_sessions.call_args.kwargs["batch_id"],
                caller_batch_id,
            )

            # Assert zero publisher calls/awaits when session list is empty
            mock_publish_event.assert_not_called()
            mock_publish_event.assert_not_awaited()
            self.assertEqual(mock_publish_event.call_count, 0)
            self.assertEqual(mock_publish_event.await_count, 0)

            # Assert response constructor invocation arguments and ordering
            mock_response_constructor.assert_called_once_with(
                batch_id=cancelled_batch.id,
                status=cancelled_batch.status,
            )
            self.assertEqual(mock_response_constructor.call_count, 1)
            self.assertEqual(mock_response_constructor.call_args.args, ())
            self.assertEqual(
                mock_response_constructor.call_args.kwargs["batch_id"],
                cancelled_batch.id,
            )
            self.assertIs(
                mock_response_constructor.call_args.kwargs["status"],
                cancelled_batch.status,
            )

            # Assert real response type and field mappings including synthetic EXPIRED status
            self.assertIsInstance(response, UploadBatchCancelResponse)
            self.assertEqual(response.batch_id, cancelled_batch.id)
            self.assertNotEqual(response.batch_id, caller_batch_id)
            self.assertEqual(response.status, UploadBatchStatus.EXPIRED)
            self.assertEqual(response.status, cancelled_batch.status)
            self.assertNotEqual(response.status, looked_up_batch.status)
            self.assertEqual(
                response.model_dump(mode="json"),
                {
                    "batch_id": cancelled_batch.id,
                    "status": "expired",
                },
            )


if __name__ == "__main__":
    unittest.main()
