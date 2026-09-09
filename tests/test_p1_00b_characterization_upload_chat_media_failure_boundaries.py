"""Characterization tests for upload_chat_media failure boundaries.

Seed: API-MSG-POST-CHAT-UPLOAD-CHAT-MEDIA-L2338
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001

Evidence scope:
Evidence is handler failure ordering/cleanup with mocked persistence and commit boundaries,
not durable rollback, real media persistence, concurrency/reload, seed closure or Stage acceptance.

Stage status tracking:
Preserves P1-00 COMPLETE, P1-00b IN_PROGRESS, and BLOCKED_EXPLICIT.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import upload_chat_media


class ObservablePersistenceResultBoundary:
    """Observable boundary for persist_chat_media_file_bytes result.

    Fails immediately if response metadata attributes are accessed when
    db.commit fails, verifying that response assembly is not executed
    prior to commit completion.
    """

    def __init__(self) -> None:
        self.accessed_attributes: list[str] = []

    def _trap_access(self, attribute_name: str) -> Any:
        self.accessed_attributes.append(attribute_name)
        raise AssertionError(
            f"Response metadata '{attribute_name}' was accessed on persistence result "
            "despite commit failure; response assembly must not precede successful commit."
        )

    @property
    def chat_file(self) -> Any:
        return self._trap_access("chat_file")

    @property
    def width(self) -> Any:
        return self._trap_access("width")

    @property
    def height(self) -> Any:
        return self._trap_access("height")

    def __getattr__(self, name: str) -> Any:
        return self._trap_access(name)


class TestUploadChatMediaFailureBoundariesCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_upload_chat_media_file_read_failure_boundary(self) -> None:
        call_order: list[str] = []
        read_error = RuntimeError("Synthetic file.read failure boundary")

        async def _read_side_effect() -> bytes:
            call_order.append("file.read")
            raise read_error

        async def _close_side_effect() -> None:
            call_order.append("file.close")

        mock_read = AsyncMock(side_effect=_read_side_effect)
        mock_close = AsyncMock(side_effect=_close_side_effect)
        synthetic_file = SimpleNamespace(
            filename="test_upload_file.png",
            content_type="image/png",
            read=mock_read,
            close=mock_close,
        )
        fake_db = SimpleNamespace(commit=AsyncMock())
        current_user = SimpleNamespace(id=9001)
        thumbnail = "synthetic-thumbnail-data"

        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new_callable=AsyncMock,
        ) as mock_persist:
            captured_exc: RuntimeError | None = None
            try:
                await upload_chat_media(
                    file=synthetic_file,
                    thumbnail=thumbnail,
                    current_user=current_user,
                    db=fake_db,
                )
            except RuntimeError as exc:
                call_order.append("caller_caught")
                captured_exc = exc

            self.assertIs(captured_exc, read_error)
            self.assertEqual(
                call_order,
                ["file.read", "file.close", "caller_caught"],
            )

            mock_read.assert_awaited_once_with()
            mock_read.assert_called_once_with()
            self.assertEqual(mock_read.call_args.args, ())
            self.assertEqual(mock_read.call_args.kwargs, {})

            mock_persist.assert_not_called()
            mock_persist.assert_not_awaited()

            fake_db.commit.assert_not_called()
            fake_db.commit.assert_not_awaited()

            mock_close.assert_awaited_once_with()
            mock_close.assert_called_once_with()
            self.assertEqual(mock_close.call_args.args, ())
            self.assertEqual(mock_close.call_args.kwargs, {})

    async def test_upload_chat_media_persistence_failure_boundary(self) -> None:
        call_order: list[str] = []
        persist_error = RuntimeError("Synthetic persist_chat_media_file_bytes failure boundary")
        synthetic_contents = b"synthetic-chat-media-binary-content-42"
        synthetic_filename = "custom_chat_document.pdf"
        synthetic_content_type = "application/pdf"
        synthetic_thumbnail = "synthetic-thumbnail-payload-base64"
        synthetic_user_id = 9002

        async def _read_side_effect() -> bytes:
            call_order.append("file.read")
            return synthetic_contents

        async def _persist_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("persist_chat_media_file_bytes")
            raise persist_error

        async def _close_side_effect() -> None:
            call_order.append("file.close")

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

        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new_callable=AsyncMock,
            side_effect=_persist_side_effect,
        ) as mock_persist:
            captured_exc: RuntimeError | None = None
            try:
                await upload_chat_media(
                    file=synthetic_file,
                    thumbnail=synthetic_thumbnail,
                    current_user=current_user,
                    db=fake_db,
                )
            except RuntimeError as exc:
                call_order.append("caller_caught")
                captured_exc = exc

            self.assertIs(captured_exc, persist_error)
            self.assertEqual(
                call_order,
                [
                    "file.read",
                    "persist_chat_media_file_bytes",
                    "file.close",
                    "caller_caught",
                ],
            )

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

    async def test_upload_chat_media_commit_failure_boundary(self) -> None:
        call_order: list[str] = []
        commit_error = RuntimeError("Synthetic db.commit failure boundary")
        synthetic_contents = b"synthetic-commit-failure-media-bytes"
        synthetic_filename = "recorded_voice.ogg"
        synthetic_content_type = "audio/ogg"
        synthetic_thumbnail = "synthetic-audio-cover-thumbnail"
        synthetic_user_id = 9003

        observable_result = ObservablePersistenceResultBoundary()

        async def _read_side_effect() -> bytes:
            call_order.append("file.read")
            return synthetic_contents

        async def _persist_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("persist_chat_media_file_bytes")
            return observable_result

        async def _commit_side_effect() -> None:
            call_order.append("db.commit")
            raise commit_error

        async def _close_side_effect() -> None:
            call_order.append("file.close")

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

        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new_callable=AsyncMock,
            side_effect=_persist_side_effect,
        ) as mock_persist:
            captured_exc: RuntimeError | None = None
            try:
                await upload_chat_media(
                    file=synthetic_file,
                    thumbnail=synthetic_thumbnail,
                    current_user=current_user,
                    db=fake_db,
                )
            except RuntimeError as exc:
                call_order.append("caller_caught")
                captured_exc = exc

            self.assertIs(captured_exc, commit_error)
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

            self.assertEqual(observable_result.accessed_attributes, [])

            mock_close.assert_awaited_once_with()
            mock_close.assert_called_once_with()
            self.assertEqual(mock_close.call_args.args, ())
            self.assertEqual(mock_close.call_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
