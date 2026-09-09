"""Characterization tests for upload_chat_media close contract.

Seed: API-MSG-POST-CHAT-UPLOAD-CHAT-MEDIA-L2338
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001

Evidence scope:
Evidence is strictly limited to mocked handler cleanup/response/exception contract under close handling scenarios,
and does not evidence durable persistence, rollback, concurrency/reload, seed closure, or Stage acceptance.

Stage status tracking:
Preserves P1-00 COMPLETE, P1-00b IN_PROGRESS, and open High BLOCKED_EXPLICIT gap.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import upload_chat_media


class TestUploadChatMediaCloseContractCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_upload_chat_media_async_close_failure_after_successful_commit(self) -> None:
        call_order: list[str] = []
        close_error = RuntimeError("Synthetic async close failure boundary")
        synthetic_contents = b"synthetic-binary-media-data-req2"
        synthetic_filename = "sample_photo.png"
        synthetic_content_type = "image/png"
        synthetic_thumbnail = "synthetic-thumbnail-payload-req2"
        synthetic_user_id = 9101

        async def _read_side_effect() -> bytes:
            call_order.append("file.read")
            return synthetic_contents

        chat_file_record = SimpleNamespace(
            id="chat-file-uuid-req2",
            thumbnail=synthetic_thumbnail,
            file_name=synthetic_filename,
            mime_type=synthetic_content_type,
            size=len(synthetic_contents),
        )
        synthetic_result = SimpleNamespace(
            chat_file=chat_file_record,
            width=800,
            height=600,
        )

        async def _persist_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("persist_chat_media_file_bytes")
            return synthetic_result

        async def _commit_side_effect() -> None:
            call_order.append("db.commit")

        async def _close_side_effect() -> None:
            call_order.append("file.close")
            raise close_error

        mock_read = AsyncMock(side_effect=_read_side_effect)
        mock_close = AsyncMock(side_effect=_close_side_effect)
        synthetic_file = SimpleNamespace(
            filename=synthetic_filename,
            content_type=synthetic_content_type,
            read=mock_read,
            close=mock_close,
        )
        fake_db = SimpleNamespace(commit=AsyncMock(side_effect=_commit_side_effect))
        current_user = SimpleNamespace(id=synthetic_user_id)

        response_sentinel = object()
        response: Any = response_sentinel
        captured_exc: RuntimeError | None = None

        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new_callable=AsyncMock,
            side_effect=_persist_side_effect,
        ) as mock_persist:
            try:
                response = await upload_chat_media(
                    file=synthetic_file,
                    thumbnail=synthetic_thumbnail,
                    current_user=current_user,
                    db=fake_db,
                )
            except RuntimeError as exc:
                call_order.append("caller_caught")
                captured_exc = exc

            self.assertEqual(
                call_order,
                [
                    "file.read",
                    "persist_chat_media_file_bytes",
                    "db.commit",
                    "file.close",
                    "caller_caught",
                ],
            )
            self.assertIs(captured_exc, close_error)
            self.assertIsNone(captured_exc.__context__)
            self.assertIs(response, response_sentinel)

            mock_read.assert_awaited_once_with()
            mock_read.assert_called_once_with()
            self.assertEqual(mock_read.call_args.args, ())
            self.assertEqual(mock_read.call_args.kwargs, {})

            mock_persist.assert_awaited_once_with(
                fake_db,
                uploader_id=synthetic_user_id,
                file_name=synthetic_filename,
                declared_content_type=synthetic_content_type,
                contents=synthetic_contents,
                thumbnail=synthetic_thumbnail,
            )
            mock_persist.assert_called_once_with(
                fake_db,
                uploader_id=synthetic_user_id,
                file_name=synthetic_filename,
                declared_content_type=synthetic_content_type,
                contents=synthetic_contents,
                thumbnail=synthetic_thumbnail,
            )
            self.assertIs(mock_persist.call_args.args[0], fake_db)
            self.assertIs(mock_persist.await_args.args[0], fake_db)
            self.assertEqual(mock_persist.call_args.args, (fake_db,))
            self.assertEqual(
                mock_persist.call_args.kwargs,
                {
                    "uploader_id": synthetic_user_id,
                    "file_name": synthetic_filename,
                    "declared_content_type": synthetic_content_type,
                    "contents": synthetic_contents,
                    "thumbnail": synthetic_thumbnail,
                },
            )

            fake_db.commit.assert_awaited_once_with()
            fake_db.commit.assert_called_once_with()
            self.assertEqual(fake_db.commit.call_args.args, ())
            self.assertEqual(fake_db.commit.call_args.kwargs, {})

            mock_close.assert_awaited_once_with()
            mock_close.assert_called_once_with()
            self.assertEqual(mock_close.call_args.args, ())
            self.assertEqual(mock_close.call_args.kwargs, {})

    async def test_upload_chat_media_async_close_failure_after_persistence_failure(self) -> None:
        call_order: list[str] = []
        persist_error = RuntimeError("Synthetic persist failure boundary")
        close_error = RuntimeError("Synthetic async close failure boundary")
        synthetic_contents = b"synthetic-binary-media-data-req3"
        synthetic_filename = "document_record.pdf"
        synthetic_content_type = "application/pdf"
        synthetic_thumbnail = None
        synthetic_user_id = 9102

        async def _read_side_effect() -> bytes:
            call_order.append("file.read")
            return synthetic_contents

        async def _persist_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("persist_chat_media_file_bytes")
            raise persist_error

        async def _close_side_effect() -> None:
            call_order.append("file.close")
            raise close_error

        mock_read = AsyncMock(side_effect=_read_side_effect)
        mock_close = AsyncMock(side_effect=_close_side_effect)
        synthetic_file = SimpleNamespace(
            filename=synthetic_filename,
            content_type=synthetic_content_type,
            read=mock_read,
            close=mock_close,
        )
        fake_db = SimpleNamespace(commit=AsyncMock())
        current_user = SimpleNamespace(id=synthetic_user_id)

        response_sentinel = object()
        response: Any = response_sentinel
        captured_exc: RuntimeError | None = None

        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new_callable=AsyncMock,
            side_effect=_persist_side_effect,
        ) as mock_persist:
            try:
                response = await upload_chat_media(
                    file=synthetic_file,
                    thumbnail=synthetic_thumbnail,
                    current_user=current_user,
                    db=fake_db,
                )
            except RuntimeError as exc:
                call_order.append("caller_caught")
                captured_exc = exc

            self.assertEqual(
                call_order,
                [
                    "file.read",
                    "persist_chat_media_file_bytes",
                    "file.close",
                    "caller_caught",
                ],
            )
            self.assertIs(captured_exc, close_error)
            self.assertIs(captured_exc.__context__, persist_error)
            self.assertIsNone(captured_exc.__cause__)
            self.assertFalse(captured_exc.__suppress_context__)
            self.assertIs(response, response_sentinel)

            mock_read.assert_awaited_once_with()
            mock_read.assert_called_once_with()
            self.assertEqual(mock_read.call_args.args, ())
            self.assertEqual(mock_read.call_args.kwargs, {})

            mock_persist.assert_awaited_once_with(
                fake_db,
                uploader_id=synthetic_user_id,
                file_name=synthetic_filename,
                declared_content_type=synthetic_content_type,
                contents=synthetic_contents,
                thumbnail=synthetic_thumbnail,
            )
            mock_persist.assert_called_once_with(
                fake_db,
                uploader_id=synthetic_user_id,
                file_name=synthetic_filename,
                declared_content_type=synthetic_content_type,
                contents=synthetic_contents,
                thumbnail=synthetic_thumbnail,
            )
            self.assertIs(mock_persist.call_args.args[0], fake_db)
            self.assertIs(mock_persist.await_args.args[0], fake_db)
            self.assertEqual(mock_persist.call_args.args, (fake_db,))
            self.assertEqual(
                mock_persist.call_args.kwargs,
                {
                    "uploader_id": synthetic_user_id,
                    "file_name": synthetic_filename,
                    "declared_content_type": synthetic_content_type,
                    "contents": synthetic_contents,
                    "thumbnail": synthetic_thumbnail,
                },
            )

            fake_db.commit.assert_not_called()
            fake_db.commit.assert_not_awaited()

            mock_close.assert_awaited_once_with()
            mock_close.assert_called_once_with()
            self.assertEqual(mock_close.call_args.args, ())
            self.assertEqual(mock_close.call_args.kwargs, {})

    async def test_upload_chat_media_sync_close_non_awaitable_sentinel_success(self) -> None:
        call_order: list[str] = []
        close_sentinel = object()
        synthetic_contents = b"synthetic-binary-media-data-req4"
        synthetic_filename = "photo_scene.webp"
        synthetic_content_type = "image/webp"
        synthetic_thumbnail = "synthetic-thumbnail-payload-req4"
        synthetic_user_id = 9103
        expected_width = 1280
        expected_height = 720

        async def _read_side_effect() -> bytes:
            call_order.append("file.read")
            return synthetic_contents

        chat_file_record = SimpleNamespace(
            id="chat-file-uuid-req4",
            thumbnail=synthetic_thumbnail,
            file_name=synthetic_filename,
            mime_type=synthetic_content_type,
            size=len(synthetic_contents),
        )
        synthetic_result = SimpleNamespace(
            chat_file=chat_file_record,
            width=expected_width,
            height=expected_height,
        )

        async def _persist_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("persist_chat_media_file_bytes")
            return synthetic_result

        async def _commit_side_effect() -> None:
            call_order.append("db.commit")

        def _close_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("file.close")
            return close_sentinel

        mock_read = AsyncMock(side_effect=_read_side_effect)
        mock_close = Mock(side_effect=_close_side_effect)
        synthetic_file = SimpleNamespace(
            filename=synthetic_filename,
            content_type=synthetic_content_type,
            read=mock_read,
            close=mock_close,
        )
        fake_db = SimpleNamespace(commit=AsyncMock(side_effect=_commit_side_effect))
        current_user = SimpleNamespace(id=synthetic_user_id)

        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new_callable=AsyncMock,
            side_effect=_persist_side_effect,
        ) as mock_persist:
            response = await upload_chat_media(
                file=synthetic_file,
                thumbnail=synthetic_thumbnail,
                current_user=current_user,
                db=fake_db,
            )
            call_order.append("caller_received")

            self.assertEqual(
                call_order,
                [
                    "file.read",
                    "persist_chat_media_file_bytes",
                    "db.commit",
                    "file.close",
                    "caller_received",
                ],
            )

            expected_response = {
                "file_id": "chat-file-uuid-req4",
                "thumbnail": synthetic_thumbnail,
                "file_name": synthetic_filename,
                "mime_type": synthetic_content_type,
                "size": len(synthetic_contents),
                "width": expected_width,
                "height": expected_height,
            }
            self.assertEqual(response, expected_response)
            self.assertIsNot(response, close_sentinel)

            mock_read.assert_awaited_once_with()
            mock_read.assert_called_once_with()
            self.assertEqual(mock_read.call_args.args, ())
            self.assertEqual(mock_read.call_args.kwargs, {})

            mock_persist.assert_awaited_once_with(
                fake_db,
                uploader_id=synthetic_user_id,
                file_name=synthetic_filename,
                declared_content_type=synthetic_content_type,
                contents=synthetic_contents,
                thumbnail=synthetic_thumbnail,
            )
            mock_persist.assert_called_once_with(
                fake_db,
                uploader_id=synthetic_user_id,
                file_name=synthetic_filename,
                declared_content_type=synthetic_content_type,
                contents=synthetic_contents,
                thumbnail=synthetic_thumbnail,
            )
            self.assertIs(mock_persist.call_args.args[0], fake_db)
            self.assertIs(mock_persist.await_args.args[0], fake_db)
            self.assertEqual(mock_persist.call_args.args, (fake_db,))
            self.assertEqual(
                mock_persist.call_args.kwargs,
                {
                    "uploader_id": synthetic_user_id,
                    "file_name": synthetic_filename,
                    "declared_content_type": synthetic_content_type,
                    "contents": synthetic_contents,
                    "thumbnail": synthetic_thumbnail,
                },
            )

            fake_db.commit.assert_awaited_once_with()
            fake_db.commit.assert_called_once_with()
            self.assertEqual(fake_db.commit.call_args.args, ())
            self.assertEqual(fake_db.commit.call_args.kwargs, {})

            mock_close.assert_called_once_with()
            self.assertEqual(mock_close.call_args.args, ())
            self.assertEqual(mock_close.call_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
