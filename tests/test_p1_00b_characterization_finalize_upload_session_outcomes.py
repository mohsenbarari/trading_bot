"""Handler outcome characterization tests for chat upload session finalization.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-MSG-POST-CHAT-FINALIZE-UPLOAD-SESSION-ENDPOINT-L2636
Tracked Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Evidence Limits:
This unit test module characterizes direct handler outcome mapping, sequencing, and failure
handling for `finalize_upload_session_endpoint` in isolation. All service dependencies
(`get_upload_session_for_current_user`, `finalize_upload_session`) and runtime publisher
(`_publish_upload_session_runtime_event`) at `api.routers.chat` import sites are completely
mocked. No database, Redis, network, or filesystem IO is performed.

This unit provides handler seam evidence only; it explicitly excludes durability,
concurrency, process reload, event delivery, and multi-server semantics. This module does
not claim to close tracked gap P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE or complete Stage P1-00b.
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


class FinalizeUploadSessionOutcomesCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_success_outcome_mapping_and_sequencing(self):
        """Assert success outcome mapping and execution sequencing for finalize_upload_session_endpoint.

        Note: Differing session IDs across requested ID, looked-up session ID, and finalized
        session ID are a deliberate handler seam probe to ensure no stale lookup/request reuse,
        not persisted service behavior.
        """
        sentinel_db = object()
        sentinel_user = object()

        requested_session_id = "req-session-id-111"
        looked_up_id = "lookup-session-id-222"
        finalized_id = "finalized-session-id-333"
        finalized_file_id = "chat-file-uuid-777"

        # Explicit check that requested, looked-up, and finalized IDs are all distinct
        assert requested_session_id != looked_up_id
        assert requested_session_id != finalized_id
        assert looked_up_id != finalized_id

        # Looked-up session before finalization with contrasting status and null final file ID
        looked_up_session = _make_simple_session(
            id=looked_up_id,
            status=UploadSessionStatus.UPLOADING,
            final_chat_file_id=None,
        )

        # Separate finalized session returned by finalization with READY status and non-null file ID
        finalized_session = _make_simple_session(
            id=finalized_id,
            status=UploadSessionStatus.READY,
            final_chat_file_id=finalized_file_id,
        )

        assert looked_up_session.status != finalized_session.status
        assert looked_up_session.final_chat_file_id != finalized_session.final_chat_file_id
        assert finalized_session.final_chat_file_id is not None

        phase_log: list[str] = []

        async def lookup_side_effect(db, *, session_id, current_user):
            assert phase_log == []
            phase_log.append("lookup")
            assert mock_finalize.call_count == 0
            assert mock_publish.call_count == 0
            assert mock_response.call_count == 0
            return looked_up_session

        async def finalize_side_effect(db, *, session, current_user):
            assert phase_log == ["lookup"]
            phase_log.append("finalize")
            assert mock_lookup.call_count == 1
            assert mock_publish.call_count == 0
            assert mock_response.call_count == 0
            return finalized_session

        async def publish_side_effect(*, db, current_user, session, event_name, batch_status=None):
            assert phase_log == ["lookup", "finalize"]
            phase_log.append("publish")
            assert mock_lookup.call_count == 1
            assert mock_finalize.call_count == 1
            assert mock_response.call_count == 0
            return None

        def response_constructor_spy(*args, **kwargs):
            # Proves response is constructed only after publication returns
            assert phase_log == ["lookup", "finalize", "publish"]
            phase_log.append("response_construction")
            assert mock_lookup.call_count == 1
            assert mock_finalize.call_count == 1
            assert mock_publish.call_count == 1
            return UploadSessionStatusChangeResponse(*args, **kwargs)

        mock_lookup = AsyncMock(side_effect=lookup_side_effect)
        mock_finalize = AsyncMock(side_effect=finalize_side_effect)
        mock_publish = AsyncMock(side_effect=publish_side_effect)
        mock_response = MagicMock(wraps=UploadSessionStatusChangeResponse, side_effect=response_constructor_spy)

        tracker = MagicMock()
        tracker.attach_mock(mock_lookup, "lookup")
        tracker.attach_mock(mock_finalize, "finalize")
        tracker.attach_mock(mock_publish, "publish")
        tracker.attach_mock(mock_response, "response_cls")

        with patch.object(chat_router_module, "get_upload_session_for_current_user", mock_lookup), \
             patch.object(chat_router_module, "finalize_upload_session", mock_finalize), \
             patch.object(chat_router_module, "_publish_upload_session_runtime_event", mock_publish), \
             patch.object(chat_router_module, "UploadSessionStatusChangeResponse", mock_response):

            response = await finalize_upload_session_endpoint(
                session_id=requested_session_id,
                current_user=sentinel_user,
                db=sentinel_db,
            )

        # 1. Assert actual response model type and independent literal values
        assert isinstance(response, UploadSessionStatusChangeResponse)
        assert type(response) is UploadSessionStatusChangeResponse
        assert response.session_id == "finalized-session-id-333"
        assert response.status == UploadSessionStatus.READY
        assert response.final_chat_file_id == "chat-file-uuid-777"

        # 2. Assert exact model_dump() fields and values
        assert response.model_dump() == {
            "session_id": "finalized-session-id-333",
            "status": UploadSessionStatus.READY,
            "final_chat_file_id": "chat-file-uuid-777",
        }

        # 3. Assert execution sequencing and once-only call/await counts
        assert phase_log == ["lookup", "finalize", "publish", "response_construction"]
        assert mock_lookup.call_count == 1
        assert mock_lookup.await_count == 1
        assert mock_finalize.call_count == 1
        assert mock_finalize.await_count == 1
        assert mock_publish.call_count == 1
        assert mock_publish.await_count == 1
        assert mock_response.call_count == 1

        # 4. Assert exact arguments and object identity across each stage
        lookup_args, lookup_kwargs = mock_lookup.await_args
        assert lookup_args == (sentinel_db,)
        assert lookup_args[0] is sentinel_db
        assert lookup_kwargs["session_id"] == requested_session_id
        assert lookup_kwargs["current_user"] is sentinel_user

        finalize_args, finalize_kwargs = mock_finalize.await_args
        assert finalize_args == (sentinel_db,)
        assert finalize_args[0] is sentinel_db
        assert finalize_kwargs["session"] is looked_up_session
        assert finalize_kwargs["current_user"] is sentinel_user

        pub_args, pub_kwargs = mock_publish.await_args
        assert pub_args == ()
        assert pub_kwargs["db"] is sentinel_db
        assert pub_kwargs["current_user"] is sentinel_user
        assert pub_kwargs["session"] is finalized_session
        assert pub_kwargs["session"] is not looked_up_session
        assert pub_kwargs["event_name"] == "ready"

        resp_args, resp_kwargs = mock_response.call_args
        assert resp_args == ()
        assert resp_kwargs["session_id"] == finalized_id
        assert resp_kwargs["status"] == UploadSessionStatus.READY
        assert resp_kwargs["final_chat_file_id"] == finalized_file_id

        assert tracker.mock_calls == [
            call.lookup(sentinel_db, session_id=requested_session_id, current_user=sentinel_user),
            call.finalize(sentinel_db, session=looked_up_session, current_user=sentinel_user),
            call.publish(
                db=sentinel_db,
                current_user=sentinel_user,
                session=finalized_session,
                event_name="ready",
            ),
            call.response_cls(
                session_id=finalized_id,
                status=UploadSessionStatus.READY,
                final_chat_file_id=finalized_file_id,
            ),
        ]

        # 5. Assert no fallback failed event or extra retries
        assert all(c.kwargs.get("event_name") != "failed" for c in mock_publish.await_args_list)
        assert all(c.kwargs.get("event_name") != "failed" for c in mock_publish.call_args_list)

    async def test_finalize_failure_outcome_mutation_and_propagation(self):
        """Assert failure outcome mutation, failed-event publication, and exception re-raising."""
        sentinel_db = object()
        sentinel_user = object()
        session_id = "session-failure-seam-test"

        # Start with an UPLOADING looked-up session
        looked_up_session = _make_simple_session(
            id=session_id,
            status=UploadSessionStatus.UPLOADING,
            final_chat_file_id=None,
            last_error=None,
        )
        assert looked_up_session.status == UploadSessionStatus.UPLOADING
        assert looked_up_session.last_error is None

        synthetic_headers = {"X-Characterization-Failure": "quota-exceeded-400"}
        synthetic_last_error = "storage-quota-exhausted"
        finalizer_exc = HTTPException(
            status_code=400,
            detail="Upload finalization failed due to quota exhaustion",
            headers=synthetic_headers,
        )

        phase_log: list[str] = []
        observed_publication_payloads: list[dict[str, object]] = []

        async def lookup_side_effect(db, *, session_id, current_user):
            assert phase_log == []
            phase_log.append("lookup")
            assert mock_finalize.call_count == 0
            assert mock_publish.call_count == 0
            assert mock_response.call_count == 0
            return looked_up_session

        async def finalize_mutation_side_effect(db, *, session, current_user):
            assert phase_log == ["lookup"]
            phase_log.append("finalize")
            assert mock_lookup.call_count == 1
            assert mock_publish.call_count == 0
            assert mock_response.call_count == 0
            # Mutate that same looked-up session object to FAILED with synthetic last_error
            session.status = UploadSessionStatus.FAILED
            session.last_error = synthetic_last_error
            raise finalizer_exc

        async def publish_side_effect(*, db, current_user, session, event_name, batch_status=None):
            # Phase assertion: strictly after finalization
            assert phase_log == ["lookup", "finalize"]
            phase_log.append("publish")
            assert mock_lookup.call_count == 1
            assert mock_finalize.call_count == 1
            assert mock_response.call_count == 0
            # Publication side-effect assertions proving failure state is already visible at publication time
            assert session.status == UploadSessionStatus.FAILED
            assert session.last_error == synthetic_last_error
            assert event_name == "failed"
            assert db is sentinel_db
            assert current_user is sentinel_user
            observed_publication_payloads.append({
                "status": session.status,
                "last_error": session.last_error,
                "event_name": event_name,
                "session_id": session.id,
                "session_ref": session,
            })
            return None

        mock_lookup = AsyncMock(side_effect=lookup_side_effect)
        mock_finalize = AsyncMock(side_effect=finalize_mutation_side_effect)
        mock_publish = AsyncMock(side_effect=publish_side_effect)
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

        # 1. Assert the handler re-raises the identical HTTPException preserving status, detail, and headers
        assert exc_info.value is finalizer_exc
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Upload finalization failed due to quota exhaustion"
        assert exc_info.value.headers == synthetic_headers

        # 2. Assert no response construction
        mock_response.assert_not_called()
        assert mock_response.call_count == 0

        # 3. Assert no ready event and no retry
        assert all(c.kwargs.get("event_name") != "ready" for c in mock_publish.await_args_list)
        assert all(c.kwargs.get("event_name") != "ready" for c in mock_publish.call_args_list)

        # 4. Assert once-only lookup, finalization, and publication calls and awaits
        assert mock_lookup.call_count == 1
        assert mock_lookup.await_count == 1
        assert mock_finalize.call_count == 1
        assert mock_finalize.await_count == 1
        assert mock_publish.call_count == 1
        assert mock_publish.await_count == 1

        # 5. Assert exact arguments and object identity: same mutated session passed to publish
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

        pub_args, pub_kwargs = mock_publish.await_args
        assert pub_args == ()
        assert pub_kwargs["db"] is sentinel_db
        assert pub_kwargs["current_user"] is sentinel_user
        assert pub_kwargs["session"] is looked_up_session
        assert pub_kwargs["session"].status == UploadSessionStatus.FAILED
        assert pub_kwargs["session"].last_error == synthetic_last_error
        assert pub_kwargs["event_name"] == "failed"

        assert phase_log == ["lookup", "finalize", "publish"]

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

        # 6. Assert publication side-effect captured the failure state in flight
        assert len(observed_publication_payloads) == 1
        assert observed_publication_payloads[0]["status"] == UploadSessionStatus.FAILED
        assert observed_publication_payloads[0]["last_error"] == synthetic_last_error
        assert observed_publication_payloads[0]["event_name"] == "failed"
        assert observed_publication_payloads[0]["session_ref"] is looked_up_session


if __name__ == "__main__":
    unittest.main()
