"""Hermetic direct-handler characterization tests for api.routers.chat.post_upload_batch.

Stage: P1-00b (docs/refactor/two-server/section-1/stages/P1-00b.md)
Assignment: WA-P1-00B-FREEZE-20260904-001
Tracked Seed: API-MSG-POST-CHAT-POST-UPLOAD-BATCH-L2517
Tracked Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Evidence Limit:
Characterization evidence is strictly limited to direct-handler contract assertions for
api.routers.chat.post_upload_batch with the router-level collaborator create_upload_batch
mocked. This test module isolates endpoint argument forwarding and response mapping hermetically
without invoking service internals, persistence, Redis, networks, or operational data. It does
not evidence durability, concurrency, full seed closure, or Stage acceptance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import post_upload_batch
from api.routers.chat_schemas import (
    UploadBatchCreateRequest,
    UploadBatchCreateResponse,
)
from models.upload_session import (
    UploadBatchMessageKind,
    UploadBatchStatus,
    UploadCaptionPolicy,
    UploadRoomKind,
)


class P100bPostUploadBatchContractCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_post_upload_batch_group_album_explicit_caption_forwards_and_maps_response(
        self,
    ) -> None:
        db = object()
        current_user = object()
        request = UploadBatchCreateRequest(
            room_kind=UploadRoomKind.GROUP,
            target_id=9012,
            message_kind=UploadBatchMessageKind.ALBUM,
            expected_items=5,
            caption_policy=UploadCaptionPolicy.FIRST_ITEM_ONLY,
            idempotency_key="idem-group-album-9012-key",
        )
        synthetic_expiry = datetime(2026, 9, 10, 14, 30, 0, tzinfo=timezone.utc)
        synthetic_batch = SimpleNamespace(
            id="batch-group-album-9012-synthetic",
            status=UploadBatchStatus.UPLOADING,  # legitimate non-COLLECTING probe
            expires_at=synthetic_expiry,
        )

        with patch(
            "api.routers.chat.create_upload_batch", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = synthetic_batch

            response = await post_upload_batch(
                data=request,
                current_user=current_user,
                db=db,
            )

        self.assertEqual(mock_create.await_count, 1)
        self.assertEqual(mock_create.call_count, 1)
        args, kwargs = mock_create.call_args
        self.assertEqual(len(args), 1)
        self.assertIs(args[0], db)
        self.assertIs(kwargs["current_user"], current_user)
        self.assertEqual(
            set(kwargs.keys()),
            {
                "current_user",
                "room_kind",
                "target_id",
                "message_kind",
                "expected_items",
                "caption_policy",
                "idempotency_key",
            },
        )
        self.assertEqual(kwargs["room_kind"], UploadRoomKind.GROUP)
        self.assertEqual(kwargs["target_id"], 9012)
        self.assertEqual(kwargs["message_kind"], UploadBatchMessageKind.ALBUM)
        self.assertEqual(kwargs["expected_items"], 5)
        self.assertEqual(
            kwargs["caption_policy"], UploadCaptionPolicy.FIRST_ITEM_ONLY
        )
        self.assertEqual(
            kwargs["idempotency_key"], "idem-group-album-9012-key"
        )

        self.assertIsInstance(response, UploadBatchCreateResponse)
        self.assertEqual(
            response.model_dump(),
            {
                "batch_id": "batch-group-album-9012-synthetic",
                "status": UploadBatchStatus.UPLOADING,
                "expires_at": synthetic_expiry,
            },
        )
        self.assertEqual(response.batch_id, "batch-group-album-9012-synthetic")
        self.assertEqual(response.status, UploadBatchStatus.UPLOADING)
        self.assertEqual(response.expires_at, synthetic_expiry)

    async def test_post_upload_batch_direct_single_default_caption_forwards_and_maps_response(
        self,
    ) -> None:
        db = object()
        current_user = object()
        request = UploadBatchCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=4055,
            message_kind=UploadBatchMessageKind.SINGLE,
            expected_items=1,
            idempotency_key="idem-direct-single-4055-key",
        )
        synthetic_expiry = datetime(2026, 9, 11, 8, 15, 0, tzinfo=timezone.utc)
        synthetic_batch = SimpleNamespace(
            id="batch-direct-single-4055-synthetic",
            status=UploadBatchStatus.COLLECTING,
            expires_at=synthetic_expiry,
        )

        with patch(
            "api.routers.chat.create_upload_batch", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = synthetic_batch

            response = await post_upload_batch(
                data=request,
                current_user=current_user,
                db=db,
            )

        self.assertEqual(mock_create.await_count, 1)
        self.assertEqual(mock_create.call_count, 1)
        args, kwargs = mock_create.call_args
        self.assertEqual(len(args), 1)
        self.assertIs(args[0], db)
        self.assertIs(kwargs["current_user"], current_user)
        self.assertEqual(
            set(kwargs.keys()),
            {
                "current_user",
                "room_kind",
                "target_id",
                "message_kind",
                "expected_items",
                "caption_policy",
                "idempotency_key",
            },
        )
        self.assertEqual(kwargs["room_kind"], UploadRoomKind.DIRECT)
        self.assertEqual(kwargs["target_id"], 4055)
        self.assertEqual(kwargs["message_kind"], UploadBatchMessageKind.SINGLE)
        self.assertEqual(kwargs["expected_items"], 1)
        self.assertEqual(kwargs["caption_policy"], UploadCaptionPolicy.NONE)
        self.assertEqual(
            kwargs["idempotency_key"], "idem-direct-single-4055-key"
        )

        self.assertIsInstance(response, UploadBatchCreateResponse)
        self.assertEqual(
            response.model_dump(),
            {
                "batch_id": "batch-direct-single-4055-synthetic",
                "status": UploadBatchStatus.COLLECTING,
                "expires_at": synthetic_expiry,
            },
        )
        self.assertEqual(response.batch_id, "batch-direct-single-4055-synthetic")
        self.assertEqual(response.status, UploadBatchStatus.COLLECTING)
        self.assertEqual(response.expires_at, synthetic_expiry)

    async def test_post_upload_batch_service_runtime_error_escapes_without_response_construction(
        self,
    ) -> None:
        db = object()
        current_user = object()
        request = UploadBatchCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=7788,
            message_kind=UploadBatchMessageKind.SINGLE,
            expected_items=1,
            caption_policy=UploadCaptionPolicy.NONE,
            idempotency_key="idem-failure-test-7788-key",
        )
        test_error = RuntimeError("create_upload_batch failure simulation")

        with patch(
            "api.routers.chat.create_upload_batch", new_callable=AsyncMock
        ) as mock_create, patch(
            "api.routers.chat.UploadBatchCreateResponse"
        ) as mock_response_cls:
            mock_create.side_effect = test_error

            with self.assertRaises(RuntimeError) as ctx:
                await post_upload_batch(
                    data=request,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(ctx.exception, test_error)

        self.assertEqual(mock_create.await_count, 1)
        self.assertEqual(mock_create.call_count, 1)
        args, kwargs = mock_create.call_args
        self.assertEqual(len(args), 1)
        self.assertIs(args[0], db)
        self.assertIs(kwargs["current_user"], current_user)
        self.assertEqual(
            set(kwargs.keys()),
            {
                "current_user",
                "room_kind",
                "target_id",
                "message_kind",
                "expected_items",
                "caption_policy",
                "idempotency_key",
            },
        )
        self.assertEqual(kwargs["room_kind"], UploadRoomKind.DIRECT)
        self.assertEqual(kwargs["target_id"], 7788)
        self.assertEqual(kwargs["message_kind"], UploadBatchMessageKind.SINGLE)
        self.assertEqual(kwargs["expected_items"], 1)
        self.assertEqual(kwargs["caption_policy"], UploadCaptionPolicy.NONE)
        self.assertEqual(
            kwargs["idempotency_key"], "idem-failure-test-7788-key"
        )

        mock_response_cls.assert_not_called()
        self.assertEqual(mock_response_cls.call_count, 0)


if __name__ == "__main__":
    unittest.main()
