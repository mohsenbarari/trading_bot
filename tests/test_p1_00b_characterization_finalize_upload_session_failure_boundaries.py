"""Handler-boundary characterization tests for chat upload session finalization failures.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-MSG-POST-CHAT-FINALIZE-UPLOAD-SESSION-ENDPOINT-L2636
Tracked Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Evidence Limits:
This unit test module characterizes direct handler-boundary control flow and failure
propagation for `finalize_upload_session_endpoint` in isolation. All service dependencies
(`get_upload_session_for_current_user`, `finalize_upload_session`) and runtime publisher
(`_publish_upload_session_runtime_event`) at `api.routers.chat` import sites are completely
mocked. No database, Redis, network, or filesystem IO is performed.

This unit provides handler failure-boundary evidence only; it does not test durability,
concurrency, process reload, or multi-server semantics. This module does not claim to close
tracked gap P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE or complete Stage P1-00b.
The tracked HIGH inventory blocker remains BLOCKED_EXPLICIT, P1-00 remains COMPLETE,
and P1-00b remains IN_PROGRESS.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch
from fastapi import HTTPException
import pytest

import api.routers.chat as chat_router_module
from api.routers.chat import finalize_upload_session_endpoint
from api.routers.chat_schemas import UploadSessionStatusChangeResponse
from models.upload_session import UploadRoomKind, UploadSessionStatus


def _make_simple_session(**kwargs: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "id": "session-test-id",
        "batch_id": None,
        "owner_user_id": 101,
        "actor_user_id": 101,
        "room_kind": UploadRoomKind.DIRECT,
        "target_id": 202,
        "media_type": "image",
        "original_file_name": "test_upload.jpg",
        "mime_type": "image/jpeg",
        "total_bytes": 1024,
        "chunk_size": 512,
        "received_bytes": 1024,
        "next_offset": 1024,
        "chunk_count": 2,
        "sha256_full": None,
        "sha256_chunks": None,
        "status": UploadSessionStatus.UPLOADING,
        "temp_storage_path": "uploads/chat_sessions/101/session-test-id.part",
        "final_chat_file_id": None,
        "preview_metadata": {},
        "resume_token": "token-test-123",
        "retry_count": 0,
        "last_error": None,
        "created_at": None,
        "updated_at": None,
        "expires_at": None,
        "last_activity_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class FinalizeUploadSessionFailureBoundariesCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_incomplete_session_409_propagates_without_publication(self):
        """Assert 409 HTTPException on incomplete session propagates without publication or response creation."""
        sentinel_db = object()
        sentinel_user = object()
        session_id = "session-inc-409"

        looked_up_session = _make_simple_session(
            id=session_id,
            status=UploadSessionStatus.UPLOADING,
            total_bytes=1024,
            chunk_size=512,
            received_bytes=512,
            next_offset=512,
            chunk_count=1,
        )

        # Direct independent assertion of the incomplete fixture
        assert looked_up_session.received_bytes > 0
        assert looked_up_session.received_bytes < looked_up_session.total_bytes
        assert looked_up_session.received_bytes != looked_up_session.total_bytes
        assert looked_up_session.next_offset == looked_up_session.received_bytes
        assert looked_up_session.chunk_count == 1

        synthetic_headers = {"X-Characterization-Trace": "incomplete-session-409"}
        incomplete_exc = HTTPException(
            status_code=409,
            detail="Upload session is incomplete",
            headers=synthetic_headers,
        )

        mock_lookup = AsyncMock(return_value=looked_up_session)
        mock_finalize = AsyncMock(side_effect=incomplete_exc)
        mock_publish = AsyncMock()
        mock_response = MagicMock(wraps=UploadSessionStatusChangeResponse)

        tracker = MagicMock()
        tracker.attach_mock(mock_lookup, "lookup")
        tracker.attach_mock(mock_finalize, "finalize")
        tracker.attach_mock(mock_publish, "publish")
        tracker.attach_mock(mock_response, "response_cls")

        with patch.object(chat_router_module, "get_upload_session_for_current_user", mock_lookup), \
             patch.object(chat_router_module, "finalize_upload_session", mock_finalize), \
             patch.object(chat_router_module, "_publish_upload_session_runtime_event", mock_publish), \
             patch.object(chat_router_module, "UploadSessionStatusChangeResponse", mock_response):

            with pytest.raises(HTTPException) as exc_info:
                await finalize_upload_session_endpoint(
                    session_id=session_id,
                    current_user=sentinel_user,
                    db=sentinel_db,
                )

        # 1. Assert exact exception instance propagates with unchanged status, detail, and synthetic headers
        assert exc_info.value is incomplete_exc
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Upload session is incomplete"
        assert exc_info.value.headers == synthetic_headers

        # 2. Assert session status remains UPLOADING and fixture remains incomplete
        assert looked_up_session.status == UploadSessionStatus.UPLOADING
        assert looked_up_session.received_bytes > 0
        assert looked_up_session.received_bytes < looked_up_session.total_bytes
        assert looked_up_session.received_bytes != looked_up_session.total_bytes
        assert looked_up_session.next_offset == looked_up_session.received_bytes
        assert looked_up_session.chunk_count == 1

        # 3. Assert lookup and finalize awaited once in order with exact sentinels and session identity
        assert mock_lookup.await_count == 1
        assert mock_lookup.call_count == 1
        assert mock_finalize.await_count == 1
        assert mock_finalize.call_count == 1

        lookup_args, lookup_kwargs = mock_lookup.await_args
        assert lookup_args == (sentinel_db,)
        assert lookup_args[0] is sentinel_db
        assert lookup_kwargs["session_id"] == session_id
        assert lookup_kwargs["current_user"] is sentinel_user

        finalize_args, finalize_kwargs = mock_finalize.await_args
        assert finalize_args == (sentinel_db,)
        assert finalize_args[0] is sentinel_db
        assert finalize_kwargs["session"] is looked_up_session
        assert finalize_kwargs["current_user"] is sentinel_user

        assert tracker.mock_calls == [
            call.lookup(sentinel_db, session_id=session_id, current_user=sentinel_user),
            call.finalize(sentinel_db, session=looked_up_session, current_user=sentinel_user),
        ]

        # 4. Assert publication was neither called nor awaited
        mock_publish.assert_not_called()
        mock_publish.assert_not_awaited()
        assert mock_publish.call_count == 0
        assert mock_publish.await_count == 0

        # 5. Assert response constructor was not called
        mock_response.assert_not_called()
        assert mock_response.call_count == 0

    async def test_finalize_ready_session_publication_failure_propagates_without_fallback(self):
        """Assert publication failure on READY session propagates without fallback to failed event or retry."""
        sentinel_db = object()
        sentinel_user = object()
        session_id = "session-ready-distinct"

        initial_session = _make_simple_session(
            id=session_id,
            status=UploadSessionStatus.UPLOADING,
            final_chat_file_id=None,
        )
        ready_session = _make_simple_session(
            id=session_id,
            status=UploadSessionStatus.READY,
            final_chat_file_id="chat-file-uuid-ready",
        )
        assert ready_session is not initial_session

        sentinel_pub_exc = RuntimeError("sentinel publication failure for ready event")

        mock_lookup = AsyncMock(return_value=initial_session)
        mock_finalize = AsyncMock(return_value=ready_session)
        mock_publish = AsyncMock(side_effect=sentinel_pub_exc)
        mock_response = MagicMock(wraps=UploadSessionStatusChangeResponse)

        tracker = MagicMock()
        tracker.attach_mock(mock_lookup, "lookup")
        tracker.attach_mock(mock_finalize, "finalize")
        tracker.attach_mock(mock_publish, "publish")
        tracker.attach_mock(mock_response, "response_cls")

        with patch.object(chat_router_module, "get_upload_session_for_current_user", mock_lookup), \
             patch.object(chat_router_module, "finalize_upload_session", mock_finalize), \
             patch.object(chat_router_module, "_publish_upload_session_runtime_event", mock_publish), \
             patch.object(chat_router_module, "UploadSessionStatusChangeResponse", mock_response):

            with pytest.raises(RuntimeError) as exc_info:
                await finalize_upload_session_endpoint(
                    session_id=session_id,
                    current_user=sentinel_user,
                    db=sentinel_db,
                )

        # 1. Assert exact publication exception identity propagates
        assert exc_info.value is sentinel_pub_exc

        # 2. Assert lookup, finalize, and publish awaited exactly once in order
        assert mock_lookup.await_count == 1
        assert mock_lookup.call_count == 1
        assert mock_finalize.await_count == 1
        assert mock_finalize.call_count == 1
        assert mock_publish.await_count == 1
        assert mock_publish.call_count == 1

        lookup_args, lookup_kwargs = mock_lookup.await_args
        assert lookup_args == (sentinel_db,)
        assert lookup_args[0] is sentinel_db
        assert lookup_kwargs["session_id"] == session_id
        assert lookup_kwargs["current_user"] is sentinel_user

        finalize_args, finalize_kwargs = mock_finalize.await_args
        assert finalize_args == (sentinel_db,)
        assert finalize_args[0] is sentinel_db
        assert finalize_kwargs["session"] is initial_session
        assert finalize_kwargs["current_user"] is sentinel_user

        assert tracker.mock_calls == [
            call.lookup(sentinel_db, session_id=session_id, current_user=sentinel_user),
            call.finalize(sentinel_db, session=initial_session, current_user=sentinel_user),
            call.publish(
                db=sentinel_db,
                current_user=sentinel_user,
                session=ready_session,
                event_name="ready",
            ),
        ]

        # 3. Assert publish receives exact returned READY session and event_name ready with identities
        pub_args, pub_kwargs = mock_publish.await_args
        assert pub_args == ()
        assert pub_kwargs["db"] is sentinel_db
        assert pub_kwargs["current_user"] is sentinel_user
        assert pub_kwargs["session"] is ready_session
        assert pub_kwargs["session"] is not initial_session
        assert pub_kwargs["session"].status == UploadSessionStatus.READY
        assert pub_kwargs["event_name"] == "ready"

        # 4. Assert no response construction
        mock_response.assert_not_called()
        assert mock_response.call_count == 0

        # 5. Assert no fallback failed event or retry
        assert all(c.kwargs.get("event_name") != "failed" for c in mock_publish.await_args_list)
        assert all(c.kwargs.get("event_name") != "failed" for c in mock_publish.call_args_list)

    async def test_finalize_failed_session_publication_failure_replaces_original_http_exception(self):
        """Assert failed-event publication failure replaces the original finalization HTTPException with __context__."""
        sentinel_db = object()
        sentinel_user = object()
        session_id = "session-failed-mutation"

        looked_up_session = _make_simple_session(
            id=session_id,
            status=UploadSessionStatus.UPLOADING,
            final_chat_file_id=None,
            last_error=None,
        )

        synthetic_headers = {"X-Finalize-Failure": "storage-io-error"}
        sentinel_finalize_http_exc = HTTPException(
            status_code=500,
            detail="Storage failure during session finalization",
            headers=synthetic_headers,
        )
        sentinel_failed_pub_exc = RuntimeError("sentinel publication failure for failed event")

        async def mutate_session_to_failed_and_raise(db, *, session, current_user):
            session.status = UploadSessionStatus.FAILED
            session.last_error = "storage-io-error"
            raise sentinel_finalize_http_exc

        mock_lookup = AsyncMock(return_value=looked_up_session)
        mock_finalize = AsyncMock(side_effect=mutate_session_to_failed_and_raise)
        mock_publish = AsyncMock(side_effect=sentinel_failed_pub_exc)
        mock_response = MagicMock(wraps=UploadSessionStatusChangeResponse)

        tracker = MagicMock()
        tracker.attach_mock(mock_lookup, "lookup")
        tracker.attach_mock(mock_finalize, "finalize")
        tracker.attach_mock(mock_publish, "publish")
        tracker.attach_mock(mock_response, "response_cls")

        with patch.object(chat_router_module, "get_upload_session_for_current_user", mock_lookup), \
             patch.object(chat_router_module, "finalize_upload_session", mock_finalize), \
             patch.object(chat_router_module, "_publish_upload_session_runtime_event", mock_publish), \
             patch.object(chat_router_module, "UploadSessionStatusChangeResponse", mock_response):

            with pytest.raises(RuntimeError) as exc_info:
                await finalize_upload_session_endpoint(
                    session_id=session_id,
                    current_user=sentinel_user,
                    db=sentinel_db,
                )

        # 1. Assert exact publication exception replaces the original finalization exception with __context__
        assert exc_info.value is sentinel_failed_pub_exc
        assert exc_info.value.__context__ is sentinel_finalize_http_exc
        assert isinstance(exc_info.value.__context__, HTTPException)
        assert exc_info.value.__context__.status_code == 500
        assert exc_info.value.__context__.detail == "Storage failure during session finalization"
        assert exc_info.value.__context__.headers == synthetic_headers

        # 2. Assert lookup, finalize, and failed publication order and exact identities
        assert mock_lookup.await_count == 1
        assert mock_lookup.call_count == 1
        assert mock_finalize.await_count == 1
        assert mock_finalize.call_count == 1
        assert mock_publish.await_count == 1
        assert mock_publish.call_count == 1

        lookup_args, lookup_kwargs = mock_lookup.await_args
        assert lookup_args == (sentinel_db,)
        assert lookup_args[0] is sentinel_db
        assert lookup_kwargs["session_id"] == session_id
        assert lookup_kwargs["current_user"] is sentinel_user

        finalize_args, finalize_kwargs = mock_finalize.await_args
        assert finalize_args == (sentinel_db,)
        assert finalize_args[0] is sentinel_db
        assert finalize_kwargs["session"] is looked_up_session
        assert finalize_kwargs["current_user"] is sentinel_user

        assert tracker.mock_calls == [
            call.lookup(sentinel_db, session_id=session_id, current_user=sentinel_user),
            call.finalize(sentinel_db, session=looked_up_session, current_user=sentinel_user),
            call.publish(
                db=sentinel_db,
                current_user=sentinel_user,
                session=looked_up_session,
                event_name="failed",
            ),
        ]

        # 3. Assert one failed publication using the mutated original session
        pub_args, pub_kwargs = mock_publish.await_args
        assert pub_args == ()
        assert pub_kwargs["db"] is sentinel_db
        assert pub_kwargs["current_user"] is sentinel_user
        assert pub_kwargs["session"] is looked_up_session
        assert pub_kwargs["session"].status == UploadSessionStatus.FAILED
        assert pub_kwargs["session"].last_error == "storage-io-error"
        assert pub_kwargs["event_name"] == "failed"

        # 4. Assert no ready publication and no retry
        assert all(c.kwargs.get("event_name") != "ready" for c in mock_publish.await_args_list)
        assert all(c.kwargs.get("event_name") != "ready" for c in mock_publish.call_args_list)

        # 5. Assert no response construction
        mock_response.assert_not_called()
        assert mock_response.call_count == 0


if __name__ == "__main__":
    unittest.main()
