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
        self.close = AsyncMock()

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
        self.flush = AsyncMock()

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

    async def test_upload_chat_media_surfaces_helper_validation_errors(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB()

        upload_file = FakeUploadFile(content_type="application/x-msdownload", filename="evil.exe", contents=b"x")
        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new=AsyncMock(side_effect=HTTPException(status_code=400, detail="Unsupported file type: application/x-msdownload")),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await upload_chat_media(
                    file=upload_file,
                    current_user=current_user,
                    db=db,
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("Unsupported file type", exc_info.exception.detail)
        upload_file.close.assert_awaited_once()

        upload_file = FakeUploadFile(content_type="application/pdf", filename="a.pdf", contents=b"pdf")
        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new=AsyncMock(side_effect=HTTPException(status_code=400, detail="Invalid file content. Real type is application/x-msdownload and base type is application/pdf")),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await upload_chat_media(
                    file=upload_file,
                    current_user=current_user,
                    db=db,
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("Invalid file content", exc_info.exception.detail)
        upload_file.close.assert_awaited_once()

    async def test_upload_chat_media_rejects_oversized_files(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB()
        huge_contents = b"x" * (50 * 1024 * 1024 + 1)

        upload_file = FakeUploadFile(content_type="application/pdf", filename="a.pdf", contents=huge_contents)
        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new=AsyncMock(side_effect=HTTPException(status_code=413, detail="File too large (max 50MB)")),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await upload_chat_media(
                    file=upload_file,
                    current_user=current_user,
                    db=db,
                )
        self.assertEqual(exc_info.exception.status_code, 413)
        self.assertIn("File too large", exc_info.exception.detail)
        upload_file.close.assert_awaited_once()

    async def test_upload_chat_media_persists_file_and_returns_metadata(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB()
        chat_file = SimpleNamespace(
            id="uuid-1",
            thumbnail="thumb",
            file_name="report.pdf",
            mime_type="application/pdf",
            size=len(b"pdf-bytes"),
        )

        with patch(
            "api.routers.chat.persist_chat_media_file_bytes",
            new=AsyncMock(return_value=SimpleNamespace(chat_file=chat_file, width=640, height=480)),
        ):
            upload_file = FakeUploadFile(content_type="application/pdf", filename="report.pdf", contents=b"pdf-bytes")
            result = await upload_chat_media(
                file=upload_file,
                thumbnail="thumb",
                current_user=current_user,
                db=db,
            )

        db.commit.assert_awaited_once()
        self.assertEqual(result["file_id"], "uuid-1")
        self.assertEqual(result["mime_type"], "application/pdf")
        self.assertEqual(result["size"], len(b"pdf-bytes"))
        self.assertEqual(result["width"], 640)
        self.assertEqual(result["height"], 480)
        upload_file.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()