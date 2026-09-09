"""Characterization tests for cancel_upload_session_endpoint successful forwarding and response mapping.

Stage: P1-00b
Stage Card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001
Tracked Seed: API-MSG-POST-CHAT-CANCEL-UPLOAD-SESSION-ENDPOINT-L2726
Gap Reference: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides direct-handler characterization for successful returned-session
forwarding and response mapping in api.routers.chat.cancel_upload_session_endpoint:
1. Success with nonempty returned final_chat_file_id and UploadSessionStatus.CANCELLED.
2. Success with None returned final_chat_file_id and UploadSessionStatus.EXPIRED as an
   explicitly labelled synthetic response-mapping contract probe.

This characterization executes the endpoint directly with mocked dependencies
and synthetic sentinels only. It does not prove service persistence, durability,
concurrency, reload, actual test execution, or Stage/gap closure.
The HIGH inventory gap P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE remains open.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
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
from models.upload_session import UploadSessionStatus


class CancelUploadSessionSuccessCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_cancel_upload_session_success_with_nonempty_final_chat_file(
        self,
    ) -> None:
        caller_session_id = "session-caller-req-nonempty-001"
        fake_db = object()
        fake_current_user = object()

        looked_up_session = SimpleNamespace(
            id="session-lookup-id-nonempty-001",
            status=UploadSessionStatus.UPLOADING,
            final_chat_file_id="chat-file-lookup-initial-001",
        )
        returned_session = SimpleNamespace(
            id="session-returned-id-nonempty-001",
            status=UploadSessionStatus.CANCELLED,
            final_chat_file_id="chat-file-synthetic-nonempty-001",
        )

        self.assertNotEqual(caller_session_id, looked_up_session.id)
        self.assertNotEqual(caller_session_id, returned_session.id)
        self.assertNotEqual(looked_up_session.id, returned_session.id)
        self.assertNotEqual(looked_up_session.status, returned_session.status)
        self.assertNotEqual(
            looked_up_session.final_chat_file_id,
            returned_session.final_chat_file_id,
        )
        self.assertIsNot(looked_up_session, returned_session)

        events: list[str] = []
        publication_started = asyncio.Event()
        allow_publication_to_finish = asyncio.Event()
        publication_finished = asyncio.Event()

        async def release_publication_task() -> None:
            await asyncio.wait_for(publication_started.wait(), timeout=2.0)
            await asyncio.sleep(0)
            allow_publication_to_finish.set()

        async def fake_get_upload_session(
            db: object,
            *,
            session_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(session_id, caller_session_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            return looked_up_session

        async def fake_cancel_upload_session(
            db: object,
            *,
            session: object,
            **extra_kwargs: Any,
        ) -> object:
            self.assertEqual(events, ["lookup"])
            self.assertIs(db, fake_db)
            self.assertIs(session, looked_up_session)
            self.assertEqual(
                extra_kwargs,
                {},
                "cancel_upload_session must be called with no reason override",
            )
            events.append("cancel")
            return returned_session

        async def fake_publish_event(
            *pos_args: Any,
            db: object,
            current_user: object,
            session: object,
            event_name: str,
            **extra_kwargs: Any,
        ) -> None:
            self.assertEqual(
                pos_args,
                (),
                "publication collaborator called with unexpected positional args",
            )
            self.assertEqual(events, ["lookup", "cancel"])
            self.assertIs(db, fake_db)
            self.assertIs(current_user, fake_current_user)
            self.assertIs(
                session,
                returned_session,
                "publication must receive cancellation-returned session by identity",
            )
            self.assertEqual(event_name, "cancelled")
            self.assertEqual(
                extra_kwargs,
                {},
                "publication collaborator must receive no batch_status override",
            )
            events.append("publish_begin")
            publication_started.set()
            await asyncio.wait_for(
                allow_publication_to_finish.wait(), timeout=2.0
            )
            events.append("publish_complete")
            publication_finished.set()

        real_response_constructor = UploadSessionStatusChangeResponse
        response_constructor_calls: list[dict[str, Any]] = []

        def spy_response_constructor(
            *args: Any,
            **kwargs: Any,
        ) -> UploadSessionStatusChangeResponse:
            self.assertTrue(
                publication_finished.is_set(),
                "Publication must complete before response model constructor is invoked",
            )
            self.assertIn(
                "publish_complete",
                events,
                "publish_complete must precede response model constructor invocation",
            )
            self.assertEqual(
                events,
                ["lookup", "cancel", "publish_begin", "publish_complete"],
                "Ordered progression must be lookup -> cancel -> publish_begin -> publish_complete before response construction",
            )
            events.append("response_constructed")
            response_constructor_calls.append({"args": args, "kwargs": kwargs})
            return real_response_constructor(*args, **kwargs)

        release_task: asyncio.Task[None] | None = None
        try:
            release_task = asyncio.create_task(release_publication_task())

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
                side_effect=spy_response_constructor,
            ) as mock_response_constructor:
                response = await cancel_upload_session_endpoint(
                    session_id=caller_session_id,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            await release_task
            self.assertEqual(
                events,
                [
                    "lookup",
                    "cancel",
                    "publish_begin",
                    "publish_complete",
                    "response_constructed",
                ],
            )

            mock_get_upload_session.assert_awaited_once_with(
                fake_db,
                session_id=caller_session_id,
                current_user=fake_current_user,
            )
            self.assertEqual(mock_get_upload_session.call_count, 1)
            self.assertEqual(mock_get_upload_session.await_count, 1)

            mock_cancel_upload_session.assert_awaited_once_with(
                fake_db,
                session=looked_up_session,
            )
            self.assertEqual(mock_cancel_upload_session.call_count, 1)
            self.assertEqual(mock_cancel_upload_session.await_count, 1)

            mock_publish_event.assert_awaited_once_with(
                db=fake_db,
                current_user=fake_current_user,
                session=returned_session,
                event_name="cancelled",
            )
            self.assertEqual(mock_publish_event.call_count, 1)
            self.assertEqual(mock_publish_event.await_count, 1)

            mock_response_constructor.assert_called_once_with(
                session_id="session-returned-id-nonempty-001",
                status=UploadSessionStatus.CANCELLED,
                final_chat_file_id="chat-file-synthetic-nonempty-001",
            )
            self.assertEqual(mock_response_constructor.call_count, 1)
            self.assertEqual(len(response_constructor_calls), 1)

            self.assertIs(type(response), UploadSessionStatusChangeResponse)
            self.assertIsInstance(response, UploadSessionStatusChangeResponse)
            self.assertEqual(
                response.session_id, "session-returned-id-nonempty-001"
            )
            self.assertIs(response.status, UploadSessionStatus.CANCELLED)
            self.assertEqual(response.status, UploadSessionStatus.CANCELLED)
            self.assertEqual(
                response.final_chat_file_id,
                "chat-file-synthetic-nonempty-001",
            )

            expected_dump = {
                "session_id": "session-returned-id-nonempty-001",
                "status": "cancelled",
                "final_chat_file_id": "chat-file-synthetic-nonempty-001",
            }
            self.assertEqual(response.model_dump(mode="json"), expected_dump)
        finally:
            if release_task is not None and not release_task.done():
                release_task.cancel()
                try:
                    await release_task
                except asyncio.CancelledError:
                    pass

    async def test_cancel_upload_session_success_with_none_final_chat_file(
        self,
    ) -> None:
        caller_session_id = "session-caller-req-none-file-002"
        fake_db = object()
        fake_current_user = object()

        looked_up_session = SimpleNamespace(
            id="session-lookup-id-none-file-002",
            status=UploadSessionStatus.READY,
            final_chat_file_id="chat-file-lookup-initial-002",
        )
        # Synthetic response-mapping contract probe: status is UploadSessionStatus.EXPIRED
        # to verify faithful status forwarding to the response model constructor.
        # This is a synthetic probe, not a claim that real cancellation returns EXPIRED.
        returned_session = SimpleNamespace(
            id="session-returned-id-none-file-002",
            status=UploadSessionStatus.EXPIRED,
            final_chat_file_id=None,
        )

        self.assertNotEqual(caller_session_id, looked_up_session.id)
        self.assertNotEqual(caller_session_id, returned_session.id)
        self.assertNotEqual(looked_up_session.id, returned_session.id)
        self.assertNotEqual(looked_up_session.status, returned_session.status)
        self.assertNotEqual(
            looked_up_session.final_chat_file_id,
            returned_session.final_chat_file_id,
        )
        self.assertIsNot(looked_up_session, returned_session)

        events: list[str] = []
        publication_started = asyncio.Event()
        allow_publication_to_finish = asyncio.Event()
        publication_finished = asyncio.Event()

        async def release_publication_task() -> None:
            await asyncio.wait_for(publication_started.wait(), timeout=2.0)
            await asyncio.sleep(0)
            allow_publication_to_finish.set()

        async def fake_get_upload_session(
            db: object,
            *,
            session_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(session_id, caller_session_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            return looked_up_session

        async def fake_cancel_upload_session(
            db: object,
            *,
            session: object,
            **extra_kwargs: Any,
        ) -> object:
            self.assertEqual(events, ["lookup"])
            self.assertIs(db, fake_db)
            self.assertIs(session, looked_up_session)
            self.assertEqual(
                extra_kwargs,
                {},
                "cancel_upload_session must be called with no reason override",
            )
            events.append("cancel")
            return returned_session

        async def fake_publish_event(
            *pos_args: Any,
            db: object,
            current_user: object,
            session: object,
            event_name: str,
            **extra_kwargs: Any,
        ) -> None:
            self.assertEqual(
                pos_args,
                (),
                "publication collaborator called with unexpected positional args",
            )
            self.assertEqual(events, ["lookup", "cancel"])
            self.assertIs(db, fake_db)
            self.assertIs(current_user, fake_current_user)
            self.assertIs(
                session,
                returned_session,
                "publication must receive cancellation-returned session by identity",
            )
            self.assertEqual(event_name, "cancelled")
            self.assertEqual(
                extra_kwargs,
                {},
                "publication collaborator must receive no batch_status override",
            )
            events.append("publish_begin")
            publication_started.set()
            await asyncio.wait_for(
                allow_publication_to_finish.wait(), timeout=2.0
            )
            events.append("publish_complete")
            publication_finished.set()

        real_response_constructor = UploadSessionStatusChangeResponse
        response_constructor_calls: list[dict[str, Any]] = []

        def spy_response_constructor(
            *args: Any,
            **kwargs: Any,
        ) -> UploadSessionStatusChangeResponse:
            self.assertTrue(
                publication_finished.is_set(),
                "Publication must complete before response model constructor is invoked",
            )
            self.assertIn(
                "publish_complete",
                events,
                "publish_complete must precede response model constructor invocation",
            )
            self.assertEqual(
                events,
                ["lookup", "cancel", "publish_begin", "publish_complete"],
                "Ordered progression must be lookup -> cancel -> publish_begin -> publish_complete before response construction",
            )
            events.append("response_constructed")
            response_constructor_calls.append({"args": args, "kwargs": kwargs})
            return real_response_constructor(*args, **kwargs)

        release_task: asyncio.Task[None] | None = None
        try:
            release_task = asyncio.create_task(release_publication_task())

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
                side_effect=spy_response_constructor,
            ) as mock_response_constructor:
                response = await cancel_upload_session_endpoint(
                    session_id=caller_session_id,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            await release_task
            self.assertEqual(
                events,
                [
                    "lookup",
                    "cancel",
                    "publish_begin",
                    "publish_complete",
                    "response_constructed",
                ],
            )

            mock_get_upload_session.assert_awaited_once_with(
                fake_db,
                session_id=caller_session_id,
                current_user=fake_current_user,
            )
            self.assertEqual(mock_get_upload_session.call_count, 1)
            self.assertEqual(mock_get_upload_session.await_count, 1)

            mock_cancel_upload_session.assert_awaited_once_with(
                fake_db,
                session=looked_up_session,
            )
            self.assertEqual(mock_cancel_upload_session.call_count, 1)
            self.assertEqual(mock_cancel_upload_session.await_count, 1)

            mock_publish_event.assert_awaited_once_with(
                db=fake_db,
                current_user=fake_current_user,
                session=returned_session,
                event_name="cancelled",
            )
            self.assertEqual(mock_publish_event.call_count, 1)
            self.assertEqual(mock_publish_event.await_count, 1)

            mock_response_constructor.assert_called_once_with(
                session_id="session-returned-id-none-file-002",
                status=UploadSessionStatus.EXPIRED,
                final_chat_file_id=None,
            )
            self.assertEqual(mock_response_constructor.call_count, 1)
            self.assertEqual(len(response_constructor_calls), 1)

            self.assertIs(type(response), UploadSessionStatusChangeResponse)
            self.assertIsInstance(response, UploadSessionStatusChangeResponse)
            self.assertEqual(
                response.session_id, "session-returned-id-none-file-002"
            )
            self.assertIs(response.status, UploadSessionStatus.EXPIRED)
            self.assertEqual(response.status, UploadSessionStatus.EXPIRED)
            self.assertIsNone(response.final_chat_file_id)

            expected_dump = {
                "session_id": "session-returned-id-none-file-002",
                "status": "expired",
                "final_chat_file_id": None,
            }
            self.assertEqual(response.model_dump(mode="json"), expected_dump)
        finally:
            if release_task is not None and not release_task.done():
                release_task.cancel()
                try:
                    await release_task
                except asyncio.CancelledError:
                    pass


if __name__ == "__main__":
    unittest.main()
