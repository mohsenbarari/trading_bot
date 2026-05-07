import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.responses import FileResponse

from api.routers.chat import get_chat_file, get_stickers, upload_chat_media


class FakeUploadFile:
    def __init__(self, *, content_type, filename, contents):
        self.content_type = content_type
        self.filename = filename
        self._contents = contents

    async def read(self):
        return self._contents


class FakeAsyncFile:
    def __init__(self):
        self.write = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDB:
    def __init__(self, *, get_map=None):
        self.get_map = dict(get_map or {})
        self.added = []
        self.commit = AsyncMock()

    def add(self, obj):
        self.added.append(obj)

    async def get(self, _model, primary_key):
        return self.get_map.get(primary_key)


class ChatRouterMediaEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_stickers_returns_expected_catalog(self):
        packs = await get_stickers()

        self.assertEqual(len(packs), 3)
        self.assertEqual(packs[0].id, "emotions")
        self.assertIn("happy", packs[0].stickers)

    async def test_get_chat_file_validates_token_and_file_existence(self):
        db = FakeDB()

        with self.assertRaises(HTTPException) as exc_info:
            await get_chat_file("file-1", db=db, token=None)
        self.assertEqual(exc_info.exception.status_code, 401)

        with patch("api.routers.chat.jwt.decode", side_effect=Exception("boom")):
            with self.assertRaises(Exception):
                await get_chat_file("file-1", db=db, token="bad")

    async def test_get_chat_file_handles_invalid_jwt_and_missing_resources(self):
        db = FakeDB()

        with patch("api.routers.chat.jwt.decode", side_effect=__import__("jose").JWTError()):
            with self.assertRaises(HTTPException) as exc_info:
                await get_chat_file("file-1", db=db, token="bad")
        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "Invalid token")

        with patch("api.routers.chat.jwt.decode", return_value={}) as decode_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await get_chat_file("file-1", db=db, token="good")
        decode_mock.assert_called_once()
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "File not found")

        chat_file = SimpleNamespace(s3_key="/tmp/missing.pdf", mime_type="application/pdf", file_name="x.pdf")
        db = FakeDB(get_map={"file-1": chat_file})
        with patch("api.routers.chat.jwt.decode", return_value={}), patch("api.routers.chat.os.path.exists", return_value=False):
            with self.assertRaises(HTTPException) as exc_info:
                await get_chat_file("file-1", db=db, token="good")
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "File not found on disk")

    async def test_get_chat_file_returns_file_response_on_success(self):
        chat_file = SimpleNamespace(s3_key="/tmp/file.pdf", mime_type="application/pdf", file_name="report.pdf")
        db = FakeDB(get_map={"file-1": chat_file})

        with patch("api.routers.chat.jwt.decode", return_value={}), patch("api.routers.chat.os.path.exists", return_value=True):
            response = await get_chat_file("file-1", db=db, token="good")

        self.assertIsInstance(response, FileResponse)
        self.assertEqual(response.path, "/tmp/file.pdf")
        self.assertEqual(response.media_type, "application/pdf")
        self.assertIn("report.pdf", response.headers.get("content-disposition", ""))

    async def test_upload_chat_media_rejects_unsupported_or_mismatched_content(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB()

        with self.assertRaises(HTTPException) as exc_info:
            await upload_chat_media(
                file=FakeUploadFile(content_type="application/x-msdownload", filename="evil.exe", contents=b"x"),
                current_user=current_user,
                db=db,
            )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("Unsupported file type", exc_info.exception.detail)

        with patch("api.routers.chat.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *args: fn(*args))), patch(
            "api.routers.chat.magic.from_buffer",
            return_value="application/x-msdownload",
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await upload_chat_media(
                    file=FakeUploadFile(content_type="application/pdf", filename="a.pdf", contents=b"pdf"),
                    current_user=current_user,
                    db=db,
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("Invalid file content", exc_info.exception.detail)

    async def test_upload_chat_media_rejects_oversized_files(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB()
        huge_contents = b"x" * (50 * 1024 * 1024 + 1)

        with patch("api.routers.chat.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *args: fn(*args))), patch(
            "api.routers.chat.magic.from_buffer",
            return_value="application/pdf",
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await upload_chat_media(
                    file=FakeUploadFile(content_type="application/pdf", filename="a.pdf", contents=huge_contents),
                    current_user=current_user,
                    db=db,
                )
        self.assertEqual(exc_info.exception.status_code, 413)
        self.assertIn("File too large", exc_info.exception.detail)

    async def test_upload_chat_media_persists_file_and_returns_metadata(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB()
        fake_file = FakeAsyncFile()

        with patch("api.routers.chat.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *args: fn(*args))), patch(
            "api.routers.chat.magic.from_buffer",
            return_value="application/pdf",
        ), patch("api.routers.chat.uuid.uuid4", return_value="uuid-1"), patch(
            "api.routers.chat.os.makedirs"
        ) as makedirs_mock, patch(
            "api.routers.chat.aiofiles.open",
            return_value=fake_file,
        ):
            result = await upload_chat_media(
                file=FakeUploadFile(content_type="application/pdf", filename="report.pdf", contents=b"pdf-bytes"),
                thumbnail="thumb",
                current_user=current_user,
                db=db,
            )

        makedirs_mock.assert_called_once()
        fake_file.write.assert_awaited_once_with(b"pdf-bytes")
        db.commit.assert_awaited_once()
        self.assertEqual(len(db.added), 1)
        chat_file = db.added[0]
        self.assertEqual(chat_file.id, "uuid-1")
        self.assertEqual(chat_file.file_name, "report.pdf")
        self.assertEqual(chat_file.mime_type, "application/pdf")
        self.assertEqual(chat_file.thumbnail, "thumb")
        self.assertEqual(result["file_id"], "uuid-1")
        self.assertEqual(result["mime_type"], "application/pdf")
        self.assertEqual(result["size"], len(b"pdf-bytes"))


if __name__ == "__main__":
    unittest.main()